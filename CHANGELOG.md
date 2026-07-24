# Changelog

All notable changes use Semantic Versioning. Compatibility-profile changes are listed separately
from packaging or performance changes because profile output is a data contract.

## Unreleased

### Added

- Hidden bounded and coarse native output now allocates exact slotted `Edge` objects directly
  through the CPython stable ABI, after validating their canonical object-base/member layout.
  Production drains execute no Python `Edge` factory or constructor callback; exact type, payload,
  distinct identity, canonical factory, and unchanged layout are still revalidated before cursor,
  counters, or retained role state commits. The capability remains unadvertised.
- Native final-batch validation now rechecks every exact `Edge` after the last constructor callback,
  rejects aliased final objects, and rechecks statistics after the iterator callback. A later
  callback cannot corrupt an earlier result before cursor, counters, session, or retained Scala
  role state commits; bounded failures remain exactly retryable and the capability stays hidden.
- Native final-object validation now checks payload identity as well as exact type: every `Edge`
  string, all 60 statistics integers, and the iterator owner/statistics/batch/initial-count fields
  must match their native inputs before cursor, session, counters, or retained Scala role state can
  commit. Constructor-injected corruption fails atomically; the capability remains unadvertised.
- Post-native coarse, session, and bounded-drain envelope validation now uses import-time canonical
  `Edge`, statistics, and iterator identities. A canonical constructor that mutates its replaceable
  module factory name can no longer make Python reject already-committed cursor progress, counters,
  or retained Scala role state; the capability remains unadvertised.
- Hidden bounded drains and the legacy private coarse compiler now validate each final `Edge`
  factory identity and exact result type inside their native transactions; the coarse path applies
  the same check to its statistics result. Malformed or replaced factories cannot advance a
  cursor, publish output counters, or commit retained Scala role state, and bounded drains remain
  exactly retryable; the capability remains unadvertised.
- Hidden iterator preparation now validates canonical statistics and iterator factory identities
  and their exact result types before publishing the batch session or retained Scala role-state
  transition. Malformed factory returns fail with no session/counters or retained-map changes; the
  capability remains unadvertised.
- Hidden iterator preparation now constructs its final iterator wrapper, including its compiler
  owner and statistics references, before publishing the batch session or retained Scala
  role-state transition. Injected iterator allocation failure publishes no session/counters,
  releases exclusive role use, and leaves retained maps unchanged; the capability remains
  unadvertised.
- Hidden iterator preparation now constructs its final statistics object before publishing the
  batch session or retained Scala role-state transition. Injected statistics allocation failure
  publishes no session/counters, releases exclusive role use, and leaves retained maps unchanged;
  the capability remains unadvertised.
- Each hidden native iterator drain now constructs its final bounded tuple of `Edge` objects inside
  the cursor transaction. The wrapper no longer duplicates an intermediate Python tuple-edge list,
  and injected final-edge allocation failure leaves cursor position and counters retryable; the
  capability remains unadvertised.
- The legacy private coarse compiler now constructs final `Edge` and statistics objects inside its
  retained-role transaction, eliminating the wrapper's second ontology-sized Python edge list.
  Injected final-edge and final-statistics allocation failures leave reusable role state unchanged;
  the capability remains unadvertised.
- The legacy private coarse compiler now builds its required Python result list through a shared
  resumable cursor in 256-edge native chunks, with zero complete Rust output-vector edges. Its
  retained Scala role-state transition commits only after every edge and statistics tuple has
  been constructed successfully; the capability remains unadvertised.
- The hidden native iterator now emits through a resumable Rust cursor instead of retaining a
  complete projected-edge vector. Each FFI drain buffers at most the configured caller batch,
  cursor movement commits only after final `Edge` tuple construction, and additive provenance
  counters distinguish compiled edges, zero vector-backed edges, and the peak buffered batch. Exact
  immutable-input preflight now publishes that cursor with zero emission attempts, so ordered
  output traversal starts at the first caller drain. Installed-wheel evidence is recorded without
  advertising the compiler.
- The hidden native iterator now binds explicit `scala-instance` calls to persistent Rust
  subrole/inverse maps across ordered and conflicting views, reports retained-map counts, and
  switches permanently to the scalar lifecycle after any whole-operation scalar selection. Exact
  installed-wheel parity and transition evidence is recorded without advertising the compiler.
- The hidden broad encoded compiler now performs root-only annotation provenance joins for
  segmented multi-document closure and root tables, retaining every resolved lease and exposing
  exact closure/root segment counters without scalar traversal.
- Exact installed-wheel P7 evidence that the private root-provenance join preserves root-only
  annotations across diamond import deduplication and cyclic import closures without scalar
  materialization; the encoded compiler remains unadvertised.
- Private kernel-v30 root-provenance joining for exact-direct multi-document annotation
  projection, with closure-wide anonymous identifiers and explicit auxiliary-buffer counters; the
  encoded structural compiler remains unadvertised.
- Versioned `pyowl-projector.consumer-conformance/1` kit with a packaged CC0 ontology,
  Exact-compatible OWL2Vec*/literal/taxonomy goldens, snapshot/provider identity probes,
  provenance assertions, and path/stream/origin reparse sentinels.
- Reproducible Exact-OM 2.0 WP-B baseline comparison, consumer-handoff benchmark, and dependency-
  DAG CI that rejects projector reverse dependencies and OAEI projector/Exact dependencies.
- Public core closure-identity and loader-diagnostic provenance in reports and portable artifacts,
  with zero-copy mapped-snapshot support through `OntologyIdentityIndex`.
- Release audits for Java-facing optional dependencies and JVM/JNI markers embedded in native
  extension payloads.
- Release compatibility evidence that pins every pyOWLCore source-checkout lane while retaining a
  normal index-resolvable runtime dependency, plus complete/path-safe hash-manifest and
  duplicate-archive-member validation.

### Fixed

- Overlay and composite segment graphs now resolve through explicit frames, so valid deeply nested
  manifests no longer fail at Python's recursion limit while direct and transitive cycles retain
  their fail-before-output errors.
- Canonical structural identity comparison now computes memoized node lengths and streams bytes
  with explicit frames, preserving public-core canonical bytes for deep graphs without Python
  recursion or whole-node canonical materialization.
- Broad structural class-expression and data-range cycle preflights now use iterative color walks;
  valid 1,200-node chains no longer fail at Python's recursion limit, while forged cycles still
  fail before root classification or output.
- The broad Python structural decoder now performs the same iterative nested-annotation cycle
  preflight as kernel v31 for closure and retained root-provenance tables, closing the hidden
  fallback seam before root classification or output without advertising the compiler.
- Private kernel-v31 preflight now rejects direct or transitive cycles in nested annotation
  metadata for both closure and retained root-provenance tables before allocation, joining, or
  output; valid deeply nested metadata remains iterative and the capability stays unadvertised.
- Release evidence no longer reports the successfully projected NCIT checkpoint as a parser
  blocker; the remaining corpus blockers are the unavailable GO/licensed-scale inputs and
  hosted/reference-machine release evidence.
- Source-checkout test, native-wheel, and offline-packaging lanes now pin their approved
  pyOWLCore compatibility commit instead of following a mutable default branch. Consumer
  fixture evidence reflects core's reviewed acquisition-provenance-independent structural
  fingerprint without changing logical/signature fingerprints or projector edges.
- Portable artifacts no longer vary with automatic-backend fallback state or direct/provider/
  verified-wire execution provenance when their semantic inputs are identical.
- Malformed option/capability and native ABI metadata failures now retain stable projector error
  categories; caller-owned writers that report no progress fail instead of truncating silently.
- Native probing rejects unsupported CPython subinterpreters and free-threaded builds before
  importing the PyO3 extension, preserving the complete Python fallback policy.

### Compatibility

- Both Exact mini-ontology captures match without edge, multiplicity, ordering, or digest changes
  under `duplicates="unique"`, canonical isolated projection, and dedicated taxonomy semantics.
- Exact's consumer-side WP-M M0–M4 migration has landed independently; M5 and cross-repository
  release acceptance remain open and do not alter the projector dependency graph.
- No profile, default option, edge artifact schema, compiler-cache, sink protocol, or existing API
  behavior changed. The conformance and provenance fields are additive.

## 0.1.0rc1 — 2026-07-17

### Added

- Complete Java-free Python implementation of `mowl-d993536-v1`, with all 184 pinned Scala
  oracle invocations represented by checked-in golden evidence.
- Optional abi3 Rust edge-policy accelerator with exact Python differential tests. It remains
  explicit opt-in because the P3 auto-selection throughput threshold was not met.
- Bounded encounter and canonical streaming, checksummed external runs, exact disk-backed
  duplicate accounting, synchronous batch sinks, portable JSONL artifacts, and canonical hashes.
- Conditional PEP 517 backend: normal wheel/sdist builds do not request Rust tooling; explicit
  native builds request `setuptools-rust` in their isolated build environment.
- CPython 3.10–3.13 and platform packaging workflow definitions, compiler-absent/network-disabled
  fallback smokes, deterministic SBOM/license generation, artifact audits, hashes, and
  reproducibility comparison tooling.
- API/profile/artifact/cache compatibility table and beta-to-RC migration notes.

### Changed

- Version advanced from `0.1.0b1` to the unpublished `0.1.0rc1` release candidate.
- Metadata now claims CPython only; the unexecuted PyPy classifier was removed.

### Compatibility

- No projector rule, output ordering, multiplicity, public API, artifact schema, sink protocol,
  compiler-cache schema, or reference-profile change from `0.1.0b1`.

### Release blockers

- Final `0.1.0` publication remains blocked by a compatible ordinary-index pyOWLCore release,
  authenticated distribution-name ownership, private-index artifact selection, the hosted
  platform matrix, signed provenance, current vulnerability feeds, and unavailable release
  corpora. See `release/external-gates.json`.

## 0.1.0b1 — 2026-07-17

- Implemented the pure compiler, optional native edge engine, and bounded P4 streaming/artifact
  contracts. This prerelease was not published from this workspace.
