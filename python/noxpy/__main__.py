"""Thin CLI entry point for `python -m noxpy` (`AGENTS.md` §15.4)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .build import (
    build_project_editable_wheel,
    build_project_sdist,
    build_project_wheel,
    clean_project,
    develop_install,
    init_project,
    inspect_module_ir,
    scaffold_project,
)
from .codegen import generate_c_adapter, generate_pyi
from .config import load_pyproject
from .doctor import collect_doctor_report
from .errors import NoxPyError
from .ir import parse_upstream_metadata_json
from .lockfile import compute_lock_digest


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
    lock_digest = compute_lock_digest(pyproject_path.parent)
    ir = None
    if args.ir:
        raw = json.loads(Path(args.ir).read_text(encoding="utf-8"))
        ir = parse_upstream_metadata_json(
            raw,
            expected_module_name=cfg.module_name,
            abi=cfg.abi,
            free_threaded=cfg.free_threaded,
        )
    elif args.inspect:
        ir = inspect_module_ir(pyproject_path.parent, cfg)

    if args.json:
        out: dict[str, object] = {
            "status": "ok",
            "distribution": cfg.distribution_name,
            "module": cfg.module_name,
            "abi": cfg.abi,
            "free_threaded": cfg.free_threaded,
            "nox_lock_digest": lock_digest,
        }
        if ir is not None:
            out["exported_functions"] = [f.name for f in ir.functions]
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
    else:
        print(f"OK: {cfg.distribution_name} ({cfg.module_name}, {cfg.abi})")
    return 0


def _cmd_generate_stubs(args: argparse.Namespace) -> int:
    if args.ir:
        raw = json.loads(Path(args.ir).read_text(encoding="utf-8"))
        ir = parse_upstream_metadata_json(raw)
        default_out = None
    else:
        proj = Path(args.project).resolve()
        cfg = load_pyproject(proj / "pyproject.toml")
        ir = inspect_module_ir(proj, cfg)
        pkg_prefix = "/".join(cfg.package_parts[:-1])
        base = (cfg.python_source_dir or proj) if cfg.python_source_dir else proj
        default_out = (base / pkg_prefix / f"{cfg.leaf_name}.pyi") if pkg_prefix else (base / f"{cfg.leaf_name}.pyi")

    pyi_text = generate_pyi(ir)
    target_out = Path(args.out).resolve() if args.out else default_out

    if args.check:
        if target_out is None or not target_out.is_file():
            raise NoxPyError("NPY-E-CODEGEN", f"Expected generated stub missing: {target_out}")
        if target_out.read_text(encoding="utf-8") != pyi_text:
            raise NoxPyError("NPY-E-CODEGEN", f"Stub out of date: {target_out}")
        print(f"OK: {target_out} matches Binding IR")
        return 0

    if target_out is not None and (args.out or not args.ir):
        target_out.parent.mkdir(parents=True, exist_ok=True)
        target_out.write_text(pyi_text, encoding="utf-8")
        print(f"Wrote stub: {target_out}")
    else:
        sys.stdout.write(pyi_text)
        if args.emit_c:
            sys.stdout.write("\n/* --- C ADAPTER --- */\n")
            sys.stdout.write(generate_c_adapter(ir))
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    proj = Path(args.project).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (proj / "dist")
    use_cache = not bool(args.no_cache)

    if args.sdist:
        sdist_path = build_project_sdist(proj, out_dir)
        if args.json:
            sys.stdout.write(json.dumps({"sdist_path": str(sdist_path), "sdist_name": sdist_path.name}, indent=2) + "\n")
        else:
            print(f"Built sdist: {sdist_path}")
        return 0

    if args.editable:
        res = build_project_editable_wheel(proj, out_dir, release=bool(args.release), use_cache=use_cache)
    else:
        res = build_project_wheel(proj, out_dir, release=bool(args.release), use_cache=use_cache)

    if args.json:
        sys.stdout.write(json.dumps(res.to_dict(), indent=2) + "\n")
    else:
        print(f"Built wheel: {res.wheel_path}")
        print(f"Tags:        {', '.join(res.tags)}")
        print(f"SHA-256:     {res.wheel_sha256}")
        print(f"Exports:     {', '.join(res.exported_functions)}")
        print(f"Cache hit:   {res.cache_hit}")
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


def _cmd_init(args: argparse.Namespace) -> int:
    created = init_project(args.project)
    if args.json:
        sys.stdout.write(json.dumps({"status": "ok", "created": created}, indent=2) + "\n")
    else:
        print(f"Initialized NoxPy files: {', '.join(created)}")
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    removed = clean_project(args.project)
    if args.json:
        sys.stdout.write(json.dumps({"status": "ok", "removed": removed}, indent=2) + "\n")
    else:
        print(f"Cleaned: {', '.join(removed) if removed else '(nothing to remove)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="noxpy")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="Inspect toolchain, CPython 3.15/3.15t, and readiness.")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=_cmd_doctor)

    p_chk = sub.add_parser("check", help="Validate pyproject.toml, nox.lock, and optional IR.")
    p_chk.add_argument("pyproject", nargs="?", default="pyproject.toml")
    p_chk.add_argument("--ir", help="Path to UR-007 JSON metadata manifest.")
    p_chk.add_argument("--inspect", action="store_true", help="Invoke noxc expand --json on source.")
    p_chk.add_argument("--json", action="store_true")
    p_chk.set_defaults(func=_cmd_check)

    p_stubs = sub.add_parser("generate-stubs", help="Generate/check deterministic .pyi stubs.")
    p_stubs.add_argument("project", nargs="?", default=".")
    p_stubs.add_argument("--ir", help="Path to UR-007 JSON metadata manifest.")
    p_stubs.add_argument("--out", "-o", help="Output .pyi file path.")
    p_stubs.add_argument("--check", action="store_true", help="Fail if on-disk .pyi differs from IR.")
    p_stubs.add_argument("--emit-c", action="store_true", help="Also print generated abi3t C adapter.")
    p_stubs.set_defaults(func=_cmd_generate_stubs)

    p_bld = sub.add_parser("build", help="Build audited cp315-abi3.abi3t wheel, editable wheel, or sdist.")
    p_bld.add_argument("project", nargs="?", default=".")
    p_bld.add_argument("--out-dir", "-o", help="Output directory (defaults to <project>/dist).")
    p_bld.add_argument("--release", action="store_true", help="Enable noxc --release optimization.")
    p_bld.add_argument("--sdist", action="store_true", help="Build deterministic .tar.gz sdist.")
    p_bld.add_argument("--editable", action="store_true", help="Build PEP 660 editable wheel.")
    p_bld.add_argument("--no-cache", action="store_true", help="Bypass deterministic .noxpy-cache.")
    p_bld.add_argument("--json", action="store_true")
    p_bld.set_defaults(func=_cmd_build)

    p_dev = sub.add_parser("develop", help="Build and install extension into active or --python virtualenv.")
    p_dev.add_argument("project", nargs="?", default=".")
    p_dev.add_argument("--python", help="Target virtualenv Python interpreter.")
    p_dev.add_argument("--release", action="store_true")
    p_dev.add_argument("--json", action="store_true")
    p_dev.set_defaults(func=_cmd_develop)

    p_new = sub.add_parser("new", help="Scaffold a new NoxPy extension project.")
    p_new.add_argument("name", help="Distribution/module identifier.")
    p_new.add_argument("--dir", help="Target directory (defaults to ./<name>).")
    p_new.add_argument("--json", action="store_true")
    p_new.set_defaults(func=_cmd_new)

    p_init = sub.add_parser("init", help="Initialize NoxPy metadata in an existing directory.")
    p_init.add_argument("project", nargs="?", default=".")
    p_init.add_argument("--json", action="store_true")
    p_init.set_defaults(func=_cmd_init)

    p_clean = sub.add_parser("clean", help="Remove project-owned .noxpy-cache, build, and dist.")
    p_clean.add_argument("project", nargs="?", default=".")
    p_clean.add_argument("--json", action="store_true")
    p_clean.set_defaults(func=_cmd_clean)

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
