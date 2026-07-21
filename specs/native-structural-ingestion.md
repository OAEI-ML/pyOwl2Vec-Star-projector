# Encoded structural ingestion and complete native projection

Status: normative successor optimization for the implemented projector. It preserves
`mowl-d993536-v1`, all public edge/artifact contracts, deterministic streaming, and the complete
pure-Python fallback.

Implementation note (2026-07-20): this document is the acceptance design, not a statement that
the production Rust compiler exists. The broad internal structural-columns slice described in
section 2 is currently implemented in Python. Rust has only the private, unadvertised direct
exact-`bytes` unannotated named-class foundation recorded in the P7 report: declarations,
named taxonomy and simple named-property/named-filler restriction `SubClassOf`, named-only
`EquivalentClasses`, named `ClassAssertion`, named-property `ObjectPropertyAssertion` over named
individuals, skipped named-or-inverse `NegativeObjectPropertyAssertion`, and paired named
object-property domain/range roots. Unannotated named-or-inverse `SubObjectPropertyOf` and
`InverseObjectProperties` roots build the exact compatibility role maps used to expand restrictions
and domain/range products; equivalent/disjoint object-property sets and the seven object-property
characteristics are validated and skipped. Positive inverse assertions reproduce the pinned typed
reference failure. Normal projection still uses Python semantic compilation followed by the older
Rust edge-policy bridge. All annotations and property chains, anonymous assertion operands, complex
class expressions, mmap and segmented exporters, and every other constructor remain outside that
private Rust slice.

## 1. Objective

The optimized native backend MUST compile the caller's exact retained
`pyowl_core.OntologyView` from the public `EncodedStructuralView` into projector-private role
indexes and edge batches. It replaces scalar Python OWL traversal and the current partial native
edge-policy accelerator; it does not introduce another parser, public ontology model, or Java
dependency.

The native compiler owns the complete projection rule traversal for every pinned profile option.
It may allocate only projection state required by those rules. It MUST NOT recreate an
ontology-sized Rust structural graph, materialize all Python axioms, serialize the snapshot, or
call Python once per axiom or edge.

## 2. Capability negotiation and fallback

The projector validates `CoreCapabilities.encoded_view_schemas`, then requests
`EncodedStructuralView` through `OntologyView.view(...)`. It retains the view owner until the last
edge iterator/batch/session is closed and validates:

- schema name/version, core model schema, descriptor digest, scope/options, and structural
  fingerprint;
- little-endian tags/scalars, string and sequence offsets, buffer bounds/alignment, and all
  references; and
- direct, decoded, mmap, overlay, and composite segment manifests without flattening their bases.

The package never imports `pyowl_core._native`, borrows a private arena, or persists encoded dense
IDs. A scalar-only provider remains supported by the complete Python compiler. Explicit
`backend="native"` may use the existing scalar/batched compatibility bridge until encoded-native
passes its promotion gates; provenance names `scalar-python`, `scalar-native`, or
`encoded-native` ingestion. Malformed advertised encoded data fails before output and cannot
silently switch paths after partial emission.

Before any compiler dispatch, the public adapter checks closure scope, the bounded segment family,
fixed-width column pairing, monotone offsets, and root/node/item/scalar/posting references directly
over retained read-only byte views. This validation constructs no OWL value or dense-ID cache and
does not advertise or promote the unfinished encoded-native compiler path.

The current Python internal compiler slice additionally preflights canonical direct views containing only
declarations; `SubClassOf`, `EquivalentClasses`, `ClassAssertion`, and object-property
domain/range axioms over named classes, intersections/unions, and the validated restriction
envelope; direct `ObjectPropertyAssertion` axioms over named or anonymous individuals; and named
role axioms. Anonymous identifiers use the scalar
profile's `_:genid2147483648` sequence in canonical encoded-node order; the compiler validates but
does not expose or reinterpret their document scope and local key. For a single-document closure,
the slice also emits allowlisted class `AnnotationAssertion` edges in the pinned category order,
including raw string/plain values, IRI and anonymous values, malformed non-string rendering, and
the matching grouped diagnostics. With `include_literals=False` the same annotation rows are
preflighted but unobserved. Structurally valid ontology `Annotation` roots are always silent:
they emit no edge or diagnostic, cannot mutate role state, and anonymous values reachable only
from those roots do not participate in scalar-compatible blank-ID assignment.

For a canonical supported multi-document closure with visible annotation assertions, the compiler
requests and retains a second public `EncodedStructuralView` at `AxiomScope.ROOT`, resolves and
fully validates direct, overlay, or composite columns on both sides, and intersects its
annotation-assertion identities with the closure roots. Class-signature membership and blank IDs
still come from the closure. Missing public manifest metadata or unavailable root scope selects one
whole-operation scalar fallback before output; malformed auxiliary columns or segment graphs fail
closed.
Before root classification, the broad decoder validates nested annotation, supported recursive
class-expression, and recursive data-range graphs with iterative color walks. Valid depth is not
bounded by Python's call stack; self or transitive cycles fail before fallback selection or output.
Canonical root identity lengths and bytes are likewise traversed with explicit frames, so retained
root, overlay, and composite comparisons do not reintroduce graph recursion or build whole-node
canonical byte strings.
Named-subject `SubClassOf` axioms over
named-property/named-filler some, all, minimum, and maximum restrictions use the same role
expansion as domain/range edges. A one-level `ObjectInverseOf` property expression in those
restrictions reproduces the scalar helper's historical behavior by projecting through the
underlying named property IRI; it does not reverse the emitted edge. A single named class
paired by `EquivalentClasses` with an intersection or union of named classes and those restrictions
uses the pinned expression ordering and role expansion. Named-or-inverse
`SubObjectPropertyOf` and `InverseObjectProperties` axioms update the retained compatibility role
state in exact OWLAPI hash-set visitation order. Their projected state uses the underlying named
IRI while their distinct named/inverse OWLAPI expression hashes continue to control visitation and
overwrite behavior. `SubObjectPropertyOf` axioms whose subproperty is an ordered
`ObjectPropertyChain` of named or inverse property expressions are fully preflighted but do not
mutate that role state. Each increments `ignored_shapes` once without adding a grouped diagnostic,
matching the scalar role scan. Ignored chains still contribute to OWLAPI hash-set capacity, so
unrelated executable role axioms retain their exact scalar visitation and overwrite order.
For the hidden Projector candidate, one retained native state belongs to the same non-concurrent
`scala-instance` lock and call history as its Projector. Each successfully prepared call snapshots
the maps into the scalar-compatible shadow. If any stateful call selects a whole-operation scalar
compiler, that shadow becomes authoritative and later calls on the Projector cannot re-enter the
native state. Isolated calls never receive a retained handle. The public encoded feature remains
unadvertised until the rest of the production gates pass.
Annotations on every otherwise-executable declaration/logical axiom are
fully preflighted and remain part of canonical root identity, preserving annotated-variant
multiplicity and cross-table structural deduplication. Annotation property/value hashes contribute
to role-axiom visitation exactly as in the scalar compiler; nested annotation sets remain validated
but, matching OWLAPI 4.5.22, do not contribute to `OWLAnnotation.hashCode`. A role annotation value
whose anonymous key cannot reproduce the scalar Java-string hash selects whole-operation scalar
compilation before output. The slice emits direct ABox triples and streams restriction and
domain/range edges with that state through the existing projector `Edge` IR in caller-bounded
batches, without reconstructing core OWL values. The dedicated asserted-taxonomy API preflights
the same slice but emits only named-to-named `SubClassOf` edges.

For the hidden kernel-v33 iterator, caller-bounded means the Rust output itself is cursor-backed.
Exhaustive immutable structural, semantic, output-count, and capacity preflight publishes no
partial session on failure and starts the cursor with zero emission attempts; there is no complete
projected-edge vector or pre-publication output replay. A drain clones the small cursor state, owns
at most the configured edge count, and commits that state only after the final Python batch has
been constructed. Kernel v36 creates final `Edge` objects and their tuple inside that transaction,
without an intermediate Python tuple-edge list; factory failure restores the unchanged cursor for
an exact retry. Kernel v37 also constructs the final statistics object before installing that
session or committing its retained role-state transition; failure publishes neither. Kernel v38
constructs the final owner-holding iterator wrapper after those statistics but before the same
publication boundary; iterator allocation failure likewise publishes neither. GIL-detached
generation does not hold the output mutex; a raced close discards the unpublished bounded batch.
Kernel v34 routes the legacy private coarse call through the same cursor
in fixed 256-edge native chunks. That call still returns one materialized Python list, but retains
no complete Rust output vector. Kernel v35 constructs the final `Edge` instances and statistics
object inside that same transaction, without a second complete tuple-edge list; its reusable role
state commits only after those final objects are complete.
These facts remain hidden-checkpoint behavior, not public encoded-native acceptance.

Within that validated expression envelope, non-projecting combinations stay encoded-native and
reproduce scalar `MOWL_IGNORED_SHAPE` counts by root constructor: complex/complex and
named/aggregate `SubClassOf`, direct restriction or otherwise unsupported ordered
`EquivalentClasses` pairs, complex or anonymous-individual `ClassAssertion`, and complex
domain/range expressions. An inverse property in a domain/range axiom is likewise preflighted but
ignored once under the root constructor, matching the scalar named-property type check.
`DisjointClasses` canonical sets over the same validated class-expression envelope are fully
preflighted and skipped. Valid members outside that bounded envelope retain one whole-operation
scalar fallback.
`DisjointUnion` axioms with a named defined class and a canonical set over that same validated
class-expression envelope follow the same skipped, state-neutral path. A valid member outside the
bounded envelope retains one whole-operation scalar fallback before output.
`EquivalentObjectProperties` and `DisjointObjectProperties` sets over named or inverse property
expressions are fully preflighted and stay encoded-native, but the pinned projector never visits
them as role axioms. The unary named-or-inverse `FunctionalObjectProperty`,
`InverseFunctionalObjectProperty`, `ReflexiveObjectProperty`, `IrreflexiveObjectProperty`,
`SymmetricObjectProperty`, `AsymmetricObjectProperty`, and `TransitiveObjectProperty` constructors
follow the same non-projecting path.
Named `SubDataPropertyOf` axioms and canonical `EquivalentDataProperties` or
`DisjointDataProperties` sets are likewise fully preflighted and skipped without entering the
object-role closure.
`DataPropertyDomain` over a named data property and a class expression in the validated native
envelope follows that skipped-only path; other valid class expressions retain whole-operation
scalar fallback.
`DataPropertyRange` over a named data property and named datatype is also preflighted and skipped;
composite data ranges retain whole-operation scalar fallback.
Unary named `FunctionalDataProperty` axioms follow the same skipped-only native path.
`DatatypeDefinition` between named datatypes is preflighted and skipped; composite defining data
ranges retain whole-operation scalar fallback.
`HasKey` over a validated class expression, supported object-property expressions, and named data
properties is likewise preflighted and skipped; either property set may be empty, but not both.
Class expressions outside the bounded envelope retain whole-operation scalar fallback.
`SameIndividual` over a minimum-two canonical set of named or anonymous individuals is preflighted
and skipped while its anonymous members remain part of deterministic blank-ID assignment.
`DifferentIndividuals` follows the same individual-set, annotation, skip, and blank-ID contract.
`NegativeObjectPropertyAssertion` over a named or inverse object property and supported individuals
is likewise preflighted and skipped, retaining anonymous members for deterministic blank IDs.
`DataPropertyAssertion` over a named data property, supported individual, and validated literal
follows the same bounded skip contract, retaining literal-byte and anonymous-ID accounting.
`NegativeDataPropertyAssertion` follows the same data-assertion validation and skip contract.
`SubAnnotationPropertyOf` between named annotation properties is preflighted and skipped.
`AnnotationPropertyDomain` over a named annotation property and IRI is preflighted and skipped.
`AnnotationPropertyRange` follows the symmetric named-property/IRI skipped path.
Each distinct skipped-axiom root above increments `skipped_axioms`, contributes to the grouped
`MOWL_SKIPPED_AXIOM` diagnostic for its constructor, and leaves role state unchanged.

`SWRLRule` is different: it is a structural extension root, not an axiom root, and the scalar
projector does not traverse or diagnose it. The encoded compiler accepts the frozen tags 140--148
only as a structurally validated, non-executing envelope. Variables must reference IRIs; class
atoms use the validated native class-expression envelope; data-range atoms use named datatypes;
object-property atoms accept named or inverse properties; and the remaining data-property,
built-in, same-individual, and different-individual atom fields retain their frozen argument
types. Empty rule sets and empty built-in argument sequences are valid. Accepted rules emit no
edges, do not increment `skipped_axioms`, do not add a diagnostic, and cannot mutate role state.
Rule-only anonymous individuals are excluded from the scalar-compatible axiom blank-ID pool,
while classes mentioned by rules remain visible to the ontology signature used for selected class
annotations. Valid rule predicates outside the bounded class-expression envelope, including
complements, and composite data ranges select one whole-operation scalar fallback before output;
no SWRL inference or atom execution is claimed by this path.

`EquivalentClasses` still examines only the first two expressions in pinned expression order.
Consequently, an n-ary axiom whose first two expressions are named emits only that named pair and
silently ignores a later aggregate or restriction. Inverse object-property assertions remain on
the whole-operation scalar error path. Other constructors not semantically validated by this
bounded slice, including complex restriction fillers, complement expressions, and property
constructors outside the validated role envelope, continue to select whole-operation scalar
compilation before output.

One bounded segmented form uses the same compiler: an overlay-base manifest may reference a fully
revalidated canonical direct closure with `ALL` or sorted `EXCLUDE` root postings. It may either
have empty top-local columns or be followed by exactly one fully revalidated top-local delta
segment. A direct source stays on the original fast lane, with its existing counters and
order-changing-scope fallback. A canonical segmented overlay or composite source instead enters
the iterative resolver, so a top `EXCLUDE` removes only roots local to that immediate source while
preserving its inherited roots. The compilation retains every distinct referenced lease/owner,
applies postings before rule indexes and blank-ID assignment, and accepts anonymous scope maps
only when their composed leaf-to-effective mappings remain strictly canonical-order preserving.
For a delta, a bounded exact cursor memoizes canonical node lengths and streams canonical-model
bytes directly from each retained column table. A reusable canonical root merge combines the
selected base and local delta after scope remapping and structurally deduplicates equal roots
without reconstructing OWL values or allocating an ontology-sized canonical arena. Domain/range
indexes, role state, class signatures, and anonymous identifiers are then derived across every
resolved table.

The same cursor and arbitrary-group merge execute canonical composite manifests with at least two
token-sorted unique members and an optional nonempty top-local bridge. Member sources and
segmented top-overlay bases resolve recursively without flattening. `ALL` retains every resolved
source occurrence; `INCLUDE` selects only the posted roots local to the immediate referenced view;
and `EXCLUDE` removes only those source-local roots while preserving roots inherited by that view.
Anonymous scope maps compose from leaf scope to effective scope at each reference boundary before
final canonical merge and structural deduplication. Every distinct referenced table is fully
revalidated once, and every required lease/owner remains retained by the prepared compilation.
Explicit resolver frames bound traversal by manifest size rather than Python call-stack depth;
active-path identities still reject direct or transitive segment cycles before output.

A valid order-changing scope map selects whole-operation scalar-native compilation before output.
Malformed posting bounds/layout, scope rows, owner links, base/delta/member/bridge metadata, member
tokens, recursive cycles, local-root order, empty-local claims, or referenced columns fail closed.
Any other valid constructor or unsupported shape in the declaration/logical-axiom families also
selects scalar-native compilation for the complete operation before edge output. Test-visible
counters cover inspected roots/nodes/scalar bytes, silent ontology annotations, auxiliary
root-provenance roots/nodes,
supported axiom kinds, referenced/member segments, posting/scope rows,
source/delta/bridge/selected and deduplicated roots, canonical bytes compared, batches, raw edges,
and scalar fallbacks. This slice is intentionally absent from the native feature ledger; it does
not change public backend promotion.

## 3. Native projection compiler

```text
exact OntologyView identity
          |
          +-- scalar compatibility --> Python rule compiler --> edge stream
          |
          `-- EncodedStructuralView columns/segments
                       |
             Rust rule traversal + private role/inverse/subrole maps
                       |
            packed bounded edge batches / external-sort sink
                       |
            existing Edge, iterator, sink, digest and artifact APIs
```

The accepted Rust compiler MUST implement every `reference-rules.json` rule and all option interactions,
including compatibility-state lifecycle, bag multiplicity, annotation string conversion,
historical subrole/inverse behavior, ABox, dedicated taxonomy projection, encounter order, and
canonical order. Generated relation strings and compatibility labels are projector-owned; core
strings and structural values remain immutable.

Internal edges SHOULD use compact dictionary/string IDs while the owner is retained. The Python
boundary receives a bounded packed batch, not one callback per edge. Materialized `project()` may
create `O(E)` public `Edge` objects because its API promises a list; streaming, digest, and
artifact paths never require the complete edge list in Python or Rust.

Canonical sorting and uniqueness may reuse the P4 spill machinery or a parity-proven Rust
equivalent. Backend/buffer/fan-in/worker choices cannot change edge order, multiplicity, digests,
artifact bytes, cleanup, backpressure, or time-to-first-edge semantics.

## 4. Exact parity and cache identity

Scalar and encoded compilers expose test-only canonical rule counters and ordered/bag edge digests.
Exact equality is required for every oracle golden, option matrix, repeated Scala-instance call
sequence, generated constructor/permutation case, direct/wire/mmap view, overlay/composite, and
large licensed corpus.

Compiler caches include:

```text
(core structural fingerprint and segment/import manifest,
 core model and encoded-view schema/descriptor digest,
 projection profile and normalized semantic options,
 projector package/API/compiler-cache schema,
 native implementation version)
```

Backend selection is excluded from portable artifact semantics. Schema-local IDs cannot be used
outside the retained owner or as standalone cache identity.

## 5. Lifetime, safety, and failure

Acceptance evidence MUST cover buffer borrowing, iterator lifetime, re-entrancy,
`scala-instance` exclusion, thread movement, fork, cancellation, generator close, interpreter
shutdown, and panic conversion. Rust MUST release the GIL without Python callbacks and validate
count-derived allocations before growth. Failure before or during streaming MUST publish no
cache/artifact and MUST clean private spill resources under the existing P4 rules.

A copied structural column is allowed only for a measured alignment/ownership need and MUST be
reported by byte count. Accepted direct and mmap retained-native views MUST have zero
ontology-sized staging copy. Every completed production encoded projection MUST publish the selected path, monotonic public-view
publication/validation and compiler-setup durations, and the bounded copy/materialization ledger
through `ProjectionReport.provenance.ingestion`. Consumers such as Exact read that report; they do
not request, flatten, or decode an encoded view themselves. The report contains no paths, object
identities, pointers, or buffer addresses and remains outside portable artifact semantics.

## 6. Performance and memory gates

Benchmarks record separately:

1. encoded-view acquisition/validation;
2. projection compiler setup and private role indexes;
3. time to first edge and encounter throughput;
4. canonical/unique spill and artifact throughput;
5. parser, scalar materialization, buffer-call, edge-batch, copy/allocation, temporary-byte, RSS,
   and cleanup counters; and
6. complete shared-snapshot-to-artifact time used by Exact-OM.

Every comparison fixes the exact input view/fingerprints, profile/options, edge counter/digests,
cold/warm cache state, buffer/fan-in limits, workers, and output destination. It includes NCIT,
DOID, GO, the million-axiom synthetic case, and the largest licensed corpus available.

In addition to `performance-packaging.md`, encoded-native acceptance requires:

- zero parser/resolver/core-wire/scalar-axiom/base-flattening calls for an existing compatible
  view;
- no complete Python or Rust ontology-sized structural copy and no per-axiom/per-edge FFI;
- view validation plus Python/Rust boundary below 5% of load-excluded native projection time on
  each designated medium/large workload;
- encoded-native at least 2x faster than the scalar Python compiler on two large corpora, never
  more than 10% slower on the third, and at least 2x faster than the existing scalar-native path
  by geometric mean;
- no more than 10% incremental-RSS regression and continued satisfaction of the 1.5 GiB synthetic
  memory gate; and
- Exact-OM shared-stack reruns that remove the current projector regression while preserving the
  pinned rule-level semantic classification and output digests.

`auto` selects encoded-native only after these gates pass on supported wheels. Until then the
backend remains opt-in and documentation reports measured limitations.

## 7. Versions, packaging, and consumers

Implementation records the compatible core package/API/adapter and exact encoded schema range.
It increments `COMPILER_CACHE_SCHEMA` when private compilation/cache meaning changes, but does not
change the projection profile or artifact schema without a semantic/artifact change. Unsupported
caches rebuild rather than reinterpret IDs.

Pure wheels and compiler-free sdists remain complete on Python 3.10+. Native wheels pass the
existing ABI, sanitizer, dependency, reproducibility, license, and no-Java audits. The projector
does not depend on Exact-OM, OAEI, pyELK, or pyHermiT; Exact only passes its existing snapshot
identity and reads the public projection report.

## 8. Completion

Completion requires exact rule/edge/artifact parity, bounded streaming and lifetime safety,
direct/mmap/overlay/composite coverage, controlled time/RSS/copy evidence, promoted backend policy,
updated provenance/cache/docs/version ranges, and Exact-OM conformance over the exact released
core/projector revisions.
