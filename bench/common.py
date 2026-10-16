"""Shared helpers for the benchmark runners."""
import csv
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP_ROLE = "bench_app"

# Variant -> which policy file to apply and which query flavour to run.
VARIANTS = {
    "A": {"policy": "rls_off.sql", "queries": "a",
          "desc": "no RLS; query carries an explicit tenant predicate"},
    "B": {"policy": "rls_session.sql", "queries": "rls",
          "desc": "RLS session-setting policy; query has no tenant predicate"},
    "C": {"policy": "rls_membership.sql", "queries": "rls",
          "desc": "RLS membership-join policy; query has no tenant predicate"},
    "D": {"policy": "rls_session.sql", "queries": "a",
          "desc": "RLS session-setting policy plus explicit tenant predicate"},
}


def get_dsn(arg):
    dsn = arg or os.environ.get("BENCH_DSN")
    if not dsn:
        sys.exit("set --dsn or BENCH_DSN, e.g. postgresql://postgres:postgres@localhost:5432/bench")
    return dsn


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = max(0, min(len(sorted_vals) - 1, math.ceil(p / 100 * len(sorted_vals)) - 1))
    return sorted_vals[k]


def apply_policy(dsn, variant):
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute((HERE / VARIANTS[variant]["policy"]).read_text())


def verify_variant(dsn, variant, dpt):
    """Fail loudly if RLS is not in the state the variant claims. Also proves isolation."""
    want_rls = VARIANTS[variant]["policy"] != "rls_off.sql"
    with psycopg.connect(dsn, autocommit=True) as c:
        row = c.execute(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'telemetry' AND relkind IN ('r','p')"
        ).fetchone()
    if row is None or bool(row[0]) != want_rls:
        raise RuntimeError(f"variant {variant}: relrowsecurity={row} but expected {want_rls}")
    if not want_rls:
        return
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"SET ROLE {APP_ROLE}")
        c.execute("SET app.tenant_id = '1'")
        c.execute("SET app.user_id = '1'")
        own = c.execute("SELECT count(*) FROM telemetry WHERE device_id = 1").fetchone()[0]
        other = c.execute("SELECT count(*) FROM telemetry WHERE device_id = %s", (dpt + 1,)).fetchone()[0]
    if own == 0 or other != 0:
        raise RuntimeError(f"variant {variant}: isolation check failed (own={own}, other-tenant={other})")


def verify_variant_empty_ok(dsn, variant):
    """RLS-state check for tests that run on an empty telemetry table (no isolation probe possible)."""
    want_rls = VARIANTS[variant]["policy"] != "rls_off.sql"
    with psycopg.connect(dsn, autocommit=True) as c:
        row = c.execute(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'telemetry' AND relkind IN ('r','p')"
        ).fetchone()
    if row is None or bool(row[0]) != want_rls:
        raise RuntimeError(f"variant {variant}: relrowsecurity={row} but expected {want_rls}")


def _read_meminfo():
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def capture_environment(dsn, results_dir, extra=None):
    env = {
        "captured_at": utcnow(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "mem_total_bytes": _read_meminfo(),
        "pgbench": None,
    }
    if shutil.which("pgbench"):
        env["pgbench"] = subprocess.run(["pgbench", "--version"], capture_output=True, text=True).stdout.strip()
    with psycopg.connect(dsn, autocommit=True) as c:
        env["postgres"] = c.execute("SELECT version()").fetchone()[0]
        r = c.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'").fetchone()
        env["timescaledb"] = r[0] if r else None
        names = ("shared_buffers", "effective_cache_size", "work_mem", "maintenance_work_mem",
                 "max_wal_size", "checkpoint_timeout", "synchronous_commit", "max_connections",
                 "wal_level", "autovacuum", "random_page_cost", "jit", "max_parallel_workers_per_gather")
        env["settings"] = dict(c.execute(
            "SELECT name::text, (setting || coalesce(unit, ''))::text FROM pg_settings WHERE name = ANY(%s)",
            (list(names),)
        ).fetchall())
    if extra:
        env.update(extra)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "environment.json").write_text(json.dumps(env, indent=2, default=str))
    return env


def dataset_stats(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        rows = c.execute("SELECT count(*) FROM telemetry").fetchone()[0]
        try:
            size = c.execute("SELECT hypertable_size('telemetry')").fetchone()[0]
        except psycopg.Error:
            size = c.execute("SELECT pg_total_relation_size('telemetry')").fetchone()[0]
    return {"rows": rows, "total_bytes": size, "bytes_per_row": round(size / rows, 1) if rows else None}


def start_sysstat(path):
    """Background vmstat (includes CPU steal) for the whole session, if available."""
    if not shutil.which("vmstat"):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(["vmstat", "-t", "1"], stdout=open(path, "w"), stderr=subprocess.DEVNULL)


def stop_sysstat(proc):
    if proc is not None:
        proc.terminate()


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def summarize(jsonl, keys, metrics, out_csv):
    """Median / min / max across measured (non-warm-up) runs per configuration."""
    if not Path(jsonl).exists():
        return []
    groups = defaultdict(list)
    for line in open(jsonl):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("warmup"):
            continue
        groups[tuple(r[k] for k in keys)].append(r)
    out = []
    for g, rs in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        rec = dict(zip(keys, g))
        rec["n_runs"] = len(rs)
        for m in metrics:
            vals = [r[m] for r in rs]
            rec[m + "_median"] = round(statistics.median(vals), 4)
            rec[m + "_min"] = round(min(vals), 4)
            rec[m + "_max"] = round(max(vals), 4)
        out.append(rec)
    if out:
        with open(out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
    return out
