"""Seeded property & fuzz tests for Binding IR, pyproject config, nox.lock, and compiled C shim."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.config import load_pyproject  # noqa: E402
from noxpy.errors import NoxPyError  # noqa: E402
from noxpy.ir import parse_upstream_metadata_json  # noqa: E402
from noxpy.lockfile import parse_nox_lock_json  # noqa: E402


class TestFuzzAndProperty(unittest.TestCase):
    def test_fuzz_ir_never_crashes_on_malformed_inputs(self) -> None:
        rng = random.Random(20261125)
        bad_payloads: list[object] = [
            None,
            [],
            42,
            "not-an-object",
            {},
            {"schema_version": 2},
            {"schema_version": 1, "compiler": None},
            {"schema_version": 1, "compiler": {"name": "noxc", "version": ""}, "module": {"name": "a"}, "exports": []},
            {
                "schema_version": 1,
                "compiler": {"name": "noxc", "version": "2.2.81"},
                "module": {"name": "valid", "source": "a.nox"},
                "exports": [None, 123, {"kind": "class", "span": {}}],
            },
        ]
        for _ in range(100):
            name = "".join(rng.choice("abc123_-!@#$\x00 ") for _ in range(rng.randint(0, 12)))
            bad_payloads.append(
                {
                    "schema_version": 1,
                    "compiler": {"name": "noxc", "version": "2.2.81"},
                    "module": {"name": "mod", "source": "a.nox"},
                    "exports": [
                        {
                            "kind": "function",
                            "id": f"id_{rng.randint(1, 5)}",
                            "name": name,
                            "c_symbol": "nox_host_export_mod_x",
                            "span": {"file": "a.nox", "line": 1, "column": 1},
                            "params": [
                                {
                                    "name": name,
                                    "type": rng.choice(
                                        [
                                            "int",
                                            "list[int]",
                                            "bad",
                                            "list[",
                                            "dict[str]",
                                            "tuple[]",
                                            "tuple__int_str",
                                            "list[int | None]",
                                            "list[" * 18 + "int" + "]" * 18,
                                        ]
                                    ),
                                    "has_default": rng.choice([False, True]),
                                    "default": rng.choice([None, 123, {"type": "bytes", "hex": "zz"}, {"type": "int", "value": "bad"}, {"type": "int", "value": 5}]),
                                }
                            ],
                            "return_type": rng.choice(["int", "None", "dict[str, int]", "set[int]"]),
                        }
                    ],
                }
            )

        for payload in bad_payloads:
            try:
                ir = parse_upstream_metadata_json(payload)  # type: ignore[arg-type]
                self.assertEqual(ir.schema_version, 1)
            except NoxPyError as err:
                self.assertTrue(err.code.startswith("NPY-E-"))

    def test_fuzz_lockfile_and_pyproject_path_traversal(self) -> None:
        rng = random.Random(9911)
        for bad_lock in (None, 123, {"packages": 1}, {"packages": [None]}, {"packages": [{"alias": "nox", "repo": "r", "ref": "1", "resolved": "abcdef0"}]}):
            with self.assertRaises(NoxPyError):
                parse_nox_lock_json(bad_lock)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pyproject.toml"
            for bad_src in ("../a.nox", "a/../../b.nox", "/etc/passwd", "", "   ", "\\abs\\path.nox"):
                p.write_text(
                    f'[project]\nname="pkg"\nversion="0.1.0"\nrequires-python=">=3.15"\n'
                    f'[tool.noxpy]\nmodule-name="pkg"\nsource={bad_src!r}\n',
                    encoding="utf-8",
                )
                with self.assertRaises(NoxPyError):
                    load_pyproject(p)

            # Random invalid requires-python floors
            for maj, min_v in ((2, 7), (3, 8), (3, 12), (3, 14)):
                p.write_text(
                    f'[project]\nname="pkg"\nversion="0.1.0"\nrequires-python=">={maj}.{min_v}"\n'
                    f'[tool.noxpy]\nmodule-name="pkg"\nsource="src/a.nox"\n',
                    encoding="utf-8",
                )
                with self.assertRaises(NoxPyError):
                    load_pyproject(p)
        _ = rng

    def test_property_compiled_shim_random_utf8_and_containers(self) -> None:
        if sys.platform not in ("darwin",) and not sys.platform.startswith("linux"):
            self.skipTest("Native shim property test targets macOS/Linux")
        cc = shutil.which("clang") or shutil.which("cc")
        py_inc = sysconfig.get_path("include")
        assert cc and py_inc

        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            so_path = td_p / "_noxpy_shim_test.abi3t.so"
            if sys.platform == "darwin":
                exp = td_p / "exp.txt"
                exp.write_text("_PyModExport__noxpy_shim_test\n", encoding="utf-8")
                link_flags = ["-bundle", "-undefined", "dynamic_lookup", f"-Wl,-exported_symbols_list,{exp}"]
            else:
                ver = td_p / "exp.ver"
                ver.write_text("{\n  global: PyModExport__noxpy_shim_test;\n  local: *;\n};\n", encoding="utf-8")
                link_flags = ["-shared", f"-Wl,--version-script,{ver}"]

            subprocess.run(
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
                check=True,
                capture_output=True,
                text=True,
            )

            prop_script = """
import random
import _noxpy_shim_test as m

rng = random.Random(315)
for _ in range(100):
    v = rng.randint(-(1 << 63), (1 << 63) - 1)
    assert m.rt_i64(v) == v
    assert m.rt_opt_i64(v) == v
    assert m.rt_opt_i64(None) is None

    s = "".join(rng.choice(["a", "\\x00", "ğ", "🚀", "\\n", "\\t", "\\""]) for _ in range(rng.randint(0, 32)))
    assert m.rt_str(s) == s

    b = bytes(rng.randrange(0, 256) for _ in range(rng.randint(0, 32)))
    assert m.rt_bytes(b) == b

    xs = [rng.randint(-(1 << 62), (1 << 62)) for _ in range(rng.randint(0, 25))]
    assert m.rt_list_i64(xs) == xs

    d = {f"k\\x00{i}_{rng.randint(0, 999)}": rng.randint(-1000, 1000) for i in range(rng.randint(0, 12))}
    assert m.rt_dict_str_i64(d) == d
"""
            env = dict(os.environ)
            env["PYTHONPATH"] = str(td_p)
            res = subprocess.run([sys.executable, "-c", prop_script], capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
