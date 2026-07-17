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

Exact-OM integration and removal of its migrated projector implementation are WP-P6 work. This
candidate does not modify Exact-OM or introduce a reverse dependency.
