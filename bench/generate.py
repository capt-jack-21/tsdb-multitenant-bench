#!/usr/bin/env python3
"""Synthetic drone-fleet telemetry generator (seeded, reproducible).

Fixed total rows and a fixed simulated time span across all tenant counts, so
chunking is comparable between configurations. The per-device sampling interval
is derived: span / (total_rows / devices).

Rows are emitted time-major (all devices for t0, then t1, ...), like a live
fleet reporting, not device-by-device.

Examples:
  python bench/generate.py --dry-run --total-rows 1000000
  export BENCH_DSN="postgresql://postgres:postgres@localhost:5432/bench"
  python bench/generate.py --apply-schema --tenants 100 --total-rows 20000000
"""
import argparse
import math
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
HERE = Path(__file__).resolve().parent

COPY_SQL = (
    "COPY telemetry (time, tenant_id, device_id, mission_id, lat, lon, alt_m, "
    "battery_pct, temp_c, status, odometer_m) FROM STDIN"
)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dsn", default=os.environ.get("BENCH_DSN"), help="PostgreSQL DSN (or set BENCH_DSN)")
    p.add_argument("--tenants", type=int, default=100)
    p.add_argument("--devices-per-tenant", type=int, default=10)
    p.add_argument("--total-rows", type=int, default=20_000_000, help="approximate; duplicates add a little")
    p.add_argument("--span-days", type=float, default=7.0, help="simulated time span")
    p.add_argument("--mission-len", type=int, default=600, help="samples per mission")
    p.add_argument("--late-frac", type=float, default=0.01, help="fraction of late (back-dated) rows")
    p.add_argument("--dup-frac", type=float, default=0.005, help="fraction of duplicated rows")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--apply-schema", action="store_true", help="run the schema file first (destructive)")
    p.add_argument("--schema", default=None, help="schema file (default: bench/schema.sql)")
    p.add_argument("--reference-only", action="store_true",
                   help="load tenants/devices/missions/memberships only, no telemetry rows")
    p.add_argument("--dry-run", action="store_true", help="generate rows, do not touch a database")
    return p.parse_args()


def load_reference_data(conn, a, devices, per_device, step_s, missions_per_device):
    """Insert tenants, memberships, devices, missions."""
    with conn.cursor() as cur:
        with cur.copy("COPY tenants (tenant_id, name) FROM STDIN") as cp:
            for t in range(1, a.tenants + 1):
                cp.write_row((t, f"tenant-{t:04d}"))
        # One user per tenant for the membership policy (user_id == tenant_id).
        with cur.copy("COPY memberships (user_id, tenant_id) FROM STDIN") as cp:
            for t in range(1, a.tenants + 1):
                cp.write_row((t, t))
        with cur.copy("COPY devices (device_id, tenant_id, name) FROM STDIN") as cp:
            for d in range(1, devices + 1):
                cp.write_row((d, (d - 1) // a.devices_per_tenant + 1, f"drone-{d:06d}"))
        with cur.copy(
            "COPY missions (mission_id, tenant_id, device_id, started_at, ended_at) FROM STDIN"
        ) as cp:
            for d in range(1, devices + 1):
                tenant = (d - 1) // a.devices_per_tenant + 1
                for m in range(missions_per_device):
                    first = m * a.mission_len
                    last = min((m + 1) * a.mission_len, per_device) - 1
                    cp.write_row((
                        (d - 1) * missions_per_device + m + 1, tenant, d,
                        EPOCH + timedelta(seconds=first * step_s),
                        EPOCH + timedelta(seconds=last * step_s),
                    ))


def generate_rows(a, devices, per_device, step_s, missions_per_device):
    """Yield telemetry tuples in time-major order."""
    rng = random.Random(a.seed)
    lat = [12.0 + rng.random() for _ in range(devices)]
    lon = [77.0 + rng.random() for _ in range(devices)]
    odo = [0.0] * devices
    base_temp = [rng.uniform(15, 35) for _ in range(devices)]
    L = a.mission_len

    for i in range(per_device):
        ts = EPOCH + timedelta(seconds=i * step_s)
        in_mission = i % L
        phase = in_mission / L
        mission_idx = i // L
        status = 1 if phase < 0.05 else (3 if phase > 0.95 else 2)
        alt = 0.0 if status in (1, 3) and in_mission < 3 else 60.0 + 40.0 * math.sin(phase * math.pi)
        battery = 100.0 - 80.0 * phase
        for d in range(devices):
            lat[d] += rng.uniform(-1, 1) * 1e-5
            lon[d] += rng.uniform(-1, 1) * 1e-5
            odo[d] += rng.uniform(0.0, 15.0) * step_s
            st = 4 if rng.random() < 0.0005 else status
            row_ts = ts
            if rng.random() < a.late_frac:
                row_ts = max(EPOCH, ts - timedelta(seconds=rng.uniform(1, 300)))
            row = (
                row_ts,
                d // a.devices_per_tenant + 1,
                d + 1,
                d * missions_per_device + mission_idx + 1,
                lat[d], lon[d], alt + rng.uniform(-2, 2),
                battery + rng.uniform(-0.5, 0.5),
                base_temp[d] + rng.uniform(-1, 1),
                st, odo[d],
            )
            yield row
            if rng.random() < a.dup_frac:
                yield row


def main():
    a = parse_args()
    devices = a.tenants * a.devices_per_tenant
    per_device = a.total_rows // devices
    if per_device < 1:
        sys.exit("total-rows too small for this many devices")
    step_s = a.span_days * 86400.0 / per_device
    missions_per_device = math.ceil(per_device / a.mission_len)
    print(
        f"tenants={a.tenants} devices={devices} rows/device={per_device} "
        f"interval={step_s:.2f}s span={a.span_days}d seed={a.seed}",
        flush=True,
    )
    if step_s < 1:
        print("note: sampling interval below 1 s; fine for a benchmark, unlike a real drone fleet", flush=True)

    conn = None
    if not a.dry_run:
        if not a.dsn:
            sys.exit("set --dsn or BENCH_DSN (or use --dry-run)")
        import psycopg  # imported here so --dry-run needs no driver
        conn = psycopg.connect(a.dsn)
        if a.apply_schema:
            schema_path = Path(a.schema) if a.schema else HERE / "schema.sql"
            conn.execute(schema_path.read_text())
            conn.commit()
        load_reference_data(conn, a, devices, per_device, step_s, missions_per_device)
        conn.commit()
        if a.reference_only:
            conn.close()
            print("reference data loaded (no telemetry)")
            return

    t0 = time.perf_counter()
    count = 0
    rows = generate_rows(a, devices, per_device, step_s, missions_per_device)
    if conn is None:
        for _ in rows:
            count += 1
    else:
        with conn.cursor() as cur, cur.copy(COPY_SQL) as cp:
            for row in rows:
                cp.write_row(row)
                count += 1
                if count % 1_000_000 == 0:
                    print(f"{count:>12,} rows  {count / (time.perf_counter() - t0):,.0f} rows/s", flush=True)
        conn.commit()
        conn.execute("ANALYZE telemetry")
        conn.commit()
        conn.close()
    dt = time.perf_counter() - t0
    print(f"done: {count:,} rows in {dt:.1f}s ({count / dt:,.0f} rows/s)")


if __name__ == "__main__":
    main()
