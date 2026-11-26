"""Phase 6.5-B E2E: Universal Nox package bindings for unmodified packages + sibling Noxen & Atlas."""

from __future__ import annotations

import json
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
from noxpy.pkg_bridge import generate_package_wrapper, inspect_nox_package  # noqa: E402


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
class TestPhase65UniversalPackages(unittest.TestCase):
    def test_unmodified_git_nox_package_wraps_and_runs_dual_mode(self) -> None:
        if NOXC is None or UV is None or PY315T is None:
            if REQUIRE_NATIVE:
                self.fail(
                    f"NOXPY_REQUIRE_NATIVE=1 set, but missing toolchain (NOXC={NOXC}, UV={UV}, PY315T={PY315T})"
                )
            self.skipTest("requires noxc >= 2.2.95, uv, and python3.15t")
        assert UV is not None and PY315T is not None

        with tempfile.TemporaryDirectory(prefix="noxpy-p65-univ-") as td:
            tmp = Path(td)
            upstream = tmp / "mymath_pkg"
            upstream.mkdir()
            # Upstream package has NO @export decorators anywhere!
            (upstream / "nox.json").write_text(
                json.dumps({"name": "mymath", "version": "0.2.0", "source": "nox", "entry": "main.nox"}),
                encoding="utf-8",
            )
            (upstream / "main.nox").write_text('import arith\nprint(arith.weighted_sum([1.0, 2.0]))\n', encoding="utf-8")
            (upstream / "sub").mkdir()
            (upstream / "sub" / "acc.nox").write_text(
                """
class Acc:
    total: int
    def __init__(self, start: int = 0) -> None:
        self.total = start
    def add(self, delta: int) -> int:
        self.total = self.total + delta
        return self.total
    @property
    def value(self) -> int:
        return self.total
    @staticmethod
    def zero() -> int:
        return 0
""",
                encoding="utf-8",
            )
            (upstream / "arith.nox").write_text(
                """
def weighted_sum(xs: list[float], scale: float = 1.0) -> float:
    acc: float = 0.0
    for x in xs:
        acc = acc + x
    return acc * scale

def clamp_i64(v: int, lo: int = 0, hi: int = 100) -> int:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v
""",
                encoding="utf-8",
            )
            subprocess.run(["git", "init"], cwd=upstream, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@noxpy.dev"], cwd=upstream, check=True)
            subprocess.run(["git", "config", "user.name", "NoxPy Test"], cwd=upstream, check=True)
            subprocess.run(["git", "add", "-A"], cwd=upstream, check=True)
            subprocess.run(["git", "commit", "-m", "initial unmodified nox package"], cwd=upstream, check=True, capture_output=True)

            wrap_dir = tmp / "mymath_wrapper"
            info = generate_package_wrapper(
                upstream, wrap_dir, module_name="mymath_native", multi_module=True, include_classes=True, fetch=True
            )
            self.assertEqual(sorted(info["exported_functions"]), ["clamp_i64", "weighted_sum"])
            self.assertEqual(info["exported_classes"], ["Acc"])
            self.assertTrue((wrap_dir / "nox.lock").is_file())

            wheel_res = build_project_wheel(wrap_dir, tmp / "dist", release=True, use_cache=False)

            check_script = """
import array, sys
import mymath_native.arith as arith
from mymath_native.sub.acc import Acc
assert arith.weighted_sum([1.5, 2.5, 3.0], scale=2.0) == 14.0
assert arith.weighted_sum(array.array("d", [10.0, 20.0])) == 30.0
assert arith.clamp_i64(-5) == 0
assert arith.clamp_i64(250, hi=99) == 99
a = Acc(10)
assert a.add(5) == 15
assert a.value == 15
assert Acc.zero() == 0
expect_gil = sys.argv[1] == "1"
assert sys._is_gil_enabled() is expect_gil
print("UNIV_PKG_OK", sys._is_gil_enabled())
"""
            for label, py_exe, expect_gil in (("3.15", sys.executable, "1"), ("3.15t", PY315T, "0")):
                vdir = tmp / f"venv-{label}"
                subprocess.run([UV, "venv", "--python", py_exe, str(vdir)], check=True)
                vpy = vdir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
                subprocess.run([UV, "pip", "install", "--python", str(vpy), str(wheel_res.wheel_path)], check=True)
                env = os.environ.copy()
                env["PATH"] = ""
                run = subprocess.run([str(vpy), "-c", check_script, expect_gil], env=env, capture_output=True, text=True)
                if run.returncode != 0:
                    self.fail(f"Universal package wheel failed on {label}:\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}")
                self.assertIn("UNIV_PKG_OK", run.stdout)

    def test_real_sibling_noxen_and_atlas_bindings_if_present(self) -> None:
        sibling_noxen = ROOT.parent / "noxen"
        sibling_atlas = ROOT.parent / "atlas"
        if not sibling_noxen.is_dir() or not sibling_atlas.is_dir():
            self.skipTest("sibling noxen/atlas repositories not present alongside noxPY")
        if NOXC is None or UV is None or PY315T is None:
            self.skipTest("requires noxc, uv, and python3.15t")
        assert UV is not None and PY315T is not None

        # Verify compatibility inspection on unmodified real Noxen and real Atlas
        noxen_rep = inspect_nox_package(sibling_noxen)
        atlas_rep = inspect_nox_package(sibling_atlas)
        self.assertGreater(noxen_rep["summary"]["exportable_count"], 5)
        self.assertGreater(atlas_rep["summary"]["exportable_count"], 20)
        # Honest UR-016 detection: atlas.values uses bare intra-package `import errors`
        self.assertIn("errors", atlas_rep["intra_package_imports"].get("values", []))

        with tempfile.TemporaryDirectory(prefix="noxpy-sibling-real-") as td:
            tmp = Path(td)
            nx_wrap = tmp / "noxen_real_wrap"
            generate_package_wrapper(
                sibling_noxen,
                nx_wrap,
                module_name="noxen_upstream",
                modules=["validators"],
                select=["is_valid_email", "is_valid_date", "is_valid_time", "is_valid_ipv4", "is_valid_int"],
                fetch=True,
            )
            nx_whl = build_project_wheel(nx_wrap, tmp / "dist", release=True, use_cache=False)

            verify = """
import sys
import noxen_upstream as nu

assert nu.is_valid_email("alice@example.org") is True
assert nu.is_valid_email("bad@@host") is False
assert nu.is_valid_date("2024-02-29") is True
assert nu.is_valid_date("2025-02-29") is False
assert nu.is_valid_time("23:59:59Z") is True
assert nu.is_valid_ipv4("192.168.1.1") is True
assert nu.is_valid_int(42.0) is True
assert nu.is_valid_int(42.5) is False
print("REAL_SIBLING_OK", sys.version.split()[0], sys._is_gil_enabled())
"""
            for label, py_exe, expect_gil in (("3.15", sys.executable, True), ("3.15t", PY315T, False)):
                vdir = tmp / f"venv-{label}"
                subprocess.run([UV, "venv", "--python", py_exe, str(vdir)], check=True)
                vpy = vdir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
                subprocess.run(
                    [UV, "pip", "install", "--python", str(vpy), str(nx_whl.wheel_path)],
                    check=True,
                )
                env = os.environ.copy()
                env["PATH"] = ""
                run = subprocess.run([str(vpy), "-c", verify], env=env, capture_output=True, text=True)
                if run.returncode != 0:
                    self.fail(f"Sibling real package test failed on {label}:\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}")
                self.assertIn(f" {expect_gil}", run.stdout)


if __name__ == "__main__":
    unittest.main()
