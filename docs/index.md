# pyOwl2Vec-Star-projector documentation

pyOwl2Vec-Star-projector creates deterministic OWL2Vec* graph edges without
Java. It accepts standalone ontology inputs or an existing `pyowl-core` view so
applications can parse once and share the same immutable ontology with
reasoners and projectors.

```bash
python -m pip install pyowl2vec-star-projector
```

## Choose a guide

- [Getting started](getting-started.md) covers first projection, options,
  taxonomy projection, streaming, sinks, artifacts, backend selection, reports,
  and error handling.
- [API reference](api-reference.md) documents the complete public surface:
  `Projector`, the module-level convenience functions, options, streaming
  limits, results/provenance, errors, and version constants.
- [Compatibility matrix](compatibility.md) lists supported Python versions,
  artifact targets, semantic contracts, and backend behavior.
- [Migration guide](migration.md) explains the coordinated pyOWLCore/projector
  `0.2.0` upgrade, preserves guidance for earlier releases, and covers the
  consumer-conformance kit for replacing an in-application projector.
- The repository [README](../README.md) is the project overview: what the
  package is, quickstart, backends, status, and the full documentation map.

## For contributors and maintainers

- The [specification index](../specs/README.md) is the entry to the normative
  documents: [`SPEC.md`](../specs/SPEC.md) (API and behavior),
  [`contracts.md`](../specs/contracts.md) (Python API and artifact contracts),
  and [`reference-behavior.md`](../specs/reference-behavior.md) (the pinned
  Scala quirks that must remain projector-local).
- [Performance and correctness evidence](evidence.md) indexes the phase reports
  under `reports/` and preserves the private P7 checkpoint chronology.
- [Release procedure](../RELEASING.md) is for maintainers publishing signed
  artifacts; machine-readable gate closures live under
  [`release/`](../release/README.md).
- The [changelog](../CHANGELOG.md) records profile, packaging, and performance
  changes separately, because profile output is a data contract.

## Package names

| Purpose | Name |
|---|---|
| PyPI distribution | `pyowl2vec-star-projector` |
| Python import | `pyowl2vec_star_projector` |
| Shared ontology dependency | `pyowl-core` / `pyowl_core` |

The installed package never downloads or invokes the historical Scala/Java
oracle. That oracle is isolated maintainer infrastructure used only to create
checked-in compatibility evidence.
