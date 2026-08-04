"""
Thin client for the OpenSky Network REST API — OAuth2 client-credentials
auth + /states/all polling.

Verified against OpenSky's real docs
(https://openskynetwork.github.io/opensky-api/rest.html, fetched
2026-08-03) — not assumed. Relevant bits:

Auth (OAuth2 client credentials):
    POST https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token
    Content-Type: application/x-www-form-urlencoded
    Body: grant_type=client_credentials&client_id=...&client_secret=...
    Response JSON: {"access_token": "...", "expires_in": 1800, ...}
    Tokens expire after 30 minutes (1800s); a 401 means the token expired.
    Use as: Authorization: Bearer <access_token>

/states/all:
    GET https://opensky-network.org/api/states/all
    Query params: lamin, lomin, lamax, lomax (bounding box)

Credits (tracked in a separate bucket per endpoint family — /states/*,
/tracks/*, /flights/*):
    Tier            Credits   Refill
    Anonymous       400       daily
    Standard user   4,000     daily
    Active feeder   8,000     daily
    Licensed user   14,400    hourly

    /states/all cost by bounding box area (lat range x lon range, sq
    degrees):
        <= 25 sq deg or serial-only query: 1 credit
        25-100 sq deg:                     2 credits
        100-400 sq deg:                    3 credits
        > 400 sq deg or global:            4 credits

    Response headers: X-Rate-Limit-Remaining (remaining balance in the
    current bucket); on 429, X-Rate-Limit-Retry-After-Seconds.
"""

import time
from dataclasses import dataclass
from typing import Optional

import requests

from src.config import OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET

TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)
STATES_ALL_URL = "https://opensky-network.org/api/states/all"

# proactively refresh this many seconds before actual expiry
TOKEN_REFRESH_MARGIN_SECONDS = 30
DEFAULT_TOKEN_LIFETIME_SECONDS = 1800  # OpenSky's documented default


class OpenSkyError(Exception):
    pass


class OpenSkyAuthError(OpenSkyError):
    pass


class OpenSkyRateLimited(OpenSkyError):
    def __init__(self, message: str, retry_after_seconds: Optional[int] = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class OpenSkyRequestError(OpenSkyError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class TokenManager:
    """Fetches and caches an OAuth2 client-credentials token, refreshing
    proactively before it expires (mirrors OpenSky's own documented
    TokenManager example)."""

    def __init__(self, client_id: str = OPENSKY_CLIENT_ID, client_secret: str = OPENSKY_CLIENT_SECRET):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    def get_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        return self._fetch_token()

    def _fetch_token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise OpenSkyAuthError("OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET not set in environment")

        try:
            resp = requests.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            raise OpenSkyAuthError(f"network error fetching OpenSky token: {exc}") from exc

        if not resp.ok:
            raise OpenSkyAuthError(f"OpenSky token request failed: HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise OpenSkyAuthError(f"OpenSky token response missing access_token: {data}")

        expires_in = data.get("expires_in", DEFAULT_TOKEN_LIFETIME_SECONDS)
        self._token = token
        self._expires_at = time.monotonic() + expires_in - TOKEN_REFRESH_MARGIN_SECONDS
        return token

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.get_token()}"}


@dataclass
class StatesAllResult:
    status_code: int
    raw_json: Optional[dict]
    state_vector_count: Optional[int]
    rate_limit_remaining: Optional[int]
    retry_after_seconds: Optional[int]


def get_states_all(
    token_manager: TokenManager,
    lamin: float,
    lomin: float,
    lamax: float,
    lomax: float,
    timeout: int = 20,
) -> StatesAllResult:
    params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}

    try:
        resp = requests.get(
            STATES_ALL_URL, headers=token_manager.headers(), params=params, timeout=timeout
        )
    except requests.RequestException as exc:
        raise OpenSkyRequestError(f"network error calling /states/all: {exc}") from exc

    rate_limit_remaining = resp.headers.get("X-Rate-Limit-Remaining")
    rate_limit_remaining = int(rate_limit_remaining) if rate_limit_remaining is not None else None

    if resp.status_code == 429:
        retry_after = resp.headers.get("X-Rate-Limit-Retry-After-Seconds")
        raise OpenSkyRateLimited(
            "OpenSky rate limited (429)",
            retry_after_seconds=int(retry_after) if retry_after is not None else None,
        )

    if resp.status_code == 401:
        raise OpenSkyAuthError("OpenSky returned 401 (token expired/invalid)")

    if not resp.ok:
        raise OpenSkyRequestError(
            f"OpenSky /states/all returned HTTP {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )

    data = resp.json()
    states = data.get("states") or []

    return StatesAllResult(
        status_code=resp.status_code,
        raw_json=data,
        state_vector_count=len(states),
        rate_limit_remaining=rate_limit_remaining,
        retry_after_seconds=None,
    )
