#!/usr/bin/env python3
"""Tests 1 and 3: write throughput.

  baseline : no RLS, fixed tenant count; vary method (COPY vs INSERT), batch size, writers.
  rls      : INSERT only (COPY cannot write to an RLS table); vary tenant count and variant
             A (no RLS) / B (session policy) / C (membership policy). Batches are per-tenant
             and every batch sets the tenant context first, in all variants, for symmetry.

Each run inserts the same rows into an empty telemetry table (TRUNCATE between runs). Batches are
pre-built and pre-encoded before the clock starts, so the timed region is network + server work.
Writers are separate processes. Repetition 0 is a warm-up and is excluded from the summary.

  python bench/run_ingest.py baseline
  python bench/run_ingest.py rls
  python bench/run_ingest.py baseline --smoke
"""
import argparse
import math
import multiprocessing as mp
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import psycopg

import common as C
import generate as gen

HERE = C.HERE

INSERT_SQL = (
    "INSERT INTO telemetry (time, tenant_id, device_id, mission_id, lat, lon, alt_m, battery_pct, "
    "temp_c, status, odometer_m) SELECT * FROM unnest(%s::timestamptz[], %s::int[], %s::int[], "
    "%s::int[], %s::float8[], %s::float8[], %s::real[], %s::real[], %s::real[], %s::smallint[], "
    "%s::float8[])"
)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("test", choices=["baseline", "rls"])
    p.add_argument("--dsn")
    p.add_argument("--schema")
    p.add_argument("--tenants", help="baseline default 100; rls default 10,100,1000")
    p.add_argument("--dpt", type=int, default=10)
    p.add_argument("--rows", type=int, default=1_000_000, help="rows inserted per run")
    p.add_argument("--span-days", type=float, default=7.0)
    p.add_argument("--mission-len", type=int, default=600)
    p.add_argument("--methods", help="baseline default copy,insert; rls is insert only")
    p.add_argument("--batch-sizes", help="baseline default 500,2000,10000; rls default 1000")
    p.add_argument("--writers", help="baseline default 1,4,8; rls default 4")
    p.add_argument("--variants", default="A,B,C", help="rls test only")
    p.add_argument("--reps", type=int, default=6, help="the first is a warm-up")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--results-dir", default=str(C.ROOT / "results"))
    p.add_argument("--smoke", action="store_true")
    a = p.parse_args()
    base = a.test == "baseline"
    a.tenants = [int(x) for x in (a.tenants or ("100" if base else "10,100,1000")).split(",")]
    a.methods = (a.methods or ("copy,insert" if base else "insert")).split(",")
    a.batch_sizes = [int(x) for x in (a.batch_sizes or ("500,2000,10000" if base else "1000")).split(",")]
    a.writers = [int(x) for x in (a.writers or ("1,4,8" if base else "4")).split(",")]
    a.variants = a.variants.split(",") if not base else ["A"]
    if a.smoke:
        a.rows, a.reps = 50_000, 2
        a.batch_sizes = a.batch_sizes[:1]
        a.writers = a.writers[:2]
        a.tenants = a.tenants[:2] if not base else [10]
        if a.results_dir == str(C.ROOT / "results"):
            a.results_dir = str(C.ROOT / "results" / "smoke")
    if a.test == "rls" and "copy" in a.methods:
        sys.exit("COPY FROM is not supported on RLS tables; the rls test is INSERT only")
    return a


def build_rows(a, tenants):
    ns = SimpleNamespace(tenants=tenants, devices_per_tenant=a.dpt, mission_len=a.mission_len,
                         late_frac=0.01, dup_frac=0.005, seed=a.seed)
    devices = tenants * a.dpt
    per_device = a.rows // devices
    step = a.span_days * 86400.0 / per_device
    mpd = math.ceil(per_device / a.mission_len)
    return list(gen.generate_rows(ns, devices, per_device, step, mpd))


def make_batches(rows, batch_size, per_tenant):
    if not per_tenant:
        return [(None, rows[i:i + batch_size]) for i in range(0, len(rows), batch_size)]
    by = defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    chunks = {t: [rs[i:i + batch_size] for i in range(0, len(rs), batch_size)] for t, rs in by.items()}
    out = []
    for k in range(max(len(v) for v in chunks.values())):
        for t in sorted(chunks):
            if k < len(chunks[t]):
                out.append((t, chunks[t][k]))
    return out


def _fmt(v):
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def encode(batches, method):
    out = []
    for tenant, rs in batches:
        if method == "insert":
            payload = [list(col) for col in zip(*rs)]
        else:
            payload = "".join("\t".join(map(_fmt, r)) + "\n" for r in rs).encode()
        out.append((tenant, len(rs), payload))
    return out


def writer(dsn, method, items, barrier, out_q):
    try:
        conn = psycopg.connect(dsn, autocommit=True)
        conn.execute(f"SET ROLE {C.APP_ROLE}")
        cur = conn.cursor()
        lat = []
        barrier.wait()
        t_start = time.perf_counter()
        for tenant, _n, payload in items:
            b0 = time.perf_counter()
            if tenant is not None:
                cur.execute("SELECT set_config('app.tenant_id', %s, false), "
                            "set_config('app.user_id', %s, false)", (str(tenant), str(tenant)))
            if method == "insert":
                cur.execute(INSERT_SQL, payload)
            else:
                with cur.copy(gen.COPY_SQL) as cp:
                    cp.write(payload)
            lat.append(time.perf_counter() - b0)
        t_end = time.perf_counter()
        conn.close()
        out_q.put(("ok", t_start, t_end, lat))
    except Exception as e:  # report to parent instead of hanging the barrier
        try:
            barrier.abort()
        except Exception:
            pass
        out_q.put(("error", repr(e), 0, []))


def run_once(dsn, method, encoded, n_writers, expected_rows):
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("TRUNCATE telemetry")
        c.execute("CHECKPOINT")
    time.sleep(1)
    ctx = mp.get_context()
    barrier = ctx.Barrier(n_writers)
    q = ctx.Queue()
    procs = [ctx.Process(target=writer, args=(dsn, method, encoded[w::n_writers], barrier, q))
             for w in range(n_writers)]
    for p in procs:
        p.start()
    results = [q.get() for _ in procs]
    for p in procs:
        p.join()
    errors = [r[1] for r in results if r[0] == "error"]
    if errors:
        sys.exit(f"writer failed: {errors[0]}")
    t0 = min(r[1] for r in results)
    t1 = max(r[2] for r in results)
    lat = sorted(x * 1000.0 for r in results for x in r[3])
    with psycopg.connect(dsn, autocommit=True) as c:
        got = c.execute("SELECT count(*) FROM telemetry").fetchone()[0]
    if got != expected_rows:
        sys.exit(f"row count mismatch: expected {expected_rows}, table has {got}")
    secs = t1 - t0
    return {"rows": expected_rows, "seconds": secs, "rows_per_s": expected_rows / secs,
            "batches": len(lat), "p50_ms": C.percentile(lat, 50), "p95_ms": C.percentile(lat, 95),
            "p99_ms": C.percentile(lat, 99)}


def main():
    a = parse_args()
    dsn = C.get_dsn(a.dsn)
    results_dir = Path(a.results_dir)
    raw = results_dir / f"ingest_{a.test}_raw.jsonl"
    tag = time.strftime("%Y%m%d-%H%M%S")
    sysstat = C.start_sysstat(results_dir / "raw" / f"sysstat_ingest_{tag}.log")
    env = C.capture_environment(dsn, results_dir, {"run_tag": tag, "args": vars(a)})
    print(f"PostgreSQL: {env['postgres'].split(',')[0]} | TimescaleDB: {env['timescaledb']}", flush=True)
    per_tenant = a.test == "rls"
    try:
        for tenants in a.tenants:
            print(f"\n=== tenants={tenants} ===", flush=True)
            cmd = [sys.executable, str(HERE / "generate.py"), "--apply-schema", "--reference-only", "--dsn", dsn,
                   "--tenants", str(tenants), "--devices-per-tenant", str(a.dpt),
                   "--total-rows", str(a.rows), "--span-days", str(a.span_days),
                   "--mission-len", str(a.mission_len), "--seed", str(a.seed)]
            if a.schema:
                cmd += ["--schema", a.schema]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"reference load failed:\n{r.stdout}\n{r.stderr}")
            rows = build_rows(a, tenants)
            for batch_size in a.batch_sizes:
                batches = make_batches(rows, batch_size, per_tenant)
                for method in a.methods:
                    encoded = encode(batches, method)
                    for n_writers in a.writers:
                        for rep in range(a.reps):
                            order = a.variants[rep % len(a.variants):] + a.variants[:rep % len(a.variants)]
                            for variant in order:
                                C.apply_policy(dsn, variant)
                                C.verify_variant_empty_ok(dsn, variant)
                                res = run_once(dsn, method, encoded, n_writers, len(rows))
                                C.append_jsonl(raw, {
                                    "tag": tag, "time": C.utcnow(), "test": a.test, "tenants": tenants,
                                    "variant": variant, "method": method, "batch_size": batch_size,
                                    "writers": n_writers, "rep": rep, "warmup": rep == 0, **res})
                                print(f"  rep{rep}{' (warm-up)' if rep == 0 else ''} {variant} {method} "
                                      f"bs={batch_size} w={n_writers}: {res['rows_per_s']:,.0f} rows/s, "
                                      f"batch p50={res['p50_ms']:.1f} p95={res['p95_ms']:.1f} "
                                      f"p99={res['p99_ms']:.1f} ms", flush=True)
    finally:
        C.stop_sysstat(sysstat)
    summary = C.summarize(raw, ["tenants", "variant", "method", "batch_size", "writers"],
                          ["rows_per_s", "p50_ms", "p95_ms", "p99_ms"],
                          results_dir / f"ingest_{a.test}_summary.csv")
    print(f"\nwrote {len(summary)} rows to {results_dir / f'ingest_{a.test}_summary.csv'}")


if __name__ == "__main__":
    main()
