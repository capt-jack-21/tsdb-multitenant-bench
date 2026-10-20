#!/usr/bin/env python3
"""Build a report skeleton from a results directory: tables, charts, environment, CPU steal.

  python bench/report.py --results-dir results/official [--prepared-dir results/official_prepared]

Writes <results-dir>/report.md and <results-dir>/charts/*.png. Everything in it is generated from the
summary files, so every number is traceable. The narrative (answer, recommendation, limitations) is
written by hand on top of this; nothing here interprets the numbers.
"""
import argparse
import csv
import json
import math
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

VARIANT_NAMES = {
    "A": "A  no RLS, explicit predicate",
    "B": "B  RLS session setting",
    "C": "C  RLS membership subquery (naive)",
    "D": "D  RLS session setting + predicate",
    "E": "E  RLS membership function (rewritten)",
}
# Fixed entity -> colour (categorical slots 1-5 of the validated default palette; never reordered).
COLORS = {"A": "#2a78d6", "B": "#eb6834", "C": "#1baf7a", "D": "#eda100", "E": "#e87ba4"}
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"


def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fmt(x, d=2):
    return "n/a" if x is None else f"{x:,.{d}f}"


def cell(row, metric, d=2):
    med, lo, hi = (num(row.get(f"{metric}_{s}")) for s in ("median", "min", "max"))
    if med is None:
        return "n/a"
    return f"{fmt(med, d)} ({fmt(lo, d)}-{fmt(hi, d)})"


def pretty_size(v):
    """pg_settings values like '163848kB' -> '160 MB' (shared_buffers is stored in 8 kB pages)."""
    m = re.fullmatch(r"(\d+)kB", str(v or ""))
    if not m:
        return v
    kb = int(m.group(1))
    return f"{kb / 1048576:.1f} GB" if kb >= 1048576 else f"{kb / 1024:.0f} MB"


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def ratio_str(v, base):
    if v is None or not base:
        return ""
    return f"{v / base:.2f}x"


# ---------- sections ----------

def section_environment(res):
    out = ["## Environment", ""]
    env_path = res / "environment.json"
    if env_path.exists():
        env = json.loads(env_path.read_text())
        s = env.get("settings", {})
        rows = [("PostgreSQL", env.get("postgres", "").split(",")[0]),
                ("TimescaleDB", env.get("timescaledb")),
                ("pgbench (client)", env.get("pgbench")),
                ("Platform", env.get("platform")),
                ("vCPUs (as seen by the OS)", env.get("cpu_count")),
                ("RAM", f"{(env.get('mem_total_bytes') or 0) / 2**30:.1f} GiB"),
                ("shared_buffers / effective_cache_size", f"{pretty_size(s.get('shared_buffers'))} / {pretty_size(s.get('effective_cache_size'))}"),
                ("work_mem / max_wal_size", f"{pretty_size(s.get('work_mem'))} / {pretty_size(s.get('max_wal_size'))}"),
                ("synchronous_commit / jit", f"{s.get('synchronous_commit')} / {s.get('jit')}"),
                ("Run tag", env.get("run_tag")), ("Captured", env.get("captured_at"))]
        out.append(md_table(["Item", "Value"], rows))
    else:
        out.append("_environment.json not found._")
    vm = res / "vm_environment.txt"
    if vm.exists():
        out += ["", "VM details (from `vm_environment.txt`):", "", "```", vm.read_text().strip(), "```"]
    return "\n".join(out)


def section_dataset(res):
    path = res / "datasets.jsonl"
    if not path.exists():
        return ""
    latest = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            latest[r["tenants"]] = r
    rows = [(t, f"{r['rows']:,}", fmt(r["total_bytes"] / 1e6, 0), r.get("bytes_per_row"), r.get("load_seconds"))
            for t, r in sorted(latest.items())]
    return "\n".join(["## Dataset", "", md_table(["Tenants", "Rows", "Size (MB)", "Bytes per row", "Load (s)"], rows)])


def query_data(res):
    rows = read_csv(res / "queries_summary.csv")
    for r in rows:
        r.setdefault("protocol", "simple")
        r["tenants"], r["clients"] = int(r["tenants"]), int(r["clients"])
    return rows


def section_queries(rows, title="Read latency (Tests 2 and 4)"):
    if not rows:
        return ""
    out = [f"## {title}", "",
           "Each cell is the median of the measured runs, with the min to max range in brackets. "
           "`x A` is the median p50 divided by variant A's median p50 at the same tenant count; "
           "A is the no-RLS baseline. Latencies are in milliseconds per transaction.", ""]
    by = defaultdict(dict)
    for r in rows:
        by[(r["protocol"], r["query"], r["clients"])][(r["tenants"], r["variant"])] = r
    tenants = sorted({r["tenants"] for r in rows})
    variants = sorted({r["variant"] for r in rows})
    for (proto, q, c) in sorted(by):
        d = by[(proto, q, c)]
        out += [f"### {q.upper()}, {c} client{'s' if c > 1 else ''}, {proto} protocol", ""]
        for metric, label in (("p50_ms", "p50"), ("p95_ms", "p95"), ("p99_ms", "p99")):
            header = ["Variant"] + [f"{t} tenants" for t in tenants]
            body = []
            for v in variants:
                cells = []
                for t in tenants:
                    r = d.get((t, v))
                    if not r:
                        cells.append("n/a")
                        continue
                    s = cell(r, metric)
                    if metric == "p50_ms":
                        base = num((d.get((t, "A")) or {}).get("p50_ms_median"))
                        rs = ratio_str(num(r["p50_ms_median"]), base)
                        s += f" {rs} A" if rs and v != "A" else ""
                    cells.append(s)
                body.append([VARIANT_NAMES.get(v, v)] + cells)
            out += [f"**{label}**", "", md_table(header, body), ""]
    return "\n".join(out)


def section_plans(res):
    path = res / "plans_summary.csv"
    rows = read_csv(path)
    if not rows:
        return ""
    latest = {}
    for r in rows:
        latest[(int(r["tenants"]), r["variant"], r["query"])] = r  # later tags overwrite earlier
    out = ["## Query plans (Test 4)", "",
           "One EXPLAIN (ANALYZE, BUFFERS) per variant, query and tenant count, taken in the first repetition. "
           "Full plan JSON is in `plans/`.", ""]
    body = []
    for (t, v, q), r in sorted(latest.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])):
        scans = r.get("telemetry_scans", "").replace(";", ", ")
        idx = r.get("indexes_used", "").replace(";", ", ").replace("_hyper_", "~")[:70]
        body.append([q.upper(), t, v, scans, r.get("chunks_scanned", ""), "yes" if r.get("telemetry_seq_scan") == "True" else "no",
                     fmt(num(r.get("execution_ms")), 2), idx])
    out.append(md_table(["Query", "Tenants", "Variant", "Scan nodes on telemetry", "Chunks scanned",
                         "Seq scan", "Exec ms", "Indexes"], body))
    return "\n".join(out)


def section_ingest(res):
    out = []
    rls = read_csv(res / "ingest_rls_summary.csv")
    if rls:
        out += ["## Write throughput with and without RLS (Test 3)", "",
                "INSERT only (COPY is not allowed on RLS tables). Median rows per second with the min to max range; "
                "`x A` is relative to variant A at the same tenant count and writer count.", ""]
        groups = defaultdict(dict)
        for r in rls:
            groups[(int(r["tenants"]), int(r["writers"]), int(r["batch_size"]))][r["variant"]] = r
        variants = sorted({r["variant"] for r in rls})
        body = []
        for (t, w, bs), d in sorted(groups.items()):
            base = num((d.get("A") or {}).get("rows_per_s_median"))
            row = [t, w, bs]
            for v in variants:
                r = d.get(v)
                if not r:
                    row.append("n/a")
                    continue
                s = cell(r, "rows_per_s", 0)
                rs = ratio_str(num(r["rows_per_s_median"]), base)
                row.append(s + (f" {rs} A" if rs and v != "A" else ""))
            body.append(row)
        out += [md_table(["Tenants", "Writers", "Batch"] + [VARIANT_NAMES.get(v, v).split("  ")[0] for v in variants], body), ""]
    base = read_csv(res / "ingest_baseline_summary.csv")
    if base:
        out += ["## Ingest baseline (Test 1)", "",
                "No RLS. Median rows per second (min to max) and batch p95 latency in milliseconds.", ""]
        body = [[r["method"], r["batch_size"], r["writers"], cell(r, "rows_per_s", 0), fmt(num(r.get("p95_ms_median")), 1)]
                for r in sorted(base, key=lambda r: (r["method"], int(r["batch_size"]), int(r["writers"])))]
        out.append(md_table(["Method", "Batch size", "Writers", "Rows/s", "Batch p95 ms"], body))
    return "\n".join(out)


def steal_summary(res_dirs):
    out = []
    for d in res_dirs:
        for f in sorted(Path(d).glob("raw/sysstat_*.log")):
            col, vals = None, []
            for i, line in enumerate(f.read_text().splitlines()):
                parts = line.split()
                if "st" in parts and col is None:
                    col = parts.index("st")
                    continue
                if col is not None and len(parts) > col and parts[col].isdigit():
                    vals.append(int(parts[col]))
            if vals:
                vals.sort()
                out.append((f"{Path(d).name}/{f.name}", len(vals), statistics.mean(vals), vals[int(0.99 * (len(vals) - 1))], vals[-1]))
    if not out:
        return ""
    body = [(n, c, f"{m:.2f}", p99, mx) for n, c, m, p99, mx in out]
    return "\n".join(["## CPU steal time", "",
                      "Percentage of time the VM's CPU was taken by the hypervisor (vmstat `st`, one sample per second). "
                      "Sustained values above a few percent mean other tenants affected the measurements.", "",
                      md_table(["Log", "Samples", "Mean %", "p99 %", "Max %"], body)])


# ---------- charts ----------

def charts(rows, res):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping charts (pip install matplotlib)")
        return []
    out_dir = res / "charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": GRID, "axes.labelcolor": INK2,
                         "xtick.color": INK2, "ytick.color": INK2, "text.color": INK})

    def line_figure(kind, clients, proto="simple"):
        sel = [r for r in rows if r["clients"] == clients and r["protocol"] == proto]
        queries = sorted({r["query"] for r in sel})
        tenants = sorted({r["tenants"] for r in sel})
        variants = sorted({r["variant"] for r in sel})
        if not sel or len(tenants) < 2:
            return None
        fig, axes = plt.subplots(1, len(queries), figsize=(4.6 * len(queries), 4.2), sharey=True, facecolor=SURFACE)
        axes = [axes] if len(queries) == 1 else list(axes)
        for ax, q in zip(axes, queries):
            ax.set_facecolor(SURFACE)
            d = {(r["tenants"], r["variant"]): num(r["p50_ms_median"]) for r in sel if r["query"] == q}
            ends = []  # (log10 y, variant) of line ends, used to skip labels that would collide
            for v in variants:
                xs, ys = [], []
                for t in tenants:
                    y = d.get((t, v))
                    base = d.get((t, "A"))
                    if y is None or (kind == "ratio" and not base):
                        continue
                    xs.append(t)
                    ys.append(y / base if kind == "ratio" else y)
                if xs:
                    ax.plot(xs, ys, color=COLORS.get(v, INK2), lw=2, marker="o", ms=6, mec=SURFACE, mew=1.5, label=v)
                    ly = math.log10(ys[-1])
                    if all(abs(ly - other) > 0.07 for other, _ in ends):  # direct label only when clear of others
                        ax.annotate(v, (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points", va="center",
                                    fontsize=9, color=INK2)
                    ends.append((ly, v))
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_xticks(tenants)
            ax.set_xticklabels([str(t) for t in tenants])
            ax.minorticks_off()
            if kind == "ratio":
                ax.axhline(1, color=INK2, lw=1, ls=(0, (4, 3)))
            ax.grid(axis="y", color=GRID, lw=0.8)
            ax.set_title(q.upper(), fontsize=11, loc="left", color=INK)
            ax.set_xlabel("Tenants")
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
        axes[0].set_ylabel("p50 latency relative to A (log scale)" if kind == "ratio" else "p50 latency, ms (log scale)")
        handles = [plt.Line2D([], [], color=COLORS.get(v, INK2), lw=2, marker="o", label=VARIANT_NAMES.get(v, v)) for v in variants]
        fig.legend(handles=handles, loc="lower center", ncol=min(len(variants), 3), frameon=False, fontsize=9)
        fig.suptitle(f"Read latency by RLS variant, {clients} client{'s' if clients > 1 else ''}", x=0.01, ha="left", fontsize=12)
        fig.tight_layout(rect=(0, 0.1, 1, 0.94))
        p = out_dir / f"latency_{kind}_{clients}c.png"
        fig.savefig(p, dpi=150, facecolor=SURFACE)
        plt.close(fig)
        return p

    for c in sorted({r["clients"] for r in rows}):
        for kind in ("ratio", "abs"):
            p = line_figure(kind, c)
            if p:
                made.append(p)

    ing = read_csv(res / "ingest_rls_summary.csv")
    if ing:
        tenants = sorted({int(r["tenants"]) for r in ing})
        variants = sorted({r["variant"] for r in ing})
        fig, ax = plt.subplots(figsize=(6.4, 4), facecolor=SURFACE)
        ax.set_facecolor(SURFACE)
        w = 0.8 / len(variants)
        for i, v in enumerate(variants):
            ys = []
            for t in tenants:
                vals = [num(r["rows_per_s_median"]) for r in ing if int(r["tenants"]) == t and r["variant"] == v]
                ys.append(vals[0] if vals else 0)
            xs = [j + (i - (len(variants) - 1) / 2) * w for j in range(len(tenants))]
            ax.bar(xs, ys, width=w * 0.9, color=COLORS.get(v, INK2), label=VARIANT_NAMES.get(v, v), edgecolor=SURFACE, linewidth=2)
        ax.set_xticks(range(len(tenants)))
        ax.set_xticklabels([str(t) for t in tenants])
        ax.set_xlabel("Tenants")
        ax.set_ylabel("Rows per second (median)")
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2)
        ax.set_title("INSERT throughput by RLS variant", loc="left", fontsize=12)
        fig.tight_layout()
        p = out_dir / "ingest_rls.png"
        fig.savefig(p, dpi=150, facecolor=SURFACE)
        plt.close(fig)
        made.append(p)
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", default="results/official")
    ap.add_argument("--prepared-dir", default=None, help="results from the --protocol prepared subset")
    ap.add_argument("--no-charts", action="store_true")
    a = ap.parse_args()
    res = Path(a.results_dir)
    if not res.exists():
        sys.exit(f"{res} not found")
    if (res / "plans").exists():
        subprocess.run([sys.executable, str(HERE / "summarize_plans.py"), "--results-dir", str(res)], check=False,
                       stdout=subprocess.DEVNULL)
    rows = query_data(res)
    prep = Path(a.prepared_dir) if a.prepared_dir else None
    parts = ["# Benchmark report (generated)", "",
             "Generated from the summary files in this directory by `bench/report.py`. Numbers only; the answer, "
             "recommendation and limitations are written separately.", "",
             section_environment(res), "", section_dataset(res), "", section_queries(rows), "",
             section_plans(res), "", section_ingest(res), ""]
    if prep and prep.exists():
        prows = query_data(prep)
        parts += [section_queries(prows, "Prepared-statement subset"), ""]
    parts += [steal_summary([res] + ([prep] if prep else [])), ""]
    imgs = [] if a.no_charts else charts(rows, res)
    if imgs:
        parts += ["## Charts", ""] + [f"![{p.stem}](charts/{p.name})\n" for p in imgs]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip() + "\n"
    (res / "report.md").write_text(text)
    print(f"wrote {res / 'report.md'}" + (f" and {len(imgs)} charts" if imgs else ""))


if __name__ == "__main__":
    main()
