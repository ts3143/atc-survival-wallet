#!/usr/bin/env python
"""
M3 — Wallet Engine (DB-facing wrapper around src/lib/wallet_scoring.py).

For every active wallet_pick, looks up its flight_instance's current state,
computes the pool-average delay_stddev (across active flight_definitions)
for the volatility multiplier, calls evaluate_tick(), writes the resulting
wallet_events row(s), updates wallets.balance, and — if the tick resolved
the pick — updates wallet_picks.status/resolved_amount.

Meant to run after each opensky_poller.py poll cycle (wired in there), but
is also directly callable/importable for manual runs and testing:
    python -m jobs.wallet_engine
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from src.db import SessionLocal
from src.lib.wallet_scoring import FlightState, compute_volatility_multiplier, evaluate_tick
from src.models.flight_definitions import FlightDefinition
from src.models.flight_instances import FlightInstance
from src.models.flight_volatility_stats import FlightVolatilityStats
from src.models.wallet_events import WalletEvent
from src.models.wallet_picks import WalletPick
from src.models.wallets import Wallet


def get_pool_avg_delay_stddev(session) -> Decimal:
    avg = session.execute(
        select(func.avg(FlightVolatilityStats.delay_stddev))
        .select_from(FlightVolatilityStats)
        .join(FlightDefinition, FlightDefinition.id == FlightVolatilityStats.flight_definition_id)
        .where(FlightDefinition.active.is_(True))
    ).scalar_one()
    return avg


def get_prior_events_sum(session, wallet_pick_id) -> Decimal:
    total = session.execute(
        select(func.coalesce(func.sum(WalletEvent.amount), 0)).where(
            WalletEvent.wallet_pick_id == wallet_pick_id
        )
    ).scalar_one()
    return Decimal(total)


def process_pick(session, pick: WalletPick, pool_avg_stddev: Decimal, occurred_at: datetime) -> dict:
    # Explicit precondition, not just implicit reliance on the caller's
    # query filter: cancellation/diversion/resolution are one-time
    # terminal events (see TERMINAL_EVENTS in wallet_scoring.py) — once a
    # pick resolves, nothing should ever call process_pick on it again.
    assert pick.status == "active", f"process_pick called on non-active pick {pick.id} (status={pick.status})"

    fi = session.get(FlightInstance, pick.flight_instance_id)
    fd = session.get(FlightDefinition, fi.flight_definition_id)
    vstats = session.execute(
        select(FlightVolatilityStats).where(FlightVolatilityStats.flight_definition_id == fd.id)
    ).scalar_one_or_none()
    delay_stddev = vstats.delay_stddev if vstats else None

    multiplier = compute_volatility_multiplier(delay_stddev, pool_avg_stddev)

    flight_state = FlightState(
        status=fi.status,
        scheduled_dep_utc=fi.scheduled_dep_utc,
        scheduled_arr_utc=fi.scheduled_arr_utc,
        actual_dep_utc=fi.actual_dep_utc,
        actual_arr_utc=fi.actual_arr_utc,
    )

    events, resolve_as, new_last_charged = evaluate_tick(
        pick.staked_amount, flight_state, multiplier, pick.last_charged_delay_minutes
    )
    pick.last_charged_delay_minutes = new_last_charged

    total_delta = Decimal(0)
    for ev in events:
        metadata = {
            **ev.metadata,
            "volatility_multiplier": float(multiplier),
            "source": "opensky_poll",
        }
        session.add(
            WalletEvent(
                wallet_pick_id=pick.id,
                event_type=ev.event_type,
                amount=ev.amount,
                occurred_at=occurred_at,
                event_metadata=metadata,
            )
        )
        total_delta += ev.amount

    wallet = session.get(Wallet, pick.wallet_id)
    wallet.balance += total_delta

    if resolve_as:
        prior_sum = get_prior_events_sum(session, pick.id)
        pick.status = resolve_as
        pick.resolved_amount = pick.staked_amount + prior_sum + total_delta

    return {
        "pick_id": pick.id,
        "event_types": [ev.event_type for ev in events],
        "total_delta": total_delta,
        "resolved_as": resolve_as,
        "multiplier": multiplier,
    }


def run_wallet_tick(session, occurred_at: datetime = None) -> list:
    occurred_at = occurred_at or datetime.now(timezone.utc)
    pool_avg_stddev = get_pool_avg_delay_stddev(session)

    active_picks = (
        session.execute(select(WalletPick).where(WalletPick.status == "active")).scalars().all()
    )

    results = [process_pick(session, pick, pool_avg_stddev, occurred_at) for pick in active_picks]
    session.commit()
    return results


def main():
    with SessionLocal() as session:
        results = run_wallet_tick(session)

    print(f"Processed {len(results)} active wallet_pick(s)")
    for r in results:
        resolved = f" -> {r['resolved_as']}" if r["resolved_as"] else ""
        print(
            f"  pick {r['pick_id']}: {r['event_types']} delta={r['total_delta']:+.4f} "
            f"(x{r['multiplier']:.2f}){resolved}"
        )


if __name__ == "__main__":
    main()
