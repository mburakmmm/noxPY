"""Phase 2 end-to-end tests: mixed package, sdist->wheel, PEP 660 editable, stubs, signatures, cache, 3.15/3.15t."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.build import (  # noqa: E402
    build_editable,
    build_project_wheel,
    build_sdist,
    build_wheel,
    clean_project,
    init_project,
)


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


@unittest.skipUnless(REQUIRE_NATIVE or (NOXC is not None and UV is not None), "requires noxc >= 2.2.78 and uv")
class TestPhase2Packaging(unittest.TestCase):
    def test_mixed_package_wheel_cache_sdist_editable_and_dual_mode(self) -> None:
        if NOXC is None or UV is None or PY315T is None:
            if REQUIRE_NATIVE:
                self.fail(f"NOXPY_REQUIRE_NATIVE=1 set, but missing toolchain (NOXC={NOXC}, UV={UV}, PY315T={PY315T})")
            self.skipTest("requires noxc >= 2.2.86, uv, and python3.15t")
        assert UV is not None and PY315T is not None
        with tempfile.TemporaryDirectory(prefix="noxpy-p2-") as td:
            tmp = Path(td)
            proj = tmp / "mixed_copy"
            shutil.copytree(ROOT / "examples" / "mixed_package", proj)

            dist1 = tmp / "dist1"
            dist2 = tmp / "dist2"
            os.environ["SOURCE_DATE_EPOCH"] = "1790000000"
            try:
                res1 = build_project_wheel(proj, dist1, release=True, use_cache=True)
                res2 = build_project_wheel(proj, dist2, release=True, use_cache=True)
            finally:
                os.environ.pop("SOURCE_DATE_EPOCH", None)

            self.assertFalse(res1.cache_hit)
            self.assertTrue(res2.cache_hit)
            self.assertEqual(res1.wheel_sha256, res2.wheel_sha256)

            with zipfile.ZipFile(res1.wheel_path, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("mixed_pkg/__init__.py", names)
                self.assertIn("mixed_pkg/_native.abi3t.so", names)
                self.assertIn("mixed_pkg/_native.pyi", names)
                self.assertIn("mixed_pkg/py.typed", names)

            # Build reproducible sdist -> unpack in clean directory (NO git checkout) -> build wheel from sdist
            sdist_dir = tmp / "sdist_out"
            old_cwd = Path.cwd()
            try:
                os.chdir(proj)
                sdist_name = build_sdist(str(sdist_dir))
            finally:
                os.chdir(old_cwd)
            sdist_path = sdist_dir / sdist_name
            unpack = tmp / "sdist_unpacked"
            unpack.mkdir()
            with tarfile.open(sdist_path, "r:gz") as tf:
                tf.extractall(unpack, filter="data")
            extracted_proj = next(unpack.iterdir())
            self.assertTrue((extracted_proj / "PKG-INFO").is_file())
            self.assertTrue((extracted_proj / "pyproject.toml").is_file())

            sdist_whl_dir = tmp / "sdist_wheel"
            try:
                os.chdir(extracted_proj)
                sdist_whl_name = build_wheel(str(sdist_whl_dir))
            finally:
                os.chdir(old_cwd)
            sdist_whl = sdist_whl_dir / sdist_whl_name

            # Install sdist-built wheel into clean 3.15 & unforced 3.15t venvs
            smoke_mixed = """
import concurrent.futures, inspect, sys, threading
import mixed_pkg

assert mixed_pkg.add(20, b=22) == 42
assert mixed_pkg.greet("Nox\\x00Py") == "Hello from Nox, Nox\\x00Py!"
assert mixed_pkg.greet_many(["a", "b"]) == ["Hello from Nox, a!", "Hello from Nox, b!"]

# Defaults + PEP 457 inspect.signature verification
assert mixed_pkg.opt_int() is None
assert mixed_pkg.opt_int(None) is None
assert mixed_pkg.opt_int(42) == 42
assert mixed_pkg.opt_str() == "got world"
assert mixed_pkg.opt_str(None) == "none"
assert mixed_pkg.opt_str("Nox") == "got Nox"
assert mixed_pkg.sum_list([1, 2, 3, 4]) == 10
assert mixed_pkg.scale_list([1.5, -2.0]) == [3.0, -4.0]
assert mixed_pkg.scale_list([1.5], factor=4.0) == [6.0]
assert mixed_pkg.tag_bytes(b"\\x00\\xff") == b"\\x00\\xff"
assert mixed_pkg.tag_bytes(b"\\x00a", repeat=2) == b"\\x00a\\x00a"
assert mixed_pkg.double_map({"b": 2, "a\\x00x": 21}) == {"b": 4, "a\\x00x": 42}
assert mixed_pkg.swap_pair((7, "ok")) == ("ok", 7)
assert mixed_pkg.nested_groups([(1, "alpha"), (2, "beta")]) == {
    "alpha": ["alpha", None],
    "beta": ["beta", None],
}
assert mixed_pkg.echo_blobs([b"a\\x00b"], [True]) == [b"a\\x00b", b"\\x00z"]

sig_add = inspect.signature(mixed_pkg._native.add)
assert list(sig_add.parameters.keys()) == ["a", "b"], sig_add
assert "Add two integers natively." in (mixed_pkg._native.add.__doc__ or "")

sig_scale = inspect.signature(mixed_pkg._native.scale_list)
assert sig_scale.parameters["factor"].default == 2.0
sig_opt = inspect.signature(mixed_pkg._native.opt_str)
assert sig_opt.parameters["x"].default == "world"
sig_tag = inspect.signature(mixed_pkg._native.tag_bytes)
assert sig_tag.parameters["repeat"].default == 1
assert sig_tag.parameters["repeat"].kind == inspect.Parameter.KEYWORD_ONLY

# Strict validation & containment errors
for bad_call in (
    lambda: mixed_pkg.sum_list((1, 2)),
    lambda: mixed_pkg.sum_list([1, True]),
    lambda: mixed_pkg.swap_pair([1, "x"]),
    lambda: mixed_pkg.swap_pair((1,)),
    lambda: mixed_pkg.opt_int(True),
):
    try:
        bad_call()
        raise AssertionError("expected TypeError")
    except TypeError:
        pass

cyc = []
cyc.append(cyc)
for fn in (lambda: mixed_pkg.sum_list(cyc),):
    try:
        fn()
        raise AssertionError("expected TypeError or ValueError on cyclic list")
    except (TypeError, ValueError):
        pass

try:
    mixed_pkg.boom_list([1, 2])
    raise AssertionError("expected ValueError")
except ValueError as e:
    assert "boom_list" in str(e)

try:
    mixed_pkg.checked_div(10, 0)
    raise AssertionError("expected ValueError")
except ValueError as e:
    assert "division by zero" in str(e)

# Free-threaded 8-worker concurrent container + concurrent mutation stress test
barrier = threading.Barrier(8)
def worker(idx: int) -> None:
    barrier.wait(timeout=10.0)
    for step in range(25):
        res = mixed_pkg.nested_groups([(idx, f"k{step}")])
        assert res == {f"k{step}": [f"k{step}", None]}
        d_out = mixed_pkg.double_map({f"m{idx}": step, "n": 21})
        assert d_out == {f"m{idx}": step * 2, "n": 42}
        assert mixed_pkg.swap_pair((step, f"s{idx}")) == (f"s{idx}", step)
        assert mixed_pkg.opt_int(None if step % 2 else idx) == (None if step % 2 else idx)
        assert mixed_pkg.echo_blobs([b"x\\x00y"], [True]) == [b"x\\x00y", b"\\x00z"]

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    futs = [pool.submit(worker, i) for i in range(8)]
    for fut in futs:
        fut.result()

# Snapshot safety under concurrent Python list mutation in another thread
shared_list = list(range(50))
stop = False
def mutator() -> None:
    while not stop:
        shared_list.append(1)
        shared_list.pop()

t_mut = threading.Thread(target=mutator)
t_mut.start()
try:
    for _ in range(100):
        s = mixed_pkg.sum_list(shared_list)
        assert s >= sum(range(50))
finally:
    stop = True
    t_mut.join(timeout=5.0)

print("MIXED OK", sys.version.split()[0], sys._is_gil_enabled())
"""
            empty_cwd = tmp / "consumer"
            empty_cwd.mkdir()
            clean_env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp)}

            for label, py_base, expect_gil in (("gil", sys.executable, True), ("ft", PY315T, False)):
                venv_p = tmp / f"venv_{label}"
                subprocess.run([UV, "venv", "--python", py_base, str(venv_p)], check=True, capture_output=True, text=True)
                v_py = venv_p / "bin" / "python"
                subprocess.run([UV, "pip", "install", "--python", str(v_py), "--no-deps", str(sdist_whl)], check=True, capture_output=True, text=True)
                prog = (
                    "import sys\n"
                    f"assert sys._is_gil_enabled() is {expect_gil}\n"
                    + smoke_mixed
                    + f"assert sys._is_gil_enabled() is {expect_gil}\n"
                )
                r = subprocess.run([str(v_py), "-c", prog], cwd=empty_cwd, env=clean_env, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{label} failed:\n{r.stdout}\n{r.stderr}")
                self.assertEqual(r.stderr.strip(), "")

            # PEP 660 editable install test on temp copy
            ed_dir = tmp / "ed_whl"
            try:
                os.chdir(proj)
                ed_name = build_editable(str(ed_dir))
            finally:
                os.chdir(old_cwd)
            ed_venv = tmp / "venv_ed"
            subprocess.run([UV, "venv", "--python", sys.executable, str(ed_venv)], check=True, capture_output=True, text=True)
            ed_py = ed_venv / "bin" / "python"
            subprocess.run([UV, "pip", "install", "--python", str(ed_py), "--no-deps", str(ed_dir / ed_name)], check=True, capture_output=True, text=True)

            # Edit pure-Python wrapper in-place and confirm live reload without rebuild
            init_py = proj / "python" / "mixed_pkg" / "__init__.py"
            init_py.write_text(init_py.read_text(encoding="utf-8") + "\nEDITABLE_SENTINEL = 999\n", encoding="utf-8")
            ed_res = subprocess.run(
                [str(ed_py), "-c", "import mixed_pkg; assert mixed_pkg.EDITABLE_SENTINEL == 999; assert mixed_pkg.add(20, 22) == 42"],
                cwd=empty_cwd,
                env=clean_env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(ed_res.returncode, 0, ed_res.stderr)

            # generate-stubs --check & noxpy init / clean
            env_cli = dict(os.environ)
            env_cli["PYTHONPATH"] = str(ROOT / "python")
            stub_p = proj / "python" / "mixed_pkg" / "_native.pyi"
            res_chk = subprocess.run(
                [sys.executable, "-m", "noxpy", "generate-stubs", str(proj), "--check"],
                capture_output=True,
                text=True,
                env=env_cli,
            )
            self.assertEqual(res_chk.returncode, 0, res_chk.stderr)
            self.assertTrue(stub_p.is_file())

            removed = clean_project(proj)
            self.assertIn(".noxpy-cache", removed)

            init_dir = tmp / "init_demo"
            init_dir.mkdir()
            created = init_project(init_dir)
            self.assertIn("pyproject.toml", created)
            self.assertIn("src/lib.nox", created)


if __name__ == "__main__":
    unittest.main()
