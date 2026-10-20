#!/usr/bin/env bash
# The official run. Start it inside tmux so it survives a dropped SSH session:
#   tmux new -s bench
#   bash bench/vm/run_all.sh
# Order: smoke (validates everything) -> Test 2/4 (main) -> Test 3 -> Test 1 -> prepared-protocol subset.
# Expect roughly 4-6 hours in total. Output goes to results/official/ and logs/.
set -uo pipefail
cd "$(dirname "$0")/../.."

export BENCH_DSN="${BENCH_DSN:-postgresql://postgres:postgres@localhost:5432/bench}"
PY=.venv/bin/python
OUT=results/official
mkdir -p "$OUT" logs

# Scale concurrency to the machine: pgbench and Postgres share the same cores, so more clients than
# cores would measure queueing, not query cost.
NCPU=$(nproc)
if [ "$NCPU" -le 2 ]; then
  CLIENTS=1,2; WRITERS_BASE=1,2,4; WRITERS_RLS=2
else
  CLIENTS=1,8; WRITERS_BASE=1,4,8; WRITERS_RLS=4
fi
echo "vCPU=$NCPU clients=$CLIENTS baseline writers=$WRITERS_BASE rls writers=$WRITERS_RLS" | tee -a logs/run_all.log
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
step() {
  local name="$1"; shift
  echo "[$(stamp)] START $name" | tee -a logs/run_all.log
  if "$@" 2>&1 | tee "logs/${name}.log"; then
    echo "[$(stamp)] DONE  $name" | tee -a logs/run_all.log
  else
    echo "[$(stamp)] FAIL  $name (see logs/${name}.log)" | tee -a logs/run_all.log
    return 1
  fi
}

# 0. Smoke on the real VM. Stop here if anything is wrong; it is cheap.
step smoke_queries $PY bench/run_queries.py --smoke --results-dir results/smoke || exit 1
step smoke_ingest_baseline $PY bench/run_ingest.py baseline --smoke --results-dir results/smoke || exit 1
step smoke_ingest_rls $PY bench/run_ingest.py rls --smoke --results-dir results/smoke || exit 1

# 1. Main test: read latency with and without RLS, plus plans (Tests 2 and 4).
step test2_queries $PY bench/run_queries.py --clients "$CLIENTS" --results-dir "$OUT"

# 2. Writes (Tests 3 and 1).
step test3_ingest_rls $PY bench/run_ingest.py rls --writers "$WRITERS_RLS" --results-dir "$OUT"
step test1_ingest_baseline $PY bench/run_ingest.py baseline --writers "$WRITERS_BASE" --results-dir "$OUT"

# 3. Prepared-statement subset: how much of the RLS cost is planning?
step prepared_subset $PY bench/run_queries.py --tenants 100 --protocol prepared \
    --variants A,B,E --queries q1,q3 --clients 1 --reps 4 --results-dir results/official_prepared

$PY bench/summarize_plans.py --results-dir "$OUT" || true
echo "[$(stamp)] ALL STEPS FINISHED" | tee -a logs/run_all.log
