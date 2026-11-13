"""Behind-the-meter battery and demand-flexibility screening (task 1.6).

Dispatch is simulated hour by hour for every customer at once (vectorised over customers),
with state of charge carried across days so multi-day heat waves / cold snaps are handled:

  charge   (hours 00-05):  c_h = min(P, (E_max - SOC)/RTE, max(cap - load_h, 0));  SOC += RTE * c_h
  discharge (target hours): level = min(P, (SOC - E_min) / n_target)   (flat, energy-feasible)
                            d_h   = min(level, load_h, SOC - E_min);   SOC -= d_h

RTE is applied on charge (grid supplies c_h, battery stores RTE * c_h). Discharge never exceeds the
home's own load (behind the meter, no export). `cap` = the home's own pre-battery p95 load, so
recharging never creates a new customer peak. Relief that counts = discharge during the system's
top hours, i.e. what the battery actually delivered when the system peaked.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from avangrid import config as C


@dataclass
class DispatchResult:
    discharge: np.ndarray        # [n, 8760] kW
    charge: np.ndarray           # [n, 8760] kW from the grid
    soc_min_seen: np.ndarray     # [n]
    soc_max_seen: np.ndarray     # [n]
    soc_trace: np.ndarray        # [k, 8760] for the rows in trace_rows
    event_level: dict            # day -> [n] planned flat kW on that event day
    soc_start: float
    soc_end: np.ndarray          # [n]


def simulate(load: np.ndarray, targets: dict[int, np.ndarray], battery: C.Battery,
             cap: np.ndarray, trace_rows: np.ndarray | None = None) -> DispatchResult:
    n, t = load.shape
    L = np.nan_to_num(load, nan=0.0).astype(np.float64)
    e_max = battery.kwh * battery.soc_max_frac
    e_min = battery.kwh * battery.soc_min_frac
    p = battery.kw
    soc = np.full(n, e_max)
    dis = np.zeros((n, t), dtype=np.float32)
    chg = np.zeros((n, t), dtype=np.float32)
    trace_rows = np.array([], dtype=int) if trace_rows is None else np.asarray(trace_rows)
    trace = np.zeros((len(trace_rows), t), dtype=np.float32)
    smin, smax = soc.copy(), soc.copy()
    levels = {}
    for d in range(t // 24):
        base = d * 24
        tgt = targets.get(d)
        tgt_set = set() if tgt is None else set(int(h) for h in tgt)
        level = None
        for h in range(24):
            col = base + h
            if h in C.RECHARGE_HOURS and h not in tgt_set:
                c = np.minimum.reduce([np.full(n, p), (e_max - soc) / battery.rte, np.maximum(cap - L[:, col], 0)])
                c = np.maximum(c, 0)
                soc = soc + c * battery.rte
                chg[:, col] = c
            if h in tgt_set:
                if level is None:   # plan a flat level for the day's remaining target hours
                    n_left = sum(1 for x in tgt_set if x >= h)
                    level = np.minimum(p, np.maximum(soc - e_min, 0) / n_left)
                    levels[d] = level
                dd = np.minimum.reduce([level, L[:, col], np.maximum(soc - e_min, 0)])
                soc = soc - dd
                dis[:, col] = dd
            smin = np.minimum(smin, soc)
            smax = np.maximum(smax, soc)
            if len(trace_rows):
                trace[:, col] = soc[trace_rows]
    return DispatchResult(dis, chg, smin, smax, trace, levels, e_max, soc)


def perfect_targets(peaks: dict, hours: pd.DataFrame) -> dict[int, np.ndarray]:
    """Perfect foresight: discharge exactly in the system's top hours, on the days they occur."""
    hod, day = hours.hod.to_numpy(), hours.day.to_numpy()
    out: dict[int, list] = {}
    for s in ("summer", "winter"):
        for col in peaks[s]:
            out.setdefault(int(day[col]), []).append(int(hod[col]))
    return {d: np.array(sorted(v)) for d, v in out.items()}


def realistic_targets(hours: pd.DataFrame, seed: int = C.SEED) -> tuple[dict[int, np.ndarray], dict]:
    """Call EVENT_BUDGET days per season on the hottest / coldest forecast days (forecast = actual
    daily mean temperature + N(0, FORECAST_SD_F)), discharging over a fixed seasonal window."""
    rng = np.random.default_rng(seed)
    d = hours.groupby("day").agg(tmean=("temp", "mean"), month=("month", "first"))
    d["forecast"] = d.tmean + rng.normal(0, C.FORECAST_SD_F, len(d))
    out, called = {}, {}
    for s, months, hot in (("summer", C.SUMMER_MONTHS, True), ("winter", C.WINTER_MONTHS, False)):
        seas = d[d.month.isin(months)]
        days = seas.sort_values("forecast", ascending=not hot).head(C.EVENT_BUDGET_PER_SEASON).index
        called[s] = np.array(sorted(days))
        for day in days:
            out[int(day)] = np.array(C.WINDOW_HOURS[s])
    return out, called


def cp_relief(res: DispatchResult, load: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Average kW delivered during the given system top hours (hours without a reading count as 0)."""
    return res.discharge[:, cols].mean(axis=1)


def event_need(load: np.ndarray, hours: pd.DataFrame, event_days: np.ndarray, window: tuple[int, ...],
               season_months: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    """Energy (kWh) and power (kW) needed to hold the home at its own seasonal median load through the
    window, per event day -> two [n, event_days] arrays."""
    month = hours.month.to_numpy()
    seas = np.isin(month, season_months)
    target = np.nanmedian(np.where(seas[None, :], load, np.nan), axis=1)
    cols = np.array([d * 24 + h for d in event_days for h in window]).reshape(len(event_days), len(window))
    x = np.nan_to_num(load[:, cols.ravel()].reshape(load.shape[0], len(event_days), len(window)), nan=0)
    excess = np.maximum(x - target[:, None, None], 0)
    need_kwh = excess.sum(axis=2)
    need_kw = excess.max(axis=2)
    return need_kwh, need_kw


def size_recommendation(need_kwh: np.ndarray, need_kw: np.ndarray) -> np.ndarray:
    one = C.BATTERY.with_units(1)
    return np.select(
        [need_kwh < 1.0,
         (need_kwh <= one.kwh) & (need_kw <= one.kw),
         (need_kwh <= C.BATTERY.kwh) & (need_kw <= C.BATTERY.kw)],
        ["no battery needed", "1 unit", "2 units"], "2 units + DR (battery alone insufficient)")


def demand_flex(load: np.ndarray, hours: pd.DataFrame, targets: dict[int, np.ndarray], ac: np.ndarray,
                eheat: np.ndarray, ev: np.ndarray, base20: np.ndarray, ev_kw: np.ndarray) -> np.ndarray:
    """Net load change from DR events (no battery): AC / electric-heat thermostat shed (rebounds in the
    DR_REBOUND_HOURS after the window) and EV charging deferred to the next night's off-peak hours
    (managed charging). All shifted energy comes back (DR_REBOUND_SHARE).
    Returns delta [n, 8760] (negative = reduction)."""
    n = load.shape[0]
    L = np.nan_to_num(load, nan=0.0)
    month, hod = hours.month.to_numpy(), hours.hod.to_numpy()
    # weather-driven load ~ load above the home's own shoulder-season mean at that hour of day
    shoulder = np.isin(month, C.SHOULDER_MONTHS)
    shoulder_hod = np.stack([np.nanmean(np.where((shoulder & (hod == h))[None, :], load, np.nan), axis=1)
                             for h in range(24)], axis=1)
    shoulder_hod = np.nan_to_num(shoulder_hod, nan=0.0)
    base20 = np.nan_to_num(base20, nan=0.0)
    delta = np.zeros_like(L, dtype=np.float32)
    for d, hrs in targets.items():
        cols = d * 24 + hrs
        summer = month[cols[0]] in C.SUMMER_MONTHS
        hvac = np.maximum(L[:, cols] - shoulder_hod[:, hrs], 0)
        shed = np.zeros_like(hvac)
        if summer:
            shed += C.DR_AC_SHED_FRAC * hvac * ac[:, None]
        else:
            shed += C.DR_HEAT_SHED_FRAC * hvac * eheat[:, None]
        shed = np.minimum(shed, L[:, cols])
        evl = np.minimum(np.maximum(L[:, cols] - base20[:, cols], 0), np.nan_to_num(ev_kw)[:, None]) * ev[:, None]
        evl = np.minimum(evl, L[:, cols] - shed)
        delta[:, cols] -= shed + evl
        # thermal rebound: the hours right after the window
        after = cols[-1] + 1 + np.arange(C.DR_REBOUND_HOURS)
        after = after[after < L.shape[1]]
        if len(after):
            delta[:, after] += (C.DR_REBOUND_SHARE * shed.sum(axis=1) / len(after))[:, None]
        # managed EV charging: deferred energy recharges in the next night's off-peak hours (00-05)
        night = (d + 1) * 24 + np.array(C.RECHARGE_HOURS)
        night = night[night < L.shape[1]]
        if len(night):
            delta[:, night] += (C.DR_REBOUND_SHARE * evl.sum(axis=1) / len(night))[:, None]
    return delta


def npv_factor(years: int = C.ANALYSIS_YEARS, rate: float = C.DISCOUNT_RATE) -> float:
    return float(sum(1 / (1 + rate) ** t for t in range(1, years + 1)))


def avoided_cost(relief_summer: np.ndarray, relief_winter: np.ndarray, peak_season: str = "summer") -> pd.DataFrame:
    """Annual avoided buildout $ per customer (low / mid / high).
    capacity      x relief in the season of the annual system peak
    transmission  x mean of summer and winter relief (monthly network peaks across the year)
    distribution  x max of summer and winter relief (the local peak that binds)"""
    rs, rw = np.nan_to_num(relief_summer), np.nan_to_num(relief_winter)
    peak = rs if peak_season == "summer" else rw
    out = {}
    for lvl in ("low", "mid", "high"):
        v = C.AVOIDED_COST_PER_KW_YR
        out[f"avoided_usd_yr_{lvl}"] = (getattr(v["capacity"], lvl) * peak
                                        + getattr(v["transmission"], lvl) * (rs + rw) / 2
                                        + getattr(v["distribution_deferral"], lvl) * np.maximum(rs, rw))
    return pd.DataFrame(out)


def priority_score(df: pd.DataFrame) -> pd.Series:
    """Weighted percentile mix among candidates -> 0..1."""
    pct = lambda s: s.rank(pct=True)  # noqa: E731
    return (0.45 * pct(df.avoided_usd_yr_mid) + 0.15 * pct(df.persistence)
            + 0.15 * df.event_coverage + 0.15 * df.electrified.astype(float) + 0.10 * df.pv.astype(float))


def tiers(score: pd.Series) -> pd.Series:
    r = score.rank(pct=True, ascending=False)
    return pd.Series(np.select([r <= 0.10, r <= 0.30, r <= 0.60], ["Tier 1", "Tier 2", "Tier 3"], "Not prioritised"),
                     index=score.index)
