"""Differential verification: genuine Nox CLI codegen vs Python reference + clang -Werror."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.codegen import generate_c_adapter, generate_pyi  # noqa: E402
from noxpy.ir import parse_upstream_metadata_json  # noqa: E402

MANIFEST = {
    "schema_version": 1,
    "compiler": {"name": "noxc", "version": "2.2.71", "commit": "abc123"},
    "module": {"name": "pkg._native", "source": "src/lib.nox"},
    "exports": [
        {
            "kind": "function",
            "id": "pkg::add",
            "name": "add",
            "c_symbol": "nox_host_export_pkg_add",
            "doc": 'Add "two" ints.\nLine 2.',
            "thread_policy": "parallel",
            "span": {"file": "src/lib.nox", "line": 3, "column": 1},
            "params": [
                {"name": "a", "type": "int", "kind": "positional_only", "has_default": False},
                {"name": "b", "type": "int", "kind": "positional_or_keyword", "has_default": False},
            ],
            "return_type": "int",
        },
        {
            "kind": "function",
            "id": "pkg::echo_bytes",
            "name": "echo_bytes",
            "c_symbol": "nox_host_export_pkg_echo_bytes",
            "doc": None,
            "thread_policy": "parallel",
            "span": {"file": "src/lib.nox", "line": 7, "column": 1},
            "params": [{"name": "payload", "type": "bytes", "kind": "keyword_only", "has_default": False}],
            "return_type": "bytes",
        },
        {
            "kind": "function",
            "id": "pkg::greet",
            "name": "greet",
            "c_symbol": "nox_host_export_pkg_greet",
            "doc": None,
            "thread_policy": "parallel",
            "span": {"file": "src/lib.nox", "line": 11, "column": 1},
            "params": [
                {"name": "name", "type": "str"},
                {"name": "flag", "type": "bool", "has_default": True, "default": {"type": "bool", "value": False}},
                {"name": "scale", "type": "float", "has_default": True, "default": {"type": "float", "value": 2.0}},
            ],
            "return_type": "None",
        },
        {
            "kind": "function",
            "id": "pkg::transform",
            "name": "transform",
            "c_symbol": "nox_host_export_pkg_transform",
            "doc": "Phase 2 containers and defaults.",
            "thread_policy": "parallel",
            "span": {"file": "src/lib.nox", "line": 16, "column": 1},
            "params": [
                {"name": "xs", "type": "list[tuple[int, str]]", "marshal": "value"},
                {"name": "opt", "type": "int | None", "marshal": "value", "has_default": True, "default": {"type": "None", "value": None}},
                {"name": "note", "type": "str", "marshal": "span", "kind": "keyword_only", "has_default": True, "default": {"type": "str", "value": "hi\n"}},
                {"name": "raw", "type": "bytes", "marshal": "span", "kind": "keyword_only", "has_default": True, "default": {"type": "bytes", "hex": "00ff"}},
                {"name": "limit", "type": "int", "marshal": "scalar", "kind": "keyword_only", "has_default": True, "default": {"type": "int", "value": -9223372036854775808}},
            ],
            "return_marshal": "value",
            "return_type": "dict[str, list[str | None]]",
        },
    ],
}


def _find_noxc() -> str | None:
    on_path = shutil.which("noxc")
    if on_path:
        return on_path
    home_bin = Path.home() / ".nox-lang" / "bin" / "noxc"
    if home_bin.is_file():
        return str(home_bin)
    return None


NOXC = _find_noxc()


@unittest.skipUnless(NOXC is not None, "noxc compiler binary not installed on this runner")
class TestNoxVsPythonCodegen(unittest.TestCase):
    def test_nox_cli_matches_python_reference_byte_for_byte(self) -> None:
        assert NOXC is not None
        ir = parse_upstream_metadata_json(MANIFEST)
        expected_pyi = generate_pyi(ir)
        expected_c = generate_c_adapter(ir)

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            manifest_path = tmp / "manifest.json"
            out_pyi = tmp / "_native.pyi"
            out_c = tmp / "_native_adapter.c"
            manifest_path.write_text(json.dumps(MANIFEST), encoding="utf-8")

            res_pyi = subprocess.run(
                [NOXC, "run", "src/cli/main.nox", "--", "generate-stubs", str(manifest_path), "-o", str(out_pyi)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_pyi.returncode, 0, f"nox generate-stubs failed:\n{res_pyi.stdout}\n{res_pyi.stderr}")
            self.assertEqual(out_pyi.read_text(encoding="utf-8"), expected_pyi)

            res_c = subprocess.run(
                [NOXC, "run", "src/cli/main.nox", "--", "emit-c", str(manifest_path), "-o", str(out_c)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_c.returncode, 0, f"nox emit-c failed:\n{res_c.stdout}\n{res_c.stderr}")
            self.assertEqual(out_c.read_text(encoding="utf-8"), expected_c)

            py_inc = sysconfig.get_path("include")
            clang = shutil.which("clang") or shutil.which("cc")
            if py_inc and clang:
                res_clang = subprocess.run(
                    [
                        clang,
                        "-std=c11",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        "-fsyntax-only",
                        f"-I{ROOT / 'native' / 'include'}",
                        f"-I{py_inc}",
                        str(out_c),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(res_clang.returncode, 0, f"clang failed on Nox-emitted C:\n{res_clang.stderr}")

    def test_nox_cli_check_config_and_fail_closed_build(self) -> None:
        assert NOXC is not None
        with tempfile.TemporaryDirectory() as td:
            toml_path = Path(td) / "pyproject.toml"
            toml_path.write_text(
                '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.15"\n\n'
                '[tool.noxpy]\nmodule-name = "demo._nat"\nsource = "src/lib.nox"\n',
                encoding="utf-8",
            )
            res_cfg = subprocess.run(
                [NOXC, "run", "src/cli/main.nox", "--", "check-config", str(toml_path), "--json"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_cfg.returncode, 0, f"check-config failed:\n{res_cfg.stdout}\n{res_cfg.stderr}")
            parsed = json.loads(res_cfg.stdout)
            self.assertEqual(parsed["distribution_name"], "demo")
            self.assertEqual(parsed["leaf_name"], "_nat")
            self.assertEqual(parsed["abi"], "abi3t")
            self.assertIs(parsed["free_threaded"], True)

        res_build = subprocess.run(
            [NOXC, "run", "src/cli/main.nox", "--", "build"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_build.returncode, 2)
        self.assertIn("NPY-E-UPSTREAM", res_build.stdout + res_build.stderr)


if __name__ == "__main__":
    unittest.main()
