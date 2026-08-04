"""
Infrastructure tables for the jobs/ scheduled jobs — not part of the app's
domain schema (spec section 2), so kept out of src/models. Still managed by
Alembic (attached to Base.metadata) so `alembic upgrade head` provisions
everything in one shot.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.db import Base

# Generic per-job rotation cursor. Used by schedule_verifier to cycle
# through the flight_definitions pool ~9/day over ~14 days without relying
# on calendar-date math (which would silently skip coverage on days the
# job doesn't run) — the cursor only advances on an actual run.
job_cursors = Table(
    "job_cursors",
    Base.metadata,
    Column("job_name", String, primary_key=True),
    Column("cursor", Integer, nullable=False, server_default=text("0")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)

# Every AeroDataBox call made, successful or not — lets us both audit usage
# against the 600 req/month free-tier budget and see exactly what was
# checked and when.
aerodatabox_call_log = Table(
    "aerodatabox_call_log",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "flight_definition_id",
        UUID(as_uuid=True),
        ForeignKey("flight_definitions.id"),
        nullable=False,
        index=True,
    ),
    Column("endpoint", String, nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    # the flight_date being verified (per origin-local calendar) — distinct
    # from requested_at, which is when the call actually happened. Needed
    # so the "already verified today" cache check compares against the
    # right date even when --date backfills are run out of band.
    Column("flight_date", Date, nullable=False),
    Column("success", Boolean, nullable=False),
    Column("status_code", Integer, nullable=True),
    Column("error_message", Text, nullable=True),
)

# Raw /states/all poll responses — temporary storage only, per M2's "store
# raw responses, don't build matching logic yet" scope. No FK to
# flight_instances; that comes with the callsign-matching step later.
opensky_raw_poll_log = Table(
    "opensky_raw_poll_log",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("polled_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("lamin", Numeric(8, 4), nullable=False),
    Column("lomin", Numeric(8, 4), nullable=False),
    Column("lamax", Numeric(8, 4), nullable=False),
    Column("lomax", Numeric(8, 4), nullable=False),
    Column("http_status", Integer, nullable=True),
    Column("state_vector_count", Integer, nullable=True),
    Column("credits_remaining", Integer, nullable=True),
    Column("success", Boolean, nullable=False),
    Column("error_message", Text, nullable=True),
    Column("raw_response", JSONB, nullable=True),
)
