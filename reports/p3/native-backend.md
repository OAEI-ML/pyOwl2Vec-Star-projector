# WP-P3 native backend report

## Outcome

`0.1.0b1` adds a complete optional Rust/PyO3 edge-policy backend and leaves the Python compiler
as the semantic authority. Exact parity passed, but the native implementation did **not** satisfy
the release rule requiring a 2x end-to-end improvement on two large corpora. Consequently
`backend="native"` is an explicit opt-in and `backend="auto"` continues to select the complete
Python implementation with the once-per-process fallback warning. No P4 external merge/spill
behavior is present.

## Boundary and ownership

Profiling was performed before choosing the native boundary. A prepared Python `Compilation`
continues to implement the pinned mOWL quirks, role state, structural dispatch, diagnostics, and
typed semantic failures. Rust receives only owned `(source, relation, destination)` strings in
bounded batches and applies duplicate accounting, stable encounter filtering, and canonical
UTF-8 tuple ordering. This is the narrowest boundary available before `pyowl-core` exposes a
reviewed contiguous/interned bulk view; it avoids inventing a Rust OWL model or copying all axioms
into Rust.

The invariants are:

- the extension contains no OWL type and never receives, mutates, or retains a core view;
- a processor owns only edge strings and counters for one invocation;
- Python-to-Rust pushes and Rust-to-Python drains are bounded by `buffer_edges`;
- canonical sorting is still in-memory because external runs belong exclusively to P4;
- encounter output is emitted after each batch; duplicate accounting retains an exact distinct
  set, matching the Python backend;
- `finish()` releases the GIL around Rust sorting; processors share no mutable global state;
- iterator close/exception calls `cancel()`, clearing vectors and hash tables;
- all fallible reservations use `try_reserve`, the injected allocation limit is tested, and
  Python converts native memory errors to `ProjectionResourceError`;
- every exposed operation has a `catch_unwind` guard; injected panics become a normal Python
  `RuntimeError` and then `ProjectionError` at the public bridge;
- no Python references are stored by Rust, so drop during interpreter shutdown requires no GIL;
  and
- the crate contains no project-authored `unsafe` block.

## Correctness evidence

The native-enabled suite passed on CPython 3.10 and 3.12 with 95 tests plus 6 `unittest`
subtests on each interpreter. It covers:

- every fresh oracle fixture and all eight historical flag combinations;
- preserve/unique and encounter/canonical policy combinations;
- all three ordered mutable-role lifecycle sessions;
- exact ordered edges, error class/code/message/details, diagnostics, ignored/skipped counts,
  duplicate counts, semantic call-history digest, and report fields other than backend identity;
- the independent taxonomy API for all policy combinations;
- 800 deterministic generated differential cases (40 seeds, two duplicate policies, two orders,
  and five boundary chunk sizes), including NUL, controls, composed/decomposed Unicode, CJK, and
  supplementary-plane text;
- snapshot identity/fingerprint non-mutation, four-thread reentrancy, iterator cancellation,
  panic containment, injected allocation failure, and unfinished-processor interpreter shutdown.

All generated differential cases had zero mismatches. The original 160 oracle cases and 184
oracle invocations remain green; the expected inverse-property assertion failure is raised by
Python semantic compilation before the native edge stage and therefore retains exactly the same
typed fields.

## Profiling and performance decision

Measurements ran on macOS x86_64 (Darwin 25.5.0), CPython 3.12.3, Rust 1.97.1, release LTO,
one process, no concurrent workload. Ontology parsing and construction of the indexed activation
view were excluded. The reproducible runner is `benchmarks/benchmark_backends.py`.

### Python profile

The local OAEI Bio-ML DOID target contains 55,687 parsed axioms and emits 9,388 edges. A Python
profile recorded 11.434 s under profiler instrumentation. `prepare_compilation` consumed 11.334 s
(99.1%); structural `canonical_bytes` sorting accounted for 7.622 s and anonymous-value traversal
for 3.441 s. Edge-policy work was below 1% of profiled end-to-end time. A second profile using the
uncached compatibility view was even more dominated by shared-view signature construction.

This explains the measured limit: the implemented Rust stage cannot produce a 2x end-to-end
gain while more than 99% of profiled time remains before its boundary. Moving that structural
work would require a reviewed core bulk/string-ID contract; serializing a second ontology model
is prohibited.

### Corpus results

| Corpus | Axioms | Edges | Mode | Python median | Native median | Native/Python |
|---|---:|---:|---|---:|---:|---:|
| OAEI Bio-ML DOID target | 55,687 | 9,388 | encounter, 7 runs | 2.370 s | 2.702 s | 1.140x |
| OAEI Bio-ML DOID target | 55,687 | 9,388 | canonical, 7 runs | 2.659 s | 2.736 s | 1.029x |
| HermiT pizza functional example | 939 | 401 | encounter, 7 runs | 0.03017 s | 0.02858 s | 0.948x |
| HermiT pizza functional example | 939 | 401 | canonical, 7 runs | 0.02548 s | 0.02592 s | 1.017x |

The DOID source file was 6,687,536 bytes with SHA-256
`76f41cce3616ad1a9ba6353f469e96bde7addba5d43e541651a3ab703f9ba2bc`; its core document
fingerprint was `dba81ae191733905b32bdf92e0c40de44ab81a246086db1d28e6db97f4bea1e7`.
A separate three-run repetition had Python/native medians of 2.323/2.206 s encounter and
2.518/2.502 s canonical, demonstrating ordinary host noise but still nowhere near 2x. Peak
process RSS for that parse-plus-benchmark process was 193,200,128 bytes.

The pizza functional source was 228,538 bytes with SHA-256
`64d2f3c8219108293c2bd19ef6a9ea26e80c74b7114471c4c161b0c106a5f683`; its process peak RSS
was 28,508,160 bytes.

The available 57,163,710-byte NCIT source was attempted first and is recorded rather than silently
discarded. `pyowl-core` WP02 stopped during load with typed `UnsupportedSyntaxError`
(`RDF_MAPPING_UNSUPPORTED`: a blank node was ambiguously used as individual and list), even with
partial RDF mapping enabled. Its SHA-256 was
`379a37f47c0c8e7c30397769358cca955140d16b2797a1cc75da4b1fc2b354eb`. Because loading is core
scope, P3 did not weaken RDF mapping or construct a private parser. The performance exception is
owned jointly by core/projector integration and expires at `0.1.0rc1`; it cannot enable `auto`.

### Scaling and memory surrogate

The deterministic one-million-edge canonical/preserve benchmark completed with matching output
digest `73d6d617827db55b87acc6c1879bcda97ad1be3250686a7eae0d8f457c9a5de5`. Python took 0.734 s,
native took 6.062 s, and total process peak RSS was 1,180,102,656 bytes, below the 1.5 GiB
reference cap. This deliberately includes a materialized million-edge input and output and proves
the P3 boundary is finite, but it is not claimed as the P4 one-million-axiom external-sort gate.
The large transfer cost reinforces the opt-in decision.

## Rust, package, and binary gates

- `cargo fmt --check`: passed.
- `cargo clippy --locked --all-targets --no-default-features -- -D warnings`: passed.
- debug Rust unit tests without extension-module linking: 4 passed.
- release extension build from the lockfile: passed.
- a scheduled Linux nightly AddressSanitizer job and regular debug/fmt/Clippy jobs are defined in
  `.github/workflows/native.yml`; the local stable macOS toolchain cannot execute nightly ASan.
- Ruff, strict mypy, runtime dependency audit, and pinned baseline audit: passed.
- explicit native wheel:
  `pyowl2vec_star_projector-0.1.0b1-cp310-abi3-macosx_14_0_x86_64.whl`; clean Python 3.10 and
  3.12 installs imported the same extension and passed the Unicode/duplicate smoke projection.
- pure sdist/wheel builds completed with Cargo absent; the pure wheel is `py3-none-any`.
- `otool -L` found only the extension install name, `/usr/lib/libiconv.2.dylib`, and
  `/usr/lib/libSystem.B.dylib`; symbol filtering found `PyInit__native` and no JNI/Java/JVM symbol.
- wheel/sdist archive checks exclude Java/Scala/JAR/class files, oracle sources, fixtures,
  goldens, Rust target output, and compiled native code from the sdist/pure wheel.
- both wheels bundle the project license/notice, third-party notices, and the lockfile-matched
  native dependency license inventory through PEP 639 license metadata.
- Cargo dependencies are checksum locked and their SPDX expressions are recorded in
  `native/THIRD_PARTY_LICENSES.md`.

## Handoff

P4 may replace in-memory canonical storage with external sorted runs without changing the native
semantic interface. A future performance work package should revisit the Rust boundary only after
`pyowl-core` publishes immutable contiguous string-ID/axiom views; until the two-large-corpus 2x
gate is demonstrated, changing `NATIVE_AUTO_PREFERRED` to true is release-blocking.
