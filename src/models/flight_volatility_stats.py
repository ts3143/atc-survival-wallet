from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from src.db import Base


class FlightVolatilityStats(Base):
    __tablename__ = "flight_volatility_stats"
    __table_args__ = (
        # one row per flight_definitions entry (see spec section 2)
        UniqueConstraint("flight_definition_id", name="uq_flight_volatility_stats_flight_definition_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    flight_definition_id = Column(
        UUID(as_uuid=True), ForeignKey("flight_definitions.id"), nullable=False
    )
    on_time_pct = Column(Numeric(5, 2), nullable=True)
    avg_delay_minutes = Column(Numeric(8, 2), nullable=True)
    delay_stddev = Column(Numeric(8, 2), nullable=True)
    cancellation_pct = Column(Numeric(5, 2), nullable=True)
    diversion_pct = Column(Numeric(5, 2), nullable=True)
    sample_size = Column(Integer, nullable=True)
    computed_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
