# API reference

Everything documented here is importable from the top-level `pyowl2vec_star_projector` package
and is covered by `PROJECTOR_API_VERSION = 1`: compatible additions may ship within a minor line,
while incompatible call semantics require a new major API value. The package ships `py.typed`; all
public values are fully typed and the option/result records are frozen dataclasses.

The normative contract is [`specs/contracts.md`](../specs/contracts.md); this page is a
navigational summary.

## Core types

### `Edge`

One OWL2Vec* edge: frozen, slotted, ordered, value-comparable.

| Attribute / method | Description |
|---|---|
| `source: str`, `relation: str`, `destination: str` | Edge fields; strictly `str`-validated. |
| `canonical_key() -> tuple[bytes, bytes, bytes]` | Locale-independent UTF-8 ordering key. |
| `as_tuple() -> tuple[str, str, str]` | Plain-tuple view. |

### `ProjectionOptions`

Frozen, strictly validated configuration. Invalid values raise
`InvalidProjectionOptionsError`; an unknown profile raises `UnsupportedProfileError`.

| Field | Default | Meaning |
|---|---|---|
| `profile` | `"mowl-d993536-v1"` | The only supported compatibility profile in `0.2.x`. |
| `bidirectional_taxonomy` | `False` | Also emit reverse taxonomy edges. |
| `only_taxonomy` | `False` | The historical projector-local taxonomy mode (with its pinned reference defect; prefer `project_taxonomy` for clean asserted taxonomies). |
| `include_literals` | `False` | Include supported literal edges. |
| `duplicates` | `"preserve"` | `"preserve"` keeps historical bag multiplicity; `"unique"` deduplicates exactly. |
| `order` | `"canonical"` | `"canonical"` is deterministic global UTF-8 order; `"encounter"` is lower-latency arrival order. |
| `compatibility_state` | `"isolated"` | `"isolated"` gives independent calls; `"scala-instance"` replays the reference implementation's mutable per-instance role-state lifecycle across calls. |
| `backend` | `"auto"` | `"auto"`, `"python"`, or `"native"` — see [backend behavior](compatibility.md#backend-behavior). |

`to_dict()` returns the normalized JSON-compatible record used in provenance and cache keys.

## Projecting from a shared core view

### `Projector`

Compiles shared `pyowl_core.OntologyView` objects (snapshots, overlays, composites) by identity —
no reparsing or ontology materialization. One instance may be reused; `scala-instance`
compatibility state is keyed to the instance.

All projection methods take the view first plus keyword-only `options: ProjectionOptions | None`.
The streaming entry points additionally accept:

- `buffer_edges: int = 250_000` — maximum in-memory distinct keys before spilling,
- `temp_directory: PathLike | None` — parent for the private spill workspace,
- `streaming_limits: StreamingLimits | None` — explicit resource bounds,
- `cancellation_token` — any object with a `check() -> None` method that raises to cancel.

| Method | Returns | Description |
|---|---|---|
| `project(view, *, options=None)` | `list[Edge]` | Materialize the full projection. |
| `project_with_report(view, *, options=None)` | `ProjectionResult` | Edges plus the completed report. |
| `iter_edges(view, *, options=None, buffer_edges=..., ...)` | `Iterator[Edge]` | Backpressured iterator with bounded canonical spill. |
| `project_to_sink(view, sink, *, options=None, batch_size=65_536, ...)` | `ProjectionReport` | Push bounded immutable batches to a sink. |
| `write_artifact(view, destination, *, options=None, ...)` | `EdgeArtifactResult` | Stream a portable version-1 JSONL artifact (path or binary stream destination). |
| `canonical_digest(view, *, options=None, ...)` | `CanonicalEdgeDigest` | Hash canonical JSON edge records in one traversal. |
| `project_taxonomy(view, *, bidirectional=False, duplicates="preserve", order="canonical", backend="auto", ...)` | `list[Edge]` | The separate asserted named-class taxonomy (no `only_taxonomy` defect). |
| `iter_taxonomy_edges(view, *, ...)` | `Iterator[Edge]` | Streaming form of the taxonomy API. |

Diagnostic properties (thread-safe, describing the most recent completed operation):

| Property | Description |
|---|---|
| `last_view` | The exact view object last consumed (identity-preserving hook for integration tests). |
| `last_report` | The most recent `ProjectionReport`, or `None`. |
| `last_spill_metrics` | Path-free `SpillMetrics` for the most recently active iterator. |

### Sinks

`project_to_sink` accepts either form (`EdgeBatchSink` is the union type):

- a plain callable `(tuple[Edge, ...]) -> object`, or
- an `EdgeBatchSinkV1` protocol object: `protocol_version: int` (must be
  `BATCH_SINK_PROTOCOL_VERSION == 1`) and `write_batch(batch: tuple[Edge, ...])`, with an optional
  `finish(report)` that receives the completed report.

Batches are immutable and delivery is synchronous: returning from `write_batch` is the
backpressure acknowledgement. A sink that raises cancels the projection and cleans up spill state.

## Projecting standalone inputs

These free functions accept the complete `pyowl_core.OntologyInput` contract (paths, streams,
bytes, existing views, provider results) and delegate coercion to
`pyowl_core.coerce_snapshot(...)` exactly once. Existing views retain concrete identity; format
detection, imports, resolvers, cancellation, and loader errors remain owned by core. `load_options`
and `resolver` pass through to core.

| Function | Returns | Description |
|---|---|---|
| `project_source(source, *, options=None, load_options=None, resolver=None)` | `list[Edge]` | One-shot convenience projection. |
| `iter_source_edges(source, *, options=None, load_options=None, resolver=None, buffer_edges=..., ...)` | `Iterator[Edge]` | Streaming form. |
| `write_edge_artifact(view, destination, *, options=None, ...)` | `EdgeArtifactResult` | Artifact writer for an existing shared view without keeping a `Projector`. |
| `project_taxonomy(source, *, bidirectional=False, ..., load_options=None, resolver=None)` | `list[Edge]` | Module-level asserted-taxonomy convenience. |
| `iter_taxonomy_edges(source, *, ...)` | `Iterator[Edge]` | Streaming form. |

## Streaming controls

### `StreamingLimits`

Frozen resource bounds for spill-backed iteration. `None` means unlimited.

| Field | Default | Meaning |
|---|---|---|
| `merge_fan_in` | `32` | Maximum runs merged per pass. |
| `max_open_files` | `64` | Open-file ceiling for merges. |
| `max_total_edges` | `None` | Hard cap on projected edges. |
| `max_spill_bytes` | `None` | Cumulative spill-write ceiling. |
| `max_temporary_bytes` | `None` | Live temporary-space ceiling. |
| `cancellation_check_interval` | `4_096` | Edges between cancellation-token checks. |

Exceeding a limit raises `ProjectionResourceError`. The spill workspace is a random mode-`0700`
directory with mode-`0600` checksummed run files, removed on exhaustion, close, cancellation,
failure, or interpreter shutdown.

### `SpillMetrics`

Path-free accounting: `runs_created`, `merge_passes`, `peak_live_bytes`, `total_spill_bytes`.

## Results, reports, and provenance

| Type | Contents |
|---|---|
| `ProjectionResult` | `edges: tuple[Edge, ...]` plus `report`. |
| `ProjectionReport` | `provenance: ProjectionProvenance` plus grouped `diagnostics: tuple[ProjectionDiagnostic, ...]`. |
| `ProjectionProvenance` | Normalized `options`, `selected_backend`, `source_kind`, `core: CoreProvenance`, `counts: ProjectionCounts`, projector/API/schema versions, diagnostics digest, invocation count and call-history digest (for `scala-instance` replay), and `ingestion: IngestionProvenance`. |
| `CoreProvenance` | Core package/API/model/wire/adapter versions, structural/logical/signature fingerprints, import-manifest digest, closure document identities, loader-diagnostics digest. |
| `ProjectionCounts` | `edges`, `duplicates`, `skipped_axioms`, `ignored_shapes`, `warnings`. |
| `IngestionProvenance` | Selected ingestion path (e.g. scalar vs. encoded-native), reason, encoded schema name/version, phase durations, and bounded per-operation counters. Execution diagnostics are excluded from portable artifact hashes, and reading them never requests another encoded view from core. |
| `EdgeArtifactResult` | `artifact_sha256`, `canonical_edges_sha256`, `edge_count`, `duplicate_count`, `bytes_written`, `metadata`, `report`. |
| `CanonicalEdgeDigest` | `sha256`, `edge_count`, `duplicate_count`, `report`. |
| `ProjectionDiagnostic` | Stable `code`, `message`, `severity` (`"info"`/`"warning"`), grouped `count`, optional `constructor`. The profile's intentionally ignored OWL shapes surface here as data instead of stdout noise. |

## Errors and warnings

All failures derive from `ProjectionError`, which carries a stable machine-readable code.

| Exception | Raised when |
|---|---|
| `UnsupportedProfileError` | `ProjectionOptions.profile` is not a supported named profile. |
| `InvalidProjectionOptionsError` (also `ValueError`) | An option value fails strict validation. |
| `UnsupportedAxiomShapeError` | The pinned profile defines a typed failure for the input shape (e.g. the expected inverse-property assertion case). |
| `NativeBackendUnavailableError` | Explicit `backend="native"` with no usable extension; carries the load cause. |
| `SnapshotCompatibilityError` | The core view's API/model/wire/adapter/encoded versions are outside the pinned compatibility window — fails before traversal. |
| `ProjectionResourceError` | A streaming limit, temp-space, or I/O resource failure. |
| `ConsumerConformanceError` (also `AssertionError`) | A consumer violated the versioned identity/semantic handoff contract. |

Warnings: `ProjectionWarning` (base `UserWarning` category) and `NativeBackendFallbackWarning`
(emitted once per process at first `backend="auto"` projection while Python remains the measured
default — never at import time).

## Backend inspection

| Function / type | Description |
|---|---|
| `probe_native_backend() -> NativeBackendStatus` | `available`, `implementation_version`, `reason`, `auto_preferred` — without emitting warnings. |
| `select_backend(requested, *, probe=None) -> BackendSelection` | The dispatch decision: `requested`, `selected`, `fallback_reason`. |

## Consumer conformance kit

The versioned handoff gate (`CONSUMER_CONFORMANCE_SCHEMA = "pyowl-projector.consumer-conformance/1"`)
verifies that an integration consumes the shared snapshot correctly. See the
[migration guide](migration.md) for the workflow.

| Export | Description |
|---|---|
| `consumer_conformance_fixture() -> bytes` | The packaged CC0 functional-syntax ontology. |
| `consumer_conformance_fixture_metadata() -> ConsumerFixture` | Document IRI, axiom/signature counts, pinned core fingerprints. |
| `consumer_conformance_cases()` / `consumer_conformance_case(case_id)` | The three deterministic goldens: Exact-compatible OWL2Vec* (`"exact-owl2vec"`), literal, and dedicated-taxonomy settings. |
| `verify_consumer_conformance(view, *, case_id="exact-owl2vec", backend="python", identity_probes=None, required_ingestion_path=None)` | Runs the probe: exactly one `SnapshotProvider` call, zero path/stream/origin accesses, exact snapshot identity, unchanged fingerprints/counts, frozen edge bytes and provenance. Returns `ConsumerConformanceResult`; violations raise `ConsumerConformanceError`. |
| `SnapshotProviderProbe`, `IdentityProbe` | The provider wrapper and lazy-view identity probe types; use `identity_probes={"labels": lambda: source.labels_view}` to assert consumer-owned views retain identity. |
| `canonical_edges_sha256(edges)` | Canonical digest of an edge sequence. |

## Version and schema constants

| Constant | `0.2.0` value |
|---|---|
| `__version__` | `"0.2.0"` |
| `PROJECTOR_API_VERSION` | `1` |
| `REFERENCE_PROFILE` | `"mowl-d993536-v1"` |
| `EDGE_ARTIFACT_SCHEMA` | `"pyowl-projector.edge-list/1"` |
| `BATCH_SINK_PROTOCOL_VERSION` | `1` |
| `COMPILER_CACHE_SCHEMA` | `"pyowl-projector.compiler-cache/1"` |
| `CONSUMER_CONFORMANCE_SCHEMA` | `"pyowl-projector.consumer-conformance/1"` |
| `INGESTION_PROVENANCE_SCHEMA` | `"pyowl-projector.ingestion/1"` |
| `CORE_API_VERSION` | `(0, 2)` |
| `CORE_MODEL_SCHEMA_VERSION` | `2` |
| `CORE_WIRE_FORMAT_VERSION` | `(1, 2)` |
| `CORE_ADAPTER_PROTOCOL_VERSION` | `1` |
| `ENCODED_SCHEMA_NAME` / `ENCODED_SCHEMA_VERSION` | `"pyowl-core/structural-columns"` / `2` |

The compatibility rules governing each constant are in the
[compatibility matrix](compatibility.md).
