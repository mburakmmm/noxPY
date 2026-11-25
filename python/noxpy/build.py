"""Minimal PEP 517 build backend, native abi3t wheel packager, `develop`, and `new` (`AGENTS.md` §15)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
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
    plat = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    return plat


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _record_hash_b64(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _source_date_tuple() -> tuple[int, int, int, int, int, int]:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw:
        ts = max(int(raw), 315532800)  # ZIP format minimum year 1980
        gm = time.gmtime(ts)
        return (gm.tm_year, gm.tm_mon, gm.tm_mday, gm.tm_hour, gm.tm_min, gm.tm_sec)
    return (2026, 1, 1, 0, 0, 0)


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
            hint="Install nox-lang (>= 2.2.78) or set NOXC=/path/to/noxc.",
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
                zi.create_system = 3  # Unix permissions
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


def build_project_wheel(
    project_dir: str | Path,
    out_dir: str | Path,
    *,
    release: bool = True,
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

    native_root = _find_native_root()
    noxc_p, noxrt_p, nox_inc, noxc_ver, noxc_sha, noxrt_sha = _find_nox_toolchain()
    cc = _find_c_compiler()
    py_inc = _find_python_abi3t_include()

    ir = inspect_module_ir(proj_root, cfg, noxc_path=noxc_p)
    c_src = generate_c_adapter(ir)
    pyi_src = generate_pyi(ir) if cfg.generate_stubs else None

    if sys.platform not in ("darwin",) and not sys.platform.startswith("linux"):
        raise NoxPyError("NPY-E-CONFIG", f"Phase 1 wheel linking not yet implemented on {sys.platform}")

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

        wheel_files: dict[str, tuple[bytes, int]] = {}
        pkg_prefix = "/".join(cfg.package_parts[:-1])
        ext_rel = f"{pkg_prefix}/{cfg.leaf_name}.abi3t.so" if pkg_prefix else f"{cfg.leaf_name}.abi3t.so"
        wheel_files[ext_rel] = (so_path.read_bytes(), 0o755)

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
        exported_functions=tuple(fn.name for fn in ir.functions),
    )


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
    """Scaffold a minimal Phase 1 NoxPy extension project without overwriting existing files."""
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


# --- PEP 517 Build Backend Hooks ---


def get_requires_for_build_wheel(config_settings: dict[str, object] | None = None) -> list[str]:
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


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _ = (config_settings, metadata_directory)
    res = build_project_wheel(Path.cwd(), Path(wheel_directory), release=True)
    return res.wheel_path.name
