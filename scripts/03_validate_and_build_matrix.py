"""Validate the converted AMI Parquet and build a dense customer x hour load matrix.

Checks: customer count and ID coverage, duplicates across parts, nulls/negatives,
interval types, per-customer coverage, and presence of the labeled customers.

Outputs (data_processed/):
  load_matrix.npy        float32 [n_customers, 8760], NaN where no reading
  customer_index.parquet row order of the matrix + coverage stats + labels
  hour_index.parquet     column order of the matrix (usage_date, hour_ending, weather)

Matrix column = (usage_date - 2025-09-01) * 24 + (hour_ending - 1). The slot for the
spring-forward hour (2026-03-08 HE3) and the dropped fall-back hour (2025-11-02 HE2)
are always NaN.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data_processed"
AMI_GLOB = str(PROC / "ami" / "*.parquet")
LABELS = ROOT / "Data" / "EV_Heat Pump_Solar PV Customers.xlsx"
START = pd.Timestamp("2025-09-01")
N_DAYS = 365
N_HOURS = N_DAYS * 24


def main() -> None:
    con = duckdb.connect()
    con.execute(f"CREATE VIEW ami AS SELECT * FROM read_parquet('{AMI_GLOB}', filename=true)")

    print("== overall")
    print(con.sql("""
        SELECT count(*) AS rows, count(DISTINCT customer_id) AS customers,
               min(customer_id) AS min_id, max(customer_id) AS max_id,
               min(usage_date) AS first_day, max(usage_date) AS last_day,
               count(*) FILTER (WHERE kwh IS NULL) AS nulls,
               count(*) FILTER (WHERE kwh < 0) AS negatives,
               count(*) FILTER (WHERE kwh = 0) AS zeros,
               count(*) FILTER (WHERE NOT is_complete_hour) AS incomplete,
               max(kwh) AS max_kwh
        FROM ami""").df().T.to_string())
    print(con.sql("SELECT interval_type, interval_count, count(*) n FROM ami GROUP BY ALL").df().to_string())

    split = con.sql("""SELECT count(*) FROM (SELECT customer_id FROM ami GROUP BY 1
                       HAVING count(DISTINCT filename) > 1)""").fetchall()[0][0]
    dups = con.sql("""SELECT count(*) FROM (SELECT customer_id, usage_date, hour_ending FROM ami
                      GROUP BY ALL HAVING count(*) > 1)""").fetchall()[0][0]
    print(f"customers split across files: {split}; duplicate customer-hours: {dups}")

    cov = con.sql("""
        SELECT customer_id, count(*) AS n_hours, count(DISTINCT usage_date) AS n_days,
               min(usage_date) AS first_day, max(usage_date) AS last_day,
               sum(kwh) AS annual_kwh_observed, max(kwh) AS max_kwh,
               count(*) FILTER (WHERE kwh = 0) AS zero_hours
        FROM ami GROUP BY 1 ORDER BY 1""").df()
    ids = cov.customer_id.to_numpy()
    missing_ids = sorted(set(range(1, 35726)) - set(ids))
    print(f"IDs missing from 1..35725: {len(missing_ids)} {missing_ids[:10]}")
    print("hours per customer:")
    print(cov.n_hours.describe().round(0).to_string())
    print("coverage buckets (days):")
    print(pd.cut(cov.n_days, [0, 90, 180, 270, 330, 364, 365]).value_counts().sort_index().to_string())

    # labels
    xl = pd.ExcelFile(LABELS)
    for sheet, col in [("EV Customers", "label_ev"), ("HP Customers", "label_hp"), ("PV Customers", "label_pv")]:
        lab = set(xl.parse(sheet).CUSTOMER_NUMBER)
        cov[col] = cov.customer_id.isin(lab)
        print(f"{sheet}: {len(lab)} listed, {int(cov[col].sum())} found in AMI")
    print(f"overlaps EV&PV {int((cov.label_ev & cov.label_pv).sum())}, "
          f"HP&PV {int((cov.label_hp & cov.label_pv).sum())}, EV&HP {int((cov.label_ev & cov.label_hp).sum())}")

    # dense matrix
    row_of = pd.Series(np.arange(len(ids)), index=ids)
    mat = np.full((len(ids), N_HOURS), np.nan, dtype=np.float32)
    files = [r[0] for r in con.sql("SELECT DISTINCT filename FROM ami ORDER BY 1").fetchall()]
    for i, f in enumerate(files, 1):
        cid, slot, kwh = con.execute(f"""
            SELECT customer_id,
                   (date_diff('day', DATE '{START.date()}', usage_date) * 24 + hour_ending - 1)::INTEGER,
                   kwh
            FROM read_parquet('{f}')""").fetchnumpy().values()
        mat[row_of.loc[cid].to_numpy(), slot] = kwh
        if i % 50 == 0:
            print(f"  matrix: {i}/{len(files)} files loaded", flush=True)
    np.save(PROC / "load_matrix.npy", mat)
    nan_share = np.isnan(mat).mean()
    print(f"matrix {mat.shape}, {mat.nbytes / 1e9:.2f} GB, NaN share {nan_share:.4%}, "
          f"total observed {np.nansum(mat) / 1e6:.1f} GWh")

    cov.to_parquet(PROC / "customer_index.parquet", index=False)

    hours = pd.DataFrame({"slot": np.arange(N_HOURS)})
    hours["usage_date"] = START + pd.to_timedelta(hours.slot // 24, unit="D")
    hours["hour_ending"] = (hours.slot % 24 + 1).astype("int8")
    wx = pd.read_parquet(PROC / "weather_hourly.parquet")
    hours = hours.merge(wx, on=["usage_date", "hour_ending"], how="left")
    assert hours.temp.notna().all(), "weather missing for some matrix slots"
    hours.to_parquet(PROC / "hour_index.parquet", index=False)
    print("wrote load_matrix.npy, customer_index.parquet, hour_index.parquet")


if __name__ == "__main__":
    main()
