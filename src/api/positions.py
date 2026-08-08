"""
Map/debugging endpoints (not part of the wallet game loop) — read-only
views over state_vector_log, populated by jobs/opensky_matcher.py every
poll cycle. See jobs/README.md for how that data gets there.
"""

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.schemas import CurrentPositionOut, TrackPointOut
from src.db import get_db
from src.models.flight_definitions import FlightDefinition
from src.models.flight_instances import FlightInstance
from src.models.state_vector_log import StateVectorLog

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("/current", response_model=List[CurrentPositionOut])
def get_current_positions(db: Session = Depends(get_db)):
    """Latest state_vector_log row per flight_instance currently marked
    'airborne', for TODAY specifically.

    The date filter matters, not just belt-and-suspenders: found a real
    stale-data case while building this (DL1331) — a flight_instance from
    an earlier day can get stuck at status='airborne' forever if it was
    matched once during a mis-targeted poller run and never polled against
    again (nothing ever transitions it to 'landed'), which without this
    filter would show up as a permanent ghost marker sitting motionless on
    the map. flight_date >= today (not ==) so we never accidentally
    exclude a genuinely current flight near a UTC midnight boundary.
    """
    today_utc = datetime.now(timezone.utc).date()
    stmt = (
        select(StateVectorLog, FlightInstance, FlightDefinition)
        .join(FlightInstance, FlightInstance.id == StateVectorLog.flight_instance_id)
        .join(FlightDefinition, FlightDefinition.id == FlightInstance.flight_definition_id)
        .where(FlightInstance.status == "airborne", FlightInstance.flight_date >= today_utc)
        .distinct(StateVectorLog.flight_instance_id)
        .order_by(StateVectorLog.flight_instance_id, StateVectorLog.polled_at.desc())
    )
    rows = db.execute(stmt).all()

    return [
        CurrentPositionOut(
            flight_instance_id=fi.id,
            carrier_code=fd.carrier_code,
            flight_number=fd.flight_number,
            origin_airport=fd.origin_airport,
            dest_airport=fd.dest_airport,
            status=fi.status,
            latitude=sv.latitude,
            longitude=sv.longitude,
            heading=sv.heading,
            altitude_m=sv.altitude_m,
            velocity_ms=sv.velocity_ms,
            vertical_rate=sv.vertical_rate,
            on_ground=sv.on_ground,
            polled_at=sv.polled_at,
        )
        for sv, fi, fd in rows
    ]


@router.get("/{flight_instance_id}/track", response_model=List[TrackPointOut])
def get_flight_track(flight_instance_id: UUID, db: Session = Depends(get_db)):
    fi = db.get(FlightInstance, flight_instance_id)
    if fi is None:
        raise HTTPException(status_code=404, detail="flight_instance not found")

    points = (
        db.execute(
            select(StateVectorLog)
            .where(StateVectorLog.flight_instance_id == flight_instance_id)
            .order_by(StateVectorLog.polled_at.asc())
        )
        .scalars()
        .all()
    )
    return [TrackPointOut.model_validate(p) for p in points]
