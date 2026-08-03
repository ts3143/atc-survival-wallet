from sqlalchemy import Column, DateTime, Enum, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.db import Base

wallet_event_type_enum = Enum(
    "decay_tick",
    "gain_tick",
    "delay_penalty",
    "cancellation_penalty",
    "diversion_penalty",
    "cash_out",
    "resolution",
    name="wallet_event_type",
)


class WalletEvent(Base):
    __tablename__ = "wallet_events"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    wallet_pick_id = Column(
        UUID(as_uuid=True), ForeignKey("wallet_picks.id"), nullable=False, index=True
    )
    event_type = Column(wallet_event_type_enum, nullable=False)
    amount = Column(Numeric(14, 4), nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    # Python attribute renamed from "metadata" (reserved on declarative Base); DB column stays "metadata".
    event_metadata = Column("metadata", JSONB, nullable=True)
