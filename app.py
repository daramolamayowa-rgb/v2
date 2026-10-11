import os
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from calculations import (
    calculate_recoverable_volumes,
    calculate_component_volumes,
    calculate_emissions_abatement,
    build_mole_fractions,
    generate_risk_matrix,
    implied_carbon_price,
    route_operating_emissions,
    DEFAULT_RECOVERY_FACTOR,
)
from route import (
    evaluate_routes,
    TRAILER_CAPACITY_MSCF,
    CNG_LOADS_FLAG,
)
from economics import (
    PENALTY_RATE_HIGH,
    PENALTY_RATE_LOW,
    HIGH_PRODUCER_THRESHOLD_BPD,
)
import analysis as A
import costing as K
import finance as F
import report_data as RD

# --- CONFIGURATION ---
st.set_page_config(
    page_title="GASROUTE 2030",
    page_icon="🔥",
    layout="wide"
)

st.title("GASROUTE 2030")
st.subheader("Intelligent gas flaring commercialization, route ranking, and boardroom decision support.")
st.markdown("---")


# --- SMALL HELPERS ---
# Streamlit deprecated `use_container_width` in favour of `width="stretch"` (about v1.50).
# This picks whichever the installed version understands.
try:
    _st_version = tuple(int(p) for p in st.__version__.split(".")[:2])
except ValueError:
    _st_version = (0, 0)
CHART_KW = {"width": "stretch"} if _st_version >= (1, 50) else {"use_container_width": True}


def pretty(text):
    return str(text).replace("_", " ")


def fmt_m(x):
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x) / 1e6:,.2f}M"


def esc(text):
    """Streamlit markdown treats paired $ signs as LaTeX; escape them in prose."""
    return str(text).replace("$", "\\$")


def fmt_pct(x):
    return "n/a" if x is None else f"{x:,.1f}%"


def fmt_years(x):
    return "n/a" if x is None else f"{x:.1f} years"


def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in ("TRUE", "1", "YES", "Y"):
        return True
    if text in ("FALSE", "0", "NO", "N"):
        return False
    return default


def _num(row, key, default, lo=None, hi=None):
    """Reads a numeric CSV field, falling back to a default for missing/NaN/out-of-range values."""
    try:
        value = float(row.get(key, default))
    except (TypeError, ValueError):
        value = default
    if value != value:  # NaN
        value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


# --- DATA STORAGE ENGINE ---
@st.cache_data
def load_base_database():
    paths = ["facilities.csv", "data/facilities.csv"]
    for path in paths:
        if os.path.exists(path):
            try:
                df = pd.read_csv(path)
                if not df.empty:
                    return df
            except Exception:
                pass
    return pd.DataFrame()


base_df = load_base_database()

# --- REPOSITORY MANAGEMENT ---
st.markdown("### 📂 Asset Repository Management")
col_upload, col_select = st.columns(2)

with col_upload:
    uploaded_file = st.file_uploader("📥 Upload Custom Facility Dataset (CSV)", type=["csv"])

db_df = base_df.copy()
if uploaded_file is not None:
    try:
        uploaded_df = pd.read_csv(uploaded_file)
        if uploaded_df.empty or "facility_name" not in uploaded_df.columns:
            st.error("The uploaded CSV needs at least a `facility_name` column and one row of data.")
        else:
            db_df = pd.concat([uploaded_df, base_df], ignore_index=True).drop_duplicates(subset=["facility_name"])
            expected = ["asset_category", "flare_mmscfd", "c1_percent", "c3_c4_percent", "wobbe_index",
                        "distance_to_network_km", "distance_to_market_km", "power_demand_mw",
                        "collocated_demand_mmscfd", "road_access", "reinjection_suitable", "location_type",
                        "oil_production_bpd", "flaring_authorized", "is_deep_offshore"]
            # h2s_percent, co2_percent and h2o_percent are optional; they default to 0 (sweet, dry gas)
            missing = [c for c in expected if c not in uploaded_df.columns]
            if missing:
                st.warning("These columns are missing and will fall back to defaults: " + ", ".join(missing))
            token = f"{uploaded_file.name}:{uploaded_file.size}"
            if st.session_state.get("_last_upload") != token:
                st.session_state["_last_upload"] = token
                st.toast("Custom file parsed and appended successfully!", icon="✅")
    except Exception as e:
        st.error(f"Failed to read file: {e}")

if not db_df.empty:
    if "facility_name" in db_df.columns:
        db_df["facility_name"] = db_df["facility_name"].astype(str).str.strip()
    if "asset_category" in db_df.columns:
        db_df["asset_category"] = db_df["asset_category"].astype(str).str.strip()

# --- SESSION STATE INITIALIZATION ---
cat_options = ["Non-AG_Forfeiture_Risk", "Active_AG_Upset_Audit", "NGFCP_Awarded_ThirdParty"]
loc_options = ["Onshore", "Offshore", "Swamp"]

default_vals = {
    "flare_in": 8.5,
    "c1_in": 88.0,
    "c3c4_in": 4.5,
    "wobbe_in": 46.2,
    "dist_net_in": 85.0,
    "dist_mkt_in": 150.0,
    "power_in": 0.0,
    "colloc_in": 0.0,
    "road_in": True,
    "reinj_in": False,
    "asset_cat": "Non-AG_Forfeiture_Risk",
    "location_type_in": "Onshore",
    "oil_prod_in": 12500.0,
    "flaring_auth_in": False,
    "is_deep_in": False,
    "h2s_in": 0.0,
    "co2_in": 0.0,
    "h2o_in": 0.0,
}

for key, val in default_vals.items():
    if key not in st.session_state:
        st.session_state[key] = val


def on_preset_change():
    chosen = st.session_state["preset_selector"]
    if chosen != "Manual Input Only" and not db_df.empty:
        matched = db_df[db_df["facility_name"] == chosen]
        if not matched.empty:
            row = matched.iloc[0]
            st.session_state["flare_in"] = _num(row, "flare_mmscfd", 8.5, lo=0.0)
            st.session_state["c1_in"] = _num(row, "c1_percent", 88.0, lo=50.0, hi=100.0)
            st.session_state["c3c4_in"] = _num(row, "c3_c4_percent", 4.5, lo=0.0, hi=20.0)
            st.session_state["wobbe_in"] = _num(row, "wobbe_index", 46.2, lo=30.0)
            st.session_state["dist_net_in"] = _num(row, "distance_to_network_km", 85.0, lo=0.0)
            st.session_state["dist_mkt_in"] = _num(row, "distance_to_market_km", 150.0, lo=0.0)
            st.session_state["power_in"] = _num(row, "power_demand_mw", 0.0, lo=0.0)
            st.session_state["colloc_in"] = _num(row, "collocated_demand_mmscfd", 0.0, lo=0.0)
            st.session_state["road_in"] = _to_bool(row.get("road_access", True), True)
            st.session_state["reinj_in"] = _to_bool(row.get("reinjection_suitable", False), False)
            category = str(row.get("asset_category", default_vals["asset_cat"]))
            st.session_state["asset_cat"] = category if category in cat_options else default_vals["asset_cat"]
            location = str(row.get("location_type", "Onshore"))
            st.session_state["location_type_in"] = location if location in loc_options else "Onshore"
            st.session_state["oil_prod_in"] = _num(row, "oil_production_bpd", 12500.0, lo=0.0)
            st.session_state["flaring_auth_in"] = _to_bool(row.get("flaring_authorized", False), False)
            st.session_state["is_deep_in"] = _to_bool(row.get("is_deep_offshore", False), False)
            st.session_state["h2s_in"] = _num(row, "h2s_percent", 0.0, lo=0.0, hi=100.0)
            st.session_state["co2_in"] = _num(row, "co2_percent", 0.0, lo=0.0, hi=100.0)
            st.session_state["h2o_in"] = _num(row, "h2o_percent", 0.0, lo=0.0, hi=100.0)


with col_select:
    preset_options = ["Manual Input Only"]
    if not db_df.empty and "facility_name" in db_df.columns:
        preset_options += db_df["facility_name"].tolist()
    st.selectbox("📂 Load Profile Template Target", preset_options, key="preset_selector", on_change=on_preset_change)

st.markdown("---")

# --- SIDEBAR INPUTS ---
st.sidebar.header("🎛️ Asset Parameters")
asset_cat = st.sidebar.selectbox("Asset Category", cat_options, key="asset_cat", format_func=pretty)

flare = st.sidebar.number_input("Flare Volume (MMSCFD)", min_value=0.0, key="flare_in")

st.sidebar.subheader("Gas & Field Characteristics")
c1 = st.sidebar.slider("Methane (C1) %", 50.0, 100.0, key="c1_in")
c3c4 = st.sidebar.slider("LPG/NGL (C3+) %", 0.0, 20.0, key="c3c4_in")
wobbe = st.sidebar.number_input("Wobbe Index", min_value=30.0, key="wobbe_in")
if c1 + c3c4 > 100.0:
    st.sidebar.warning("Methane + C3+ exceeds 100%. C3+ has been capped for the calculations.")
c3c4_eff = max(0.0, min(c3c4, 100.0 - c1))

with st.sidebar.expander("Gas quality (mol %): sets pretreatment"):
    st.caption("Leave at 0 for sweet, dry gas. Anything above a technology's limit (World Bank Table 7) adds "
               "pretreatment CAPEX and OPEX to that route.")
    h2s_pct = st.number_input("H2S (mol %)", min_value=0.0, max_value=100.0, step=0.01, format="%.2f", key="h2s_in")
    co2_pct = st.number_input("CO2 (mol %)", min_value=0.0, max_value=100.0, step=0.1, format="%.2f", key="co2_in")
    h2o_pct = st.number_input("H2O (mol %)", min_value=0.0, max_value=100.0, step=0.01, format="%.2f", key="h2o_in")

location_type = st.sidebar.selectbox("Location Type", loc_options, key="location_type_in")

oil_prod = st.sidebar.number_input("Oil Production (BPD)", min_value=0.0, key="oil_prod_in")
flaring_auth = st.sidebar.checkbox("Flaring Authorized by NUPRC", key="flaring_auth_in")
is_deep = st.sidebar.checkbox("Deep Offshore Asset", key="is_deep_in")

st.sidebar.subheader("Market & Infrastructure")
dist_net = st.sidebar.number_input("Distance to Gas Network (km)", min_value=0.0, key="dist_net_in")
dist_mkt = st.sidebar.number_input("Distance to Trucking Market (km)", min_value=0.0, key="dist_mkt_in")
power_demand = st.sidebar.number_input("Local Power Demand (MW)", min_value=0.0, key="power_in")
colloc_demand = st.sidebar.number_input("Collocated Offtaker Demand (MMSCFD)", min_value=0.0, key="colloc_in")
road_access = st.sidebar.checkbox("Road / Logistics Access", key="road_in")
reinjection = st.sidebar.checkbox("Reservoir Suitable for Reinjection", key="reinj_in")

st.sidebar.subheader("Deal terms")
penalty_share_pct = st.sidebar.slider(
    "Avoided penalty shared with developer (%)", 0, 100, 0, key="share_in",
    help="Option B only. The operator hands this share of the flare penalty it no longer pays to the developer. "
         "At 0% the developer lives on gas sales alone. The verdict shows the share that would make the deal work.")
case_label = st.sidebar.radio("Case shown on tabs 2 and 3", ["Pessimistic", "Base", "Optimistic"], index=1, key="case_in",
                              help="Pessimistic = high CAPEX/OPEX, low prices, slower build. Optimistic = the reverse. "
                                   "Ranges are the Low/High cases of the World Bank GGFR report.")
case_key = case_label.lower()

with st.sidebar.expander("⚙️ Model assumptions (editable)"):
    st.caption("Costs and prices come from the World Bank GGFR small-scale AG report. Replace them with supplier "
               "quotes and offtake terms before relying on the outputs.")
    region = st.selectbox("Region (CAPEX factor and discount rate)", list(RD.REGIONS), key="region_in",
                          index=list(RD.REGIONS).index(RD.DEFAULT_REGION))
    reg = RD.REGIONS[region]
    st.caption(f"Table 12: CAPEX factor ×{reg['capex_factor']:.2f}, discount rate {reg['discount_rate'] * 100:.0f}%.")
    discount_override = st.number_input("Discount rate override (%, 0 = regional)", min_value=0.0, max_value=40.0,
                                        value=0.0, step=0.5, key="disc_in")
    profile = st.selectbox("Associated-gas production profile", list(RD.PRODUCTION_PROFILES), key="profile_in",
                           help="Table 4 shapes, rescaled so the average equals the recoverable volume entered.")
    sizing = st.selectbox("Plant sizing strategy", list(RD.SIZING_STRATEGIES), key="sizing_in",
                          help="Table 17: sizing to the average rate gives better economics than sizing to the peak. "
                               "Only matters with a non-flat profile.")
    build_months = st.slider("Build / commissioning time (months)", 6, 24, RD.BUILD_MONTHS["typical"], step=3, key="build_in",
                             help="Report: 6, 12 or 18 months. Pessimistic adds 6 months, optimistic removes 6.")
    recovery_pct = st.slider("Gas recovery factor (%)", 50, 100, int(DEFAULT_RECOVERY_FACTOR * 100))
    dry_gas_price = st.number_input("Dry-gas contract price ($/MSCF, 0 = report range)", min_value=0.0, max_value=30.0,
                                    value=0.0, step=0.10, key="dry_in",
                                    help=f"Report netback is ${RD.NETBACK['dry_gas_usd_mscf'][0]:.2f} to "
                                         f"${RD.NETBACK['dry_gas_usd_mscf'][1]:.2f} per MSCF. Applies to pipeline, "
                                         "collocated and GPP dry-gas sales.")
    host_fee = st.number_input("Host fee to operator ($/MSCF)", min_value=0.0, max_value=3.0, value=0.40, step=0.05, key="host_in")
    penalty_override = st.number_input(
        "Penalty rate override ($/MSCF, 0 = published tier)", min_value=0.0, max_value=20.0, value=0.0, step=0.25,
        help=(f"Published tiers: ${PENALTY_RATE_HIGH:.2f}/MSCF at or above {HIGH_PRODUCER_THRESHOLD_BPD:,} bpd and "
              f"${PENALTY_RATE_LOW:.2f}/MSCF below (Flare Gas (Prevention of Waste and Pollution) Regulations 2018). "
              "Enter a different rate only if you hold a source for it."))
    flare_eff_pct = st.slider("Flare combustion efficiency (%)", 85.0, 99.5, 98.0, step=0.5)
    leakage_pct = st.slider("Project methane leakage (% of gas handled)", 0.0, 3.0, 0.5, step=0.1)
    st.markdown("**Bankability gate**")
    min_route_score = st.slider("Minimum route score", 20, 80, int(F.THRESHOLDS["min_route_score"]))
    dev_hurdle = st.slider("Developer base-case IRR hurdle (%)", 10, 40, int(F.THRESHOLDS["developer_hurdle_irr_pct"]))
    stress_floor = st.slider("Developer pessimistic-case IRR floor (%)", 0, 30, int(F.THRESHOLDS["stress_floor_irr_pct"]))
    max_share = st.slider("Most penalty the operator will share (%)", 10, 100, int(F.THRESHOLDS["max_penalty_share_pct"]))

# --- BACKEND COMPUTATIONS ---
recovery_factor = recovery_pct / 100.0
vol_data = calculate_recoverable_volumes(flare, asset_cat, recovery_factor)

mole_fractions = build_mole_fractions(c1, c3c4_eff)
comp_data = calculate_component_volumes(vol_data["daily_recoverable_mmscfd"], mole_fractions)

facility_data = {
    "design_capacity_mmscfd": vol_data["design_capacity_mmscfd"],
    "flare_mmscfd": flare,
    "c1_percent": c1,
    "c3_c4_percent": c3c4_eff,
    "wobbe_index": wobbe,
    "distance_to_network_km": dist_net,
    "distance_to_market_km": dist_mkt,
    "power_demand_mw": power_demand,
    "collocated_demand_mmscfd": colloc_demand,
    "road_access": road_access,
    "reinjection_suitable": reinjection,
    "asset_category": asset_cat,
    "location_type": location_type,
    "oil_production_bpd": oil_prod,
    "flaring_authorized": flaring_auth,
    "is_deep_offshore": is_deep,
    "gpm_yields": comp_data["gpm_yields"],
}

ranked_routes = evaluate_routes(facility_data)

settings = A.Settings(
    recoverable_mmscfd=vol_data["daily_recoverable_mmscfd"],
    region=region, profile=profile, sizing=sizing, build_months=int(build_months),
    flaring_days=vol_data["flaring_days_per_year"],
    oil_production_bpd=oil_prod, flaring_authorized=flaring_auth, is_deep_offshore=is_deep,
    penalty_rate_override=penalty_override if penalty_override > 0 else None,
    dry_gas_price_usd_mscf=dry_gas_price if dry_gas_price > 0 else None,
    penalty_share=penalty_share_pct / 100.0, host_fee_usd_mscf=host_fee,
    discount_rate=discount_override / 100.0 if discount_override > 0 else None,
    power_demand_mw=power_demand, collocated_demand_mmscfd=colloc_demand, distance_to_network_km=dist_net,
    gpm_yields=comp_data["gpm_yields"],
    quality={"h2s": h2s_pct, "co2": co2_pct, "h2o": h2o_pct, "c3plus": c3c4_eff},
    thresholds={"min_route_score": float(min_route_score), "developer_hurdle_irr_pct": float(dev_hurdle),
                "stress_floor_irr_pct": float(stress_floor), "max_penalty_share_pct": float(max_share)},
)
analyses = A.analyse_routes(ranked_routes, settings)
recommendation = A.select_recommendation(ranked_routes, analyses, reinjection)
top_route = recommendation["primary"]
fallback_route = recommendation["fallback"]
top = analyses[top_route["route_name"]]

if not top["available"]:
    st.error(f"### ⛔ NOT BANKABLE AS SCOPED: no route can be priced ({top.get('reason', 'no recoverable gas')})")
    st.caption("Enter a flare volume above zero and a route that is not ruled out by a fatal flaw.")
    st.stop()

verdict = top["verdict"]
verdict_name = verdict["verdict"]
ev = top["evals"][case_key]
fin_c = top["fins"][case_key]
plan_c = top["plans"][case_key]
cplan = top["costing"][case_key]
rate_label = f"{fin_c.discount_rate * 100:.0f}%"
build_years = plan_c.build_years
penalty_rate = plan_c.penalty_rate
sens = F.sensitivity_table(plan_c, fin_c)

truckloads = cplan["capacity_mmscfd"] * 1000.0 / TRAILER_CAPACITY_MSCF
risks = generate_risk_matrix(
    asset_cat, top_route["route_name"],
    context={"truckloads_per_day": truckloads, "distance_to_market_km": dist_mkt,
             "residual_flare_mmscfd": vol_data["residual_flare_mmscfd"], "build_years": round(build_years, 1),
             "flaring_authorized": flaring_auth},
)
emissions = calculate_emissions_abatement(
    vol_data["annual_recoverable_mmscf"], mole_fractions,
    flare_combustion_efficiency=flare_eff_pct / 100.0,
    project_leakage_rate=leakage_pct / 100.0,
)
route_emis = route_operating_emissions(top_route["route_name"], vol_data["annual_recoverable_mmscf"], mole_fractions,
                                       dist_mkt, TRAILER_CAPACITY_MSCF, RD.CNG_FUEL_FRACTION)
carbon_price_low = implied_carbon_price(PENALTY_RATE_LOW, mole_fractions)
carbon_price_high = implied_carbon_price(PENALTY_RATE_HIGH, mole_fractions)
carbon_price_now = implied_carbon_price(penalty_rate, mole_fractions)


def fmt_share(x):
    return "not reachable" if x is None else f"{x * 100:.0f}%"


# --- BANKABILITY VERDICT (visible on every tab) ---
if verdict_name == "BANKABLE":
    st.success(f"### ✅ BANKABLE: {top_route['label']} clears all {len(verdict['checks'])} checks")
elif verdict_name == "MARGINAL":
    st.warning(f"### ⚠️ MARGINAL: {top_route['label']} clears the base case but not the pessimistic case")
elif verdict_name == "CONDITIONALLY BANKABLE":
    st.warning(f"### 🤝 CONDITIONALLY BANKABLE: {top_route['label']} works if the developer receives about "
               f"{fmt_share(verdict['required_share'])} of the avoided flare penalty (offered: {penalty_share_pct}%)")
else:
    st.error(f"### ⛔ NOT BANKABLE AS SCOPED: {top_route['label']} does not clear the investor gate")
with st.expander("See the checks behind this verdict"):
    for chk in verdict["checks"]:
        st.markdown(f"{'✅' if chk['passed'] else '❌'} **{chk['name']}:** {esc(chk['detail'])}")
    st.markdown(f"**Penalty share that lifts the base case to the hurdle:** {fmt_share(verdict['breakeven_share_base'])}  \n"
                f"**Penalty share that lifts the pessimistic case to the floor:** {fmt_share(verdict['breakeven_share_pessimistic'])}  \n"
                f"**Most the operator is willing to share:** {max_share}%")
    for warn in verdict["warnings"]:
        st.markdown(f"⚠️ {esc(warn)}")
    st.caption("Thresholds are editable in the sidebar under Model assumptions.")

# ==========================================
# EXECUTIVE TAB WORKFLOW
# ==========================================
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Asset Intelligence & Route Ranking",
    "💰 Financial & Commercial Structuring",
    "🌪️ Risk & Sensitivity Analysis",
    "🏛️ Boardroom Action Directives"
])

# --- TAB 1: ROUTE RANKING ---
with tab1:
    st.header("Section 1: Stranded Asset Intelligence & Route Ranking", divider="blue")

    if dist_net <= 15:
        grid_note = "(Within tie-in range)"
    elif dist_net <= 50:
        grid_note = "(Marginal tie-in)"
    else:
        grid_note = "(Stranded)"

    p1, p2, p3 = st.columns(3)
    p1.markdown("Baseline Condition")
    p1.markdown(f"#### {pretty(asset_cat)}")
    p1.metric("Operating Flaring Days/Yr", vol_data["flaring_days_per_year"])
    p2.metric("Daily Recoverable Gas", f"{vol_data['daily_recoverable_mmscfd']:.2f} MMSCFD")
    p2.metric("Residual Flare (not captured)", f"{vol_data['residual_flare_mmscfd']:.2f} MMSCFD")
    p3.metric("Methane (C1) Purity", f"{c1:.1f}%")
    p3.metric("Distance to Grid Tie-in", f"{dist_net:g} km {grid_note}")
    q1, q2, q3 = st.columns(3)
    q1.metric("LPG Liquids Potential", f"{comp_data['lpg_liquids_gpd']:,.0f} GPD",
              help=f"C3+ yield {comp_data['gpm_yields'].get('Total_C3+', 0):.2f} GPM, assuming a 60:40 propane to butane+ split.")
    q2.metric("Plant capacity (this route)", f"{cplan['capacity_mmscfd']:.2f} MMSCFD",
              help="Sized by the strategy chosen in the sidebar and capped by what a power load or off-taker can absorb.")

    st.markdown("---")
    st.subheader("Algorithmic Route Viability Mapping")
    route_df = pd.DataFrame(ranked_routes)
    fig = px.bar(
        route_df, x="label", y="score", range_y=[0, 100],
        title="Comparative Feasibility Score Mapping", color="score",
        color_continuous_scale=["#FF4B4B", "#F1C40F", "#2ECC71"], text="score",
        labels={"label": "Route", "score": "Score"},
    )
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, **CHART_KW)

    st.success(f"### 🏆 Primary Recommendation: **{top_route['label']}**")
    st.caption("Chosen on bankability first (verdict, then base-case developer IRR, then score), among routes scoring "
               f"at least {A.MIN_RECOMMEND_SCORE:.0f}.")
    st.write(f"**Justification:** {top_route.get('justification') or 'High commercial alignment with field parameters.'}")
    st.write(f"**Required Infrastructure:** {', '.join(top_route['infrastructure'])}")
    st.write(f"**CapEx Profile:** {top_route['capex_profile']}")
    cap_line = f"**Indicative CAPEX ({case_label.lower()} case):** {fmt_m(cplan['capex_usd'])}"
    if cplan["capacity_mmscfd"] > 0:
        cap_line += f" (about ${cplan['capex_usd'] / 1e6 / cplan['capacity_mmscfd']:.2f}M per MMSCFD installed)"
    st.write(esc(cap_line))
    for note in cplan["notes"]:
        st.caption(esc(note))
    if cplan["benchmark_source"] == "placeholder":
        st.warning("This route is not costed in the World Bank report. Its CAPEX and OPEX are placeholder benchmarks; "
                   "get a quote before relying on them.")
    for flag in top_route["flags"]:
        st.warning(flag)
    if fallback_route is not None:
        st.info(f"**Compliance fallback:** {fallback_route['label']} (score {fallback_route['score']:.0f}). "
                "It removes flaring but earns no gas revenue, so it is shown separately from the commercial ranking.")

    # ---- every route, priced ----
    st.subheader("Every route, priced")
    price_rows = []
    for r in ranked_routes:
        a = analyses[r["route_name"]]
        row = {"Route": r["label"], "Score": r["score"]}
        if a["available"]:
            e = a["evals"]
            row.update({
                "CAPEX, base ($M)": round(e["base"]["capex"] / 1e6, 1),
                "Developer IRR: pessimistic": fmt_pct(e["pessimistic"]["irr_dev"]),
                "base": fmt_pct(e["base"]["irr_dev"]),
                "optimistic": fmt_pct(e["optimistic"]["irr_dev"]),
                "Penalty share needed": fmt_share(a["shares"]["base"]),
                "Verdict": a["verdict"]["verdict"] if r["revenue_generating"] else "Compliance route",
            })
        else:
            row.update({"CAPEX, base ($M)": None, "Developer IRR: pessimistic": "n/a", "base": "n/a",
                        "optimistic": "n/a", "Penalty share needed": "n/a", "Verdict": a.get("reason", "n/a")})
        price_rows.append(row)
    st.dataframe(pd.DataFrame(price_rows), hide_index=True)
    st.caption("Developer IRR runs on the report's Low/High cases (Table 8-14). 'Penalty share needed' is the part of "
               "the avoided flare penalty the operator would have to pass to the developer for a base-case IRR at the hurdle.")

    # ---- gas quality gate ----
    if cplan["quality_rows"]:
        st.subheader(f"Gas-quality gate for {top_route['label']}")
        st.dataframe(pd.DataFrame(cplan["quality_rows"]), hide_index=True)
        if h2s_pct == 0 and co2_pct == 0 and h2o_pct == 0:
            st.caption("H2S, CO2 and H2O are entered as 0 (sweet, dry gas). Measure them: acid-gas removal costs about "
                       "$0.65 to $0.85M per MMSCFD of installed CAPEX before the regional factor.")

    with st.expander("Why these scores?"):
        table = pd.DataFrame([{
            "Route": r["label"], "Score": r["score"],
            "Revenue route": "Yes" if r["revenue_generating"] else "No (compliance)",
            "Rationale": r["justification"], "Flags": " ".join(r["flags"]),
        } for r in ranked_routes])
        st.dataframe(table, hide_index=True)
        st.caption("Scores are rule-based heuristics. Ties are broken in favour of revenue-generating routes, then lower CAPEX.")

# --- TAB 2: COMMERCIAL STRUCTURING ---
with tab2:
    st.header("Section 2: Commercial Structuring (Option A vs Option B)", divider="blue")
    st.markdown(esc(f"Contrasting direct JV balance sheet funding (Option A) with midstream third-party BOOT outsourcing "
                    f"(Option B). Showing the **{case_label.lower()}** case, {int(round(build_years * 12))}-month build, "
                    f"{rate_label} discount rate ({region})."))

    ev_p, ev_o = top["evals"]["pessimistic"], top["evals"]["optimistic"]
    col_opt_a, col_opt_b = st.columns(2)

    with col_opt_a:
        with st.container(border=True):
            st.subheader("Option A: Internal JV Funding")
            st.caption("100% Renaissance Funded | Full Revenue Retention")
            st.metric("Renaissance CAPEX", fmt_m(ev["capex"]))
            st.metric(f"Project NPV ({rate_label})", fmt_m(ev["npv_a"]),
                      help=f"Range: {fmt_m(ev_p['npv_a'])} (pessimistic) to {fmt_m(ev_o['npv_a'])} (optimistic).")
            st.metric("Internal IRR", fmt_pct(ev["irr_a"]),
                      help=f"Range: {fmt_pct(ev_p['irr_a'])} to {fmt_pct(ev_o['irr_a'])}.")
            st.metric("Payback Period", fmt_years(ev["payback_a"]), help="From the start of construction.")

    with col_opt_b:
        with st.container(border=True):
            st.subheader("Option B: Midstream BOOT Outsourcing")
            st.caption("0% Renaissance CAPEX | Third-Party Financed | No CAPEX exposure")
            st.metric("Renaissance CAPEX", "$0.00M", delta="100% Capital Saved", delta_color="normal")
            st.metric(f"NPV ({rate_label}) to Renaissance", fmt_m(ev["npv_b"]),
                      help=f"Host fee after tax plus the penalty saved, less the {penalty_share_pct}% shared with the developer. "
                           f"Range: {fmt_m(ev_p['npv_b'])} to {fmt_m(ev_o['npv_b'])}.")
            st.metric("Annual Value Delivered (peak year)", f"{fmt_m(ev['peak_annual_cf_b'])}/yr")
            st.metric("First Benefit", f"After {int(round(build_years * 12))}-month build", delta="No capital exposed", delta_color="off")

    peak_pen = ev["peak_annual_avoided_penalty"]
    kept_pen = peak_pen * (1.0 - fin_c.penalty_share)
    pen_pct = (kept_pen / ev["peak_annual_cf_b"] * 100.0) if ev["peak_annual_cf_b"] > 0 else 0.0
    st.info(esc(
        f"**Where Option B's value comes from:** {pen_pct:.0f}% is NUPRC penalty saved "
        f"(about {fmt_m(kept_pen)}/yr kept at ${penalty_rate:.2f}/MSCF, after sharing {penalty_share_pct}% with the developer). "
        f"The rest is the host fee. The developer's own case has to stand on product sales plus whatever penalty share it is offered."
    ))

    with st.container(border=True):
        st.subheader("Developer view: what the EOI has to satisfy")
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Developer CAPEX", fmt_m(ev["capex"]))
        d2.metric("Peak annual cash flow (after tax)", fmt_m(ev["peak_annual_dev_cf"]))
        d3.metric("Developer IRR", fmt_pct(ev["irr_dev"]))
        d4.metric("Developer payback", fmt_years(ev["payback_dev"]))
        r1, r2, r3 = st.columns(3)
        r1.metric("IRR, pessimistic", fmt_pct(ev_p["irr_dev"]))
        r2.metric("IRR, base", fmt_pct(top["evals"]["base"]["irr_dev"]))
        r3.metric("IRR, optimistic", fmt_pct(ev_o["irr_dev"]))
        st.caption("Developer pays the host fee and royalty, is taxed at CIT only (midstream), and receives the penalty share. "
                   "Hurdle and floor are set in the sidebar.")

        be_base = verdict["breakeven_share_base"]
        if be_base is None:
            st.error("Even handing over 100% of the avoided penalty does not lift the developer to the hurdle. "
                     "The route needs lower costs, better offtake pricing, or a different route.")
        elif be_base == 0.0:
            st.success("The developer clears the hurdle on gas sales alone: no penalty sharing needed.")
        else:
            st.info(f"To reach the {dev_hurdle}% hurdle the developer needs about **{be_base * 100:.0f}%** of the avoided penalty "
                    f"(you offer {penalty_share_pct}%). That leaves the operator "
                    f"{(1 - be_base) * 100:.0f}% of the penalty plus the host fee, and still $0 CAPEX.")

        cf_fig = go.Figure()
        for name, e_, color in (("Pessimistic", ev_p, "#FF4B4B"), ("Base", top["evals"]["base"], "#F1C40F"),
                                ("Optimistic", ev_o, "#2ECC71")):
            cum, running = [], 0.0
            for f_ in e_["flows_dev"]:
                running += f_
                cum.append(running / 1e6)
            cf_fig.add_trace(go.Scatter(x=e_["years"], y=cum, mode="lines+markers", name=name, line=dict(color=color)))
        cf_fig.update_layout(title="<b>Developer cumulative cash flow ($M)</b>", xaxis_title="Project year (0 = construction start)",
                             yaxis_title="$ millions", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        cf_fig.add_hline(y=0, line_color="gray")
        st.plotly_chart(cf_fig, **CHART_KW)

        if cplan["revenue_breakdown"]:
            st.markdown("**Product revenue at the plant's steady throughput (this case)**")
            steady_mmscf = max(ev["throughput_mmscf"]) if ev["throughput_mmscf"] else 0.0
            rev_rows = [{"Product": k, "Revenue ($M/yr)": round(v * steady_mmscf / 1e6, 2)}
                        for k, v in cplan["revenue_breakdown"].items()]
            st.dataframe(pd.DataFrame(rev_rows), hide_index=True)

# --- TAB 3: RISK & SENSITIVITY ---
with tab3:
    st.header("Section 3: Risk Assessment & Sensitivity Bounds", divider="blue")

    base_npv_m = ev["npv_a"] / 1e6
    labels = [s["label"] for s in sens]

    t_fig = go.Figure()
    t_fig.add_trace(go.Bar(
        y=labels, x=[s["adverse_delta_usd"] / 1e6 for s in sens], name="Adverse case", orientation="h",
        marker=dict(color="#FF4B4B"), hoverinfo="text",
        hovertext=[f"{s['adverse_text']}: {s['adverse_delta_usd'] / 1e6:+.2f}M NPV" for s in sens]))
    t_fig.add_trace(go.Bar(
        y=labels, x=[s["favorable_delta_usd"] / 1e6 for s in sens], name="Favorable case", orientation="h",
        marker=dict(color="#2ECC71"), hoverinfo="text",
        hovertext=[f"{s['favorable_text']}: {s['favorable_delta_usd'] / 1e6:+.2f}M NPV" for s in sens]))
    t_fig.update_layout(
        title=f"<b>Option A NPV sensitivity at {rate_label} ({case_label.lower()} case, baseline: ${base_npv_m:.2f}M)</b>",
        barmode="relative", bargap=0.3,
        xaxis=dict(title="NPV change ($ millions USD)", zeroline=True, zerolinecolor="gray"),
        yaxis=dict(autorange="reversed"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(t_fig, **CHART_KW)
    st.caption("Bars are coloured by outcome: red lowers NPV, green raises it. Each bar changes one input at a time.")

    sens_df = pd.DataFrame([{
        "Input": s["label"],
        "NPV change, adverse ($M)": round(s["adverse_delta_usd"] / 1e6, 2),
        "NPV change, favorable ($M)": round(s["favorable_delta_usd"] / 1e6, 2),
        "Developer IRR, adverse": fmt_pct(s["dev_irr_adverse"]),
        "Developer IRR, favorable": fmt_pct(s["dev_irr_favorable"]),
    } for s in sens])
    st.dataframe(sens_df, hide_index=True)

    st.markdown("#### Dynamic Risk Assessment Matrix")
    st.table(pd.DataFrame(risks))

# --- TAB 4: BOARDROOM DIRECTIVES ---
with tab4:
    st.header("Section 4: Executive Directives & ESG Profile", divider="blue")

    if asset_cat == "Non-AG_Forfeiture_Risk":
        if verdict_name == "NOT BANKABLE":
            directive_title = "NOT YET BANKABLE: RESOLVE THE GAPS BEFORE ISSUING AN EOI"
            action_steps = [f"**{chk['name']}:** {chk['detail']}" for chk in verdict["failed"]]
            action_steps.append("**Validate inputs:** Replace benchmark assumptions with measured gas quality, supplier quotes and offtake terms, then re-run.")
        elif verdict_name == "CONDITIONALLY BANKABLE":
            directive_title = "ISSUE THE EOI WITH A PENALTY-SHARING TERM"
            action_steps = [
                f"**Offer a penalty-sharing term:** the developer needs about {fmt_share(verdict['required_share'])} of the avoided flare "
                f"penalty to reach a {dev_hurdle}% IRR. Put a range around that figure in the EOI and let bidders compete on it.",
                "**Capital Preservation:** Option B keeps Renaissance CAPEX at $0 while the operator still keeps the rest of the penalty saving and the host fee.",
                "**Regulatory Filing:** Submit the commercialization pathway plan to NUPRC and request the treatment of penalty accruals during development (subject to NUPRC confirmation).",
                "**Test the price:** ask bidders for a firm offtake price. The result turns on it (see the optimistic and pessimistic IRRs).",
            ]
        else:
            directive_title = "EXECUTE THIRD-PARTY MIDSTREAM OUTSOURCING (BOOT)"
            action_steps = [
                "**Issue Expression of Interest (EOI):** Release a midstream developer tender within 30 days to start the path to compliance and reduce forfeiture exposure.",
                "**Capital Preservation:** Approve the $0 CAPEX structure (Option B) to protect the balance sheet while pursuing compliance.",
                "**Regulatory Filing:** Submit the commercialization pathway plan to NUPRC and request the treatment of penalty accruals during development (subject to NUPRC confirmation).",
            ]
            if verdict_name == "MARGINAL":
                action_steps.append("**Before the EOI goes out:** the pessimistic case fails. Ask bidders to price cost and offtake risk, or add a price floor.")
    elif asset_cat == "Active_AG_Upset_Audit":
        directive_title = "OPERATIONAL UPSET RELIABILITY AUDIT"
        action_steps = [
            "**Compressor Reliability Audit:** Review trip frequencies and buy-back backpressure constraints.",
            "**Auto-Turndown Control Valves:** Install automated control systems to manage operational flare spikes.",
            "**Gas Swap Agreement:** Establish off-take relief agreements with adjacent pipeline operators."
        ]
    else:
        directive_title = "HISTORICAL BENCHMARK MONITORING"
        action_steps = ["Asset already transferred under NGFCP. Retain for comparative evaluation."]

    with st.container(border=True):
        st.subheader(f"🎯 Recommended Board Action: {directive_title}")
        st.markdown(f"**Target Route:** `{top_route['label']}`")
        st.markdown(f"**Asset Profile:** `{pretty(asset_cat)}`")

        st.markdown("#### Immediate Execution Directives")
        for step in action_steps:
            st.markdown(f"- {esc(step)}")

    st.markdown("#### ESG & Decarbonization Profile")
    col1, col2, col3 = st.columns(3)
    col1.metric(
        "Net CO2e Abated", f"{emissions['net_abatement_tco2e'] / 1000:,.1f}k t/yr",
        help=(f"Flare basis: unburned methane avoided ({100 - flare_eff_pct:.1f}% flare slip minus {leakage_pct:.1f}% project "
              f"leakage), GWP 28. For context, the same gas vented unburned would be about "
              f"{emissions['vent_equivalent_tco2e'] / 1000:,.0f}k tCO2e/yr, and burning it emits about "
              f"{emissions['flare_combustion_co2_t'] / 1000:,.0f}k tCO2/yr either way. Displaced-fuel credits are not included."))
    col2.metric(
        "NUPRC Penalty Avoided (peak year)", f"{fmt_m(ev['peak_annual_avoided_penalty'])}/yr",
        help=(f"${penalty_rate:.2f}/MSCF on recovered gas "
              + ("(user override)." if penalty_override > 0 else
                 f"(published tier for {'≥' if oil_prod >= HIGH_PRODUCER_THRESHOLD_BPD else '<'}{HIGH_PRODUCER_THRESHOLD_BPD:,} bpd).")))
    col3.metric("Flare Reduction", f"{recovery_pct}%",
                delta=f"{vol_data['residual_flare_mmscfd']:.2f} MMSCFD still flared", delta_color="off")
    c4, c5, c6 = st.columns(3)
    c4.metric("Penalty as a carbon price", f"${carbon_price_now:,.0f}/tCO2",
              help=(f"The flare penalty divided by the CO2 released when this gas is flared. The two published tiers equal "
                    f"${carbon_price_low:,.0f} and ${carbon_price_high:,.0f} per tonne on this gas. Compare with the abatement "
                    "cost per tonne of the route (World Bank report, Table 18)."))
    c5.metric("Route operating emissions", f"{route_emis['total_tco2'] / 1000:,.1f}k tCO2/yr",
              help=(f"Compression fuel {route_emis['compression_fuel_tco2'] / 1000:,.1f}k + truck haulage "
                    f"{route_emis['truck_tco2'] / 1000:,.1f}k tCO2/yr (CNG only; 1 kg CO2 per truck-km is an assumption)."))
    if vol_data["residual_flare_mmscfd"] > 0:
        residual_pen = (vol_data["residual_flare_mmscfd"] * vol_data["flaring_days_per_year"] * 1000.0 * penalty_rate)
        st.caption(esc(f"Residual flare keeps accruing about {fmt_m(residual_pen)}/yr in penalties "
                       "and needs an approved safety or pilot flare. 'Zero flare' is only true at 100% recovery."))
    if not flaring_auth:
        st.warning("Flaring is not authorized. The economics price this at the published penalty tier, but unauthorized "
                   "flaring can carry exposure beyond the penalty (enforcement, asset forfeiture) that the model does not price.")

# --- ASSUMPTIONS & METHODOLOGY ---
with st.expander("📋 Assumptions & methodology"):
    st.markdown(esc(f"""
**Source for costs and prices:** {RD.CITATION}. Installed CAPEX = technology CAPEX (Table 8) plus any pretreatment (Tables 10-11),
times the economy-of-scale factor (Table 13) and the regional factor (Table 12, ×{RD.REGIONS[region]['capex_factor']:.2f} for {region}).
OPEX is per MSCF processed (Table 9). Pessimistic / base / optimistic = High / midpoint / Low cost with Low / midpoint / High netback (Table 14).
**Products:** CNG, LNG and power are sold at the Table 14 netbacks. NGL removal recovers {RD.NGL_REMOVAL_YIELD:.0%} of propane and butane. CNG burns {RD.CNG_FUEL_FRACTION:.0%} of the gas for compression;
LNG converts {RD.LNG_CONVERSION:.0%} of feed; engines use {RD.POWER_MMSCFD_PER_MW} MMSCFD per MW at {RD.POWER_CAPACITY_FACTOR:.0%} availability.
**Pipeline, collocated and reinjection routes** are not in the report and use placeholder CAPEX/OPEX benchmarks. Dry gas is sold at the report's dry-gas netback unless you enter a contract price.
**Penalty:** ${penalty_rate:.2f}/MSCF applied to recovered gas (published tiers: ${PENALTY_RATE_HIGH:.2f} at or above
{HIGH_PRODUCER_THRESHOLD_BPD:,} bpd, ${PENALTY_RATE_LOW:.2f} below).
**Royalty:** 5%. **Tax:** 30% CIT, plus 15% Hydrocarbon Tax on the operator unless deep offshore; tax losses are not carried forward.
**Horizon:** {RD.PROJECT_LIFE_YEARS} years including the build ({int(round(build_years * 12))} months in this case); straight-line depreciation; discount rate {rate_label}.
**Option B:** the operator receives a ${host_fee:.2f}/MSCF host fee plus the penalty saved, less the share paid to the developer ({penalty_share_pct}%); the developer funds CAPEX and OPEX.
**CNG logistics:** {TRAILER_CAPACITY_MSCF} Mscf per trailer; a warning shows above {CNG_LOADS_FLAG} loads/day.
**Emissions:** flare basis, GWP 28; excludes displaced-fuel credits.
**Not modelled:** price escalation, financing costs, working capital, transport of dry gas or NGLs to the buyer, the cost of the residual flare, and cluster projects.
"""))
