"""Facts for the segment cards and program playbook (task 2.4).

Every number a card prints comes from `segment_facts()` / `global_facts()`, which read the pipeline's
outputs. The card *text* (insight_text.py) was written by Claude at build time from these aggregates
only (PLAN.md: no runtime API, no customer rows); it holds no literal numbers, and each qualitative
claim in it carries a check over these facts, so a rerun that changes the data cannot leave a stale
number or a claim that no longer holds.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from avangrid import config as C
from avangrid import segment as SEG

S = C.OUTPUTS_SUMMARY
TWO_DR = "2 units + DR (battery alone insufficient)"


def _csv(name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(S / f"{name}.csv", **kw)


def _nmf_short(row: pd.Series) -> str:
    when = "all day" if row.window_hours >= 12 else row.half_energy_window
    return f"{row.season} {when}"


def segment_facts() -> pd.DataFrame:
    """One row per business segment; column names are the card placeholders."""
    pc = _csv("peak_contribution_by_segment").set_index("group")
    ss = _csv("segment_summary").set_index("segment")
    f = pd.DataFrame(index=pc.index)
    f["customers"] = pc.customers.astype(int)
    f["share_customers"] = pc.share_customers
    f["share_kwh"] = pc.share_annual_kwh
    f["median_kwh"] = ss.median_annual_kwh
    f["load_factor"] = ss.median_load_factor
    for s, t in (("summer", "s"), ("winter", "w")):
        f[f"cp_{t}_kw"] = pc[f"mean_cp_kw_{s}"]
        f[f"share_cp_{t}"] = pc[f"share_cp_kw_{s}"]
        f[f"share_cp_{t}_lo"] = pc[f"share_cp_kw_{s}_lo"]
        f[f"share_cp_{t}_hi"] = pc[f"share_cp_kw_{s}_hi"]
        f[f"idx_{t}"] = pc[f"{s}_cp_index"]       # share of CP / share of customers
    f["winter_over_summer_cp"] = f.cp_w_kw / f.cp_s_kw

    # appliance mix (in-model customers)
    seg = pd.read_parquet(C.OUTPUTS / "segments.parquet")
    k = pd.read_parquet(C.OUTPUTS / "classify.parquet")
    d = seg.merge(k, on="customer_id")
    d = d[d.in_model]
    g = d.groupby("segment")
    f["ev"] = g.ev_physics.mean()
    f["l2"] = g.ev_class.apply(lambda x: (x == "L2 detectable").mean())
    f["l1"] = g.ev_class.apply(lambda x: (x == "L1 detectable").mean())
    f["eheat"] = g.electric_heat.mean()
    f["hp"] = g.heat_type.apply(lambda x: x.isin(["HP", "HP+backup"]).mean())
    f["resistive"] = g.heat_type.apply(lambda x: (x == "resistive").mean())
    f["eheat_unclear"] = g.heat_type.apply(lambda x: (x == "electric (type unclear)").mean())
    f["central_ac"] = g.ac_type.apply(lambda x: (x == "central").mean())
    f["pool"] = g.pool.mean()
    f["pv"] = g.pv.mean()
    f["labeled_ev"] = g.label_ev.sum().astype(int)
    f["labeled_hp"] = g.label_hp.sum().astype(int)
    f["labeled_pv"] = g.label_pv.sum().astype(int)
    f["emerging"] = g.flag_emerging.mean()
    f["also_winter_peak"] = g.flag_winter_peak.mean()
    f["also_summer_peak"] = g.flag_summer_peak.mean()
    f["anomaly"] = g.anomaly.mean()
    f["top_archetype"] = g.archetype_name.agg(lambda x: x.value_counts().index[0])
    f["top_archetype_share"] = g.archetype_name.agg(lambda x: x.value_counts(normalize=True).iloc[0])

    # BESS / demand flex (candidates only: task 1.6 screen)
    b = pd.read_parquet(C.OUTPUTS / "bess_results.parquet")
    gb = b.groupby("segment")
    f["bess_candidates"] = gb.size()
    f["bess_candidate_share"] = f.bess_candidates / f.customers
    for t in ("Tier 1", "Tier 2", "Tier 3"):
        f[t.lower().replace(" ", "")] = gb.tier.apply(lambda x, t=t: int((x == t).sum()))
    f["tier12"] = f.tier1 + f.tier2
    f["relief_s_kw"] = gb.relief_kw_summer.mean()
    f["relief_w_kw"] = gb.relief_kw_winter.mean()
    f["relief_s_mw"] = gb.relief_kw_summer.sum() / 1000
    f["relief_w_mw"] = gb.relief_kw_winter.sum() / 1000
    f["dr_s_kw"] = gb.dr_relief_kw_summer.mean()
    f["dr_w_kw"] = gb.dr_relief_kw_winter.mean()
    f["dr_s_mw"] = gb.dr_relief_kw_summer.sum() / 1000
    f["dr_w_mw"] = gb.dr_relief_kw_winter.sum() / 1000
    t12 = b[b.tier.isin(["Tier 1", "Tier 2"])].groupby("segment")
    f["relief_s_mw_t12"] = (t12.relief_kw_summer.sum() / 1000).reindex(f.index).fillna(0.0)
    f["relief_w_mw_t12"] = (t12.relief_kw_winter.sum() / 1000).reindex(f.index).fillna(0.0)
    f["usd_mid"] = gb.avoided_usd_yr_mid.mean()
    f["usd_low"] = gb.avoided_usd_yr_low.mean()
    f["usd_high"] = gb.avoided_usd_yr_high.mean()
    f["musd_mid"] = gb.avoided_usd_yr_mid.sum() / 1e6
    f["two_units_dr"] = gb["size"].apply(lambda x: (x == TWO_DR).mean())
    f["one_unit"] = gb["size"].apply(lambda x: (x == "1 unit").mean())
    num = ["bess_candidates", "tier1", "tier2", "tier3", "tier12"]
    f[num] = f[num].fillna(0).astype(int)
    f["tier1_pct"] = f.tier1 / f.bess_candidates.where(f.bess_candidates > 0)

    # future winter peak (headline HP-adoption model, task 2.2)
    fw = _csv("future_winter_cp_by_segment").set_index("segment")
    f["wcp_today"] = fw.share_winter_cp_0pct
    f["wcp_30"] = fw.share_winter_cp_30pct

    # NMF fingerprint (task 2.3): the part most over-represented vs all modeled customers
    mix = _csv("nmf_mix_by_segment").set_index("group")
    comp = _csv("nmf_components")
    parts = [c for c in mix.columns if c.startswith("C")]
    delta = mix.loc[f.index, parts] - mix.loc["All modeled customers", parts]
    top = delta.idxmax(axis=1)
    f["nmf_top_part"] = top
    f["nmf_top_desc"] = [_nmf_short(comp.iloc[int(p[1:])]) for p in top]
    f["nmf_top_share"] = [mix.at[s, p] for s, p in top.items()]
    f["nmf_top_delta"] = [delta.at[s, p] for s, p in top.items()]
    f["nmf_top_delta_pts"] = f.nmf_top_delta * 100
    f["nmf_top_match"] = [comp.best_match[int(p[1:])] if comp.explained[int(p[1:])] else "no detected appliance"
                          for p in top]
    return f


def global_facts() -> dict:
    out: dict = {}
    pk = _csv("system_peaks")
    for s in ("summer", "winter"):
        r = pk[(pk.season == s) & (pk["rank"] == 1)].iloc[0]
        out[f"peak_{s}_mw"] = r.system_kwh / 1000
        out[f"peak_{s}_date"] = pd.Timestamp(r.date).strftime("%b %-d")
        out[f"peak_{s}_temp"] = r.temp_f
    out["winter_over_summer_peak"] = out["peak_winter_mw"] / out["peak_summer_mw"]
    pc = _csv("peak_contribution_by_segment")
    out["customers_model"] = int(pc.customers.sum())
    sw = _csv("future_crossover_sweep")
    for m in ("A", "B", "C"):
        x = sw[(sw.model == m) & (sw.winter_peak_kw >= sw.summer_peak_kw)]
        out[f"crossover_{m}"] = float(x.hp_share.min()) if len(x) else np.nan
    sc = _csv("future_scenarios").set_index(["model", "hp_share"])
    r30 = sc.loc[("B", 0.3)]
    fw = _csv("future_winter_cp_by_segment").set_index("segment")
    out["new_hp_wcp_30"] = fw.at["of which: new HP load", "share_winter_cp_30pct"]
    out["new_hp_wcp_30_pts"] = out["new_hp_wcp_30"] * 100
    out["wcp_mw_today"] = sc.loc[("B", 0.0)].winter_cp_mw
    out["wcp_mw_30"] = sc.loc[("B", 0.3)].winter_cp_mw
    out["hp30_winter_mw"] = r30.winter_peak_mw
    out["hp30_summer_mw"] = r30.summer_peak_mw
    fb = _csv("future_bess_winter").set_index("hp_share").loc[0.3]
    out["hp30_peak_after_coord_mw"] = fb.annual_peak_after_mw_coordinated
    out["hp30_peak_after_uncoord_mw"] = fb.annual_peak_after_mw_relaxed
    out["hp_added_kw_per_adopter"] = fb.added_winter_cp_kw_per_adopter
    hd = _csv("future_headroom").set_index("metric").value
    out["kva_exceed"] = hd["Share exceeding +3 kVA over old peak, no battery"]
    out["kva_fix_1"] = hd["Of those exceeding, share brought within cap by 1 unit"]
    out["kva_fix_2"] = hd["Of those exceeding, share brought within cap by 2 units"]
    out["kva_hours"] = hd["Median hours/yr above the cap, no battery (those exceeding)"]
    t = _csv("bess_tiers").set_index("tier")
    for tier in ("Tier 1", "Tier 2", "Tier 3"):
        key = tier.lower().replace(" ", "")
        out[f"{key}_homes"] = int(t.at[tier, "customers"])
        out[f"{key}_mw_s"] = t.at[tier, "battery_relief_mw_summer"]
        out[f"{key}_mw_w"] = t.at[tier, "battery_relief_mw_winter"]
        out[f"{key}_usd"] = t.at[tier, "avoided_usd_per_home_yr_mid"]
        out[f"{key}_npv"] = t.at[tier, "npv_usd_per_home_mid"]
    out["bess_candidates_total"] = int(t.customers.sum())
    out["usd_mid_all"] = float(pd.read_parquet(C.OUTPUTS / "bess_results.parquet").avoided_usd_yr_mid.mean())
    out["peak_q_pct"] = 1 - SEG.PEAK_CONTRIB_Q
    out["hp_top"] = max(C.HP_ADOPTION_SCENARIOS)
    ad = pd.read_parquet(C.OUTPUTS / "future_adopters.parquet")
    out["adopters_typical_share"] = float((ad.segment == "Typical").mean())
    return out
