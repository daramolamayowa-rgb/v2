"""
finance.py: year-by-year cash-flow engine for GASROUTE 2030 v2.

Takes a plan from costing.py and returns, for the same project:
  * Option A: the operator funds and runs the plant
  * Option B: a third-party developer builds, owns and operates (BOOT); the operator gets a host fee and
    keeps the avoided flare penalty less any share handed to the developer
  * the developer's own returns (what a lender or an EOI has to satisfy)

Project life is 10 years INCLUDING construction (as in the report). Penalties, taxes and the penalty-share
mechanism reuse economics.py so the two engines cannot drift apart.

Simplifications: no inflation, no price escalation, no financing costs, tax losses are not carried forward
(conservative for the developer), straight-line depreciation over the operating years.
"""
import math
from dataclasses import dataclass, replace
from typing import Optional, Tuple

import numpy as np
import numpy_financial as npf

import economics as E
import report_data as RD

THRESHOLDS = {
    "min_route_score": 40.0,           # a route must at least be plausible to be recommended
    "developer_hurdle_irr_pct": 20.0,  # developer IRR needed in the base case
    "stress_floor_irr_pct": 12.0,      # developer IRR still needed in the pessimistic case
    "max_penalty_share_pct": 75.0,     # most of the avoided penalty the operator is willing to hand over
}


@dataclass(frozen=True)
class Plan:
    capex: float
    opex_per_mmscf: float
    revenue_per_mmscf: float
    full_year_mmscf: Tuple[float, ...]          # MMSCF per project year if operating all year
    build_years: float                          # may be fractional (6 months = 0.5)
    penalty_rate: float                         # USD per MSCF avoided on every MSCF recovered


@dataclass(frozen=True)
class Fin:
    discount_rate: float = 0.10
    penalty_share: float = 0.0                  # fraction of avoided penalty paid to the developer (Option B)
    host_fee_usd_mscf: float = 0.40
    royalty_rate: float = 0.05
    is_deep_offshore: bool = False
    life_years: int = RD.PROJECT_LIFE_YEARS


def _irr_pct(flows):
    if not any(f < 0 for f in flows) or not any(f > 0 for f in flows):
        return None
    try:
        v = npf.irr(flows)
    except Exception:
        return None
    return None if v is None or np.isnan(v) else float(v) * 100.0


def _payback(flows):
    """Undiscounted payback in years from the start of the project; None if never."""
    cum = 0.0
    for t, f in enumerate(flows):
        prev = cum
        cum += f
        if t > 0 and cum >= 0 > prev and f > 0:
            return (t - 1) + (-prev) / f
    return 0.0 if flows and flows[0] >= 0 else None


def _ops_fraction(year_index, build_years):
    """Share of project year `year_index` (1-based) that the plant operates."""
    return float(min(1.0, max(0.0, year_index - build_years)))


def evaluate(plan: Plan, fin: Fin) -> dict:
    H = int(fin.life_years)
    vols = list(plan.full_year_mmscf[:H]) + [0.0] * max(0, H - len(plan.full_year_mmscf))
    thr = [vols[t - 1] * _ops_fraction(t, plan.build_years) for t in range(1, H + 1)]     # MMSCF actually processed

    op_years = max(1.0, H - math.ceil(plan.build_years))
    dep = plan.capex / op_years if plan.capex > 0 else 0.0

    rev = [v * plan.revenue_per_mmscf for v in thr]
    opex = [v * plan.opex_per_mmscf for v in thr]
    roy = [r * fin.royalty_rate for r in rev]
    avoided = [v * 1000.0 * plan.penalty_rate for v in thr]
    host = [v * 1000.0 * fin.host_fee_usd_mscf for v in thr]
    share = [a * fin.penalty_share for a in avoided]

    def taxes(revenue, costs, depreciation, hydrocarbon):
        return E.calculate_pia_taxes(revenue, costs, depreciation, fin.is_deep_offshore,
                                     hydrocarbon_tax=hydrocarbon)["Total_Tax"]

    # Option A: operator funds, runs, sells and keeps the penalty saving
    cf_a, tax_a = [], []
    for t in range(H):
        tx = taxes(rev[t], opex[t] + roy[t], dep if thr[t] > 0 else 0.0, True)
        tax_a.append(tx)
        cf_a.append(rev[t] + avoided[t] - opex[t] - roy[t] - tx)

    # Option B: host fee (taxed) plus penalty saving less the share paid over
    cf_b, tax_b = [], []
    for t in range(H):
        tx = taxes(host[t], 0.0, 0.0, True)
        tax_b.append(tx)
        cf_b.append(host[t] - tx + avoided[t] - share[t])

    # Developer: sells the product, pays the host fee and royalty, receives the penalty share. CIT only.
    cf_d, tax_d = [], []
    for t in range(H):
        income = rev[t] - host[t] + share[t]
        tx = taxes(income, opex[t] + roy[t], dep if thr[t] > 0 else 0.0, False)
        tax_d.append(tx)
        cf_d.append(income - opex[t] - roy[t] - tx)

    flows_a = [-plan.capex] + cf_a
    flows_b = [0.0] + cf_b
    flows_d = [-plan.capex] + cf_d
    r = fin.discount_rate
    steady = max(cf_d) if cf_d else 0.0

    return {
        "years": list(range(0, H + 1)),
        "throughput_mmscf": thr,
        "total_mmscf": float(sum(thr)),
        "capex": plan.capex,
        "flows_a": flows_a, "flows_b": flows_b, "flows_dev": flows_d,
        "npv_a": float(npf.npv(r, flows_a)), "irr_a": _irr_pct(flows_a), "payback_a": _payback(flows_a),
        "npv_b": float(npf.npv(r, flows_b)),
        "npv_dev": float(npf.npv(r, flows_d)), "irr_dev": _irr_pct(flows_d), "payback_dev": _payback(flows_d),
        "avoided_penalty_total": float(sum(avoided)),
        "host_fee_total": float(sum(host)),
        "penalty_share_total": float(sum(share)),
        "gross_revenue_total": float(sum(rev)),
        "peak_annual_revenue": float(max(rev)) if rev else 0.0,
        "peak_annual_dev_cf": float(steady),
        "peak_annual_avoided_penalty": float(max(avoided)) if avoided else 0.0,
        "peak_annual_host_fee": float(max(host)) if host else 0.0,
        "peak_annual_cf_b": float(max(cf_b)) if cf_b else 0.0,
        "peak_annual_cf_a": float(max(cf_a)) if cf_a else 0.0,
    }


def breakeven_penalty_share(plan: Plan, fin: Fin, target_irr_pct: float) -> Optional[float]:
    """
    Smallest share (0..1) of the avoided flare penalty that, paid to the developer, lifts the developer's
    IRR to `target_irr_pct`. 0.0 if the project already clears it; None if even 100% is not enough.
    """
    def irr_at(s):
        v = evaluate(plan, replace(fin, penalty_share=s))["irr_dev"]
        return -1e9 if v is None else v

    if irr_at(0.0) >= target_irr_pct:
        return 0.0
    if irr_at(1.0) < target_irr_pct:
        return None
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if irr_at(mid) >= target_irr_pct:
            hi = mid
        else:
            lo = mid
    return hi


# ---------------------------------------------------------------------------
# Sensitivities (Option A NPV, full re-run each time)
# ---------------------------------------------------------------------------
def sensitivity_table(plan: Plan, fin: Fin) -> list:
    base = evaluate(plan, fin)
    defs = [
        ("Offtake price", "-20%", lambda p: replace(p, revenue_per_mmscf=p.revenue_per_mmscf * 0.8),
                          "+20%", lambda p: replace(p, revenue_per_mmscf=p.revenue_per_mmscf * 1.2)),
        ("CAPEX", "+50% overrun", lambda p: replace(p, capex=p.capex * 1.5),
                  "-20%", lambda p: replace(p, capex=p.capex * 0.8)),
        ("Recovered volume", "-20%", lambda p: replace(p, full_year_mmscf=tuple(v * 0.8 for v in p.full_year_mmscf)),
                             "+20%", lambda p: replace(p, full_year_mmscf=tuple(v * 1.2 for v in p.full_year_mmscf))),
        ("OPEX", "+20%", lambda p: replace(p, opex_per_mmscf=p.opex_per_mmscf * 1.2),
                 "-20%", lambda p: replace(p, opex_per_mmscf=p.opex_per_mmscf * 0.8)),
        ("Penalty rate", "-25%", lambda p: replace(p, penalty_rate=p.penalty_rate * 0.75),
                         "+25%", lambda p: replace(p, penalty_rate=p.penalty_rate * 1.25)),
        ("Build time", "+6 months", lambda p: replace(p, build_years=p.build_years + 0.5),
                       "-6 months", lambda p: replace(p, build_years=max(0.0, p.build_years - 0.5))),
    ]
    rows = []
    for label, adv_text, adv_fn, fav_text, fav_fn in defs:
        adv, fav = evaluate(adv_fn(plan), fin), evaluate(fav_fn(plan), fin)
        rows.append({
            "label": f"{label} ({adv_text} / {fav_text})",
            "adverse_text": f"{label} {adv_text}", "favorable_text": f"{label} {fav_text}",
            "adverse_delta_usd": adv["npv_a"] - base["npv_a"],
            "favorable_delta_usd": fav["npv_a"] - base["npv_a"],
            "dev_irr_adverse": adv["irr_dev"], "dev_irr_favorable": fav["irr_dev"],
        })
    rows.sort(key=lambda r: abs(r["adverse_delta_usd"]) + abs(r["favorable_delta_usd"]), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Bankability gate
# ---------------------------------------------------------------------------
VERDICT_RANK = {"BANKABLE": 0, "MARGINAL": 1, "CONDITIONALLY BANKABLE": 2, "NOT BANKABLE": 3}


def assess_bankability(route_score, route_label, evals, shares, thresholds=None, warnings=None,
                       current_share=0.0):
    """
    evals:  {"pessimistic": eval, "base": eval, "optimistic": eval}
    shares: {"base": breakeven share for the hurdle in the base case,
             "pessimistic": breakeven share for the stress floor in the pessimistic case}

    BANKABLE                 route fits; developer clears the hurdle (base) and the floor (pessimistic) at the
                             current penalty share
    MARGINAL                 clears the hurdle in the base case but not the floor in the pessimistic case
    CONDITIONALLY BANKABLE   does not clear on its own, but a penalty share within the operator's limit would
                             lift the base case to the hurdle: the deal depends on the penalty-sharing terms
    NOT BANKABLE             anything else, including a route that does not fit the site
    """
    t = dict(THRESHOLDS)
    t.update(thresholds or {})
    cap = t["max_penalty_share_pct"] / 100.0

    base_irr = evals["base"]["irr_dev"]
    pess_irr = evals["pessimistic"]["irr_dev"]

    def fmt(x):
        return "n/a" if x is None else f"{x:.1f}%"

    fits = route_score >= t["min_route_score"]
    base_ok = base_irr is not None and base_irr >= t["developer_hurdle_irr_pct"]
    pess_ok = pess_irr is not None and pess_irr >= t["stress_floor_irr_pct"]

    be = shares.get("base")
    checks = [
        {"name": "Route fit", "passed": fits,
         "detail": f"{route_label} scores {route_score:.0f} (minimum {t['min_route_score']:.0f})"},
        {"name": "Developer base-case IRR", "passed": base_ok,
         "detail": f"{fmt(base_irr)} (hurdle {t['developer_hurdle_irr_pct']:.0f}%)"},
        {"name": "Developer pessimistic-case IRR", "passed": pess_ok,
         "detail": f"{fmt(pess_irr)} (floor {t['stress_floor_irr_pct']:.0f}%) with high costs, low prices and a slower build"},
    ]

    if not fits:
        verdict = "NOT BANKABLE"
    elif base_ok and pess_ok:
        verdict = "BANKABLE"
    elif base_ok:
        verdict = "MARGINAL"
    elif be is not None and be <= cap:
        verdict = "CONDITIONALLY BANKABLE"
    else:
        verdict = "NOT BANKABLE"

    needed = None
    if verdict == "CONDITIONALLY BANKABLE":
        needed = be
    return {
        "verdict": verdict, "checks": checks, "failed": [c for c in checks if not c["passed"]],
        "warnings": list(warnings or []), "thresholds": t,
        "breakeven_share_base": be, "breakeven_share_pessimistic": shares.get("pessimistic"),
        "required_share": needed, "current_share": current_share,
    }
