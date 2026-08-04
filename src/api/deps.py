"""
Shared FastAPI dependencies for the M4 API layer.

get_test_wallet(): no real auth exists yet (users table was empty, no auth
code anywhere at time of writing — confirmed before building this, not
assumed). Per M4's explicit scope, this lazily creates and reuses a single
hardcoded test user + wallet rather than blocking M4 on building real auth.
Every request in this API acts as this one user — there is no login, no
session, no multi-user support. Revisit before this goes anywhere near
real users.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.users import User
from src.models.wallets import Wallet

TEST_USER_EMAIL = "test-player@atc-survival-wallet.local"
TEST_WALLET_STARTING_BALANCE = Decimal("1000.0000")  # arbitrary, not spec'd


def get_test_wallet(session: Session) -> Wallet:
    user = session.execute(select(User).where(User.email == TEST_USER_EMAIL)).scalar_one_or_none()
    if user is None:
        user = User(email=TEST_USER_EMAIL)
        session.add(user)
        session.flush()

    wallet = session.execute(select(Wallet).where(Wallet.user_id == user.id)).scalar_one_or_none()
    if wallet is None:
        wallet = Wallet(user_id=user.id, balance=TEST_WALLET_STARTING_BALANCE)
        session.add(wallet)
        session.flush()

    return wallet
