#!/usr/bin/env python3
"""Dry-run phase release verification helper (never performs git push/tag mutations)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_step(label: str, cmd: list[str], env: dict[str, str] | None = None) -> None:
    print(f"==> {label}")
    res = subprocess.run(cmd, cwd=ROOT, env=env)
    if res.returncode != 0:
        raise SystemExit(res.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify local readiness for a phase release.")
    parser.add_argument("--tag", help="Target tag to validate against pyproject.toml / CHANGELOG.md.")
    parser.add_argument("--dry-run", action="store_true", help="Run verification only (default behavior).")
    parser.add_argument("--skip-reproducers", action="store_true", help="Skip upstream reproducers check.")
    args = parser.parse_args(argv)

    run_step("Unit tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests/unit", "-v"])
    run_step("Codegen tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests/codegen", "-v"])

    env_py = dict(os.environ)
    env_py["PYTHONPATH"] = str(ROOT / "python")
    run_step("noxpy doctor", [sys.executable, "-m", "noxpy", "doctor"], env=env_py)
    run_step("RTK enforcement check", [sys.executable, "scripts/hooks/verify_rtk_enforcement.py"])

    if not args.skip_reproducers and os.path.exists(ROOT / "tests/reproducers/run_all.sh"):
        run_step("Upstream reproducers", ["bash", "tests/reproducers/run_all.sh"])

    py_data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    cur_ver = (py_data.get("project") or {}).get("version")
    print(f"\nCurrent pyproject.toml version: {cur_ver}")

    if args.tag:
        run_step(
            f"Release gate for {args.tag}",
            [sys.executable, "scripts/release/check_release_gate.py", "--tag", args.tag, "--json"],
        )
        print(
            "\nReady for maintainer-approved release:\n"
            f"  git tag -a {args.tag} -m \"NoxPy {args.tag}\"\n"
            f"  git push origin main --follow-tags\n"
        )
    else:
        print(
            "\nDry-run local checks PASSED.\n"
            "When ready to cut a phase release:\n"
            "  1. Bump version in pyproject.toml and python/noxpy/__init__.py\n"
            "  2. Add `## [<version>]` notes in CHANGELOG.md and evidence in docs/evidence/\n"
            "  3. Re-run: python3.15 scripts/release/prepare_phase_release.py --tag v<version> --dry-run\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
