#!/usr/bin/env python3
"""Integration, strict conversion, exception containment, spans, concurrency, and teardown tests for Phase 0."""

from __future__ import annotations

import os
import subprocess
import sys
import threading


class Indexable:
    def __index__(self) -> int:
        return 42


def assert_raises(exc_type: type[BaseException], fn, *args) -> BaseException:
    try:
        fn(*args)
    except exc_type as exc:
        return exc
    raise AssertionError(f"Expected {exc_type.__name__} from {fn.__name__}{args}, but it succeeded")


def test_basic_and_multi_module() -> None:
    import _noxpy_poc
    import _noxpy_poc_b

    assert _noxpy_poc.add(20, 22) == 42
    assert _noxpy_poc.add(-100, 58) == -42
    assert _noxpy_poc.add((1 << 62), (1 << 62) - 1) == (1 << 63) - 1
    assert _noxpy_poc.add(-(1 << 63), 0) == -(1 << 63)
    assert _noxpy_poc.fib(0) == 0
    assert _noxpy_poc.fib(1) == 1
    assert _noxpy_poc.fib(30) == 832040
    assert _noxpy_poc.alloc_checksum(1000) == 499500
    assert _noxpy_poc.checked_div(42, 6) == 7

    s = "hello\x00ğüşiöç🚀world"
    assert _noxpy_poc.echo_str(s) == s
    assert len(_noxpy_poc.echo_str("a\x00b")) == 3
    raw = b"\x00\x01\xffnox\x00end"
    assert _noxpy_poc.echo_bytes(raw) == raw

    assert _noxpy_poc_b.mul(6, 7) == 42
    assert _noxpy_poc_b.sub(100, 58) == 42
    print("[PASS] basic arithmetic, int64 boundaries, fib, alloc_checksum, binary-safe str/bytes, dual-module")


def test_strict_conversions() -> None:
    import _noxpy_poc

    assert_raises(TypeError, _noxpy_poc.add, True, 1)
    assert_raises(TypeError, _noxpy_poc.add, 1, False)
    assert_raises(TypeError, _noxpy_poc.add, 1.5, 2)
    assert_raises(TypeError, _noxpy_poc.add, "20", 22)
    assert_raises(TypeError, _noxpy_poc.add, None, 22)
    assert_raises(TypeError, _noxpy_poc.add, Indexable(), 1)
    assert_raises(TypeError, _noxpy_poc.add, 1)
    assert_raises(TypeError, _noxpy_poc.add, 1, 2, 3)
    assert_raises(OverflowError, _noxpy_poc.add, 1 << 63, 1)
    assert_raises(OverflowError, _noxpy_poc.add, -(1 << 63) - 1, 0)
    assert_raises(TypeError, _noxpy_poc.echo_str, b"bytes")
    assert_raises(UnicodeEncodeError, _noxpy_poc.echo_str, "\ud800")
    assert_raises(TypeError, _noxpy_poc.echo_bytes, "str")
    print("[PASS] strict int/bool/str/bytes/no-__index__/overflow/arity rejections")


def test_exception_containment() -> None:
    import _noxpy_poc

    exc = assert_raises(ValueError, _noxpy_poc.checked_div, 42, 0)
    assert "ValueError" in str(exc) and "division by zero" in str(exc), str(exc)
    exc2 = assert_raises(ValueError, _noxpy_poc.fib, -1)
    assert "ValueError" in str(exc2) and "negative fibonacci index" in str(exc2), str(exc2)
    assert _noxpy_poc.add(20, 22) == 42
    print("[PASS] Nox ValueError inspection & containment without process abort or leak")


def test_concurrency() -> None:
    import _noxpy_poc
    import _noxpy_poc_b

    for n_threads in (1, 2, 4, 8):
        _noxpy_poc.reset_peak_in_flight()
        barrier = threading.Barrier(n_threads)
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker(tid: int) -> None:
            try:
                barrier.wait(timeout=5.0)
                for i in range(15):
                    assert _noxpy_poc.add(tid, 40) == tid + 40
                    w = _noxpy_poc.work_loop(250_000, tid + i)
                    assert isinstance(w, int)
                    assert _noxpy_poc.alloc_checksum(200) == 19900
                    assert _noxpy_poc.echo_str(f"t{tid}\x00{i}") == f"t{tid}\x00{i}"
                    assert _noxpy_poc.echo_bytes(bytes([0, tid, i & 0xFF])) == bytes([0, tid, i & 0xFF])
                    assert _noxpy_poc_b.mul(tid + 2, 7) == (tid + 2) * 7
                    assert _noxpy_poc_b.sub(100, tid) == 100 - tid
                    try:
                        _noxpy_poc.checked_div(10, 0)
                        raise AssertionError("expected ValueError")
                    except ValueError:
                        pass
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
            assert not t.is_alive(), "worker thread timed out"

        assert not errors, f"thread errors for n={n_threads}: {errors}"
        peak = _noxpy_poc.peak_in_flight()
        if n_threads >= 2:
            assert peak >= 2, f"expected parallel overlap >= 2 for n={n_threads}, got {peak}"
        print(f"[PASS] concurrency threads={n_threads} peak_in_flight={peak}")


def test_subprocess_teardown() -> None:
    code = """
import threading, _noxpy_poc, _noxpy_poc_b
b = threading.Barrier(4)
errs = []
def w(i):
    try:
        b.wait(5)
        assert _noxpy_poc.add(20, 22) == 42
        assert _noxpy_poc.echo_str("hi\\x00nox") == "hi\\x00nox"
        assert _noxpy_poc.alloc_checksum(100) == 4950
        assert _noxpy_poc_b.mul(6, 7) == 42
    except BaseException as e:
        errs.append(e)
ts = [threading.Thread(target=w, args=(i,)) for i in range(4)]
for t in ts:
    t.start()
for t in ts:
    t.join()
assert not errs, errs
"""
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"subprocess failed: rc={res.returncode} stderr={res.stderr}"
    assert res.stderr.strip() == "", f"unexpected stderr on teardown (possible leak report): {res.stderr}"
    print("[PASS] clean subprocess multi-module teardown with zero stderr leak output")


def main() -> int:
    assert sys.version_info >= (3, 15), f"expected Python >= 3.15, got {sys.version}"
    print(f"Python: {sys.version.splitlines()[0]} | gil_enabled={sys._is_gil_enabled()}")
    test_basic_and_multi_module()
    test_strict_conversions()
    test_exception_containment()
    test_concurrency()
    test_subprocess_teardown()
    print("ALL PHASE 0 INTEGRATION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
