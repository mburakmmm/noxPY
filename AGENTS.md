# AGENTS.md — NOXPY

> Native Python extensions written in Nox. Free-threaded first, safe by design.
>
> This is the normative engineering charter for all humans and coding agents working on NoxPy. It defines the target architecture and release gates, not a claim that the features already exist.

## 0. Read this first

- Project name: **NoxPy**; proposed repository: `mburakmmm/noxpy`.
- Upstream language: [Nox](https://github.com/mburakmmm/nox-lang); compiler: `noxc`.
- Minimum supported interpreter: **CPython 3.15**, including both ordinary CPython and free-threaded CPython (`3.15t`). Free-threaded support is mandatory, not experimental for stable releases.
- Target stable extension ABI: **abi3t, with dual abi3/abi3t compatibility** when the produced binary and its dependency closure meet the contract.
- Default native compiler backend: LLVM. Do not require QBE for NoxPy; do not claim QBE support without evidence.
- Implementation language: Nox wherever practical; a small audited C shim for CPython headers, macros, initialization and ABI boundaries; Zig only when justified by upstream runtime or platform needs; thin Python packaging/bootstrap adapter. No mandatory Rust, PyO3 or Maturin dependency.
- Proposed license: MIT, subject to maintainer approval; preserve third-party licenses.
- Primary product: native extension modules loaded inside Python's process, not RPC, subprocesses, WASM or Python-to-Nox source transpilation.
- Never claim a build, test, release, compatibility level or benchmark was successful without actual evidence.

Normative terms MUST, MUST NOT, SHOULD and MAY are used deliberately. A later accepted ADR can refine a design decision, but cannot silently waive a safety/release gate. User instructions and upstream repository policies take precedence for work in their respective repositories.

## 1. Mission and boundaries

NoxPy combines the roles commonly served by a native binding framework and a Python build/distribution tool. A developer should author typed Nox code, expose functions/classes, build a platform wheel, install it with `pip` or `uv`, and import it as a normal Python module. Wheel consumers must not install `noxc`, Zig, a C compiler, or the Nox registry client.

Long-term goals:

- Functions, native classes, methods, properties, exceptions, rich signatures, type stubs, protocols, callbacks, iterators and async support.
- Correct free-threaded execution with concurrent independent calls and explicit shared-state semantics.
- Versioned stable ABI, reproducible builds, safe ownership and interpreter isolation.
- Python objects available to Nox through managed handles; later, optional standalone Nox embedding of CPython.
- PEP 517/518/621/660/561 packaging, standard wheels and sdists, CLI, diagnostics, CI and PyPI releases.
- Typed buffers, memoryview, optional NumPy and Arrow interop; actual zero-copy only with a proven lifetime contract.
- Real ecosystem examples with Atlas and Noxen; later Nova, Quartz and Pulse where useful.

Non-goals: CPython replacement; Python syntax or bytecode compiler; Python <=3.14; PyPy or other interpreters initially; mandatory JIT; blanket automatic conversion of arbitrary Python graphs; a security sandbox for native code; automatic thread safety of arbitrary Nox code; universal zero-copy; blanket compatibility with untested future Python/platform releases. No requirement to recreate every PyO3/Maturin feature or API spelling.

## 2. Upstream evidence and compatibility baseline

The initial architectural review observed Nox `v2.2.66` (2026-11-25). This is an investigation baseline, **not** a proven minimum compatible release. At the start of each upstream task, read the current Nox `AGENTS.md`, `VERSIONING.md`, changelog and relevant sources; record the exact compiler version and commit in evidence. Do not assume `main` or the newest release is compatible.

| Existing documented Nox capability | Consequence for NoxPy |
|---|---|
| Native AOT, LLVM hosted targets, Zig runtime | Reuse compiler and runtime; verify PIC and desktop library linking. |
| `extern def`, `noxc bindgen`, C shims | Useful for calling C from Nox; does not by itself export Nox functions to arbitrary host threads. |
| NNI v1, `include/nox_nni.h` | Native plugins extend Nox; not a Python-host extension ABI. Its opaque handles and limited value set must not be mistaken for arbitrary Nox object interoperability. |
| `include/nox_embed.h`, embedding lifecycle | Useful precedent; not proof of reentrant Python calls or safe foreign-thread attachment. |
| `RuntimeState` passed as hidden first parameter, per-program/per-worker contexts | Build on explicit state; prove lifecycle and ownership across interpreter/module/thread boundaries. |
| ARC/cycle collection, scheduler, `nox.thread`, atomic/shared-memory APIs | Reuse only after audit; native-thread safety of NoxPy remains to be proven. |
| Library emission flags documented for mobile | Desktop shared-library/static-object output is a Phase 0 verification/implementation item. |
| FFI callbacks documented for Nox-created threads | Arbitrary CPython/native host threads require a new supported contract. |
| Private internal ABI may change in patch releases | Do not link against guessed mangled symbols or depend on heap layouts. |

Read these authoritative sources:

- https://github.com/mburakmmm/nox-lang/blob/main/AGENTS.md
- https://github.com/mburakmmm/nox-lang/blob/main/VERSIONING.md
- https://github.com/mburakmmm/nox-lang/blob/main/docs/apis/nni.md
- https://github.com/mburakmmm/nox-lang/blob/main/docs/apis/internal-abi.md
- https://github.com/mburakmmm/nox-lang/blob/main/docs/language/ffi.md
- https://github.com/mburakmmm/nox-lang/blob/main/docs/tools/bindgen.md
- https://github.com/mburakmmm/nox-lang/blob/main/docs/tools/noxc.md
- https://github.com/mburakmmm/nox-lang/blob/main/docs/internals/runtime.md
- https://github.com/mburakmmm/nox-lang/blob/main/docs/stdlib/thread.md
- https://github.com/mburakmmm/nox-lang/blob/main/include/nox_embed.h

If upstream lacks a required primitive, create a minimal reproducer, a blocker record, a proposed upstream API and an issue/patch plan. Do not invent `noxc` commands, syntax, decorators, compiler APIs or released capabilities. Keep integration code behind a feature gate until it actually works. Never modify or publish to the upstream repository without authorization. Any authorized upstream patch must obey upstream tests and review policies, including backend regression coverage required by Nox.

## 3. Architecture and dependency rules

```text
Python package / user code
    -> CPython native module (.abi3t.so / .pyd)
       -> generated C API adapters + tiny audited CPython shim
          -> versioned Nox host/export boundary (Phase 0)
             -> compiled Nox wrappers + user Nox code + matching Nox runtime

Build path (not imported at application runtime):
    pip / uv / python -m build -> PEP 517 backend -> NoxPy builder
       -> compiler metadata -> binding IR -> generated C/Nox + stubs
       -> noxc / native linker -> audit -> wheel / sdist
```

Separate concerns:

1. **Core**: private CPython shim, reference utilities, error handling and ABI checks.
2. **Runtime**: interpreter/module state, Nox thread attachment, handles, lifecycle, synchronization, callback routing.
3. **Bindings**: typed conversions, signature parsing, classes and protocols; no build or network logic.
4. **Codegen**: deterministic binding intermediate representation (IR) and emitters; source locations and validation.
5. **Builder/packaging**: compiler discovery, target config, build graph, linker, wheel/sdist, audit and cache.
6. **CLI**: thin orchestration, human/JSON diagnostics.
7. **Integrations**: optional NumPy/Arrow/asyncio adapters; do not pollute core ABI.

Dependency direction: CLI/backend -> builder -> codegen -> binding IR; generated modules -> bindings/core/runtime -> documented upstream contract. Runtime must never import the builder, CLI, registry or require a network. Avoid circular dependencies and duplicated conversion implementations. Prefer one schema for codegen, stubs, docs and validation.

The compiler should own semantic type resolution. Prefer a supported typed export manifest or compiler API over parsing source with regexes or reverse-engineering compiler IR. If it does not exist, propose upstream metadata support; a limited explicit manifest for Phase 0/1 is acceptable with signature verification. No arbitrary source parser as a permanent substitute for the compiler.

## 4. Repository layout

```text
noxpy/
  AGENTS.md
  README.md
  LICENSE
  CHANGELOG.md
  SECURITY.md
  CONTRIBUTING.md
  nox.json
  nox.lock
  pyproject.toml
  src/
    core/ bindings/ runtime/ types/ exceptions/ threading/
    async_bridge/ buffer/ codegen/ builder/ packaging/ cli/
  native/
    include/ cpython/ runtime/
  python/noxpy/
    __init__.py
    build.py
    __main__.py
  integrations/
    numpy/ arrow/ asyncio/
  examples/
    hello/ fastmath/ mixed_package/ classes/ parallel/ atlas/ noxen/
  tests/
    unit/ codegen/ integration/ threading/ lifetime/ abi/
    packaging/ subprocess/ stress/ fuzz/ compatibility/
  benchmarks/
  docs/
    architecture/ adr/ roadmap/ api/ guide/ evidence/
  scripts/
  .github/workflows/
```

Adapt folder names to actual Nox package resolution rules, not the reverse. Do not introduce empty scaffolding or fake implementations solely to match this tree. Use `docs/adr/NNNN-title.md`, `docs/roadmap/`, and `docs/evidence/` for decisions, milestones and verification. Generated code lives in ignored build directories unless a reviewed fixture/golden file is intentionally committed. Mark generated files and provide a regeneration command.

## 5. CPython 3.15 and ABI rules

Authoritative specifications: PEP 703, 779, 803, 793, 489, 573, 630, 697; CPython 3.15 C API documentation is the final authority for actual symbols, flags, ownership and platform linkage.

- `Requires-Python: >=3.15` in all published Python projects. Reject unsupported implementation/version before building or loading native code where possible. Metadata and error messages must agree.
- Build the default stable path with `Py_TARGET_ABI3T=0x030F0000` before including `Python.h`. Verify the effective ABI configuration using real 3.15 headers. Do not guess the effect of defining `Py_LIMITED_API`; follow the actual header rules for dual compatibility.
- `Py_GIL_DISABLED` at compile time is not a runtime GIL detector, especially with abi3t. Test interpreter build mode and current GIL state separately. Use documented Python/runtime APIs; never assume a macro proves thread safety.
- Treat `PyObject`, `PyTypeObject`, and interpreter internals as opaque. No direct `ob_refcnt`, `ob_type`, raw object-layout casts, `_Py*` private APIs, unsupported macros or static `PyTypeObject` definitions in the stable path.
- Use CPython 3.15 `PyModExport_<leaf_module>` with `PyMODEXPORT_FUNC`, ABI info and documented `PySlot` entries. Return immutable/static metadata only; perform fallible initialization in execution slots. Use `PyABIInfo_Check` where required. Do not depend on legacy `PyInit_*` for the stable path.
- Module state must be per instance and contain interpreter-local resources. Implement traverse, clear and free callbacks for owned Python references, and rollback on partial initialization. No global cached `PyObject*` or module state.
- Declare `Py_mod_gil = Py_MOD_GIL_NOT_USED` only for audited, verified code and dependencies. No silent GIL reenablement; unsupported exports fail build or import with a clear error. Never use `-X gil=0` alone as proof of compatibility because it can mask default behavior.
- Declare `Py_mod_multiple_interpreters` support accurately; until isolation is proven use the documented unsupported setting. Do not claim subinterpreter support simply because the module uses multiple-phase initialization.
- Use heap types and supported type data APIs. Fixed-size native instance data can hold a handle, not embedded opaque CPython headers. Use the documented negative basicsize/type-data technique where applicable and test subclasses. Variable-sized extension types outside abi3t 3.15 are out of scope for the stable path.
- Audit every CPython symbol in the binary and bundled dependency chain against the targeted ABI. Header compilation is necessary, not sufficient.
- Prefer one dual-compatible wheel per platform: `example-0.1.0-cp315-abi3.abi3t-<platform>.whl`; `.abi3t.so` on Unix-like systems, `.pyd` on Windows. Use actual supported platform tags and appropriate stable import libraries/linker flags. Verify Windows linkage explicitly; never guess DLL names.
- Version-specific `cp315`/`cp315t` ABI builds MAY be internal diagnostics or explicit opt-in adapters if necessary. They cannot silently replace the default stable profile; do not label them `abi3t`. Free-threading safety remains mandatory. An optional ABI-specific artifact needs an ADR, distinct tags and independent tests.
- Test the exact same dual-tag wheel in clean regular and free-threaded CPython environments. Stable ABI compatibility does not prove semantic compatibility with future versions.

## 6. Phase 0: Nox host/export foundation (hard dependency)

Do this before implementing a high-level binding framework. First verify actual upstream capabilities with small executable experiments. Required contracts, with illustrative names only:

- Hosted desktop object/archive/shared-library emission, PIC runtime and position-independent user code on supported targets; no executable `main` or automatic process-global initialization in library mode.
- Documented C calling convention and explicit export metadata; versioned symbols, hidden visibility for private runtime symbols; no calls to unversioned generated names from NoxPy.
- Host context create/retain/release, thread attach/detach or equivalent entry guard, begin/end invocation, lifecycle shutdown. Explicit status codes and error objects with ownership rules.
- Safe host-callable generated wrappers for primitive functions, argument/result validation, owned byte/string values with explicit lengths and destruction functions. Do not use C `long` for fixed-width Nox integer semantics.
- A supported foreign-thread model: isolated contexts or documented shared runtime, correct allocator/ARC/cycle collector state, reentrancy, thread-local exception state and scheduler restrictions. Thread entry must not depend on Nox-created OS threads only.
- Stable object handles for future classes, pin/retain/release rules, per-owner validation, defined behavior for invalid handles, overflow and shutdown. Keep Nox object layout private.
- Exception containment: catch recoverable Nox errors before C; never unwind, `longjmp` or propagate a panic across the ABI. If an upstream fatal failure cannot be recovered, document it as a blocker or process-fatal failure; do not pretend to catch it.
- Multiple native extension modules in one process, including different compiler versions where the linkage model permits it. Prefer statically linked, symbol-hidden runtime per extension; test collision and visibility. No assumption that static linking alone ensures isolation.
- Exact compiler/runtime build matching; record artifact digests and upstream revision. A stable Python ABI does not make the private Nox runtime ABI stable.

Initial host ABI design (proposed, not a declaration of upstream exports): version+structure size, opaque context and call/handle types, fixed-width status codes, span/pointer+length, explicit allocation/release, capability flags. Document ownership, allowed threads, reentrancy, blocking, nullability and error contract per function. Version structs by size and version; never reuse a status code or silently change semantics. Prefer a new upstream host ABI alongside NNI instead of overloading the NNI plugin direction. Keep bridge C header C/C++ compatible; compile-time size/alignment checks across architectures.

**Required Phase 0 evidence:** a C host test calls a Nox function from multiple foreign OS threads, returns typed data/errors, reenters only through a supported path, shuts down safely, and loads two separately built Nox libraries. A minimal actual CPython extension imports in both 3.15 variants and computes `add(20, 22) == 42` using Nox, not C arithmetic. Run unforced GIL-state test and race/lifetime checks. A serialized debug prototype is permitted for bring-up but must not be released or described as satisfying free-threaded acceptance.

## 7. Binding IR and public API

Target syntax (illustrative; validate compiler support before adoption):

```nox
from noxpy import export, pyclass, pymethod

@export
def add(a: int, b: int) -> int:
    return a + b

@pyclass
class Counter:
    value: int

    def __init__(self, start: int) -> None:
        self.value = start

    @pymethod
    def increment(self) -> int:
        self.value += 1
        return self.value
```

```python
from fastmath import add, Counter
assert add(20, 22) == 42
counter = Counter(10)
assert counter.increment() == 11
```

Counter requires the class synchronization policy below before free-threaded export. Decorators (`export`, `pyclass`, `pymethod`, `pyproperty`, `pystaticmethod`, `pyclassmethod`, `pyexception`, `pyasync`, `pyiterator`, `pybuffer`, `pythreadsafe`) are target API names, not evidence of current Nox syntax. Prefer normal documented Nox annotations/metadata; propose compiler support only when required. Do not make annotations no-ops that falsely claim safety. `@pythreadsafe` is a reviewable contract, not a proof or a bypass.

IR MUST contain schema version, compiler identity, module/leaf name, source paths/locations, unique stable export IDs, resolved types, parameter kind/name/default, return type, docstring, exceptions, thread policy, ownership, callback/async behavior, capabilities, feature gates and ABI requirements. Deterministic ordering; reject duplicate names, invalid identifiers, reserved symbols, ambiguous overloads, unsupported generics, unresolved types, invalid defaults and unsupported policies at build time. Compile source and manifest consistently; fail if metadata is stale. Do not execute arbitrary Python or Nox code to discover signatures by default.

Generate wrappers and `.pyi` from the same IR. Emit accurate docstrings/signatures, `__module__`, public names and export table. Match positional-only, positional-or-keyword, keyword-only, default and varargs semantics when implemented; reject unsupported combinations, do not silently accept them. `METH_FASTCALL`/vectorcall are optional optimization paths only if allowed by target ABI, with fallback semantics identical to baseline. Generic exports require explicit specializations; no fake dynamic polymorphism.

## 8. Conversion semantics

| Nox type | Python type | Required policy |
|---|---|---|
| `int`, fixed width signed/unsigned | `int` | Check exact upstream width and bounds; overflow raises `OverflowError`; no truncation. |
| `float`, supported fixed floats | `float` | Document rounding, NaN/Inf, signed zero; check overflow for narrowing. |
| `bool` | `bool` | Strict by default; do not silently treat bool as an integer. |
| `str` | `str` | Length-aware UTF-8; embedded NUL preserved; invalid/surrogate policy explicit, normally strict error. |
| `bytes` / approved binary type | `bytes` | Byte-exact copy initially, length and allocation limits. |
| `None` | `None` | Explicit nullable/void distinction. |
| `T | None` | `T | None` | No magic sentinel or invalid null pointer. |
| `list[T]` | `list[T]` | Owned snapshot/copy; recursively validate each element; bound depth/size. |
| `dict[K,V]` | `dict[K,V]` | Define supported key types, equality/collision semantics, iteration and conversion failures. |
| tuple | tuple | Validate arity and element types. |
| class | heap type instance | Validated opaque handle; preserve identity only where documented. |
| `PyObject` | Python object | Managed interpreter-affine handle, not a raw pointer. |
| buffers | memoryview / exporter | Pinned owner + explicit layout/readonly/lifetime checks. |

No implicit user-code invocation for conversions unless a separately documented coercion policy permits it (`__index__`, `__float__`, `__iter__`, etc.). Strict type policy must state subclass handling. Reject cyclic copied inputs cleanly or use a documented graph-aware path; never recurse indefinitely. Avoid integer multiplication/addition overflow for buffer sizes, lengths and strides. Conversion of a mutable Python collection is not inherently an atomic snapshot: use supported synchronization for bounded snapshot operations; never iterate a changing dict using unprotected borrowed references. For unsupported concurrent mutation patterns document and reject where possible; avoid data races even when application semantics are unspecified.

Each conversion documents input ownership, output ownership, allocation, failure cleanup, callback potential, interpreter affinity and thread requirements. Errors include argument name/index or nested path, not sensitive values. Keep conversion limits configurable where needed and test large inputs. Copy is the correct default until borrowing/pinning has proven safety.

## 9. Ownership, lifetime and GC

Python and Nox have separate memory management. ARC does not automatically understand Python references and Python GC does not automatically understand Nox cycles.

- Distinguish owned strong reference, borrowed scoped reference and stolen reference. Annotate every CPython call contract in the shim. Borrowed values cannot escape call scopes or survive concurrent mutation unless protected/pinned by a documented mechanism. Prefer strong-reference APIs when reading mutable containers.
- Use opaque managed handles with interpreter/module identity, generation/liveness and defined ownership. A handle is not a pointer exposed to Python. Reject cross-interpreter use and use-after-close before dereference. Check generation wrap/ABA and concurrent close/use.
- No mixing of allocators; allocate and free in the same domain. Python object allocations must follow CPython allocator rules; native non-object buffers use appropriate memory domains. Nox values must be destroyed with their matching runtime.
- `Py_DECREF` and other reference drops can run arbitrary finalizers or callbacks. Do not perform them under a lock that callbacks could reacquire; move to local temporaries and release outside locks when safe.
- Release all acquired resources on every failure branch, including partial sequence/class/module initialization, failed callback registration and cancellation. Use scoped guards/explicit cleanup supported by Nox; no unwinding across C.
- Each exported heap type that owns Python references must expose them to Python traversal and clear them correctly. Any Python->Nox->Python edge must be visible to GC or explicitly prevented. Do not promise arbitrary mixed cycles until proven. Test weakref/finalizer behavior and resurrection if supported.
- `close` is idempotent at the public wrapper, but must not reuse freed raw addresses. Destructors must tolerate partial initialization, repeated clearing and shutdown. Native resource-owning classes should support context-manager semantics when available.
- Do not retain borrowed Python objects in Nox workers; deferred decref/finalization must execute under a valid thread state of the correct interpreter, before it is destroyed. Shutdown must drain or reject pending references safely.
- Do not unload shared native libraries while Python types, handles, callbacks, worker tasks, buffers or runtime references remain alive.

Maintain an ownership/lifetime table for every exported boundary API. Review new raw pointer use as security-sensitive code. Safe public APIs must not require callers to understand reference counts or pointer lifetimes.

## 10. Free-threaded execution and synchronization

### 10.1 State model

Use immutable process-global metadata only; interpreter-specific module state owns runtime configuration and registered types. Each invocation uses a valid Nox execution context and CPython thread state. Prefer isolated per-worker/per-call contexts for pure functions; use stable owner-managed contexts for persistent Nox objects only if the upstream contract supports them. Do not move ARC allocations between contexts by copying pointers. Choose and document the actual runtime strategy in an ADR after Phase 0 tests. Thread-local caches must be scoped/keyed to interpreter/module identity, not raw reusable thread IDs alone.

### 10.2 Thread policy for exports

- `parallel`: independent calls on distinct state may execute at the same time; shared data requires explicit approved synchronization.
- `instance_locked`: serialize mutation and access to one object; unrelated instances/calls remain concurrent. Default for ordinary mutable classes when safe ownership and locking are implemented.
- `thread_affine`: enforce owner thread at runtime; reject other callers clearly. Only allowed if finalization and teardown from foreign threads remain safe.
- Explicitly immutable/shareable types MAY use lock-free reads only after proof. Unsafe or unknown sharing is rejected, not advertised as compatible.

These policy names are design targets. `parallel` is not permission to mutate arbitrary Nox globals. Module state mutations must have a documented lock or atomic protocol; per-worker globals cannot be presented as a single shared Python global unless semantics are explicitly implemented.

### 10.3 Python C API access

Always have a valid attached `PyThreadState` for Python C API operations, including free-threaded builds. On conventional CPython this also entails the GIL as required. Detach/reattach around pure native work or blocking operations using documented APIs, with RAII/cleanup-equivalent guards. No Python C API or Python reference manipulation while detached. Do not use main-interpreter convenience APIs as a universal solution for foreign threads or subinterpreters; use documented interpreter-aware attachment and teardown or marshal work to a known attached thread. Native-to-Python callbacks require a live interpreter and admission to its callback lifecycle. Treat attachment and GIL state as distinct conditions.

### 10.4 Locking and reentrancy

No process-global invocation mutex, no hidden single-thread worker that serializes all calls, no reliance on Python's GIL as a native mutex. Internal narrow global synchronization for truly process-global immutable initialization may be allowed with justification.

Define lock order and ownership in `docs/architecture/threading.md`. Lifecycle lock protects admission/state transitions only; never hold it during arbitrary user work, thread joins, Python calls or finalizers. Avoid nesting instance/handle locks; if needed define canonical ordering. Never hold a Nox lock across a Python callback, a potentially reentrant C API operation, a blocking wait or decref that may run user code. Use snapshot/retain, release lock, call, reacquire and revalidate. CPython critical sections are not a substitute for protecting Nox state; follow their documented nesting and blocking semantics. If a safe callback path cannot be made reentrant, reject the operation explicitly.

Use atomics with documented memory order where justified; default to clear locking rather than bespoke lock-free designs. Add adversarial concurrency tests for same object, distinct object, nested calls, callbacks, concurrent destruction, exceptions and cancellation. A passing stress test is not proof of absence of races; combine review, sanitizer evidence and contracts.

### 10.5 Shutdown

Lifecycle: `NEW -> INITIALIZING -> READY -> CLOSING -> CLOSED` (with explicit failure/rollback state). Acquire an invocation lease only while READY. Closing denies new calls/callbacks, requests worker cancellation, waits/drains in-flight work without deadlock, releases handles/buffers/Python refs in correct domains, and destroys runtime last. Detect self-join/reentrant shutdown. Do not call Python during late finalization when attachment is no longer safe. Define behavior for interpreter shutdown with active workers, repeated imports, failed initialization, fork and process exit; unsupported fork-after-threads behavior must be documented or rejected in child process without deadlock. No daemon-thread leak as a shutdown strategy.

## 11. Exceptions, diagnostics and failure containment

- No recoverable exception may unwind across C/Nox/Python boundaries. Each trampoline returns an explicit status or the correct CPython failure sentinel with an exception set. A successful return must not leave a Python exception pending.
- Map Nox errors predictably: invalid type -> `TypeError`, invalid value -> `ValueError`, range overflow -> `OverflowError`, allocation failure -> `MemoryError`, index/key error -> corresponding Python exception, OS failure -> `OSError` with preserved code where appropriate, cancellation -> appropriate documented cancellation exception; unmapped recoverable errors -> module-specific `NoxError` or `RuntimeError`. Verify actual upstream exception APIs rather than guessing type names.
- Preserve original Python exceptions and traceback on callback failure; avoid overwriting with a generic Nox error. Keep error objects interpreter-local. Preserve cause/context where appropriate.
- Do not swallow errors, return arbitrary defaults on failure, or use assert as public validation. Do not promise recovery from native UB, OOM abort, stack overflow or unrecoverable upstream panic. Test fatal paths only in subprocesses and report limitations.
- Build errors use stable codes (e.g. `NPY-E-ABI`, `NPY-E-UPSTREAM`, `NPY-E-TYPE`, proposed), source location, target, concise actionable message and optional verbose diagnostics. No credentials, tokens, full environment or private inputs in logs.
- Native crash reports should include build IDs and symbols in debug artifacts; production tracebacks must not expose raw addresses or secrets by default.

## 12. Classes, methods and Python protocols

- Generate real CPython heap types with interpreter-local type objects. Do not implement class support as an untyped capsule plus a mandatory Python proxy. Private capsules MAY be used for documented inter-module C interfaces with validated names and lifetime.
- Fixed-size instance data owns an opaque Nox handle and lifecycle policy. Construction validates inputs and handles partial allocation/initialization. Destruction releases exactly once. Reject object use before initialization/after close.
- Support constructor signatures, instance/static/class methods, properties/getters/setters, docstrings, type stubs, repr and equality as declared. No arbitrary attribute mutation, subclassing, weakref, hashing, pickling, operators, or multiple inheritance until explicitly supported and tested.
- Hash consistency with equality is mandatory; mutable equality-based instances must not be hashable by default. Repr must avoid recursion, sensitive data and unexpected native side effects.
- Python rich comparison returns `NotImplemented` when appropriate; no silent fallback for invalid operations. Protocol methods follow the corresponding CPython contracts for errors and result ownership.
- Classes crossing NoxPy modules require an explicit interoperable type/handle ABI, version negotiation and ownership test. Until then reject incompatible native handles; convert via supported public types instead.
- Iterator protocol: preserve iterator owner lifetime, stop with `StopIteration`, distinguish end-of-stream from error; define mutation behavior and concurrency. Context managers: cleanup even on exception; async variants require async bridge.
- Generic class specializations, dataclass-like metadata, enums, overloads and descriptors are later features. They must be explicit in the IR and reflected in stubs.

## 13. Python objects, callbacks, async and embedding

### 13.1 Python from Nox inside an extension

Provide a managed `PyObject` abstraction with scoped conversion, attribute access, function/method calls, imports and exception handling. Target interface (not current syntax): `pyimport("numpy")`, `obj.call(...)`, `obj.to_float()`. Do not expose raw `PyObject*` as a public safe API. Keep dynamic operations explicit and require the correct attached thread state. Callback data must own strong references, track interpreter and registration lifetime, and reject invocations after closing.

Callbacks invoked synchronously on an already attached Python thread may use the supported reentrant entry path. Worker callbacks use either a verified interpreter-aware attach path or dispatch through a known owner/event-loop thread. Never route to a random interpreter, dead interpreter or unrelated Nox runtime. Never block waiting for a callback while holding a lock it needs. Registration, invocation, deregistration, concurrent cancellation and cleanup all require tests. Preserve Python exceptions.

### 13.2 Asyncio integration

Python sees a genuine awaitable; Nox tasks execute via a supported scheduler bridge. Do not create an event loop per call, call `asyncio.run()` inside an existing loop, or block the event loop waiting on a native worker. Create Python futures on the caller's running loop; complete them thread-safely via documented event-loop APIs and exactly-once completion state. Handle cancellation before start, during native work, after completion, loop close, shutdown and races between error/result/cancel. Map exceptions correctly. Python contextvars behavior must be documented, captured only when needed and tested. Never use Python APIs in detached native tasks. Provide configurable bounded worker capacity and backpressure; avoid Python executor + Nox scheduler oversubscription. Async support must not imply parallelism or cancellation of arbitrary native instructions.

### 13.3 Standalone embedding (later, separate product surface)

Nox launching CPython is a distinct deployment/linking/lifecycle problem. Use the documented CPython initialization/configuration API for 3.15+, explicit interpreter ownership, search-path configuration, secure environment handling and deterministic cleanup. Do not initialize/finalize CPython from an extension imported by Python. No process-wide finalization of an interpreter owned by another host. Embed builds may require a different ABI/linking profile; never label the result an abi3t extension solely because it uses NoxPy. Test installed Python discovery, virtual environments, module import, thread attachment, errors and repeated processes. Repeated initialize/finalize in one process is unsupported until proven. Optional adapters must reflect their own free-threading and Python-version compatibility.

## 14. Buffer, NumPy and Arrow

- Start with explicit copying. A zero-copy export requires a stable owner reference, immutable or pinned allocation, correct allocator, pointer validity until the last consumer releases it and synchronized access for writable data. Never expose a pointer into an ordinary resizable Nox list/string or moving/reclaimed allocation.
- Use supported CPython buffer protocol operations and `Py_buffer` fields only where guaranteed by target ABI. Correctly acquire/release on success/failure; keep the exporter alive; validate format, itemsize, shape, strides, dimensions, byte order, alignment, readonly flags, contiguous requirements and byte-length overflow. Handle zero-length/negative strides/non-contiguous arrays explicitly or reject them.
- No resize or deallocation while exported writable/readonly views exist. Concurrent writable aliases require a documented synchronization contract; otherwise use readonly views or exclusive access. Python GC traversal must see owned references where needed.
- NumPy is an optional dependency. Prefer PEP 3118-compatible buffers or a documented supported interchange protocol to coupling core ABI to NumPy internals. If direct NumPy C API is needed, use separately tested adapter profiles and do not infer compatibility from CPython abi3t alone. Test dtype, strides, endian, ownership, alignment, readonly and absent dependency. Pin supported versions in CI, not permanently in consumer metadata without reason.
- Arrow uses documented C Data/C Stream interface and/or supported capsule interchange. Validate schema, child arrays, offsets, null bitmap, lengths, release callback ownership and producer/consumer lifetimes. Release exactly once; reject invalid/untrusted structures before use where validation is possible. Document limitations: raw native pointer exchange is a trust boundary, not a sandbox.
- Verify actual aliasing for zero-copy claims (pointer/ownership tests, mutation visibility as permitted), memory use and release behavior. If an adapter copies, report it honestly.

## 15. Build system, CLI and packaging

### 15.1 Packaging contract

A generated project should look like this (proposed schema; implement and test before documenting as usable):

```toml
[build-system]
requires = ["noxpy>=0.1"]
build-backend = "noxpy.build"

[project]
name = "fastmath"
version = "0.1.0"
requires-python = ">=3.15"

[tool.noxpy]
module-name = "fastmath._native"
source = "src/lib.nox"
backend = "llvm"
abi = "abi3t"
free-threaded = true
generate-stubs = true
```

The canonical schema MUST distinguish distribution name, import package/module, leaf export symbol, Nox entry, dependencies/lockfile, target ABI, compiler constraints, native dependency policy and build profile. Validate names, paths, values and incompatible options. Do not confuse distribution hyphens with Python identifiers. Resolve relative paths from project root; prevent traversal outside explicitly allowed roots. Unknown config keys should fail with clear diagnostics in stable schema. Support pure-native package and mixed Python/native package, including nested module names. Use one documented import layout per template; no accidental duplicate extension or shadowing of Python package.

PEP 517 hooks: `build_wheel`, `build_sdist`, `get_requires_for_build_wheel`, `get_requires_for_build_sdist` and metadata preparation when supported. PEP 660: `build_editable`, related requirement/metadata hooks, editable install semantics; native changes may require explicit rebuild, document this. Correct hook signatures, return basenames, honor isolated environments and `config_settings`; metadata must match wheel if metadata directory is provided. Use Python packaging utilities where justified rather than reimplementing requirement parsing or resolver behavior.

Wheel must include extension, importable package files, `.pyi`/`py.typed` where appropriate (PEP 561), license notices and `.dist-info` with valid `METADATA`, `WHEEL`, `RECORD` and expanded tags. `Root-Is-Purelib: false` for platform extension wheels. No build path, secrets, development binaries or unrelated compiler files. sdist must contain all necessary source/metadata/generated input as documented; no dependency on a Git checkout or untracked working files. Test installation from sdist in isolated environment. Build dependencies must not become wheel runtime dependencies unless required. Ensure normalized names/version and reproducible archives.

### 15.2 Compiler discovery and bootstrap

`noxpy` as a build tool needs a working Nox compiler and native linker/headers; consumer packages do not. Discover explicit config/env override, validated toolchain path, then PATH. Validate version, target, SHA/checksum where appropriate and runtime resources. Never silently download or execute a compiler or run a remote shell install script. Optional explicit toolchain bootstrap is allowed only with user consent, pinned verified artifacts, secure cache and clear platform errors. Use `nox.lock` and immutable dependency references. Explain missing compiler/headers/linker in `doctor` output. A PEP 517 isolated Python environment does not itself provision a native toolchain; document source-build prerequisites. Avoid recursive bootstrap (NoxPy cannot depend on itself to build its first wheel without an explicit bootstrapping route).

### 15.3 Build pipeline

1. Resolve and validate project config, target Python/ABI, noxc version, native toolchain and dependency lock.
2. Obtain typed metadata, construct/validate binding IR and thread/ownership contracts.
3. Generate Nox wrappers, C shim/adapters, stubs and package metadata deterministically.
4. Compile PIC code and matching runtime using verified upstream flags. Link using correct platform/target Python rules and hidden symbols.
5. Audit ABI symbols, native dependencies, RPATH/install names, CPU baseline, platform policy and exported symbols; fail closed on mismatch.
6. Assemble wheel/sdist and validate metadata/RECORD/tags. Optionally repair platform wheel with compatible approved tooling; re-audit repaired artifact.
7. Run fresh-env import/smoke, exact-artifact dual-interpreter tests and package checks; emit build manifest, digests and diagnostic summary.

Use argument-vector subprocess invocations (no shell interpolation), timeouts, explicit cwd and safe environment handling. Cross-compiling requires target Python headers/ABI metadata and target toolchain/sysroot; never use host interpreter configuration as target truth. Do not claim cross target support without target execution tests. Cache keys include compiler/runtime digest, sources/lock, IR/generator version, Python ABI floor, headers, target, linker/flags, build profile and dependencies. Cache writes atomic and safe under concurrent builds. Do not load an artifact solely because filename matches; validate integrity. Respect `SOURCE_DATE_EPOCH`, sorted entries, deterministic path handling and timestamps; report reproducibility limitations. Offline mode must perform no network; fail if required locked artifact missing.

### 15.4 CLI contract

Commands (implement incrementally; no fake success or undocumented aliases):

| Command | Behavior |
|---|---|
| `noxpy new NAME` | Scaffold validated project; no overwriting existing files by default. |
| `noxpy init` | Add configuration to existing project without destructive changes. |
| `noxpy doctor` | Detect versions, ABI, target, headers, compiler and missing prerequisites; no mutation. |
| `noxpy check` | Validate config, exports, bindings and safety policies without publishing. |
| `noxpy build --release` | Build/audit wheel; optional `--sdist`, target/ABI options with explicit semantics. |
| `noxpy develop` | Build and install into explicitly selected or active environment; reject ambiguous interpreter selection. |
| `noxpy generate-stubs` | Generate/check deterministic `.pyi` and `py.typed`. |
| `noxpy test` | Run declared native and Python tests, surface failures. |
| `noxpy benchmark` | Run defined workload, emit raw data and environment metadata. |
| `noxpy publish` | Validate artifacts and upload only with explicit authorization; dry-run available. |
| `noxpy clean` | Delete only project-owned build/cache paths; no global destructive defaults. |

`--help`, `--version`, `--verbose`, structured `--json` where useful; actionable nonzero exit codes on failure. Keep machine-readable stdout separate from progress stderr. Do not parse unstable noxc diagnostic text as machine protocol. `develop` must not install into system Python accidentally. Prefer standards-based backend integration with `pip`, `uv`, `python -m build`; do not create a competing package resolver.

### 15.5 Distribution and platform policy

P0: macOS arm64, Linux x86_64 (documented manylinux baseline, initially target `manylinux_2_28` only if verified), Windows x86_64. P1: Linux aarch64, macOS x86_64. P2: musllinux x86_64/aarch64. P3: Windows arm64. Platform priorities are targets, not claims. Each platform has native build/import and clean environment evidence for ordinary and free-threaded CPython 3.15. For macOS, set a verified deployment target; for Linux, audit glibc/musl and shared libraries; for Windows, use matching import libs, architecture and DLL discovery without unsafe search paths. A wheel must not use CPU instructions unavailable on its advertised baseline; use runtime dispatch for optional SIMD and test fallback. No platform label before compliance audit and runtime smoke tests. Use auditwheel/delocate/delvewheel or compatible alternatives only when verified to support the chosen ABI/tags. Preserve licenses when bundling shared libraries; prefer hidden matching Nox runtime, no external Nox installation. Avoid native dependency conflicts with unrelated Python packages.

## 16. Security, supply chain and privacy

NoxPy executes native code in the Python process. It is not a sandbox. Do not represent capability manifests, handles, free-threading or WASM concepts as security isolation. Treat external sources/build scripts, nox packages, FFI and wheels as executable code with host privileges.

- Threat model: untrusted build input, dependency substitution, malicious manifests/path traversal, ABI mismatch, handle forgery, UAF, double-free, refcount races, integer overflow, deadlock, callback reentrancy and shutdown. Document mitigations and residual risks.
- Validate every boundary: lengths, pointer/null requirements, type tags, bounds, alignment, arithmetic overflow, owner identity, handle generation and state. Never trust Python type hints as runtime validation.
- Prefer audited safe APIs. Mark all unsafe/native blocks and explain why safety invariants hold. No private CPython/Nox layout hacks, arbitrary code injection into generated C from names/docstrings, shell execution of user strings or unchecked extraction of archives. Escape generated identifiers/string literals correctly; reject unsupported names.
- Lock and verify dependencies and release artifacts; review licenses and provenance; maintain SBOM (upstream tooling may be reused), dependency/vulnerability checks and signed/attested provenance where available. No secrets in source, examples, CI logs or wheels. Pin CI actions by reviewed commit SHA; least-privilege workflow permissions; separate untrusted PR workflows from publish credentials.
- PyPI release should use trusted publishing/OIDC when available, staged validation and approval. Never publish automatically from PR or arbitrary branch. No automatic git push/tag/delete/force operations or public registry claims without permission. Verify PyPI distribution name availability before publishing; `noxpy` and example names are proposals, avoid confusion with the existing `nox` test tool.
- Provide `SECURITY.md` with reporting channel, supported versions and responsible disclosure process. Treat reproducible data races, unsafe references and memory corruption as release blockers. Native dependency compromise or crash must not be masked by a Python wrapper.
- Telemetry is opt-in if ever introduced; no network at import or by default in tests/diagnostics. No collection of user data or environment secrets.

## 17. Testing requirements

All tests must exercise actual code; no mocks substituted for final integration evidence. Use upstream-supported Nox test runner and Python test runner (pytest or stdlib as appropriate), real CPython 3.15 and 3.15t executables. Pin versions in CI; record exact build configuration. Native sanitizer tooling availability varies; missing coverage is a documented gap, not a passing result.

| Suite | Mandatory coverage |
|---|---|
| Unit | Type bounds, nulls, bool vs int, Unicode/NUL, length overflow, malformed config, ownership states. |
| Codegen | Golden deterministic IR/C/Nox/stubs, escaping, invalid names/types, diagnostics and source maps. |
| Integration | Real compiled Nox exports, mixed packages, kwargs/defaults, exceptions, repeated imports, real Python classes. |
| ABI | Stable header build, symbol allowlist, no private symbols/layout access, wheel tags/suffix, Windows linkage, same wheel in both modes. |
| Threading | 1/2/4/8 workers, barrier-synchronized overlap, independent calls, same instance, lock order, concurrent close/callback, errors. |
| Lifetime/GC | Allocation/refcount balance, partial init, cross-language cycles, weakrefs/finalizers, invalid handles, buffer pinning. |
| Subprocess | Crash isolation for tests, shutdown with active work, fork policy, module reload, failed import, multi-module runtime collisions. |
| Subinterpreter | Independent state, no leaked objects, cross-interpreter handle rejection, teardown, accurate support flag. |
| Async | Await result/errors, cancellation races, closed loops, backpressure, repeated invocation, no event-loop blocking. |
| Packaging | Isolated PEP 517 builds, sdist -> wheel, editable install, pip/uv install, uninstall, metadata/RECORD, offline wheel consumption. |
| Fuzz/property | Converter input shapes, metadata/parser, handles, buffer sizes, generated identifiers and error paths. |
| Stress | Long-running concurrent allocations, repeated import/exit in subprocesses, class mutation, callback and shutdown race reproduction. |

Free-threading acceptance test example (Python 3.15t only; run in a clean subprocess **without** forcing the GIL off):

```python
import sys
assert not sys._is_gil_enabled(), "test did not start in free-threaded mode"
import fastmath
assert not sys._is_gil_enabled(), "extension enabled the GIL"
assert fastmath.add(20, 22) == 42
```

Also test ordinary CPython and free-threaded build with GIL explicitly enabled and explicitly disabled, as supplementary cases. Capture import warnings, dependencies and environment flags. Detect actual build mode separately from current GIL state. Test a real concurrent workload; prove overlap using instrumentation/barriers with deadlines rather than wall time alone. Verify no global invocation lock by design review and test independent call progress. Use `PYTHONMALLOC=debug`, debug Python builds where compatible, ASan/UBSan/TSan and platform tools as supported; a debug build may need its own artifact profile. Keep sanitizer suppressions minimal, reviewed and tied to upstream issues. Test exception and memory fault injection; do not simply raise synthetic errors in wrappers instead of exercising failure cleanup. Mark unavailable tests honestly; flaky tests require a root-cause issue, not indiscriminate retries.

Test dependency closure: import optional adapters in clean env, ensure their import does not enable GIL, report absent support. Test two extension modules compiled independently; include different Nox versions if claimed. Build once and test installed wheel outside repo with no Nox compiler or source tree on PATH. Verify the wheel is the exact audited hash. Test on 3.16+ when available; prerelease CI may be nonblocking until promoted to supported matrix, never claim versions without evidence.

## 18. Performance and benchmarks

Correctness precedes optimization. Benchmark native call overhead (zero/small args), scalar conversion, large collections, buffer transfer, CPU kernels, strings/JSON, class allocation/mutation, callbacks, async, memory use and scaling across 1/2/4/8 or available cores. Compare ordinary/free-threaded CPython, native Nox baseline, C extension and optionally PyO3/Cython/Numba with equivalent algorithms, inputs, precision, compiler optimization and thread count. Benchmark optional tooling only when actually installed; no fake comparisons.

- Separate import/build cost, marshaling, computation and end-to-end latency. Report warmups, sample count, median and tail/dispersion, CPU/OS/compiler/Python mode, memory, affinity, versions and raw output. Use fixed seeds and verify output equivalence.
- Demonstrate concurrency with correctness tests, not only speedup. No guaranteed linear scaling or faster-than-Rust/C claims. Record scheduler worker count and oversubscription.
- Set regression budgets after collecting a stable baseline; proposed default investigation threshold: >10% median regression on controlled hardware, confirmed by repeated runs. Not a fabricated achievement or hard gate on noisy shared CI. Maintain baseline and rationale for accepted regression.
- Do not weaken validation, ownership, locking or error handling for microbenchmarks. Stable ABI path is default; opt-in full ABI optimizations need separate profiling/evidence. SIMD and zero-copy are opt-in where safe; document actual copies and limitations.

## 19. CI, release and versioning

CI stages: formatting/lint -> unit/codegen -> upstream toolchain contract -> native build -> ABI/dependency audit -> wheel install test -> thread/GC/stress -> platform matrix -> artifact manifest. Run on PRs and main with least privilege. Use Linux/macOS/Windows runners for P0; include both CPython modes, debug/sanitizer variants where feasible, stable build profile and optional diagnostic profile. Artifact build/test jobs must share immutable artifact hashes; do not rebuild silently between audit and publish. Nightly/periodic jobs may run heavy stress, fuzz and next Python version. A skipped required check is not green. Provide logs and artifacts for failures; no blanket `continue-on-error` for release gates.

Version Python distributions according to PEP 440 (e.g. `0.1.0a1` for alpha) with semantic-version intent; version Nox package and public ABI/IR schema separately. Document support window, deprecations and upstream compiler constraints. Pre-1.0 breaking changes still require changelog/migration notes. 1.x public APIs cannot break without declared major release policy; maintain a compatibility test suite. Never reuse published version/tag or silently replace artifacts. Changes to exported ABI use explicit version negotiation, append-only compatible fields or a new version. Pin minimum supported CPython ABI to 3.15 until maintainer decides otherwise; new APIs requiring a higher floor must use a compatible fallback or a separately labeled future profile.

Release checklist:

- [ ] Version, changelog, migration notes, support matrix and actual feature status updated.
- [ ] Exact upstream version, compiler/runtime artifact hashes and build configuration recorded.
- [ ] All required tests pass, including unforced GIL-state, concurrent calls and teardown.
- [ ] Stable ABI/dependency audit and platform compliance succeed on every shipped wheel.
- [ ] Same dual-tag wheel installs/imports in ordinary and free-threaded 3.15; no compiler needed.
- [ ] Sdist and PEP 517/660 hooks work for supported configurations; stubs and docs match implementation.
- [ ] No known critical memory safety/data race/ABI/security regression; limitations documented.
- [ ] Licenses/SBOM, checksums and provenance complete; package metadata validated.
- [ ] PyPI name and ownership confirmed, staging/dry-run verified, maintainer approved publication.
- [ ] Release evidence stored under `docs/evidence/` or attached CI artifacts with links; release summary truthful.

Never publish a wheel claiming free-threaded support if it depends on implicit GIL serialization or enables the GIL. If no platform meets gates, release source-only developer preview only with explicit maintainer approval and clear warnings; do not market it as production-ready.

## 20. Roadmap — phases and exit criteria

Each phase has a feature freeze, actual implementation, docs, tests and evidence. Do not jump to advanced integrations before foundations pass. Planned versions are not promised dates or existing releases. If upstream is blocked, work on isolated IR/config/packaging tests that do not falsely claim native functionality.

### Phase 0 — compiler/runtime feasibility

Tasks: inspect upstream, create minimal C and CPython reproducer, implement/obtain desktop PIC library mode and host ABI, foreign-thread attachment and error cleanup, symbol isolation, pin compatible compiler. Exit: all Section 6 evidence; P0 feasibility tracked independently; first real dual-mode import with Nox implementation; blockers recorded. No fake fallback.

### Phase 1 — 0.1 alpha: functions and wheel

Tasks: minimal C shim, typed export manifest/IR, primitive types, named arguments as implemented, module init/lifecycle, CLI `new`, `doctor`, `build`, `develop`, minimal PEP 517 backend, wheel packaging, hello/fastmath. Exit: clean installed actual wheel on at least one verified target, no-GIL import, parallel calls with correctness, failures and overflow handled; platform limitations explicit. Do not advertise full P0 until all pass.

### Phase 2 — 0.2: types and packaging

Tasks: collections/optional/tuples, strict conversions, metadata/stubs, docstrings/signatures, mixed packages, complete PEP 517 and sdist, PEP 660 editable, dependency locks/cache, pip/uv. Exit: deterministic codegen, negative/fuzz tests, isolated builds, editable rebuild docs, both interpreters; accurate type checker tests.

### Phase 3 — 0.3: native objects

Tasks: stable heap types, constructors, methods/properties, custom exceptions, per-instance synchronization, handle validation, GC traversal/clear, object lifecycle. Exit: same-instance mutation correctness, distinct instances overlap, partial init and finalizer tests, cycles either supported with evidence or explicitly rejected, no leak in covered scenarios.

### Phase 4 — 0.4: callbacks, async and isolation

Tasks: managed Python object/callable bridge, safe reentrant callbacks, asyncio futures/cancellation, iterator protocols, subinterpreter state and teardown. Exit: callbacks from supported threads, Python exception preservation, cancellation/shutdown stress, subinterpreter support flag matches tests, cross-interpreter handles rejected. Unsupported paths fail predictably.

### Phase 5 — 0.5: native data and Atlas

Tasks: typed buffers, memoryview, optional NumPy adapter, readonly/exclusive mutable views, profiling and baseline benchmarks; integrate a real Atlas operation. Exit: lifetime/stride/dtype tests, copy/zero-copy claims proven, benchmark raw results, Atlas package imports without enabling GIL in supported configuration.

### Phase 6 — 0.6: Arrow, Noxen and optional embed

Tasks: Arrow interoperability, Noxen validation example, other ecosystem adapters based on demand; standalone embedding as a separate gated profile, improved cross-platform support. Exit: interchange ownership tests, optional dependency compatibility, realistic production examples, embedding lifecycle evidence if included. Embedding may be deferred independently without compromising extension correctness.

### Phase 7 — 1.0 stable

Tasks: finalize stable API/config/ABI policy; finish P0 matrix, complete packaging, documentation and migration guide, security review, reproducibility and support policy; verified release process. Exit: all Section 19 gates; public supported feature matrix; no known release-blocking defects; no reliance on private ABI; independent integration examples from clean install. P1/P2/P3 ship only if verified. Optional uncompleted features remain explicitly experimental or deferred; never represent placeholders as stable functionality.

## 21. Agent workflow and coding rules

1. Read this file and relevant nested AGENTS.md, current git status, changelog, ADRs and test instructions. Identify phase, task boundaries, existing implementation, dependency contracts and exact upstream revision. Do not overwrite unrelated user changes.
2. Write a short task plan with acceptance checks. If blocked by upstream, capture evidence and propose the smallest supported change; no private ABI shortcut. Do not expand scope without necessity.
3. Implement vertical slices: real code + tests + docs. Use Nox idioms, typed functions, explicit errors, narrow native shim, small focused modules and clear names. Prefer standard library and existing utilities; justify each new dependency. Follow upstream formatting/typing rules and use actual tool commands discovered from repo. Do not invent compiler flags or APIs.
4. For every FFI/native change, document ownership, thread state, context, lock/callback potential, error semantics and cleanup. Review all exit paths, concurrent close and partial failures. Add regression tests for each fixed bug. Unsafe code requires a nearby safety explanation and reviewer attention.
5. Run relevant unit/integration tests and actual build; when native code changes run ABI/thread/lifetime checks. For release changes use the full matrix. Never delete or weaken tests merely to pass. Do not create dummy TODOs/stubs that report success. Explicitly gate unfinished features with clear errors and issue references.
6. Update README/docs/examples/changelog and generated stubs/snapshots when behavior changes. Make docs distinguish target, experimental and tested support. Keep public docs concise with real runnable examples. Document config migration for breaking changes.
7. Report changed files, implementation, exact commands/results, skipped/failed checks, safety review, decisions, blockers and next actionable step. Include artifact hashes and evidence when relevant. Do not claim work outside actual execution.

Code conventions: identifiers/docs/error codes in English; user-facing localization optional later. Use consistent names and versioned schemas. No magic constants without documented meaning; use typed bounds/lengths; prefer explicit ownership and state over hidden globals. Minimize allocations and copies only after correctness. Keep Python adapter thin and platform-neutral. Generated C should be deterministic, warning-clean under supported toolchains, auditable and use public APIs. Do not hand-edit generated artifacts unless updating fixture with reproducible generator output.

Repository hygiene: no credentials, private user data, huge binaries, cache artifacts or vendored dependencies without approval. No destructive git commands, commits/pushes/tags, release publication, external repository edits or license changes without explicit permission. Work on current requested scope; do not modify upstream Nox automatically. Review diffs and test before declaring completion.

Graphify (optional repository navigation aid): if already installed/configured, inspect graph and use it to locate call paths, ABI boundaries and dependencies; refresh after architectural changes with the project's documented command. Treat graph as a navigation aid, not authoritative evidence; inspect actual source and tests. Do not make Graphify a build/runtime dependency or install it silently. Do not commit generated graph artifacts unless project policy explicitly requires it.

### Definition of done for every task

- Requested behavior implemented, no fake completion, scope respected.
- Types/ownership/threading/error contracts documented where relevant.
- Tests added and executed; exact results or explicit blockers recorded.
- No regression in supported interfaces, build profiles or security invariants.
- Docs/stubs/examples synchronized; no unverified compatibility claims.
- Change set minimal and reviewable; user decisions and deviations reported.

### Recommended first agent assignment

Create an evidence-driven Phase 0 spike, not an entire framework scaffold. Read current upstream files, identify the actual desktop library and runtime entry contracts, record `noxc` version and commit, build a minimal native Nox arithmetic function callable from C, prove foreign-thread behavior, then build a CPython 3.15 `abi3t` module containing that Nox function. Import the same binary in 3.15 and 3.15t, check unforced GIL state, execute independent concurrent calls, inspect symbols and test teardown. If any step is impossible, stop that path and write a concrete upstream blocker with reproducer, required contract and proposed patch; do not substitute Python or C arithmetic. Commit no changes automatically. Report exact results and distinguish proof-of-concept from release quality.

## 22. Decision register and evidence template

Initial proposed ADRs (create as decisions are investigated):

- ADR-0001: CPython 3.15+ and dual Stable ABI policy.
- ADR-0002: Nox hosted library export ABI and compiler integration.
- ADR-0003: per-interpreter/module/thread runtime ownership and lock ordering.
- ADR-0004: binding metadata/IR, decorator and conversion semantics.
- ADR-0005: CPython shim, references, GC and mixed object cycles.
- ADR-0006: PEP 517 backend, bootstrap and package distribution.
- ADR-0007: class concurrency and callback/async strategy.
- ADR-0008: buffers, NumPy and Arrow ownership contracts.
- ADR-0009: platform support, release evidence and dependency policy.

Each ADR: status (proposed/accepted/superseded), context, options, decision, consequences, security/thread/lifetime impact, compatibility, alternatives rejected and verification evidence. Do not treat the proposed architectural defaults here as tested implementation.

For each phase/release, record:

```text
Task / phase:
Commit / upstream revision:
Compiler + runtime digests:
Python version / build mode / runtime GIL state:
OS / architecture / linker / ABI flags:
Exact commands and exit codes:
Artifacts / SHA-256:
Tests passed / failed / skipped:
ABI and dependency audit:
Concurrency / lifetime evidence:
Known limitations / blockers:
Decisions / ADRs:
Next action:
```

## 23. Normative references

Use these primary references and the upstream sources in Section 2. Check the exact version being targeted; do not rely on moving `latest` docs when a 3.15 API contract is required.

- CPython 3.15: https://docs.python.org/3.15/
- Free-threaded extension guide: https://docs.python.org/3.15/howto/free-threading-extensions.html
- abi3t migration: https://docs.python.org/3.15/howto/abi3t-migration.html
- C API stability: https://docs.python.org/3.15/c-api/stable.html
- Extension module export: https://docs.python.org/3.15/c-api/extension-modules.html
- Module API: https://docs.python.org/3.15/c-api/module.html
- Thread states: https://docs.python.org/3.15/c-api/threads.html
- Isolating extensions: https://docs.python.org/3.15/howto/isolating-extensions.html
- Buffer protocol: https://docs.python.org/3.15/c-api/buffer.html
- PEP 703 / 779 (free threading): https://peps.python.org/pep-0703/ ; https://peps.python.org/pep-0779/
- PEP 803 / 793 (stable ABI / export hook): https://peps.python.org/pep-0803/ ; https://peps.python.org/pep-0793/
- PEP 489 / 573 / 630 / 697 (module/type isolation): https://peps.python.org/pep-0489/ ; https://peps.python.org/pep-0573/ ; https://peps.python.org/pep-0630/ ; https://peps.python.org/pep-0697/
- Packaging PEPs 517, 518, 621, 660, 561, 440: https://peps.python.org/
- PyPA wheel format: https://packaging.python.org/en/latest/specifications/binary-distribution-format/
- PyPA tags: https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/
- PyPA project metadata: https://packaging.python.org/en/latest/specifications/pyproject-toml/
- NumPy documentation: https://numpy.org/doc/stable/
- Arrow C Data Interface: https://arrow.apache.org/docs/format/CDataInterface.html
- Arrow C Stream Interface: https://arrow.apache.org/docs/format/CStreamInterface.html

**Final invariant:** NoxPy is successful when Python developers can ship genuine Nox-native extensions with familiar typed syntax, correct ownership, actual parallel execution, standard packaging and verified free-threaded support. Never trade these guarantees for a superficial demo, an inaccurate wheel tag or an undocumented runtime hack.
