"""Task 2.4: segment insight cards + program playbook, rendered from aggregate facts.

The prose (src/avangrid/insight_text.py) was written by Claude at build time from aggregate tables only.
This script fills its placeholders from the pipeline outputs and refuses to publish if
  * a template contains a literal number (every number must come from a fact), or
  * a qualitative claim's check is False on the current data, or
  * a rendered card shows a missing value (nan).

Outputs
  docs/insights/segment_cards.md     one card per business segment (deck source)
  docs/insights/segment_cards.json   same, plus key metrics, for the app
  docs/insights/playbook.md          segment -> program table, system context, caveats
  outputs/summary/insight_facts.csv  every per-segment fact the cards can cite
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from avangrid import config as C  # noqa: E402
from avangrid import insight_text as T  # noqa: E402
from avangrid import insights as I  # noqa: E402
from avangrid import segment as S  # noqa: E402

PLACEHOLDER = re.compile(r"\{[^{}]*\}")
ALLOWED_LITERALS = [r"\bL[12]\b", r"Tier [123](\+2)?", r"\b3 kVA\b", r"\b[12] units?\b", r"\bQ[1-5]\b",
                    r"task \d\.\d+b?"]


def literal_numbers(template: str) -> list[str]:
    text = PLACEHOLDER.sub("", template)
    for pat in ALLOWED_LITERALS:
        text = re.sub(pat, "", text)
    return re.findall(r"\d[\d.,]*", text)


def plain(v):
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    return v


def main() -> None:
    f = I.segment_facts()
    g = I.global_facts()
    g["max_tier1"] = int(f.tier1.max())
    g["typical_median_kwh"] = float(f.loc["Typical", "median_kwh"])
    g["max_idx_w_residential"] = float(f.drop(index="Very high load / non-residential").idx_w.max())
    problems: list[str] = []

    # ---- audit templates: no literal numbers
    templates = [(seg, fld, card[fld]) for seg, card in T.CARDS.items() for fld in T.CARD_FIELDS]
    templates += [("playbook", k, row[k]) for row in T.PLAYBOOK for k in ("program", "target", "mw")]
    templates += [("playbook", "intro", s) for s in T.PLAYBOOK_INTRO]
    for seg, fld, tpl in templates:
        lit = literal_numbers(tpl)
        if lit:
            problems.append(f"literal number(s) {lit} in {seg} / {fld}")
    missing = set(S.SEGMENT_ORDER) - set(T.CARDS)
    if missing:
        problems.append(f"no card for segments {sorted(missing)}")

    # ---- render cards + run claim checks
    cards, checks = [], []
    for seg in [s for s in S.SEGMENT_ORDER if s in f.index]:
        ctx = {**g, **{k: plain(v) for k, v in f.loc[seg].items()}}
        card = T.CARDS[seg]
        out = {"segment": seg}
        for fld in T.CARD_FIELDS:
            try:
                out[fld] = card[fld].format_map(ctx)
            except KeyError as e:
                problems.append(f"unknown placeholder {e} in {seg} / {fld}")
                out[fld] = card[fld]
            if re.search(r"\bnan\b", out[fld]):
                problems.append(f"nan rendered in {seg} / {fld}")
        for expr in card["checks"]:
            try:
                ok = bool(eval(expr, {}, ctx))  # noqa: S307 (build-time expressions we wrote)
            except Exception as e:  # noqa: BLE001
                ok = False
                expr = f"{expr}  [error: {e}]"
            checks.append({"scope": seg, "claim_check": expr, "holds": ok})
            if not ok:
                problems.append(f"claim check failed for {seg}: {expr}")
        r = f.loc[seg]
        out["metrics"] = {
            "customers": int(r.customers), "share_customers": round(float(r.share_customers), 4),
            "share_summer_cp": round(float(r.share_cp_s), 4),
            "share_summer_cp_ci": [round(float(r.share_cp_s_lo), 4), round(float(r.share_cp_s_hi), 4)],
            "share_winter_cp": round(float(r.share_cp_w), 4),
            "share_winter_cp_ci": [round(float(r.share_cp_w_lo), 4), round(float(r.share_cp_w_hi), 4)],
            "bess_candidates": int(r.bess_candidates), "tier1": int(r.tier1), "tier2": int(r.tier2),
            "battery_relief_kw_summer": None if pd.isna(r.relief_s_kw) else round(float(r.relief_s_kw), 3),
            "battery_relief_kw_winter": None if pd.isna(r.relief_w_kw) else round(float(r.relief_w_kw), 3),
            "dr_relief_kw_summer": None if pd.isna(r.dr_s_kw) else round(float(r.dr_s_kw), 3),
            "dr_relief_kw_winter": None if pd.isna(r.dr_w_kw) else round(float(r.dr_w_kw), 3),
            "avoided_usd_per_home_yr": None if pd.isna(r.usd_mid) else
            {"low": round(float(r.usd_low)), "mid": round(float(r.usd_mid)), "high": round(float(r.usd_high))},
        }
        cards.append(out)

    gctx = {k: plain(v) for k, v in g.items()}
    intro = [s.format_map(gctx) for s in T.PLAYBOOK_INTRO]
    for expr in T.PLAYBOOK_CHECKS:
        ok = bool(eval(expr, {}, gctx))  # noqa: S307
        checks.append({"scope": "playbook", "claim_check": expr, "holds": ok})
        if not ok:
            problems.append(f"claim check failed for playbook: {expr}")
    rows = []
    for row in T.PLAYBOOK:
        ctx = {**g, **{k: plain(v) for k, v in f.loc[row["segment"]].items()}}
        rows.append({"When": row["when"], "Segment": row["segment"], "Program": row["program"],
                     "Target": row["target"].format_map(ctx),
                     "Battery relief, Tier 1+2 (MW summer / winter)": row["mw"].format_map(ctx)})
    pb = pd.DataFrame(rows)

    # ---- write
    C.INSIGHTS.mkdir(parents=True, exist_ok=True)
    md = ["# Segment insight cards (task 2.4)", "",
          "Generated by `scripts/09_insights.py`. Text written by Claude at build time from aggregate tables "
          "only; every number is filled from `outputs/` (see `outputs/summary/insight_facts.csv`) and every "
          f"qualitative claim is checked against the data ({sum(c['holds'] for c in checks)}/{len(checks)} "
          "checks hold). $ values use placeholder avoided-cost rates (config.py) pending Avangrid's figures.", ""]
    for c in cards:
        m = c["metrics"]
        md += [f"## {c['segment']}", "", f"**{c['headline']}**", "",
               f"- **Who:** {c['who']}", f"- **Why it matters:** {c['why']}",
               f"- **Program:** {c['program']}", f"- **Watch out:** {c['watch']}", ""]
        usd = m["avoided_usd_per_home_yr"]
        md += ["| customers | share of homes | summer peak share | winter peak share | Tier 1 / 2 | battery kW S / W | DR kW S / W | avoided $/home/yr (low-mid-high) |",
               "|---:|---:|---:|---:|---:|---:|---:|---:|",
               f"| {m['customers']:,} | {m['share_customers']:.1%} | {m['share_summer_cp']:.1%} "
               f"({m['share_summer_cp_ci'][0]:.1%}-{m['share_summer_cp_ci'][1]:.1%}) | {m['share_winter_cp']:.1%} "
               f"({m['share_winter_cp_ci'][0]:.1%}-{m['share_winter_cp_ci'][1]:.1%}) | {m['tier1']:,} / {m['tier2']:,} | "
               + (f"{m['battery_relief_kw_summer']:.2f} / {m['battery_relief_kw_winter']:.2f} | "
                  f"{m['dr_relief_kw_summer']:.2f} / {m['dr_relief_kw_winter']:.2f} | "
                  f"{usd['low']:,}-{usd['mid']:,}-{usd['high']:,} |" if usd else "not screened | not screened | n/a |"),
               ""]
    (C.INSIGHTS / "segment_cards.md").write_text("\n".join(md) + "\n")
    (C.INSIGHTS / "segment_cards.json").write_text(json.dumps(cards, indent=2, ensure_ascii=False) + "\n")

    pmd = ["# Program playbook (task 2.4)", "",
           "Generated by `scripts/09_insights.py` from the same facts as the segment cards.", "",
           "## System context", ""] + [f"- {s}" for s in intro] + [
           "", "## Segment → program", "",
           "Now = launch with today's data; Next = after the pilot or once avoided-cost values are confirmed; "
           "Watch = monitor, no offer yet. Battery relief = realistic dispatch (task 1.6) summed over the "
           "segment's Tier 1 and Tier 2 homes.", "",
           pb.to_markdown(index=False), "", "## Caveats", ""] + [f"- {c}" for c in T.CAVEATS] + [
           "", "## Claim checks", "", pd.DataFrame(checks).to_markdown(index=False), ""]
    (C.INSIGHTS / "playbook.md").write_text("\n".join(pmd) + "\n")

    keep = [c for c in f.columns if not c.startswith("nmf_top_part")]
    f[keep].round(4).rename_axis("segment").to_csv(C.OUTPUTS_SUMMARY / "insight_facts.csv")

    print(f"{len(cards)} cards, {len(pb)} playbook rows, {sum(c['holds'] for c in checks)}/{len(checks)} claim checks hold")
    if problems:
        print("\nPROBLEMS:\n- " + "\n- ".join(problems))
        sys.exit(1)


if __name__ == "__main__":
    main()
