"""PEP 517/660 build backend, deterministic cache, sdist, wheel packager, `develop`, `new`, `init`, `clean` (`AGENTS.md` §15)."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import time
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .audit import AuditResult, audit_extension_binary
from .codegen import generate_c_adapter, generate_pyi
from .config import DIST_NAME_RE, NoxPyProjectConfig, load_pyproject
from .errors import DiagnosticLocation, NoxPyError
from .ir import IDENT_RE, ModuleIR, parse_upstream_metadata_json
from .lockfile import compute_lock_digest


@dataclass(frozen=True)
class BuildResult:
    wheel_path: Path
    wheel_sha256: str
    tags: tuple[str, ...]
    module_name: str
    audit: AuditResult
    noxc_version: str
    noxc_sha256: str
    noxrt_sha256: str
    exported_functions: tuple[str, ...]
    cache_hit: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "wheel_path": str(self.wheel_path),
            "wheel_name": self.wheel_path.name,
            "wheel_sha256": self.wheel_sha256,
            "tags": list(self.tags),
            "module_name": self.module_name,
            "exported_functions": list(self.exported_functions),
            "extension_sha256": self.audit.sha256,
            "extension_exports": list(self.audit.exports),
            "noxc_version": self.noxc_version,
            "noxc_sha256": self.noxc_sha256,
            "noxrt_sha256": self.noxrt_sha256,
            "cache_hit": self.cache_hit,
        }


def normalize_dist_name(name: str) -> str:
    """Normalize PEP 503/427 distribution name."""
    return re.sub(r"[-_.]+", "_", name).lower()


def compute_platform_tag() -> str:
    """Compute supported PEP 425 platform tag aligned with compiler deployment target."""
    if sys.platform == "darwin":
        mach = platform.machine().lower()
        arch = "arm64" if mach in ("arm64", "aarch64") else "x86_64"
        return f"macosx_14_0_{arch}"
    return sysconfig.get_platform().replace("-", "_").replace(".", "_")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _record_hash_b64(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _source_date_epoch() -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw:
        return max(int(raw), 315532800)  # ZIP minimum year 1980
    return 1767225600  # 2026-01-01T00:00:00Z


def _source_date_tuple() -> tuple[int, int, int, int, int, int]:
    gm = time.gmtime(_source_date_epoch())
    return (gm.tm_year, gm.tm_mon, gm.tm_mday, gm.tm_hour, gm.tm_min, gm.tm_sec)


def _find_native_root() -> Path:
    pkg_dir = Path(__file__).resolve().parent
    bundled = pkg_dir / "_native"
    if (bundled / "include" / "noxpy_abi.h").is_file():
        return bundled
    repo_native = pkg_dir.parents[1] / "native"
    if (repo_native / "include" / "noxpy_abi.h").is_file():
        return repo_native
    raise NoxPyError("NPY-E-CONFIG", "Unable to locate NoxPy native/ shim headers and C sources")


def _find_nox_toolchain() -> tuple[Path, Path, Path, str, str, str]:
    noxc = os.environ.get("NOXC") or shutil.which("noxc")
    if not noxc:
        home_cand = Path.home() / ".nox-lang" / "bin" / "noxc"
        if home_cand.is_file() and os.access(home_cand, os.X_OK):
            noxc = str(home_cand)
    if not noxc:
        raise NoxPyError(
            "NPY-E-UPSTREAM",
            "noxc compiler not found on PATH or NOXC environment variable",
            hint="Install nox-lang (>= 2.2.86 for full Phase 2 containers) or set NOXC=/path/to/noxc.",
        )
    noxc_p = Path(noxc).resolve()
    prefix = noxc_p.parent.parent
    noxrt_p = Path(os.environ.get("NOXRT") or (prefix / "lib" / "noxrt.o")).resolve()
    nox_inc = Path(os.environ.get("NOX_INCLUDE") or (prefix / "include")).resolve()

    if not noxrt_p.is_file():
        raise NoxPyError("NPY-E-UPSTREAM", f"Missing Nox runtime archive/object: {noxrt_p}")
    if not (nox_inc / "nox_host.h").is_file():
        raise NoxPyError(
            "NPY-E-UPSTREAM",
            f"Upstream include/nox_host.h not found under {nox_inc} (requires nox-lang >= 2.2.78)",
        )

    ver_res = subprocess.run([str(noxc_p), "--version"], capture_output=True, text=True)
    ver_line = (ver_res.stdout + ver_res.stderr).strip().splitlines()[0] if (ver_res.stdout or ver_res.stderr) else "unknown"
    return noxc_p, noxrt_p, nox_inc, ver_line, _sha256_file(noxc_p), _sha256_file(noxrt_p)


def _find_c_compiler() -> str:
    cc = os.environ.get("CC") or shutil.which("clang") or shutil.which("cc")
    if not cc:
        raise NoxPyError("NPY-E-CONFIG", "C11 compiler (clang or cc) not found on PATH")
    return cc


def _c_compiler_version(cc: str) -> str:
    """Return a normalized C compiler version string for deterministic cache keys."""
    try:
        res = subprocess.run([cc, "--version"], capture_output=True, text=True)
    except OSError:
        return "unknown"
    text = (res.stdout + "\n" + res.stderr).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:2]) if lines else "unknown"


_CACHE_SKIP_DIRS = frozenset(
    {
        ".git",
        ".noxpy-cache",
        "build",
        "dist",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
    }
)


def _collect_project_nox_sources(proj_root: Path, entry_source: Path) -> tuple[tuple[str, bytes], ...]:
    """Collect all project `.nox` files (and `entry_source`) sorted by normalized path for transitive cache invalidation."""
    root_resolved = proj_root.resolve()
    entry_resolved = entry_source.resolve()
    collected: dict[str, bytes] = {}

    if root_resolved.is_dir():
        for dirpath, dirnames, filenames in os.walk(root_resolved):
            dirnames[:] = sorted(d for d in dirnames if d not in _CACHE_SKIP_DIRS)
            for fname in sorted(filenames):
                if not fname.endswith(".nox"):
                    continue
                fpath = (Path(dirpath) / fname).resolve()
                rel = fpath.relative_to(root_resolved).as_posix()
                collected[rel] = fpath.read_bytes()

    if entry_resolved.is_file():
        try:
            entry_key = entry_resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            entry_key = f"__external__/{entry_resolved.as_posix()}"
        collected[entry_key] = entry_resolved.read_bytes()

    return tuple((k, collected[k]) for k in sorted(collected))


def _find_python_abi3t_include() -> Path:
    py_inc = sysconfig.get_path("include")
    if not py_inc:
        raise NoxPyError("NPY-E-ABI", "Unable to locate CPython include directory via sysconfig")
    inc_p = Path(py_inc)
    pyabi_h = inc_p / "pyabi.h"
    if not pyabi_h.is_file() or "Py_TARGET_ABI3T" not in pyabi_h.read_text(encoding="utf-8", errors="ignore"):
        raise NoxPyError(
            "NPY-E-ABI",
            f"CPython headers at {inc_p} do not expose Py_TARGET_ABI3T (requires CPython 3.15+)",
        )
    return inc_p


def format_metadata_text(cfg: NoxPyProjectConfig, *, summary: str | None = None) -> str:
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {cfg.distribution_name}",
        f"Version: {cfg.version}",
    ]
    if summary:
        lines.append(f"Summary: {summary}")
    lines.append(f"Requires-Python: {cfg.requires_python}")
    return "\n".join(lines) + "\n"


def format_wheel_info_text(plat_tag: str) -> str:
    return "\n".join(
        [
            "Wheel-Version: 1.0",
            f"Generator: noxpy {__version__}",
            "Root-Is-Purelib: false",
            f"Tag: cp315-abi3-{plat_tag}",
            f"Tag: cp315-abi3t-{plat_tag}",
            "",
        ]
    )


def write_deterministic_wheel(
    out_dir: Path,
    cfg: NoxPyProjectConfig,
    files: dict[str, tuple[bytes, int]],
    *,
    plat_tag: str,
    summary: str | None = None,
) -> Path:
    """Write a PEP 427 dual-tag (`cp315-abi3.abi3t`) wheel with deterministic hashes & timestamps."""
    norm_dist = normalize_dist_name(cfg.distribution_name)
    dist_info = f"{norm_dist}-{cfg.version}.dist-info"
    wheel_name = f"{norm_dist}-{cfg.version}-cp315-abi3.abi3t-{plat_tag}.whl"
    out_dir.mkdir(parents=True, exist_ok=True)
    wheel_path = out_dir / wheel_name

    all_files = dict(files)
    all_files[f"{dist_info}/METADATA"] = (format_metadata_text(cfg, summary=summary).encode("utf-8"), 0o644)
    all_files[f"{dist_info}/WHEEL"] = (format_wheel_info_text(plat_tag).encode("utf-8"), 0o644)

    record_lines: list[str] = []
    for rel_path in sorted(all_files):
        payload, _ = all_files[rel_path]
        record_lines.append(f"{rel_path},{_record_hash_b64(payload)},{len(payload)}")
    record_rel = f"{dist_info}/RECORD"
    record_lines.append(f"{record_rel},,")
    all_files[record_rel] = (("\n".join(record_lines) + "\n").encode("utf-8"), 0o644)

    date_time = _source_date_tuple()
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".noxpy-whl-", suffix=".tmp", dir=str(out_dir))
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel_path in sorted(all_files):
                payload, mode = all_files[rel_path]
                zi = zipfile.ZipInfo(filename=rel_path, date_time=date_time)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.create_system = 3
                zi.external_attr = (mode & 0xFFFF) << 16
                zf.writestr(zi, payload)
        tmp_path.replace(wheel_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return wheel_path


def inspect_module_ir(
    project_dir: Path,
    cfg: NoxPyProjectConfig,
    *,
    noxc_path: Path | None = None,
) -> ModuleIR:
    """Invoke `noxc expand --json` on project source and parse validated Binding IR."""
    if noxc_path is None:
        noxc_path, _, _, _, _, _ = _find_nox_toolchain()
    cmd = [str(noxc_path), "expand", "--json", "--module", cfg.leaf_name, str(cfg.source_path)]
    res = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True)
    if res.returncode != 0:
        raise NoxPyError(
            "NPY-E-CODEGEN",
            f"noxc expand --json failed:\n{res.stderr.strip() or res.stdout.strip()}",
            location=DiagnosticLocation(file=str(cfg.source_path)),
        )
    try:
        raw_json = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise NoxPyError(
            "NPY-E-CODEGEN",
            f"noxc expand --json returned invalid JSON: {exc}",
            location=DiagnosticLocation(file=str(cfg.source_path)),
        ) from exc

    ir = parse_upstream_metadata_json(
        raw_json,
        expected_module_name=cfg.leaf_name,
        abi=cfg.abi,
        free_threaded=cfg.free_threaded,
    )
    if ir.leaf_name != cfg.leaf_name:
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"IR leaf_name {ir.leaf_name!r} does not match configured leaf_name {cfg.leaf_name!r}",
        )
    return ir


def _compute_cache_key(
    *,
    noxc_sha: str,
    noxrt_sha: str,
    cc_version: str,
    upstream_host_h_bytes: bytes,
    pyproject_bytes: bytes,
    nox_sources: tuple[tuple[str, bytes], ...],
    lock_digest: str,
    native_root: Path,
    release: bool,
    plat_tag: str,
) -> str:
    h = hashlib.sha256()
    parts: list[bytes] = [
        __version__.encode("utf-8"),
        noxc_sha.encode("utf-8"),
        noxrt_sha.encode("utf-8"),
        cc_version.encode("utf-8"),
        upstream_host_h_bytes,
        lock_digest.encode("utf-8"),
        b"release=1" if release else b"release=0",
        plat_tag.encode("utf-8"),
        (Path(__file__).resolve().parent / "codegen.py").read_bytes(),
        (Path(__file__).resolve().parent / "ir.py").read_bytes(),
        pyproject_bytes,
        (native_root / "include" / "noxpy_abi.h").read_bytes(),
        (native_root / "include" / "nox_host.h").read_bytes(),
        (native_root / "cpython" / "noxpy_shim.c").read_bytes(),
    ]
    for rel_path, src_bytes in sorted(nox_sources):
        parts.append(rel_path.encode("utf-8"))
        parts.append(src_bytes)
    for part in parts:
        h.update(len(part).to_bytes(8, "little"))
        h.update(part)
    return h.hexdigest()


def _compile_and_audit_extension(
    proj_root: Path,
    cfg: NoxPyProjectConfig,
    *,
    release: bool,
    use_cache: bool,
) -> tuple[bytes, str | None, tuple[str, ...], AuditResult, str, str, str, bool]:
    native_root = _find_native_root()
    noxc_p, noxrt_p, nox_inc, noxc_ver, noxc_sha, noxrt_sha = _find_nox_toolchain()
    if sys.platform not in ("darwin",) and not sys.platform.startswith("linux"):
        raise NoxPyError("NPY-E-CONFIG", f"Extension linking not yet implemented on {sys.platform}")
    cc = _find_c_compiler()
    cc_version = _c_compiler_version(cc)
    lock_digest = compute_lock_digest(proj_root)
    plat_tag = compute_platform_tag()

    cache_key = _compute_cache_key(
        noxc_sha=noxc_sha,
        noxrt_sha=noxrt_sha,
        cc_version=cc_version,
        upstream_host_h_bytes=(nox_inc / "nox_host.h").read_bytes(),
        pyproject_bytes=(proj_root / "pyproject.toml").read_bytes(),
        nox_sources=_collect_project_nox_sources(proj_root, cfg.source_path),
        lock_digest=lock_digest,
        native_root=native_root,
        release=release,
        plat_tag=plat_tag,
    )
    cache_root = proj_root / ".noxpy-cache"
    cache_entry = cache_root / cache_key
    cached_so = cache_entry / f"{cfg.leaf_name}.abi3t.so"
    cached_meta = cache_entry / "meta.json"
    cached_pyi = cache_entry / f"{cfg.leaf_name}.pyi"

    if use_cache and cached_so.is_file() and cached_meta.is_file():
        try:
            meta = json.loads(cached_meta.read_text(encoding="utf-8"))
            audit = audit_extension_binary(cached_so, f"PyModExport_{cfg.leaf_name}")
            if audit.sha256 == meta.get("extension_sha256"):
                pyi_src = cached_pyi.read_text(encoding="utf-8") if (cfg.generate_stubs and cached_pyi.is_file()) else None
                exports = tuple(str(x) for x in meta.get("exported_functions", []))
                return cached_so.read_bytes(), pyi_src, exports, audit, noxc_ver, noxc_sha, noxrt_sha, True
        except (OSError, json.JSONDecodeError, NoxPyError):
            pass  # Fall through to clean recompile if cache entry is corrupt

    py_inc = _find_python_abi3t_include()
    ir = inspect_module_ir(proj_root, cfg, noxc_path=noxc_p)
    c_src = generate_c_adapter(ir)
    pyi_src = generate_pyi(ir) if cfg.generate_stubs else None

    with tempfile.TemporaryDirectory(prefix="noxpy-build-") as td:
        work = Path(td)
        nox_obj = work / f"{cfg.leaf_name}_nox.o"
        adapter_c = work / f"{cfg.leaf_name}_adapter.c"
        adapter_o = work / f"{cfg.leaf_name}_adapter.o"
        shim_o = work / "noxpy_shim.o"
        so_path = work / f"{cfg.leaf_name}.abi3t.so"

        adapter_c.write_text(c_src, encoding="utf-8")

        noxc_cmd = [str(noxc_p), "build", "--backend", "llvm"]
        if release:
            noxc_cmd.append("--release")
        noxc_cmd.extend(
            [
                "--host-abi",
                "--module-name",
                cfg.leaf_name,
                "--emit-obj",
                "-o",
                str(nox_obj),
                str(cfg.source_path),
            ]
        )
        res_nox = subprocess.run(noxc_cmd, cwd=proj_root, capture_output=True, text=True)
        if res_nox.returncode != 0:
            raise NoxPyError(
                "NPY-E-CODEGEN",
                f"noxc native object compilation failed:\n{res_nox.stderr.strip() or res_nox.stdout.strip()}",
                location=DiagnosticLocation(file=str(cfg.source_path)),
            )

        cflags = [
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fPIC",
            "-fvisibility=hidden",
            f"-I{native_root / 'include'}",
            f"-I{nox_inc}",
            f"-I{py_inc}",
        ]
        if sys.platform == "darwin":
            cflags.append("-mmacosx-version-min=14.0")

        for src_c, out_o in (
            (adapter_c, adapter_o),
            (native_root / "cpython" / "noxpy_shim.c", shim_o),
        ):
            res_cc = subprocess.run([cc, *cflags, "-c", str(src_c), "-o", str(out_o)], capture_output=True, text=True)
            if res_cc.returncode != 0:
                raise NoxPyError(
                    "NPY-E-CODEGEN",
                    f"C compilation failed for {src_c.name}:\n{res_cc.stderr.strip() or res_cc.stdout.strip()}",
                )

        if sys.platform == "darwin":
            exp_file = work / "exports.txt"
            exp_file.write_text(f"_PyModExport_{cfg.leaf_name}\n", encoding="utf-8")
            link_cmd = [
                cc,
                "-mmacosx-version-min=14.0",
                "-bundle",
                "-undefined",
                "dynamic_lookup",
                "-Wl,-dead_strip",
                f"-Wl,-exported_symbols_list,{exp_file}",
                "-o",
                str(so_path),
                str(adapter_o),
                str(shim_o),
                str(nox_obj),
                str(noxrt_p),
                "-lm",
            ]
        else:
            ver_file = work / "exports.ver"
            ver_file.write_text(
                f"{{\n  global: PyModExport_{cfg.leaf_name};\n  local: *;\n}};\n",
                encoding="utf-8",
            )
            link_cmd = [
                cc,
                "-shared",
                "-Wl,--gc-sections",
                f"-Wl,--version-script,{ver_file}",
                "-o",
                str(so_path),
                str(adapter_o),
                str(shim_o),
                str(nox_obj),
                str(noxrt_p),
                "-lm",
                "-lpthread",
            ]

        res_ld = subprocess.run(link_cmd, capture_output=True, text=True)
        if res_ld.returncode != 0:
            raise NoxPyError(
                "NPY-E-ABI",
                f"Extension linking failed:\n{res_ld.stderr.strip() or res_ld.stdout.strip()}",
            )

        audit = audit_extension_binary(so_path, f"PyModExport_{cfg.leaf_name}")
        so_bytes = so_path.read_bytes()
        exported_fns = tuple(fn.name for fn in ir.functions)

    if use_cache:
        cache_root.mkdir(parents=True, exist_ok=True)
        tmp_cache = Path(tempfile.mkdtemp(prefix=f".{cache_key[:12]}-tmp-", dir=str(cache_root)))
        try:
            (tmp_cache / f"{cfg.leaf_name}.abi3t.so").write_bytes(so_bytes)
            if pyi_src is not None:
                (tmp_cache / f"{cfg.leaf_name}.pyi").write_text(pyi_src, encoding="utf-8")
            (tmp_cache / "meta.json").write_text(
                json.dumps(
                    {
                        "extension_sha256": audit.sha256,
                        "exported_functions": list(exported_fns),
                    }
                ),
                encoding="utf-8",
            )
            if cache_entry.exists():
                shutil.rmtree(cache_entry, ignore_errors=True)
            tmp_cache.replace(cache_entry)
        finally:
            if tmp_cache.exists():
                shutil.rmtree(tmp_cache, ignore_errors=True)

    return so_bytes, pyi_src, exported_fns, audit, noxc_ver, noxc_sha, noxrt_sha, False


def build_project_wheel(
    project_dir: str | Path,
    out_dir: str | Path,
    *,
    release: bool = True,
    use_cache: bool = True,
) -> BuildResult:
    """Compile a NoxPy project end-to-end and package an audited dual-tag `cp315-abi3.abi3t` wheel."""
    proj_root = Path(project_dir).resolve()
    pyproject_path = proj_root / "pyproject.toml"
    cfg = load_pyproject(pyproject_path)

    py_meta = tomllib.loads(pyproject_path.read_text(encoding="utf-8")).get("project") or {}
    summary = py_meta.get("description") if isinstance(py_meta.get("description"), str) else None

    if not cfg.source_path.is_file():
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"Nox source file not found: {cfg.source_path}",
            location=DiagnosticLocation(file=str(pyproject_path)),
        )

    so_bytes, pyi_src, exported_fns, audit, noxc_ver, noxc_sha, noxrt_sha, cache_hit = _compile_and_audit_extension(
        proj_root,
        cfg,
        release=release,
        use_cache=use_cache,
    )

    wheel_files: dict[str, tuple[bytes, int]] = {}
    pkg_prefix = "/".join(cfg.package_parts[:-1])
    ext_rel = f"{pkg_prefix}/{cfg.leaf_name}.abi3t.so" if pkg_prefix else f"{cfg.leaf_name}.abi3t.so"
    wheel_files[ext_rel] = (so_bytes, 0o755)

    if pyi_src is not None:
        pyi_rel = f"{pkg_prefix}/{cfg.leaf_name}.pyi" if pkg_prefix else f"{cfg.leaf_name}.pyi"
        wheel_files[pyi_rel] = (pyi_src.encode("utf-8"), 0o644)
        if pkg_prefix:
            wheel_files[f"{pkg_prefix}/py.typed"] = (b"", 0o644)

    if cfg.python_source_dir and cfg.python_source_dir.is_dir():
        for py_file in sorted(cfg.python_source_dir.rglob("*.py")):
            rel_py = py_file.relative_to(cfg.python_source_dir).as_posix()
            wheel_files[rel_py] = (py_file.read_bytes(), 0o644)

    lic = proj_root / "LICENSE"
    if lic.is_file():
        norm_dist = normalize_dist_name(cfg.distribution_name)
        wheel_files[f"{norm_dist}-{cfg.version}.dist-info/licenses/LICENSE"] = (lic.read_bytes(), 0o644)

    plat_tag = compute_platform_tag()
    wheel_path = write_deterministic_wheel(Path(out_dir).resolve(), cfg, wheel_files, plat_tag=plat_tag, summary=summary)

    return BuildResult(
        wheel_path=wheel_path,
        wheel_sha256=_sha256_file(wheel_path),
        tags=(f"cp315-abi3-{plat_tag}", f"cp315-abi3t-{plat_tag}"),
        module_name=cfg.module_name,
        audit=audit,
        noxc_version=noxc_ver,
        noxc_sha256=noxc_sha,
        noxrt_sha256=noxrt_sha,
        exported_functions=exported_fns,
        cache_hit=cache_hit,
    )


def build_project_editable_wheel(
    project_dir: str | Path,
    out_dir: str | Path,
    *,
    release: bool = False,
    use_cache: bool = True,
) -> BuildResult:
    """Build a PEP 660 editable wheel exposing pure-Python sources live and audited `.abi3t.so`."""
    proj_root = Path(project_dir).resolve()
    pyproject_path = proj_root / "pyproject.toml"
    cfg = load_pyproject(pyproject_path)

    py_meta = tomllib.loads(pyproject_path.read_text(encoding="utf-8")).get("project") or {}
    summary = py_meta.get("description") if isinstance(py_meta.get("description"), str) else None

    if not cfg.source_path.is_file():
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"Nox source file not found: {cfg.source_path}",
            location=DiagnosticLocation(file=str(pyproject_path)),
        )

    so_bytes, pyi_src, exported_fns, audit, noxc_ver, noxc_sha, noxrt_sha, cache_hit = _compile_and_audit_extension(
        proj_root,
        cfg,
        release=release,
        use_cache=use_cache,
    )

    pkg_prefix = "/".join(cfg.package_parts[:-1])
    if cfg.python_source_dir is not None:
        base_dir = cfg.python_source_dir.resolve()
        ext_dir = (base_dir / pkg_prefix) if pkg_prefix else base_dir
    else:
        base_dir = (proj_root / ".noxpy-cache" / "editable" / cfg.leaf_name).resolve()
        ext_dir = base_dir

    ext_dir.mkdir(parents=True, exist_ok=True)
    ext_file = ext_dir / f"{cfg.leaf_name}.abi3t.so"
    ext_file.write_bytes(so_bytes)
    os.chmod(ext_file, 0o755)

    if pyi_src is not None:
        (ext_dir / f"{cfg.leaf_name}.pyi").write_text(pyi_src, encoding="utf-8")
        if pkg_prefix:
            (ext_dir / "py.typed").write_bytes(b"")

    norm_dist = normalize_dist_name(cfg.distribution_name)
    pth_name = f"{norm_dist}_editable.pth"
    wheel_files: dict[str, tuple[bytes, int]] = {
        pth_name: ((str(base_dir) + "\n").encode("utf-8"), 0o644),
    }
    plat_tag = compute_platform_tag()
    wheel_path = write_deterministic_wheel(Path(out_dir).resolve(), cfg, wheel_files, plat_tag=plat_tag, summary=summary)

    return BuildResult(
        wheel_path=wheel_path,
        wheel_sha256=_sha256_file(wheel_path),
        tags=(f"cp315-abi3-{plat_tag}", f"cp315-abi3t-{plat_tag}"),
        module_name=cfg.module_name,
        audit=audit,
        noxc_version=noxc_ver,
        noxc_sha256=noxc_sha,
        noxrt_sha256=noxrt_sha,
        exported_functions=exported_fns,
        cache_hit=cache_hit,
    )


def build_project_sdist(project_dir: str | Path, out_dir: str | Path) -> Path:
    """Create a deterministic PEP 517 source distribution (`.tar.gz`)."""
    proj_root = Path(project_dir).resolve()
    pyproject_path = proj_root / "pyproject.toml"
    cfg = load_pyproject(pyproject_path)
    compute_lock_digest(proj_root)

    py_meta = tomllib.loads(pyproject_path.read_text(encoding="utf-8")).get("project") or {}
    summary = py_meta.get("description") if isinstance(py_meta.get("description"), str) else None

    norm_dist = normalize_dist_name(cfg.distribution_name)
    top_dir = f"{norm_dist}-{cfg.version}"
    out_p = Path(out_dir).resolve()
    out_p.mkdir(parents=True, exist_ok=True)
    sdist_path = out_p / f"{top_dir}.tar.gz"

    skip_dirs = {".git", ".noxpy-cache", "build", "dist", "__pycache__", ".venv", "venv"}
    skip_suffixes = (".pyc", ".pyo", ".o", ".so", ".dylib", ".dll", ".pyd", ".whl", ".tar.gz")

    entries: dict[str, tuple[bytes, int]] = {
        f"{top_dir}/PKG-INFO": (format_metadata_text(cfg, summary=summary).encode("utf-8"), 0o644),
    }

    for path in sorted(proj_root.rglob("*")):
        rel = path.relative_to(proj_root)
        if any(part in skip_dirs for part in rel.parts):
            continue
        if not path.is_file():
            continue
        if path.name.endswith(skip_suffixes) or path.name == ".DS_Store":
            continue
        mode = 0o755 if path.suffix == ".sh" else 0o644
        entries[f"{top_dir}/{rel.as_posix()}"] = (path.read_bytes(), mode)

    mtime = _source_date_epoch()
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".noxpy-sdist-", suffix=".tmp", dir=str(out_p))
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        with open(tmp_path, "wb") as raw_fh:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_fh, mtime=mtime) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf:
                    for arcname in sorted(entries):
                        data, mode = entries[arcname]
                        ti = tarfile.TarInfo(name=arcname)
                        ti.size = len(data)
                        ti.mtime = mtime
                        ti.mode = mode
                        ti.uid = 0
                        ti.gid = 0
                        ti.uname = ""
                        ti.gname = ""
                        tf.addfile(ti, io.BytesIO(data))
        tmp_path.replace(sdist_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return sdist_path


def develop_install(
    project_dir: str | Path,
    *,
    python_exe: str | None = None,
    release: bool = False,
) -> BuildResult:
    """Build extension wheel and install into an active virtualenv or explicit venv `--python`."""
    target_py = python_exe or sys.executable
    probe = subprocess.run(
        [target_py, "-c", "import sys; print(int(sys.prefix != sys.base_prefix), int(sys.version_info >= (3, 15)))"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise NoxPyError("NPY-E-CONFIG", f"Target Python executable failed probe: {target_py}")
    parts = probe.stdout.strip().split()
    in_venv = parts[0] == "1" if len(parts) >= 1 else False
    meets_315 = parts[1] == "1" if len(parts) >= 2 else False
    if not meets_315:
        raise NoxPyError("NPY-E-ABI", f"Target Python {target_py} does not satisfy >= 3.15")
    if not in_venv:
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"Refusing to install into non-virtualenv system Python: {target_py}",
            hint="Activate a virtual environment or pass `--python /path/to/venv/bin/python`.",
        )

    with tempfile.TemporaryDirectory(prefix="noxpy-dev-") as td:
        res = build_project_wheel(project_dir, td, release=release)
        uv = shutil.which("uv")
        if uv:
            inst_cmd = [uv, "pip", "install", "--python", target_py, "--force-reinstall", "--no-deps", str(res.wheel_path)]
        else:
            inst_cmd = [target_py, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(res.wheel_path)]
        inst = subprocess.run(inst_cmd, capture_output=True, text=True)
        if inst.returncode != 0:
            raise NoxPyError(
                "NPY-E-CONFIG",
                f"Failed to install wheel into {target_py}:\n{inst.stderr.strip() or inst.stdout.strip()}",
            )
    return res


def scaffold_project(name: str, target_dir: str | Path | None = None) -> Path:
    """Scaffold a minimal NoxPy extension project without overwriting existing files."""
    if not IDENT_RE.match(name) or not DIST_NAME_RE.match(name):
        raise NoxPyError("NPY-E-CONFIG", f"Invalid NoxPy project/module name: {name!r}")

    dest = (Path(target_dir) if target_dir else Path.cwd() / name).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise NoxPyError(
            "NPY-E-CONFIG",
            f"Refusing to overwrite non-empty target directory: {dest}",
        )

    (dest / "src").mkdir(parents=True, exist_ok=True)
    (dest / "tests").mkdir(parents=True, exist_ok=True)

    (dest / "pyproject.toml").write_text(
        "\n".join(
            [
                "[build-system]",
                'requires = ["noxpy>=0.1.0a1"]',
                'build-backend = "noxpy.build"',
                "",
                "[project]",
                f'name = "{name}"',
                'version = "0.1.0a1"',
                'requires-python = ">=3.15"',
                'description = "NoxPy native extension module"',
                "",
                "[tool.noxpy]",
                f'module-name = "{name}"',
                'source = "src/lib.nox"',
                'backend = "llvm"',
                'abi = "abi3t"',
                "free-threaded = true",
                "generate-stubs = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (dest / "src" / "lib.nox").write_text(
        "\n".join(
            [
                "@export",
                "def add(a: int, b: int) -> int:",
                '    """Add two signed 64-bit integers."""',
                "    return a + b",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (dest / "tests" / "test_basic.py").write_text(
        "\n".join(
            [
                f"import {name}",
                "",
                f"assert {name}.add(20, 22) == 42",
                f"assert {name}.add(b=22, a=20) == 42",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return dest


def init_project(project_dir: str | Path) -> list[str]:
    """Initialize NoxPy configuration in an existing directory without clobbering user files."""
    dest = Path(project_dir).resolve()
    if not dest.is_dir():
        raise NoxPyError("NPY-E-CONFIG", f"Target project directory does not exist: {dest}")

    raw_name = dest.name.replace("-", "_")
    mod_name = raw_name if IDENT_RE.match(raw_name) else "noxpy_ext"
    dist_name = dest.name if DIST_NAME_RE.match(dest.name) else "noxpy-ext"

    created: list[str] = []
    pyproject_p = dest / "pyproject.toml"
    if not pyproject_p.is_file():
        pyproject_p.write_text(
            "\n".join(
                [
                    "[build-system]",
                    'requires = ["noxpy>=0.1.0a1"]',
                    'build-backend = "noxpy.build"',
                    "",
                    "[project]",
                    f'name = "{dist_name}"',
                    'version = "0.1.0a1"',
                    'requires-python = ">=3.15"',
                    "",
                    "[tool.noxpy]",
                    f'module-name = "{mod_name}"',
                    'source = "src/lib.nox"',
                    'backend = "llvm"',
                    'abi = "abi3t"',
                    "free-threaded = true",
                    "generate-stubs = true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        created.append("pyproject.toml")
    else:
        existing_data = tomllib.loads(pyproject_p.read_text(encoding="utf-8"))
        if isinstance(existing_data.get("tool"), dict) and "noxpy" in existing_data["tool"]:
            raise NoxPyError("NPY-E-CONFIG", f"{pyproject_p} already contains [tool.noxpy]")
        append_text = "\n[tool.noxpy]\n" + "\n".join(
            [
                f'module-name = "{mod_name}"',
                'source = "src/lib.nox"',
                'backend = "llvm"',
                'abi = "abi3t"',
                "free-threaded = true",
                "generate-stubs = true",
                "",
            ]
        )
        pyproject_p.write_text(pyproject_p.read_text(encoding="utf-8").rstrip() + "\n" + append_text, encoding="utf-8")
        created.append("pyproject.toml (updated)")

    src_file = dest / "src" / "lib.nox"
    if not src_file.exists():
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text(
            "@export\ndef add(a: int, b: int) -> int:\n    return a + b\n",
            encoding="utf-8",
        )
        created.append("src/lib.nox")
    return created


def clean_project(project_dir: str | Path) -> list[str]:
    """Remove only project-owned NoxPy build/cache directories."""
    proj_root = Path(project_dir).resolve()
    if not proj_root.is_dir():
        raise NoxPyError("NPY-E-CONFIG", f"Project directory not found: {proj_root}")
    removed: list[str] = []
    for name in (".noxpy-cache", "build", "dist"):
        target = proj_root / name
        if target.exists() and target.is_dir():
            shutil.rmtree(target)
            removed.append(name)
    return removed


# --- PEP 517 / PEP 660 Build Backend Hooks ---


def get_requires_for_build_wheel(config_settings: dict[str, object] | None = None) -> list[str]:
    _ = config_settings
    return []


def get_requires_for_build_sdist(config_settings: dict[str, object] | None = None) -> list[str]:
    _ = config_settings
    return []


def get_requires_for_build_editable(config_settings: dict[str, object] | None = None) -> list[str]:
    _ = config_settings
    return []


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    _ = config_settings
    cfg = load_pyproject(Path.cwd() / "pyproject.toml")
    py_meta = tomllib.loads((Path.cwd() / "pyproject.toml").read_text(encoding="utf-8")).get("project") or {}
    summary = py_meta.get("description") if isinstance(py_meta.get("description"), str) else None

    norm_dist = normalize_dist_name(cfg.distribution_name)
    dist_info_name = f"{norm_dist}-{cfg.version}.dist-info"
    dist_dir = Path(metadata_directory) / dist_info_name
    dist_dir.mkdir(parents=True, exist_ok=True)
    plat_tag = compute_platform_tag()

    meta_bytes = format_metadata_text(cfg, summary=summary).encode("utf-8")
    wheel_bytes = format_wheel_info_text(plat_tag).encode("utf-8")
    (dist_dir / "METADATA").write_bytes(meta_bytes)
    (dist_dir / "WHEEL").write_bytes(wheel_bytes)
    (dist_dir / "RECORD").write_text(
        "\n".join(
            [
                f"{dist_info_name}/METADATA,{_record_hash_b64(meta_bytes)},{len(meta_bytes)}",
                f"{dist_info_name}/WHEEL,{_record_hash_b64(wheel_bytes)},{len(wheel_bytes)}",
                f"{dist_info_name}/RECORD,,",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return dist_info_name


def prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    return prepare_metadata_for_build_wheel(metadata_directory, config_settings=config_settings)


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _ = (config_settings, metadata_directory)
    res = build_project_wheel(Path.cwd(), Path(wheel_directory), release=True)
    return res.wheel_path.name


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    _ = config_settings
    sdist_path = build_project_sdist(Path.cwd(), Path(sdist_directory))
    return sdist_path.name


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _ = (config_settings, metadata_directory)
    res = build_project_editable_wheel(Path.cwd(), Path(wheel_directory), release=False)
    return res.wheel_path.name
