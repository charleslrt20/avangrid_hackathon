"""Assemble the per-customer feature table (task 1.2).

`build_features` takes an optional `day_mask` so the same code can be rerun on interleaved
odd / even weeks for the stability tests (PLAN.md §5).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from avangrid import config as C
from avangrid.features import events as E
from avangrid.features import shape as S
from avangrid.features import weather as W

DESCRIPTIONS = {
    # weather
    "base_kwh_day": ("weather", "high", "Weather-independent daily kWh (change-point intercept)"),
    "heat_slope": ("weather", "high", "kWh/day per °F below the heating balance point"),
    "cool_slope": ("weather", "high", "kWh/day per °F above the cooling balance point"),
    "cold_slope_extra": ("weather", "medium", "Extra kWh/day per °F below 20 °F (HP COP drop / strip backup)"),
    "heat_break_ratio": ("weather", "medium", "(heat_slope + cold_slope_extra) / heat_slope; >1 = steepening in deep cold"),
    "wind_heat_coef": ("weather", "medium", "Heating kWh per HDD per wind s.d. ('leaky home')"),
    "hum_cool_coef": ("weather", "medium", "Cooling kWh per CDD per humidity s.d."),
    "weekend_kwh_day": ("weather", "high", "Weekend minus weekday daily kWh"),
    "heat_bp_f": ("weather", "medium", "Heating balance point, °F"),
    "cool_bp_f": ("weather", "medium", "Cooling balance point, °F"),
    "weather_r2": ("weather", "high", "R² of the change-point model"),
    "heat_slope_rel": ("weather", "high", "heat_slope / mean daily kWh"),
    "cool_slope_rel": ("weather", "high", "cool_slope / mean daily kWh"),
    "resid_trend_per30d": ("trend", "medium", "Trend of weather-normalised daily kWh, kWh/day per 30 days"),
    "resid_max_step": ("trend", "medium", "Max minus min monthly weather-normalised residual"),
    # EV
    "ev_l2_per_week": ("ev", "high", "L2 charging blocks per week of mild hours"),
    "ev_l2_week_share": ("ev", "high", "Share of weeks with at least one L2 block"),
    "ev_l2_block_kwh": ("ev", "high", "Median kWh above baseline per L2 block"),
    "ev_l2_block_kw": ("ev", "high", "Median peak hourly excess in an L2 block (≈ charger kW)"),
    "ev_l2_block_hours": ("ev", "high", "Median L2 block length, h"),
    "ev_l2_evening_share": ("ev", "high", "Share of L2 blocks starting 18:00-02:00"),
    "ev_l1_night_share": ("ev", "medium", "Share of mild nights with a flat 1.0-2.2 kWh overnight plateau"),
    "night_quiet_kwh": ("ev", "high", "Customer's quiet-night hourly load (20th pct of mild nights)"),
    # other appliances
    "pool_timer_score": ("pool", "medium", "Same-hour on and off steps on summer days, minus winter"),
    "pool_run_hours": ("pool", "medium", "Hours between the timer on and off steps"),
    "summer_base_lift_kwh": ("pool", "medium", "Jul-Aug minus Apr-May 10th-pct hourly load"),
    "wh_bump_freq": ("water_heater", "low", "Share of mild days with a ≥1.5 kWh morning bump (hourly data blurs 20-40 min bursts)"),
    "wh_morning_bump_kwh": ("water_heater", "low", "Median morning bump over the overnight minimum"),
    "hot_tub_cycle_share": ("hot_tub", "low", "Share of mild nights with isolated heater-cycling bumps"),
    # PV
    "pv_midday_zero_summer": ("pv", "high", "Share of Apr-Aug 10-15h hours at exactly 0 kWh (exports clipped)"),
    "pv_night_zero_summer": ("pv", "high", "Share of Apr-Aug 00-05h hours at 0 kWh (vacancy check)"),
    "pv_midday_zero_winter": ("pv", "high", "Share of Dec-Jan 10-15h hours at 0 kWh"),
    "pv_zero_season_contrast": ("pv", "high", "Summer minus winter midday zero share"),
    "pv_midday_dip": ("pv", "medium", "1 - midday load / mean of 7-9h and 17-19h load, Apr-Aug"),
    # size, shape, peaks
    "annual_kwh": ("size", "high", "Annual kWh (mean × 8,760)"),
    "peak_kw": ("peak", "high", "Max hourly kWh"),
    "p99_kw": ("peak", "high", "99th percentile hourly kWh"),
    "load_factor": ("peak", "high", "Mean / peak"),
    "zero_share": ("size", "high", "Share of hours at exactly 0"),
    "base_kw_p05": ("size", "high", "5th percentile hourly kWh"),
    "winter_summer_ratio": ("season", "high", "Mean winter / mean summer load"),
    "summer_shoulder_ratio": ("season", "high", "Mean summer / mean shoulder load"),
    "weekend_weekday_ratio": ("shape", "high", "Mean weekend / weekday load"),
    "evening_share": ("shape", "high", "Mean 16-21h load / mean load"),
    "peak_hod_mode": ("shape", "high", "Most common hour of the daily maximum (hour starting)"),
    "cp_kw_summer": ("peak", "high", f"Mean load in the system's top {C.PEAK_TOP_HOURS} summer hours"),
    "cp_kw_winter": ("peak", "high", f"Mean load in the system's top {C.PEAK_TOP_HOURS} winter hours"),
    "cp_share_summer": ("peak", "high", "Customer's share of the summer coincident-peak load"),
    "cp_share_winter": ("peak", "high", "Customer's share of the winter coincident-peak load"),
    "cp_ratio_summer": ("peak", "high", "Summer CP load / own mean load"),
    "cp_ratio_winter": ("peak", "high", "Winter CP load / own mean load"),
    "peak_day_persistence_summer": ("peak", "high", f"Share of the top {C.PEAK_TOP_DAYS} summer system days where own max ≥ own p95"),
    "peak_day_persistence_winter": ("peak", "high", f"Share of the top {C.PEAK_TOP_DAYS} winter system days where own max ≥ own p95"),
}


def build_features(mat: np.ndarray, hours: pd.DataFrame, cust: pd.DataFrame,
                   day_mask: np.ndarray | None = None, peaks: dict | None = None):
    """Returns (features, profiles, peaks). `mat` is not modified."""
    if day_mask is not None:
        mat = mat.copy()
        mat[:, ~np.repeat(day_mask, 24)] = np.nan
    n = mat.shape[0]
    wx = W.daily_weather(hours)
    days = mat.reshape(n, -1, 24)

    daily = W.daily_kwh(mat)
    coef, resid = W.fit_changepoint(daily, wx)
    trend = W.residual_features(resid, wx)

    base = E.own_baseline(days, wx.month.to_numpy())
    excess = (days - base).reshape(n, -1)
    del base
    l2 = E.ev_l2(excess, hours)
    del excess
    l1 = E.ev_l1(days, hours)
    pool = E.pool(days, wx)
    whtub = E.water_heater_hot_tub(days, wx)

    if peaks is None:
        peaks = S.system_peak_hours(mat, cust.row.to_numpy()[cust.in_model.to_numpy()], hours)
    pk = S.peak_features(mat, hours, peaks)
    pv = S.pv_features(mat, hours)
    prof = S.seasonal_profiles(mat, hours)

    feats = pd.concat([coef, trend, l2, l1, pool, whtub, pk, pv], axis=1)
    mean_day = feats.mean_kw * 24
    feats["heat_slope_rel"] = feats.heat_slope / mean_day.where(mean_day > 0)
    feats["cool_slope_rel"] = feats.cool_slope / mean_day.where(mean_day > 0)
    keys = cust[["customer_id", "in_model", "exclude_reason", "very_high_load", "likely_nonresidential",
                 "label_ev", "label_hp", "label_pv", "n_days"]].reset_index(drop=True)
    feats = pd.concat([keys, feats], axis=1)
    prof.insert(0, "customer_id", cust.customer_id.to_numpy())
    return feats, prof, peaks
