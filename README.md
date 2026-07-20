# pyOwl2Vec-Star-projector

`pyOwl2Vec-Star-projector` is a Java-free OWL2Vec* projection package for Python 3.10 and newer.
Its complete pure-Python compiler implements the pinned mOWL compatibility profile, including
historical bag multiplicity, role-map defects, lifecycle replay, annotation rendering, and
deterministic output. The Scala oracle is quarantined maintainer infrastructure and is never an
install, runtime, test, or release dependency.

The package will consume the same `pyowl_core.OntologyView` used by parsers, reasoners, and
callers—normally a concrete `OntologySnapshot`, and optionally a persistent `OntologyOverlay` or
zero-copy `OntologyComposite`. An in-process projection therefore does not parse, serialize,
materialize, or copy an ontology.
Standalone inputs remain available through an explicit convenience boundary which delegates the
complete `pyowl_core.OntologyInput` contract to `pyowl_core.coerce_snapshot(...)` once.

The first compatibility profile is pinned to mOWL's Scala
[`OWL2VecStarProjector.scala`](https://github.com/bio-ontology-research-group/mowl/blob/d9935369144f9a618ece38b7b2a8f4293afe8c26/gateway/src/main/scala/org/mowl/Projectors/OWL2VecStarProjector.scala)
at commit `d9935369144f9a618ece38b7b2a8f4293afe8c26`. Java is permitted only in a quarantined
development oracle that generates checked-in goldens. It is never an install, runtime, test, or
release dependency.

The Python backend is complete. `0.1.0rc1` also contains an equivalent optional Rust/PyO3 edge
engine:

- `backend="python"` selects the complete, compiler-free fallback explicitly and quietly;
- `backend="auto"` uses the measured default backend and warns once when Python is selected; and
- `backend="native"` selects the extension explicitly or fails clearly when it is unavailable.

The P3 real-corpus measurements did not meet the required 2x end-to-end threshold, so this beta
keeps native opt-in even when installed. This is a performance decision only: Python and native
produce the same ordered edges, multiplicities, diagnostics, and typed semantic errors. Explicit
Python is quiet; an unavailable explicit native request fails instead of silently changing
backend.

Start with the [specification index](specs/README.md). The normative API and behavior live in
[`SPEC.md`](specs/SPEC.md), while the observed Scala quirks that must remain projector-local are
catalogued in [`reference-behavior.md`](specs/reference-behavior.md).

## Status

Unpublished release candidate: `0.1.0rc1`; planned final release: `0.1.0`.

All 184 pinned Scala invocations match in canonical edge bytes,
including the expected typed inverse-property assertion failure and the loader-owned missing-
import outcome. The native edge-policy engine consumes bounded owned batches, stores no Python or
OWL objects, and P4 now applies global policy through bounded private runs rather than a complete
in-memory edge vector. Normal tests, installs, wheels, and sdists remain Java-free.

P5 supplies conditional compiler-free builds, platform workflow definitions, offline install
smokes, reproducibility/hash tooling, SBOMs, license inventory, compatibility tables, and release
instructions. Final publication is deliberately blocked until authenticated name ownership,
private-index selection, hosted matrices, signed provenance, current advisory audits, and release
corpora have evidence. See the [compatibility matrix](docs/compatibility.md), [migration
notes](docs/migration.md), [release procedure](RELEASING.md), and machine-readable
[external gates](release/external-gates.json).

P6 now ships a versioned consumer-conformance kit. Its CC0 fixture and three deterministic
goldens exercise Exact-compatible OWL2Vec*, literal, and dedicated taxonomy settings. The probe
accepts only a `SnapshotProvider` handoff: path, stream, or origin access raises a typed failure,
while successful verification asserts one provider call, exact snapshot identity, unchanged
fingerprints/counts, edge bytes, and provenance. The projector-side comparison against both
Exact-OM 2.0 WP-B mini-ontology captures has zero differences. Exact's consumer-side WP-M M0–M4
migration has now landed in its own repository without a reverse dependency; its M5 scale,
release-evidence, cleanup, documentation, and `2.1.0` gates remain open.

## Usage

Project an existing shared view without parsing or copying it:

```python
from pyowl2vec_star_projector import ProjectionOptions, Projector

projector = Projector()
edges = projector.project(
    ontology_view,
    options=ProjectionOptions(backend="python", include_literals=True),
)
assert projector.last_view is ontology_view
```

After successful consumption, `projector.last_report.provenance.ingestion` exposes the selected
path and its path-safe handoff diagnostics. It records monotonic encoded-view
publication/validation and compiler-setup durations plus bounded scalar-row, borrowed-buffer,
segment, posting, and staging-copy counters. Scalar paths publish the same counter vocabulary
with exact zero encoded values. These execution diagnostics are intentionally excluded from
portable artifact hashes, and reading them never asks core for another encoded view.

For low-latency consumption, set `order="encounter"` and use `iter_edges`; for bounded delivery,
use `project_to_sink` with a batch callback. `project_taxonomy` is the separate asserted named-
class taxonomy API and does not inherit the historical `only_taxonomy` defect.

Canonical iteration uses checksummed external runs. Tune bounded resources explicitly when an
application has tighter limits:

```python
from pyowl2vec_star_projector import ProjectionOptions, Projector, StreamingLimits

edges = projector.iter_edges(
    ontology_view,
    options=ProjectionOptions(backend="python", order="canonical"),
    buffer_edges=100_000,
    temp_directory="/private/projector-tmp",
    streaming_limits=StreamingLimits(
        merge_fan_in=32,
        max_open_files=64,
        max_total_edges=20_000_000,
        max_temporary_bytes=8 * 1024**3,
        max_spill_bytes=32 * 1024**3,
    ),
)
for edge in edges:
    consume(edge)
```

The iterator owns a random mode-`0700` workspace and mode-`0600` files. Exhausting, closing,
cancelling, failing, or normally shutting down removes them. Encounter mode is backpressured and
uses at most `buffer_edges` in-memory distinct keys before moving exact duplicate accounting to a
private bounded-cache disk index.

Write a portable JSONL artifact or calculate its canonical edge-record digest without building a
list or parsing again:

```python
result = projector.write_artifact(ontology_view, "edges.jsonl", buffer_edges=100_000)
digest = projector.canonical_digest(ontology_view, buffer_edges=100_000)
assert result.canonical_edges_sha256 == digest.sha256
```

Object sinks declare `protocol_version = 1` and implement `write_batch(tuple[Edge, ...])`; an
optional `finish(report)` receives the completed report. Existing callable batch sinks remain
supported. See the [P4 report](reports/p4/streaming.md) for artifact hashing, exact cleanup tests,
the million-axiom memory gate, and honest corpus availability notes.

Standalone inputs use the core facade exactly once:

```python
from pyowl2vec_star_projector import project_source

edges = project_source("ontology.ofn")
```

`project_source` accepts the full `pyowl_core.OntologyInput` contract. Existing snapshots,
overlays, composites, decoded wire views, and `SnapshotProvider` results retain concrete identity;
format detection, imports, resolvers, cancellation, and loader errors remain owned by core.

Consumer integrations can run the packaged handoff gate without Java or a native compiler:

```python
import pyowl_core
from pyowl2vec_star_projector import (
    consumer_conformance_fixture,
    consumer_conformance_fixture_metadata,
    verify_consumer_conformance,
)

fixture = consumer_conformance_fixture_metadata()
snapshot = pyowl_core.load_snapshot(
    consumer_conformance_fixture(),
    document_iri=fixture.document_iri,
    options=pyowl_core.LoadOptions(format=pyowl_core.DocumentFormat.FUNCTIONAL),
)
result = verify_consumer_conformance(snapshot, case_id="exact-owl2vec")
assert result.provider_calls == 1
assert result.source_accesses == 0
assert result.core_before == result.core_after
```

Use `identity_probes={"labels": lambda: source.labels_view}` to assert that consumer-owned lazy views
also retain object identity. The benchmark and Exact baseline comparator live under
`benchmarks/benchmark_consumer_handoff.py` and `tools/compare_exact_baselines.py`.

The successor P7 harness measures an already-loaded public view through the production projector
path and records first-edge/complete wall and CPU time, RSS, edge hashes, core operation deltas,
public ingestion phases/counters, and missing acceptance evidence:

```bash
PYTHONPATH=src:../pyOWLCore/src python benchmarks/benchmark_encoded_compiler.py ontology.ofn \
  --format functional --load-backend native --projector-backend native
```

Use `--require-encoded-native` only as a release-evidence gate. It fails unless every repetition
selects encoded-native and exposes a complete zero-forbidden-counter, zero-staging-copy,
released-GIL record; it never relabels scalar fallback as accelerated evidence.

## Optional native build

Every distribution contains the complete Python backend. The default build is always the
compiler-free universal fallback and requests no Rust build tooling. Build a platform wheel with
the pinned Rust accelerator by setting `PYOWL2VEC_BUILD_NATIVE=1`; the conditional PEP 517 backend
then installs `setuptools-rust==1.13.0` into the isolated build environment. Cargo and rustc must
already exist for this explicit path. The extension uses PyO3's `abi3-py310` API; the release
matrix covers CPython 3.10 through 3.13.

```bash
PYOWL2VEC_BUILD_NATIVE=1 python -m build --wheel
```

The Rust boundary owns only strings for edge batches. It never borrows, mutates, or retains a
`pyowl_core` view. Closing a projection iterator cancels and clears its processor; native panics
are contained and resource failures become stable projector exceptions. See the
[P3 report](reports/p3/native-backend.md) for parity, performance, memory, and binary evidence.
The [P4 report](reports/p4/streaming.md) covers bounded external sorting and artifacts.
