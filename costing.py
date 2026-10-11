"""
costing.py: report-calibrated CAPEX, OPEX, gas-quality gates and product revenue for each route.

A "plan" is everything the cash-flow engine (finance.py) needs about one route in one scenario:
installed CAPEX, OPEX and sales revenue per MMSCF of gas processed, the pretreatment the gas needs,
and any volume cap (an off-taker or a power load can absorb only so much gas).

Scenarios follow the report's Low / High cases:
    pessimistic = high CAPEX and OPEX, low netback
    base        = midpoint of the Low and High cases
    optimistic  = low CAPEX and OPEX, high netback

Routes the report does not cost (pipeline tie-in, collocated off-taker, reinjection) keep the v1
PLACEHOLDER benchmarks. They are flagged `benchmark_source = "placeholder"` so the UI can say so.
"""
import math

import report_data as RD

SCENARIOS = ("pessimistic", "base", "optimistic")

ROUTE_TECH = {"CNG": "CNG", "LNG": "LNG", "POWER": "POWER", "LPG_NGL": "GPP"}

# v1 placeholders for routes the report does not cover (USD per MMSCFD, USD per km, fixed items)
PLACEHOLDER_CAPEX_USD_PER_MMSCFD = {"GAS_NETWORK_TIE_IN": 500_000, "COLLOCATED_OFFTAKER": 250_000,
                                    "REINJECTION": 1_400_000}
PIPELINE_USD_PER_KM = 600_000
PLACEHOLDER_OPEX_FRACTION = 0.08
PLACEHOLDER_FIXED_CAPEX = 1_000_000
PLACEHOLDER_FIXED_OPEX = 300_000


def _cost(pair, scenario):
    lo, hi = pair
    return {"pessimistic": hi, "base": (lo + hi) / 2.0, "optimistic": lo}[scenario]


def _price(pair, scenario):
    lo, hi = pair
    return {"pessimistic": lo, "base": (lo + hi) / 2.0, "optimistic": hi}[scenario]


def scale_factor(tech, capacity_mmscfd):
    """Table 13: unit CAPEX multiplier for a capacity below 15 MMSCFD (1.0 at or above 15)."""
    factors = RD.SCALE_FACTORS[tech]
    for bound, f in zip(RD.SCALE_BANDS, factors):
        if capacity_mmscfd < bound:
            return f
    return 1.0


# ---------------------------------------------------------------------------
# Gas quality (Table 7)
# ---------------------------------------------------------------------------
def gas_quality_report(tech, quality):
    """
    Compares the gas with the technology's limits. `quality` keys: h2s, co2, h2o, c3plus (mole %).
    Returns one row per impurity and the list of pretreatment steps needed. For GPP the C3+ limit is
    100%, so NGL removal is the product, not pretreatment.
    """
    limits = RD.GAS_QUALITY_LIMITS[tech]
    rows, needs = [], []
    step = {"h2s": "H2S removal", "co2": "CO2 removal", "h2o": "Dehydration", "c3plus": "NGL removal (C3+)"}
    names = {"h2s": "H2S", "co2": "CO2", "h2o": "H2O", "c3plus": "C3+"}
    for key in ("h2s", "co2", "h2o", "c3plus"):
        value = float(quality.get(key, 0.0) or 0.0)
        ok = value <= limits[key] + 1e-12
        rows.append({"Impurity": names[key], "Gas (mol %)": value, "Limit (mol %)": limits[key],
                     "Status": "OK" if ok else f"Exceeds: add {step[key]}"})
        if not ok:
            needs.append(step[key])
    return rows, needs


# ---------------------------------------------------------------------------
# Liquids
# ---------------------------------------------------------------------------
def ngl_stream(annual_mscf, gpm_yields, recovery=RD.NGL_REMOVAL_YIELD):
    """
    Propane and butane recovered by NGL removal (65% of C3 and C4, section 4.1.7), and the dry-gas
    volume that leaves with them. Yields are in gallons per MSCF (GPM).
    """
    c3_gal = annual_mscf * gpm_yields.get("C3", 0.0) * recovery
    c4_gal = annual_mscf * (gpm_yields.get("C4+", gpm_yields.get("C4", 0.0))) * recovery
    return {
        "propane_t": c3_gal * RD.PROPANE_T_PER_GAL,
        "butane_t": c4_gal * RD.BUTANE_T_PER_GAL,
        "shrink_mscf": (c3_gal * RD.PROPANE_SCF_PER_GAL + c4_gal * RD.BUTANE_SCF_PER_GAL) / 1000.0,
    }


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------
def build_plan(route_name, scenario, capacity_mmscfd, *, region, gpm_yields, quality,
               distance_to_network_km=0.0, dry_gas_price_usd_mscf=None, annual_flow_check=None):
    """
    Returns a dict:
        capex_usd, opex_per_mmscf, revenue_per_mmscf, availability, pretreatment, notes, benchmark_source,
        revenue_breakdown (USD per MMSCF), quality_rows, tech
    `capacity_mmscfd` is the design capacity AFTER any demand cap.
    `dry_gas_price_usd_mscf` overrides the report's dry-gas netback range (USD per MSCF) when the user
    has a contract price; it applies to the pipeline, collocated and GPP dry-gas streams only.
    """
    reg = RD.REGIONS[region]
    cap = max(capacity_mmscfd, 0.0)
    notes, pretreat, quality_rows = [], [], []
    tech = ROUTE_TECH.get(route_name)
    availability = 1.0
    breakdown = {}
    annual_mscf_per_mmscf = 1000.0     # one MMSCF = 1,000 MSCF

    def dry_price():
        if dry_gas_price_usd_mscf is not None and dry_gas_price_usd_mscf > 0:
            return float(dry_gas_price_usd_mscf)
        return _price(RD.NETBACK["dry_gas_usd_mscf"], scenario)

    # ---- routes costed from the report ---------------------------------------------------------------
    if tech:
        quality_rows, needs = gas_quality_report(tech, quality)
        if tech == "GPP":
            needs = [n for n in needs if n != "NGL removal (C3+)"]
        pretreat = needs
        capex_unit = _cost(RD.TECH_CAPEX[tech], scenario) + sum(_cost(RD.PRETREAT_CAPEX[n], scenario) for n in needs)
        opex_unit = _cost(RD.TECH_OPEX[tech], scenario) + sum(_cost(RD.PRETREAT_OPEX[n], scenario) for n in needs)
        sf = scale_factor(tech, cap)
        capex = capex_unit * 1e6 * sf * reg["capex_factor"] * cap
        opex_per_mmscf = opex_unit * annual_mscf_per_mmscf          # USD per MSCF x 1,000 MSCF
        source = "report"
        notes.append(f"Unit CAPEX x{sf:.2f} scale factor (Table 13) x{reg['capex_factor']:.2f} regional factor (Table 12).")

        # product streams, per MMSCF of feed
        feed = annual_mscf_per_mmscf
        ngl = ngl_stream(feed, gpm_yields) if (tech == "GPP" or "NGL removal (C3+)" in needs) else None
        shrink = ngl["shrink_mscf"] if ngl else 0.0
        if ngl:
            breakdown["Propane"] = ngl["propane_t"] * _price(RD.NETBACK["propane_usd_t"], scenario)
            breakdown["Butane"] = ngl["butane_t"] * _price(RD.NETBACK["butane_usd_t"], scenario)
        residual = feed - shrink                                     # MSCF of gas left after liquids come out

        if tech == "CNG":
            sales = residual * (1.0 - RD.CNG_FUEL_FRACTION)
            breakdown["CNG"] = sales * _price(RD.NETBACK["cng_usd_mscf"], scenario)
            notes.append(f"{RD.CNG_FUEL_FRACTION:.0%} of the gas is burned for compression.")
            if cap > RD.CNG_MAX_MMSCFD:
                notes.append(f"{cap:.1f} MMSCFD is above the ~{RD.CNG_MAX_MMSCFD:g} MMSCFD where CNG stays competitive; "
                             "LNG or a pipeline is normally preferred.")
        elif tech == "LNG":
            tonnes = residual * RD.LNG_CONVERSION / RD.LNG_MSCF_PER_TONNE
            breakdown["LNG"] = tonnes * _price(RD.NETBACK["lng_usd_t"], scenario)
        elif tech == "POWER":
            availability = RD.POWER_CAPACITY_FACTOR
            mwh = residual / (RD.POWER_MMSCFD_PER_MW * 1000.0 / 24.0)   # MSCF per MWh = 0.34 MMSCFD/MW / 24 h
            breakdown["Power"] = mwh * _price(RD.NETBACK["power_usd_mwh"], scenario)
            notes.append(f"Engine availability {availability:.0%}; minimum engine load {RD.ENGINE_MIN_LOAD:.0%}.")
        elif tech == "GPP":
            breakdown["Dry gas"] = residual * dry_price()
            notes.append("Dry gas is sold at the dry-gas netback; transport to the buyer is not costed (report scope).")
        if pretreat:
            notes.append("Pretreatment added: " + ", ".join(pretreat) + ".")

    # ---- routes the report does not cost (placeholders) ------------------------------------------------
    else:
        source = "placeholder"
        variable = PLACEHOLDER_CAPEX_USD_PER_MMSCFD.get(route_name, 1_000_000) * cap
        if route_name == "GAS_NETWORK_TIE_IN":
            variable += PIPELINE_USD_PER_KM * distance_to_network_km
        capex = (variable + PLACEHOLDER_FIXED_CAPEX) if cap > 0 else 0.0
        fixed_opex = PLACEHOLDER_FIXED_OPEX if cap > 0 else 0.0
        mult = {"pessimistic": 1.25, "base": 1.0, "optimistic": 0.85}[scenario]
        capex *= mult
        # per-MMSCF opex so that opex_total = frac x variable capex + fixed at full design throughput
        design_mmscf = max(cap * 365.0, 1e-9)
        opex_per_mmscf = (variable * PLACEHOLDER_OPEX_FRACTION * mult + fixed_opex) / design_mmscf
        if route_name == "REINJECTION":
            notes.append("Compliance route: no gas revenue. Value comes only from the avoided flare penalty.")
        else:
            breakdown["Dry gas"] = annual_mscf_per_mmscf * dry_price()
            notes.append("CAPEX and OPEX are placeholder benchmarks (the report does not cost pipelines or off-take).")

    return {
        "route": route_name, "tech": tech, "scenario": scenario, "capacity_mmscfd": cap,
        "capex_usd": capex, "opex_per_mmscf": opex_per_mmscf,
        "revenue_per_mmscf": sum(breakdown.values()), "revenue_breakdown": breakdown,
        "availability": availability, "pretreatment": pretreat, "quality_rows": quality_rows,
        "notes": notes, "benchmark_source": source,
    }


# ---------------------------------------------------------------------------
# Volumes: production profile and sizing strategy (Table 4, Table 17)
# ---------------------------------------------------------------------------
def profile_shape(profile_name, years=RD.PROJECT_LIFE_YEARS):
    """Year-by-year multiplier (mean 1.0 over the project life) for a Table 4 profile."""
    raw = RD.PRODUCTION_PROFILES.get(profile_name)
    if not raw:
        return tuple([1.0] * years)
    window = raw[:years]
    mean = sum(window) / len(window)
    return tuple(v / mean for v in window)


def design_capacity(recoverable_mmscfd, shape, strategy, demand_cap_mmscfd=None):
    """Plant capacity under a sizing strategy (average / maximum / minimum of the profile)."""
    if strategy.startswith("Maximum"):
        f = max(shape)
    elif strategy.startswith("Minimum"):
        f = min(shape)
    else:
        f = sum(shape) / len(shape)
    cap = recoverable_mmscfd * f
    if demand_cap_mmscfd is not None:
        cap = min(cap, demand_cap_mmscfd)
    return cap


def full_year_volumes(recoverable_mmscfd, shape, capacity_mmscfd, flaring_days, availability=1.0):
    """MMSCF processed in each project year if the plant ran the whole year (before the build delay)."""
    return tuple(min(recoverable_mmscfd * s, capacity_mmscfd) * flaring_days * availability for s in shape)


def demand_cap(route_name, power_demand_mw=0.0, collocated_demand_mmscfd=0.0):
    """Maximum gas (MMSCFD) the route's customer can absorb; None means no cap."""
    if route_name == "POWER":
        return max(power_demand_mw, 0.0) * RD.POWER_MMSCFD_PER_MW
    if route_name == "COLLOCATED_OFFTAKER":
        return max(collocated_demand_mmscfd, 0.0)
    return None
