"""
IATA <-> ICAO airline code mapping, scoped to whatever carriers are
actually present in flight_definitions — per spec section 4's callsign
matching logic ("An ICAO <-> IATA airline code mapping table (small static
reference table, easy to source once)"), we deliberately don't carry
mappings for carriers we don't have.

The table below is a static reference (general aviation-industry
knowledge). AA/DL/UA/F9/OH/OO/YX were cross-checked against a real OpenSky
/states/all poll (opensky_raw_poll_log ids 1-7, captured 2026-08-03): each
showed up hundreds of times in real CONUS traffic that day under the
expected ICAO prefix. AS/B6 were added when the pool was rebalanced
(curated_pool_v5_final.csv) and not yet independently re-confirmed against
a fresh live poll at time of writing — same live-poll cross-check is
planned for the M2c test run.
"""

from sqlalchemy import select

# IATA -> (ICAO, name). Superset of what any given pool might need; the
# actually-used subset is computed by get_pool_airline_codes() below.
IATA_TO_ICAO = {
    "AA": ("AAL", "American Airlines"),
    "DL": ("DAL", "Delta Air Lines"),
    "UA": ("UAL", "United Airlines"),
    "F9": ("FFT", "Frontier Airlines"),
    "OH": ("JIA", "PSA Airlines"),
    "OO": ("SKW", "SkyWest Airlines"),
    "YX": ("RPA", "Republic Airways"),
    "AS": ("ASA", "Alaska Airlines"),
    "B6": ("JBU", "JetBlue Airways"),
}


def get_pool_airline_codes(session) -> dict:
    """Distinct active carrier_code values in flight_definitions, mapped to
    their ICAO prefix. Raises if any pool carrier has no known mapping."""
    from src.models.flight_definitions import FlightDefinition

    carriers = sorted(
        set(
            session.execute(
                select(FlightDefinition.carrier_code).where(FlightDefinition.active.is_(True))
            )
            .scalars()
            .all()
        )
    )

    mapping = {}
    missing = []
    for carrier in carriers:
        if carrier in IATA_TO_ICAO:
            mapping[carrier] = IATA_TO_ICAO[carrier][0]
        else:
            missing.append(carrier)

    if missing:
        raise ValueError(
            f"No ICAO mapping for carrier(s) present in flight_definitions: {missing} "
            f"— add them to IATA_TO_ICAO in src/lib/airline_codes.py"
        )

    return mapping
