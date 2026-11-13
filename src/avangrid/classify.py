"""Appliance classification (task 1.3).

Two kinds of evidence, kept separate so validation is never circular (PLAN.md §5):
  1. Physics gates: thresholds from equipment ratings (config) -> appliance flags.
  2. PU logistic ranking (EV, HP): labeled vs unlabeled, out-of-fold scores. Used to *rank*
     and to grade confidence, not to set flag thresholds.
Labels are used only to check: recall of labeled customers, SCAR-implied precision
(labeled share among flagged / c), counts vs the implied totals, c estimated from data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import QuantileTransformer

from avangrid import config as C
from avangrid.features.build import DESCRIPTIONS

# --------------------------------------------------------------------------- physics thresholds
# Electric space heating: a typical home loses ~100-150 W/°F (2.4-3.6 kWh/day/°F of heat).
# A heat pump at COP 2-3 needs ~0.8-1.8 kWh/day/°F of electricity, resistance heat 2.4-3.6;
# fuel-heated homes only run fans/pumps (<0.3). 0.5 sits between fuel and the smallest HP homes.
HEAT_ELECTRIC_SLOPE = 0.5
HEAT_RESISTIVE_SLOPE = 1.5        # steep and linear (COP 1 does not change with temperature)
HP_STEEPEN_RATIO = 1.25           # >= 25% steeper below 20 °F: COP falls / backup strips
HP_BACKUP_RATIO = 2.0             # slope at least doubles below 20 °F: resistance backup dominates
# Cooling: central AC 2.5-5 kW; room units 0.5-1.5 kW each.
COOL_ANY_SLOPE = 0.5
COOL_CENTRAL_SLOPE = 1.5
# Pool pump on a timer: a same-hour on/off step on most pool-season days.
POOL_SCORE = 0.4
POOL_RUN_HOURS = (3, 14)
# PV with clipped exports: zeros that follow the sun (summer minus winter), not vacancy (night zeros).
PV_SEASON_CONTRAST = 0.10
PV_MAX_NIGHT_ZERO = 0.05
PV_BATTERY_EVENING_ZERO = 0.30    # PV home that also reads 0 at 17-21h in summer: battery covering evening
# EV L2: regular blocks (>= 1 week in 4) at a plausible charger rating and duration.
EV_L2_WEEK_SHARE = 0.25
EV_L2_KW = (5.5, 12.0)
EV_L2_MAX_BLOCK_H = 5
# Low-confidence (hourly data blurs 20-40 min cycling)
WH_FREQ = 0.5
TUB_SHARE = 0.4

PU_EXCLUDE = {"pv_midday_zero_summer", "pv_night_zero_summer", "pv_midday_zero_winter",
              "pv_zero_season_contrast", "pv_midday_dip"}


def pu_matrix(f: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in DESCRIPTIONS if c not in PU_EXCLUDE]
    X = f[cols].copy()
    for c in cols:
        if X[c].isna().any():
            X[c + "_na"] = X[c].isna().astype(float)
    return X.fillna(X.median(numeric_only=True))


def pu_model() -> Pipeline:
    return make_pipeline(QuantileTransformer(output_distribution="normal", n_quantiles=200, random_state=0),
                         LogisticRegression(C=0.1, max_iter=3000))


def pu_scores(X: pd.DataFrame, y: np.ndarray, seed: int = C.SEED) -> tuple[np.ndarray, Pipeline, dict]:
    """Out-of-fold g(x) = P(labeled | x), a model fit on all data, and CV metrics."""
    cv = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
    g = cross_val_predict(pu_model(), X, y, cv=cv, method="predict_proba")[:, 1]
    fold_auc = []
    for tr, te in cv.split(X, y):
        m = pu_model().fit(X.iloc[tr], y[tr])
        fold_auc.append(roc_auc_score(y[te], m.predict_proba(X.iloc[te])[:, 1]))
    model = pu_model().fit(X, y)
    metrics = {"cv_auc": roc_auc_score(y, g), "cv_auc_sd": float(np.std(fold_auc)),
               "c_hat_elkan_noto": float(g[y].mean())}
    for c in C.PU_C_SENSITIVITY:
        metrics[f"en_flags_c{c}"] = int((g / c >= 0.5).sum())
        metrics[f"en_recall_c{c}"] = float((g[y] / c >= 0.5).mean())
    return g, model, metrics


def odds_ratios(model: Pipeline, columns: list[str], top: int = 12) -> pd.DataFrame:
    coef = model[-1].coef_[0]
    df = pd.DataFrame({"feature": columns, "coef_per_sd": coef, "odds_ratio_per_sd": np.exp(coef)})
    return df.reindex(df.coef_per_sd.abs().sort_values(ascending=False).index).head(top)


def heat_type(f: pd.DataFrame) -> pd.Series:
    hs, br = f.heat_slope, f.heat_break_ratio
    electric = hs >= HEAT_ELECTRIC_SLOPE
    return pd.Series(np.select(
        [~electric,
         electric & (br >= HP_BACKUP_RATIO),
         electric & (br >= HP_STEEPEN_RATIO),
         electric & (hs >= HEAT_RESISTIVE_SLOPE),
         electric],
        ["none/non-electric", "HP+backup", "HP", "resistive", "electric (type unclear)"],
        "none/non-electric"), index=f.index)


def physics_flags(f: pd.DataFrame, evening_zero: np.ndarray) -> pd.DataFrame:
    out = pd.DataFrame(index=f.index)
    l2 = ((f.ev_l2_per_week >= C.EV_L2_DETECT_PER_WEEK) & (f.ev_l2_week_share >= EV_L2_WEEK_SHARE)
          & f.ev_l2_block_kw.between(*EV_L2_KW) & (f.ev_l2_block_hours <= EV_L2_MAX_BLOCK_H))
    l1 = ~l2 & (f.ev_l1_night_share >= C.EV_L1_DETECT_SHARE)
    out["ev_class"] = np.select([l2, l1], ["L2 detectable", "L1 detectable"], "no home-charging signature")
    out["ev_physics"] = l2 | l1
    out["ev_charger_kw"] = f.ev_l2_block_kw.where(l2)
    out["heat_type"] = heat_type(f)
    out["electric_heat"] = out.heat_type != "none/non-electric"
    out["ac_type"] = np.select([f.cool_slope >= COOL_CENTRAL_SLOPE, f.cool_slope >= COOL_ANY_SLOPE],
                               ["central", "room/partial"], "none/minimal")
    out["pv"] = (f.pv_zero_season_contrast >= PV_SEASON_CONTRAST) & (f.pv_night_zero_summer < PV_MAX_NIGHT_ZERO)
    out["pv_battery"] = out.pv & (evening_zero >= PV_BATTERY_EVENING_ZERO)
    out["pool"] = (f.pool_timer_score >= POOL_SCORE) & f.pool_run_hours.between(*POOL_RUN_HOURS)
    out["water_heater_lowconf"] = f.wh_bump_freq >= WH_FREQ
    out["hot_tub_lowconf"] = f.hot_tub_cycle_share >= TUB_SHARE
    # Estimated connected load (kW) at design conditions, from the weather model
    out["heat_kw_design_0f"] = (f.heat_slope.clip(lower=0) * (f.heat_bp_f - 0)
                                + f.cold_slope_extra.clip(lower=0) * C.HP_SLOPE_BREAK_F) / 24
    out["cool_kw_design_90f"] = f.cool_slope.clip(lower=0) * (90 - f.cool_bp_f) / 24
    return out


def confidence(physics: pd.Series, pct: pd.Series) -> pd.Series:
    """high = physics evidence and PU rank in the top decile; medium = physics only;
    low = PU top 2% with no physics signature (e.g. charges away from home)."""
    return pd.Series(np.select([physics & (pct >= 0.90), physics, pct >= 0.98],
                               ["high", "medium", "low"], ""), index=physics.index)


def scar_precision(flag: pd.Series, labeled: pd.Series, c: float = C.PU_C) -> float:
    """If labels are a random c-share of true owners, labeled share among flagged / c estimates precision."""
    return float(labeled[flag].mean() / c) if flag.any() else float("nan")


def selection_bias(f: pd.DataFrame, labeled: pd.Series, flagged_unlabeled: pd.Series, cols: list[str]) -> pd.DataFrame:
    """Standardised mean difference, labeled positives vs flagged-but-unlabeled customers."""
    a, b = f.loc[labeled, cols], f.loc[flagged_unlabeled, cols]
    sd = f[cols].std()
    return pd.DataFrame({"labeled_median": a.median(), "flagged_unlabeled_median": b.median(),
                         "smd": (a.mean() - b.mean()) / sd}).round(3)
