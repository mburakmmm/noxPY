"""Compile and exercise `noxpy_shim.c` primitive converters on real CPython 3.15 / 3.15t."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestShimConverters(unittest.TestCase):
    def test_compiled_abi3t_converters_and_audit(self) -> None:
        if sys.platform not in ("darwin",) and not sys.platform.startswith("linux"):
            self.skipTest(f"Native C shim build test targets macOS/Linux; got {sys.platform}")

        cc = shutil.which("clang") or shutil.which("cc")
        if not cc:
            self.fail("C compiler (clang/cc) not found")

        py_inc = sysconfig.get_path("include")
        assert py_inc
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            so_path = td_p / "_noxpy_shim_test.abi3t.so"

            if sys.platform == "darwin":
                exp_file = td_p / "exports.txt"
                exp_file.write_text("_PyModExport__noxpy_shim_test\n", encoding="utf-8")
                expected_sym = "_PyModExport__noxpy_shim_test"
                link_flags = [
                    "-bundle",
                    "-undefined",
                    "dynamic_lookup",
                    f"-Wl,-exported_symbols_list,{exp_file}",
                ]
            else:
                ver_file = td_p / "exports.ver"
                ver_file.write_text(
                    "{\n  global: PyModExport__noxpy_shim_test;\n  local: *;\n};\n",
                    encoding="utf-8",
                )
                expected_sym = "PyModExport__noxpy_shim_test"
                link_flags = [
                    "-shared",
                    f"-Wl,--version-script,{ver_file}",
                ]

            build = subprocess.run(
                [
                    cc,
                    "-std=c11",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fPIC",
                    "-fvisibility=hidden",
                    *link_flags,
                    f"-I{ROOT / 'native' / 'include'}",
                    f"-I{py_inc}",
                    "-o",
                    str(so_path),
                    str(ROOT / "tests" / "unit" / "shim_test_ext.c"),
                    str(ROOT / "native" / "cpython" / "noxpy_shim.c"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, f"{cc} failed:\n{build.stderr}")

            audit = subprocess.run(
                [sys.executable, str(ROOT / "tests" / "abi" / "audit_binary.py"), str(so_path), expected_sym],
                capture_output=True,
                text=True,
            )
            self.assertEqual(audit.returncode, 0, f"audit failed:\n{audit.stderr}\n{audit.stdout}")

            check_script = """
import math, sys, _noxpy_shim_test as m

gil_before = sys._is_gil_enabled()
assert m.rt_i64(-(1 << 63)) == -(1 << 63)
assert m.rt_i64((1 << 63) - 1) == (1 << 63) - 1
for bad in (True, False, 1.0, "1"):
    try: m.rt_i64(bad); raise AssertionError("expected TypeError")
    except TypeError: pass
try: m.rt_i64(1 << 63); raise AssertionError("expected OverflowError")
except OverflowError: pass

assert m.rt_bool(True) is True
assert m.rt_bool(False) is False
for bad in (0, 1, "true", None):
    try: m.rt_bool(bad); raise AssertionError("expected TypeError")
    except TypeError: pass

assert m.rt_f64(3.25) == 3.25
assert math.isnan(m.rt_f64(float("nan")))
assert math.copysign(1.0, m.rt_f64(-0.0)) == -1.0
for bad in (1, True, "1.5"):
    try: m.rt_f64(bad); raise AssertionError("expected TypeError")
    except TypeError: pass

s = "hello\\x00ğüşiöç🚀world"
assert m.rt_str(s) == s
assert len(m.rt_str("a\\x00b")) == 3
try: m.rt_str(b"bytes"); raise AssertionError("expected TypeError")
except TypeError: pass
try: m.rt_str("\\ud800"); raise AssertionError("expected UnicodeEncodeError")
except UnicodeEncodeError: pass

raw = b"\\x00\\x01\\xffnox"
assert m.rt_bytes(raw) == raw
try: m.rt_bytes("str"); raise AssertionError("expected TypeError")
except TypeError: pass

assert m.rt_opt_i64(None) is None
assert m.rt_opt_i64(42) == 42
try: m.rt_opt_i64(True); raise AssertionError("expected TypeError")
except TypeError: pass

assert m.rt_list_i64([]) == []
assert m.rt_list_i64([1, -2, (1 << 62)]) == [1, -2, (1 << 62)]
try: m.rt_list_i64([1, True]); raise AssertionError("expected TypeError")
except TypeError: pass
try: m.rt_list_i64((1, 2)); raise AssertionError("expected TypeError")
except TypeError: pass

assert m.rt_dict_str_i64({"a": 1, "b\\x00c": -7}) == {"a": 1, "b\\x00c": -7}
try: m.rt_dict_str_i64({1: 2}); raise AssertionError("expected TypeError")
except TypeError: pass
try: m.rt_dict_str_i64({"a": True}); raise AssertionError("expected TypeError")
except TypeError: pass

cyc = []
cyc.append(cyc)
try: m.validate_tree(cyc); raise AssertionError("expected ValueError on cyclic list")
except ValueError: pass

d_cyc = {}
d_cyc["self"] = d_cyc
try: m.validate_tree(d_cyc); raise AssertionError("expected ValueError on cyclic dict")
except ValueError: pass

deep = 1
for _ in range(35):
    deep = [deep]
try: m.validate_tree(deep); raise AssertionError("expected RecursionError on deep tree")
except RecursionError: pass

assert sys._is_gil_enabled() == gil_before, "extension mutated GIL state!"
"""
            env = dict(os.environ)
            env["PYTHONPATH"] = str(td_p)
            run_res = subprocess.run(
                [sys.executable, "-c", check_script],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(run_res.returncode, 0, f"shim tests failed:\n{run_res.stdout}\n{run_res.stderr}")


if __name__ == "__main__":
    unittest.main()
