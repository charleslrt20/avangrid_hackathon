"""Task 1.2: per-customer feature table.

Outputs
  data_processed/features.parquet         one row per customer (all 34,238; filter on in_model)
  data_processed/shape_profiles.parquet   72-dim seasonal shape (season x hour / annual mean)
  outputs/summary/system_peaks.csv        the system's top summer / winter hours (aggregate)
  docs/features.md                        feature dictionary, coverage, single-feature AUC checks
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402

from avangrid import config as C  # noqa: E402
from avangrid import io  # noqa: E402
from avangrid.features.build import DESCRIPTIONS, build_features  # noqa: E402
from avangrid.features.events import FEATURE_CONFIDENCE  # noqa: E402


def main() -> None:
    t0 = time.time()
    C.ensure_dirs()
    cust = io.load_customers()
    hours = io.load_hours()
    mat = io.load_matrix(mmap=False)
    feats, prof, peaks = build_features(mat, hours, cust)
    print(f"features built in {time.time() - t0:.0f}s: {feats.shape}")

    feats.to_parquet(C.FEATURES, index=False)
    prof.to_parquet(C.DATA_PROC / "shape_profiles.parquet", index=False)

    rows = []
    for s in ("summer", "winter"):
        for rank, col in enumerate(peaks[s], 1):
            h = hours.iloc[col]
            rows.append({"season": s, "rank": rank, "date": h.usage_date.date(), "hour_ending": int(h.hour_ending),
                         "temp_f": round(float(h.temp), 1), "system_kwh": round(float(peaks["total"][col]), 0)})
    sp = pd.DataFrame(rows)
    sp.to_csv(C.OUTPUTS_SUMMARY / "system_peaks.csv", index=False)

    # ---- documentation + checks (single-feature AUCs are *checks*, never used to set thresholds)
    m = feats[feats.in_model]
    lines = ["# Feature dictionary (task 1.2)", "",
             f"Built by `scripts/04_features.py` in {time.time() - t0:.0f}s for {len(feats):,} customers "
             f"({int(feats.in_model.sum()):,} in the modeling population). Thresholds come from equipment "
             "ratings in `src/avangrid/config.py`, never from labels.", "",
             "Confidence reflects what hourly data can resolve: fast cycling loads (water heater, hot tub) "
             "average into small hourly bumps, so those scores are tagged low.", "",
             "## System peaks", ""]
    for s in ("summer", "winter"):
        q = sp[sp.season == s]
        lines.append(f"- **{s.title()}**: top {C.PEAK_TOP_HOURS} hours span {q.date.nunique()} days "
                     f"({q.date.min()} to {q.date.max()}), hour-ending {q.hour_ending.min()}-{q.hour_ending.max()} "
                     f"(mode {q.hour_ending.mode().iat[0]}), {q.temp_f.min()}-{q.temp_f.max()} °F; "
                     f"mean system load {q.system_kwh.mean():,.0f} kWh/h.")
    ratio = sp[sp.season == "winter"].system_kwh.mean() / sp[sp.season == "summer"].system_kwh.mean()
    lines += ["", f"Winter coincident peak is {ratio:.0%} of the summer coincident peak today.", "",
              "## Features", "",
              "| feature | group | confidence | definition | non-null (model pop.) | median | AUC EV | AUC HP | AUC PV |",
              "|---|---|---|---|---|---|---|---|---|"]
    for f, (grp, conf, desc) in DESCRIPTIONS.items():
        x = m[f]
        aucs = []
        for lab in ("label_ev", "label_hp", "label_pv"):
            ok = x.notna()
            aucs.append(f"{roc_auc_score(m.loc[ok, lab], x[ok]):.2f}" if m.loc[ok, lab].nunique() == 2 else "-")
        lines.append(f"| `{f}` | {grp} | {conf} | {desc} | {x.notna().mean():.1%} | {x.median():.3g} | "
                     + " | ".join(aucs) + " |")
    lines += ["", "AUC = how well the single raw feature ranks labeled vs unlabeled customers "
              "(0.5 = no signal, <0.5 = signal in the other direction). Unlabeled customers include "
              "unlabeled owners (PU setting), so these understate true separability.", "",
              f"Detector confidence tags: {FEATURE_CONFIDENCE}.", "",
              "Also stored: 12 monthly weather-normalised residuals (`resid_mXX_mon`), seasonal means/peaks, "
              "and 72 shape-profile columns in `shape_profiles.parquet`.", ""]
    nan_cols = m.columns[m.isna().any()].tolist()
    lines += ["## Missing values (modeling population)", ""]
    for c in nan_cols:
        lines.append(f"- `{c}`: {m[c].isna().sum():,} NaN"
                     + (" (no L2 blocks → no block statistics)" if c.startswith("ev_l2_block") or c == "ev_l2_evening_share" else "")
                     + (" (heat slope ≈ 0 → ratio undefined)" if c == "heat_break_ratio" else ""))
    (C.DOCS / "features.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {C.FEATURES}, shape_profiles.parquet, system_peaks.csv, docs/features.md "
          f"in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
