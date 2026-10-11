"""
route.py: scores and ranks gas commercialization routes for GASROUTE 2030.

Changes vs. the original version are listed in CHANGELOG.md. In short:
  * power and collocated off-taker scores now depend on how much gas the demand can absorb
    (previously POWER scored 55 with 0 MW of demand)
  * ties are broken explicitly (revenue-generating first, then cheaper CAPEX)
  * REINJECTION is flagged as a compliance route, not a revenue route
  * CAPEX benchmarks live here (moved out of app.py) and the pipeline route now scales with distance
  * every route carries `flags` that the UI shows as warnings

Values marked PLACEHOLDER are not engineering-validated and should be replaced with
project-specific numbers.
"""

# ---------------------------------------------------------------------------
# Route metadata and benchmarks
# ---------------------------------------------------------------------------
ROUTE_META = {
    #                       label                    revenue?  cheaper = lower rank (tie-break)
    "COLLOCATED_OFFTAKER": {"label": "Collocated Off-taker", "revenue_generating": True,  "capex_rank": 1},
    "GAS_NETWORK_TIE_IN":  {"label": "Gas Network Tie-in",   "revenue_generating": True,  "capex_rank": 2},
    "CNG":                 {"label": "CNG (Virtual Pipeline)", "revenue_generating": True, "capex_rank": 3},
    "REINJECTION":         {"label": "Reinjection / EOR",    "revenue_generating": False, "capex_rank": 4},
    "POWER":               {"label": "Power Generation",     "revenue_generating": True,  "capex_rank": 5},
    "LPG_NGL":             {"label": "LPG / NGL Recovery",   "revenue_generating": True,  "capex_rank": 6},
    "LNG":                 {"label": "Small-scale LNG",      "revenue_generating": True,  "capex_rank": 7},
}

# Benchmark CAPEX, USD per MMSCFD of design capacity (carried over from the original app.py)
CAPEX_USD_PER_MMSCFD = {
    "GAS_NETWORK_TIE_IN": 500_000, "COLLOCATED_OFFTAKER": 250_000, "CNG": 1_200_000,
    "LNG": 4_500_000, "POWER": 1_800_000, "LPG_NGL": 2_500_000, "REINJECTION": 1_400_000,
}
PIPELINE_USD_PER_KM = 600_000        # PLACEHOLDER: added so the tie-in route cost grows with distance
OPEX_FRACTION_OF_CAPEX = 0.08        # carried over from the original app.py

TRAILER_CAPACITY_MSCF = 250          # report: a CNG truck carries about 0.25 MMSCF
CNG_MAX_MMSCFD = 5.0                 # report: CNG suits roughly below 5 MMSCFD
CNG_LOADS_FLAG = 20                  # PLACEHOLDER: truckloads/day above which the UI raises a logistics warning
POWER_GAS_USE_MMSCFD_PER_MW = 0.34   # report: reciprocating gas engines use ~0.34 MMSCFD per MW


def estimate_capex(route_name, design_mmscfd, distance_to_network_km=0.0, multiplier=1.0):
    """CAPEX in USD for a route. The tie-in route adds a per-km pipeline term."""
    base = CAPEX_USD_PER_MMSCFD.get(route_name, 1_000_000) * design_mmscfd
    if route_name == "GAS_NETWORK_TIE_IN":
        base += PIPELINE_USD_PER_KM * distance_to_network_km
    return base * multiplier


FIXED_CAPEX_USD = 1_000_000          # PLACEHOLDER: site works, permitting, minimum plant size, mobilization
FIXED_OPEX_USD_PER_YEAR = 300_000    # PLACEHOLDER: crew, security, insurance, minimum maintenance


def estimate_costs(route_name, design_mmscfd, distance_to_network_km=0.0, multiplier=1.0,
                   fixed_capex=FIXED_CAPEX_USD, fixed_opex=FIXED_OPEX_USD_PER_YEAR):
    """
    Returns (capex, annual_opex) in USD.
    Variable CAPEX scales with volume; fixed CAPEX/OPEX do not. Without a fixed component IRR is
    identical for a 0.3 and an 8.5 MMSCFD flare, so tiny "dead flares" could never be rejected.
    """
    if design_mmscfd <= 0:
        return 0.0, 0.0
    variable_capex = estimate_capex(route_name, design_mmscfd, distance_to_network_km, multiplier)
    capex = variable_capex + fixed_capex
    opex = variable_capex * OPEX_FRACTION_OF_CAPEX + fixed_opex
    return capex, opex


def _sort_key(route):
    meta = ROUTE_META[route["route_name"]]
    return (-route["score"], 0 if meta["revenue_generating"] else 1, meta["capex_rank"])


def select_recommendation(ranked_routes, reinjection_suitable=False):
    """
    Primary = best revenue-generating route. Reinjection earns nothing, so it is shown
    as a compliance fallback instead of competing with commercial routes on score.
    """
    commercial = [r for r in ranked_routes if r["revenue_generating"]]
    primary = commercial[0] if commercial else ranked_routes[0]
    fallback = None
    if reinjection_suitable:
        fallback = next((r for r in ranked_routes if not r["revenue_generating"]), None)
    return {"primary": primary, "fallback": fallback}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def evaluate_routes(data):
    """
    Evaluates and ranks potential gas commercialization routes based on facility data,
    gas composition (GPM), spatial intelligence, and utilization profiles.

    `design_capacity_mmscfd` should be the RECOVERABLE design capacity (calculations.py),
    not the gross flare volume.
    """
    flare = data.get("design_capacity_mmscfd", data.get("flare_mmscfd", 0))
    asset_category = data.get("asset_category", "Non-AG_Forfeiture_Risk")

    location_type = data.get("location_type", "Onshore")
    gpm_yields = data.get("gpm_yields", {})
    total_c3_plus = gpm_yields.get("Total_C3+", 0.0)

    c1_percent = data.get("c1_percent", 0)
    wobbe_index = data.get("wobbe_index", 0)
    distance_to_network_km = data.get("distance_to_network_km", 999)
    distance_to_market_km = data.get("distance_to_market_km", 999)
    road_access = data.get("road_access", False)

    power_demand_mw = data.get("power_demand_mw", 0)
    collocated_demand_mmscfd = data.get("collocated_demand_mmscfd", 0)
    reinjection_suitable = data.get("reinjection_suitable", False)

    # Utilization penalty: upset flares run < 20% of the year, so high-CAPEX routes lose throughput.
    is_upset = (asset_category == "Active_AG_Upset_Audit")
    capex_penalty = 0.3 if is_upset else 1.0
    no_trucking = (not road_access) or location_type == 'Offshore'

    routes = {}

    def register(name, score, infrastructure, capex_profile, justification, flags=None):
        meta = ROUTE_META[name]
        routes[name] = {
            "route_name": name,
            "label": meta["label"],
            "score": round(max(0.0, min(float(score), 100.0)), 1),
            "revenue_generating": meta["revenue_generating"],
            "infrastructure": infrastructure,
            "capex_profile": capex_profile,
            "justification": " | ".join(justification),
            "flags": flags or [],
        }

    # ROUTE 1: GAS NETWORK TIE-IN ------------------------------------------------
    score, why = 20, []
    if distance_to_network_km <= 15:
        score += 50
        why.append("Excellent proximity to gas trunkline.")
    elif distance_to_network_km <= 50:
        score += 25
        why.append("Viable tie-in distance.")
    else:
        why.append("Pipeline distance exceeds economic threshold.")
    if flare >= 3:
        score += 15
    if c1_percent >= 85 and wobbe_index > 40:
        score += 15
    score *= capex_penalty
    if is_upset:
        why.append("Warning: Low utilization (Upset Flare) destroys pipeline ROI.")
    register("GAS_NETWORK_TIE_IN", score, ["Metering", "Pipeline Tie-in", "Pressure Control"],
             "Low to Moderate - Highly distance dependent", why)

    # ROUTE 2: COLLOCATED OFF-TAKER ----------------------------------------------
    score, why = 10, []
    if collocated_demand_mmscfd > 0 and flare > 0:
        coverage = min(1.0, collocated_demand_mmscfd / flare)      # share of the gas the off-taker can absorb
        score += 70 * coverage
        why.append(f"Off-taker can absorb {coverage * 100:.0f}% of recoverable gas.")
    else:
        why.append("No local collocated demand identified.")
    if is_upset:
        why.append("Ideal for upset facilities (shifts CAPEX risk to off-taker).")
    register("COLLOCATED_OFFTAKER", score, ["Metering", "Minimal Piping Tie-in"],
             "Minimal - Off-taker handles processing", why)

    # ROUTE 3: CNG ---------------------------------------------------------------
    score, why, flags = 30, [], []
    loads_per_day = (flare * 1000.0) / TRAILER_CAPACITY_MSCF if flare > 0 else 0.0
    if no_trucking:
        score = 0
        why.append("Fatal Flaw: No road access or offshore site prevents truck logistics.")
    else:
        score += 20
        why.append("Road access supports virtual pipeline.")
        if distance_to_market_km <= 150:
            score += 20
            why.append("Target market within optimal trucking radius.")
        if flare >= 1:
            score += 15
        if c1_percent >= 85:
            score += 15
        if loads_per_day > 0:
            why.append(f"Needs ~{loads_per_day:.0f} truckloads/day at {TRAILER_CAPACITY_MSCF} Mscf per trailer.")
        if loads_per_day > CNG_LOADS_FLAG:
            flags.append(f"CNG at this volume means roughly {loads_per_day:.0f} truckloads per day. "
                         "Validate fleet size, road capacity and compression cost before relying on this route.")
    # Report: CNG is competitive below ~5 MMSCFD; above that LNG or a pipeline is normally preferred.
    if flare > CNG_MAX_MMSCFD:
        score *= 0.85 if flare <= 10 else 0.70
        why.append(f"Above ~{CNG_MAX_MMSCFD:g} MMSCFD CNG loses ground to LNG/pipeline (World Bank GGFR).")
    score *= (0.5 if is_upset else 1.0)
    register("CNG", score, ["KO Drum", "Modular Compressor", "Loading Facility"],
             "Moderate - Requires compression and truck logistics", why, flags)

    # ROUTE 4: LNG ---------------------------------------------------------------
    score, why = 20, []
    if flare >= 5:
        score += 30
        why.append("Volume supports small-scale train.")
    if c1_percent >= 90:
        score += 20
        why.append("High methane purity ideal for liquefaction.")
    if no_trucking:
        score = 0
        why.append("Fatal Flaw: Requires deepwater marine loading or onshore road access.")
    score *= capex_penalty
    if is_upset:
        why.append("Warning: Cryogenic facilities require continuous baseload flow.")
    register("LNG", score, ["Pretreatment", "Liquefaction Unit", "Cryo Storage"],
             "High - Complex cryogenic equipment", why)

    # ROUTE 5: POWER GENERATION ---------------------------------------------------
    why = []
    if power_demand_mw <= 0:
        score = 10
        why.append("No local power demand identified.")
    else:
        absorbed = power_demand_mw * POWER_GAS_USE_MMSCFD_PER_MW
        coverage = min(1.0, absorbed / flare) if flare > 0 else 0.0
        score = 25 + 45 * coverage
        why.append(f"{power_demand_mw:g} MW of demand absorbs ~{absorbed:.2f} MMSCFD "
                   f"({coverage * 100:.0f}% of recoverable gas).")
        if flare >= 1:
            score += 20
        if c1_percent >= 75:
            score += 10
        score *= (0.7 if is_upset else 1.0)
    register("POWER", score, ["Gas Engine/Turbine", "Generator", "Distribution"],
             "Moderate to High", why)

    # ROUTE 6: LPG / NGL RECOVERY -------------------------------------------------
    score, why = 15, []
    if no_trucking:
        score = 0
        why.append("Fatal Flaw: Requires road access for bullet truck evacuation.")
    else:
        if total_c3_plus >= 3.0:
            score += 50
            why.append(f"Highly lucrative rich gas stream ({total_c3_plus:.2f} GPM).")
        elif total_c3_plus >= 1.5:
            score += 25
            why.append(f"Viable liquid yields ({total_c3_plus:.2f} GPM).")
        else:
            why.append(f"Lean gas ({total_c3_plus:.2f} GPM) severely limits LPG recovery.")
        if flare >= 3:
            score += 20
    score *= capex_penalty
    register("LPG_NGL", score, ["NGL Recovery Unit", "Fractionation", "Pressurized Storage"],
             "High - Requires fractionation towers", why)

    # ROUTE 7: REINJECTION / EOR (compliance route, earns no gas revenue) ---------
    score, why = 10, []
    if reinjection_suitable:
        score += 60
        why.append("Reservoir geometry supports reinjection.")
    else:
        why.append("Reservoir not designated for gas support.")
    if flare >= 2:
        score += 30
    why.append("Compliance route: removes flaring but earns no gas revenue.")
    score *= capex_penalty
    register("REINJECTION", score, ["Heavy Compression", "Injection Well"],
             "High - Specialized well interventions", why)

    ranked = sorted(routes.values(), key=_sort_key)
    for r in ranked:
        r["disqualified"] = r["score"] <= 0
    return ranked
