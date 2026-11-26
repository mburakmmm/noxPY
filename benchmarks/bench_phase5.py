#!/usr/bin/env python3
"""Phase 5 Benchmark Harness (`AGENTS.md` §15, §20): Pure Python vs list vs TYPED_ARRAY vs zero-copy Buffer & 3.15t scaling."""

from __future__ import annotations

import array
import concurrent.futures
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.build import build_project_wheel  # noqa: E402


def _measure(fn, *, warmups: int = 3, iters: int = 20) -> dict[str, float]:
    for _ in range(warmups):
        fn()
    samples_ms: list[float] = []
    for _ in range(iters):
        t0 = time.perf_counter_ns()
        fn()
        samples_ms.append((time.perf_counter_ns() - t0) / 1e6)
    samples_ms.sort()
    q1 = statistics.quantiles(samples_ms, n=4)[0] if len(samples_ms) >= 4 else samples_ms[0]
    q3 = statistics.quantiles(samples_ms, n=4)[2] if len(samples_ms) >= 4 else samples_ms[-1]
    p95 = samples_ms[min(len(samples_ms) - 1, math.ceil(0.95 * len(samples_ms)) - 1)]
    p99 = samples_ms[min(len(samples_ms) - 1, math.ceil(0.99 * len(samples_ms)) - 1)]
    return {
        "median_ms": round(statistics.median(samples_ms), 3),
        "iqr_ms": round(q3 - q1, 3),
        "p95_ms": round(p95, 3),
        "p99_ms": round(p99, 3),
        "min_ms": round(samples_ms[0], 3),
    }


def _pure_py_neumaier(xs: list[float]) -> float:
    s = 0.0
    c = 0.0
    for x in xs:
        t = s + x
        if abs(s) >= abs(x):
            c += (s - t) + x
        else:
            c += (x - t) + s
        s = t
    return s + c


def main() -> int:
    uv = shutil.which("uv")
    py315t = shutil.which("python3.15t") or str(Path.home() / ".local" / "bin" / "python3.15t")
    if not uv:
        raise RuntimeError("uv required")

    with tempfile.TemporaryDirectory(prefix="noxpy-bench5-") as td:
        tmp = Path(td)
        proj = tmp / "atlas"
        shutil.copytree(ROOT / "examples" / "atlas", proj)
        res = build_project_wheel(proj, tmp / "dist", release=True, use_cache=True)

        runner = """
import array, concurrent.futures, json, math, statistics, sys, time
import atlas_demo as ad

N = 200_000
py_list = [float(i % 1000) * 0.25 for i in range(N)]
c_arr = array.array("d", py_list)
nox_buf = ad.make_f64_column(N)
with memoryview(nox_buf.writable()) as wmv:
    wmv[:] = c_arr

def pure_py_neumaier(xs):
    s = 0.0
    c = 0.0
    for x in xs:
        t = s + x
        if abs(s) >= abs(x):
            c += (s - t) + x
        else:
            c += (x - t) + s
        s = t
    return s + c

def bench(fn, warmups=3, iters=15):
    for _ in range(warmups):
        fn()
    xs = []
    for _ in range(iters):
        t0 = time.perf_counter_ns()
        fn()
        xs.append((time.perf_counter_ns() - t0) / 1e6)
    xs.sort()
    qs = statistics.quantiles(xs, n=4)
    return {
        "median_ms": round(statistics.median(xs), 3),
        "iqr_ms": round(qs[2] - qs[0], 3),
        "p95_ms": round(xs[min(len(xs)-1, math.ceil(0.95*len(xs))-1)], 3),
        "p99_ms": round(xs[-1], 3),
    }

out = {
    "python_version": sys.version.split()[0],
    "gil_enabled": getattr(sys, "_is_gil_enabled", lambda: True)(),
    "n_elements": N,
    "pure_python_neumaier": bench(lambda: pure_py_neumaier(py_list)),
    "nox_boxed_list_float": bench(lambda: ad.neumaier_sum_bulk(py_list)),
    "nox_bulk_typed_array": bench(lambda: ad.neumaier_sum_bulk(c_arr)),
    "nox_zero_copy_buf_sum": bench(lambda: ad.neumaier_sum_buf(nox_buf)),
    "nox_zero_copy_welford": bench(lambda: ad.welford_variance_buf(nox_buf, 1)),
}

# Multi-thread scaling (1/2/4/8 threads, total 8 tasks of 200k each over TYPED_ARRAY)
scaling = {}
for threads in (1, 2, 4, 8):
    def run_mt():
        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
            futs = [pool.submit(ad.neumaier_sum_bulk, c_arr) for _ in range(8)]
            for f in futs:
                f.result()
    scaling[str(threads)] = bench(run_mt, warmups=1, iters=6)
base_1t = scaling["1"]["median_ms"]
for k, v in scaling.items():
    v["speedup_vs_1t"] = round(base_1t / max(v["median_ms"], 1e-6), 2)
out["scaling_8x200k_typed_array"] = scaling
nox_buf.close()
print(json.dumps(out, indent=2))
"""

        all_results = {}
        for label, exe in (("3.15", sys.executable), ("3.15t", py315t)):
            if not Path(exe).is_file():
                continue
            vdir = tmp / f"venv-{label}"
            subprocess.run([uv, "venv", "--python", exe, str(vdir)], check=True, capture_output=True)
            vpy = str(vdir / "bin" / "python")
            subprocess.run([uv, "pip", "install", "--python", vpy, str(res.wheel_path)], check=True, capture_output=True)
            run = subprocess.run([vpy, "-c", runner], capture_output=True, text=True, check=True)
            all_results[label] = json.loads(run.stdout)

        print(json.dumps(all_results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
