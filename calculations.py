"""
calculations.py: gas properties, recoverable volumes, emissions and risk matrix
for GASROUTE 2030.

Changes vs. the original version are listed in CHANGELOG.md. In short:
  * C4+ liquids are no longer dropped from the GPM / LPG calculation
  * volumes keep full precision (round only when displaying)
  * CO2e abatement is computed on a flare basis, not a venting basis
  * the risk matrix reacts to inputs and no longer recommends the lowest-scored route
"""

# Standard physical properties for gas components at standard conditions
# Molecular Weight (MW) in lb/lb-mol, Liquid Density (rho) in lb/gal
GAS_PROPERTIES = {
    'C1':  {'MW': 16.043, 'rho_liquid': 2.50},  # Methane
    'C2':  {'MW': 30.070, 'rho_liquid': 2.97},  # Ethane
    'C3':  {'MW': 44.097, 'rho_liquid': 4.23},  # Propane
    'C4+': {'MW': 58.120, 'rho_liquid': 4.86},  # Butane+ (n-butane as proxy)
    'N2':  {'MW': 28.013, 'rho_liquid': 6.74},  # Nitrogen
    'CO2': {'MW': 44.010, 'rho_liquid': 8.50},  # Carbon Dioxide
    'H2S': {'MW': 34.080, 'rho_liquid': 7.97}   # Hydrogen Sulfide
}

SCF_PER_LBMOL = 379.4          # standard molar volume used throughout
LB_TO_KG = 0.45359237
CO2_MW = 44.01
CARBON_ATOMS = {'C1': 1, 'C2': 2, 'C3': 3, 'C4+': 4}

# Days per year the flare is actually burning, by asset category
FLARING_DAYS_BY_CATEGORY = {
    "Non-AG_Forfeiture_Risk": 365,      # continuous routine flaring
    "Active_AG_Upset_Audit": 60,        # cumulative trips/upsets per year
    "NGFCP_Awarded_ThirdParty": 365,
}

DEFAULT_RECOVERY_FACTOR = 0.85
DEFAULT_C3_SHARE_OF_C3PLUS = 0.60   # assumed C3 : C4+ split of the "C3+" input (60 : 40)


# ---------------------------------------------------------------------------
# Composition helpers
# ---------------------------------------------------------------------------
def _normalise_fractions(mole_fractions):
    """
    Maps the keys the app uses ('C4') onto the keys the property table uses ('C4+')
    and drops anything that is not a known component (e.g. 'Total_C3+').
    This fixes the original bug where the C4 fraction was silently ignored.
    """
    out = {}
    for comp, z in mole_fractions.items():
        key = "C4+" if comp in ("C4", "C4+") else comp
        if key in GAS_PROPERTIES:
            out[key] = out.get(key, 0.0) + float(z)
    return out


def build_mole_fractions(c1_percent, c3plus_percent, c3_share=DEFAULT_C3_SHARE_OF_C3PLUS):
    """Builds the mole-fraction dict from the two sidebar inputs."""
    c3p = c3plus_percent / 100.0
    return {"C1": c1_percent / 100.0, "C3": c3p * c3_share, "C4+": c3p * (1.0 - c3_share)}


# ---------------------------------------------------------------------------
# Volumes
# ---------------------------------------------------------------------------
def calculate_recoverable_volumes(flare_mmscfd, asset_category, recovery_factor=DEFAULT_RECOVERY_FACTOR):
    """
    Separates peak design capacity (for equipment sizing) from annual volume
    (for economics), and reports the residual flare that the project does NOT capture.
    """
    flaring_days = FLARING_DAYS_BY_CATEGORY.get(asset_category, 365)
    f_down = (365 - flaring_days) / 365.0

    daily_recoverable = flare_mmscfd * recovery_factor
    residual_flare = flare_mmscfd - daily_recoverable

    return {
        "daily_recoverable_mmscfd": daily_recoverable,
        "design_capacity_mmscfd": daily_recoverable,       # passed to equipment sizing / routing
        "residual_flare_mmscfd": residual_flare,
        "annual_recoverable_mmscf": daily_recoverable * flaring_days,
        "annual_residual_mmscf": residual_flare * flaring_days,
        "flaring_days_per_year": flaring_days,
        "f_down": round(f_down, 4),
        "recovery_factor": recovery_factor,
    }


def calculate_gpm(mole_fractions):
    """
    Gallons per thousand cubic feet (GPM) for condensable components.
    GPM_i = (z_i * 1000 / 379.4) * (MW_i / rho_liquid_i)
    """
    z = _normalise_fractions(mole_fractions)
    gpm_results = {}
    for comp in ('C2', 'C3', 'C4+'):
        zi = z.get(comp, 0.0)
        if zi > 0:
            mw = GAS_PROPERTIES[comp]['MW']
            rho = GAS_PROPERTIES[comp]['rho_liquid']
            gpm_results[comp] = round((zi * 1000 / SCF_PER_LBMOL) * (mw / rho), 3)
    gpm_results['Total_C3+'] = round(gpm_results.get('C3', 0) + gpm_results.get('C4+', 0), 3)
    return gpm_results


def calculate_component_volumes(daily_recoverable_mmscfd, mole_fractions):
    """Breaks the recoverable stream into commercial products."""
    z = _normalise_fractions(mole_fractions)
    gpm_yields = calculate_gpm(mole_fractions)

    methane_mmscfd = daily_recoverable_mmscfd * z.get('C1', 0.70)
    lpg_gpm = gpm_yields.get('Total_C3+', 0)
    lpg_gallons_per_day = (daily_recoverable_mmscfd * 1000) * lpg_gpm   # MSCFD * GPM = GPD

    return {
        "methane_mmscfd": methane_mmscfd,
        "lpg_liquids_gpd": lpg_gallons_per_day,
        "gpm_yields": gpm_yields,
    }


# ---------------------------------------------------------------------------
# Emissions
# ---------------------------------------------------------------------------
def calculate_emissions_abatement(annual_recoverable_mmscf, mole_fractions,
                                  flare_combustion_efficiency=0.98,
                                  project_leakage_rate=0.005,
                                  gwp_ch4=28.0):
    """
    Emissions benefit of capturing gas that was being FLARED.

    A flare already burns most of the methane. Capturing the gas avoids only the methane
    that escapes the flare unburned (1 - combustion efficiency), minus whatever the new
    project leaks. The CO2 from burning the gas is emitted again when the gas is used,
    so it is NOT counted as avoided (displaced-fuel credits are not modelled here).

    The old app formula assumed every molecule of methane was vented to atmosphere and
    reported ~1.25M tCO2e/yr for 7.2 MMSCFD. That figure is only valid for gas that is
    currently vented; it is returned separately as `vent_equivalent_tco2e` for reference.
    """
    z = _normalise_fractions(mole_fractions)
    lbmol_per_year = annual_recoverable_mmscf * 1e6 / SCF_PER_LBMOL

    ch4_tonnes = lbmol_per_year * z.get('C1', 0.0) * GAS_PROPERTIES['C1']['MW'] * LB_TO_KG / 1000.0

    slip = max(0.0, (1.0 - flare_combustion_efficiency) - project_leakage_rate)
    net_abatement = ch4_tonnes * slip * gwp_ch4
    vent_equivalent = ch4_tonnes * gwp_ch4

    co2_lb_per_lbmol_gas = sum(z.get(c, 0.0) * n for c, n in CARBON_ATOMS.items()) * CO2_MW
    flare_co2_tonnes = lbmol_per_year * co2_lb_per_lbmol_gas * LB_TO_KG / 1000.0 * flare_combustion_efficiency

    return {
        "methane_handled_t": ch4_tonnes,
        "net_abatement_tco2e": net_abatement,
        "vent_equivalent_tco2e": vent_equivalent,
        "flare_combustion_co2_t": flare_co2_tonnes,
    }


def co2_tonnes_per_mscf_burned(mole_fractions, combustion_efficiency=1.0):
    """Tonnes of CO2 released by burning 1 MSCF of this gas (carbon in = CO2 out)."""
    z = _normalise_fractions(mole_fractions)
    lbmol = 1000.0 / SCF_PER_LBMOL
    co2_lb = lbmol * sum(z.get(c, 0.0) * n for c, n in CARBON_ATOMS.items()) * CO2_MW
    return co2_lb * LB_TO_KG / 1000.0 * combustion_efficiency


def implied_carbon_price(penalty_usd_per_mscf, mole_fractions):
    """
    The flare penalty expressed in USD per tonne of CO2 released when that gas is flared.
    Lets the penalty be compared with carbon prices and abatement costs (report Table 18).
    """
    t = co2_tonnes_per_mscf_burned(mole_fractions, 0.98)
    return penalty_usd_per_mscf / t if t > 0 else 0.0


# Operating-emission assumptions for the capture route itself (ASSUMPTIONS, not from the report except
# the ~10% compression fuel for CNG)
TRUCK_KG_CO2_PER_KM = 1.0           # loaded diesel tanker/trailer, per km driven (round trip counted)


def route_operating_emissions(route_name, annual_mmscf, mole_fractions, distance_to_market_km=0.0,
                              truck_mscf=250.0, fuel_fraction=0.10):
    """
    CO2 emitted by running the route: compression fuel (CNG) and truck haulage (CNG/LNG/LPG).
    Returned in tonnes CO2 per year. Not netted from `net_abatement_tco2e`: shown beside it so the
    net climate case can be judged.
    """
    per_mscf = co2_tonnes_per_mscf_burned(mole_fractions)
    fuel = annual_mmscf * 1000.0 * fuel_fraction * per_mscf if route_name == "CNG" else 0.0
    trucked = route_name in ("CNG", "LNG", "LPG_NGL")
    loads = (annual_mmscf * 1000.0 / truck_mscf) if (trucked and route_name == "CNG") else 0.0
    truck = loads * 2.0 * distance_to_market_km * TRUCK_KG_CO2_PER_KM / 1000.0
    return {"compression_fuel_tco2": fuel, "truck_tco2": truck, "total_tco2": fuel + truck}


# ---------------------------------------------------------------------------
# Risk matrix
# ---------------------------------------------------------------------------
def generate_risk_matrix(asset_category, recommended_route, context=None):
    """
    Risk Assessment Matrix tailored to the facility baseline, the recommended route and
    (optionally) live inputs.

    context keys (all optional): truckloads_per_day, distance_to_market_km,
    residual_flare_mmscfd, build_years.
    """
    ctx = context or {}
    risks = []

    def add(risk, impact, probability, mitigation):
        risks.append({"Risk": risk, "Impact": impact, "Probability": probability, "Mitigation": mitigation})

    # 1. Base risks defined by the facility's current situation
    if asset_category == "Active_AG_Upset_Audit":
        add("Upstream Backpressure", "High", "High",
            "Retain safety flare fallback; utilize modular backup sizing to prevent oil production bottlenecks.")
        add("Variable Upset Flow", "Medium", "High",
            "Ensure wide turndown capability in secondary gas handling equipment.")
    elif asset_category == "Non-AG_Forfeiture_Risk":
        add("Capital Overrun", "High", "Medium",
            "Deploy standardized modular skids rather than bespoke stick-built facilities.")
        add("Asset Forfeiture", "Critical", "Medium",
            "File the commercialization pathway with NUPRC early, secure a binding developer commitment, "
            "and agree an interim flare-reduction schedule.")

    if ctx.get("flaring_authorized") is False:
        add("Unauthorized Flaring Exposure", "Critical", "High",
            "Regularize the flaring status with NUPRC before commissioning. Enforcement outcomes can go beyond "
            "the published penalty tiers and are not priced in the model.")

    # 2. Risks every project of this type carries
    build_years = ctx.get("build_years")
    if build_years:
        add("Schedule Delay", "High", "Medium",
            f"Penalties keep accruing during the ~{build_years}-year build. Pre-order long-lead modular skids "
            "and include delay damages in the BOOT agreement.")
    add("Developer / Off-taker Default", "High", "Low",
        "Pre-qualify developers on financial strength; require performance bonds, take-or-pay and step-in rights.")
    add("Regulatory Approval Timing", "Medium", "Medium",
        "Engage NUPRC before filing; confirm required approvals and the treatment of penalty accruals in writing.")
    add("Host Community & Site Security", "Medium", "Medium",
        "Community engagement and local-content plan; site security and surveillance for exposed assets.")
    if ctx.get("residual_flare_mmscfd", 0) > 0:
        add("Residual Flaring", "Medium", "High",
            f"About {ctx['residual_flare_mmscfd']:.2f} MMSCFD is not captured. Keep an approved safety/pilot flare "
            "and report residual volumes to NUPRC.")

    # 3. Route-specific risks
    if recommended_route == "GAS_NETWORK_TIE_IN":
        add("Pipeline Sabotage", "High", "Medium",
            "Bury tie-in lines; execute community engagement and surveillance.")
    elif recommended_route == "COLLOCATED_OFFTAKER":
        add("Off-taker Default", "High", "Low",
            "Implement rigid Take-or-Pay agreements and guarantee letters of credit.")
    elif recommended_route == "CNG":
        loads = ctx.get("truckloads_per_day", 0)
        heavy = loads > 20 or ctx.get("distance_to_market_km", 0) > 150
        add("Logistics Failure", "High" if loads > 20 else "Medium", "High" if heavy else "Medium",
            (f"About {loads:.0f} truckloads/day are needed. " if loads else "")
            + "Maintain N+2 truck fleet redundancy; survey road conditions seasonally.")
    elif recommended_route == "LPG_NGL":
        add("Storage Saturation", "High", "Medium",
            "Ensure continuous truck evacuation to prevent forced upstream shutdowns.")
    elif recommended_route == "LNG":
        add("Baseload Dependency", "High", "Medium",
            "Secure continuous feed gas and standby storage; cryogenic units do not tolerate intermittent flow.")
    elif recommended_route == "POWER":
        add("Demand Mismatch", "Medium", "Medium",
            "Size generation to firm local demand; contract an anchor load before committing capital.")
    elif recommended_route == "REINJECTION":
        add("Reservoir Performance", "High", "Medium",
            "Confirm injectivity and pressure support with reservoir studies before heavy compression is ordered.")

    return risks
