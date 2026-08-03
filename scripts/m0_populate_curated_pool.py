#!/usr/bin/env python
"""
M0 — populate flight_definitions + flight_volatility_stats from a
hand-curated pool CSV (see scripts/m0_bts_pool_candidates.py for how the
candidate list this was picked from was generated).

Idempotent: upserts by natural key rather than inserting duplicates, so it's
safe to re-run against a refreshed CSV — flight_definitions on
(carrier_code, flight_number, origin_airport, dest_airport), and
flight_volatility_stats on flight_definition_id (one row per definition, per
spec section 2). Existing rows for flights still present in the CSV get their
values (including computed_at) refreshed and are set active=true.

Any flight_definitions row whose natural key is NOT in the input CSV gets
soft-deactivated (active=false) — never deleted or hard-removed, since
flight_instances / wallet_picks may reference it historically. Re-running
with that flight added back to the CSV reactivates it (active=true) and
refreshes its volatility stats.

Usage:
    python -m scripts.m0_populate_curated_pool scripts/output/curated_pool_v2_final.csv
"""

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db import SessionLocal
from src.models.flight_definitions import FlightDefinition
from src.models.flight_volatility_stats import FlightVolatilityStats

REQUIRED_COLUMNS = [
    "carrier_code",
    "flight_number",
    "origin_airport",
    "dest_airport",
    "sample_size",
    "on_time_pct",
    "avg_delay_minutes",
    "delay_stddev",
    "cancellation_pct",
    "diversion_pct",
    "distance_bucket",
    "typical_dep_time_local",
    "typical_arr_time_local",
    "days_of_week_candidate",
]

NATURAL_KEY = ["carrier_code", "flight_number", "origin_airport", "dest_airport"]


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)

    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")

    if df[REQUIRED_COLUMNS].isnull().any().any():
        bad = df[df[REQUIRED_COLUMNS].isnull().any(axis=1)]
        raise ValueError(f"CSV has null values in required columns:\n{bad}")

    dupes = df.duplicated(subset=NATURAL_KEY, keep=False)
    if dupes.any():
        raise ValueError(f"CSV has duplicate {NATURAL_KEY} rows:\n{df[dupes]}")

    bad_bucket = ~df["distance_bucket"].isin(["short", "medium", "long"])
    if bad_bucket.any():
        raise ValueError(f"CSV has invalid distance_bucket values:\n{df[bad_bucket]}")

    return df


def parse_days_of_week(raw: str) -> list:
    return [int(d) for d in str(raw).split(",") if d.strip()]


def parse_time(raw: str):
    return datetime.strptime(str(raw).strip(), "%H:%M:%S").time()


def upsert_row(session, row) -> None:
    fd_stmt = pg_insert(FlightDefinition).values(
        carrier_code=row["carrier_code"],
        flight_number=str(row["flight_number"]),
        origin_airport=row["origin_airport"],
        dest_airport=row["dest_airport"],
        days_of_week=parse_days_of_week(row["days_of_week_candidate"]),
        typical_dep_time=parse_time(row["typical_dep_time_local"]),
        typical_arr_time=parse_time(row["typical_arr_time_local"]),
        distance_bucket=row["distance_bucket"],
        active=True,
    )
    fd_stmt = fd_stmt.on_conflict_do_update(
        constraint="uq_flight_definitions_carrier_flight_route",
        set_={
            "days_of_week": fd_stmt.excluded.days_of_week,
            "typical_dep_time": fd_stmt.excluded.typical_dep_time,
            "typical_arr_time": fd_stmt.excluded.typical_arr_time,
            "distance_bucket": fd_stmt.excluded.distance_bucket,
            "active": fd_stmt.excluded.active,
        },
    ).returning(FlightDefinition.id)
    flight_definition_id = session.execute(fd_stmt).scalar_one()

    fvs_stmt = pg_insert(FlightVolatilityStats).values(
        flight_definition_id=flight_definition_id,
        on_time_pct=row["on_time_pct"],
        avg_delay_minutes=row["avg_delay_minutes"],
        delay_stddev=row["delay_stddev"],
        cancellation_pct=row["cancellation_pct"],
        diversion_pct=row["diversion_pct"],
        sample_size=int(row["sample_size"]),
        computed_at=func.now(),
    )
    fvs_stmt = fvs_stmt.on_conflict_do_update(
        constraint="uq_flight_volatility_stats_flight_definition_id",
        set_={
            "on_time_pct": fvs_stmt.excluded.on_time_pct,
            "avg_delay_minutes": fvs_stmt.excluded.avg_delay_minutes,
            "delay_stddev": fvs_stmt.excluded.delay_stddev,
            "cancellation_pct": fvs_stmt.excluded.cancellation_pct,
            "diversion_pct": fvs_stmt.excluded.diversion_pct,
            "sample_size": fvs_stmt.excluded.sample_size,
            "computed_at": func.now(),
        },
    )
    session.execute(fvs_stmt)


def deactivate_missing(session, present_keys: set) -> int:
    """Soft-deactivate any currently-active flight_definitions row whose
    natural key isn't in present_keys. Never deletes."""
    active_rows = session.execute(
        select(
            FlightDefinition.id,
            FlightDefinition.carrier_code,
            FlightDefinition.flight_number,
            FlightDefinition.origin_airport,
            FlightDefinition.dest_airport,
        ).where(FlightDefinition.active.is_(True))
    ).all()

    ids_to_deactivate = [
        row.id
        for row in active_rows
        if (row.carrier_code, row.flight_number, row.origin_airport, row.dest_airport) not in present_keys
    ]

    if ids_to_deactivate:
        session.execute(
            update(FlightDefinition)
            .where(FlightDefinition.id.in_(ids_to_deactivate))
            .values(active=False)
        )

    return len(ids_to_deactivate)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()

    df = load_csv(args.csv_path)
    print(f"Loaded {len(df)} rows from {args.csv_path}")

    present_keys = set(
        zip(
            df["carrier_code"].astype(str),
            df["flight_number"].astype(str),
            df["origin_airport"].astype(str),
            df["dest_airport"].astype(str),
        )
    )

    with SessionLocal() as session:
        for _, row in df.iterrows():
            upsert_row(session, row)
        deactivated = deactivate_missing(session, present_keys)
        session.commit()

    print(f"Upserted {len(df)} flight_definitions + flight_volatility_stats rows.")
    print(f"Soft-deactivated {deactivated} flight_definitions no longer in the pool.")


if __name__ == "__main__":
    main()
