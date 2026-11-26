"""Phase 4 end-to-end tests: callbacks (HostObject), asyncio.Future bridge, iterators on 3.15 & 3.15t."""

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


@unittest.skipUnless(REQUIRE_NATIVE or (NOXC is not None and UV is not None), "requires noxc >= 2.2.92 and uv")
class TestPhase4CallbacksAsync(unittest.TestCase):
    def test_callbacks_async_and_iterators_dual_mode(self) -> None:
        if NOXC is None or UV is None or PY315T is None:
            if REQUIRE_NATIVE:
                self.fail(f"NOXPY_REQUIRE_NATIVE=1 set, but missing toolchain (NOXC={NOXC}, UV={UV}, PY315T={PY315T})")
            self.skipTest("requires noxc >= 2.2.92, uv, and python3.15t")
        assert UV is not None and PY315T is not None

        with tempfile.TemporaryDirectory(prefix="noxpy-p4-cb-") as td:
            tmp = Path(td)
            proj = tmp / "callbacks_async_copy"
            shutil.copytree(ROOT / "examples" / "callbacks_async", proj)

            dist_dir = tmp / "dist"
            res = build_project_wheel(proj, dist_dir, release=True, use_cache=True)
            self.assertIn("apply_twice", res.exported_functions)
            self.assertIn("async_add", res.exported_functions)
            self.assertIn("AsyncWorker", res.exported_functions)
            self.assertIn("RangeIter", res.exported_functions)

            with zipfile.ZipFile(res.wheel_path, "r") as zf:
                pyi = zf.read("callbacks_async_demo.pyi").decode("utf-8")
                self.assertIn("class CallbackDemoError(ValueError):", pyi)
                self.assertIn("def apply_twice(cb: object, x: int) -> int:", pyi)
                self.assertIn("async def async_add(a: int, b: int, ms: int) -> int:", pyi)
                self.assertIn("async def later(self, delta: int, ms: int) -> int:", pyi)
                self.assertIn("def __iter__(self) -> RangeIter:", pyi)
                self.assertIn("def __next__(self) -> int:", pyi)

            smoke_code = """
import asyncio, concurrent.futures, gc, sys, time, weakref
import callbacks_async_demo as m

# 1. HostObject callbacks + reentrancy + weakref lifetime
called = []
def reentrant_cb(v):
    called.append(v)
    return m.double_val(v) + 1

assert m.apply_twice(reentrant_cb, 3) == 15  # 3 -> 7 -> 15
assert called == [3, 7]

class SecretBoom(Exception):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code

def boom():
    raise SecretBoom("py failure", 77)

assert m.try_call(boom) == "caught: py failure"

try:
    m.call_uncaught(boom)
    raise AssertionError("expected SecretBoom")
except SecretBoom as exc:
    assert exc.code == 77
    assert "py failure" in str(exc)

# Passthrough identity & refcount release
class Sentinel:
    pass
sentinel = Sentinel()
wr = weakref.ref(sentinel)
returned = m.passthrough(sentinel)
assert returned is sentinel
del sentinel, returned
gc.collect()
assert wr() is None

assert m.call_all([lambda v: v + 1, lambda v: v * 10], 5) == [6, 50]
assert m.maybe_call() == -1
assert m.maybe_call(None) == -1
assert m.maybe_call(lambda: 99) == 99

# 2. Async/await, concurrency, HostObject lifetime in worker, error, cancel, deadlock containment
async def run_async_tests():
    t0 = time.monotonic()
    results = await asyncio.gather(
        m.async_add(20, 22, 25),
        m.async_add(1, 2, 25),
        m.async_add(100, 200, 25),
        m.async_greet("NoxPy", 25),
    )
    elapsed = time.monotonic() - t0
    assert results == [42, 3, 300, "Hello, NoxPy!"]
    assert elapsed < 0.25, f"expected parallel contexts, took {elapsed:.3f}s"

    assert await m.async_nothing(5) is None
    assert await m.async_with_cb(lambda x: x * 3, 14, 15) == 42

    try:
        await m.async_boom(5)
        raise AssertionError("expected CallbackDemoError")
    except m.CallbackDemoError as exc:
        assert "async boom" in str(exc)

    # Cooperative cancellation (direct Future.cancel() + asyncio.wait_for timeout cancel)
    slow_fut = m.async_slow(5000)
    await asyncio.sleep(0.02)
    slow_fut.cancel()
    try:
        await slow_fut
        raise AssertionError("expected CancelledError")
    except asyncio.CancelledError:
        pass

    try:
        await asyncio.wait_for(m.async_slow(5000), timeout=0.02)
        raise AssertionError("expected TimeoutError")
    except TimeoutError:
        pass

    # Async deadlock containment
    try:
        await m.async_stuck()
        raise AssertionError("expected RuntimeError on deadlock")
    except RuntimeError as exc:
        assert "deadlock" in str(exc).lower()

    # Instance async method + top-level async class handle return + deferred close safety
    w = await m.make_worker_async(10, 5)
    assert isinstance(w, m.AsyncWorker)
    assert await w.later(32, 10) == 42

    w2 = m.AsyncWorker(100)
    pending = w2.later(5, 30)
    await asyncio.sleep(0.005)
    w2.close()
    assert await pending == 105

asyncio.run(run_async_tests())

# 3. Iterators: StopIteration vs domain error & shared context factory
assert list(m.RangeIter(2, 6)) == [2, 3, 4, 5]
rs = m.RangeSeq(10, 14)
assert list(rs) == [10, 11, 12, 13]
assert list(rs) == [10, 11, 12, 13]

fi = m.FailingIter()
assert next(fi) == 99
try:
    next(fi)
    raise AssertionError("expected CallbackDemoError from FailingIter")
except m.CallbackDemoError as exc:
    assert "iter boom" in str(exc)

# 4. Concurrent threads hitting callbacks & iterators on 3.15t without GIL
def worker_fn(idx):
    res = m.apply_twice(lambda v: v + idx, 1)
    assert res == 1 + 2 * idx
    assert list(m.RangeIter(0, 5)) == [0, 1, 2, 3, 4]
    return res

with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    outs = list(pool.map(worker_fn, range(24)))
assert outs == [1 + 2 * i for i in range(24)]
"""

            for py_label, py_exe in (("3.15", sys.executable), ("3.15t", PY315T)):
                venv_dir = tmp / f"venv-{py_label}"
                clean_env = {k: v for k, v in os.environ.items() if k != "PATH"}
                clean_env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
                subprocess.run([UV, "venv", "--python", py_exe, str(venv_dir)], check=True)
                vpy = str(venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
                subprocess.run([UV, "pip", "install", "--python", vpy, str(res.wheel_path)], check=True)

                gil_probe = subprocess.run(
                    [
                        vpy,
                        "-c",
                        (
                            "import sys, callbacks_async_demo; "
                            "print(getattr(sys, '_is_gil_enabled', lambda: True)())"
                        ),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=clean_env,
                )
                expected_gil = "True" if py_label == "3.15" else "False"
                self.assertEqual(gil_probe.stdout.strip(), expected_gil, f"{py_label} GIL check failed")

                run = subprocess.run(
                    [vpy, "-c", smoke_code],
                    capture_output=True,
                    text=True,
                    env=clean_env,
                )
                self.assertEqual(run.returncode, 0, f"{py_label} smoke failed:\nSTDOUT:\n{run.stdout}\nSTDERR:\n{run.stderr}")


if __name__ == "__main__":
    unittest.main()
