#!/usr/bin/env python3
"""Tests 2 and 4: read latency with and without RLS, and the query plans behind it.

For each tenant count: load the dataset once (as the table owner, via COPY), then for every
repetition and every variant: apply the variant's policy, verify RLS state and isolation,
capture query plans, and run pgbench for each query and client count as the non-owner role.
Variant order is rotated each repetition so drift does not favour one variant.
Repetition 0 is a warm-up and is excluded from the summary.

Requires pgbench on PATH (Linux; on Windows run inside WSL2).

  python bench/run_queries.py --tenants 10,100,1000 --rows 20000000
  python bench/run_queries.py --smoke
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg

import common as C

HERE = C.HERE


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dsn")
    p.add_argument("--schema", help="schema file for generate.py (default bench/schema.sql)")
    p.add_argument("--tenants", default="10,100,1000")
    p.add_argument("--dpt", type=int, default=10, help="devices per tenant")
    p.add_argument("--rows", type=int, default=20_000_000)
    p.add_argument("--span-days", type=float, default=7.0)
    p.add_argument("--mission-len", type=int, default=600)
    p.add_argument("--variants", default="A,B,C,D")
    p.add_argument("--queries", default="q1,q2")
    p.add_argument("--clients", default="1,8")
    p.add_argument("--duration", type=int, default=20, help="seconds per pgbench run")
    p.add_argument("--reps", type=int, default=6, help="repetitions per config; the first is a warm-up")
    p.add_argument("--threads", type=int, default=4, help="max pgbench threads")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--skip-load", action="store_true", help="reuse the data already loaded (single tenant count only)")
    p.add_argument("--results-dir", default=str(C.ROOT / "results"))
    p.add_argument("--smoke", action="store_true", help="tiny, fast configuration to validate the harness")
    a = p.parse_args()
    if a.smoke:
        a.tenants, a.rows, a.duration, a.reps, a.clients = "10,20", 200_000, 3, 2, "1,2"
        if a.results_dir == str(C.ROOT / "results"):
            a.results_dir = str(C.ROOT / "results" / "smoke")
    return a


def load_dataset(a, dsn, tenants):
    cmd = [sys.executable, str(HERE / "generate.py"), "--apply-schema", "--dsn", dsn,
           "--tenants", str(tenants), "--devices-per-tenant", str(a.dpt),
           "--total-rows", str(a.rows), "--span-days", str(a.span_days),
           "--mission-len", str(a.mission_len), "--seed", str(a.seed)]
    if a.schema:
        cmd += ["--schema", a.schema]
    t0 = time.time()
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"load failed:\n{out.stdout}\n{out.stderr}")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("VACUUM (ANALYZE) telemetry")
        c.execute("CHECKPOINT")
    stats = C.dataset_stats(dsn)
    stats["load_seconds"] = round(time.time() - t0, 1)
    print(f"  loaded {stats['rows']:,} rows, {stats['total_bytes'] / 1e6:,.0f} MB "
          f"({stats['bytes_per_row']} B/row) in {stats['load_seconds']}s", flush=True)
    return stats


def sample_sql(script, t, dev, mid):
    """The SELECT of a pgbench script with fixed literals, for EXPLAIN."""
    for line in script.read_text().splitlines():
        if line.startswith("SELECT"):
            return (line.rstrip(";").replace(":dev", str(dev)).replace(":mid", str(mid))
                    .replace(":t", str(t)))
    raise RuntimeError(f"no SELECT in {script}")


def capture_plan(dsn, variant, qname, script, results_dir, tenants, dpt, tag):
    sql = sample_sql(script, 1, 1, 1)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"SET ROLE {C.APP_ROLE}")
        c.execute("SET app.tenant_id = '1'")
        c.execute("SET app.user_id = '1'")
        plan = c.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql).fetchone()[0]
    d = results_dir / "plans"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tag}_t{tenants}_{variant}_{qname}.json").write_text(json.dumps(plan, indent=1))
    info = C.analyze_plan(plan)
    C.append_jsonl(results_dir / "plans_summary.jsonl", {
        "tag": tag, "tenants": tenants, "variant": variant, "query": qname, **info})
    return info


def run_pgbench(a, dsn, script, tenants, mpd, mpf, clients, rep, logdir):
    for f in logdir.glob("pgb*"):
        f.unlink()
    cmd = ["pgbench", "-n", "-f", str(script), "-c", str(clients), "-j", str(min(clients, a.threads)),
           "-T", str(a.duration), "--random-seed", str(a.seed + rep),
           "-D", f"tenants={tenants}", "-D", f"dpt={a.dpt}", "-D", f"mpd={mpd}", "-D", f"mpf={mpf}",
           "-l", f"--log-prefix={logdir / 'pgb'}", dsn]
    env = dict(os.environ, PGOPTIONS=f"-c role={C.APP_ROLE}")
    out = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if out.returncode != 0:
        sys.exit(f"pgbench failed:\n{out.stdout}\n{out.stderr}")
    lat = []
    for f in logdir.glob("pgb*"):
        with open(f) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3:
                    lat.append(int(parts[2]) / 1000.0)  # microseconds -> ms
    lat.sort()
    tps = re.search(r"tps = ([\d.]+)", out.stdout)
    failed = re.search(r"number of failed transactions: (\d+)", out.stdout)
    n_failed = int(failed.group(1)) if failed else 0
    if not lat or n_failed:
        sys.exit(f"bad pgbench run (txns={len(lat)}, failed={n_failed}):\n{out.stdout}\n{out.stderr}")
    return {
        "txns": len(lat), "failed": n_failed, "tps": float(tps.group(1)) if tps else None,
        "mean_ms": sum(lat) / len(lat), "p50_ms": C.percentile(lat, 50),
        "p95_ms": C.percentile(lat, 95), "p99_ms": C.percentile(lat, 99), "max_ms": lat[-1],
    }


def main():
    a = parse_args()
    dsn = C.get_dsn(a.dsn)
    results_dir = Path(a.results_dir)
    raw = results_dir / "queries_raw.jsonl"
    tag = time.strftime("%Y%m%d-%H%M%S")
    tenant_list = [int(x) for x in a.tenants.split(",")]
    variants = a.variants.split(",")
    queries = a.queries.split(",")
    clients_list = [int(x) for x in a.clients.split(",")]
    if a.skip_load and len(tenant_list) != 1:
        sys.exit("--skip-load needs exactly one --tenants value")
    for t in tenant_list:
        if t < 2:
            sys.exit("need at least 2 tenants for the isolation check")

    sysstat = C.start_sysstat(results_dir / "raw" / f"sysstat_{tag}.log")
    env = C.capture_environment(dsn, results_dir, {"run_tag": tag, "args": vars(a)})
    print(f"PostgreSQL: {env['postgres'].split(',')[0]} | TimescaleDB: {env['timescaledb']}", flush=True)
    logdir = Path(tempfile.mkdtemp(prefix="pgbench_logs_"))

    try:
        for tenants in tenant_list:
            print(f"\n=== tenants={tenants} ===", flush=True)
            stats = None if a.skip_load else load_dataset(a, dsn, tenants)
            if stats is None:
                stats = C.dataset_stats(dsn)
            C.append_jsonl(results_dir / "datasets.jsonl", {"tenants": tenants, "tag": tag, **stats})
            per_device = a.rows // (tenants * a.dpt)
            mpd = math.ceil(per_device / a.mission_len)
            mpf = per_device // a.mission_len
            if mpf < 1:
                sys.exit("rows per device smaller than one mission; raise --rows or lower --mission-len")

            for rep in range(a.reps):
                order = variants[rep % len(variants):] + variants[:rep % len(variants)]
                for variant in order:
                    C.apply_policy(dsn, variant)
                    C.verify_variant(dsn, variant, a.dpt)
                    for q in queries:
                        script = HERE / "queries" / f"{q}_{C.VARIANTS[variant]['queries']}.sql"
                        if rep == 0:
                            info = capture_plan(dsn, variant, q, script, results_dir, tenants, a.dpt, tag)
                            print(f"  plan {variant}/{q}: telemetry scans = {', '.join(info['telemetry_scans']) or 'none'}; "
                                  f"chunks scanned = {info['chunks_scanned']}; indexes = {', '.join(info['indexes_used']) or 'none'}", flush=True)
                        for clients in clients_list:
                            with psycopg.connect(dsn, autocommit=True) as c:
                                c.execute("CHECKPOINT")
                            res = run_pgbench(a, dsn, script, tenants, mpd, mpf, clients, rep, logdir)
                            C.append_jsonl(raw, {
                                "tag": tag, "time": C.utcnow(), "tenants": tenants, "variant": variant,
                                "query": q, "clients": clients, "rep": rep, "warmup": rep == 0, **res})
                            print(f"  rep{rep}{' (warm-up)' if rep == 0 else ''} {variant} {q} c={clients}: "
                                  f"p50={res['p50_ms']:.2f} p95={res['p95_ms']:.2f} p99={res['p99_ms']:.2f} ms, "
                                  f"{res['tps']:.0f} tps", flush=True)
    finally:
        C.stop_sysstat(sysstat)

    summary = C.summarize(raw, ["tenants", "variant", "query", "clients"],
                          ["p50_ms", "p95_ms", "p99_ms", "tps"], results_dir / "queries_summary.csv")
    print(f"\nwrote {len(summary)} rows to {results_dir / 'queries_summary.csv'}")


if __name__ == "__main__":
    main()
