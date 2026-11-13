# Avangrid 2026 Hackathon: AMI electrification and behind-the-meter battery framework

From one year of hourly smart-meter (AMI) data for 34,238 residential customers plus one weather series, this
pipeline:

1. **classifies appliances** (EV Level 1 / Level 2, heat pump vs resistive heat, central AC, solar PV, pool, water heater, hot tub),
2. **segments customers** by load behaviour and links segments to the summer and winter system peaks, and
3. **screens and sizes behind-the-meter batteries and demand flexibility** by avoided buildout cost, today and in a winter-peaking future with more heat pumps.

Every number in the deck is written by a script in this repo. The method is in
[docs/methodology.md](docs/methodology.md); each stage has its own results page in [docs/](docs/).

## Main findings

- **The labels are not a random sample.** The brief says labeled EV and heat-pump customers are a random 25% of owners. Estimated from the data, that share is 3.5% (EV) and 1.1% (HP), and physics finds 2,582 home chargers where the labels imply about 744. PU-learning scores are therefore used to rank customers, not as probabilities.
- **PV is found from the metering itself.** Meters record delivered energy only, so exports appear as exact zeros at midday. That rule matches the PV list at 92% precision / 90% recall and finds 31 unregistered systems.
- **Electric-heat homes drive the winter peak:** 16% of customers, 34% of the winter coincident peak. The system turns winter-peaking at about 20% heat-pump adoption (range 20–36%).
- **Batteries:** 2,263 Tier 1 homes, with relief computed by energy-feasible dispatch (state of charge tracked hour by hour across multi-day events). At placeholder avoided-cost values, avoided buildout alone (~$335 per home per year) does not pay for a battery. In winter, batteries only help if the fleet recharges in a coordinated way.

## Requirements

- Python 3.13 (macOS or Linux), about 16 GB RAM, about 5 GB free disk for `data_processed/` and outputs.
- The raw hackathon data (not in this repo; it is confidential).
- Optional: Node.js ≥ 18 to rebuild the deck; Google Chrome to capture app screenshots.

## Setup

```bash
git clone <this repo> && cd Avangrid_hackathon
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` pins the exact versions the pipeline was checked with. Put the raw files in `Data/`, with the names
they were distributed under:

```
Data/
├── 2026 Avangrid Hackathon Data_1_10000.zip
├── 2026 Avangrid Hackathon Data_10001_20000_v2.zip
├── 2026 Avangrid Hackathon Data_20001_30000.zip
├── 2026 Avangrid Hackathon Data_30001_35725.zip
├── EV_Heat Pump_Solar PV Customers.xlsx
└── Weather Data_Avangrid_Hackathon .xlsx        (note the space before .xlsx)
```

## Run

```bash
bash scripts/run_all.sh --from-raw   # first time: build data_processed/ from Data/, then run everything
bash scripts/run_all.sh              # afterwards: rerun from data_processed/
```

Set `PYTHON=.venv/bin/python` if the venv is not activated. From a fresh clone, the full `--from-raw` run takes
about 15 minutes on a 10-core laptop: about 3 minutes to build `data_processed/`, then about 12 for the
pipeline, of which about 5 is validation. The pipeline is deterministic (fixed seeds in `config.py`). A fresh clone
in a clean venv reproduces `data_processed/` byte for byte, plus every committed `outputs/summary/*.csv`, figure and
insight card. The only `docs/*.md` lines that change are runtimes, and the audit's rows for the brief PDFs, which
are not in the repo.

| Step | Script | Writes |
|---|---|---|
| Convert and validate raw data | `01`–`03`, `00_data_audit.py` | `data_processed/` (Parquet, `load_matrix.npy` 34,238 × 8,760, aligned weather), `docs/data_audit.md` |
| Data checks | `03b_checks.py` | `docs/data_checks.md`, exclusion list |
| Features | `04_features.py` | `data_processed/features.parquet`, `docs/features.md` |
| Appliance classification | `05_classify.py` | `outputs/classify.parquet`, `models/`, `docs/classification.md` |
| Segmentation | `06_segment.py` | `outputs/segments.parquet`, `docs/segmentation.md` |
| NMF decomposition, exemplars | `06b_decompose.py` | `outputs/nmf_mix.parquet`, `docs/decomposition.md` |
| Drivers of load and peaks | `07_drivers.py` | `docs/drivers.md` |
| Battery and demand-flex screening | `08_bess.py` | `outputs/bess_results.parquet`, `docs/bess.md` |
| Heat-pump adoption scenarios | `08b_future.py` | `outputs/future_adopters.parquet`, `docs/future.md` |
| Validation (37 checks) | `09a_validate.py` | `docs/validation.md` |
| Segment cards, program playbook | `09_insights.py` | `docs/insights/` |
| Deck figures | `10_figures.py` | `docs/figures/` |

Every step also writes aggregate tables to `outputs/summary/` and figures to `docs/figures/`.

## Demo app

```bash
streamlit run app/streamlit_app.py
```

Runs on the cached outputs (each view loads in well under 3 s). Four views: overview with the program playbook;
customer lookup (load carpet, weather response, "why flagged" evidence, battery dispatch with state of charge,
NMF mix); segment explorer (insight cards); battery ranking. Customer IDs are hidden by default (sidebar toggle).
`python app/screenshots.py` (needs `pip install playwright` and Google Chrome) saves `docs/figures/app_*.png` from
a running app.

## Deck

```bash
(cd deck && npm ci)
bash deck/build.sh            # scripts/11_deck_facts.py → deck/facts.json → deck/build_deck.js → .pptx
bash deck/build.sh --render   # macOS + Microsoft PowerPoint + pymupdf: also renders slide PNGs for QA
```

All deck numbers come from `deck/facts.json`, which `11_deck_facts.py` reads from the pipeline outputs. The build
ends with `scripts/12_number_audit.py`, which checks every number on the slides, in the notes and in the README
against the facts and writes [docs/number_audit.md](docs/number_audit.md); it fails the build on any unmatched number.
`deck/figure_list.md` maps each slide to its figures and source tables.

## Repository layout

```
scripts/        numbered pipeline steps and run_all.sh
src/avangrid/   package: config.py (every constant and assumption), io.py, features/, classify, segment,
                decompose, drivers, bess, future, validate, insights, plots, app_data
app/            Streamlit demo and screenshot script
docs/           methodology.md, one results page per stage, figures/, insights/
deck/           deck builder (pptxgenjs), facts.json, assets, final .pptx
outputs/        customer-level results (gitignored); outputs/summary/ aggregate CSVs (committed)
models/         fitted PU models (gitignored)
PLAN.md         project plan, task list and validation rules
```

## Assumptions to confirm with Avangrid

`src/avangrid/config.py` holds every assumption. The ones that change the $ figures (not the MW) are placeholders:
avoided capacity, transmission and distribution-deferral values ($/kW-yr, low/mid/high) and the TOU tariff for
the bill sidebar. Battery spec: 2 × Enphase IQ 10C (20.16 kWh, 7.68 kW, 89.4% round-trip efficiency); 3 kVA
headroom rule for 200 A service.

## Data handling and LLM use

Raw data, `data_processed/`, customer-level outputs and fitted models are gitignored; only aggregates are
committed. Claude was used at build time to write code and to draft segment cards, playbook text and deck
narrative from aggregate tables. No customer-level data was sent to it, and nothing calls an LLM at runtime.
