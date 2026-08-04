from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps import get_test_wallet
from src.api.schemas import (
    CreateWalletPickRequest,
    FlightInstanceOut,
    PickFlightSummary,
    WalletEventOut,
    WalletOut,
    WalletPickOut,
)
from src.db import get_db
from src.models.flight_definitions import FlightDefinition
from src.models.flight_instances import FlightInstance
from src.models.wallet_events import WalletEvent
from src.models.wallet_picks import WalletPick
from src.models.wallets import Wallet

router = APIRouter(prefix="/api", tags=["wallet"])


def _build_pick_out(pick: WalletPick, fi: FlightInstance, fd: FlightDefinition) -> WalletPickOut:
    return WalletPickOut(
        id=pick.id,
        staked_amount=pick.staked_amount,
        status=pick.status,
        resolved_amount=pick.resolved_amount,
        last_charged_delay_minutes=pick.last_charged_delay_minutes,
        created_at=pick.created_at,
        cashed_out_at=pick.cashed_out_at,
        flight=PickFlightSummary(
            carrier_code=fd.carrier_code,
            flight_number=fd.flight_number,
            origin_airport=fd.origin_airport,
            dest_airport=fd.dest_airport,
        ),
        flight_instance=FlightInstanceOut.model_validate(fi),
    )


@router.get("/wallet", response_model=WalletOut)
def get_wallet(db: Session = Depends(get_db)):
    wallet = get_test_wallet(db)
    db.commit()  # persist a lazily-created test user/wallet

    rows = db.execute(
        select(WalletPick, FlightInstance, FlightDefinition)
        .join(FlightInstance, FlightInstance.id == WalletPick.flight_instance_id)
        .join(FlightDefinition, FlightDefinition.id == FlightInstance.flight_definition_id)
        .where(WalletPick.wallet_id == wallet.id)
        .order_by(WalletPick.created_at.desc())
    ).all()

    return WalletOut(
        id=wallet.id,
        balance=wallet.balance,
        started_at=wallet.started_at,
        picks=[_build_pick_out(pick, fi, fd) for pick, fi, fd in rows],
    )


@router.post("/wallet-picks", response_model=WalletPickOut, status_code=201)
def create_wallet_pick(body: CreateWalletPickRequest, db: Session = Depends(get_db)):
    if body.staked_amount <= 0:
        raise HTTPException(status_code=400, detail="staked_amount must be positive")

    fd = db.get(FlightDefinition, body.flight_definition_id)
    if fd is None or not fd.active:
        raise HTTPException(status_code=404, detail="flight not found")

    # "current" instance: most recent by flight_date, same simplification
    # as GET /api/flights/{id} — see that endpoint's comment.
    fi = db.execute(
        select(FlightInstance)
        .where(FlightInstance.flight_definition_id == fd.id)
        .order_by(FlightInstance.flight_date.desc(), FlightInstance.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if fi is None:
        raise HTTPException(
            status_code=409,
            detail="no flight_instance exists yet for this flight — the schedule refresher (M1) hasn't run for it",
        )

    wallet = get_test_wallet(db)

    pick = WalletPick(
        wallet_id=wallet.id,
        flight_instance_id=fi.id,
        staked_amount=body.staked_amount,
        status="active",
    )
    db.add(pick)
    db.commit()
    db.refresh(pick)

    return _build_pick_out(pick, fi, fd)


@router.get("/wallet-picks/{pick_id}/events", response_model=List[WalletEventOut])
def get_wallet_pick_events(pick_id: UUID, db: Session = Depends(get_db)):
    pick = db.get(WalletPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail="pick not found")

    events = db.execute(
        select(WalletEvent).where(WalletEvent.wallet_pick_id == pick_id).order_by(WalletEvent.occurred_at.asc())
    ).scalars().all()

    return [WalletEventOut.model_validate(e) for e in events]
