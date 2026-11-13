"""Data audit: inventory, schema, joins, quality and quick EDA signal checks.

Run after scripts 01-03 (it reads their outputs, which were verified row-for-row
against the raw zips). Writes docs/data_audit.md and prints the same report.

    python3 scripts/00_data_audit.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "Data"
PROC = ROOT / "data_processed"
OUT = ROOT / "docs" / "data_audit.md"

lines: list[str] = []


def emit(s: str = "") -> None:
    lines.append(s)
    print(s)


def table(df: pd.DataFrame, floatfmt: str = "{:,.3g}") -> None:
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].map(lambda v: "" if pd.isna(v) else floatfmt.format(v))
    cells = [list(map(str, df.columns))] + [[str(v) for v in r] for r in df.itertuples(index=False)]
    emit("| " + " | ".join(cells[0]) + " |")
    emit("|" + "---|" * len(df.columns))
    for r in cells[1:]:
        emit("| " + " | ".join(r) + " |")
    emit()


def mb(n: float) -> str:
    return f"{n / 1e6:,.1f} MB" if n < 1e9 else f"{n / 1e9:,.2f} GB"


# ---------------------------------------------------------------- 1. inventory
def inventory() -> None:
    emit("## 1. File inventory\n")
    rows = []
    for p in sorted(RAW.iterdir()):
        if p.suffix == ".zip":
            with zipfile.ZipFile(p) as zf:
                infos = [i for i in zf.infolist() if i.filename.endswith(".csv")]
            rows.append(dict(file=p.name, format="zip of CSV parts", on_disk=mb(p.stat().st_size),
                             uncompressed=mb(sum(i.file_size for i in infos)), parts=len(infos)))
        elif p.suffix == ".xlsx":
            xl = pd.ExcelFile(p)
            rows.append(dict(file=p.name, format=f"xlsx, sheets: {', '.join(xl.sheet_names)}",
                             on_disk=mb(p.stat().st_size), uncompressed="", parts=len(xl.sheet_names)))
    for p in sorted(ROOT.glob("*.pdf")):
        rows.append(dict(file=p.name, format="pdf (brief)", on_disk=mb(p.stat().st_size),
                         uncompressed="", parts=""))
    table(pd.DataFrame(rows))


# ---------------------------------------------------------------- 2. schemas
def schemas(con: duckdb.DuckDBPyConnection) -> None:
    emit("## 2. Schemas, ranges, granularity\n")
    emit("**AMI (raw CSV columns)**: `CUSTOMER_NUMBER, usage_date, hour_ending, day_type, "
         "hourly_kwh, interval_count, interval_type, is_complete_hour`. Stored as Parquet "
         "(`day_type` dropped, derivable from date).\n")
    per_zip = con.sql("""
        SELECT regexp_extract(filename, 'ami/(.*)__part', 1) AS zip_chunk,
               count(*) AS rows, count(DISTINCT customer_id) AS customers,
               min(customer_id) AS min_id, max(customer_id) AS max_id,
               min(usage_date)::DATE AS first_day, max(usage_date)::DATE AS last_day
        FROM ami GROUP BY 1 ORDER BY 1""").df()
    table(per_zip)
    emit("Granularity: one row per customer per hour (hour_ending 1-24, local clock). "
         "Source meters are 15-min (one customer is 5-min), pre-aggregated to hourly; "
         "`is_complete_hour` is 1 on every row.\n")

    wx = pd.read_excel(RAW / "Weather Data_Avangrid_Hackathon .xlsx")
    emit(f"**Weather**: {wx.shape[0]:,} rows x {wx.shape[1]} cols "
         f"`{', '.join(f'{c}:{t}' for c, t in wx.dtypes.astype(str).items())}`; "
         f"{wx['date'].min():%Y-%m-%d} to {wx['date'].max():%Y-%m-%d}, hourly, clock hours 0-23, "
         "single station (no location given). Missing: "
         + ", ".join(f"{c} {int(wx[c].isna().sum())}" for c in ["temp", "humidity", "windspeed"]) + ".\n")

    xl = pd.ExcelFile(RAW / "EV_Heat Pump_Solar PV Customers.xlsx")
    lab = {s: xl.parse(s) for s in ["EV Customers", "HP Customers", "PV Customers"]}
    emit("**Labels**: one column `CUSTOMER_NUMBER` (int) per sheet; "
         + ", ".join(f"{s} {len(d)} rows ({d.CUSTOMER_NUMBER.duplicated().sum()} dup)" for s, d in lab.items())
         + ". EV and HP are ~25% random samples of 'likely' owners; PV is the full list.\n")


# ---------------------------------------------------------------- 3. joins
def joins(cust: pd.DataFrame, hours: pd.DataFrame) -> None:
    emit("## 3. Join keys and match quality\n")
    xl = pd.ExcelFile(RAW / "EV_Heat Pump_Solar PV Customers.xlsx")
    ids = set(cust.customer_id)
    rows = []
    for s in ["EV Customers", "HP Customers", "PV Customers"]:
        l = set(xl.parse(s).CUSTOMER_NUMBER)
        rows.append(dict(join=f"AMI.customer_id = {s}.CUSTOMER_NUMBER", left=len(ids), right=len(l),
                         matched=len(l & ids), match_rate=len(l & ids) / len(l), unmatched=len(l - ids)))
    rows.append(dict(join="AMI(usage_date, hour_ending) = weather(date, time+1)", left=8760, right=8761,
                     matched=int(hours.temp.notna().sum()), match_rate=hours.temp.notna().mean(),
                     unmatched=0))
    table(pd.DataFrame(rows))
    emit("- No geography, feeder, transformer, premise type, tariff, or account dates exist. "
         "The only keys are customer ID (to labels) and timestamp (to weather). "
         "All customers share one weather series, so weather explains *when* load moves, "
         "and differences *between* customers come from their sensitivity to it.")
    emit("- Weather time alignment: hour_ending h covers clock [h-1, h); weather is joined as the "
         "mean of the h-1 and h readings. DST: AMI omits 2025-11-02 HE2 (fall-back duplicate dropped) "
         "and 2026-03-08 HE3 (spring-forward); weather duplicates 2025-11-02 01:00 (averaged).")
    emit(f"- Missing keys: {35725 - len(ids):,} of the 35,725 IDs in the brief have no AMI rows "
         "(checked against raw zip rows). Label rows for those IDs cannot be used.\n")


# ---------------------------------------------------------------- 4. quality
def quality(cust: pd.DataFrame, mat: np.ndarray, hours: pd.DataFrame) -> pd.DataFrame:
    emit("## 4. Data quality\n")
    n = len(cust)
    obs = ~np.isnan(mat)
    full_year = cust.n_days >= 364
    late_start = cust.first_day > pd.Timestamp("2025-09-01")
    early_end = cust.last_day < pd.Timestamp("2026-08-31")
    emit(f"- Coverage: {full_year.mean():.1%} of customers have >=364 days; "
         f"{late_start.sum():,} start late (move-in / new meter), {early_end.sum():,} end early "
         f"(move-out); {(cust.n_days < 180).sum()} have <180 days. Matrix NaN share {1 - obs.mean():.2%}.")

    annual = np.nanmean(mat, axis=1) * 8760
    peak = np.nanmax(mat, axis=1)
    zeros = (mat == 0).sum(axis=1)
    same_as_prev = (np.diff(mat, axis=1) == 0).sum(axis=1) / obs.sum(axis=1)
    feats = pd.DataFrame({"customer_id": cust.customer_id, "annual_kwh": annual, "peak_kwh": peak,
                          "zero_share": zeros / obs.sum(axis=1), "flat_share": same_as_prev})
    emit(f"- Annualised use: median {np.nanmedian(annual):,.0f} kWh, p5 {np.nanpercentile(annual, 5):,.0f}, "
         f"p95 {np.nanpercentile(annual, 95):,.0f}, max {np.nanmax(annual):,.0f}.")
    emit(f"- Outliers: {(peak > 25).sum()} customers peak above 25 kWh/h (max {peak.max():.1f}; "
         f"a 200 A / 240 V service tops out near 48 kW, typical homes peak at 5-12); "
         f"{(annual > 50_000).sum()} use >50 MWh/yr. Likely non-residential or multi-unit.")
    emit(f"- Near-vacant: {(annual < 1_000).sum()} customers <1,000 kWh/yr; "
         f"{(feats.zero_share > 0.5).sum()} have >50% zero hours.")
    emit(f"- Stuck / flat meters: {(feats.flat_share > 0.5).sum()} customers repeat the previous "
         "hour's exact value in >50% of hours.")
    emit(f"- Zeros: {(mat == 0).sum():,} zero-kWh hours overall; "
         f"{(mat[cust.label_pv.to_numpy()] == 0).sum() / max((mat == 0).sum(), 1):.0%} of them "
         f"belong to the {cust.label_pv.sum()} labeled PV customers (delivered-only metering).")
    emit(f"- Weather: {int(hours.imputed.sum())} imputed interval-hours (2026-03-08..03-11 gap), flagged.")
    emit(f"- Class balance (implied by the 25% sampling): EV ~{cust.label_ev.sum() / 0.25:.0f} "
         f"({cust.label_ev.sum() / 0.25 / n:.1%}), HP ~{cust.label_hp.sum() / 0.25:.0f} "
         f"({cust.label_hp.sum() / 0.25 / n:.1%}), PV {cust.label_pv.sum()} ({cust.label_pv.mean():.1%}). "
         "EV/HP unlabeled customers are a mix of negatives and ~75% of positives (PU setting).")
    lab_by_chunk = cust.assign(chunk=pd.cut(cust.customer_id, [0, 10000, 20000, 30000, 35725])) \
        .groupby("chunk", observed=True)[["label_ev", "label_hp", "label_pv"]].mean().mul(100).round(2)
    emit("- Label rate (%) by ID range / zip chunk (a skew would hint at sampling leakage):\n")
    table(lab_by_chunk.reset_index().astype({"chunk": str}))
    emit("- Leakage risks: (1) if the utility's 'likely EV/HP' lists were themselves produced "
         "from AMI heuristics, a model can score well by re-learning their rule; "
         "(2) any validation that lets features see the whole year cannot claim forecasting skill, "
         "so forecasting tests need a time split; (3) customer ID carries no meaning and is excluded.")
    emit("- PII: no names, addresses, or locations. Hourly load still reveals occupancy and routines, "
         "and the brief restricts use to this event, so raw rows must not leave the laptop "
         "(no raw data in git, no raw rows sent to hosted LLM APIs; aggregated outputs only).\n")
    return feats


# ---------------------------------------------------------------- 5. EDA
def eda(cust: pd.DataFrame, mat: np.ndarray, hours: pd.DataFrame, feats: pd.DataFrame) -> None:
    emit("## 5. Quick EDA signal checks\n")
    month = hours.usage_date.dt.month.to_numpy()
    he = hours.hour_ending.to_numpy()

    # system load
    sys_load = np.nansum(mat, axis=0) / 1000  # MWh per hour
    h = hours.assign(mwh=sys_load)
    top = h.nlargest(10, "mwh")[["usage_date", "hour_ending", "mwh", "temp"]]
    top["usage_date"] = top.usage_date.dt.date
    emit("**A. System peak** (sum of all customers, MWh/h). Top 10 hours:\n")
    table(top)
    mon = h.groupby(h.usage_date.dt.to_period("M")).agg(peak_mwh=("mwh", "max"), mean_mwh=("mwh", "mean"),
                                                         mean_temp=("temp", "mean")).reset_index()
    mon["usage_date"] = mon.usage_date.astype(str)
    table(mon)
    r = np.corrcoef(h.mwh, h.temp)[0, 1]
    emit(f"Hourly system load vs temperature: r = {r:.2f}.\n")

    # daily change-point-free regression: daily kWh ~ a + b*HDD + c*CDD
    n = mat.shape[0]
    daily = mat.reshape(n, 365, 24)
    cnt = (~np.isnan(daily)).sum(axis=2)
    dk = np.where(cnt >= 20, np.nanmean(daily, axis=2) * 24, np.nan)  # (n, 365)
    dt = hours.groupby(hours.slot // 24).temp.mean().to_numpy()
    X = np.column_stack([np.ones(365), np.clip(65 - dt, 0, None), np.clip(dt - 65, 0, None)])
    W = (~np.isnan(dk)).astype(float)
    Y = np.nan_to_num(dk)
    XtWX = np.einsum("nd,di,dj->nij", W, X, X)
    XtWy = np.einsum("nd,di,nd->ni", W, X, Y)
    ok = W.sum(axis=1) >= 60
    beta = np.full((n, 3), np.nan)
    beta[ok] = np.linalg.solve(XtWX[ok] + 1e-6 * np.eye(3), XtWy[ok][..., None])[..., 0]
    pred = beta @ X.T
    ss_res = np.nansum(W * (Y - pred) ** 2, axis=1)
    ybar = np.nansum(W * Y, axis=1) / W.sum(axis=1)
    ss_tot = np.nansum(W * (Y - ybar[:, None]) ** 2, axis=1)
    feats["base_kwh_day"], feats["heat_slope"], feats["cool_slope"] = beta.T
    feats["r2_weather"] = 1 - ss_res / ss_tot
    emit("**B. Weather sensitivity** (per-customer OLS: daily kWh = a + b*HDD65 + c*CDD65):\n")
    q = feats[["base_kwh_day", "heat_slope", "cool_slope", "r2_weather"]].describe(
        percentiles=[.1, .5, .9]).T[["10%", "50%", "90%"]].reset_index()
    table(q)
    emit(f"{(feats.heat_slope > 1.0).mean():.1%} of customers add >1 kWh/day per HDD "
         f"(strong electric heating signal); {(feats.cool_slope > 1.0).mean():.1%} add >1 kWh/day per CDD.\n")

    # candidate signatures vs labels
    shoulder = np.isin(month, [4, 5, 10])
    night = (he >= 20) | (he <= 6)
    sub = mat[:, shoulder & night]
    med = np.nanmedian(mat[:, shoulder], axis=1, keepdims=True)
    feats["ev_jump_hours"] = ((sub - med) >= 4.0).sum(axis=1) / (~np.isnan(sub)).sum(axis=1)
    midday = (he >= 11) & (he <= 15) & np.isin(month, [4, 5, 6, 7, 8])
    feats["pv_midday_zero"] = (mat[:, midday] == 0).mean(axis=1)
    winter = np.isin(month, [12, 1, 2])
    summer = np.isin(month, [6, 7, 8])
    feats["winter_summer_ratio"] = np.nanmean(mat[:, winter], axis=1) / np.nanmean(mat[:, summer], axis=1)
    cold = hours.temp.to_numpy() < 10
    feats["cold_snap_kwh"] = np.nanmean(mat[:, cold], axis=1)

    emit("**C. Do simple signatures separate the labeled customers?** "
         "ROC AUC of one feature vs label (unlabeled includes hidden positives, so these are "
         "conservative):\n")
    checks = [("EV", "label_ev", "ev_jump_hours", "share of shoulder-month night hours >=4 kWh above median"),
              ("EV", "label_ev", "peak_kwh", "annual peak hour"),
              ("HP", "label_hp", "heat_slope", "kWh/day per HDD"),
              ("HP", "label_hp", "cool_slope", "kWh/day per CDD"),
              ("HP", "label_hp", "winter_summer_ratio", "winter / summer mean load"),
              ("HP", "label_hp", "cold_snap_kwh", "mean load when T < 10F"),
              ("PV", "label_pv", "pv_midday_zero", "share of Apr-Aug 11-15h hours at 0 kWh"),
              ("PV", "label_pv", "annual_kwh", "annual kWh")]
    rows = []
    for tag, lab, f, desc in checks:
        y = cust[lab].to_numpy()
        x = feats[f].to_numpy()
        m = np.isfinite(x)
        rows.append(dict(label=tag, feature=f, meaning=desc, auc=roc_auc_score(y[m], x[m]),
                         median_labeled=np.nanmedian(x[y]), median_other=np.nanmedian(x[~y])))
    table(pd.DataFrame(rows))

    pv_flag = feats.pv_midday_zero > 0.2
    y = cust.label_pv.to_numpy()
    tp, fp, fn = (pv_flag & y).sum(), (pv_flag & ~y).sum(), (~pv_flag & y).sum()
    emit(f"PV rule `midday zero share > 0.2`: precision {tp / (tp + fp):.1%}, recall {tp / (tp + fn):.1%} "
         f"({fp} unlabeled customers flagged).")
    night_s = (he >= 22) | (he <= 4)
    feats["night_zero"] = (mat[:, night_s & np.isin(month, [4, 5, 6, 7, 8])] == 0).mean(axis=1)
    pv2 = pv_flag & (feats.night_zero < 0.05)
    tp, fp, fn = (pv2 & y).sum(), (pv2 & ~y).sum(), (~pv2 & y).sum()
    vac = pv_flag & ~y & (feats.night_zero > 0.5)
    emit(f"Adding `night zero share < 0.05` (zeros at midday only): precision {tp / (tp + fp):.1%}, "
         f"recall {tp / (tp + fn):.1%}; {fp} unlabeled solar-like customers remain (possible unregistered PV). "
         f"Of the first rule's false positives, {vac.sum()} are zero at night too (vacant / disconnected).\n")

    feats.to_parquet(PROC / "audit_features.parquet", index=False)


def main() -> None:
    con = duckdb.connect()
    con.execute(f"CREATE VIEW ami AS SELECT * FROM read_parquet('{PROC / 'ami' / '*.parquet'}', filename=true)")
    cust = pd.read_parquet(PROC / "customer_index.parquet")
    hours = pd.read_parquet(PROC / "hour_index.parquet")
    mat = np.load(PROC / "load_matrix.npy", mmap_mode="r")
    mat = np.asarray(mat)

    emit("# Data audit\n")
    emit("_Generated by `scripts/00_data_audit.py`._\n")
    inventory()
    schemas(con)
    joins(cust, hours)
    feats = quality(cust, mat, hours)
    eda(cust, mat, hours, feats)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
