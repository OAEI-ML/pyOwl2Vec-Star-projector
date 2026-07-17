# Golden change policy

Golden changes require two reviewers who did not both author the regeneration change. The pull
request must include the exact command, mOWL commit and blob, dependency-set/JDK-SBOM hashes, the
two-run stability report, and a counter-level before/after summary.

Every changed case must be assigned exactly one classification:

1. **upstream pin correction** — the previously recorded source identity was wrong;
2. **oracle correction** — fixture, transport, metadata, or generation logic was wrong; or
3. **new profile** — intended behavior differs and therefore receives a new profile identifier.

“Update expected output” is not an acceptable explanation. A reviewer must verify that ordinary
Python CI still consumes only committed JSON and that wheel/sdist inventories contain no oracle
source, bytecode, Maven metadata, JAR, or JVM dependency. Reviewer names and the classification
belong in the pull request and changelog; generated files contain no nondeterministic signatures
or timestamps.
