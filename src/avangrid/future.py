"""Winter-peaking future: heat-pump adoption scenarios (task 2.2).

Added load per adopter is *measured*, not assumed. For every hour of day h we regress the gap between
the mean load of heat-pump homes and the mean load of non-electric-heat homes on the weather:

  gap_h(d) = a_h + b_h * HDD(T) + c_h * max(T_break - T, 0) + e_h * CDD(T)
  HDD = max(T_heat - T, 0),  CDD = max(T - T_cool, 0),  T_break = 20 °F (COP drop / strip backup)

a_h is the non-weather difference between the two groups (home size, occupancy) and is dropped. The
heat-pump increment for hour t is then  inc(t) = max(b_h HDD + c_h cold + e_h CDD, 0)  on the observed
weather year. Both groups see the same winter lighting and occupancy, so only the heating (and extra
cooling) signal survives the difference.

Each adopter gets the profile scaled by a size ratio drawn from the donor homes' own design heating kW
(mean 1), so system totals equal N x profile while individual homes vary as real HP homes do.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from avangrid import config as C


def group_mean(mat: np.ndarray, rows: np.ndarray, chunk: int = 4000) -> np.ndarray:
    """nanmean over customers, chunked so we never copy the full matrix."""
    s = np.zeros(mat.shape[1])
    n = np.zeros(mat.shape[1])
    rows = np.sort(rows)
    for i in range(0, len(rows), chunk):
        x = np.asarray(mat[rows[i:i + chunk]], dtype=np.float64)
        s += np.nansum(x, axis=0)
        n += (~np.isnan(x)).sum(axis=0)
    return s / np.maximum(n, 1)


def _design(temp: np.ndarray, t_heat: float, t_cool: float) -> np.ndarray:
    return np.stack([np.ones_like(temp), np.maximum(t_heat - temp, 0),
                     np.maximum(C.HP_SLOPE_BREAK_F - temp, 0), np.maximum(temp - t_cool, 0)], axis=-1)


@dataclass
class HpModel:
    name: str
    n_donors: int
    t_heat: float
    t_cool: float
    coef: np.ndarray          # [24, 4]: intercept, heat, cold-extra, cool (kW per °F)
    r2: float
    profile: np.ndarray       # [8760] kW added per average adopter

    def at(self, temp: float) -> np.ndarray:
        """Added kW by hour of day at a constant temperature (for reporting)."""
        x = _design(np.full(24, float(temp)), self.t_heat, self.t_cool)
        return np.maximum((x[:, 1:] * self.coef[:, 1:]).sum(axis=1), 0)


def fit_hp_increment(gap: np.ndarray, hours: pd.DataFrame, name: str, n_donors: int) -> HpModel:
    """Grid-search shared balance points; one least-squares fit per hour of day (365 points each)."""
    temp = hours.temp.to_numpy(dtype=float)
    hod = hours.hod.to_numpy()
    best = None
    for th in np.arange(C.HEAT_BALANCE_RANGE_F[0], C.HEAT_BALANCE_RANGE_F[1] + 0.1, 2.5):
        for tc in np.arange(C.COOL_BALANCE_RANGE_F[0], C.COOL_BALANCE_RANGE_F[1] + 0.1, 2.5):
            X = _design(temp, th, tc)
            coef = np.zeros((24, 4))
            sse = 0.0
            for h in range(24):
                m = hod == h
                b, *_ = np.linalg.lstsq(X[m], gap[m], rcond=None)
                coef[h] = b
                sse += float(((X[m] @ b - gap[m]) ** 2).sum())
            if best is None or sse < best[0]:
                best = (sse, th, tc, coef)
    sse, th, tc, coef = best
    # light circular smoothing across hours of day: 49-88 donor homes make single-hour slopes noisy
    k = np.array([0.25, 0.5, 0.25])
    sm = np.stack([np.convolve(np.r_[coef[-1:, j], coef[:, j], coef[:1, j]], k, mode="valid") for j in range(4)], axis=1)
    X = _design(temp, th, tc)
    prof = np.maximum((X[:, 1:] * sm[hod, 1:]).sum(axis=1), 0)
    r2 = 1 - sse / float(((gap - gap.mean()) ** 2).sum())
    return HpModel(name, n_donors, float(th), float(tc), sm, r2, prof.astype(np.float32))


def best_window(cols: np.ndarray, hours: pd.DataFrame, length: int = 6) -> tuple[int, ...]:
    """Contiguous `length`-hour window (hour-starting) covering the most of the given top hours."""
    hod = hours.hod.to_numpy()[cols]
    cover = [(np.isin(hod, range(s, s + length)).sum(), s) for s in range(0, 24 - length + 1)]
    _, s = max(cover)
    return tuple(range(s, s + length))


def event_windows(cols: np.ndarray, hours: pd.DataFrame, morning_min_share: float = 0.15) -> list[tuple[int, ...]]:
    """Winter event windows from the season's top hours: the best 6 h evening window (start 13-17h), plus the
    best 4 h morning window (start 4-8h) when mornings hold at least `morning_min_share` of the top hours."""
    hod = hours.hod.to_numpy()[cols]

    def best(starts, length):
        return max((np.isin(hod, range(s, s + length)).sum(), s) for s in starts)

    _, se = best(range(13, 18), 6)
    out = [tuple(range(se, se + 6))]
    n_m, sm = best(range(4, 9), 4)
    if n_m / len(cols) >= morning_min_share:
        out.insert(0, tuple(range(sm, sm + 4)))
    return out


def shave_energy(total: np.ndarray, mask: np.ndarray, hours: pd.DataFrame, depth_kw: float) -> dict:
    """Energy a perfectly placed fleet would need to cut the season's peak by `depth_kw`: per day, the kWh above
    (peak - depth). Reports the worst day, hours above the line, days affected and the longest run of such days."""
    idx = np.flatnonzero(mask)
    day = hours.day.to_numpy()[idx]
    thr = total[idx].max() - depth_kw
    ex = np.maximum(total[idx] - thr, 0)
    e_day = pd.Series(ex).groupby(day).sum()
    h_day = pd.Series(ex > 0).groupby(day).sum()
    hit = np.sort(e_day.index[e_day > 0].to_numpy())
    run = longest = 0
    for i, d in enumerate(hit):
        run = run + 1 if i and d == hit[i - 1] + 1 else 1
        longest = max(longest, run)
    return {"worst_day_mwh": e_day.max() / 1000, "hours_above_worst_day": int(h_day.max()),
            "days_needing_relief": len(hit), "longest_run_days": longest,
            "mwh_per_mw": e_day.max() / max(depth_kw, 1e-9)}


def season_peaks(total: np.ndarray, hours: pd.DataFrame) -> dict:
    out = {}
    for s, months in (("summer", C.SUMMER_MONTHS), ("winter", C.WINTER_MONTHS)):
        idx = np.flatnonzero(hours.month.isin(months).to_numpy())
        top = idx[np.argsort(total[idx])[::-1][:C.PEAK_TOP_HOURS]]
        out[s] = top
        out[f"{s}_peak_kw"] = float(total[idx].max())
        out[f"{s}_peak_col"] = int(idx[np.argmax(total[idx])])
        out[f"{s}_cp_kw"] = float(total[top].mean())
    return out


def crossover_sweep(base_total: np.ndarray, profile: np.ndarray, n_customers: int, hours: pd.DataFrame,
                    shares: np.ndarray) -> pd.DataFrame:
    """System summer / winter peak (kW) as the adopting share of customers grows."""
    summer = hours.month.isin(C.SUMMER_MONTHS).to_numpy()
    winter = hours.month.isin(C.WINTER_MONTHS).to_numpy()
    rows = []
    for s in shares:
        tot = base_total + s * n_customers * profile
        rows.append({"hp_share": s, "summer_peak_kw": tot[summer].max(), "winter_peak_kw": tot[winter].max()})
    return pd.DataFrame(rows)


def crossover_share(sweep: pd.DataFrame) -> float:
    """First adoption share at which the winter peak reaches the summer peak (linear interpolation)."""
    d = (sweep.winter_peak_kw - sweep.summer_peak_kw).to_numpy()
    s = sweep.hp_share.to_numpy()
    i = np.flatnonzero(d >= 0)
    if len(i) == 0:
        return float("nan")
    i = i[0]
    if i == 0:
        return float(s[0])
    return float(s[i - 1] + (s[i] - s[i - 1]) * (-d[i - 1]) / (d[i] - d[i - 1]))


def simulate_fleet(load: np.ndarray, targets: dict[int, np.ndarray], battery: C.Battery, cap: np.ndarray,
                   sys_total: np.ndarray, limit_kw: np.ndarray,
                   recharge_hours: tuple[int, ...] = C.FLEET_RECHARGE_HOURS):
    """Like bess.simulate, but recharge is coordinated across the fleet:
    - each battery spreads its remaining recharge evenly over the hours left in the current recharge block
      (valley filling, not as-fast-as-possible), up to its home's own `cap` (annual peak), never exporting;
    - in every recharge hour the fleet's total charging is scaled down so the system (sys_total, which already
      contains these homes' load) stays at or below limit_kw[col]."""
    from avangrid.bess import DispatchResult

    rech_sorted = sorted(recharge_hours)
    left_in_block = {}
    for h in rech_sorted:                       # hours left in the contiguous recharge block, this one included
        k = 0
        while h + k in recharge_hours:
            k += 1
        left_in_block[h] = k
    n, t = load.shape
    L = np.nan_to_num(load, nan=0.0).astype(np.float64)
    e_max, e_min, p = battery.kwh * battery.soc_max_frac, battery.kwh * battery.soc_min_frac, battery.kw
    soc = np.full(n, e_max)
    dis = np.zeros((n, t), dtype=np.float32)
    chg = np.zeros((n, t), dtype=np.float32)
    smin, smax = soc.copy(), soc.copy()
    rech = set(recharge_hours)
    levels = {}
    for d in range(t // 24):
        tgt = targets.get(d)
        tgt_set = set() if tgt is None else set(int(h) for h in tgt)
        level = None
        for h in range(24):
            col = d * 24 + h
            if h in rech and h not in tgt_set:
                want = (e_max - soc) / battery.rte / left_in_block[h]
                c = np.maximum(np.minimum.reduce([np.full(n, p), want, cap - L[:, col]]), 0)
                room = max(limit_kw[col] - sys_total[col], 0.0)
                tot = c.sum()
                if tot > room:
                    c *= room / tot
                soc = soc + c * battery.rte
                chg[:, col] = c
            if h in tgt_set:
                if level is None or (h - 1) not in tgt_set:   # re-plan at the start of each event block
                    n_left = sum(1 for x in tgt_set if x >= h)  # spread over all remaining event hours today
                    level = np.minimum(p, np.maximum(soc - e_min, 0) / n_left)
                    levels[d] = level
                dd = np.minimum.reduce([level, L[:, col], np.maximum(soc - e_min, 0)])
                soc = soc - dd
                dis[:, col] = dd
            smin = np.minimum(smin, soc)
            smax = np.maximum(smax, soc)
    return DispatchResult(dis, chg, smin, smax, np.zeros((0, t), dtype=np.float32), levels, e_max, soc)


@dataclass
class CapResult:
    over_hours_before: np.ndarray   # [n] hours above the cap with no battery
    over_hours_after: np.ndarray    # [n] hours still above the cap with the battery
    unserved_kwh: np.ndarray        # [n] energy above the cap the battery could not cover
    net_peak: np.ndarray            # [n] kW
    soc_min_seen: np.ndarray
    soc_max_seen: np.ndarray
    charge_kwh: np.ndarray
    discharge_kwh: np.ndarray
    soc_end: np.ndarray


def cap_dispatch(load: np.ndarray, cap: np.ndarray, battery: C.Battery) -> CapResult:
    """Keep each home's net load at or below `cap` (its pre-adoption peak + 3 kVA) all year.

    Discharge whenever load > cap: d = min(load - cap, P, SOC - E_min). Recharge only in the off-peak hours
    (RECHARGE_HOURS) and only up to the cap: c = min(P, (E_max - SOC)/RTE, cap - load). SOC carried hour by
    hour, so multi-day cold snaps deplete the battery exactly as they would in the field."""
    n, t = load.shape
    L = np.nan_to_num(load, nan=0.0).astype(np.float64)
    e_max, e_min, p = battery.kwh * battery.soc_max_frac, battery.kwh * battery.soc_min_frac, battery.kw
    soc = np.full(n, e_max)
    smin, smax = soc.copy(), soc.copy()
    over_b = np.zeros(n)
    over_a = np.zeros(n)
    unserved = np.zeros(n)
    net_peak = np.zeros(n)
    ch = np.zeros(n)
    dis = np.zeros(n)
    rech = np.zeros(24, dtype=bool)
    rech[list(C.RECHARGE_HOURS)] = True
    for col in range(t):
        x = L[:, col]
        excess = np.maximum(x - cap, 0)
        d = np.minimum.reduce([excess, np.full(n, p), np.maximum(soc - e_min, 0)])
        soc -= d
        dis += d
        net = x - d
        if rech[col % 24]:
            c = np.maximum(np.minimum.reduce([np.full(n, p), (e_max - soc) / battery.rte, cap - net]), 0)
            soc += c * battery.rte
            ch += c
            net = net + c
        over_b += excess > 1e-6
        over_a += net > cap + 1e-6
        unserved += np.maximum(net - cap, 0)
        net_peak = np.maximum(net_peak, net)
        smin = np.minimum(smin, soc)
        smax = np.maximum(smax, soc)
    return CapResult(over_b, over_a, unserved, net_peak, smin, smax, ch, dis, soc)
