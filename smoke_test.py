import sys
from streamlit.testing.v1 import AppTest


def fresh():
    at = AppTest.from_file("app.py", default_timeout=60)
    at.run()
    return at


def banner(at):
    for kind in ("success", "warning", "error"):
        for el in getattr(at, kind):
            text = el.value
            if "BANKABLE" in text or "MARGINAL" in text:
                return text.strip().splitlines()[0]
    return None


def check(name, at):
    assert not at.exception, f"{name}: {[e.value for e in at.exception]}"
    print(f"OK  {name:<34} -> {banner(at)}")


at = fresh(); check("default", at)

at.sidebar.selectbox(key="asset_cat").select("Active_AG_Upset_Audit").run(); check("upset category", at)
at.sidebar.selectbox(key="asset_cat").select("NGFCP_Awarded_ThirdParty").run(); check("NGFCP category", at)
at.sidebar.selectbox(key="asset_cat").select("Non-AG_Forfeiture_Risk").run()

at.sidebar.number_input(key="flare_in").set_value(0.0).run(); check("flare = 0", at)
at.sidebar.number_input(key="flare_in").set_value(0.3).run(); check("flare = 0.3 (tiny)", at)
assert "NOT BANKABLE" in banner(at), "a 0.3 MMSCFD flare should fail the gate"
at.sidebar.number_input(key="flare_in").set_value(8.5).run()

at.sidebar.slider(key="c1_in").set_value(95.0).run()
at.sidebar.slider(key="c3c4_in").set_value(10.0).run(); check("C1+C3+ > 100", at)
assert any("exceeds 100" in w.value for w in at.sidebar.warning), "composition warning missing"
at.sidebar.slider(key="c1_in").set_value(88.0).run()
at.sidebar.slider(key="c3c4_in").set_value(4.5).run()

at.sidebar.checkbox(key="road_in").uncheck().run(); check("no road access", at)
at.sidebar.checkbox(key="road_in").check().run()
at.sidebar.checkbox(key="reinj_in").check().run(); check("reinjection suitable", at)
at.sidebar.number_input(key="power_in").set_value(5.0).run(); check("5 MW power demand", at)
at.sidebar.number_input(key="colloc_in").set_value(2.0).run(); check("2 MMSCFD off-taker", at)
at.sidebar.number_input(key="oil_prod_in").set_value(5000.0).run(); check("5,000 bpd (low penalty tier)", at)

for name in ["Ren_NonAG_Urgent_01", "Ren_NonAG_Urgent_02", "Ren_NonAG_Urgent_03", "Hyp_NonAG_Near_Grid",
             "Hyp_NonAG_Swamp_Offtaker", "Hyp_NonAG_Rich_Gas", "Hyp_NonAG_Offshore_Shelf", "Hyp_NonAG_Dead_Flare",
             "Hyp_NonAG_Sour_Gas"]:
    at.selectbox(key="preset_selector").select(name).run()
    check(f"preset: {name}", at)

# preset values really landed in the widgets
at.selectbox(key="preset_selector").select("Hyp_NonAG_Sour_Gas").run()
assert abs(at.sidebar.number_input(key="flare_in").value - 6.0) < 1e-9
assert abs(at.sidebar.number_input(key="h2s_in").value - 0.6) < 1e-9
print("OK  preset values reach the sidebar widgets (incl. gas quality)")

at.selectbox(key="preset_selector").select("Hyp_NonAG_Swamp_Offtaker").run()
assert "COLLOCATED" in banner(at).upper() or "OFF-TAKER" in banner(at).upper(), banner(at)
print("OK  swamp asset recommends the collocated off-taker")

# v2 controls
at = fresh()
at.selectbox(key="preset_selector").select("Ren_NonAG_Urgent_01").run()
assert "CONDITIONALLY BANKABLE" in banner(at), banner(at)
print("OK  Urgent_01 is conditionally bankable")
at.sidebar.slider(key="share_in").set_value(90).run(); check("90% penalty share", at)
assert "CONDITIONALLY" not in banner(at), banner(at)
for case in ["Pessimistic", "Optimistic", "Base"]:
    at.sidebar.radio(key="case_in").set_value(case).run(); check(f"case: {case}", at)
at.sidebar.selectbox(key="region_in").select("Africa (high risk)").run(); check("Africa high risk", at)
at.sidebar.selectbox(key="profile_in").select("C: small-medium, sharp peak").run(); check("profile C", at)
at.sidebar.selectbox(key="sizing_in").select("Maximum rate").run(); check("max sizing", at)
at.sidebar.number_input(key="h2s_in").set_value(0.6).run(); check("H2S 0.6%", at)
at.sidebar.number_input(key="dry_in").set_value(2.0).run(); check("dry gas contract price", at)

# regression: preset whose category equals the widget's ORIGINAL index must still apply
at = fresh()
at.sidebar.selectbox(key="asset_cat").select("Active_AG_Upset_Audit").run()
at.selectbox(key="preset_selector").select("Ren_NonAG_Urgent_01").run()
assert at.sidebar.selectbox(key="asset_cat").value == "Non-AG_Forfeiture_Risk"
print("OK  preset overrides a manual category choice")

print("ALL SMOKE TESTS PASSED")
