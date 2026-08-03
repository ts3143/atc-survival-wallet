"""
IATA airport -> IANA timezone mapping, and local-time-of-day -> UTC
conversion for a specific calendar date (DST-aware).

flight_definitions.typical_dep_time/typical_arr_time are local TIME values
with no date or timezone attached (per spec section 2). To populate
flight_instances.scheduled_dep_utc/scheduled_arr_utc for a specific day, we
need to know which timezone "local" means for that flight's origin/dest
airport, then localize + convert to UTC for that specific date (so DST
transitions are handled correctly).

IMPORTANT: this table was built from general geographic knowledge, not a
verified authoritative airport database (no external lookup was used, to
avoid burning API budget) — it covers exactly the airports present in the
curated pool as of this writing. Small/regional airports in historically
contested timezone-boundary areas (e.g. western TX/ND, MI's Upper
Peninsula, southern NM) are the most likely to be wrong; spot-check before
relying on this for anything beyond the wallet game's own scheduling.
"""

import pytz

# All IANA zones actually used below (kept small & canonical — avoids
# obscure historical sub-zone aliases like America/Indiana/Indianapolis,
# which behave identically to America/New_York for any current/future date).
EASTERN = "America/New_York"
CENTRAL = "America/Chicago"
MOUNTAIN = "America/Denver"
ARIZONA = "America/Phoenix"  # no DST
PACIFIC = "America/Los_Angeles"
ALASKA = "America/Anchorage"
HAWAII = "Pacific/Honolulu"  # no DST
PUERTO_RICO = "America/Puerto_Rico"  # no DST

AIRPORT_TIMEZONES = {
    "ASE": MOUNTAIN,
    "ATL": EASTERN,
    "AUS": CENTRAL,
    "BIL": MOUNTAIN,
    "BIS": CENTRAL,
    "BNA": CENTRAL,
    "BPT": CENTRAL,
    "CAE": EASTERN,
    "CAK": EASTERN,
    "CLE": EASTERN,
    "CLT": EASTERN,
    "CMH": EASTERN,
    "CMX": EASTERN,
    "COS": MOUNTAIN,
    "CRW": EASTERN,
    "DCA": EASTERN,
    "DEN": MOUNTAIN,
    "DFW": CENTRAL,
    "DTW": EASTERN,
    "EUG": PACIFIC,
    "FAR": CENTRAL,
    "FCA": MOUNTAIN,
    "FLG": ARIZONA,
    "GEG": PACIFIC,
    "GRR": EASTERN,
    "HNL": HAWAII,
    "HOB": CENTRAL,
    "IAD": EASTERN,
    "IAH": CENTRAL,
    "JAC": MOUNTAIN,
    "JFK": EASTERN,
    "JLN": CENTRAL,
    "JST": EASTERN,
    "KOA": HAWAII,
    "LAS": PACIFIC,
    "LAX": PACIFIC,
    "LGA": EASTERN,
    "LIH": HAWAII,
    "LIT": CENTRAL,
    "MAF": CENTRAL,
    "MCO": EASTERN,
    "MIA": EASTERN,
    "MKE": CENTRAL,
    "MOT": CENTRAL,
    "MSP": CENTRAL,
    "OGG": HAWAII,
    "ORD": CENTRAL,
    "ORF": EASTERN,
    "PDX": PACIFIC,
    "PHL": EASTERN,
    "PHX": ARIZONA,
    "PNS": CENTRAL,
    "PRC": ARIZONA,
    "RDD": PACIFIC,
    "RDU": EASTERN,
    "RKS": MOUNTAIN,
    "RNO": PACIFIC,
    "ROA": EASTERN,
    "SAN": PACIFIC,
    "SAT": CENTRAL,
    "SAV": EASTERN,
    "SBN": EASTERN,
    "SBP": PACIFIC,
    "SDF": EASTERN,
    "SEA": PACIFIC,
    "SFO": PACIFIC,
    "SGU": MOUNTAIN,
    "SJU": PUERTO_RICO,
    "SLC": MOUNTAIN,
    "SMF": PACIFIC,
    "SNA": PACIFIC,
    "VCT": CENTRAL,
    "XNA": CENTRAL,
    "XWA": CENTRAL,
}


class UnknownAirportTimezoneError(KeyError):
    pass


def get_timezone(iata_code: str) -> pytz.BaseTzInfo:
    try:
        zone_name = AIRPORT_TIMEZONES[iata_code]
    except KeyError:
        raise UnknownAirportTimezoneError(
            f"No timezone mapping for airport {iata_code!r} — add it to "
            f"src/lib/airport_timezones.py before scheduling this flight."
        ) from None
    return pytz.timezone(zone_name)


def local_time_to_utc(local_date, local_time, iata_code: str):
    """Localize a (date, time) pair at the given airport to that airport's
    timezone (DST-aware for the given date) and convert to UTC."""
    import datetime

    tz = get_timezone(iata_code)
    naive = datetime.datetime.combine(local_date, local_time)
    localized = tz.localize(naive)
    return localized.astimezone(pytz.utc)
