# WP-P6 — Consumer migration and conformance

**Target:** projector `0.1.x`, Exact-OM `2.1.0`. **Depends on:** P5. **Status:** projector-side
kit implemented; Exact WP-M M0–M4 landed; cross-repository M5/release acceptance remains open.

## Deliverables

- Publish a consumer conformance kit containing snapshot/provider identity probes, deterministic
  fixture goldens, provenance assertions, and no-reparse instrumentation.
- Migrate Exact-OM according to its `WP-M-shared-owl-stack.md`: remove its local OWL2Vec*
  compiler, retain existing public projection behavior through explicit options, and feed the
  exact shared snapshot instance.
- Compare Exact's committed mOWL/2.0 baselines with `duplicates="unique"`, dedicated taxonomy,
  and the chosen OWL2Vec* profile. Explain every difference before changing a baseline.
- Test Exact with pure Python only, native acceleration, reasoners absent, and optional native
  reasoners present.
- Add dependency-DAG CI proving projector imports neither Exact nor OAEI and OAEI does not acquire
  a projector dependency through coherence.

## Acceptance

One ontology load supplies Exact, projector, and optional reasoners without conversion or path
reparse. Exact's default outputs remain within its WP-M preservation gates. Both packages remain
independently usable and uninstallable. Consumer-specific behavior does not enter the shared
core model or the pinned projector profile.

## Repository boundary

This repository publishes the conformance resources, probes, Exact baseline comparator,
dependency-DAG CI, and handoff benchmark. It does not import or patch Exact. Deleting Exact's
local compiler rules and wiring its `OwlOntologySource`/reasoners landed in Exact's M0–M4
implementation under `WP-M-shared-owl-stack.md`. Cross-repository acceptance remains open until
Exact completes M5's scale/parity, dependency-release, hosted-matrix, cleanup, documentation, and
version gates; this projector repository cannot waive or manufacture that consumer evidence.
