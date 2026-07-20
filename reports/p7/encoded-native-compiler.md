# P7 encoded-native compiler checkpoint

Date: 2026-07-20. Projector revision: `99330e9`. pyOWLCore candidate revision:
`1d3cd25`. Exact-OM integration revision: `3c3cfce`.

## Outcome

P7 has a substantial, fail-closed implementation checkpoint, but it is **not accepted or
promoted**. The projector can negotiate and validate the public
`pyowl-core/structural-columns` version 1 view and compile the currently validated direct and
segmented constructor envelope in Rust. Unsupported valid shapes select one whole-operation
scalar fallback before output, while malformed descriptors fail closed. The complete Python
compiler remains the semantic fallback and `auto` does not advertise or select encoded-native.

This report records repository-owned implementation and verification only. It deliberately does
not claim the labelled performance, hosted wheel, sanitizer, licensed-corpus, or released-revision
evidence required by WP-P7.

## Implemented checkpoint

- Public capability negotiation validates the core model schema, encoded schema/version,
  descriptor digest, scope, structural fingerprint, owner lifetime, little-endian columns,
  offsets, references, and segment manifests without importing core internals.
- The native compiler covers declarations; the pinned subclass/equivalence/restriction,
  annotation, ABox, taxonomy, domain/range, and role-state rules in the documented native
  envelope; and all currently enumerated non-projecting/skipped constructors. Silent SWRL and
  ontology-annotation roots are structurally validated without changing projection semantics.
- Canonical direct views, retained overlay bases/deltas, recursive overlay bases, and composite
  member groups compile through retained public leases. Root postings and anonymous-scope maps
  are applied before a canonical streaming merge and structural deduplication; valid
  order-changing mappings select scalar fallback rather than being reinterpreted.
- Rust emits bounded packed edge batches through the existing materialized, iterator, sink,
  digest, and artifact surfaces. The boundary does not perform a Python call per axiom or edge.
- Projection provenance records the selected path, encoded schema and descriptor identity,
  monotonic publication/validation/compiler durations, and a bounded public ledger for scalar
  rows, borrowed/indexed/detached buffers, segments, postings, staging copies, and GIL state.
  Scalar paths publish the same counter vocabulary with encoded values fixed at zero.
- Test-only parity instrumentation compares canonical rule counters, ordered and bag edge digests,
  diagnostics, and fallbacks against the scalar compiler. Hostile segment/descriptor fixtures and
  retained-owner lifetime cases exercise failure before output.

The constructor and segmented-view work is represented by the implementation sequence from
`ab2b809` through `99330e9`, including direct aggregate/ABox/annotation compilation, overlay and
composite resolution, compatibility role state, every documented skipped constructor, hostile
segment fixtures, silent structural extensions, fallback validation, and public phase diagnostics.

## Local verification captured at this checkpoint

The source-tree candidate passed the following repository-owned gates at revision `99330e9`:

| Gate | Result |
|---|---|
| Complete projector test suite | 828 passed |
| Encoded compiler/dispatch/segment suite | 663 passed |
| Rust tests, formatting, and Clippy | passed |
| Python Ruff and mypy gates | passed |
| Exact focused snapshot/projection integration | passed |

These results establish the bounded implementation slice; they are not a substitute for the
labelled P7 acceptance matrix. Portable artifact identity remains independent of the new execution
timings and counters.

## Acceptance ledger

| WP-P7 requirement | Checkpoint state |
|---|---|
| Freeze and validate the public encoded descriptor and owner lifetime | Implemented for structural-columns v1 candidate; final released core pin remains open |
| Complete pinned projection-rule and option parity | Broad documented constructor envelope implemented; full generated/oracle/large-corpus acceptance remains open |
| Bounded packed batches without per-row FFI | Implemented and locally tested |
| Preserve scalar paths and versioned diagnostics | Implemented; Python remains complete and encoded-native remains unadvertised |
| Direct/mmap/overlay/composite parity matrix | Direct and segmented overlay/composite coverage implemented; installed-wheel mmap cross-product remains open |
| Lifetime, hostile-input, thread/fork/cancel/panic safety | Focused local coverage exists; fuzz, sanitizer, Miri-equivalent ownership evidence, and hosted matrix remain open |
| Zero parser/resolver/wire/scalar/base-flattening/materialization ledger | Public counters are wired; labelled direct/mmap proof across consumers remains open |
| NCIT/DOID/GO/million-axiom/licensed-corpus time and RSS gates | Open; no performance threshold is claimed |
| Exact shared-stack identity, parity, scale, and dependency-DAG gate | Focused source integration passed; full scale matrix and exact released revisions remain open |
| Wheels, SBOM/licenses, reproducibility, and Python/platform matrix | Open for the encoded capability |

## Promotion decision and remaining work

`auto` remains unchanged. The encoded path stays an internal opt-in candidate and is absent from
the public native feature ledger until all of the following evidence is committed:

1. exact scalar/encoded rule, multiplicity, order, digest, artifact, error, and lifecycle parity
   over the complete oracle/generated/direct/mmap/overlay/composite and licensed-corpus matrices;
2. fuzz and sanitizer lanes plus thread, fork, cancellation, panic, interpreter-shutdown, and
   installed-wheel lifetime evidence on every supported platform/Python pair;
3. labelled NCIT, DOID, GO, million-axiom synthetic, and largest-available licensed-corpus runs
   proving the timing, RSS, copy/materialization, boundary-overhead, and cleanup thresholds in
   `specs/native-structural-ingestion.md`;
4. Exact-OM shared-stack scale reruns with fixed identities, semantic digests, zero forbidden
   handoff counters, and no regression relative to both scalar baselines; and
5. exact released core/projector compatibility pins, wheel audits, SBOM/license evidence, and a
   reviewed backend-promotion decision.

Until those gates pass, P7 remains in progress and no version, compatibility, or performance claim
is inferred from this checkpoint.
