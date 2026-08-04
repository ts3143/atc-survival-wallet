from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.schemas import FlightDetail, FlightListItem, FlightInstanceOut
from src.db import get_db
from src.models.flight_definitions import FlightDefinition
from src.models.flight_instances import FlightInstance
from src.models.flight_volatility_stats import FlightVolatilityStats

router = APIRouter(prefix="/api/flights", tags=["flights"])

SORTABLE_COLUMNS = {
    "carrier_code": FlightDefinition.carrier_code,
    "flight_number": FlightDefinition.flight_number,
    "on_time_pct": FlightVolatilityStats.on_time_pct,
    "delay_stddev": FlightVolatilityStats.delay_stddev,
    "cancellation_pct": FlightVolatilityStats.cancellation_pct,
}


@router.get("", response_model=List[FlightListItem])
def list_flights(
    carrier_code: Optional[str] = None,
    sort_by: Optional[str] = Query(None, description=f"one of {sorted(SORTABLE_COLUMNS)}"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    stmt = (
        select(FlightDefinition, FlightVolatilityStats)
        .join(FlightVolatilityStats, FlightVolatilityStats.flight_definition_id == FlightDefinition.id)
        .where(FlightDefinition.active.is_(True))
    )

    if carrier_code:
        stmt = stmt.where(FlightDefinition.carrier_code == carrier_code.upper())

    if sort_by:
        column = SORTABLE_COLUMNS.get(sort_by)
        if column is None:
            raise HTTPException(status_code=400, detail=f"sort_by must be one of {sorted(SORTABLE_COLUMNS)}")
        stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    else:
        stmt = stmt.order_by(FlightDefinition.carrier_code, FlightDefinition.flight_number)

    rows = db.execute(stmt).all()
    return [_merge_flight_row(fd, fvs) for fd, fvs in rows]


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

    merged = _merge_flight_row(fd, fvs)
    return FlightDetail(
        **merged.model_dump(),
        current_instance=FlightInstanceOut.model_validate(latest_instance) if latest_instance else None,
    )


def _merge_flight_row(fd: FlightDefinition, fvs: Optional[FlightVolatilityStats]) -> FlightListItem:
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
    )
