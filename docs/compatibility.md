# Compatibility matrix

This table is normative for the `0.1.0rc1` candidate. “Workflow target” means the repository has
an executable CI definition; it is not evidence that hosted CI ran. Local evidence and remaining
external gates are distinguished in `reports/p5/packaging-release.md`.

## Semantic and interchange contracts

| Contract | `0.1.0rc1` value | Compatibility rule |
|---|---|---|
| Projector API | `PROJECTOR_API_VERSION = 1` | Additions may ship in `0.1.x`; incompatible call semantics require a major API value. |
| Reference profile | `mowl-d993536-v1` | Frozen Scala-observed edge bag; changed behavior requires a new named profile. |
| Edge artifact | `pyowl-projector.edge-list/1` | Readers reject unsupported major schemas; backend selection is excluded from portable bytes. |
| Batch sink | `BATCH_SINK_PROTOCOL_VERSION = 1` | Synchronous immutable tuple batches; returning is backpressure acknowledgement. |
| Compiler cache | `pyowl-projector.compiler-cache/1` | Cache keys include core fingerprints/versions, profile, normalized options, schema, and package version. |
| Core model | `pyowl-core>=0.1,<0.2` | The exact shared `OntologyView` is consumed by identity; no source path or Python pickle handoff. |

`0.1.0b1` and `0.1.0rc1` use identical values in every row. Portable edge artifacts remain
byte-compatible when the core fingerprints and normalized semantic options are the same.

## Interpreter and artifact targets

| Target | Universal fallback | Native candidate | Evidence required before final release |
|---|---:|---:|---|
| CPython 3.10–3.13 | `py3-none-any` | `cp310-abi3` | Clean installed smoke on every interpreter. |
| Linux x86_64 | yes | manylinux | cibuildwheel plus auditwheel. |
| Linux aarch64 | yes | manylinux | Native runner/emulation parity plus auditwheel. |
| macOS x86_64 | yes | `macosx` | Clean 3.10/3.12 local smoke and hosted 3.10–3.13 matrix. |
| macOS arm64 | yes | `macosx` | Native runner parity plus delocate. |
| Windows x86_64 | yes | `win_amd64` | Clean 3.10–3.13 smoke plus delvewheel. |
| Other Python 3.10+ platform | yes when compatible | none | Pip must select the universal wheel without invoking a compiler. |
| musllinux | yes when pure Python is usable | not claimed in `0.1.0rc1` | Add only after a dedicated build and binary audit. |
| PyPy/free-threaded CPython | unclaimed | unclaimed | Requires dedicated compatibility policy and tests. |

Every platform wheel also contains the complete Python backend. The sdist defaults to the same
fallback and neither probes nor invokes Cargo. `PYOWL2VEC_BUILD_NATIVE=1` is the only supported
way to request a native source build.

## Backend behavior

| Request | Native extension state | Result |
|---|---|---|
| `backend="python"` | any | Complete Python backend; no fallback warning. |
| `backend="native"` | usable | Native bounded edge-policy processing with identical ordered output. |
| `backend="native"` | absent/unusable | `NativeBackendUnavailableError` with the load cause. |
| `backend="auto"` | any in `0.1.0rc1` | Python plus one first-projection `NativeBackendFallbackWarning`; native remains experimental because its performance gate is unmet. |

Import never emits the warning. A compiler or native-extension failure never disables the
complete Python projector.

## Semantic option compatibility

All eight historical boolean combinations of `bidirectional_taxonomy`, `only_taxonomy`, and
`include_literals`, both duplicate policies, both order policies, and both compatibility-state
modes retain their P2–P4 behavior. `project_taxonomy` remains the separate, unambiguous asserted
taxonomy API.
