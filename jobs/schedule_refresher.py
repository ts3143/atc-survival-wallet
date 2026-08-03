#!/usr/bin/env python
"""
M1 — Schedule Refresher (daily default population, zero external API calls).

For every active flight_definitions row, ensures a flight_instances row
exists for "today" with scheduled_dep_utc/scheduled_arr_utc derived from
typical_dep_time/typical_arr_time (BTS-sourced local times), converted to
UTC via each airport's own timezone (see src/lib/airport_timezones.py).
"Today" is computed per-flight in the *origin airport's* local timezone,
not a single global date — a flight out of LAX and a flight out of JFK can
be on different calendar dates around midnight UTC.

This job makes zero external API calls. See schedule_verifier.py for the
separate, lower-frequency AeroDataBox-backed spot-check that corrects drift
in flight_definitions' typical times.

Idempotent / cache-aware: if today's flight_instances row for a flight
already has scheduled_dep_utc populated — whether from a previous run of
this job today, or because schedule_verifier already wrote an
AeroDataBox-confirmed value for it today — this job leaves it alone.

Usage (manual or cron):
    python -m jobs.schedule_refresher
    python -m jobs.schedule_refresher --date 2026-08-10   # backfill/testing
"""

import argparse
from datetime import date, datetime, timedelta

from sqlalchemy import select

from src.db import SessionLocal
from src.lib.airport_timezones import get_timezone, local_time_to_utc
from src.models.flight_definitions import FlightDefinition
from src.models.flight_instances import FlightInstance


def local_today(iata_code: str) -> date:
    return datetime.now(get_timezone(iata_code)).date()


def instance_exists_with_schedule(session, flight_definition_id, flight_date) -> bool:
    row = session.execute(
        select(FlightInstance.id).where(
            FlightInstance.flight_definition_id == flight_definition_id,
            FlightInstance.flight_date == flight_date,
            FlightInstance.scheduled_dep_utc.isnot(None),
        )
    ).first()
    return row is not None


def derive_scheduled_times(fd: FlightDefinition, flight_date: date):
    dep_utc = local_time_to_utc(flight_date, fd.typical_dep_time, fd.origin_airport)

    # Naive overnight/red-eye heuristic: if the local arrival clock-time is
    # earlier than the local departure clock-time, treat it as landing the
    # next calendar day at the destination. No US domestic route (even
    # JFK->HNL) is long/timezone-shifted enough to wrap more than one day,
    # so this holds for our pool, but isn't a general-purpose rule.
    arr_local_date = flight_date
    if fd.typical_arr_time < fd.typical_dep_time:
        arr_local_date = flight_date + timedelta(days=1)

    arr_utc = local_time_to_utc(arr_local_date, fd.typical_arr_time, fd.dest_airport)
    return dep_utc, arr_utc


def ensure_instance_for_today(session, fd: FlightDefinition, flight_date: date) -> str:
    """Returns 'created', 'updated', or 'skipped'."""
    if instance_exists_with_schedule(session, fd.id, flight_date):
        return "skipped"

    dep_utc, arr_utc = derive_scheduled_times(fd, flight_date)

    existing = session.execute(
        select(FlightInstance).where(
            FlightInstance.flight_definition_id == fd.id,
            FlightInstance.flight_date == flight_date,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.scheduled_dep_utc = dep_utc
        existing.scheduled_arr_utc = arr_utc
        return "updated"

    session.add(
        FlightInstance(
            flight_definition_id=fd.id,
            flight_date=flight_date,
            scheduled_dep_utc=dep_utc,
            scheduled_arr_utc=arr_utc,
            status="scheduled",
        )
    )
    return "created"


def run(date_override: date = None) -> dict:
    counts = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}

    with SessionLocal() as session:
        flight_defs = (
            session.execute(select(FlightDefinition).where(FlightDefinition.active.is_(True)))
            .scalars()
            .all()
        )

        for fd in flight_defs:
            flight_date = date_override or local_today(fd.origin_airport)
            try:
                outcome = ensure_instance_for_today(session, fd, flight_date)
                counts[outcome] += 1
            except Exception as exc:  # noqa: BLE001 - log per-flight, keep going
                print(
                    f"  [{fd.carrier_code}{fd.flight_number} "
                    f"{fd.origin_airport}->{fd.dest_airport}] ERROR: {exc}"
                )
                counts["errors"] += 1

        session.commit()

    return counts


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="flight date to populate for ALL flights, overriding per-origin local "
        "'today' (YYYY-MM-DD) — mainly for backfill/testing",
    )
    args = parser.parse_args()

    print(f"Schedule refresher — date override: {args.date or '(per-origin local today)'}")
    counts = run(args.date)
    print(
        f"Done. created={counts['created']} updated={counts['updated']} "
        f"skipped={counts['skipped']} errors={counts['errors']}"
    )


if __name__ == "__main__":
    main()
