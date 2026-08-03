# ATC Survival Wallet

A web app where users draft real, curated US domestic flights, stake a currency
balance on them, and watch that balance rise or fall in near-real-time based on
the flight's actual behavior — on-time tracking trickles the balance up, while
delays, diversions, and cancellations drain it — with each route's historical
volatility (from BTS On-Time Performance data) baked into the payout curve via
scheduled backend jobs that poll AeroDataBox and OpenSky and write to a
Postgres-backed wallet ledger. See `atc-survival-wallet-spec.md` for the full
technical spec. This repository currently contains only project scaffolding
(backend skeleton, DB schema, folder structure) — no business logic yet.

## Stack

- Python 3.11+ (Anaconda's base environment was 3.8 at the time of this
  scaffold; a per-project venv is recommended regardless — see below)
- FastAPI (backend API)
- SQLAlchemy 2.0 (ORM / models)
- Alembic (migrations)
- PostgreSQL 13+ (uses `gen_random_uuid()` via the `pgcrypto` extension,
  enabled automatically by the initial migration)

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

## Project layout

```
src/         FastAPI app: config, DB session, SQLAlchemy models
jobs/        Scheduled jobs (schedule refresher, live poller, wallet engine)
scripts/     One-off scripts (e.g. the M0 BTS data loader)
migrations/  Alembic migration environment + versions
tests/       Test suite
```
