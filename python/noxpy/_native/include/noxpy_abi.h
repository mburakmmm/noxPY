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
#include <stddef.h>
#include <stdint.h>

#include "nox_host.h"

#if PY_VERSION_HEX < 0x030F0000
#  error "NoxPy requires CPython 3.15+ headers (PY_VERSION_HEX >= 0x030F0000)."
#endif

#ifndef _Py_OPAQUE_PYOBJECT
#  error "NoxPy stable path requires _Py_OPAQUE_PYOBJECT via Py_TARGET_ABI3T."
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define NOXPY_CTX_POOL_CAP 64

typedef enum NoxPyModuleLifecycle {
    NOXPY_MOD_NEW = 0,
    NOXPY_MOD_INITIALIZING = 1,
    NOXPY_MOD_READY = 2,
    NOXPY_MOD_CLOSING = 3,
    NOXPY_MOD_CLOSED = 4
} NoxPyModuleLifecycle;

/*
 * Per-extension function pointer table binding module-local Nox runtime symbols
 * into the shared shim logic without process-global symbol leakage.
 */
typedef struct NoxPyHostOps {
    uint32_t struct_size;
    uint32_t abi_version;
    NoxHostStatus (*context_create)(const NoxHostContextConfig *config, NoxHostContext **out);
    void (*context_destroy)(NoxHostContext *ctx);
    NoxHostStatus (*module_init)(NoxHostContext *ctx, NoxHostError **err);
    void (*module_fini)(NoxHostContext *ctx);
    void (*buffer_release)(NoxHostBuffer *buf);
    void (*error_release)(NoxHostError *err);
    int64_t (*error_line)(const NoxHostError *err);
    NoxHostStatus (*error_class_name)(const NoxHostError *err, NoxHostSpan *out);
    NoxHostStatus (*error_message)(const NoxHostError *err, NoxHostSpan *out);
} NoxPyHostOps;

/*
 * Interpreter-local per-module state stored in CPython module state (`PyModule_GetState`).
 * Contains zero process-global invocation locks; caches up to NOXPY_CTX_POOL_CAP independent
 * `NoxHostContext` instances so concurrent 3.15t threads execute without serialization.
 */
typedef struct NoxPyModuleState {
    uint32_t struct_size;
    _Atomic int32_t lifecycle;
    _Atomic uint64_t active_calls;
    _Atomic uint64_t current_in_flight;
    _Atomic uint64_t peak_in_flight;
    const NoxPyHostOps *host_ops;
    atomic_flag pool_lock;
    size_t pool_count;
    NoxHostContext *pool[NOXPY_CTX_POOL_CAP];
} NoxPyModuleState;

/*
 * Initialize per-module state during Py_mod_exec and warm one NoxHostContext.
 * Returns 0 on success, -1 with Python exception set on failure.
 */
int noxpy_module_state_init(NoxPyModuleState *st, const NoxPyHostOps *ops);

/*
 * Acquire an invocation lease while lifecycle == NOXPY_MOD_READY.
 * Lock-free atomic check-and-increment; returns 0 on success, -1 with RuntimeError on failure.
 */
int noxpy_module_lease_acquire(NoxPyModuleState *st);
void noxpy_module_lease_release(NoxPyModuleState *st);

/*
 * Acquire an initialized per-thread NoxHostContext from the module pool (or create one outside locks).
 * Must be paired with noxpy_module_ctx_release(st, ctx). Returns 0 on success, -1 with exception set.
 */
int noxpy_module_ctx_acquire(NoxPyModuleState *st, NoxHostContext **out_ctx);
void noxpy_module_ctx_release(NoxPyModuleState *st, NoxHostContext *ctx);

/*
 * Transition module state to CLOSING, drain active_calls, destroy all cached contexts, mark CLOSED.
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
 * Strict Python <-> Nox primitive converters (AGENTS.md §8).
 */
int noxpy_py_to_i64_strict(PyObject *obj, int64_t *out, const char *arg_name);
PyObject *noxpy_i64_to_py(int64_t val);

int noxpy_py_to_bool_strict(PyObject *obj, uint8_t *out, const char *arg_name);
PyObject *noxpy_bool_to_py(uint8_t val);

int noxpy_py_to_f64_strict(PyObject *obj, double *out, const char *arg_name);
PyObject *noxpy_f64_to_py(double val);

int noxpy_py_to_utf8_span_strict(PyObject *obj, NoxHostSpan *out, const char *arg_name);
PyObject *noxpy_utf8_span_to_py(NoxHostSpan span);
PyObject *noxpy_utf8_buffer_to_py(const NoxPyHostOps *ops, NoxHostBuffer *buf);

int noxpy_py_to_bytes_span_strict(PyObject *obj, NoxHostSpan *out, const char *arg_name);
PyObject *noxpy_bytes_span_to_py(NoxHostSpan span);
PyObject *noxpy_bytes_buffer_to_py(const NoxPyHostOps *ops, NoxHostBuffer *buf);

/*
 * Map NoxHostStatus + optional NoxHostError to a Python exception and ALWAYS release `err`.
 * Must be called while the owning `ctx` is still alive.
 */
void noxpy_set_exception_from_host(const NoxPyHostOps *ops, NoxHostStatus status, NoxHostError *err);

#define NOXPY_PARAM_POS_ONLY 0u
#define NOXPY_PARAM_POS_OR_KW 1u
#define NOXPY_PARAM_KW_ONLY 2u

typedef struct NoxPyParamSpec {
    const char *name;
    uint8_t kind;
} NoxPyParamSpec;

/*
 * Bind METH_FASTCALL | METH_KEYWORDS positional and keyword arguments into `bound_out`.
 * Enforces positional-only (`/`), positional-or-keyword, and keyword-only (`*`) constraints
 * with borrowed strong-for-call `PyObject*` references. Returns 0 on success, -1 on TypeError.
 */
int noxpy_bind_fastcall_kwargs(
    const char *fn_name,
    const NoxPyParamSpec *specs,
    Py_ssize_t nparams,
    PyObject *const *args,
    Py_ssize_t nargs,
    PyObject *kwnames,
    PyObject **bound_out);

#ifdef __cplusplus
}
#endif

#endif /* NOXPY_ABI_H */
