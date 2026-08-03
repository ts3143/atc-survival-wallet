from sqlalchemy import Column, DateTime, Enum, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID

from src.db import Base

wallet_pick_status_enum = Enum(
    "active", "resolved_win", "resolved_loss", "cashed_out", name="wallet_pick_status"
)


class WalletPick(Base):
    __tablename__ = "wallet_picks"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    wallet_id = Column(UUID(as_uuid=True), ForeignKey("wallets.id"), nullable=False, index=True)
    flight_instance_id = Column(
        UUID(as_uuid=True), ForeignKey("flight_instances.id"), nullable=False, index=True
    )
    staked_amount = Column(Numeric(14, 4), nullable=False)
    status = Column(wallet_pick_status_enum, nullable=False, server_default=text("'active'"))
    resolved_amount = Column(Numeric(14, 4), nullable=True)
    cashed_out_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
