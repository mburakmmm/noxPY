"""Unit tests for universal Nox package bindings (`noxpy.pkg_bridge`, `UR-016`)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.config import load_pyproject  # noqa: E402
from noxpy.errors import NoxPyError  # noqa: E402
from noxpy.pkg_bridge import generate_package_wrapper, inspect_nox_package  # noqa: E402


class TestPkgBridge(unittest.TestCase):
    def _make_sample_pkg(self, root: Path, *, include_unsafe_net: bool = False) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "nox.json").write_text(
            json.dumps({"name": "samplepkg", "version": "0.3.1", "source": "nox", "entry": "main.nox"}),
            encoding="utf-8",
        )
        (root / "main.nox").write_text("import core\nprint(core.add_nums(1, 2))\n", encoding="utf-8")
        net_line = "import nox.http\n" if include_unsafe_net else ""
        (root / "core.nox").write_text(
            net_line
            + """
__all__: list[str] = ["add_nums", "greet_opt", "scale_list", "gen_id", "bad_obj", "Widget", "Box", "_private_fn"]

def _private_fn(x: int) -> int:
    return x

def add_nums(a: int, b: int = 5) -> int:
    return a + b

def greet_opt(name: str | None) -> str:
    return name if name is not None else "anon"

def scale_list(xs: list[float], factor: float = 2.0) -> list[float]:
    out: list[float] = []
    for x in xs:
        out.append(x * factor)
    return out

def gen_id[T](x: T) -> T:
    return x

def bad_obj(w: Widget) -> int:
    return 0

class Widget:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class Box[T]:
    v: T
    def __init__(self, v: T) -> None:
        self.v = v
""",
            encoding="utf-8",
        )
        return root

    def test_inspect_compatibility_classifications_and_subinterpreter_warnings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="noxpy-bridge-unit-") as td:
            pkg = self._make_sample_pkg(Path(td) / "samplepkg", include_unsafe_net=True)
            rep = inspect_nox_package(pkg)
            self.assertEqual(rep["package"], "samplepkg")
            self.assertEqual(rep["version"], "0.3.1")
            self.assertEqual(rep["modules_scanned"], ["core"])
            self.assertFalse(rep["subinterpreter_safe"])
            self.assertIn("nox.http", rep["subinterpreter_warnings"]["core"])

            exp_names = [f["name"] for f in rep["exportable"]]
            self.assertEqual(exp_names, ["add_nums", "greet_opt", "scale_list"])
            self.assertNotIn("_private_fn", exp_names)

            spec_symbols = [x.get("name") or x.get("symbol") for x in rep["needs_specialization"]]
            self.assertIn("gen_id", spec_symbols)
            self.assertIn("core.Box[T]", spec_symbols)

            unsup_symbols = [x.get("name") or x.get("symbol") for x in rep["unsupported"]]
            self.assertIn("bad_obj", unsup_symbols)
            self.assertIn("core.Widget", unsup_symbols)

    def test_generate_wrapper_and_config_export_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="noxpy-bridge-gen-") as td:
            tmp = Path(td)
            pkg = self._make_sample_pkg(tmp / "samplepkg")
            out = tmp / "wrapped"
            res = generate_package_wrapper(pkg, out, module_name="sample_bridge", select=["add_nums", "scale_list"])
            self.assertEqual(res["module_name"], "sample_bridge")
            self.assertEqual(res["exported_functions"], ["add_nums", "scale_list"])

            cfg = load_pyproject(out / "pyproject.toml")
            self.assertEqual(cfg.module_name, "sample_bridge")

            lib_src = (out / "src" / "lib.nox").read_text(encoding="utf-8")
            self.assertIn("import samplepkg.core", lib_src)
            self.assertIn("@export\ndef add_nums(a: int, b: int = 5) -> int:", lib_src)
            self.assertIn("return samplepkg.core.add_nums(a, b)", lib_src)
            self.assertIn("return samplepkg.core.scale_list(xs, factor)", lib_src)

            nox_json = json.loads((out / "nox.json").read_text(encoding="utf-8"))
            self.assertEqual(nox_json["requires"][0]["alias"], "samplepkg")

            # Invalid [tool.noxpy].export MUST fail cleanly
            bad_toml = out / "bad.toml"
            bad_toml.write_text(
                (out / "pyproject.toml").read_text(encoding="utf-8").replace('export = "public"', 'export = "magic"'),
                encoding="utf-8",
            )
            with self.assertRaises(NoxPyError):
                load_pyproject(bad_toml)

    def test_cli_check_compatibility_and_init_from_package(self) -> None:
        with tempfile.TemporaryDirectory(prefix="noxpy-bridge-cli-") as td:
            tmp = Path(td)
            pkg = self._make_sample_pkg(tmp / "samplepkg")
            env = {"PYTHONPATH": str(ROOT / "python"), "PATH": ""}
            chk = subprocess.run(
                [sys.executable, "-m", "noxpy", "check", "--compatibility", str(pkg), "--json"],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(chk.stdout)
            self.assertEqual(data["summary"]["exportable_count"], 3)

            out = tmp / "cli_out"
            init = subprocess.run(
                [sys.executable, "-m", "noxpy", "init", "--from-nox-package", str(pkg), "--out", str(out), "--json"],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            init_data = json.loads(init.stdout)
            self.assertEqual(init_data["distribution_name"], "noxpy-samplepkg")
            self.assertTrue((out / "src" / "lib.nox").is_file())


if __name__ == "__main__":
    unittest.main()
