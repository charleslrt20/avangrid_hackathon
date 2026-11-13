"""Drivers (task 1.5): what drives load and the system peaks.

Weather is exogenous, so weather -> load slopes are defensibly causal. Appliance / segment ->
peak links are associations and are labelled as such.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from avangrid import config as C
from avangrid.features import weather as W


def contribution(groups: pd.Series, f: pd.DataFrame, n_boot: int = 500, seed: int = C.SEED) -> pd.DataFrame:
    """Share of customers / annual kWh / summer and winter coincident-peak load per group, with
    Poisson-bootstrap 95% CIs on the peak shares (resampling customers)."""
    rng = np.random.default_rng(seed)
    w = rng.poisson(1.0, size=(n_boot, len(f))).astype(np.float32)
    cols = {"annual_kwh": f.annual_kwh.fillna(0).to_numpy(),
            "cp_kw_summer": f.cp_kw_summer.fillna(0).to_numpy(),
            "cp_kw_winter": f.cp_kw_winter.fillna(0).to_numpy()}
    rows = []
    for g in groups.dropna().unique():
        m = (groups == g).to_numpy()
        row = {"group": g, "customers": int(m.sum()), "share_customers": m.mean()}
        for name, x in cols.items():
            row[f"share_{name}"] = x[m].sum() / x.sum()
            if name.startswith("cp"):
                boot = (w[:, m] @ x[m]) / (w @ x)
                row[f"share_{name}_lo"], row[f"share_{name}_hi"] = np.percentile(boot, [2.5, 97.5])
                row[f"mean_{name}"] = x[m].mean()
        rows.append(row)
    out = pd.DataFrame(rows)
    out["summer_cp_index"] = out.share_cp_kw_summer / out.share_customers   # >1 = over-represented at peak
    out["winter_cp_index"] = out.share_cp_kw_winter / out.share_customers
    return out.sort_values("share_cp_kw_winter", ascending=False)


def flag_effect(flag: pd.Series, y: pd.Series, n_boot: int = 500, seed: int = C.SEED) -> dict:
    """Difference in mean y with vs without a flag, bootstrap 95% CI (association, not causation)."""
    rng = np.random.default_rng(seed)
    a, b = y[flag].dropna().to_numpy(), y[~flag].dropna().to_numpy()
    diffs = [rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(n_boot)]
    return {"with": a.mean(), "without": b.mean(), "diff": a.mean() - b.mean(),
            "lo": np.percentile(diffs, 2.5), "hi": np.percentile(diffs, 97.5)}


def group_hourly_mean(mat: np.ndarray, groups: pd.Series, rows: np.ndarray) -> pd.DataFrame:
    """Mean hourly kWh per customer for each group -> [groups, 8760]."""
    out = {}
    for g in groups.dropna().unique():
        idx = rows[(groups == g).to_numpy()]
        out[g] = np.nanmean(np.asarray(mat[np.sort(idx)]), axis=0)
    return pd.DataFrame(out).T


def temperature_response(hourly: pd.DataFrame, temp: np.ndarray, bins=np.arange(-5, 100, 5)) -> pd.DataFrame:
    b = pd.cut(temp, bins)
    return hourly.T.groupby(b, observed=True).mean().T


def weather_permutation(daily: np.ndarray, wx: pd.DataFrame, n_perm: int = 200, seed: int = C.SEED) -> pd.DataFrame:
    """Change-point fit on group-aggregate daily kWh; balance points are held at their observed values
    under the null so slopes are comparable. Wind and humidity effects are tested by
    permuting that variable across days *within the same month* (keeps season and temperature
    structure); heating/cooling slopes by permuting whole days' weather (no-relationship null)."""
    rng = np.random.default_rng(seed)
    coef, _ = W.fit_changepoint(daily, wx)
    bp = (coef.heat_bp_f.to_numpy(), coef.cool_bp_f.to_numpy())     # held fixed under the null
    obs = coef[["heat_slope", "cool_slope", "wind_heat_coef", "hum_cool_coef"]].to_numpy()
    exceed = np.zeros_like(obs)
    month = wx.month.to_numpy()
    for _ in range(n_perm):
        wxp = wx.copy()
        for col in ("wind", "humidity"):
            vals = wxp[col].to_numpy().copy()
            for m in np.unique(month):
                idx = np.flatnonzero(month == m)
                vals[idx] = vals[rng.permutation(idx)]
            wxp[col] = vals
        cp, _ = W.fit_changepoint(daily, wxp, fixed_bp=bp)
        exceed[:, 2] += np.abs(cp.wind_heat_coef.to_numpy()) >= np.abs(obs[:, 2])
        exceed[:, 3] += np.abs(cp.hum_cool_coef.to_numpy()) >= np.abs(obs[:, 3])
        wxs = wx.iloc[rng.permutation(len(wx))].reset_index(drop=True)
        cs, _ = W.fit_changepoint(daily, wxs, fixed_bp=bp)
        exceed[:, 0] += cs.heat_slope.to_numpy() >= obs[:, 0]
        exceed[:, 1] += cs.cool_slope.to_numpy() >= obs[:, 1]
    p = (exceed + 1) / (n_perm + 1)
    out = coef[["base_kwh_day", "heat_slope", "cool_slope", "cold_slope_extra", "wind_heat_coef",
                "hum_cool_coef", "heat_bp_f", "cool_bp_f", "weather_r2"]].copy()
    out["p_heat"], out["p_cool"], out["p_wind"], out["p_humidity"] = p.T
    return out
