#!/usr/bin/env python
"""
One-off recompute: fixes flight_volatility_stats' on_time_pct/
avg_delay_minutes/delay_stddev to be WHEELS-based (wheels_on_delay_minutes/
wheels_on_del15 — see scripts/m0_bts_pool_candidates.py's
wheels_delay_minutes_series()) instead of BTS's own gate-based ArrDelay/
ArrDel15, for the currently-active flight_definitions only.

Why: our OpenSky-based live tracking (jobs/opensky_matcher.py) can only
ever observe wheels events (airborne/on_ground transitions via ADS-B) —
it has no visibility into gate pushback or jet-bridge docking. The
original M0 pipeline computed these three stats from BTS's ArrDelay,
which BTS's own field dictionary confirms is gate-based (ArrTime -
CRSArrTime). That's a real definitional mismatch against what we actually
observe live.

Does NOT touch: flight_definitions (pool membership, active flags,
typical_dep_time/typical_arr_time, days_of_week) or
cancellation_pct/diversion_pct (unaffected by gate-vs-wheels — cancelled/
diverted is cancelled/diverted regardless of which clock you're reading).
sample_size is refreshed too (same population/filter as before, so it
should come out the same — a mismatch there would itself be worth
investigating).

Re-loads the same BTS raw data window the curated pool came from
(2025-05 through 2026-04 by default) via
scripts/m0_bts_pool_candidates.py's own load_month(), now pulling
WheelsOff/WheelsOn too — reuses whatever's already cached in
scripts/.bts_cache/, no re-download needed for months already pulled.

Usage:
    python -m scripts.m0_recompute_wheels_based_stats
    python -m scripts.m0_recompute_wheels_based_stats --start 2025-05 --end 2026-04
"""

import argparse
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from scripts.m0_bts_pool_candidates import AGGREGATE_SQL, DEFAULT_CACHE_DIR, load_month, month_range
from src.db import SessionLocal, engine
from src.models.flight_definitions import FlightDefinition
from src.models.flight_volatility_stats import FlightVolatilityStats


def to_decimal(value, places="0.01"):
    if value is None or pd.isna(value):
        return None
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2025-05", help="first month, YYYY-MM (default 2025-05, matching curated_pool_v5_final.csv)")
    parser.add_argument("--end", default="2026-04", help="last month, YYYY-MM inclusive (default 2026-04)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--chunksize", type=int, default=100_000)
    parser.add_argument(
        "--skip-load",
        action="store_true",
        help="skip re-loading raw BTS data, just re-run aggregation against what's already staged "
        "(only valid if bts_ontime_performance_raw already has wheels columns populated)",
    )
    args = parser.parse_args()

    months = month_range(args.start, args.end)
    print(f"Month range: {months[0][0]}-{months[0][1]:02d} .. {months[-1][0]}-{months[-1][1]:02d} ({len(months)} months)")

    if not args.skip_load:
        failures = []
        for y, m in months:
            try:
                load_month(y, m, args.cache_dir, force_download=False, chunksize=args.chunksize)
            except Exception as exc:  # noqa: BLE001 - report and continue with remaining months
                print(f"  [{y}-{m:02d}] FAILED: {exc}")
                failures.append((y, m))
        if failures:
            print(f"WARNING: {len(failures)} month(s) failed to reload: {failures}")

    print("Aggregating wheels-based stats...")
    with engine.connect() as conn:
        agg_df = pd.read_sql(AGGREGATE_SQL, conn)
    key_cols = ["carrier_code", "flight_number", "origin_airport", "dest_airport"]
    agg_df = agg_df.set_index(key_cols)

    updated = []
    sample_size_mismatches = []
    missing = []

    with SessionLocal() as session:
        active_flights = (
            session.execute(select(FlightDefinition).where(FlightDefinition.active.is_(True)))
            .scalars()
            .all()
        )

        for fd in active_flights:
            key = (fd.carrier_code, fd.flight_number, fd.origin_airport, fd.dest_airport)
            if key not in agg_df.index:
                missing.append(key)
                continue
            row = agg_df.loc[key]

            fvs = session.execute(
                select(FlightVolatilityStats).where(FlightVolatilityStats.flight_definition_id == fd.id)
            ).scalar_one()

            before = {
                "on_time_pct": fvs.on_time_pct,
                "avg_delay_minutes": fvs.avg_delay_minutes,
                "delay_stddev": fvs.delay_stddev,
                "sample_size": fvs.sample_size,
            }

            new_sample_size = int(row["sample_size"])
            if new_sample_size != fvs.sample_size:
                sample_size_mismatches.append((f"{fd.carrier_code}{fd.flight_number}", fvs.sample_size, new_sample_size))

            fvs.on_time_pct = to_decimal(row["on_time_pct"])
            fvs.avg_delay_minutes = to_decimal(row["avg_delay_minutes"])
            fvs.delay_stddev = to_decimal(row["delay_stddev"])
            fvs.sample_size = new_sample_size

            updated.append(
                (
                    f"{fd.carrier_code}{fd.flight_number} {fd.origin_airport}->{fd.dest_airport}",
                    before,
                    {
                        "on_time_pct": fvs.on_time_pct,
                        "avg_delay_minutes": fvs.avg_delay_minutes,
                        "delay_stddev": fvs.delay_stddev,
                        "sample_size": fvs.sample_size,
                    },
                )
            )

        session.commit()

    print(f"\nUpdated {len(updated)} flight_volatility_stats rows (of {len(active_flights)} active flights)")
    if missing:
        print(f"WARNING: {len(missing)} active flight(s) had no matching row in this BTS pull: {missing}")
    if sample_size_mismatches:
        print(f"NOTE: sample_size changed for {len(sample_size_mismatches)} flight(s) (wheels data availability differs slightly from gate data availability):")
        for label, old, new in sample_size_mismatches[:10]:
            print(f"  {label}: {old} -> {new}")

    print("\nSample of before -> after:")
    for label, before, after in updated[:15]:
        print(
            f"  {label}: on_time {before['on_time_pct']}->{after['on_time_pct']}, "
            f"avg_delay {before['avg_delay_minutes']}->{after['avg_delay_minutes']}, "
            f"stddev {before['delay_stddev']}->{after['delay_stddev']}"
        )


if __name__ == "__main__":
    main()
