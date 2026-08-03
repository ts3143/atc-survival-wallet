"""
Thin client for the AeroDataBox "Flight Status (single day)" endpoint, via
RapidAPI.

Verified against AeroDataBox's real OpenAPI spec
(https://doc.aerodatabox.com/docs/openapi-rapidapi-v1.yaml, fetched
2026-08-03, spec version 1.15.1.0) — not assumed. Relevant bits:

    GET https://aerodatabox.p.rapidapi.com/flights/{searchBy}/{searchParam}/{dateLocal}

    searchBy:    path param, enum. Spec's schema declares the canonical
                 value "Number" (PascalCase), but the parameter's own
                 description text consistently uses lowercase "number" as
                 the value to pass, and this is an ASP.NET Core API, whose
                 route enum binding is case-insensitive by default — so
                 both should work. This client sends lowercase "number" to
                 match the documented usage examples. If a real call ever
                 400s on this, that's the first thing to check.
    searchParam: flight number, IATA or ICAO, e.g. "AA1234" (no space
                 required).
    dateLocal:   path param, "YYYY-MM-DD".
    dateLocalRole: query param, default "Both" — we pass "Departure"
                 explicitly since we're checking a specific origin's
                 scheduled departure.

    Auth headers: X-RapidAPI-Key, X-RapidAPI-Host (must be exactly
                 "aerodatabox.p.rapidapi.com").

    Response: 200 -> JSON array of FlightContract (can be empty, or
                 contain multiple flights if the number is ambiguous /
                 codeshared that day — caller must filter by
                 origin/destination). 204 -> no content, flight not found
                 for that number/date. 400/401/451/503 -> documented error
                 responses. 429 (rate limited) is NOT part of AeroDataBox's
                 own spec — it's enforced at the RapidAPI gateway level,
                 standard across all RapidAPI-hosted APIs, so it's handled
                 here by status code alone.

    Each FlightContract has `departure.airport.iata`,
    `departure.scheduledTime.utc` / `.local`, and the equivalent under
    `arrival`. Times are ISO-8601 strings.

This endpoint is billed as "TIER 2" per the spec; exact RapidAPI unit cost
per call was not independently confirmed (the pricing page needs a live
fetch we were asked not to make) — AERODATABOX_COST_PER_CALL below is a
conservative default of 1 unit/call; adjust it if your RapidAPI dashboard
shows otherwise.
"""

from dataclasses import dataclass
from typing import Optional

import requests

from src.config import AERODATABOX_API_KEY

API_HOST = "aerodatabox.p.rapidapi.com"
BASE_URL = f"https://{API_HOST}"
AERODATABOX_COST_PER_CALL = 1


class AeroDataBoxError(Exception):
    """Base class for all AeroDataBox client errors."""


class AeroDataBoxRateLimited(AeroDataBoxError):
    """RapidAPI gateway returned 429 — quota/rate limit hit."""


class AeroDataBoxRequestError(AeroDataBoxError):
    """Non-2xx/204 response, or a network-level failure."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class FlightMovement:
    airport_iata: Optional[str]
    scheduled_utc: Optional[str]
    scheduled_local: Optional[str]


@dataclass
class FlightResult:
    number: str
    status: Optional[str]
    departure: FlightMovement
    arrival: FlightMovement


def _parse_movement(raw: dict) -> FlightMovement:
    airport = raw.get("airport") or {}
    scheduled = raw.get("scheduledTime") or {}
    return FlightMovement(
        airport_iata=airport.get("iata"),
        scheduled_utc=scheduled.get("utc"),
        scheduled_local=scheduled.get("local"),
    )


def get_flight_schedule(flight_number: str, date_local: str, timeout: int = 15) -> list:
    """
    Look up a flight's schedule for a specific local date.

    flight_number: e.g. "AA1234" (carrier_code + flight_number, no space).
    date_local: "YYYY-MM-DD".

    Returns a list of FlightResult (may be empty if no match — that's a
    normal outcome, not an error, since not every flight_number/date
    combination will have data).

    Raises AeroDataBoxRateLimited on HTTP 429, AeroDataBoxRequestError on
    any other non-2xx/204 response or network failure.
    """
    if not AERODATABOX_API_KEY:
        raise AeroDataBoxRequestError("AERODATABOX_API_KEY is not set in the environment")

    url = f"{BASE_URL}/flights/number/{flight_number}/{date_local}"
    headers = {
        "X-RapidAPI-Key": AERODATABOX_API_KEY,
        "X-RapidAPI-Host": API_HOST,
    }
    params = {"dateLocalRole": "Departure"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise AeroDataBoxRequestError(f"network error calling AeroDataBox: {exc}") from exc

    if resp.status_code == 429:
        raise AeroDataBoxRateLimited("RapidAPI gateway returned 429 (rate limited / quota exceeded)")

    if resp.status_code == 204:
        return []

    if not resp.ok:
        raise AeroDataBoxRequestError(
            f"AeroDataBox returned HTTP {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise AeroDataBoxRequestError(f"AeroDataBox returned non-JSON response: {exc}") from exc

    results = []
    for item in data:
        results.append(
            FlightResult(
                number=item.get("number"),
                status=item.get("status"),
                departure=_parse_movement(item.get("departure") or {}),
                arrival=_parse_movement(item.get("arrival") or {}),
            )
        )
    return results
