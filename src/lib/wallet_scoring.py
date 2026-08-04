"""
M3 — Wallet Engine scoring logic (spec section 7, "Provisional v0").

Pure functions only — no DB access — so the scoring math is unit-testable
independent of live data. jobs/wallet_engine.py is the thin DB-facing
wrapper that fetches state, calls evaluate_tick(), and persists results.

Formula (verbatim from spec section 7, explicitly marked provisional and
expected to need retuning once seen against real numbers):
    Grace window:              15 minutes
    Base tick gain:             +2% of stake per tick, while on schedule
    Delay penalty:               -1% of stake per minute beyond grace
    Cancellation:                -50% of stake (flat, terminal)
    Diversion:                   -25% of stake (flat, terminal)
    Volatility multiplier:       delay_stddev / pool-average delay_stddev,
                                  clipped to [0.5x, 3.0x], applied to both
                                  gains and penalties
    On-time resolution bonus:    +15% of stake, once, if landed within grace

Design decisions made to turn this provisional prose into code (all
worth revisiting once real numbers are in — see jobs/README.md):

1. "On schedule" for an in-progress flight (status scheduled/departed/
   airborne — the DB enum has no separate "not_yet_departed" value, that's
   a diagnostic-only concept from M2c) is evaluated against DEPARTURE
   delay (actual_dep_utc vs scheduled_dep_utc). A flight with no
   actual_dep_utc yet (hasn't been observed departing) is treated as
   on-schedule (delay=0) — we simply don't have evidence otherwise.

2. Once landed, the relevant delay switches to ARRIVAL delay
   (actual_arr_utc vs scheduled_arr_utc) — that's the more complete,
   final picture, and it's what the spec's own resolution-bonus rule
   ("if flight lands within grace") is checking against.

3. FIXED BUG (found via M3 real-data testing against AA1205, confirmed in
   spec section 7's "CRITICAL implementation note"): the delay penalty is
   a *cumulative* quantity (total elapsed delay minutes beyond grace), not
   a per-tick event, so it must be charged incrementally, not
   re-evaluated from scratch and charged in full every tick. Each
   wallet_pick persists `last_charged_delay_minutes` — the excess-over-
   grace minutes already billed. Each tick:
       current = max(0, observed_delay_minutes - GRACE_MINUTES)
       increment = max(0, current - last_charged_delay_minutes)
       charge = -1% * increment * stake * volatility_multiplier
       last_charged_delay_minutes = current
   A static delay (unchanged actual_dep_utc, which is the normal case
   once a flight has departed — see apply_match() in
   jobs/opensky_matcher.py, which only ever sets actual_dep_utc once)
   therefore charges once, then zero on every subsequent tick, exactly as
   intended. This module applies the SAME incremental model to the
   landed-late path too (not just in-progress) — the spec's fix
   description focuses on the in-progress case since that's what AA1205
   demonstrated, but leaving the landed-late charge as a full
   non-incremental amount would just be a milder recurrence of the same
   bug if a pick had already accrued some in-progress delay-penalty
   charges before landing; treating last_charged_delay_minutes as one
   continuous "how much lateness have we already billed for" counter
   across both phases avoids that.

4. Cancellation and diversion are explicit, one-time, pick-resolving
   events (TERMINAL_EVENTS below) — never repeated, no delay-minutes
   bookkeeping involved. The caller (jobs/wallet_engine.py) must not call
   evaluate_tick again for a pick once resolve_as is returned; it only
   ever queries wallet_picks with status='active', which enforces this
   after the pick's status is updated.

5. Landing outside the grace window resolves the pick as resolved_loss
   (no bonus, and it already accrued delay penalties along the way) —
   the spec doesn't explicitly state the resolution status for "landed
   late," this is the natural symmetric reading.

6. The wallet_event_type enum includes "decay_tick", which this v0
   formula never produces — per spec section 7, it's reserved for a
   future idle-wallet-cost mechanic that hasn't been designed yet, not
   something this implementation should invent a use for.

7. This module does NOT touch wallets.balance or handle stake deduction
   at pick-creation time — that flow is out of scope for M3 (no pick-
   creation event type exists in wallet_event_type either). The tick
   engine only ever ADDS the computed delta to balance as it accrues.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

GRACE_MINUTES = Decimal(15)
BASE_GAIN_PCT = Decimal("0.02")
DELAY_PENALTY_PCT_PER_MIN = Decimal("0.01")
CANCELLATION_PCT = Decimal("0.50")
DIVERSION_PCT = Decimal("0.25")
RESOLUTION_BONUS_PCT = Decimal("0.15")
VOLATILITY_MIN = Decimal("0.5")
VOLATILITY_MAX = Decimal("3.0")

# Terminal, one-time events: no delay-minutes bookkeeping, no repeat
# ticks — the pick resolves immediately as resolved_loss. Explicit table
# (not implicit if-branches) per spec section 7's terminality note.
TERMINAL_EVENTS = {
    "cancelled": ("cancellation_penalty", CANCELLATION_PCT),
    "diverted": ("diversion_penalty", DIVERSION_PCT),
}


@dataclass
class FlightState:
    status: str
    scheduled_dep_utc: Optional[datetime]
    scheduled_arr_utc: Optional[datetime]
    actual_dep_utc: Optional[datetime]
    actual_arr_utc: Optional[datetime]


@dataclass
class TickEvent:
    event_type: str
    amount: Decimal
    metadata: dict


def compute_volatility_multiplier(delay_stddev, pool_avg_stddev) -> Decimal:
    """delay_stddev / pool average, clipped to [0.5, 3.0]. Defaults to a
    neutral 1.0x if either input is missing or the pool average is zero."""
    if delay_stddev is None or pool_avg_stddev is None or pool_avg_stddev == 0:
        return Decimal("1.0")
    raw = Decimal(delay_stddev) / Decimal(pool_avg_stddev)
    return max(VOLATILITY_MIN, min(VOLATILITY_MAX, raw))


def _delay_minutes(scheduled: Optional[datetime], actual: Optional[datetime]) -> Decimal:
    """Minutes late, floored at 0 (early/on-time both read as 0 — this
    formula has no early-departure bonus)."""
    if scheduled is None or actual is None:
        return Decimal(0)
    delta_minutes = Decimal((actual - scheduled).total_seconds()) / Decimal(60)
    return max(Decimal(0), delta_minutes)


def _excess_over_grace(scheduled: Optional[datetime], actual: Optional[datetime]) -> Decimal:
    return max(Decimal(0), _delay_minutes(scheduled, actual) - GRACE_MINUTES)


def _incremental_delay_charge(current_excess: Decimal, last_charged_delay_minutes: Decimal, stake: Decimal, volatility_multiplier: Decimal):
    """Returns (charge_amount, new_last_charged_delay_minutes). charge_amount
    is 0 (not omitted) when the excess hasn't grown since the last charge —
    the caller decides whether to emit a $0 event or skip it."""
    last_charged = Decimal(last_charged_delay_minutes or 0)
    increment = max(Decimal(0), current_excess - last_charged)
    charge = -(DELAY_PENALTY_PCT_PER_MIN * increment * stake * volatility_multiplier)
    return charge, current_excess


def evaluate_tick(stake: Decimal, flight: FlightState, volatility_multiplier: Decimal, last_charged_delay_minutes: Decimal = Decimal(0)):
    """Returns (events: list[TickEvent], resolve_as: str | None,
    new_last_charged_delay_minutes: Decimal).

    resolve_as, when not None, is the terminal wallet_picks.status the
    caller should apply ("resolved_win" or "resolved_loss") — the caller
    must not call evaluate_tick again for this pick afterward (see
    TERMINAL_EVENTS / module docstring point 4).
    """
    stake = Decimal(stake)
    last_charged_delay_minutes = Decimal(last_charged_delay_minutes or 0)

    if flight.status in TERMINAL_EVENTS:
        event_type, pct = TERMINAL_EVENTS[flight.status]
        amount = -(pct * stake * volatility_multiplier)
        events = [TickEvent(event_type, amount, {"flight_status": flight.status})]
        return events, "resolved_loss", last_charged_delay_minutes

    if flight.status == "landed":
        raw_delay = _delay_minutes(flight.scheduled_arr_utc, flight.actual_arr_utc)
        meta = {"flight_status": "landed", "delay_minutes": float(raw_delay)}

        if raw_delay <= GRACE_MINUTES:
            events = [
                TickEvent("gain_tick", BASE_GAIN_PCT * stake * volatility_multiplier, {**meta, "phase": "landed_on_time"}),
                TickEvent("resolution", RESOLUTION_BONUS_PCT * stake * volatility_multiplier, meta),
            ]
            return events, "resolved_win", last_charged_delay_minutes

        current_excess = raw_delay - GRACE_MINUTES
        charge, new_last_charged = _incremental_delay_charge(current_excess, last_charged_delay_minutes, stake, volatility_multiplier)
        events = [
            TickEvent(
                "delay_penalty",
                charge,
                {**meta, "excess_minutes": float(current_excess), "increment_minutes": float(new_last_charged - last_charged_delay_minutes), "phase": "landed_late"},
            )
        ]
        return events, "resolved_loss", new_last_charged

    # in-progress: scheduled / boarding / departed / airborne / delayed —
    # score against observed departure delay (or 0 if not yet observed)
    raw_delay = _delay_minutes(flight.scheduled_dep_utc, flight.actual_dep_utc)
    meta = {"flight_status": flight.status, "delay_minutes": float(raw_delay), "phase": "in_progress"}

    if raw_delay <= GRACE_MINUTES:
        amount = BASE_GAIN_PCT * stake * volatility_multiplier
        # excess is 0 here; still reset last_charged down to 0 in case a
        # previously-observed delay reading somehow recovered to on-time
        return [TickEvent("gain_tick", amount, meta)], None, Decimal(0)

    current_excess = raw_delay - GRACE_MINUTES
    charge, new_last_charged = _incremental_delay_charge(current_excess, last_charged_delay_minutes, stake, volatility_multiplier)
    events = [
        TickEvent(
            "delay_penalty",
            charge,
            {**meta, "excess_minutes": float(current_excess), "increment_minutes": float(new_last_charged - last_charged_delay_minutes)},
        )
    ]
    return events, None, new_last_charged
