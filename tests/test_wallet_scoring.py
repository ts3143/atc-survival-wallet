"""
Unit tests for src/lib/wallet_scoring.py (M3 Wallet Engine, spec section 7
"Provisional v0" formula). Pure-function tests — no DB required.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.lib.wallet_scoring import (
    BASE_GAIN_PCT,
    CANCELLATION_PCT,
    DELAY_PENALTY_PCT_PER_MIN,
    DIVERSION_PCT,
    GRACE_MINUTES,
    RESOLUTION_BONUS_PCT,
    VOLATILITY_MAX,
    VOLATILITY_MIN,
    FlightState,
    compute_volatility_multiplier,
    evaluate_tick,
)

STAKE = Decimal("100")
DEP = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
ARR = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)

POOL_AVG_STDDEV = Decimal("40")
LOW_VOL_STDDEV = Decimal("20")  # raw 0.5 -> clip floor
HIGH_VOL_STDDEV = Decimal("120")  # raw 3.0 -> clip ceiling

LOW_MULT = compute_volatility_multiplier(LOW_VOL_STDDEV, POOL_AVG_STDDEV)
HIGH_MULT = compute_volatility_multiplier(HIGH_VOL_STDDEV, POOL_AVG_STDDEV)


# ---------------------------------------------------------------------------
# compute_volatility_multiplier
# ---------------------------------------------------------------------------


def test_volatility_multiplier_neutral_at_pool_average():
    assert compute_volatility_multiplier(Decimal("40"), POOL_AVG_STDDEV) == Decimal("1.0")


def test_volatility_multiplier_clips_to_floor():
    # raw would be 20/40 = 0.5 exactly at the floor
    assert compute_volatility_multiplier(LOW_VOL_STDDEV, POOL_AVG_STDDEV) == VOLATILITY_MIN
    # something even lower should still clip to the same floor, not go below it
    assert compute_volatility_multiplier(Decimal("1"), POOL_AVG_STDDEV) == VOLATILITY_MIN


def test_volatility_multiplier_clips_to_ceiling():
    assert compute_volatility_multiplier(HIGH_VOL_STDDEV, POOL_AVG_STDDEV) == VOLATILITY_MAX
    assert compute_volatility_multiplier(Decimal("10000"), POOL_AVG_STDDEV) == VOLATILITY_MAX


def test_volatility_multiplier_defaults_neutral_on_missing_data():
    assert compute_volatility_multiplier(None, POOL_AVG_STDDEV) == Decimal("1.0")
    assert compute_volatility_multiplier(LOW_VOL_STDDEV, None) == Decimal("1.0")
    assert compute_volatility_multiplier(LOW_VOL_STDDEV, Decimal(0)) == Decimal("1.0")


# ---------------------------------------------------------------------------
# The 4 required mocked scenarios x 2 volatility levels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("multiplier", [LOW_MULT, HIGH_MULT], ids=["low_vol", "high_vol"])
class TestFourScenarios:
    def test_on_time_throughout(self, multiplier):
        """In-progress flight, right on schedule (no delay observed yet)."""
        flight = FlightState(
            status="airborne",
            scheduled_dep_utc=DEP,
            scheduled_arr_utc=ARR,
            actual_dep_utc=DEP,
            actual_arr_utc=None,
        )
        events, resolve_as, new_last_charged = evaluate_tick(STAKE, flight, multiplier)

        assert resolve_as is None
        assert new_last_charged == Decimal(0)
        assert len(events) == 1
        assert events[0].event_type == "gain_tick"
        expected = BASE_GAIN_PCT * STAKE * multiplier
        assert events[0].amount == expected
        assert events[0].amount > 0

    def test_delayed_20_minutes_first_tick(self, multiplier):
        """Departed 20 min late -> 5 min beyond the 15-min grace window,
        first time this pick has ever been ticked (last_charged=0)."""
        flight = FlightState(
            status="airborne",
            scheduled_dep_utc=DEP,
            scheduled_arr_utc=ARR,
            actual_dep_utc=DEP + timedelta(minutes=20),
            actual_arr_utc=None,
        )
        events, resolve_as, new_last_charged = evaluate_tick(STAKE, flight, multiplier)

        assert resolve_as is None
        assert len(events) == 1
        assert events[0].event_type == "delay_penalty"
        excess_minutes = Decimal(20) - GRACE_MINUTES
        expected = -(DELAY_PENALTY_PCT_PER_MIN * excess_minutes * STAKE * multiplier)
        assert events[0].amount == expected
        assert events[0].amount < 0
        assert events[0].metadata["excess_minutes"] == pytest.approx(5.0)
        assert new_last_charged == excess_minutes

    def test_cancelled_mid_flight(self, multiplier):
        flight = FlightState(
            status="cancelled",
            scheduled_dep_utc=DEP,
            scheduled_arr_utc=ARR,
            actual_dep_utc=DEP,
            actual_arr_utc=None,
        )
        events, resolve_as, _ = evaluate_tick(STAKE, flight, multiplier)

        assert resolve_as == "resolved_loss"
        assert len(events) == 1
        assert events[0].event_type == "cancellation_penalty"
        expected = -(CANCELLATION_PCT * STAKE * multiplier)
        assert events[0].amount == expected

    def test_diverted(self, multiplier):
        flight = FlightState(
            status="diverted",
            scheduled_dep_utc=DEP,
            scheduled_arr_utc=ARR,
            actual_dep_utc=DEP,
            actual_arr_utc=None,
        )
        events, resolve_as, _ = evaluate_tick(STAKE, flight, multiplier)

        assert resolve_as == "resolved_loss"
        assert len(events) == 1
        assert events[0].event_type == "diversion_penalty"
        expected = -(DIVERSION_PCT * STAKE * multiplier)
        assert events[0].amount == expected


def test_low_and_high_volatility_produce_different_magnitudes():
    """Sanity check that the multiplier is actually doing something —
    same scenario, different stddev, should scale the amount by 6x
    (HIGH_MULT / LOW_MULT = 3.0 / 0.5)."""
    flight = FlightState(
        status="airborne", scheduled_dep_utc=DEP, scheduled_arr_utc=ARR, actual_dep_utc=DEP, actual_arr_utc=None
    )
    low_events, _, _ = evaluate_tick(STAKE, flight, LOW_MULT)
    high_events, _, _ = evaluate_tick(STAKE, flight, HIGH_MULT)

    assert high_events[0].amount == low_events[0].amount * 6


# ---------------------------------------------------------------------------
# Incremental delay-penalty charging (the bug fix) — spec section 7
# "CRITICAL implementation note", found via M3 real-data testing (AA1205:
# identical -$76.32 penalty charged on 3 consecutive unchanged ticks).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("multiplier", [LOW_MULT, HIGH_MULT], ids=["low_vol", "high_vol"])
def test_static_delay_charges_once_then_zero(multiplier):
    """A delay that doesn't change across 3+ ticks should be charged on
    the first tick only; every subsequent tick at the same delay must
    produce a $0 delta (increment=0), not a repeat of the full penalty."""
    flight = FlightState(
        status="airborne",
        scheduled_dep_utc=DEP,
        scheduled_arr_utc=ARR,
        actual_dep_utc=DEP + timedelta(minutes=60),
        actual_arr_utc=None,
    )
    excess = Decimal(60) - GRACE_MINUTES  # 45
    expected_first_charge = -(DELAY_PENALTY_PCT_PER_MIN * excess * STAKE * multiplier)

    # tick 1: first time this delay is observed
    events1, resolve1, last_charged1 = evaluate_tick(STAKE, flight, multiplier, Decimal(0))
    assert events1[0].amount == expected_first_charge
    assert resolve1 is None
    assert last_charged1 == excess

    # tick 2 and 3: identical flight state, delay hasn't changed
    events2, resolve2, last_charged2 = evaluate_tick(STAKE, flight, multiplier, last_charged1)
    assert events2[0].amount == Decimal(0)
    assert last_charged2 == excess

    events3, resolve3, last_charged3 = evaluate_tick(STAKE, flight, multiplier, last_charged2)
    assert events3[0].amount == Decimal(0)
    assert last_charged3 == excess


@pytest.mark.parametrize("multiplier", [LOW_MULT, HIGH_MULT], ids=["low_vol", "high_vol"])
def test_increasing_delay_charges_only_the_increment(multiplier):
    """Delay grows across ticks (60min -> 75min -> 100min); each tick
    should charge only the newly-discovered excess, and the sum of all
    per-tick charges must equal a single charge for the final total
    excess (no double-charging, no under-charging)."""
    excess_60 = Decimal(60) - GRACE_MINUTES  # 45
    excess_75 = Decimal(75) - GRACE_MINUTES  # 60
    excess_100 = Decimal(100) - GRACE_MINUTES  # 85

    def flight_with_delay(minutes):
        return FlightState(
            status="airborne",
            scheduled_dep_utc=DEP,
            scheduled_arr_utc=ARR,
            actual_dep_utc=DEP + timedelta(minutes=minutes),
            actual_arr_utc=None,
        )

    events1, _, last_charged1 = evaluate_tick(STAKE, flight_with_delay(60), multiplier, Decimal(0))
    events2, _, last_charged2 = evaluate_tick(STAKE, flight_with_delay(75), multiplier, last_charged1)
    events3, _, last_charged3 = evaluate_tick(STAKE, flight_with_delay(100), multiplier, last_charged2)

    charge1 = events1[0].amount
    charge2 = events2[0].amount
    charge3 = events3[0].amount

    # tick 1: full charge for 45 min excess (first ever observation)
    assert charge1 == -(DELAY_PENALTY_PCT_PER_MIN * excess_60 * STAKE * multiplier)
    # tick 2: only the incremental 15 min (60 - 45), not the full 60
    increment_2 = excess_75 - excess_60
    assert charge2 == -(DELAY_PENALTY_PCT_PER_MIN * increment_2 * STAKE * multiplier)
    assert events2[0].metadata["increment_minutes"] == pytest.approx(float(increment_2))
    # tick 3: only the incremental 25 min (85 - 60), not the full 85
    increment_3 = excess_100 - excess_75
    assert charge3 == -(DELAY_PENALTY_PCT_PER_MIN * increment_3 * STAKE * multiplier)

    # sum of incremental charges == one single charge for the full final excess
    total_incremental = charge1 + charge2 + charge3
    single_full_charge = -(DELAY_PENALTY_PCT_PER_MIN * excess_100 * STAKE * multiplier)
    assert total_incremental == single_full_charge

    assert last_charged3 == excess_100


def test_delay_recovering_to_on_time_resets_last_charged():
    """If a later tick reads on-time (<=grace), last_charged_delay_minutes
    resets to 0 (matches spec step 4: update to the new current value,
    which is 0 within grace) — a defensive path, since actual_dep_utc is
    only ever set once in practice (see apply_match() in
    jobs/opensky_matcher.py), so this can't currently happen from real
    data, but the pure function still needs to behave correctly if called
    this way."""
    on_time_flight = FlightState(
        status="airborne", scheduled_dep_utc=DEP, scheduled_arr_utc=ARR, actual_dep_utc=DEP, actual_arr_utc=None
    )
    events, resolve_as, new_last_charged = evaluate_tick(STAKE, on_time_flight, Decimal("1.0"), Decimal("45"))
    assert events[0].event_type == "gain_tick"
    assert new_last_charged == Decimal(0)


# ---------------------------------------------------------------------------
# Landed / resolution paths — the +15% resolution bonus is only reachable
# via "landed"; landed-late also uses incremental delay charging.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("multiplier", [LOW_MULT, HIGH_MULT], ids=["low_vol", "high_vol"])
def test_landed_within_grace_gets_gain_and_bonus(multiplier):
    flight = FlightState(
        status="landed",
        scheduled_dep_utc=DEP,
        scheduled_arr_utc=ARR,
        actual_dep_utc=DEP,
        actual_arr_utc=ARR + timedelta(minutes=10),
    )
    events, resolve_as, _ = evaluate_tick(STAKE, flight, multiplier)

    assert resolve_as == "resolved_win"
    assert [e.event_type for e in events] == ["gain_tick", "resolution"]
    assert events[0].amount == BASE_GAIN_PCT * STAKE * multiplier
    assert events[1].amount == RESOLUTION_BONUS_PCT * STAKE * multiplier


@pytest.mark.parametrize("multiplier", [LOW_MULT, HIGH_MULT], ids=["low_vol", "high_vol"])
def test_landed_outside_grace_gets_penalty_no_bonus(multiplier):
    flight = FlightState(
        status="landed",
        scheduled_dep_utc=DEP,
        scheduled_arr_utc=ARR,
        actual_dep_utc=DEP,
        actual_arr_utc=ARR + timedelta(minutes=45),
    )
    events, resolve_as, _ = evaluate_tick(STAKE, flight, multiplier)

    assert resolve_as == "resolved_loss"
    assert [e.event_type for e in events] == ["delay_penalty"]
    excess_minutes = Decimal(45) - GRACE_MINUTES
    expected = -(DELAY_PENALTY_PCT_PER_MIN * excess_minutes * STAKE * multiplier)
    assert events[0].amount == expected


def test_landed_late_only_charges_increment_beyond_in_progress_delay():
    """A pick that already accrued in-progress delay-penalty charges
    (e.g. 45 min excess during boarding/departure) should, on landing
    with MORE delay (e.g. 70 min excess), be charged only the additional
    excess (25 min), not the full 70 again."""
    flight = FlightState(
        status="landed",
        scheduled_dep_utc=DEP,
        scheduled_arr_utc=ARR,
        actual_dep_utc=DEP + timedelta(minutes=60),
        actual_arr_utc=ARR + timedelta(minutes=85),  # 85 min late -> 70 excess
    )
    already_charged = Decimal(45)  # from an earlier in-progress tick
    events, resolve_as, new_last_charged = evaluate_tick(STAKE, flight, Decimal("1.0"), already_charged)

    excess = Decimal(85) - GRACE_MINUTES  # 70
    increment = excess - already_charged  # 25
    assert resolve_as == "resolved_loss"
    assert events[0].amount == -(DELAY_PENALTY_PCT_PER_MIN * increment * STAKE * Decimal("1.0"))
    assert new_last_charged == excess


def test_early_or_on_time_departure_never_negative():
    """No actual_dep_utc yet (hasn't been observed departing) should read
    as fully on-schedule, not penalized."""
    flight = FlightState(
        status="scheduled", scheduled_dep_utc=DEP, scheduled_arr_utc=ARR, actual_dep_utc=None, actual_arr_utc=None
    )
    events, resolve_as, _ = evaluate_tick(STAKE, flight, Decimal("1.0"))
    assert resolve_as is None
    assert events[0].event_type == "gain_tick"
    assert events[0].amount > 0


def test_early_departure_not_penalized_or_bonused():
    """Departing 10 minutes EARLY should read as delay=0 (floored), same
    as exactly on-time — this formula has no early-departure bonus."""
    flight = FlightState(
        status="airborne",
        scheduled_dep_utc=DEP,
        scheduled_arr_utc=ARR,
        actual_dep_utc=DEP - timedelta(minutes=10),
        actual_arr_utc=None,
    )
    events, resolve_as, _ = evaluate_tick(STAKE, flight, Decimal("1.0"))
    assert events[0].event_type == "gain_tick"
    assert events[0].amount == BASE_GAIN_PCT * STAKE * Decimal("1.0")
