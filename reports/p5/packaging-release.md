# P5 packaging and release-candidate report

Date: 2026-07-17. Candidate: `0.1.0rc1`. Host: Darwin 25.5.0 x86_64.

## Outcome

The locally provable P5 implementation is complete. The project now builds a compiler-free
universal wheel and default sdist, and an explicitly requested isolated native wheel. Both
fallback artifacts are byte-for-byte reproducible when `SOURCE_DATE_EPOCH` is set. The release
gate reports `local_passed: true` and `release_passed: false`: no package was uploaded, signed, or
presented as final.

The final `0.1.0` is intentionally blocked. Authenticated distribution-name ownership,
private-index artifact selection, hosted CPython/platform wheels, signed OIDC provenance, current
network-backed vulnerability feeds, and unavailable release corpora cannot be established in
this workspace. Their required evidence is machine-readable in `release/external-gates.json`.

## Artifact design

One distribution retains the same import and contains the complete Python implementation in every
wheel:

- default PEP 517 build: `py3-none-any` and no `setuptools-rust`/Cargo probe;
- default sdist install: the same fallback, including when Cargo and Java are hidden;
- explicit `PYOWL2VEC_BUILD_NATIVE=1`: an isolated conditional requirement on
  `setuptools-rust==1.13.0`, locked Cargo compilation, and a `cp310-abi3` platform wheel.

The in-tree PEP 517 adapter removes Rust tooling from fallback build requirements and normalizes
sdist gzip/tar timestamps, ownership, modes, and PAX timing headers under `SOURCE_DATE_EPOCH`.
The normal setup path no longer sends an unregistered `rust_extensions` keyword to setuptools.

Final local fallback artifacts, built twice with setuptools 83.0.0 and wheel 0.46.3:

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `pyowl2vec_star_projector-0.1.0rc1-py3-none-any.whl` | 49,333 | `5227797598f6fbd63164854de435debb369ff70cf6820cacd80050cfac4c984a` |
| `pyowl2vec_star_projector-0.1.0rc1.tar.gz` | 111,316 | `eb9d15345cd4c2678b1afb92b2af31182e8e1c16423adf33c59c790d04513f16` |

`tools/compare_artifacts.py` reported byte-for-byte identity for both independent builds.
`tools/audit_release.py`, the SHA-256 create/verify round trip, and Twine 6.2.0 passed both. The
sdist contains the conditional backend, lockfile, source, release instructions, SBOMs, and notices;
it prunes tests, golden/oracle inputs, the Java oracle, native targets, and this machine-specific
P5 evidence directory.

The isolated native build automatically requested `setuptools-rust==1.13.0` and produced
`pyowl2vec_star_projector-0.1.0rc1-cp310-abi3-macosx_14_0_x86_64.whl` (258,102 bytes,
SHA-256 `0f642c23b4a83f174b28a295d34ff0ed0ecfdd74cdd09c4f141bb3d0db660d34`). Artifact audit and
Twine passed. `otool -L` found only the extension install name, `/usr/lib/libiconv.2.dylib`, and
`/usr/lib/libSystem.B.dylib`; no JVM/JNI library appeared.

## Clean installation and selection evidence

Fresh CPython 3.10.11 and 3.12.3 environments installed the final sdist with `PIP_NO_INDEX=1`, a
local wheelhouse containing only setuptools/wheel/packaging, and a `PATH` containing only the
environment's executables. Cargo, rustc, Java, javac, ROBOT, and forbidden Java-facing Python
modules were therefore unavailable. Both installations imported, projected an empty conforming
shared view through explicit Python, exercised the once-only automatic-fallback warning, preserved
view identity, and passed uninstall-oriented metadata checks. The universal wheel passed the same
smoke. The native wheel imported and executed explicit native projection on both interpreters
without build tools visible.

This proves no-index installation, not kernel-level network isolation: the local Docker daemon was
unavailable. `.github/workflows/packaging.yml` defines the stronger `docker --network none` sdist
and wheel smoke for CPython 3.10–3.13, but no hosted run is claimed.

With the universal and native wheels offered together through local `--find-links`, pip on the
supported macOS host selected the `cp310-abi3` wheel. A simulated CPython 3.12 Windows ARM target,
for which no native artifact existed, selected `py3-none-any` without considering the sdist. This
is diagnostic evidence only; it does not satisfy the authenticated private-index gate.

## Matrix and supply chain

The workflows define:

- pure tests on CPython 3.10–3.13;
- installed smokes on Linux x86_64, macOS x86_64/arm64, and Windows x86_64;
- `cp310-abi3` wheels for Linux x86_64/aarch64, macOS x86_64/arm64, and Windows x86_64;
- installation of every native wheel on CPython 3.10–3.13 with required (non-skippable) native
  parity tests;
- auditwheel/delocate/delvewheel steps; and
- a manual attested candidate build with no package-index upload step.

These are build definitions, not hosted results. Musllinux, PyPy, and free-threaded Python are not
claimed.

`release/sbom/runtime.cdx.json` inventories only the projector and `pyowl-core`. The native-build
CycloneDX record covers exact Python build tools and all 14 non-root Cargo packages, including
lockfile checksums and SPDX expressions. The generator cross-checks the Cargo license table and
writes `release/license-inventory.json`; it records zero Java components and distinguishes mOWL as
a non-shipped BSD-3-Clause behavioral reference. Supply-chain regeneration is deterministic.
The wheel chooses Apache-2.0 for dual-licensed crates, reuses the shipped project license text, and
bundles the required LLVM exception and Unicode-3.0 terms in `native/THIRD_PARTY_LICENSES.md`.

## Validation

- CPython 3.10.11: 114 tests and 6 unittest subtests passed.
- CPython 3.12.3: 114 tests and 6 unittest subtests passed.
- Ruff check and format check passed for source, tests, tools, backend, and setup.
- Strict mypy passed 14 source files against the local `pyowl-core` source.
- `audit_runtime.py`, baseline validation, supply-chain freshness, release artifact audit,
  reproducibility, hashes, Twine, and the local release gate passed.
- Cargo format and Clippy with warnings denied passed; four Rust tests passed.
- The explicit external gate returned status 2, correctly refusing to call the candidate a
  releasable final build.

Exact machine-readable values are in `evidence/local-validation.json`.
