# Owner release authorization for 0.2.0

Date: 2026-08-02

The repository owner explicitly directed that all remaining release gates be
closed and that the complete current-main 0.2.0 release be published after the
coordinated pyOWLCore 0.2.0 release.

The exact tested core source is commit
`7d501d2b16d859d00ee9dcc2933dd78faf173891`, tree
`9728fe62ff0806dff0eaf712810d2dea330bb29d`.

This authorization accepts the recorded residual risks; it is not a claim that
a hosted workflow has already run. `external-gates.json` records each closure
and residual risk. Publication remains fail-closed on one atomic
seven-distribution set: the compiler-free universal wheel, source distribution,
and five supported native wheels for manylinux x86_64/aarch64, macOS
x86_64/arm64, and Windows AMD64.

The environment-protected `release.yml` workflow must build, install-test,
audit, hash, gate, and attest that complete set before its single PyPI
trusted-publishing job. No credential or token is stored in this repository.
