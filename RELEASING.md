# Release procedure

The procedure separates reproducible local evidence from authenticated or hosted evidence. For
0.1.1, the repository owner authorized the complete seven-distribution patch release through an
environment-protected PyPI trusted publisher. The exact closures are reviewable in
`release/external-gates.json` and `release/owner-release-authorization-0.1.1.md`; they are not
claims that a hosted run has already succeeded.

## 1. Prepare and inspect

- Use a clean, signed release commit and CPython 3.12 for deterministic tooling.
- Install the exact compiler-free artifact environment from
  `release/fallback-build-requirements.txt`.
- Publish and verify `pyowl-core==0.1.1` before uploading this distribution.
- Confirm the selected core release contains the source baseline in
  `release/core-compatibility.json`. Source-checkout CI must use that exact commit; the package
  metadata remains a normal `>=0.1,<0.2` dependency and must never become a Git runtime
  dependency.
- Run `python tools/generate_supply_chain.py --check` and review both CycloneDX SBOMs and the
  machine-readable license inventory.
- Run the full Python 3.10–3.13, Rust, golden, scale, and corpus jobs. Ordinary jobs remain
  Java-free; only the separately quarantined oracle regeneration may contain Java.

## 2. Reproducible fallback artifacts

Use the commit timestamp for both independent builds. The explicit zero makes it impossible for
a developer shell to request native compilation accidentally.

```bash
export SOURCE_DATE_EPOCH="$(git log -1 --pretty=%ct)"
export PYTHONHASHSEED=0
python -m pip install -r release/fallback-build-requirements.txt
PYOWL2VEC_BUILD_NATIVE=0 python -m build --sdist --wheel --outdir dist-a
PYOWL2VEC_BUILD_NATIVE=0 python -m build --sdist --wheel --outdir dist-b
python tools/compare_artifacts.py dist-a dist-b
python tools/audit_release.py dist-a --report release-audit.json
python tools/hash_artifacts.py dist-a --output dist-a/SHA256SUMS
python tools/hash_artifacts.py dist-a --verify dist-a/SHA256SUMS
```

Run the second build in a clean checkout/container for release evidence. `compare_artifacts.py`
requires byte-identical archives and reports differing member hashes if they diverge.

The sdist smoke must use a clean container/VM with no Cargo, rustc, Java, ROBOT, mOWL, JPype, or
DeepOnto. Prepare a local wheelhouse containing its PEP 517 build requirements and the approved
`pyowl-core` wheel, then disable both the package index and container network:

```bash
PIP_NO_INDEX=1 python -m pip install --find-links=/wheelhouse /dist/*.tar.gz
PATH=/venv/bin python /source/tools/installed_smoke.py --require-tools-absent
```

The release workflow performs this with `docker --network none`. It tests the universal wheel
and sdist independently and uninstalls each.

## 3. Native artifacts

Native wheel builders set `PYOWL2VEC_BUILD_NATIVE=1`; the in-tree PEP 517 adapter then supplies
`setuptools-rust==1.13.0` as an isolated build requirement. Cargo uses `native/Cargo.lock` with
`--locked`. Build `cp310-abi3` wheels through cibuildwheel, then install each on every supported
CPython version for its platform.

`release/native-build-requirements.txt` pins the equivalent non-isolated maintainer environment;
it is never installed for a fallback build.

Run Python/native ordered differential and artifact parity, then the platform audit tool:

- Linux: `auditwheel show` on x86_64 and aarch64 manylinux wheels;
- macOS: `delocate-listdeps` on x86_64 and arm64 wheels;
- Windows: `delvewheel show` on x86_64 wheels.

Also prove the interpreter policy before accepting an abi3 artifact: in a CPython
subinterpreter, `backend="auto"` must select the complete Python backend without attempting to
import `_native`, while explicit `backend="native"` must raise
`NativeBackendUnavailableError`. Run a complete Python projection only on a host whose own
standard-library extension modules support subinterpreter teardown; record that clean installed
smoke as external evidence. Repeat the policy test on a free-threaded build, where native remains
unclaimed.

Do not claim musllinux, PyPy, free-threaded CPython, or another target until it has its own tests
and compatibility entry.

## 4. Optional artifact-selection proof

Upload the sdist, universal wheel, and native wheels with the same candidate version to a
disposable authenticated private index. In clean supported environments, record that pip chooses
the compatible native wheel; in an unsupported environment, record that it chooses the universal
wheel and never starts a source build. Repeat with Cargo absent. If the index cannot guarantee
this selection, publish a separately named accelerator distribution while retaining the same
projector import, API, and warning contract.

Local `--find-links` selection is useful diagnostics. The owner retained the private-index
selection waiver, but every native wheel must pass its own hosted platform build, smoke, and
binary audit before the single atomic upload.

## 5. Supply-chain and final gate

Run current `pip-audit` and `cargo audit` databases, inspect native symbols, attach both SBOMs,
license inventory, corpus reports, `SHA256SUMS`, binary-audit output, and hosted matrix URLs.
GitHub's `release.yml` workflow creates OIDC build-provenance attestations for the complete
distribution set in an authenticated repository context. It stages `SHA256SUMS`, release-audit,
and release-gate evidence separately from the distributions sent to PyPI.

```bash
python tools/release_gate.py --artifacts dist-a --audit-report release-audit.json \
  --include-external --report release-gate.json
```

Exit status `2` means one or more external records are still unresolved; 0.1.1 records explicit
owner-authorized closures and therefore requires exit status `0`. Configure the PyPI trusted
publisher for the exact repository, `release.yml`, and `pypi` environment. Upload
`pyowl-core==0.1.1` first, push annotated tag `v0.1.1`, then manually dispatch the atomic workflow
from that tag with `publish=true`. Approve the protected environment only after all seven
artifacts and their attestations pass. No PyPI API token is used by the workflow.
