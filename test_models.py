"""
Regression tests for the GASROUTE 2030 models. Run with:  python -m pytest -q test_models.py
Each test pins down a bug found in the original code (see CHANGELOG.md).
"""
import pytest

import calculations as C
import economics as E
import route as R


def _facility(**over):
    d = {"design_capacity_mmscfd": 7.2, "flare_mmscfd": 8.5, "c1_percent": 88.0, "wobbe_index": 46.2,
         "distance_to_network_km": 85.0, "distance_to_market_km": 150.0, "power_demand_mw": 0.0,
         "collocated_demand_mmscfd": 0.0, "road_access": True, "reinjection_suitable": True,
         "asset_category": "Non-AG_Forfeiture_Risk", "location_type": "Onshore",
         "gpm_yields": {"Total_C3+": 1.3}}
    d.update(over)
    return d


def _score(ranked, name):
    return next(r["score"] for r in ranked if r["route_name"] == name)


# --- calculations.py ---------------------------------------------------------
def test_gpm_includes_butane_fraction():
    """Original dropped C4 because the app sent 'C4' but the code looked for 'C4+'."""
    gpm = C.calculate_gpm(C.build_mole_fractions(88.0, 4.5))
    assert gpm["C3"] == pytest.approx(0.742, abs=0.001)
    assert gpm["C4+"] == pytest.approx(0.567, abs=0.001)
    assert gpm["Total_C3+"] == pytest.approx(1.309, abs=0.002)


def test_gpm_accepts_either_key_name():
    a = C.calculate_gpm({"C1": 0.88, "C3": 0.027, "C4": 0.018})
    b = C.calculate_gpm({"C1": 0.88, "C3": 0.027, "C4+": 0.018})
    assert a == b


def test_upset_asset_annual_volume_uses_flaring_days():
    v = C.calculate_recoverable_volumes(8.5, "Active_AG_Upset_Audit")
    assert v["annual_recoverable_mmscf"] == pytest.approx(v["daily_recoverable_mmscfd"] * 60)


def test_residual_flare_is_reported():
    v = C.calculate_recoverable_volumes(8.5, "Non-AG_Forfeiture_Risk", recovery_factor=0.85)
    assert v["residual_flare_mmscfd"] == pytest.approx(1.275)


def test_emissions_use_flare_basis():
    v = C.calculate_recoverable_volumes(8.5, "Non-AG_Forfeiture_Risk")
    em = C.calculate_emissions_abatement(v["annual_recoverable_mmscf"], C.build_mole_fractions(88.0, 4.5))
    assert em["vent_equivalent_tco2e"] == pytest.approx(1.246e6, rel=0.01)   # the old headline number
    assert em["net_abatement_tco2e"] < em["vent_equivalent_tco2e"] / 20
    assert em["net_abatement_tco2e"] > 0


def test_risk_matrix_does_not_recommend_lowest_route():
    risks = C.generate_risk_matrix("Non-AG_Forfeiture_Risk", "CNG")
    forfeiture = next(r for r in risks if r["Risk"] == "Asset Forfeiture")
    assert "off-taker" not in forfeiture["Mitigation"].lower()


# --- route.py -----------------------------------------------------------------
def test_power_needs_demand():
    ranked = R.evaluate_routes(_facility(power_demand_mw=0.0))
    assert _score(ranked, "POWER") <= 10


def test_power_score_scales_with_absorbed_gas():
    small = _score(R.evaluate_routes(_facility(power_demand_mw=2.0)), "POWER")
    big = _score(R.evaluate_routes(_facility(power_demand_mw=30.0)), "POWER")
    assert big > small


def test_collocated_score_scales_with_coverage():
    low = _score(R.evaluate_routes(_facility(collocated_demand_mmscfd=0.1)), "COLLOCATED_OFFTAKER")
    high = _score(R.evaluate_routes(_facility(collocated_demand_mmscfd=7.2)), "COLLOCATED_OFFTAKER")
    assert high > low + 40


def test_tie_goes_to_revenue_route_and_reinjection_is_never_primary():
    ranked = R.evaluate_routes(_facility(design_capacity_mmscfd=4.0))   # small enough that CNG is not down-weighted
    assert _score(ranked, "CNG") == _score(ranked, "REINJECTION") == 100
    rec = R.select_recommendation(ranked, reinjection_suitable=True)
    assert rec["primary"]["route_name"] == "CNG"
    assert rec["fallback"]["route_name"] == "REINJECTION"


def test_no_road_disqualifies_trucking_routes():
    ranked = R.evaluate_routes(_facility(road_access=False))
    assert _score(ranked, "CNG") == 0 and _score(ranked, "LNG") == 0 and _score(ranked, "LPG_NGL") == 0


def test_pipeline_capex_grows_with_distance():
    near = R.estimate_capex("GAS_NETWORK_TIE_IN", 5.0, 10.0)
    far = R.estimate_capex("GAS_NETWORK_TIE_IN", 5.0, 85.0)
    assert far > near * 5


# --- economics.py -------------------------------------------------------------
def test_published_penalty_tiers():
    assert E.get_penalty_rate(12500) == 2.00
    assert E.get_penalty_rate(5000) == 0.50
    assert E.get_penalty_rate(5000, rate_override=3.5) == 3.5


def test_authorization_flag_does_not_invent_a_rate():
    assert E.get_penalty_rate(12500, flaring_authorized=False) == E.get_penalty_rate(12500, flaring_authorized=True)


def test_matches_original_numbers_when_new_features_are_off():
    """Original screen: NPV $34.55M, IRR 99.29%, payback 1.01 y, Option B NPV $29.36M, $5.85M/yr."""
    capex = 1_200_000 * 7.22
    e = E.calculate_economics(7.22, capex, capex * 0.08, oil_production_bpd=12500, build_years=0)
    assert e["option_a_internal"]["npv_usd"] / 1e6 == pytest.approx(34.55, abs=0.01)
    assert e["option_a_internal"]["irr_percent"] == pytest.approx(99.29, abs=0.05)
    assert e["option_a_internal"]["payback_years"] == pytest.approx(1.01, abs=0.01)
    assert e["option_b_outsourced"]["npv_usd"] / 1e6 == pytest.approx(29.36, abs=0.01)
    assert e["option_b_outsourced"]["annual_net_benefit_usd"] / 1e6 == pytest.approx(5.85, abs=0.01)


def test_sidebar_inputs_now_reach_the_model():
    kw = dict(annual_volume_mmscf=2635.3, build_years=1)
    high = E.calculate_economics(7.22, 8e6, 6e5, oil_production_bpd=12500, **kw)
    low = E.calculate_economics(7.22, 8e6, 6e5, oil_production_bpd=5000, **kw)
    assert high["flare_penalty_avoided_usd"] > low["flare_penalty_avoided_usd"] * 3


def test_build_period_lowers_value_and_delays_payback():
    now = E.calculate_economics(7.22, 8e6, 6e5, build_years=0)
    later = E.calculate_economics(7.22, 8e6, 6e5, build_years=1)
    assert later["option_a_internal"]["npv_usd"] < now["option_a_internal"]["npv_usd"]
    assert later["option_a_internal"]["payback_years"] > now["option_a_internal"]["payback_years"]


def test_sensitivity_adverse_cases_reduce_npv():
    e = E.calculate_economics(7.22, 8e6, 6e5)
    for s in e["sensitivities"]:
        assert s["adverse_delta_usd"] <= 1e-6
        assert s["favorable_delta_usd"] >= -1e-6


def test_developer_returns_are_computed():
    e = E.calculate_economics(7.22, 8.67e6, 6.9e5)
    assert e["developer"]["irr_percent"] is not None
    assert e["stress_case"]["developer_irr_percent"] < e["developer"]["irr_percent"]


def test_dead_flare_is_not_bankable():
    """Tiny flare, expensive route: the gate has to say no."""
    capex = R.estimate_capex("LNG", 0.3, 0.0)
    e = E.calculate_economics(0.3, capex, capex * 0.08, oil_production_bpd=5000)
    v = E.assess_bankability(60, "Small-scale LNG", e)
    assert v["verdict"] == "NOT BANKABLE"


def test_fixed_costs_make_small_flares_uneconomic():
    """Without fixed costs IRR was identical at 0.3 and 8.5 MMSCFD, so size could never disqualify a flare."""
    def dev_irr(mmscfd):
        capex, opex = R.estimate_costs("CNG", mmscfd * 0.85, 0.0)
        e = E.calculate_economics(mmscfd * 0.85, capex, opex, oil_production_bpd=12500)
        return e["developer"]["irr_percent"], e
    big, _ = dev_irr(8.5)
    small, e_small = dev_irr(0.3)
    assert big is not None and big > 15
    assert small is None or small < big / 2
    assert E.assess_bankability(80, "CNG", e_small)["verdict"] == "NOT BANKABLE"


def test_estimate_costs_zero_volume():
    assert R.estimate_costs("CNG", 0.0, 0.0) == (0.0, 0.0)


def test_zero_volume_does_not_crash():
    e = E.calculate_economics(0.0, 0.0, 0.0)
    v = E.assess_bankability(0, "None", e)
    assert v["verdict"] == "NOT BANKABLE"


# --- v2: report-calibrated costing, finance engine, bankability ---------------------------------------
import analysis as A
import costing as K
import finance as F
import report_data as RD


GPM = {"C3": 0.742, "C4+": 0.567, "Total_C3+": 1.309}
CLEAN = {"h2s": 0, "co2": 0, "h2o": 0, "c3plus": 4.5}


def _plan(route="CNG", scenario="base", cap=7.2, quality=CLEAN, region=RD.DEFAULT_REGION, **kw):
    return K.build_plan(route, scenario, cap, region=region, gpm_yields=GPM, quality=quality, **kw)


def test_cng_capex_matches_hand_calculation():
    """7.2 MMSCFD CNG, Africa normal risk: mid unit CAPEX 1.8 x scale 1.25 (<10) x regional 1.75 x 7.2."""
    assert _plan()["capex_usd"] == pytest.approx(1.8e6 * 1.25 * 1.75 * 7.2, rel=1e-9)


def test_scale_factor_bands():
    assert K.scale_factor("CNG", 0.5) == 2.25 and K.scale_factor("CNG", 4.0) == 1.6
    assert K.scale_factor("CNG", 20.0) == 1.0


def test_scenario_ordering():
    p, b, o = (_plan(scenario=s) for s in K.SCENARIOS)
    assert p["capex_usd"] > b["capex_usd"] > o["capex_usd"]
    assert p["revenue_per_mmscf"] < b["revenue_per_mmscf"] < o["revenue_per_mmscf"]
    assert p["opex_per_mmscf"] > o["opex_per_mmscf"]


def test_gas_quality_gate_adds_pretreatment():
    clean = _plan("CNG")
    sour = _plan("CNG", quality={"h2s": 0.6, "co2": 4.5, "h2o": 0.3, "c3plus": 4.5})
    assert set(sour["pretreatment"]) == {"H2S removal", "CO2 removal", "Dehydration"}
    assert sour["capex_usd"] > clean["capex_usd"] * 1.4
    assert clean["pretreatment"] == []


def test_rich_gas_triggers_ngl_removal_and_liquids_revenue():
    rich = _plan("CNG", quality={"h2s": 0, "co2": 0, "h2o": 0, "c3plus": 8.0})
    assert "NGL removal (C3+)" in rich["pretreatment"]
    assert "Propane" in rich["revenue_breakdown"]


def test_lng_limits_are_stricter_than_cng():
    q = {"h2s": 0, "co2": 0.5, "h2o": 0, "c3plus": 4.5}
    assert "CO2 removal" in _plan("LNG", quality=q)["pretreatment"]
    assert "CO2 removal" not in _plan("CNG", quality=q)["pretreatment"]


def test_gpp_has_no_c3_limit():
    assert _plan("LPG_NGL", quality={"h2s": 0, "co2": 0, "h2o": 0, "c3plus": 14.0})["pretreatment"] == []


def test_demand_caps():
    assert K.demand_cap("POWER", power_demand_mw=10.0) == pytest.approx(3.4)
    assert K.demand_cap("COLLOCATED_OFFTAKER", collocated_demand_mmscfd=2.0) == 2.0
    assert K.demand_cap("CNG") is None


def test_average_sizing_beats_peak_sizing_on_a_peaky_profile():
    """Report Table 17: sizing to the maximum rate wastes CAPEX."""
    shape = K.profile_shape("C: small-medium, sharp peak")
    assert sum(shape) / len(shape) == pytest.approx(1.0)
    assert K.design_capacity(5.0, shape, "Maximum rate") > K.design_capacity(5.0, shape, RD.SIZING_STRATEGIES[0])


def _settings(**kw):
    base = dict(recoverable_mmscfd=7.2, gpm_yields=GPM, quality=CLEAN, oil_production_bpd=12500, power_demand_mw=0.0,
                collocated_demand_mmscfd=0.0, distance_to_network_km=85.0)
    base.update(kw)
    return A.Settings(**base)


def test_finance_flows_and_build_delay():
    plan = A.make_plan("CNG", "base", _settings())[1]
    fin = A.make_plan("CNG", "base", _settings())[2]
    e = F.evaluate(plan, fin)
    assert e["flows_dev"][0] == pytest.approx(-plan.capex)
    assert e["throughput_mmscf"][0] == 0.0           # one-year build: no gas in year 1
    assert e["throughput_mmscf"][1] > 0
    slower = F.evaluate(F.Plan(**{**plan.__dict__, "build_years": 1.5}), fin)
    assert slower["npv_a"] < e["npv_a"]


def test_penalty_share_moves_value_from_operator_to_developer():
    plan, fin = A.make_plan("CNG", "base", _settings())[1:]
    e0 = F.evaluate(plan, fin)
    e5 = F.evaluate(plan, F.Fin(**{**fin.__dict__, "penalty_share": 0.5}))
    assert e5["irr_dev"] > e0["irr_dev"]
    assert e5["npv_b"] < e0["npv_b"]


def test_breakeven_share_hits_the_target():
    plan, fin = A.make_plan("CNG", "base", _settings())[1:]
    share = F.breakeven_penalty_share(plan, fin, 20.0)
    assert share is not None and 0 < share < 1
    irr = F.evaluate(plan, F.Fin(**{**fin.__dict__, "penalty_share": share}))["irr_dev"]
    assert irr == pytest.approx(20.0, abs=0.05)


def test_breakeven_share_unreachable_when_penalty_is_tiny():
    s = _settings(recoverable_mmscfd=3.0, oil_production_bpd=5000)
    plan, fin = A.make_plan("LNG", "base", s)[1:]
    assert F.breakeven_penalty_share(plan, fin, 20.0) is None


def test_sensitivities_have_the_right_signs():
    plan, fin = A.make_plan("CNG", "base", _settings())[1:]
    for row in F.sensitivity_table(plan, fin):
        assert row["adverse_delta_usd"] <= 1e-6 and row["favorable_delta_usd"] >= -1e-6


def _facility_for(**over):
    return _facility(design_capacity_mmscfd=over.pop("design", 7.2), gpm_yields=GPM, **over)


def test_urgent_01_like_asset_is_conditionally_bankable():
    """The headline v2 result: CNG at 8.5 MMSCFD, 12,500 bpd works only with penalty sharing."""
    ranked = R.evaluate_routes(_facility_for())
    a = A.analyse_routes(ranked, _settings())["CNG"]
    assert a["verdict"]["verdict"] == "CONDITIONALLY BANKABLE"
    assert 0.3 < a["verdict"]["required_share"] <= 0.75


def test_tiny_flare_is_not_bankable_in_v2():
    ranked = R.evaluate_routes(_facility_for(design=0.3, flare_mmscfd=0.35))
    s = _settings(recoverable_mmscfd=0.3, oil_production_bpd=5000)
    an = A.analyse_routes(ranked, s)
    rec = A.select_recommendation(ranked, an)
    assert an[rec["primary"]["route_name"]]["verdict"]["verdict"] == "NOT BANKABLE"


def test_zero_gas_does_not_crash():
    ranked = R.evaluate_routes(_facility_for(design=0.0, flare_mmscfd=0.0))
    an = A.analyse_routes(ranked, _settings(recoverable_mmscfd=0.0))
    assert not any(a["available"] for a in an.values())


def test_recommendation_prefers_bankable_route_over_higher_score():
    """Swamp site: off-taker (cheap, sells dry gas) should beat power despite similar scores."""
    f = _facility_for(design=4.25, flare_mmscfd=5.0, road_access=False, collocated_demand_mmscfd=4.2, power_demand_mw=5.0)
    ranked = R.evaluate_routes(f)
    an = A.analyse_routes(ranked, _settings(recoverable_mmscfd=4.25, collocated_demand_mmscfd=4.2, power_demand_mw=5.0))
    assert A.select_recommendation(ranked, an)["primary"]["route_name"] == "COLLOCATED_OFFTAKER"


def test_cng_above_5_mmscfd_is_down_weighted():
    small = _score(R.evaluate_routes(_facility(design_capacity_mmscfd=4.0)), "CNG")
    large = _score(R.evaluate_routes(_facility(design_capacity_mmscfd=12.0)), "CNG")
    assert large < small


def test_implied_carbon_price_matches_published_tiers():
    z = C.build_mole_fractions(88.0, 4.5)
    assert C.implied_carbon_price(2.0, z) == pytest.approx(37.5, abs=1.0)
    assert C.implied_carbon_price(0.5, z) == pytest.approx(9.4, abs=0.5)


def test_route_operating_emissions_only_for_cng_compression():
    z = C.build_mole_fractions(88.0, 4.5)
    assert C.route_operating_emissions("CNG", 2000, z, 150)["compression_fuel_tco2"] > 0
    assert C.route_operating_emissions("LNG", 2000, z, 150)["compression_fuel_tco2"] == 0
