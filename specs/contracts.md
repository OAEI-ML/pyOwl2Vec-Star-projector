# Public contracts

## 1. Version constants

The package exports immutable constants with these initial values:

```python
PROJECTOR_API_VERSION = 1
EDGE_ARTIFACT_SCHEMA = "pyowl-projector.edge-list/1"
COMPILER_CACHE_SCHEMA = "pyowl-projector.compiler-cache/1"
REFERENCE_PROFILE = "mowl-d993536-v1"
```

`pyowl-core` separately owns its package SemVer, `MODEL_SCHEMA_VERSION`,
`WIRE_FORMAT_VERSION`, and `ADAPTER_PROTOCOL_VERSION`. The projector records but never aliases
or increments those values.

## 2. Python API

The following is the normative shape, not an implementation stub:

```python
from collections.abc import Iterator
from os import PathLike
from typing import Literal

from pyowl_core import ImportResolver, LoadOptions, OntologyInput, OntologyView

Backend = Literal["auto", "native", "python"]
DuplicatePolicy = Literal["preserve", "unique"]
EdgeOrder = Literal["canonical", "encounter"]
CompatibilityState = Literal["isolated", "scala-instance"]

class Edge:
    source: str
    relation: str
    destination: str

class ProjectionOptions:
    profile: str = "mowl-d993536-v1"
    bidirectional_taxonomy: bool = False
    only_taxonomy: bool = False
    include_literals: bool = False
    duplicates: DuplicatePolicy = "preserve"
    order: EdgeOrder = "canonical"
    compatibility_state: CompatibilityState = "isolated"
    backend: Backend = "auto"

class Projector:
    def project(
        self,
        view: OntologyView,
        *,
        options: ProjectionOptions | None = None,
    ) -> list[Edge]: ...

    def iter_edges(
        self,
        view: OntologyView,
        *,
        options: ProjectionOptions | None = None,
        buffer_edges: int = 250_000,
        temp_directory: PathLike[str] | None = None,
    ) -> Iterator[Edge]: ...

def project_source(
    source: OntologyInput,
    *,
    options: ProjectionOptions | None = None,
    load_options: LoadOptions | None = None,
    resolver: ImportResolver | None = None,
) -> list[Edge]: ...
```

`project_source` accepts the complete core `OntologyInput` contract, including snapshots,
overlays, composites, documents, paths, byte/text/binary sources as the core permits them, and an
object implementing `SnapshotProvider.owl_snapshot()`. It calls
`pyowl_core.coerce_snapshot(source, options=load_options, resolver=resolver)` once. If the source
is already a snapshot/overlay or provider, `coerce_snapshot` preserves the concrete object
identity and shared lazy-view cache. It then delegates to the strict view boundary. All source
ownership, format, stream-mode, import, and resolver rules belong to core; the projector neither
narrows nor reinterprets them.

The materialized result owns edges, not ontology structures. `iter_edges` is the required
large-ontology API. Dedicated `project_taxonomy` and `iter_taxonomy_edges` functions mirror the
same input and backend rules but use the unambiguous asserted-taxonomy compiler.

## 3. Input and handoff rules

### In-process

The canonical handoff is a `pyowl_core.OntologyView`. For a concrete
`pyowl_core.OntologySnapshot`, the projector MUST be testable with:

```python
snapshot = pyowl_core.load_snapshot(path, options=options, resolver=resolver)
edges = Projector().project(snapshot)
assert projector_debug.last_view is snapshot
```

Production code need not expose `projector_debug`; an instrumented test backend may establish
the assertion. A consumer such as Exact-OM implements `SnapshotProvider.owl_snapshot()` and
returns that same object. `OntologyOverlay` and `OntologyComposite` are sibling `OntologyView`
implementations, not snapshot subtypes; they are projected by identity and never implicitly
materialized.

### Cross-process

Python pickle, a temporary ontology path, and "send the original path and parse again" are
forbidden protocols. Producers use `pyowl_core.encode_snapshot(snapshot)` or a durable core wire
file. Consumers use `decode_snapshot(buffer)` or `open_snapshot(path, mmap=True, verify=True)`.
The core wire carries the closure/resolution manifest and fingerprint. Unsupported wire major
versions fail clearly; supported minor evolution follows the core contract.

IPC orchestration may transport wire bytes or a wire-file descriptor/path. Such a path is a
versioned core snapshot artifact, never an OWL source path. A cache key includes at least:

```text
(structural_fingerprint,
 core MODEL_SCHEMA_VERSION,
 core WIRE_FORMAT_VERSION,
 projector profile,
 normalized options,
 COMPILER_CACHE_SCHEMA,
 package version)
```

## 4. Output sinks and artifacts

The library supports three equivalent consumers:

1. materialized `list[Edge]`;
2. iterator consumption; and
3. a sink callback/writer that receives edge batches without constructing a list.

The version-1 portable artifact is UTF-8 JSON Lines with a mandatory first metadata record and
then edge records. Writers use `\n` on every platform and escape JSON canonically.

```json
{"schema":"pyowl-projector.edge-list/1","profile":"mowl-d993536-v1","options":{},"snapshot":{},"provenance":{}}
{"source":"http://example/A","relation":"http://subclassof","destination":"http://example/B"}
```

The metadata includes the core structural/logical/signature fingerprints, import-resolution
manifest digest, package and backend versions, all effective options, edge count, duplicate
count, warning summary, and artifact SHA-256. It MUST NOT include nondeterministic timestamps in
the hashed canonical payload. An optional envelope may record creation time outside that payload.

`duplicates="preserve"` artifacts retain repeated records. Consumers must not infer set
semantics from JSONL.

## 5. Determinism

`order="canonical"` compares raw UTF-8 bytes for all three fields. Python's locale comparison,
Rust's platform locale, object hashes, and dictionary/set traversal are not accepted ordering
sources. The deterministic compiler also assigns a canonical visit order before expansion.

`order="encounter"` means that deterministic compiler visit order; it does not mean incidental
container order. It exists for low-latency streaming. Canonical output may spill sorted runs and
merge them as specified in `performance-packaging.md`.

## 6. Errors and warnings

Public failures derive from `ProjectionError` and include:

- `UnsupportedProfileError`;
- `InvalidProjectionOptionsError`;
- `UnsupportedAxiomShapeError` only when a strict future profile requests rejection;
- `NativeBackendUnavailableError`;
- `SnapshotCompatibilityError`; and
- `ProjectionResourceError` for exhausted spill space or configured limits.

Pinned mOWL shapes that it ignores are ignored and optionally counted; they are not errors.
Loader/import/format exceptions remain typed core exceptions and are chained, not flattened.

`NativeBackendFallbackWarning` is emitted at the first actual projection in `backend="auto"`
when native loading failed. It is emitted at most once per process, even across projector
instances, and contains the explicit `backend="python"` quieting instruction. The warning must
not fire at module import, when Python was explicitly selected, or on a platform for which the
installed distribution intentionally contains no extension and the application has suppressed
the documented category. `backend="native"` raises instead.

## 7. Provenance

Every report/artifact can expose a serializable `ProjectionProvenance` containing:

- projector package version, API version, profile, compiler-cache schema, and normalized options;
- selected backend, native crate version/target/features or Python implementation version;
- core package/API/model/wire versions and snapshot fingerprints;
- closure document identities, resolution manifest digest, and loader diagnostics digest;
- edge, duplicate, skipped-axiom, ignored-shape, and warning counts; and
- whether the source arrived directly, through a provider, or through verified core wire.

It never records a secret resolver credential or assumes the original source path is portable.
Two runs can be semantically compared by fingerprints, profile, options, and schema without
matching machine-specific fields.

## 8. Stability

The `0.1` package may add fields with defaults, but it may not silently change profile behavior,
edge ordering, warning timing, or artifact bytes. A future default profile requires a documented
minor release and an opt-in period; removing an old profile requires a major release.
