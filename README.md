# Avangrid 2026 Hackathon: AMI electrification and behind-the-meter battery framework

**Team 08 · MIT Climate Hackers**: Conor Mcmanamy, Charlotte Adriaenssens, Charles Loriot

This repo is a tool that takes one year of hourly smart-meter (AMI) data for 34,238 residential customers, plus one
weather series, and:

1. **estimates each home's appliance inventory**: heating (heat pump, heat pump + backup, resistive), cooling
   (central vs room AC), EV charging (Level 1 / Level 2), solar PV, pool pump, and (low confidence) water heater and hot tub;
2. **segments customers** by load behaviour and measures how much each segment adds to the summer and winter system peaks;
3. **identifies customers eligible for programs and batteries**: it ranks and sizes behind-the-meter batteries by
   avoided buildout cost, flags demand-flexibility programs per home, and tests both in a winter-peaking future with
   more heat pumps.

The main output is one table with a row per customer (`outputs/customer_results.csv`, see [The tool's
output](#the-tools-output-one-row-per-customer)). The deck's figures and numbers, a demo app and one results page per stage in
[docs/](docs/) are built from the same run.

**Contents:** [Main findings](#main-findings) · [Quick start](#quick-start-for-a-fresh-clone) ·
[Methodology](#methodology) · [Algorithms](#algorithms-employed) · [The tool's output](#the-tools-output-one-row-per-customer) ·
[Pipeline steps](#pipeline-steps) · [Demo app](#demo-app) · [Deck](#deck) · [Repo layout](#repository-layout) ·
[Assumptions](#assumptions-and-placeholders) · [Troubleshooting](#troubleshooting) · [Data handling](#data-handling-and-llm-use)

## Main findings

- **The labels are not a random sample.** The brief says labeled EV and heat-pump customers are a random 25% of owners. Estimated from the data, that share is 3.5% (EV) and 1.1% (HP), and physics finds 2,582 home chargers where the labels imply about 744. PU-learning scores are therefore used to rank customers, not as probabilities.
- **PV is found from the metering itself.** Meters record delivered energy only, so exports appear as exact zeros at midday. That rule matches the PV list at 92% precision / 90% recall and finds 31 unregistered systems.
- **Electric-heat homes drive the winter peak:** 16% of customers, 34% of the winter coincident peak. The system turns winter-peaking at about 20% heat-pump adoption (range 20–36%).
- **Batteries:** 2,263 Tier 1 homes, with relief computed by energy-feasible dispatch (state of charge tracked hour by hour across multi-day events). At placeholder avoided-cost values, avoided buildout alone (~$335 per home per year) does not pay for a battery. In winter, batteries only help if the fleet recharges in a coordinated way.

## Quick start (for a fresh clone)

### 1. What you need

- **macOS or Linux** (on Windows, use WSL: the runner is a bash script).
- **Python 3.13**. Check with `python3.13 --version`. If it's missing: `brew install python@3.13` on macOS, or the
  installer from python.org.
- **About 16 GB of RAM and 5 GB of free disk** (the processed load matrix alone is 1.2 GB).
- **The hackathon data**, which is *not* in this repo. Avangrid sent it to each team by email and SharePoint. It is for
  use in this hackathon only, so never commit or share it.
- Optional: Google Chrome to retake app screenshots.

### 2. Install

```bash
git clone https://github.com/charleslrt20/avangrid_hackathon.git
cd avangrid_hackathon
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` pins the exact package versions the pipeline was checked with.

### 3. Add the data

Create a `Data/` folder in the repo root and copy the six files in, keeping their original names:

```
Data/
├── 2026 Avangrid Hackathon Data_1_10000.zip
├── 2026 Avangrid Hackathon Data_10001_20000_v2.zip
├── 2026 Avangrid Hackathon Data_20001_30000.zip
├── 2026 Avangrid Hackathon Data_30001_35725.zip
├── EV_Heat Pump_Solar PV Customers.xlsx
└── Weather Data_Avangrid_Hackathon .xlsx        (note the space before .xlsx)
```

Don't unzip the AMI files: the pipeline streams them straight into Parquet.

### 4. Run everything

```bash
bash scripts/run_all.sh --from-raw
```

This takes about 15 minutes on a recent laptop: about 3 minutes to convert the raw data into `data_processed/`, then
about 12 for the analysis (5 of which is validation). After the first run, `bash scripts/run_all.sh` (without
`--from-raw`) skips the conversion. If the venv isn't activated, prefix the command with `PYTHON=.venv/bin/python`.

**It worked if** the run ends with `done`, step `09_insights.py` prints `49/49 claim checks hold`, and
`outputs/customer_results.csv` exists. The pipeline is deterministic (fixed seeds), so your `outputs/summary/*.csv`,
`docs/figures/` and `docs/insights/` should come out identical to the committed ones: `git status` shows no changes
there. Only the runtime lines in a few `docs/*.md` files change, plus the list of brief PDFs in `docs/data_audit.md`
(those PDFs are not in the repo).

### 5. Look at the results

- `outputs/customer_results.csv`: the per-customer inventory and eligibility table (open in Excel or pandas).
- `streamlit run app/streamlit_app.py`: the demo app (see [Demo app](#demo-app)).
- `docs/methodology.md`, then the results page for any stage you care about (listed under [Pipeline steps](#pipeline-steps)).

## Methodology

A summary is below. [docs/methodology.md](docs/methodology.md) has the full method with equations, and each stage has a
results page in `docs/`.

1. **Data preparation.** The AMI zips are streamed into Parquet with DuckDB, then into a dense matrix of
   34,238 customers × 8,760 hours, with weather aligned to it (AMI hour-ending 1–24 vs weather clock hours 0–23).
   Data checks showed the meters are **delivered-only** (never negative, so PV exports show as exact zeros). Stuck
   meters, data errors and accounts with under 180 days are left out of model fitting, which leaves a modeling
   population of 33,927.
2. **Features (one row per home).** A change-point regression of daily kWh on temperature gives the heating and
   cooling slopes and balance points, plus an extra slope below 20 °F (the heat-pump signature) and wind and humidity
   terms. Event detectors find Level-2 charging blocks and Level-1 overnight plateaus. Further features cover
   PV zero structure, pool-pump timer steps, seasonality and load shape, and each home's load during the system's
   top 1% of hours in summer and in winter (its coincident peak).
3. **Appliance classification.** Each flag is a physics rule with thresholds from equipment ratings (for example
   L2 chargers 6–11.5 kW). Labels are never used to set a threshold. Heat type comes from the *shape* of the
   temperature response: a heat pump's slope steepens in deep cold, resistive heat stays linear. For EV and heat
   pumps, positive-unlabeled (PU) learning ranks customers. The labels turned out not to be a random sample, so the
   PU scores are used as rankings and confidence grades, not as probabilities.
4. **Segmentation.** There are three views of the same homes: rule-based **business segments** (the ones programs
   act on), **K-Means archetypes** of daily and seasonal shape, and an **NMF decomposition** that writes each home as
   a mix of load parts (k chosen without labels, by reproducibility on odd vs even weeks). IsolationForest flags the
   most unusual homes.
5. **Drivers.** Weather → load is causal (weather is exogenous). Segment and appliance → system peak are
   associations, reported as each group's share of customers vs its share of the summer and winter peaks, with
   bootstrap confidence intervals.
6. **Batteries and demand flexibility.** A battery of 2 × Enphase IQ 10C is simulated hour by hour for each candidate
   home over the whole year. State of charge carries across days, discharge is capped by power, energy and the
   home's own load, and recharge happens overnight without creating a new peak. We claim only the kW a battery can
   sustain through the peak window. A realistic variant calls events on forecast-hottest and coldest days. Relief is
   valued as **avoided buildout cost** (capacity, transmission and distribution $/kW-yr), and a priority score sorts
   candidates into Tier 1 / 2 / 3. Demand-flex options (AC and heat shed, managed EV charging) include rebound.
7. **Winter-peaking future.** Heat-pump load is measured from labeled heat-pump homes, then added to 5–30% of homes
   on the observed weather year. This finds the adoption level where winter overtakes summer, what batteries can do
   against longer winter events (coordinated fleet recharge is essential), and the 3 kVA electrification-headroom check.
8. **Validation.** One rule throughout: a benchmark either sets a threshold or checks one, never both. There are 37
   checks: cross-validated AUC, out-of-time AUC, stability on interleaved odd vs even weeks (Cohen's κ / ARI),
   seasonal sanity, plausibility against the EIA RECS survey, and battery energy-balance checks. See
   [docs/validation.md](docs/validation.md).

## Algorithms employed

| Algorithm | Used for | Code |
|---|---|---|
| Change-point regression (least squares over a balance-point grid) | heating / cooling slopes, deep-cold slope break, wind and humidity effects | `src/avangrid/features/weather.py` |
| Rule-based event detectors | L2 charging blocks, L1 overnight plateaus, pool timer steps, PV zero structure, water-heater / hot-tub bumps | `src/avangrid/features/events.py`, `shape.py` |
| Physics-gated classifiers | appliance flags and heat type (HP, HP + backup, resistive) | `src/avangrid/classify.py` |
| PU learning (Elkan–Noto) with logistic regression | EV and heat-pump ranking, odds ratios, estimate of the label rate c | `classify.py` (scikit-learn) |
| Bagged PU LightGBM + SHAP | EV ranking (chosen because it beats logistic on held-out recall) | `classify.py` (LightGBM, SHAP) |
| Rule-based segmentation | business segments | `src/avangrid/segment.py` |
| K-Means | shape archetypes (72-dim seasonal profiles) | `segment.py` (scikit-learn) |
| IsolationForest | anomalous homes | `segment.py` (scikit-learn) |
| Non-negative matrix factorisation + Hungarian matching | load-part decomposition, k chosen by odd/even-week reproducibility | `src/avangrid/decompose.py` (scikit-learn, SciPy) |
| Poisson bootstrap, day-shuffle permutation test, Kruskal–Wallis | confidence intervals and significance of peak shares, weather slopes and segment differences | `src/avangrid/drivers.py`, `segment.py` |
| SOC-constrained battery dispatch (perfect foresight and forecast-based) | battery relief, sizing, avoided cost, priority tiers | `src/avangrid/bess.py` |
| Scenario simulation | heat-pump adoption, winter crossover, coordinated recharge, 3 kVA lens | `src/avangrid/future.py` |
| Cohen's κ, adjusted Rand index, silhouette / Davies–Bouldin | stability and cluster-quality checks | `src/avangrid/validate.py` |

All of it runs on a laptop CPU with numpy, pandas, DuckDB, scikit-learn, LightGBM and SciPy. No deep learning and no
external API.

## The tool's output: one row per customer

`scripts/13_customer_results.py` (last step of `run_all.sh`, under a second) writes
`outputs/customer_results.csv` and `.parquet`. These are customer-level files, so they are gitignored. Rows are sorted by
battery priority. Aggregate counts of every flag are committed in
[outputs/summary/program_eligibility.csv](outputs/summary/program_eligibility.csv).

| Column group | Columns |
|---|---|
| Who | `customer_id`, `in_model` (False = excluded meter), `segment`, `archetype`, `anomaly` |
| Load | `annual_kwh`, `peak_kw`, `summer_peak_kw`, `winter_peak_kw` (mean load in the system's top 88 hours of each season) |
| Heating | `heat_type` (HP / HP+backup / resistive / electric (type unclear) / none/non-electric), `heat_kw_at_0f` |
| Cooling | `ac_type` (central / room/partial / none/minimal), `cool_kw_at_90f` |
| Other appliances | `ev_class` (L2 / L1 / no home-charging signature), `ev_charger_kw`, `ev_confidence`, `ev_rank_pct`, `hp_confidence`, `hp_rank_pct`, `solar_pv`, `pv_with_battery`, `pool_pump`, `water_heater_lowconf`, `hot_tub_lowconf` |
| Avangrid labels | `label_ev`, `label_hp`, `label_pv` (for comparison only) |
| Recommended program | `program`, `program_timing` (Now / Next / Watch / None), from the segment playbook in [docs/insights/playbook.md](docs/insights/playbook.md) |
| Eligibility flags | `battery_tier_1_2`, `managed_ev_charging` (EV charging detected), `ac_thermostat_dr` (central AC), `heat_dr` (electric heat), `heat_pump_conversion` (resistive heat), `pv_plus_storage` (PV detected) |
| Battery screening | `battery_candidate`, `battery_tier`, `battery_size` (1 unit / 2 units / 2 units + DR), `battery_priority`, `battery_relief_kw_summer/_winter` (realistic dispatch), `battery_firm_kw_summer/_winter`, `avoided_usd_yr_mid`, `dr_relief_kw_summer/_winter` |

`*_rank_pct` columns are rankings (1 = most EV- or heat-pump-like), not probabilities. Columns ending `_lowconf` are
low confidence because hourly data blurs short appliance cycles. Excluded meters get no program flags.

## Pipeline steps

`scripts/run_all.sh` runs these in order. Each script can also be run on its own (`python scripts/<name>.py` from the
repo root) once the steps before it have run.

| Step | Script | Writes | Results page |
|---|---|---|---|
| Convert raw data (`--from-raw` only) | `01`–`03`, `00_data_audit.py` | `data_processed/` | [data_audit.md](docs/data_audit.md) |
| Data checks | `03b_checks.py` | exclusion list | [data_checks.md](docs/data_checks.md) |
| Features | `04_features.py` | `data_processed/features.parquet` | [features.md](docs/features.md) |
| Appliance classification | `05_classify.py` | `outputs/classify.parquet`, `models/` | [classification.md](docs/classification.md) |
| Segmentation | `06_segment.py` | `outputs/segments.parquet` | [segmentation.md](docs/segmentation.md) |
| NMF decomposition, exemplar homes | `06b_decompose.py` | `outputs/nmf_mix.parquet` | [decomposition.md](docs/decomposition.md) |
| Drivers of load and peaks | `07_drivers.py` | summary tables | [drivers.md](docs/drivers.md) |
| Battery and demand-flex screening | `08_bess.py` | `outputs/bess_results.parquet` | [bess.md](docs/bess.md) |
| Heat-pump adoption scenarios | `08b_future.py` | `outputs/future_adopters.parquet` | [future.md](docs/future.md) |
| Validation (37 checks) | `09a_validate.py` | summary tables | [validation.md](docs/validation.md) |
| Segment cards, program playbook | `09_insights.py` | `docs/insights/` | [playbook.md](docs/insights/playbook.md) |
| Deck figures | `10_figures.py` | `docs/figures/` | [figures/](docs/figures/) |
| Per-customer results (the tool's output) | `13_customer_results.py` | `outputs/customer_results.csv` | this README |

Every step also writes aggregate tables to `outputs/summary/` (committed) and figures to `docs/figures/`.

## Demo app

```bash
streamlit run app/streamlit_app.py
```

The app opens in the browser and runs on the cached outputs, so run the pipeline first. Each view loads in well under
a second. There are four views:
- **Overview**: the system story and the program playbook.
- **Customer lookup**: one home's load carpet, its weather response, "why flagged" evidence for each appliance, and
  battery dispatch with state of charge.
- **Segment explorer**: the insight card for each segment.
- **Battery ranking**: filter by tier, segment and size, and click a home to see its dispatch.

Customer IDs are shown as aliases by default (sidebar toggle). `python app/screenshots.py` (needs
`pip install playwright` and Google Chrome) retakes `docs/figures/app_*.png` from a running app.

## Deck

The slides are kept in the team's Google Drive, not in this repo. Their figures come from `docs/figures/`
(`10_figures.py`) and their numbers from the aggregate tables in `outputs/summary/`.

## Repository layout

```
scripts/        numbered pipeline steps (run in order by run_all.sh)
src/avangrid/   the package: config.py (every constant and assumption), io.py (loaders),
                features/ (weather, events, shape), classify, segment, decompose, drivers, bess, future,
                validate, insights + insight_text (cards and playbook), plots, app_data (app data layer)
app/            Streamlit demo and screenshot script
docs/           methodology.md, one results page per stage, figures/, insights/
outputs/        customer-level results (gitignored); outputs/summary/ aggregate CSVs (committed)
data_processed/ Parquet + load matrix built from Data/ (gitignored)
models/         fitted PU models (gitignored)
Data/           raw hackathon data, which you add yourself (gitignored)
PLAN.md         project plan, task list and validation rules
```

**Where to start reading the code:** `src/avangrid/config.py` (all thresholds and assumptions, with comments),
then `scripts/05_classify.py` → `src/avangrid/classify.py` for the appliance logic, and `src/avangrid/bess.py` for the
battery dispatch.

## Assumptions and placeholders

`src/avangrid/config.py` holds every assumption, so changing a value and rerunning updates everything downstream,
including the figures and summary tables.

- **Battery:** 2 × Enphase IQ 10C (20.16 kWh, 7.68 kW, 89.4% round-trip efficiency), from the brief.
- **3 kVA headroom rule** for 200 A service, from the brief.
- **Placeholders, still to confirm with Avangrid:** avoided capacity, transmission and distribution-deferral values
  ($/kW-yr, low/mid/high) and the 7% discount rate. These change every $ figure but none of the MW or kW results.
  `config.py` also defines a TOU tariff for a bill-savings view that was never built; nothing uses it.

## Troubleshooting

| Problem | Fix |
|---|---|
| `FileNotFoundError` for a zip or `.xlsx` | Check the file names in `Data/` against step 3 exactly (including the space in `Weather Data_Avangrid_Hackathon .xlsx`). |
| `ModuleNotFoundError` (pandas, duckdb, ...) | Activate the venv (`source .venv/bin/activate`) or run with `PYTHON=.venv/bin/python bash scripts/run_all.sh`. |
| `python3.13: command not found` | Install Python 3.13 (step 1). Another 3.11+ Python may work, but the pinned versions were checked on 3.13. |
| A later step fails with a missing `outputs/...parquet` | Run the earlier steps first: `bash scripts/run_all.sh` runs them all in order. |
| Out of memory | Close other apps; the pipeline memory-maps the 1.2 GB load matrix and needs about 16 GB of RAM in total. |
| The app fails with a missing-file error | Run the pipeline before `streamlit run app/streamlit_app.py`. |

## Data handling and LLM use

The data is Avangrid's and is for use in this hackathon only. Raw data, `data_processed/`, customer-level outputs and
fitted models are gitignored, and only aggregates are committed. Keep the repo private and never commit anything from
`Data/`.

Claude (Anthropic) was used to help write the code.
