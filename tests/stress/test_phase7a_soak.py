"""Phase 7A Stress & Soak: subinterpreter shutdown with active workers + 8-thread dual-mode soak."""

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


@unittest.skipUnless(REQUIRE_NATIVE or (NOXC is not None and UV is not None), "requires noxc and uv")
class TestPhase7aSoak(unittest.TestCase):
    def test_subinterpreter_shutdown_and_8_thread_soak_dual_mode(self) -> None:
        if NOXC is None or UV is None or PY315T is None:
            if REQUIRE_NATIVE:
                self.fail(f"NOXPY_REQUIRE_NATIVE=1 set, but missing toolchain (NOXC={NOXC}, UV={UV}, PY315T={PY315T})")
            self.skipTest("requires noxc, uv, and python3.15t")
        assert UV is not None and PY315T is not None

        with tempfile.TemporaryDirectory(prefix="noxpy-p7a-soak-") as td:
            tmp = Path(td)
            dist_dir = tmp / "dist"
            cb_proj = tmp / "cb_copy"
            at_proj = tmp / "at_copy"
            shutil.copytree(ROOT / "examples" / "callbacks_async", cb_proj)
            shutil.copytree(ROOT / "examples" / "atlas", at_proj)
            cb_res = build_project_wheel(cb_proj, dist_dir, release=True, use_cache=False)
            at_res = build_project_wheel(at_proj, dist_dir, release=True, use_cache=False)

            soak_script = r"""
import asyncio
import concurrent.futures
import concurrent.interpreters as ci
import gc
import json
import sys
import threading
import time

import atlas_demo as ad
import callbacks_async_demo as cad

expect_gil = sys.argv[1] == "1"
assert sys._is_gil_enabled() is expect_gil

# 1. Subinterpreter shutdown while native async workers are still in-flight
site_paths = json.dumps([p for p in sys.path if p])
sub_shutdown_code = f'''
import asyncio, json, sys
for p in json.loads({site_paths!r}):
    if p not in sys.path:
        sys.path.insert(0, p)
import callbacks_async_demo as cad

async def launch_and_drop():
    loop = asyncio.get_running_loop()
    # Start slow worker (60ms) and close immediately without awaiting
    _ = asyncio.ensure_future(cad.async_slow(60))
    await asyncio.sleep(0.002)

asyncio.run(launch_and_drop())
'''

for _ in range(4):
    interp = ci.create()
    interp.exec(sub_shutdown_code)
    interp.close()
    gc.collect()

time.sleep(0.08)

# Fresh subinterpreter still executes sync + async + callbacks cleanly after prior shutdowns
fresh = ci.create()
try:
    fresh.exec(f'''
import asyncio, json, sys
for p in json.loads({site_paths!r}):
    if p not in sys.path:
        sys.path.insert(0, p)
import callbacks_async_demo as cad
assert cad.apply_twice(lambda x: x + 1, 20) == 22
async def _fresh_async():
    return await cad.async_add(20, 22, 2)
assert asyncio.run(_fresh_async()) == 42
''')
finally:
    fresh.close()

# 2. 8-thread bounded soak (100 iterations per worker) mixing buffers, bulk C-pack, callbacks, and asyncio
errors = []
def worker(tid: int) -> None:
    try:
        vals = [float((tid + i) % 32) for i in range(64)]
        for it in range(100):
            s = ad.neumaier_sum_bulk(vals)
            assert s > 0.0
            buf = ad.make_f64_column(8)
            try:
                with memoryview(buf) as rmv:
                    assert len(rmv) == 8
                with memoryview(buf.writable()) as wmv:
                    wmv[0] = float(it)
            finally:
                buf.close()
            if it % 20 == 0:
                assert cad.apply_twice(lambda x: x + 1, it) == it + 2
            if it % 50 == 0:
                async def _step_async():
                    return await cad.async_add(it, 1, 1)
                assert asyncio.run(_step_async()) == it + 1
    except Exception as exc:
        errors.append((tid, repr(exc)))

threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
for th in threads:
    th.start()
for th in threads:
    th.join()

assert not errors, f"soak errors: {errors}"
assert sys._is_gil_enabled() is expect_gil
print(f"SOAK_OK gil={sys._is_gil_enabled()}")
"""
            for label, py_exe, expect_gil in (("3.15", sys.executable, "1"), ("3.15t", PY315T, "0")):
                vdir = tmp / f"venv-{label}"
                subprocess.run([UV, "venv", "--python", py_exe, str(vdir)], check=True)
                vpy = vdir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
                subprocess.run([UV, "pip", "install", "--python", str(vpy), str(cb_res.wheel_path), str(at_res.wheel_path)], check=True)
                env = os.environ.copy()
                env["PATH"] = ""
                run = subprocess.run([str(vpy), "-c", soak_script, expect_gil], env=env, capture_output=True, text=True)
                if run.returncode != 0:
                    self.fail(f"Phase 7A soak failed on {label}:\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}")
                self.assertIn("SOAK_OK", run.stdout)


if __name__ == "__main__":
    unittest.main()
