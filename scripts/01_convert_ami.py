"""Convert the zipped hourly AMI CSVs to compressed Parquet, one file per CSV part.

The raw data is ~14.4 GB of CSV inside four zips, more than fits on disk unzipped,
so each CSV member is extracted to a temp dir, converted with DuckDB, then deleted.
The run is resumable: parts whose Parquet already exists are skipped.

Output: data_processed/ami/<zip-stem>__<part>.parquet plus data_processed/ami_manifest.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import tempfile
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "Data"
OUT_DIR = ROOT / "data_processed" / "ami"
MANIFEST = ROOT / "data_processed" / "ami_manifest.csv"

# day_type is dropped: it is derivable from usage_date.
SELECT_SQL = """
SELECT
    CAST(CUSTOMER_NUMBER AS INTEGER)  AS customer_id,
    CAST(usage_date AS DATE)          AS usage_date,
    CAST(hour_ending AS TINYINT)      AS hour_ending,
    CAST(hourly_kwh AS FLOAT)         AS kwh,
    CAST(interval_count AS TINYINT)   AS interval_count,
    interval_type,
    CAST(is_complete_hour AS BOOLEAN) AS is_complete_hour
FROM read_csv('{src}', header=true, columns={{
    'CUSTOMER_NUMBER': 'BIGINT', 'usage_date': 'DATE', 'hour_ending': 'INTEGER',
    'day_type': 'VARCHAR', 'hourly_kwh': 'DOUBLE', 'interval_count': 'INTEGER',
    'interval_type': 'VARCHAR', 'is_complete_hour': 'INTEGER'}})
ORDER BY customer_id, usage_date, hour_ending
"""

STATS_SQL = """
SELECT count(*) AS rows, count(DISTINCT customer_id) AS customers,
       min(customer_id) AS min_customer, max(customer_id) AS max_customer,
       min(usage_date) AS min_date, max(usage_date) AS max_date,
       sum(CASE WHEN kwh IS NULL THEN 1 ELSE 0 END) AS null_kwh,
       sum(CASE WHEN kwh < 0 THEN 1 ELSE 0 END) AS negative_kwh,
       sum(CASE WHEN NOT is_complete_hour THEN 1 ELSE 0 END) AS incomplete_hours,
       string_agg(DISTINCT interval_type, '|') AS interval_types
FROM read_parquet('{dst}')
"""


def convert_member(zip_path: str, member: str, out_path: str, tmp_root: str, threads: int) -> dict:
    t0 = time.time()
    tmp_dir = tempfile.mkdtemp(dir=tmp_root)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            src = zf.extract(member, tmp_dir)
        partial = out_path + ".partial"
        con = duckdb.connect()
        con.execute(f"SET threads={threads}")
        con.execute(
            f"COPY ({SELECT_SQL.format(src=src)}) TO '{partial}' "
            "(FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 500000)"
        )
        os.replace(partial, out_path)
        stats = con.execute(STATS_SQL.format(dst=out_path)).fetchdf().iloc[0].to_dict()
        con.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    stats.update(zip=Path(zip_path).name, member=member, parquet=Path(out_path).name,
                 seconds=round(time.time() - t0, 1))
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None, help="convert only N parts (testing)")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_root = tempfile.mkdtemp(prefix="ami_extract_")
    threads = max(1, (os.cpu_count() or 4) // args.workers)

    jobs = []
    for zip_path in sorted(RAW_DIR.glob("*.zip")):
        stem = zip_path.stem.replace("2026 Avangrid Hackathon Data_", "").replace(" ", "_")
        with zipfile.ZipFile(zip_path) as zf:
            members = sorted(m for m in zf.namelist() if m.endswith(".csv"))
        for m in members:
            part = m.split("-")[1]  # part-00013-<uuid>-c000.csv -> 00013
            out = OUT_DIR / f"{stem}__part{part}.parquet"
            if not out.exists():
                jobs.append((str(zip_path), m, str(out)))
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"{len(jobs)} parts to convert, {args.workers} workers x {threads} threads", flush=True)

    results = []
    t0 = time.time()
    try:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(convert_member, z, m, o, tmp_root, threads) for z, m, o in jobs]
            for i, f in enumerate(as_completed(futs), 1):
                r = f.result()
                results.append(r)
                print(f"[{i}/{len(jobs)}] {r['parquet']}: {r['rows']:,} rows, "
                      f"{r['customers']} customers, {r['seconds']}s "
                      f"(elapsed {time.time() - t0:.0f}s)", flush=True)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    if results:
        new = not MANIFEST.exists()
        with open(MANIFEST, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
            if new:
                w.writeheader()
            w.writerows(results)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
