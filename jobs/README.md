# jobs

Scheduled background jobs, run outside the request/response cycle (cron, worker, etc).

## M1 — Schedule Refresher + Verifier

The original spec called for a single daily job that calls AeroDataBox once
per active flight to populate `flight_instances`. At ~123 pool flights that's
~3,700 calls/month against a 600/month free-tier budget, so M1 is split into
two jobs instead:

### `schedule_refresher.py` — daily, zero API calls

For every active `flight_definitions` row, ensures a `flight_instances` row
exists for "today" (computed per-flight in the *origin airport's* local
timezone — see `src/lib/airport_timezones.py`) with `scheduled_dep_utc` /
`scheduled_arr_utc` derived from `typical_dep_time` / `typical_arr_time`
(the BTS-sourced values from M0). This is the default source of truth for
scheduled times and never calls AeroDataBox.

```bash
python -m jobs.schedule_refresher
python -m jobs.schedule_refresher --date 2026-08-10   # backfill/testing
```

Idempotent — if today's row already has a scheduled time (from a prior run
today, or because `schedule_verifier` already wrote a verified value for
it today), it's left alone.

Verified against the real pool: ran twice against the live DB — first run
created all 123 `flight_instances` rows with correct DST-aware UTC
conversions (including a same-airport red-eye that correctly rolls to the
next calendar day at the destination), second run skipped all 123
(idempotency confirmed).

### `schedule_verifier.py` — low-frequency, calls AeroDataBox

Cycles through the active pool over 14 days (~9 flights/day for 123
flights) via a persisted rotation cursor (`job_cursors` table — advances
each run, not calendar-based, so it self-heals if a day's run is missed).
For each flight selected on a given day:

1. Calls AeroDataBox's flight-status-by-number endpoint to confirm today's
   actual scheduled departure/arrival.
2. If the confirmed time differs from `flight_definitions.typical_dep_time`
   by more than 10 minutes, logs a discrepancy and corrects
   `flight_definitions` (both `typical_dep_time`/`typical_arr_time` and
   `last_verified_at` — the latter is bumped on every successful check,
   discrepancy or not).
3. Upserts today's `flight_instances` row with the AeroDataBox-confirmed
   time (more authoritative than the BTS-derived default) — this is what
   makes `schedule_refresher`'s cache check meaningful: once this job has
   written a verified value for today, the refresher leaves it alone.

```bash
python -m jobs.schedule_verifier
python -m jobs.schedule_verifier --date 2026-08-10   # backfill/testing
```

**AeroDataBox contract** (verified against the real OpenAPI spec at
`https://doc.aerodatabox.com/docs/openapi-rapidapi-v1.yaml`, spec version
1.15.1.0, not assumed — see `src/lib/aerodatabox.py`'s module docstring for
the full trail): `GET
https://aerodatabox.p.rapidapi.com/flights/number/{flightNumber}/{YYYY-MM-DD}`,
auth via `X-RapidAPI-Key` / `X-RapidAPI-Host` headers, response is a JSON
array of flights (filter by `departure.airport.iata` /
`arrival.airport.iata` to find the one matching our route) with
`departure.scheduledTime.utc` / `.local` and the equivalent under
`arrival`. One open item: the spec's `searchBy` enum is technically
PascalCase (`Number`), but the parameter's own description text and every
worked example use lowercase `number` — the client sends lowercase. If the
very first real call 400s, that's the first thing to check.

**Caching / idempotency**: a flight is skipped (no API call) if it was
already *successfully* verified for that exact `flight_date` — tracked via
`aerodatabox_call_log`, which this job is the sole writer of, not via
`flight_instances` (which `schedule_refresher` populates for every active
flight every day regardless of rotation, so gating on "does
`flight_instances` already have a scheduled time" would make this job a
permanent no-op).

**Budget tracking**: every call, successful or not, is logged to
`aerodatabox_call_log` (timestamp, flight, endpoint, success, status code,
error). Each run prints a warning — not a hard fail — if the current
calendar month's logged call count is at 90%+ of the 600/month free-tier
budget, or has passed it. The per-call cost assumes 1 RapidAPI unit; this
endpoint is billed as "TIER 2" and the exact unit cost wasn't independently
confirmed (would have needed a live fetch of the pricing page).

**Error handling**: HTTP 429 (RapidAPI rate limit) stops the run early —
further calls would fail too — without crashing. Any other per-flight
failure (network error, non-2xx/204 response) is logged to
`aerodatabox_call_log` and the run continues with the next flight.

Verified with `src.lib.aerodatabox.get_flight_schedule` mocked (**no real
AeroDataBox calls were made** while building this — the free-tier budget
was left untouched) against a throwaway Postgres instance with synthetic
flights, covering: a within-threshold confirmation (no
`flight_definitions` change), a >10min discrepancy (correctly updates
`typical_dep_time`/`typical_arr_time`), a simulated 500 error (logged,
run continues), a simulated 429 (logged, run stops early), and same-day
re-verification (correctly skipped without calling the mock again). Also
smoke-tested the rotation cursor over 16 consecutive runs — confirmed full
coverage and correct wraparound at the 14-run boundary.

### Cron (example)

```cron
# every day at 05:00 — populate today's flight_instances from typical times
0 5 * * * cd /path/to/atc-survival-wallet && .venv/bin/python -m jobs.schedule_refresher >> /var/log/atc/schedule_refresher.log 2>&1

# every day at 06:00 — verify ~9 flights' schedules against AeroDataBox
0 6 * * * cd /path/to/atc-survival-wallet && .venv/bin/python -m jobs.schedule_verifier >> /var/log/atc/schedule_verifier.log 2>&1
```

Order between the two doesn't actually matter for correctness (both are
idempotent and the cache checks are keyed differently — see above), but
running the refresher first means every flight has *some* scheduled time
as early as possible, with the verifier upgrading ~9 of them to a
confirmed value shortly after.

## Not yet built

- Live State Poller (M2) — calls OpenSky, matches callsigns, updates flight status
- Wallet Engine (M3) — runs after each poll, computes wallet balance deltas
