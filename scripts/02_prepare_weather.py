"""Clean the hourly weather sheet and align it to the AMI hour_ending convention.

Raw weather: local clock time 00:00-23:00 (instantaneous readings), 8,761 rows.
AMI: usage_date + hour_ending 1-24, where hour_ending h covers clock time [h-1, h).

Steps
1. Collapse the duplicated 01:00 reading on the fall-back day (2025-11-02) by averaging.
2. Fill gaps (mostly 2026-03-08..03-11): short gaps (<=3 h) by linear interpolation,
   longer gaps with the same-hour mean of the 3 days before and after, shifted so
   the fill joins the observed values on each side. Filled rows are flagged.
3. Interval value for hour_ending h = mean of the readings at clock h-1 and h
   (hour_ending 24 uses 23:00 and next day's 00:00).
4. Add degree-hours (base 65 F), dew point, and heat index helpers used later.

Output: data_processed/weather_hourly.parquet keyed by (usage_date, hour_ending).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "Data" / "Weather Data_Avangrid_Hackathon .xlsx"
DST = ROOT / "data_processed" / "weather_hourly.parquet"
VARS = ["temp", "humidity", "windspeed"]
BASE_F = 65.0


def fill_gaps(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (filled series, mask of originally missing values)."""
    missing = s.isna()
    out = s.interpolate(limit=3, limit_area="inside")
    long_gap = out.isna()
    if not long_gap.any():
        return out, missing

    # climatological fill: same clock hour, 3 days either side
    shifted = [s.shift(24 * d) for d in (-3, -2, -1, 1, 2, 3)]
    clim = pd.concat(shifted, axis=1).mean(axis=1)
    out[long_gap] = clim[long_gap]

    # add a linear ramp so each gap meets the observed values at both edges
    run_id = (long_gap != long_gap.shift()).cumsum()[long_gap]
    one_h = pd.Timedelta(hours=1)
    for _, idx in run_id.groupby(run_id).groups.items():
        left, right = idx[0] - one_h, idx[-1] + one_h
        e0 = s.get(left, np.nan) - clim.get(left, np.nan)
        e1 = s.get(right, np.nan) - clim.get(right, np.nan)
        e0 = 0.0 if pd.isna(e0) else e0
        e1 = 0.0 if pd.isna(e1) else e1
        out[idx] = out[idx] + np.linspace(e0, e1, len(idx))

    return out.interpolate(limit_area="inside").bfill().ffill(), missing


def dew_point_f(temp_f: pd.Series, rh: pd.Series) -> pd.Series:
    t = (temp_f - 32) / 1.8
    a, b = 17.625, 243.04
    g = np.log(rh.clip(1, 100) / 100) + a * t / (b + t)
    return (b * g / (a - g)) * 1.8 + 32


def heat_index_f(t: pd.Series, rh: pd.Series) -> pd.Series:
    """NWS Rothfusz regression; equals temperature below 80 F."""
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
          - 6.83783e-3 * t**2 - 5.481717e-2 * rh**2 + 1.22874e-3 * t**2 * rh
          + 8.5282e-4 * t * rh**2 - 1.99e-6 * t**2 * rh**2)
    return hi.where(t >= 80, t)


def main() -> None:
    w = pd.read_excel(SRC)
    w["ts"] = pd.to_datetime(w["date"].astype(str) + " " + w["time"].astype(str))
    n_raw = len(w)
    w = w.groupby("ts", as_index=True)[VARS].mean()  # collapses the fall-back duplicate
    full = pd.date_range(w.index.min(), w.index.max() + pd.Timedelta(hours=1), freq="h")
    w = w.reindex(full)

    flags = {}
    for v in VARS:
        w[v], flags[v] = fill_gaps(w[v])
    w["imputed"] = pd.concat(flags, axis=1).any(axis=1)

    # interval mean for hour_ending h = mean(reading at h-1, reading at h)
    nxt = w[VARS].shift(-1)
    iv = (w[VARS] + nxt.fillna(w[VARS])) / 2
    iv["imputed"] = w["imputed"] | w["imputed"].shift(-1, fill_value=False)
    iv = iv.iloc[:-1]  # drop the padded extra hour
    iv["usage_date"] = iv.index.date
    iv["hour_ending"] = (iv.index.hour + 1).astype("int8")

    iv["dew_point"] = dew_point_f(iv["temp"], iv["humidity"])
    iv["heat_index"] = heat_index_f(iv["temp"], iv["humidity"])
    iv["hdh"] = (BASE_F - iv["temp"]).clip(lower=0)  # heating degree-hours
    iv["cdh"] = (iv["temp"] - BASE_F).clip(lower=0)  # cooling degree-hours

    out = iv.reset_index(drop=True)[
        ["usage_date", "hour_ending", "temp", "humidity", "windspeed",
         "dew_point", "heat_index", "hdh", "cdh", "imputed"]
    ]
    out["usage_date"] = pd.to_datetime(out["usage_date"])
    for c in ["temp", "humidity", "windspeed", "dew_point", "heat_index", "hdh", "cdh"]:
        out[c] = out[c].astype("float32").round(2)
    DST.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(DST, index=False)

    print(f"raw rows {n_raw} -> {len(out)} interval rows "
          f"({out.usage_date.dt.date.nunique()} days), imputed {int(out.imputed.sum())}")
    print(out[["temp", "humidity", "windspeed", "hdh", "cdh"]].describe().round(2).to_string())
    print(f"annual HDD65 {out.hdh.sum() / 24:.0f}, CDD65 {out.cdh.sum() / 24:.0f}")


if __name__ == "__main__":
    main()
