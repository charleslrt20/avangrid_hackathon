"""Task 1.6: BTM battery + demand-flex candidates, SOC-constrained dispatch, sizing, avoided cost, tiers.

Outputs
  outputs/bess_results.parquet                  per candidate (gitignored)
  outputs/summary/bess_tiers.csv, bess_variants.csv, bess_sizing.csv, bess_checks.csv
  docs/bess.md, docs/figures/{bess_dispatch_example,bess_tiers,bess_variants}.png
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
from avangrid import io  # noqa: E402
from avangrid import plots as P  # noqa: E402
from avangrid.features.events import own_baseline  # noqa: E402
from avangrid.features.shape import system_peak_hours  # noqa: E402

lines: list[str] = []


def emit(s: str = "") -> None:
    print(s)
    lines.append(s)


def md(df: pd.DataFrame, index: bool = False) -> None:
    emit(df.to_markdown(index=index, floatfmt=",.3g"))
    emit()


def main() -> None:
    t0 = time.time()
    P.style()
    f_all = pd.read_parquet(C.FEATURES)
    k_all = pd.read_parquet(C.OUTPUTS / "classify.parquet")
    s_all = pd.read_parquet(C.OUTPUTS / "segments.parquet").set_index("customer_id")
    hours = io.load_hours()
    mat = io.load_matrix()
    pos = io.customer_position()
    im = f_all.in_model.to_numpy()
    peaks = system_peak_hours(mat, np.flatnonzero(im), hours)

    f = f_all[im].reset_index(drop=True)
    k = k_all[im].reset_index(drop=True)
    seg = s_all.loc[f.customer_id, "segment"].to_numpy()
    cand = ((f[["cp_kw_summer", "cp_kw_winter"]].max(axis=1) >= C.CANDIDATE_MIN_CP_KW)
            & (seg != "Low use / seasonal")).to_numpy()
    fc, kc = f[cand].reset_index(drop=True), k[cand].reset_index(drop=True)
    rows = fc.customer_id.map(pos).to_numpy()
    load = np.asarray(mat[np.sort(rows)], dtype=np.float32)
    order = np.argsort(np.argsort(rows))             # mat[sorted] -> candidate order
    load = load[order]
    n = len(fc)
    print(f"{n:,} candidates; loaded in {time.time() - t0:.0f}s")

    cap = np.maximum(fc.p95_kw.to_numpy(), 0)
    cs, cw = peaks["summer"], peaks["winter"]
    perfect_t = B.perfect_targets(peaks, hours)
    real_t, called = B.realistic_targets(hours)

    variants = {
        "Perfect foresight, 2 units": (perfect_t, C.BATTERY),
        "Realistic, 2 units": (real_t, C.BATTERY),
        "Realistic, 1 unit": (real_t, C.BATTERY.with_units(1)),
        "Realistic, 2 units, 20% backup reserve": (real_t, C.Battery(soc_min_frac=0.2)),
    }
    res, relief = {}, {}
    trace_rows = np.arange(n)  # small: we keep only a few after choosing the example
    checks = []
    for name, (tg, bat) in variants.items():
        r = B.simulate(load, tg, bat, cap, trace_rows=None)
        res[name] = r
        relief[name] = (B.cp_relief(r, load, cs), B.cp_relief(r, load, cw))
        L0 = np.nan_to_num(load)
        net = L0 + r.charge - r.discharge
        balance = r.soc_start + bat.rte * r.charge.sum(axis=1, dtype=np.float64) - r.discharge.sum(axis=1, dtype=np.float64) - r.soc_end
        checks.append({
            "variant": name,
            "power_violations": int(((r.charge > bat.kw + 1e-4) | (r.discharge > bat.kw + 1e-4)).any(axis=1).sum()),
            "soc_violations": int(((r.soc_min_seen < bat.kwh * bat.soc_min_frac - 1e-4)
                                   | (r.soc_max_seen > bat.kwh * bat.soc_max_frac + 1e-4)).sum()),
            "export_violations": int((r.discharge > L0 + 1e-4).any(axis=1).sum()),
            "max_energy_balance_error_kwh": float(np.abs(balance).max()),
            "customers_with_new_peak": int((net.max(axis=1) > L0.max(axis=1) + 1e-4).sum()),
            "total_discharge_mwh": float(r.discharge.sum() / 1000),
            "total_charge_mwh": float(r.charge.sum() / 1000),
        })
    checks = pd.DataFrame(checks)
    checks.to_csv(C.OUTPUTS_SUMMARY / "bess_checks.csv", index=False)

    # firm (sustained) kW: P10 across called event days of the flat level actually delivered
    rr = res["Realistic, 2 units"]
    firm = {}
    for s in ("summer", "winter"):
        days = called[s]
        per_day = np.stack([rr.discharge[:, d * 24 + np.array(C.WINDOW_HOURS[s])].mean(axis=1) for d in days], axis=1)
        firm[s] = np.percentile(per_day, 10, axis=1)

    # sizing need on the true system event days, over the realistic window
    need = {}
    for s, months in (("summer", C.SUMMER_MONTHS), ("winter", C.WINTER_MONTHS)):
        ev_days = np.array(sorted({int(hours.day.iat[c]) for c in peaks[s]}))
        kwh, kw = B.event_need(load, hours, ev_days, C.WINDOW_HOURS[s], months)
        need[s] = (np.percentile(kwh, 90, axis=1), np.percentile(kw, 90, axis=1),
                   ((kwh <= C.BATTERY.kwh) & (kw <= C.BATTERY.kw)).mean(axis=1))
    need_kwh = np.maximum(need["summer"][0], need["winter"][0])
    need_kw = np.maximum(need["summer"][1], need["winter"][1])
    size = B.size_recommendation(need_kwh, need_kw)
    coverage = (need["summer"][2] + need["winter"][2]) / 2

    # demand flexibility (no battery), realistic events
    days = load.reshape(n, 365, 24)
    base20 = own_baseline(days, hours.month.to_numpy()[::24]).reshape(n, -1)
    dr = B.demand_flex(load, hours, real_t, (kc.ac_type == "central").to_numpy(), kc.electric_heat.to_numpy(),
                       kc.ev_physics.to_numpy(), base20, kc.ev_charger_kw.fillna(7.0).to_numpy())
    del base20
    dr_s, dr_w = -dr[:, cs].mean(axis=1), -dr[:, cw].mean(axis=1)
    L0 = np.nan_to_num(load)
    dr_new_peak = int(((L0 + dr).max(axis=1) > L0.max(axis=1) + 1e-4).sum())

    rs, rw = relief["Realistic, 2 units"]
    ps, pw = relief["Perfect foresight, 2 units"]
    out = pd.DataFrame({
        "customer_id": fc.customer_id, "segment": seg[cand],
        "cp_kw_summer": fc.cp_kw_summer, "cp_kw_winter": fc.cp_kw_winter,
        "relief_kw_summer": rs, "relief_kw_winter": rw,
        "relief_kw_summer_perfect": ps, "relief_kw_winter_perfect": pw,
        "relief_kw_summer_1unit": relief["Realistic, 1 unit"][0], "relief_kw_winter_1unit": relief["Realistic, 1 unit"][1],
        "firm_kw_summer": firm["summer"], "firm_kw_winter": firm["winter"],
        "need_kwh_p90": need_kwh, "need_kw_p90": need_kw, "size": size, "event_coverage": coverage,
        "dr_relief_kw_summer": dr_s, "dr_relief_kw_winter": dr_w,
        "persistence": (fc.peak_day_persistence_summer + fc.peak_day_persistence_winter) / 2,
        "electrified": (kc.ev_physics | kc.electric_heat).to_numpy(), "pv": kc.pv.to_numpy(),
        "ev": kc.ev_physics.to_numpy(), "heat_type": kc.heat_type.to_numpy(),
    })
    out = pd.concat([out, B.avoided_cost(rs, rw)], axis=1)
    out = pd.concat([out, B.avoided_cost(dr_s, dr_w).add_prefix("dr_")], axis=1)
    out["priority"] = B.priority_score(out)
    out["tier"] = B.tiers(out.priority)
    out.to_parquet(C.OUTPUTS / "bess_results.parquet", index=False)
    npv = B.npv_factor()

    # ---------------------------------------------------------------- aggregates
    def agg(g: pd.DataFrame) -> pd.Series:
        return pd.Series({
            "customers": len(g),
            "battery_relief_mw_summer": g.relief_kw_summer.sum() / 1000,
            "battery_relief_mw_winter": g.relief_kw_winter.sum() / 1000,
            "firm_mw_summer": g.firm_kw_summer.sum() / 1000, "firm_mw_winter": g.firm_kw_winter.sum() / 1000,
            "kw_per_home_summer": g.relief_kw_summer.mean(), "kw_per_home_winter": g.relief_kw_winter.mean(),
            "avoided_musd_yr_low": g.avoided_usd_yr_low.sum() / 1e6, "avoided_musd_yr_mid": g.avoided_usd_yr_mid.sum() / 1e6,
            "avoided_musd_yr_high": g.avoided_usd_yr_high.sum() / 1e6,
            "avoided_usd_per_home_yr_mid": g.avoided_usd_yr_mid.mean(),
            "npv_usd_per_home_mid": g.avoided_usd_yr_mid.mean() * npv,
            "dr_relief_mw_summer": g.dr_relief_kw_summer.sum() / 1000, "dr_relief_mw_winter": g.dr_relief_kw_winter.sum() / 1000,
            "share_electrified": g.electrified.mean(), "share_2units_plus_dr": (g["size"].str.contains("DR")).mean(),
        })
    tier_tab = out.groupby("tier").apply(agg).reindex(["Tier 1", "Tier 2", "Tier 3", "Not prioritised"])
    tier_tab.to_csv(C.OUTPUTS_SUMMARY / "bess_tiers.csv")
    var_tab = pd.DataFrame([{"variant": k_, "relief_mw_summer_all": v[0].sum() / 1000, "relief_mw_winter_all": v[1].sum() / 1000,
                             "relief_mw_summer_tier1": v[0][(out.tier == "Tier 1").to_numpy()].sum() / 1000,
                             "relief_mw_winter_tier1": v[1][(out.tier == "Tier 1").to_numpy()].sum() / 1000}
                            for k_, v in relief.items()]
                           + [{"variant": "Demand flex only (no battery), after rebound",
                               "relief_mw_summer_all": dr_s.sum() / 1000, "relief_mw_winter_all": dr_w.sum() / 1000,
                               "relief_mw_summer_tier1": dr_s[(out.tier == "Tier 1").to_numpy()].sum() / 1000,
                               "relief_mw_winter_tier1": dr_w[(out.tier == "Tier 1").to_numpy()].sum() / 1000}])
    var_tab.to_csv(C.OUTPUTS_SUMMARY / "bess_variants.csv", index=False)
    size_tab = pd.crosstab(out["size"], out.tier).reindex(columns=["Tier 1", "Tier 2", "Tier 3", "Not prioritised"])
    size_tab.to_csv(C.OUTPUTS_SUMMARY / "bess_sizing.csv")

    # system rebound: does fleet charging (Tier 1) create a new system peak?
    t1 = (out.tier == "Tier 1").to_numpy()
    tot = peaks["total"].astype(float)
    fleet_net = (res["Realistic, 2 units"].charge[t1].sum(axis=0) - res["Realistic, 2 units"].discharge[t1].sum(axis=0))
    new_sys = tot + fleet_net
    sys_check = {s: (tot[peaks[s]].max(), new_sys[np.isin(hours.month, m)].max())
                 for s, m in (("summer", C.SUMMER_MONTHS), ("winter", C.WINTER_MONTHS))}

    # ---------------------------------------------------------------- report
    emit("# Behind-the-meter battery and demand-flexibility screening (task 1.6)")
    emit()
    emit(f"Generated by `scripts/08_bess.py`. Battery: {C.BATTERY.units} x Enphase IQ 10C = {C.BATTERY.kwh} kWh, "
         f"{C.BATTERY.kw} kW continuous, {C.BATTERY.rte:.1%} round-trip efficiency. **Headline metric: avoided "
         "buildout cost** from coincident-peak relief; $ values are PLACEHOLDER ranges in `config.py` to confirm "
         "with Avangrid.")
    emit()
    emit("## Method")
    emit()
    emit("- **Candidates:** modeling population, not low-use/seasonal, with ≥ "
         f"{C.CANDIDATE_MIN_CP_KW} kW of load in the summer or winter system peak hours → **{n:,}** homes.")
    emit("- **Dispatch (hour by hour, state of charge carried across days):** recharge 00-05h only, never above the "
         "home's own p95 load (no new customer peak); discharge flat at min(7.68 kW, usable energy / hours left), "
         "never more than the home's load (no export). Multi-day heat waves and cold snaps run in sequence, so an "
         "incomplete overnight recharge reduces the next day's relief.")
    emit(f"- **Perfect foresight** (upper bound): discharge exactly in the system's top {C.PEAK_TOP_HOURS} hours per season.")
    emit(f"- **Realistic** (headline): {C.EVENT_BUDGET_PER_SEASON} events per season on the hottest / coldest forecast "
         f"days (forecast error s.d. {C.FORECAST_SD_F} °F), fixed window HE{C.WINDOW_HOURS['summer'][0] + 1}-"
         f"{C.WINDOW_HOURS['summer'][-1] + 1} summer and HE{C.WINDOW_HOURS['winter'][0] + 1}-{C.WINDOW_HOURS['winter'][-1] + 1} winter. "
         f"Events caught: {len(set(called['summer']) & {int(hours.day.iat[c]) for c in cs})}/"
         f"{len({int(hours.day.iat[c]) for c in cs})} summer and {len(set(called['winter']) & {int(hours.day.iat[c]) for c in cw})}/"
         f"{len({int(hours.day.iat[c]) for c in cw})} winter peak days.")
    emit("- **Relief that counts** = average kW the battery actually delivered across the system's top hours "
         "(missed hours count as zero). **Firm kW** = P10 across called event days of the flat level held "
         "through the whole window: the sustained kW we claim, not the 7.68 kW nameplate (6 h x 7.68 kW = 46 kWh "
         "> 20.16 kWh, so a full window is energy-limited to 3.36 kW).")
    emit("- **Sizing:** energy and power needed to hold the home at its own seasonal median through the window on "
         "each true peak day, P90 across days → no battery / 1 unit / 2 units / 2 units + DR.")
    emit(f"- **Demand flex (no battery):** AC shed {C.DR_AC_SHED_FRAC:.0%} and electric-heat shed "
         f"{C.DR_HEAT_SHED_FRAC:.0%} of weather-driven load (rebounds in the {C.DR_REBOUND_HOURS} hours after the "
         "window); EV charging in the window deferred to the next night's 00-05h (managed charging). 100% of "
         "shifted energy comes back. Relief is net of rebound.")
    emit("- **Avoided buildout $/yr** = capacity × relief at the annual system peak (summer today) + transmission × "
         "mean(summer, winter relief) + distribution deferral × max(summer, winter relief). "
         f"$/kW-yr low/mid/high: " + "; ".join(f"{k_} {v.low:.0f}/{v.mid:.0f}/{v.high:.0f}"
                                                for k_, v in C.AVOIDED_COST_PER_KW_YR.items())
         + f". NPV over {C.ANALYSIS_YEARS} years at {C.DISCOUNT_RATE:.0%} (factor {npv:.2f}).")
    emit("- **Priority score** = 0.45 avoided-$ rank + 0.15 peak-day persistence + 0.15 energy-feasible event "
         "coverage + 0.15 electrified (EV or electric heat) + 0.10 PV. Tier 1 = top 10% of candidates, "
         "Tier 2 next 20%, Tier 3 next 30%.")
    emit()
    emit("## Results by tier (realistic dispatch, 2 units)")
    emit()
    md(tier_tab.reset_index())
    t1r = tier_tab.loc["Tier 1"]
    emit(f"**Economics, honestly:** a Tier-1 home avoids about ${t1r.avoided_usd_per_home_yr_mid:,.0f}/yr of buildout "
         f"at mid values (NPV ≈ ${t1r.npv_usd_per_home_mid:,.0f} over {C.ANALYSIS_YEARS} years). That covers only part "
         "of a two-unit installation, so system-wide avoided cost alone does not justify a battery program. The case "
         "rests on (1) placing batteries where a specific feeder or transformer upgrade is deferred (distribution "
         "value is location-specific and can be far above the system average), (2) enabling electrification within "
         "the 3 kVA headroom (task 2.2), and (3) stacking customer value (backup power, bill savings).")
    emit()
    emit("## Dispatch variants (MW of coincident-peak relief)")
    emit()
    md(var_tab)
    gap_s = 1 - var_tab.relief_mw_summer_all.iat[1] / var_tab.relief_mw_summer_all.iat[0]
    gap_w = 1 - var_tab.relief_mw_winter_all.iat[1] / var_tab.relief_mw_winter_all.iat[0]
    emit(f"Realistic dispatch delivers {1 - gap_s:.0%} (summer) and {1 - gap_w:.0%} (winter) of the perfect-foresight "
         "relief: missed event days, a 6-hour window instead of the exact peak hours, and multi-day depletion.")
    emit()
    emit("## Sizing (customers)")
    emit()
    md(size_tab.reset_index())
    emit("## Checks")
    emit()
    md(checks)
    t1_dr = dr[t1].sum(axis=0)
    dr_sys = {s_: (tot[peaks[s_]].max(), (tot + t1_dr)[np.isin(hours.month, m)].max())
              for s_, m in (("summer", C.SUMMER_MONTHS), ("winter", C.WINTER_MONTHS))}
    emit(f"- Demand flex: {dr_new_peak:,} of {n:,} candidates would set a new *personal* annual peak from the "
         f"thermal rebound in the {C.DR_REBOUND_HOURS} h after an event, so rebound must be staggered. At system level, "
         "Tier-1 DR with rebound: " + "; ".join(f"{s_} {a / 1000:,.2f} MW vs {b / 1000:,.2f} MW before"
                                              for s_, (b, a) in dr_sys.items()) + ".")
    for s, (before, after) in sys_check.items():
        emit(f"- System {s} peak with Tier-1 fleet charging and discharging: {after / 1000:,.2f} MW vs "
             f"{before / 1000:,.2f} MW before (fleet recharge does not create a new system peak: "
             f"{'yes' if after <= before + 1e-6 else 'NO'}).")
    emit("- PV homes: the meter is delivered-only, so battery relief is limited to imports; PV + battery value is a lower bound.")
    emit()

    # ---------------------------------------------------------------- figures
    ex = out[out.tier == "Tier 1"].sort_values("priority", ascending=False).index[0]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4), sharey=False)
    for ax, s in zip(axes, ("summer", "winter")):
        evd = sorted({int(hours.day.iat[c]) for c in peaks[s]})
        # longest run of consecutive event days
        runs, cur = [], [evd[0]]
        for d in evd[1:]:
            if d == cur[-1] + 1:
                cur.append(d)
            else:
                runs.append(cur)
                cur = [d]
        runs.append(cur)
        run = max(runs, key=len)[:4]
        cols = np.arange(run[0] * 24, (run[-1] + 1) * 24)
        r2 = B.simulate(load[[ex]], real_t, C.BATTERY, cap[[ex]], trace_rows=np.array([0]))
        x = np.arange(len(cols))
        ax.plot(x, np.nan_to_num(load[ex, cols]), color=P.NEUTRAL, lw=1.5, label="Load without battery")
        ax.plot(x, np.nan_to_num(load[ex, cols]) + r2.charge[0, cols] - r2.discharge[0, cols],
                color=P.SUMMER if s == "summer" else P.WINTER, lw=2, label="Net load with battery")
        topc = np.isin(cols, peaks[s])
        ax.fill_between(x, 0, np.nan_to_num(load[ex, cols]).max() * 1.05, where=topc, color="#F0E442", alpha=0.35,
                        step="mid", label="System top hours")
        ax2 = ax.twinx()
        ax2.plot(x, r2.soc_trace[0, cols], color="black", lw=1, ls="--", label="State of charge (kWh)")
        ax2.set_ylim(0, C.BATTERY.kwh * 1.1)
        ax2.set_ylabel("kWh")
        ax2.grid(False)
        dates = hours.usage_date.iloc[cols[0]].date(), hours.usage_date.iloc[cols[-1]].date()
        ax.set_title(f"{s.title()} peak run {dates[0]} to {dates[1]}")
        ax.set_xticks(range(0, len(cols) + 1, 24), [f"day {i + 1}" for i in range(len(run) + 1)])
        ax.set_ylabel("kW")
        if s == "summer":
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            fig.legend(h1 + h2, l1 + l2, loc="upper center", bbox_to_anchor=(0.5, 0.02), ncol=4, fontsize=9)
    fig.subplots_adjust(wspace=0.35)
    fig.suptitle("Realistic dispatch for a Tier-1 home (2 x IQ 10C): SOC-constrained, multi-day", fontweight="bold")
    P.save(fig, "bess_dispatch_example")

    fig, ax = plt.subplots(figsize=(8, 4))
    tt = tier_tab.iloc[:3]
    x = np.arange(len(tt))
    ax.bar(x - 0.2, tt.battery_relief_mw_summer, 0.4, color=P.SUMMER, label="Summer peak relief (MW)")
    ax.bar(x + 0.2, tt.battery_relief_mw_winter, 0.4, color=P.WINTER, label="Winter peak relief (MW)")
    for i, (_, r_) in enumerate(tt.iterrows()):
        ax.text(i, max(r_.battery_relief_mw_summer, r_.battery_relief_mw_winter) * 1.03,
                f"{int(r_.customers):,} homes\n${r_.avoided_musd_yr_low:.1f}-{r_.avoided_musd_yr_high:.1f}M/yr",
                ha="center", fontsize=8)
    ax.set_xticks(x, tt.index)
    ax.set_ylabel("MW of coincident-peak relief")
    ax.set_title("Battery program by tier (realistic dispatch, 2 units)")
    ax.legend(loc="upper left")
    ax.set_ylim(0, max(tt.battery_relief_mw_summer.max(), tt.battery_relief_mw_winter.max()) * 1.3)
    P.save(fig, "bess_tiers")

    fig, ax = plt.subplots(figsize=(8, 4))
    v = var_tab.set_index("variant")
    y = np.arange(len(v))
    ax.barh(y + 0.2, v.relief_mw_summer_tier1, 0.4, color=P.SUMMER, label="Summer")
    ax.barh(y - 0.2, v.relief_mw_winter_tier1, 0.4, color=P.WINTER, label="Winter")
    ax.set_yticks(y, v.index)
    ax.invert_yaxis()
    ax.set_xlabel("MW of coincident-peak relief, Tier-1 homes")
    ax.set_title("What the kWh limit and realistic dispatch cost")
    ax.legend()
    ax.grid(axis="y", visible=False)
    P.save(fig, "bess_variants")

    (C.DOCS / "bess.md").write_text("\n".join(lines) + "\n")
    print(f"\ndone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
