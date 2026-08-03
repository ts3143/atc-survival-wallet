import uuid

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    DateTime,
    Enum,
    Integer,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from src.db import Base

distance_bucket_enum = Enum("short", "medium", "long", name="distance_bucket")


class FlightDefinition(Base):
    __tablename__ = "flight_definitions"
    __table_args__ = (
        UniqueConstraint(
            "carrier_code",
            "flight_number",
            "origin_airport",
            "dest_airport",
            name="uq_flight_definitions_carrier_flight_route",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    carrier_code = Column(String(2), nullable=False)
    flight_number = Column(String(6), nullable=False)
    origin_airport = Column(String(3), nullable=False)
    dest_airport = Column(String(3), nullable=False)
    days_of_week = Column(ARRAY(Integer), nullable=False)
    typical_dep_time = Column(Time, nullable=False)
    typical_arr_time = Column(Time, nullable=False)
    distance_bucket = Column(distance_bucket_enum, nullable=False)
    active = Column(Boolean, nullable=False, server_default=text("true"))
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
