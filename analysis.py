"""
analysis.py: ties scoring (route.py), costing (costing.py) and cash flows (finance.py) together.

analyse_routes() prices EVERY viable route, runs the pessimistic / base / optimistic cases, finds the penalty
share that would make the deal work, and issues a bankability verdict per route. select_recommendation()
then picks the route on bankability, not on score alone.
"""
from dataclasses import dataclass, replace
from typing import Optional

import costing as K
import economics as E
import finance as F
import report_data as RD
import route as R


@dataclass(frozen=True)
class Settings:
    recoverable_mmscfd: float
    region: str = RD.DEFAULT_REGION
    profile: str = "Flat (as entered)"
    sizing: str = RD.SIZING_STRATEGIES[0]
    build_months: int = 12
    flaring_days: int = 365
    oil_production_bpd: float = 15000.0
    flaring_authorized: bool = True
    is_deep_offshore: bool = False
    penalty_rate_override: Optional[float] = None
    dry_gas_price_usd_mscf: Optional[float] = None        # contract price overriding the report range
    penalty_share: float = 0.0
    host_fee_usd_mscf: float = 0.40
    royalty_rate: float = 0.05
    discount_rate: Optional[float] = None                 # None = regional rate from Table 12
    power_demand_mw: float = 0.0
    collocated_demand_mmscfd: float = 0.0
    distance_to_network_km: float = 0.0
    gpm_yields: Optional[dict] = None
    quality: Optional[dict] = None                        # h2s, co2, h2o, c3plus (mol %)
    thresholds: Optional[dict] = None


def _build_years(months, scenario):
    delta = {"pessimistic": 6, "base": 0, "optimistic": -6}[scenario]
    return max(3, months + delta) / 12.0


def make_plan(route_name, scenario, s: Settings):
    """Returns (costing plan, finance Plan, Fin) or None when the route can take no gas."""
    shape = K.profile_shape(s.profile)
    cap = K.design_capacity(s.recoverable_mmscfd, shape, s.sizing,
                            K.demand_cap(route_name, s.power_demand_mw, s.collocated_demand_mmscfd))
    if cap <= 0:
        return None
    cplan = K.build_plan(route_name, scenario, cap, region=s.region, gpm_yields=s.gpm_yields or {},
                         quality=s.quality or {}, distance_to_network_km=s.distance_to_network_km,
                         dry_gas_price_usd_mscf=s.dry_gas_price_usd_mscf)
    vols = K.full_year_volumes(s.recoverable_mmscfd, shape, cap, s.flaring_days, cplan["availability"])
    rate = E.get_penalty_rate(s.oil_production_bpd, s.flaring_authorized, s.penalty_rate_override)
    plan = F.Plan(capex=cplan["capex_usd"], opex_per_mmscf=cplan["opex_per_mmscf"],
                  revenue_per_mmscf=cplan["revenue_per_mmscf"], full_year_mmscf=vols,
                  build_years=_build_years(s.build_months, scenario), penalty_rate=rate)
    rate_dr = s.discount_rate if s.discount_rate is not None else RD.REGIONS[s.region]["discount_rate"]
    fin = F.Fin(discount_rate=rate_dr, penalty_share=s.penalty_share, host_fee_usd_mscf=s.host_fee_usd_mscf,
                royalty_rate=s.royalty_rate, is_deep_offshore=s.is_deep_offshore)
    return cplan, plan, fin


def analyse_route(route, s: Settings):
    """Full three-scenario analysis of one scored route (a dict from route.evaluate_routes)."""
    name = route["route_name"]
    out = {"route_name": name, "label": route["label"], "score": route["score"], "available": False}
    if route.get("disqualified"):
        out["reason"] = "Disqualified by a fatal flaw (see justification)."
        return out
    built = {sc: make_plan(name, sc, s) for sc in K.SCENARIOS}
    if built["base"] is None:
        out["reason"] = ("No gas can be absorbed (zero demand or zero recoverable gas)."
                         if name in ("POWER", "COLLOCATED_OFFTAKER") else "No recoverable gas.")
        return out

    cplans = {sc: b[0] for sc, b in built.items()}
    plans = {sc: b[1] for sc, b in built.items()}
    fins = {sc: b[2] for sc, b in built.items()}
    evals = {sc: F.evaluate(plans[sc], fins[sc]) for sc in K.SCENARIOS}
    t = dict(F.THRESHOLDS)
    t.update(s.thresholds or {})
    shares = {
        "base": F.breakeven_penalty_share(plans["base"], fins["base"], t["developer_hurdle_irr_pct"]),
        "pessimistic": F.breakeven_penalty_share(plans["pessimistic"], fins["pessimistic"], t["stress_floor_irr_pct"]),
    }
    verdict = F.assess_bankability(route["score"], route["label"], evals, shares, t,
                                   warnings=list(route.get("flags", [])) + _quality_warnings(cplans["base"]),
                                   current_share=s.penalty_share)
    out.update({"available": True, "plans": plans, "fins": fins, "costing": cplans, "evals": evals,
                "verdict": verdict, "shares": shares})
    return out


def _quality_warnings(cplan):
    w = []
    if cplan["pretreatment"]:
        w.append("Gas quality exceeds the technology limits (Table 7), so this plan includes: "
                 + ", ".join(cplan["pretreatment"]) + ".")
    return w


def analyse_routes(ranked_routes, s: Settings):
    return {r["route_name"]: analyse_route(r, s) for r in ranked_routes}


MIN_RECOMMEND_SCORE = 40.0


def select_recommendation(ranked_routes, analyses, reinjection_suitable=False, min_score=MIN_RECOMMEND_SCORE):
    """
    Primary = the commercial route with the best bankability verdict, then the highest base-case developer
    IRR, then the highest score. Routes scoring below `min_score` are shown but not recommended. If no
    commercial route has economics, fall back to the score order.
    """
    commercial = [r for r in ranked_routes if r["revenue_generating"] and not r.get("disqualified")
                  and r["score"] >= min_score and analyses.get(r["route_name"], {}).get("available")]

    def key(r):
        a = analyses[r["route_name"]]
        irr = a["evals"]["base"]["irr_dev"]
        return (F.VERDICT_RANK[a["verdict"]["verdict"]], -(irr if irr is not None else -1e9), -r["score"])

    if commercial:
        primary = sorted(commercial, key=key)[0]
    else:
        fallback = R.select_recommendation(ranked_routes, reinjection_suitable)
        primary = fallback["primary"]
    fb = None
    if reinjection_suitable:
        fb = next((r for r in ranked_routes if not r["revenue_generating"] and not r.get("disqualified")), None)
    return {"primary": primary, "fallback": fb}
