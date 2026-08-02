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
  streaming, artifacts, and backend selection.
- [Compatibility matrix](compatibility.md) lists supported Python versions,
  artifact targets, semantic contracts, and backend behavior.
- [Migration guide](migration.md) explains the coordinated pyOWLCore/projector `0.2.0`
  upgrade and preserves guidance for earlier releases.
- The repository [README](../README.md) documents advanced streaming limits,
  consumer conformance, and performance evidence.
- [Release procedure](../RELEASING.md) is for maintainers publishing signed
  artifacts.

## Package names

| Purpose | Name |
|---|---|
| PyPI distribution | `pyowl2vec-star-projector` |
| Python import | `pyowl2vec_star_projector` |
| Shared ontology dependency | `pyowl-core` / `pyowl_core` |

The installed package never downloads or invokes the historical Scala/Java
oracle. That oracle is isolated maintainer infrastructure used only to create
checked-in compatibility evidence.
