# pyOwl2Vec-Star-projector v1 projection profile / pyOWLCore 0.2 contract

## 1. Objective

The package compiles an immutable OWL ontology snapshot into OWL2Vec*-style `(source,
relation, destination)` string edges without starting, embedding, or shelling out to a JVM. It
has four equal requirements:

1. faithful, named compatibility with a pinned mOWL Scala implementation;
2. one shared OWL model in-process, with no duplicate parse or structural copy;
3. deterministic and memory-bounded operation on large biomedical ontologies; and
4. identical observable semantics from native and pure-Python backends.

The distribution name is `pyowl2vec-star-projector`; the import package is
`pyowl2vec_star_projector`. The supported interpreter range begins at Python 3.10.

## 2. Scope

The version-1 projection profile projects the TBox, RBox, ABox, and selected class annotations supported by the
pinned Scala target. It includes imports already resolved into the supplied snapshot. It exposes
a dedicated asserted-taxonomy projection for Exact-OM in addition to the historical
`only_taxonomy` flag, whose upstream behavior is misleading.

The following are deliberately out of scope:

- parsing or owning a second OWL object model;
- reasoning, classification, materialization, or inferred-axiom generation;
- silently resolving network imports;
- repairing or normalizing the caller's ontology;
- Java, JPype, ROBOT, OWLAPI, mOWL, or Scala in a released environment; and
- changing `pyowl_core` records to make an upstream projector quirk easier to emulate.

Reasoner-derived axioms may be projected only after a caller explicitly constructs a new core
snapshot or overlay. The projector itself never invokes pyELK or pyHermiT.

## 3. Architecture

```text
path / bytes / binary stream                 existing in-process snapshot
             |                                            |
             v                                            |
pyowl_core.coerce_snapshot(...)                            |
             |                                            |
             +--------------------+-----------------------+
                                  v
            exact same pyowl_core.OntologyView identity
       (Snapshot, persistent Overlay, or zero-copy Composite)
                                  |
                     profile-local compiler plan
                     (no model mutation or copy)
                         /                   \
              complete Python backend     Rust/PyO3 backend
                         \                   /
                          deterministic edge sink
```

`Projector.project(view)` is the canonical boundary and requires `pyowl_core.OntologyView`.
It MUST retain and read that exact object; it MUST NOT serialize, materialize an overlay,
round-trip, reconstruct, or path-reparse it. For the normal concrete
`pyowl_core.OntologySnapshot` case, object identity is a release-blocking assertion. Shared lazy
views are obtained from the view's own/base cache. A convenience `project_source(...)` accepts
the complete core `OntologyInput` surface, calls
`pyowl_core.coerce_snapshot(...)` exactly once, and delegates.

Compilation creates only projector-private indexes needed by the selected rules. Compatibility
ordering, subrole defects, multiplicity behavior, and lifecycle emulation live in the compiler
or its options. They never alter core axioms, fingerprints, ordinals, imports, or indexes.

## 4. Compatibility profile

The required profile identifier is:

```text
mowl-d993536-v1
```

It pins mOWL commit `d9935369144f9a618ece38b7b2a8f4293afe8c26`, target source blob
`a7f7584bbe687ae341cf0547bc0492ada87cf4b8`, OWLAPI `4.5.22`, Scala `2.11.12`, and the
rule/defect catalogue in `reference-behavior.md`.

A profile is more precise than a package version. Fixing a compiler crash without changing
edges may be a patch release under the same profile. Any intentional edge-set or multiplicity
change MUST introduce a new profile and keep the old profile available for at least the rest of
the `0.x` line.

## 5. Options

The public immutable `ProjectionOptions` has these version-1 fields:

| Field | Values | Default | Contract |
|---|---|---|---|
| `profile` | registered profile ID | `mowl-d993536-v1` | pins rule semantics |
| `bidirectional_taxonomy` | `bool` | `False` | adds `http://superclassof` only where upstream does |
| `only_taxonomy` | `bool` | `False` | preserves the pinned upstream flag, including its defects |
| `include_literals` | `bool` | `False` | enables the pinned selected class-annotation path (literal or IRI values) |
| `duplicates` | `preserve`, `unique` | `preserve` | bag parity or stable de-duplication |
| `order` | `canonical`, `encounter` | `canonical` | canonical sort or deterministic compiler encounter order |
| `compatibility_state` | `isolated`, `scala-instance` | `isolated` | safe invocation state or explicit replay of upstream leakage |
| `backend` | `auto`, `native`, `python` | `auto` | implementation selection only; never semantics |

All booleans require real `bool` values. Integers such as `0` and `1` are rejected. Unknown
profile IDs, options, and enum values fail before traversal.

`compatibility_state="isolated"` creates invocation-local inverse/subrole state and is the
deterministic production contract. `"scala-instance"` exists only to reproduce repeated calls
on one mutable upstream Scala object; cache use is disabled, the ordered call history is part of
provenance, and concurrent calls on the instance are rejected. It is not selected implicitly by
the compatibility profile.

`project_taxonomy(snapshot, bidirectional=False, ...)` is a separate, unambiguous operation. It
emits only named `SubClassOf` edges (and optional reverse edges) and MUST NOT be implemented by
setting the historically defective `only_taxonomy` flag.

## 6. Edge semantics

An `Edge` is an immutable triple of Unicode strings: `source`, `relation`, and `destination`.
IRIs are emitted in their core canonical string form. Annotation destinations follow the pinned
profile's lossy string conversion; the edge type intentionally does not invent RDF literal
metadata that the reference never emitted.

The reference Scala `Triple` has identity equality, so its final `.distinct` does not remove
equal-looking triples. Consequently, `duplicates="preserve"` preserves the exact edge
multiplicity (bag semantics). `duplicates="unique"` removes repeated triples after rule
expansion and is the compatibility setting for Exact-OM's current local projector.

Canonical order is the ascending bytewise UTF-8 order of `(source, relation, destination)`.
Duplicate edges remain adjacent and retain their count. No locale, hash seed, OS directory
order, thread schedule, or backend may affect output.

## 7. Imports and documents

The projector traverses the resolved import closure present in the snapshot and does not ask a
resolver for additional documents. Standalone loading delegates import policy to
`pyowl_core.load_snapshot` through `coerce_snapshot`; callers must provide an explicit resolver
for non-local imports. Missing, blocked, or cyclic import diagnostics originate in the core and
remain attached to projection provenance.

Projecting the same closure obtained from a direct snapshot and from a serialized wire snapshot
MUST produce identical canonical artifact bytes when fingerprints, profile, and options match.

## 8. Backends

The pure-Python backend is the semantic reference implementation and MUST implement every
profile rule, option, streaming mode, and diagnostic. It is not a reduced fallback. The native
backend consumes public core views through a reviewed zero-copy or bounded-copy bridge and MUST
not define its own ontology records.

When a compatible `EncodedStructuralView` is available, the optimized native path follows
`native-structural-ingestion.md`: Rust performs complete rule traversal from public structural
columns/segments and emits bounded edge batches without scalar Python axiom expansion. The
pure-Python compiler remains complete, and encoded support does not change profile semantics.

Backend identity is observable only through diagnostics/provenance and performance. Edges,
ordering, multiplicities, exceptions caused by invalid ontology constructs, and artifact bytes
must otherwise agree exactly.

## 9. Concurrency and lifecycle

`Projector` is re-entrant and safe for concurrent isolated calls against one immutable snapshot.
It never mutates the snapshot or its shared lazy views. Each iterator owns its projector-private
buffers and temporary files. Closing, exhausting, cancelling, or garbage-collecting an iterator
releases those resources.

The explicit `scala-instance` mode is stateful and therefore neither concurrent nor cacheable.
The implementation must reject overlapping calls rather than race.

## 10. Release invariants

A released artifact MUST satisfy all of the following:

- install and run with Java absent from `PATH` and no `JAVA_HOME`;
- support every maintained CPython version from 3.10 onward according to the wheel matrix;
- install a usable pure-Python backend from both wheel and sdist without a Rust toolchain;
- produce backend-identical deterministic goldens;
- expose package/API, profile, edge artifact, compiler cache, core model, and wire versions in
  provenance; and
- contain no runtime dependency or optional extra on mOWL, ROBOT, DeepOnto, JPype, OWLAPI,
  HermiT Java, or ELK Java.
