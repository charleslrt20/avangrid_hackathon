"""Segmentation (task 1.4): rule-based business segments x K-Means shape archetypes.

Load shapes form a continuum (benchmark silhouette ~0.12), so K-Means groups are presented as
*archetypes on a continuum*; the primary, actionable grouping is the rule-based business segment.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.metrics import adjusted_rand_score, davies_bouldin_score, silhouette_score
from sklearn.preprocessing import QuantileTransformer

from avangrid import config as C

# Order = priority: a customer gets the first segment whose rule fires.
SEGMENT_ORDER = [
    "Very high load / non-residential",
    "Solar PV",
    "Electrified: EV + electric heat",
    "EV home",
    "Electric-heat home",
    "Low use / seasonal",
    "Winter peak contributor",
    "Summer peak contributor",
    "Emerging load",
    "Typical",
]
EMERGING_TREND = 1.0       # kWh/day per 30 days of weather-normalised growth (~+12 kWh/day over a year ~ an EV)
LOW_USE_KWH = 2000.0       # annual kWh; below a typical occupied home's base load
PEAK_CONTRIB_Q = 0.85      # top 15% of coincident-peak kW in the modeling population


def business_segments(f: pd.DataFrame, k: pd.DataFrame) -> pd.DataFrame:
    cs = f.cp_kw_summer.quantile(PEAK_CONTRIB_Q)
    cw = f.cp_kw_winter.quantile(PEAK_CONTRIB_Q)
    rules = {
        "Very high load / non-residential": f.very_high_load | f.likely_nonresidential,
        "Solar PV": k.pv,
        "Electrified: EV + electric heat": k.ev_physics & k.electric_heat,
        "EV home": k.ev_physics,
        "Electric-heat home": k.electric_heat,
        "Low use / seasonal": (f.annual_kwh < LOW_USE_KWH) | (f.zero_share >= 0.2),
        "Winter peak contributor": f.cp_kw_winter >= cw,
        "Summer peak contributor": f.cp_kw_summer >= cs,
        "Emerging load": f.resid_trend_per30d >= EMERGING_TREND,
        "Typical": pd.Series(True, index=f.index),
    }
    seg = pd.Series(np.select([rules[s].fillna(False).to_numpy() for s in SEGMENT_ORDER], SEGMENT_ORDER, "Typical"),
                    index=f.index)
    out = pd.DataFrame({"segment": seg})
    # secondary flags (a customer can carry several)
    out["flag_summer_peak"] = (f.cp_kw_summer >= cs).to_numpy()
    out["flag_winter_peak"] = (f.cp_kw_winter >= cw).to_numpy()
    out["flag_emerging"] = (f.resid_trend_per30d >= EMERGING_TREND).to_numpy()
    out.attrs["thresholds"] = {"cp_kw_summer_p85": float(cs), "cp_kw_winter_p85": float(cw)}
    return out


def profile_matrix(prof: pd.DataFrame) -> np.ndarray:
    X = prof.drop(columns=["customer_id"]).to_numpy(dtype=float)
    col_med = np.nanmedian(X, axis=0)
    X = np.where(np.isnan(X), col_med, X)
    return np.clip(X, 0, 6)     # cap extreme shape ratios (near-zero-mean homes)


def choose_k(X: np.ndarray, ks=range(4, 11), sample: int = 8000, seed: int = C.SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), size=min(sample, len(X)), replace=False)
    rows = []
    for k in ks:
        km = KMeans(k, n_init=4, random_state=seed).fit(X)
        lab = km.labels_[idx]
        rows.append({"k": k, "silhouette": silhouette_score(X[idx], lab),
                     "davies_bouldin": davies_bouldin_score(X[idx], lab), "inertia": km.inertia_})
    return pd.DataFrame(rows)


def fit_archetypes(X: np.ndarray, k: int, seed: int = C.SEED) -> KMeans:
    return KMeans(k, n_init=10, random_state=seed).fit(X)


def bootstrap_ari(X: np.ndarray, k: int, ref: np.ndarray, n_boot: int = 20, seed: int = C.SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = []
    for b in range(n_boot):
        idx = rng.choice(len(X), size=len(X), replace=True)
        km = KMeans(k, n_init=2, random_state=seed + b).fit(X[idx])
        out.append(adjusted_rand_score(ref, km.predict(X)))
    return np.array(out)


def name_archetype(centroid: np.ndarray) -> str:
    """Readable name from the centroid's shape (winter | shoulder | summer x 24 h, scaled to annual mean)."""
    w, su = centroid[:24], centroid[48:]
    ratio = w.mean() / max(su.mean(), 1e-6)
    season = ("strongly winter-peaking" if ratio >= 2.5 else "winter-leaning" if ratio >= 1.3 else
              "balanced" if ratio >= 0.8 else "summer-leaning" if ratio >= 0.55 else "strongly summer-peaking")
    pk_w, pk_su = int(np.argmax(w)) + 1, int(np.argmax(su)) + 1     # hour ending
    if pk_w <= 10:
        timing = "winter morning + evening peaks"
    else:
        pk = pk_su if ratio < 1 else pk_w
        timing = "early-evening peak" if pk <= 19 else "late-evening peak"
    spread = centroid.max() / max(centroid.min(), 1e-3)
    shape = "flat" if spread < 2.3 else "peaky" if spread > 3.5 else ""
    day = "daytime summer use" if su[0:5].mean() < 0.6 * su[10:15].mean() else ""
    return ", ".join(x for x in (season, timing, shape, day) if x)


def anomalies(f: pd.DataFrame, cols: list[str], contamination: float = 0.01, seed: int = C.SEED) -> pd.DataFrame:
    X = f[cols].fillna(f[cols].median())
    Xt = QuantileTransformer(output_distribution="normal", n_quantiles=200, random_state=0).fit_transform(X)
    iso = IsolationForest(n_estimators=300, contamination=contamination, random_state=seed).fit(Xt)
    return pd.DataFrame({"anomaly_score": -iso.score_samples(Xt), "anomaly": iso.predict(Xt) == -1}, index=f.index)
