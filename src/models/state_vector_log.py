from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID

from src.db import Base


class StateVectorLog(Base):
    __tablename__ = "state_vector_log"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    flight_instance_id = Column(
        UUID(as_uuid=True), ForeignKey("flight_instances.id"), nullable=False, index=True
    )
    polled_at = Column(DateTime(timezone=True), nullable=False)
    latitude = Column(Numeric(9, 6), nullable=True)
    longitude = Column(Numeric(9, 6), nullable=True)
    altitude_m = Column(Numeric(10, 2), nullable=True)
    velocity_ms = Column(Numeric(8, 2), nullable=True)
    heading = Column(Numeric(6, 2), nullable=True)
    vertical_rate = Column(Numeric(8, 2), nullable=True)
    on_ground = Column(Boolean, nullable=True)
