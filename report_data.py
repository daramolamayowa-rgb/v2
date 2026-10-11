"""
report_data.py: benchmark data transcribed from the World Bank GGFR report
"Report on Small-scale Technologies for Utilization of Associated Gas" (referred to below as "the report").

Everything here is a published benchmark for the SMALL-SCALE (<15 MMSCFD) market, not a quote for a
specific project. Replace with supplier quotes as they arrive. Table numbers refer to the report.

Units: CAPEX in USD million per MMSCFD of capacity (= USD per SCFD), US Gulf Coast equipment cost;
OPEX in USD per MSCF of gas processed; gas quality limits in mole %.
"""

# ---------------------------------------------------------------------------
# Table 12: regional CAPEX factor (shipping x 25% installation) and discount rate
# ---------------------------------------------------------------------------
REGIONS = {
    "Africa (normal risk)": {"capex_factor": 1.75, "discount_rate": 0.10},
    "Africa (high risk)":   {"capex_factor": 1.88, "discount_rate": 0.14},
    "North America":        {"capex_factor": 1.38, "discount_rate": 0.07},
    "South America":        {"capex_factor": 1.63, "discount_rate": 0.10},
    "Europe":               {"capex_factor": 1.50, "discount_rate": 0.07},
    "West Asia":            {"capex_factor": 1.63, "discount_rate": 0.12},
    "Central Asia":         {"capex_factor": 1.75, "discount_rate": 0.10},
    "East Asia":            {"capex_factor": 1.63, "discount_rate": 0.08},
    "South Asia":           {"capex_factor": 1.63, "discount_rate": 0.10},
    "Oceania":              {"capex_factor": 1.50, "discount_rate": 0.07},
}
DEFAULT_REGION = "Africa (normal risk)"

# ---------------------------------------------------------------------------
# Table 13: economy-of-scale multiplier on unit CAPEX, by capacity band (upper bound, MMSCFD)
# ---------------------------------------------------------------------------
SCALE_BANDS = (1.0, 3.0, 5.0, 10.0, 15.0)
SCALE_FACTORS = {
    "CNG":      (2.25, 1.90, 1.60, 1.25, 1.0),
    "LNG":      (2.00, 1.75, 1.40, 1.25, 1.0),
    "GPP":      (1.80, 1.50, 1.20, 1.10, 1.0),
    "POWER":    (1.50, 1.20, 1.10, 1.05, 1.0),
}

# ---------------------------------------------------------------------------
# Table 7: maximum impurity allowed (mole %). C3+ is the heavy-hydrocarbon limit.
# ---------------------------------------------------------------------------
GAS_QUALITY_LIMITS = {
    "CNG":   {"h2s": 0.05, "co2": 1.0,  "h2o": 0.20, "c3plus": 5.0},
    "LNG":   {"h2s": 0.05, "co2": 0.30, "h2o": 0.03, "c3plus": 5.0},
    "POWER": {"h2s": 0.15, "co2": 20.0, "h2o": 3.0,  "c3plus": 15.0},   # small-scale gas engines
    "GPP":   {"h2s": 0.20, "co2": 0.40, "h2o": 0.20, "c3plus": 100.0},
}
# H2S / CO2 / H2O are not in the facility data. Until they are measured the model assumes sweet, dry gas
# (no acid-gas or dehydration pretreatment) and the UI says so. Enter real values to see the cost.
DEFAULT_IMPURITIES = {"h2s": 0.0, "co2": 0.0, "h2o": 0.0}

# ---------------------------------------------------------------------------
# Table 8: installed technology CAPEX, (Low, High) USD M per MMSCFD
# Table 9: OPEX, (Low, High) USD per MSCF
# ---------------------------------------------------------------------------
TECH_CAPEX = {
    "CNG":   (1.6, 2.0),
    "LNG":   (3.0, 5.0),
    "POWER": (2.0, 3.0),     # small-scale reciprocating gas engines
    "GPP":   (1.0, 1.4),     # mini GPP, NGL recovery, dry gas export
}
TECH_OPEX = {
    "CNG":   (1.2, 1.7),
    "LNG":   (1.9, 2.3),
    "POWER": (0.7, 0.9),
    "GPP":   (0.7, 0.9),
}

# ---------------------------------------------------------------------------
# Tables 10-11: pretreatment, (Low, High). CAPEX USD M per MMSCFD, OPEX USD per MSCF.
# ---------------------------------------------------------------------------
PRETREAT_CAPEX = {
    "NGL removal (C3+)":  (0.8, 1.0),
    "Dehydration":        (0.3, 0.4),
    "H2S removal":        (0.65, 0.85),
    "CO2 removal":        (0.65, 0.85),
}
PRETREAT_OPEX = {
    "NGL removal (C3+)":  (0.30, 0.50),
    "Dehydration":        (0.20, 0.30),
    "H2S removal":        (0.70, 0.90),
    "CO2 removal":        (0.70, 0.90),
}

# ---------------------------------------------------------------------------
# Table 14: product netbacks (Low, High)
# ---------------------------------------------------------------------------
NETBACK = {
    "dry_gas_usd_mscf":   (1.13, 2.25),
    "propane_usd_t":      (165.0, 280.0),
    "butane_usd_t":       (175.0, 300.0),
    "condensate_usd_t":   (220.0, 350.0),
    "power_usd_mwh":      (45.0, 60.0),
    "cng_usd_mscf":       (3.8, 6.8),
    "lng_usd_t":          (275.0, 500.0),
}

# ---------------------------------------------------------------------------
# Technical parameters from the report text
# ---------------------------------------------------------------------------
NGL_REMOVAL_YIELD = 0.65            # share of C3 and C4 recovered by NGL removal (section 4.1.7)
CNG_FUEL_FRACTION = 0.10            # share of the gas burned to run compression
LNG_CONVERSION = 0.80               # mid of the 70-90% of feed gas converted to LNG
LNG_MSCF_PER_TONNE = 47.5           # feed gas per tonne of LNG at 100% conversion (5.4-6.9 kt/yr per MMSCFD at 70-90%)
POWER_MMSCFD_PER_MW = 0.34          # reciprocating gas engines
POWER_CAPACITY_FACTOR = 0.90        # ASSUMPTION (not from the report): engine availability
CNG_TRUCK_MSCF = 250                # ~0.25 MMSCF per CNG trailer
CNG_MAX_MMSCFD = 5.0                # CNG is competitive below about 5 MMSCFD, LNG above
ENGINE_MIN_LOAD = 0.40              # reciprocating engines: 40% minimum load

# Liquid properties used to turn GPM yields into tonnes (ASSUMPTIONS: standard densities)
PROPANE_T_PER_GAL = 0.001919        # 0.507 kg/L
BUTANE_T_PER_GAL = 0.002169         # 0.573 kg/L
PROPANE_SCF_PER_GAL = 36.4          # gas volume equivalent of one gallon of liquid propane
BUTANE_SCF_PER_GAL = 31.5

# ---------------------------------------------------------------------------
# Implementation time (months) and project life
# ---------------------------------------------------------------------------
BUILD_MONTHS = {"fast": 6, "typical": 12, "slow": 18}
PROJECT_LIFE_YEARS = 10             # including construction, as in the report

# ---------------------------------------------------------------------------
# Table 4: associated gas production profiles (average MMSCFD per year, 12 years)
# ---------------------------------------------------------------------------
PRODUCTION_PROFILES = {
    "Flat (as entered)": None,
    "A: very small, rises then declines": (2.3, 2.6, 2.8, 3.0, 3.1, 2.9, 2.6, 2.5, 2.5, 2.5, 2.5, 2.5),
    "B: small, dips then recovers":       (6.2, 5.6, 4.9, 3.5, 3.3, 3.5, 3.6, 4.0, 4.2, 4.4, 4.7, 4.9),
    "C: small-medium, sharp peak":        (3.1, 3.7, 5.9, 10.3, 12.7, 10.8, 7.0, 4.5, 3.9, 3.7, 3.5, 3.3),
    "D: medium, slow build":              (8.3, 9.2, 9.8, 10.2, 10.6, 11.0, 11.1, 11.1, 10.9, 10.5, 10.1, 9.7),
    "E: large, gradual decline":          (14.4, 14.7, 13.8, 13.0, 13.6, 12.6, 12.3, 13.0, 12.7, 12.6, 11.7, 12.3),
}
SIZING_STRATEGIES = ("Average rate (recommended)", "Maximum rate", "Minimum rate")   # Table 17: average beats maximum

CITATION = ("World Bank GGFR, 'Report on Small-scale Technologies for Utilization of Associated Gas' "
            "(Tables 4, 7-14 and sections 4.1-4.2)")
