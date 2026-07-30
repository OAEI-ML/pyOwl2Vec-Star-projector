# Owner release authorization for 0.1.1

Date: 2026-07-30

The repository owner explicitly directed that all remaining external gates be closed and that
the complete current-main 0.1.1 patch release be published after the coordinated pyOWLCore 0.1.1
release.

The exact tested core source is commit `0aab7b137b5a6eef173b8ec000aa84ff8d41e196`,
tree `ca01ade1c99f804b7be550ac245a94fbf7411149`.

This authorization is a risk acceptance, not a claim that a hosted workflow has already run.
`external-gates.json` records each closure and residual risk. Publication is fail-closed on one
atomic seven-distribution set: the compiler-free universal wheel, source distribution, and five
supported native wheels for manylinux x86_64/aarch64, macOS x86_64/arm64, and Windows AMD64.

The environment-protected `release.yml` workflow must build, install-test, audit, hash, gate, and
attest that complete set before its single PyPI trusted-publishing job. No credential or token is
stored in this repository.
