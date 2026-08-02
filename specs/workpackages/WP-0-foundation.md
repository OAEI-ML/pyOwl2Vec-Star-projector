# WP-P0 — Foundation and contract freeze

**Target:** `0.1.0a1`. **Depends on:** none. **Status:** implemented (2026-07-17).

Evidence is recorded in [`../../reports/p0/handoff.md`](../../reports/p0/handoff.md), with name
and dependency audits in the same directory. The authenticated PyPI reservation remains a P5
release gate; P0 performs no publishing action.

## Deliverables

- Create the PEP 621 package for Python `>=3.10` with import name
  `pyowl2vec_star_projector` and `pyowl-core>=0.2,<0.3`.
- Implement only public value objects, option validation, version constants, typed errors,
  provenance schemas, and backend selection seams. No projection rules land here.
- Pin the compatibility baseline and check source/blob/toolchain digests.
- Record mOWL's BSD-3-Clause license/copyright, define the behavioral-rewrite boundary, and add
  third-party notice/SBOM checks for oracle and release artifacts.
- Perform a release-blocking ownership/reservation check for the provisional PyPI distribution
  `pyowl2vec-star-projector`; rename before public release if control cannot be established.
- Audit the existing `owl2vec-star==0.2.0` project for API/name collision, dependency and Python
  range, license, and source provenance. Record results; copy no code.
- Establish ruff/type/test/license/SBOM CI on Python 3.10 and the newest supported Python.
- Add import-boundary tests proving the package does not depend on Exact-OM, OAEI, reasoners, or
  Java-facing modules.

## Acceptance

The skeleton installs from an offline wheel with Java and Rust absent. Contract snapshots match
`../contracts.md`; invalid booleans/enums/profiles fail predictably. `pip`/index name evidence is
attached to the release checklist. Any contract deviation updates the normative docs before code
review.

No engine, placeholder fake edges, or second ontology records are permitted.
