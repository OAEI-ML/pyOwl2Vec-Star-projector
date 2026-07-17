# WP-P1 oracle verification report

Verified on 2026-07-17 against mOWL commit
`d9935369144f9a618ece38b7b2a8f4293afe8c26` and projector Git blob
`a7f7584bbe687ae341cf0547bc0492ada87cf4b8`.

## Corpus and reproducibility

- 17 fresh-instance fixtures and 3 ordered same-instance sessions.
- Every entry runs all 8 combinations of `bidirectional_taxonomy`, `only_taxonomy`, and
  `include_literals`: 160 cases and 184 total invocations.
- 47 source observations have machine-checked fixture mappings.
- Two consecutive Java 11/Scala 2.11.12/OWLAPI 4.5.22 runs matched in edge counters, canonical
  derivatives, and contract metadata.
- Locked environment SHA-256:
  `0cdb6ef27b7356ffa3be896ec44f6648c4794dcc2da63418442b985a3109bb68`.
- Maven runtime dependency-set SHA-256:
  `cb09ad291eb54aa1533207487932dc0eb8a4ce3d4a1ff7172c04ae659a7ddd36`.
- JDK SPDX SBOM SHA-256:
  `425fbc327144e7435ae339931fb7854693dca55f0110d33a531dcc501b3eccd3`.
- OWLAPI raw order differed between the two runs in four taxonomy cases. Their counters and
  canonical derivatives were identical; the exact cases are recorded in
  `regeneration-report.json` and raw order is not promoted to a contract.

## Oracle corrections to the draft catalogue

Fresh execution resolved several ambiguities that source inspection alone did not:

- OWLAPI 4.5.22 excludes domain/range axioms from `getRBoxAxioms`, so the two syntactic source
  collection sites make one effective pass. Annotated duplicate axioms still preserve equal-edge
  multiplicity (27 edges, 12 distinct, 15 duplicates in the isolated fixture).
- The class loop sees the imports closure, but annotation lookup uses the root ontology; an
  imported-only label is not emitted.
- An anonymous object-assertion subject emits its OWLAPI blank-node identifier. An inverse object
  property assertion instead raises the pinned `java.lang.ClassCastException`.
- A strict missing import fails during oracle loading with
  `OWLOntologyFactoryNotFoundException`; import policy remains owned by `pyowl-core`.
- Non-string datatypes retain the source's malformed/truncated `stripValue` behavior, including
  `42\"^^xsd:intege` and `true\"^^xsd:boolea` destinations.

## Java-free gates

- Python 3.10: 25 unit tests passed, 2 baseline tests skipped only because the declared `tomli`
  development extra was not installed.
- Python 3.12: 25 unit tests passed.
- Ruff format/lint and strict mypy passed for runtime source, oracle driver, and golden tests.
- Runtime-dependency and baseline audits passed.
- A PEP 517 sdist and universal wheel built successfully. Archive inspection confirmed that the
  Maven project, Scala runner, fixture corpus, committed goldens, JARs, and oracle integrity test
  are absent. The small reference-rule catalogue remains in the sdist; the wheel contains runtime
  Python files and license metadata only.

Setuptools 80 emitted its existing deprecation warning for the P0 license-table/classifier form.
It did not affect this gate and is deferred to the packaging work package rather than mixed into
the behavioral oracle commit.
