"""Loaders for the processed data, with the task 1.0b decisions applied in one place."""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from avangrid import config as C


@lru_cache(maxsize=1)
def decisions() -> dict:
    """Exclusions and flags written by scripts/03b_checks.py."""
    if not C.DECISIONS.exists():
        raise FileNotFoundError(f"{C.DECISIONS} missing: run scripts/03b_checks.py first")
    return json.loads(C.DECISIONS.read_text())


def load_matrix(mmap: bool = True, repair: bool = True) -> np.ndarray:
    """Hourly kWh, float32 [n_customers, 8760], NaN where there is no reading.

    mmap=True is read-only and cheap. repair=True loads into memory and nulls the
    spike hours listed in decisions.json (requires mmap=False semantics, so it copies).
    """
    mat = np.load(C.LOAD_MATRIX, mmap_mode="r" if mmap else None)
    spikes = decisions().get("null_spike_slots", {}) if repair else {}
    if not spikes:
        return mat
    mat = np.array(mat)  # writable copy
    pos = customer_position()
    for cid, slots in spikes.items():
        mat[pos[int(cid)], slots] = np.nan
    return mat


@lru_cache(maxsize=1)
def _customers_cached() -> pd.DataFrame:
    cust = pd.read_parquet(C.CUSTOMER_INDEX)
    d = decisions()
    ids = cust.customer_id
    cust["stuck"] = ids.isin(d["exclude_stuck"])
    cust["data_error"] = ids.isin(d["drop_data_error"])
    cust["short_history"] = cust.n_days < C.MIN_DAYS_FOR_MODEL
    cust["very_high_load"] = ids.isin(d["very_high_load"])
    cust["likely_nonresidential"] = ids.isin(d["likely_nonresidential"])
    cust["in_model"] = ~(cust.stuck | cust.data_error | cust.short_history)
    cust["exclude_reason"] = np.select(
        [cust.data_error, cust.stuck, cust.short_history],
        ["data_error", "stuck_or_vacant_meter", "short_history"], "")
    cust["row"] = np.arange(len(cust))
    return cust


def load_customers() -> pd.DataFrame:
    """One row per matrix row: coverage, labels, 1.0b flags, `in_model`, `row`."""
    return _customers_cached().copy()


def customer_position() -> dict[int, int]:
    cust = _customers_cached()
    return dict(zip(cust.customer_id.to_numpy().tolist(), cust.row.to_numpy().tolist()))


@lru_cache(maxsize=1)
def _hours_cached() -> pd.DataFrame:
    h = pd.read_parquet(C.HOUR_INDEX)
    d = pd.to_datetime(h.usage_date)
    h["hod"] = (h.hour_ending - 1).astype("int8")        # hour starting, 0-23
    h["month"] = d.dt.month.astype("int8")
    h["dow"] = d.dt.dayofweek.astype("int8")
    h["weekday"] = h.dow < 5
    h["day"] = (h.slot // 24).astype("int16")
    h["season"] = np.select(
        [h.month.isin(C.SUMMER_MONTHS), h.month.isin(C.WINTER_MONTHS)], ["summer", "winter"], "shoulder")
    h["odd_week"] = ((h.day // 7) % 2 == 1)               # interleaved stability split (PLAN.md §5)
    return h


def load_hours() -> pd.DataFrame:
    """One row per matrix column: date, hour, weather, calendar and season helpers."""
    return _hours_cached().copy()


def model_rows() -> np.ndarray:
    """Matrix row indices of the modeling population."""
    cust = _customers_cached()
    return cust.row.to_numpy()[cust.in_model.to_numpy()]
