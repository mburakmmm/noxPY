"""Pure-Python unit tests for wheel metadata, tags, RECORD integrity, and develop safety."""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.build import (  # noqa: E402
    compute_platform_tag,
    develop_install,
    normalize_dist_name,
    prepare_metadata_for_build_wheel,
    write_deterministic_wheel,
)
from noxpy.config import load_pyproject  # noqa: E402
from noxpy.errors import NoxPyError  # noqa: E402


class TestPackagingUnit(unittest.TestCase):
    def test_deterministic_wheel_metadata_and_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            proj = td_p / "proj"
            proj.mkdir()
            (proj / "pyproject.toml").write_text(
                '[project]\nname = "my-pkg"\nversion = "0.1.0a1"\nrequires-python = ">=3.15"\ndescription = "Demo"\n'
                '[tool.noxpy]\nmodule-name = "my_pkg"\nsource = "src/lib.nox"\n',
                encoding="utf-8",
            )
            cfg = load_pyproject(proj / "pyproject.toml")
            out_dir = td_p / "dist"
            files = {
                "my_pkg.abi3t.so": (b"\x7fELFdummy", 0o755),
                "my_pkg.pyi": (b"def add(a: int, b: int) -> int: ...\n", 0o644),
            }
            plat = compute_platform_tag()
            os.environ["SOURCE_DATE_EPOCH"] = "1790000000"
            try:
                whl1 = write_deterministic_wheel(out_dir / "r1", cfg, files, plat_tag=plat, summary="Demo")
                whl2 = write_deterministic_wheel(out_dir / "r2", cfg, files, plat_tag=plat, summary="Demo")
            finally:
                os.environ.pop("SOURCE_DATE_EPOCH", None)

            self.assertEqual(whl1.name, f"my_pkg-0.1.0a1-cp315-abi3.abi3t-{plat}.whl")
            self.assertEqual(hashlib.sha256(whl1.read_bytes()).hexdigest(), hashlib.sha256(whl2.read_bytes()).hexdigest())

            with zipfile.ZipFile(whl1, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("my_pkg-0.1.0a1.dist-info/METADATA", names)
                self.assertIn("my_pkg-0.1.0a1.dist-info/WHEEL", names)
                self.assertIn("my_pkg-0.1.0a1.dist-info/RECORD", names)
                meta = zf.read("my_pkg-0.1.0a1.dist-info/METADATA").decode("utf-8")
                self.assertIn("Requires-Python: >=3.15", meta)
                wheel_txt = zf.read("my_pkg-0.1.0a1.dist-info/WHEEL").decode("utf-8")
                self.assertIn("Root-Is-Purelib: false", wheel_txt)
                self.assertIn(f"Tag: cp315-abi3-{plat}", wheel_txt)
                self.assertIn(f"Tag: cp315-abi3t-{plat}", wheel_txt)

                record = zf.read("my_pkg-0.1.0a1.dist-info/RECORD").decode("utf-8")
                for line in record.strip().splitlines():
                    rel, h_field, size_s = line.split(",")
                    if rel.endswith("/RECORD"):
                        self.assertEqual(h_field, "")
                        self.assertEqual(size_s, "")
                    else:
                        data = zf.read(rel)
                        expected_h = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
                        self.assertEqual(h_field, expected_h)
                        self.assertEqual(int(size_s), len(data))

    def test_prepare_metadata_for_build_wheel(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            proj = td_p / "proj"
            meta_out = td_p / "meta"
            proj.mkdir()
            meta_out.mkdir()
            (proj / "pyproject.toml").write_text(
                '[project]\nname = "demo.ext"\nversion = "0.1.0a1"\nrequires-python = ">=3.15"\n'
                '[tool.noxpy]\nmodule-name = "demo_ext"\nsource = "src/lib.nox"\n',
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            try:
                os.chdir(proj)
                dist_info = prepare_metadata_for_build_wheel(str(meta_out))
            finally:
                os.chdir(old_cwd)
            self.assertEqual(dist_info, f"{normalize_dist_name('demo.ext')}-0.1.0a1.dist-info")
            self.assertTrue((meta_out / dist_info / "METADATA").is_file())
            self.assertTrue((meta_out / dist_info / "WHEEL").is_file())
            self.assertTrue((meta_out / dist_info / "RECORD").is_file())

    def test_develop_refuses_system_python_outside_venv(self) -> None:
        if sys.prefix != sys.base_prefix:
            self.skipTest("Running inside a virtualenv; system Python guard tested only against base interpreter")
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(NoxPyError) as ctx:
                develop_install(td, python_exe=sys.executable)
            self.assertEqual(ctx.exception.code, "NPY-E-CONFIG")
            self.assertIn("non-virtualenv system Python", ctx.exception.message)


    def test_bundled_native_assets_match_canonical_sources(self) -> None:
        pairs = [
            (ROOT / "native" / "include" / "nox_host.h", ROOT / "python" / "noxpy" / "_native" / "include" / "nox_host.h"),
            (ROOT / "native" / "include" / "noxpy_abi.h", ROOT / "python" / "noxpy" / "_native" / "include" / "noxpy_abi.h"),
            (ROOT / "native" / "cpython" / "noxpy_shim.c", ROOT / "python" / "noxpy" / "_native" / "cpython" / "noxpy_shim.c"),
        ]
        for src, bundled in pairs:
            self.assertTrue(bundled.is_file(), f"Missing bundled native asset: {bundled}")
            self.assertEqual(src.read_bytes(), bundled.read_bytes(), f"Bundled {bundled.name} drifted from {src}")


if __name__ == "__main__":
    unittest.main()
