"""
M2c — callsign matching + flight_instances/state_vector_log writes.

For each poll's raw /states/all response, matches callsigns (see
src.lib.callsigns) against today's active-pool flight_instances, and:
  - on a match: updates flight_instances.status / actual_dep_utc /
    actual_arr_utc / current_icao24, and writes the matched state vector
    to state_vector_log.
  - classifies every active pool flight (matched or not) into a
    diagnostic category for run-level reporting: not_yet_departed,
    already_landed, out_of_coverage, genuinely_missing, or matched. These
    categories are NOT persisted anywhere (flight_instances.status only
    ever uses the spec's own enum) — they exist purely to make sense of
    match coverage while this is still a foundation-building phase.

Status transitions (deliberately conservative — this only trusts what it
can directly observe from on_ground; it does not infer delayed/diverted/
cancelled, which need other signals we don't have yet):
    scheduled -> departed   (first match, on_ground=True)
    scheduled -> airborne   (first match, on_ground=False)
    departed  -> airborne   (subsequent match, on_ground=False)
    airborne  -> landed     (subsequent match, on_ground=True — i.e. was
                              flying, now on the ground = arrived)
    landed    -> landed     (terminal, idempotent)
A ground sighting while still "departed" (never yet seen airborne) stays
"departed" rather than jumping to "landed" — otherwise a flight sitting on
the ground pre-departure would look like it had already arrived.

actual_dep_utc is set once, the first time status becomes departed or
airborne. actual_arr_utc is set once, the first time status becomes
landed. Both use the poll's polled_at as the observed timestamp (OpenSky's
own per-vector time_position/last_contact would be marginally more
precise, but polled_at is what state_vector_log's own schema column
represents).

Known structural coverage gap: some routes are entirely outside the CONUS
bounding box polled by opensky_poller.py (Hawaii was one, removed from the
pool in curated_pool_v5_final.csv; San Juan (SJU) still is one — see
KNOWN_OUT_OF_CONUS_BBOX_AIRPORTS below). Flights on those routes are
classified "out_of_coverage" rather than "genuinely_missing" — they're not
a matching-logic problem, they're a bounding-box problem.
"""

from datetime import timedelta

from sqlalchemy import select

from src.lib.callsigns import build_pool_lookup, match_callsign
from src.models.flight_instances import FlightInstance
from src.models.state_vector_log import StateVectorLog

GRACE_BEFORE_DEP_MINUTES = 10
GRACE_AFTER_ARR_MINUTES = 45

# South San Juan (SJU) sits south of lamin=24.4 and east of lomax=-66.93 —
# entirely outside the CONUS bbox polled by opensky_poller.py, same
# structural issue Hawaii had. Confirmed present in
# curated_pool_v5_final.csv (UA1192, UA1996, UA701, UA668, DL1854).
KNOWN_OUT_OF_CONUS_BBOX_AIRPORTS = {"SJU"}


def apply_match(fi: FlightInstance, state: list, polled_at):
    """Mutates fi in place based on one observed state vector. Returns
    (old_status, new_status)."""
    icao24 = state[0]
    on_ground = state[8]

    old_status = fi.status
    fi.current_icao24 = icao24

    if old_status == "landed":
        new_status = "landed"
    elif on_ground:
        new_status = "landed" if old_status == "airborne" else "departed"
    else:
        new_status = "airborne"

    fi.status = new_status

    if new_status in ("departed", "airborne") and fi.actual_dep_utc is None:
        fi.actual_dep_utc = polled_at
    if new_status == "landed" and fi.actual_arr_utc is None:
        fi.actual_arr_utc = polled_at

    return old_status, new_status


def log_state_vector(session, flight_instance_id, state: list, polled_at):
    session.add(
        StateVectorLog(
            flight_instance_id=flight_instance_id,
            polled_at=polled_at,
            longitude=state[5],
            latitude=state[6],
            altitude_m=state[7],
            on_ground=state[8],
            velocity_ms=state[9],
            heading=state[10],
            vertical_rate=state[11],
        )
    )


def process_poll(session, raw_response: dict, polled_at, pool_lookup: dict) -> dict:
    """Matches every state vector against pool_lookup; updates
    flight_instances + writes state_vector_log for matches.

    Returns {"matched_flight_definition_ids": set, "transitions": [(label, old, new), ...]}.
    """
    matched_ids = set()
    transitions = []

    for state in raw_response.get("states") or []:
        match = match_callsign(state[1], pool_lookup)
        if match is None:
            continue
        fd, fi = match
        matched_ids.add(fd.id)

        old_status, new_status = apply_match(fi, state, polled_at)
        if old_status != new_status:
            transitions.append((f"{fd.carrier_code}{fd.flight_number}", old_status, new_status))

        log_state_vector(session, fi.id, state, polled_at)

    return {"matched_flight_definition_ids": matched_ids, "transitions": transitions}


def classify_flight(fd, fi, now_utc) -> str:
    # fi.status is a persisted signal: it flips off "scheduled" the moment
    # ANY poll (this run or a prior one) has ever matched this flight, so
    # it's a more complete "has this ever been observed" check than a
    # single run's in-memory matched-id set — and it must be checked
    # before the structural out_of_coverage guess below, otherwise a real
    # match on a route that merely touches a known-gap airport (e.g.
    # UA668 SJU->IAH, caught near its CONUS-side arrival) gets incorrectly
    # relabeled "out_of_coverage" even though we actually saw it.
    if fi.status != "scheduled":
        return "matched"

    if fd.origin_airport in KNOWN_OUT_OF_CONUS_BBOX_AIRPORTS or fd.dest_airport in KNOWN_OUT_OF_CONUS_BBOX_AIRPORTS:
        return "out_of_coverage"

    trackable_start = fi.scheduled_dep_utc - timedelta(minutes=GRACE_BEFORE_DEP_MINUTES)
    trackable_end = fi.scheduled_arr_utc + timedelta(minutes=GRACE_AFTER_ARR_MINUTES)

    if now_utc < trackable_start:
        return "not_yet_departed"
    if now_utc > trackable_end:
        return "already_landed"
    return "genuinely_missing"


def classify_all(session, pool_lookup: dict, now_utc) -> dict:
    """Classifies every flight in pool_lookup as of now_utc, using each
    flight_instance's persisted status (not an in-memory matched-id set —
    see classify_flight's docstring note). Returns {flight_label: category}."""
    result = {}
    for (icao_prefix, flight_number), (fd, fi) in pool_lookup.items():
        label = f"{fd.carrier_code}{fd.flight_number} {fd.origin_airport}->{fd.dest_airport}"
        result[label] = classify_flight(fd, fi, now_utc)
    return result
