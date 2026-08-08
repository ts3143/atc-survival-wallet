"""
Pydantic request/response models for the M4 API layer.

Kept deliberately flat/minimal — this is the first API layer the project
has (M0-M3 only ever touched the DB directly from jobs/scripts), built to
support M4's minimal frontend, not a general-purpose public API.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FlightListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    carrier_code: str
    flight_number: str
    origin_airport: str
    dest_airport: str
    distance_bucket: str
    typical_dep_time: time
    typical_arr_time: time
    on_time_pct: Optional[Decimal] = None
    avg_delay_minutes: Optional[Decimal] = None
    delay_stddev: Optional[Decimal] = None
    cancellation_pct: Optional[Decimal] = None
    diversion_pct: Optional[Decimal] = None
    sample_size: Optional[int] = None
    # IANA zone names, from src.lib.airport_timezones — reused rather than
    # duplicating a second copy of the same lookup table in the frontend.
    origin_timezone: Optional[str] = None
    dest_timezone: Optional[str] = None
    # From the flight's most recent flight_instance (same "most recent by
    # flight_date" simplification used elsewhere in this API). status is
    # "not_scheduled_yet" (not a flight_instances.status enum value — a
    # synthetic marker) when no flight_instance exists yet for this flight,
    # rather than erroring.
    status: str = "not_scheduled_yet"
    scheduled_dep_utc: Optional[datetime] = None
    scheduled_arr_utc: Optional[datetime] = None
    actual_dep_utc: Optional[datetime] = None
    actual_arr_utc: Optional[datetime] = None


class FlightInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    flight_date: date
    status: str
    scheduled_dep_utc: Optional[datetime] = None
    scheduled_arr_utc: Optional[datetime] = None
    actual_dep_utc: Optional[datetime] = None
    actual_arr_utc: Optional[datetime] = None
    current_icao24: Optional[str] = None


class FlightDetail(FlightListItem):
    current_instance: Optional[FlightInstanceOut] = None


class CreateWalletPickRequest(BaseModel):
    flight_definition_id: UUID
    staked_amount: Decimal


class PickFlightSummary(BaseModel):
    carrier_code: str
    flight_number: str
    origin_airport: str
    dest_airport: str
    # IANA zone names (e.g. "America/Los_Angeles"), from
    # src.lib.airport_timezones — reused here rather than duplicating a
    # second copy of the same lookup table in the frontend. None if the
    # airport isn't in that table (shouldn't happen for pool airports, but
    # not guaranteed for arbitrary ones).
    origin_timezone: Optional[str] = None
    dest_timezone: Optional[str] = None


class WalletPickOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    staked_amount: Decimal
    status: str
    resolved_amount: Optional[Decimal] = None
    last_charged_delay_minutes: Decimal
    created_at: datetime
    cashed_out_at: Optional[datetime] = None
    flight: PickFlightSummary
    flight_instance: FlightInstanceOut


class WalletOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    balance: Decimal
    started_at: datetime
    picks: List[WalletPickOut]


class WalletEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    amount: Decimal
    occurred_at: datetime
    event_metadata: Optional[Dict[str, Any]] = None


class CurrentPositionOut(BaseModel):
    """One row = the latest state_vector_log reading for one currently-
    airborne flight_instance. Map/debugging use only (GET /api/positions/*)."""

    model_config = ConfigDict(from_attributes=True)

    flight_instance_id: UUID
    carrier_code: str
    flight_number: str
    origin_airport: str
    dest_airport: str
    status: str
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    heading: Optional[Decimal] = None
    altitude_m: Optional[Decimal] = None
    velocity_ms: Optional[Decimal] = None
    vertical_rate: Optional[Decimal] = None
    on_ground: Optional[bool] = None
    polled_at: datetime


class TrackPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    altitude_m: Optional[Decimal] = None
    velocity_ms: Optional[Decimal] = None
    heading: Optional[Decimal] = None
    vertical_rate: Optional[Decimal] = None
    on_ground: Optional[bool] = None
    polled_at: datetime
