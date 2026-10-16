# 2026-10-16: harness check on plain PostgreSQL 16

Environment: PostgreSQL 16.15, no TimescaleDB, a sandbox container, small data (about 200k to 400k rows). These are checks of the harness and of PostgreSQL behaviour, not benchmark results. Numbers from this run are not to be quoted.

## Verified behaviour (PostgreSQL 16)

- `COPY FROM` into a table with RLS enabled fails with `COPY FROM not supported with row-level security` (hint: use INSERT). So the RLS write comparison must use INSERT.
- `INSERT` into an RLS table as the non-owner role works when the row matches the policy, and a row for another tenant is rejected by `WITH CHECK`.
- A policy using `current_setting('app.tenant_id')::integer` was pushed into the index condition on `(tenant_id, device_id, time DESC)`.
- A membership-join policy, with a query that has no explicit tenant predicate, fell back to a (parallel) sequential scan in this small test. Adding an explicit `tenant_id` predicate restored an index scan. This is a hint to test properly on TimescaleDB at scale (Tests 2 and 4), not a finding.
- A non-leakproof predicate on a column (`status::text = '2'`) was applied as a filter after the index condition in the one plan checked.

## Harness status

- `run_queries.py --smoke`, `run_ingest.py baseline --smoke` and `run_ingest.py rls --smoke` all completed end to end: dataset load, variant rotation, RLS-state and isolation checks, plan capture, pgbench percentile parsing, row-count verification, summaries.
- `run_ingest.py rls --methods copy` correctly refuses to run.
- The only failure seen was a test-database issue: psycopg returns bytes for SQL_ASCII databases. The TimescaleDB image uses UTF-8.

## Not yet verified

- `create_hypertable` and every TimescaleDB-specific path (schema.sql as written).
- Anything at the data sizes in the protocol.
