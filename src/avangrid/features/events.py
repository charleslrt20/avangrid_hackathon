"""Appliance event detectors on hourly load. Thresholds come from equipment ratings (config).

Every detector compares a customer with their *own* baseline, so large and small homes are
treated alike. Hourly data averages fast cycling (water heater, hot tub, HP compressor), so
those scores are tagged low confidence (FEATURE_CONFIDENCE).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from avangrid import config as C

FEATURE_CONFIDENCE = {
    "ev_l2": "high", "ev_l1": "medium", "pool": "medium",
    "water_heater": "low", "hot_tub": "low",
}


def own_baseline(days: np.ndarray, day_month: np.ndarray, q: float = C.BASELINE_QUANTILE) -> np.ndarray:
    """Customer's q-th percentile load for each (month, hour of day) -> [n, 365, 24]."""
    base = np.empty_like(days)
    for m in range(1, 13):
        sel = day_month == m
        if sel.any():
            base[:, sel, :] = nanquantile_axis1(days[:, sel, :], q / 100.0)[:, None, :]
    return base


def nan_share(cond: np.ndarray, valid: np.ndarray, axis: int = 1) -> np.ndarray:
    """Share of the `valid` entries where `cond` holds (NaN if none are valid).

    A plain np.nanmean(x >= t) is wrong on data with gaps: NaN comparisons evaluate to False, so missing
    readings count as 'no event' and dilute the share for customers with missing days (and halve it on the
    interleaved-week validation halves)."""
    cnt = valid.sum(axis=axis)
    return np.where(cnt > 0, (cond & valid).sum(axis=axis) / np.maximum(cnt, 1), np.nan)


def nanquantile_axis1(a: np.ndarray, q: float) -> np.ndarray:
    """Fast nan-aware quantile along axis 1 (lower-index method). np.nanpercentile is ~50x slower."""
    s = np.sort(a, axis=1)                                   # NaNs sort to the end
    cnt = (~np.isnan(a)).sum(axis=1)
    k = np.floor(q * np.maximum(cnt - 1, 0)).astype(np.int64)
    out = np.take_along_axis(s, k[:, None, ...], axis=1)[:, 0, ...]
    return np.where(cnt > 0, out, np.nan)


def _runs_2d(mask: np.ndarray):
    """True-runs of every row at once. Returns (row, start, length) arrays."""
    n, t = mask.shape
    padded = np.zeros((n, t + 1), dtype=np.int8)
    padded[:, :t] = mask
    flat = np.concatenate([[0], padded.ravel()])
    d = np.diff(flat)
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    rows = starts // (t + 1)
    return rows, starts % (t + 1), ends - starts, starts


def ev_l2(excess: np.ndarray, hours: pd.DataFrame) -> pd.DataFrame:
    """L2 charging blocks: 1-6 h runs >= EV_L2_RUN_KWH above own baseline peaking >= EV_L2_PEAK_KWH,
    counted on mild hours only (no HVAC surges)."""
    n, t = excess.shape
    temp = hours.temp.to_numpy()
    eligible = (temp >= C.MILD_TEMP_F[0]) & (temp <= C.MILD_TEMP_F[1])
    ex = np.nan_to_num(excess, nan=0.0)
    on = (ex >= C.EV_L2_RUN_KWH) & eligible[None, :]
    rows, start, length, flat_start = _runs_2d(on)
    padded = np.zeros((n, t + 1), dtype=np.float32)
    padded[:, :t] = ex
    flat = padded.ravel()
    if len(flat_start):
        csum = np.concatenate([[0.0], np.cumsum(flat, dtype=np.float64)])
        energy = csum[flat_start + length] - csum[flat_start]
        # max over [start, end): reduceat on interleaved (start, end) pairs, keep every other result
        idx = np.column_stack([flat_start, flat_start + length]).ravel()
        peak = np.maximum.reduceat(flat, idx)[::2]
    else:
        energy = peak = np.array([])
    good = (length <= C.EV_L2_MAX_HOURS) & (peak >= C.EV_L2_PEAK_KWH)
    rows, start, length, energy, peak = rows[good], start[good], length[good], energy[good], peak[good]
    hod = hours.hod.to_numpy()
    start_hod = hod[start]
    obs = ~np.isnan(excess)
    weeks = (obs & eligible[None, :]).sum(axis=1) / 168.0
    df = pd.DataFrame({"row": rows, "hod": start_hod, "len": length, "kwh": energy, "peak": peak,
                       "night": np.isin(start_hod, [18, 19, 20, 21, 22, 23, 0, 1, 2])})
    g = df.groupby("row")
    out = pd.DataFrame(index=np.arange(n))
    out["ev_l2_blocks"] = g.size().reindex(out.index, fill_value=0)
    out["ev_l2_per_week"] = out.ev_l2_blocks / np.maximum(weeks, 1.0)
    out["ev_l2_block_kwh"] = g.kwh.median().reindex(out.index)
    out["ev_l2_block_hours"] = g.len.median().reindex(out.index)
    out["ev_l2_block_kw"] = g.peak.median().reindex(out.index)
    out["ev_l2_evening_share"] = g.night.mean().reindex(out.index)
    # regularity: share of observed weeks with at least one block
    week = (hours.day.to_numpy() // 7)[start]
    wk = pd.DataFrame({"row": rows, "week": week}).drop_duplicates().groupby("row").size()
    out["ev_l2_week_share"] = (wk.reindex(out.index, fill_value=0) / np.maximum(np.ceil(weeks), 1)).clip(0, 1)
    return out


def ev_l1(days: np.ndarray, hours: pd.DataFrame) -> pd.DataFrame:
    """L1 overnight plateau: on mild nights, >= 5 of 6 hours (00-05) sit 1.0-2.2 kWh above the
    customer's quiet-night level, with a flat profile."""
    lo, hi = C.EV_L1_KW
    night = [0, 1, 2, 3, 4, 5]
    ntemp = hours.temp.to_numpy().reshape(-1, 24)[:, night].mean(axis=1)
    mild = (ntemp > 45) & (ntemp < 70)
    nl = days[:, mild][:, :, night]
    nm = np.nanmean(nl, axis=2)
    ref = np.nanpercentile(nm, 20, axis=1, keepdims=True)
    lift = nl - ref[:, :, None]
    in_band = (lift >= lo) & (lift <= hi + 0.5)
    flat = np.nanstd(nl, axis=2) < 0.6
    l1 = (in_band.sum(axis=2) >= 5) & flat & ((nm - ref) >= lo) & ((nm - ref) <= hi)
    valid = (~np.isnan(nm)).sum(axis=1)
    return pd.DataFrame({"ev_l1_night_share": l1.sum(axis=1) / np.maximum(valid, 1),
                         "night_quiet_kwh": ref[:, 0]})


def pool(days: np.ndarray, wx: pd.DataFrame) -> pd.DataFrame:
    """Pool pump on a timer: a >= 0.7 kWh step up (and later down) at the *same* hour on most
    pool-season days, independent of temperature (mild summer days), absent in winter."""
    month = wx.month.to_numpy()
    t = wx.temp.to_numpy()
    season = np.isin(month, [6, 7, 8, 9]) & (t >= 60) & (t <= 74)
    winter = np.isin(month, [12, 1, 2])

    def frac(sel: np.ndarray, sign: int) -> np.ndarray:
        dd = np.diff(days[:, sel, :], axis=2) * sign            # [n, d, 23]
        return nan_share(dd >= 0.7, ~np.isnan(dd), axis=1)       # [n, 23]

    on_s, off_s = frac(season, 1), frac(season, -1)
    on_w, off_w = frac(winter, 1), frac(winter, -1)
    # A customer with no pool-season or no winter days cannot show "on in summer, absent in winter": NaN score.
    d_on, d_off = on_s - on_w, off_s - off_w
    ok = ~np.isnan(d_on).all(axis=1)
    on = np.where(ok, np.max(np.nan_to_num(d_on, nan=-np.inf), axis=1), np.nan)
    off = np.where(ok, np.max(np.nan_to_num(d_off, nan=-np.inf), axis=1), np.nan)
    on_h = np.argmax(np.nan_to_num(d_on, nan=-np.inf), axis=1)
    off_h = np.argmax(np.nan_to_num(d_off, nan=-np.inf), axis=1)
    run_h = np.where(ok, (off_h - on_h) % 23, np.nan)
    summer_base = np.nanpercentile(days[:, np.isin(month, [7, 8])].reshape(days.shape[0], -1), 10, axis=1)
    spring_base = np.nanpercentile(days[:, np.isin(month, [4, 5])].reshape(days.shape[0], -1), 10, axis=1)
    return pd.DataFrame({
        "pool_timer_on": on, "pool_timer_off": off, "pool_timer_score": np.minimum(on, off),
        "pool_run_hours": run_h, "pool_on_hod": on_h + 1,
        "summer_base_lift_kwh": summer_base - spring_base,
    })


def water_heater_hot_tub(days: np.ndarray, wx: pd.DataFrame) -> pd.DataFrame:
    """Low-confidence scores. Water heater: morning bump (05-09h) of >= 1.5 kWh over the overnight
    minimum on mild days. Hot tub: isolated night bumps (heater cycling) on mild nights."""
    t = wx.temp.to_numpy()
    mild = (t >= 45) & (t <= 65)
    d = days[:, mild]
    night_min = np.nanmin(d[:, :, 1:5], axis=2)
    morning = np.nanmax(d[:, :, 5:10], axis=2) - night_min
    wh_freq = nan_share(morning >= 1.5, ~np.isnan(morning))
    nights = d[:, :, 0:6]
    q = np.nanpercentile(nights.reshape(d.shape[0], -1), 20, axis=1)[:, None, None]
    bump = (nights - q) >= 1.2
    # isolated: a bump hour whose neighbours are not bumps (cycling, not a plateau like L1/heating)
    iso = bump.copy()
    iso[:, :, 1:] &= ~bump[:, :, :-1]
    iso[:, :, :-1] &= ~bump[:, :, 1:]
    tub = nan_share(iso.sum(axis=2) >= 1, ~np.isnan(nights).all(axis=2))
    return pd.DataFrame({"wh_morning_bump_kwh": np.nanmedian(morning, axis=1), "wh_bump_freq": wh_freq,
                         "hot_tub_cycle_share": tub})
