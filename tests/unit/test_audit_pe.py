"""Unit tests for pure-Python PE32+ Windows `.pyd` ABI auditor (`noxpy.audit._audit_windows`)."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.audit import _audit_windows  # noqa: E402
from noxpy.errors import DiagnosticLocation, NoxPyError  # noqa: E402


def _build_synthetic_pe32plus(
    *,
    exports: list[str],
    imports: dict[str, list[str]],
) -> bytes:
    """Build a minimal valid PE32+ image with 1 `.rdata` section (RVA == file offset)."""
    # Layout:
    # 0x000..0x040: DOS header (e_lfanew = 0x80)
    # 0x080..0x188: PE sig + COFF(20) + Optional(240) + Section(40)
    # 0x200..     : .rdata section (VAddr=0x200, RawPtr=0x200)
    rdata = bytearray()
    base_rva = 0x200

    def _add_cstr(s: str) -> int:
        rva = base_rva + len(rdata)
        rdata.extend(s.encode("ascii") + b"\x00")
        return rva

    # Export directory (40 bytes)
    exp_rva = base_rva + len(rdata)
    rdata.extend(b"\x00" * 40)
    names_tbl_rva = base_rva + len(rdata)
    rdata.extend(b"\x00" * (4 * len(exports)))
    exp_name_rvas = [_add_cstr(name) for name in exports]
    for i, nrva in enumerate(exp_name_rvas):
        struct.pack_into("<I", rdata, (names_tbl_rva - base_rva) + i * 4, nrva)
    struct.pack_into("<II", rdata, exp_rva - base_rva + 20, len(exports), len(exports))
    struct.pack_into("<I", rdata, exp_rva - base_rva + 32, names_tbl_rva)
    exp_size = (base_rva + len(rdata)) - exp_rva

    # Align to 8 bytes
    while len(rdata) % 8:
        rdata.append(0)

    # Import descriptors ((len(imports) + 1) * 20 bytes)
    imp_rva = base_rva + len(rdata)
    descs_off = len(rdata)
    rdata.extend(b"\x00" * ((len(imports) + 1) * 20))
    for idx, (dll_name, syms) in enumerate(imports.items()):
        dll_rva = _add_cstr(dll_name)
        while len(rdata) % 8:
            rdata.append(0)
        ilt_rva = base_rva + len(rdata)
        ilt_off = len(rdata)
        rdata.extend(b"\x00" * ((len(syms) + 1) * 8))
        for j, sym in enumerate(syms):
            while len(rdata) % 2:
                rdata.append(0)
            hn_rva = base_rva + len(rdata)
            rdata.extend(b"\x00\x00" + sym.encode("ascii") + b"\x00")
            struct.pack_into("<Q", rdata, ilt_off + j * 8, hn_rva)
        struct.pack_into("<IIIII", rdata, descs_off + idx * 20, ilt_rva, 0, 0, dll_rva, ilt_rva)
    imp_size = (base_rva + len(rdata)) - imp_rva

    hdr = bytearray(0x200)
    hdr[0:2] = b"MZ"
    struct.pack_into("<I", hdr, 0x3C, 0x80)
    hdr[0x80:0x84] = b"PE\x00\x00"
    # COFF: AMD64 (0x8664), 1 section, OptionalHeader size 240
    struct.pack_into("<HHIIIHH", hdr, 0x84, 0x8664, 1, 0, 0, 0, 240, 0x2022)
    opt = 0x84 + 20
    struct.pack_into("<H", hdr, opt, 0x20B)  # PE32+
    struct.pack_into("<I", hdr, opt + 108, 16)  # NumberOfRvaAndSizes
    struct.pack_into("<IIII", hdr, opt + 112, exp_rva, exp_size, imp_rva, imp_size)
    sec = opt + 240
    hdr[sec : sec + 6] = b".rdata"
    struct.pack_into("<IIII", hdr, sec + 8, len(rdata), base_rva, len(rdata), base_rva)
    return bytes(hdr) + bytes(rdata)


class TestWindowsPEAudit(unittest.TestCase):
    def test_valid_abi3t_pyd_passes(self) -> None:
        pe = _build_synthetic_pe32plus(
            exports=["PyModExport_hello"],
            imports={"python3.dll": ["PyABIInfo_Check", "PyLong_FromInt64"], "KERNEL32.dll": ["GetCurrentThreadId"]},
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "hello.pyd"
            p.write_bytes(pe)
            exps, psyms, deps = _audit_windows(p, "PyModExport_hello", DiagnosticLocation(file=str(p)))
            self.assertEqual(exps, ["PyModExport_hello"])
            self.assertEqual(psyms, ["PyABIInfo_Check", "PyLong_FromInt64"])
            self.assertIn("python3.dll", deps)

    def test_rejects_versioned_python_dll(self) -> None:
        pe = _build_synthetic_pe32plus(
            exports=["PyModExport_hello"],
            imports={"python315t.dll": ["PyLong_FromInt64"]},
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "hello.pyd"
            p.write_bytes(pe)
            with self.assertRaises(NoxPyError):
                _audit_windows(p, "PyModExport_hello", DiagnosticLocation(file=str(p)))


if __name__ == "__main__":
    unittest.main()
