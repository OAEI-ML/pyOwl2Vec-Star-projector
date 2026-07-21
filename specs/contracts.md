# Public contracts

## 1. Version constants

The package exports immutable constants with these initial values:

```python
PROJECTOR_API_VERSION = 1
BATCH_SINK_PROTOCOL_VERSION = 1
EDGE_ARTIFACT_SCHEMA = "pyowl-projector.edge-list/1"
COMPILER_CACHE_SCHEMA = "pyowl-projector.compiler-cache/1"
CONSUMER_CONFORMANCE_SCHEMA = "pyowl-projector.consumer-conformance/1"
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

class StreamingLimits:
    merge_fan_in: int = 32
    max_open_files: int = 64
    max_total_edges: int | None = None
    max_spill_bytes: int | None = None
    max_temporary_bytes: int | None = None
    cancellation_check_interval: int = 4_096

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
        streaming_limits: StreamingLimits | None = None,
        cancellation_token: CancellationToken | None = None,
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

The version-1 object sink declares `protocol_version = BATCH_SINK_PROTOCOL_VERSION` and implements
`write_batch(tuple[Edge, ...])`. It may implement `finish(ProjectionReport)`. Calls are
synchronous: returning from `write_batch` is the backpressure acknowledgement. A plain callable
with the same batch argument remains supported.

The version-1 portable artifact is UTF-8 JSON Lines with a mandatory first metadata record and
then edge records. Writers use `\n` on every platform and escape JSON canonically.

```json
{"schema":"pyowl-projector.edge-list/1","profile":"mowl-d993536-v1","options":{},"snapshot":{},"provenance":{}}
{"source":"http://example/A","relation":"http://subclassof","destination":"http://example/B"}
```

The metadata includes the core structural/logical/signature fingerprints, import-resolution
manifest digest, package and backend semantic API versions, all semantic options, edge count,
duplicate count, warning summary, and artifact SHA-256. The execution-only `backend` selector is
excluded so equivalent Python/native execution has identical portable bytes; selected/requested
backend remains in `ProjectionReport`. It MUST NOT include nondeterministic timestamps in the
hashed canonical payload. An optional envelope may record creation time outside that payload.

`artifact_sha256` hashes canonical metadata JSON with `artifact_sha256` omitted, its newline, and
the exact edge records. `canonical_edges_sha256` hashes the edge records alone. This explicit
self-excluding preimage is verifiable and avoids a recursively self-hashed metadata field.

`Projector.write_artifact(view, destination, ...)` accepts a path or caller-owned binary writer
and returns counts, both digests, bytes written, metadata, and the projection report. Path output
is atomically replaced only after success. `Projector.canonical_digest(view, ...)` forces
canonical order and hashes edge records during the same ontology traversal.

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
- `SnapshotCompatibilityError`;
- `ConsumerConformanceError` for a failed versioned consumer handoff assertion; and
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

The versioned ingestion subrecord additionally carries execution-only
`encoded_view_publication_seconds` and `consumer_compile_seconds` values when measured, plus an
immutable allowlisted counter map. The map covers consumer-side scalar rows materialized,
borrowed/zero-copy encoded buffers and bytes, detached/indexed buffers, segment/referenced-view
and posting counts, bounded staging and total structural-copy bytes, parser/resolver/wire calls,
scalar axiom/term materializations, base flattening, per-row FFI, and whether compilation released
the GIL. Hidden native lifecycle checkpoints additionally report the retained subrole- and
inverse-property map sizes without exposing IRIs. Hidden bounded-cursor checkpoints additionally
report the native compiled-edge count, zero or nonzero complete output-vector edge count, configured
batch bound, native batch/boundary-call counts, and peak native buffered-output edges. The peak is
bounded by the configured batch and is zero before the first drain; it does not count retained
encoded input, role maps, or validation indexes. Kernel-v33 source evidence additionally requires
zero cursor emission attempts at preparation; output traversal begins at the first drain.
Kernel-v34 coarse-call evidence additionally records the fixed internal chunk bound, native chunk
count, zero complete Rust output-vector edges, and peak native coarse buffer. The returned Python
list remains the legacy call's materialized result. Kernel-v35 evidence additionally records zero
intermediate complete tuple-edge-list entries and requires both final `Edge` instances and the
final statistics object to be constructed before reusable role state commits. A failed final
factory publishes neither counters nor role-state changes. Kernel-v36 bounded-drain evidence
additionally records zero intermediate Python-list edges and requires the final bounded `Edge`
tuple to exist before cursor/counter commit. A final-edge factory failure leaves the drain exactly
retryable. Kernel-v37 session evidence requires the final statistics object to exist before the
batch session, compiler-finished state, or retained role transition is published. A failed
statistics factory leaves the session absent, its counters zero, and retained roles unchanged.
Zero operation counters assert that the projector handoff itself did not perform that work; they do
not describe acquisition completed before an existing view was supplied. The publication duration
includes public encoded-view acquisition and the Projector adapter's in-place validation. These
fields are monotonic, non-negative, path-free diagnostics; they do not enter portable artifact
bytes or semantic digests. Reading the report
cannot trigger another core view request.

It never records a secret resolver credential or assumes the original source path is portable.
Two runs can be semantically compared by fingerprints, profile, options, and schema without
matching machine-specific fields.

## 8. Stability

The `0.1` package may add fields with defaults, but it may not silently change profile behavior,
edge ordering, warning timing, or artifact bytes. A future default profile requires a documented
minor release and an opt-in period; removing an old profile requires a major release.

## 9. Consumer conformance

The additive `pyowl-projector.consumer-conformance/1` kit ships one CC0 Functional Syntax
fixture and deterministic Exact-compatible goldens for ordinary OWL2Vec*, literal-enabled
OWL2Vec*, and dedicated asserted taxonomy. Fixture bytes, expected fingerprints/counts, ordered
edge records, and canonical edge digests are package resources and MUST be present in wheels and
sdists.

`SnapshotProviderProbe` returns one caller-supplied view from `owl_snapshot()` and instruments
provider calls. Any path protocol, stream read, path open, or origin access raises
`ConsumerConformanceError`; this makes an attempted source reparse a typed test failure.
`verify_consumer_conformance(view, ...)` calls core coercion once through that probe and succeeds
only when:

- the provider is called exactly once and no source access occurs;
- the core and projector retain the exact supplied object identity;
- core fingerprints, axiom/signature counts, and registered consumer lazy-view identities remain
  unchanged;
- ordered edges and canonical edge-record SHA-256 match the selected golden; and
- OWL2Vec* provenance records provider source kind, the supplied core record, normalized
  Exact-compatible options, and exact counts.

The kit never imports Exact, OAEI, or reasoners and adds no consumer-specific projection option.
It is testing/migration infrastructure over the ordinary public view/provider contracts, not a
second ontology model or parser.
