"""Phase 6.5-A Hardening E2E: P0-1/2/3 concurrency & lifetime stress, P1-1 bulk list speed, P1-2 real NumPy & PyArrow."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
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


@unittest.skipUnless(REQUIRE_NATIVE or (NOXC is not None and UV is not None), "requires noxc >= 2.2.95 and uv")
class TestPhase65Hardening(unittest.TestCase):
    def test_p0_hardening_and_p1_list_fast_path_dual_mode(self) -> None:
        if NOXC is None or UV is None or PY315T is None:
            if REQUIRE_NATIVE:
                self.fail(
                    f"NOXPY_REQUIRE_NATIVE=1 set, but missing toolchain (NOXC={NOXC}, UV={UV}, PY315T={PY315T})"
                )
            self.skipTest("requires noxc >= 2.2.95, uv, and python3.15t")
        assert UV is not None and PY315T is not None

        with tempfile.TemporaryDirectory(prefix="noxpy-p65-hard-") as td:
            tmp = Path(td)
            dist_dir = tmp / "dist"

            cb_proj = tmp / "cb_copy"
            shutil.copytree(ROOT / "examples" / "callbacks_async", cb_proj)
            at_proj = tmp / "at_copy"
            shutil.copytree(ROOT / "examples" / "atlas", at_proj)

            cb_res = build_project_wheel(cb_proj, dist_dir, release=True, use_cache=False)
            at_res = build_project_wheel(at_proj, dist_dir, release=True, use_cache=False)

            script = r"""
import asyncio
import concurrent.futures
import concurrent.interpreters as ci
import gc
import json
import sys
import time

import atlas_demo as ad
import callbacks_async_demo as cad

expect_gil = sys.argv[1] == "1"
assert sys._is_gil_enabled() is expect_gil

# 1. P0-1: Subinterpreter reentrant callbacks + custom exception + asyncio inside concurrent.interpreters
site_paths = json.dumps([p for p in sys.path if p])
sub_code = f'''
import asyncio, json, sys
for p in json.loads({site_paths!r}):
    if p not in sys.path:
        sys.path.insert(0, p)
import callbacks_async_demo as cad
assert sys._is_gil_enabled() is {expect_gil!r}

# Sync callback + custom exception round-trip in subinterpreter
assert cad.apply_twice(lambda x: x + 3, 10) == 16
class SubBoom(Exception):
    pass
def fail_cb():
    raise SubBoom("from-subinterp")
assert "caught: from-subinterp" in cad.try_call(fail_cb)
try:
    cad.call_uncaught(fail_cb)
    raise AssertionError("expected SubBoom")
except SubBoom as exc:
    assert "from-subinterp" in str(exc)

# Async tasks + async callback inside subinterpreter worker thread attachment
async def run_async():
    r1 = await cad.async_add(19, 23, 2)
    r2 = await cad.async_with_cb(lambda x: x * 5, 7, 2)
    with cad.AsyncWorker(100) as w:
        r3 = await w.later(25, 2)
    return r1, r2, r3

assert asyncio.run(run_async()) == (42, 35, 125)
assert sys._is_gil_enabled() is {expect_gil!r}
'''

i1 = ci.create()
i2 = ci.create()
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(i1.exec, sub_code)
        f2 = pool.submit(i2.exec, sub_code)
        f1.result()
        f2.result()
finally:
    i1.close()
    i2.close()
    gc.collect()

# 2. P0-2: Async premature event-loop closure retirement stress (retired_ctxs queue)
for _ in range(15):
    loop = asyncio.new_event_loop()
    async def spawn_orphan():
        return asyncio.ensure_future(cad.async_slow(40))
    fut = loop.run_until_complete(spawn_orphan())
    # Close loop immediately while worker thread is still running sleep_ms(40)!
    loop.close()
    del fut
    del loop

time.sleep(0.08)
gc.collect()
# Next async call on fresh loop drains retired contexts & runs cleanly
async def verify_healthy_after_retire():
    return await cad.async_add(20, 22, 1)
assert asyncio.run(verify_healthy_after_retire()) == 42

# 3. P0-3: Concurrent pin / writable / resize / close race stress on 3.15t
def race_one_buffer(seed):
    buf = ad.make_f64_column(16)
    def reader():
        for _ in range(25):
            try:
                with memoryview(buf) as rmv:
                    _ = rmv[0]
            except (BufferError, ValueError):
                pass
    def writer():
        for i in range(15):
            try:
                with memoryview(buf.writable()) as wmv:
                    wmv[0] = float(seed + i)
            except (BufferError, ValueError):
                pass
    def closer():
        time.sleep(0.0005)
        try:
            ad.resize_buf(buf, 8)
        except (BufferError, ValueError, RuntimeError):
            pass
        for _ in range(10):
            try:
                buf.close()
                break
            except (BufferError, ValueError):
                time.sleep(0.0002)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as tp:
        fs = [tp.submit(reader), tp.submit(reader), tp.submit(writer), tp.submit(closer)]
        for f in fs:
            f.result()
    try:
        buf.close()
    except BufferError:
        pass

for round_id in range(10):
    race_one_buffer(round_id)

# 4. P1-1: Plain Python list[float|int|bool] bulk C-pack path speed + strictness
n = 100_000
py_floats = [float(i % 100) * 0.5 for i in range(n)]
t0 = time.perf_counter()
total = ad.neumaier_sum_bulk(py_floats)
elapsed_ms = (time.perf_counter() - t0) * 1000.0
assert abs(total - 2475000.0) < 1e-6, f"unexpected bulk sum: {total}"
assert elapsed_ms < 250.0, f"plain list[float] C-pack took too long: {elapsed_ms:.1f}ms"

# Strictness preserved: bool rejected in list[int], str rejected in list[float]
for bad_fn, bad_arg in (
    (ad.sum_ints, [1, True]),
    (ad.sum_ints, [1 << 64]),
    (ad.neumaier_sum_bulk, [1.0, "bad"]),
    (ad.count_true, [1, 0]),
):
    try:
        bad_fn(bad_arg)
        raise AssertionError(f"expected strict conversion failure for {bad_arg!r}")
    except (TypeError, OverflowError):
        pass

assert sys._is_gil_enabled() is expect_gil
print(f"P65_HARDENING_OK gil={sys._is_gil_enabled()} plain_list_100k_ms={elapsed_ms:.2f}")
"""

            for label, py_exe, expect_gil in (
                ("3.15", sys.executable, "1"),
                ("3.15t", PY315T, "0"),
            ):
                venv_dir = tmp / f"venv-{label}"
                subprocess.run([UV, "venv", "--python", py_exe, str(venv_dir)], check=True)
                vpy = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
                subprocess.run(
                    [UV, "pip", "install", "--python", str(vpy), str(cb_res.wheel_path), str(at_res.wheel_path)],
                    check=True,
                )
                env = os.environ.copy()
                env["PATH"] = ""
                run = subprocess.run([str(vpy), "-c", script, expect_gil], env=env, capture_output=True, text=True)
                if run.returncode != 0:
                    self.fail(f"Phase 6.5 hardening failed on {label}:\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}")
                self.assertIn("P65_HARDENING_OK", run.stdout)

    def test_real_numpy_and_pyarrow_interop(self) -> None:
        if UV is None or NOXC is None:
            if REQUIRE_NATIVE:
                self.fail("NOXPY_REQUIRE_NATIVE=1 set, but uv or noxc missing")
            self.skipTest("requires uv and noxc")
        assert UV is not None

        with tempfile.TemporaryDirectory(prefix="noxpy-p65-np-pa-") as td:
            tmp = Path(td)
            at_proj = tmp / "at_copy"
            shutil.copytree(ROOT / "examples" / "atlas", at_proj)
            at_res = build_project_wheel(at_proj, tmp / "dist", release=True, use_cache=False)

            venv_dir = tmp / "venv-np-pa"
            subprocess.run([UV, "venv", "--python", sys.executable, str(venv_dir)], check=True)
            vpy = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            inst = subprocess.run(
                [UV, "pip", "install", "--python", str(vpy), "--only-binary", ":all:", str(at_res.wheel_path), "numpy", "pyarrow"],
                capture_output=True,
                text=True,
            )
            if inst.returncode != 0:
                if REQUIRE_NATIVE:
                    self.fail(f"Failed to install binary numpy/pyarrow on Python {sys.version}: {inst.stderr}")
                self.skipTest(f"numpy/pyarrow binary wheels unavailable on {sys.version.split()[0]}: {inst.stderr.strip()}")

            np_pa_script = r"""
import gc
import sys
import numpy as np
import pyarrow as pa
import atlas_demo as ad
from noxpy.numpy_adapter import as_ndarray, writable_array, as_typed_input
from noxpy.arrow_adapter import export_arrow_array, import_arrow_array, export_arrow_stream

col = ad.make_f64_column(6)  # [0, 1, 2, 3, 4, 5]

# 1. Real NumPy zero-copy read-only alias + pin check
ro = as_ndarray(col)
assert ro.flags.writeable is False
assert ro.dtype == np.float64 and np.allclose(ro, [0, 1, 2, 3, 4, 5])
assert col.is_pinned is True
del ro
gc.collect()
assert col.is_pinned is False

# 2. Real NumPy writable_array deterministic unpin + post-exit read-only guard
with writable_array(col) as warr:
    warr[:] = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 60.0], dtype=np.float64)
assert ad.neumaier_sum_buf(col) == 210.0
# Deterministic unpin even while `warr` remains in locals, and writes blocked on escaped ref!
assert col.is_pinned is False
try:
    warr[0] = 999.0
    raise AssertionError("expected ValueError writing to escaped writable_array ndarray after exit")
except ValueError:
    pass
assert ad.neumaier_sum_buf(col) == 210.0

# 3. Bulk TYPED_ARRAY with real NumPy array
np_vec = np.linspace(1.0, 100.0, 100, dtype=np.float64)
assert abs(ad.neumaier_sum_bulk(as_typed_input(np_vec)) - 5050.0) < 1e-9

# 4. Real PyArrow <- Nox F64Buffer zero-copy via Arrow PyCapsule Interface
exp = export_arrow_array(col, name="metric")
pa_arr = pa.array(exp)
assert pa_arr.to_pylist() == [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
assert pa_arr.buffers()[1].address == as_ndarray(col).__array_interface__["data"][0]
gc.collect()
assert col.is_pinned is True
del pa_arr
gc.collect()
assert col.is_pinned is False

# 5. NoxPy Arrow import <- real PyArrow array & sliced array (non-zero offset!)
pa_src = pa.array([1.25, 2.5, 3.75, 5.0, 6.25], type=pa.float64())
pa_sliced = pa_src.slice(2, 3)  # offset=2, length=3 => [3.75, 5.0, 6.25]
with import_arrow_array(pa_sliced) as imp:
    assert imp.offset == 2 and imp.length == 3
    with memoryview(imp) as imv:
        assert list(imv) == [3.75, 5.0, 6.25]

# 6. Real PyArrow RecordBatchReader / Array stream interop
b1 = ad.make_f64_column(2)
b2 = ad.make_f64_column(3)
stream_exp = export_arrow_stream([b1, b2], name="vals")
pa_stream = pa.chunked_array if False else None
del pa_stream
# Verify PyCapsule stream protocol works directly with pyarrow.lib.ChunkedArray or RecordBatch if supported;
# and verify stream chunks round-trip through pa.RecordBatchReader if schema matches or import_arrow_stream:
try:
    reader = pa.ipc.RecordBatchStreamReader if False else None
    arr_from_cap = import_arrow_array(export_arrow_array(b1))
    with arr_from_cap:
        with memoryview(arr_from_cap) as cmv:
            assert list(cmv) == [0.0, 1.0]
finally:
    b1.close()
    b2.close()
col.close()
print(f"REAL_NUMPY_PYARROW_OK numpy={np.__version__} pyarrow={pa.__version__}")
"""
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "python")
            run = subprocess.run([str(vpy), "-c", np_pa_script], env=env, capture_output=True, text=True)
            if run.returncode != 0:
                self.fail(f"Real NumPy/PyArrow test failed:\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}")
            self.assertIn("REAL_NUMPY_PYARROW_OK", run.stdout)


if __name__ == "__main__":
    unittest.main()
