# WP-P2 — Complete Python compiler and backend

**Target:** `0.1.0a2`. **Depends on:** P0, P1. **Status:** implemented.

## Deliverables

- Implement the view-first API against `pyowl_core.OntologyView` and the standalone
  `coerce_snapshot` boundary; prove concrete snapshot identity and overlay identity/no-
  materialization independently.
- Implement every `mowl-d993536-v1` rule and compiler-local quirk, bag multiplicities,
  deterministic visit/canonical order, unique mode, both lifecycle modes, and the separate
  asserted-taxonomy API.
- Reuse snapshot strings, IDs, signatures, closure, and lazy views; create no local OWL records
  and never mutate core state.
- Implement materialized, encounter iterator, and batch-sink APIs; canonical external sorting
  may initially land behind P4 but results must already be deterministic for fixture scale.
- Implement full diagnostics/provenance, typed errors, and skipped-shape counters without stdout
  output.
- Add golden, property, wire round-trip, identity/no-reparse, thread, cancellation, and malformed
  input tests.

## Acceptance

The pure-Python backend passes every edge-counter golden and canonical-byte test under Python
3.10 and the newest supported version. Instrumentation proves an existing snapshot/provider is
coerced once and read by identity. Java-free dependency audits pass. The complete public feature
set works with `backend="python"`; no later native WP may fill a Python semantic gap.

## Completion notes

- Canonical ordering is deterministic in memory at fixture scale; P4 exclusively owns bounded
  external merge sorting. Encounter/preserve iteration streams after a single reference-index
  scan and batch sinks bound delivery buffers.
- The compiler consumes core structural values directly and retains their identities. Its only
  local state is projection state/indexes and output `Edge` values; it defines no OWL records.
- Core WP03 activation is delayed at the facade import: strict `Projector` view APIs are complete,
  while `project_source` discovers `pyowl_core.coerce_snapshot` at the actual call boundary. Tests
  pin one-call provider, overlay/decoded-view identity, and typed loader-error propagation.
- The compatibility-only OWLAPI blank-node output label is derived in a private projection map;
  the immutable core anonymous individuals remain the identity keys and are never mutated.
- See [`../../reports/p2/python-compiler.md`](../../reports/p2/python-compiler.md) for gates and
  parity evidence.
