# GASROUTE 2030: model review and fixes

Files changed: `app.py`, `calculations.py`, `economics.py`, `route.py`. `facilities.csv` is unchanged.
New: `test_models.py` (23 tests) and `smoke_test.py` (headless app run). Needs `numpy_financial` (already in your requirements).

## Bugs fixed
| # | Where | Problem | Fix |
|---|---|---|---|
| 1 | economics | Sidebar inputs (oil production, flaring authorization, deep offshore, gas price) never reached the model; `calculate_economics` ran on defaults, so the penalty tier was always based on 15,000 bpd | All inputs are passed through |
| 2 | economics | Annual volume ignored flaring days: an Upset asset (60 days) was valued on 365 | Uses `annual_recoverable_mmscf` from calculations |
| 3 | economics | Penalty rates: code used $3.50 / $2.00 / $0.50 and the CO2 tab hard-coded $3.50 | Published tiers only ($2.00 at or above 10,000 bpd, $0.50 below; Flare Gas Regulations 2018). $3.50 could not be verified, so it is now an optional override |
| 4 | calculations | Butane (C4) was silently dropped from GPM because the app sent `C4` and the code looked for `C4+`. LPG potential was understated (5,357 vs 9,458 GPD in the default case) | Keys normalised |
| 5 | calculations | CO2e abatement (1,246k t/yr) assumed all methane was vented, but the gas is flared | Flare-basis method (about 18.7k t/yr). The vent-basis figure stays in the tooltip for context |
| 6 | route | POWER scored 55 with 0 MW demand; COLLOCATED was all-or-nothing | Scores scale with the share of gas the demand can absorb |
| 7 | route/app | CNG and Reinjection tied at 100 with no tie-break, and Reinjection (no revenue) competed with commercial routes | Explicit tie-break; Reinjection is shown as a "compliance fallback" |
| 8 | calculations | Forfeiture risk mitigation recommended the off-taker route, which scored lowest | Reworded; added schedule, counterparty, regulatory, security and residual-flare risks |
| 9 | app | Selecting a preset could fail to update the Asset Category / Location widgets (`index=` fought session state) | Widgets are keyed to state |
| 10 | app | Volumes were rounded before the maths; bad CSV values (NaN, out-of-range, unknown category) could crash or mislead | Full-precision maths; defensive CSV parsing |
| 11 | app | Sensitivity chart painted a CAPEX increase green | Coloured by outcome (red lowers NPV, green raises it) |

## New capabilities
- **Bankability gate** (banner on every tab): BANKABLE / MARGINAL / NOT BANKABLE from route fit, developer base-case IRR and a stress case. Thresholds are editable in the sidebar.
- **Developer returns** and a stress case (CAPEX +30%, volume -20%, price -20%, +1 year build). This is what an EOI has to satisfy.
- **Build period**, so benefits no longer start on Day 1; payback includes it.
- **Fixed CAPEX / OPEX**, so tiny flares can actually fail the gate.
- **Wider sensitivities**: volume, penalty rate, gas price, CAPEX overrun (+50%), OPEX, build time. Each re-runs the full model.
- **"Where Option B's value comes from"**: about 90% in the default case is avoided penalty, not host fees.
- **CNG logistics check** (truckloads/day) with a warning above 20/day.
- **Residual flare** shown (15% of the gas is not captured at an 85% recovery factor).
- **Assumptions panel** (sidebar), methodology expander, "why these scores" table, readable labels.

## Default-case results (8.5 MMSCFD, 12,500 bpd, CNG)
| | Before | After |
|---|---|---|
| Option A NPV / IRR / payback | $34.55M / 99.3% / 1.0 y | $27.41M / 55.7% / 2.1 y |
| Option B NPV | $29.36M | $25.55M |
| Developer IRR (base / stress) | not shown | 22.8% / 5.1% |
| LPG potential | 5,357 GPD | 9,458 GPD |
| Net CO2e abated | 1,246.7k t/yr | 18.7k t/yr |
| Verdict | none | MARGINAL (fails the stress test) |

## Still placeholders (replace with project numbers)
CAPEX per MMSCFD by route, pipeline $/km, fixed CAPEX/OPEX, trailer capacity, 85% recovery factor, $0.40/MMBtu host fee, $2.42/MMBtu gas price, 10-year horizon, stress-case sizes, gate thresholds, CIT/HT/royalty treatment. Not modelled: acid-gas treatment, price escalation, field decline, financing costs.
