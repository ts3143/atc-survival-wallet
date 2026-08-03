from sqlalchemy import Column, DateTime, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID

from src.db import Base


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    balance = Column(Numeric(14, 4), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
