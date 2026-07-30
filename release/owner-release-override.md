# Owner release override for 0.1.0

Date: 2026-07-30

The repository owner explicitly directed that all remaining external gates be closed, the
projector be promoted from `0.1.0rc1` to production `0.1.0`, and the release be uploaded locally
with their authenticated PyPI account after `pyowl-core` 0.1.0.

This authorization is a risk acceptance, not a claim that the previously absent hosted evidence
was generated. `external-gates.json` records each closure, the accepted evidence, and the residual
risk. In particular, the first production upload contains the complete compiler-free universal
wheel and sdist. Platform-specific native wheels may be added only after their own build and
binary-audit workflows complete.

No credential or token is stored in this repository.
