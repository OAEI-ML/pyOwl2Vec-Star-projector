# WP-P1 — Scala oracle and golden corpus

**Target:** `0.1.0a1`. **Depends on:** P0. **Status:** implemented.

## Deliverables

- Build the quarantined, digest-pinned Java 11/Scala 2.11.12/OWLAPI 4.5.22 oracle around the
  exact source blob in `../baseline.toml`.
- Add the isolated fixtures in `../verification.md`, including import closures, every supported
  restriction, n-ary equivalence ordering, RBox collisions, the source's apparent duplicate
  domain/range collection sites and their effective OWLAPI classification,
  ABox, datatype conversion, ignored constructs, and mutable-instance leakage.
- Generate raw ordered lists, edge counters, metadata, and canonicalized derivative goldens for
  all eight historical boolean combinations.
- Document a one-command manual regeneration workflow, container/SBOM hashes, and two-reviewer
  golden-change policy.
- Confirm every claim in `../reference-behavior.md` against source plus a minimal fixture; update
  the catalogue rather than relying on documentation memory.

## Isolation

Oracle tooling lives outside the Python package and is omitted from wheel/sdist manifests.
Ordinary CI consumes committed data only and succeeds on a host with no JVM. Network access is
limited to an explicit build/regeneration job and all fetched inputs are hash-verified.

## Acceptance

Two consecutive oracle runs in the pinned environment produce identical edge counters and
metadata digests. Incidental raw list-order differences, if observed across JVMs, are documented
and never promoted to canonical ordering. Every normative behavior has a fixture ID.

## Evidence

- `tools/java-oracle/` contains the staged-source verifier, Maven lock, transport runner,
  regeneration command, and two-reviewer policy.
- `tests/fixtures/oracle/` contains the CC0 isolated corpus and rule inventory.
- `tests/goldens/mowl-d993536-v1/` contains the eight-flag matrices plus the ordered mutable-state
  session and consecutive-run report.
- `specs/reference-rules.json` gives every normative observation a fixture-backed rule ID.
- ordinary unit tests validate all committed hashes and matrices without Java or network access.
