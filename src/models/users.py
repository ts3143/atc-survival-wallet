from sqlalchemy import Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID

from src.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    email = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
