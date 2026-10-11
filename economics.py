"""
economics.py: commercial structuring, developer returns, sensitivities and the
bankability gate for GASROUTE 2030.

Changes vs. the original version are listed in CHANGELOG.md. In short:
  * the penalty uses the published NUPRC tiers; the unverified $3.50 tier is now an optional override
  * annual volume honours flaring days (the original overstated Upset assets 6x)
  * a build period is modelled, so benefits no longer start on Day 1
  * the developer's returns are calculated (they are what an EOI has to satisfy)
  * sensitivities re-run the full model, include wider bounds, and are classified by outcome
  * assess_bankability() turns the numbers into a BANKABLE / MARGINAL / NOT BANKABLE verdict

Values marked PLACEHOLDER are not validated and should be replaced with project numbers.
"""
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
import numpy_financial as npf

# ---------------------------------------------------------------------------
# Penalty tiers: Flare Gas (Prevention of Waste and Pollution) Regulations 2018,
# still the rate cited in April 2026 press coverage of NUPRC penalty collections.
# ---------------------------------------------------------------------------
PENALTY_RATE_HIGH = 2.00             # USD per MSCF, producers at or above the threshold
PENALTY_RATE_LOW = 0.50              # USD per MSCF, producers below the threshold
HIGH_PRODUCER_THRESHOLD_BPD = 10_000

# PLACEHOLDERS for the bankability gate (editable in the app sidebar)
BANKABILITY_DEFAULTS = {
    "min_route_score": 50.0,             # best commercial route must score at least this
    "developer_hurdle_irr_pct": 20.0,    # developer IRR needed in the base case
    "stress_floor_irr_pct": 15.0,        # developer IRR still needed in the stress case
}
# PLACEHOLDER stress case: all applied together
STRESS_CAPEX_MULT = 1.30
STRESS_VOLUME_MULT = 0.80
STRESS_PRICE_MULT = 0.80
STRESS_EXTRA_BUILD_YEARS = 1


def get_penalty_rate(oil_production_bpd, flaring_authorized=True, rate_override=None):
    """
    Published tiers by production level. `flaring_authorized` is accepted for backward
    compatibility but does not change the rate: no published unauthorized-flaring tier
    could be verified. If you hold a source for a different rate, pass it as `rate_override`.
    """
    if rate_override is not None and rate_override > 0:
        return float(rate_override)
    return PENALTY_RATE_HIGH if oil_production_bpd >= HIGH_PRODUCER_THRESHOLD_BPD else PENALTY_RATE_LOW


def calculate_flare_penalty(oil_production_bpd: float, gas_flared_mscf: float,
                            flaring_authorized: bool = True,
                            rate_override: Optional[float] = None) -> float:
    return gas_flared_mscf * get_penalty_rate(oil_production_bpd, flaring_authorized, rate_override)


def calculate_pia_taxes(revenue: float, opex: float, depreciation: float,
                        is_deep_offshore: bool = False, hydrocarbon_tax: bool = True) -> dict:
    """
    Companies Income Tax (CIT) and Hydrocarbon Tax (HT) under the Petroleum Industry Act.
    HT is an upstream tax: pass hydrocarbon_tax=False for a midstream developer.
    """
    taxable_income = max(0.0, revenue - opex - depreciation)

    cit_rate = 0.30
    ht_rate = 0.0 if (is_deep_offshore or not hydrocarbon_tax) else 0.15

    cit_liability = taxable_income * cit_rate
    ht_liability = taxable_income * ht_rate
    return {
        'Taxable_Income': taxable_income,
        'CIT': cit_liability,
        'HT': ht_liability,
        'Total_Tax': cit_liability + ht_liability,
    }


# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Case:
    annual_volume_mmscf: float
    capex: float
    annual_opex: float
    oil_production_bpd: float = 15000.0
    gas_price_usd_mmbtu: float = 2.42
    flaring_authorized: bool = True
    is_deep_offshore: bool = False
    penalty_rate_override: Optional[float] = None
    discount_rate: float = 0.15
    horizon_years: int = 10
    build_years: int = 1
    host_fee_usd_mmbtu: float = 0.40
    royalty_rate: float = 0.05
    heating_value_btu_scf: float = 1000.0


def _irr_pct(flows):
    try:
        value = npf.irr(flows)
    except Exception:
        return None
    if value is None or np.isnan(value):
        return None
    return float(value) * 100.0


def _evaluate(c: Case) -> dict:
    """One full pass of the model for Option A, Option B and the developer."""
    vol_mmscf = c.annual_volume_mmscf
    vol_mscf = vol_mmscf * 1000.0
    annual_mmbtu = vol_mmscf * c.heating_value_btu_scf       # MMscf x Btu/scf = MMBtu
    n, b = int(c.horizon_years), int(c.build_years)

    gross = annual_mmbtu * c.gas_price_usd_mmbtu
    penalty_rate = get_penalty_rate(c.oil_production_bpd, c.flaring_authorized, c.penalty_rate_override)
    avoided_penalty = calculate_flare_penalty(c.oil_production_bpd, vol_mscf, c.flaring_authorized,
                                              c.penalty_rate_override)
    depreciation = c.capex / n if c.capex > 0 else 0.0

    # Option A: Renaissance funds and operates
    royalty = gross * c.royalty_rate
    tax_a = calculate_pia_taxes(gross, c.annual_opex + royalty, depreciation, c.is_deep_offshore)
    cf_a = gross + avoided_penalty - c.annual_opex - royalty - tax_a['Total_Tax']
    flows_a = [-c.capex] + [0.0] * b + [cf_a] * n

    # Option B: third-party BOOT, Renaissance receives a host fee and keeps the penalty saving
    host_fee = annual_mmbtu * c.host_fee_usd_mmbtu
    tax_b = calculate_pia_taxes(host_fee, 0.0, 0.0, c.is_deep_offshore)
    cf_b = host_fee + avoided_penalty - tax_b['Total_Tax']
    flows_b = [0.0] * (b + 1) + [cf_b] * n

    # Developer: funds the plant, sells the gas, pays the host fee and royalty. CIT only (midstream).
    dev_revenue = gross - host_fee
    tax_dev = calculate_pia_taxes(dev_revenue, c.annual_opex + royalty, depreciation,
                                  c.is_deep_offshore, hydrocarbon_tax=False)
    cf_dev = dev_revenue - c.annual_opex - royalty - tax_dev['Total_Tax']
    flows_dev = [-c.capex] + [0.0] * b + [cf_dev] * n

    def payback(cf):
        if c.capex <= 0:
            return 0.0
        return b + c.capex / cf if cf > 0 else None

    return {
        "gross": gross, "avoided_penalty": avoided_penalty, "penalty_rate": penalty_rate,
        "royalty": royalty,
        "cf_a": cf_a, "tax_a": tax_a['Total_Tax'],
        "npv_a": float(npf.npv(c.discount_rate, flows_a)),
        "irr_a": _irr_pct(flows_a) if c.capex > 0 else None,
        "payback_a": payback(cf_a),
        "host_fee": host_fee, "tax_b": tax_b['Total_Tax'], "cf_b": cf_b,
        "npv_b": float(npf.npv(c.discount_rate, flows_b)),
        "cf_dev": cf_dev, "tax_dev": tax_dev['Total_Tax'],
        "npv_dev": float(npf.npv(c.discount_rate, flows_dev)),
        "irr_dev": _irr_pct(flows_dev) if c.capex > 0 else None,
        "payback_dev": payback(cf_dev),
    }


def _sensitivity_table(base: Case, base_eval: dict) -> list:
    base_rate = get_penalty_rate(base.oil_production_bpd, base.flaring_authorized, base.penalty_rate_override)
    defs = [
        ("Gas price", "-20%", lambda c: replace(c, gas_price_usd_mmbtu=c.gas_price_usd_mmbtu * 0.8),
                       "+20%", lambda c: replace(c, gas_price_usd_mmbtu=c.gas_price_usd_mmbtu * 1.2)),
        ("CAPEX", "+50% overrun", lambda c: replace(c, capex=c.capex * 1.5),
                  "-20%", lambda c: replace(c, capex=c.capex * 0.8)),
        ("Recovered volume", "-20%", lambda c: replace(c, annual_volume_mmscf=c.annual_volume_mmscf * 0.8),
                             "+20%", lambda c: replace(c, annual_volume_mmscf=c.annual_volume_mmscf * 1.2)),
        ("OPEX", "+20%", lambda c: replace(c, annual_opex=c.annual_opex * 1.2),
                 "-20%", lambda c: replace(c, annual_opex=c.annual_opex * 0.8)),
        ("Penalty rate", "-25%", lambda c: replace(c, penalty_rate_override=base_rate * 0.75),
                         "+25%", lambda c: replace(c, penalty_rate_override=base_rate * 1.25)),
        ("Build time", "+1 year", lambda c: replace(c, build_years=c.build_years + 1),
                       "no change", lambda c: c),
    ]
    rows = []
    for label, adv_text, adv_fn, fav_text, fav_fn in defs:
        adv, fav = _evaluate(adv_fn(base)), _evaluate(fav_fn(base))
        rows.append({
            "label": f"{label} ({adv_text} / {fav_text})",
            "adverse_text": f"{label} {adv_text}",
            "favorable_text": f"{label} {fav_text}",
            "adverse_delta_usd": adv["npv_a"] - base_eval["npv_a"],
            "favorable_delta_usd": fav["npv_a"] - base_eval["npv_a"],
            "dev_irr_adverse": adv["irr_dev"],
            "dev_irr_favorable": fav["irr_dev"],
        })
    rows.sort(key=lambda r: abs(r["adverse_delta_usd"]) + abs(r["favorable_delta_usd"]), reverse=True)
    return rows


def calculate_economics(recovered_gas_mmscfd, capex, annual_opex,
                        oil_production_bpd=15000, gas_price_usd_mmbtu=2.42,
                        flaring_authorized=True, is_deep_offshore=False, *,
                        annual_volume_mmscf=None, flaring_days_per_year=365,
                        penalty_rate_override=None, discount_rate=0.15, horizon_years=10,
                        build_years=1, host_fee_usd_mmbtu=0.40, royalty_rate=0.05,
                        heating_value_btu_scf=1000.0, residual_flare_mmscfd=0.0):
    """
    Option A: 100% internal funding.
    Option B: third-party BOOT, $0 CAPEX to the operator.
    Also returns the developer's view, a stress case and a sensitivity table.

    Pass `annual_volume_mmscf` (from calculations.py) or `flaring_days_per_year`; otherwise
    the volume assumes 365 flaring days.
    """
    if annual_volume_mmscf is None:
        annual_volume_mmscf = recovered_gas_mmscfd * flaring_days_per_year

    base = Case(
        annual_volume_mmscf=annual_volume_mmscf, capex=capex, annual_opex=annual_opex,
        oil_production_bpd=oil_production_bpd, gas_price_usd_mmbtu=gas_price_usd_mmbtu,
        flaring_authorized=flaring_authorized, is_deep_offshore=is_deep_offshore,
        penalty_rate_override=penalty_rate_override, discount_rate=discount_rate,
        horizon_years=horizon_years, build_years=build_years,
        host_fee_usd_mmbtu=host_fee_usd_mmbtu, royalty_rate=royalty_rate,
        heating_value_btu_scf=heating_value_btu_scf,
    )
    r = _evaluate(base)

    stress_case = replace(
        base, capex=base.capex * STRESS_CAPEX_MULT,
        annual_volume_mmscf=base.annual_volume_mmscf * STRESS_VOLUME_MULT,
        gas_price_usd_mmbtu=base.gas_price_usd_mmbtu * STRESS_PRICE_MULT,
        build_years=base.build_years + STRESS_EXTRA_BUILD_YEARS,
    )
    s = _evaluate(stress_case)

    host_after_tax = r["host_fee"] - r["tax_b"]
    penalty_share = (r["avoided_penalty"] / r["cf_b"] * 100.0) if r["cf_b"] > 0 else 0.0
    residual_penalty = residual_flare_mmscfd * flaring_days_per_year * 1000.0 * r["penalty_rate"]

    return {
        "annual_volume_mmscf": annual_volume_mmscf,
        "annual_gross_revenue_usd": r["gross"],
        "flare_penalty_avoided_usd": r["avoided_penalty"],
        "penalty_rate_usd_per_mscf": r["penalty_rate"],
        "residual_flare_penalty_usd": residual_penalty,
        "assumptions": {
            "gas_price_usd_mmbtu": gas_price_usd_mmbtu, "discount_rate": discount_rate,
            "horizon_years": horizon_years, "build_years": build_years,
            "host_fee_usd_mmbtu": host_fee_usd_mmbtu, "royalty_rate": royalty_rate,
            "heating_value_btu_scf": heating_value_btu_scf,
        },
        "option_a_internal": {
            "capex_usd": capex,
            "annual_net_cash_flow_usd": r["cf_a"],
            "annual_total_tax_usd": r["tax_a"],
            "npv_usd": r["npv_a"],
            "irr_percent": r["irr_a"],
            "payback_years": r["payback_a"],
        },
        "option_b_outsourced": {
            "capex_usd": 0.0,
            "annual_host_fee_usd": r["host_fee"],
            "annual_host_fee_after_tax_usd": host_after_tax,
            "annual_penalty_benefit_usd": r["avoided_penalty"],
            "penalty_share_pct": penalty_share,
            "annual_total_tax_usd": r["tax_b"],
            "annual_net_benefit_usd": r["cf_b"],
            "npv_usd": r["npv_b"],
            "first_benefit_after_years": build_years,
        },
        "developer": {
            "capex_usd": capex,
            "annual_net_cash_flow_usd": r["cf_dev"],
            "annual_total_tax_usd": r["tax_dev"],
            "npv_usd": r["npv_dev"],
            "irr_percent": r["irr_dev"],
            "payback_years": r["payback_dev"],
        },
        "stress_case": {
            "description": (f"CAPEX +{(STRESS_CAPEX_MULT - 1) * 100:.0f}%, volume "
                            f"-{(1 - STRESS_VOLUME_MULT) * 100:.0f}%, gas price "
                            f"-{(1 - STRESS_PRICE_MULT) * 100:.0f}%, build time +{STRESS_EXTRA_BUILD_YEARS} year"),
            "developer_irr_percent": s["irr_dev"],
            "developer_npv_usd": s["npv_dev"],
            "option_a_npv_usd": s["npv_a"],
            "option_b_npv_usd": s["npv_b"],
        },
        "sensitivities": _sensitivity_table(base, r),
    }


# ---------------------------------------------------------------------------
# Bankability gate
# ---------------------------------------------------------------------------
def assess_bankability(top_route_score, top_route_label, econ, thresholds=None, route_flags=None):
    """
    BANKABLE      all three checks pass
    MARGINAL      route fits and the developer clears the stress floor in the base case, but
                  a hurdle or the stress test fails
    NOT BANKABLE  anything else (a "dead flare" as scoped)
    """
    t = dict(BANKABILITY_DEFAULTS)
    t.update(thresholds or {})

    dev = econ["developer"]
    stress = econ["stress_case"]
    base_irr, stress_irr = dev["irr_percent"], stress["developer_irr_percent"]

    def fmt(x):
        return "n/a" if x is None else f"{x:.1f}%"

    checks = [
        {"name": "Route fit", "passed": top_route_score >= t["min_route_score"],
         "detail": f"{top_route_label} scores {top_route_score:.0f} (minimum {t['min_route_score']:.0f})"},
        {"name": "Developer base-case IRR", "passed": base_irr is not None and base_irr >= t["developer_hurdle_irr_pct"],
         "detail": f"{fmt(base_irr)} (hurdle {t['developer_hurdle_irr_pct']:.0f}%)"},
        {"name": "Developer stress-case IRR", "passed": stress_irr is not None and stress_irr >= t["stress_floor_irr_pct"],
         "detail": f"{fmt(stress_irr)} (floor {t['stress_floor_irr_pct']:.0f}%) under: {stress['description']}"},
    ]

    if all(c["passed"] for c in checks):
        verdict = "BANKABLE"
    elif checks[0]["passed"] and base_irr is not None and base_irr >= t["stress_floor_irr_pct"]:
        verdict = "MARGINAL"
    else:
        verdict = "NOT BANKABLE"

    return {
        "verdict": verdict,
        "checks": checks,
        "failed": [c for c in checks if not c["passed"]],
        "warnings": list(route_flags or []),
        "thresholds": t,
    }
