.PHONY: up down smoke test1 test3 test2 clean

# DSN used by all runners. Override: make test2 BENCH_DSN=postgresql://...
BENCH_DSN ?= postgresql://postgres:postgres@localhost:5432/bench
export BENCH_DSN

up:
	docker compose up -d --wait

down:
	docker compose down -v

# Tiny run to validate the harness (needs pgbench on PATH; use Linux or WSL2).
smoke:
	python bench/run_queries.py --smoke
	python bench/run_ingest.py baseline --smoke
	python bench/run_ingest.py rls --smoke

test1:
	python bench/run_ingest.py baseline

test3:
	python bench/run_ingest.py rls

# Tests 2 and 4 (plans are captured in the same run).
test2:
	python bench/run_queries.py

clean:
	rm -rf data/ __pycache__ bench/__pycache__ .venv
