#!/usr/bin/env python
"""
M1 — Schedule Verifier (low-frequency AeroDataBox spot-check).

Separate from schedule_refresher.py (which populates flight_instances daily
from flight_definitions.typical_dep_time/typical_arr_time with zero API
calls). This job cycles through the active flight_definitions pool over
~ROTATION_DAYS days (~9 flights/day for a 123-flight pool), and for each
flight selected today:

  1. Calls AeroDataBox to confirm today's actual scheduled departure/arrival
     for that flight.
  2. Compares the confirmed time to flight_definitions.typical_dep_time /
     typical_arr_time. If off by more than DISCREPANCY_THRESHOLD_MINUTES,
     logs a discrepancy and corrects flight_definitions (also bumps
     last_verified_at either way).
  3. Upserts today's flight_instances row with the AeroDataBox-confirmed
     time (more authoritative than the BTS-derived default) — this is what
     makes the "skip if already populated" cache check on the OTHER job
     meaningful: once this job has written a verified value for today,
     schedule_refresher leaves it alone rather than overwriting it with the
     derived value.

Caching / idempotency: a flight is skipped (no API call) if it was already
*successfully verified* today — tracked via aerodatabox_call_log, not via
flight_instances. flight_instances.scheduled_dep_utc is populated for every
active flight every day by schedule_refresher regardless of whether it's
this flight's turn in the rotation, so gating on "does flight_instances
already have a scheduled time" would make this job a permanent no-op (every
flight always has one by the time this runs). aerodatabox_call_log is only
ever written by this job, so it's the thing that actually tracks "have we
verified this flight today."

Every call (successful or not) is logged to aerodatabox_call_log for
budget auditing (600 req/month free-tier). A per-run warning (not a hard
fail) is printed if the current calendar month's call count is approaching
or has passed that budget.

Rate limiting (HTTP 429) stops the run early (further calls would fail
too). Any other per-flight failure (network error, non-2xx/204 response)
is logged and the run continues with the next flight.

Usage (manual or cron):
    python -m jobs.schedule_verifier
    python -m jobs.schedule_verifier --date 2026-08-10   # backfill/testing
"""

import argparse
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from jobs.schema import aerodatabox_call_log, job_cursors
from src.db import SessionLocal
from src.lib.aerodatabox import (
    AeroDataBoxError,
    AeroDataBoxRateLimited,
    get_flight_schedule,
)
from src.lib.airport_timezones import get_timezone, local_time_to_utc
from src.models.flight_definitions import FlightDefinition
from src.models.flight_instances import FlightInstance

ROTATION_DAYS = 14
JOB_NAME = "schedule_verifier"
DISCREPANCY_THRESHOLD_MINUTES = 10
MONTHLY_BUDGET = 600
BUDGET_WARN_PCT = 0.9
ENDPOINT_TEMPLATE = "GET /flights/number/{flight_number}/{date_local}"


def local_today(iata_code: str) -> date:
    return datetime.now(get_timezone(iata_code)).date()


def parse_iso_utc(raw):
    if not raw:
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_monthly_budget(session) -> int:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count = session.execute(
        select(func.count())
        .select_from(aerodatabox_call_log)
        .where(aerodatabox_call_log.c.requested_at >= month_start)
    ).scalar_one()

    if count >= MONTHLY_BUDGET:
        print(
            f"[WARN] AeroDataBox calls this month ({count}) have reached/exceeded the "
            f"{MONTHLY_BUDGET}/month free-tier budget. Continuing anyway (not a hard fail) — "
            f"expect RapidAPI to start returning 429s."
        )
    elif count >= MONTHLY_BUDGET * BUDGET_WARN_PCT:
        print(
            f"[WARN] AeroDataBox calls this month ({count}) are approaching the "
            f"{MONTHLY_BUDGET}/month free-tier budget ({BUDGET_WARN_PCT:.0%} threshold)."
        )
    return count


def get_and_advance_cursor(session) -> int:
    row = session.execute(
        select(job_cursors.c.cursor).where(job_cursors.c.job_name == JOB_NAME)
    ).first()

    if row is None:
        current = 0
        session.execute(pg_insert(job_cursors).values(job_name=JOB_NAME, cursor=0))
    else:
        current = row.cursor

    next_cursor = (current + 1) % ROTATION_DAYS
    session.execute(
        pg_insert(job_cursors)
        .values(job_name=JOB_NAME, cursor=next_cursor)
        .on_conflict_do_update(index_elements=["job_name"], set_={"cursor": next_cursor, "updated_at": func.now()})
    )
    return current


def select_todays_flights(session, cursor: int):
    flight_defs = (
        session.execute(
            select(FlightDefinition)
            .where(FlightDefinition.active.is_(True))
            .order_by(FlightDefinition.id)
        )
        .scalars()
        .all()
    )
    return [fd for i, fd in enumerate(flight_defs) if i % ROTATION_DAYS == cursor]


def already_verified_today(session, fd: FlightDefinition, flight_date: date) -> bool:
    match = session.execute(
        select(aerodatabox_call_log.c.id)
        .where(
            aerodatabox_call_log.c.flight_definition_id == fd.id,
            aerodatabox_call_log.c.flight_date == flight_date,
            aerodatabox_call_log.c.success.is_(True),
        )
        .limit(1)
    ).first()
    return match is not None


def log_call(session, fd: FlightDefinition, flight_date: date, endpoint: str, success: bool, status_code=None, error_message=None):
    session.execute(
        pg_insert(aerodatabox_call_log).values(
            flight_definition_id=fd.id,
            flight_date=flight_date,
            endpoint=endpoint,
            success=success,
            status_code=status_code,
            error_message=error_message,
        )
    )


def upsert_verified_instance(session, fd: FlightDefinition, flight_date: date, dep_utc, arr_utc):
    existing = session.execute(
        select(FlightInstance).where(
            FlightInstance.flight_definition_id == fd.id,
            FlightInstance.flight_date == flight_date,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.scheduled_dep_utc = dep_utc
        existing.scheduled_arr_utc = arr_utc
    else:
        session.add(
            FlightInstance(
                flight_definition_id=fd.id,
                flight_date=flight_date,
                scheduled_dep_utc=dep_utc,
                scheduled_arr_utc=arr_utc,
                status="scheduled",
            )
        )


def verify_flight(session, fd: FlightDefinition, flight_date: date) -> str:
    """Returns one of: 'skipped_cached', 'no_match', 'confirmed', 'discrepancy', 'error'."""
    label = f"{fd.carrier_code}{fd.flight_number} {fd.origin_airport}->{fd.dest_airport}"

    if already_verified_today(session, fd, flight_date):
        print(f"  [{label}] already verified today, skipping API call")
        return "skipped_cached"

    flight_number = f"{fd.carrier_code}{fd.flight_number}"
    date_local = flight_date.isoformat()
    endpoint = ENDPOINT_TEMPLATE.format(flight_number=flight_number, date_local=date_local)

    try:
        results = get_flight_schedule(flight_number, date_local)
    except AeroDataBoxRateLimited as exc:
        log_call(session, fd, flight_date, endpoint, success=False, error_message=str(exc))
        print(f"  [{label}] RATE LIMITED: {exc}")
        raise
    except AeroDataBoxError as exc:
        status_code = getattr(exc, "status_code", None)
        log_call(session, fd, flight_date, endpoint, success=False, status_code=status_code, error_message=str(exc))
        print(f"  [{label}] ERROR: {exc}")
        return "error"

    log_call(session, fd, flight_date, endpoint, success=True)

    match = next(
        (
            r
            for r in results
            if r.departure.airport_iata == fd.origin_airport and r.arrival.airport_iata == fd.dest_airport
        ),
        None,
    )
    if match is None:
        print(f"  [{label}] no matching flight in AeroDataBox response for {date_local}")
        return "no_match"

    confirmed_dep_utc = parse_iso_utc(match.departure.scheduled_utc)
    confirmed_arr_utc = parse_iso_utc(match.arrival.scheduled_utc)
    if confirmed_dep_utc is None or confirmed_arr_utc is None:
        print(f"  [{label}] matched flight but missing scheduled_utc, skipping")
        return "no_match"

    our_dep_utc = local_time_to_utc(flight_date, fd.typical_dep_time, fd.origin_airport)
    dep_diff_minutes = abs((confirmed_dep_utc - our_dep_utc).total_seconds()) / 60

    fd.last_verified_at = datetime.now(timezone.utc)

    if dep_diff_minutes > DISCREPANCY_THRESHOLD_MINUTES:
        old_dep, old_arr = fd.typical_dep_time, fd.typical_arr_time
        fd.typical_dep_time = confirmed_dep_utc.astimezone(get_timezone(fd.origin_airport)).time()
        fd.typical_arr_time = confirmed_arr_utc.astimezone(get_timezone(fd.dest_airport)).time()
        print(
            f"  [{label}] DISCREPANCY: stored dep {old_dep} -> confirmed "
            f"{fd.typical_dep_time} (off by {dep_diff_minutes:.0f} min); "
            f"stored arr {old_arr} -> confirmed {fd.typical_arr_time}"
        )
        outcome = "discrepancy"
    else:
        print(f"  [{label}] confirmed, within {DISCREPANCY_THRESHOLD_MINUTES} min of stored schedule")
        outcome = "confirmed"

    upsert_verified_instance(session, fd, flight_date, confirmed_dep_utc, confirmed_arr_utc)
    return outcome


def run(date_override: date = None) -> dict:
    counts = {
        "skipped_cached": 0,
        "confirmed": 0,
        "discrepancy": 0,
        "no_match": 0,
        "error": 0,
        "rate_limited": 0,
    }

    with SessionLocal() as session:
        check_monthly_budget(session)
        cursor = get_and_advance_cursor(session)
        session.commit()

        todays_flights = select_todays_flights(session, cursor)
        print(
            f"Rotation day {cursor + 1}/{ROTATION_DAYS} — verifying {len(todays_flights)} "
            f"flight(s) today"
        )

        for fd in todays_flights:
            flight_date = date_override or local_today(fd.origin_airport)
            try:
                outcome = verify_flight(session, fd, flight_date)
                counts[outcome] += 1
                session.commit()
            except AeroDataBoxRateLimited:
                counts["rate_limited"] += 1
                session.commit()
                print("Stopping run early due to rate limiting.")
                break

    return counts


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="flight date to verify, overriding per-origin local 'today' "
        "(YYYY-MM-DD) — mainly for backfill/testing",
    )
    args = parser.parse_args()

    counts = run(args.date)
    print(
        "Done. "
        f"confirmed={counts['confirmed']} discrepancy={counts['discrepancy']} "
        f"no_match={counts['no_match']} skipped_cached={counts['skipped_cached']} "
        f"error={counts['error']} rate_limited={counts['rate_limited']}"
    )


if __name__ == "__main__":
    main()
