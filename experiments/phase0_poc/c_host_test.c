#include <assert.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef int32_t (*fn_2i64)(int64_t, int64_t, int64_t *);
typedef int32_t (*fn_1i64)(int64_t, int64_t *);

typedef struct SharedApi {
    fn_2i64 add;
    fn_1i64 fib;
    fn_2i64 work_loop;
    fn_2i64 checked_div;
    fn_1i64 alloc_checksum;
    fn_2i64 mul;
    fn_2i64 sub;
} SharedApi;

typedef struct ThreadCtx {
    const SharedApi *api;
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
    ThreadCtx *ctx = (ThreadCtx *)arg;

    /* Barrier so all foreign OS threads start native Nox calls together. */
    pthread_mutex_lock(ctx->mu);
    (*ctx->arrived)++;
    if (*ctx->arrived == ctx->num_threads) {
        pthread_cond_broadcast(ctx->cv);
    } else {
        while (*ctx->arrived < ctx->num_threads) {
            pthread_cond_wait(ctx->cv, ctx->mu);
        }
    }
    pthread_mutex_unlock(ctx->mu);

    for (int iter = 0; iter < 10; iter++) {
        int64_t cur = atomic_fetch_add_explicit(ctx->in_flight, 1, memory_order_acq_rel) + 1;
        record_peak(ctx->max_in_flight, cur);

        int64_t out = 0;
        if (ctx->api->add(20 + ctx->thread_id, 22, &out) != 0 || out != 42 + ctx->thread_id) {
            atomic_fetch_add(ctx->failures, 1);
        }
        if (ctx->api->work_loop(200000, ctx->thread_id + iter, &out) != 0) {
            atomic_fetch_add(ctx->failures, 1);
        }
        if (ctx->api->alloc_checksum(200, &out) != 0 || out != 19900) {
            atomic_fetch_add(ctx->failures, 1);
        }
        if (ctx->api->checked_div(42, 0, &out) != -6) {
            atomic_fetch_add(ctx->failures, 1);
        }
        if (ctx->api->mul(6 + ctx->thread_id, 7, &out) != 0 || out != (6 + ctx->thread_id) * 7) {
            atomic_fetch_add(ctx->failures, 1);
        }
        if (ctx->api->sub(100, ctx->thread_id, &out) != 0 || out != 100 - ctx->thread_id) {
            atomic_fetch_add(ctx->failures, 1);
        }

        atomic_fetch_sub_explicit(ctx->in_flight, 1, memory_order_release);
    }
    return NULL;
}

static int run_thread_round(const SharedApi *api, int n_threads) {
    pthread_t threads[8];
    ThreadCtx ctxs[8];
    pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
    pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
    int arrived = 0;
    _Atomic int64_t in_flight = 0;
    _Atomic int64_t max_in_flight = 0;
    _Atomic int failures = 0;

    for (int i = 0; i < n_threads; i++) {
        ctxs[i].api = api;
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

    SharedApi api = {
        .add = (fn_2i64)dlsym(ha, "poc_a_add"),
        .fib = (fn_1i64)dlsym(ha, "poc_a_fib"),
        .work_loop = (fn_2i64)dlsym(ha, "poc_a_work_loop"),
        .checked_div = (fn_2i64)dlsym(ha, "poc_a_checked_div"),
        .alloc_checksum = (fn_1i64)dlsym(ha, "poc_a_alloc_checksum"),
        .mul = (fn_2i64)dlsym(hb, "poc_b_mul"),
        .sub = (fn_2i64)dlsym(hb, "poc_b_sub"),
    };
    if (!api.add || !api.fib || !api.work_loop || !api.checked_div || !api.alloc_checksum ||
        !api.mul || !api.sub) {
        fprintf(stderr, "dlsym failed for one or more symbols\n");
        return 1;
    }

    int64_t out = 0;
    assert(api.add(20, 22, &out) == 0 && out == 42);
    assert(api.fib(30, &out) == 0 && out == 832040);
    assert(api.alloc_checksum(1000, &out) == 0 && out == 499500);
    assert(api.checked_div(42, 6, &out) == 0 && out == 7);
    assert(api.checked_div(42, 0, &out) == -6);
    assert(api.mul(6, 7, &out) == 0 && out == 42);
    assert(api.sub(50, 8, &out) == 0 && out == 42);
    printf("c_host_test: basic dual-library calls PASS\n");

    const int counts[] = {1, 2, 4, 8};
    for (size_t i = 0; i < sizeof(counts) / sizeof(counts[0]); i++) {
        if (run_thread_round(&api, counts[i]) != 0) {
            return 1;
        }
    }

    dlclose(hb);
    dlclose(ha);
    printf("c_host_test: clean dlclose & teardown PASS\n");
    return 0;
}
