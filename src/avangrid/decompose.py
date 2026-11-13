"""NMF load decomposition and exemplar selection (task 2.3).

Load shapes form a continuum (K-Means silhouette ~0.1), so instead of forcing each home into one
group, NMF writes every home's month x hour shape as a non-negative *mix* of a few parts:

    S_i  ≈  sum_j  w_ij * H_j          S_i = customer i's 288-cell month x hour mean load, scaled to sum 1
                                        H_j = component j (sums to 1), w_ij >= 0

Because S_i and H_j both sum to 1, w_ij is (approximately) the share of customer i's annual energy that
component j explains, and w_ij x annual kWh is the kWh it explains: an "electrification fingerprint".

Components are described from their own shape *before* they are compared with appliance flags or labels
(PLAN.md §2 Task B, "not prescriptive"); a component no flag explains is reported as unexplained.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.decomposition import NMF
from sklearn.metrics import roc_auc_score

from avangrid import config as C

MONTH_ORDER = (9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8)     # data year Sep 2025 -> Aug 2026
MONTH_ABBR = ("Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug")
K_GRID = range(3, 11)
K_MAX_READABLE = 8          # more parts than this stop being explainable on one slide
STABLE_COSINE = 0.90        # a component counts as reproduced if the other half finds one this similar
ASSOC_AUC = 0.75            # component "matches" a flag when its weight ranks flagged homes this well


# --------------------------------------------------------------------------- profiles
def month_hour_profiles(mat: np.ndarray, hours: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Mean kWh by (month, hour of day), months in data order -> [n, 288], plus the share of cells filled.

    A month with no readings (gappy meters) takes the customer's own mean for that hour across the
    other months, so the gap does not read as a zero-load month."""
    n = mat.shape[0]
    days = mat.reshape(n, -1, 24)
    dmonth = hours.month.to_numpy().reshape(-1, 24)[:, 0]
    prof = np.full((n, 12, 24), np.nan, dtype=np.float64)
    for j, m in enumerate(MONTH_ORDER):
        sel = dmonth == m
        if sel.any():
            with np.errstate(invalid="ignore"):
                prof[:, j, :] = np.nanmean(days[:, sel, :], axis=1)
    missing = np.isnan(prof)
    hod_mean = np.nanmean(prof, axis=1, keepdims=True)                 # [n, 1, 24]
    prof = np.where(missing, np.broadcast_to(hod_mean, prof.shape), prof)
    prof = np.where(np.isnan(prof), np.nanmean(prof.reshape(n, -1), axis=1)[:, None, None], prof)
    return np.nan_to_num(prof, nan=0.0).reshape(n, 288), missing.reshape(n, -1).mean(axis=1)


def to_shape(P: np.ndarray) -> np.ndarray:
    """Scale each row to sum 1 (shape only; size is put back later through annual kWh)."""
    tot = P.sum(axis=1, keepdims=True)
    return np.where(tot > 0, P / np.where(tot > 0, tot, 1.0), 1.0 / P.shape[1])


# --------------------------------------------------------------------------- NMF
def fit_nmf(S: np.ndarray, k: int, seed: int = C.SEED, max_iter: int = 600) -> tuple[np.ndarray, np.ndarray, float]:
    """Returns (W [n,k], H [k,288] with rows summing to 1, relative Frobenius error)."""
    model = NMF(n_components=k, init="nndsvda", max_iter=max_iter, tol=1e-5, random_state=seed)
    W = model.fit_transform(S)
    H = model.components_
    scale = H.sum(axis=1)
    H = H / scale[:, None]
    W = W * scale[None, :]
    err = float(np.linalg.norm(S - W @ H) / np.linalg.norm(S))
    return W, H, err


def r2_vs_mean_shape(S: np.ndarray, W: np.ndarray, H: np.ndarray) -> float:
    """Share of the between-customer shape variation the k parts reproduce (1 = all of it)."""
    resid = ((S - W @ H) ** 2).sum()
    tot = ((S - S.mean(axis=0)) ** 2).sum()
    return float(1 - resid / tot)


def match_components(Ha: np.ndarray, Hb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Hungarian match on cosine similarity. Returns (index in b for each a, cosine per pair)."""
    na = Ha / np.linalg.norm(Ha, axis=1, keepdims=True)
    nb = Hb / np.linalg.norm(Hb, axis=1, keepdims=True)
    cos = na @ nb.T
    ia, ib = linear_sum_assignment(-cos)
    order = np.empty(len(Ha), dtype=int)
    order[ia] = ib
    return order, cos[np.arange(len(Ha)), order]


def mix(W: np.ndarray) -> np.ndarray:
    tot = W.sum(axis=1, keepdims=True)
    return np.where(tot > 0, W / np.where(tot > 0, tot, 1.0), 0.0)


# --------------------------------------------------------------------------- label-free description
def describe_component(h: np.ndarray) -> dict:
    """Timing of a component read from its own 12 x 24 shape only (no appliance labels)."""
    g = h.reshape(12, 24)
    by_month = g.sum(axis=1)
    by_hour = g.sum(axis=0)
    months = np.array(MONTH_ORDER)
    winter = by_month[np.isin(months, C.WINTER_MONTHS)].sum()
    summer = by_month[np.isin(months, C.SUMMER_MONTHS)].sum()
    # season tag vs a flat-year expectation (winter 3/12, summer 4/12 of the energy)
    w_lift, s_lift = winter / (3 / 12), summer / (4 / 12)
    season = ("winter" if w_lift >= 1.5 and w_lift > s_lift else
              "summer" if s_lift >= 1.5 else
              "cold-half" if w_lift >= 1.2 else "warm-half" if s_lift >= 1.2 else "year-round")
    # tightest window of consecutive hours (wrapping midnight) holding half the component's daily energy
    best = (24, 0)
    for L in range(1, 25):
        sums = np.array([by_hour[(s + np.arange(L)) % 24].sum() for s in range(24)])
        if sums.max() >= 0.5:
            best = (L, int(sums.argmax()))
            break
    L, start = best
    timing = "all day" if L >= 12 else f"{start:02d}-{(start + L) % 24:02d}h"
    top_month = MONTH_ABBR[int(by_month.argmax())]
    return {"season": season, "half_energy_window": timing, "window_hours": L, "peak_month": top_month,
            "peak_hour": int(by_hour.argmax()), "winter_share": float(winter), "summer_share": float(summer)}


def describe_text(d: dict) -> str:
    when = "flat, all hours" if d["window_hours"] >= 12 else f"half its energy in {d['half_energy_window']}"
    return f"{d['season']}, {when}, peaks {d['peak_month']}"


# --------------------------------------------------------------------------- association review
def appliance_targets(k: pd.DataFrame) -> dict[str, pd.Series]:
    """Detected appliances and labels, used only *after* the components are described."""
    return {
        "EV L2 signature": k.ev_class == "L2 detectable",
        "EV L1 signature": k.ev_class == "L1 detectable",
        "EV label": k.label_ev,
        "electric heat (any)": k.electric_heat,
        "heat pump (HP, HP+backup)": k.heat_type.isin(["HP", "HP+backup"]),
        "resistive heat": k.heat_type == "resistive",
        "HP label": k.label_hp,
        "central AC": k.ac_type == "central",
        "pool": k.pool,
        "PV signature": k.pv,
        "water heater (low conf.)": k.water_heater_lowconf,
        "hot tub (low conf.)": k.hot_tub_lowconf,
    }


def associations(M: np.ndarray, targets: dict[str, pd.Series]) -> pd.DataFrame:
    """AUC of each component's weight for each flag (0.5 = no relation, 1 = weight ranks every flagged home first)."""
    rows = []
    for name, y in targets.items():
        y = y.fillna(False).to_numpy(dtype=bool)
        if y.sum() < 5 or (~y).sum() < 5:
            continue
        for j in range(M.shape[1]):
            rows.append({"component": j, "target": name, "n_flagged": int(y.sum()),
                         "auc": roc_auc_score(y, M[:, j]),
                         "mean_weight_flagged": float(M[y, j].mean()), "mean_weight_rest": float(M[~y, j].mean())})
    return pd.DataFrame(rows)


def name_components(assoc: pd.DataFrame, desc: list[dict]) -> pd.DataFrame:
    best = assoc.loc[assoc.groupby("component").auc.idxmax()].set_index("component")
    out = []
    for j, d in enumerate(desc):
        b = best.loc[j]
        explained = b.auc >= ASSOC_AUC
        out.append({"component": j, "label_free": describe_text(d),
                    "best_match": b.target, "best_auc": round(float(b.auc), 3),
                    # the window keeps two parts that match the same flag apart (e.g. daytime vs overnight heat)
                    "name": f"{b.target}, {d['half_energy_window']}" if explained else "unexplained",
                    "explained": bool(explained)})
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- exemplars
def exemplar_classes(k: pd.DataFrame) -> list[dict]:
    """One exemplar per appliance class. `feats` define 'typical' for the medoid rule;
    `prefer` = use labeled members when there are enough of them; `lowconf` = hourly data blurs it."""
    hp = k.heat_type.isin(["HP"])
    return [
        {"cls": "EV, L2 charging", "mask": k.ev_class == "L2 detectable", "prefer": k.label_ev,
         "feats": ["ev_l2_per_week", "ev_l2_block_kw", "ev_l2_block_hours"]},
        {"cls": "EV, L1 charging", "mask": k.ev_class == "L1 detectable", "prefer": k.label_ev,
         "feats": ["ev_l1_night_share", "night_quiet_kwh"]},
        {"cls": "EV label, no home-charging signature", "mask": k.label_ev & (k.ev_class == "no home-charging signature"),
         "prefer": None, "feats": ["annual_kwh", "peak_kw"]},
        {"cls": "Heat pump", "mask": hp, "prefer": k.label_hp, "feats": ["heat_slope", "heat_break_ratio"]},
        {"cls": "Heat pump + resistance backup", "mask": k.heat_type == "HP+backup", "prefer": k.label_hp,
         "feats": ["heat_slope", "heat_break_ratio"]},
        {"cls": "Resistive heat", "mask": k.heat_type == "resistive", "prefer": None,
         "feats": ["heat_slope", "heat_break_ratio"]},
        {"cls": "Central AC", "mask": (k.ac_type == "central") & ~k.electric_heat, "prefer": None,
         "feats": ["cool_slope", "cool_bp_f"]},
        {"cls": "Pool pump", "mask": k.pool, "prefer": None, "feats": ["pool_timer_score", "pool_run_hours"]},
        {"cls": "Solar PV", "mask": k.pv, "prefer": k.label_pv,
         "feats": ["pv_midday_zero_summer", "pv_zero_season_contrast"], "allow_pv": True},
        {"cls": "Solar PV + battery", "mask": k.pv_battery, "prefer": None,
         "feats": ["pv_midday_zero_summer", "evening_share"], "allow_pv": True},
        {"cls": "Water heater", "mask": k.water_heater_lowconf, "prefer": None,
         "feats": ["wh_morning_bump_kwh", "wh_bump_freq"], "lowconf": True},
        {"cls": "Hot tub", "mask": k.hot_tub_lowconf, "prefer": None, "feats": ["hot_tub_cycle_share"], "lowconf": True},
    ]


def pick_exemplar(df: pd.DataFrame, spec: dict, min_days: int = 350, min_labeled: int = 3) -> dict:
    """Medoid rule: the class member closest to the class median on the class's defining features
    (robust z = (x - median) / IQR). Typical, not the most extreme case. Full-year meters only, no
    very-high-load accounts, and no PV homes outside the PV classes (clipped zeros hide other loads)."""
    mask = spec["mask"].fillna(False).to_numpy(dtype=bool) & df.in_model.to_numpy()
    n_class = int(mask.sum())
    cand = mask & (df.n_days >= min_days).to_numpy() & ~(df.very_high_load | df.likely_nonresidential).to_numpy()
    if not spec.get("allow_pv"):
        cand &= ~df.pv.to_numpy()
    if cand.sum() == 0:            # tiny classes (PV + battery): relax the history rule
        cand = mask
    used_label = False
    if spec.get("prefer") is not None:
        lab = cand & spec["prefer"].fillna(False).to_numpy(dtype=bool)
        if lab.sum() >= min_labeled:
            cand, used_label = lab, True
    sub = df.loc[cand, spec["feats"]].astype(float)
    med = sub.median()
    iqr = (sub.quantile(0.75) - sub.quantile(0.25)).replace(0, np.nan).fillna(sub.std()).replace(0, 1.0)
    dist = np.sqrt((((sub - med) / iqr) ** 2).fillna(1e6).sum(axis=1))
    i = dist.idxmin()
    return {"cls": spec["cls"], "customer_id": int(df.customer_id[i]), "class_size": n_class,
            "candidates": int(cand.sum()), "picked_from_labeled": used_label,
            "confidence": "low (hourly data blurs it)" if spec.get("lowconf") else "",
            **{f: float(df.at[i, f]) for f in spec["feats"]},
            **{f"class_median_{f}": float(med[f]) for f in spec["feats"]}}
