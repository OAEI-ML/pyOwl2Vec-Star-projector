# Owner release authorization for 0.2.0

Date: 2026-08-02

The repository owner explicitly directed that all remaining release gates be
closed and that the complete current-main 0.2.0 release be published after the
coordinated pyOWLCore 0.2.0 release.

The exact tested core source is commit
`85415249251b84474735a3581b94bb1d881bb902`, tree
`724396db74844daa7a3453ea28449b076bc7aacf`.

This authorization accepts the recorded residual risks; it is not a claim that
a hosted workflow has already run. `external-gates.json` records each closure
and residual risk. Publication remains fail-closed on one atomic
seven-distribution set: the compiler-free universal wheel, source distribution,
and five supported native wheels for manylinux x86_64/aarch64, macOS
x86_64/arm64, and Windows AMD64.

The environment-protected `release.yml` workflow must build, install-test,
audit, hash, gate, and attest that complete set before its single PyPI
trusted-publishing job. No credential or token is stored in this repository.
