# Verification and parity plan

## 1. Testing layers

Conformance is established in five layers:

1. core-model contract tests using hand-constructed snapshots;
2. pure-Python rule and property tests;
3. checked-in golden tests produced by the pinned Scala oracle;
4. exact native-versus-Python differential tests; and
5. scale, packaging, and consumer integration tests.

Normal CI has no JDK, Scala, mOWL, OWLAPI, ROBOT, DeepOnto, or network dependency. Java appears
only in the quarantined golden-regeneration job.

## 2. Quarantined reference oracle

The oracle is a digest-pinned container or locked environment containing Java 11, Scala 2.11.12,
OWLAPI 4.5.22, and the source at mOWL commit
`d9935369144f9a618ece38b7b2a8f4293afe8c26`. It accepts an ontology fixture plus the three
historical booleans and writes raw triples, preserving list order and multiplicity, alongside:

- upstream commit and source-blob hashes;
- dependency/JDK/container digests;
- input bytes SHA-256 and imported-document manifest;
- options and ordered invocation history;
- raw edge count and edge-counter SHA-256; and
- stderr/stdout captured as non-contract diagnostics.

The oracle directory is excluded from release wheels and sdists. Its generated golden JSON and
metadata are committed. Regeneration requires an explicit maintainer command, yields a clean
working-tree diff, and never runs in ordinary pull requests. A scheduled/manual job runs the
oracle and fails if freshly generated counters differ from committed goldens.

The implemented oracle uses deterministic JSON documents rather than separate JSONL streams so
the raw list, counter, canonical derivative, metadata, and diagnostics for each eight-flag matrix
remain one review unit. `tools/java-oracle/regenerate.py` is the one-command entry point. It
verifies the four staged mOWL Git blobs, every Maven runtime JAR, and the JDK SPDX SBOM before
executing the projector. The committed dependency lock and environment digest are the locked
environment equivalent of a container digest; no unbuilt OCI image is presented as evidence.

Oracle precedence is explicit:

1. a fresh instance of the pinned Scala projector and its captured edge bag is authoritative;
2. Exact-OM's current `exact/ontology/projection.py` plus its committed WP-B mOWL captures are a
   secondary executable comparator, especially for Java-hash ordering and the sibling-subrole
   overwrite emulation; and
3. mOWL's public documentation table and prose are explanatory only.

If the first two differ, the fresh Scala golden wins and the Exact migration deviation is
documented before changing any Exact baseline. The Scala object's undesirable cross-call role
state is not part of fresh-instance goldens: it has a separate ordered-session fixture exercised
only with `compatibility_state="scala-instance"`.

## 3. Comparison semantics

Strict Scala parity compares a multiset/`Counter[(source, relation, destination)]`, because
OWLAPI traversal order is not stable and Scala `Triple.distinct` uses reference equality.
Canonical projector output is separately compared byte-for-byte after the specified UTF-8 sort.

Tests must never hide missing multiplicities by converting both sides to sets. For
`duplicates="unique"`, the expected result is derived by stable de-duplication of the golden
bag, not by a separate handwritten oracle.

The native backend must equal the Python backend in ordered `Edge` sequences, exception types
and fields, provenance fields other than backend identity/performance, ignored-shape counts, and
portable artifact bytes.

## 4. Fixture matrix

Fixtures are small and redistributable. Together they exercise:

| Area | Required cases |
|---|---|
| imports | none, one level, diamond, cycle, excluded/missing import policy |
| subclass | named/named; restriction on each side; some/only/min/max/exact; qualified/unqualified |
| fillers | named, intersection, union, anonymous/unsupported |
| equivalence | 2 and 3+ members; member permutations; named, restriction, intersection, union |
| roles | one and sibling subroles, chained subroles, multiple inverses, order-sensitive collisions |
| domain/range | zero/one/many each; cross-product; effective-pass classification; annotated duplicates; anonymous classes |
| assertions | named and anonymous individuals/classes/properties; object and data assertions |
| annotations | every allowed datatype; language strings; unsupported datatype; non-class subjects |
| flags | every combination of the three historical booleans |
| multiplicity | equal triples from independent axioms/rules and annotated domain/range axioms |
| lifecycle | fresh call, repeated same ontology, two ontologies with conflicting role maps |
| Unicode | non-ASCII IRIs/literals, combining forms, embedded control escapes |

Every fixture inventory records which rule it isolates. At least one "kitchen sink" fixture
guards rule interaction, but no behavior is proven only by that fixture.

The checked-in inventory and `reference-rules.json` are machine-cross-validated in Java-free CI.
Synthetic ontology inputs are CC0-1.0 and contain no third-party ontology content.
The missing-import case records the strict oracle loader's typed pre-projection failure; import
resolution policy remains a `pyowl-core` concern and is not silently redefined by the projector.

All `2^3 = 8` combinations of `bidirectional_taxonomy`, `only_taxonomy`, and
`include_literals` run against each applicable golden. Both duplicate policies and both
backends then derive from those cases. The dedicated taxonomy API has independent goldens so the
upstream flag defect cannot contaminate it.

## 5. Property and metamorphic tests

Generated snapshots check that:

- canonical order is monotone by raw UTF-8 tuple bytes;
- unique output is exactly the stable set of preserve output;
- chunk size, worker count, sink batch size, and spill directory do not change canonical bytes;
- explicit Python selection never emits a fallback warning;
- auto fallback warns exactly once per process;
- projecting never changes snapshot fingerprints, axiom counts, or lazy-view identity;
- a `SnapshotProvider` supplies the exact snapshot identity once;
- encode/decode and memory-mapped core wire yield identical edges;
- locale, `PYTHONHASHSEED`, timezone, platform, and repeated runs do not change output; and
- cancelled/failed iterators close temporary files and leave the snapshot usable.

Malformed options and corrupt/unsupported core wire inputs have typed, stable failure tests.

P7 also supplies `tools/differential_encoded_native.py`, a bounded deterministic valid-input
campaign for the unadvertised encoded cursor. SplitMix64 keeps source generation stable across
supported Python versions; each failure identifies only the seed, provider, and violated invariant.
The exact checkpoint covers both supported direct exporter layouts, all 32
semantic-boolean/duplicate/order combinations, and batch bounds one through seven while comparing
ordered edges, counts, diagnostics, and call-history provenance. It fails on scalar fallback,
staging copy, per-row FFI, or a missing released-GIL record. It supplements rather than replaces
the malformed, mutational, sanitizer, Scala-oracle, segmented-provider, and corpus matrices.

P7 also supplies `tools/hostile_encoded_native.py`, a bounded deterministic invalid-column
campaign for the same private cursor. Twenty-nine predefined cases exercise tag, reference,
canonicality, offset, shape, and scalar validation across all eleven structural columns and both
supported direct exporter layouts. Every case requires a typed compatibility failure before
output, equal direct/Projector and provider-layout failures, terminal failed state without a batch
session or output counters, no report publication, and closeable-view cleanup. The exact checkpoint
runs the maximum 256 generated sources, or 14,848 cases. It remains finite invalid-fixture
verification, not coverage-guided or mutational fuzzing, sanitizer evidence, or an exhaustive
invalid-input claim.

The exact empty-overlay-alias checkpoints exercise the hidden cursor through both supported direct
exporter layouts behind canonical `OVERLAY_BASE`/`ALL` containers. The recursive checkpoint
requires exact scalar edge/report parity through a three-container chain, every owner to remain
retained until cursor close, 44 retained and 11 Rust-detached zero-copy buffers, four segments,
three referenced views, and zero posting, staging, flattening, scalar-materialization, or
per-row-FFI work. The default public depth boundary admits 32 aliases and rejects 33 before report
publication; reduced depth and cumulative-work limits and a transitive cycle likewise fail before
output. Edited and annotation-sensitive overlays still select one whole-operation scalar compiler,
while malformed referenced columns fail before output or report publication. This focused slice
does not establish posting, local-delta, composite, mmap, public-dispatch, or corpus acceptance.

## 6. Real-ontology corpus

Release candidates run on legally obtainable pinned versions of GO, NCIT, and a SNOMED-scale
or equivalently large synthetic ontology. Ontology bytes are downloaded only in an opt-in job,
verified against a manifest, and never silently refreshed. If redistribution is forbidden, the
manifest, acquisition instructions, summary counters, and approved hashes are committed instead.

The corpus records snapshot fingerprints, compiler options, edge counters, output digest,
duration, peak RSS, spill bytes, and backend. A changed source ontology creates a new corpus ID;
it never overwrites a baseline.

## 7. Consumer conformance

Exact-OM integration proves:

- its `OwlOntologySource.owl_snapshot()` returns the same object used for labels/hierarchy;
- projection performs no parser call and no structural-record conversion;
- `duplicates="unique"` plus the relevant profile reproduces its committed 2.0 projection
  baselines, with every intentional difference reviewed; and
- Exact remains usable without installing native wheels or reasoners.

No projector dependency is added to OAEI-Bio-ML-eval; a dependency-cycle test enforces the DAG
in the spec index.

The projector publishes this gate as `pyowl-projector.consumer-conformance/1`. Its packaged CC0
fixture has immutable bytes, fingerprints/counts, ordered edge goldens, and canonical digests for
Exact-compatible OWL2Vec*, literal, and dedicated-taxonomy cases. `SnapshotProviderProbe` exposes
only `owl_snapshot()` successfully and fails path/stream/origin access, so a passing consumer test
proves one provider call and no source reparse. Optional identity callbacks assert that consumer-
owned label/hierarchy/lazy views remain the same objects.

`tools/compare_exact_baselines.py` performs the secondary executable comparison without importing
Exact: it loads each committed fixture once through core and compares ordered unique output to
Exact's compressed WP-B capture. Any difference is release-blocking and remains unclassified;
the tool never rewrites a baseline. `tools/check_dependency_dag.py` inspects both dependency
metadata and runtime imports, including OAEI's optional coherence dependencies.

## 8. Merge and release gates

A release is blocked unless:

- all fixture counters match the pinned Scala goldens;
- Python/native ordered outputs and artifacts match exactly;
- every supported Python/platform install smoke test projects a fixture with Java unavailable;
- sdist installation succeeds in an isolated environment without Rust;
- snapshot identity/no-reparse instrumentation passes;
- deterministic and bounded-memory tests pass;
- benchmark thresholds in `performance-packaging.md` pass or have a reviewed, expiry-dated
  exception; and
- provenance and software-bill-of-materials audits show no Java runtime dependency.

Golden changes require two reviewers and a changelog that classifies each difference as upstream
pin correction, oracle correction, or new profile. "Update expected output" is not sufficient.
