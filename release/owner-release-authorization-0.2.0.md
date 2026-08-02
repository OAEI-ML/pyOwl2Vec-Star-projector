# Owner release authorization for 0.2.0

Date: 2026-08-02

The repository owner explicitly directed that all remaining release gates be
closed and that the complete current-main 0.2.0 release be published after the
coordinated pyOWLCore 0.2.0 release.

The exact tested core source is commit
`d39fe9c9bb9513db8c14fe2bc6d4864377901ad1`, tree
`d29bbcc65684c5a246b5d952a91d8a62e07e1b35`.

This authorization accepts the recorded residual risks; it is not a claim that
a hosted workflow has already run. `external-gates.json` records each closure
and residual risk. Publication remains fail-closed on one atomic
seven-distribution set: the compiler-free universal wheel, source distribution,
and five supported native wheels for manylinux x86_64/aarch64, macOS
x86_64/arm64, and Windows AMD64.

The environment-protected `release.yml` workflow must build, install-test,
audit, hash, gate, and attest that complete set before its single PyPI
trusted-publishing job. No credential or token is stored in this repository.
