# WP-P2 — Complete Python compiler and backend

**Target:** `0.1.0a2`. **Depends on:** P0, P1. **Status:** planned.

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
