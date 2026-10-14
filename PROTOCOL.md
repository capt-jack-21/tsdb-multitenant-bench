# Benchmark Protocol: What does row-level security cost on a multi-tenant TimescaleDB hypertable?

Status: DRAFT v0.1, published before any results exist. Parameters marked (TBC) are confirmed on Day 2 and frozen before official runs on Day 10.
Author: Adithya (captjacksparrow1109@gmail.com)

## 1. Question

For a multi-tenant telemetry workload stored in one shared hypertable, what do PostgreSQL row-level security (RLS) policies cost in:

1. sustained ingest throughput, and
2. query latency,

at 10, 100 and 1,000 tenants, and which policy designs preserve index-driven query plans?

## 2. Hypotheses (directional only)

- H1: RLS with a session-setting policy adds measurable but modest query overhead when the planner can still use the (tenant, device, time) index.
- H2: A policy that joins to a membership table can change query plans and cost more than a session-setting policy.
- H3: Ingest throughput is largely unaffected by RLS, since the policy applies to reads and writes through the app role; this must be confirmed for the write path in use.

These are guesses. The results may contradict them, and that will be reported.

## 3. Workload (synthetic drone fleet)

- Tenants own devices; devices fly missions; each device emits telemetry.
- Fixed total row count N across all configurations (TBC, tens of millions; sized to the benchmark VM).
- Devices per tenant: 10 (TBC). Rows per device = N / (tenants x 10).
- Reporting interval: 1 sample per second per device in simulated time; timestamps are generated, not real time.
- Columns: time, tenant_id, device_id, mission_id, lat, lon, alt_m, battery_pct, temp_c, status, odometer_m (monotonic counter).
- Generator: Python 3 with psycopg3 COPY; seeded for reproducibility.

## 4. Schema

- Tables: tenants, devices, missions, telemetry (hypertable on time).
- Indexes: (tenant_id, device_id, time DESC) and (mission_id, time).
- Chunk interval fixed across all runs (TBC). No compression, retention or continuous aggregates in E1 to E3.
- Queries run as a non-owner, non-superuser role. The table owner and superusers bypass RLS unless FORCE ROW LEVEL SECURITY is set, so every run verifies RLS is actually active before measuring.

## 5. Experiments

### E1: ingest baseline (no RLS, single shared table)
Vary batch size (TBC list) and writer count (TBC list). Compare COPY with multi-row INSERT.

### E2: tenant model cost
For 10, 100 and 1,000 tenants at the same N:
- Variant A: no RLS.
- Variant B: RLS enabled, session-setting policy.

Measure ingest and the two queries below.

### E3: policy design
- Policy 1: `tenant_id = current_setting('app.tenant_id')::bigint`.
- Policy 2: `tenant_id IN (SELECT tenant_id FROM memberships WHERE user_id = current_setting('app.user_id')::bigint)`.

Capture `EXPLAIN (ANALYZE, BUFFERS)` for both queries and report whether index scans are preserved.

### E4 (stretch): chunk interval and compression
Storage per row and query time. Only if E1 to E3 are complete.

## 6. Queries

- Q1: last 1 hour of telemetry for one device.
- Q2: full mission timeline for one mission (ordered by time).

Each query type runs 200 times per run with randomly chosen targets (seeded).

## 7. Metrics (every run)

- Sustained ingest rows/s.
- Insert batch latency: p50, p95, p99.
- Query latency: p50, p95, p99.
- CPU and IO utilisation, and CPU steal time.
- Bytes per row on disk (table plus indexes).

## 8. Method

- Fresh database per run; identical postgresql.conf across runs (published).
- 1 warm-up run discarded, then at least 5 measured runs per configuration.
- Report median and spread (min to max). Never report the best run.
- Report whether query numbers are warm cache; add one cold-cache run after a restart where feasible.
- All raw outputs saved under results/ as JSON. Every number in public text maps to a run ID and commit in results/CLAIMS.md.

## 9. Environment (fill in on Day 10)

| Item | Value |
|---|---|
| Provider / instance type | TBC |
| vCPU (dedicated or shared) | TBC |
| RAM | TBC |
| Disk | TBC |
| OS | TBC |
| PostgreSQL version | TBC |
| TimescaleDB version | TBC |
| Generator host | Same VM (limitation) |

## 10. Out of scope

Schema-per-tenant and database-per-tenant, replication, connection pooling (direct connections are used), continuous aggregates, retention, multi-node setups, and any managed-service comparison.

## 11. Limitations (known in advance)

- Synthetic data; real fleets have burstier and more skewed traffic.
- Single VM, with the generator co-located.
- Results apply to the tested versions and configuration only.

## 12. Feedback

If you see a flaw in this protocol, please open an issue before the results are published.
