#!/usr/bin/env python
"""
M0 — BTS pool candidate generator.

Downloads BTS Airline On-Time Performance data for a given month range from
transtats.bts.gov, loads the columns we care about into the
`bts_ontime_performance_raw` staging table, groups by (carrier, flight
number, origin, dest), and writes a CSV of "recurring" candidate flights —
sorted by sample size and delay stddev — for hand-curation into the final
~100-150 pool.

This script does NOT populate flight_definitions / flight_volatility_stats.
That's a separate step, once you've hand-picked the final list from the CSV
this produces.

Usage:
    python -m scripts.m0_bts_pool_candidates --start 2024-01 --end 2024-12
"""

import argparse
import calendar
import zipfile
from datetime import date, time as dt_time
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import text

from scripts.bts_staging_schema import bts_ontime_performance_raw
from src.db import engine

BTS_URL_TEMPLATE = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)

DEFAULT_CACHE_DIR = Path(__file__).parent / ".bts_cache"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output"

RAW_COLUMNS = [
    "Year",
    "Month",
    "DayofMonth",
    "DayOfWeek",
    "FlightDate",
    "IATA_CODE_Reporting_Airline",
    "Flight_Number_Reporting_Airline",
    "Origin",
    "Dest",
    "CRSDepTime",
    "CRSArrTime",
    "ArrDelay",
    "ArrDel15",
    "Cancelled",
    "Diverted",
    "Distance",
]

RENAME = {
    "Year": "year",
    "Month": "month",
    "DayofMonth": "day_of_month",
    "DayOfWeek": "day_of_week",
    "FlightDate": "flight_date",
    "IATA_CODE_Reporting_Airline": "carrier_code",
    "Flight_Number_Reporting_Airline": "flight_number",
    "Origin": "origin_airport",
    "Dest": "dest_airport",
    "CRSDepTime": "crs_dep_time_local",
    "CRSArrTime": "crs_arr_time_local",
    "ArrDelay": "arr_delay_minutes",
    "ArrDel15": "arr_del15",
    "Cancelled": "cancelled",
    "Diverted": "diverted",
    "Distance": "distance_miles",
}

STAGING_COLUMNS = [
    "year",
    "month",
    "day_of_month",
    "day_of_week",
    "flight_date",
    "carrier_code",
    "flight_number",
    "origin_airport",
    "dest_airport",
    "crs_dep_time_local",
    "crs_arr_time_local",
    "arr_delay_minutes",
    "arr_del15",
    "cancelled",
    "diverted",
    "distance_miles",
]


def month_range(start: str, end: str) -> list:
    start_y, start_m = (int(p) for p in start.split("-"))
    end_y, end_m = (int(p) for p in end.split("-"))
    if (start_y, start_m) > (end_y, end_m):
        raise ValueError(f"--start {start} is after --end {end}")
    months = []
    y, m = start_y, start_m
    while (y, m) <= (end_y, end_m):
        months.append((y, m))
        m += 1
        if m == 13:
            m = 1
            y += 1
    return months


def download_month(year: int, month: int, cache_dir: Path, force: bool) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f"bts_{year}_{month}.zip"
    if zip_path.exists() and not force:
        print(f"  [{year}-{month:02d}] using cached {zip_path.name}")
        return zip_path

    url = BTS_URL_TEMPLATE.format(year=year, month=month)
    print(f"  [{year}-{month:02d}] downloading {url}")
    resp = requests.get(url, timeout=180, stream=True)
    resp.raise_for_status()
    tmp_path = zip_path.with_suffix(".zip.partial")
    with open(tmp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    tmp_path.rename(zip_path)
    return zip_path


def parse_hhmm(raw):
    """BTS local times are HHMM (e.g. 856 -> 08:56), with 2400 used for midnight."""
    if raw is None or pd.isna(raw):
        return None
    hhmm = int(float(raw))
    if hhmm < 0 or hhmm > 2400:
        return None
    s = str(hhmm).zfill(4)
    hh, mm = int(s[:2]), int(s[2:])
    if hh == 24:
        hh = 0
    if hh > 23 or mm > 59:
        return None
    return dt_time(hour=hh, minute=mm)


def clean_chunk(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=RENAME)
    df["flight_date"] = pd.to_datetime(df["flight_date"]).dt.date
    df["carrier_code"] = df["carrier_code"].astype(str).str.strip()
    df["flight_number"] = df["flight_number"].astype("Int64").astype(str)
    df["origin_airport"] = df["origin_airport"].astype(str).str.strip()
    df["dest_airport"] = df["dest_airport"].astype(str).str.strip()
    df["crs_dep_time_local"] = df["crs_dep_time_local"].apply(parse_hhmm)
    df["crs_arr_time_local"] = df["crs_arr_time_local"].apply(parse_hhmm)
    df["cancelled"] = df["cancelled"].fillna(0).astype(float).astype(bool)
    df["diverted"] = df["diverted"].fillna(0).astype(float).astype(bool)
    # nullable: NaN for cancelled/diverted flights that never arrived
    df["arr_del15"] = df["arr_del15"].map({0: False, 1: True, 0.0: False, 1.0: True})
    return df[STAGING_COLUMNS]


def iter_raw_chunks(zip_path: Path, chunksize: int):
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError(f"no CSV member found in {zip_path}")
        with zf.open(csv_names[0]) as f:
            for chunk in pd.read_csv(f, usecols=RAW_COLUMNS, chunksize=chunksize, low_memory=False):
                yield clean_chunk(chunk)


def load_month(year: int, month: int, cache_dir: Path, force_download: bool, chunksize: int) -> int:
    zip_path = download_month(year, month, cache_dir, force_download)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM bts_ontime_performance_raw WHERE year = :y AND month = :m"),
            {"y": year, "m": month},
        )
    total = 0
    for chunk in iter_raw_chunks(zip_path, chunksize):
        chunk.to_sql(
            bts_ontime_performance_raw.name,
            engine,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=2000,
        )
        total += len(chunk)
    print(f"  [{year}-{month:02d}] loaded {total:,} rows")
    return total


AGGREGATE_SQL = text(
    """
    SELECT
        carrier_code,
        flight_number,
        origin_airport,
        dest_airport,
        count(*) AS total_ops,
        count(*) FILTER (WHERE NOT cancelled AND NOT diverted) AS sample_size,
        count(DISTINCT flight_date) AS distinct_days,
        count(*) FILTER (WHERE cancelled) AS cancelled_count,
        count(*) FILTER (WHERE diverted) AS diverted_count,
        100.0 * count(*) FILTER (WHERE NOT cancelled AND NOT diverted AND NOT arr_del15)
            / NULLIF(count(*) FILTER (WHERE NOT cancelled AND NOT diverted), 0) AS on_time_pct,
        avg(arr_delay_minutes) FILTER (WHERE NOT cancelled AND NOT diverted) AS avg_delay_minutes,
        stddev_samp(arr_delay_minutes) FILTER (WHERE NOT cancelled AND NOT diverted) AS delay_stddev,
        100.0 * count(*) FILTER (WHERE cancelled) / NULLIF(count(*), 0) AS cancellation_pct,
        100.0 * count(*) FILTER (WHERE diverted) / NULLIF(count(*), 0) AS diversion_pct,
        avg(distance_miles) AS avg_distance_miles,
        (percentile_cont(0.5) WITHIN GROUP (ORDER BY crs_dep_time_local))::time AS typical_dep_time_local,
        (percentile_cont(0.5) WITHIN GROUP (ORDER BY crs_arr_time_local))::time AS typical_arr_time_local
    FROM bts_ontime_performance_raw
    GROUP BY carrier_code, flight_number, origin_airport, dest_airport
    """
)

WEEKDAY_SQL = text(
    """
    SELECT carrier_code, flight_number, origin_airport, dest_airport,
           day_of_week, count(DISTINCT flight_date) AS days_operated
    FROM bts_ontime_performance_raw
    GROUP BY carrier_code, flight_number, origin_airport, dest_airport, day_of_week
    """
)


def weekday_occurrence_totals(months: list) -> dict:
    """How many Mondays/Tuesdays/etc fall within the requested month range."""
    totals = {i: 0 for i in range(1, 8)}
    for y, m in months:
        _, last_day = calendar.monthrange(y, m)
        for d in range(1, last_day + 1):
            totals[date(y, m, d).isoweekday()] += 1
    return totals


def build_candidates(months: list, min_days: int, weekday_threshold: float) -> pd.DataFrame:
    with engine.connect() as conn:
        agg_df = pd.read_sql(AGGREGATE_SQL, conn)
        weekday_df = pd.read_sql(WEEKDAY_SQL, conn)

    key_cols = ["carrier_code", "flight_number", "origin_airport", "dest_airport"]
    totals = weekday_occurrence_totals(months)

    weekday_df["ratio"] = weekday_df.apply(
        lambda r: r["days_operated"] / totals[r["day_of_week"]] if totals[r["day_of_week"]] else 0.0,
        axis=1,
    )
    active = weekday_df[weekday_df["ratio"] >= weekday_threshold]
    grouped = active.groupby(key_cols)
    pattern_info = grouped["day_of_week"].apply(lambda s: sorted(s.tolist())).rename("days_of_week_candidate")
    pattern_score = grouped["ratio"].mean().rename("weekday_consistency_score")

    df = agg_df.merge(pattern_info, on=key_cols, how="left").merge(pattern_score, on=key_cols, how="left")
    df["days_of_week_candidate"] = df["days_of_week_candidate"].apply(
        lambda v: v if isinstance(v, list) else []
    )
    df["weekday_consistency_score"] = df["weekday_consistency_score"].fillna(0.0)

    df["is_recurring_by_count"] = df["distinct_days"] >= min_days
    df["is_recurring_by_pattern"] = df["days_of_week_candidate"].apply(len) > 0
    df["is_recurring"] = df["is_recurring_by_count"] | df["is_recurring_by_pattern"]

    df["days_of_week_candidate"] = df["days_of_week_candidate"].apply(
        lambda v: ",".join(str(d) for d in v)
    )

    return df[df["is_recurring"]].copy()


OUTPUT_COLUMNS = [
    "carrier_code",
    "flight_number",
    "origin_airport",
    "dest_airport",
    "sample_size",
    "total_ops",
    "distinct_days",
    "on_time_pct",
    "avg_delay_minutes",
    "delay_stddev",
    "cancellation_pct",
    "diversion_pct",
    "avg_distance_miles",
    "typical_dep_time_local",
    "typical_arr_time_local",
    "days_of_week_candidate",
    "weekday_consistency_score",
    "is_recurring_by_count",
    "is_recurring_by_pattern",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help="first month, YYYY-MM")
    parser.add_argument("--end", required=True, help="last month, YYYY-MM (inclusive)")
    parser.add_argument("--min-days", type=int, default=200, help="distinct operating days to count as recurring (default 200)")
    parser.add_argument("--weekday-threshold", type=float, default=0.8, help="fraction of a weekday's occurrences a flight must operate on to count as a consistent weekday pattern (default 0.8)")
    parser.add_argument("--out", type=Path, default=None, help="output CSV path")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--force-download", action="store_true", help="re-download even if cached zip exists")
    parser.add_argument("--chunksize", type=int, default=100_000, help="CSV read chunk size")
    parser.add_argument("--skip-load", action="store_true", help="skip download/load, just re-run aggregation + CSV export against already-loaded data")
    args = parser.parse_args()

    months = month_range(args.start, args.end)
    print(f"Month range: {months[0][0]}-{months[0][1]:02d} .. {months[-1][0]}-{months[-1][1]:02d} ({len(months)} months)")

    if not args.skip_load:
        failures = []
        for y, m in months:
            try:
                load_month(y, m, args.cache_dir, args.force_download, args.chunksize)
            except Exception as exc:  # noqa: BLE001 - report and continue with remaining months
                print(f"  [{y}-{m:02d}] FAILED: {exc}")
                failures.append((y, m))
        if failures:
            print(f"WARNING: {len(failures)} month(s) failed to load: {failures}")

    print("Aggregating candidates...")
    candidates = build_candidates(months, args.min_days, args.weekday_threshold)
    candidates = candidates.sort_values(["sample_size", "delay_stddev"], ascending=[False, False])

    out_path = args.out
    if out_path is None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DEFAULT_OUTPUT_DIR / f"bts_pool_candidates_{args.start}_{args.end}.csv"
    candidates[OUTPUT_COLUMNS].to_csv(out_path, index=False)
    print(f"Wrote {len(candidates):,} recurring candidates to {out_path}")


if __name__ == "__main__":
    main()
