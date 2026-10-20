#!/usr/bin/env bash
# The official run. Start it inside tmux so it survives a dropped SSH session:
#   tmux new -s bench
#   bash bench/vm/run_all.sh
# Order: smoke (validates everything) -> Test 2/4 (main) -> Test 3 -> Test 1 -> prepared-protocol subset.
# Expect roughly 4-6 hours in total. Output goes to results/official/ and logs/.
set -uo pipefail
cd "$(dirname "$0")/../.."

# The DSN comes from .env (written by setup.sh with a random password), unless BENCH_DSN is set.
if [ -z "${BENCH_DSN:-}" ]; then
  PGPW=$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
  export BENCH_DSN="postgresql://postgres:${PGPW}@127.0.0.1:5432/bench"
fi
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

# Abort the whole run if the database stops answering, instead of failing every later step.
check_db() { psql "$BENCH_DSN" -Atc "select 1" >/dev/null 2>&1 || { echo "[$(stamp)] ABORT database unreachable before $1" | tee -a logs/run_all.log; exit 1; }; }
check_db start

# 0. Smoke on the real VM. Stop here if anything is wrong; it is cheap.
step smoke_queries $PY bench/run_queries.py --smoke --results-dir results/smoke || exit 1
step smoke_ingest_baseline $PY bench/run_ingest.py baseline --smoke --results-dir results/smoke || exit 1
step smoke_ingest_rls $PY bench/run_ingest.py rls --smoke --results-dir results/smoke || exit 1

# 1. Main test: read latency with and without RLS, plus plans (Tests 2 and 4).
check_db test2
step test2_queries $PY bench/run_queries.py --clients "$CLIENTS" --results-dir "$OUT" || exit 1

# 2. Writes (Tests 3 and 1).
check_db test3
step test3_ingest_rls $PY bench/run_ingest.py rls --writers "$WRITERS_RLS" --results-dir "$OUT" || exit 1
check_db test1
step test1_ingest_baseline $PY bench/run_ingest.py baseline --writers "$WRITERS_BASE" --results-dir "$OUT" || exit 1

# 3. Prepared-statement subset: how much of the RLS cost is planning?
check_db prepared
step prepared_subset $PY bench/run_queries.py --tenants 100 --protocol prepared \
    --variants A,B,E --queries q1,q3 --clients 1 --reps 4 --results-dir results/official_prepared || exit 1

$PY bench/summarize_plans.py --results-dir "$OUT" || true
echo "[$(stamp)] ALL STEPS FINISHED" | tee -a logs/run_all.log
