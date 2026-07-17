# WP-P5 — Packaging, compatibility matrix, and 0.1 release

**Target:** `0.1.0`. **Depends on:** P2–P4. **Status:** implemented as `0.1.0rc1`;
final release externally blocked.

## Deliverables

- Prove the chosen native-plus-universal artifact strategy on a private index. If one
  distribution cannot reliably provide both, introduce a separately named accelerator while
  retaining one import/API and warning contract.
- Build/test the CPython and platform wheel matrix in `../performance-packaging.md`.
- Ensure sdist's default build installs a working Python backend with Cargo/Rust absent.
- Run clean-environment smoke tests with Java/ROBOT/mOWL/JPype absent and network disabled.
- Publish API/profile/artifact/cache compatibility tables, changelog, migration notes,
  reproducible-build instructions, signed hashes/attestations where supported, SBOM, and license
  inventory.
- Run release corpora, determinism matrix, dependency audit, and package-name ownership check.

## Acceptance

All gates in `../verification.md` pass. A user on a supported platform always gets a functional
pure-Python projector; missing native acceleration never becomes an install failure. Project
metadata claims only tested interpreters/platforms. The uploaded distribution name is owned and
cannot be confused with the unrelated `owl2vec-star` package.

## Implementation state

The compiler-free sdist/universal-wheel path, conditional isolated native build, CI matrices,
offline smokes, artifact/hash/reproducibility tooling, SBOMs, license inventory, compatibility
tables, changelog, and migration/release instructions are implemented. Local same-version
`--find-links` selection chooses the native wheel on a supported target and the universal wheel
for a simulated unsupported target.

This work package does **not** claim the final acceptance paragraph yet. Authenticated package-name
ownership, a compatible ordinary-index `pyowl-core` release, a disposable private-index proof,
hosted platform runs, signed attestations, current advisory feeds, and unavailable release corpora
are explicit blockers in `../../release/external-gates.json`. The candidate remains `0.1.0rc1` and
no publication workflow or credential is present.
