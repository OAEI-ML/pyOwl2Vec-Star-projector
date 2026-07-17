# WP-P4 — Deterministic bounded-memory streaming

**Target:** `0.1.0b1`. **Depends on:** P2; native integration follows P3. **Status:** implemented.

## Deliverables

- Implement backpressured encounter-order iteration and the version-1 batch-sink protocol.
- Implement checksummed external run sort and k-way merge for canonical ordering, with preserve
  and unique duplicate policies.
- Enforce buffer, temporary-space, open-file/fan-in, and optional total-edge/spill limits.
- Clean up on success, generator close, cancellation, `KeyboardInterrupt`, disk-full, corrupt
  run, backend exception, and controlled process shutdown.
- Add portable JSONL artifact streaming and canonical digest computation without re-reading the
  ontology or materializing all edges.
- Benchmark synthetic million-axiom, GO, NCIT, and licensed large-ontology cases.

## Acceptance

Changing buffer size, fan-in, sink batch size, backend, or worker scheduling produces identical
canonical bytes. Encounter mode reaches its first edge without traversing the full ontology.
Peak incremental RSS and spill cleanup meet `../performance-packaging.md`; tests verify temp
files have private permissions and contain no source-path data.
