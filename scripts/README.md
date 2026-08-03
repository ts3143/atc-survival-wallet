# scripts

One-off / manually-run scripts.

## `m0_bts_pool_candidates.py` (M0 — pool candidate generator)

Downloads BTS Airline On-Time Performance data (from transtats.bts.gov, no
API key needed) for a given month range, loads the columns we need into the
`bts_ontime_performance_raw` staging table, groups by
(carrier, flight number, origin, dest), and writes a CSV of "recurring"
candidate flights for you to hand-pick the final ~100-150 from.

It does **not** populate `flight_definitions` / `flight_volatility_stats` —
that's a deliberately separate step once you've reviewed the CSV.

```bash
python -m scripts.m0_bts_pool_candidates --start 2024-01 --end 2024-12
```

- `--start` / `--end`: `YYYY-MM`, inclusive. **Use at least 6-12 months**
  (per spec section 5, M0) — a single month was used to validate this script
  end-to-end and produced noisy, near-meaningless "recurring" flags (see
  below); the recurring-detection logic only becomes meaningful with enough
  weeks/months of history behind it.
- `--min-days` (default 200): distinct operating days to qualify as
  recurring "by count."
- `--weekday-threshold` (default 0.8): a flight must have operated on a
  given weekday at least this fraction of the times that weekday occurred
  in the pulled range to count as a consistent weekday pattern.
- `--out`: output CSV path (default `scripts/output/bts_pool_candidates_<start>_<end>.csv`).
- `--cache-dir`: where downloaded zips are cached (default `scripts/.bts_cache/`,
  gitignored — re-running with the same range reuses the cache instead of
  re-downloading).
- `--skip-load`: skip download/load and just re-run aggregation + CSV export
  against whatever is already in the staging table (fast iteration on
  `--min-days` / `--weekday-threshold` without re-downloading/re-loading).

Expect roughly 2-3 minutes per month to download + load (each month is
~500k+ rows) — a 12-month pull will take on the order of 30-45 minutes.

Verified against real BTS data (January 2024) against a live Postgres
instance: ~547k rows loaded, overall cancellation/diversion/on-time rates
matched known Jan 2024 winter-storm disruption levels (~3.7% cancelled,
~0.28% diverted, ~76% on-time).

## `m0_populate_curated_pool.py` (M0 — final pool loader)

Takes the hand-curated final pool CSV (you filter `m0_bts_pool_candidates.py`'s
output down to ~100-150 by hand, save as its own file) and upserts
`flight_definitions` + `flight_volatility_stats` per spec section 2.

```bash
python -m scripts.m0_populate_curated_pool scripts/output/curated_pool_v2_final.csv
```

Expected CSV columns: `carrier_code, flight_number, origin_airport,
dest_airport, sample_size, on_time_pct, avg_delay_minutes, delay_stddev,
cancellation_pct, diversion_pct, avg_distance_miles, distance_bucket,
typical_dep_time_local, typical_arr_time_local, days_of_week_candidate`
(`avg_distance_miles` is accepted but not stored — there's no column for it
on `flight_definitions`).

Idempotent by natural key — re-running against a refreshed CSV upserts
existing rows (`flight_definitions` on carrier+flight+route,
`flight_volatility_stats` one-to-one with its definition) rather than
duplicating them; `computed_at` is bumped to `now()` on every run, including
updates.

**Pool-refresh / deactivation policy:** every run is treated as the full,
current pool. Flights present in the CSV are upserted with `active=true`.
Any `flight_definitions` row **not** present in the CSV — i.e. it was in a
previous pool but has since been dropped — is **soft-deactivated**
(`active=false`); it is never deleted, since `flight_instances` /
`wallet_picks` may reference it historically. If a flight is added back to
a later CSV, re-running reactivates it (`active=true`) and refreshes its
volatility stats. In other words: the CSV you pass in each run *is* the
pool — anything missing from it is treated as removed, not "not mentioned."

Validates the CSV before writing anything: required columns present, no
nulls, no duplicate natural keys, `distance_bucket` values only
`short`/`medium`/`long`.
