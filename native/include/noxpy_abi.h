#ifndef NOXPY_ABI_H
#define NOXPY_ABI_H

/*
 * Audited CPython 3.15+ dual-compatible abi3t core shim boundary.
 * See AGENTS.md §5, §8, §9, §10, §11.
 */

#ifndef Py_TARGET_ABI3T
#  define Py_TARGET_ABI3T 0x030F0000
#endif

#include <Python.h>
#include <stdatomic.h>
#include <stdint.h>

#include "nox_host_abi.h"

#if PY_VERSION_HEX < 0x030F0000
#  error "NoxPy requires CPython 3.15+ headers (PY_VERSION_HEX >= 0x030F0000)."
#endif

#ifndef _Py_OPAQUE_PYOBJECT
#  error "NoxPy stable path requires _Py_OPAQUE_PYOBJECT via Py_TARGET_ABI3T."
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef enum NoxPyModuleLifecycle {
    NOXPY_MOD_NEW = 0,
    NOXPY_MOD_INITIALIZING = 1,
    NOXPY_MOD_READY = 2,
    NOXPY_MOD_CLOSING = 3,
    NOXPY_MOD_CLOSED = 4
} NoxPyModuleLifecycle;

/*
 * Interpreter-local per-module state stored in CPython module state (`PyModule_GetState`).
 * Contains zero process-global invocation locks.
 */
typedef struct NoxPyModuleState {
    uint32_t struct_size;
    _Atomic int32_t lifecycle;
    _Atomic uint64_t active_calls;
    _Atomic uint64_t current_in_flight;
    _Atomic uint64_t peak_in_flight;
    const NoxHostRuntimeOps *runtime_ops;
    NoxHostContext *host_ctx;
} NoxPyModuleState;

/*
 * Initialize per-module state during Py_mod_exec.
 * Threading: called once per module instance under CPython import machinery.
 * Ownership: caller retains ownership of `ops` (static metadata); `host_ctx` (if created)
 * is owned by `st` until `noxpy_module_state_close`.
 * Returns 0 on success, -1 with Python exception set on failure.
 */
int noxpy_module_state_init(NoxPyModuleState *st, const NoxHostRuntimeOps *ops);

/*
 * Acquire an invocation lease while lifecycle == NOXPY_MOD_READY.
 * Threading: lock-free atomic CAS/fetch_add; safe from concurrent threads.
 * Returns 0 on success, -1 with RuntimeError set if module is closing/closed/unready.
 */
int noxpy_module_lease_acquire(NoxPyModuleState *st);

/*
 * Release a previously acquired invocation lease.
 */
void noxpy_module_lease_release(NoxPyModuleState *st);

/*
 * Transition module state to CLOSING, drain active_calls, destroy host_ctx, mark CLOSED.
 * Threading: safe during Py_mod_state_free; idempotent.
 */
void noxpy_module_state_close(NoxPyModuleState *st);

/*
 * Concurrency instrumentation helpers on per-module state (atomic max tracking).
 */
void noxpy_module_inflight_enter(NoxPyModuleState *st);
void noxpy_module_inflight_exit(NoxPyModuleState *st);
uint64_t noxpy_module_peak_in_flight(const NoxPyModuleState *st);
void noxpy_module_reset_peak_in_flight(NoxPyModuleState *st);

/*
 * Strict Python -> int64_t conversion.
 * Contract:
 *  - Requires an attached PyThreadState.
 *  - Rejects `bool` strictly with TypeError (AGENTS.md §8).
 *  - Rejects non-`int` objects (including objects with `__index__`) with TypeError.
 *  - Rejects integers outside [INT64_MIN, INT64_MAX] with OverflowError naming `arg_name`.
 *  - Borrows `obj`, never mutates reference counts on `obj`.
 * Returns 0 on success, -1 with Python exception set on failure.
 */
int noxpy_py_to_i64_strict(PyObject *obj, int64_t *out, const char *arg_name);

/*
 * Convert int64_t -> Python int (new strong reference, or NULL with exception set).
 * Requires an attached PyThreadState.
 */
PyObject *noxpy_i64_to_py(int64_t val);

/*
 * Strict Python -> bool conversion.
 * Rejects non-bool objects (including int 0/1) with TypeError.
 */
int noxpy_py_to_bool_strict(PyObject *obj, uint8_t *out, const char *arg_name);
PyObject *noxpy_bool_to_py(uint8_t val);

/*
 * Strict Python -> IEEE-754 double conversion.
 * Rejects non-float objects (including bool, int, and __float__ objects) with TypeError.
 */
int noxpy_py_to_f64_strict(PyObject *obj, double *out, const char *arg_name);
PyObject *noxpy_f64_to_py(double val);

/*
 * Strict Python str -> borrowed UTF-8 span (length-aware, preserves embedded NULs).
 * Rejects non-str objects with TypeError and lone surrogates via PyUnicode_AsUTF8AndSize.
 * Ownership: out->data is borrowed from `obj` for the lifetime of `obj` within the call;
 * do NOT retain past the call without copying.
 */
int noxpy_py_to_utf8_span_strict(PyObject *obj, NoxHostSpan *out, const char *arg_name);
PyObject *noxpy_utf8_span_to_py(NoxHostSpan span);

/*
 * Strict Python bytes -> borrowed byte span (length-aware, preserves embedded NULs).
 * Rejects non-bytes objects with TypeError.
 * Ownership: out->data is borrowed from immutable `obj` for the duration of the call.
 */
int noxpy_py_to_bytes_span_strict(PyObject *obj, NoxHostSpan *out, const char *arg_name);
PyObject *noxpy_bytes_span_to_py(NoxHostSpan span);

/*
 * Map a NoxHostStatus + optional NoxHostError to a Python exception on the attached thread.
 * Always leaves a Python exception set when called for a failure status.
 */
void noxpy_set_exception_from_host(NoxHostStatus status, const NoxHostError *err);

#ifdef __cplusplus
}
#endif

#endif /* NOXPY_ABI_H */
