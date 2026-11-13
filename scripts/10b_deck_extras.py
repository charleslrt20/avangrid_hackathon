"""Extra deck facts (review round 2): bill savings, demand flex in kW, solar-charged batteries, confidence table.

Writes outputs/summary/deck_extras.json (aggregates only). Runs in about a minute on the cached load matrix.
- Bill savings use the PLACEHOLDER TOU tariff in config.py (TARIFF): the battery discharges into the home's own
  weekday on-peak load (no export) and recharges off-peak; savings = on-peak kWh avoided x on-peak rate minus the
  off-peak kWh needed to refill it (divided by round-trip efficiency) x off-peak rate. Event days included.
- Demand flex (no battery) = the task 1.6 DR model already in outputs/bess_results.parquet (rebound included).
- Solar charging: delivered-only meters hide PV surplus, so we count the midday hours a PV home sits at 0 kWh
  (solar covers the whole house load and the rest is exported): on those days the battery can charge from solar.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from avangrid import app_data as A  # noqa: E402
from avangrid import config as C  # noqa: E402
from avangrid import io  # noqa: E402

S = C.OUTPUTS_SUMMARY


def main() -> None:
    cust = io.load_customers()
    h = io.load_hours()
    res = pd.read_parquet(C.OUTPUTS / "customer_results.parquet").set_index("customer_id")
    bess = pd.read_parquet(C.OUTPUTS / "bess_results.parquet").set_index("customer_id")
    mat = np.load(C.LOAD_MATRIX, mmap_mode="r")
    pos = io.customer_position()
    out: dict = {}

    # ---------------------------------------------------------------- bill savings (placeholder TOU)
    tf, bat = C.TARIFF, C.BATTERY
    hod, wkday = h.hod.to_numpy(), h.weekday.to_numpy().astype(bool)
    on = np.isin(hod, tf.on_peak_hours) & (wkday if tf.weekdays_only else True)
    ndays = len(h) // 24
    on_day = on.reshape(ndays, 24)
    groups = {"Tier 1": bess.index[bess.tier == "Tier 1"], "Tier 2": bess.index[bess.tier == "Tier 2"],
              "All candidates": bess.index}
    ids = bess.index.to_numpy()
    rows = np.array([pos[int(c)] for c in ids])
    save = np.zeros(len(ids))
    onpk_kwh = np.zeros(len(ids))
    for i0 in range(0, len(ids), 2000):
        r = rows[i0:i0 + 2000]
        L = np.nan_to_num(np.asarray(mat[np.sort(r)], dtype=np.float32))
        L = L[np.argsort(np.argsort(r))]  # back to request order
        Ld = L.reshape(len(r), ndays, 24)
        served = np.minimum(Ld, bat.kw) * on_day[None]
        daily = np.minimum(served.sum(axis=2), bat.kwh)       # kWh the battery can cover each day
        e = daily.sum(axis=1)
        save[i0:i0 + 2000] = e * tf.on_peak_rate - e / bat.rte * tf.off_peak_rate
        onpk_kwh[i0:i0 + 2000] = (Ld * on_day[None]).sum(axis=(1, 2))
    sv = pd.Series(save, index=ids)
    ok = pd.Series(onpk_kwh, index=ids)
    out["bill"] = {
        "on_peak_rate": tf.on_peak_rate, "off_peak_rate": tf.off_peak_rate,
        "on_peak_start": min(tf.on_peak_hours), "on_peak_end": max(tf.on_peak_hours) + 1,
        **{f"{k.lower().replace(' ', '_')}_usd_yr_median": float(sv.loc[v].median()) for k, v in groups.items()},
        **{f"{k.lower().replace(' ', '_')}_usd_yr_mean": float(sv.loc[v].mean()) for k, v in groups.items()},
        "tier1_onpeak_covered_share": float((sv.loc[groups["Tier 1"]] > 0).mean()),
        "tier1_onpeak_kwh_median": float(ok.loc[groups["Tier 1"]].median()),
        "tier1_grid_usd_yr_mid": float(bess.loc[groups["Tier 1"], "avoided_usd_yr_mid"].mean()),
    }
    out["bill"]["tier1_total_usd_yr_mid"] = out["bill"]["tier1_grid_usd_yr_mid"] + out["bill"]["tier_1_usd_yr_mean"]

    # ---------------------------------------------------------------- demand flex without a battery (kW)
    b = bess.join(res[["ac_type", "ev_class", "heat_type"]], rsuffix="_r")
    eheat = b.heat_type_r.isin(["HP", "HP+backup", "resistive", "electric (type unclear)"])
    evh = b.ev_class.isin(["L2 detectable", "L1 detectable"])
    cac = b.ac_type == "central"
    flex = []
    for name, m, season in [("Managed EV charging", evh, "summer"), ("Managed EV charging", evh, "winter"),
                            ("AC thermostat", cac & ~evh, "summer"), ("Heat thermostat", eheat & ~evh, "winter")]:
        dr = b.loc[m, f"dr_relief_kw_{season}"]
        bt = b.loc[m, f"relief_kw_{season}"]
        flex.append({"program": name, "season": season, "homes": int(m.sum()), "dr_kw_per_home": float(dr.mean()),
                     "dr_mw": float(dr.sum() / 1000), "battery_kw_per_home": float(bt.mean())})
    t1 = b.tier == "Tier 1"
    out["flex"] = flex
    out["flex_tier1"] = {s: {"dr_kw": float(b.loc[t1, f"dr_relief_kw_{s}"].mean()),
                             "battery_kw": float(b.loc[t1, f"relief_kw_{s}"].mean()),
                             "dr_mw": float(b.loc[t1, f"dr_relief_kw_{s}"].sum() / 1000)} for s in ("summer", "winter")}

    # ---------------------------------------------------------------- solar + battery
    pv_ids = res.index[res.solar_pv.fillna(False).astype(bool) & res.in_model]
    pr = np.array([pos[int(c)] for c in pv_ids])
    P = np.asarray(mat[np.sort(pr)], dtype=np.float32).reshape(len(pr), ndays, 24)
    month_day = h.month.to_numpy().reshape(ndays, 24)[:, 0]
    midday = (np.arange(24) >= 9) & (np.arange(24) <= 14)
    zero = (P[:, :, midday] == 0)
    zh = zero.sum(axis=2)                                      # midday zero hours per home-day
    summer_d = np.isin(month_day, C.SUMMER_MONTHS)
    winter_d = np.isin(month_day, C.WINTER_MONTHS)
    targets, called = A.event_calls()
    ev_s = np.array(sorted(called["summer"]))
    ev_w = np.array(sorted(called["winter"]))
    # rough solar surplus floor: each zero hour means PV >= house load; use the home's own median load at
    # that hour on non-zero days of the same season as the surplus floor
    out["pv"] = {
        "homes": int(len(pv_ids)), "with_battery_detected": int(res.pv_with_battery.fillna(False).sum()),
        "zero_hours_summer": float(np.nanmean(zh[:, summer_d])), "zero_hours_winter": float(np.nanmean(zh[:, winter_d])),
        "days3_summer": float(np.nanmean(zh[:, summer_d] >= 3)), "days3_winter": float(np.nanmean(zh[:, winter_d] >= 3)),
        "event_days3_summer": float(np.nanmean(zh[:, ev_s] >= 3)), "event_days3_winter": float(np.nanmean(zh[:, ev_w] >= 3)),
        "tier1": int((bess.loc[bess.index.intersection(pv_ids), "tier"] == "Tier 1").sum()),
        "tier12": int(bess.loc[bess.index.intersection(pv_ids), "tier"].isin(["Tier 1", "Tier 2"]).sum()),
        "relief_kw_summer": float(bess.loc[bess.index.intersection(pv_ids), "relief_kw_summer"].mean()),
        "relief_kw_winter": float(bess.loc[bess.index.intersection(pv_ids), "relief_kw_winter"].mean()),
        "evening_peak_share": None,
    }
    # share of PV homes' summer system-peak-hour load (the battery's job) vs a typical home
    pc = A.peak_cols()
    Pf = P.reshape(len(pr), -1)
    out["pv"]["cp_kw_summer"] = float(np.nanmean(Pf[:, pc["summer"]]))
    out["pv"]["cp_kw_winter"] = float(np.nanmean(Pf[:, pc["winter"]]))
    # battery recharge from solar: kWh the battery needs per event day (p50) vs the midday zero hours
    rk = bess.loc[bess.index.intersection(pv_ids)]
    out["pv"]["recharge_kwh_event_p50"] = float(np.nanmedian(rk.need_kwh_p90)) if "need_kwh_p90" in rk else None

    # ---------------------------------------------------------------- confidence / summary stats per appliance
    stab = pd.read_csv(S / "validation_flag_stability.csv").set_index("flag")
    ac = pd.read_csv(S / "appliance_counts.csv")
    lab = cust.set_index("customer_id")
    rr = res.join(lab[["label_ev", "label_hp", "label_pv"]], rsuffix="_c")
    inm = rr.in_model.astype(bool)

    def recall(flag, label):
        m = inm & rr[label].astype(bool)
        return float(flag[m].mean())

    ev_flag = rr.ev_class.isin(["L2 detectable", "L1 detectable"])
    hp_flag = rr.heat_type.isin(["HP", "HP+backup"])
    eh_flag = rr.heat_type.isin(["HP", "HP+backup", "resistive", "electric (type unclear)"])
    pv_flag = rr.solar_pv.fillna(False).astype(bool)
    tab = []

    def add(name, cnt_row, stab_row, method, check):
        a = ac.loc[ac.appliance.str.strip() == cnt_row].iloc[0]
        k = float(stab.loc[stab_row, "kappa_odd_vs_even"]) if stab_row else None
        tab.append({"appliance": name, "homes": int(a.customers), "share": float(a.share), "confidence": a.confidence,
                    "kappa": k, "method": method, "check": check})

    add("EV home charging", "EV (L2 or L1 home charging)", "EV home charging (L2 or L1)", "charging blocks / overnight plateau",
        f"{recall(ev_flag, 'label_ev'):.0%} of labeled EV homes flagged")
    add("Electric space heat", "Electric space heat (any)", "Electric heat (any)", "cold-weather slope",
        f"{recall(eh_flag, 'label_hp'):.0%} of labeled HP homes flagged")
    add("Heat pump", "HP / HP+backup", "Heat pump (HP / HP+backup)", "shape of the cold response",
        f"{recall(hp_flag, 'label_hp'):.0%} of labeled HP homes")
    add("Central AC", "Central AC", "Central AC", "hot-weather slope", "any AC close to the survey")
    add("Solar PV", "Solar PV", "Solar PV", "midday zeros that follow the sun",
        f"{recall(pv_flag, 'label_pv'):.0%} recall")
    add("Pool pump", "Pool pump", "Pool pump", "summer timer band", "no labels or survey")
    add("Water heater", "Electric water heater", "Water heater (low conf.)", "short bursts (blurred hourly)", "well below the survey")
    add("Hot tub", "Hot tub", "Hot tub (low conf.)", "cycling (blurred hourly)", "no labels or survey")
    out["confidence"] = tab
    out["recall"] = {"ev": recall(ev_flag, "label_ev"), "eheat_hp": recall(eh_flag, "label_hp"),
                     "hp": recall(hp_flag, "label_hp"), "pv": recall(pv_flag, "label_pv")}

    # ---------------------------------------------------------------- data funnel
    out["funnel"] = {"brief_meters": C.BRIEF_CUSTOMERS if hasattr(C, "BRIEF_CUSTOMERS") else 35725,
                     "in_files": int(len(cust)), "modeled": int(cust.in_model.sum())}
    # ---- scaling: measured laptop runtimes (README) and the in-memory matrix size
    n = int(len(cust))
    out["scaling"] = {"homes": n, "pipeline_min": 12, "raw_min": 3,
                      "matrix_gb": n * C.N_HOURS * 4 / 1e9, "gb_per_million": 1e6 * C.N_HOURS * 4 / 1e9,
                      "laptop_h_per_million": (12 + 3) * 1e6 / n / 60}
    out["funnel"]["absent"] = out["funnel"]["brief_meters"] - out["funnel"]["in_files"]

    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(v) for v in o]
        if isinstance(o, (np.floating, float)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, np.integer):
            return int(o)
        return o

    (S / "deck_extras.json").write_text(json.dumps(clean(out), indent=1) + "\n")
    print(json.dumps(clean(out), indent=1))


if __name__ == "__main__":
    main()
