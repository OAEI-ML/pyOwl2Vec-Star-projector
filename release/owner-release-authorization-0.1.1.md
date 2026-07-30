# Owner release authorization for 0.1.1

Date: 2026-07-30

The repository owner explicitly directed that all remaining external gates be closed and that
the complete current-main 0.1.1 patch release be published after the coordinated pyOWLCore 0.1.1
release.

The exact tested core source is commit `989a95e38cc74e659282c37ed55ba787ff13f12c`,
tree `28dff7644baeff03ea72472c13b6c7b321b4873e`.

This authorization is a risk acceptance, not a claim that a hosted workflow has already run.
`external-gates.json` records each closure and residual risk. Publication is fail-closed on one
atomic seven-distribution set: the compiler-free universal wheel, source distribution, and five
supported native wheels for manylinux x86_64/aarch64, macOS x86_64/arm64, and Windows AMD64.

The environment-protected `release.yml` workflow must build, install-test, audit, hash, gate, and
attest that complete set before its single PyPI trusted-publishing job. No credential or token is
stored in this repository.
