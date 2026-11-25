"""Thin CLI entry point for `python -m noxpy` (`AGENTS.md` §15.4)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .build import build_project_wheel, develop_install, scaffold_project
from .codegen import generate_c_adapter, generate_pyi
from .config import load_pyproject
from .doctor import collect_doctor_report
from .errors import NoxPyError
from .ir import parse_upstream_metadata_json


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = collect_doctor_report()
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        py = report["python"]
        nx = report["noxc"]
        p315t = report["python_315t"]
        print(f"Python:   {py['version']} ({py['executable']}) abi3t_headers={py['abi3t_header_ready']}")
        print(f"PythonT:  found={p315t['found']} ({p315t['probe']})")
        print(f"noxc:     found={nx['found']} version={nx['version']} path={nx['path']}")
        print(f"clang:    found={report['clang']['found']} ({report['clang']['version']})")
        print(f"rtk:      found={report['rtk']['found']} ({report['rtk']['version']})")
        print(
            f"Phase:    {report['phase_gate']['current_phase']} "
            f"(phase0_exit_ready={report['phase_gate']['phase0_exit_ready']}, "
            f"phase1_ready={report['phase_gate']['phase1_ready']})"
        )
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    pyproject_path = Path(args.pyproject).resolve()
    cfg = load_pyproject(pyproject_path)
    ir = None
    if args.ir:
        raw = json.loads(Path(args.ir).read_text(encoding="utf-8"))
        ir = parse_upstream_metadata_json(
            raw,
            expected_module_name=cfg.module_name,
            abi=cfg.abi,
            free_threaded=cfg.free_threaded,
        )
    elif getattr(args, "inspect", False):
        from .build import inspect_module_ir

        ir = inspect_module_ir(pyproject_path.parent, cfg)

    if args.json:
        out: dict[str, object] = {
            "status": "ok",
            "distribution": cfg.distribution_name,
            "module": cfg.module_name,
            "abi": cfg.abi,
            "free_threaded": cfg.free_threaded,
        }
        if ir is not None:
            out["exported_functions"] = [f.name for f in ir.functions]
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
    else:
        print(f"OK: {cfg.distribution_name} ({cfg.module_name}, {cfg.abi})")
    return 0


def _cmd_generate_stubs(args: argparse.Namespace) -> int:
    raw = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    ir = parse_upstream_metadata_json(raw)
    sys.stdout.write(generate_pyi(ir))
    if args.emit_c:
        sys.stdout.write("\n/* --- C ADAPTER --- */\n")
        sys.stdout.write(generate_c_adapter(ir))
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    proj = Path(args.project).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (proj / "dist")
    res = build_project_wheel(proj, out_dir, release=bool(args.release))
    if args.json:
        sys.stdout.write(json.dumps(res.to_dict(), indent=2) + "\n")
    else:
        print(f"Built wheel: {res.wheel_path}")
        print(f"Tags:        {', '.join(res.tags)}")
        print(f"SHA-256:     {res.wheel_sha256}")
        print(f"Exports:     {', '.join(res.exported_functions)}")
    return 0


def _cmd_develop(args: argparse.Namespace) -> int:
    res = develop_install(args.project, python_exe=args.python, release=bool(args.release))
    if args.json:
        sys.stdout.write(json.dumps(res.to_dict(), indent=2) + "\n")
    else:
        print(f"Installed {res.module_name} ({', '.join(res.exported_functions)})")
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    dest = scaffold_project(args.name, args.dir)
    if args.json:
        sys.stdout.write(json.dumps({"status": "ok", "path": str(dest), "name": args.name}, indent=2) + "\n")
    else:
        print(f"Created NoxPy project at {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="noxpy")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="Inspect toolchain, CPython 3.15/3.15t, and readiness.")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=_cmd_doctor)

    p_chk = sub.add_parser("check", help="Validate pyproject.toml and optional UR-007 IR JSON manifest.")
    p_chk.add_argument("pyproject", nargs="?", default="pyproject.toml")
    p_chk.add_argument("--ir", help="Path to UR-007 JSON metadata manifest.")
    p_chk.add_argument("--inspect", action="store_true", help="Invoke noxc expand --json on source.")
    p_chk.add_argument("--json", action="store_true")
    p_chk.set_defaults(func=_cmd_check)

    p_stubs = sub.add_parser("generate-stubs", help="Generate deterministic .pyi from UR-007 IR JSON.")
    p_stubs.add_argument("--ir", required=True, help="Path to UR-007 JSON metadata manifest.")
    p_stubs.add_argument("--emit-c", action="store_true", help="Also print generated abi3t C adapter.")
    p_stubs.set_defaults(func=_cmd_generate_stubs)

    p_bld = sub.add_parser("build", help="Compile Nox extension and package audited cp315-abi3.abi3t wheel.")
    p_bld.add_argument("project", nargs="?", default=".", help="Project directory containing pyproject.toml.")
    p_bld.add_argument("--out-dir", "-o", help="Output wheel directory (defaults to <project>/dist).")
    p_bld.add_argument("--release", action="store_true", help="Enable noxc --release optimization.")
    p_bld.add_argument("--json", action="store_true")
    p_bld.set_defaults(func=_cmd_build)

    p_dev = sub.add_parser("develop", help="Build and install extension into active or --python virtualenv.")
    p_dev.add_argument("project", nargs="?", default=".", help="Project directory containing pyproject.toml.")
    p_dev.add_argument("--python", help="Target virtualenv Python interpreter.")
    p_dev.add_argument("--release", action="store_true")
    p_dev.add_argument("--json", action="store_true")
    p_dev.set_defaults(func=_cmd_develop)

    p_new = sub.add_parser("new", help="Scaffold a new Phase 1 NoxPy extension project.")
    p_new.add_argument("name", help="Distribution/module identifier (e.g. fastmath).")
    p_new.add_argument("--dir", help="Target directory (defaults to ./<name>).")
    p_new.add_argument("--json", action="store_true")
    p_new.set_defaults(func=_cmd_new)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except NoxPyError as exc:
        sys.stderr.write(f"error: {exc}\n")
        if exc.hint:
            sys.stderr.write(f"hint: {exc.hint}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
