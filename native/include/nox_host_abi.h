#ifndef NOX_HOST_ABI_H
#define NOX_HOST_ABI_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Target Nox Host Export ABI (v1, proposed upstream contract — see UPSTREAM_REQUIREMENTS.md UR-002).
 *
 * Design invariants:
 *  - Pure C/C++ compatible header; zero private Nox runtime structs or ARC headers.
 *  - All structs carry `struct_size` and `abi_version` for forward-compatible checking.
 *  - Fixed-width integer types only (no C `long` for Nox integers).
 *  - Explicit ownership: errors returned by Nox calls must be released with `error_clear` / `err->release`.
 */

#define NOX_HOST_ABI_VERSION 1u

#define NOX_HOST_CAP_PARALLEL_CALLS (1ULL << 0)
#define NOX_HOST_CAP_FOREIGN_THREADS (1ULL << 1)
#define NOX_HOST_CAP_EXCEPTION_INFO (1ULL << 2)

typedef enum NoxHostStatus {
    NOX_HOST_OK = 0,
    NOX_HOST_ERR_VERSION = -1,
    NOX_HOST_ERR_INVALID_ARG = -2,
    NOX_HOST_ERR_STATE = -3,
    NOX_HOST_ERR_OVERFLOW = -4,
    NOX_HOST_ERR_OOM = -5,
    NOX_HOST_ERR_EXCEPTION = -6,
    NOX_HOST_ERR_UNSUPPORTED = -7,
    NOX_HOST_ERR_INTERNAL = -8
} NoxHostStatus;

typedef struct NoxHostSpan {
    const uint8_t *data;
    size_t len;
} NoxHostSpan;

typedef struct NoxHostError {
    uint32_t struct_size;
    int32_t code;
    NoxHostSpan class_name;
    NoxHostSpan message;
    int64_t line;
    void (*release)(void *ctx, struct NoxHostError *err);
    void *release_ctx;
} NoxHostError;

typedef struct NoxHostContext NoxHostContext;

typedef struct NoxHostContextConfig {
    uint32_t struct_size;
    uint32_t abi_version;
    uint64_t flags;
} NoxHostContextConfig;

typedef struct NoxHostRuntimeOps {
    uint32_t struct_size;
    uint32_t abi_version;
    uint64_t capabilities;
    NoxHostStatus (*context_create)(const NoxHostContextConfig *cfg, NoxHostContext **out_ctx);
    void (*context_destroy)(NoxHostContext *ctx);
    void (*error_clear)(NoxHostError *err);
} NoxHostRuntimeOps;

#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
_Static_assert(sizeof(int32_t) == 4, "int32_t must be 4 bytes");
_Static_assert(sizeof(int64_t) == 8, "int64_t must be 8 bytes");
_Static_assert(sizeof(NoxHostSpan) == 2 * sizeof(void *), "NoxHostSpan layout check");
_Static_assert(sizeof(NoxHostContextConfig) >= 16, "NoxHostContextConfig size check");
_Static_assert(sizeof(NoxHostError) >= 48, "NoxHostError size check");
_Static_assert(sizeof(NoxHostRuntimeOps) >= 32, "NoxHostRuntimeOps size check");
#endif

#ifdef __cplusplus
}
#endif

#endif /* NOX_HOST_ABI_H */
