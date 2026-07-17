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

The Python backend is complete. A future work package may add an equivalent optimized Rust/PyO3
backend:

- `backend="python"` selects the complete, compiler-free fallback explicitly and quietly;
- `backend="auto"` prefers a compatible native wheel and otherwise warns once before using
  Python; and
- `backend="native"` fails clearly while no native extension is installed.

If automatic selection cannot load the native extension, the package uses Python and emits one
actionable warning per process. Explicitly selecting Python is quiet; explicitly selecting native
fails instead of silently changing backend.

Start with the [specification index](specs/README.md). The normative API and behavior live in
[`SPEC.md`](specs/SPEC.md), while the observed Scala quirks that must remain projector-local are
catalogued in [`reference-behavior.md`](specs/reference-behavior.md).

## Status

Planned initial release: `0.1.0`.

`0.1.0a2` implements WP-P2. All 184 pinned Scala invocations match in canonical edge bytes,
including the expected typed inverse-property assertion failure and the loader-owned missing-
import outcome. Normal tests, installs, wheels, and sdists remain Java-free. Bounded external
canonical sorting and a native backend remain isolated future work packages; encounter-order
iteration already streams edge production after the identity-preserving plan scan.

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

For low-latency consumption, set `order="encounter"` and use `iter_edges`; for bounded delivery,
use `project_to_sink` with a batch callback. `project_taxonomy` is the separate asserted named-
class taxonomy API and does not inherit the historical `only_taxonomy` defect.

Standalone inputs use the core facade exactly once:

```python
from pyowl2vec_star_projector import project_source

edges = project_source("ontology.ofn")
```

`project_source` accepts the full `pyowl_core.OntologyInput` contract. Existing snapshots,
overlays, composites, decoded wire views, and `SnapshotProvider` results retain concrete identity;
format detection, imports, resolvers, cancellation, and loader errors remain owned by core.
