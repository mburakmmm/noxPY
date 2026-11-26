"""Phase 6 E2E: Arrow C Data/Stream + Nox F64Buffer, Noxen validation wheel, & subinterpreters (3.15 & 3.15t)."""

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
class TestPhase6ArrowNoxenSubinterp(unittest.TestCase):
    def test_arrow_noxen_and_subinterpreters_dual_mode(self) -> None:
        if NOXC is None or UV is None or PY315T is None:
            if REQUIRE_NATIVE:
                self.fail(
                    f"NOXPY_REQUIRE_NATIVE=1 set, but missing toolchain (NOXC={NOXC}, UV={UV}, PY315T={PY315T})"
                )
            self.skipTest("requires noxc >= 2.2.95, uv, and python3.15t")
        assert UV is not None and PY315T is not None

        with tempfile.TemporaryDirectory(prefix="noxpy-p6-") as td:
            tmp = Path(td)
            dist_dir = tmp / "dist"

            atlas_proj = tmp / "atlas_copy"
            shutil.copytree(ROOT / "examples" / "atlas", atlas_proj)
            noxen_proj = tmp / "noxen_copy"
            shutil.copytree(ROOT / "examples" / "noxen", noxen_proj)

            atlas_res = build_project_wheel(atlas_proj, dist_dir, release=True, use_cache=False)
            noxen_res = build_project_wheel(noxen_proj, dist_dir, release=True, use_cache=False)

            verify_script = r"""
import array
import concurrent.futures
import concurrent.interpreters as ci
import ctypes
import json
import sys

import atlas_demo as ad
import noxen_demo as nx
from noxpy.arrow_adapter import (
    export_arrow_array,
    export_arrow_stream,
    import_arrow_array,
    import_arrow_stream,
)

expect_gil = sys.argv[1] == "1"
assert sys._is_gil_enabled() is expect_gil, f"unexpected GIL state {sys._is_gil_enabled()}"

# 1. Noxen field validators & UserValidator + TYPED_ARRAY + concurrent threads
assert nx.is_valid_email("alice@example.org") is True
assert nx.is_valid_email("bad@@example.org") is False
assert nx.is_valid_email("alice@localhost") is False
assert nx.is_valid_date("2024-02-29") is True
assert nx.is_valid_date("2025-02-29") is False
assert nx.is_valid_ipv4("192.168.1.42") is True
assert nx.is_valid_ipv4("01.2.3.4") is False
assert nx.is_valid_ipv4("256.0.0.1") is False

with nx.UserValidator(18, 99, "example.org") as uv:
    rec = uv.validate_record("bob@example.org", "2026-11-26", 31, "10.0.0.7")
    assert rec == {"email": "bob@example.org", "signup_date": "2026-11-26", "ip": "10.0.0.7", "status": "ok"}
    try:
        uv.validate_record("bob@wrong.org", "2026-11-26", 31, "10.0.0.7")
        raise AssertionError("expected ValidationError")
    except nx.ValidationError as exc:
        assert isinstance(exc, ValueError)
        assert "domain_mismatch" in str(exc)
    assert uv.validate_emails_batch(["a@b.co", "bad", "c@d.io"]) == [True, False, True]
    assert uv.count_valid_ages([17, 18, 50, 100]) == 2
    assert uv.count_valid_ages(array.array("q", [10, 20, 30, 120, 99])) == 3
    assert uv.stats() == (1, 1)

def _worker(tid):
    with nx.UserValidator(18, 80, "") as lv:
        for idx in range(50):
            lv.validate_record(f"u{tid}_{idx}@test.dev", "2026-01-15", 20 + (idx % 40), "127.0.0.1")
        return lv.stats()

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    results = list(pool.map(_worker, range(8)))
assert results == [(50, 0)] * 8

# 2. Arrow C Data & C Stream zero-copy pin over real Nox F64Buffer
buf = ad.make_f64_column(6)  # [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
class _PyBuf(ctypes.Structure):
    _fields_ = [
        ("buf", ctypes.c_void_p), ("obj", ctypes.c_void_p),
        ("len", ctypes.c_ssize_t), ("itemsize", ctypes.c_ssize_t),
        ("readonly", ctypes.c_int), ("ndim", ctypes.c_int),
        ("format", ctypes.c_char_p), ("shape", ctypes.POINTER(ctypes.c_ssize_t)),
        ("strides", ctypes.POINTER(ctypes.c_ssize_t)), ("suboffsets", ctypes.POINTER(ctypes.c_ssize_t)),
        ("internal", ctypes.c_void_p),
    ]

_PyObjGetBuf = ctypes.PYFUNCTYPE(ctypes.c_int, ctypes.py_object, ctypes.POINTER(_PyBuf), ctypes.c_int)(
    ctypes.cast(ctypes.pythonapi.PyObject_GetBuffer, ctypes.c_void_p).value
)
_PyBufRel = ctypes.PYFUNCTYPE(None, ctypes.POINTER(_PyBuf))(
    ctypes.cast(ctypes.pythonapi.PyBuffer_Release, ctypes.c_void_p).value
)

probe = _PyBuf()
assert _PyObjGetBuf(buf, ctypes.byref(probe), 0x001c) == 0
buf_addr = int(probe.buf)
_PyBufRel(ctypes.byref(probe))

exp = export_arrow_array(buf, name="col_a")
imp = import_arrow_array(exp)
assert imp.data_address == buf_addr, "Arrow import did not alias Nox buffer zero-copy"
assert buf.is_pinned is True

try:
    ad.resize_buf(buf, 12)
    raise AssertionError("resize succeeded while Arrow pin was held")
except (BufferError, RuntimeError):
    pass

try:
    buf.close()
    raise AssertionError("close succeeded while Arrow pin was held")
except BufferError:
    pass

mv = imp.memoryview()
assert mv.readonly is True
assert list(mv) == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
mv.release()
del mv
imp.close()
assert buf.is_pinned is False
ad.resize_buf(buf, 8)
assert len(buf) == 8
buf.close()

b1 = ad.make_f64_column(3)
b2 = ad.make_f64_column(2)
chunks = import_arrow_stream(export_arrow_stream([b1, b2], name="stream_col"))
assert len(chunks) == 2
assert b1.is_pinned is True and b2.is_pinned is True
stream_vals = []
for ch in chunks:
    cmv = ch.memoryview()
    stream_vals.extend(cmv)
    cmv.release()
    ch.close()
assert stream_vals == [0.0, 1.0, 2.0, 0.0, 1.0]
assert b1.is_pinned is False and b2.is_pinned is False
b1.close()
b2.close()

# 3. Subinterpreter isolation (concurrent.interpreters, UR-013)
site_paths = json.dumps([p for p in sys.path if p])
interp_code_tpl = '''
import json, sys
for p in json.loads({paths_repr}):
    if p not in sys.path:
        sys.path.insert(0, p)
import atlas_demo as ad
import noxen_demo as nx
assert sys._is_gil_enabled() is {expect_gil_repr}, f"subinterp GIL mismatch: {{sys._is_gil_enabled()}}"
assert nx.is_valid_email("sub{idx}@example.org") is True
uv = nx.UserValidator(18, 99, "example.org")
for _ in range({iters}):
    uv.validate_record("sub{idx}@example.org", "2026-11-26", 25, "192.0.2.1")
assert uv.stats() == ({iters}, 0), f"subinterp stats mismatch: {{uv.stats()}}"
uv.close()
col = ad.make_f64_column(4)
assert ad.neumaier_sum_buf(col) == 6.0
col.close()
'''

i1 = ci.create()
i2 = ci.create()
try:
    c1 = interp_code_tpl.format(paths_repr=repr(site_paths), expect_gil_repr=repr(expect_gil), idx=1, iters=5)
    c2 = interp_code_tpl.format(paths_repr=repr(site_paths), expect_gil_repr=repr(expect_gil), idx=2, iters=9)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(i1.exec, c1)
        f2 = pool.submit(i2.exec, c2)
        f1.result()
        f2.result()
finally:
    i1.close()
    i2.close()

assert sys._is_gil_enabled() is expect_gil, "module/subinterp import re-enabled GIL!"
print("PHASE6_DUAL_OK", sys.version.split()[0], expect_gil)
"""

            for label, py_exe, expect_gil in (
                ("3.15", sys.executable, "1"),
                ("3.15t", PY315T, "0"),
            ):
                venv_dir = tmp / f"venv-{label}"
                subprocess.run([UV, "venv", "--python", py_exe, str(venv_dir)], check=True)
                vpy = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
                subprocess.run(
                    [UV, "pip", "install", "--python", str(vpy), str(atlas_res.wheel_path), str(noxen_res.wheel_path)],
                    check=True,
                )

                env = os.environ.copy()
                env["PATH"] = ""
                env["PYTHONPATH"] = str(ROOT / "python")
                run = subprocess.run([str(vpy), "-c", verify_script, expect_gil], env=env, capture_output=True, text=True)
                if run.returncode != 0:
                    self.fail(
                        f"Phase 6 verification failed on {label}:\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
                    )
                self.assertIn("PHASE6_DUAL_OK", run.stdout)


if __name__ == "__main__":
    unittest.main()
