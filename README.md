# Avangrid 2026 Hackathon: AMI electrification and BTM battery framework

Classifies customer appliances (EV L1/L2, heat pump vs resistive heat, AC, PV, pool, ...), segments
customers by load behaviour, and screens and sizes behind-the-meter battery (BTM BESS) and demand
flexibility candidates by avoided buildout cost, using one year of hourly AMI data plus weather.
See `PLAN.md` for the full method.

## Setup
```bash
pip install -r requirements.txt
```
Python 3.13. Raw data (not in this repo) goes in `Data/`.

## Run
```bash
bash scripts/run_all.sh --from-raw   # first time: build data_processed/ from Data/
bash scripts/run_all.sh              # afterwards: rerun the pipeline
```

## Layout
- `scripts/` numbered pipeline steps (`00`-`09`) and `run_all.sh`
- `src/avangrid/` package: `config.py` (all constants and assumptions), `io.py` (loaders), models
- `docs/` data audit, data checks, methodology, figures
- `app/` Streamlit demo, `deck/` final slides

Customer-level data and outputs never leave the laptop (`.gitignore`); only aggregates are committed.
