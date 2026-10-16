# bench/

| File | Purpose |
|---|---|
| `schema.sql` | tables, hypertable, indexes, `bench_app` role (destructive) |
| `rls_off.sql`, `rls_session.sql`, `rls_membership.sql` | the three policy states |
| `generate.py` | seeded synthetic fleet generator and loader (COPY, as owner) |
| `queries/*.sql` | pgbench scripts: `q1`/`q2` x `a` (explicit tenant predicate) / `rls` (policy only) |
| `run_queries.py` | Tests 2 and 4: pgbench latency per variant, plus EXPLAIN plans |
| `run_ingest.py` | Tests 1 and 3: write throughput (`baseline` and `rls`) |
| `common.py` | shared helpers: environment capture, RLS checks, summaries |

## Requirements

- Python 3.10+ and `pip install -r bench/requirements.txt`
- `pgbench` on PATH (Linux; on Windows use WSL2 with `postgresql-contrib` or the Linux VM)
- A TimescaleDB instance (`docker compose up -d --wait`) and `BENCH_DSN` set

## Quick check

```
python bench/run_queries.py --smoke
python bench/run_ingest.py baseline --smoke
python bench/run_ingest.py rls --smoke
```

## Outputs (in `results/`)

- `environment.json`: versions, hardware, PostgreSQL settings, arguments
- `queries_raw.jsonl`, `queries_summary.csv`: Test 2, one line per run / median and range per config
- `plans/`, `plans_summary.jsonl`: Test 4, EXPLAIN (ANALYZE, BUFFERS) JSON per variant and query
- `ingest_baseline_*`, `ingest_rls_*`: Tests 1 and 3
- `datasets.jsonl`: rows, size and bytes per row per loaded dataset
- `raw/sysstat_*.log`: background vmstat (CPU, steal) when available

## Notes

- Repetition 0 of every configuration is a warm-up and is excluded from summaries.
- Queries run as `bench_app` (via `PGOPTIONS=-c role=bench_app`). Do not run them as the owner or a superuser: RLS would be bypassed.
- Smoke-run numbers on a laptop are not results; official numbers come from the pinned VM only.
