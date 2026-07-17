# Quarantined Scala oracle

This directory is maintainer-only parity infrastructure. It stages four exact source files from
mOWL commit `d9935369144f9a618ece38b7b2a8f4293afe8c26`, verifies their Git blobs before copying,
compiles them with Java 11/Scala 2.11.12, and runs them over synthetic CC0 fixtures. Nothing here
is imported by `src/`, run by ordinary CI, or included in a wheel or sdist.

## Regenerate

Use a clean checkout of the pinned mOWL commit. The following is the complete regeneration
command for the recorded Homebrew toolchain; equivalent paths may be supplied on another host:

```bash
JAVA_HOME=/private/tmp/exact-owl-toolchains/openjdk@11/11.0.31/libexec/openjdk.jdk/Contents/Home \
MOWL_CHECKOUT=/private/tmp/mowl-pinned \
/usr/local/bin/python3.12 tools/java-oracle/regenerate.py \
  --maven /private/tmp/exact-owl-toolchains/maven/3.9.16/libexec/bin/mvn
```

The command deliberately runs the complete matrix twice. It fails unless edge counters,
canonical derivatives, and contract metadata match. Raw OWLAPI list order is retained in the
first run; any harmless second-run raw-order difference is listed in
`regeneration-report.json` and is never treated as canonical order.

Maven may access the network only when its explicit local cache lacks a pinned dependency.
Maven verifies repository checksums during resolution; before any oracle invocation, the driver
also verifies every resolved runtime JAR against `dependency-lock.json`. The JDK's SPDX SBOM is
hash-pinned in the same lock. `--write-dependency-lock` exists only to bootstrap a consciously
reviewed toolchain update and must not be used during routine regeneration.

## Isolation and cleanup

Generated compiler state lives under `.oracle-work/` and Maven's normal external cache. Both are
outside release artifacts. `target/` is globally ignored. Removing those directories has no
effect on committed goldens or normal Python tests.

The authoritative outputs are `tests/goldens/mowl-d993536-v1/*.json`. Each case stores the raw
ordered edge list, an edge counter and digest, a UTF-8 canonical derivative and digest, imported
document hashes, effective flags, typed outcome, invocation history, toolchain hashes, and
captured stdout/stderr explicitly labelled non-contract.

## Golden changes

Follow [`GOLDEN_CHANGE_POLICY.md`](GOLDEN_CHANGE_POLICY.md). In particular, regeneration is not
permission to accept a diff: two reviewers must classify and explain every semantic change.
