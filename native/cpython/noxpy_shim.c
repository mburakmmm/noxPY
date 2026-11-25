#include "noxpy_abi.h"

#include <limits.h>
#include <sched.h>
#include <string.h>

static int span_eq_cstr(NoxHostSpan span, const char *lit) {
    if (!span.data || !lit) {
        return 0;
    }
    size_t lit_len = strlen(lit);
    return span.len == lit_len && memcmp(span.data, lit, lit_len) == 0;
}

int noxpy_module_state_init(NoxPyModuleState *st, const NoxHostRuntimeOps *ops) {
    if (!st) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL module state");
        return -1;
    }
    if (!ops || ops->struct_size < sizeof(NoxHostRuntimeOps) || ops->abi_version != NOX_HOST_ABI_VERSION) {
        PyErr_SetString(PyExc_RuntimeError, "noxpy: incompatible Nox host ABI ops table");
        return -1;
    }

    st->struct_size = (uint32_t)sizeof(NoxPyModuleState);
    atomic_store_explicit(&st->lifecycle, NOXPY_MOD_INITIALIZING, memory_order_release);
    atomic_store_explicit(&st->active_calls, 0, memory_order_relaxed);
    atomic_store_explicit(&st->current_in_flight, 0, memory_order_relaxed);
    atomic_store_explicit(&st->peak_in_flight, 0, memory_order_relaxed);
    st->runtime_ops = ops;
    st->host_ctx = NULL;

    if (ops->context_create) {
        NoxHostContextConfig cfg = {
            .struct_size = (uint32_t)sizeof(NoxHostContextConfig),
            .abi_version = NOX_HOST_ABI_VERSION,
            .flags = 0,
        };
        NoxHostStatus rc = ops->context_create(&cfg, &st->host_ctx);
        if (rc != NOX_HOST_OK) {
            atomic_store_explicit(&st->lifecycle, NOXPY_MOD_CLOSED, memory_order_release);
            noxpy_set_exception_from_host(rc, NULL);
            return -1;
        }
    }

    atomic_store_explicit(&st->lifecycle, NOXPY_MOD_READY, memory_order_release);
    return 0;
}

int noxpy_module_lease_acquire(NoxPyModuleState *st) {
    if (!st) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL module state");
        return -1;
    }
    int32_t lc = atomic_load_explicit(&st->lifecycle, memory_order_acquire);
    if (lc != NOXPY_MOD_READY) {
        PyErr_SetString(PyExc_RuntimeError, "noxpy: module is not ready (closing or closed)");
        return -1;
    }

    atomic_fetch_add_explicit(&st->active_calls, 1, memory_order_acq_rel);
    /* Re-check lifecycle after incrementing active_calls to close the check-then-act window. */
    lc = atomic_load_explicit(&st->lifecycle, memory_order_acquire);
    if (lc != NOXPY_MOD_READY) {
        atomic_fetch_sub_explicit(&st->active_calls, 1, memory_order_release);
        PyErr_SetString(PyExc_RuntimeError, "noxpy: module entered shutdown concurrently");
        return -1;
    }
    return 0;
}

void noxpy_module_lease_release(NoxPyModuleState *st) {
    if (!st) {
        return;
    }
    atomic_fetch_sub_explicit(&st->active_calls, 1, memory_order_release);
}

void noxpy_module_state_close(NoxPyModuleState *st) {
    if (!st || st->struct_size != sizeof(NoxPyModuleState)) {
        return;
    }
    int32_t prev = atomic_exchange_explicit(&st->lifecycle, NOXPY_MOD_CLOSING, memory_order_acq_rel);
    if (prev == NOXPY_MOD_CLOSED || prev == NOXPY_MOD_NEW) {
        atomic_store_explicit(&st->lifecycle, NOXPY_MOD_CLOSED, memory_order_release);
        return;
    }

    while (atomic_load_explicit(&st->active_calls, memory_order_acquire) > 0) {
        sched_yield();
    }

    if (st->runtime_ops && st->runtime_ops->context_destroy && st->host_ctx) {
        st->runtime_ops->context_destroy(st->host_ctx);
        st->host_ctx = NULL;
    }

    atomic_store_explicit(&st->lifecycle, NOXPY_MOD_CLOSED, memory_order_release);
}

void noxpy_module_inflight_enter(NoxPyModuleState *st) {
    uint64_t now = atomic_fetch_add_explicit(&st->current_in_flight, 1, memory_order_acq_rel) + 1;
    uint64_t peak = atomic_load_explicit(&st->peak_in_flight, memory_order_relaxed);
    while (now > peak) {
        if (atomic_compare_exchange_weak_explicit(
                &st->peak_in_flight, &peak, now, memory_order_release, memory_order_relaxed)) {
            break;
        }
    }
}

void noxpy_module_inflight_exit(NoxPyModuleState *st) {
    atomic_fetch_sub_explicit(&st->current_in_flight, 1, memory_order_release);
}

uint64_t noxpy_module_peak_in_flight(const NoxPyModuleState *st) {
    return atomic_load_explicit(&((NoxPyModuleState *)st)->peak_in_flight, memory_order_acquire);
}

void noxpy_module_reset_peak_in_flight(NoxPyModuleState *st) {
    atomic_store_explicit(&st->peak_in_flight, 0, memory_order_release);
}

int noxpy_py_to_i64_strict(PyObject *obj, int64_t *out, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null conversion argument");
        return -1;
    }
    if (PyBool_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be int, not bool", label);
        return -1;
    }
    if (!PyLong_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be int", label);
        return -1;
    }
    int64_t v = 0;
    if (PyLong_AsInt64(obj, &v) < 0) {
        if (PyErr_ExceptionMatches(PyExc_OverflowError)) {
            PyErr_Format(PyExc_OverflowError, "argument '%s' overflows 64-bit signed int", label);
        }
        return -1;
    }
    *out = v;
    return 0;
}

PyObject *noxpy_i64_to_py(int64_t val) {
    return PyLong_FromInt64(val);
}

int noxpy_py_to_bool_strict(PyObject *obj, uint8_t *out, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null conversion argument");
        return -1;
    }
    if (!PyBool_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be bool", label);
        return -1;
    }
    *out = Py_IsTrue(obj) ? 1u : 0u;
    return 0;
}

PyObject *noxpy_bool_to_py(uint8_t val) {
    return PyBool_FromLong(val ? 1L : 0L);
}

int noxpy_py_to_f64_strict(PyObject *obj, double *out, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null conversion argument");
        return -1;
    }
    if (!PyFloat_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be float", label);
        return -1;
    }
    double v = PyFloat_AsDouble(obj);
    if (v == -1.0 && PyErr_Occurred()) {
        return -1;
    }
    *out = v;
    return 0;
}

PyObject *noxpy_f64_to_py(double val) {
    return PyFloat_FromDouble(val);
}

int noxpy_py_to_utf8_span_strict(PyObject *obj, NoxHostSpan *out, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null conversion argument");
        return -1;
    }
    if (!PyUnicode_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be str", label);
        return -1;
    }
    Py_ssize_t sz = 0;
    const char *utf8 = PyUnicode_AsUTF8AndSize(obj, &sz);
    if (!utf8 || sz < 0) {
        return -1;
    }
    out->data = (const uint8_t *)utf8;
    out->len = (size_t)sz;
    return 0;
}

PyObject *noxpy_utf8_span_to_py(NoxHostSpan span) {
    if (span.len > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "noxpy: string length exceeds PY_SSIZE_T_MAX");
        return NULL;
    }
    if (span.len > 0 && !span.data) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL data pointer with non-zero span length");
        return NULL;
    }
    const char *ptr = span.data ? (const char *)span.data : "";
    return PyUnicode_DecodeUTF8(ptr, (Py_ssize_t)span.len, "strict");
}

int noxpy_py_to_bytes_span_strict(PyObject *obj, NoxHostSpan *out, const char *arg_name) {
    const char *label = arg_name ? arg_name : "?";
    if (!obj || !out) {
        PyErr_SetString(PyExc_SystemError, "noxpy: invalid null conversion argument");
        return -1;
    }
    if (!PyBytes_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "argument '%s' must be bytes", label);
        return -1;
    }
    char *buf = NULL;
    Py_ssize_t sz = 0;
    if (PyBytes_AsStringAndSize(obj, &buf, &sz) < 0) {
        return -1;
    }
    if (sz < 0) {
        PyErr_SetString(PyExc_SystemError, "noxpy: negative bytes length");
        return -1;
    }
    out->data = (const uint8_t *)buf;
    out->len = (size_t)sz;
    return 0;
}

PyObject *noxpy_bytes_span_to_py(NoxHostSpan span) {
    if (span.len > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "noxpy: bytes length exceeds PY_SSIZE_T_MAX");
        return NULL;
    }
    if (span.len > 0 && !span.data) {
        PyErr_SetString(PyExc_SystemError, "noxpy: NULL data pointer with non-zero span length");
        return NULL;
    }
    const char *ptr = span.data ? (const char *)span.data : "";
    return PyBytes_FromStringAndSize(ptr, (Py_ssize_t)span.len);
}

static PyObject *pick_exc_type(NoxHostStatus status, const NoxHostError *err) {
    if (err && err->class_name.data && err->class_name.len > 0) {
        if (span_eq_cstr(err->class_name, "ValueError")) return PyExc_ValueError;
        if (span_eq_cstr(err->class_name, "TypeError")) return PyExc_TypeError;
        if (span_eq_cstr(err->class_name, "OverflowError")) return PyExc_OverflowError;
        if (span_eq_cstr(err->class_name, "MemoryError")) return PyExc_MemoryError;
        if (span_eq_cstr(err->class_name, "IndexError")) return PyExc_IndexError;
        if (span_eq_cstr(err->class_name, "KeyError")) return PyExc_KeyError;
        if (span_eq_cstr(err->class_name, "ZeroDivisionError")) return PyExc_ZeroDivisionError;
    }
    switch (status) {
        case NOX_HOST_ERR_OVERFLOW:
            return PyExc_OverflowError;
        case NOX_HOST_ERR_OOM:
            return PyExc_MemoryError;
        case NOX_HOST_ERR_INVALID_ARG:
            return PyExc_ValueError;
        case NOX_HOST_ERR_UNSUPPORTED:
            return PyExc_NotImplementedError;
        case NOX_HOST_ERR_VERSION:
        case NOX_HOST_ERR_STATE:
        case NOX_HOST_ERR_EXCEPTION:
        case NOX_HOST_ERR_INTERNAL:
        default:
            return PyExc_RuntimeError;
    }
}

void noxpy_set_exception_from_host(NoxHostStatus status, const NoxHostError *err) {
    PyObject *exc_type = pick_exc_type(status, err);
    if (err && err->message.data && err->message.len > 0) {
        int msg_len = err->message.len > (size_t)INT_MAX ? INT_MAX : (int)err->message.len;
        if (err->class_name.data && err->class_name.len > 0) {
            int cls_len = err->class_name.len > (size_t)INT_MAX ? INT_MAX : (int)err->class_name.len;
            PyErr_Format(
                exc_type,
                "%.*s: %.*s (line %lld)",
                cls_len,
                (const char *)err->class_name.data,
                msg_len,
                (const char *)err->message.data,
                (long long)err->line);
        } else {
            PyErr_Format(exc_type, "%.*s", msg_len, (const char *)err->message.data);
        }
        return;
    }
    if (err && err->class_name.data && err->class_name.len > 0) {
        int cls_len = err->class_name.len > (size_t)INT_MAX ? INT_MAX : (int)err->class_name.len;
        PyErr_Format(
            exc_type,
            "Nox exception '%.*s' (line %lld)",
            cls_len,
            (const char *)err->class_name.data,
            (long long)err->line);
        return;
    }
    PyErr_Format(exc_type, "Nox host error status=%d", (int)status);
}
