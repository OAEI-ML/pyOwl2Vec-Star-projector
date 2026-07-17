# WP-P2 pure-Python compiler verification report

Verified on 2026-07-17 for `pyowl2vec-star-projector` `0.1.0a2`.

## Delivered behavior

- Complete `mowl-d993536-v1` pure-Python compiler over public immutable `pyowl-core` structural
  objects, with no parser, resolver, private OWL model, or core mutation.
- Exact named/restriction/equivalence, RBox overwrite/collision, inverse, domain/range cross-
  product, ABox, selected annotation, duplicate-bag, flag-defect, and Scala-instance lifecycle
  behavior pinned by P1.
- Deterministic UTF-8 canonical output, deterministic encounter output, stable-first unique mode,
  materialized lists, iterators, and bounded batch callbacks.
- Separate asserted named-class taxonomy API.
- Typed inverse-property assertion failure corresponding to the pinned Java `ClassCastException`;
  ignored shapes are grouped diagnostics, and malformed non-string literal rendering is recorded
  without stdout.
- Serializable counts, core/projector fingerprints and versions, diagnostic digest, invocation
  number, and call-history digest.

## Shared-core boundary

The strict API validates adapter protocol 1/model schema 1, keeps `last_view is input_view`, and
indexes references to the original axioms. It creates no local OWL records. Root-only annotation
lookup and closure-wide logical/signature traversal are separate, preserving the corrected P1
import behavior.

The standalone facade imports `pyowl_core.coerce_snapshot` only at an actual call. Conformance
tests activate that forthcoming WP03 API and prove exactly one call, provider-returned identity,
decoded-wire-view identity, and unchanged propagation of core loader/import failures. Until the
core facade lands, strict existing-view projection is fully operational and standalone calls fail
with an actionable `SnapshotCompatibilityError`; no private parser is used as a fallback.

## Oracle parity

- 17 fresh fixtures and 3 ordered lifecycle sessions remain the source of truth.
- All 8 flag combinations are checked for each entry: 160 cases and 184 invocations.
- Every successful invocation matches the committed canonical edge objects and SHA-256 bytes.
- The inverse-property assertion fixture raises the required typed projector error with the pinned
  reference outcome recorded in details.
- The strict missing-import outcome is verified at the core loader boundary, before compilation.
- Corrected facts are covered explicitly: one effective domain/range pass with annotated
  multiplicity, root-only imported annotation lookup, anonymous assertion output, and malformed
  datatype rendering.

## Verification gates

- Python 3.10: 66 tests and 6 `unittest` subtests passed.
- Python 3.12: 66 tests and 6 `unittest` subtests passed.
- Ruff format/lint: passed.
- Strict mypy for runtime and P2 conformance tests: passed.
- Runtime dependency and pinned-baseline audits: passed.
- PEP 517 built a universal wheel and sdist. Both installed/imported under Python 3.10 and 3.12;
  archive inspection confirmed that Java/Scala oracle code, JAR/class artifacts, fixtures,
  goldens, and fixture-bound P2 tests are absent.

Setuptools emitted the already-recorded license table/classifier deprecation warning. Packaging
metadata modernization remains P5 scope and did not affect artifact validity.

P2 intentionally contains no Rust/native backend and no external merge sorter. Those remain P3
and P4, respectively, and cannot fill a Python semantic gap.
