#!/usr/bin/env python
"""
M2b — callsign matching report (read-only, no writes).

Uses the real /states/all responses already captured in
opensky_raw_poll_log (from the M2a live test run) to match callsigns
against today's flight_instances, and prints a report: how many of the
123 pool flights got a match, example matches, and unmatched pool flights
worth investigating.

Does NOT write to flight_instances or state_vector_log — that's M2c.

Usage:
    python -m scripts.m2b_callsign_matching_report
"""

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from src.db import SessionLocal
from src.lib.airline_codes import get_pool_airline_codes
from src.lib.callsigns import build_pool_lookup, normalize_callsign
from jobs.schema import opensky_raw_poll_log
from src.models.flight_definitions import FlightDefinition
from src.models.flight_instances import FlightInstance


def load_poll_data(session):
    rows = session.execute(
        select(
            opensky_raw_poll_log.c.id,
            opensky_raw_poll_log.c.polled_at,
            opensky_raw_poll_log.c.raw_response,
        )
        .where(opensky_raw_poll_log.c.success.is_(True))
        .order_by(opensky_raw_poll_log.c.id)
    ).all()
    return rows


def main():
    with SessionLocal() as session:
        icao_by_iata = get_pool_airline_codes(session)
        print(f"Pool airline codes ({len(icao_by_iata)}): {icao_by_iata}\n")

        poll_rows = load_poll_data(session)
        print(f"Loaded {len(poll_rows)} real poll response(s) from opensky_raw_poll_log")
        if not poll_rows:
            print("No poll data found — run jobs.opensky_poller first.")
            return

        # all our flight_instances are for a single flight_date from the M1
        # test run; use that as "today" for the lookup.
        flight_dates = session.execute(select(FlightInstance.flight_date.distinct())).scalars().all()
        if len(flight_dates) != 1:
            print(f"WARNING: multiple flight_date values in flight_instances: {flight_dates}; using the first")
        flight_date = flight_dates[0]
        print(f"Matching against flight_instances.flight_date = {flight_date}\n")

        pool_lookup = build_pool_lookup(session, flight_date)
        total_pool_flights = session.execute(
            select(FlightDefinition.id).where(FlightDefinition.active.is_(True))
        ).scalars().all()
        print(f"Pool size: {len(total_pool_flights)} active flight_definitions\n")

        # collect every sighting across all polls, keyed by raw callsign,
        # remembering earliest/latest polled_at seen and which poll ids
        raw_callsign_sightings = defaultdict(list)  # raw_callsign -> [(poll_id, polled_at), ...]
        for poll_id, polled_at, raw in poll_rows:
            for state in raw.get("states", []):
                cs = state[1]
                if cs and cs.strip():
                    raw_callsign_sightings[cs].append((poll_id, polled_at))

        print(f"Distinct raw (unstripped) callsigns across all polls: {len(raw_callsign_sightings)}\n")

        matched_flight_definition_ids = set()
        example_rows = []  # for the 10-15 example table
        unmatched_pool_prefix_callsigns = defaultdict(list)  # icao_prefix -> [raw callsigns not matching any flight_number]

        icao_prefixes = set(icao_by_iata.values())

        for raw_cs, sightings in raw_callsign_sightings.items():
            normalized = normalize_callsign(raw_cs)
            match = pool_lookup.get(normalized) if normalized else None

            if match:
                fd, fi = match
                matched_flight_definition_ids.add(fd.id)
                example_rows.append(
                    {
                        "raw": raw_cs,
                        "normalized": normalized,
                        "match": f"{fd.carrier_code}{fd.flight_number} {fd.origin_airport}->{fd.dest_airport}",
                        "sightings": len(sightings),
                    }
                )
            elif normalized and normalized[0] in icao_prefixes:
                # looked like one of our carriers but flight_number didn't match anything in the pool
                unmatched_pool_prefix_callsigns[normalized[0]].append((raw_cs, normalized))

        print(f"=== RESULT: {len(matched_flight_definition_ids)} / {len(total_pool_flights)} pool flights matched ===\n")

        print("=== Example matches (up to 15) ===")
        # prefer variety: one per carrier prefix where possible, else just take first 15
        example_rows.sort(key=lambda r: r["normalized"][0])
        for row in example_rows[:15]:
            print(f"  {row['raw']!r:12} -> {row['normalized']} -> {row['match']} ({row['sightings']} sighting(s))")
        print()

        print("=== Same-carrier-prefix callsigns that did NOT match any pool flight_number (sample) ===")
        for prefix, items in unmatched_pool_prefix_callsigns.items():
            print(f"  {prefix}: {len(items)} distinct callsign(s), e.g. {[i[0].strip() for i in items[:5]]}")
        print()

        print("=== Unmatched pool flights: investigating why ===")
        now_examples = poll_rows[0][1], poll_rows[-1][1]
        poll_window_start, poll_window_end = min(p[1] for p in poll_rows), max(p[1] for p in poll_rows)
        print(f"(poll window: {poll_window_start} .. {poll_window_end})\n")

        fd_by_id = {fd.id: fd for fd, _fi in pool_lookup.values()}
        fi_by_fd_id = {fd.id: fi for fd, fi in pool_lookup.values()}

        unmatched_count = 0
        for fd_id, fd in fd_by_id.items():
            if fd_id in matched_flight_definition_ids:
                continue
            unmatched_count += 1
            fi = fi_by_fd_id[fd_id]
            dep, arr = fi.scheduled_dep_utc, fi.scheduled_arr_utc
            in_window = dep is not None and arr is not None and dep <= poll_window_end and arr >= poll_window_start
            reason = (
                "scheduled window overlaps poll window — expected in-flight, but no match: WORTH INVESTIGATING"
                if in_window
                else f"scheduled dep={dep} arr={arr} does not overlap poll window — likely not airborne yet/already landed, expected miss"
            )
            print(f"  {fd.carrier_code}{fd.flight_number} {fd.origin_airport}->{fd.dest_airport}: {reason}")

        print(f"\nTotal unmatched: {unmatched_count}")


if __name__ == "__main__":
    main()
