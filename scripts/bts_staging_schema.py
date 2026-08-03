"""
Shared table definition for the BTS On-Time Performance staging table.

This is ETL infrastructure for the M0 pool-curation script, not part of the
app's domain schema (spec section 2) — it lives outside `src/models` so that
package stays an exact mirror of the spec. It's still managed by Alembic
(see migrations/versions/) so `alembic upgrade head` provisions the whole DB.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Time,
    text,
)

from src.db import Base

bts_ontime_performance_raw = Table(
    "bts_ontime_performance_raw",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("year", Integer, nullable=False),
    Column("month", Integer, nullable=False),
    Column("day_of_month", Integer, nullable=False),
    # BTS's own DayOfWeek convention: 1=Monday ... 7=Sunday
    Column("day_of_week", Integer, nullable=False),
    Column("flight_date", Date, nullable=False),
    Column("carrier_code", String(2), nullable=False),
    Column("flight_number", String(6), nullable=False),
    Column("origin_airport", String(3), nullable=False),
    Column("dest_airport", String(3), nullable=False),
    Column("crs_dep_time_local", Time, nullable=True),
    Column("crs_arr_time_local", Time, nullable=True),
    # signed minutes: negative = early, positive = late. NULL for
    # cancelled/diverted flights, which never recorded an arrival.
    Column("arr_delay_minutes", Numeric(7, 2), nullable=True),
    Column("arr_del15", Boolean, nullable=True),
    Column("cancelled", Boolean, nullable=False),
    Column("diverted", Boolean, nullable=False),
    Column("distance_miles", Numeric(8, 2), nullable=True),
    Column("loaded_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Index(
        "ix_bts_raw_carrier_flight_route",
        "carrier_code",
        "flight_number",
        "origin_airport",
        "dest_airport",
    ),
    # supports idempotent re-loads: delete-then-insert per (year, month)
    Index("ix_bts_raw_year_month", "year", "month"),
)
