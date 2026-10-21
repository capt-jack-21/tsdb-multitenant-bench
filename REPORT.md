# What row-level security costs on a multi-tenant TimescaleDB hypertable

A reproducible benchmark of PostgreSQL row-level security (RLS) on a TimescaleDB hypertable, with 10, 100 and 1,000 tenants and 20 million rows. Everything here can be re-run from this repository. The full tables are in [`results/official/report.md`](results/official/report.md), the raw data in `results/official/`, and the protocol in [`PROTOCOL.md`](PROTOCOL.md).

## Answer in brief

1. **A well-written RLS policy is cheap.** A policy that compares `tenant_id` with a session setting added about **0.33 to 0.35 ms** to a 0.9 ms point lookup (1.35x to 1.40x, p50). The overhead did not grow with tenant count. It was about 1.15x on a time-bounded query and at most 1.12x on a larger mission scan at 10 tenants. Write throughput was 1% to 4% lower, which is close to run-to-run noise.
2. **The shape of the policy matters far more than whether RLS is on.** A policy that looks up the user's tenants with a subquery (`tenant_id IN (SELECT ... FROM memberships ...)`) cost **2.7x, 15x and 142x** the baseline at 10, 100 and 1,000 tenants. The planner cannot use the `(tenant_id, device_id, time)` index with it, so it walks the time index and filters rows.
3. **Rewriting the membership policy as a function did not make it safe.** It brought the 1,000-tenant case from 133 ms down to 4.3 ms, but at 100 tenants the same query took 35 ms at p50 with a p99 of 763 ms. The cost followed the number of rows per device, not the tenant count.
4. **Most of the RLS premium on simple lookups is planning, not execution.** In a smaller prepared-statement run, the session-setting policy was not slower than the baseline. See the caveats below, because I did not fully explain the baseline's behaviour there.

**Practical recommendation:** if each request acts for exactly one tenant, put the tenant id in a session setting and write the policy as a plain comparison against it. Keep the explicit `tenant_id` predicate in queries only if you want it for readability, because it added nothing measurable on Q1 and Q3 (variant D behaved like B). Avoid per-row membership subqueries, and test any policy that allows several tenants at once against your own row counts.

## What was measured

| Variant | Policy | Query carries a tenant predicate |
|---|---|---|
| A | no RLS | yes (baseline) |
| B | `tenant_id = current_setting('app.tenant_id')::int` | no |
| C | `tenant_id IN (SELECT tenant_id FROM memberships WHERE user_id = ...)` (naive) | no |
| D | same policy as B | yes |
| E | membership policy through a `STABLE` SQL function returning an array, used as `tenant_id = ANY ((SELECT fn())::int[])` | no |

Queries (all as a non-owner role, because table owners bypass RLS):

- **Q1:** latest 100 readings for a device, no time bound.
- **Q2:** the full timeline of one mission.
- **Q3:** latest 100 readings for a device in the last 24 hours.

Data: 20.1 million rows, 10 devices per tenant, a 7-day span in 1-day chunks (7 chunks), 1% late and 0.5% duplicate rows, fixed seed. Each configuration ran 6 times of 20 s with the first as warm-up, so 5 measured repetitions. Variant order was rotated between repetitions. Reads ran with 1 and 2 concurrent clients. Writes used `INSERT ... SELECT FROM unnest(...)` because `COPY` is refused on RLS tables.

**Environment:** a 2 vCPU / 7.7 GB KVM VPS (AMD EPYC 7C13), Ubuntu 26.04, PostgreSQL 16.15 and TimescaleDB 2.30.2 in a container (image pinned by digest in `results/official/vm_environment.txt`), `shared_buffers` 2 GB, `effective_cache_size` 5 GB, `work_mem` 32 MB, `synchronous_commit` on. Client: pgbench 18.6. CPU steal was **0.01% on average, 1% at most** over about 15,000 one-second samples, so the shared host did not distort this run.

## Results

### 1. The session-setting policy (B) is cheap

p50 latency in ms, 1 client (the 2-client numbers show the same pattern):

| Query | Tenants | A (no RLS) | B (session) | B minus A | B / A |
|---|---|---|---|---|---|
| Q1 | 10 | 0.88 | 1.22 | +0.34 | 1.38x |
| Q1 | 100 | 0.90 | 1.25 | +0.35 | 1.40x |
| Q1 | 1000 | 0.93 | 1.26 | +0.33 | 1.36x |
| Q3 | 10 | 0.72 | 0.82 | +0.10 | 1.14x |
| Q3 | 100 | 0.69 | 0.83 | +0.14 | 1.20x |
| Q3 | 1000 | 0.73 | 0.85 | +0.12 | 1.16x |

The p99 gap was similar in absolute terms: 1.20 vs 1.63 ms at 10 tenants for Q1. The overhead is a fixed cost per query, so it matters most on very cheap queries. On Q1 and Q3, variant D (policy plus explicit predicate) was within 0.05 ms of B.

### 2. The naive membership policy (C) collapses as tenants grow

| Query | Tenants | A | C | C / A |
|---|---|---|---|---|
| Q1 | 10 | 0.88 | 2.42 | 2.7x |
| Q1 | 100 | 0.90 | 13.80 | 15x |
| Q1 | 1000 | 0.93 | 132.53 | 142x |
| Q3 | 100 | 0.69 | 28.58 | 41x |
| Q3 | 1000 | 0.73 | 43.51 | 59x |

The query plans explain it. With C the planner reads the `telemetry_time_idx` index of each chunk and filters rows by the membership subquery. With B and D it uses `telemetry_tenant_device_time`. The membership subquery cannot become an index condition, so the more tenants there are, the more rows are read and thrown away. Adding a time window (Q3) helps but does not fix it.

### 3. The rewritten membership policy (E) depends on rows per device

| Tenants | Rows per device | E, Q1 p50 | E, Q1 p99 | Index used |
|---|---|---|---|---|
| 10 | about 201,000 | 2.08 ms | 2.57 ms | time index |
| 100 | about 20,100 | 35.17 ms (range 31.7 to 45.2) | 763 ms (range 82 to 917) | tenant/device/time index |
| 1000 | about 2,010 | 4.34 ms | 9.62 ms | tenant/device/time index |

My reading, which fits the data but I did not isolate: a policy that allows several tenants cannot return rows in time order from the tenant index, so for an unbounded "latest 100" the database reads and sorts every row of the device (about 20,000 at 100 tenants, about 2,000 at 1,000 tenants). With 200,000 rows per device the planner avoids that and walks the time index instead. That would produce the non-monotonic pattern above. The 100-tenant case is also the noisiest result in the whole run, so treat its absolute value with care. The direction is not in doubt: E is not a general fix.

### 4. Planning is most of the premium on simple lookups (prepared subset)

A smaller run at 100 tenants, 1 client, prepared statements, 3 measured repetitions:

| Query | A | B | E |
|---|---|---|---|
| Q1 p50 (ms) | 0.99 | 0.71 | 21.24 |
| Q3 p50 (ms) | 0.86 | 0.93 | 3.63 |

Under prepared statements B was *faster* than A on Q1, even though B's context setting costs two extra round trips in this mode. I do not read that as "RLS speeds queries up". It says that the 1.4x gap in the simple protocol is largely per-query planning. A was also about 10% slower prepared than simple (0.99 vs 0.90 ms), which suggests the generic plan for A's explicit predicate is worse than the plan with literal values. I did not compare plans for this case, so I list it as an open question rather than a finding.

### 5. Writes

INSERT throughput, 2 writers, batches of 1,000, rows per second (median of 5):

| Tenants | A | B | C | E |
|---|---|---|---|---|
| 10 | 68,143 | 66,631 (0.98x) | 67,031 (0.98x) | 65,695 (0.96x) |
| 100 | 68,727 | 68,102 (0.99x) | 67,746 (0.99x) | 67,800 (0.99x) |
| 1000 | 65,250 | 63,287 (0.97x) | 64,321 (0.99x) | 63,330 (0.97x) |

RLS cost 1% to 4% of write throughput, and the ranges overlap or nearly touch, so I would only claim "a few percent at most". Unlike reads, the policy shape did not matter for writes here.

Baseline ingest, no RLS (rows per second, median):

- `COPY` was 2.5x to 3x faster than `INSERT`: 115,000 vs 38,500 rows/s for one writer at batch size 10,000.
- Larger batches helped (COPY with 1 writer: 88,000 at 500 rows to 115,000 at 10,000) but raised batch latency.
- Going from 1 to 2 writers gave 1.6x. Going to 4 writers gave nothing (1.01x) because the 2 vCPUs were already saturated, and batch p95 latency doubled.

## What I can and cannot claim

**Supported by the data:**

- On this setup, a session-setting RLS policy adds a roughly constant 0.1 to 0.35 ms per query and does not grow with tenant count (10 to 1,000).
- A membership subquery policy degrades sharply with tenant count because it defeats the useful index.
- Write throughput overhead from RLS is a few percent or less.

**Not supported, or open:**

- **Q2 at 100 and 1,000 tenants.** Variants A, B and D are slow and noisy there (A: p50 range 9.9 to 23.3 ms at 100 tenants, median p99 about 170 ms), while C and E are faster (about 4 to 5 ms). The plans show A, B and D using a bitmap heap scan across two indexes, and C and E using an index scan on the mission index. This is a planner choice, not an RLS speed-up. The "x A" ratios below 1 in those rows should not be read as "RLS is faster".
- **Chunk count.** The data has only 7 chunks. RLS conditions are applied per chunk, so the planning overhead may grow with the number of chunks. Not tested.
- **Why bytes per row differ** (214.6, 207.7 and 177.4 B/row at 10, 100 and 1,000 tenants) is still unexplained. It does not affect the latency conclusions.
- **The prepared-statement result** above, for the reason given there.

## Limitations

- One 2 vCPU shared-vCPU VPS, one run per configuration with 5 measured repetitions (3 in the prepared subset). Absolute numbers will differ on other hardware.
- The dataset (about 4 GB) mostly fits in memory (2 GB of `shared_buffers` plus the OS cache). A larger-than-memory dataset would change the read results.
- Synthetic data, three query shapes, one policy per variant, single node, no compression, no continuous aggregates, at most 2 concurrent clients.
- pgbench 18.6 as the client against PostgreSQL 16.15 on a musl-based container image, Ubuntu 26.04 host.
- Memberships are small (a user belongs to few tenants). A user with hundreds of tenants will behave differently.

## Reproduce

```
bash bench/vm/setup.sh          # Ubuntu VM: Docker, pgbench, Python, TimescaleDB
bash bench/vm/run_all.sh        # about 4.5 hours on 2 vCPU
python bench/report.py --results-dir results/official --prepared-dir results/official_prepared
```

## Next experiments

1. **Variant F:** cache the user's tenant list in a session setting (`app.tenant_ids`) and compare with E. This tests whether the membership lookup is the remaining cost.
2. **More chunks:** repeat B vs A with 30 to 100 chunks to see whether per-chunk RLS planning cost grows.
3. **Look at the A, B and D Q2 plans** at 100 tenants to see why the planner picks the bitmap plan, and whether `ANALYZE` settings change it.
4. **Larger-than-memory run** and compression enabled.

## A note on safety

The first attempt at this run was hijacked: the database container was published on a public port with a default password, and a bot used `COPY ... FROM PROGRAM` to install a cryptominer. All results from that server were discarded and the run was repeated on a rebuilt server. The repository now binds the database to localhost only and generates a random password during setup (see `docs/VM_RUNBOOK.md`).
