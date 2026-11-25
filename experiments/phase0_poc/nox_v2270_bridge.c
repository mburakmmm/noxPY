#ifndef NOXPY_PHASE0_EXPERIMENTAL_SELFHOST_BRIDGE
#  error "nox_v2270_bridge.c is restricted to the gated Phase 0 PoC spike."
#endif

#include "nox_v2270_bridge.h"

#include <string.h>

/*
 * Upstream `v2.2.70` symbols:
 *  - Documented in include/nox_embed.h:
 *      nox_exception_pending, nox_exception_take, nox_exception_release
 *  - Private internal ABI / undocumented --selfhost-obj contract (UR-002, UR-004):
 *      nox_runtime_init, nox_runtime_deinit, nox_lib_init, nox_lib_fini
 *
 * Notice: NO private runtime struct layouts are dereferenced here; `rt` and
 * `exc` remain strictly opaque `void *`. Each call creates and destroys its
 * own isolated `RuntimeState`, enabling concurrent lock-free execution on
 * independent foreign OS threads.
 */
extern void *nox_runtime_init(void);
extern void nox_runtime_deinit(void *rt);
extern void nox_lib_init(void *rt);
extern void nox_lib_fini(void *rt);

extern int32_t nox_exception_pending(void *rt);
extern void *nox_exception_take(void *rt);
extern void nox_exception_release(void *rt, void *exception);

static NoxHostStatus v2270_ctx_create(const NoxHostContextConfig *cfg, NoxHostContext **out_ctx) {
    if (!cfg || !out_ctx || cfg->struct_size < sizeof(NoxHostContextConfig)) {
        return NOX_HOST_ERR_INVALID_ARG;
    }
    if (cfg->abi_version != NOX_HOST_ABI_VERSION) {
        return NOX_HOST_ERR_VERSION;
    }
    /* Per-call runtime isolation does not store a shared RuntimeState in host_ctx. */
    *out_ctx = (NoxHostContext *)(uintptr_t)0x1;
    return NOX_HOST_OK;
}

static void v2270_ctx_destroy(NoxHostContext *ctx) {
    (void)ctx;
}

static void v2270_error_clear(NoxHostError *err) {
    if (!err) {
        return;
    }
    if (err->release) {
        err->release(err->release_ctx, err);
    }
    memset(err, 0, sizeof(*err));
}

static const NoxHostRuntimeOps g_v2270_ops = {
    .struct_size = (uint32_t)sizeof(NoxHostRuntimeOps),
    .abi_version = NOX_HOST_ABI_VERSION,
    .capabilities = NOX_HOST_CAP_PARALLEL_CALLS | NOX_HOST_CAP_FOREIGN_THREADS,
    .context_create = v2270_ctx_create,
    .context_destroy = v2270_ctx_destroy,
    .error_clear = v2270_error_clear,
};

const NoxHostRuntimeOps *nox_v2270_runtime_ops(void) {
    return &g_v2270_ops;
}

static void populate_opaque_exception(NoxHostError *err_out) {
    if (!err_out) {
        return;
    }
    static const char msg[] =
        "Nox exception raised (class/message inspection blocked by UR-005)";
    memset(err_out, 0, sizeof(*err_out));
    err_out->struct_size = (uint32_t)sizeof(NoxHostError);
    err_out->code = NOX_HOST_ERR_EXCEPTION;
    err_out->class_name.data = (const uint8_t *)"NoxException";
    err_out->class_name.len = sizeof("NoxException") - 1;
    err_out->message.data = (const uint8_t *)msg;
    err_out->message.len = sizeof(msg) - 1;
    err_out->line = 0;
}

static NoxHostStatus finish_call(void *rt, NoxHostError *err_out) {
    if (nox_exception_pending(rt) != 0) {
        void *exc = nox_exception_take(rt);
        if (exc != NULL) {
            nox_exception_release(rt, exc);
        }
        populate_opaque_exception(err_out);
        nox_lib_fini(rt);
        nox_runtime_deinit(rt);
        return NOX_HOST_ERR_EXCEPTION;
    }
    nox_lib_fini(rt);
    nox_runtime_deinit(rt);
    return NOX_HOST_OK;
}

NoxHostStatus nox_v2270_call_1_i64(NoxRawFn1I64 fn, int64_t a, int64_t *out, NoxHostError *err_out) {
    if (!fn || !out) {
        return NOX_HOST_ERR_INVALID_ARG;
    }
    void *rt = nox_runtime_init();
    if (!rt) {
        return NOX_HOST_ERR_OOM;
    }
    nox_lib_init(rt);
    int64_t result = fn(rt, a);
    NoxHostStatus st = finish_call(rt, err_out);
    if (st == NOX_HOST_OK) {
        *out = result;
    }
    return st;
}

NoxHostStatus nox_v2270_call_2_i64(NoxRawFn2I64 fn, int64_t a, int64_t b, int64_t *out, NoxHostError *err_out) {
    if (!fn || !out) {
        return NOX_HOST_ERR_INVALID_ARG;
    }
    void *rt = nox_runtime_init();
    if (!rt) {
        return NOX_HOST_ERR_OOM;
    }
    nox_lib_init(rt);
    int64_t result = fn(rt, a, b);
    NoxHostStatus st = finish_call(rt, err_out);
    if (st == NOX_HOST_OK) {
        *out = result;
    }
    return st;
}
