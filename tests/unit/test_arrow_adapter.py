"""Unit tests for optional Arrow C Data & C Stream interface adapter (`noxpy.arrow_adapter`)."""

from __future__ import annotations

import array
import ctypes
import gc
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.arrow_adapter import (  # noqa: E402
    ArrowArray,
    ArrowArrayStream,
    ArrowSchema,
    export_arrow_array,
    export_arrow_stream,
    import_arrow_array,
    import_arrow_stream,
)


class TestArrowAdapter(unittest.TestCase):
    def test_arrow_abi_struct_sizes(self) -> None:
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(ArrowSchema), 72)
            self.assertEqual(ctypes.sizeof(ArrowArray), 80)
            self.assertEqual(ctypes.sizeof(ArrowArrayStream), 40)

    def test_zero_copy_roundtrip_all_supported_dtypes_and_idempotent_close(self) -> None:
        cases = [
            ("d", [1.5, -2.25, 3.75, 4.0], "g"),
            ("q", [10, -20, 30, 1 << 40], "l"),
            ("i", [1, -2, 3, 4], "i"),
            ("B", [0, 127, 255, 42], "C"),
        ]
        for fmt, vals, expected_arrow_fmt in cases:
            with self.subTest(fmt=fmt):
                src = array.array(fmt, vals)
                src_addr, _ = src.buffer_info()
                exp = export_arrow_array(src, name=f"col_{fmt}")
                self.assertEqual(exp.arrow_format, expected_arrow_fmt)
                self.assertEqual(exp.length, len(vals))

                imp = import_arrow_array(exp)
                self.assertEqual(imp.name, f"col_{fmt}")
                self.assertEqual(imp.arrow_format, expected_arrow_fmt)
                self.assertEqual(imp.data_address, src_addr)

                mv = imp.memoryview()
                self.assertTrue(mv.readonly)
                self.assertEqual(list(mv), vals)
                # Closing while a memoryview backs it MUST fail with BufferError
                with self.assertRaises(BufferError):
                    imp.close()
                mv.release()
                del mv

                imp.close()
                imp.close()  # idempotent
                self.assertTrue(imp.is_closed)
                with self.assertRaises(ValueError):
                    imp.memoryview()

    def test_offset_slicing_and_rejection_contracts(self) -> None:
        src = array.array("q", [100, 200, 300, 400, 500])
        base_addr, _ = src.buffer_info()
        bufs = (ctypes.c_void_p * 2)(None, base_addr)

        released_flags = {"schema": 0, "array": 0}
        s_rel_t = ctypes.CFUNCTYPE(None, ctypes.POINTER(ArrowSchema))
        a_rel_t = ctypes.CFUNCTYPE(None, ctypes.POINTER(ArrowArray))

        @s_rel_t
        def s_rel(sp):
            released_flags["schema"] += 1
            sp.contents.release = None

        @a_rel_t
        def a_rel(ap):
            released_flags["array"] += 1
            ap.contents.release = None

        sc = ArrowSchema(
            format=b"l",
            name=b"sliced",
            n_children=0,
            release=ctypes.cast(s_rel, ctypes.c_void_p).value,
        )
        ar = ArrowArray(
            length=3,
            null_count=0,
            offset=2,
            n_buffers=2,
            buffers=ctypes.cast(bufs, ctypes.POINTER(ctypes.c_void_p)),
            release=ctypes.cast(a_rel, ctypes.c_void_p).value,
        )
        imp = ImportedArrowBufferDirect(sc, ar)
        mv = imp.memoryview()
        self.assertEqual(list(mv), [300, 400, 500])
        mv.release()
        imp.close()
        self.assertEqual(released_flags, {"schema": 1, "array": 1})

        # Non-zero null_count MUST be rejected
        sc2 = ArrowSchema(format=b"l", release=ctypes.cast(s_rel, ctypes.c_void_p).value)
        ar2 = ArrowArray(
            length=2,
            null_count=1,
            offset=0,
            n_buffers=2,
            buffers=ctypes.cast(bufs, ctypes.POINTER(ctypes.c_void_p)),
            release=ctypes.cast(a_rel, ctypes.c_void_p).value,
        )
        with self.assertRaises(ValueError):
            ImportedArrowBufferDirect(sc2, ar2)
        self.assertEqual(released_flags, {"schema": 2, "array": 2})

    def test_unconsumed_capsule_dtor_and_consumed_capsule_rejection(self) -> None:
        raw = bytearray(b"abcd")
        exp = export_arrow_array(raw)
        caps = exp.__arrow_c_array__()
        # Exported bytearray fails to resize while pinned by capsule
        with self.assertRaises(BufferError):
            raw.append(65)

        imp = import_arrow_array(caps)
        # Re-importing consumed capsules must fail cleanly
        with self.assertRaises(TypeError):
            import_arrow_array(caps)
        imp.close()
        # Now bytearray is unpinned and resizable again
        raw.append(65)
        self.assertEqual(bytes(raw), b"abcdA")

        # Drop an unconsumed capsule pair -> destructor must unpin cleanly
        caps2 = exp.__arrow_c_array__()
        with self.assertRaises(BufferError):
            raw.append(66)
        del caps2
        gc.collect()
        raw.append(66)
        self.assertEqual(bytes(raw), b"abcdAB")

    def test_arrow_stream_chunks_and_dtype_validation(self) -> None:
        c1 = array.array("d", [1.0, 2.0])
        c2 = array.array("d", [3.0, 4.0, 5.0])
        stream = export_arrow_stream([c1, c2], name="vals")
        self.assertEqual(stream.chunk_count, 2)
        imported = import_arrow_stream(stream)
        self.assertEqual(len(imported), 2)
        values = []
        for ch in imported:
            mv = ch.memoryview()
            values.extend(mv)
            mv.release()
            ch.close()
        self.assertEqual(values, [1.0, 2.0, 3.0, 4.0, 5.0])

        # Mismatched chunk dtypes must fail loudly
        bad_q = array.array("q", [1, 2])
        with self.assertRaises(ValueError):
            export_arrow_stream([c1, bad_q])

        # Non-contiguous 2D buffer must fail cleanly
        mv2d = memoryview(bytearray(12)).cast("B", shape=(3, 4))[::2]
        with self.assertRaises(ValueError):
            export_arrow_array(mv2d)


def ImportedArrowBufferDirect(sc: ArrowSchema, ar: ArrowArray):
    from noxpy.arrow_adapter import ImportedArrowBuffer

    return ImportedArrowBuffer(sc, ar)


if __name__ == "__main__":
    unittest.main()
