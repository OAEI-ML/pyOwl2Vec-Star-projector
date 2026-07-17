# Changelog

All notable changes use Semantic Versioning. Compatibility-profile changes are listed separately
from packaging or performance changes because profile output is a data contract.

## 0.1.0rc1 — 2026-07-17

### Added

- Complete Java-free Python implementation of `mowl-d993536-v1`, with all 184 pinned Scala
  oracle invocations represented by checked-in golden evidence.
- Optional abi3 Rust edge-policy accelerator with exact Python differential tests. It remains
  explicit opt-in because the P3 auto-selection throughput threshold was not met.
- Bounded encounter and canonical streaming, checksummed external runs, exact disk-backed
  duplicate accounting, synchronous batch sinks, portable JSONL artifacts, and canonical hashes.
- Conditional PEP 517 backend: normal wheel/sdist builds do not request Rust tooling; explicit
  native builds request `setuptools-rust` in their isolated build environment.
- CPython 3.10–3.13 and platform packaging workflow definitions, compiler-absent/network-disabled
  fallback smokes, deterministic SBOM/license generation, artifact audits, hashes, and
  reproducibility comparison tooling.
- API/profile/artifact/cache compatibility table and beta-to-RC migration notes.

### Changed

- Version advanced from `0.1.0b1` to the unpublished `0.1.0rc1` release candidate.
- Metadata now claims CPython only; the unexecuted PyPy classifier was removed.

### Compatibility

- No projector rule, output ordering, multiplicity, public API, artifact schema, sink protocol,
  compiler-cache schema, or reference-profile change from `0.1.0b1`.

### Release blockers

- Final `0.1.0` publication remains blocked by authenticated distribution-name ownership,
  private-index artifact selection, the hosted platform matrix, signed provenance, current
  vulnerability feeds, and unavailable release corpora. See `release/external-gates.json`.

## 0.1.0b1 — 2026-07-17

- Implemented the pure compiler, optional native edge engine, and bounded P4 streaming/artifact
  contracts. This prerelease was not published from this workspace.
