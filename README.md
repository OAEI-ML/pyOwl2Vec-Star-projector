# pyOwl2Vec-Star-projector

[![PyPI](https://img.shields.io/pypi/v/pyowl2vec-star-projector)](https://pypi.org/project/pyowl2vec-star-projector/)
[![Python](https://img.shields.io/pypi/pyversions/pyowl2vec-star-projector)](https://pypi.org/project/pyowl2vec-star-projector/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

`pyOwl2Vec-Star-projector` is a Java-free OWL2Vec* projection package for Python 3.10 and newer.
Its complete pure-Python compiler implements the pinned mOWL compatibility profile, including
historical bag multiplicity, role-map defects, lifecycle replay, annotation rendering, and
deterministic output. The Scala oracle is quarantined maintainer infrastructure and is never an
install, runtime, test, or release dependency.

The package consumes the same `pyowl_core.OntologyView` used by parsers, reasoners, and
callers—normally a concrete `OntologySnapshot`, and optionally a persistent `OntologyOverlay` or
zero-copy `OntologyComposite`. An in-process projection therefore does not parse, serialize,
materialize, or copy an ontology.
Standalone inputs remain available through an explicit convenience boundary which delegates the
complete `pyowl_core.OntologyInput` contract to `pyowl_core.coerce_snapshot(...)` once.

The first compatibility profile is pinned to mOWL's Scala
[`OWL2VecStarProjector.scala`](https://github.com/bio-ontology-research-group/mowl/blob/d9935369144f9a618ece38b7b2a8f4293afe8c26/gateway/src/main/scala/org/mowl/Projectors/OWL2VecStarProjector.scala)
at commit `d9935369144f9a618ece38b7b2a8f4293afe8c26`. Java is permitted only in a quarantined
development oracle that generates checked-in goldens. It is never an install, runtime, test, or
release dependency.

## Installation

```bash
python -m pip install pyowl2vec-star-projector
```

The normal installation includes the complete Python projector and requires no
Java or native compiler. Supported platform wheels may also contain the optional
Rust engine; select it explicitly with `ProjectionOptions(backend="native")`.

Start with the [documentation index](docs/index.md) and
[getting-started guide](docs/getting-started.md). Compatibility and migration
details are in [the compatibility matrix](docs/compatibility.md) and
[migration guide](docs/migration.md).

The Python backend is complete. `0.1.1` also contains an equivalent optional Rust/PyO3 edge
engine:

- `backend="python"` selects the complete, compiler-free fallback explicitly and quietly;
- `backend="auto"` uses the measured default backend and warns once when Python is selected; and
- `backend="native"` selects the extension explicitly or fails clearly when it is unavailable.

The P3 real-corpus measurements did not meet the required 2x end-to-end threshold, so this release
keeps native opt-in even when installed. This is a performance decision only: Python and native
produce the same ordered edges, multiplicities, diagnostics, and typed semantic errors. Explicit
Python is quiet; an unavailable explicit native request fails instead of silently changing
backend.

Start with the [specification index](specs/README.md). The normative API and behavior live in
[`SPEC.md`](specs/SPEC.md), while the observed Scala quirks that must remain projector-local are
catalogued in [`reference-behavior.md`](specs/reference-behavior.md).

## Status

Production release: `0.1.1`.

All 184 pinned Scala invocations match in canonical edge bytes,
including the expected typed inverse-property assertion failure and the loader-owned missing-
import outcome. The native edge-policy engine consumes bounded owned batches, stores no Python or
OWL objects, and P4 now applies global policy through bounded private runs rather than a complete
in-memory edge vector. Normal tests, installs, wheels, and sdists remain Java-free.

P5 supplies conditional compiler-free builds, platform workflow definitions, offline install
smokes, reproducibility/hash tooling, SBOMs, license inventory, compatibility tables, and release
instructions. The 0.1.1 workflow publishes the universal wheel, source distribution, and all five
supported native wheels atomically through an environment-protected PyPI trusted publisher.
Every closure remains visible in the machine-readable
[external gates](release/external-gates.json) and
[0.1.1 authorization](release/owner-release-authorization-0.1.1.md). See also the [compatibility
matrix](docs/compatibility.md), [migration notes](docs/migration.md), and
[release procedure](RELEASING.md).

P6 now ships a versioned consumer-conformance kit. Its CC0 fixture and three deterministic
goldens exercise Exact-compatible OWL2Vec*, literal, and dedicated taxonomy settings. The probe
accepts only a `SnapshotProvider` handoff: path, stream, or origin access raises a typed failure,
while successful verification asserts one provider call, exact snapshot identity, unchanged
fingerprints/counts, edge bytes, and provenance. The projector-side comparison against both
Exact-OM 2.0 WP-B mini-ontology captures has zero differences. Exact's consumer-side WP-M M0–M4
migration has now landed in its own repository without a reverse dependency; its M5 scale,
release-evidence, cleanup, documentation, and `2.1.0` gates remain open.

## Usage

Project an existing shared view without parsing or copying it:

```python
from pyowl2vec_star_projector import ProjectionOptions, Projector

projector = Projector()
edges = projector.project(
    ontology_view,
    options=ProjectionOptions(backend="python", include_literals=True),
)
assert projector.last_view is ontology_view
```

After successful consumption, `projector.last_report.provenance.ingestion` exposes the selected
path and its path-safe handoff diagnostics. It records monotonic encoded-view
publication/validation and compiler-setup durations plus bounded scalar-row, borrowed-buffer,
segment, posting, staging/structural-copy, parser/resolver/wire, scalar-materialization,
base-flattening, per-row-FFI, GIL, and retained native role-map property counters. The
operation-local zero counters document work that the projector boundary did not perform; scalar
paths publish the same vocabulary with exact zero encoded values. These execution diagnostics are
intentionally excluded from portable artifact hashes, and reading them never asks core for
another encoded view.

For low-latency consumption, set `order="encounter"` and use `iter_edges`; for bounded delivery,
use `project_to_sink` with a batch callback. `project_taxonomy` is the separate asserted named-
class taxonomy API and does not inherit the historical `only_taxonomy` defect.

Canonical iteration uses checksummed external runs. Tune bounded resources explicitly when an
application has tighter limits:

```python
from pyowl2vec_star_projector import ProjectionOptions, Projector, StreamingLimits

edges = projector.iter_edges(
    ontology_view,
    options=ProjectionOptions(backend="python", order="canonical"),
    buffer_edges=100_000,
    temp_directory="/private/projector-tmp",
    streaming_limits=StreamingLimits(
        merge_fan_in=32,
        max_open_files=64,
        max_total_edges=20_000_000,
        max_temporary_bytes=8 * 1024**3,
        max_spill_bytes=32 * 1024**3,
    ),
)
for edge in edges:
    consume(edge)
```

The iterator owns a random mode-`0700` workspace and mode-`0600` files. Exhausting, closing,
cancelling, failing, or normally shutting down removes them. Encounter mode is backpressured and
uses at most `buffer_edges` in-memory distinct keys before moving exact duplicate accounting to a
private bounded-cache disk index.

Write a portable JSONL artifact or calculate its canonical edge-record digest without building a
list or parsing again:

```python
result = projector.write_artifact(ontology_view, "edges.jsonl", buffer_edges=100_000)
digest = projector.canonical_digest(ontology_view, buffer_edges=100_000)
assert result.canonical_edges_sha256 == digest.sha256
```

Object sinks declare `protocol_version = 1` and implement `write_batch(tuple[Edge, ...])`; an
optional `finish(report)` receives the completed report. Existing callable batch sinks remain
supported. See the [P4 report](reports/p4/streaming.md) for artifact hashing, exact cleanup tests,
the million-axiom memory gate, and honest corpus availability notes.

Standalone inputs use the core facade exactly once:

```python
from pyowl2vec_star_projector import project_source

edges = project_source("ontology.ofn")
```

`project_source` accepts the full `pyowl_core.OntologyInput` contract. Existing snapshots,
overlays, composites, decoded wire views, and `SnapshotProvider` results retain concrete identity;
format detection, imports, resolvers, cancellation, and loader errors remain owned by core.

Consumer integrations can run the packaged handoff gate without Java or a native compiler:

```python
import pyowl_core
from pyowl2vec_star_projector import (
    consumer_conformance_fixture,
    consumer_conformance_fixture_metadata,
    verify_consumer_conformance,
)

fixture = consumer_conformance_fixture_metadata()
snapshot = pyowl_core.load_snapshot(
    consumer_conformance_fixture(),
    document_iri=fixture.document_iri,
    options=pyowl_core.LoadOptions(format=pyowl_core.DocumentFormat.FUNCTIONAL),
)
result = verify_consumer_conformance(snapshot, case_id="exact-owl2vec")
assert result.provider_calls == 1
assert result.source_accesses == 0
assert result.core_before == result.core_after
```

Use `identity_probes={"labels": lambda: source.labels_view}` to assert that consumer-owned lazy views
also retain object identity. The benchmark and Exact baseline comparator live under
`benchmarks/benchmark_consumer_handoff.py` and `tools/compare_exact_baselines.py`.

The successor P7 harness measures an already-loaded public view through the production projector
path and records first-edge/complete wall and CPU time, RSS, edge hashes, core operation deltas,
public ingestion phases/counters, and fail-closed acceptance evidence:

```bash
PYTHONPATH=src:../pyOWLCore/src python benchmarks/benchmark_encoded_compiler.py ontology.ofn \
  --format functional --load-backend native --projector-backend native
```

Use `--require-encoded-native` only as a release-evidence gate. It fails unless every repetition
selects encoded-native and exposes a complete zero-forbidden-counter, zero-staging-copy,
released-GIL record; it never relabels scalar fallback as accelerated evidence.

P7 development can separately measure the unadvertised exact-direct candidate with
`--private-native-candidate`. This calls the hidden iterator, labels every sample
`private-native-candidate`, hashes the complete ingestion and core-operation ledgers, and records
the loaded distribution `RECORD`, package module, native binary, feature ledger, and kernel
version. Supply the exact 40-character revisions with `--projector-revision` and
`--core-revision`. `--require-private-native-candidate` additionally requires both packages to be
loaded from installed distribution payloads. Source-tree runs can prove the private boundary but
cannot pass that installed-evidence gate.

Private-candidate mode is deliberately incompatible with `--require-encoded-native` and can never
set public `acceptance_ready`. Its evidence records the current production blockers: only exact
full `bytes` exporters or the canonical eleven-column packed direct-`bytes` arena are supported,
public iterator/sink/digest/artifact dispatch is unchanged, and the public Scala-instance lifecycle
remains scalar. The hidden iterator does retain Scala-instance role maps natively across ordered
calls, while maintaining a scalar-compatible shadow and selecting the scalar lifecycle permanently
after any native decline or other whole-operation scalar selection. Its native output is now a
resumable cursor: each drain owns at most the configured batch, reports zero vector-backed output
edges, and commits only after its final `Edge` tuple exists. No intermediate Python tuple-edge list
is returned to the wrapper, and a final-edge allocation failure leaves the cursor retryable.
Exhaustive immutable-input and exact-count preflight remains fail-before-publication, but the cursor
records zero emission attempts until the first caller drain. The legacy private coarse call now
uses the same cursor to build only its required Python list through 256-edge native chunks; it
retains no complete Rust output vector or second complete tuple-edge list. Final `Edge` and
statistics factories run inside the same
transaction, so reusable role state commits only after the complete final Python result exists.
The exact installed-wheel candidate checkpoints are recorded in
[`reports/p7/evidence/installed-coarse-cursor-checkpoint.json`](reports/p7/evidence/installed-coarse-cursor-checkpoint.json)
and
[`reports/p7/evidence/installed-final-result-checkpoint.json`](reports/p7/evidence/installed-final-result-checkpoint.json);
they are explicitly private, incomplete, and not release-performance evidence.

The final bounded-batch transaction is independently hash-bound in
[`reports/p7/evidence/installed-final-batch-checkpoint.json`](reports/p7/evidence/installed-final-batch-checkpoint.json).
Iterator preparation likewise constructs its final statistics object before publishing the batch
session or retained role transition; its exact failure-atomicity evidence is recorded in
[`reports/p7/evidence/installed-batch-session-checkpoint.json`](reports/p7/evidence/installed-batch-session-checkpoint.json).
The final iterator wrapper, with its compiler owner and statistics references, is now constructed
inside that same transaction; exact allocation-failure evidence is recorded in
[`reports/p7/evidence/installed-iterator-publication-checkpoint.json`](reports/p7/evidence/installed-iterator-publication-checkpoint.json).
Canonical factory identities and exact result types are validated before the transaction publishes;
exact malformed-result evidence is recorded in
[`reports/p7/evidence/installed-factory-validation-checkpoint.json`](reports/p7/evidence/installed-factory-validation-checkpoint.json).
Every final bounded-drain `Edge` and every legacy coarse-call `Edge`/statistics result now receives
the same exact-type and canonical-factory validation before cursor or retained-role commit; exact
retry and state-atomicity evidence is recorded in
[`reports/p7/evidence/installed-edge-factory-validation-checkpoint.json`](reports/p7/evidence/installed-edge-factory-validation-checkpoint.json).
The Python envelope checks retain those same import-time identities instead of rereading mutable
module globals after native commit; exact constructor-mutation evidence is recorded in
[`reports/p7/evidence/installed-canonical-wrapper-checkpoint.json`](reports/p7/evidence/installed-canonical-wrapper-checkpoint.json).
Native validation now also matches every final-object payload field to its transaction input before
commit; exact corruption and retry evidence is recorded in
[`reports/p7/evidence/installed-final-payload-validation-checkpoint.json`](reports/p7/evidence/installed-final-payload-validation-checkpoint.json).
The complete final batch is then revalidated after its last constructor callback, including
distinct object identity, and statistics are revalidated after the iterator callback; exact
installed evidence is recorded in
[`reports/p7/evidence/installed-complete-batch-validation-checkpoint.json`](reports/p7/evidence/installed-complete-batch-validation-checkpoint.json).
The hidden bounded and coarse transactions now allocate those exact slotted `Edge` objects directly
through the CPython stable ABI, with no Python `Edge` factory or constructor callback. Their
canonical object-base/member layout is checked before allocation and again after each complete
chunk; exact installed evidence is recorded in
[`reports/p7/evidence/installed-direct-edge-allocation-checkpoint.json`](reports/p7/evidence/installed-direct-edge-allocation-checkpoint.json).
The exact 60-slot statistics object is now allocated through that same validated stable-ABI
boundary on both coarse and batch-session paths. No 60-field argument tuple, Python statistics
factory call, or constructor callback remains; exact evidence is recorded in
[`reports/p7/evidence/installed-direct-statistics-allocation-checkpoint.json`](reports/p7/evidence/installed-direct-statistics-allocation-checkpoint.json).
Batch-session preparation now also allocates the exact eight-slot iterator directly, without its
three-field argument tuple or Python factory/constructor callback. Its compatible base, canonical
layout, owner/statistics identities, and initial state are transactionally validated; exact
evidence is recorded in
[`reports/p7/evidence/installed-direct-iterator-allocation-checkpoint.json`](reports/p7/evidence/installed-direct-iterator-allocation-checkpoint.json).
The cursor is also exercised through hidden Projector-level protocol-sink, canonical-digest, and
portable-artifact adapters while reusing the existing policy and cleanup machinery. Exact
installed-wheel evidence covers scalar-equivalent batches and reports, equal digests,
byte-identical artifacts, and sink-failure cancellation in
[`reports/p7/evidence/installed-stream-surfaces-checkpoint.json`](reports/p7/evidence/installed-stream-surfaces-checkpoint.json).
These adapters do not change ordinary public dispatch.
The load-excluded P7 harness can measure those hidden iterator, sink, digest, and artifact consumers
as separate labelled surfaces, binding the surface and its consumer metrics into the evidence hash.
Exact installed-payload smoke evidence is recorded in
[`reports/p7/evidence/installed-stream-surface-benchmark-checkpoint.json`](reports/p7/evidence/installed-stream-surface-benchmark-checkpoint.json);
it is not corpus-scale or public-acceptance evidence.

The hidden cursor's focused lifecycle matrix is recorded in
[`reports/p7/evidence/installed-lifecycle-safety-checkpoint.json`](reports/p7/evidence/installed-lifecycle-safety-checkpoint.json).
Against the isolated installed wheels it covers iterator handoff to another thread, concurrent
isolated-mode calls on one `Projector`, independent parent/child draining after a quiescent POSIX
fork, and normal interpreter shutdown with an unfinished retained cursor. Those cases complement
the existing owner-lifetime, released-GIL cancellation, close, fallback, sink-failure, and panic
conversion checks. They are private macOS checkpoint evidence, not a claim about multithreaded
fork, every platform/interpreter configuration, or public dispatch.

The replayable generated differential runner lives at
[`tools/differential_encoded_native.py`](tools/differential_encoded_native.py). Its exact installed
checkpoint runs 128 SplitMix64-generated mixed-rule ontologies through both the independent-bytes
and canonical packed-bytes core providers, covering all 32 combinations of the three historical
booleans, duplicate policy, and output order. It requires ordered edge and semantic-report parity
with scalar Python plus encoded-native, eleven-buffer zero-copy, zero-staging-copy, zero-per-row-FFI
evidence in every execution. The hash-bound result is in
[`reports/p7/evidence/installed-generated-differential-checkpoint.json`](reports/p7/evidence/installed-generated-differential-checkpoint.json).
This is a finite deterministic interaction matrix, not coverage-guided fuzzing.

The bounded invalid-column runner is
[`tools/hostile_encoded_native.py`](tools/hostile_encoded_native.py). Its exact installed checkpoint
uses the maximum 256 generated sources, both supported direct exporter layouts, and 29 predefined
validation cases per source, for 14,848 total executions covering all eleven structural columns.
Every case requires the same typed rejection through the direct compiler and hidden Projector,
equal failures across provider layouts, terminal failed state without a batch session or output
counters, zero edges, no report publication, and explicit native-view cleanup. The hash-bound
result is in
[`reports/p7/evidence/installed-encoded-column-validation-checkpoint.json`](reports/p7/evidence/installed-encoded-column-validation-checkpoint.json).
This is finite deterministic compatibility evidence, not mutational or coverage-guided fuzzing.

One bounded segmented-owner slice is now connected to the hidden Rust cursor. A bounded chain of
canonical one-segment `OVERLAY_BASE`/`ALL` views with empty local columns, postings, and
anonymous-scope maps fully revalidates each container and its terminal exact-direct source, then
passes only the terminal source buffers to Rust without flattening or staging. The three-alias
checkpoint retains every owner and reports 44 retained zero-copy buffers, 11 Rust-detached
buffers, four retained segments, and three referenced views. Both direct exporter layouts have
exact scalar edge/report parity; depth, cumulative-work, and transitive-cycle failures occur before
output. Edited overlays, annotation-sensitive aliases, multiple posting-selection layers,
`INCLUDE`, and other segmented families remain on whole-operation fallback. The initial one-alias
evidence remains in
[`reports/p7/evidence/installed-empty-overlay-alias-checkpoint.json`](reports/p7/evidence/installed-empty-overlay-alias-checkpoint.json);
the recursive exact installed-wheel evidence is in
[`reports/p7/evidence/installed-recursive-empty-overlay-alias-checkpoint.json`](reports/p7/evidence/installed-recursive-empty-overlay-alias-checkpoint.json).
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
[`reports/p7/evidence/installed-excluding-overlay-alias-checkpoint.json`](reports/p7/evidence/installed-excluding-overlay-alias-checkpoint.json).
It is focused private correctness evidence, not general posted-overlay, performance, public
dispatch, or release acceptance.

The lifecycle binding is independently hash-bound in
[`reports/p7/evidence/installed-scala-lifecycle-checkpoint.json`](reports/p7/evidence/installed-scala-lifecycle-checkpoint.json).
Three native calls cover initial role acquisition, later restriction/domain/range consumption, and
conflicting overwrite behavior; a separate injected-decline sequence proves a one-way transition
to scalar compilation without re-entering stale native state. That lifecycle checkpoint used
kernel v31; the bounded, lazy, coarse-cursor, final-result, final-batch, batch-session,
iterator-publication, factory-validation, edge-factory-validation, canonical-wrapper,
final-payload-validation, complete-batch-validation, direct-edge-allocation, and
direct-statistics-allocation and direct-iterator-allocation checkpoints advance the private kernel
through v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, and v46 while the
public feature ledger remains exactly
`abi3-py310` and `bounded-batches`.

The hidden candidate also proves root-scoped annotation provenance before admitting visible
annotations. Kernel v30 retains an unequal exact-direct root table alongside the closure and joins
its canonical annotation identities to closure nodes before edge counting or publication. This
keeps imported subclasses native while suppressing imported-only annotations, preserves
closure-wide anonymous identifiers, and reports both retained zero-copy tables. Byte-identical
single-document selections still use one table; unavailable, non-direct, or sliced root providers
select one whole-operation scalar compiler. Annotation-free imported closures skip the root request.
The source-built installed correctness checkpoint is recorded in
[`reports/p7/evidence/installed-root-provenance-join-checkpoint.json`](reports/p7/evidence/installed-root-provenance-join-checkpoint.json);
it carries no release or performance claim. The preceding fail-closed fallback evidence remains in
[`installed-annotation-provenance-checkpoint.json`](reports/p7/evidence/installed-annotation-provenance-checkpoint.json).

Kernel v31 also closes a hostile-input hole in nested annotation metadata. Local annotation fields
were already validated, but forged structural columns could form a self or transitive cycle while
retaining valid tags and arities. The private kernel now performs an iterative graph preflight on
both the closure and any retained root table before provenance matching or output allocation. A
4,096-level acyclic chain and installed closure/root cycle cases prove stack-safe admission and
fail-before-output rejection. The exact installed correctness checkpoint is recorded in
[`reports/p7/evidence/installed-annotation-cycle-checkpoint.json`](reports/p7/evidence/installed-annotation-cycle-checkpoint.json);
the public feature ledger remains unchanged.

The broad Python structural decoder now enforces the same acyclic nested-metadata invariant before
root classification. This covers both its closure table and the independently retained root table,
so the hidden broad-decoder path cannot compare or compile a forged cyclic annotation identity.
Two exact installed-wheel hostile cases and the complete suite are hash-bound in
[`reports/p7/evidence/installed-broad-annotation-cycle-checkpoint.json`](reports/p7/evidence/installed-broad-annotation-cycle-checkpoint.json).

The broad decoder's recursive class-expression and data-range checks are stack-safe as well. They
now use iterative color walks while retaining their existing accepted envelope, fallback choices,
and stable cycle failures. Exact installed tests admit independently forged 1,200-node acyclic
chains for both graph families, reject both cyclic variants before output, and are recorded in
[`reports/p7/evidence/installed-recursive-graph-checkpoint.json`](reports/p7/evidence/installed-recursive-graph-checkpoint.json).

Cross-table and cross-segment canonical identity comparison no longer reintroduces a recursion
limit after that preflight. The cursor computes child-first lengths with explicit frames and emits
the same public-core canonical bytes through lazy component frames, retaining only its per-node
length cache and traversal state. Exact byte parity, sequence order, 1,200-node class/data streams,
and generic cycle rejection are recorded in
[`reports/p7/evidence/installed-canonical-cursor-checkpoint.json`](reports/p7/evidence/installed-canonical-cursor-checkpoint.json).

Segment-manifest traversal is stack-safe too. Overlay and composite dependencies are suspended in
explicit resolver frames while preserving source-local postings, composed anonymous scopes,
lease retention, cache reuse, and active-path cycle rejection. An exact installed 1,100-overlay
chain plus nested overlay/composite and hostile-cycle cases are recorded in
[`reports/p7/evidence/installed-segment-resolution-checkpoint.json`](reports/p7/evidence/installed-segment-resolution-checkpoint.json).

That resolver now also covers the broad compiler's multi-document annotation-provenance join.
Supported overlay/composite closure tables and segmented root selections retain all source leases,
intersect canonical root identities, and suppress imported-only annotations without scalar
traversal. Exact installed parity and fallback evidence is recorded in
[`reports/p7/evidence/installed-segmented-provenance-checkpoint.json`](reports/p7/evidence/installed-segmented-provenance-checkpoint.json).

The same v31 boundary is installed-tested over diamond and cyclic import graphs. A shared diamond
member is projected once, a two-document cycle terminates with both taxonomy edges, and in each
topology only the root document's annotation remains visible. Both runs retain the independent
closure/root tables, apply the exact selected-edge limit, and report zero scalar materialization.
The hash-bound record is
[`reports/p7/evidence/installed-import-topology-checkpoint.json`](reports/p7/evidence/installed-import-topology-checkpoint.json).

## Optional native build

Every distribution contains the complete Python backend. The default build is always the
compiler-free universal fallback and requests no Rust build tooling. Build a platform wheel with
the pinned Rust accelerator by setting `PYOWL2VEC_BUILD_NATIVE=1`; the conditional PEP 517 backend
then installs `setuptools-rust==1.13.0` into the isolated build environment. Cargo and rustc must
already exist for this explicit path. The extension uses PyO3's `abi3-py310` API; the release
matrix covers CPython 3.10 through 3.13.

```bash
PYOWL2VEC_BUILD_NATIVE=1 python -m build --wheel
```

The advertised Rust boundary owns only strings for edge batches. Closing a projection iterator
cancels and clears its processor; native panics are contained and resource failures become stable
projector exceptions. An unadvertised P7 foundation additionally retains exact public
structural-column views backed by full immutable `bytes` exporters or the native provider's
canonical eleven-column packed immutable-`bytes` arena for a direct, unannotated named-class and
named-role kernel (`SubClassOf`, `EquivalentClasses`, `ClassAssertion`, simple restrictions, and
named-individual object-property assertions plus domain/range products); negative named/inverse
object-property assertions are validated and skipped, while positive inverse assertions preserve
the pinned typed failure. Unannotated named/inverse subproperty and inverse-property axioms now
expand restriction and domain/range edges in exact compatibility order; object-property
equivalence, disjointness, and characteristics are validated skipped roots. It is not selected by
production dispatch and is not a complete compiler. See the
[P3 report](reports/p3/native-backend.md) for parity, performance, memory, and binary evidence.
The [P4 report](reports/p4/streaming.md) covers bounded external sorting and artifacts.
