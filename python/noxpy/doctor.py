"""Read-only environment & toolchain diagnostics (`noxpy doctor`, `AGENTS.md` §15.4)."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _detect_noxc() -> dict[str, object]:
    noxc = os.environ.get("NOXC") or shutil.which("noxc")
    if not noxc:
        candidate = Path.home() / ".nox-lang" / "bin" / "noxc"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            noxc = str(candidate)
    if not noxc:
        return {
            "found": False,
            "path": None,
            "version": None,
            "sha256": None,
            "host_abi_header": {"found": False, "path": None},
            "noxrt_o": None,
        }
    noxc_p = Path(noxc)
    res = subprocess.run([str(noxc_p), "--version"], capture_output=True, text=True)
    ver = (res.stdout + res.stderr).strip().splitlines()[0] if (res.stdout or res.stderr) else None
    prefix_dir = noxc_p.parent.parent
    noxrt_p = Path(os.environ.get("NOXRT") or (prefix_dir / "lib" / "noxrt.o"))
    host_h_p = prefix_dir / "include" / "nox_host.h"
    return {
        "found": True,
        "path": str(noxc_p),
        "version": ver,
        "sha256": _sha256_file(noxc_p),
        "host_abi_header": {
            "found": host_h_p.is_file(),
            "path": str(host_h_p),
        },
        "noxrt_o": {
            "found": noxrt_p.is_file(),
            "path": str(noxrt_p),
            "sha256": _sha256_file(noxrt_p),
        },
    }


def _detect_315t() -> dict[str, object]:
    candidates = [
        os.environ.get("NOXPY_PYTHON_315T", ""),
        shutil.which("python3.15t") or "",
        str(Path.home() / ".local" / "bin" / "python3.15t"),
        "/Library/Frameworks/PythonT.framework/Versions/3.15/bin/python3.15t",
        "/opt/homebrew/bin/python3.15t",
    ]
    uv = shutil.which("uv")
    if uv:
        uv_probe = subprocess.run([uv, "python", "find", "3.15t"], capture_output=True, text=True)
        if uv_probe.returncode == 0 and uv_probe.stdout.strip():
            candidates.append(uv_probe.stdout.strip())
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            res = subprocess.run(
                [c, "-c", "import sys; print(sys.version.split()[0], sys._is_gil_enabled())"],
                capture_output=True,
                text=True,
            )
            return {
                "found": res.returncode == 0,
                "path": c,
                "probe": res.stdout.strip() or res.stderr.strip(),
            }
    return {"found": False, "path": None, "probe": "python3.15t not installed"}


def collect_doctor_report() -> dict[str, object]:
    inc_dir = Path(sysconfig.get_path("include") or "")
    pyabi_h = inc_dir / "pyabi.h"
    has_abi3t = pyabi_h.is_file() and "Py_TARGET_ABI3T" in pyabi_h.read_text(encoding="utf-8", errors="ignore")

    clang_path = shutil.which("clang")
    clang_ver = None
    if clang_path:
        cr = subprocess.run([clang_path, "--version"], capture_output=True, text=True)
        clang_ver = (cr.stdout or cr.stderr).strip().splitlines()[0]

    rtk_path = shutil.which("rtk")
    rtk_ver = None
    if rtk_path:
        rr = subprocess.run([rtk_path, "--version"], capture_output=True, text=True)
        rtk_ver = rr.stdout.strip()

    noxc_info = _detect_noxc()
    py315t_info = _detect_315t()
    has_noxc = bool(noxc_info.get("found"))
    has_host_abi = bool((noxc_info.get("host_abi_header") or {}).get("found"))
    has_315t = bool(py315t_info.get("found"))

    blocking: list[str] = []
    if not has_noxc:
        blocking.append("noxc-missing")
    if not has_host_abi:
        blocking.extend(["UR-001", "UR-002", "UR-003", "UR-004", "UR-005", "UR-006", "UR-007"])
    if not has_315t:
        blocking.append("python3.15t-missing")

    phase0_ready = bool(has_abi3t and has_noxc and has_host_abi and has_315t)
    phase1_ready = phase0_ready

    return {
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "meets_315_floor": sys.version_info >= (3, 15),
            "gil_enabled": sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else None,
            "include_dir": str(inc_dir),
            "abi3t_header_ready": has_abi3t,
        },
        "python_315t": py315t_info,
        "clang": {"found": clang_path is not None, "path": clang_path, "version": clang_ver},
        "noxc": noxc_info,
        "rtk": {"found": rtk_path is not None, "path": rtk_path, "version": rtk_ver},
        "phase_gate": {
            "current_phase": 1 if phase1_ready else 0,
            "phase0_exit_ready": phase0_ready,
            "phase1_ready": phase1_ready,
            "blocking_requirements": blocking,
        },
    }
