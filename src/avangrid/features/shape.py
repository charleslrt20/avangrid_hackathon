"""Load-shape, peak, coincident-peak and PV features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from avangrid import config as C
from avangrid.features.events import nanquantile_axis1

SEASONS = ("winter", "shoulder", "summer")


def seasonal_profiles(mat: np.ndarray, hours: pd.DataFrame) -> pd.DataFrame:
    """72-dim shape: mean load by season x hour of day, divided by the customer's annual mean.
    Shape only (scale removed), so clustering groups *how* homes use energy, not how much."""
    n = mat.shape[0]
    hod = hours.hod.to_numpy()
    season = hours.season.to_numpy()
    mean = np.nanmean(mat, axis=1)
    cols = {}
    for s in SEASONS:
        for h in range(24):
            sel = (season == s) & (hod == h)
            cols[f"prof_{s}_{h:02d}"] = np.nanmean(mat[:, sel], axis=1) / np.where(mean > 0, mean, np.nan)
    return pd.DataFrame(cols, index=np.arange(n))


def system_peak_hours(mat: np.ndarray, rows: np.ndarray, hours: pd.DataFrame) -> dict[str, np.ndarray]:
    """Top-N hours of total load of the modeling population, per season (column indices)."""
    total = np.nansum(mat[rows], axis=0)
    out = {}
    for s, months in (("summer", C.SUMMER_MONTHS), ("winter", C.WINTER_MONTHS)):
        idx = np.flatnonzero(hours.month.isin(months).to_numpy())
        out[s] = idx[np.argsort(total[idx])[::-1][:C.PEAK_TOP_HOURS]]
        days = hours.day.to_numpy()
        daily = pd.Series(total[idx]).groupby(days[idx]).max()
        out[f"{s}_days"] = daily.sort_values(ascending=False).index[:C.PEAK_TOP_DAYS].to_numpy()
    out["total"] = total
    return out


def peak_features(mat: np.ndarray, hours: pd.DataFrame, peaks: dict) -> pd.DataFrame:
    n = mat.shape[0]
    mean = np.nanmean(mat, axis=1)
    season = hours.season.to_numpy()
    hod = hours.hod.to_numpy()
    p95 = nanquantile_axis1(mat[:, :, None], 0.95)[:, 0]
    p99 = nanquantile_axis1(mat[:, :, None], 0.99)[:, 0]
    peak = np.nanmax(mat, axis=1)
    out = pd.DataFrame(index=np.arange(n))
    out["annual_kwh"] = mean * C.N_HOURS
    out["mean_kw"] = mean
    out["peak_kw"] = peak
    out["p99_kw"] = p99
    out["p95_kw"] = p95
    out["load_factor"] = mean / np.where(peak > 0, peak, np.nan)
    out["zero_share"] = np.nanmean(mat == 0, axis=1)
    out["base_kw_p05"] = nanquantile_axis1(mat[:, :, None], 0.05)[:, 0]
    for s in SEASONS:
        out[f"mean_kw_{s}"] = np.nanmean(mat[:, season == s], axis=1)
        out[f"peak_kw_{s}"] = np.nanmax(mat[:, season == s], axis=1)
    out["winter_summer_ratio"] = out.mean_kw_winter / out.mean_kw_summer.where(out.mean_kw_summer > 0)
    out["summer_shoulder_ratio"] = out.mean_kw_summer / out.mean_kw_shoulder.where(out.mean_kw_shoulder > 0)
    wk = hours.weekday.to_numpy()
    out["weekend_weekday_ratio"] = np.nanmean(mat[:, ~wk], axis=1) / np.where(
        np.nanmean(mat[:, wk], axis=1) > 0, np.nanmean(mat[:, wk], axis=1), np.nan)
    ev = (hod >= 16) & (hod < 21)
    out["evening_share"] = np.nanmean(mat[:, ev], axis=1) / np.where(mean > 0, mean, np.nan)
    # Typical hour of the daily maximum (mode over days)
    days = mat.reshape(n, -1, 24)
    filled = np.where(np.isnan(days), -np.inf, days)
    argmax = filled.argmax(axis=2)
    counts = np.stack([(argmax == h).sum(axis=1) for h in range(24)], axis=1)
    out["peak_hod_mode"] = counts.argmax(axis=1)

    # Coincident peak: customer's mean load in the system's top hours, per season
    total = peaks["total"]
    for s in ("summer", "winter"):
        cp = peaks[s]
        out[f"cp_kw_{s}"] = np.nanmean(mat[:, cp], axis=1)
        out[f"cp_share_{s}"] = out[f"cp_kw_{s}"] / (total[cp].mean())        # share of system CP load
        out[f"cp_ratio_{s}"] = out[f"cp_kw_{s}"] / np.where(mean > 0, mean, np.nan)  # CP load / own average
        # persistence: share of top system days on which the customer's max is >= own p95
        top_days = peaks[f"{s}_days"]
        dmax = np.nanmax(days[:, top_days, :], axis=2)
        out[f"peak_day_persistence_{s}"] = np.nanmean(dmax >= p95[:, None], axis=1)
    return out


def pv_features(mat: np.ndarray, hours: pd.DataFrame) -> pd.DataFrame:
    """Delivered-only metering clips exports to 0, so PV shows as exact midday zeros that track the sun."""
    hod = hours.hod.to_numpy()
    month = hours.month.to_numpy()
    midday = np.isin(hod, [10, 11, 12, 13, 14])
    night = np.isin(hod, [0, 1, 2, 3, 4])
    summer = np.isin(month, [4, 5, 6, 7, 8])
    winter = np.isin(month, [12, 1])
    out = pd.DataFrame({
        "pv_midday_zero_summer": np.nanmean(mat[:, midday & summer] == 0, axis=1),
        "pv_night_zero_summer": np.nanmean(mat[:, night & summer] == 0, axis=1),
        "pv_midday_zero_winter": np.nanmean(mat[:, midday & winter] == 0, axis=1),
    })
    # Midday dip relative to the shoulders of the day (works even when exports are small)
    morning = np.isin(hod, [7, 8])
    evening = np.isin(hod, [17, 18])
    shoulders = (np.nanmean(mat[:, morning & summer], axis=1) + np.nanmean(mat[:, evening & summer], axis=1)) / 2
    out["pv_midday_dip"] = 1 - np.nanmean(mat[:, midday & summer], axis=1) / np.where(shoulders > 0, shoulders, np.nan)
    # Seasonal contrast: zeros at midday in summer, much less in winter (sun is lower)
    out["pv_zero_season_contrast"] = out.pv_midday_zero_summer - out.pv_midday_zero_winter
    return out
