#!/usr/bin/env bash
# Reproduce every output from data_processed/.
#   bash scripts/run_all.sh            # from processed data (03b onward)
#   bash scripts/run_all.sh --from-raw # also rebuild data_processed/ from the raw zips in Data/
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python3}"

run() { echo "== $1"; "$PY" -W ignore "scripts/$1"; }

if [[ "${1:-}" == "--from-raw" ]]; then
  run 01_convert_ami.py
  run 02_prepare_weather.py
  run 03_validate_and_build_matrix.py
  run 00_data_audit.py
fi

run 03b_checks.py
# Pipeline steps are added here as they land (PLAN.md §3):
for s in 04_features.py 05_classify.py 06_segment.py 06b_decompose.py 07_drivers.py 08_bess.py 08b_future.py 09a_validate.py 09_insights.py; do
  if [[ -f "scripts/$s" ]]; then run "$s"; else echo "-- skip $s (not built yet)"; fi
done
echo "done"
