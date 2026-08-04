# ATC Survival Wallet

A web app where users draft real, curated US domestic flights, stake a currency
balance on them, and watch that balance rise or fall in near-real-time based on
the flight's actual behavior — on-time tracking trickles the balance up, while
delays, diversions, and cancellations drain it — with each route's historical
volatility (from BTS On-Time Performance data) baked into the payout curve via
scheduled backend jobs that poll AeroDataBox and OpenSky and write to a
Postgres-backed wallet ledger. See `atc-survival-wallet-spec.md` for the full
technical spec. Backend jobs (BTS pool curation, schedule refresh/verify,
OpenSky polling + callsign matching, wallet engine), a FastAPI JSON API, and
a minimal React frontend are all built — see `jobs/README.md` and
`scripts/README.md` for the backend milestones, and the Frontend section
below for M4.

## Stack

- Python 3.11+ (Anaconda's base environment was 3.8 at the time of this
  scaffold; a per-project venv is recommended regardless — see below)
- FastAPI (backend API)
- SQLAlchemy 2.0 (ORM / models)
- Alembic (migrations)
- PostgreSQL 13+ (uses `gen_random_uuid()` via the `pgcrypto` extension,
  enabled automatically by the initial migration)
- React + Vite + Tailwind CSS v4 + TanStack Query (frontend, M4 — see below)

## Setup

1. **Create a virtualenv and install dependencies:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Provision a Postgres database** (local install, Docker, or a hosted
   instance) and copy the env template:

   ```bash
   cp .env.example .env
   # then edit .env: set DATABASE_URL, and the OpenSky / AeroDataBox
   # credentials once you have them (not needed for scaffolding/migrations)
   ```

3. **Run migrations:**

   ```bash
   alembic upgrade head
   ```

4. **Run the API:**

   ```bash
   uvicorn src.main:app --reload
   ```

   `GET /health` should return `{"status": "ok"}`.

5. **(M0) Generate BTS pool candidates:** see [`scripts/README.md`](scripts/README.md).

6. **Run the frontend** (separate terminal):

   ```bash
   cd frontend
   npm install
   cp .env.example .env   # defaults to http://localhost:8000 — update if
                           # your backend is running on a different port
   npm run dev
   ```

   Opens on `http://localhost:5173`. The backend's CORS config
   (`src/main.py`) only allows that origin — if you need a different
   frontend port, update both `vite.config.js` and the `allow_origins`
   list together.

## Project layout

```
src/         FastAPI app: config, DB session, SQLAlchemy models, api/ (M4 JSON API)
jobs/        Scheduled jobs (schedule refresher, live poller, wallet engine)
scripts/     One-off scripts (e.g. the M0 BTS data loader)
migrations/  Alembic migration environment + versions
tests/       Test suite
frontend/    React + Vite + Tailwind + TanStack Query (M4 — see below)
```

## Frontend (M4)

Minimal, unstyled-beyond-basic-Tailwind, functional-only UI to click
through the full loop: browse the flight pool → draft a flight → watch the
wallet update as the OpenSky poller / wallet engine (`jobs/opensky_poller.py`)
run in the background.

**No real auth exists yet** (`users` table was empty, no auth code
anywhere, confirmed before building this). Every request acts as one
hardcoded test user/wallet, lazily created on first API call
(`src/api/deps.py`) with a starting balance of $1,000 (arbitrary, not
spec'd). There is no login, no session, no multi-user support — this is
explicitly out of scope for M4 and needs real work before this goes near
actual users.

**Pages:**
- `/` — flight pool (all active `flight_definitions`), sortable by
  on-time %, volatility, or cancellation %, filterable by carrier
- `/draft/:flightId` — flight detail + stake amount form → creates a
  `wallet_pick` against that flight's most recent `flight_instance`
- `/wallet` — balance + active/resolved picks, each expandable to show its
  full `wallet_events` history; polls every 5s (TanStack Query
  `refetchInterval`, no WebSockets) so ticks from a running poller show up
  without a manual refresh

**API layer** (`src/api/`) is new — M0-M3 only ever touched the DB
directly from jobs/scripts, there was no HTTP API before this. Simplification
worth knowing about: "draft a flight" resolves to the flight's *most
recent* `flight_instance` by `flight_date` (not the per-origin-timezone
"local today" `flight_instances.flight_date` that `schedule_refresher.py`
computes) — fine while there's one instance per flight in the DB, but
worth revisiting once multiple days' instances coexist.

To actually see the wallet move, run `jobs/opensky_poller.py` (which runs
the wallet engine after every poll) alongside both dev servers.
