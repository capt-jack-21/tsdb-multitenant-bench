# Benchmark Protocol: What does row-level security cost on a multi-tenant TimescaleDB hypertable?

Status: DRAFT v0.2. Nothing has been published and no official results exist.
Author: Adithya (captjacksparrow1109@gmail.com)

Parameters are frozen before the official runs; any change after that is recorded in the Changelog.

## Changelog

- v0.3: added variant E (index-friendly membership policy), query Q3 (device, last 24 h, time-bounded), and an optional prepared-statement protocol run. Added after dev-scale runs showed variant C's plan depends on how the policy is written.
- v0.2: ingest comparison uses INSERT, not COPY (COPY FROM is refused on RLS tables, verified on PostgreSQL 16). Query workload changed so result size is constant across tenant counts. Fixed simulated time span with derived sampling interval. Variants A to D defined. Read tests use pgbench. Tests numbered 1 to 5.
- v0.1: initial draft.

## 1. Question

For a multi-tenant telemetry workload in one shared hypertable, what do PostgreSQL row-level security (RLS) policies cost in **read latency** and **write throughput** at 10, 100 and 1,000 tenants, and which policy designs keep index-driven query plans?

## 2. Expectations (guesses, not findings)

- E-1: A session-setting policy (`tenant_id = current_setting(...)`) keeps index use and adds a small read overhead.
- E-2: A membership-join policy can change plans and cost more, especially when the query has no explicit tenant predicate.
- E-3: Write overhead from RLS `WITH CHECK` is small compared with INSERT overhead itself.

The results may contradict these, and that will be reported.

## 3. Tests

| # | Test | Varies | Measures |
|---|---|---|---|
| 1 | Write baseline (no RLS) | method (COPY, INSERT), batch size, writers | rows/s, batch latency p50/p95/p99 |
| 2 | Read latency with and without RLS (main test) | tenants (10, 100, 1,000), variant (A to E), query (Q1 to Q3), clients (1, 8) | latency p50/p95/p99, transactions/s |
| 3 | Write throughput with and without RLS | tenants, variant (A, B, C) | rows/s, batch latency p50/p95/p99 |
| 4 | Plans behind Test 2 | tenants, variant, query | plan node types, index use, execution time (EXPLAIN ANALYZE, BUFFERS) |
| 5 (stretch) | Storage and chunk interval | chunk interval, compression | bytes/row, query time |

Tests 1 to 4 are in scope. Test 5 runs only if Tests 1 to 4 are complete and reviewed.

## 4. Data

Synthetic drone fleet. Tenants own devices, devices fly missions, missions emit telemetry.

- Total rows N (Tests 2, 4): 20,000,000 (frozen). Tests 1 and 3 insert 1,000,000 rows per run into an empty table.
- Devices per tenant: 10. Tenant counts: 10, 100, 1,000 (so 100, 1,000, 10,000 devices).
- Simulated time span: 7 days, identical for every tenant count. The per-device sampling interval is derived (span / rows per device), so chunking is comparable across configurations.
- Mission length: 600 samples. Late events: 1% of rows back-dated by up to 300 s. Duplicate deliveries: 0.5% of rows.
- Seed 42. Rows are produced time-major. The generator is `bench/generate.py`.
- Schema: `bench/schema.sql`. Hypertable on `time`, chunk interval 1 day (frozen). Indexes `(tenant_id, device_id, time DESC)` and `(mission_id, time)`. No compression, retention or continuous aggregates in Tests 1 to 4.
- Data is loaded as the table owner with COPY, then `VACUUM (ANALYZE)` and `CHECKPOINT`.

## 5. Variants

All measurements run as a non-owner, non-superuser role (`bench_app`). The table owner and superusers bypass RLS, so a run as `postgres` would measure nothing.

| Variant | RLS policy | Query text | Isolation comes from |
|---|---|---|---|
| A | none | has `tenant_id = :t AND ...` | the query |
| B | session setting | has no tenant predicate | RLS |
| C | membership join | has no tenant predicate | RLS |
| D | session setting | has the explicit predicate too | both |
| E | membership via a STABLE array function: `tenant_id = ANY ((SELECT app_user_tenants())::integer[])` (the `SELECT` wrapper makes the function run once per query instead of per row) | has no tenant predicate | RLS |

C is the obvious way to write a membership policy and hides the tenant from the planner (`IN (subquery)` becomes a per-row filter). E carries the same logic in a form the planner can use as an index condition. Both are reported so the post can show what the naive form costs and what rewriting it recovers.

Every query transaction first sets `app.tenant_id` and `app.user_id` in all variants, including A, so the per-request overhead is identical and only the policy and predicate differ. Before each variant runs, the harness checks that the RLS state is as claimed and that tenant 1 sees none of tenant 2's rows.

## 6. Queries (Tests 2, 4)

- **Q1:** the latest 100 readings for one device (`ORDER BY time DESC LIMIT 100`). Fixed result size.
- **Q3:** the latest 100 readings for one device within the last 24 h of data (`time >= window start ORDER BY time DESC LIMIT 100`). Fixed result size. Q1 has no time bound and is the worst case; Q3 is the usual dashboard shape and lets chunk exclusion work.
- **Q2:** the full timeline of one mission (about 600 rows). Fixed result size; only full missions are sampled.

Default protocol is pgbench simple (statements batched with `\;`, one round trip). `--protocol prepared` re-runs with prepared statements; there the context settings are sent as separate `set_config` calls, so results are compared only within one protocol. Plans in Test 4 are always captured with plain EXPLAIN.

Targets are chosen at random per transaction by pgbench with a fixed seed; the same seed is used for every variant within a repetition. Scripts are in `bench/queries/`.

## 7. Write workload (Tests 1, 3)

- Writers are separate processes with their own connections. Batches are built and encoded before the clock starts.
- Test 1: tenants = 100, RLS off, methods COPY and INSERT, batch sizes 500, 2,000, 10,000, writers 1, 4, 8.
- Test 3: INSERT only (`INSERT ... SELECT FROM unnest(...)`), batch size 1,000, 4 writers, per-tenant batches, tenants 10, 100, 1,000, variants A, B, C, E. Each batch sets the tenant context first, in all variants.
- Each run starts from an empty telemetry table (TRUNCATE plus CHECKPOINT) and is committed per batch (`synchronous_commit = on`). Steady-state ingest into a large existing table is out of scope.
- After each run the table row count must equal the rows sent, or the run fails.

## 8. Metrics

Latency p50/p95/p99 and mean, transactions/s (reads); rows/s and batch latency p50/p95/p99 (writes); bytes per row on disk; plan node types and execution time; background `vmstat` log for CPU and steal time.

## 9. Method

- Per configuration: 6 repetitions; the first is a warm-up and is discarded, leaving 5. Report median and min to max. Never the best run.
- Variant order is rotated each repetition so drift does not favour one variant.
- Read tests: 20 s per pgbench run, 1 and 8 clients, `CHECKPOINT` before each run.
- Data is warm and in memory (it fits in RAM on the benchmark VM; confirmed by dataset size in `results/datasets.jsonl`). A cold-cache run is optional and reported separately.
- Fixed PostgreSQL settings, published in `docker-compose.yml` and recorded in `results/environment.json`.
- Every public number appears in `results/CLAIMS.md` with a run tag and commit.

## 10. Environment (completed before the official runs)

| Item | Value |
|---|---|
| Provider / instance | TBC |
| vCPU (dedicated) / RAM / disk | TBC |
| OS | TBC |
| PostgreSQL / TimescaleDB versions | TBC (captured automatically) |
| Docker image digest | TBC |
| Harness host | same VM (limitation) |

Numbers from laptops, WSL or Docker Desktop are for development only and are not published.

## 11. Execution plan (3 days)

- Day 1: harness smoke run on the VM, Test 1, then Test 3.
- Day 2: Test 2 and Test 4 (three dataset loads, about 290 pgbench runs).
- Day 3: re-run anything that failed review, freeze data, write up.

## 12. Out of scope

Schema-per-tenant and database-per-tenant, replication, connection pooling, continuous aggregates, retention, compression (except Test 5), multi-node, managed-service comparisons, skewed tenant sizes.

## 13. Known limitations

- Synthetic uniform data; real fleets are bursty and skewed.
- Single VM, with the harness on the same machine.
- Writes go into an empty table, not a large existing one.
- Results apply to the tested versions and settings only.
- The two query shapes cover common dashboard reads, not every access pattern.

## 14. Feedback

If you see a flaw in this protocol, please open an issue before results are published.
