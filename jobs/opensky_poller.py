#!/usr/bin/env python
"""
M2 — OpenSky /states/all poller + callsign matching (M2a + M2b + M2c).

Polls OpenSky's /states/all for a continental US bounding box on a fixed
interval (default 3 minutes), logs the credit cost reported per call
(X-Rate-Limit-Remaining response header) and stores the raw response to
opensky_raw_poll_log. Then matches callsigns against today's active-pool
flight_instances (see jobs/opensky_matcher.py) — on a match, updates
flight_instances.status/actual_dep_utc/actual_arr_utc/current_icao24 and
writes the matched state vector to state_vector_log.

Auth: OAuth2 client-credentials via src.lib.opensky.TokenManager, verified
against OpenSky's real docs (see that module's docstring). Tokens expire
after 30 minutes; TokenManager refreshes proactively 30s before expiry, and
this poller forces one extra refresh-and-retry if a 401 slips through
anyway (e.g. server-side early invalidation).

Rate limiting: on HTTP 429, sleeps for X-Rate-Limit-Retry-After-Seconds (or
a conservative default if that header is missing) and retries the same
poll cycle, up to MAX_RATE_LIMIT_RETRIES times, before giving up on that
cycle and waiting for the next scheduled interval.

Two ways to run it:
  - Continuous loop (this is what was used for both the M2a credit-cost
    test and the M2c matching test run):
        python -m jobs.opensky_poller --duration-minutes 60
  - Single shot, e.g. for cron (`*/3 * * * *`):
        python -m jobs.opensky_poller --once
"""

import argparse
import time
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert

from jobs.opensky_matcher import classify_all, process_poll
from jobs.schema import opensky_raw_poll_log
from src.db import SessionLocal
from src.lib.callsigns import build_pool_lookup
from src.lib.opensky import (
    OpenSkyAuthError,
    OpenSkyError,
    OpenSkyRateLimited,
    TokenManager,
    get_states_all,
)

# Continental US bounding box (excludes AK/HI) — matches spec section 1's
# "CONUS bbox" scope. Area = (49.384358-24.396308) x (-66.93457 - -125.0)
# = ~1450.9 sq degrees, which is in the ">400 sq deg or global" tier
# (4 credits/call per OpenSky's documented cost table) — logged/confirmed
# per-call from the real response headers below, not just computed here.
CONUS_BBOX = {
    "lamin": 24.396308,
    "lomin": -125.0,
    "lamax": 49.384358,
    "lomax": -66.93457,
}

DEFAULT_INTERVAL_SECONDS = 180
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 60


def _log_failed_poll(session, error_message: str, polled_at):
    session.execute(
        pg_insert(opensky_raw_poll_log).values(
            **CONUS_BBOX, polled_at=polled_at, success=False, error_message=error_message
        )
    )
    session.commit()


def poll_once(token_manager: TokenManager, session, flight_date) -> dict:
    """Makes one /states/all call (retrying through rate limits), logs it,
    matches callsigns, and returns a summary dict."""
    polled_at = datetime.now(timezone.utc)
    result = None

    for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 1):
        try:
            result = get_states_all(token_manager, **CONUS_BBOX)
            break
        except OpenSkyAuthError as exc:
            print(f"  [auth error, retrying with fresh token] {exc}")
            token_manager._token = None  # noqa: SLF001 - intentional forced refresh
            try:
                result = get_states_all(token_manager, **CONUS_BBOX)
                break
            except OpenSkyError as exc2:
                _log_failed_poll(session, str(exc2), polled_at)
                print(f"  FAILED (auth retry also failed): {exc2}")
                return {"success": False}
        except OpenSkyRateLimited as exc:
            backoff = exc.retry_after_seconds or DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
            if attempt >= MAX_RATE_LIMIT_RETRIES:
                _log_failed_poll(session, str(exc), polled_at)
                print(f"  RATE LIMITED (attempt {attempt}/{MAX_RATE_LIMIT_RETRIES}), giving up this cycle: {exc}")
                return {"success": False, "rate_limited": True}
            print(f"  RATE LIMITED (attempt {attempt}/{MAX_RATE_LIMIT_RETRIES}), backing off {backoff}s: {exc}")
            time.sleep(backoff)
        except OpenSkyError as exc:
            _log_failed_poll(session, str(exc), polled_at)
            print(f"  FAILED: {exc}")
            return {"success": False}

    if result is None:
        _log_failed_poll(session, "exhausted retries with no result", polled_at)
        return {"success": False}

    session.execute(
        pg_insert(opensky_raw_poll_log).values(
            **CONUS_BBOX,
            polled_at=polled_at,
            http_status=result.status_code,
            state_vector_count=result.state_vector_count,
            credits_remaining=result.rate_limit_remaining,
            success=True,
            raw_response=result.raw_json,
        )
    )

    pool_lookup = build_pool_lookup(session, flight_date)
    match_summary = process_poll(session, result.raw_json, polled_at, pool_lookup)
    session.commit()

    for label, old, new in match_summary["transitions"]:
        print(f"    transition: {label} {old} -> {new}")

    print(
        f"  OK — {result.state_vector_count} state vectors, "
        f"{len(match_summary['matched_flight_definition_ids'])} pool matches, "
        f"X-Rate-Limit-Remaining={result.rate_limit_remaining}"
    )
    return {
        "success": True,
        "state_vector_count": result.state_vector_count,
        "credits_remaining": result.rate_limit_remaining,
        "matched_flight_definition_ids": match_summary["matched_flight_definition_ids"],
        "transitions": match_summary["transitions"],
    }


def get_baseline_credits_remaining(session):
    """Balance as of the most recent successful poll *before* this run
    starts, so the run summary can report true total consumption rather
    than missing the first poll's own cost."""
    row = session.execute(
        opensky_raw_poll_log.select()
        .where(opensky_raw_poll_log.c.success.is_(True))
        .order_by(opensky_raw_poll_log.c.id.desc())
        .limit(1)
    ).first()
    return row.credits_remaining if row is not None else None


def run_loop(interval_seconds: int, duration_minutes: float = None, max_polls: int = None, flight_date=None):
    token_manager = TokenManager()
    deadline = (
        time.monotonic() + duration_minutes * 60 if duration_minutes is not None else None
    )

    with SessionLocal() as session:
        baseline_remaining = get_baseline_credits_remaining(session)
        if flight_date is None:
            from sqlalchemy import select

            from src.models.flight_instances import FlightInstance

            flight_date = session.execute(select(FlightInstance.flight_date.distinct())).scalars().first()

    poll_count = 0
    first_remaining = baseline_remaining
    last_remaining = None
    success_count = 0
    fail_count = 0
    ever_matched_ids = set()
    all_transitions = []

    try:
        while True:
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{now}] poll #{poll_count + 1}")

            with SessionLocal() as session:
                summary = poll_once(token_manager, session, flight_date)

            poll_count += 1
            if summary.get("success"):
                success_count += 1
                remaining = summary.get("credits_remaining")
                if remaining is not None:
                    if first_remaining is None:
                        first_remaining = remaining
                    last_remaining = remaining
                ever_matched_ids |= summary.get("matched_flight_definition_ids", set())
                all_transitions.extend(summary.get("transitions", []))
            else:
                fail_count += 1

            if max_polls is not None and poll_count >= max_polls:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            if deadline is not None:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    break
                time.sleep(min(interval_seconds, remaining_time))
            else:
                time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    print("\n=== Run summary ===")
    print(f"Polls attempted: {poll_count} (success={success_count}, failed={fail_count})")
    if first_remaining is not None and last_remaining is not None:
        consumed = first_remaining - last_remaining
        note = "" if baseline_remaining is not None else " (no prior poll on record — excludes poll #1's own cost)"
        print(f"X-Rate-Limit-Remaining: baseline={first_remaining}, last={last_remaining}")
        print(f"Credits consumed this run: {consumed}{note} ({consumed / max(success_count, 1):.1f}/successful call)")
    else:
        print("No credit data captured (no successful calls, or header not present).")

    print(f"\nDistinct status transitions observed: {len(all_transitions)}")
    for label, old, new in all_transitions:
        print(f"  {label}: {old} -> {new}")

    print(f"\nFlights matched at least once this run: {len(ever_matched_ids)}")

    if flight_date is not None:
        with SessionLocal() as session:
            pool_lookup = build_pool_lookup(session, flight_date)
            now_utc = datetime.now(timezone.utc)
            classification = classify_all(session, pool_lookup, now_utc)

        from collections import Counter

        counts = Counter(classification.values())
        print("\n=== Final classification (as of run end) ===")
        for category, count in counts.most_common():
            print(f"  {category}: {count}")

        genuinely_missing = sorted(label for label, cat in classification.items() if cat == "genuinely_missing")
        if genuinely_missing:
            print(f"\ngenuinely_missing ({len(genuinely_missing)}):")
            for label in genuinely_missing:
                print(f"  {label}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--once", action="store_true", help="make a single poll and exit (for cron)")
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="seconds between polls (default 180)"
    )
    parser.add_argument("--duration-minutes", type=float, default=None, help="run for this long, then stop")
    parser.add_argument("--max-polls", type=int, default=None, help="stop after this many polls")
    args = parser.parse_args()

    if args.once:
        token_manager = TokenManager()
        with SessionLocal() as session:
            from sqlalchemy import select

            from src.models.flight_instances import FlightInstance

            flight_date = session.execute(select(FlightInstance.flight_date.distinct())).scalars().first()
            poll_once(token_manager, session, flight_date)
        return

    run_loop(args.interval, duration_minutes=args.duration_minutes, max_polls=args.max_polls)


if __name__ == "__main__":
    main()
