"""Phase 3 end-to-end tests: automatic @export class/property/exception codegen, wheels, GC & 3.15t."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.build import build_project_wheel  # noqa: E402


def _find_noxc() -> str | None:
    on_path = shutil.which("noxc")
    if on_path:
        return on_path
    home_bin = Path.home() / ".nox-lang" / "bin" / "noxc"
    if home_bin.is_file():
        return str(home_bin)
    return None


def _find_315t() -> str | None:
    candidates = [
        os.environ.get("NOXPY_PYTHON_315T", ""),
        shutil.which("python3.15t") or "",
        str(Path.home() / ".local" / "bin" / "python3.15t"),
        "/Library/Frameworks/PythonT.framework/Versions/3.15/bin/python3.15t",
    ]
    uv = shutil.which("uv")
    if uv:
        probe = subprocess.run([uv, "python", "find", "3.15t"], capture_output=True, text=True)
        if probe.returncode == 0 and probe.stdout.strip():
            candidates.append(probe.stdout.strip())
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


NOXC = _find_noxc()
UV = shutil.which("uv")
PY315T = _find_315t()
REQUIRE_NATIVE = os.environ.get("NOXPY_REQUIRE_NATIVE") == "1"


@unittest.skipUnless(REQUIRE_NATIVE or (NOXC is not None and UV is not None), "requires noxc >= 2.2.90 and uv")
class TestPhase3Classes(unittest.TestCase):
    def test_classes_and_parallel_wheels_dual_mode(self) -> None:
        if NOXC is None or UV is None or PY315T is None:
            if REQUIRE_NATIVE:
                self.fail(f"NOXPY_REQUIRE_NATIVE=1 set, but missing toolchain (NOXC={NOXC}, UV={UV}, PY315T={PY315T})")
            self.skipTest("requires noxc >= 2.2.90, uv, and python3.15t")
        assert UV is not None and PY315T is not None

        with tempfile.TemporaryDirectory(prefix="noxpy-p3-cls-") as td:
            tmp = Path(td)
            cls_proj = tmp / "classes_copy"
            par_proj = tmp / "parallel_copy"
            shutil.copytree(ROOT / "examples" / "classes", cls_proj)
            shutil.copytree(ROOT / "examples" / "parallel", par_proj)

            dist_dir = tmp / "dist"
            cls_res = build_project_wheel(cls_proj, dist_dir, release=True, use_cache=True)
            par_res = build_project_wheel(par_proj, dist_dir, release=True, use_cache=True)

            self.assertIn("Counter", cls_res.exported_functions)
            self.assertIn("CounterError", cls_res.exported_functions)
            self.assertIn("Worker", par_res.exported_functions)

            with zipfile.ZipFile(cls_res.wheel_path, "r") as zf:
                pyi = zf.read("classes_demo.pyi").decode("utf-8")
                self.assertIn("class CounterError(ValueError):", pyi)
                self.assertIn("class Counter:", pyi)
                self.assertIn("@Counter.value.setter" if False else "@value.setter", pyi)
                self.assertIn("def inc(self, delta: int = 1) -> int:", pyi)
                self.assertIn("def make_counter(start: int = 0) -> Counter:", pyi)

            smoke_code = """
import concurrent.futures, gc, inspect, sys, threading
import classes_demo as cd
import parallel_demo as pd

# Custom exception hierarchy
assert issubclass(cd.CounterError, ValueError)
try:
    cd.Counter(-1)
    raise AssertionError("expected CounterError")
except cd.CounterError as e:
    assert "non-negative" in str(e)
except ValueError:
    raise AssertionError("expected CounterError subclass before ValueError catch")

try:
    cd.Counter(-2)
    raise AssertionError("expected ValueError")
except ValueError:
    pass

# Constructor defaults, kwargs, property getter/setter, staticmethod, classmethod, top-level handle factory
c = cd.Counter()
assert c.value == 0
assert c.inc() == 1
assert c.inc(delta=4) == 5
assert repr(c) == "Counter(5)"
assert str(c) == "count=5"
assert len(c) == 5

c.value = 12
assert c.value == 12
try:
    c.value = -5
    raise AssertionError("expected CounterError on negative property setter")
except cd.CounterError:
    pass
assert c.value == 12

assert cd.Counter.zero() == 0
c2 = cd.Counter.make(9)
assert repr(c2) == "Counter(9)"
c3 = cd.make_counter(start=7)
assert c3.value == 7 and len(c3) == 7
assert "delta=1" in str(inspect.signature(c.inc)) or "delta: int = 1" in str(inspect.signature(c.inc)), str(inspect.signature(c.inc))

# Immutable heap type & unhashable
try:
    hash(c)
    raise AssertionError("expected TypeError")
except TypeError:
    pass
try:
    class Sub(cd.Counter):
        pass
    raise AssertionError("expected TypeError on immutable heap type")
except TypeError:
    pass

# Idempotent close & context manager & GC cycle cleanup
c.close()
c.close()
assert repr(c) == "<Counter (closed)>"
try:
    c.inc(1)
    raise AssertionError("expected ValueError on closed object")
except ValueError:
    pass

with cd.Counter(3) as cm:
    assert cm.inc(2) == 5
try:
    _ = cm.value
    raise AssertionError("expected ValueError after with exit")
except ValueError:
    pass

c2.close()
c3.close()
cyc = cd.Counter(4)
lst = [cyc]
lst.append(lst)
del cyc, lst
gc.collect()

# thread_affine class enforcement
box = cd.AffineBox(11)
assert box.get() == 11
box.set_val(17)
assert box.get() == 17
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    try:
        pool.submit(box.get).result()
        raise AssertionError("expected RuntimeError on foreign thread")
    except RuntimeError as exc:
        assert "thread_affine" in str(exc)
box.close()

# parallel_demo: same-instance instance_locked contention (8x250 == 2000)
acc = pd.Accumulator(0)
b1 = threading.Barrier(8)
def acc_worker():
    b1.wait(timeout=10.0)
    for _ in range(250):
        acc.add(1)

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    for f in [pool.submit(acc_worker) for _ in range(8)]:
        f.result()
assert acc.total == 2000, f"lost update: {acc.total}"
acc.close()

# parallel_demo: 8 independent parallel Workers + container method
b2 = threading.Barrier(8)
def par_worker(idx: int) -> int:
    w = pd.Worker(seed=idx)
    b2.wait(timeout=10.0)
    r1 = w.compute(500000)
    r2 = w.sum_vec([1, 2, 3, 4])
    w.close()
    assert r2 == idx + 10
    return r1

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    outs = [f.result() for f in [pool.submit(par_worker, i) for i in range(8)]]
assert len(outs) == 8

print("PHASE3_PKG_OK", sys.version.split()[0], sys._is_gil_enabled())
"""
            empty_cwd = tmp / "consumer"
            empty_cwd.mkdir()
            clean_env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp)}

            for label, py_base, expect_gil in (("gil", sys.executable, True), ("ft", PY315T, False)):
                venv_p = tmp / f"venv_{label}"
                subprocess.run([UV, "venv", "--python", py_base, str(venv_p)], check=True, capture_output=True, text=True)
                v_py = venv_p / "bin" / "python"
                subprocess.run(
                    [UV, "pip", "install", "--python", str(v_py), "--no-deps", str(cls_res.wheel_path), str(par_res.wheel_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                prog = (
                    "import sys\n"
                    f"assert sys._is_gil_enabled() is {expect_gil}\n"
                    + smoke_code
                    + f"assert sys._is_gil_enabled() is {expect_gil}\n"
                )
                run_r = subprocess.run([str(v_py), "-c", prog], cwd=empty_cwd, env=clean_env, capture_output=True, text=True)
                self.assertEqual(run_r.returncode, 0, f"{label} failed:\n{run_r.stdout}\n{run_r.stderr}")
                self.assertEqual(run_r.stderr.strip(), "", f"unexpected stderr on {label}: {run_r.stderr}")


if __name__ == "__main__":
    unittest.main()
