#include <assert.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nox_host.h"

/*
 * Standalone C host test against upstream `include/nox_host.h` (`v1`).
 * Uses ONLY documented `nox_host_*` symbols resolved via dlopen/dlsym
 * from two independently built `--emit-shared-lib --host-abi` dylibs.
 */

typedef uint32_t (*fn_abi_ver)(void);
typedef NoxHostStatus (*fn_ctx_create)(const NoxHostContextConfig *, NoxHostContext **);
typedef void (*fn_ctx_destroy)(NoxHostContext *);
typedef void (*fn_buf_release)(NoxHostBuffer *);
typedef NoxHostStatus (*fn_handle_release)(NoxHostContext *, NoxHostHandle);
typedef NoxHostStatus (*fn_handle_retain)(NoxHostContext *, NoxHostHandle, NoxHostHandle *);
typedef int32_t (*fn_handle_valid)(NoxHostContext *, NoxHostHandle);
typedef void (*fn_err_release)(NoxHostError *);
typedef int64_t (*fn_err_line)(const NoxHostError *);
typedef NoxHostStatus (*fn_err_span)(const NoxHostError *, NoxHostSpan *);

typedef NoxHostStatus (*fn_mod_init)(NoxHostContext *, NoxHostError **);
typedef void (*fn_mod_fini)(NoxHostContext *);
typedef NoxHostStatus (*fn_2i64)(NoxHostContext *, int64_t, int64_t, int64_t *, NoxHostError **);
typedef NoxHostStatus (*fn_1i64)(NoxHostContext *, int64_t, int64_t *, NoxHostError **);
typedef NoxHostStatus (*fn_bytes_io)(NoxHostContext *, const uint8_t *, size_t, NoxHostBuffer *, NoxHostError **);
typedef NoxHostStatus (*fn_handle_new)(NoxHostContext *, int64_t, NoxHostHandle *, NoxHostError **);
typedef NoxHostStatus (*fn_counter_inc)(NoxHostContext *, NoxHostHandle, int64_t, int64_t *, NoxHostError **);
typedef NoxHostStatus (*fn_box_get)(NoxHostContext *, NoxHostHandle, int64_t *, NoxHostError **);

typedef struct HostLibA {
    fn_abi_ver abi_version;
    fn_ctx_create ctx_create;
    fn_ctx_destroy ctx_destroy;
    fn_buf_release buf_release;
    fn_handle_release handle_release;
    fn_handle_retain handle_retain;
    fn_handle_valid handle_is_valid;
    fn_err_release err_release;
    fn_err_line err_line;
    fn_err_span err_class_name;
    fn_err_span err_message;
    fn_mod_init mod_init;
    fn_mod_fini mod_fini;
    fn_2i64 add;
    fn_1i64 fib;
    fn_2i64 work_loop;
    fn_2i64 checked_div;
    fn_1i64 alloc_checksum;
    fn_bytes_io echo_str;
    fn_bytes_io echo_bytes;
    fn_handle_new counter_new;
    fn_counter_inc counter_inc;
} HostLibA;

typedef struct HostLibB {
    fn_ctx_create ctx_create;
    fn_ctx_destroy ctx_destroy;
    fn_mod_init mod_init;
    fn_mod_fini mod_fini;
    fn_2i64 mul;
    fn_2i64 sub;
    fn_handle_new box_new;
    fn_box_get box_get;
} HostLibB;

typedef struct ThreadCtx {
    const HostLibA *a;
    const HostLibB *b;
    int thread_id;
    int num_threads;
    pthread_mutex_t *mu;
    pthread_cond_t *cv;
    int *arrived;
    _Atomic int64_t *in_flight;
    _Atomic int64_t *max_in_flight;
    _Atomic int *failures;
} ThreadCtx;

static void record_peak(_Atomic int64_t *max_v, int64_t val) {
    int64_t prev = atomic_load_explicit(max_v, memory_order_relaxed);
    while (val > prev) {
        if (atomic_compare_exchange_weak_explicit(
                max_v, &prev, val, memory_order_release, memory_order_relaxed)) {
            break;
        }
    }
}

static void *worker_main(void *arg) {
    ThreadCtx *tc = (ThreadCtx *)arg;

    pthread_mutex_lock(tc->mu);
    (*tc->arrived)++;
    if (*tc->arrived == tc->num_threads) {
        pthread_cond_broadcast(tc->cv);
    } else {
        while (*tc->arrived < tc->num_threads) {
            pthread_cond_wait(tc->cv, tc->mu);
        }
    }
    pthread_mutex_unlock(tc->mu);

    NoxHostContextConfig cfg = {
        .size = (uint32_t)sizeof(NoxHostContextConfig),
        .abi_version = NOX_HOST_ABI_VERSION,
        .allocator = NULL,
    };
    NoxHostContext *ctx_a = NULL;
    NoxHostContext *ctx_b = NULL;
    NoxHostError *err = NULL;

    if (tc->a->ctx_create(&cfg, &ctx_a) != NOX_HOST_OK || !ctx_a ||
        tc->b->ctx_create(&cfg, &ctx_b) != NOX_HOST_OK || !ctx_b) {
        atomic_fetch_add(tc->failures, 1);
        return NULL;
    }
    if (tc->a->mod_init(ctx_a, &err) != NOX_HOST_OK || err != NULL ||
        tc->b->mod_init(ctx_b, &err) != NOX_HOST_OK || err != NULL) {
        atomic_fetch_add(tc->failures, 1);
        return NULL;
    }

    for (int iter = 0; iter < 10; iter++) {
        int64_t cur = atomic_fetch_add_explicit(tc->in_flight, 1, memory_order_acq_rel) + 1;
        record_peak(tc->max_in_flight, cur);

        int64_t out = 0;
        err = NULL;
        if (tc->a->add(ctx_a, 20 + tc->thread_id, 22, &out, &err) != NOX_HOST_OK ||
            out != 42 + tc->thread_id || err != NULL) {
            atomic_fetch_add(tc->failures, 1);
        }
        if (tc->a->work_loop(ctx_a, 200000, tc->thread_id + iter, &out, &err) != NOX_HOST_OK) {
            atomic_fetch_add(tc->failures, 1);
        }
        if (tc->a->alloc_checksum(ctx_a, 200, &out, &err) != NOX_HOST_OK || out != 19900) {
            atomic_fetch_add(tc->failures, 1);
        }
        if (tc->a->checked_div(ctx_a, 42, 0, &out, &err) != NOX_HOST_ERR_EXCEPTION || !err) {
            atomic_fetch_add(tc->failures, 1);
        } else {
            tc->a->err_release(err);
            err = NULL;
        }
        if (tc->b->mul(ctx_b, 6 + tc->thread_id, 7, &out, &err) != NOX_HOST_OK ||
            out != (6 + tc->thread_id) * 7) {
            atomic_fetch_add(tc->failures, 1);
        }
        if (tc->b->sub(ctx_b, 100, tc->thread_id, &out, &err) != NOX_HOST_OK ||
            out != 100 - tc->thread_id) {
            atomic_fetch_add(tc->failures, 1);
        }

        atomic_fetch_sub_explicit(tc->in_flight, 1, memory_order_release);
    }

    tc->a->mod_fini(ctx_a);
    tc->a->ctx_destroy(ctx_a);
    tc->b->mod_fini(ctx_b);
    tc->b->ctx_destroy(ctx_b);
    return NULL;
}

static void *churn_worker(void *arg) {
    const HostLibA *a = (const HostLibA *)arg;
    NoxHostContextConfig cfg = {
        .size = (uint32_t)sizeof(NoxHostContextConfig),
        .abi_version = NOX_HOST_ABI_VERSION,
        .allocator = NULL,
    };
    NoxHostContext *ctx = NULL;
    NoxHostError *err = NULL;
    assert(a->ctx_create(&cfg, &ctx) == NOX_HOST_OK && ctx != NULL);
    assert(a->mod_init(ctx, &err) == NOX_HOST_OK && err == NULL);
    int64_t out = 0;
    assert(a->add(ctx, 19, 23, &out, &err) == NOX_HOST_OK && out == 42);
    a->mod_fini(ctx);
    a->ctx_destroy(ctx);
    return NULL;
}

static int run_thread_round(const HostLibA *a, const HostLibB *b, int n_threads) {
    pthread_t threads[8];
    ThreadCtx ctxs[8];
    pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
    pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
    int arrived = 0;
    _Atomic int64_t in_flight = 0;
    _Atomic int64_t max_in_flight = 0;
    _Atomic int failures = 0;

    for (int i = 0; i < n_threads; i++) {
        ctxs[i].a = a;
        ctxs[i].b = b;
        ctxs[i].thread_id = i;
        ctxs[i].num_threads = n_threads;
        ctxs[i].mu = &mu;
        ctxs[i].cv = &cv;
        ctxs[i].arrived = &arrived;
        ctxs[i].in_flight = &in_flight;
        ctxs[i].max_in_flight = &max_in_flight;
        ctxs[i].failures = &failures;
        if (pthread_create(&threads[i], NULL, worker_main, &ctxs[i]) != 0) {
            fprintf(stderr, "pthread_create failed\n");
            return 1;
        }
    }
    for (int i = 0; i < n_threads; i++) {
        pthread_join(threads[i], NULL);
    }
    pthread_mutex_destroy(&mu);
    pthread_cond_destroy(&cv);

    int64_t peak = atomic_load(&max_in_flight);
    int fails = atomic_load(&failures);
    printf("c_host_test: threads=%d peak_in_flight=%lld failures=%d\n", n_threads, (long long)peak, fails);
    if (fails != 0) {
        return 1;
    }
    if (n_threads >= 2 && peak < 2) {
        fprintf(stderr, "ERROR: expected concurrent overlap peak >= 2 for threads=%d, got %lld\n",
                n_threads, (long long)peak);
        return 1;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <libpoc_a.dylib> <libpoc_b.dylib>\n", argv[0]);
        return 2;
    }

    void *ha = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!ha) {
        fprintf(stderr, "dlopen(%s) failed: %s\n", argv[1], dlerror());
        return 1;
    }
    void *hb = dlopen(argv[2], RTLD_NOW | RTLD_LOCAL);
    if (!hb) {
        fprintf(stderr, "dlopen(%s) failed: %s\n", argv[2], dlerror());
        dlclose(ha);
        return 1;
    }

    HostLibA a = {
        .abi_version = (fn_abi_ver)dlsym(ha, "nox_host_abi_version"),
        .ctx_create = (fn_ctx_create)dlsym(ha, "nox_host_context_create"),
        .ctx_destroy = (fn_ctx_destroy)dlsym(ha, "nox_host_context_destroy"),
        .buf_release = (fn_buf_release)dlsym(ha, "nox_host_buffer_release"),
        .handle_release = (fn_handle_release)dlsym(ha, "nox_host_handle_release"),
        .handle_retain = (fn_handle_retain)dlsym(ha, "nox_host_handle_retain"),
        .handle_is_valid = (fn_handle_valid)dlsym(ha, "nox_host_handle_is_valid"),
        .err_release = (fn_err_release)dlsym(ha, "nox_host_error_release"),
        .err_line = (fn_err_line)dlsym(ha, "nox_host_error_line"),
        .err_class_name = (fn_err_span)dlsym(ha, "nox_host_error_class_name"),
        .err_message = (fn_err_span)dlsym(ha, "nox_host_error_message"),
        .mod_init = (fn_mod_init)dlsym(ha, "nox_host_module_init_poc_a"),
        .mod_fini = (fn_mod_fini)dlsym(ha, "nox_host_module_fini_poc_a"),
        .add = (fn_2i64)dlsym(ha, "nox_host_export_poc_a_add"),
        .fib = (fn_1i64)dlsym(ha, "nox_host_export_poc_a_fib"),
        .work_loop = (fn_2i64)dlsym(ha, "nox_host_export_poc_a_work_loop"),
        .checked_div = (fn_2i64)dlsym(ha, "nox_host_export_poc_a_checked_div"),
        .alloc_checksum = (fn_1i64)dlsym(ha, "nox_host_export_poc_a_alloc_checksum"),
        .echo_str = (fn_bytes_io)dlsym(ha, "nox_host_export_poc_a_echo_str"),
        .echo_bytes = (fn_bytes_io)dlsym(ha, "nox_host_export_poc_a_echo_bytes"),
        .counter_new = (fn_handle_new)dlsym(ha, "nox_host_export_poc_a_counter_new"),
        .counter_inc = (fn_counter_inc)dlsym(ha, "nox_host_export_poc_a_counter_inc"),
    };
    HostLibB b = {
        .ctx_create = (fn_ctx_create)dlsym(hb, "nox_host_context_create"),
        .ctx_destroy = (fn_ctx_destroy)dlsym(hb, "nox_host_context_destroy"),
        .mod_init = (fn_mod_init)dlsym(hb, "nox_host_module_init_poc_b"),
        .mod_fini = (fn_mod_fini)dlsym(hb, "nox_host_module_fini_poc_b"),
        .mul = (fn_2i64)dlsym(hb, "nox_host_export_poc_b_mul"),
        .sub = (fn_2i64)dlsym(hb, "nox_host_export_poc_b_sub"),
        .box_new = (fn_handle_new)dlsym(hb, "nox_host_export_poc_b_box_new"),
        .box_get = (fn_box_get)dlsym(hb, "nox_host_export_poc_b_box_get"),
    };

    assert(a.abi_version && a.ctx_create && a.mod_init && a.add && a.echo_str && a.echo_bytes && a.counter_new && a.counter_inc);
    assert(b.ctx_create && b.mod_init && b.mul && b.sub && b.box_new && b.box_get);
    assert(a.abi_version() == NOX_HOST_ABI_VERSION);

    NoxHostContextConfig cfg = {
        .size = (uint32_t)sizeof(NoxHostContextConfig),
        .abi_version = NOX_HOST_ABI_VERSION,
        .allocator = NULL,
    };
    NoxHostContext *ctx_a = NULL, *ctx_a2 = NULL, *ctx_b = NULL;
    NoxHostError *err = NULL;
    assert(a.ctx_create(&cfg, &ctx_a) == NOX_HOST_OK);
    assert(a.ctx_create(&cfg, &ctx_a2) == NOX_HOST_OK);
    assert(b.ctx_create(&cfg, &ctx_b) == NOX_HOST_OK);
    assert(a.mod_init(ctx_a, &err) == NOX_HOST_OK && err == NULL);
    assert(a.mod_init(ctx_a2, &err) == NOX_HOST_OK && err == NULL);
    assert(b.mod_init(ctx_b, &err) == NOX_HOST_OK && err == NULL);

    /* 1. Arithmetic & dual-module verification */
    int64_t out = 0;
    assert(a.add(ctx_a, 20, 22, &out, &err) == NOX_HOST_OK && out == 42);
    assert(a.fib(ctx_a, 30, &out, &err) == NOX_HOST_OK && out == 832040);
    assert(a.alloc_checksum(ctx_a, 1000, &out, &err) == NOX_HOST_OK && out == 499500);
    assert(a.checked_div(ctx_a, 42, 6, &out, &err) == NOX_HOST_OK && out == 7);
    assert(b.mul(ctx_b, 6, 7, &out, &err) == NOX_HOST_OK && out == 42);
    assert(b.sub(ctx_b, 50, 8, &out, &err) == NOX_HOST_OK && out == 42);

    /* 2. Binary-safe UTF-8 str & bytes spans (UR-006) + UTF-8 validation */
    const uint8_t nul_str[] = {'a', '\0', 'b'};
    NoxHostBuffer buf = {0};
    assert(a.echo_str(ctx_a, nul_str, sizeof(nul_str), &buf, &err) == NOX_HOST_OK && err == NULL);
    assert(buf.len == 3 && memcmp(buf.data, nul_str, 3) == 0);
    a.buf_release(&buf);
    assert(buf.data == NULL && buf.len == 0);

    const uint8_t raw_bytes[] = {0x00, 0x01, 0xff, 'n', 'o', 'x'};
    assert(a.echo_bytes(ctx_a, raw_bytes, sizeof(raw_bytes), &buf, &err) == NOX_HOST_OK && err == NULL);
    assert(buf.len == sizeof(raw_bytes) && memcmp(buf.data, raw_bytes, sizeof(raw_bytes)) == 0);
    a.buf_release(&buf);

    const uint8_t bad_utf8[] = {0xff, 0xfe};
    assert(a.echo_str(ctx_a, bad_utf8, sizeof(bad_utf8), &buf, &err) == NOX_HOST_ERR_UTF8);
    assert(err == NULL);

    /* 3. Exception containment & public inspection (UR-005) */
    assert(a.checked_div(ctx_a, 42, 0, &out, &err) == NOX_HOST_ERR_EXCEPTION && err != NULL);
    NoxHostSpan cls = {0}, msg = {0};
    assert(a.err_class_name(err, &cls) == NOX_HOST_OK);
    assert(a.err_message(err, &msg) == NOX_HOST_OK);
    assert(cls.len == strlen("ValueError") && memcmp(cls.data, "ValueError", cls.len) == 0);
    assert(msg.len == strlen("division by zero") && memcmp(msg.data, "division by zero", msg.len) == 0);
    assert(a.err_line(err) > 0);
    a.err_release(err);
    err = NULL;

    /* 4. Opaque generation-checked & cross-context/cross-module handles (UR-006, UR-003) */
    NoxHostHandle h1 = 0, h2 = 0, hbox = 0;
    assert(a.counter_new(ctx_a, 10, &h1, &err) == NOX_HOST_OK && h1 != 0);
    assert(a.handle_is_valid(ctx_a, h1) == 1);
    assert(a.counter_inc(ctx_a, h1, 5, &out, &err) == NOX_HOST_OK && out == 15);
    assert(a.handle_retain(ctx_a, h1, &h2) == NOX_HOST_OK && h2 != 0 && h2 != h1);
    assert(a.counter_inc(ctx_a, h2, 7, &out, &err) == NOX_HOST_OK && out == 22);
    assert(a.handle_release(ctx_a, h1) == NOX_HOST_OK);
    assert(a.handle_is_valid(ctx_a, h1) == 0);
    assert(a.counter_inc(ctx_a, h1, 1, &out, &err) == NOX_HOST_ERR_INVALID_HANDLE);
    assert(a.counter_inc(ctx_a2, h2, 1, &out, &err) == NOX_HOST_ERR_INVALID_HANDLE);
    assert(a.counter_inc(ctx_a, h2, 1, &out, &err) == NOX_HOST_OK && out == 23);

    assert(b.box_new(ctx_b, 99, &hbox, &err) == NOX_HOST_OK && hbox != 0);
    assert(b.box_get(ctx_b, hbox, &out, &err) == NOX_HOST_OK && out == 99);
    assert(a.handle_release(ctx_a, h2) == NOX_HOST_OK);

    a.mod_fini(ctx_a);
    a.ctx_destroy(ctx_a);
    a.mod_fini(ctx_a2);
    a.ctx_destroy(ctx_a2);
    b.mod_fini(ctx_b);
    b.ctx_destroy(ctx_b);
    printf("c_host_test: nox_host.h contract (spans, errors, handles, dual-lib) PASS\n");

    /* 5. Concurrent barrier rounds (1/2/4/8 threads, per-thread NoxHostContext) */
    const int counts[] = {1, 2, 4, 8};
    for (size_t i = 0; i < sizeof(counts) / sizeof(counts[0]); i++) {
        if (run_thread_round(&a, &b, counts[i]) != 0) {
            return 1;
        }
    }

    /* 6. Sequential short-lived thread churn (>64 threads to prove zero MAX_POOL_WORKERS cap) */
    for (int i = 0; i < 80; i++) {
        pthread_t th;
        assert(pthread_create(&th, NULL, churn_worker, &a) == 0);
        pthread_join(th, NULL);
    }
    printf("c_host_test: 80 sequential thread churn PASS\n");

    dlclose(hb);
    dlclose(ha);
    printf("c_host_test: clean dlclose & teardown PASS\n");
    return 0;
}
