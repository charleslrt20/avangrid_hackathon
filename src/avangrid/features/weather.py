"""Per-customer change-point regression of daily kWh on weather.

    kWh_d = b0 + bh * HDD(Tbh)_d + bc * CDD(Tbc)_d + bx * HDD(20F)_d
            + bw * HDD(Tbh)_d * wind_z_d + bm * CDD(Tbc)_d * hum_z_d + be * weekend_d + e_d

HDD(T)_d = max(T - temp_d, 0) and CDD(T)_d = max(temp_d - T, 0) with temp_d the daily mean.
Balance points Tbh / Tbc are picked per customer by grid search on the 3-term model; the full
model is then fitted at those balance points. bx is the extra slope below 20 F: a heat pump's
COP falls and resistance backup engages, so HP homes steepen; resistive heat stays linear.

Because every customer shares one weather series, the design matrix is shared within a
balance-point combination, so all customers are fitted with a few matrix products
(weighted normal equations with a per-customer missing-day mask).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from avangrid import config as C

HB_GRID = np.arange(C.HEAT_BALANCE_RANGE_F[0], C.HEAT_BALANCE_RANGE_F[1] + 0.1, 2.5)
CB_GRID = np.arange(C.COOL_BALANCE_RANGE_F[0], C.COOL_BALANCE_RANGE_F[1] + 0.1, 2.5)
MIN_DAYS = 60


def daily_weather(hours: pd.DataFrame) -> pd.DataFrame:
    g = hours.groupby("day")
    d = pd.DataFrame({
        "temp": g.temp.mean(), "humidity": g.humidity.mean(), "wind": g.windspeed.mean(),
        "weekend": g.dow.first() >= 5, "month": g.month.first(),
    })
    return d.reset_index(drop=True)


def daily_kwh(mat: np.ndarray, max_missing: int = 2) -> np.ndarray:
    """Daily kWh [n, 365]; days with more than `max_missing` missing hours are NaN, others rescaled."""
    days = mat.reshape(mat.shape[0], -1, 24)
    obs = (~np.isnan(days)).sum(axis=2)
    tot = np.nansum(days, axis=2) * 24.0 / np.maximum(obs, 1)
    tot[obs < 24 - max_missing] = np.nan
    return tot.astype(np.float64)


def _batched_wls(X: np.ndarray, Y: np.ndarray, W: np.ndarray, ridge: float = 1e-6):
    """Fit Y[c] ~ X with weights W[c] for every customer c. X [d,k], Y/W [n,d] (Y has 0 where W=0)."""
    k = X.shape[1]
    pair = (X[:, :, None] * X[:, None, :]).reshape(X.shape[0], k * k)      # [d, k*k]
    xtx = (W @ pair).reshape(-1, k, k) + ridge * np.eye(k)
    xty = (W * Y) @ X                                                       # [n, k]
    beta = np.linalg.solve(xtx, xty[..., None])[..., 0]
    fitted = beta @ X.T
    sse = (W * (Y - fitted) ** 2).sum(axis=1)
    return beta, sse, fitted


def fit_changepoint(daily: np.ndarray, wx: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """Returns (coefficients per customer, residual matrix [n, 365] with NaN on missing days)."""
    n = daily.shape[0]
    W = (~np.isnan(daily)).astype(np.float64)
    Y = np.nan_to_num(daily)
    t = wx.temp.to_numpy()
    wind_z = ((wx.wind - wx.wind.mean()) / wx.wind.std()).to_numpy()
    hum_z = ((wx.humidity - wx.humidity.mean()) / wx.humidity.std()).to_numpy()
    wkend = wx.weekend.to_numpy().astype(float)

    # Stage 1: balance points by grid search on [1, HDD, CDD] (cooling bp kept >= heating bp).
    best_sse = np.full(n, np.inf)
    best_hb = np.zeros(n)
    best_cb = np.zeros(n)
    for hb in HB_GRID:
        for cb in CB_GRID:
            if cb < hb:
                continue
            X = np.column_stack([np.ones_like(t), np.maximum(hb - t, 0), np.maximum(t - cb, 0)])
            beta, sse, _ = _batched_wls(X, Y, W)
            # A negative slope means the term is not real; penalise so the grid does not chase noise.
            sse = np.where((beta[:, 1] < 0) | (beta[:, 2] < 0), sse * 1.0001, sse)
            better = sse < best_sse
            best_sse[better], best_hb[better], best_cb[better] = sse[better], hb, cb

    # Stage 2: full model at the chosen balance points, one group per (hb, cb).
    names = ["base_kwh_day", "heat_slope", "cool_slope", "cold_slope_extra",
             "wind_heat_coef", "hum_cool_coef", "weekend_kwh_day"]
    coef = np.full((n, len(names)), np.nan)
    resid = np.full_like(daily, np.nan)
    r2 = np.full(n, np.nan)
    hdd20 = np.maximum(C.HP_SLOPE_BREAK_F - t, 0)
    for hb in np.unique(best_hb):
        for cb in np.unique(best_cb[best_hb == hb]):
            g = np.flatnonzero((best_hb == hb) & (best_cb == cb))
            hdd = np.maximum(hb - t, 0)
            cdd = np.maximum(t - cb, 0)
            X = np.column_stack([np.ones_like(t), hdd, cdd, hdd20, hdd * wind_z, cdd * hum_z, wkend])
            beta, sse, fitted = _batched_wls(X, Y[g], W[g])
            coef[g] = beta
            r = Y[g] - fitted
            r[W[g] == 0] = np.nan
            resid[g] = r
            ybar = np.nansum(Y[g] * W[g], axis=1) / np.maximum(W[g].sum(axis=1), 1)
            sst = (W[g] * (Y[g] - ybar[:, None]) ** 2).sum(axis=1)
            r2[g] = 1 - sse / np.maximum(sst, 1e-9)

    out = pd.DataFrame(coef, columns=names)
    out["heat_bp_f"] = best_hb
    out["cool_bp_f"] = best_cb
    out["weather_r2"] = r2
    out["weather_days"] = W.sum(axis=1).astype(int)
    few = out.weather_days < MIN_DAYS
    out.loc[few, names + ["heat_bp_f", "cool_bp_f", "weather_r2"]] = np.nan
    resid[few] = np.nan
    # Shape of the heating response: >1 means the slope steepens below 20 F (HP signature).
    hs = out.heat_slope.where(out.heat_slope > 0.05)
    out["heat_break_ratio"] = (out.heat_slope + out.cold_slope_extra) / hs
    return out, resid


def residual_features(resid: np.ndarray, wx: pd.DataFrame) -> pd.DataFrame:
    """Weather-normalised monthly residuals (kWh/day) and a within-year trend."""
    order = ((wx.month.to_numpy() - 9) % 12)            # 0 = Sep 2025 ... 11 = Aug 2026
    cols = {}
    for k in range(12):
        m = (k + 8) % 12 + 1
        cols[f"resid_m{k + 1:02d}_{pd.Timestamp(2000, m, 1):%b}".lower()] = np.nanmean(resid[:, order == k], axis=1)
    out = pd.DataFrame(cols)
    # trend: OLS slope of daily residual on time, kWh/day per 30 days
    x = np.arange(resid.shape[1], dtype=float)
    w = ~np.isnan(resid)
    xm = (w * x).sum(1) / np.maximum(w.sum(1), 1)
    ym = np.nansum(resid, 1) / np.maximum(w.sum(1), 1)
    cov = np.nansum((x - xm[:, None]) * (resid - ym[:, None]), 1)
    var = (w * (x - xm[:, None]) ** 2).sum(1)
    out["resid_trend_per30d"] = 30 * cov / np.maximum(var, 1e-9)
    monthly = out.iloc[:, :12].to_numpy()
    out["resid_max_step"] = np.nanmax(monthly, axis=1) - np.nanmin(monthly, axis=1)
    return out
