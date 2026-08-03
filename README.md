# pyOwl2Vec-Star-projector

[![PyPI](https://img.shields.io/pypi/v/pyowl2vec-star-projector)](https://pypi.org/project/pyowl2vec-star-projector/)
[![Python](https://img.shields.io/pypi/pyversions/pyowl2vec-star-projector)](https://pypi.org/project/pyowl2vec-star-projector/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

`pyOwl2Vec-Star-projector` turns an OWL ontology into the deterministic graph edges used by
[OWL2Vec*](https://doi.org/10.1007/s10994-021-05997-6) embedding pipelines — without Java. It is a
complete, pure-Python reimplementation of mOWL's Scala
[`OWL2VecStarProjector`](https://github.com/bio-ontology-research-group/mowl/blob/d9935369144f9a618ece38b7b2a8f4293afe8c26/gateway/src/main/scala/org/mowl/Projectors/OWL2VecStarProjector.scala),
pinned commit-exactly as the frozen compatibility profile `mowl-d993536-v1` (including the
reference implementation's historical quirks, so existing pipelines get byte-identical edges).

Key properties:

- **Java-free.** Installation, runtime, tests, and releases never require a JVM. The Scala oracle
  that generated the checked-in compatibility goldens is quarantined maintainer infrastructure.
- **Deterministic.** Canonical output order, exact duplicate accounting, and stable canonical
  digests; portable artifacts are byte-identical across backends and platforms.
- **Shared-view handoff.** The projector consumes a `pyowl_core.OntologyView` (snapshot, overlay,
  or composite) *by identity* — it does not parse, serialize, materialize, or copy the ontology.
  Standalone file inputs go through one explicit convenience boundary, `project_source`.
- **Bounded memory.** Streaming iterators, batch sinks, and artifact writers spill to private
  checksummed temporary runs with explicit, tunable limits — projections of millions of edges do
  not need an in-memory edge list.
- **Optional Rust acceleration.** Supported platform wheels also ship a PyO3 `abi3` extension.
  It produces exactly the same edges, reports, and errors as the Python backend and is currently
  opt-in (see [Backends](#backends)).

## Installation

```bash
python -m pip install pyowl2vec-star-projector
```

Requires Python 3.10+ and depends only on `pyowl-core>=0.2,<0.3`. The default installation is
compiler-free: no Java, no Rust toolchain. The distribution name is `pyowl2vec-star-projector`,
the import name is `pyowl2vec_star_projector`; it is intentionally distinct from the unrelated
`owl2vec-star` distribution.

## Quickstart

Project a standalone ontology file:

```python
from pyowl2vec_star_projector import ProjectionOptions, project_source

edges = project_source(
    "ontology.owl",
    options=ProjectionOptions(include_literals=True),
)
for edge in edges:
    print(edge.source, edge.relation, edge.destination)
```

If the ontology is already loaded through `pyowl-core`, hand the shared view to a `Projector`
directly — it is retained by identity and never reparsed:

```python
from pyowl2vec_star_projector import ProjectionOptions, Projector

projector = Projector()
edges = projector.project(
    ontology_view,
    options=ProjectionOptions(backend="python", include_literals=True),
)
assert projector.last_view is ontology_view
```

`ProjectionOptions` controls the profile-compatible switches: `include_literals`,
`bidirectional_taxonomy`, `only_taxonomy`, `duplicates="preserve" | "unique"`,
`order="canonical" | "encounter"`, and `backend`. `project_taxonomy` /
`iter_taxonomy_edges` provide a separate, unambiguous asserted named-class taxonomy that does
not inherit the historical `only_taxonomy` defect.

See the [getting-started guide](docs/getting-started.md) for a walkthrough and the
[API reference](docs/api-reference.md) for the complete public surface.

## Streaming, artifacts, and digests

Large projections should never materialize an edge list. The iterator is backpressured and spills
to a private mode-`0700` workspace with mode-`0600` files, removed on exhaustion, close,
cancellation, failure, or shutdown:

```python
from pyowl2vec_star_projector import ProjectionOptions, Projector, StreamingLimits

projector = Projector()
edges = projector.iter_edges(
    ontology_view,
    options=ProjectionOptions(order="canonical"),
    buffer_edges=100_000,
    streaming_limits=StreamingLimits(max_total_edges=20_000_000),
)
for edge in edges:
    consume(edge)
```

- `project_to_sink` pushes bounded immutable batches to a callable or a version-1 protocol sink
  (`protocol_version = 1`, `write_batch(tuple[Edge, ...])`, optional `finish(report)`).
- `write_artifact` streams a portable versioned JSONL artifact; `canonical_digest` computes the
  matching canonical edge digest in one traversal:

```python
result = projector.write_artifact(ontology_view, "edges.jsonl", buffer_edges=100_000)
digest = projector.canonical_digest(ontology_view, buffer_edges=100_000)
assert result.canonical_edges_sha256 == digest.sha256
```

Every projection also publishes a `ProjectionReport` (via `projector.last_report` or
`project_with_report`) carrying provenance: selected backend, core fingerprints, edge counts,
grouped diagnostics, and ingestion-path counters. Execution diagnostics are intentionally excluded
from portable artifact hashes.

## Backends

Every distribution contains the complete Python backend; native wheels add the optional Rust
compiler. All backends produce the same ordered edges, multiplicities, diagnostics, and typed
semantic errors — backend choice is a performance decision only.

| `backend=` | Behavior |
|---|---|
| `"python"` | The complete, compiler-free backend. Explicit and quiet. |
| `"auto"` (default) | Currently selects Python and warns once (`NativeBackendFallbackWarning`), because the native end-to-end performance gate (2× on real corpora) has not passed. |
| `"native"` | Selects the Rust extension explicitly, or raises `NativeBackendUnavailableError` if no compatible extension is available — it never falls back silently. |

In `0.2.0`, native wheels advertise `encoded-structural-compiler-v1`: an explicit
`backend="native"` request negotiates pyOWLCore structural-columns schema 2 and runs supported
direct and segmented plans in the Rust compiler across iterator, sink, digest, artifact, and
asserted-taxonomy entry points. A valid but unsupported plan selects whole-operation scalar-native
processing before any output; malformed advertised metadata or buffers fail with a typed
compatibility error. Backend behavior details are in the
[compatibility matrix](docs/compatibility.md).

### Building the native wheel from source

The default source build is always the compiler-free universal fallback. To build a platform wheel
with the pinned Rust accelerator (PyO3 `abi3-py310`, CPython 3.10–3.13):

```bash
PYOWL2VEC_BUILD_NATIVE=1 python -m build --wheel
```

The conditional PEP 517 backend then installs `setuptools-rust==1.13.0` into the isolated build
environment; Cargo and rustc must already be present.

## Consumer conformance kit

Integrators replacing an in-application projector can run the packaged handoff gate
(`pyowl-projector.consumer-conformance/1`) without Java or a native compiler. It verifies exact
snapshot identity, single-provider-call handoff, unchanged fingerprints/counts, and frozen edge
bytes against a CC0 fixture and three deterministic goldens:

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

See the [migration guide](docs/migration.md) for the full integration checklist.

## Status

Production release: `0.2.0`.

- All 184 pinned Scala oracle invocations match in canonical edge bytes, including the expected
  typed inverse-property assertion failure and the loader-owned missing-import outcome.
- The Python backend is complete; the equivalent Rust backend stays opt-in because measured
  real-corpus speedups did not meet the 2× threshold required to make it the default.
- Releases ship the universal wheel, sdist, and five supported native wheels atomically through an
  environment-protected PyPI trusted publisher, with SBOMs, license inventory, reproducibility
  tooling, and machine-readable [external gates](release/external-gates.json).

## Documentation map

| Audience | Start here |
|---|---|
| Users | [Documentation index](docs/index.md) · [Getting started](docs/getting-started.md) · [API reference](docs/api-reference.md) |
| Integrators | [Compatibility matrix](docs/compatibility.md) · [Migration guide](docs/migration.md) |
| Contributors | [Specification index](specs/README.md) — normative behavior is [`SPEC.md`](specs/SPEC.md); pinned Scala quirks are [`reference-behavior.md`](specs/reference-behavior.md) |
| Maintainers | [Release procedure](RELEASING.md) · [Phase reports and evidence](docs/evidence.md) · [Changelog](CHANGELOG.md) |

The detailed benchmark-harness usage and the private P7 evidence-checkpoint chronology formerly in
this README are preserved in [docs/evidence.md](docs/evidence.md).

## License

Apache-2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
