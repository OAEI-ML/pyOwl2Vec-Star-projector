# Specification index

These documents define the initial `pyowl2vec-star-projector` `0.1.x` line. The key words
**MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative in the RFC 2119 sense.

## Normative documents

| Document | Purpose |
|---|---|
| [`SPEC.md`](SPEC.md) | Scope, architecture, semantic profiles, and public behavior |
| [`contracts.md`](contracts.md) | Python API, snapshot handoff, artifacts, errors, and provenance |
| [`reference-behavior.md`](reference-behavior.md) | Pinned Scala rules and compatibility defects |
| [`verification.md`](verification.md) | Java oracle, goldens, differential tests, fixtures, and gates |
| [`performance-packaging.md`](performance-packaging.md) | Native/fallback policy, streaming, benchmarks, wheels, and sdist |
| [`baseline.toml`](baseline.toml) | Machine-readable upstream pin and toolchain facts |

The work-package directory breaks implementation into reviewable, versioned units. It does not
weaken the contracts above.

## Precedence and change control

1. `SPEC.md` and `contracts.md` define the package contract.
2. `reference-behavior.md` defines `mowl-d993536-v1` compatibility.
3. `verification.md` decides whether an implementation conforms.
4. Work-package prose is planning material when it conflicts with a normative document.

Every incompatible semantic change requires a new named projection profile. Every incompatible
Python API change requires a package major version. Edge-list artifact schemas, cache compiler
schemas, and `pyowl-core` wire versions evolve independently and are recorded in provenance.

## Dependency direction

```text
pyowl-core <--- pyowl2vec-star-projector
    ^                   ^
    |                   |
 reasoners           Exact-OM
    ^
    |
OAEI-Bio-ML-eval
```

The projector MUST depend on `pyowl-core>=0.1,<0.2` and MUST NOT depend on Exact-OM, OAEI,
pyELK, or pyHermiT. This keeps every package independently installable and prevents a cycle.

## Work packages

| WP | Deliverable | Depends on |
|---|---|---|
| [P0](workpackages/WP-0-foundation.md) | package skeleton and frozen contracts | — |
| [P1](workpackages/WP-1-oracle.md) | pinned Scala oracle and goldens | P0 |
| [P2](workpackages/WP-2-python-compiler.md) | complete Python compiler/backend | P0, P1 |
| [P3](workpackages/WP-3-native-backend.md) | Rust/PyO3 backend with parity | P2 |
| [P4](workpackages/WP-4-streaming.md) | bounded-memory deterministic output | P2 |
| [P5](workpackages/WP-5-packaging-release.md) | wheels, sdist, CI, and release | P2–P4 |
| [P6](workpackages/WP-6-integrations.md) | consumer conformance for Exact-OM | P5 |
