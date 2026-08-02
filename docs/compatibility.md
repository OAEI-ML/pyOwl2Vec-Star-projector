# Compatibility matrix

This table is normative for `0.2.0`. “Workflow target” means the repository has an executable CI
definition; publication still requires the tag-scoped hosted run to pass. Accepted residual risks
remain explicit in `release/external-gates.json`.

## Semantic and interchange contracts

| Contract | `0.2.0` value | Compatibility rule |
|---|---|---|
| Projector API | `PROJECTOR_API_VERSION = 1` | Compatible additions may ship within the `0.2.x` line; incompatible call semantics require a major API value. |
| Reference profile | `mowl-d993536-v1` | Frozen Scala-observed edge bag; changed behavior requires a new named profile. |
| Edge artifact | `pyowl-projector.edge-list/1` | Readers reject unsupported major schemas; backend selection is excluded from portable bytes. |
| Batch sink | `BATCH_SINK_PROTOCOL_VERSION = 1` | Synchronous immutable tuple batches; returning is backpressure acknowledgement. |
| Compiler cache | `pyowl-projector.compiler-cache/1` | Cache keys include core fingerprints/versions, profile, normalized options, schema, and package version. |
| Consumer conformance | `pyowl-projector.consumer-conformance/1` | Packaged CC0 fixture/goldens; incompatible fixture or assertion changes require a new schema major. |
| Core package | `pyowl-core>=0.2,<0.3` | The exact shared `OntologyView` is consumed by identity; no source path or Python pickle handoff. |
| Core API/adapter | API `(0, 2)`, adapter `1` | The projector rejects another API line or adapter protocol before traversal. |
| Core model/wire | model `2`, wire `(1, 2)` | Model-1 fingerprints and stale wire/cache artifacts are not silently reinterpreted. |
| Core encoded view | `pyowl-core/structural-columns` schema `2` | Native direct ingestion requires the frozen schema-2 descriptor digest; otherwise whole-operation scalar fallback or a typed compatibility failure applies. |

Source-checkout CI is pinned to pyOWLCore 0.2.0 commit
`422a63363e0b67857eebfca6dd67595ebaad7a09`, tree
`56dc47d73870a786a1248d89bf10a89155fcffff`, as recorded in
`release/core-compatibility.json`. This is reproducibility evidence for the release, not a Git
dependency: installed metadata intentionally retains `pyowl-core>=0.2,<0.3`. Model schema 2
changes the fixture's structural, logical, and signature fingerprint domains. The projector's
three canonical edge sequences and digests remain unchanged and are repinned alongside those
new core fingerprints. The historical direct core successor
`005c3ccad129757b3a9be125dc064b812b607ef5` is recorded only as comparator release evidence:
the compatibility checker verifies its ancestry and exact comparator-only diff while keeping the
historical runtime pin on the paired implementation revision.

`0.1.0b1`, `0.1.0rc1`, `0.1.0`, and `0.1.1` remain one historical compatibility line. `0.2.0`
requires pyOWLCore's coordinated API/model/wire/encoded-schema transition. Portable edge
artifacts remain byte-compatible because the profile and edge schema are unchanged, while
compiler caches and provenance records are regenerated under the new core identity.

## Interpreter and artifact targets

| Target | Universal fallback | Native candidate | Evidence state |
|---|---:|---:|---|
| CPython 3.10–3.13 | `py3-none-any` | `cp310-abi3` | Clean installed smoke on every interpreter. |
| Linux x86_64 | yes | manylinux | cibuildwheel plus auditwheel. |
| Linux aarch64 | yes | manylinux | Native runner/emulation parity plus auditwheel. |
| macOS x86_64 | yes | `macosx` | Clean 3.10/3.12 local smoke and hosted 3.10–3.13 matrix. |
| macOS arm64 | yes | `macosx` | Native runner parity plus delocate. |
| Windows x86_64 | yes | `win_amd64` | Clean 3.10–3.13 smoke plus delvewheel. |
| Other Python 3.10+ platform | yes when compatible | none | Pip must select the universal wheel without invoking a compiler. |
| musllinux | yes when pure Python is usable | not claimed in `0.2.0` | Add only after a dedicated build and binary audit. |
| CPython subinterpreters | host-runtime dependent | deliberately unavailable | `auto` falls back before extension import; clean full-projection smoke is an external gate. |
| PyPy/free-threaded CPython | unclaimed | unclaimed | Requires dedicated compatibility policy and tests. |

Every platform wheel also contains the complete Python backend. The sdist defaults to the same
fallback and neither probes nor invokes Cargo. `PYOWL2VEC_BUILD_NATIVE=1` is the only supported
way to request a native source build.

PyO3 0.28 does not support loading its extension modules in CPython subinterpreters. Native
probing therefore rejects a subinterpreter (and a free-threaded build) before importing the
extension: `backend="auto"` selects Python and explicit `backend="native"` raises the typed
`NativeBackendUnavailableError`. A host may use the Python backend in a subinterpreter only when
that CPython build, its standard-library extension modules, and `pyowl-core` pass the complete
installed smoke. This workspace's Homebrew CPython 3.12 aborts even for a standalone `hashlib`
import during subinterpreter teardown, so it cannot provide that release evidence.

## Backend behavior

| Request | Native extension state | Result |
|---|---|---|
| `backend="python"` | any | Complete Python backend; no fallback warning. |
| `backend="native"` | usable | Negotiates the advertised encoded-native compiler first; supported schema-2 plans run there, while valid unadmitted plans select whole-operation scalar-native processing before output. |
| `backend="native"` | absent/unusable | `NativeBackendUnavailableError` with the load cause. |
| `backend="auto"` | any in `0.2.0` | Python plus one first-projection `NativeBackendFallbackWarning`; native remains experimental because its performance gate is unmet. |

Import never emits the warning. A compiler or native-extension failure never disables the
complete Python projector.

## Semantic option compatibility

All eight historical boolean combinations of `bidirectional_taxonomy`, `only_taxonomy`, and
`include_literals`, both duplicate policies, both order policies, and both compatibility-state
modes retain their P2–P4 behavior. `project_taxonomy` remains the separate, unambiguous asserted
taxonomy API.

The P6 Exact-compatible cases fix `duplicates="unique"`, canonical order, isolated state, and
the named profile. They add no Exact-specific field to `ProjectionOptions`; Exact's ABox filtering
and source-neutral edge facade remain consumer responsibilities.
