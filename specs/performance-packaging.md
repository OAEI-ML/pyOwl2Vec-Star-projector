# Performance, streaming, and packaging

## 1. Performance model

Let `A` be axioms visited, `R` projector-private role-map entries, and `E` emitted edges including
duplicates. Compilation and encounter-order streaming target `O(A + R + E)` time. The
projector must not create an ontology-sized duplicate graph or string table; it reuses snapshot
IDs/strings and shared lazy views.

Materialized `project()` necessarily owns `O(E)` edge objects. Large callers use `iter_edges`
or a batch sink. Canonical ordering costs `O(E log E)` comparison work and bounded in-memory
runs plus spill; encounter ordering remains `O(E)`.

## 2. Backend selection

The preferred backend is a Rust implementation exposed through PyO3. `backend="auto"` attempts
the native extension once and otherwise selects the complete Python backend. Failure is reported
by one `NativeBackendFallbackWarning` at first projection, never at import. Explicit Python is
quiet. Explicit native raises `NativeBackendUnavailableError` with the load cause.

The native bridge may borrow immutable contiguous core views or copy bounded batches. It must
not copy all axioms into a Rust-owned ontology model. Unsafe zero-copy code requires lifetime,
threading, mutation, and interpreter-shutdown tests plus a documented safety invariant.

The successor optimized implementation is normative in `native-structural-ingestion.md`: it
consumes the public encoded structural view in coarse calls, runs the complete projection compiler
in Rust, and emits bounded packed edge batches. The original scalar/native bridge remains a
compatibility path and cannot be presented as proof that encoded ingestion is optimized.

Semantic shortcuts are forbidden: the Python backend is complete, and native acceleration is
released only after exact differential parity.

## 3. Bounded-memory canonical streaming

`iter_edges(order="canonical")` uses deterministic external merge sort:

1. compile edges in deterministic encounter order;
2. fill a configurable buffer, default `250_000` edges;
3. sort each run by UTF-8 `(source, relation, destination)` bytes;
4. optionally coalesce equal edges for `duplicates="unique"`;
5. write a checksummed, versioned private run format to the selected temporary directory;
6. k-way merge runs, preserving or coalescing multiplicity as configured; and
7. delete every run on success, exception, cancellation, or process-controlled cleanup.

The spill format is an implementation detail and cannot be used for IPC or durable caches. File
names contain no ontology IRI or source path. Permissions are owner-only. Available-space and
configured spill limits are checked before and during output. Resource errors report required
and available estimates without deleting caller-owned files.

`order="encounter"` yields without global spill, but may still batch across the Python/Rust
boundary. Batch size cannot alter edges. Backpressure is natural: no more than the documented
buffer is produced ahead of the consumer.

Exact duplicate counts and `duplicates="unique"` require membership state. Encounter mode keeps
at most `buffer_edges` distinct keys in memory and, only after that bound, moves membership to a
private disk index with a fixed cache. This is not a global ordering pass: already-accepted edges
continue to yield synchronously. The index follows the same permissions, limits, and cleanup
contract as sorted runs.

## 4. Memory gates

Benchmarks separate snapshot RSS from incremental projector RSS. Release gates are:

- no second parsed ontology or serialized in-memory ontology is observable;
- encounter streaming peak incremental RSS is bounded by fixed compiler indexes plus
  `buffer_edges`, not by `E`;
- canonical streaming incremental RSS stays within the configured run buffer plus merge fan-in
  and private role indexes;
- the one-million-axiom synthetic case completes with `buffer_edges=100_000` under a 1.5 GiB
  process limit on the reference runner; and
- temporary spill is reclaimed after successful, cancelled, and injected-failure runs.

GO, NCIT, and the largest licensed test record absolute peak RSS. The initial evidence run sets
hardware-normalized baselines; subsequent CI fails above a 20% incremental-RSS regression and
release review investigates any absolute increase above 256 MiB.

## 5. Throughput gates

The benchmark suite measures load-excluded compile time, edge throughput, canonical sort/spill,
time to first edge, artifact writing, and backend-startup overhead. It pins CPU allocation,
warm-up, repetitions, fixture fingerprint, Python/Rust build mode, and compiler options.

Fixture microbenchmarks run on pull requests with a 25% median regression gate. Nightly/release
corpus jobs use robust medians and store raw samples. Native becomes the `auto` preference only
when it is at least 2x faster than Python on two large corpora, never slower by more than 10% on
the third, and stays within the memory gates. Until then, it may ship as opt-in experimental
without changing semantics.

Performance exceptions name the benchmark, owner, reason, accepted delta, and expiry release.
They may not waive correctness or unbounded-memory failures.

The encoded-native path additionally passes the no-materialization, boundary/copy/RSS,
scalar-native comparison, and Exact shared-stack gates in `native-structural-ingestion.md` before
`auto` promotion.

## 6. Distribution layout

The distribution name is provisionally `pyowl2vec-star-projector`, avoiding the existing PyPI
project `owl2vec-star`. The import package is `pyowl2vec_star_projector`. Before publishing, a
release-blocking PyPI ownership/reservation check must confirm the chosen distribution is
available and controlled by the project. If renamed, update all metadata/docs before the first
release; never publish under an ambiguous or unowned name.

Every installation contains the Python implementation. The intended artifact matrix is:

- platform wheels with an abi3 PyO3 extension where technically supported, while retaining the
  Python modules in the same wheel;
- a universal `py3-none-any` fallback wheel for platforms without a native wheel; and
- an sdist whose default PEP 517 build succeeds without Cargo/Rust and installs Python fallback.

Because package indexes cannot generally publish both a platform and a universal wheel with
identical compatibility selection guarantees for every environment without careful filename and
build-backend behavior, WP-P5 must prove the matrix in a private index before upload. Acceptable
designs include one distribution with optional native wheels or a separately named accelerator
distribution. The user-facing import/API and fallback warning contract stay the same. Packaging
feasibility is a release gate, not an assumption.

## 7. Wheel and interpreter matrix

The minimum matrix covers CPython 3.10, 3.11, 3.12, 3.13, and each newer supported CPython at
release time on:

- Linux x86_64 and aarch64 (`manylinux`, plus musllinux when claimed);
- macOS x86_64 and arm64; and
- Windows x86_64.

abi3 is preferred only after import, subinterpreter, free-threaded-build policy, and minimum-
Python tests pass. Unsupported targets receive the pure wheel rather than a source build that
unexpectedly requires Rust.

Each artifact is tested in a clean container/VM with `java`, `javac`, `ROBOT`, and Cargo absent:
import, snapshot load, Python projection, auto selection/warning, canonical golden, metadata,
and uninstall. Native wheels additionally run native parity and extension audit tools.

## 8. Dependencies and supply chain

Runtime requires only `pyowl-core>=0.2,<0.3` plus standard-library functionality for the Python
backend. Build-only Rust/PyO3 dependencies are lockfile-pinned and absent from fallback builds.
No runtime or optional extra may install Java-facing packages.

Releases publish hashes, signed provenance/attestations when infrastructure permits, an SBOM,
license inventory, reproducible build instructions, and results of dependency-vulnerability and
binary-symbol audits. The audit includes the existing `owl2vec-star` project as a non-normative
API/license/name comparison and documents that no source was copied.

The pinned mOWL source is BSD-3-Clause. The implementation is a documented behavioral rewrite,
not a pasted translation. Any upstream source vendored into the quarantined oracle retains its
copyright and full BSD license and is excluded from runtime artifacts unless release counsel has
approved the resulting notice layout. Golden provenance and release `NOTICE`/third-party-license
files identify mOWL, the source commit/blob, and the original OWL2Vec* research. The Apache-2.0
project license must never erase upstream attribution obligations.
