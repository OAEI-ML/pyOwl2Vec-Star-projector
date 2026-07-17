# pyOwl2Vec-Star-projector

`pyOwl2Vec-Star-projector` is a Java-free OWL2Vec* projection package under implementation for
Python 3.10 and newer. WP-P0 now provides the installable typed foundation, immutable values,
strict options, provenance, backend-selection seam, and audits. It does not yet contain a
projection engine; WP-P1/P2 own the oracle and complete Python compiler.

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

Two equivalent implementations are planned:

- an optimized Rust/PyO3 backend selected by default when a compatible wheel is present; and
- a complete pure-Python backend installed from every wheel and sdist.

If automatic selection cannot load the native extension, the package uses Python and emits one
actionable warning per process. Explicitly selecting Python is quiet; explicitly selecting native
fails instead of silently changing backend.

Start with the [specification index](specs/README.md). The normative API and behavior live in
[`SPEC.md`](specs/SPEC.md), while the observed Scala quirks that must remain projector-local are
catalogued in [`reference-behavior.md`](specs/reference-behavior.md).

## Status

Planned initial release: `0.1.0`.

The `0.1.0a1` foundation API is implemented. Edge-producing APIs remain unavailable until their
work packages and compatibility gates are complete; the package does not return placeholder
edges.
