# GASROUTE 2030 v2: calibrated to the World Bank GGFR small-scale AG report

New modules: `report_data.py` (Tables 4, 7-14 and technical parameters), `costing.py` (installed CAPEX/OPEX, gas-quality gates, product revenue),
`finance.py` (year-by-year cash flows, penalty-share solver, bankability gate), `analysis.py` (prices every route, recommends on bankability).
`economics.py` is kept for the penalty tiers and PIA taxes; the v1 cash-flow functions in it are no longer called by the app.

## What changed
| Area | v1 | v2 |
|---|---|---|
| CAPEX / OPEX | Placeholders ($1.2M/MMSCFD CNG, 8% of CAPEX) | Report Tables 8-11 x scale factor (T13) x regional factor (T12, Africa 1.75). CNG at 8.5 MMSCFD: $28M, not $9M |
| Revenue | One $/MMBtu gas price for every route | Product netbacks (T14): CNG, LNG, power, dry gas, propane, butane. Fuel burn, LNG conversion, NGL yield (65%) modelled |
| Gas quality | Ignored | H2S / CO2 / H2O / C3+ checked against Table 7; pretreatment CAPEX/OPEX added when limits are exceeded |
| Cases | One base + one stress | Pessimistic / base / optimistic from the report's Low/High ranges; 6-month build swing |
| Discount rate | 15% flat | Regional (Africa normal 10%, high 14%), overridable |
| Profile and sizing | Flat | Table 4 profiles A-E; average / max / min sizing (Table 17) |
| Build time | Years | Months, fractional-year cash flows; 10-year life including construction |
| Option B | Penalty always stays with the operator | Penalty-share slider; solver finds the share that lifts developer IRR to the hurdle |
| Verdict | BANKABLE / MARGINAL / NOT | Adds CONDITIONALLY BANKABLE (works within the operator's maximum penalty share) |
| Recommendation | Highest score | Best verdict, then developer IRR, then score; every route priced in a table |
| Routes | Truck 150 Mscf, power 0.234 MMSCFD/MW | Truck 250 Mscf, 0.34 MMSCFD/MW; CNG down-weighted above 5 MMSCFD; power and off-taker volumes capped by demand |
| Carbon | Abatement only | Penalty as $/tCO2 (about $38 and $9 for the two tiers), compression and trucking emissions |
| Data | 8 assets | Adds optional h2s/co2/h2o columns and Hyp_NonAG_Sour_Gas |
| Removed from sidebar | | Gas price per MMBtu, CAPEX uplift, fixed CAPEX/OPEX (replaced by report scale factors). Dry-gas contract price override added |

## Headline results (defaults: Africa normal risk, 12-month build, 0% penalty share)
| Asset | Best route | Developer IRR (pess / base / opt) | Verdict |
|---|---|---|---|
| Urgent_01 (8.5 MMSCFD) | CNG | -3.2 / 11.7 / 27.8% | CONDITIONALLY BANKABLE (needs ~70% of penalty) |
| Urgent_02 (14 MMSCFD) | CNG | 0.3 / 15.9 / 34.7% | CONDITIONALLY BANKABLE (~29%) |
| Urgent_03 (4.2 MMSCFD, CO2 1.2%) | CNG | -39.8 / -6.7 / 6.2% | NOT BANKABLE |
| Near_Grid | Pipeline tie-in | 1.1 / 21.0 / 45.5% | MARGINAL (pipeline cost is a placeholder) |
| Swamp_Offtaker | Off-taker | 10.4 / 36.7 / 75.3% | MARGINAL (off-taker cost is a placeholder) |
| Rich_Gas, Offshore, Dead_Flare, Sour_Gas | | | NOT BANKABLE |

## Caveats
Pipeline, off-taker and reinjection are not costed in the report and still use v1 placeholders, so they look cheaper than the report-based routes.
Report prices are indicative; the result turns on the real offtake price. No inflation, financing, loss carry-forward or transport of products to the buyer. Cluster mode is not built.
Tests: `python -m pytest -q test_models.py` (44 pass). Headless app run: `python smoke_test.py`.
