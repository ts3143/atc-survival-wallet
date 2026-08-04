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

## M2 — OpenSky Poller + Callsign Matching

`opensky_poller.py` polls `/states/all` for a continental US bounding box,
logs the credit cost reported per call to `opensky_raw_poll_log`, matches
callsigns against today's active-pool `flight_instances` (`src/lib/callsigns.py`
+ `jobs/opensky_matcher.py`), updates `flight_instances.status` /
`actual_dep_utc` / `actual_arr_utc` / `current_icao24` on a match, and
writes matched state vectors to `state_vector_log`.

```bash
python -m jobs.opensky_poller --duration-minutes 60 --interval 180  # continuous loop
python -m jobs.opensky_poller --once                                # single shot, e.g. for cron
```

**Auth** — OAuth2 client credentials, verified against OpenSky's real docs
(`https://openskynetwork.github.io/opensky-api/rest.html`, fetched
2026-08-03; cross-checked the raw HTML directly, not just a fetch-tool
summary — see `src/lib/opensky.py`'s module docstring). Token endpoint
`https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token`,
`grant_type=client_credentials` + `client_id`/`client_secret` as
form-urlencoded POST body, `Bearer` token in `Authorization`. Tokens expire
after 30 minutes (1800s); `TokenManager` refreshes proactively 30s early,
and the poller forces one extra refresh-and-retry if a 401 slips through
anyway.

**Credits** — tracked in a separate bucket per endpoint family
(`/states/*` doesn't share with `/tracks/*` or `/flights/*`). Standard
authenticated tier: 4,000/day. Cost for `/states/all` scales with bounding
box area: ≤25 sq° = 1 credit, 25-100 = 2, 100-400 = 3, >400 or global = 4.
The CONUS bbox (`lamin=24.396308, lomin=-125.0, lamax=49.384358,
lomax=-66.93457`) is ~1451 sq° — top tier, 4 credits/call — confirmed for
real, not just computed (see below).

### Live test results (2026-08-03, ~22:07-22:23 UTC)

Ran a single validation poll, then a 15-minute continuous loop at the
target 3-minute cadence — 7 real polls total, all successful, real credits
consumed:

| poll | state vectors | `X-Rate-Limit-Remaining` |
|---|---|---|
| smoke test | 6,328 | 3996 |
| loop #1 | 6,323 | 3992 |
| loop #2 | 6,328 | 3988 |
| loop #3 | 6,310 | 3984 |
| loop #4 | 6,314 | 3980 |
| loop #5 | 6,307 | 3976 |
| loop #6 | 6,316 | 3972 |

**Every single call cost exactly 4 credits** — zero variance, exactly
matching the documented area-based cost table. Total spent validating
this: 28 credits out of the 4,000/day budget.

**Sustainability at 3-minute cadence**: 480 polls/day × 4 credits =
**1,920 credits/day**, i.e. **48% of the 4,000/day budget** — comfortably
sustainable, with enough headroom that a faster cadence (e.g. 90s → 3,840
credits/day) would still fit, though that leaves much less margin for
error/retries.

One thing this run caught and fixed: the loop's own end-of-run summary
line initially undercounted total credits consumed by one poll's worth (it
compared the balance *after* poll #1 to the balance after the last poll,
rather than the balance immediately before poll #1) — the per-poll data in
`opensky_raw_poll_log` was correct throughout, only the printed summary
was off. Fixed by looking up the prior run's last logged balance as a
proper baseline before the loop starts.

### Pool rebalance (curated_pool_v5_final.csv)

Hawaii routes were removed from the pool (structurally uncoverable — HNL/
OGG/KOA/LIH are nowhere near the CONUS bbox) and the carrier/distance mix
rebalanced, still 123 flights. Re-ran `m0_populate_curated_pool.py` against
the new CSV: 123 active, 16 soft-deactivated (old pool minus new pool),
confirmed zero active Hawaii routes. Picked up two new carriers not
previously in the pool (AS/Alaska, B6/JetBlue) — added to
`src/lib/airline_codes.py`, both cross-checked against real live traffic
(`AAL9`... `ASA9`, `JBU378` — matched on the very first live poll after
being added). Also picked up two airports missing from the timezone table
(BOS, FLL) — both unambiguous (`America/New_York`), added.

**One remaining out-of-bbox route**: SJU (San Juan, PR) is still in the
pool (5 UA/DL flights) and has the same structural problem Hawaii did —
south of `lamin` and east of `lomax` entirely. `jobs/opensky_matcher.py`
has a small `KNOWN_OUT_OF_CONUS_BBOX_AIRPORTS` set for this (currently just
`{"SJU"}`) so these get classified `out_of_coverage` rather than muddying
the `genuinely_missing` diagnostic bucket. Real data during the M2c test
run showed this is direction-dependent, not absolute: `UA668` (SJU→IAH,
*returning* to the mainland) was caught near its Houston approach and
matched normally, while the three outbound legs (`UA1192`, `UA1996`,
`UA701`, all CONUS→SJU) never did. `DL1854` (SJU→JFK, also inbound) also
never matched, but its scheduled window was hours before either test
session ran, so that's more likely "we never polled during its flight"
than a confirmed coverage gap either way.

### Callsign matching (`src/lib/callsigns.py`, `src/lib/airline_codes.py`)

IATA↔ICAO mapping is built only for carriers actually in the pool (see
`get_pool_airline_codes()`), not a general-purpose registry. Every prefix
has been confirmed against real live traffic at some point (AA/DL/UA/F9/
OH/OO/YX from the original M2a poll data; AS/B6 from the M2c run).

Normalization (`normalize_callsign()`) handles the real quirks found in
actual OpenSky data: callsigns are space-padded to 8 chars; SkyWest
occasionally appends a trailing letter after the digits (`SKW129H`
alongside plain `SKW3035`) — parsed rather than dropped, since the lettered
ones just naturally fail to match anything in our pool instead of needing
special-casing; non-airline traffic (GA tail numbers, military callsigns)
is rejected by the ICAO-prefix-plus-digits pattern before it ever reaches
matching.

### Status transitions (`jobs/opensky_matcher.py`)

Deliberately conservative — only what can be directly observed from
`on_ground`, no inference of delayed/diverted/cancelled (that needs other
signals this job doesn't have):

```
scheduled -> departed   (first match, on_ground=True)
scheduled -> airborne   (first match, on_ground=False)
departed  -> airborne   (subsequent match, on_ground=False)
airborne  -> landed     (subsequent match, on_ground=True)
landed    -> landed     (terminal)
```

A ground sighting while still `departed` (never yet seen airborne) stays
`departed` rather than jumping to `landed` — otherwise a flight sitting
pre-departure would look like it had already arrived. `actual_dep_utc` is
set once, the first time status becomes `departed`/`airborne`;
`actual_arr_utc` once, the first time it becomes `landed`. Both use the
poll's `polled_at` as the observed timestamp.

### Diagnostic classification (reporting only, not persisted)

For run-level reporting, every active pool flight is classified each run
into one of: `matched` (persisted `status != 'scheduled'` — the
authoritative, ever-observed signal, not just "matched this run"),
`out_of_coverage` (known bbox gap), `not_yet_departed` /
`already_landed` (scheduled window, padded ±10min/45min grace, doesn't
overlap now), or `genuinely_missing` (should be trackable, isn't). These
categories are purely diagnostic — `flight_instances.status` only ever
uses the spec's own enum.

**Bug caught during the M2c test**: `classify_flight()` originally checked
the `out_of_coverage` rule *before* checking whether the flight had
actually matched, so `UA668` (which really did match — confirmed
`status='airborne'` in the DB) got relabeled `out_of_coverage` in the
summary anyway. The underlying `flight_instances` write was always
correct; only the report miscounted. Fixed by checking `fi.status !=
'scheduled'` first.

### Rate limiting

On HTTP 429, sleeps `X-Rate-Limit-Retry-After-Seconds` (or a 60s default
if that header's missing) and retries the same poll cycle, up to 3
attempts, before giving up on that cycle and waiting for the next
scheduled interval — verified with a mocked 429 (real rate limiting was
never triggered; 4,000/day budget was nowhere close to threatened).

### Live test results

**M2b** (2026-08-03, ~22:07-22:23 UTC): matched real captured poll data
against `flight_instances` read-only, no writes yet. 13/123 pool flights
matched in a 15-minute window; 8 had scheduled windows overlapping the
poll but no match — investigated each (see git history / prior session
notes for the full breakdown; 3 were Hawaii routes since removed from the
pool).

**M2c** (2026-08-03 23:26 - 2026-08-04 00:26 UTC, ~60 min, 21 polls, all
successful, 84 credits): full matching wired in for real.

- 6 distinct status transitions observed, covering every transition path
  in the state machine: `scheduled->airborne`, `scheduled->departed`,
  `departed->airborne`, `airborne->landed` (twice, different flights).
- Final classification: 88 `already_landed`, 16 `not_yet_departed`, 9
  `matched`, 6 `genuinely_missing`, 4 `out_of_coverage`.
- Of the 4 unresolved flights from the M2b sample: `OO4644` fully resolved
  (matched, `airborne->landed` observed live). `YX4688` and `OO5066`
  technically left the `genuinely_missing` bucket, but only because their
  scheduled windows aged past the grace-padded "trackable" cutoff by the
  time of final classification — neither was ever actually matched in
  ~75 cumulative minutes of polling across both test sessions, so calling
  them "resolved" would overstate it. `AA1205` (MCO→LAX) is the one
  genuinely persistent case: never matched across two independent test
  sessions spanning its *entire* scheduled flight window (~22:00-03:42
  combined), and a broad callsign search turned up nothing close.
- `genuinely_missing` at run end: `AA1205`, `AA2772`, `F92072`, `OO4676`,
  `UA1374`, `UA259`. All 6 have `days_of_week` including today (Monday),
  ruling out a schedule-derivation bug. No near-miss callsigns found for
  any of them anywhere in this run's ~9,800 distinct real callsigns. Best
  explanation: today's *actual* schedule for these specific flights has
  drifted from the BTS-derived "typical" time enough to fall outside the
  grace window — exactly the kind of drift `schedule_verifier.py` (M1)
  exists to catch, not a callsign-matching defect.

## Not yet built

- Wallet Engine (M3) — runs after each poll, computes wallet balance deltas
