"""
OpenSky raw-callsign normalization and matching against today's
flight_instances, per spec section 4's callsign matching logic.

Observed real-world quirks (from a live CONUS /states/all poll captured
2026-08-03, opensky_raw_poll_log ids 1-7 — not assumed):
  - Callsigns are right-padded with spaces to a fixed 8 characters (e.g.
    "AAL1012 ") — strip before use.
  - Format is ICAO 3-letter airline prefix + numeric flight number, e.g.
    "AAL1012" -> American Airlines 1012.
  - Some regional-carrier (SkyWest/SKW) callsigns carry a trailing single
    letter after the digits (e.g. "SKW129H", "SKW166C") — seen alongside
    plenty of plain numeric SKW callsigns ("SKW3035") in the same poll.
    These are parsed (letter stripped) rather than dropped outright, since
    dropping them silently would hide a real ambiguity; in practice they
    just don't match anything in our pool (SkyWest operates flights for
    multiple majors under different marketing numbers/ranges, and the
    lettered ones don't fall in our pool's flight-number range) so they
    fail the lookup naturally rather than needing special-case handling.
  - Non-airline traffic (GA tail numbers like "N6545D", military/other
    callsigns like "TALON57") doesn't match the ICAO-prefix + digits
    pattern and is rejected by normalize_callsign() before it ever reaches
    the matching step.
  - Flight numbers are not zero-padded in either OpenSky's callsigns or
    our own stored flight_definitions.flight_number, but this code
    int()-roundtrips the digits anyway as a guard against that.
"""

import re

from sqlalchemy import select

CALLSIGN_PATTERN = re.compile(r"^([A-Z]{3})(\d+)([A-Z]?)$")


def normalize_callsign(raw_callsign):
    """Returns (icao_prefix, flight_number) or None if raw_callsign doesn't
    look like a scheduled-airline callsign."""
    if not raw_callsign:
        return None
    cs = raw_callsign.strip()
    m = CALLSIGN_PATTERN.match(cs)
    if not m:
        return None
    icao_prefix, digits, _trailing_letter = m.groups()
    flight_number = str(int(digits))
    return icao_prefix, flight_number


def build_pool_lookup(session, flight_date):
    """(icao_prefix, flight_number) -> (FlightDefinition, FlightInstance)
    for the active pool's instances on flight_date."""
    from src.lib.airline_codes import get_pool_airline_codes
    from src.models.flight_definitions import FlightDefinition
    from src.models.flight_instances import FlightInstance

    icao_by_iata = get_pool_airline_codes(session)

    rows = session.execute(
        select(FlightDefinition, FlightInstance)
        .join(FlightInstance, FlightInstance.flight_definition_id == FlightDefinition.id)
        .where(FlightDefinition.active.is_(True), FlightInstance.flight_date == flight_date)
    ).all()

    lookup = {}
    for fd, fi in rows:
        icao_prefix = icao_by_iata[fd.carrier_code]
        lookup[(icao_prefix, fd.flight_number)] = (fd, fi)
    return lookup


def match_callsign(raw_callsign, pool_lookup: dict):
    """Returns (FlightDefinition, FlightInstance) or None."""
    normalized = normalize_callsign(raw_callsign)
    if normalized is None:
        return None
    return pool_lookup.get(normalized)
