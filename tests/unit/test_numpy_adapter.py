"""Unit tests for optional `noxpy.numpy_adapter` without requiring NumPy in core CI."""

from __future__ import annotations

import array
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from noxpy.numpy_adapter import as_ndarray, as_typed_input, writable_array  # noqa: E402


class TestNumPyAdapter(unittest.TestCase):
    def test_as_typed_input_validates_1d_contig_and_endian(self) -> None:
        arr = array.array("d", [1.0, 2.0, 3.0])
        self.assertIs(as_typed_input(arr), arr)

        mv = memoryview(arr)
        sliced = mv[::2]
        with self.assertRaises(ValueError):
            as_typed_input(sliced)
        sliced.release()
        mv.release()

        raw = bytearray(24)
        mat = memoryview(raw).cast("d", shape=(1, 3))
        with self.assertRaises(ValueError):
            as_typed_input(mat)
        mat.release()

    def test_requires_numpy_cleanly_or_interops_when_installed(self) -> None:
        has_np = importlib.util.find_spec("numpy") is not None
        arr = array.array("d", [1.0, 2.0, 3.0])
        if not has_np:
            with self.assertRaises(ImportError):
                as_ndarray(arr)
            with self.assertRaises(ImportError):
                with writable_array(arr):
                    pass
        else:
            np_arr = as_ndarray(arr)
            self.assertFalse(np_arr.flags.writeable)
            self.assertEqual(float(np_arr.sum()), 6.0)


if __name__ == "__main__":
    unittest.main()
