# Owner release authorization for 0.2.1

Date: 2026-09-20

The repository owner requested that the native package work be committed, pushed,
built, verified in CI, and released to PyPI. This authorizes the coordinated
0.2.1 publication under the existing release and licensing policy. It does not
assert that CI has already passed or add a new waiver of technical checks.

The exact tested pyOWLCore source is commit
`6efc2db1ff19679892f36a4d1b50876f7f72440a`, tree
`ade9ed1f078ba14caf24739b31c854b6f5a067b0`, recorded in
`core-compatibility.json`. Publication follows successful pyOWLCore 0.2.1
publication and index verification.

The prior release's documented exclusions and residual limitations remain
historical evidence in `owner-release-authorization-0.2.0.md` and
`external-gates.json`. The opt-in strict path admits retained native snapshots;
it does not claim arbitrary-owner support or a general large-corpus speedup.

The environment-protected `release.yml` workflow must build, install-test,
audit, hash, gate, and attest the complete seven-distribution set before its
single PyPI trusted-publishing job. No credential or token is stored here.
