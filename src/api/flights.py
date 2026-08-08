from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.schemas import FlightDetail, FlightListItem, FlightInstanceOut
from src.db import get_db
from src.lib.airport_timezones import AIRPORT_TIMEZONES
from src.models.flight_definitions import FlightDefinition
from src.models.flight_instances import FlightInstance
from src.models.flight_volatility_stats import FlightVolatilityStats

router = APIRouter(prefix="/api/flights", tags=["flights"])

NOT_SCHEDULED_YET = "not_scheduled_yet"

# sort_by -> attribute name on FlightListItem (sorting happens in Python,
# not SQL, since scheduled_dep_utc comes from a separate merged lookup —
# see _latest_instance_by_flight — and 123 rows is trivially small to sort
# in memory rather than juggle two different sort mechanisms).
SORTABLE_FIELDS = {
    "carrier_code": "carrier_code",
    "flight_number": "flight_number",
    "on_time_pct": "on_time_pct",
    "delay_stddev": "delay_stddev",
    "cancellation_pct": "cancellation_pct",
    "scheduled_dep_utc": "scheduled_dep_utc",
}


def _latest_instance_by_flight(db: Session) -> dict:
    """flight_definition_id -> its most recent FlightInstance (by
    flight_date, then created_at) — same simplification used by
    get_flight()/create_wallet_pick() elsewhere in this API."""
    rows = (
        db.execute(
            select(FlightInstance)
            .distinct(FlightInstance.flight_definition_id)
            .order_by(
                FlightInstance.flight_definition_id,
                FlightInstance.flight_date.desc(),
                FlightInstance.created_at.desc(),
            )
        )
        .scalars()
        .all()
    )
    return {fi.flight_definition_id: fi for fi in rows}


@router.get("", response_model=List[FlightListItem])
def list_flights(
    carrier_code: Optional[str] = None,
    sort_by: Optional[str] = Query(None, description=f"one of {sorted(SORTABLE_FIELDS)}"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    if sort_by and sort_by not in SORTABLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"sort_by must be one of {sorted(SORTABLE_FIELDS)}")

    stmt = (
        select(FlightDefinition, FlightVolatilityStats)
        .join(FlightVolatilityStats, FlightVolatilityStats.flight_definition_id == FlightDefinition.id)
        .where(FlightDefinition.active.is_(True))
    )
    if carrier_code:
        stmt = stmt.where(FlightDefinition.carrier_code == carrier_code.upper())

    rows = db.execute(stmt).all()
    latest_instance_by_flight = _latest_instance_by_flight(db)
    flights = [_merge_flight_row(fd, fvs, latest_instance_by_flight.get(fd.id)) for fd, fvs in rows]

    field = SORTABLE_FIELDS[sort_by] if sort_by else "carrier_code"

    # None always sorts last regardless of asc/desc (so missing data, e.g.
    # a flight with no flight_instance yet, doesn't jump to the top on a
    # descending sort) — split rather than encode into the sort key, since
    # reverse=True would otherwise flip the None-ordering along with
    # everything else.
    with_value = [f for f in flights if getattr(f, field) is not None]
    without_value = [f for f in flights if getattr(f, field) is None]
    with_value.sort(key=lambda f: (getattr(f, field), f.carrier_code, f.flight_number), reverse=(order == "desc"))

    return with_value + without_value


@router.get("/{flight_definition_id}", response_model=FlightDetail)
def get_flight(flight_definition_id: UUID, db: Session = Depends(get_db)):
    fd = db.get(FlightDefinition, flight_definition_id)
    if fd is None or not fd.active:
        raise HTTPException(status_code=404, detail="flight not found")

    fvs = db.execute(
        select(FlightVolatilityStats).where(FlightVolatilityStats.flight_definition_id == fd.id)
    ).scalar_one_or_none()

    # "current" instance: most recent by flight_date. Deliberately simple
    # for M4 — doesn't replicate the per-origin-timezone "local today"
    # logic in jobs/schedule_refresher.py.
    latest_instance = db.execute(
        select(FlightInstance)
        .where(FlightInstance.flight_definition_id == fd.id)
        .order_by(FlightInstance.flight_date.desc(), FlightInstance.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    merged = _merge_flight_row(fd, fvs, latest_instance)
    return FlightDetail(
        **merged.model_dump(),
        current_instance=FlightInstanceOut.model_validate(latest_instance) if latest_instance else None,
    )


def _merge_flight_row(
    fd: FlightDefinition, fvs: Optional[FlightVolatilityStats], latest_instance: Optional[FlightInstance]
) -> FlightListItem:
    return FlightListItem(
        id=fd.id,
        carrier_code=fd.carrier_code,
        flight_number=fd.flight_number,
        origin_airport=fd.origin_airport,
        dest_airport=fd.dest_airport,
        distance_bucket=fd.distance_bucket,
        typical_dep_time=fd.typical_dep_time,
        typical_arr_time=fd.typical_arr_time,
        on_time_pct=fvs.on_time_pct if fvs else None,
        avg_delay_minutes=fvs.avg_delay_minutes if fvs else None,
        delay_stddev=fvs.delay_stddev if fvs else None,
        cancellation_pct=fvs.cancellation_pct if fvs else None,
        diversion_pct=fvs.diversion_pct if fvs else None,
        sample_size=fvs.sample_size if fvs else None,
        origin_timezone=AIRPORT_TIMEZONES.get(fd.origin_airport),
        dest_timezone=AIRPORT_TIMEZONES.get(fd.dest_airport),
        status=latest_instance.status if latest_instance else NOT_SCHEDULED_YET,
        scheduled_dep_utc=latest_instance.scheduled_dep_utc if latest_instance else None,
        scheduled_arr_utc=latest_instance.scheduled_arr_utc if latest_instance else None,
        actual_dep_utc=latest_instance.actual_dep_utc if latest_instance else None,
        actual_arr_utc=latest_instance.actual_arr_utc if latest_instance else None,
    )
