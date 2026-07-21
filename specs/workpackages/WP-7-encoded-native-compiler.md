# WP-P7 — Encoded-view complete native compiler

**Target:** compatible `0.1.x` successor or next minor selected by implementation review.
**Depends on:** P3–P6 and a frozen pyowl-core WP17 encoded-view candidate.
**Status:** implementation checkpoint in progress; capability unadvertised and acceptance open.

## Current implementation checkpoint

As of 2026-07-21, the broad structural-columns decoder/compiler in
`encoded_compiler.py` is Python, and the production Rust backend still receives already-created
edge strings for policy processing. Neither is a complete Rust encoded compiler.

A private unadvertised PyO3 foundation now compiles one canonical direct, exact-`bytes` slice in
Rust: silent unannotated declarations, named-to-named `SubClassOf`, named-only n-ary
`EquivalentClasses`, named `ClassAssertion`, the named-property/named-filler some/all/min/max
restriction subset of `SubClassOf`, named-property `ObjectPropertyAssertion` over named individuals,
skipped named-or-inverse `NegativeObjectPropertyAssertion`, and paired named object-property
domain/range products. Unannotated named-or-inverse `SubObjectPropertyOf` and
`InverseObjectProperties` roots reproduce OWLAPI hash-set visitation, the historical sibling
overwrite, and last-inverse-wins behavior, expanding restrictions and domain/range products but not
direct assertions. Equivalent/disjoint object-property sets and all seven object-property
characteristics are validated skipped roots. Positive inverse assertions reproduce the pinned typed
reference failure. Equivalent classes use the first two expressions in UTF-8 IRI order.
`only_taxonomy` suppresses restriction edges but preserves named equivalence, class assertions,
positive object assertions, negative-assertion/property-family skips, and expanded domain/range,
while the optional asserted-taxonomy mode emits only the direct subclass family after preflighting
the whole supported slice. It retains the owner/exporters, preflights before allocating output,
releases the GIL, supports concurrent cancellation, and returns one caller-bounded coarse batch.
The later bounded checkpoints add the recursive class/data validation, skipped/silent families,
role-state behavior, selected annotation rules, and diagnostics enumerated in the implementation
report. Kernel v30 additionally retains an independent exact-direct root table and joins canonical
root `AnnotationAssertion` identities back to closure nodes before edge limits or publication, so
visible multi-document annotations preserve root-only selection and closure-wide blank IDs without
structural copying. Property-chain emission, other projecting constructors, segment traversal, and
sliced, mmap, or non-bytes exporters remain unsupported. The safe generic PyO3 buffer API is
unavailable at the current `abi3-py310` floor, so general mmap ownership remains an explicit design
blocker. Kernel v31 additionally rejects cycles in nested annotation metadata with an iterative
preflight over both closure and retained root tables. This closes a hostile structural-columns
case without advertising the encoded compiler or changing valid projection output. Exact installed
diamond and cyclic import cases additionally prove that the v30 join's canonical root subset and
closure deduplication remain correct beyond a one-level import. The broad Python structural decoder
now applies the same iterative acyclic-annotation invariant before root classification, including
when it independently inspects a retained root-provenance table. Its supported recursive
class-expression and data-range graph checks are iterative too, admitting valid deep columns
without weakening cycle rejection or expanding the advertised capability. Canonical identity
length calculation and incremental byte emission use explicit frames as well, preserving exact
cross-table comparison for deep valid graphs. Overlay and composite dependency resolution now uses
explicit frames too, admitting deep valid segment manifests while retaining source-local posting
semantics, owner lifetime, cache reuse, and active-path cycle rejection.

This checkpoint does not satisfy any deliverable that says complete, every, production, or full
matrix. `ENCODED_NATIVE_FEATURE` remains absent from the extension feature ledger. The exact
implementation and open ledger are recorded in `reports/p7/encoded-native-compiler.md`.

## Goal

Replace scalar Python ontology traversal and partial edge-policy acceleration with a complete Rust
projection compiler that consumes public encoded structural columns/segments and feeds the
existing bounded streaming/artifact APIs.

## Read first

- `../native-structural-ingestion.md`, `../SPEC.md`, `../contracts.md`,
  `../reference-behavior.md`, `../verification.md`, and `../performance-packaging.md`; and
- pyowl-core `native-ontology-redesign.md`, `indexes-views.md`, and WP17 schema/handoff evidence.

## Owned paths

- public-core encoded-view adapter and compilation-path diagnostics;
- Rust encoded decoder, full rule compiler, private projection indexes, and packed batch bridge;
- native/Python compiler comparator and encoded differential/lifetime/hostile-input tests;
- encoded projection and Exact handoff benchmarks/reports;
- directly affected cache/provenance/API/performance docs; and
- coordinated dependency/compiler-schema/build metadata required for the new capability.

P7 does not change pinned profile semantics, rewrite P1 goldens, remove the Python compiler, or
add consumer dependencies.

## Deliverables

1. Freeze and validate the supported encoded descriptor, columns, segments, scope, and owner
   lifetime.
2. Implement every pinned projection rule/option in Rust, including compatibility defects,
   multiplicity, annotations, taxonomy, ABox, lifecycle state, and deterministic encounter order.
3. Emit bounded packed edge batches into the existing materialized, iterator, batch-sink,
   canonical-sort, digest, and artifact surfaces without per-edge FFI.
4. Preserve scalar-python/scalar-native paths and expose explicit versioned ingestion diagnostics.
5. Add exact rule-counter, ordered-edge, bag-counter, canonical-byte, artifact, error, and cleanup
   parity across the oracle, generated, direct/mmap/overlay/composite, and large-corpus matrices.
6. Prove buffer/iterator owner lifetime, GIL/thread/fork/cancel/close/panic/interpreter safety and
   run Rust fuzz/sanitizer/lint/audit lanes.
7. Record load-excluded and shared-snapshot-to-artifact time, time to first edge, throughput,
   copies/materializations/FFI, RSS/spill, semantic digests, and Exact NCIT/DOID/GO evidence.
8. Update version ranges, compiler cache/provenance, backend promotion, docs, changelog, wheels,
   SBOM/licenses, and consumer conformance pins.

## Acceptance

- Scalar and encoded outputs match every exact rule, multiplicity, order, digest, artifact, error,
  and lifecycle assertion.
- Existing compatible views incur zero parse, resolver, core-wire, scalar-axiom, ontology-sized
  structural copy, base flattening, or per-row FFI on encoded-native projection.
- Direct/mmap paths have zero ontology-sized staging copy; bounded exceptional copies and batch
  sizes are observable and meet the memory contract.
- Corrupt/incompatible buffers fail before output; cancellation/failure cleans all private
  resources and publishes no partial artifact/cache.
- Every gate in `../native-structural-ingestion.md` and `../performance-packaging.md` passes on the
  labelled runner; `auto` is changed only with that evidence.
- Pure/compiler-free Java-free installations remain complete on Python 3.10+, and supported native
  wheels pass parity and artifact audits.
- Exact-OM passes its exact snapshot-identity, baseline classification, scale, provenance, and
  dependency-DAG checks without the projector importing Exact.

## Handoff

Publish exact core/encoded schema support, projector compiler schema, rule and artifact digests,
raw performance/RSS/copy evidence, backend promotion decision, known limitations, and the Exact
revision tested. Scalar-only core providers remain supported.
