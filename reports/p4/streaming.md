# P4 deterministic bounded-memory streaming report

## Outcome

P4 is implemented for the Python and optional native dispatch paths. Canonical projection no
longer materializes the complete edge collection in Python or Rust. It writes owner-only,
checksummed binary runs, performs bounded fan-in merges, and removes its private workspace on
normal exhaustion, explicit close, cancellation, exceptions, `KeyboardInterrupt`, garbage
collection, and normal interpreter shutdown.

Encounter projection is synchronous and backpressured. It yields the first eligible named
taxonomy edge before traversing the remaining axioms. Exact duplicate accounting uses an
in-memory set no larger than `buffer_edges`; if the distinct set exceeds that bound, membership
moves to an owner-only SQLite index with a fixed 2 MiB page cache. This is an exact membership
index, not a global ordering pass, and it is removed with the iterator.

The public additions are:

- `StreamingLimits` for merge fan-in, open files, edge count, live temporary bytes, total spill
  bytes, and cancellation cadence;
- `BATCH_SINK_PROTOCOL_VERSION == 1` and `EdgeBatchSinkV1.write_batch(...)` in addition to the
  existing callback sink;
- `Projector.write_artifact(...)` / `write_edge_artifact(...)` for portable JSONL;
- `Projector.canonical_digest(...)` for a single-traversal canonical edge-record digest; and
- path-free `Projector.last_spill_metrics` evidence.

## External run and lifecycle invariants

Run names and workspace names contain random tokens only. Directories are mode `0700` and files
are `0600`. A run contains a version magic, edge count, payload length, SHA-256 payload checksum,
and length-prefixed UTF-8 fields. Readers validate the header, file size, record bounds, UTF-8,
count, trailing bytes, and checksum. The format is private and is not accepted as IPC or a cache.

Before growth, the writer enforces configured live/cumulative byte limits and a maintained
available-space estimate; filesystem and SQLite failures become `ProjectionResourceError` with
required/available or limit/observed fields. Multi-pass reduction never opens more than
`merge_fan_in + 1` files. The default is fan-in 32 under a 64-file cap.

The native P3 processor is invoked on bounded raw batches and P4 applies the global policy. Its
first batch has one edge for low latency; later batches use `buffer_edges`. This avoids the P3
native processor's whole-result canonical vector and whole-stream distinct set while retaining
native/Python semantic parity.

Tests inject cancellation, backend failure, sink failure, a zero-free-space filesystem,
checksum corruption, `KeyboardInterrupt`, and normal process exit. Every case leaves the caller's
temporary parent empty. An artifact destination path is replaced atomically only after the
metadata and payload are complete; caller-owned files are not cleanup targets.

## Portable artifact definition

Artifacts are UTF-8 JSON Lines with `\n` on every platform. The first record carries schema,
semantic options, snapshot fingerprints, core/projector versions, counts, warning summary, edge
digest, and artifact digest; following records are edges. No timestamp or machine path is in the
hashed payload. `artifact_sha256` uses the explicitly recorded preimage:

```text
canonical metadata JSON without artifact_sha256 + "\n" + exact edge records
```

Backend selection is execution provenance rather than edge semantics, so the portable metadata
records backend semantic API versions but excludes the selected/requested backend. This makes
Python and native artifacts byte-identical, as required. Full selected-backend provenance remains
available in `ProjectionReport`.

## Correctness and resource verification

The P4 suite covers buffer sizes 1/2/7/100, fan-ins 2/3/4/8, both duplicate policies, Unicode and
multiplicity through the pinned corpus, callback and protocol sinks, canonical digest
verification, permissions, and cleanup. The full Java-free suite passes all 108 tests plus six
`unittest` subtests on the primary Python 3.12 runner.

The final checkpoint gates were:

- CPython 3.10: 108 tests plus six subtests passed;
- CPython 3.12: 108 tests plus six subtests passed;
- Ruff over runtime, tests, benchmarks, and tools: passed;
- strict mypy over all 14 runtime modules: passed;
- runtime dependency and pinned-baseline audits: passed;
- isolated PEP 517 sdist plus `py3-none-any` wheel build: passed without invoking Cargo; and
- clean Python 3.10/3.12 wheel smoke tests with `JAVA_HOME` absent and `PATH=/usr/bin:/bin`:
  import plus multi-run Unicode/unique canonical projection passed.

Archive inspection found the P4 runtime modules in both artifacts, raw evidence in the sdist,
and no JAR/class, oracle, native binary, or Java-facing runtime dependency. `twine check` was not
available in the local environments and remains a P5 release-tool gate; no claim is made for it
here.

Canonical bytes are invariant across buffer/fan-in/sink batch sizes and Python/native selection.
The implementation has no worker scheduler; therefore there is no schedule-dependent traversal
or merge path in protocol version 1.

## Measured evidence

Raw evidence is committed under [`evidence/`](evidence/). The measurements are one-process,
load-excluded samples on a 12-logical-CPU x86_64 macOS host with CPython 3.12.3. `ru_maxrss` is a
process high-water mark, so the recorded delta is explicitly an upper bound, not a sampled
instantaneous allocation claim.

| Case | Axioms | Edges | Buffer | Time | Peak live spill | RSS delta upper bound |
|---|---:|---:|---:|---:|---:|---:|
| lazy named-subclass chain | 1,000,000 | 1,000,000 | 100,000 | 85.019 s | 73,000,630 B | 65,261,568 B |
| OAEI Bio-ML DOID target | 55,687 | 9,388 | 100,000 | 0.961 s | 1,037,503 B | 0 B above snapshot peak |
| OAEI Bio-ML NCIT source | 243,099 | 42,103 | 100,000 | 6.776 s | 6,700,413 B | 0 B above snapshot peak |

The million-axiom process peaked at 94,236,672 bytes, far below the 1.5 GiB gate. It used ten
runs, no intermediate merge pass at fan-in 32, and produced digest
`ae4dbdd49287b51150b98c490559fd6e1a4886b8a246dd4f6249f026463e049d`.
Unlike the P3 surrogate, neither its million axioms nor its million output edges were held in a
Python list. Canonical time to first edge was 88.659 s, as expected for a global sort.

The pinned DOID file is 6,687,536 bytes with SHA-256
`76f41cce3616ad1a9ba6353f469e96bde7addba5d43e541651a3ab703f9ba2bc`.
Its edge artifact was 1,348,756 bytes and took 1.020 s to write. Repeated P3 and P4 counts remain
9,388; P4's JSONL edge digest is
`0440e1c8a27f67692350837bca320d31a6e17ad9142aaad7bca502b903089bf5`.

The previously blocked 57,163,710-byte NCIT source now loads with pyOWLCore
`fca6a5f346711843eb7f4f830bf88b4154cabd92`, whose RDF mapper accepts explicit list structural
markers without weakening the ambiguous-list checks. The exact source SHA-256 remains
`379a37f47c0c8e7c30397769358cca955140d16b2797a1cc75da4b1fc2b354eb`. It parsed to 243,099
axioms with document fingerprint
`258175c5bc8c1ecc55509265d35e97f27050f64d6dae4df7a14b55f0db629b18`, emitted 42,103 edges,
and produced canonical edge-record digest
`b0c1186bd4004bc2a288593c1b5783d568e44f58723fedd7ba6d9c1eb6a20914`. The load took
275.510 s and established the 1,662,648,320-byte process high-water mark before projection;
projection added no observable high-water RSS and used one 6,700,413-byte spill run. This is a
single local checkpoint sample, not a robust release median or a replacement for the separately
gated hosted/reference-machine run.

## Corpus availability and non-results

No performance number is invented for an input that P4 cannot project:

- No pinned GO file is present in the workspace. The opt-in corpus job must supply and hash-pin a
  redistributable GO release before claiming that result.
- No licensed SNOMED-scale ontology is present, and this repository must not acquire or publish
  one without an operator's license. The same benchmark accepts a caller path and records only
  its base name, byte hash, document fingerprint, counters, and measurements.

The two unavailable real-corpus entries remain release-candidate evidence gates, not waived
correctness or memory failures. The NCIT parser blocker is closed by the cited core change; the
implementation, failure behavior, and reproducible harness are complete, while unavailable or
licence-gated bytes remain external inputs.

## Reproduction

```bash
PYTHONPATH=../pyOWLCore/src:src python benchmarks/benchmark_streaming.py \
  --synthetic-axioms 1000000 --corpus-id synthetic-million-subclass \
  --buffer-edges 100000 --merge-fan-in 32 --max-open-files 64 \
  --warmups 0 --repetitions 1
```

For corpus measurements, replace `--synthetic-axioms ...` with `--ontology PATH`, set a stable
`--corpus-id`, select the parser `--format`, and use `--measure-artifact` when artifact throughput
is required. Release baselines use robust medians rather than the single checkpoint samples.
