"""Universal Nox Package Bindings bridge (`AGENTS.md` §3; `UPSTREAM_REQUIREMENTS.md` `UR-016`).

Scans an existing unmodified Nox package (`nox.json` + public `.nox` modules), classifies
functions/classes into exportable Host ABI v3 targets, generic specializations, or
unsupported domain signatures, and scaffolds thin wrapper extension projects that depend
on the unmodified upstream package via `nox.json` `requires` + `nox.lock`.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .build import IDENT_RE, normalize_dist_name
from .errors import DiagnosticLocation, NoxPyError

_UNSAFE_SUBINTERP_STDLIB = (
    "nox.http",
    "nox.tls",
    "nox.sqlite",
    "nox.postgres",
    "nox.terminal",
    "nox.signal",
)

_PRIMITIVE_TYPES = frozenset(
    {
        "int",
        "float",
        "bool",
        "str",
        "bytes",
        "None",
        "F64Buffer",
        "I64Buffer",
        "I32Buffer",
        "U8Buffer",
    }
)

_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?\s*\((.*?)\)\s*(?:->\s*([^:]+?))?\s*:\s*(?:#.*)?$"
)
_CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?(?:\(([^)]*)\))?\s*:")
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_.]*)")
_ALL_RE = re.compile(r"__all__\s*(?::\s*[^=]+)?=\s*\[(.*?)\]", re.DOTALL)


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    in_sq = False
    in_dq = False
    esc = False
    for ch in text:
        if esc:
            cur.append(ch)
            esc = False
            continue
        if ch == "\\" and (in_sq or in_dq):
            cur.append(ch)
            esc = True
            continue
        if ch == "'" and not in_dq:
            in_sq = not in_sq
            cur.append(ch)
        elif ch == '"' and not in_sq:
            in_dq = not in_dq
            cur.append(ch)
        elif not in_sq and not in_dq:
            if ch in "([{" :
                depth += 1
                cur.append(ch)
            elif ch in ")]}":
                depth = max(0, depth - 1)
                cur.append(ch)
            elif ch == sep and depth == 0:
                item = "".join(cur).strip()
                if item:
                    parts.append(item)
                cur = []
                continue
            else:
                cur.append(ch)
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _split_top_level_union(t: str) -> list[str] | None:
    parts = _split_top_level(t, "|")
    if len(parts) <= 1:
        return None
    return parts


def _is_supported_abi_type(raw_type: str, *, for_return: bool = False) -> tuple[bool, str]:
    t = " ".join(raw_type.strip().split())
    if not t:
        return False, "missing type annotation"
    if t == "None":
        return (True, "") if for_return else (False, "None parameter without Optional union")
    if t in _PRIMITIVE_TYPES:
        return True, ""

    union_parts = _split_top_level_union(t)
    if union_parts is not None:
        if len(union_parts) != 2 or "None" not in union_parts:
            return False, f"unsupported union type {t!r}"
        inner = union_parts[0] if union_parts[1] == "None" else union_parts[1]
        ok, reason = _is_supported_abi_type(inner, for_return=False)
        return (ok, reason)

    if t.startswith("list[") and t.endswith("]"):
        return _is_supported_abi_type(t[5:-1], for_return=False)
    if t.startswith("dict[") and t.endswith("]"):
        kv = _split_top_level(t[5:-1], ",")
        if len(kv) != 2:
            return False, f"invalid dict type {t!r}"
        ok_k, rk = _is_supported_abi_type(kv[0], for_return=False)
        if not ok_k:
            return False, f"dict key type: {rk}"
        return _is_supported_abi_type(kv[1], for_return=False)
    if t.startswith("tuple[") and t.endswith("]"):
        elems = _split_top_level(t[6:-1], ",")
        if not elems:
            return False, f"empty tuple type {t!r}"
        for el in elems:
            ok_e, re_s = _is_supported_abi_type(el, for_return=False)
            if not ok_e:
                return False, f"tuple element type: {re_s}"
        return True, ""

    return False, f"custom/domain type {t!r} requires explicit @export class or specialization"


def _strip_default_literal(raw_default: str) -> str | None:
    d = raw_default.strip()
    if d in ("True", "False", "None"):
        return d
    if re.fullmatch(r"-?\d+", d):
        return d
    if re.fullmatch(r"-?\d+\.\d*", d):
        return d
    if (d.startswith('"') and d.endswith('"')) or (d.startswith("'") and d.endswith("'")):
        return d
    return None


@dataclass(frozen=True)
class _ParsedParam:
    name: str
    type_name: str
    default_literal: str | None


@dataclass(frozen=True)
class _ParsedFunc:
    module: str
    name: str
    params: tuple[_ParsedParam, ...]
    return_type: str
    is_async: bool
    is_generic: bool
    reason: str | None


def _discover_module_files(pkg_root: Path, entry_rel: str, only_modules: list[str] | None) -> list[Path]:
    all_nox = sorted(
        p
        for p in pkg_root.iterdir()
        if p.is_file()
        and p.suffix == ".nox"
        and not p.name.startswith("_")
        and not p.name.endswith("_test.nox")
    )
    src_dir = pkg_root / "src"
    if src_dir.is_dir():
        all_nox.extend(
            sorted(
                p
                for p in src_dir.iterdir()
                if p.is_file()
                and p.suffix == ".nox"
                and not p.name.startswith("_")
                and not p.name.endswith("_test.nox")
            )
        )
    if len(all_nox) > 1:
        all_nox = [p for p in all_nox if p.name != entry_rel]
    if only_modules is not None:
        wanted = {m.strip() for m in only_modules if m.strip()}
        all_nox = [p for p in all_nox if p.stem in wanted]
    return all_nox


def _scan_module(mod_path: Path) -> tuple[list[_ParsedFunc], list[str], list[str], list[str], list[str]]:
    text = mod_path.read_text(encoding="utf-8")
    mod_name = mod_path.stem

    all_match = _ALL_RE.search(text)
    explicit_all: set[str] | None = None
    if all_match:
        names = re.findall(r"""["']([A-Za-z_][A-Za-z0-9_]*)["']""", all_match.group(1))
        explicit_all = set(names)

    unsafe_imports: list[str] = []
    intra_imports: list[str] = []
    funcs: list[_ParsedFunc] = []
    generic_classes: list[str] = []
    public_classes: list[str] = []

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        imp_m = _IMPORT_RE.match(raw_line)
        if imp_m:
            mod_imp = imp_m.group(1)
            if mod_imp == "nox" or mod_imp.startswith("nox."):
                for unsafe in _UNSAFE_SUBINTERP_STDLIB:
                    if mod_imp == unsafe or mod_imp.startswith(unsafe + "."):
                        if mod_imp not in unsafe_imports:
                            unsafe_imports.append(mod_imp)
            else:
                if mod_imp not in intra_imports:
                    intra_imports.append(mod_imp)

        # Only top-level definitions (indent == 0)
        if raw_line and not raw_line[0].isspace():
            cls_m = _CLASS_RE.match(raw_line)
            if cls_m:
                cname = cls_m.group(1)
                if not cname.startswith("_") and (explicit_all is None or cname in explicit_all):
                    if cls_m.group(2):
                        generic_classes.append(f"{mod_name}.{cname}{cls_m.group(2)}")
                    else:
                        public_classes.append(f"{mod_name}.{cname}")
                i += 1
                continue

            stripped = raw_line.strip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                sig_buf = stripped
                # Handle multi-line def signatures cleanly
                paren_balance = sig_buf.count("(") - sig_buf.count(")")
                while paren_balance > 0 and i + 1 < len(lines):
                    i += 1
                    sig_buf += " " + lines[i].strip()
                    paren_balance = sig_buf.count("(") - sig_buf.count(")")
                m = _DEF_RE.match(sig_buf)
                if m:
                    fname = m.group(1)
                    if not fname.startswith("_") and (explicit_all is None or fname in explicit_all):
                        is_async = sig_buf.startswith("async ")
                        is_generic = bool(m.group(2))
                        params_str = m.group(3).strip()
                        ret_str = (m.group(4) or "None").strip()
                        parsed_params: list[_ParsedParam] = []
                        bad_reason: str | None = None
                        if is_generic:
                            bad_reason = "generic type parameters require explicit specialization"
                        elif params_str:
                            for p_item in _split_top_level(params_str, ","):
                                if p_item in ("/", "*"):
                                    continue
                                if p_item.startswith("*"):
                                    bad_reason = f"varargs/kwargs parameter {p_item!r} not supported"
                                    break
                                # split top-level `=` first (outside brackets)
                                eq_parts = _split_top_level(p_item, "=")
                                decl = eq_parts[0]
                                def_lit = _strip_default_literal(eq_parts[1]) if len(eq_parts) == 2 else None
                                if len(eq_parts) > 2 or (len(eq_parts) == 2 and def_lit is None):
                                    bad_reason = f"non-literal default on parameter {decl!r}"
                                    break
                                if ":" not in decl:
                                    bad_reason = f"unannotated parameter {decl!r}"
                                    break
                                pname, ptype = decl.split(":", 1)
                                pname = pname.strip()
                                ptype = " ".join(ptype.strip().split())
                                if not IDENT_RE.match(pname):
                                    bad_reason = f"invalid parameter name {pname!r}"
                                    break
                                ok_t, r_t = _is_supported_abi_type(ptype, for_return=False)
                                if not ok_t:
                                    bad_reason = f"param '{pname}': {r_t}"
                                    break
                                parsed_params.append(_ParsedParam(name=pname, type_name=ptype, default_literal=def_lit))

                        if bad_reason is None:
                            ok_r, r_r = _is_supported_abi_type(ret_str, for_return=True)
                            if not ok_r:
                                bad_reason = f"return: {r_r}"

                        funcs.append(
                            _ParsedFunc(
                                module=mod_name,
                                name=fname,
                                params=tuple(parsed_params),
                                return_type=ret_str,
                                is_async=is_async,
                                is_generic=is_generic,
                                reason=bad_reason,
                            )
                        )
        i += 1

    return funcs, unsafe_imports, intra_imports, generic_classes, public_classes


def inspect_nox_package(pkg_dir: str | Path, *, modules: list[str] | None = None) -> dict[str, Any]:
    """Inspect an unmodified Nox package (`nox.json`) and emit a binding compatibility report."""
    root = Path(pkg_dir).resolve()
    manifest_p = root / "nox.json"
    if not manifest_p.is_file():
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"Expected nox.json in Nox package directory: {root}",
            location=DiagnosticLocation(file=str(manifest_p)),
        )
    try:
        manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NoxPyError("NPY-E-CONFIG", f"Invalid nox.json in {root}: {exc}") from exc

    pkg_name = str(manifest.get("name") or root.name)
    pkg_version = str(manifest.get("version") or "0.0.0")
    entry_rel = str(manifest.get("entry") or "main.nox")

    mod_files = _discover_module_files(root, entry_rel, modules)
    exportable: list[dict[str, Any]] = []
    needs_spec: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    unsafe_mods: dict[str, list[str]] = {}
    intra_mods: dict[str, list[str]] = {}
    scanned_names: list[str] = []

    for mp in mod_files:
        scanned_names.append(mp.stem)
        funcs, u_imps, i_imps, g_cls, pub_cls = _scan_module(mp)
        if u_imps:
            unsafe_mods[mp.stem] = u_imps
        if i_imps:
            intra_mods[mp.stem] = i_imps
        for gc_name in g_cls:
            needs_spec.append({"module": mp.stem, "symbol": gc_name, "kind": "class", "reason": "generic class requires specialization"})
        for pc_name in pub_cls:
            unsupported.append({"module": mp.stem, "symbol": pc_name, "kind": "class", "reason": "non-annotated class requires explicit @export wrapper or phase-7 auto-class bridge"})
        for fn in funcs:
            entry_info = {
                "module": fn.module,
                "name": fn.name,
                "is_async": fn.is_async,
                "params": [
                    {"name": p.name, "type": p.type_name, "default": p.default_literal} for p in fn.params
                ],
                "return_type": fn.return_type,
            }
            if fn.reason is None:
                exportable.append(entry_info)
            elif fn.is_generic:
                entry_info["reason"] = fn.reason
                needs_spec.append(entry_info)
            else:
                entry_info["reason"] = fn.reason
                unsupported.append(entry_info)

    return {
        "package": pkg_name,
        "version": pkg_version,
        "package_root": str(root),
        "modules_scanned": scanned_names,
        "subinterpreter_safe": len(unsafe_mods) == 0,
        "subinterpreter_warnings": unsafe_mods,
        "intra_package_imports": intra_mods,
        "summary": {
            "exportable_count": len(exportable),
            "needs_specialization_count": len(needs_spec),
            "unsupported_count": len(unsupported),
            "intra_package_import_modules": len(intra_mods),
        },
        "exportable": exportable,
        "needs_specialization": needs_spec,
        "unsupported": unsupported,
    }


def _resolve_git_ref(pkg_root: Path, override_ref: str | None) -> str:
    if override_ref:
        return override_ref
    if (pkg_root / ".git").exists():
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=pkg_root, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    return "main"


def generate_package_wrapper(
    pkg_dir: str | Path,
    out_dir: str | Path,
    *,
    module_name: str | None = None,
    modules: list[str] | None = None,
    select: list[str] | None = None,
    git_ref: str | None = None,
    fetch: bool = False,
) -> dict[str, Any]:
    """Generate a thin NoxPy wrapper extension project referencing an unmodified Nox package via `nox.json`."""
    root = Path(pkg_dir).resolve()
    report = inspect_nox_package(root, modules=modules)
    pkg_name = str(report["package"])
    pkg_alias = re.sub(r"[^A-Za-z0-9_]", "_", pkg_name).lower()
    if not IDENT_RE.match(pkg_alias) or pkg_alias == "nox":
        pkg_alias = f"pkg_{pkg_alias}"

    mod_name = module_name or f"{pkg_alias}_py"
    if not IDENT_RE.match(mod_name):
        raise NoxPyError("NPY-E-CONFIG", f"Invalid generated Python module name: {mod_name!r}")

    selected_set = {s.strip() for s in select if s.strip()} if select else None
    chosen: list[dict[str, Any]] = []
    seen_py_names: set[str] = set()
    for fn in report["exportable"]:
        fname = str(fn["name"])
        if selected_set is not None and fname not in selected_set and f"{fn['module']}.{fname}" not in selected_set:
            continue
        py_name = fname if fname not in seen_py_names else f"{fn['module']}_{fname}"
        seen_py_names.add(py_name)
        chosen.append({**fn, "py_name": py_name})

    if not chosen:
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"No exportable Host ABI functions found in {pkg_name!r} matching filter",
            hint="Inspect supported APIs with `noxpy check --compatibility <pkg_dir>`.",
        )

    out_root = Path(out_dir).resolve()
    (out_root / "src").mkdir(parents=True, exist_ok=True)
    ref = _resolve_git_ref(root, git_ref)

    nox_json = {
        "name": normalize_dist_name(f"{mod_name}_bridge"),
        "version": str(report["version"]),
        "source": "nox",
        "entry": "src/lib.nox",
        "requires": [
            {
                "alias": pkg_alias,
                "repo": str(root),
                "ref": ref,
                "require_signed_commit": False,
            }
        ],
    }
    (out_root / "nox.json").write_text(json.dumps(nox_json, indent=2) + "\n", encoding="utf-8")

    dist_name = f"noxpy-{pkg_alias.replace('_', '-')}"
    py_ver = str(report["version"])
    pyproject_txt = "\n".join(
        [
            "[build-system]",
            'requires = ["noxpy>=0.1.0a1"]',
            'build-backend = "noxpy.build"',
            "",
            "[project]",
            f'name = "{dist_name}"',
            f'version = "{py_ver}"',
            f'description = "NoxPy native Python bindings for Nox package {pkg_name}"',
            'requires-python = ">=3.15"',
            "",
            "[tool.noxpy]",
            f'module-name = "{mod_name}"',
            'source = "src/lib.nox"',
            'backend = "llvm"',
            'abi = "abi3t"',
            "free-threaded = true",
            "generate-stubs = true",
            'export = "public"',
            "",
        ]
    )
    (out_root / "pyproject.toml").write_text(pyproject_txt, encoding="utf-8")

    needed_mods = sorted({str(fn["module"]) for fn in chosen})
    lib_lines = [
        f"# Auto-generated by `noxpy init --from-nox-package` targeting unmodified `{pkg_name}` ({ref}).",
        "",
    ]
    for m in needed_mods:
        lib_lines.append(f"import {pkg_alias}.{m}")
    lib_lines.append("")

    for fn in chosen:
        params = fn["params"]
        p_sig_parts: list[str] = []
        p_call_parts: list[str] = []
        for p in params:
            decl = f"{p['name']}: {p['type']}"
            if p["default"] is not None:
                decl += f" = {p['default']}"
            p_sig_parts.append(decl)
            p_call_parts.append(p["name"])
        sig_str = ", ".join(p_sig_parts)
        call_str = ", ".join(p_call_parts)
        ret_t = str(fn["return_type"])
        async_kw = "async " if fn["is_async"] else ""
        await_kw = "await " if fn["is_async"] else ""
        lib_lines.append("@export")
        lib_lines.append(f"{async_kw}def {fn['py_name']}({sig_str}) -> {ret_t}:")
        target_call = f"{await_kw}{pkg_alias}.{fn['module']}.{fn['name']}({call_str})"
        if ret_t == "None":
            lib_lines.append(f"    {target_call}")
        else:
            lib_lines.append(f"    return {target_call}")
        lib_lines.append("")

    (out_root / "src" / "lib.nox").write_text("\n".join(lib_lines), encoding="utf-8")

    locked_commit: str | None = None
    if fetch:
        fres = subprocess.run(["noxc", "fetch"], cwd=out_root, capture_output=True, text=True)
        if fres.returncode != 0:
            raise NoxPyError(
                "NPY-E-UPSTREAM",
                f"`noxc fetch` failed in generated wrapper {out_root}:\n{fres.stderr.strip() or fres.stdout.strip()}",
            )
        lock_p = out_root / "nox.lock"
        if lock_p.is_file():
            lock_data = json.loads(lock_p.read_text(encoding="utf-8"))
            pkgs = lock_data.get("packages") or []
            if pkgs:
                locked_commit = str(pkgs[0].get("resolved") or "")

    return {
        "status": "ok",
        "source_package": pkg_name,
        "wrapper_dir": str(out_root),
        "module_name": mod_name,
        "distribution_name": dist_name,
        "exported_functions": [fn["py_name"] for fn in chosen],
        "locked_commit": locked_commit,
    }
