# WP-P0 handoff

WP-P0 freezes the 0.1 public version constants, immutable `Edge`, strict
`ProjectionOptions`, typed failures/warning, provenance records, and backend
selection seam. It deliberately implements no edge-producing algorithm.

Acceptance evidence:

- Python 3.10 and 3.12 unit suites exercise values, validation, backend selection,
  warning timing, provenance, and forbidden dependency detection. Baseline pin/blob
  checks run on 3.12 locally and on 3.10 CI through the declared `tomli` dependency.
- Baseline static validation binds the profile, commit, Git blob, source path,
  license, bag semantics, and canonical order.
- Runtime audit parses Python imports and rejects forbidden artifacts/dependencies.
- A compiler-free Python 3.10 wheel built with PEP 517, installed into a clean venv
  with dependencies intentionally suppressed, and passed the import/options smoke.
  The sdist built successfully and its archive contained no Java bytecode/archive.

Remaining before P1: acquire the pinned oracle source/toolchain into an ignored,
hash-verified developer cache. Remaining before public release: authenticated
package-name control, full license/SBOM review, complete Python compiler, native
parity, streaming, consumer conformance, and all P5 gates.
