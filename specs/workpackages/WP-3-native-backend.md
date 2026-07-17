# WP-P3 — Rust/PyO3 backend

**Target:** `0.1.0b1`. **Depends on:** P2. **Status:** planned.

## Deliverables

- Profile Python and real ontologies first; record hotspots and justify each Rust boundary.
- Implement an optimized Rust compiler/edge emitter without defining a Rust ontology model.
  Borrow reviewed core views or consume bounded batches.
- Match Python ordered edges, multiplicities, diagnostics, exception classification, and
  artifacts exactly for fixtures, generated cases, wire snapshots, and real corpora.
- Specify and test lifetime, GIL/thread, iterator cancellation, panic conversion, Unicode,
  allocation-failure, and interpreter-shutdown safety.
- Implement `auto`/`native`/`python` selection and the once-per-process fallback warning.
- Add debug/sanitizer jobs and binary dependency/license audits.

## Acceptance

No native-only feature exists. Differential fuzzing finds zero semantic mismatches. Native code
never mutates or retains a borrowed core view beyond its valid lifetime. It satisfies memory
gates and is preferred by `auto` only after the throughput criteria in
`../performance-packaging.md`; otherwise it remains opt-in without blocking the Python release.
