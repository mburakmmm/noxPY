#!/usr/bin/env python3
"""Unforced free-threaded CPython 3.15t acceptance test (AGENTS.md §17).

Must execute `_noxpy_poc` in a clean subprocess WITHOUT `-X gil=0`, verifying:
  1. Interpreter starts with GIL disabled (`assert not sys._is_gil_enabled()`).
  2. Importing `_noxpy_poc` does NOT re-enable the GIL.
  3. Native Nox `add(20, 22) == 42` and multi-thread barrier overlap succeed.

Exits with code 2 (SKIPPED) if no free-threaded `python3.15t` executable exists locally.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def find_python_315t() -> str | None:
    candidates = [
        os.environ.get("NOXPY_PYTHON_315T", ""),
        shutil.which("python3.15t") or "",
        "/Library/Frameworks/PythonT.framework/Versions/3.15/bin/python3.15t",
        "/opt/homebrew/bin/python3.15t",
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def main() -> int:
    py315t = find_python_315t()
    if not py315t:
        print("SKIPPED: free-threaded CPython 3.15t (`python3.15t`) is not installed locally.")
        return 2

    test_code = """
import sys, threading
assert not sys._is_gil_enabled(), "test did not start in free-threaded mode"
import _noxpy_poc, _noxpy_poc_b
assert not sys._is_gil_enabled(), "extension re-enabled the GIL"
assert _noxpy_poc.add(20, 22) == 42
assert _noxpy_poc_b.mul(6, 7) == 42

_noxpy_poc.reset_peak_in_flight()
b = threading.Barrier(4)
errs = []
def worker(tid):
    try:
        b.wait(timeout=5.0)
        for _ in range(10):
            assert _noxpy_poc.add(tid, 22) == tid + 22
            _ = _noxpy_poc.work_loop(250_000, tid)
    except BaseException as exc:
        errs.append(exc)

ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
for t in ts: t.start()
for t in ts: t.join(timeout=30.0)
assert not errs, errs
assert _noxpy_poc.peak_in_flight() >= 2, f"expected peak >= 2, got {_noxpy_poc.peak_in_flight()}"
assert not sys._is_gil_enabled(), "GIL became enabled during concurrent calls"
print(f"3.15t PASS: peak_in_flight={_noxpy_poc.peak_in_flight()} gil_enabled={sys._is_gil_enabled()}")
"""
    res = subprocess.run([py315t, "-c", test_code], capture_output=True, text=True)
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    return res.returncode


if __name__ == "__main__":
    raise SystemExit(main())
