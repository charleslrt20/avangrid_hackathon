"""Data layer for the Streamlit demo (task 2.5). Pure functions over cached pipeline outputs, no Streamlit,
so they can be tested and timed outside the app.

Per-customer hourly load is read one row at a time from the memory-mapped matrix, and battery dispatch
is re-simulated for that one home with the same code, battery and event calls as task 1.6, so the
chart and the stored relief numbers agree.
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from avangrid import bess as B
from avangrid import config as C
from avangrid import io
from avangrid.features.build import DESCRIPTIONS
from avangrid.features.weather import daily_kwh, daily_weather

FEATURE_COLS = ["customer_id", "in_model", "exclude_reason", "label_ev", "label_hp", "label_pv", "n_days",
                "annual_kwh", "peak_kw", "p95_kw", "load_factor", "cp_kw_summer", "cp_kw_winter",
                "winter_summer_ratio", "evening_share", "base_kwh_day", "heat_slope", "heat_break_ratio",
                "heat_bp_f", "cool_slope", "cool_bp_f", "ev_l2_per_week", "ev_l2_block_kw", "ev_l2_block_hours",
                "ev_l1_night_share", "pv_midday_zero_summer", "pv_night_zero_summer", "pv_zero_season_contrast",
                "pool_timer_score", "pool_run_hours", "wh_bump_freq", "hot_tub_cycle_share", "resid_trend_per30d"]
CLASSIFY_COLS = ["customer_id", "ev_class", "ev_physics", "ev_charger_kw", "heat_type", "electric_heat", "ac_type",
                 "pv", "pv_battery", "pool", "water_heater_lowconf", "hot_tub_lowconf", "ev_pu_pct", "hp_pu_pct",
                 "ev_conf", "hp_conf"]
BESS_COLS = ["customer_id", "tier", "priority", "size", "relief_kw_summer", "relief_kw_winter",
             "relief_kw_summer_perfect", "relief_kw_winter_perfect", "firm_kw_summer", "firm_kw_winter",
             "need_kwh_p90", "event_coverage", "dr_relief_kw_summer", "dr_relief_kw_winter",
             "avoided_usd_yr_low", "avoided_usd_yr_mid", "avoided_usd_yr_high"]


@lru_cache(maxsize=1)
def customer_table() -> pd.DataFrame:
    """One row per customer: features, appliance flags, segment, BESS results, NMF mix, exemplar class."""
    f = pd.read_parquet(C.FEATURES, columns=FEATURE_COLS)
    k = pd.read_parquet(C.OUTPUTS / "classify.parquet", columns=CLASSIFY_COLS)
    s = pd.read_parquet(C.OUTPUTS / "segments.parquet",
                        columns=["customer_id", "segment", "archetype", "archetype_name", "anomaly"])
    b = pd.read_parquet(C.OUTPUTS / "bess_results.parquet", columns=BESS_COLS)
    d = f.merge(k, on="customer_id", how="left").merge(s, on="customer_id", how="left") \
         .merge(b, on="customer_id", how="left")
    mix_path = C.OUTPUTS / "nmf_mix.parquet"
    if mix_path.exists():
        mix = pd.read_parquet(mix_path)
        d = d.merge(mix[["customer_id"] + [c for c in mix.columns if c.startswith("share_C")]],
                    on="customer_id", how="left")
    ex_path = C.OUTPUTS / "exemplars.parquet"
    if ex_path.exists():
        ex = pd.read_parquet(ex_path, columns=["customer_id", "cls"]).rename(columns={"cls": "exemplar_of"})
        d = d.merge(ex.drop_duplicates("customer_id"), on="customer_id", how="left")
    d["bess_rank"] = d.priority.rank(ascending=False, method="first")
    return d.set_index("customer_id", drop=False)


def exemplars() -> pd.DataFrame:
    p = C.OUTPUTS / "exemplars.parquet"
    return pd.read_parquet(p, columns=["cls", "customer_id"]) if p.exists() else pd.DataFrame(columns=["cls", "customer_id"])


@lru_cache(maxsize=1)
def hours() -> pd.DataFrame:
    return io.load_hours()


@lru_cache(maxsize=1)
def daily_wx() -> pd.DataFrame:
    w = daily_weather(hours())
    w["date"] = C.START + pd.to_timedelta(np.arange(len(w)), unit="D")
    return w


@lru_cache(maxsize=1)
def _matrix():
    return np.load(C.LOAD_MATRIX, mmap_mode="r")


def load_row(customer_id: int) -> np.ndarray:
    """Hourly kWh [8760] for one customer, with the task 1.0b spike repairs applied."""
    pos = io.customer_position()[int(customer_id)]
    x = np.array(_matrix()[pos], dtype=np.float32)
    slots = io.decisions().get("null_spike_slots", {})
    if slots and str(customer_id) in slots:
        x[slots[str(customer_id)]] = np.nan
    return x


def carpet(customer_id: int) -> pd.DataFrame:
    """[24 hours x 365 days] for a heatmap; columns are dates."""
    x = load_row(customer_id).reshape(-1, 24).T
    return pd.DataFrame(x, index=range(24), columns=daily_wx().date)


def daily_vs_temp(customer_id: int) -> pd.DataFrame:
    w = daily_wx()
    return pd.DataFrame({"date": w.date, "temp_f": w.temp, "kwh": daily_kwh(load_row(customer_id)[None, :])[0],
                         "weekend": w.weekend})


@lru_cache(maxsize=1)
def peak_cols() -> dict[str, np.ndarray]:
    """System top hours per season as matrix columns (from outputs/summary/system_peaks.csv)."""
    p = pd.read_csv(C.OUTPUTS_SUMMARY / "system_peaks.csv", parse_dates=["date"])
    col = (p.date - C.START).dt.days * 24 + p.hour_ending - 1
    return {s: np.sort(col[p.season == s].to_numpy()) for s in ("summer", "winter")}


@lru_cache(maxsize=1)
def event_calls() -> tuple[dict, dict]:
    return B.realistic_targets(hours())


def dispatch(customer_id: int, season: str, days: int = 4, units: int = 2) -> tuple[pd.DataFrame, dict]:
    """Re-run the realistic (task 1.6) dispatch for one home and return the hours around the season's
    biggest system day, plus the year's relief numbers for that home."""
    row = customer_table().loc[int(customer_id)]
    x = load_row(customer_id)
    bat = C.BATTERY.with_units(units) if units != C.BATTERY.units else C.BATTERY
    targets, called = event_calls()
    cap = np.array([max(float(row.p95_kw), 0.0)])
    r = B.simulate(x[None, :], targets, bat, cap, trace_rows=np.array([0]))
    pc = peak_cols()[season]
    top_day = int(pd.Series(pc // 24).value_counts().idxmax())
    d0 = max(0, min(top_day - 1, C.N_DAYS - days))
    cols = np.arange(d0 * 24, (d0 + days) * 24)
    h = hours()
    ts = pd.to_datetime(h.usage_date.iloc[cols].to_numpy()) + pd.to_timedelta(h.hod.iloc[cols].to_numpy(), unit="h")
    ev = np.zeros(len(cols), dtype=bool)
    for i, c in enumerate(cols):
        t = targets.get(int(c // 24))
        ev[i] = t is not None and int(c % 24) in set(int(v) for v in t)
    load = np.nan_to_num(x[cols])
    df = pd.DataFrame({"time": ts, "load_kw": load, "discharge_kw": r.discharge[0, cols],
                       "charge_kw": r.charge[0, cols], "soc_kwh": r.soc_trace[0, cols],
                       "system_top_hour": np.isin(cols, pc), "event_hour": ev})
    df["net_kw"] = df.load_kw - df.discharge_kw + df.charge_kw
    summary = {
        "relief_kw": float(B.cp_relief(r, x[None, :], pc)[0]),
        "relief_kw_other": float(B.cp_relief(r, x[None, :], peak_cols()["winter" if season == "summer" else "summer"])[0]),
        "battery_kwh": bat.kwh, "battery_kw": bat.kw,
        "soc_min": float(r.soc_min_seen[0]), "soc_max": float(r.soc_max_seen[0]),
        "events_called": int(len(called[season])),
        "top_hours_in_window": int(df.system_top_hour.sum()),
    }
    return df, summary


# --------------------------------------------------------------------------- explanations
EVIDENCE = {
    "EV charging": ("ev_class", "ev_conf", ["ev_l2_per_week", "ev_l2_block_kw", "ev_l2_block_hours", "ev_l1_night_share"]),
    "Space heating": ("heat_type", "hp_conf", ["heat_slope", "heat_break_ratio", "heat_bp_f"]),
    "Air conditioning": ("ac_type", None, ["cool_slope", "cool_bp_f"]),
    "Solar PV": ("pv", None, ["pv_midday_zero_summer", "pv_night_zero_summer", "pv_zero_season_contrast"]),
    "Pool pump": ("pool", None, ["pool_timer_score", "pool_run_hours"]),
    "Water heater (low conf.)": ("water_heater_lowconf", None, ["wh_bump_freq"]),
    "Hot tub (low conf.)": ("hot_tub_lowconf", None, ["hot_tub_cycle_share"]),
}


@lru_cache(maxsize=1)
def population_medians() -> pd.Series:
    d = customer_table()
    cols = sorted({c for _, _, cs in EVIDENCE.values() for c in cs})
    return d.loc[d.in_model, cols].median()


def appliance_evidence(customer_id: int) -> pd.DataFrame:
    """Per appliance: what was detected, its confidence tag, and the evidence values vs the population median."""
    r = customer_table().loc[int(customer_id)]
    med = population_medians()
    rows = []
    for name, (flag, conf, feats) in EVIDENCE.items():
        v = r[flag]
        detected = (v if isinstance(v, str) else ("yes" if bool(v) else "no")) if pd.notna(v) else "n/a"
        found = detected not in ("no", "n/a", "none/non-electric", "none/minimal", "no home-charging signature")
        if not found:
            tag = ""
        elif conf and isinstance(r[conf], str) and r[conf]:
            tag = r[conf]
        else:
            tag = "low (hourly data)" if "low conf" in name else "physics gate"
        ev = []
        for c in feats:
            val = r[c]
            if pd.isna(val):
                continue
            desc = DESCRIPTIONS.get(c, ("", "", c))[2]
            ev.append(f"{desc}: {val:.3g} (median {med[c]:.3g})")
        rows.append({"appliance": name, "detected": detected, "confidence": tag, "evidence": "\n".join(ev)})
    return pd.DataFrame(rows)


@lru_cache(maxsize=1)
def segment_profiles() -> pd.DataFrame:
    """Mean 72-dim seasonal shape (load / own annual mean) per segment, modeling population."""
    prof = pd.read_parquet(C.DATA_PROC / "shape_profiles.parquet")
    seg = customer_table()[["segment", "in_model"]]
    prof = prof.merge(seg, left_on="customer_id", right_index=True)
    prof = prof[prof.in_model]
    cols = [c for c in prof.columns if c.startswith("prof_")]
    out = prof.groupby("segment")[cols].mean()
    out.loc["All modeled customers"] = prof[cols].mean()
    return out


def cards() -> dict[str, dict]:
    p = C.INSIGHTS / "segment_cards.json"
    return {c["segment"]: c for c in json.loads(p.read_text())} if p.exists() else {}


def summary_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(C.OUTPUTS_SUMMARY / f"{name}.csv")
