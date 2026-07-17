# P6 projector-side consumer conformance

Date: 2026-07-17. Projector candidate: `0.1.0rc1` plus additive P6 changes.

## Outcome

The projector-owned P6 deliverables are implemented. The distribution ships a versioned
`pyowl-projector.consumer-conformance/1` kit containing a CC0 Functional Syntax fixture, three
canonical Exact-compatible goldens, provider/no-reparse instrumentation, identity/fingerprint/
count checks, provenance assertions, and pure/native parity checks. No Java component, parser,
Exact module, OAEI module, or reasoner enters the package.

The cross-repository P6 acceptance is intentionally not mislabeled as complete: Exact's deletion
of its local compiler and migration of `OwlOntologySource` and optional reasoners belong to
Exact's `WP-M-shared-owl-stack.md`. This repository supplies the executable gate and confirms the
current baselines, but does not patch or import Exact.

## Frozen consumer kit

The packaged `consumer.ofn` is SHA-256
`84e1b6cf5088cbb7ea4276f26b5b461a4cfe74aed257abbab20ba78c1136ef0d` and is dedicated
under CC0-1.0. Loaded from its documented bytes/IRI it has 13 axioms, eight signature entities,
and structural fingerprint
`443361ca770168b6676a729820f98da38bfdebc8b073ecac90cba07f882130e8`.

The original P6 capture used pyOWLCore
`de9b8f9717cd31050bc0123cc2ba62ff0e63aa3d` and recorded structural fingerprint
`c892d33f03273022cae0018e13a7afebb32d95005eb332a4b645a8c1859166fd`. The P5 release baseline
uses `d546da77f4402ac4cda08a480c8798c37601b16c`, which contains the reviewed canonical-identity
correction `884b6a96024d701d3669936f9c2ac169d7adff39`: acquisition bytes and parser provenance no
longer contribute to canonical ontology identity. It also contains behavior-preserving strict
mypy and duplicate-RDF-operand parser corrections. Only the structural fingerprint changed from
the original P6 capture. The logical fingerprint, signature fingerprint, ordered edge lists,
multiplicities, and all three edge digests remain identical. `release/core-compatibility.json`
records the transition and the source commit used by CI.

| Case | Edges | Canonical edge-record SHA-256 |
|---|---:|---|
| `exact-owl2vec` | 7 | `80866e23827159cd5312cb0e267c5e012371476561db0f394a3d97a11055f602` |
| `exact-owl2vec-literals` | 8 | `ed09a9e55221a6ef533c2f61a05628741946b6fdf61a0c1b45ed051d7660ed76` |
| `exact-taxonomy` | 1 | `8a1e0c6b8aead777c4992648f9f075d3e9269ae2c7fbfea739770050566bddd5` |

`SnapshotProviderProbe` returns the supplied view while counting `owl_snapshot()` calls. Its path
protocol, stream/path reads, open, path, and origin accessors raise `ConsumerConformanceError` and
increment a source-access counter. `verify_consumer_conformance` coerces that provider once and
requires one provider call, zero source accesses, exact view identity, unchanged core provenance
and axiom/signature counts, unchanged registered consumer lazy-view identities, exact ordered
edges/digest, and provider-aware OWL2Vec* provenance.

## Exact 2.0 preservation

`tools/compare_exact_baselines.py` loaded each Exact fixture once through `pyowl-core`, projected
the same snapshot through ordinary and literal-enabled OWL2Vec* with `duplicates="unique"`,
canonical order and isolated state, and used the dedicated asserted-taxonomy API. Both committed
WP-B compressed captures match byte order and content exactly:

| Fixture | OWL2Vec* | With literals | Taxonomy | Differences |
|---|---:|---:|---:|---:|
| `mini_src` | 42 | 76 | 28 | 0 |
| `mini_tgt` | 42 | 76 | 28 | 0 |

Every observed digest equals its baseline digest; no baseline was changed and the difference
classification is `none`. Exact machine-readable fingerprints and digests are in
`evidence/exact-baselines.json`.

## Dependency and performance evidence

`tools/check_dependency_dag.py` statically inspects runtime imports and all PEP 621/Poetry base and
optional dependencies. It rejects projector dependencies/imports of Exact, OAEI, pyELK, or
pyHermiT, and rejects OAEI dependencies/imports of Exact or the projector. CI checks the current
OAEI repository; the release gate checks the projector boundary independently.

The refreshed load-excluded handoff benchmark ran 25 samples after three warm-ups. Provider
projection added 4.4–12.4% at these sub-2.4 ms fixture scales, called the provider exactly once
per projection, never accessed a source, and produced the same digest for Python 3.10, Python
3.12, and the native backend. Full summary values are in `evidence/consumer-handoff.json`; raw
samples are emitted by `benchmarks/benchmark_consumer_handoff.py` for reproducible host-specific
collection.

## Verification commands

```text
python -m pytest -q
python -m ruff check src tests tools benchmarks _build_backend.py setup.py
python -m ruff format --check src tests tools benchmarks _build_backend.py setup.py
MYPYPATH=../pyOWLCore/src python -m mypy src
python tools/audit_runtime.py
python tools/check_dependency_dag.py --oaei-root ../OAEI-Bio-ML-eval
python tools/compare_exact_baselines.py --exact-root ../Exact-OM
python benchmarks/benchmark_consumer_handoff.py --backend python
```

Fallback wheel/sdist and native-wheel gates additionally assert that all conformance resources
are installed and that default build/install paths remain compiler- and Java-free.
