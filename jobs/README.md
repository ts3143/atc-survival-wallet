# jobs

Scheduled background jobs, run outside the request/response cycle (cron, worker, etc):

- Schedule Refresher (M1) — calls AeroDataBox, populates `flight_instances`
- Live State Poller (M2) — calls OpenSky, matches callsigns, updates flight status
- Wallet Engine (M3) — runs after each poll, computes wallet balance deltas

Empty for now — this is scaffolding only.
