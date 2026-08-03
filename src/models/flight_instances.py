from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID

from src.db import Base

flight_instance_status_enum = Enum(
    "scheduled",
    "boarding",
    "departed",
    "airborne",
    "landed",
    "delayed",
    "diverted",
    "cancelled",
    name="flight_instance_status",
)


class FlightInstance(Base):
    __tablename__ = "flight_instances"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    flight_definition_id = Column(
        UUID(as_uuid=True), ForeignKey("flight_definitions.id"), nullable=False, index=True
    )
    flight_date = Column(Date, nullable=False, index=True)
    scheduled_dep_utc = Column(DateTime(timezone=True), nullable=True)
    scheduled_arr_utc = Column(DateTime(timezone=True), nullable=True)
    actual_dep_utc = Column(DateTime(timezone=True), nullable=True)
    actual_arr_utc = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        flight_instance_status_enum, nullable=False, server_default=text("'scheduled'")
    )
    current_icao24 = Column(String(6), nullable=True)
    last_polled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
