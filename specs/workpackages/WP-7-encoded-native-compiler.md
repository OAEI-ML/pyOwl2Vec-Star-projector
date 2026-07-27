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
skipped named-or-inverse `NegativeObjectPropertyAssertion`, skipped named-source
`DataPropertyAssertion` and `NegativeDataPropertyAssertion` with a fully validated literal,
skipped named `SubDataPropertyOf`, and paired named object-property domain/range products.
Unannotated named-or-inverse `SubObjectPropertyOf` and
`InverseObjectProperties` roots reproduce OWLAPI hash-set visitation, the historical sibling
overwrite, and last-inverse-wins behavior, expanding restrictions and domain/range products but not
direct assertions. Equivalent/disjoint object-property sets and all seven object-property
characteristics are validated skipped roots. Positive inverse assertions reproduce the pinned typed
reference failure. Equivalent classes use the first two expressions in UTF-8 IRI order.
`only_taxonomy` suppresses restriction edges but preserves named equivalence, class assertions,
positive object assertions, negative-assertion/property-family skips, and expanded domain/range,
while the optional asserted-taxonomy mode emits only the direct subclass family after preflighting
the whole supported slice. It retains the owner/exporters, preflights before allocating output,
releases the GIL, supports concurrent cancellation, and retains a legacy one-list coarse call.
The later bounded checkpoints add the recursive class/data validation, skipped/silent families,
role-state behavior, selected annotation rules, and diagnostics enumerated in the implementation
report. Kernel v30 additionally retains an independent exact-direct root table and joins canonical
root `AnnotationAssertion` identities back to closure nodes before edge limits or publication, so
visible multi-document annotations preserve root-only selection and closure-wide blank IDs without
structural copying. Property-chain emission, remaining projecting constructors, and general
segment traversal, plus sliced, mmap, or non-bytes exporters, remain unsupported. The safe generic
PyO3 buffer API is unavailable at the current `abi3-py310` floor, so general mmap ownership remains
an explicit design blocker. The pinned PyO3 0.28.3 `buffer` module is wholly gated on either a
non-limited build or
`Py_3_11`, and its FFI exports `Py_buffer`, `PyObject_GetBuffer`, and `PyBuffer_Release` only under
`Py_3_11`; the actual `abi3-py310` extension build sets `Py_LIMITED_API` and stops its version cfgs
at `Py_3_10`. This matches CPython's designation of the complete buffer structure and lease API as
Stable ABI only since Python 3.11. Kernel v119 therefore keeps the ABI floor and capability ledger
unchanged, introduces one exporter-neutral validated-buffer candidate and one isolated retained
storage seam for a future `PyUntypedBuffer` variant, and rejects valid readonly C-contiguous
general exporters there without copying. Focused mmap cleanup and writable, strided,
multidimensional, and signed-format cases prove typed fail-closed behavior before retention or
output. Kernel v120 admits one further bounded segmented family without changing that buffer or
capability policy: a nested one-local-root overlay composite may carry one source-local base
`EXCLUDE` while its distinct direct sibling independently carries one outer `EXCLUDE`. Both exact
posting exporters remain attached to their own source tables and are searched in place; selection
on the nested member, `INCLUDE`, scope remapping, a second direct sibling, and broader recursive
plans still fail closed. Kernel v31 additionally rejects cycles in nested annotation metadata with
an iterative preflight over both closure and retained root tables. This closes a hostile structural-columns
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
semantics, owner lifetime, cache reuse, and active-path cycle rejection. The broad compiler now
uses that same resolver on both sides of its multi-document root-annotation join, so segmented
closure/root selections preserve scalar provenance without flattening or scalar traversal.
The hidden Projector iterator now also binds explicit `scala-instance` calls to a persistent native
role-state handle. Ordered consumers and conflicting overwrites match the scalar lifecycle, while
an exact scalar-compatible shadow permits a one-way transition after any whole-operation native
decline or other scalar selection. Retained subrole/inverse property counts are public-path-safe
diagnostics. Kernel v32 replaces the hidden iterator's complete native output vector with a
resumable cursor. Kernel v33 removes its redundant pre-publication emission replay: exhaustive
immutable structural, semantic, count, and capacity preflight publishes the cursor with zero
emission attempts. Each drain buffers no more than the configured batch, and cursor movement
commits only after final batch construction. Kernel v36 constructs the final `Edge` tuple inside
that transaction, removes the wrapper's intermediate Python tuple-edge list, and restores the exact
cursor/counters if a final-edge factory fails. Kernel v37 constructs final statistics before the
session, compiler-finished state, and retained role transition are published; factory failure
publishes none of them. Kernel v38 also constructs the final owner-holding iterator after those
statistics and before the same publication boundary; iterator factory failure publishes none of
them. Kernel v39 validates canonical factory identities and exact result types before commit;
malformed final objects publish none of them. Kernel v40 extends those checks
to each bounded-drain `Edge` before cursor/counter commit and to every coarse `Edge` plus final
statistics before retained-role/output-counter commit; bounded failures remain retryable and
coarse failures leave role maps unchanged. Kernel v41 also pins the post-native Python envelope
checks to the same retained canonical identities, preventing a constructor-time module-global
mutation from rejecting state that native code already committed. Kernel v42 validates every
final edge/statistics payload field plus the iterator's owner, statistics, bound, and initial count
before native commit, preserving the same atomic outcomes for constructor-injected payload
corruption. Kernel v43 revalidates the complete edge chunk after its last constructor callback,
requires distinct edge identities, and revalidates statistics after the iterator callback, so a
later callback cannot mutate or alias an earlier final object across the commit boundary. Kernel
v44 then removes production `Edge` constructor callbacks: Rust validates the canonical exact
slotted layout, allocates each object through the CPython stable ABI, assigns the three validated
member fields, and rechecks the complete layout/type/payload/identity transaction before commit.
Malformed or changed layouts preserve the same atomic failure outcomes. Kernel v45 uses the same
validated allocator for the exact 60-slot statistics type, removing the 60-field argument tuple
and Python statistics factory/constructor callback from coarse and session preparation. All 60
integer payloads and the layout are rechecked before counters, session, or retained roles publish.
Kernel v46 then directly allocates the exact eight-slot iterator, removing its three-field argument
tuple and Python factory/constructor callback while validating the compatible base, ordered slots,
owner/statistics identities, and every initial state field before the same publication boundary.
Projector revision `e531a02` connects this cursor to hidden protocol-sink, canonical-digest, and
portable-artifact adapters through the existing policy and cleanup implementations. Exact installed
tests require scalar-equivalent batches and reports, equal digests, byte-identical artifacts, and
cursor cancellation without report publication after sink failure. These adapters do not advertise
the capability or alter ordinary public dispatch.
Harness revision `5ac8ef3` adds independent hidden iterator, sink, digest, and artifact measurement
surfaces. It hash-binds the surface and consumer metrics, records observable iterator/sink
time-to-first output, and marks aggregate digest/artifact first-edge latency unavailable. The exact
installed-payload smoke is not corpus-scale, threshold, or public-acceptance evidence.
Lifecycle-test revision `de12687` moves an active hidden iterator between threads, proves
concurrent isolated-mode reuse of one `Projector`, independently drains the copied quiescent cursor
in parent and child after POSIX fork, and retains an unfinished cursor through normal interpreter
shutdown. The exact installed-wheel checkpoint also reruns the existing owner-lifetime,
released-GIL cancellation, close/fallback/sink-failure cleanup, panic conversion, and legacy
shutdown cases. This is focused macOS evidence; multithreaded fork and the full
platform/interpreter/sanitizer/fuzz matrix remain open.
Verification revision `4e0f54d` adds a bounded, version-independent SplitMix64 campaign. Its exact
installed run uses 128 generated mixed-rule ontologies, both independent- and packed-bytes core
providers, all 32 semantic-boolean/duplicate/order combinations, and batch bounds one through
seven. All 256 executions require exact scalar ordered-edge/report parity and encoded-native
zero-copy/per-row-FFI-free counters. It is finite generated interaction evidence, not the remaining
malformed-input, coverage-guided fuzz, sanitizer, or independent Scala-oracle matrix.
Validation revision `bf24a90` adds a bounded invalid encoded-column campaign. Its exact installed
run applies 29 predefined cases to all eleven structural columns for 256 generated sources and both
supported direct exporter layouts: 14,848 executions. Every case requires typed rejection before
output, equal direct/Projector and provider-layout failures, terminal failed state without a batch
session or output counters, no report, and closeable-view cleanup. It is finite compatibility
evidence, not mutational fuzzing, sanitizer evidence, or an exhaustive malformed-input claim.
Adapter revision `fc15ea7` admits one canonical zero-delta overlay alias to the hidden Rust cursor.
Revision `3ef4f15` generalizes that slice to a bounded iterative chain. Every
`OVERLAY_BASE`/`ALL` container has empty local columns, postings, and scope mappings; the terminal
exact-direct source is fully revalidated and passed to Rust without copying or flattening. Every
lease remains retained, with the three-alias checkpoint reporting 44 retained versus 11
Rust-detached zero-copy buffers, four segments, and three referenced views. Exact installed tests
cover both direct exporter layouts, owner lifetime, public depth and cumulative-work bounds,
transitive-cycle rejection, whole-call fallback for edited and annotation-sensitive overlays, and
typed rejection of malformed referenced columns before output.
Adapter revision `c8b6755` adds one canonical nonempty `EXCLUDE` posting table on the
terminal-adjacent alias in that bounded empty-local chain. Kernel v47 retains the exact immutable
posting exporter, validates complete sorted/unique in-range 1-based source-root positions, and
binary-searches it in place across root classification, role-state construction, anonymous-ID
reachability, semantic counts, and cursor emission. The terminal source, including excluded roots,
is still fully validated. Corrective revision `5ada96a` independently requires the posting
carrier's immediate source identity to be that terminal direct view. Public adapter validation
rejects a nonterminal posting as an out-of-range source-local reference, while a forged
prevalidated lease selects whole-call fallback.
Exact installed cases cover both exporter layouts and the projecting/state/silent removal matrix,
anonymous-ID recomputation, zero output, retained-owner recursive traversal, malformed postings,
and whole-call fallback for a nonterminal or second exclusion layer. The one-container ledger
reports 22 retained structural buffers, 12 native inputs including the posting exporter, exact
posting bytes, and zero selection indexing, flattening, staging, scalar materialization, or
per-row FFI. Multiple `EXCLUDE` layers, `INCLUDE`/composite selection,
annotation-sensitive aliases, mmap, and public selection remain open.
Kernels v48–v71 add one bounded two-segment `OVERLAY_BASE`/`OVERLAY_DELTA` slice. The base may use
`ALL` or one exact `EXCLUDE` table, while the `ALL` delta may contain exactly one unannotated
named-to-named `SubClassOf`, supported named-role Some/All/Min/Max restriction `SubClassOf`, named
entity `Declaration`, named-class/named-individual `ClassAssertion`, an ignored-shape `SubClassOf`
or `ClassAssertion` whose complete direct classifier returns ignored and whose graph reaches no
anonymous individual, a canonical binary or ternary ignored-shape `EquivalentClasses` set whose
complete direct classifier returns ignored and whose graph reaches no anonymous individual, or
named-property/named-individual positive `ObjectPropertyAssertion`, or
named-individual negative object assertion with a named or inverse named property, or named-
property/named-individual `DataPropertyAssertion` with a fully validated literal, its negative-
data-assertion counterpart, named `SubDataPropertyOf`, or a canonical binary or ternary named-
property `EquivalentDataProperties` or
`DisjointDataProperties` set, a named-property `DataPropertyDomain` over the existing recursive
class-expression envelope, a named-property `DataPropertyRange` over the existing recursive
data-range envelope, a named `FunctionalDataProperty`, or a named-to-recursive-range
`DatatypeDefinition`, a `HasKey` over the existing recursive class-expression envelope and
canonical named/inverse-object and named-data-property sets, a canonical binary or ternary
named-individual `SameIndividual` or `DifferentIndividuals` set, a canonical binary or ternary
named/inverse-object-property `EquivalentObjectProperties` or `DisjointObjectProperties` set, or
one of the seven unary object-property characteristic axioms over a named or inverse named
object-property expression, or a named-annotation-property `SubAnnotationPropertyOf`,
`AnnotationPropertyDomain`, or `AnnotationPropertyRange` with named annotation-property or IRI
targets as appropriate, or a canonical binary or ternary `DisjointClasses` set over recursive
class expressions, or a `DisjointUnion` with a named defined class and canonical binary or ternary
recursive class-expression set. The canonical merger derives one insertion scalar without
flattening or indexing either table; unsupported, annotated, anonymous, multi-root, nested, or
literal-emitting local shapes retain whole-operation fallback or typed pre-output rejection.
Kernel v52 inserts the local `ClassAssertion` in the scalar class-assertion phase.
Kernel v53 inserts the local positive object assertion in the scalar object-assertion phase and
fails closed with a typed reference error for an inverse local property before output. Both
preserve their historical `only_taxonomy` edges and suppress them only in asserted-taxonomy mode
while retaining exact counts. Kernel v54 validates and merges the local negative object assertion
as a silent skipped root without retaining an emitting delta record; `only_taxonomy` preserves its
skip diagnostic while asserted-taxonomy preserves its constructor count but suppresses that
diagnostic. Kernel v55 applies the same silent-root transaction to a local positive data assertion,
including string and typed-integer literal validation, exact constructor counts, and the same
option-specific skip-diagnostic behavior. Kernel v56 admits the negative data-assertion counterpart
through the same four-field validation, silent-root transaction, and option-specific diagnostic
rules. Kernel v57 admits one local `SubDataPropertyOf`, validates both named data properties, and
applies the same silent-root transaction and diagnostic rules. Kernel v58 admits one local
`EquivalentDataProperties`, validates its canonical binary or ternary named-property set, and
applies the same silent-root transaction and diagnostic rules. Kernel v59 admits the corresponding
local `DisjointDataProperties` set through the same validation, transaction, and diagnostic rules.
Kernel v60 admits a local `DataPropertyDomain`, validates its named property and recursive class
expression, and applies the same silent-root transaction and diagnostic rules.
Kernel v61 admits a local `DataPropertyRange`, validates its named property and recursive data
range, and applies the same silent-root transaction and diagnostic rules.
Kernel v62 admits a local `FunctionalDataProperty`, validates its named property, and applies the
same silent-root transaction and diagnostic rules.
Kernel v63 admits a local `DatatypeDefinition`, validates its named datatype and complete recursive
defining data range, and applies the same silent-root transaction and diagnostic rules.
Kernel v64 admits a local `SameIndividual`, validates its canonical binary or ternary set of named
individuals, and applies the same silent-root transaction and diagnostic rules.
Kernel v65 admits the corresponding local `DifferentIndividuals` set through the shared exact
individual-set validation, transaction, and diagnostic rules.
Kernel v66 admits a local `HasKey`, validates its recursive class expression and canonical
object/data property sets with at least one property total, and applies the same silent-root
transaction and diagnostic rules.
Kernel v67 admits local `EquivalentObjectProperties`, `DisjointObjectProperties`,
`FunctionalObjectProperty`, `InverseFunctionalObjectProperty`, `ReflexiveObjectProperty`,
`IrreflexiveObjectProperty`, `SymmetricObjectProperty`, `AsymmetricObjectProperty`, and
`TransitiveObjectProperty` through one exact classifier and silent-root transaction. It validates
canonical binary or ternary property sets and named or inverse named property expressions, and
preserves the exact constructor count under normal, taxonomy-only, and asserted-taxonomy modes.
Kernel v68 admits local `SubAnnotationPropertyOf`, `AnnotationPropertyDomain`, and
`AnnotationPropertyRange` through one exact classifier and silent-root transaction. It reuses the
complete direct validators for named annotation properties and IRI domain/range targets, requires
an empty axiom annotation set, and preserves the exact constructor count under normal,
taxonomy-only, and asserted-taxonomy modes.
Kernel v69 admits local `DisjointClasses` and `DisjointUnion` through one exact classifier and
silent-root transaction. It reuses the complete recursive class-expression validators, requires a
canonical binary or ternary member set and empty axiom annotations, rechecks the named
`DisjointUnion` head, and preserves the exact constructor count under normal, taxonomy-only, and
asserted-taxonomy modes.
Kernel v70 admits local ignored-shape `SubClassOf` and `ClassAssertion` roots through one exact
classifier and the silent-root transaction. It reuses the complete direct projection classifiers,
requires the result to remain ignored after structural validation, requires an empty axiom
annotation set, and rejects every reachable anonymous individual until local scope remapping is
defined. Normal, taxonomy-only, and asserted-taxonomy modes preserve the exact constructor and
ignored-shape counters, leave the skipped-axiom counter at zero, and retain no emitting delta
record.
Kernel v71 admits one canonical binary or ternary local `EquivalentClasses` root through the
silent-root transaction only when the complete direct equivalence classifier returns ignored. It
reuses the complete recursive class-expression and canonical-set validators, requires empty axiom
annotations, and rejects every reachable anonymous individual until local scope remapping is
defined. Normal and taxonomy-only modes preserve the exact constructor and ignored-equivalence
counts; asserted-taxonomy preserves the constructor count and suppresses the ignored-equivalence
count, matching the complete direct path. The aggregate-equivalence and skipped-axiom counters
remain zero, and no emitting delta record is retained.
The private counter ledger separates compiled edges
from zero vector-backed output edges and the peak buffered batch. Kernel v34 also removes the
legacy coarse call's complete Rust output vector and duplicate emitter: the required Python list is
built through fixed 256-edge cursor drains, and retained role state commits only after complete
Python output construction. Kernel v35 constructs final `Edge` and statistics objects in that
transaction, removing the wrapper's second complete tuple-edge list and preserving state atomicity
when either final factory fails. Public dispatch remains unchanged and the encoded capability
remains absent from the extension feature ledger.

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
