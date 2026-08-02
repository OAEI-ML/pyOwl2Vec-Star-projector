# Release evidence

This directory stores deterministic source-controlled inputs and generated supply-chain records.
It deliberately contains no credential, signature, package-index token, or claim that hosted jobs
ran.

- `sbom/runtime.cdx.json`: runtime CycloneDX inventory (projector and `pyowl-core`).
- `sbom/native-build.cdx.json`: conditional Python build tools and all lockfile-pinned Rust crates.
- `license-inventory.json`: machine-readable licenses, shipped/non-shipped scope, and mOWL
  behavioral-reference provenance.
- `build-provenance.json`: exact deterministic build/release recipe inputs and tool pins.
- `fallback-build-requirements.txt`: exact compiler-free artifact-construction tools.
- `native-build-requirements.txt`: the fallback tools plus native-only Python build helpers.
- `external-gates.json`: explicit owner-authorized closures and retained residual risks for checks
  which could not be proven in this workspace.
- `owner-release-override.md`: human-readable authorization and scope for those closures.
- `core-compatibility.json`: exact pyOWLCore 0.2.0 release commit and public API/model/wire/encoded
  contract for reproducible source-checkout CI, plus the published dependency range and repinned
  fixture fingerprints.
- `owner-release-authorization-0.2.0.md`: version-specific owner authorization for the complete
  atomic release; earlier authorizations remain as historical records.

Regenerate and compare the deterministic records with
`python tools/generate_supply_chain.py --check`. Per-build hashes, signatures, attestations,
platform audit output, and hosted run URLs are release assets rather than timeless source files.
