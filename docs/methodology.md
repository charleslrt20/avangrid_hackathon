# Methodology

This page explains how the pipeline works, step by step. Results and the full tables are in the stage docs it links to; every number there is written by the script that produced it. Parameters (equipment ratings, battery spec, avoided-cost ranges, seeds) live in one file, `src/avangrid/config.py`.

| Step | Script | Detail doc |
|---|---|---|
| Data audit and conversion | `00`–`03` | [data_audit.md](data_audit.md) |
| Data checks (metering, high-load accounts, stuck meters, labeled EVs) | `03b_checks.py` | [data_checks.md](data_checks.md) |
| Features | `04_features.py` | [features.md](features.md) |
| Appliance classification | `05_classify.py` | [classification.md](classification.md) |
| Segmentation | `06_segment.py` | [segmentation.md](segmentation.md) |
| NMF load decomposition and exemplars | `06b_decompose.py` | [decomposition.md](decomposition.md) |
| Drivers of load and system peaks | `07_drivers.py` | [drivers.md](drivers.md) |
| Battery and demand-flex screening | `08_bess.py` | [bess.md](bess.md) |
| Winter-peaking future (heat-pump adoption) | `08b_future.py` | [future.md](future.md) |
| Validation | `09a_validate.py` | [validation.md](validation.md) |
| Segment cards and program playbook | `09_insights.py` | [insights/](insights/) |
| Deck figures and deck numbers | `10_figures.py`, `11_deck_facts.py` | [../deck/figure_list.md](../deck/figure_list.md) |

## 1. Data

**Inputs.** One year (2025-09-01 to 2026-08-31) of hourly AMI readings in four zips, one hourly weather series (temperature, humidity, wind) from a single station, and a label list of EV, heat-pump and solar-PV customers. There is no geography, feeder, tariff or demographic data, so the only joins are customer ID (to labels) and timestamp (to weather).

**Conversion.** The zips (about 14 GB of CSV) are streamed into Parquet with DuckDB, never unzipped to disk. The result is a dense matrix `load_matrix.npy` of 34,238 customers × 8,760 hours (missing hours are NaN), plus a customer index and an hour index with weather aligned to it. AMI uses hour-ending 1–24 and weather uses clock hours 0–23, so hour-ending *h* gets the mean of the weather readings at *h*−1 and *h*. DST gaps and duplicates are handled as documented in the audit.

**Data checks that shaped the design** ([data_checks.md](data_checks.md)):
- **Metering is delivered-only.** No reading is ever negative, and PV homes show exact zeros at midday. Exports are clipped, so PV generation and self-consumption are invisible. PV is detected from the zero structure, and PV homes' load and battery numbers are lower bounds.
- **Modeling population = 33,927.** 311 customers are left out of model fitting: stuck or flat meters (reported as their own segment), one data error, and accounts with under 180 days of data (kept for lookup). Accounts above 25 kWh in an hour are kept when the load is sustained; they form a "very high load / likely non-residential" segment.
- **44% of labeled EVs show no home Level-2 charging.** Level-1 charging and mid-year purchase explain only part of it, which led to the three-way EV output and the label-provenance finding below.

## 2. Features

One row per customer, about 50 explainable features plus 72 shape-profile columns ([features.md](features.md) has the full dictionary). Thresholds come from equipment ratings, never from labels. The main groups:

- **Weather response.** A change-point regression per home on daily kWh:

  kWh_d = β₀ + β_h·HDD_d(T_h) + β_c·CDD_d(T_c) + β_x·HDD_d(20 °F) + β_w·HDD_d·wind_d + β_m·CDD_d·humidity_d + β_e·weekend_d

  Wind and humidity are standardized. The balance points T_h and T_c are chosen per home by grid search (2.5 °F steps) on the three-term model, then the full model is fitted at those balance points. β_x > 0 means the heating slope steepens in deep cold, which is the heat-pump signature (falling COP, then resistance-strip backup); resistive heat stays linear. Humidity and wind terms capture muggy-day cooling and "leaky home" heating.
- **EV charging.** Level-2: blocks of ≥ 3 kWh above the home's own baseline (20th percentile of its month × hour load), peak 5.5–12 kW, at most 5 h. Level-1: a flat 1.0–2.2 kWh overnight plateau on mild nights.
- **Other appliances.** Pool pump (same-hour on/off timer steps in summer, not winter), water heater and hot tub (tagged **low confidence**, because hourly data blurs their 20–40 minute cycles), PV (summer-minus-winter midday zero share, with a night-zero vacancy check).
- **Size, shape and season.** Annual kWh, load factor, base load, evening share, winter/summer ratio, weather-normalized monthly residuals and their trend (for emerging load).
- **System-peak exposure.** The system peak is the top 1% of hours per season (88 hours, summer and winter separately). Each home's coincident-peak (CP) kW is its mean load in those hours; persistence is the share of the top 10 system days on which the home is also near its own peak.

## 3. Appliance classification (hackathon Part 1)

**Physics gates set every flag.** Each appliance flag is a rule on the features above, with thresholds from equipment ratings (L2 chargers 6–11.5 kW, L1 at 120 V 12–16 A, central AC 2.5–5 kW, and so on). Heat type comes from the shape of the temperature response: electric if the heating slope is ≥ 0.5 kWh/day/°F; heat pump if the slope steepens ≥ 1.25× below 20 °F (HP + backup if it at least doubles); resistive if it is linear and ≥ 1.5; otherwise "electric, type unclear".

**PU learning ranks; it does not flag.** The brief says the labeled EV and HP customers are a random 25% of true owners. That is the SCAR assumption that Elkan–Noto positive-unlabeled learning needs:

g(x) = P(labeled | x),  P(owner | x) = g(x) / c,  ĉ = mean of g(x) over labeled customers

We fit g with 5-fold out-of-fold scores and estimate c from the data. It comes out at 0.035 (EV) and 0.011 (HP), not 0.25, and the physics EV flag finds 3.5× the owners the labels imply. Our reading is that the labels are not a random sample (they mark ownership, not how the home charges or heats). So PU scores are used only to **rank** customers and to grade confidence, never as probabilities. For EV the ranking model is a bagged PU LightGBM, adopted because it beat the logistic model on held-out recall at every cut-off on every CV split; HP keeps the logistic model, whose coefficients double as odds ratios.

**PV** is the one appliance with a complete label list, so it gets a straight precision/recall check. Customers flagged but not on the list are reported as unregistered-PV candidates.

Every flagged customer carries a "why flagged" record (top evidence features and values) and a confidence tag (high / medium / low).

## 4. Segmentation and pattern discovery (Part 2)

Three views, because load shapes form a continuum rather than separate clusters (silhouette stays near 0.1 for every k):

1. **Business segments (primary).** Rule-based and ordered: very high load, solar PV, electrified (EV + electric heat), EV, electric heat, low use / seasonal, winter peak contributor, summer peak contributor, emerging load, typical. Each customer takes the first rule that fires. Peak contributors are the top 15% of CP kW per season. These are the segments the playbook acts on.
2. **Shape archetypes.** K-Means (k = 6) on 72-dimensional profiles (season × hour of day, divided by the home's annual mean), so they group *when* homes use energy, not how much. Archetypes were reviewed and named from their centroid curves *before* checking which appliance or label is over-represented in each; groups with no clear match are reported as unexplained patterns.
3. **NMF decomposition.** Each home's 12-month × 24-hour shape S_i (scaled to sum to 1) is written as a mix of parts, S_i ≈ Σ_j w_ij H_j with w_ij ≥ 0 and each H_j summing to 1, so w_ij is roughly the share of the home's energy that part *j* explains. k = 7 is chosen without labels: the largest k ≤ 8 at which every part reappears (cosine ≥ 0.9) in separate fits on odd and even weeks. Parts are then compared with appliance flags, and three remain unexplained.

IsolationForest on size, shape and weather features flags the 1% most unusual homes. Exemplar plots (the medoid customer of each appliance class) show what each signature looks like.

## 5. Drivers: what drives what

- **Weather → load** is causal (weather is exogenous): segment temperature-response curves and slopes, tested against a day-shuffle permutation null.
- **Segment / appliance → system peak** is an association: each group's share of customers is compared with its share of the summer and winter coincident peaks, with Poisson-bootstrap 95% confidence intervals over customers.

## 6. Batteries and demand flexibility (Part 3)

**Headline metric: avoided buildout cost**, not customer bill savings.

**Battery.** Two Enphase IQ 10C units: E = 20.16 kWh, P = 7.68 kW, η = 89.4% round-trip efficiency (one unit = half the energy and power).

**Candidates.** In-model homes that are not low use and have ≥ 1.5 kW of load in the summer or winter system-peak hours.

**Dispatch is energy-feasible, hour by hour, with state of charge carried across days:**

SOC_{t+1} = SOC_t + η·c_t − d_t,  0 ≤ SOC ≤ E,  d_t ≤ min(P, load_t)

Recharge happens only from 00:00 to 05:00 and never pushes the home above its own 95th-percentile load, so no new customer peak is created. Discharge is flat at min(P, usable energy / hours left) and never exceeds the home's load (no export). Multi-day heat waves and cold snaps run in sequence, so a partial overnight recharge cuts the next day's relief.

Two variants: **perfect foresight** discharges exactly in the system's top hours (upper bound). **Realistic** (headline) calls 15 events per season on the hottest or coldest *forecast* days (forecast error s.d. 2 °F) with a fixed window (HE15–20 summer, HE16–21 winter). Relief counts only the kW actually delivered in the system's top hours, with missed hours counted as zero. We claim the **sustained** kW the battery can hold through the whole window, not the 7.68 kW nameplate: a 6-hour window limits a full battery to 3.36 kW.

**Sizing.** Energy and power needed to hold the home at its own seasonal median through the window on each true peak day, P90 across days → no battery / 1 unit / 2 units / 2 units + demand response.

**Demand flex without a battery.** AC shed (30% of weather-driven cooling), electric-heat shed (20%), and managed EV charging (window charging moved to the next night). All shifted energy comes back as rebound in the following hours, and relief is reported net of it.

**Avoided cost.**

V = p_cap·ΔkW_peak + p_tx·mean(ΔkW_S, ΔkW_W) + p_dist·max(ΔkW_S, ΔkW_W)

Capacity is valued at the annual system peak (summer today), transmission at the mean of the two seasons, and distribution deferral at the larger of the two. The $/kW-yr values are **placeholder low/mid/high ranges** in `config.py` pending Avangrid's figures; NPV is over 15 years at 7%.

**Priority score and tiers.** score = 0.45·rank(V) + 0.15·peak-day persistence + 0.15·energy-feasible event coverage + 0.15·electrified + 0.10·PV. Tier 1 / 2 / 3 = top 10% / next 20% / next 30% of candidates.

## 7. Winter-peaking future

New England is expected to turn winter-peaking as homes electrify heat, so we model heat-pump adoption on the observed weather year.

- **Added load per heat-pump home is measured, not assumed.** For each hour of the day, the gap between the mean load of heat-pump homes and of non-electric-heat homes is regressed on HDD, deep-cold degree-hours below 20 °F and CDD. The intercept (house size and occupancy) is dropped, leaving the heating and cooling the heat pump adds at each temperature and hour. Three donor groups bracket the answer: all labeled HP homes (partial displacement), labeled HP homes with an electric-heat signature (headline), and physics-detected HP homes with no labels used (independent check).
- **Adoption scenarios** (5%, 15%, 30%, nested, seed 20261112) add that load to randomly chosen homes without electric heat today, scaled per home by the donors' spread of heating size. System peaks are recomputed per scenario, and a sweep finds the adoption level at which winter overtakes summer.
- **Batteries in winter** are re-run with coordinated fleet recharge, because the base rule empties batteries after the first day of a cold snap, and free recharge creates a new night peak.
- **3 kVA lens.** For adopters, can 1 or 2 battery units keep the home within its old peak + 3 kVA (the headroom rule for 200 A service)? This is the electrification-enablement and avoided-service-upgrade case.

## 8. Validation

The rule from the plan: **every benchmark either sets a threshold or checks one, never both.** Labels, the label-implied owner counts and EIA RECS saturation figures are only ever checks. [validation.md](validation.md) has the full table (37 checks) and a circularity audit listing where each threshold comes from. In short:

- **Classification:** 5-fold CV AUC against the read-only benchmark; out-of-customer and out-of-time AUC; labeled recall reported separately for L2, L1 and no-signature EVs; PV precision/recall against the complete list.
- **Stability:** flags, heat type, EV class, segments and archetypes are refit on interleaved odd and even weeks (both halves cover every season) and compared with Cohen's κ / ARI. Seasonal sanity is checked on its own: heating flags must add load in winter and not summer, cooling flags the opposite.
- **Clustering:** silhouette and Davies–Bouldin vs k, 20-resample bootstrap ARI, Kruskal–Wallis effect sizes between segments.
- **Plausibility:** appliance shares against RECS 2020 New England, never used for tuning, and allowed to fail.
- **Batteries:** zero power, SOC or export violations, energy balance closes to 1e-5 kWh, and no recharge sets a new customer peak.

Three checks are designed to be able to fail (estimated c for EV and HP, and the EV flag count vs the implied 744). All three fail, and that failure is the label-provenance finding.

## 9. Use of an LLM

Claude was used at build time, in the coding session, to write code and to draft the segment cards, playbook text and deck narrative from **aggregate** tables. There is no runtime API call, no customer-level data was sent to it, and the app is deterministic. The card templates contain no literal numbers: `09_insights.py` fills every number from the outputs and checks every qualitative claim against the facts (the script exits with an error if any claim fails).

## 10. Limitations

- **Labels are not a random sample** (SCAR fails), so PU outputs are rankings; owner counts come from physics flags, not from label-based estimates.
- **Hourly resolution** cannot resolve fast-cycling loads: water heater and hot tub flags are low confidence (the water-heater share is far below RECS) and are not used downstream.
- **Delivered-only metering** hides PV generation and exports; PV homes' load and battery value are lower bounds.
- **Avoided-cost $/kW-yr and the TOU tariff are placeholders** pending Avangrid's figures; MW of relief does not depend on them, the $ values scale linearly.
- **One weather station, one year.** Peaks and scenarios reflect the 2025–26 weather year; there is no geography, so feeder-level headroom cannot be assessed.
- **Appliance → peak links are associations**, not causal effects.
