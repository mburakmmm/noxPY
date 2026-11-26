"""End-to-end Phase 1 wheel build, PEP 517, and isolated clean-venv dual-mode tests."""

from __future__ import annotations

import base64
import hashlib
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

from noxpy.build import build_project_wheel, build_wheel, develop_install, scaffold_project  # noqa: E402


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


def _verify_wheel_record_and_tags(whl_path: Path, expected_module: str, expected_stub: bool = True) -> None:
    assert "-cp315-abi3.abi3t-" in whl_path.name, f"unexpected wheel tag: {whl_path.name}"
    with zipfile.ZipFile(whl_path, "r") as zf:
        names = set(zf.namelist())
        assert f"{expected_module}.abi3t.so" in names, names
        if expected_stub:
            assert f"{expected_module}.pyi" in names, names
        dist_infos = {n.split("/")[0] for n in names if ".dist-info/" in n}
        assert len(dist_infos) == 1
        di = next(iter(dist_infos))
        meta = zf.read(f"{di}/METADATA").decode("utf-8")
        wheel_txt = zf.read(f"{di}/WHEEL").decode("utf-8")
        assert "Requires-Python: >=3.15" in meta
        assert "Root-Is-Purelib: false" in wheel_txt
        assert "Tag: cp315-abi3-" in wheel_txt
        assert "Tag: cp315-abi3t-" in wheel_txt

        record = zf.read(f"{di}/RECORD").decode("utf-8")
        for line in record.strip().splitlines():
            rel, h_field, size_s = line.split(",")
            if rel.endswith("/RECORD"):
                continue
            payload = zf.read(rel)
            expected_h = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
            assert h_field == expected_h, f"hash mismatch for {rel}"
            assert int(size_s) == len(payload)


@unittest.skipUnless(REQUIRE_NATIVE or (NOXC is not None and UV is not None), "requires noxc >= 2.2.78 and uv")
class TestPhase1Wheels(unittest.TestCase):
    def test_examples_build_install_and_run_in_clean_dual_envs_without_compiler(self) -> None:
        if NOXC is None or UV is None:
            self.fail("NOXPY_REQUIRE_NATIVE=1 set, but noxc or uv is missing")
        assert UV is not None
        with tempfile.TemporaryDirectory(prefix="noxpy-p1-test-") as td:
            tmp = Path(td)
            dist_dir = tmp / "dist"
            hello_res = build_project_wheel(ROOT / "examples" / "hello", dist_dir, release=True)

            # Also verify PEP 517 `build_wheel` hook on fastmath directly
            old_cwd = Path.cwd()
            try:
                os.chdir(ROOT / "examples" / "fastmath")
                fast_whl_name = build_wheel(str(dist_dir))
            finally:
                os.chdir(old_cwd)
            fast_whl = dist_dir / fast_whl_name

            _verify_wheel_record_and_tags(hello_res.wheel_path, "hello")
            _verify_wheel_record_and_tags(fast_whl, "fastmath")

            # Scaffold via `noxpy new`, build wheel, verify it imports too
            sample_dir = scaffold_project("sample_ext", tmp / "sample_ext")
            sample_res = build_project_wheel(sample_dir, dist_dir, release=True)
            _verify_wheel_record_and_tags(sample_res.wheel_path, "sample_ext")

            # Clean isolated venv for ordinary CPython 3.15
            venv_gil = tmp / "venv_gil"
            subprocess.run([UV, "venv", "--python", sys.executable, str(venv_gil)], check=True, capture_output=True, text=True)
            py_gil = venv_gil / "bin" / "python"

            # Verify `noxpy develop` installs cleanly into the isolated virtualenv
            dev_res = develop_install(ROOT / "examples" / "hello", python_exe=str(py_gil), release=True)
            self.assertEqual(dev_res.module_name, "hello")

            subprocess.run(
                [UV, "pip", "install", "--python", str(py_gil), "--no-deps", str(fast_whl), str(sample_res.wheel_path)],
                check=True,
                capture_output=True,
                text=True,
            )

            smoke_code = """
import sys, threading
import hello, fastmath, sample_ext

assert hello.add(20, 22) == 42
assert hello.add(b=22, a=20) == 42
assert sample_ext.add(a=19, b=23) == 42
assert hello.greet("a\\x00ğ🚀") == "Hello, a\\x00ğ🚀!"
assert hello.echo_bytes(b"\\x00\\x01\\xff") == b"\\x00\\x01\\xff"
assert hello.negate(True) is False
assert hello.negate(False) is True
assert hello.scale(2.5, factor=4.0) == 10.0
assert hello.noop() is None

for fn, args, kw, exc_ty in [
    (hello.add, (True, 1), {}, TypeError),
    (hello.add, (1 << 63, 0), {}, OverflowError),
    (hello.add, (1, 2), {"a": 1}, TypeError),
    (hello.add, (1,), {"bad": 2}, TypeError),
    (hello.add, (1,), {}, TypeError),
    (hello.noop, (1,), {}, TypeError),
    (hello.noop, (), {"x": 1}, TypeError),
    (hello.negate, (1,), {}, TypeError),
    (hello.scale, (2, 3.0), {}, TypeError),
    (fastmath.checked_div, (10, 0), {}, ValueError),
    (fastmath.fib, (-1,), {}, ValueError),
]:
    try:
        fn(*args, **kw)
        raise AssertionError(f"Expected {exc_ty.__name__} for {fn.__name__}{args}{kw}")
    except exc_ty:
        pass

assert fastmath.fib(30) == 832040
assert fastmath.is_even(42) is True
assert fastmath.fma_f64(2.0, 3.0, c=4.5) == 10.5

# 8-thread concurrent barrier calls
barrier = threading.Barrier(8)
errs = []
def w(tid):
    try:
        barrier.wait(5.0)
        for i in range(10):
            assert fastmath.add(tid, b=i) == tid + i
            _ = fastmath.work_loop(200_000, seed=tid + i)
            assert hello.greet(f"w{tid}\\x00{i}") == f"Hello, w{tid}\\x00{i}!"
            assert hello.echo_bytes(bytes([0, tid, i])) == bytes([0, tid, i])
    except BaseException as exc:
        errs.append(exc)

ts = [threading.Thread(target=w, args=(i,)) for i in range(8)]
for t in ts: t.start()
for t in ts: t.join(30.0)
assert not errs, errs
print(f"WHEEL SMOKE PASS ({sys.version.split()[0]} gil={sys._is_gil_enabled()})")
"""
            # Consumer isolation: run outside repo with NO noxc/clang on PATH and NO PYTHONPATH
            empty_cwd = tmp / "consumer_cwd"
            empty_cwd.mkdir()
            clean_env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp)}

            res_gil = subprocess.run([str(py_gil), "-c", smoke_code], cwd=empty_cwd, env=clean_env, capture_output=True, text=True)
            self.assertEqual(res_gil.returncode, 0, f"3.15 wheel failed:\n{res_gil.stdout}\n{res_gil.stderr}")
            self.assertEqual(res_gil.stderr.strip(), "", f"unexpected stderr: {res_gil.stderr}")

            # Install the EXACT SAME wheels into a clean unforced 3.15t virtualenv
            if PY315T is None:
                self.fail("python3.15t is required locally after uv installation")
            venv_ft = tmp / "venv_ft"
            subprocess.run([UV, "venv", "--python", PY315T, str(venv_ft)], check=True, capture_output=True, text=True)
            py_ft = venv_ft / "bin" / "python"
            subprocess.run(
                [
                    UV,
                    "pip",
                    "install",
                    "--python",
                    str(py_ft),
                    "--no-deps",
                    str(hello_res.wheel_path),
                    str(fast_whl),
                    str(sample_res.wheel_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            ft_wrapper = (
                "import sys\n"
                "assert not sys._is_gil_enabled(), 'did not start free-threaded'\n"
                + smoke_code
                + "assert not sys._is_gil_enabled(), 'installed abi3t wheel re-enabled the GIL!'\n"
            )
            res_ft = subprocess.run([str(py_ft), "-c", ft_wrapper], cwd=empty_cwd, env=clean_env, capture_output=True, text=True)
            self.assertEqual(res_ft.returncode, 0, f"3.15t wheel failed:\n{res_ft.stdout}\n{res_ft.stderr}")
            self.assertEqual(res_ft.stderr.strip(), "", f"unexpected stderr on 3.15t: {res_ft.stderr}")


if __name__ == "__main__":
    unittest.main()
