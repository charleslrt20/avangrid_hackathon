"""Validation helpers (task 2.1, PLAN.md §5).

Rule: every benchmark is used either to set a threshold or to check one, never both. Everything here is
a *check*: nothing in this module feeds back into a threshold.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, cohen_kappa_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold

from avangrid import classify as K
from avangrid import config as C

# EIA RECS 2020, New England division (5.88 M homes), tables HC6.7 / HC7.7 / HC8.7 (final release March 2023).
# Shares of all homes. Used ONLY as an independent plausibility check (never to tune a threshold).
RECS_NEW_ENGLAND = {
    "homes_m": 5.88,
    "any_ac": 4.97 / 5.88,                       # uses AC equipment
    "central_ac": 1.64 / 5.88,                   # main AC = central (incl. central heat pump)
    "central_or_ductless_ac": (1.64 + 0.32) / 5.88,
    "electric_main_heat": 0.91 / 5.88,
    "hp_main_heat": (0.10 + 0.08) / 5.88,        # central + ductless heat pump as main heat
    "hp_secondary_ductless": 0.18 / 5.88,
    "resistive_main_heat": (0.14 + 0.50) / 5.88,  # electric furnace + built-in electric units
    "electric_water_heater": 1.92 / 5.88,
}
RECS_SOURCE = ("EIA, 2020 Residential Energy Consumption Survey, tables HC6.7 (space heating), HC7.7 (air "
               "conditioning), HC8.7 (water heating), New England column")

# Flags compared across the interleaved halves: name -> function(classify-like frame) -> bool Series
FLAG_DEFS = {
    "EV home charging (L2 or L1)": lambda k: k.ev_physics,
    "EV L2 detectable": lambda k: k.ev_class == "L2 detectable",
    "Electric heat (any)": lambda k: k.electric_heat,
    "Heat pump (HP / HP+backup)": lambda k: k.heat_type.isin(["HP", "HP+backup"]),
    "Resistive heat": lambda k: k.heat_type == "resistive",
    "Central AC": lambda k: k.ac_type == "central",
    "Any AC": lambda k: k.ac_type != "none/minimal",
    "Solar PV": lambda k: k.pv,
    "Pool pump": lambda k: k.pool,
    "Water heater (low conf.)": lambda k: k.water_heater_lowconf,
    "Hot tub (low conf.)": lambda k: k.hot_tub_lowconf,
}

# Seasonal expectation per flag: what the flagged group's *extra* daily kWh (vs unflagged) should look like.
SEASON_EXPECT = {
    "Electric heat (any)": "winter",
    "Heat pump (HP / HP+backup)": "winter",
    "Resistive heat": "winter",
    "Central AC": "summer",
    "Pool pump": "summer",
    "EV home charging (L2 or L1)": "all year",
    "Water heater (low conf.)": "all year",
    "Hot tub (low conf.)": "all year",
    "Solar PV": "less in summer",
}
SEASON_DOMINANCE = 2.0     # 'winter' = winter extra kWh >= 2x summer extra kWh (and > 0)
YEAR_ROUND_MAX_RATIO = 2.5  # 'all year' = extra kWh > 0 in every season, max/min season <= 2.5


def flag_stability(k_a: pd.DataFrame, k_b: pd.DataFrame, k_full: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, fn in FLAG_DEFS.items():
        a, b, full = fn(k_a).to_numpy(bool), fn(k_b).to_numpy(bool), fn(k_full).to_numpy(bool)
        both = a | b
        rows.append({"flag": name, "share_full_year": full.mean(), "share_odd_weeks": a.mean(),
                     "share_even_weeks": b.mean(), "agreement": (a == b).mean(),
                     "kappa_odd_vs_even": cohen_kappa_score(a, b),
                     "jaccard_odd_vs_even": (a & b).sum() / both.sum() if both.any() else np.nan,
                     "full_year_flags_in_both_halves": (a & b)[full].mean() if full.any() else np.nan})
    return pd.DataFrame(rows)


def multiclass_stability(a: pd.Series, b: pd.Series) -> dict:
    return {"agreement": float((a.to_numpy() == b.to_numpy()).mean()),
            "kappa": float(cohen_kappa_score(a.astype(str), b.astype(str)))}


def persistence_by_class(a: pd.Series, b: pd.Series) -> pd.DataFrame:
    """Share of customers in class X (odd weeks) that are still X in the even weeks, and the reverse."""
    out = []
    for c in sorted(set(a) | set(b), key=str):
        ia, ib = a.to_numpy() == c, b.to_numpy() == c
        out.append({"class": c, "customers_odd": int(ia.sum()), "customers_even": int(ib.sum()),
                    "same_in_even": ib[ia].mean() if ia.any() else np.nan,
                    "same_in_odd": ia[ib].mean() if ib.any() else np.nan})
    return pd.DataFrame(out)


def seasonal_check(f: pd.DataFrame, k: pd.DataFrame) -> pd.DataFrame:
    """Extra daily kWh of flagged vs unflagged customers by season, against the expected seasonal pattern."""
    rows = []
    for name, expect in SEASON_EXPECT.items():
        flag = FLAG_DEFS[name](k).to_numpy(bool)
        ex = {s: 24 * (np.nanmean(f[f"mean_kw_{s}"].to_numpy()[flag]) - np.nanmean(f[f"mean_kw_{s}"].to_numpy()[~flag]))
              for s in ("winter", "shoulder", "summer")}
        w, sh, su = ex["winter"], ex["shoulder"], ex["summer"]
        if expect == "winter":
            ok = w > 0 and w >= SEASON_DOMINANCE * max(su, 0)
        elif expect == "summer":
            ok = su > 0 and su >= SEASON_DOMINANCE * max(w, 0)
        elif expect == "all year":
            vals = np.array([w, sh, su])
            ok = bool((vals > 0).all() and vals.max() / vals.min() <= YEAR_ROUND_MAX_RATIO)
        else:  # PV: clipped exports cut summer imports
            ok = su < w
        rows.append({"flag": name, "expected": expect, "extra_kwh_day_winter": w, "extra_kwh_day_shoulder": sh,
                     "extra_kwh_day_summer": su, "pass": bool(ok)})
    return pd.DataFrame(rows)


def cross_time_pu(X_train_time: pd.DataFrame, X_test_time: pd.DataFrame, y: np.ndarray,
                  seed: int = C.SEED) -> np.ndarray:
    """Out-of-customer *and* out-of-time PU scores: fit on one half-year's features of the training
    customers, score the other half-year's features of the held-out customers."""
    cv = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
    g = np.zeros(len(y))
    for tr, te in cv.split(X_train_time, y):
        m = K.pu_model().fit(X_train_time.iloc[tr], y[tr])
        g[te] = m.predict_proba(X_test_time.iloc[te])[:, 1]
    return g


def seed_robustness(X: pd.DataFrame, y: np.ndarray, seeds) -> pd.DataFrame:
    """Bagged PU LightGBM minus PU logistic, out-of-fold, for several CV splits."""
    rows = []
    for seed in seeds:
        lo = ranking_metrics(K.pu_scores(X, y, seed)[0], y)
        lg = ranking_metrics(K.bagged_pu_oof(X, y, seed), y)
        rows.append({"seed": seed, **{f"diff_{k}": lg[k] - lo[k] for k in lo if k != "base_rate"}})
    return pd.DataFrame(rows)


def ranking_metrics(g: np.ndarray, y: np.ndarray, top_shares=(0.01, 0.02, 0.05)) -> dict:
    """Held-out (out-of-fold) ranking quality. The top-share cut-offs are fixed fractions, identical for
    every model, not tuned on the labels."""
    order = np.argsort(-g)
    out = {"auc": roc_auc_score(y, g), "average_precision": average_precision_score(y, g),
           "base_rate": y.mean()}
    for s in top_shares:
        top = order[: int(round(s * len(g)))]
        out[f"recall_top{s:.0%}"] = y[top].sum() / y.sum()
    return out


def recs_comparison(k: pd.DataFrame) -> pd.DataFrame:
    """Detected shares vs RECS 2020 New England. Ratio in [0.67, 1.5] = consistent."""
    r = RECS_NEW_ENGLAND
    ht = k.heat_type
    rows = [
        ("Any AC", (k.ac_type != "none/minimal").mean(), r["any_ac"], "uses AC equipment"),
        ("Central AC", (k.ac_type == "central").mean(), r["central_ac"], "main AC central (incl. central HP)"),
        ("Central AC", (k.ac_type == "central").mean(), r["central_or_ductless_ac"], "main AC central or ductless HP"),
        ("Electric heat (any)", k.electric_heat.mean(), r["electric_main_heat"], "electricity = main heating fuel"),
        ("Heat pump (HP / HP+backup)", ht.isin(["HP", "HP+backup"]).mean(), r["hp_main_heat"], "HP = main heating equipment"),
        ("Heat pump (HP / HP+backup)", ht.isin(["HP", "HP+backup"]).mean(),
         r["hp_main_heat"] + r["hp_secondary_ductless"], "HP main + ductless HP secondary"),
        ("Resistive heat", (ht == "resistive").mean(), r["resistive_main_heat"], "electric furnace or baseboard = main"),
        ("Resistive + electric (type unclear)", ht.isin(["resistive", "electric (type unclear)"]).mean(),
         r["resistive_main_heat"], "electric furnace or baseboard = main"),
        ("Water heater (low conf.)", k.water_heater_lowconf.mean(), r["electric_water_heater"], "electric main water heater"),
    ]
    df = pd.DataFrame(rows, columns=["detected", "share_detected", "share_recs_ne", "recs_definition"])
    df["ratio"] = df.share_detected / df.share_recs_ne
    df["consistent"] = df.ratio.between(0.67, 1.5)
    return df
