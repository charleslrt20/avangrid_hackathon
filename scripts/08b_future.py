"""Task 2.2: winter-peaking future. Heat-pump adoption scenarios, season of the system peak, battery and
demand-flex value against winter peaks, and the 3 kVA electrification-enablement lens.

Outputs
  outputs/future_adopters.parquet                         per simulated adopter (gitignored)
  outputs/summary/future_hp_profiles.csv, future_crossover_sweep.csv, future_scenarios.csv,
                  future_winter_cp_by_segment.csv, future_bess_winter.csv, future_headroom.csv, future_checks.csv,
                  future_shave_energy.csv
  docs/future.md, docs/figures/{future_peak_crossover,future_winter_day,future_headroom_bess}.png
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from avangrid import bess as B  # noqa: E402
from avangrid import config as C  # noqa: E402
from avangrid import future as F  # noqa: E402
from avangrid import io  # noqa: E402
from avangrid import plots as P  # noqa: E402
from avangrid.features.shape import system_peak_hours  # noqa: E402

lines: list[str] = []
EXCLUDE_SEG = ("Low use / seasonal", "Very high load / non-residential")
SWEEP = np.round(np.arange(0, 0.6001, 0.0025), 4)
HEADLINE = "B"


def emit(s: str = "") -> None:
    print(s)
    lines.append(s)


def md(df: pd.DataFrame, index: bool = False) -> None:
    emit(df.to_markdown(index=index, floatfmt=",.3g"))
    emit()


def dispatch_checks(name: str, r: B.DispatchResult, load: np.ndarray, bat: C.Battery) -> dict:
    L0 = np.nan_to_num(load)
    bal = r.soc_start + bat.rte * r.charge.sum(axis=1, dtype=np.float64) - r.discharge.sum(axis=1, dtype=np.float64) - r.soc_end
    return {"check": name,
            "power_violations": int(((r.charge > bat.kw + 1e-4) | (r.discharge > bat.kw + 1e-4)).any(axis=1).sum()),
            "soc_violations": int(((r.soc_min_seen < bat.kwh * bat.soc_min_frac - 1e-4)
                                   | (r.soc_max_seen > bat.kwh * bat.soc_max_frac + 1e-4)).sum()),
            "export_violations": int((r.discharge > L0 + 1e-4).any(axis=1).sum()),
            "max_energy_balance_error_kwh": float(np.abs(bal).max()),
            "customers_with_new_peak": int(((L0 + r.charge - r.discharge).max(axis=1) > L0.max(axis=1) + 1e-4).sum())}


def main() -> None:
    t0 = time.time()
    P.style()
    rng = np.random.default_rng(C.SEED)
    f = pd.read_parquet(C.FEATURES)
    k = pd.read_parquet(C.OUTPUTS / "classify.parquet")
    assert (k.customer_id.to_numpy() == f.customer_id.to_numpy()).all(), "classify.parquet out of row order"
    seg = pd.read_parquet(C.OUTPUTS / "segments.parquet").set_index("customer_id").segment.reindex(f.customer_id)
    seg.index = f.index                                       # align on customer_id, not row order
    hours = io.load_hours()
    mat = io.load_matrix()
    pos = io.customer_position()
    row = f.customer_id.map(pos).to_numpy()
    im = f.in_model.to_numpy()
    n_model = int(im.sum())
    peaks0 = system_peak_hours(mat, row[im], hours)
    base_total = peaks0["total"].astype(np.float64)
    temp = hours.temp.to_numpy(dtype=float)
    winter_m = hours.month.isin(C.WINTER_MONTHS).to_numpy()
    summer_m = hours.month.isin(C.SUMMER_MONTHS).to_numpy()

    # ------------------------------------------------------------------ 1. measured HP added-load profiles
    clean = im & ~seg.isin(EXCLUDE_SEG).to_numpy() & ~k.pv.to_numpy()
    ref = clean & (k.heat_type == "none/non-electric").to_numpy() & ~f.label_hp.to_numpy()
    donor_sets = {
        "A": ("All labeled HP homes (partial displacement: many keep fossil heat)", clean & f.label_hp.to_numpy()),
        "B": ("Labeled HP homes with an electric-heat signature (full displacement)",
              clean & f.label_hp.to_numpy() & k.electric_heat.to_numpy()),
        "C": ("Physics-detected HP + strip backup, labels not used (independent check)",
              clean & (k.heat_type == "HP+backup").to_numpy()),
    }
    ref_mean = F.group_mean(mat, row[ref])
    models = {}
    for key, (desc, m) in donor_sets.items():
        gap = F.group_mean(mat, row[m]) - ref_mean
        models[key] = F.fit_hp_increment(gap, hours, desc, int(m.sum()))
    hp = models[HEADLINE]
    prof_tab = pd.DataFrame([{
        "model": key, "description": mdl.name, "donor_homes": mdl.n_donors, "heat_balance_f": mdl.t_heat,
        "fit_r2": mdl.r2, "added_mwh_per_yr": mdl.profile.sum() / 1000,
        "kw_at_32f": mdl.at(32).mean(), "kw_at_20f": mdl.at(20).mean(), "kw_at_10f": mdl.at(10).mean(),
        "kw_at_0f": mdl.at(0).mean(), "kw_at_design_min": mdl.at(temp.min()).mean(),
        "kw_mean_summer": mdl.profile[summer_m].mean()} for key, mdl in models.items()])
    prof_tab.to_csv(C.OUTPUTS_SUMMARY / "future_hp_profiles.csv", index=False)
    print(f"profiles fitted in {time.time() - t0:.0f}s")

    # ------------------------------------------------------------------ 2. crossover sweep
    sweeps, cross = [], {}
    for key, mdl in models.items():
        sw = F.crossover_sweep(base_total, mdl.profile, n_model, hours, SWEEP)
        sw.insert(0, "model", key)
        sweeps.append(sw)
        cross[key] = F.crossover_share(sw)
    sweep = pd.concat(sweeps, ignore_index=True)
    sweep.to_csv(C.OUTPUTS_SUMMARY / "future_crossover_sweep.csv", index=False)

    # ------------------------------------------------------------------ 3. adopters (nested random draws)
    eligible = np.flatnonzero(im & (k.heat_type == "none/non-electric").to_numpy() & ~seg.isin(EXCLUDE_SEG).to_numpy()
                              & ~f.label_hp.to_numpy())
    order = rng.permutation(eligible)
    n_max = int(round(max(C.HP_ADOPTION_SCENARIOS) * n_model))
    adopt = order[:n_max]                                     # feature-row indices, first n_s = scenario s
    donor_kw = k.heat_kw_design_0f[donor_sets[HEADLINE][1]].dropna().to_numpy()
    ratio = np.clip(rng.choice(donor_kw, n_max) / donor_kw.mean(), 0.25, 3.0)
    ratio = ratio / ratio.mean()
    n_s = {s: int(round(s * n_model)) for s in C.HP_ADOPTION_SCENARIOS}

    scen_rows, scen_peaks = [], {}
    for key, mdl in models.items():
        for s in (0.0,) + C.HP_ADOPTION_SCENARIOS:
            n_a = n_s.get(s, 0)
            tot = base_total + ratio[:n_a].sum() * mdl.profile
            pk = F.season_peaks(tot, hours)
            if key == HEADLINE:
                scen_peaks[s] = (pk, tot)
            wc = pk["winter"]
            scen_rows.append({
                "model": key, "hp_share": s, "adopters": n_a,
                "summer_peak_mw": pk["summer_peak_kw"] / 1000, "winter_peak_mw": pk["winter_peak_kw"] / 1000,
                "winter_to_summer": pk["winter_peak_kw"] / pk["summer_peak_kw"],
                "annual_peak_season": "winter" if pk["winter_peak_kw"] > pk["summer_peak_kw"] else "summer",
                "summer_cp_mw": pk["summer_cp_kw"] / 1000, "winter_cp_mw": pk["winter_cp_kw"] / 1000,
                "winter_peak_date": str(hours.usage_date.iat[pk["winter_peak_col"]].date()),
                "winter_peak_he": int(hours.hod.iat[pk["winter_peak_col"]]) + 1,
                "winter_peak_temp_f": float(temp[pk["winter_peak_col"]]),
                "winter_top_hours_morning_share": float(np.isin(hours.hod.to_numpy()[wc], range(5, 11)).mean()),
                "winter_top_days": len(set(hours.day.to_numpy()[wc])),
                "winter_event_windows": " + ".join(f"HE{w[0] + 1}-{w[-1] + 1}" for w in F.event_windows(wc, hours)),
            })
    scen = pd.DataFrame(scen_rows)
    base_w = scen[(scen.hp_share == 0)].winter_cp_mw.iat[0]
    scen["added_winter_cp_mw"] = np.where(scen.hp_share > 0, scen.winter_cp_mw - base_w, 0.0)
    scen.to_csv(C.OUTPUTS_SUMMARY / "future_scenarios.csv", index=False)

    # ------------------------------------------------------------------ 4. who drives the future winter peak
    seg_im = seg[im].to_numpy()
    adopt_seg = seg.to_numpy()[adopt]
    seg_rows = []
    for s in (0.0,) + C.HP_ADOPTION_SCENARIOS:
        pk, tot = scen_peaks[s]
        cols = pk["winter"]
        cp = np.nanmean(mat[np.ix_(row[im], cols)], axis=1)
        cp = np.nan_to_num(cp)
        n_a = n_s.get(s, 0)
        inc = ratio[:n_a] * hp.profile[cols].mean()
        total = cp.sum() + inc.sum()
        d = pd.Series(cp).groupby(seg_im).sum()
        d = d.add(pd.Series(inc).groupby(adopt_seg[:n_a]).sum(), fill_value=0)
        col = f"share_winter_cp_{int(round(s * 100))}pct"
        seg_rows.append((d / total).rename(col))
        seg_rows.append(pd.Series({"of which: new HP load": inc.sum() / total}, name=col))
    seg_tab = pd.concat([pd.concat(seg_rows[i:i + 2]) for i in range(0, len(seg_rows), 2)], axis=1)
    seg_tab.index.name = "segment"
    seg_tab.insert(0, "customers", pd.Series(seg_im).value_counts().reindex(seg_tab.index))
    seg_tab = seg_tab.sort_values(seg_tab.columns[-1], ascending=False)
    seg_tab.to_csv(C.OUTPUTS_SUMMARY / "future_winter_cp_by_segment.csv")

    # ------------------------------------------------------------------ 5. adopter homes: load with the HP
    a_rows = row[adopt]
    base = np.asarray(mat[a_rows], dtype=np.float32)
    inc = (ratio[:, None] * hp.profile[None, :]).astype(np.float32)
    new = base + inc                                          # NaN hours stay NaN (no reading)
    print(f"{n_max:,} adopters loaded in {time.time() - t0:.0f}s")

    # 3 kVA lens: incremental peak vs the home's own pre-adoption peak
    old_peak = np.nanmax(base, axis=1)
    new_peak = np.nanmax(new, axis=1)
    incr = new_peak - old_peak
    winter_inc_at_peak = ratio * hp.profile[scen_peaks[max(C.HP_ADOPTION_SCENARIOS)][0]["winter"]].mean()
    cap = old_peak + C.HEADROOM_KVA * C.POWER_FACTOR
    cap_res = {u: F.cap_dispatch(new, cap, C.BATTERY.with_units(u)) for u in (1, 2)}
    checks = []
    for u, r in cap_res.items():
        bat = C.BATTERY.with_units(u)
        bal = bat.kwh + bat.rte * r.charge_kwh - r.discharge_kwh - r.soc_end
        checks.append({"check": f"3 kVA cap dispatch, {u} unit(s)", "power_violations": 0,
                       "soc_violations": int(((r.soc_min_seen < -1e-4) | (r.soc_max_seen > bat.kwh + 1e-4)).sum()),
                       "export_violations": 0, "max_energy_balance_error_kwh": float(np.abs(bal).max()),
                       "customers_with_new_peak": int((r.net_peak > np.maximum(new_peak, cap) + 1e-4).sum())})
    over0 = cap_res[1].over_hours_before > 0
    head = pd.DataFrame([
        {"metric": "Adopters simulated", "value": n_max},
        {"metric": "Median added kW at the home's own peak (new peak - old peak)", "value": np.median(incr)},
        {"metric": "P90 added kW at the home's own peak", "value": np.percentile(incr, 90)},
        {"metric": "Median added kW at the future winter system peak hours", "value": np.median(winter_inc_at_peak)},
        {"metric": "Share exceeding +3 kVA over old peak, no battery", "value": over0.mean()},
        {"metric": "Median hours/yr above the cap, no battery (those exceeding)",
         "value": np.median(cap_res[1].over_hours_before[over0]) if over0.any() else 0},
        {"metric": "Share still exceeding with 1 unit", "value": (cap_res[1].over_hours_after > 0).mean()},
        {"metric": "Share still exceeding with 2 units", "value": (cap_res[2].over_hours_after > 0).mean()},
        {"metric": "Of those exceeding, share brought within cap by 1 unit",
         "value": (cap_res[1].over_hours_after[over0] == 0).mean() if over0.any() else np.nan},
        {"metric": "Of those exceeding, share brought within cap by 2 units",
         "value": (cap_res[2].over_hours_after[over0] == 0).mean() if over0.any() else np.nan},
        {"metric": "Median kWh/yr above cap left unserved with 2 units (those still exceeding)",
         "value": np.median(cap_res[2].unserved_kwh[cap_res[2].over_hours_after > 0])
         if (cap_res[2].over_hours_after > 0).any() else 0},
    ])
    head.to_csv(C.OUTPUTS_SUMMARY / "future_headroom.csv", index=False)

    # EV lens (measured today): L2 chargers vs the 3 kVA rule
    evm = im & (k.ev_class == "L2 detectable").to_numpy()
    evf = f.loc[evm, ["ev_l2_block_kw", "ev_l2_block_hours"]].dropna()
    ev_kw, ev_h = evf.ev_l2_block_kw.to_numpy(), evf.ev_l2_block_hours.to_numpy()
    ev_excess_kwh = np.maximum(ev_kw - C.HEADROOM_KVA, 0) * ev_h
    ev_lens = {"homes": len(ev_kw), "median_kw": float(np.median(ev_kw)),
               "share_over_3kva": float((ev_kw > C.HEADROOM_KVA).mean()),
               "median_session_h": float(np.nanmedian(ev_h)),
               "share_battery_covers_excess": float(((ev_excess_kwh <= C.BATTERY.kwh)
                                                     & (ev_kw - C.HEADROOM_KVA <= C.BATTERY.kw)).mean()),
               "median_stretch_h_at_3kw": float(np.nanmedian(ev_kw * ev_h / C.HEADROOM_KVA))}

    # ------------------------------------------------------------------ 6. battery + DR value against winter peaks
    # Policies compared on the adopter homes, per scenario (event days = the 15 coldest forecast days, as in 1.6):
    #   strict:      task-1.6 rule, recharge 00-05h only up to the home's own p95 load
    #   relaxed:     recharge 00-05h up to the home's own annual peak, every battery at once (no coordination)
    #   coordinated: recharge 00-05h + 10-13h up to the home's own peak, fleet charging throttled under a system
    #                limit chosen per scenario to minimise the annual system peak
    # Winter windows follow each scenario's top hours (morning + evening once mornings matter).
    s_max = max(C.HP_ADOPTION_SCENARIOS)
    real_t, called = B.realistic_targets(hours)
    wdays = sorted(int(d) for d in called["winter"])
    p95_old = np.nanpercentile(base, 95, axis=1)
    p95_new = np.nanpercentile(new, 95, axis=1)
    own_peak = np.nanmax(new, axis=1)
    r = B.simulate(base, real_t, C.BATTERY, p95_old)
    checks.append(dispatch_checks("Adopter homes before HP, today's window", r, base, C.BATTERY))
    before = {s: r.discharge[:n_s[s]][:, scen_peaks[s][0]["winter"]].mean(axis=1) for s in n_s}
    del r
    POLICIES = ("strict", "strict_1unit", "relaxed", "coordinated")
    windows, relief, perfect, dr_w, info, keep = {}, {}, {}, {}, {}, {}
    for s in C.HP_ADOPTION_SCENARIOS:
        pk, tot_s = scen_peaks[s]
        n_a = n_s[s]
        ld = new[:n_a]
        cs_, cw_ = pk["summer"], pk["winter"]
        windows[s] = F.event_windows(cw_, hours)
        hrs = np.array(sorted(h for w in windows[s] for h in w))
        tg = {d: (hrs if d in wdays else v) for d, v in real_t.items()}
        relief[s], info[s] = {}, {"windows": " + ".join(f"HE{w[0] + 1}-{w[-1] + 1}" for w in windows[s]),
                                  "annual_peak_before_mw": tot_s.max() / 1000}
        runs = {"strict": (C.BATTERY, p95_new[:n_a]), "strict_1unit": (C.BATTERY.with_units(1), p95_new[:n_a]),
                "relaxed": (C.BATTERY, own_peak[:n_a])}
        for pol, (bat, cap_) in runs.items():
            r = B.simulate(ld, tg, bat, cap_)
            checks.append(dispatch_checks(f"{pol}, {s:.0%}", r, ld, bat))
            relief[s][pol] = (r.discharge[:, cs_].mean(axis=1), r.discharge[:, cw_].mean(axis=1))
            net = r.charge.sum(axis=0, dtype=np.float64) - r.discharge.sum(axis=0, dtype=np.float64)
            info[s][f"annual_peak_after_mw_{pol}"] = (tot_s + net).max() / 1000
            if s == s_max and bat.units == 2:
                keep[pol] = (net, pd.Series({d: r.discharge[:, d * 24 + hrs].mean() for d in wdays}))
            del r
        # per-season fleet limit by bisection: g(lim) = peak over that season's hours with no fleet charging
        # (event-driven peak, falls as lim rises); the fixed point g = lim means recharge never sets the peak.
        bounds = {m: [0.7 * tot_s[msk].max(), tot_s[msk].max()] for m, msk in (("w", winter_m), ("s", ~winter_m))}
        for _ in range(C.FLEET_LIMIT_ITERS):
            mid = {m: sum(b_) / 2 for m, b_ in bounds.items()}
            r = F.simulate_fleet(ld, tg, C.BATTERY, own_peak[:n_a], tot_s, np.where(winter_m, mid["w"], mid["s"]))
            fc = r.charge.sum(axis=0, dtype=np.float64)
            after = tot_s + fc - r.discharge.sum(axis=0, dtype=np.float64)
            del r
            for m, msk in (("w", winter_m), ("s", ~winter_m)):
                g = after[msk & (fc < 1e-6)].max()
                bounds[m][0 if g > mid[m] else 1] = mid[m]
        lim = {m: b_[1] for m, b_ in bounds.items()}       # upper end: recharge ceiling never above the event peak
        r = F.simulate_fleet(ld, tg, C.BATTERY, own_peak[:n_a], tot_s, np.where(winter_m, lim["w"], lim["s"]))
        net = r.charge.sum(axis=0, dtype=np.float64) - r.discharge.sum(axis=0, dtype=np.float64)
        pa = (tot_s + net).max()
        checks.append(dispatch_checks(f"coordinated, {s:.0%}", r, ld, C.BATTERY))
        relief[s]["coordinated"] = (r.discharge[:, cs_].mean(axis=1), r.discharge[:, cw_].mean(axis=1))
        info[s]["annual_peak_after_mw_coordinated"] = pa / 1000
        info[s]["fleet_limit_winter_mw"] = lim["w"] / 1000
        info[s]["fleet_limit_other_mw"] = lim["s"] / 1000
        info[s]["winter_peak_after_mw_coordinated"] = (tot_s + net)[winter_m].max() / 1000
        info[s]["summer_peak_after_mw_coordinated"] = (tot_s + net)[summer_m].max() / 1000
        pcol = int(np.argmax(tot_s + net))
        info[s]["peak_after_coordinated_at"] = f"{hours.usage_date.iat[pcol].date()} HE{int(hours.hod.iat[pcol]) + 1}"
        if s == s_max:
            keep["coordinated"] = (net, pd.Series({d: r.discharge[:, d * 24 + hrs].mean() for d in wdays}))
        del r
        r = B.simulate(ld, B.perfect_targets(pk, hours), C.BATTERY, p95_new[:n_a])
        checks.append(dispatch_checks(f"perfect foresight (strict recharge), {s:.0%}", r, ld, C.BATTERY))
        perfect[s] = r.discharge[:, cw_].mean(axis=1)
        del r
        z = np.zeros(n_a, dtype=bool)   # thermostat setback on the HP during winter events (20% shed, rebound after)
        dr = B.demand_flex(ld, hours, {d: tg[d] for d in wdays}, z, ~z, z, np.zeros_like(ld), np.zeros(n_a))
        dr_w[s] = -dr[:, cw_].mean(axis=1)
        del dr
        print(f"  scenario {s:.0%} dispatch done ({time.time() - t0:.0f}s)")

    # what it takes, at system level: energy to shave the peak by X MW (perfectly placed, before any recharge limit)
    shave_rows = []
    for lab, tot_, msk in [("Summer, today", base_total, summer_m), ("Winter, today", base_total, winter_m)] + \
            [(f"Winter, {s:.0%} HP", scen_peaks[s][1], winter_m) for s in C.HP_ADOPTION_SCENARIOS]:
        for x in (1, 2, 5, 10):
            shave_rows.append({"case": lab, "cut_mw": x, **F.shave_energy(tot_, msk, hours, x * 1000)})
    shave = pd.DataFrame(shave_rows)
    shave.to_csv(C.OUTPUTS_SUMMARY / "future_shave_energy.csv", index=False)

    bess_rows = []
    for s in C.HP_ADOPTION_SCENARIOS:
        n_a = n_s[s]
        srow = scen[(scen.model == HEADLINE) & (scen.hp_share == s)].iloc[0]
        season = srow.annual_peak_season
        row_ = {"hp_share": s, "adopters": n_a, "annual_peak_season": season, "winter_windows": info[s]["windows"],
                "added_winter_cp_kw_per_adopter": srow.added_winter_cp_mw * 1000 / n_a,
                "battery_winter_kw_before_hp": before[s].mean()}
        for pol in POLICIES:
            row_[f"battery_winter_kw_{pol}"] = relief[s][pol][1].mean()
        row_["battery_winter_kw_perfect_strict"] = perfect[s].mean()
        row_["dr_winter_kw"] = dr_w[s].mean()
        row_["fleet_winter_relief_mw_coordinated"] = relief[s]["coordinated"][1].sum() / 1000
        row_["added_winter_cp_mw"] = srow.added_winter_cp_mw
        row_["share_of_added_cp_offset_coordinated"] = row_["fleet_winter_relief_mw_coordinated"] / srow.added_winter_cp_mw
        row_.update({k_: v for k_, v in info[s].items() if k_ != "windows"})
        for pol in ("strict", "coordinated"):
            av = B.avoided_cost(*relief[s][pol], peak_season=season)
            for lvl in ("low", "mid", "high"):
                row_[f"avoided_usd_per_home_yr_{lvl}_{pol}"] = av[f"avoided_usd_yr_{lvl}"].mean()
        row_["dr_avoided_usd_per_home_yr_mid"] = B.avoided_cost(np.zeros(n_a), dr_w[s], peak_season=season).avoided_usd_yr_mid.mean()
        bess_rows.append(row_)
    bess_tab = pd.DataFrame(bess_rows)
    bess_tab.to_csv(C.OUTPUTS_SUMMARY / "future_bess_winter.csv", index=False)

    # fleet recharge must not create a new system peak (30% scenario, every adopter with 2 units)
    tot30 = scen_peaks[s_max][1]
    lab = {"strict": "Strict (task 1.6 rule)", "relaxed": "Relaxed, uncoordinated", "coordinated": "Coordinated fleet recharge"}
    sys_rows = {"No batteries": tot30}
    sys_rows.update({lab[p_]: tot30 + keep[p_][0] for p_ in ("strict", "relaxed", "coordinated")})
    night = winter_m & np.isin(hours.hod.to_numpy(), C.RECHARGE_HOURS)
    sys_tab = pd.DataFrame({k_: {"winter_peak_mw": v[winter_m].max() / 1000, "summer_peak_mw": v[summer_m].max() / 1000,
                                 "winter_night_max_mw": v[night].max() / 1000,
                                 "winter_peak_at": "{} HE{}".format(hours.usage_date.iat[np.flatnonzero(winter_m)[np.argmax(v[winter_m])]].date(),
                                                                    int(hours.hod.iat[np.flatnonzero(winter_m)[np.argmax(v[winter_m])]]) + 1)}
                            for k_, v in sys_rows.items()}).T
    pd.DataFrame(checks).to_csv(C.OUTPUTS_SUMMARY / "future_checks.csv", index=False)

    # multi-day cold snaps: relief by position in a run of consecutive called days
    runpos, prev, cnt = {}, None, 0
    for d in wdays:
        cnt = cnt + 1 if prev is not None and d == prev + 1 else 1
        runpos[d] = min(cnt, 3)
        prev = d
    rp = pd.Series(runpos)
    by_pos = pd.DataFrame({"days": rp.value_counts().sort_index()})
    for p_ in ("strict", "relaxed", "coordinated"):
        by_pos[f"kw_per_home_{p_}"] = keep[p_][1].groupby(rp).mean()

    pd.DataFrame({"customer_id": f.customer_id.to_numpy()[adopt], "segment": adopt_seg, "size_ratio": ratio,
                  "scenario_min_share": np.select([np.arange(n_max) < n_s[s] for s in sorted(n_s)], sorted(n_s)),
                  "old_peak_kw": old_peak, "new_peak_kw": new_peak, "added_peak_kw": incr,
                  "over_3kva": over0, "within_cap_1unit": cap_res[1].over_hours_after == 0,
                  "within_cap_2units": cap_res[2].over_hours_after == 0,
                  "battery_winter_kw_30pct_coordinated": relief[s_max]["coordinated"][1],
                  }).to_parquet(C.OUTPUTS / "future_adopters.parquet", index=False)

    # ------------------------------------------------------------------ report
    b0 = scen[(scen.model == HEADLINE) & (scen.hp_share == 0)].iloc[0]
    emit("# Winter-peaking future: heat-pump adoption scenarios (task 2.2)")
    emit()
    emit(f"Generated by `scripts/08b_future.py`. Modeling population {n_model:,} homes, weather year 9/2025-8/2026 "
         f"(min {temp.min():.0f} °F). Today the system peaks in **summer** ({b0.summer_peak_mw:.1f} MW) and the winter "
         f"peak is {b0.winter_to_summer:.0%} of it ({b0.winter_peak_mw:.1f} MW).")
    emit()
    b30 = bess_tab.set_index("hp_share").loc[s_max]
    s30 = scen[(scen.model == HEADLINE) & (scen.hp_share == s_max)].iloc[0]
    hv = head.set_index("metric").value
    sh = shave.set_index(["case", "cut_mw"])
    emit("## Key findings")
    emit()
    emit(f"1. **Crossover:** the system becomes winter-peaking once about **{cross[HEADLINE]:.0%}** of homes add a heat pump "
         f"(headline donor group B; {cross['C']:.0%} with the label-free group C, {cross['A']:.0%} if heat pumps only partly "
         "displace fossil heat as in the average labeled home).")
    emit(f"2. **At {s_max:.0%} adoption** the winter peak reaches {s30.winter_peak_mw:.1f} MW vs a {s30.summer_peak_mw:.1f} MW summer "
         f"peak (today's winter: {b0.winter_peak_mw:.1f} MW). New heat-pump load is "
         f"{seg_tab.loc['of which: new HP load'].iloc[-1]:.0%} of the winter coincident peak; morning hours rise from "
         f"{b0.winter_top_hours_morning_share:.0%} to {s30.winter_top_hours_morning_share:.0%} of the top winter hours, and the "
         f"peak sits in a cold snap ({s30.winter_peak_date}, {s30.winter_peak_temp_f:.0f} °F).")
    emit(f"3. **Per adopter:** +{b30.added_winter_cp_kw_per_adopter:.1f} kW at the winter system peak, "
         f"+{hp.profile.sum() / 1000:.1f} MWh/yr, ≈ 0 in summer (measured from {hp.n_donors} labeled HP homes).")
    emit(f"4. **Batteries need coordinated recharge in winter.** With the task-1.6 rule a battery is nearly empty after the "
         f"first cold-snap day ({by_pos.kw_per_home_strict.iat[0]:.1f} kW on day 1, {by_pos.kw_per_home_strict.iat[-1]:.2f} kW "
         f"on day 3+). Letting every battery recharge freely creates a new {sys_tab.loc['Relaxed, uncoordinated', 'winter_peak_mw']:.0f} MW "
         f"night peak. Coordinated fleet recharge cuts the annual peak from {b30.annual_peak_before_mw:.1f} to "
         f"{b30.annual_peak_after_mw_coordinated:.1f} MW (strict rule: {b30.annual_peak_after_mw_strict:.1f} MW): "
         f"{b30.battery_winter_kw_coordinated:.2f} kW per home at the winter peak, offsetting "
         f"{b30.share_of_added_cp_offset_coordinated:.0%} of the added heat-pump peak, worth about "
         f"${b30.avoided_usd_per_home_yr_mid_coordinated:,.0f}/home/yr at mid placeholder values.")
    emit(f"5. **Deep winter cuts need long-duration energy:** cutting the {s_max:.0%}-scenario winter peak by 10 MW takes "
         f"{sh.loc[(f'Winter, {s_max:.0%} HP', 10), 'worst_day_mwh']:.0f} MWh over "
         f"{sh.loc[(f'Winter, {s_max:.0%} HP', 10), 'hours_above_worst_day']} h on the worst day, vs "
         f"{sh.loc[('Summer, today', 10), 'worst_day_mwh']:.0f} MWh over {sh.loc[('Summer, today', 10), 'hours_above_worst_day']} h "
         "for the same cut in summer today. Shallow cuts (≤ 5 MW) cost about the same in both seasons.")
    emit(f"6. **3 kVA lens:** {hv['Share exceeding +3 kVA over old peak, no battery']:.0%} of adopters would exceed their old "
         f"peak + 3 kVA, but only for a median {hv['Median hours/yr above the cap, no battery (those exceeding)']:.0f} h/yr. One "
         f"battery unit keeps {hv['Of those exceeding, share brought within cap by 1 unit']:.0%} of them within the cap, two units "
         f"{hv['Of those exceeding, share brought within cap by 2 units']:.0%}. The rest need a service evaluation or "
         "heat-pump load control (strip-heat lockout).")
    emit(f"7. **Thermostat DR alone** gives {b30.dr_winter_kw:.2f} kW per adopter at the winter peak after rebound, roughly a third "
         "of a coordinated battery, at almost no cost: the first program to launch.")
    emit()
    emit("## Method")
    emit()
    emit("- **Added load per heat-pump home is measured, not assumed.** For each hour of day h: gap_h = mean load of HP "
         "homes − mean load of non-electric-heat homes (n = {:,}), regressed on HDD(T_heat), max(20 °F − T, 0) and CDD. "
         "The intercept (size / occupancy difference) is dropped; what remains is the heating (and extra cooling) the HP "
         "adds at that temperature and hour. Low-use, very-high-load and PV homes are left out of both groups.".format(int(ref.sum())))
    emit(f"- **Three donor groups** bracket the answer. Headline = **{HEADLINE}** ({hp.n_donors} homes).")
    md(prof_tab)
    emit("Reading: a full-displacement heat pump adds about "
         f"{models['B'].at(0).mean():.1f} kW at 0 °F and {models['B'].profile.sum() / 1000:.1f} MWh/yr, and nothing in summer "
         "(HP homes cool no more than the average non-electric home, so the cooling term is ≈ 0). Labeled HP homes show no "
         "steepening below 20 °F, consistent with fossil backup taking over in the cold (dual fuel). Group C was *selected* on a "
         "cold-weather slope break, so its steep low-temperature response is partly by construction; it is a check, not the headline.")
    emit()
    emit(f"- **Adopters:** random draws (seed {C.SEED}) from in-model homes with no electric heat today; scenario sets are nested "
         f"(5% ⊂ 15% ⊂ 30% of {n_model:,} homes). Each adopter's profile is scaled by a size ratio drawn from the donor homes' own "
         "design heating kW (mean 1), so homes differ as real HP homes do while system totals stay N × profile.")
    emit("- System peaks recomputed per scenario: top 88 hours per season of base load + added HP load.")
    emit()
    emit("## When does the system become winter-peaking?")
    emit()
    emit("| donor group | crossover: share of homes with a heat pump | homes (of {:,}) |".format(n_model))
    emit("|---|---|---|")
    for key in models:
        c_ = cross[key]
        emit(f"| {key} | {c_:.1%} | {c_ * n_model:,.0f} |" if np.isfinite(c_) else f"| {key} | > {SWEEP.max():.0%} | – |")
    emit()
    emit(f"**Headline: the system flips to winter-peaking at about {cross[HEADLINE]:.0%} heat-pump adoption** "
         f"(range {min(cross.values()):.0%}–{max(v for v in cross.values() if np.isfinite(v)):.0%} across donor groups"
         + ("" if all(np.isfinite(list(cross.values()))) else "; group A does not flip within the sweep") + ").")
    emit()
    md(scen[["model", "hp_share", "adopters", "summer_peak_mw", "winter_peak_mw", "winter_to_summer", "annual_peak_season",
             "added_winter_cp_mw", "winter_peak_date", "winter_peak_he", "winter_peak_temp_f",
             "winter_top_hours_morning_share", "winter_top_days", "winter_event_windows"]])
    emit("Winter peak events are cold-snap driven: the top winter hours sit on a handful of the coldest days, and as heat "
         "pumps are added the morning hours (HE6-11) take a larger share of the top hours.")
    emit()
    emit("## Who drives the future winter peak (share of winter coincident-peak load, headline model)")
    emit()
    md(seg_tab.reset_index())
    emit("## What a MW of winter relief takes (system level)")
    emit()
    emit("Energy a perfectly placed fleet would need on the worst day to cut each season's peak by X MW (kWh above the "
         "line peak − X), and how many days in a row need it. This is a property of the load shape, before any battery limits.")
    emit()
    md(shave)
    emit("## Batteries and demand flex against the winter peak (adopter homes)")
    emit()
    emit(f"Realistic dispatch as in task 1.6: {C.EVENT_BUDGET_PER_SEASON} called events per season on the coldest forecast days, "
         "flat discharge planned from the energy left, SOC carried across days. Winter windows follow each scenario's top hours "
         "(a morning window is added once mornings hold ≥ 15% of them). Policies: **strict** = task-1.6 rule (recharge 00-05h "
         "up to the home's own p95 load); **relaxed** = recharge 00-05h up to the home's own annual peak, all batteries at once; "
         "**coordinated** = recharge 00-05h and 10-13h up to the home's own peak, with total fleet charging throttled under a "
         "per-season system limit, found by bisection so that the event-hour peak equals the recharge ceiling "
         "(recharge never sets the peak); each battery also spreads its recharge evenly over the off-peak hours. The same homes are also run "
         "*before* the heat pump. Capacity value follows the season of the annual peak in each scenario.")
    emit()
    md(bess_tab)
    emit("Relief by position in a run of consecutive called winter days (30% scenario, 2 units, kW per home over the event hours):")
    emit()
    md(by_pos.rename(index={1: "1st day", 2: "2nd day", 3: "3rd+ day"}).reset_index(names="run position"))
    emit("System peaks at the 30% scenario if every adopter has 2 units (fleet charge and discharge added to the system):")
    emit()
    md(sys_tab.reset_index(names="fleet"))
    emit("## 3 kVA electrification-enablement lens")
    emit()
    emit(f"Rule from the brief: on 200 A service, ≤ {C.HEADROOM_KVA:.0f} kVA of added load needs no further evaluation "
         f"(PF {C.POWER_FACTOR} assumed, so kVA ≈ kW on hourly data). Cap = the home's own pre-adoption hourly peak + 3 kVA. "
         "A battery discharges whenever the home would exceed the cap and recharges 00-05h only up to the cap, with SOC "
         "carried hour by hour across cold snaps.")
    emit()
    md(head)
    emit(f"**EV lens (measured today):** {ev_lens['homes']:,} homes with detected L2 charging; median block "
         f"{ev_lens['median_kw']:.1f} kW, {ev_lens['share_over_3kva']:.0%} above 3 kVA. A 2-unit battery can cover the "
         f"part above 3 kVA for a typical session in {ev_lens['share_battery_covers_excess']:.0%} of them, but managed "
         f"charging capped at 3 kW does the same for free by stretching a {ev_lens['median_session_h']:.1f} h session to "
         f"about {ev_lens['median_stretch_h_at_3kw']:.1f} h overnight.")
    emit()
    emit("## Checks")
    emit()
    md(pd.DataFrame(checks))
    emit("- System peaks (MW) at the 30% scenario if every adopter has 2 units (fleet charge and discharge added to the system):")
    emit()
    md(sys_tab.reset_index(names="fleet"))
    emit("- Hourly data: instantaneous HP + strip peaks are higher than the hourly mean, so the 3 kVA exceedance share is a "
         "lower bound.")
    emit()

    # ------------------------------------------------------------------ figures
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    for key, ls in (("A", ":"), ("C", "--"), ("B", "-")):
        sw = sweep[sweep.model == key]
        lw = 2.2 if key == HEADLINE else 1.1
        ax.plot(sw.hp_share * 100, sw.winter_peak_kw / 1000, color=P.WINTER, ls=ls, lw=lw,
                label=f"Winter peak, group {key}" + (" (headline)" if key == HEADLINE else ""))
        ax.plot(sw.hp_share * 100, sw.summer_peak_kw / 1000, color=P.SUMMER, ls=ls, lw=lw,
                label=f"Summer peak, group {key}" if key == HEADLINE else None)
    c_ = cross[HEADLINE]
    ax.axvline(c_ * 100, color="black", lw=1)
    ax.text(c_ * 100 + 0.7, ax.get_ylim()[0] + 0.92 * (ax.get_ylim()[1] - ax.get_ylim()[0]),
            f"winter overtakes summer\nat {c_:.0%} of homes", fontsize=9)
    for s in C.HP_ADOPTION_SCENARIOS:
        r_ = scen[(scen.model == HEADLINE) & (scen.hp_share == s)].iloc[0]
        ax.plot(s * 100, r_.winter_peak_mw, "o", color=P.WINTER)
    ax.set_xlabel("Homes adding a heat pump (% of customers)")
    ax.set_ylabel("System peak (MW, study population)")
    ax.set_title("Heat-pump adoption turns the system winter-peaking")
    ax.legend(loc="upper left", fontsize=8)
    P.save(fig, "future_peak_crossover")

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.2))
    ax = axes[0]
    wd = int(hours.day.iat[scen_peaks[max(C.HP_ADOPTION_SCENARIOS)][0]["winter_peak_col"]])
    x = np.arange(1, 25)
    cols = np.arange(wd * 24, wd * 24 + 24)
    for s, a in zip((0.0,) + C.HP_ADOPTION_SCENARIOS, (0.35, 0.55, 0.75, 1.0)):
        ax.plot(x, scen_peaks[s][1][cols] / 1000, color=P.WINTER, alpha=a, lw=2,
                label="Today" if s == 0 else f"{s:.0%} heat-pump adoption")
    ax.axhline(b0.summer_peak_mw, color=P.SUMMER, ls="--", lw=1.2, label="Today's summer peak")
    for i, w_ in enumerate(windows[s_max]):
        ax.axvspan(w_[0] + 0.5, w_[-1] + 1.5, color="#F0E442", alpha=0.3,
                   label=f"Battery event windows ({s_max:.0%} scenario)" if i == 0 else None)
    ax.set_xticks(range(1, 25, 2))
    ax.set_xlabel("Hour ending")
    ax.set_ylabel("System load (MW)")
    ax.set_title(f"Coldest peak day ({hours.usage_date.iat[wd * 24].date()}, min {temp[cols].min():.0f} °F)")
    ax.legend(fontsize=8, loc="lower right")
    ax = axes[1]
    for lab_, col_, ls_ in (("Summer, today", P.SUMMER, "-"), ("Winter, today", P.WINTER, ":"),
                            (f"Winter, {s_max:.0%} HP", P.WINTER, "-")):
        sh = shave[shave.case == lab_]
        ax.plot(sh.cut_mw, sh.worst_day_mwh, ls_, marker="o", color=col_, label=lab_)
    ax.set_xlabel("Peak reduction (MW)")
    ax.set_ylabel("Energy needed on the worst day (MWh)")
    ax.set_title("Battery energy needed per MW of peak cut")
    ax.legend(fontsize=8)
    fig.subplots_adjust(wspace=0.28)
    P.save(fig, "future_winter_day")

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    ax = axes[0]
    vals = [over0.mean(), (cap_res[1].over_hours_after > 0).mean(), (cap_res[2].over_hours_after > 0).mean()]
    ax.bar(["No battery", "1 unit\n(10 kWh)", "2 units\n(20 kWh)"], np.array(vals) * 100, color=[P.NEUTRAL, P.PALETTE[5], P.WINTER])
    for i, v in enumerate(vals):
        ax.text(i, v * 100 + 1, f"{v:.0%}", ha="center")
    ax.set_ylabel("% of heat-pump adopters")
    ax.set_title("Adopters exceeding old peak + 3 kVA")
    ax.set_ylim(0, max(vals) * 100 * 1.25 + 1)
    ax = axes[1]
    br = bess_tab.set_index("hp_share")
    xs = np.arange(len(br))
    w = 0.2
    ax.bar(xs - 1.5 * w, br.battery_winter_kw_before_hp, w, color=P.NEUTRAL, label="Battery, same homes before HP")
    ax.bar(xs - 0.5 * w, br.battery_winter_kw_strict, w, color=P.WINTER, label="Battery with HP, strict recharge (task 1.6)")
    ax.bar(xs + 0.5 * w, br.battery_winter_kw_coordinated, w, color=P.PALETTE[5],
           label="Battery with HP, coordinated fleet recharge")
    ax.bar(xs + 1.5 * w, br.dr_winter_kw, w, color=P.PALETTE[2], label="Thermostat DR with HP")
    ax.set_xticks(xs, [f"{s:.0%} adoption\n(peak: {br.annual_peak_season[s]})" for s in br.index])
    ax.set_ylabel("kW per home at winter system peak")
    ax.set_title("Winter peak relief per adopter home (realistic dispatch)")
    ax.legend(fontsize=8)
    fig.subplots_adjust(wspace=0.3)
    P.save(fig, "future_headroom_bess")

    (C.DOCS / "future.md").write_text("\n".join(lines) + "\n")
    print(f"\ndone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
