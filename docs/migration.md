# Migrating to `0.1.0rc1`

## From `0.1.0b1`

No application-code migration is required. Public imports, option defaults, edge semantics,
warning/error types, profile name, batch-sink protocol, and portable artifact bytes are
unchanged.

Packaging automation should make these deliberate updates:

1. Build the universal wheel or sdist normally, or set `PYOWL2VEC_BUILD_NATIVE=0` explicitly.
   Do not install Rust, Cargo, or `setuptools-rust` for this path.
2. For an intentional native artifact, set `PYOWL2VEC_BUILD_NATIVE=1`. The conditional PEP 517
   backend then requests pinned `setuptools-rust==1.13.0`; Cargo and rustc must already exist.
3. Keep `backend="python"` for quiet deterministic fallback. `backend="auto"` still warns once
   because native has not earned automatic preference.
4. Treat `0.1.0rc1` as an unpublished candidate until every entry in
   `release/external-gates.json` has authenticated evidence.

The distribution name is `pyowl2vec-star-projector`; the import remains
`pyowl2vec_star_projector`. It is intentionally different from the unrelated
`owl2vec-star` distribution.

## From an in-application projector

Pass the existing shared `pyowl_core.OntologyView` to `Projector.project`, `iter_edges`, or a
batch sink. Do not pass an original ontology path when a view exists: doing so would delegate a
new load to core. Cross-process callers use the versioned core wire format, never pickle or a
temporary OWL source file.

The projector-side WP-P6 kit is available through `verify_consumer_conformance`,
`SnapshotProviderProbe`, and the packaged cases returned by `consumer_conformance_cases()`.
Run it before deleting an in-application projector. The probe fails if the integration accesses
an original path/stream/origin, calls `owl_snapshot()` more than once, loses view identity,
mutates fingerprints/counts, changes a registered lazy-view identity, or differs from the frozen
edge/provenance contract.

For Exact-OM, use `duplicates="unique"`, canonical order, isolated state, the
`mowl-d993536-v1` profile, and the dedicated taxonomy API. The projector repository's comparator
matches both committed Exact 2.0 WP-B mini-ontology captures exactly. Exact's WP-M M0–M4
implementation now owns one shared core snapshot, delegates to this projector, uses shared
structural views, and exposes asserted/optional reasoner adapters. Exact's M5 scale/parity,
dependency-release, hosted-matrix, cleanup, documentation, and version gates remain consumer-
owned; this package neither imports nor modifies Exact and introduces no reverse dependency.
