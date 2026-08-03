# ATC Survival Wallet — Technical Spec

**Concept:** Users draft real, curated US domestic flights, start with a currency balance, and watch it rise/fall in near-real-time based on the flight's actual behavior (on-time = trickle up, delay/diversion/cancellation = drain), with route-level historical volatility baked into the payout curve.

**Scope:** Continental US domestic flights only. MVP is single-league (planes); architecture should stay clean enough to extend to trains/other domains later without a rewrite.

---

## 1. Data Sources Recap

| Source | Role | Budget |
|---|---|---|
| **BTS On-Time Performance** | Historical volatility scoring + pool curation | Free bulk download, no rate limit |
| **AeroDataBox** | Live/today's scheduled departure & arrival time per flight | 600 units/month — cache aggressively |
| **OpenSky Network** | Live position/state tracking (in-air status, delay detection) | 4,000 credits/day, CONUS bbox, poll every 1-3 min |

Core architectural rule: **all three are called by a backend job, never by a user's browser.** Users always read from your own DB.

---

## 2. Database Schema

### `flight_definitions`
The curated pool of ~100-150 recurring flight numbers. Refreshed quarterly.

```
id                  UUID PK
carrier_code        VARCHAR(2)      -- e.g. "AA"
flight_number       VARCHAR(6)      -- e.g. "1234"
origin_airport      VARCHAR(3)      -- IATA
dest_airport        VARCHAR(3)      -- IATA
days_of_week        INT[]           -- [1,2,3,4,5] etc, from BTS pattern analysis
typical_dep_time    TIME            -- local, from BTS historical mode/median
typical_arr_time    TIME
distance_bucket     ENUM(short, medium, long)
active              BOOLEAN
last_verified_at    TIMESTAMP       -- last time AeroDataBox confirmed schedule still matches
created_at           TIMESTAMP
```

### `flight_volatility_stats`
Precomputed from BTS, one row per `flight_definitions` entry (or per category if you go category-level instead of per-flight — see note below).

```
id                     UUID PK
flight_definition_id   UUID FK -> flight_definitions
on_time_pct            DECIMAL      -- % of historical instances <15min late
avg_delay_minutes       DECIMAL
delay_stddev            DECIMAL     -- this IS your volatility number
cancellation_pct        DECIMAL
diversion_pct           DECIMAL
sample_size             INT         -- number of historical instances used
computed_at             TIMESTAMP
```

### `flight_instances`
One row per actual real-world occurrence of a flight (today's AA1234, tomorrow's AA1234, etc).

```
id                    UUID PK
flight_definition_id  UUID FK -> flight_definitions
flight_date           DATE
scheduled_dep_utc     TIMESTAMP    -- from AeroDataBox, cached
scheduled_arr_utc     TIMESTAMP
actual_dep_utc        TIMESTAMP    -- filled in as observed via OpenSky
actual_arr_utc        TIMESTAMP
status                ENUM(scheduled, boarding, departed, airborne, landed, delayed, diverted, cancelled)
current_icao24        VARCHAR(6)   -- transponder ID once matched to an OpenSky state vector
last_polled_at         TIMESTAMP
created_at             TIMESTAMP
```

### `state_vector_log` (optional but recommended)
Raw OpenSky snapshots for matched flights — your own growing historical archive, and useful for debugging/replay.

```
id                UUID PK
flight_instance_id UUID FK -> flight_instances
polled_at          TIMESTAMP
latitude           DECIMAL
longitude          DECIMAL
altitude_m         DECIMAL
velocity_ms        DECIMAL
heading            DECIMAL
vertical_rate       DECIMAL
on_ground           BOOLEAN
```

### `users`
```
id             UUID PK
email          VARCHAR UNIQUE
created_at     TIMESTAMP
```

### `wallets`
```
id             UUID PK
user_id        UUID FK -> users
balance        DECIMAL         -- current coin count
started_at     TIMESTAMP
```

### `wallet_picks`
A user's active/past draft on a specific flight instance.

```
id                    UUID PK
wallet_id             UUID FK -> wallets
flight_instance_id    UUID FK -> flight_instances
staked_amount         DECIMAL       -- coins allocated to this pick
status                ENUM(active, resolved_win, resolved_loss, cashed_out)
resolved_amount        DECIMAL       -- final payout/loss once flight resolves
cashed_out_at          TIMESTAMP NULLABLE
created_at             TIMESTAMP
```

### `wallet_events`
Ledger of every balance change — useful for both game logic and showing users their history.

```
id                 UUID PK
wallet_pick_id      UUID FK -> wallet_picks
event_type          ENUM(decay_tick, gain_tick, delay_penalty, cancellation_penalty, diversion_penalty, cash_out, resolution)
amount              DECIMAL     -- signed
occurred_at          TIMESTAMP
metadata             JSONB      -- e.g. { "delay_minutes": 12, "source": "opensky_poll" }
```

**Design note on per-flight vs. per-category volatility:** given BTS gives you real per-flight-number sample sizes, start there — check `sample_size` in `flight_volatility_stats`, and if a given flight number has too few historical instances to be statistically meaningful, fall back to a category-level average (same carrier + distance bucket) for that one entry. This gives you per-flight granularity where the data supports it, without breaking on thin samples.

---

## 3. System Architecture

```
┌─────────────────────┐
│   BTS Batch Loader   │  (runs ~quarterly, or on-demand)
│  - downloads CSV      │
│  - populates          │
│    flight_definitions │
│    flight_volatility_ │
│    stats              │
└──────────┬───────────┘
           │
           ▼
┌─────────────────────┐        ┌──────────────────────┐
│  Schedule Refresher   │◄──────┤   AeroDataBox API      │
│  (daily job, small     │       │  (600 units/mo budget) │
│   batch per pool)      │       └──────────────────────┘
│  - populates            │
│    flight_instances      │
│    (scheduled times)     │
└──────────┬───────────┘
           │
           ▼
┌─────────────────────┐        ┌──────────────────────┐
│   Live State Poller   │◄──────┤   OpenSky API           │
│  (every 1-3 min,        │       │  (4,000 credits/day)   │
│   CONUS bbox)            │       └──────────────────────┘
│  - matches callsigns      │
│    to flight_instances     │
│  - updates status/          │
│    actual times              │
│  - writes state_vector_log   │
└──────────┬───────────┘
           │
           ▼
┌─────────────────────┐
│   Wallet Engine        │
│  (runs after each poll) │
│  - reads flight_instances│
│    status changes        │
│  - computes decay/gain/   │
│    shocks per active pick  │
│  - writes wallet_events     │
│  - updates wallets.balance  │
└──────────┬───────────┘
           │
           ▼
┌─────────────────────┐
│    API / Backend        │
│  (serves frontend)         │
│  - flight pool browse       │
│  - draft a flight             │
│  - wallet view/history          │
└──────────┬───────────┘
           │
           ▼
      Frontend (web app)
```

Everything left of "API/Backend" is scheduled jobs and internal state — the frontend never talks to BTS, AeroDataBox, or OpenSky directly.

---

## 4. Callsign Matching Logic (the trickiest piece)

OpenSky state vectors give you a raw `callsign` field (e.g., `"AAL1234 "` — note ADS-B callsigns are often ICAO airline code + flight number, padded with spaces, sometimes inconsistent). Your `flight_definitions` table uses IATA carrier codes (e.g., "AA"). You'll need:

1. An **ICAO ↔ IATA airline code mapping table** (small static reference table, easy to source once)
2. A normalization step: strip whitespace, extract the numeric flight number from the callsign string
3. A matching query: `ICAO_prefix + flight_number` → look up `flight_instances` for today where `flight_definition.carrier_code` maps to that ICAO prefix and `flight_number` matches
4. Handle the miss case gracefully — not every observed aircraft will match your curated pool, and that's expected (you're filtering the CONUS-wide state dump down to just your ~100-150 tracked flights)

---

## 5. Milestones (updated)

**M0 — BTS Pool Curation**
- Download BTS On-Time Performance data (recent 6-12 months)
- Script: group by carrier+flight number, filter to recurring (200+ operating days or consistent weekday pattern), compute on-time %, delay stddev, cancellation/diversion rates
- Hand-curate final list to ~100-150 for route variety (hub-to-hub, chaos-prone, regional mix)
- Populate `flight_definitions` + `flight_volatility_stats`
- **Done when:** you have a populated, queryable pool with real volatility numbers

**M1 — Schedule Refresher**
- Daily job: for each active flight_definition, call AeroDataBox once to get today's actual scheduled dep/arr time, write to `flight_instances`
- Cache aggressively; skip re-fetching if a flight's typical schedule hasn't shifted
- **Done when:** every morning, your pool has a fresh row of "today's flights" with real scheduled times

**M2 — Live State Poller + Matching**
- Poller job: hit OpenSky `/states/all` for CONUS bbox every 1-3 min
- Normalize callsigns, match against today's `flight_instances`
- Update status (airborne/landed/delayed), actual times, write to `state_vector_log`
- **Done when:** given a real flight in your pool, you can watch its status update automatically as it happens, via logs

**M3 — Wallet Engine**
- Core decay/gain formula: function of (scheduled vs actual timing) × (volatility multiplier from `flight_volatility_stats`)
- Event shocks: cancellation/diversion trigger discrete penalties
- Write to `wallet_events`, update `wallets.balance`
- **Done when:** one test user, one active pick, wallet balance changes automatically tied to a real flight's real behavior

**M4 — Minimal App**
- Auth, flight pool browse/draft UI, wallet view
- **Done when:** you can log in, draft from the curated pool, and watch your wallet react without touching the DB manually

**M5 — Volatility Surfacing + Extras**
- Show volatility score before drafting
- Cash-out-early mechanic
- Multiple simultaneous picks (portfolio mode)
- Leaderboard, historical replay mode

---

## 6. Concrete Claude Code Prompts

Use these as starting prompts inside the VS Code extension, one milestone at a time. Feed it this spec file as context first.

**Kickoff (once, to set shared context):**
> "I'm building a web app called ATC Survival Wallet. I'm attaching a technical spec (schema + architecture). Read it fully before we start. We'll build this in milestones — right now we're only doing M0. Don't scaffold the whole app yet; just build what M0 needs."

**M0 prompt:**
> "Write a Python script that downloads BTS Airline On-Time Performance data for [specify months/year], loads it into a Postgres DB matching the `flight_definitions` and `flight_volatility_stats` tables from the spec, and includes a grouping step that identifies flight numbers operating on 200+ distinct days (or a consistent weekday pattern) as 'recurring.' Output a CSV of candidate flights sorted by sample size and delay stddev so I can hand-pick the final ~100-150."

**M1 prompt:**
> "Now build M1: a scheduled job (can be a simple script run via cron or manually for now) that, for each active row in `flight_definitions`, calls the AeroDataBox flight-schedule endpoint once per day and inserts/updates a `flight_instances` row with today's scheduled times. Include caching logic so we don't exceed 600 requests/month — log every external call made."

**M2 prompt:**
> "Build M2: a poller that calls OpenSky's `/states/all` for a continental US bounding box every N minutes (configurable), normalizes callsigns using an ICAO/IATA mapping table, matches them against today's `flight_instances`, and updates status/actual times. Write raw matched state vectors to `state_vector_log`. Handle token refresh (OAuth2, 30 min expiry) and 429 rate-limit responses gracefully."

**M3 prompt:**
> "Build M3: the wallet engine. After each poller run, for every active `wallet_pick`, compute a balance delta based on [describe your finalized decay/gain formula], write a `wallet_events` row, and update `wallets.balance`. Include unit tests with mock flight status transitions (on-time, delayed 20 min, cancelled, diverted) so I can verify the math independently of live data."

**M4 prompt:**
> "Now scaffold a minimal frontend: a page to browse the curated flight pool, a draft action that creates a `wallet_pick`, and a wallet view showing current balance and event history. Keep it simple — no styling polish yet, just functional."

---

## 7. Scoring Formula — Provisional v0 (subject to change once BTS numbers are in)

**Status: this is a placeholder to unblock building M3, not a final balance.** Once M0 produces real `cancellation_pct`, `diversion_pct`, and `delay_stddev` numbers for the curated pool, revisit every value below — especially the cancellation/diversion penalties, which should be sized against how often those events actually occur in the real data, not guessed.

**Staking:** user chooses stake amount per pick (not fixed).

**Grace window:** 15 minutes late before any penalty applies — matches BTS's own on-time definition, so the game's threshold lines up with the same standard the volatility data uses.

**Poll interval:** every 3 minutes (matches the OpenSky poller cadence).

**Volatility multiplier:** derived per flight from `delay_stddev` relative to the pool average, clipped to a range, and applied to *both* gains and penalties — so historically volatile flights swing harder in both directions (this is the "priced risk" mechanic).

| Rule | Value |
|---|---|
| Base tick gain (flight tracking normally, within grace) | +2% of stake per tick |
| Delay penalty (per minute beyond 15-min grace) | -1% of stake per minute |
| Cancellation | -50% of stake |
| Diversion | -25% of stake |
| Volatility multiplier range | 0.5x – 3.0x |
| On-time resolution bonus (final payout if flight lands within grace) | +15% of stake |

**Why this shape:** tick rates borrow from a "fast/dramatic" variant (real-time tension, visible movement every poll), but catastrophic-event penalties are capped at a survivable level rather than a full wipeout, so one bad break doesn't feel disproportionately punishing before you know how frequently these events actually occur in the curated pool.

**Re-tuning checklist for after M0:**
- [ ] Compare -50%/-25% penalties against actual cancellation_pct/diversion_pct per flight — are rare-event penalties too harsh, or common-event penalties too soft?
- [ ] Check whether the 0.5x–3.0x volatility multiplier range actually produces meaningful spread across the curated pool, or needs widening/narrowing
- [ ] Sanity-check the +15% resolution bonus against typical on-time percentages — if most pool flights are >85% on-time, this bonus may need to be smaller to keep the "risk" framing honest

## 8. Other Open Design Decisions

- [ ] Refresh cadence for OpenSky poller (start conservative at 3 min, tune later)
- [ ] Per-flight vs. category-level volatility fallback threshold (e.g., sample_size < 30 → fall back to category)
- [ ] Whether resolution bonus/penalty should scale continuously with how early/late (vs. the flat "within grace = bonus" rule above)
