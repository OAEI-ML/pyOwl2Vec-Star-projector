# Performance and correctness evidence

This page is maintainer reference material. It preserves the detailed benchmark-harness usage and
the private P7 evidence-checkpoint chronology that previously lived in the repository README. The
public behavior contract lives in the [README](../README.md), the
[specifications](../specs/README.md), and the [compatibility matrix](compatibility.md); nothing on
this page changes that contract, and none of the private checkpoints below constitute public
acceptance or release-performance evidence.

Phase-level evidence summaries live under `reports/`:

| Phase | Report |
|---|---|
| P0 foundation | [`reports/p0/handoff.md`](../reports/p0/handoff.md) |
| P1 Scala oracle | [`reports/p1/oracle.md`](../reports/p1/oracle.md) |
| P2 Python compiler | [`reports/p2/python-compiler.md`](../reports/p2/python-compiler.md) |
| P3 native backend | [`reports/p3/native-backend.md`](../reports/p3/native-backend.md) |
| P4 streaming | [`reports/p4/streaming.md`](../reports/p4/streaming.md) |
| P5 packaging/release | [`reports/p5/packaging-release.md`](../reports/p5/packaging-release.md) |
| P6 consumer conformance | [`reports/p6/consumer-conformance.md`](../reports/p6/consumer-conformance.md) |
| P7 encoded native compiler | [`reports/p7/encoded-native-compiler.md`](../reports/p7/encoded-native-compiler.md) |

## The P7 benchmark harness

The P7 harness measures an already-loaded public view through the production projector path and
records first-edge/complete wall and CPU time, RSS, edge hashes, core operation deltas, public
ingestion phases/counters, and fail-closed acceptance evidence:

```bash
PYTHONPATH=src:../pyOWLCore/src python benchmarks/benchmark_encoded_compiler.py ontology.ofn \
  --format functional --load-backend native --projector-backend native
```

Use `--require-encoded-native` only as a release-evidence gate. It fails unless every repetition
selects encoded-native and exposes a complete zero-forbidden-counter, zero-staging-copy,
released-GIL record; it never relabels scalar fallback as accelerated evidence.

## The private native candidate

P7 development can separately measure the lower-level exact-direct candidate with
`--private-native-candidate`. This calls the internal iterator, labels every sample
`private-native-candidate`, hashes the complete ingestion and core-operation ledgers, and records
the loaded distribution `RECORD`, package module, native binary, feature ledger, and kernel
version. Supply the exact 40-character revisions with `--projector-revision` and
`--core-revision`. `--require-private-native-candidate` additionally requires both packages to be
loaded from installed distribution payloads. Source-tree runs can prove the private boundary but
cannot pass that installed-evidence gate.

Private-candidate mode is deliberately incompatible with `--require-encoded-native` and can never
set public `acceptance_ready`. Its evidence isolates implementation limits: only exact
full `bytes` exporters or the canonical eleven-column packed direct-`bytes` arena are supported,
and general stable-ABI buffer exporters such as mmap remain a transactional fallback. The public
encoded path uses the same retained Scala-instance role maps across ordered calls, while
maintaining a scalar-compatible shadow and selecting the scalar lifecycle permanently after any
native decline or other whole-operation scalar selection. Its native output is a
resumable cursor: each drain owns at most the configured batch, reports zero vector-backed output
edges, and commits only after its final `Edge` tuple exists. No intermediate Python tuple-edge list
is returned to the wrapper, and a final-edge allocation failure leaves the cursor retryable.
Exhaustive immutable-input and exact-count preflight remains fail-before-publication, but the cursor
records zero emission attempts until the first caller drain. The legacy private coarse call now
uses the same cursor to build only its required Python list through 256-edge native chunks; it
retains no complete Rust output vector or second complete tuple-edge list. Final `Edge` and
statistics factories run inside the same transaction, so reusable role state commits only after
the complete final Python result exists. The exact installed-wheel candidate checkpoints are
recorded in
[`installed-coarse-cursor-checkpoint.json`](../reports/p7/evidence/installed-coarse-cursor-checkpoint.json)
and
[`installed-final-result-checkpoint.json`](../reports/p7/evidence/installed-final-result-checkpoint.json);
they are explicitly private, incomplete, and not release-performance evidence.

## Historical pre-promotion checkpoint chronology

The detailed records below preserve the private evidence sequence that led to public promotion of
the encoded-native compiler in `0.2.0`. Statements below that ordinary dispatch was unchanged or
the feature ledger lacked the compiler describe those checkpoint revisions; the active `0.2.0`
behavior is the public contract in the README and specifications.

### Transactional final-object publication

The final bounded-batch transaction is independently hash-bound in
[`installed-final-batch-checkpoint.json`](../reports/p7/evidence/installed-final-batch-checkpoint.json).
Iterator preparation likewise constructs its final statistics object before publishing the batch
session or retained role transition; its exact failure-atomicity evidence is recorded in
[`installed-batch-session-checkpoint.json`](../reports/p7/evidence/installed-batch-session-checkpoint.json).
The final iterator wrapper, with its compiler owner and statistics references, is now constructed
inside that same transaction; exact allocation-failure evidence is recorded in
[`installed-iterator-publication-checkpoint.json`](../reports/p7/evidence/installed-iterator-publication-checkpoint.json).
Canonical factory identities and exact result types are validated before the transaction publishes;
exact malformed-result evidence is recorded in
[`installed-factory-validation-checkpoint.json`](../reports/p7/evidence/installed-factory-validation-checkpoint.json).
Every final bounded-drain `Edge` and every legacy coarse-call `Edge`/statistics result now receives
the same exact-type and canonical-factory validation before cursor or retained-role commit; exact
retry and state-atomicity evidence is recorded in
[`installed-edge-factory-validation-checkpoint.json`](../reports/p7/evidence/installed-edge-factory-validation-checkpoint.json).
The Python envelope checks retain those same import-time identities instead of rereading mutable
module globals after native commit; exact constructor-mutation evidence is recorded in
[`installed-canonical-wrapper-checkpoint.json`](../reports/p7/evidence/installed-canonical-wrapper-checkpoint.json).
Native validation now also matches every final-object payload field to its transaction input before
commit; exact corruption and retry evidence is recorded in
[`installed-final-payload-validation-checkpoint.json`](../reports/p7/evidence/installed-final-payload-validation-checkpoint.json).
The complete final batch is then revalidated after its last constructor callback, including
distinct object identity, and statistics are revalidated after the iterator callback; exact
installed evidence is recorded in
[`installed-complete-batch-validation-checkpoint.json`](../reports/p7/evidence/installed-complete-batch-validation-checkpoint.json).

### Direct stable-ABI allocation

The hidden bounded and coarse transactions now allocate exact slotted `Edge` objects directly
through the CPython stable ABI, with no Python `Edge` factory or constructor callback. Their
canonical object-base/member layout is checked before allocation and again after each complete
chunk; exact installed evidence is recorded in
[`installed-direct-edge-allocation-checkpoint.json`](../reports/p7/evidence/installed-direct-edge-allocation-checkpoint.json).
The exact 60-slot statistics object is allocated through that same validated stable-ABI
boundary on both coarse and batch-session paths. No 60-field argument tuple, Python statistics
factory call, or constructor callback remains; exact evidence is recorded in
[`installed-direct-statistics-allocation-checkpoint.json`](../reports/p7/evidence/installed-direct-statistics-allocation-checkpoint.json).
Batch-session preparation also allocates the exact eight-slot iterator directly, without its
three-field argument tuple or Python factory/constructor callback. Its compatible base, canonical
layout, owner/statistics identities, and initial state are transactionally validated; exact
evidence is recorded in
[`installed-direct-iterator-allocation-checkpoint.json`](../reports/p7/evidence/installed-direct-iterator-allocation-checkpoint.json).

### Stream surfaces and lifecycle safety

The cursor is also exercised through hidden Projector-level protocol-sink, canonical-digest, and
portable-artifact adapters while reusing the existing policy and cleanup machinery. Exact
installed-wheel evidence covers scalar-equivalent batches and reports, equal digests,
byte-identical artifacts, and sink-failure cancellation in
[`installed-stream-surfaces-checkpoint.json`](../reports/p7/evidence/installed-stream-surfaces-checkpoint.json).
These adapters do not change ordinary public dispatch.
The load-excluded P7 harness can measure those hidden iterator, sink, digest, and artifact consumers
as separate labelled surfaces, binding the surface and its consumer metrics into the evidence hash.
Exact installed-payload smoke evidence is recorded in
[`installed-stream-surface-benchmark-checkpoint.json`](../reports/p7/evidence/installed-stream-surface-benchmark-checkpoint.json);
it is not corpus-scale or public-acceptance evidence.

The hidden cursor's focused lifecycle matrix is recorded in
[`installed-lifecycle-safety-checkpoint.json`](../reports/p7/evidence/installed-lifecycle-safety-checkpoint.json).
Against the isolated installed wheels it covers iterator handoff to another thread, concurrent
isolated-mode calls on one `Projector`, independent parent/child draining after a quiescent POSIX
fork, and normal interpreter shutdown with an unfinished retained cursor. Those cases complement
the existing owner-lifetime, released-GIL cancellation, close, fallback, sink-failure, and panic
conversion checks. They are private macOS checkpoint evidence, not a claim about multithreaded
fork, every platform/interpreter configuration, or public dispatch.

### Differential and hostile-input campaigns

The replayable generated differential runner lives at
[`tools/differential_encoded_native.py`](../tools/differential_encoded_native.py). Its exact
installed checkpoint runs 128 SplitMix64-generated mixed-rule ontologies through both the
independent-bytes and canonical packed-bytes core providers, covering all 32 combinations of the
three historical booleans, duplicate policy, and output order. It requires ordered edge and
semantic-report parity with scalar Python plus encoded-native, eleven-buffer zero-copy,
zero-staging-copy, zero-per-row-FFI evidence in every execution. The hash-bound result is in
[`installed-generated-differential-checkpoint.json`](../reports/p7/evidence/installed-generated-differential-checkpoint.json).
This is a finite deterministic interaction matrix, not coverage-guided fuzzing.

The bounded invalid-column runner is
[`tools/hostile_encoded_native.py`](../tools/hostile_encoded_native.py). Its exact installed
checkpoint uses the maximum 256 generated sources, both supported direct exporter layouts, and 29
predefined validation cases per source, for 14,848 total executions covering all eleven structural
columns. Every case requires the same typed rejection through the direct compiler and hidden
Projector, equal failures across provider layouts, terminal failed state without a batch session or
output counters, zero edges, no report publication, and explicit native-view cleanup. The
hash-bound result is in
[`installed-encoded-column-validation-checkpoint.json`](../reports/p7/evidence/installed-encoded-column-validation-checkpoint.json).
This is finite deterministic compatibility evidence, not mutational or coverage-guided fuzzing.

### Segmented-owner slices

One bounded segmented-owner slice is connected to the hidden Rust cursor. A bounded chain of
canonical one-segment `OVERLAY_BASE`/`ALL` views with empty local columns, postings, and
anonymous-scope maps fully revalidates each container and its terminal exact-direct source, then
passes only the terminal source buffers to Rust without flattening or staging. The three-alias
checkpoint retains every owner and reports 44 retained zero-copy buffers, 11 Rust-detached
buffers, four retained segments, and three referenced views. Both direct exporter layouts have
exact scalar edge/report parity; depth, cumulative-work, and transitive-cycle failures occur before
output. Edited overlays, annotation-sensitive aliases, multiple posting-selection layers,
`INCLUDE`, and other segmented families remain on whole-operation fallback. The initial one-alias
evidence remains in
[`installed-empty-overlay-alias-checkpoint.json`](../reports/p7/evidence/installed-empty-overlay-alias-checkpoint.json);
the recursive exact installed-wheel evidence is in
[`installed-recursive-empty-overlay-alias-checkpoint.json`](../reports/p7/evidence/installed-recursive-empty-overlay-alias-checkpoint.json).
The public feature ledger and ordinary dispatch remain unchanged.

Kernel v47 additionally admits exactly one nonempty sorted `EXCLUDE` posting table on the
terminal-adjacent alias in that bounded empty-local chain. The exact immutable posting exporter is
retained across GIL release and binary-searched in place for root classification, role state,
anonymous-ID reachability, counts, and cursor emission, without constructing a selection index or
flattening the source. The installed removal matrix covers both direct exporter layouts, taxonomy
and restriction roots, subrole/inverse state, domain/range products, silent annotations,
nonadjacent projecting roots, anonymous-ID recomputation, zero-output removal, and retained-owner
recursive traversal. Real validation rejects a nonterminal posting because positions are local to
the immediate referenced view; the internal resolver also sends a forged prevalidated nonterminal
lease to whole-call fallback. One-container provenance reports 22 retained structural buffers, 12
native inputs including the posting exporter, its exact posting bytes, and zero indexing,
flattening, staging copy, scalar materialization, or per-row FFI. The exact checkpoint is in
[`installed-excluding-overlay-alias-checkpoint.json`](../reports/p7/evidence/installed-excluding-overlay-alias-checkpoint.json).
It is focused private correctness evidence, not general posted-overlay, performance, public
dispatch, or release acceptance.

### Scala lifecycle binding and kernel versions

The lifecycle binding is independently hash-bound in
[`installed-scala-lifecycle-checkpoint.json`](../reports/p7/evidence/installed-scala-lifecycle-checkpoint.json).
Three native calls cover initial role acquisition, later restriction/domain/range consumption, and
conflicting overwrite behavior; a separate injected-decline sequence proves a one-way transition
to scalar compilation without re-entering stale native state. That lifecycle checkpoint used
kernel v31; the bounded, lazy, coarse-cursor, final-result, final-batch, batch-session,
iterator-publication, factory-validation, edge-factory-validation, canonical-wrapper,
final-payload-validation, complete-batch-validation, direct-edge-allocation, and
direct-statistics-allocation and direct-iterator-allocation checkpoints advance the private kernel
through v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, and v46 while the
public feature ledger remains exactly `abi3-py310` and `bounded-batches`.

### Annotation provenance and cycle safety

The hidden candidate also proves root-scoped annotation provenance before admitting visible
annotations. Kernel v30 retains an unequal exact-direct root table alongside the closure and joins
its canonical annotation identities to closure nodes before edge counting or publication. This
keeps imported subclasses native while suppressing imported-only annotations, preserves
closure-wide anonymous identifiers, and reports both retained zero-copy tables. Byte-identical
single-document selections still use one table; unavailable, non-direct, or sliced root providers
select one whole-operation scalar compiler. Annotation-free imported closures skip the root
request. The source-built installed correctness checkpoint is recorded in
[`installed-root-provenance-join-checkpoint.json`](../reports/p7/evidence/installed-root-provenance-join-checkpoint.json);
it carries no release or performance claim. The preceding fail-closed fallback evidence remains in
[`installed-annotation-provenance-checkpoint.json`](../reports/p7/evidence/installed-annotation-provenance-checkpoint.json).

Kernel v31 also closes a hostile-input hole in nested annotation metadata. Local annotation fields
were already validated, but forged structural columns could form a self or transitive cycle while
retaining valid tags and arities. The private kernel now performs an iterative graph preflight on
both the closure and any retained root table before provenance matching or output allocation. A
4,096-level acyclic chain and installed closure/root cycle cases prove stack-safe admission and
fail-before-output rejection. The exact installed correctness checkpoint is recorded in
[`installed-annotation-cycle-checkpoint.json`](../reports/p7/evidence/installed-annotation-cycle-checkpoint.json);
the public feature ledger remains unchanged.

The broad Python structural decoder enforces the same acyclic nested-metadata invariant before
root classification. This covers both its closure table and the independently retained root table,
so the hidden broad-decoder path cannot compare or compile a forged cyclic annotation identity.
Two exact installed-wheel hostile cases and the complete suite are hash-bound in
[`installed-broad-annotation-cycle-checkpoint.json`](../reports/p7/evidence/installed-broad-annotation-cycle-checkpoint.json).

The broad decoder's recursive class-expression and data-range checks are stack-safe as well. They
use iterative color walks while retaining their existing accepted envelope, fallback choices,
and stable cycle failures. Exact installed tests admit independently forged 1,200-node acyclic
chains for both graph families, reject both cyclic variants before output, and are recorded in
[`installed-recursive-graph-checkpoint.json`](../reports/p7/evidence/installed-recursive-graph-checkpoint.json).

Cross-table and cross-segment canonical identity comparison no longer reintroduces a recursion
limit after that preflight. The cursor computes child-first lengths with explicit frames and emits
the same public-core canonical bytes through lazy component frames, retaining only its per-node
length cache and traversal state. Exact byte parity, sequence order, 1,200-node class/data streams,
and generic cycle rejection are recorded in
[`installed-canonical-cursor-checkpoint.json`](../reports/p7/evidence/installed-canonical-cursor-checkpoint.json).

Segment-manifest traversal is stack-safe too. Overlay and composite dependencies are suspended in
explicit resolver frames while preserving source-local postings, composed anonymous scopes,
lease retention, cache reuse, and active-path cycle rejection. An exact installed 1,100-overlay
chain plus nested overlay/composite and hostile-cycle cases are recorded in
[`installed-segment-resolution-checkpoint.json`](../reports/p7/evidence/installed-segment-resolution-checkpoint.json).

That resolver also covers the broad compiler's multi-document annotation-provenance join.
Supported overlay/composite closure tables and segmented root selections retain all source leases,
intersect canonical root identities, and suppress imported-only annotations without scalar
traversal. Exact installed parity and fallback evidence is recorded in
[`installed-segmented-provenance-checkpoint.json`](../reports/p7/evidence/installed-segmented-provenance-checkpoint.json).

### Import topologies

The v31 boundary is installed-tested over diamond and cyclic import graphs. A shared diamond
member is projected once, a two-document cycle terminates with both taxonomy edges, and in each
topology only the root document's annotation remains visible. Both runs retain the independent
closure/root tables, apply the exact selected-edge limit, and report zero scalar materialization.
The hash-bound record is
[`installed-import-topology-checkpoint.json`](../reports/p7/evidence/installed-import-topology-checkpoint.json).
