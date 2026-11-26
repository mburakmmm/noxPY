"""Phase 5 end-to-end tests: PEP 3118 zero-copy buffers, bulk TYPED_ARRAY, Atlas analytics, 3.15 & 3.15t."""

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


@unittest.skipUnless(REQUIRE_NATIVE or (NOXC is not None and UV is not None), "requires noxc >= 2.2.95 and uv")
class TestPhase5BuffersAtlas(unittest.TestCase):
    def test_atlas_zero_copy_buffers_and_typed_arrays_dual_mode(self) -> None:
        if NOXC is None or UV is None or PY315T is None:
            if REQUIRE_NATIVE:
                self.fail(f"NOXPY_REQUIRE_NATIVE=1 set, but missing toolchain (NOXC={NOXC}, UV={UV}, PY315T={PY315T})")
            self.skipTest("requires noxc >= 2.2.95, uv, and python3.15t")
        assert UV is not None and PY315T is not None

        with tempfile.TemporaryDirectory(prefix="noxpy-p5-atlas-") as td:
            tmp = Path(td)
            proj = tmp / "atlas_copy"
            shutil.copytree(ROOT / "examples" / "atlas", proj)

            dist_dir = tmp / "dist"
            res = build_project_wheel(proj, dist_dir, release=True, use_cache=True)
            self.assertIn("make_f64_column", res.exported_functions)
            self.assertIn("neumaier_sum_buf", res.exported_functions)
            self.assertIn("welford_variance_buf", res.exported_functions)
            self.assertIn("neumaier_sum_bulk", res.exported_functions)

            with zipfile.ZipFile(res.wheel_path, "r") as zf:
                pyi = zf.read("atlas_demo.pyi").decode("utf-8")
                self.assertIn("class F64Buffer:", pyi)
                self.assertIn("class U8Buffer:", pyi)
                self.assertIn("def make_f64_column(n: int) -> F64Buffer:", pyi)
                self.assertIn("def moving_average_buf(b: F64Buffer, window: int) -> F64Buffer:", pyi)

            smoke_code = """
import array, concurrent.futures, math, struct, sys
import atlas_demo as ad

# 1. PEP 3118 zero-copy F64Buffer: concurrent readonly pins + exclusive writable conflict
buf = ad.make_f64_column(5)
assert isinstance(buf, ad.F64Buffer)
assert len(buf) == 5 and buf.dtype == "float64" and buf.itemsize == 8 and buf.nbytes == 40
assert buf.is_pinned is False and ad.is_pinned_buf(buf) is False

mv1 = memoryview(buf)
mv2 = memoryview(buf)
assert mv1.readonly is True and mv1.format == "d" and list(mv1) == [0.0, 1.0, 2.0, 3.0, 4.0]
assert list(mv2) == [0.0, 1.0, 2.0, 3.0, 4.0]
assert buf.is_pinned is True and ad.is_pinned_buf(buf) is True

# Conflicting writable pin raises BufferError
try:
    _w_bad = memoryview(buf.writable())
    raise AssertionError("expected BufferError when readonly pin active")
except BufferError:
    pass

# Nox resize raises RuntimeError while pinned, close() raises BufferError
try:
    ad.resize_buf(buf, 10)
    raise AssertionError("expected RuntimeError on resize while pinned")
except RuntimeError:
    pass

try:
    buf.close()
    raise AssertionError("expected BufferError on close while pinned")
except BufferError:
    pass

mv1.release()
mv2.release()
assert buf.is_pinned is False

# 2. Bidirectional zero-copy mutation proof (Python writes memory -> Nox reads; Nox mutates -> Python reads)
with memoryview(buf.writable()) as wmv:
    assert wmv.readonly is False
    # Conflicting readonly pin fails while writable pin is open
    try:
        _ro_bad = memoryview(buf)
        raise AssertionError("expected BufferError when writable pin active")
    except BufferError:
        pass
    for i in range(5):
        wmv[i] = 10.0 * (i + 1)

assert ad.neumaier_sum_buf(buf) == 150.0
with memoryview(buf) as rmv:
    ad.scale_inplace_buf(buf, 2.0)
    assert list(rmv) == [20.0, 40.0, 60.0, 80.0, 100.0]

# Resize works cleanly once unpinned & updates len/nbytes
ad.resize_buf(buf, 3)
assert len(buf) == 3 and buf.nbytes == 24
assert ad.neumaier_sum_buf(buf) == 120.0

# 3. Atlas Neumaier cancellation & Welford variance
col = ad.make_f64_column(4)
with memoryview(col.writable()) as cmv:
    cmv[0] = 1e16
    cmv[1] = 1.0
    cmv[2] = -1e16
    cmv[3] = 2.0
assert ad.neumaier_sum_buf(col) == 3.0

vals = ad.make_f64_column(5)  # 0..4 => mean 2.0, sample variance 2.5
mean, var = ad.welford_variance_buf(vals, 1)
assert math.isclose(mean, 2.0) and math.isclose(var, 2.5)

# Moving average returning new F64Buffer + shared_ctx lifetime survival after source close()
ma = ad.moving_average_buf(vals, 2)
vals.close()
with memoryview(ma) as mamv:
    assert list(mamv) == [0.0, 0.5, 1.5, 2.5, 3.5]
ma.close()

# 4. I64Buffer / I32Buffer / U8Buffer dtypes
u8 = ad.make_u8_column(4)
with memoryview(u8.writable()) as mv_u8:
    mv_u8[:] = b"\\x01\\x02\\x03\\xfa"
assert ad.sum_u8_buf(u8) == 256

i32 = ad.make_i32_column(3)
with memoryview(i32.writable()) as mv_i32:
    mv_i32[0], mv_i32[1], mv_i32[2] = -1, 5, 6
assert ad.sum_i32_buf(i32) == 10

i64 = ad.make_i64_column(2)
with memoryview(i64.writable()) as mv_i64:
    mv_i64[0], mv_i64[1] = 100, -42
assert ad.sum_i64_buf(i64) == 58

# 5. Bulk TYPED_ARRAY inputs (single-copy, no Python list boxing)
af = array.array("f", [1.5, 2.5, 4.0])
ad_arr = array.array("d", [1e16, 1.0, -1e16])
ai64 = array.array("q", [10, 20, 30, -5])
ai32 = array.array("i", [-1, 5, 6])
ba = bytearray([1, 2, 3, 4])
assert ad.neumaier_sum_bulk(af) == 8.0
assert ad.neumaier_sum_bulk(ad_arr) == 1.0
assert ad.dot_f64_bulk(array.array("d", [1.0, 2.0, 3.0]), array.array("d", [4.0, 5.0, 6.0])) == 32.0
assert ad.sum_ints(ai64) == 55
assert ad.sum_ints(ai32) == 10
assert ad.sum_ints(ba) == 10

# bool typed array via raw '?' memoryview
braw = bytearray([1, 0, 1, 1, 0])
bmv = memoryview(braw).cast("?")
assert ad.count_true(bmv) == 3
bmv.release()

# Rejection checks: strided slice, wrong dtype, wrong type, non-native endianness
mv_src = memoryview(array.array("d", [1.0, 2.0, 3.0, 4.0]))
strided = mv_src[::2]
try:
    ad.neumaier_sum_bulk(strided)
    raise AssertionError("expected ValueError for strided buffer")
except ValueError:
    pass
strided.release()
mv_src.release()

try:
    ad.sum_ints(array.array("d", [1.0, 2.0]))
    raise AssertionError("expected ValueError for float array -> list[int]")
except ValueError:
    pass

try:
    ad.count_true(ba)
    raise AssertionError("expected ValueError for uint8 buffer -> list[bool]")
except ValueError:
    pass

# UR-015 checked integer overflow recovery: raises OverflowError and keeps context healthy
try:
    ad.sum_ints([2**63 - 1, 1])
    raise AssertionError("expected OverflowError on int64 addition overflow")
except OverflowError:
    pass
assert ad.sum_ints([20, 22]) == 42

# 6. Concurrent threads running independent zero-copy buffers & bulk TYPED_ARRAY on 3.15t
def worker(tid):
    b = ad.make_f64_column(16)
    with memoryview(b.writable()) as wmv:
        for i in range(16):
            wmv[i] = float(i + tid)
    total = ad.neumaier_sum_buf(b)
    b.close()
    bulk = ad.neumaier_sum_bulk(array.array("d", [float(tid)] * 100))
    return (total, bulk)

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    outs = list(pool.map(worker, range(16)))
for tid, (t, bk) in enumerate(outs):
    assert t == sum(float(i + tid) for i in range(16))
    assert bk == float(tid) * 100.0
"""

            for py_label, py_exe in (("3.15", sys.executable), ("3.15t", PY315T)):
                venv_dir = tmp / f"venv-{py_label}"
                clean_env = {k: v for k, v in os.environ.items() if k != "PATH"}
                clean_env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
                subprocess.run([UV, "venv", "--python", py_exe, str(venv_dir)], check=True)
                vpy = str(venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
                subprocess.run([UV, "pip", "install", "--python", vpy, str(res.wheel_path)], check=True)

                gil_probe = subprocess.run(
                    [vpy, "-c", "import sys, atlas_demo; print(getattr(sys, '_is_gil_enabled', lambda: True)())"],
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
