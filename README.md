# tsdb-multitenant-bench

A reproducible benchmark of multi-tenant telemetry on PostgreSQL / TimescaleDB, starting with one question: what does row-level security cost on a shared hypertable?

**Status:** protocol stage. No results yet. See [PROTOCOL.md](PROTOCOL.md).

## Question

What do RLS policies cost in ingest throughput and query latency at 10, 100 and 1,000 tenants, and which policy designs keep index-driven plans?

## Headline result

Not yet measured.

## Reproduce

Planned: a single command.

```
make up      # start PostgreSQL + TimescaleDB in Docker
make gen     # generate the synthetic drone fleet dataset
make bench   # run experiments, write JSON to results/
make report  # build charts from results/
```

## Layout

```
PROTOCOL.md   # question, workload, method, limits
DECISIONS.md  # design decisions and alternatives rejected
bench/        # schema, generator, runner
results/      # raw JSON and CLAIMS.md (every public number -> run ID + commit)
notes/        # per-session notes
docs/         # diagrams
```

## Licence

Code: Apache-2.0. Articles, charts and result data: CC BY 4.0.

## Contact

captjacksparrow1109@gmail.com
