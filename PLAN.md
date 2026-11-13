# PLAN: Avangrid 2026 Hackathon, AMI electrification & BTM battery framework

> On approval, this file is copied to PLAN.md in the repo root.
> **Revision 2** (Thu 11/12): includes the reviewer comments. The change log is at the end of this file.

## Context
Avangrid gave us one year (9/1/25–8/31/26) of hourly smart-meter (AMI) data for 34,238 residential customers, one hourly weather series, and partial labels (186 EV, 88 heat pump, 397 solar customers present in the data). We must (1) classify customer appliances, (2) segment customers by load behaviour, and (3) find and size behind-the-meter battery (BTM BESS) and demand-flexibility candidates. Deliverables are a **.pptx deck (the only thing scored)** and a GitHub repo with a tool, README and environment file. **Deadline: Fri 11/13, 5:00pm ET.**

The Part-1 brief maps onto the data as follows: the **"user data" is the AMI** (also the biggest dataset), and the **context data to correlate against is weather plus labels**. There is no geography, tariff or demographics. So "what drives what" means **weather → load**, **appliance → load shape / peak**, and **segment → system peak contribution (summer and winter)**.

**Headline value metric (per Avangrid's priorities): avoided buildout cost.** That means coincident-peak MW relief, priced as avoided capacity/transmission cost plus deferred distribution upgrades. Customer bill impacts are secondary and depend on tariff assumptions.

Decisions already made: no video; raw data stays in Data/; the three of us work on the same tasks (no role split); LLM use stays as now (Claude in this coding session at build time, no runtime API, no raw rows sent anywhere); libraries installed; the Avangrid experts are asked the questions in §9 at office hours.

**Changed decisions:**
- The 31 accounts above 25 kWh/h are **no longer excluded wholesale**. Each is inspected (task 1.0b). Only clear data errors are dropped. Plausible ones stay in, as a "very high load" segment, and are often the strongest BESS/DR candidates.
- The 233 stuck meters stay excluded and are reported as their own segment, after checking that none of them are PV homes with clipped exports.

## 1. Challenge summary → judging criteria

| Rubric (weight) | What wins it | Where in our solution |
|---|---|---|
| Solution & business value (40%) | BESS candidates, sizing, quantified impact, practical program actions | **Avoided buildout cost** (coincident-peak MW × $/kW-yr, deferred upgrades), energy-feasible dispatch, **winter-peaking future scenario**, 3 kVA electrification-enablement lens, segment → program playbook; bill savings as a sidebar |
| Analytics & modeling (35%) | Classification accuracy, meaningful segments, rigor, use of weather + labels, reproducibility | Physics features + PU learning with honest, **non-circular** validation; PV checked against net/clipped metering; interleaved-split stability tests; run_all pipeline |
| Presentation (15%) | Clear story, equations, model explained on slides, strong visuals | Deck: problem → data → method (equations) → findings → BESS → value → future → roadmap; app screenshots |
| Innovation (10%) | Novel techniques, creative data use, extra insights, scalability | Label-provenance finding, unregistered-PV discovery, **heat pump vs resistive heating split**, L1/L2 EV distinction, mid-year adoption detection, wind-driven "leaky home" heating, NMF load decomposition, winter-peak scenario, DuckDB scale story |

## 2. Model recommendations (based on benchmarks on our data)

**Read-only benchmark results** (33,490 customers in the modeling population, 10-core laptop):
- Load-shape clustering (72-dim normalized seasonal daily profiles): **K-Means silhouette 0.11–0.13** (k = 6–10, under 1 s); GMM −0.02; HDBSCAN: 0 clusters, 100% noise. Load shapes are a **continuum**, not separable blobs.
- PU classifiers on 13 crude audit features + 10 shape PCs, 5-fold CV: EV AUC logistic 0.736 / LightGBM 0.748 / HistGB 0.734; HP AUC 0.738 / 0.707 / 0.676. Model choice barely matters at this stage; **features are the bottleneck**.
- **44% of labeled EV customers show no detectable home Level-2 charging blocks** all year; 40% show more than 0.5 per week, versus 5.5% of everyone else. This is **not yet called label noise**. Task 1.0b tests the alternatives: Level-1 overnight charging, mid-year purchase, plug-in hybrids, and charging away from home.
- PV: "midday zeros and no night zeros" gives **91.4% precision / 86.1% recall** against the full list, plus 32 unregistered-PV candidates. **Caveat:** this may simply reflect import-only (clipped) metering, where exports show up as zeros. Task 1.0b checks for negative values and asks Avangrid whether the meter is net or delivered-only.
- HP is a heating signature: the hourly heating slope is 6× higher for labeled HP (AUC 0.715); the cooling slope does not separate (0.53).

### Task A: Appliance classification (hackathon Part 1)
| Option | Accuracy | Explainability | Train time | Demo |
|---|---|---|---|---|
| Physics/rule detectors only | Good for PV, AC, pool; unknown recall elsewhere | Excellent | Seconds | Great: show the signature |
| **PU logistic regression on physics features** (Elkan–Noto, c = 0.25 from the brief) | Ties LightGBM in benchmark | Coefficients = odds ratios | <1 s | Good |
| PU LightGBM (bagged PU) + SHAP | Possibly +0.01–0.03 AUC once features improve | SHAP | ~5 s/CV | Good |
| 1-D CNN / sequence model on raw 8,760 h | Unproven; 186 positives is too few; no torch | Poor | Minutes–hours | Risky |

- **Baseline (safe):** engineered signature features → rule scores for every appliance; PU logistic for EV and HP; PV rule.
- **PU assumption (SCAR):** Elkan–Noto assumes the labeled positives are a *random* sample of all true positives. We don't know how the labels were collected (rebate program? charger brand? self-report?). Until Avangrid confirms:
  - PU outputs are presented as a **ranking score**, not a calibrated probability. P(labeled | x) / c is reported only as an "if-SCAR-holds" estimate.
  - We run a **c-sensitivity** analysis (c = 0.15 / 0.25 / 0.40) on the implied counts.
  - We check whether labeled positives differ systematically from high-scoring unlabeled customers (e.g. load size, L2 vs L1), which would hint at selection bias. Any difference is reported as a finding.
- **EV detection covers both charging levels:** an L2 block detector (≥ 5 kW, 2–6 h plateau) **and** an L1 detector (1.2–2 kW overnight lift sustained 6–12 h, compared with the customer's own baseline nights). Results are reported as "L2 detectable", "L1 detectable" and "no home-charging signature".
- **Heating is split into types:** the heating type comes from the *shape* of the temperature response, not just its slope. Heat pumps show a steepening slope below about 20°F (falling COP and resistance-strip backup) plus summer cooling from the same system. Resistive heating shows a steeper, near-linear winter slope. Output: `heat_type ∈ {HP, HP+backup, resistive, none/non-electric}`. The 88 HP labels validate the HP class only.
- **Stretch:** bagged-PU LightGBM + SHAP, *adopted only if* it beats logistic on held-out recall.
- **Unlabeled appliances** (AC, resistance heat, water heater, pool, hot tub, PV + battery): transparent rule scores. **Thresholds come from physics** (rated kW ranges, durations) **or from natural gaps in the score distribution, never from RECS.** EIA RECS saturation is then an *independent* plausibility check that is allowed to fail.
- **Resolution caveat:** the data is hourly, so fast signatures (heat pump compressor cycling, water-heater bursts of 20–40 min, hot-tub heater cycling) average into ~1–2 kWh bumps that are hard to tell apart from other loads. Water heater and hot tub detections are therefore tagged **low confidence**. Rely on what hourly data does resolve well: temperature response, seasonality, and multi-hour blocks. The deck states this caveat explicitly.

### Task B: Pattern discovery and segmentation (hackathon Part 2)
| Option | Fit to this data | Explainability | Time | Demo |
|---|---|---|---|---|
| **K-Means on normalized seasonal shapes** | Works, but on a continuum (sil. 0.12) | Centroid curves | <1 s | Good |
| GMM / HDBSCAN | Fail here (−0.02 / all noise) | n/a | ~1 s | n/a |
| **Rule-based business segments from features** (high electrification, peak contributor (summer / winter), seasonal/vacant, flexible, PV, emerging, **very high load**) | Distinct by construction, matches the brief | Excellent | Seconds | Excellent |
| NMF load decomposition (parts-based components) | Fits a continuum: each customer is a mix | Very good | Seconds | Strong "innovation" visual |
| Autoencoder embeddings | Marginal gain, no torch | Poor | Slow | Weak |

- **Baseline:** rule-based business segments (primary, actionable) × K-Means **archetypes** (k ≈ 6, described honestly as archetypes on a continuum) with a cross-tab.
- **Not prescriptive:** the labels (EV/HP/PV) must not limit discovery. The NMF components, the K-Means archetypes and IsolationForest outliers are reviewed **before** they are mapped to known appliance names. Any component or group that matches no known label is reported as an "unexplained pattern" with exemplar plots.
- **Stretch:** NMF decomposition (k ≈ 6 components) → each customer's mix → "electrification fingerprint."
- **Anomalies / trends / sequences:** IsolationForest on features; **weather-normalized monthly residuals + change-point** to find customers whose load stepped up mid-year (emerging electrification, new EV adopters; this also explains some labeled EVs with no charging signature).

### Task C: Correlating patterns with weather and labels ("what drives what")
| Option | Accuracy | Explainability | Time | Demo |
|---|---|---|---|---|
| **Per-customer change-point regression** (daily kWh vs temperature: base, heating and cooling balance points and slopes, a **low-temperature slope break**, plus humidity and wind terms) | Physical, standard in utility load research | Excellent: slopes in kWh/°F | Seconds (vectorized) | Great |
| LightGBM hourly load model per segment (weather + calendar) + SHAP / PDP | Captures nonlinearities and interactions | Good with SHAP | Seconds–minutes | Good |
| SARIMAX / Prophet per customer | Overkill at 34k customers | Medium | Long | Weak |

- **Baseline:** change-point regression per customer; segment-level temperature-response curves; coincident-peak contribution for **both** the summer system peak (top 1% hours: July 1–2, 5–9pm, 87–92°F) **and the winter system peak** (top 1% of winter hours, coldest mornings and evenings).
- **Stretch:** segment-level LightGBM + SHAP showing humidity and wind effects, validated on blocked, interleaved folds.
- **Causality:** weather is exogenous, so weather → load is defensibly causal. Appliance/segment → peak links are **associations** and labeled as such.

### Explainability
Odds ratios (logistic); SHAP summary and dependence plots (if LightGBM is adopted); partial-dependence temperature-response curves per segment; cluster centroid curves + feature z-score "profile cards"; per-customer "why flagged" cards with the top 3 evidence features and values, **plus a confidence tag (high / medium / low, with resolution and label caveats)**.

### Where the LLM adds value (Claude in this session, build time only)
- Name and describe archetypes / segments from **aggregate** centroid stats.
- Write segment insight cards and the program playbook text.
- Draft and tighten the deck narrative and speaker notes.
- **Not** used per customer and **no runtime API**: data stays on the laptop and the demo is deterministic.

### Hackathon Part 3: BESS, demand flexibility and avoided buildout (method)
**Value framing.** The primary metric is **avoided buildout cost**:
- coincident-peak MW reduced at system top hours × avoided capacity/transmission cost ($/kW-yr), and
- deferred distribution upgrades (transformer/feeder headroom, especially where electrification adds load).

All $ values come from **parameterized assumptions in config**, shown as a low/mid/high range and confirmed with Avangrid at office hours. Customer bill savings appear only as a secondary sidebar under a documented TOU assumption.

**Energy-feasible dispatch (kWh, not just kW).**
- Battery spec: two units = 20.16 kWh, 7.68 kW, 89.4% RTE; one unit = 10.08 kWh. At full power, two units last about 2.6 h.
- Dispatch is simulated hour by hour with **SOC tracking**: discharge ≤ 7.68 kW, SOC stays between its limits, RTE is applied on recharge, recharge happens only in off-peak hours, and recharge must not create a new peak (rebound check).
- We claim **only the kW the battery can sustain across the whole peak window**, not the nameplate kW. For a 4–5 h window this is often far below 7.68 kW.
- **Multi-day events:** back-to-back heat-wave and cold-snap days are simulated in sequence, so incomplete overnight recharge reduces the next day's relief.
- Two variants: perfect foresight (upper bound) and a realistic version that forecasts from a similar prior day. **Stretch:** per-day LP (scipy.optimize.linprog) with an SOC constraint.
- **Demand flex respects energy too:** pre-cooling and EV-shift reductions include a rebound (shifted kWh reappears in adjacent hours), and the net coincident relief is reported after rebound.

**Sizing.** Energy above the target threshold per peak event, P90 across event days (summer and winter separately) → 1 unit vs 2 units vs "battery insufficient, pair with DR".

**Forward-looking: winter-peaking New England.**
- Scenario: increase HP adoption (e.g. 5% / 15% / 30% of customers). Added load is built from the **measured heating response of labeled HP homes** (by temperature, including the cold-weather slope break) and applied to the observed weather year.
- Outputs: (1) system peak by season under each scenario, and the adoption level where **winter overtakes summer** as the system peak; (2) BESS/DR value against **winter** peak events, which are longer and more often multi-day, so kWh constraints matter more; (3) which segments drive the future winter peak.
- The **3 kVA electrification lens** applies here: for customers likely to add EV/HP load (> 3 kVA incremental), can a battery keep the incremental coincident load ≤ 3 kVA in both seasons? This gives the "electrification enablement" and avoided-upgrade story.

**Priority score.** Weighted mix of avoided-cost contribution (summer + forward winter coincident peak), persistence of high-peak days, **energy-feasible** battery coverage of events, electrification flag, and PV synergy → Tier 1 / 2 / 3 with counts and aggregate MW (today and in the winter scenario).

## 3. Architecture

```
Data/ (raw zips, xlsx)                              ── never committed
   │ scripts/01-03 (done): stream unzip → Parquet → validate → dense matrix + aligned weather
   │ scripts/03b_checks (NEW): negatives / net-vs-import, high-load accounts, stuck vs PV, EV L1 check
   ▼
data_processed/  load_matrix.npy [34,238×8,760], customer_index, hour_index (+weather)
   │ 04_features: weather regressions (incl. low-temp slope break), L1 + L2 event detectors,
   │              shape profiles, NMF, change-points, summer + winter coincident-peak shares
   ▼
features.parquet (one row per customer, ~65 explainable features + confidence tags)
   ├─► 05_classify: rule detectors + PU logistic (ranking) + heat-type split → appliance inventory
   ├─► 06_segment:  business segments + K-Means archetypes + anomalies + emerging adopters + unexplained patterns
   ├─► 07_drivers:  temperature-response curves, summer/winter coincident-peak contribution, SHAP/PDP
   └─► 08_bess:     candidate screen → SOC-constrained dispatch (multi-day) → sizing → avoided cost → tiers
       08b_future:  HP-adoption winter-peak scenarios → BESS/DR value in winter
          ▼
outputs/customer_results.parquet + summary tables + docs/figures/*.png
          ├─► 09_insights: aggregate tables → Claude-written insight cards (static JSON/MD)
          ├─► app/streamlit_app.py: customer lookup · segment explorer · BESS ranking & dispatch chart (with SOC)
          └─► deck/  (pptx built from figures + numbers; app screenshots)
```

## 4. Phases and task breakdown (owner: whole team; tasks marked ∥ can run in parallel via subagents)

### Phase 1: data, features, baselines, patterns, correlations (Thu night)
| # | Task (1–3 h) | Depends on | Done when… |
|---|---|---|---|
| 1.0 | Data audit + conversion | – | ✅ done (scripts/00-03, docs/data_audit.md) |
| **1.0b** | **Data checks (NEW, ~45 min):** (a) min value, count and share of negative readings, whether PV homes ever go negative → net vs import-only; (b) inspect the 31 >25 kWh/h accounts (spike vs sustained, shape, seasonality) → keep / drop each one with a reason; (c) confirm none of the 233 stuck meters is a clipped PV home; (d) on the 44% of labeled EVs with no L2: L1 overnight lift, mid-year step change, share with neither | 1.0 | docs/data_checks.md with numbers; decisions logged in config; feature design in 1.2 adjusted |
| 1.1 | src/ package + config (paths, constants, battery spec, **avoided-cost params with low/mid/high**, tariff params, exclusion list from 1.0b); git init, local commit (with your OK) | 1.0b | python -m imports work; .gitignore blocks data and outputs |
| 1.2 | Feature engineering (04_features): change-point regression (heat/cool slopes, balance points, **low-temp slope break**, humidity and wind terms), **L2 and L1** EV events, pool seasonal-timer score, water-heater/hot-tub scores (**tagged low confidence**), PV midday/night zeros (+ negative-export features if net metered), seasonal ratios, load factor, peak stats, **summer and winter** coincident-peak share, monthly weather-normalized residuals | 1.1 | features.parquet for all customers, documented NaNs only, runs < 5 min |
| 1.3 ∥ | Classification (05_classify): rule scores (physics thresholds), PU logistic for EV and HP with 5-fold CV + c-sensitivity, **heat-type split**, PV eval, appliance inventory with estimated kW | 1.2 | Metrics per §5 logged; L1/L2/none EV breakdown; heat-type counts |
| 1.4 ∥ | Segmentation (06_segment): business segments (incl. very-high-load) + K-Means archetypes + IsolationForest + emerging adopters + **unexplained-pattern review** | 1.2 | Every customer has one primary segment + archetype; stability ARI reported; unexplained groups listed |
| 1.5 ∥ | Drivers (07_drivers): temperature-response curves, **summer and winter** coincident-peak contribution by segment and appliance, humidity/wind effects | 1.2 | Table "share of customers vs share of summer / winter system peak"; 3 figures |
| 1.6 ∥ | BESS (08_bess): candidate screen, **SOC-constrained, multi-day** dispatch (perfect + realistic), sustained-kW sizing, rebound check, **avoided buildout $** (bill $ secondary), tiers | 1.2 (+1.3 flags) | Per-candidate sustained kW / kWh / avoided $; aggregate MW per tier; zero SOC or power violations |
| 1.7 | Checkpoint: rerun 04–08 end-to-end, commit | 1.3–1.6 | One command reproduces all outputs |

### Phase 2: improve models, future scenario, LLM insights, demo app (Fri morning)
| # | Task | Depends on | Done when… |
|---|---|---|---|
| 2.1 | Validation pass (§5) + fix weak spots; LightGBM-PU + SHAP stretch if time allows; update PU framing after the office-hours answer on label provenance | 1.7 | Validation table filled; model choice justified by numbers |
| **2.2** | **Winter-peak future scenario (08b_future, NEW):** HP adoption scenarios, season of the system peak, BESS/DR winter value, 3 kVA lens | 1.6 | Crossover adoption level found; winter BESS value table; 2 figures |
| 2.3 ∥ | NMF decomposition stretch + exemplar customer plots per appliance (incl. HP vs resistive, L1 vs L2) | 1.7 | Component figure + 1 exemplar per appliance class |
| 2.4 | Insight generation: aggregate tables → Claude-written segment cards + program playbook | 2.1, 2.2 | One card per segment with numbers traced to outputs |
| 2.5 ∥ | Streamlit app (customer lookup, segment explorer, BESS ranking + dispatch chart with SOC trace) | 1.7 | Runs locally on cached outputs in under 3 s per view; screenshots captured |

*Cut order if late:* LightGBM-PU → LP dispatch → NMF → app. **The winter scenario (2.2) is not cut**, because it is core to the business-value story.

### Phase 3: polish, demo script, deck, submission (Fri midday–afternoon)
| # | Task | Depends on | Done when… |
|---|---|---|---|
| 3.1 | Figure set for the deck (consistent style, color-blind-safe) | 2.x | ~14 PNGs in docs/figures/ |
| 3.2 | Deck build (~16–18 slides + appendix with equations, assumptions, **limitations: SCAR, hourly resolution, metering type, tariff/avoided-cost assumptions**) | 3.1, 2.4 | Every rubric line has a slide; value slide leads with avoided buildout $; every number matches outputs |
| 3.3 ∥ | README (methodology, setup, run steps), requirements, methodology doc | 1.7 | Fresh clone + run_all works |
| 3.4 | Final from-scratch rerun + number audit deck ↔ outputs | 3.2, 3.3 | Zero mismatches |
| 3.5 | Submission: ##_TeamName_Final Submission.pptx to SharePoint (you upload); GitHub push **only on your explicit go** | 3.4 | File in folder before 4:30pm ET |

## 5. Evaluation and proving patterns are real
**Rule: every benchmark is used either to set a threshold or to check one, never both.**

- **Classification (PU):** stratified 5-fold CV AUC; recall on **held-out labeled** customers at a threshold fixed *independently* (physics evidence or a natural gap in the score distribution, not "top 2.2%"). The predicted count is then **compared** to the implied 744 / 352, as a check that can fail, with c-sensitivity. PV precision/recall/F1 against the complete list (interpreted in light of the metering check). EV recall reported separately for L2-detectable, L1-detectable and no-signature labeled customers. Benchmark to beat: EV AUC 0.736 / HP 0.738.
- **Stability split (replaces the Sep–Feb vs Mar–Aug split, which seasonality confounds):** **interleaved odd vs even weeks**, so both halves cover all seasons. Fit on one half, re-score on the other, and report flag agreement (Cohen's κ) and segment persistence. Seasonal behaviour is checked separately with its own expectations: heating flags should fire in winter and not in summer, and cooling flags the opposite.
- **Clustering:** silhouette / Davies–Bouldin vs k; bootstrap stability (ARI across 20 resamples); interleaved-week stability; segments must differ significantly on key features (Kruskal–Wallis, effect sizes).
- **Correlations:** weather slopes tested against a day-shuffle permutation null; coincident-peak shares with bootstrap CIs; associations are not presented as causation.
- **Plausibility (independent):** appliance saturation vs EIA RECS Northeast, *not used for tuning*; exemplar load plots for top-scored customers of each class; HP vs resistive split checked against the regional mix of electric heating types.
- **BESS:** energy and SOC balance checks, RTE applied, power ≤ 7.68 kW, sustained kW across the full window, multi-day recharge, rebound peaks below the original peak; perfect-foresight vs realistic gap reported.

## 6. Top risks and fallbacks
| Risk | Fallback |
|---|---|
| Labels not random (SCAR fails) | Present PU scores as rankings; c-sensitivity; report selection-bias evidence as a finding; confirm provenance at office hours |
| EV "no-signature" labels | Check L1, mid-year adoption and PHEV before calling it noise; wording "no detectable home L2 charging" |
| Import-only metering hides PV generation and true load | Report PV homes' load as net-import; flag a PV + battery self-consumption estimate as a lower bound; ask Avangrid |
| Circular validation | Thresholds from physics or score gaps; benchmarks only as checks (§5 rule) |
| Hourly data can't resolve fast signatures | Low-confidence tags + an explicit deck caveat; focus on robust signatures |
| Dropping real high-load customers | Per-account review in 1.0b; keep plausible ones as their own segment |
| Overstated battery relief | SOC-constrained, multi-day dispatch; claim sustained kW only |
| Summer-only analysis misses the future winter peak | Winter coincident-peak metrics + HP adoption scenario (2.2) |
| Avoided-cost / tariff values unknown | Parameterized low/mid/high in config; confirm at office hours; bill $ secondary |
| Time overrun | Baselines first; cut order in Phase 2; deck starts by 12:00 regardless |
| Compute / memory (matrix 1.2 GB, 16 GB RAM) | Already fine; process in customer chunks if needed |
| Overfitting with 88–186 positives | Regularized logistic, CV only, small grid |
| Data restriction | Raw and customer-level outputs gitignored; only aggregates in the deck and docs; repo kept private |

## 7. Timeline (ET)
**Thu 11/12**
- 19:30–20:15 · **1.0b data checks** (decide on metering, high-load accounts, EV L1)
- 20:15–20:45 · 1.1 scaffolding
- 20:45–22:30 · 1.2 features (largest task)
- 22:30–23:45 · 1.3 classification ∥ 1.4 segmentation ∥ 1.5 drivers
- 23:45–01:00 · 1.6 BESS (SOC-constrained dispatch + avoided cost)
- 01:00–01:20 · 1.7 end-to-end rerun + commit → sleep

**Fri 11/13**
- 08:00–09:30 · 2.1 validation + fixes ∥ 2.2 winter scenario
- 09:30–11:00 · 2.3 NMF/exemplars ∥ 2.4 insight cards ∥ 2.5 app
- 10:00–11:00 · office hours: Scott (load research): label provenance, metering type, missing IDs; 11–12 Brian (data engineer)
- 11:00–12:00 · 3.1 figures
- 12:00–14:30 · 3.2 deck (12–1 Andrew: programs, avoided-cost values, tariff; 2–3 Bruce: BESS assumptions, winter peak)
- 14:30–15:30 · 3.3 README ∥ 3.4 from-scratch rerun + number audit
- 15:30–16:00 · final deck review against the rubric
- 16:00–16:30 · 3.5 submission
- **16:30–17:00 · buffer**

## 8. Repo structure and tooling
```
Avangrid_hackathon/
├── Data/                 raw (stays here, gitignored)
├── data_processed/       Parquet, matrix (gitignored)
├── scripts/              00_data_audit … 03, 03b_checks, 04_features … 08b_future, 09_insights, run_all.sh
├── src/avangrid/         config.py, io.py, features/{weather,events,shape}.py,
│                         classify.py, segment.py, drivers.py, bess.py, future.py, explain.py
├── models/               fitted model objects (small)
├── outputs/              customer-level results (gitignored)
├── app/streamlit_app.py
├── notebooks/            optional exploration only
├── docs/                 data_audit.md, data_checks.md, methodology.md, figures/, insights/
├── deck/                 final .pptx
├── README.md · requirements.txt · PLAN.md · .gitignore
```
Tooling: Python 3.13, DuckDB, numpy/pandas, scikit-learn 1.7, LightGBM 4.7, SHAP, Streamlit, plotly/matplotlib, python-pptx. Pipeline scripts are pure functions over data_processed/, so `bash scripts/run_all.sh` reproduces everything.

## 9. Questions for Avangrid office hours
1. **Label provenance:** how were the EV / HP / PV labels collected (program enrollment, rebates, interconnection records, self-report)? Are they a random subset? (This decides how we frame the PU results.)
2. **Metering:** is the AMI value net (negative = export) or delivered/import-only? (This decides how we read PV.)
3. **Avoided-cost values:** which $/kW-yr for capacity/transmission, and which distribution deferral values, should we use? Is winter peak growth a planning concern?
4. **High-load accounts:** are residential accounts above 25 kW plausible in this territory (large homes, farms, home businesses)?
5. Missing IDs (as before), and the TOU tariff to use for the bill-savings sidebar.

## Verification (end to end)
1. `bash scripts/run_all.sh` from a clean outputs/ rebuilds every table and figure without errors.
2. The validation table (§5) is filled with real numbers, and no target is used as both a threshold and a check.
3. BESS outputs show zero SOC/power violations; the sustained kW claimed is ≤ the kW the battery's energy allows over the full window.
4. `streamlit run app/streamlit_app.py` loads; spot-check 3 customers (one PV, one labeled EV, one Tier-1 BESS) against their figures.
5. The number audit finds no mismatch between deck numbers and outputs/ tables.

---

## Change log (Revision 2: reviewer comments)
| Comment | Change in plan |
|---|---|
| Avoided buildout costs > bill impacts | Avoided buildout $ is the headline value metric (Context, §1, Part 3, 1.6, 3.2); bill savings secondary |
| Load shifting must obey kWh | SOC-constrained, multi-day dispatch; sustained-kW claims; DR rebound (Part 3, §5, Verification) |
| NE likely winter-peaking | Winter coincident-peak metrics everywhere + HP-adoption scenario task 2.2 (never cut) |
| Elkan–Noto needs randomly labeled positives | SCAR caveat, ranking framing, c-sensitivity, selection-bias check, office-hours Q1 |
| Self-confirming validation targets | §5 rule: thresholds from physics or score gaps; 744/352 and RECS used only as checks |
| Below-zero data / PV clipping | Task 1.0b(a); net vs import check; office-hours Q2; PV caveats |
| Time split confounded by seasonality | Interleaved odd/even-week split; seasonal checks separately |
| 44% EV maybe L1 not L2 | L1 detector; 1.0b(d) check; "no detectable home L2" wording |
| >25 kWh/h removal risky | Per-account review in 1.0b(b); plausible ones kept as a "very high load" segment |
| Hourly resolution caveat | Low-confidence tags for water heater / hot tub / cycling; deck limitation slide |
| HP vs resistive; not prescriptive | heat_type split via temperature-response shape; unexplained-pattern review in segmentation |
