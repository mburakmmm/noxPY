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
#include <pthread.h>
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
    /* ---- ABI v2 (UR-008) value-tree operations ---- */
    NoxHostStatus (*value_none)(NoxHostContext *ctx, NoxHostValue *out);
    NoxHostStatus (*value_int)(NoxHostContext *ctx, int64_t v, NoxHostValue *out);
    NoxHostStatus (*value_float)(NoxHostContext *ctx, double v, NoxHostValue *out);
    NoxHostStatus (*value_bool)(NoxHostContext *ctx, uint8_t v, NoxHostValue *out);
    NoxHostStatus (*value_str)(
        NoxHostContext *ctx, const uint8_t *data, size_t len, int32_t is_bytes, NoxHostValue *out);
    NoxHostStatus (*value_container)(NoxHostContext *ctx, int32_t kind, NoxHostValue *out);
    NoxHostStatus (*value_append)(NoxHostContext *ctx, NoxHostValue container, NoxHostValue item);
    NoxHostStatus (*value_dict_set)(
        NoxHostContext *ctx, NoxHostValue dict, NoxHostValue key, NoxHostValue value);
    NoxHostStatus (*value_kind)(NoxHostContext *ctx, NoxHostValue v, int32_t *out);
    NoxHostStatus (*value_get_int)(NoxHostContext *ctx, NoxHostValue v, int64_t *out);
    NoxHostStatus (*value_get_float)(NoxHostContext *ctx, NoxHostValue v, double *out);
    NoxHostStatus (*value_get_span)(NoxHostContext *ctx, NoxHostValue v, NoxHostSpan *out);
    NoxHostStatus (*value_len)(NoxHostContext *ctx, NoxHostValue v, size_t *out);
    NoxHostStatus (*value_item)(NoxHostContext *ctx, NoxHostValue v, size_t index, NoxHostValue *out);
    NoxHostStatus (*value_dict_entry)(
        NoxHostContext *ctx, NoxHostValue v, size_t index, NoxHostValue *key_out, NoxHostValue *value_out);
    NoxHostStatus (*value_release)(NoxHostContext *ctx, NoxHostValue v);
    /* ---- ABI v1/v2 handle operations (UR-006, Phase 3) ---- */
    NoxHostStatus (*handle_retain)(NoxHostContext *ctx, NoxHostHandle h, NoxHostHandle *out);
    NoxHostStatus (*handle_release)(NoxHostContext *ctx, NoxHostHandle h);
    int32_t (*handle_is_valid)(NoxHostContext *ctx, NoxHostHandle h);
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
    uint8_t has_default;
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

/*
 * Phase 2 bounded container/optional snapshot converters with cycle detection & depth limits.
 */
#define NOXPY_MAX_CONV_DEPTH 32u
#define NOXPY_DEFAULT_MAX_ITEMS 1000000u

typedef struct NoxPyConvGuard {
    size_t depth;
    size_t max_depth;
    size_t items;
    size_t max_items;
    const void *visited[NOXPY_MAX_CONV_DEPTH];
} NoxPyConvGuard;

void noxpy_conv_guard_init(NoxPyConvGuard *g, size_t max_depth, size_t max_items);
int noxpy_conv_guard_enter(NoxPyConvGuard *g, const void *container_ptr, const char *arg_name);
void noxpy_conv_guard_leave(NoxPyConvGuard *g);
int noxpy_conv_guard_account(NoxPyConvGuard *g, size_t add_items, const char *arg_name);

int noxpy_py_to_opt_i64_strict(PyObject *obj, int *has_value, int64_t *out, const char *arg_name);
PyObject *noxpy_opt_i64_to_py(int has_value, int64_t val);

int noxpy_py_validate_acyclic_tree(PyObject *obj, NoxPyConvGuard *g, const char *arg_name);
int noxpy_py_list_i64_snapshot_strict(
    PyObject *obj, NoxPyConvGuard *g, int64_t **out_buf, size_t *out_len, const char *arg_name);
PyObject *noxpy_i64_array_to_py_list(const int64_t *buf, size_t len);
void noxpy_free_buffer(void *p);
PyObject *noxpy_py_dict_str_i64_roundtrip_strict(PyObject *obj, NoxPyConvGuard *g, const char *arg_name);

/*
 * ABI v2 (UR-008) recursive NoxHostValue <-> PyObject marshalers.
 */
int noxpy_py_to_host_value(
    const NoxPyHostOps *ops,
    NoxHostContext *ctx,
    PyObject *obj,
    const char *type_spec,
    NoxHostValue *out_val,
    const char *arg_name);
PyObject *noxpy_host_value_to_py(
    const NoxPyHostOps *ops,
    NoxHostContext *ctx,
    NoxHostValue val);

/*
 * Phase 3 (0.3) Heap-Type Object Data & Thread-Policy Handle Runtime (`ADR-0005`).
 */
#define NOXPY_POLICY_PARALLEL 0u
#define NOXPY_POLICY_INSTANCE_LOCKED 1u
#define NOXPY_POLICY_THREAD_AFFINE 2u

typedef struct NoxPyObjectData {
    PyObject *owner_module;
    const NoxPyHostOps *host_ops;
    NoxHostContext *owner_ctx;
    NoxHostHandle handle;
    uint64_t type_tag;
    uint64_t instance_gen;
    uint64_t owner_tid;
    uint8_t thread_policy;
    uint8_t closed;
    uint8_t initialized;
    uint8_t lock_ready;
    pthread_mutex_t lock;
} NoxPyObjectData;

uint64_t noxpy_current_tid(void);
int noxpy_host_context_create_and_init(const NoxPyHostOps *ops, NoxHostContext **out_ctx);
void noxpy_host_context_fini_and_destroy(const NoxPyHostOps *ops, NoxHostContext *ctx);

int noxpy_obj_init(
    NoxPyObjectData *data,
    PyObject *owner_module,
    const NoxPyHostOps *ops,
    NoxHostContext *owner_ctx,
    NoxHostHandle handle,
    uint64_t type_tag,
    uint8_t thread_policy);
int noxpy_obj_enter_call(
    NoxPyObjectData *data,
    uint64_t expected_type_tag,
    NoxHostContext **out_ctx,
    NoxHostHandle *out_handle);
void noxpy_obj_leave_call(NoxPyObjectData *data);
int noxpy_obj_is_closed(NoxPyObjectData *data);
void noxpy_obj_close(NoxPyObjectData *data);

int noxpy_obj_traverse(PyObject *self, visitproc visit, void *arg);
int noxpy_obj_clear(PyObject *self);
void noxpy_obj_dealloc(PyObject *self);
PyObject *noxpy_obj_enter_cm(PyObject *self, PyObject *ignored);
PyObject *noxpy_obj_exit_cm(PyObject *self, PyObject *const *args, Py_ssize_t nargs);
PyObject *noxpy_obj_close_method(PyObject *self, PyObject *ignored);

#ifdef __cplusplus
}
#endif

#endif /* NOXPY_ABI_H */
