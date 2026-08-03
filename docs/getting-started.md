# Getting started with pyOwl2Vec-Star-projector

## Install

pyOwl2Vec-Star-projector requires Python 3.10 or newer:

```bash
python -m pip install --upgrade pip
python -m pip install pyowl2vec-star-projector
```

The complete Python backend is compiler-free and Java-free. The only runtime dependency is
`pyowl-core`, which owns ontology loading.

## Project a file

Use `project_source` when one component owns loading. It accepts the full
`pyowl_core.OntologyInput` contract (a path, stream, bytes, or an existing view) and delegates to
core exactly once:

```python
from pyowl2vec_star_projector import ProjectionOptions, project_source

edges = project_source(
    "ontology.owl",
    options=ProjectionOptions(
        include_literals=True,
        order="canonical",
    ),
)
for edge in edges:
    print(edge.source, edge.relation, edge.destination)
```

Each `Edge` is a frozen `(source, relation, destination)` triple of strings with value equality
and deterministic ordering.

## Project a shared view

Use `Projector.project(view)` when the ontology has already been loaded by `pyowl-core` — for
example when a reasoner and the projector share one snapshot. The projector retains that exact
view by identity and does not parse, serialize, or copy it:

```python
import pyowl_core
from pyowl2vec_star_projector import ProjectionOptions, Projector

snapshot = pyowl_core.load_snapshot("ontology.owl")
projector = Projector()
edges = projector.project(snapshot, options=ProjectionOptions(include_literals=True))
assert projector.last_view is snapshot
```

Concrete `OntologySnapshot`, persistent `OntologyOverlay`, and zero-copy `OntologyComposite`
views are all accepted.

## Choose output behavior

`ProjectionOptions` controls the compatibility profile and output:

- `include_literals=True` includes supported literal edges.
- `bidirectional_taxonomy=True` emits reverse taxonomy edges.
- `only_taxonomy=True` selects the historical projector-local taxonomy mode, including its pinned
  reference defect. For a clean asserted named-class taxonomy, use `project_taxonomy` instead
  (below).
- `duplicates="preserve" | "unique"` controls multiplicity: `"preserve"` matches the reference
  implementation's historical bag semantics, `"unique"` deduplicates exactly.
- `order="canonical" | "encounter"` chooses deterministic global order or lower-latency
  encounter order.
- `backend="auto" | "python" | "native"` controls acceleration (see below).

All options are strictly validated at construction; a bad value raises
`InvalidProjectionOptionsError` immediately rather than failing mid-projection.

## Project just the taxonomy

`project_taxonomy` and `iter_taxonomy_edges` are a separate, unambiguous asserted named-class
taxonomy API. They do not inherit the historical `only_taxonomy` defect:

```python
from pyowl2vec_star_projector import project_taxonomy

taxonomy_edges = project_taxonomy("ontology.owl", bidirectional=True)
```

## Pick a backend

Every install contains the complete Python backend; supported platform wheels also carry an
optional Rust extension. Both produce identical edges, reports, and typed errors:

- `backend="python"` — explicit, quiet, always available.
- `backend="auto"` (default) — currently selects Python and emits one
  `NativeBackendFallbackWarning` per process, because native has not passed the performance gate
  required to become the default.
- `backend="native"` — uses the Rust extension, or raises the typed
  `NativeBackendUnavailableError` when no compatible extension is available. It never silently
  changes backend.

With a `0.2.0` native wheel, an explicit native request first negotiates the schema-2 encoded
compiler; supported ontology-view plans run there, while a valid unsupported plan falls back as
one complete operation before emitting edges. Malformed advertised encoded data fails instead of
falling back. Use `probe_native_backend()` to inspect availability without triggering warnings.
Details are in the [compatibility matrix](compatibility.md#backend-behavior).

## Stream large projections

Avoid materializing all edges by consuming the iterator. It is backpressured and spills sorted
runs to a private temporary workspace instead of holding everything in memory:

```python
from pyowl2vec_star_projector import Projector, StreamingLimits

projector = Projector()
edges = projector.iter_edges(
    ontology_view,
    buffer_edges=100_000,
    streaming_limits=StreamingLimits(
        merge_fan_in=32,
        max_open_files=64,
        max_total_edges=20_000_000,
        max_temporary_bytes=8 * 1024**3,
    ),
)
for edge in edges:
    consume(edge)
```

Temporary spill files are private (mode-`0700` directory, mode-`0600` files) and removed when
iteration completes, closes, is cancelled, or fails. Exceeding a configured limit raises
`ProjectionResourceError`. Pass a `cancellation_token` (any object with a raising
`check()` method) for cooperative cancellation.

For production pipelines, `project_to_sink` delivers bounded immutable batches to a callable or a
version-1 protocol sink and returns the completed report:

```python
class JsonlSink:
    protocol_version = 1

    def __init__(self, stream):
        self._stream = stream

    def write_batch(self, batch):
        for edge in batch:
            self._stream.write(f"{edge.source}\t{edge.relation}\t{edge.destination}\n")

report = projector.project_to_sink(ontology_view, JsonlSink(stream), batch_size=65_536)
```

## Write artifacts and digests

`write_artifact` streams a portable versioned JSONL artifact, and `canonical_digest` produces the
matching canonical edge digest — neither builds an in-memory edge list, and both traverse the
ontology only once:

```python
result = projector.write_artifact(ontology_view, "edges.jsonl", buffer_edges=100_000)
digest = projector.canonical_digest(ontology_view, buffer_edges=100_000)
assert result.canonical_edges_sha256 == digest.sha256
```

Artifact bytes are deterministic: they do not vary with backend selection or execution
provenance when the semantic inputs are identical.

## Read the report

Every completed projection publishes a `ProjectionReport` with provenance and grouped
diagnostics:

```python
result = projector.project_with_report(ontology_view)
report = result.report
print(report.provenance.selected_backend)      # "python" or "native"
print(report.provenance.counts.edges)          # total emitted edges
print(report.provenance.core.structural_fingerprint)
for diagnostic in report.diagnostics:
    print(diagnostic.code, diagnostic.count, diagnostic.message)
```

Intentionally ignored OWL shapes surface as structured diagnostics instead of stdout noise.
`report.provenance.ingestion` records which ingestion path ran (scalar Python versus
encoded-native) and its bounded counters; these execution diagnostics are excluded from portable
artifact hashes.

## Handle failures

All projector failures derive from `ProjectionError` and carry a stable machine-readable code.
The ones most worth catching:

- `SnapshotCompatibilityError` — the core view is from an incompatible pyOWLCore line; fails
  before traversal. See the [migration guide](migration.md).
- `UnsupportedAxiomShapeError` — the pinned profile defines a typed failure for this input.
- `NativeBackendUnavailableError` — explicit `backend="native"` with no usable extension.
- `ProjectionResourceError` — a streaming limit or temporary-space failure.

## Next steps

- The complete public surface: [API reference](api-reference.md).
- Version pins, platforms, and contracts: [compatibility matrix](compatibility.md).
- Upgrading or replacing an in-application projector: [migration guide](migration.md), including
  the packaged consumer-conformance kit.
