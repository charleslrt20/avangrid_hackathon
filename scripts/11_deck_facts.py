"""Task 3.2: export every number the deck shows to deck/facts.json (aggregates only).

deck/build_deck.js reads this file, so a slide number always traces to outputs/ and a rerun of the pipeline
followed by `node deck/build_deck.js` rebuilds the deck with current numbers (task 3.4 audit).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from avangrid import config as C  # noqa: E402
from avangrid import insight_text as T  # noqa: E402
from avangrid import insights as I  # noqa: E402
from avangrid import io  # noqa: E402
from avangrid.features.build import DESCRIPTIONS  # noqa: E402

S = C.OUTPUTS_SUMMARY


def plain(v):
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def csv(name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(S / f"{name}.csv", **kw)


def main() -> None:
    g = I.global_facts()
    seg = I.segment_facts()
    out: dict = {"global": {k: plain(v) for k, v in g.items()}}

    # ---- data
    cust = io.load_customers()
    d = io.decisions()
    out["data"] = {
        "customers": int(len(cust)), "modeled": int(cust.in_model.sum()), "hours": C.N_HOURS,
        "excluded_stuck": int((cust.exclude_reason == "stuck_or_vacant_meter").sum()),
        "excluded_short": int((cust.exclude_reason == "short_history").sum()),
        "excluded_error": int((cust.exclude_reason == "data_error").sum()),
        "labels_ev": int(cust.label_ev.sum()), "labels_hp": int(cust.label_hp.sum()), "labels_pv": int(cust.label_pv.sum()),
        "very_high_load_kept": len(d["very_high_load"]),
        "start": str(C.START.date()), "metering": C.METERING,
    }

    # ---- classification
    k = pd.read_parquet(C.OUTPUTS / "classify.parquet")
    k = k[k.in_model]
    cm = csv("classification_metrics", index_col=0)
    num = lambda s: float(pd.to_numeric(s, errors="coerce"))  # noqa: E731
    out["classify"] = {
        "ev_detected": int(k.ev_physics.sum()), "ev_l2": int((k.ev_class == "L2 detectable").sum()),
        "ev_l1": int((k.ev_class == "L1 detectable").sum()), "ev_implied": C.IMPLIED_COUNTS["ev"],
        "hp_detected": int(k.heat_type.isin(["HP", "HP+backup"]).sum()), "hp_implied": C.IMPLIED_COUNTS["hp"],
        "hp_only": int((k.heat_type == "HP").sum()), "hp_backup": int((k.heat_type == "HP+backup").sum()),
        "resistive": int((k.heat_type == "resistive").sum()), "eheat_unclear": int((k.heat_type == "electric (type unclear)").sum()),
        "eheat": int(k.electric_heat.sum()), "central_ac": int((k.ac_type == "central").sum()),
        "pv": int(k.pv.sum()), "pv_battery": int(k.pv_battery.sum()), "pool": int(k.pool.sum()),
        "wh_lowconf": int(k.water_heater_lowconf.sum()), "tub_lowconf": int(k.hot_tub_lowconf.sum()),
        "ev_label_recall": float((k.label_ev & k.ev_physics).sum() / k.label_ev.sum()),
        "hp_label_eheat": float((k.label_hp & k.electric_heat).sum() / k.label_hp.sum()),
        "pv_label_recall": float((k.label_pv & k.pv).sum() / k.label_pv.sum()),
        "pv_precision": float((k.label_pv & k.pv).sum() / k.pv.sum()),
        "pv_unregistered": int((k.pv & ~k.label_pv).sum()),
        "c_hat_ev": num(cm.at["c_hat_elkan_noto", "ev"]), "c_hat_hp": num(cm.at["c_hat_elkan_noto", "hp"]),
        "ev_cv_auc_logit": num(cm.at["cv_auc", "ev"]), "hp_cv_auc": num(cm.at["cv_auc", "hp"]),
        "ev_cv_auc_rank": num(cm.at["ranking_cv_auc", "ev"]), "pu_c_brief": C.PU_C,
        "modeled": int(len(k)), "n_features": len(DESCRIPTIONS),
    }

    # ---- validation (key rows of the validation table, as published)
    vt = csv("validation_table")
    pick = lambda frag: vt[vt.check.str.contains(frag, regex=False)].iloc[0]  # noqa: E731
    out["validation"] = {
        "n_checks": int(len(vt)), "n_pass": int((vt.result == "pass").sum()), "n_fail": int((vt.result == "fail").sum()),
        "n_info": int((vt.result == "info").sum()),
        "kappa_flags": pick("Physics flags, Cohen").value, "kappa_heat": pick("Heat type (5 classes)").value,
        "kappa_ev": pick("EV class (L2 / L1 / none)").value, "segment_same": pick("Business segment, same segment").value,
        "seasonal": pick("Flags fire in the expected season").value,
        "ev_oot_auc": pick("EV PU, out-of-customer").value, "hp_oot_auc": pick("HP PU, out-of-customer").value,
        "pv_pr": pick("PV rule vs complete PV list").value,
        "bess_violations": pick("Power / SOC / export violations").value,
        "realistic_vs_perfect": pick("Realistic vs perfect-foresight").value,
        "recs_eheat": pick("Electric heat (any) vs RECS").value, "recs_any_ac": pick("Any AC vs RECS").value,
        "silhouette": pick("K-Means k = 6 silhouette").value,
    }

    # ---- segments, BESS, future, NMF
    out["segments"] = [{"segment": s, **{c: plain(v) for c, v in r.items()}} for s, r in seg.iterrows()]
    out["appliance_peak"] = csv("peak_contribution_by_appliance").to_dict(orient="records")
    out["bess_tiers"] = csv("bess_tiers").to_dict(orient="records")
    out["bess_variants"] = csv("bess_variants").to_dict(orient="records")
    sz = csv("bess_sizing", index_col=0)
    out["bess_sizing_tier1"] = {i: int(v) for i, v in sz["Tier 1"].items()}
    out["bess_assumptions"] = {
        "kwh": C.BATTERY.kwh, "kw": C.BATTERY.kw, "rte": C.BATTERY.rte, "events": C.EVENT_BUDGET_PER_SEASON,
        "window_summer": list(C.WINDOW_HOURS["summer"]), "window_winter": list(C.WINDOW_HOURS["winter"]),
        "avoided": {k_: v.as_dict() for k_, v in C.AVOIDED_COST_PER_KW_YR.items()},
        "years": C.ANALYSIS_YEARS, "discount": C.DISCOUNT_RATE, "candidate_min_kw": C.CANDIDATE_MIN_CP_KW,
    }
    fs = csv("future_scenarios")
    out["future_scenarios"] = fs[fs.model == "B"].to_dict(orient="records")
    out["future_bess"] = csv("future_bess_winter").to_dict(orient="records")
    comp = csv("nmf_components")
    sel = csv("nmf_k_selection")
    out["nmf"] = {
        "k": int(len(comp)), "components": comp[["component", "name", "label_free", "best_match", "best_auc",
                                                "energy_share", "explained"]].to_dict(orient="records"),
        "k8_min_cosine": float(sel.loc[sel.k == len(comp) + 1, "odd_even_min_cosine"].iloc[0]),
        "k_min_cosine": float(sel.loc[sel.k == len(comp), "odd_even_min_cosine"].iloc[0]),
        "r2": float(sel.loc[sel.k == len(comp), "r2_vs_mean_shape"].iloc[0]),
    }
    mix = csv("nmf_mix_by_segment").set_index("group")
    heat = [f"C{c.component}" for c in comp.itertuples() if c.explained and "heat" in c.best_match]
    out["nmf"]["heat_share_eheat"] = float(mix.loc["Electric-heat home", heat].sum())
    out["nmf"]["heat_share_typical"] = float(mix.loc["Typical", heat].sum())
    l2 = csv("nmf_associations")
    out["nmf"]["l2_best_auc"] = float(l2[l2.target == "EV L2 signature"].auc.max())

    # ---- playbook rows, rendered with the same templates as docs/insights/playbook.md
    rows = []
    for row in T.PLAYBOOK:
        ctx = {**g, **{c: plain(v) for c, v in seg.loc[row["segment"]].items()}}
        rows.append({"when": row["when"], "segment": row["segment"], "program": row["program"],
                     "target": row["target"].format_map(ctx), "mw": row["mw"].format_map(ctx)})
    out["playbook"] = rows
    cards = json.loads((C.INSIGHTS / "segment_cards.json").read_text())
    out["cards"] = {c["segment"]: {f: c[f] for f in ("headline", "who", "why", "program", "watch")} for c in cards}

    def clean(o):
        if isinstance(o, dict):
            return {k_: clean(v) for k_, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        o = plain(o)
        return None if isinstance(o, float) and np.isnan(o) else o

    path = C.DECK / "facts.json"
    path.write_text(json.dumps(clean(out), indent=1, allow_nan=False) + "\n")
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
