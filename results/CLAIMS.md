# Claims ledger

Every number that appears in a public post, article or README must have a row here.

| Claim | Value | Run ID | Commit | Raw file |
|---|---|---|---|---|
| B (session-setting RLS) adds about 0.33 to 0.35 ms to Q1 p50 at 10, 100 and 1000 tenants, 1 client (1.36x to 1.40x of no RLS) | 0.88 to 0.93 ms (A) vs 1.22 to 1.26 ms (B) | 20261020-141827 | 4473f22 | results/official/queries_raw.jsonl, queries_summary.csv |
| B adds about 0.10 to 0.14 ms (1.14x to 1.20x) to Q3 p50, 1 client | 0.69 to 0.73 ms (A) vs 0.82 to 0.85 ms (B) | 20261020-141827 | 4473f22 | results/official/queries_summary.csv |
| D (policy plus explicit predicate) is within 0.05 ms of B on Q1 and Q3 | 1.27/1.30/1.31 vs 1.22/1.25/1.26 ms on Q1 | 20261020-141827 | 4473f22 | results/official/queries_summary.csv |
| Naive membership subquery policy (C) is 2.7x, 15x and 142x slower than no RLS on Q1 p50 at 10, 100 and 1000 tenants | 2.42, 13.80, 132.53 ms vs 0.88, 0.90, 0.93 ms | 20261020-141827 | 4473f22 | results/official/queries_summary.csv; plans in results/official/plans/ |
| Rewritten membership policy (E) on Q1: 2.08, 35.17 and 4.34 ms p50 at 10, 100 and 1000 tenants; p99 763 ms at 100 tenants | as stated | 20261020-141827 | 4473f22 | results/official/queries_summary.csv |
| In the prepared-statement run at 100 tenants, B was 0.71 ms vs A 0.99 ms on Q1 p50 (cause not isolated; 3 measured repetitions) | 0.71 vs 0.99 ms | 20261020-182402 | 4473f22 | results/official_prepared/queries_summary.csv |
| RLS lowered INSERT throughput by 1% to 4% (2 writers, batch 1000), within overlapping ranges | B/A 0.97 to 0.99; C/A 0.98 to 0.99; E/A 0.96 to 0.99 | 20261020-173601 | 4473f22 | results/official/ingest_rls_summary.csv |
| COPY was 2.5x to 3x faster than INSERT without RLS; 4 writers gave no gain over 2 on 2 vCPU | 115,008 vs 38,546 rows/s (1 writer, batch 10,000) | 20261020-175648 | 4473f22 | results/official/ingest_baseline_summary.csv |
| CPU steal averaged 0.01% (max 1%) during the run | 0.01% / 1% | all four run tags | 4473f22 | results/official/raw/sysstat_*.log (see report.md) |
