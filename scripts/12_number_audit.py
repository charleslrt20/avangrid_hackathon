"""Task 3.4: number audit, deck <-> pipeline outputs.

Every number the deck shows (slide text, tables, speaker notes) must trace to deck/facts.json, which
scripts/11_deck_facts.py builds from outputs/. This script:

  1. reads every numeric token from the built .pptx (slides + notes);
  2. matches each against the numeric values in facts.json at the precision the slide shows
     (also as a percent, x1000 and /1000, since slides show shares as % and kW as MW);
  3. lists digits written literally in deck/build_deck.js text (not inside ${...}) for review;
  4. checks README.md numbers the same way.

Tokens that match nothing go to docs/number_audit.md as 'unmatched'. Each is either a spec constant or label
(reviewed and whitelisted below with a reason) or a mismatch to fix. Exit code 1 if any unexplained token remains.

    python scripts/12_number_audit.py [path/to/deck.pptx]
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECK = Path(sys.argv[1]) if len(sys.argv) > 1 else max((p for p in (ROOT / "deck").glob("*Final Submission.pptx") if not p.name.startswith("~$")), key=lambda p: p.stat().st_mtime)
FACTS = json.loads((ROOT / "deck" / "facts.json").read_text())

TOKEN = re.compile(r"(?<![\w.])[-−]?\$?\d[\d,]*(?:\.\d+)?(?:%|[kKM](?![a-zA-Z]))?")

# Numbers that are labels, specs from the brief or fixed method parameters, not results. Each needs a reason.
# Matched as (token, context regex): the token is accepted only when the surrounding text matches.
WHITELIST: list[tuple[str, str, str]] = [
    (r"20(25|26)", r"", "year"),
    (r"(1[0-2]|[1-9])", r"(?i)\b(1[0-2]|[1-9])/(\d{1,2})\b", "date (m/d)"),
    (r"\d{1,2}", r"(?i)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d", "calendar day"),
    (r"(1|2|3)", r"(?i)\b(part|tier|task|phase|q)\s?\d", "label: part / tier / task / Q number"),
    (r"(1|2)", r"(?i)\bL[12]\b|level[- ][12]", "label: EV charger level"),
    (r"(1|2)", r"(?i)\d units?\b|unit", "battery unit count"),
    (r"(3)", r"(?i)3 ?kva", "3 kVA headroom rule (brief)"),
    (r"(200)", r"(?i)200 ?a\b", "200 A service (brief)"),
    (r"(10)", r"(?i)iq ?10c?", "product name Enphase IQ 10C"),
    (r"(\d+)", r"(?i)\bHE ?\d+|hour[- ]ending|\d+-\d+ ?h\b|\d{1,2}[-–]\d{1,2}h|\d{2}:\d{2}", "hour-of-day label"),
    (r"(24|12|72|288|8,?760|365)", r"(?i)hour|month|profile|dim|×|x 24|day", "time-grid size"),
    (r"(0|1)", r"", "0 / 1 (bounds, equation constants)"),
    (r"2020", r"RECS 2020", "survey edition (EIA RECS 2020)"),
    (r"[\d,]+", r"of the [\d,]+ Tier 1 homes are electri", "derived in the builder: share_electrified × customers"),
]

# Hand-written numbers in deck/build_deck.js found by the perturbation test (task 3.4: rebuild the deck from a copy
# of facts.json with every number changed; these stayed put). Each was checked against its source by hand.
REVIEWED = [
    ("0 negatives", "docs/data_checks.md (a): no negative reading in 298M customer-hours"),
    ("benchmark 0.74", "PLAN.md §2 read-only benchmark, EV AUC 0.736"),
    ("3 designed to fail ... 3 survey gaps", "outputs/summary/validation_table.csv: 3 'check allowed to fail' + 3 RECS fails"),
    ("score weights 0.45 / 0.15 / 0.15 / 0.15 / 0.10", "src/avangrid/bess.py priority score"),
    ("Tier cut-offs top 10% / next 20% / next 30%", "docs/bess.md method (config tier shares)"),
    ("recharge 00-05 h", "config.RECHARGE_HOURS = range(0, 6)"),
    ("L2 1-6 h, 5.5-12 kW, >= 1 week in 4", "config.EV_L2_MAX_HOURS = 6, classify.EV_L2_KW = (5.5, 12), EV_L2_WEEK_SHARE = 0.25"),
    ("L1 1.0-2.2 kWh", "config.EV_L1_KW = (1.0, 2.2)"),
    ("HP + backup: slope >= 2x below 20 °F; resistive COP = 1", "classify.py heat-type rule; physics"),
    ("views under 0.2 s", "task 2.5 AppTest timings: every view <= 0.13 s"),
    ("34k homes (notes)", "34,238 customers in the AMI"),
    ("c = 0.25 (notes)", "the brief's labeled share"),
    ("95% bootstrap CI, top 88 hours", "docs/drivers.md method; 1% of 8,760 h per season"),
]


def fmt_forms(v: float) -> set[float]:
    return {v, v * 100, v / 1000, v * 1000, v / 1e6, -v}


def numeric_leaves(o, out: list[float]) -> None:
    if isinstance(o, bool) or o is None:
        return
    if isinstance(o, (int, float)):
        out.append(float(o))
    elif isinstance(o, str):
        for t in TOKEN.findall(o):
            v = parse(t)
            if v is not None:
                out.append(v[0])
    elif isinstance(o, dict):
        for x in o.values():
            numeric_leaves(x, out)
    elif isinstance(o, list):
        for x in o:
            numeric_leaves(x, out)


def parse(tok: str):
    t = tok.replace("−", "-").replace("$", "").replace(",", "")
    mult = 1.0
    if t[-1] in "kK":
        mult, t = 1e3, t[:-1]
    elif t[-1] == "M":
        mult, t = 1e6, t[:-1]
    t = t.rstrip("%")
    try:
        v = float(t)
    except ValueError:
        return None
    dec = len(t.split(".")[1]) if "." in t else 0
    return v * mult, dec, mult


FACT_VALUES: list[float] = []
numeric_leaves(FACTS, FACT_VALUES)
CANDIDATES = sorted({round(f, 6) for v in FACT_VALUES for f in fmt_forms(v)})


def traces(tok: str) -> bool:
    p = parse(tok)
    if p is None:
        return True
    v, dec, mult = p
    base = v / mult
    for c in CANDIDATES:
        cc = c / mult
        # the slide may round (or truncate a trailing zero): match at the shown precision
        if round(cc, dec) == round(base, dec) and abs(cc - base) <= 0.5 * 10 ** (-dec) + 1e-9:
            return True
    return False


def whitelisted(tok: str, context: str) -> str | None:
    bare = tok.replace("$", "").replace("%", "").replace("−", "-").lstrip("-")
    for pat, ctx, why in WHITELIST:
        if re.fullmatch(pat, bare) and (not ctx or re.search(ctx, context)):
            return why
    return None


def pptx_paragraphs(path: Path) -> list[tuple[str, str]]:
    z = zipfile.ZipFile(path)
    out = []
    key = lambda n: int(re.findall(r"(\d+)\.xml$", n)[0])  # noqa: E731
    for kind, pat in (("slide", r"ppt/slides/slide\d+\.xml$"), ("notes", r"ppt/notesSlides/notesSlide\d+\.xml$")):
        for n in sorted((n for n in z.namelist() if re.match(pat, n)), key=key):
            x = z.read(n).decode("utf8")
            for p in re.findall(r"<a:p>.*?</a:p>", x, flags=re.S):
                text = "".join(re.findall(r"<a:t>([^<]*)</a:t>", p))
                text = text.replace("&apos;", "'").replace("&quot;", "\"").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                if text.strip():
                    out.append((f"{kind} {key(n)}", text))
    return out


def audit(paras: list[tuple[str, str]]):
    rows = []
    for where, text in paras:
        for m in TOKEN.finditer(text):
            tok = m.group(0)
            ctx = text[max(0, m.start() - 40): m.end() + 40]
            if traces(tok):
                status, why = "traced", ""
            else:
                why = whitelisted(tok, ctx)
                status = "label/spec" if why else "UNMATCHED"
            rows.append(dict(where=where, token=tok, status=status, why=why or "", context=ctx.strip()))
    return rows


def hardcoded_in_builder() -> list[tuple[int, str]]:
    """Digits in builder text literals outside ${...}: geometry and colours are skipped (they are not text)."""
    hits = []
    for i, line in enumerate((ROOT / "deck" / "build_deck.js").read_text().splitlines(), 1):
        for lit in re.findall(r'"([^"\n]*)"|`([^`\n]*)`', line):
            s = lit[0] or lit[1]
            s = re.sub(r"\$\{[^}]*\}", "", s)
            if re.search(r"[A-Za-z]{3,}", s) and re.search(r"\d", s) and not re.fullmatch(r"[0-9A-Fa-f]{6}", s) \
                    and not s.endswith((".png", ".json", ".pptx")):
                hits.append((i, s))
    return hits


def main() -> int:
    deck_rows = audit(pptx_paragraphs(DECK))
    # README: the "Main findings" section quotes results (setup, timings and specs elsewhere are not results)
    md = (ROOT / "README.md").read_text()
    sec = re.search(r"^## Main findings\n(.*?)(?=^## )", md, flags=re.S | re.M)
    readme = [("README", p) for p in (sec.group(1) if sec else "").split("\n") if p.strip()]
    readme_rows = audit(readme)
    lits = hardcoded_in_builder()

    def table(rows):
        if not rows:
            return ["(none)"]
        out = ["| where | token | status | reason | context |", "|---|---|---|---|---|"]
        for r in rows:
            out.append(f"| {r['where']} | `{r['token']}` | {r['status']} | {r['why']} | {r['context'].replace('|', '/')} |")
        return out

    n = lambda rows, s: sum(r["status"] == s for r in rows)  # noqa: E731
    bad = [r for r in deck_rows + readme_rows if r["status"] == "UNMATCHED"]
    lines = [
        "# Number audit (task 3.4)",
        "",
        f"Generated by `scripts/12_number_audit.py` on `{DECK.name}`. Every number shown on a slide, in a table or in "
        "the speaker notes is matched against `deck/facts.json` (built from `outputs/` by `scripts/11_deck_facts.py`) at "
        "the precision the slide shows. Labels and spec constants from the brief are listed with a reason.",
        "",
        f"- Deck: {len(deck_rows)} numbers; {n(deck_rows, 'traced')} traced to facts.json, "
        f"{n(deck_rows, 'label/spec')} labels/specs, **{n(deck_rows, 'UNMATCHED')} unmatched**.",
        f"- README main findings: {len(readme_rows)} numbers; {n(readme_rows, 'traced')} traced, "
        f"{n(readme_rows, 'label/spec')} labels/specs, **{n(readme_rows, 'UNMATCHED')} unmatched**.",
        f"- Text literals with digits in `deck/build_deck.js` (written by hand, reviewed below): {len(lits)}.",
        "",
        "## Unmatched",
        "",
        *table(bad),
        "",
        "## Labels and spec constants",
        "",
        *table([r for r in deck_rows + readme_rows if r["status"] == "label/spec"]),
        "",
        "## Hand-written numbers (perturbation test)",
        "",
        "The deck was rebuilt from a copy of `facts.json` with every number changed. Numbers that did not change are "
        "written in `deck/build_deck.js` by hand. Apart from labels (tier, step and slide numbers, years, equation "
        "constants), these are the ones left, each checked against its source:",
        "",
        "| number | source checked |",
        "|---|---|",
        *[f"| {a} | {b} |" for a, b in REVIEWED],
        "",
        "## Hand-written text in deck/build_deck.js that contains digits",
        "",
        "| line | text |",
        "|---|---|",
        *[f"| {i} | {s.replace('|', '/')} |" for i, s in lits],
        "",
    ]
    out = ROOT / "docs" / "number_audit.md"
    out.write_text("\n".join(lines))
    print(f"deck: {len(deck_rows)} numbers, {n(deck_rows, 'UNMATCHED')} unmatched; README: {len(readme_rows)} numbers, "
          f"{n(readme_rows, 'UNMATCHED')} unmatched; builder literals with digits: {len(lits)} -> {out}")
    for r in bad:
        print(f"  UNMATCHED {r['where']}: {r['token']!r} in ...{r['context']}...")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
