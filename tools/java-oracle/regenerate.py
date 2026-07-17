#!/usr/bin/env python3
"""Build the pinned Scala oracle and regenerate deterministic golden vectors.

This script is intentionally outside the Python distribution. It is a maintainer
tool, not a runtime path, and requires an explicit mOWL checkout and Java toolchain.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
ORACLE_DIR = Path(__file__).resolve().parent
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "oracle"
DEFAULT_OUTPUT = ROOT / "tests" / "goldens" / "mowl-d993536-v1"
LOCK_PATH = ORACLE_DIR / "dependency-lock.json"

UPSTREAM_COMMIT = "d9935369144f9a618ece38b7b2a8f4293afe8c26"
UPSTREAM_FILES = {
    "gateway/src/main/scala/org/mowl/Projectors/OWL2VecStarProjector.scala": (
        "a7f7584bbe687ae341cf0547bc0492ada87cf4b8"
    ),
    "gateway/src/main/scala/org/mowl/Projectors/AbstractProjector.scala": (
        "5dc4a382f554c597c4c598eae7ef6edf6b769ff2"
    ),
    "gateway/src/main/scala/org/mowl/Types.scala": ("19210d0b5f354955701f259831d027935c54723b"),
    "gateway/src/main/scala/org/mowl/Utils.scala": ("ecba33446d9d969cf4688565f5ee200ed9d131e1"),
}

EdgeTuple = tuple[str, str, str]


class OracleFailure(RuntimeError):
    """A reproducibility or oracle execution gate failed."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def run_checked(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        rendered = " ".join(command)
        raise OracleFailure(
            f"command failed ({result.returncode}): {rendered}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def verify_and_stage_reference(checkout: Path) -> dict[str, str]:
    checkout = checkout.resolve()
    revision = run_checked(["git", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
    if revision != UPSTREAM_COMMIT:
        raise OracleFailure(f"mOWL checkout is {revision}; expected {UPSTREAM_COMMIT}")

    destination = ORACLE_DIR / ".oracle-work" / "reference-src"
    shutil.rmtree(destination, ignore_errors=True)
    verified: dict[str, str] = {}
    for relative, expected_blob in UPSTREAM_FILES.items():
        blob = run_checked(["git", "hash-object", relative], cwd=checkout).stdout.strip()
        if blob != expected_blob:
            raise OracleFailure(f"Git blob mismatch for {relative}: {blob} != {expected_blob}")
        source = checkout / relative
        package_relative = Path(relative).relative_to("gateway/src/main/scala")
        target = destination / package_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        verified[relative] = sha256_file(source)
    return verified


def java_environment(java_home: Path, maven: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["JAVA_HOME"] = str(java_home.resolve())
    path_parts = (java_home.resolve() / "bin", maven.resolve().parent, env.get("PATH", ""))
    env["PATH"] = os.pathsep.join(str(part) for part in path_parts)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["TZ"] = "UTC"
    return env


def find_jdk_sbom(java_home: Path) -> Path:
    for parent in (java_home.resolve(), *java_home.resolve().parents):
        candidate = parent / "sbom.spdx.json"
        if candidate.is_file():
            return candidate
    raise OracleFailure(f"no sbom.spdx.json found above JAVA_HOME {java_home}")


def build_oracle(java_home: Path, maven: Path, env: dict[str, str]) -> list[Path]:
    classpath_file = ORACLE_DIR / ".oracle-work" / "runtime-classpath.txt"
    classpath_file.parent.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            str(maven),
            "--batch-mode",
            "--no-transfer-progress",
            "-q",
            "-DskipTests",
            "package",
            "dependency:build-classpath",
            f"-Dmdep.outputFile={classpath_file}",
            "-Dmdep.includeScope=runtime",
        ],
        cwd=ORACLE_DIR,
        env=env,
        timeout=1200,
    )
    jars = [Path(item).resolve() for item in classpath_file.read_text().strip().split(os.pathsep)]
    missing = [str(path) for path in jars if not path.is_file()]
    if missing:
        raise OracleFailure(f"Maven classpath contains missing artifacts: {missing}")
    return jars


def artifact_key(path: Path) -> str:
    """Return a repository-independent Maven artifact key."""
    parts = path.parts
    try:
        marker = max(index for index, part in enumerate(parts) if part == "repository")
    except ValueError:
        return path.name
    return "/".join(parts[marker + 1 :])


def dependency_inventory(jars: list[Path]) -> list[dict[str, str]]:
    entries = [
        {"artifact": artifact_key(path), "sha256": sha256_file(path)}
        for path in sorted(jars, key=artifact_key)
    ]
    keys = [entry["artifact"] for entry in entries]
    if len(keys) != len(set(keys)):
        raise OracleFailure("runtime dependency artifact keys are not unique")
    return entries


def verify_dependency_lock(
    inventory: list[dict[str, str]],
    *,
    write_lock: bool,
    jdk_sbom: Path,
) -> dict[str, Any]:
    payload = {
        "schema": "pyowl-projector.oracle-dependencies/1",
        "jdk_sbom_sha256": sha256_file(jdk_sbom),
        "maven_runtime_artifacts": inventory,
    }
    payload["dependency_set_sha256"] = sha256_bytes(canonical_json(payload))
    if write_lock:
        LOCK_PATH.write_bytes(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode() + b"\n"
        )
        return payload
    if not LOCK_PATH.is_file():
        raise OracleFailure(
            "dependency-lock.json is absent; a maintainer must inspect artifacts and run once "
            "with --write-dependency-lock"
        )
    expected = cast(dict[str, Any], json.loads(LOCK_PATH.read_text(encoding="utf-8")))
    if expected != payload:
        raise OracleFailure("resolved Maven/JDK dependency hashes differ from dependency-lock.json")
    return expected


def decode(value: str) -> str:
    return base64.b64decode(value).decode("utf-8")


def normalize_machine_paths(value: str) -> str:
    """Remove host checkout identity from captured, non-semantic diagnostics."""
    return value.replace(str(ROOT.resolve()), "${REPOSITORY}")


def document_record(logical_iri: str, document_iri: str) -> dict[str, str]:
    record = {"ontology_iri": logical_iri, "document_iri": document_iri}
    parsed = urlparse(document_iri)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
        if path.is_file():
            relative = path.resolve().relative_to(FIXTURE_ROOT.resolve())
            record["bytes_sha256"] = sha256_file(path)
            record["document"] = str(relative)
            record["document_iri"] = f"fixture:///{relative.as_posix()}"
    return record


def parse_transport(path: Path, invocation_count: int) -> list[dict[str, Any]]:
    invocations: list[dict[str, Any]] = [
        {"documents": [], "missing_imports": [], "raw_edges": []} for _ in range(invocation_count)
    ]
    protocol_seen = False
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        kind = fields[0]
        if kind == "PROTOCOL":
            if fields[1] != "mowl-projector-oracle/1":
                raise OracleFailure(f"unsupported transport protocol: {fields[1]}")
            protocol_seen = True
        elif kind == "FLAGS":
            continue
        elif kind == "BEGIN":
            invocation = invocations[int(fields[1])]
            invocation["input_name"] = decode(fields[2])
        elif kind == "DOCUMENT":
            invocation = invocations[int(fields[1])]
            invocation["documents"].append(document_record(decode(fields[2]), decode(fields[3])))
        elif kind == "MISSING_IMPORT":
            invocation = invocations[int(fields[1])]
            invocation["missing_imports"].append(
                {"iri": decode(fields[2]), "exception_type": decode(fields[3])}
            )
        elif kind == "EDGE":
            invocations[int(fields[1])]["raw_edges"].append(
                (decode(fields[2]), decode(fields[3]), decode(fields[4]))
            )
        elif kind == "SUCCESS":
            invocation = invocations[int(fields[1])]
            invocation["outcome"] = "success"
            invocation["reported_edge_count"] = int(fields[2])
        elif kind == "ERROR":
            invocation = invocations[int(fields[1])]
            invocation["outcome"] = "error"
            invocation["error"] = {
                "type": decode(fields[2]),
                "message": normalize_machine_paths(decode(fields[3])),
            }
        elif kind in {"END"}:
            continue
        else:
            raise OracleFailure(f"unknown oracle transport record: {kind}")
    if not protocol_seen:
        raise OracleFailure("oracle transport omitted its protocol record")
    for invocation in invocations:
        if "outcome" not in invocation:
            raise OracleFailure("oracle invocation omitted a terminal outcome")
        invocation["documents"] = sorted(
            invocation["documents"], key=lambda value: value["ontology_iri"].encode()
        )
        invocation["missing_imports"] = sorted(
            invocation["missing_imports"], key=lambda value: value["iri"].encode()
        )
    return invocations


def edge_object(edge: EdgeTuple) -> dict[str, str]:
    return {"source": edge[0], "relation": edge[1], "destination": edge[2]}


def augment_edges(invocation: dict[str, Any]) -> dict[str, Any]:
    raw_edges: list[EdgeTuple] = invocation.pop("raw_edges")
    if invocation["outcome"] == "success" and invocation["reported_edge_count"] != len(raw_edges):
        raise OracleFailure("Scala-reported edge count differs from transported edges")
    counter = Counter(raw_edges)
    counter_rows = [
        {"edge": edge_object(edge), "count": count}
        for edge, count in sorted(
            counter.items(), key=lambda item: tuple(part.encode() for part in item[0])
        )
    ]
    canonical = sorted(raw_edges, key=lambda edge: tuple(part.encode() for part in edge))
    raw_objects = [edge_object(edge) for edge in raw_edges]
    canonical_objects = [edge_object(edge) for edge in canonical]
    invocation.update(
        {
            "raw_edge_count": len(raw_edges),
            "distinct_edge_count": len(counter),
            "duplicate_edge_count": len(raw_edges) - len(counter),
            "raw_order_sha256": sha256_bytes(canonical_json(raw_objects)),
            "edge_counter": counter_rows,
            "edge_counter_sha256": sha256_bytes(canonical_json(counter_rows)),
            "raw_edges": raw_objects,
            "canonical_edges": canonical_objects,
            "canonical_edges_sha256": sha256_bytes(canonical_json(canonical_objects)),
        }
    )
    stable_metadata = {
        key: value
        for key, value in invocation.items()
        if key not in {"raw_edges", "raw_order_sha256", "canonical_edges", "edge_counter"}
    }
    invocation["metadata_sha256"] = sha256_bytes(canonical_json(stable_metadata))
    return invocation


def option_key(values: tuple[bool, bool, bool]) -> str:
    return "b{}-t{}-l{}".format(*(int(value) for value in values))


def run_case(
    documents: list[str],
    flags: tuple[bool, bool, bool],
    *,
    java_home: Path,
    classpath: str,
    env: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="pyowl-projector-oracle-") as temporary:
        transport = Path(temporary) / "transport.tsv"
        inputs = [str((FIXTURE_ROOT / document).resolve()) for document in documents]
        command = [
            str(java_home / "bin" / "java"),
            "-Dfile.encoding=UTF-8",
            "-Duser.language=en",
            "-Duser.country=US",
            "-Duser.timezone=UTC",
            "-cp",
            classpath,
            "org.oaei_ml.oracle.OracleRunner",
            "--fixture-root",
            str(FIXTURE_ROOT.resolve()),
            "--output",
            str(transport),
            "--inputs",
            os.pathsep.join(inputs),
            "--bidirectional",
            str(flags[0]).lower(),
            "--only-taxonomy",
            str(flags[1]).lower(),
            "--include-literals",
            str(flags[2]).lower(),
        ]
        result = run_checked(command, cwd=ROOT, env=env, timeout=120)
        invocations = [augment_edges(value) for value in parse_transport(transport, len(documents))]
        stdout = normalize_machine_paths(result.stdout)
        stderr = normalize_machine_paths(result.stderr)
        diagnostics = {
            "contract": "non-contract",
            "stdout": stdout.splitlines(),
            "stderr": stderr.splitlines(),
            "stdout_sha256": sha256_bytes(stdout.encode()),
            "stderr_sha256": sha256_bytes(stderr.encode()),
        }
        return invocations, diagnostics


def fixture_sha256(documents: list[str]) -> str:
    records = [
        {"document": document, "sha256": sha256_file(FIXTURE_ROOT / document)}
        for document in documents
    ]
    return sha256_bytes(canonical_json(records))


def generate_corpus(
    destination: Path,
    *,
    java_home: Path,
    classpath: str,
    env: dict[str, str],
    source_hashes: dict[str, str],
    dependency_lock: dict[str, Any],
    environment_sha256: str,
) -> dict[str, str]:
    inventory = json.loads((FIXTURE_ROOT / "inventory.json").read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    output_digests: dict[str, str] = {}
    entries = [
        (fixture["id"], [fixture["document"]], fixture["rules"], "fresh-instance")
        for fixture in inventory["fixtures"]
    ]
    entries.extend(
        (session["id"], session["documents"], session["rules"], "scala-instance-session")
        for session in inventory["sessions"]
    )
    for fixture_id, documents, rules, lifecycle in entries:
        cases: dict[str, Any] = {}
        for raw_flags in itertools.product((False, True), repeat=3):
            flags = cast(tuple[bool, bool, bool], raw_flags)
            invocations, diagnostics = run_case(
                documents, flags, java_home=java_home, classpath=classpath, env=env
            )
            options = {
                "bidirectional_taxonomy": flags[0],
                "only_taxonomy": flags[1],
                "include_literals": flags[2],
            }
            stable_case = {
                "options": options,
                "invocation_history": documents,
                "invocations": invocations,
            }
            case_metadata = {
                "options": options,
                "invocation_history": documents,
                "invocations": [
                    {
                        "metadata_sha256": invocation["metadata_sha256"],
                        "edge_counter_sha256": invocation["edge_counter_sha256"],
                        "canonical_edges_sha256": invocation["canonical_edges_sha256"],
                    }
                    for invocation in invocations
                ],
            }
            stable_case["case_metadata_sha256"] = sha256_bytes(canonical_json(case_metadata))
            stable_case["non_contract_diagnostics"] = diagnostics
            cases[option_key(flags)] = stable_case
        payload = {
            "schema": "pyowl-projector.scala-golden/1",
            "profile": "mowl-d993536-v1",
            "fixture": {
                "id": fixture_id,
                "documents": documents,
                "bytes_manifest_sha256": fixture_sha256(documents),
                "rules": rules,
                "license": inventory["license"],
                "lifecycle": lifecycle,
            },
            "reference": {
                "repository": "https://github.com/bio-ontology-research-group/mowl",
                "commit": UPSTREAM_COMMIT,
                "projector_git_blob": UPSTREAM_FILES[
                    "gateway/src/main/scala/org/mowl/Projectors/OWL2VecStarProjector.scala"
                ],
                "source_sha256": source_hashes,
                "java": "11.0.31",
                "scala": "2.11.12",
                "owlapi": "4.5.22",
                "elk": "0.4.3",
                "hermit": "1.3.8.413",
                "dependency_set_sha256": dependency_lock["dependency_set_sha256"],
                "jdk_sbom_sha256": dependency_lock["jdk_sbom_sha256"],
                "environment_sha256": environment_sha256,
            },
            "cases": cases,
        }
        path = destination / f"{fixture_id}.json"
        data = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode() + b"\n"
        path.write_bytes(data)
        output_digests[path.name] = sha256_bytes(data)
    return output_digests


def stable_corpus_view(path: Path) -> dict[str, Any]:
    value = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    for case in value["cases"].values():
        case.pop("non_contract_diagnostics", None)
        for invocation in case["invocations"]:
            invocation.pop("raw_edges", None)
            invocation.pop("raw_order_sha256", None)
    return value


def raw_order_differences(first_value: dict[str, Any], second_value: dict[str, Any]) -> list[str]:
    differences: list[str] = []
    for case_name, first_case in first_value["cases"].items():
        second_case = second_value["cases"][case_name]
        for index, first_invocation in enumerate(first_case["invocations"]):
            second_invocation = second_case["invocations"][index]
            if first_invocation["raw_order_sha256"] != second_invocation["raw_order_sha256"]:
                differences.append(f"{case_name}/invocation-{index}")
    return differences


def compare_runs(first: Path, second: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    first_names = sorted(path.name for path in first.glob("*.json"))
    second_names = sorted(path.name for path in second.glob("*.json"))
    if first_names != second_names:
        raise OracleFailure("consecutive runs produced different golden file inventories")
    digests: dict[str, str] = {}
    order_differences: dict[str, list[str]] = {}
    for name in first_names:
        first_full = json.loads((first / name).read_text(encoding="utf-8"))
        second_full = json.loads((second / name).read_text(encoding="utf-8"))
        differences = raw_order_differences(first_full, second_full)
        if differences:
            order_differences[name] = differences
        first_data = stable_corpus_view(first / name)
        second_data = stable_corpus_view(second / name)
        if first_data != second_data:
            raise OracleFailure(f"consecutive oracle runs differ in stable data: {name}")
        digests[name] = sha256_bytes(canonical_json(first_data))
    return digests, order_differences


def replace_output(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    expected = {path.name for path in source.glob("*.json")}
    for prior in destination.glob("*.json"):
        if prior.name not in expected:
            prior.unlink()
    for path in source.glob("*.json"):
        shutil.copyfile(path, destination / path.name)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mowl-checkout",
        type=Path,
        default=os.environ.get("MOWL_CHECKOUT"),
        required="MOWL_CHECKOUT" not in os.environ,
    )
    parser.add_argument(
        "--java-home",
        type=Path,
        default=os.environ.get("JAVA_HOME"),
        required="JAVA_HOME" not in os.environ,
    )
    parser.add_argument(
        "--maven", type=Path, default=shutil.which("mvn"), required=shutil.which("mvn") is None
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--write-dependency-lock",
        action="store_true",
        help="bootstrap/update the reviewed dependency hash lock; not for routine regeneration",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_hashes = verify_and_stage_reference(args.mowl_checkout)
    env = java_environment(args.java_home, args.maven)
    jars = build_oracle(args.java_home, args.maven, env)
    jdk_sbom = find_jdk_sbom(args.java_home)
    lock = verify_dependency_lock(
        dependency_inventory(jars), write_lock=args.write_dependency_lock, jdk_sbom=jdk_sbom
    )
    environment_payload = {
        "dependency_set_sha256": lock["dependency_set_sha256"],
        "jdk_sbom_sha256": lock["jdk_sbom_sha256"],
        "pom_sha256": sha256_file(ORACLE_DIR / "pom.xml"),
        "source_sha256": source_hashes,
    }
    environment_sha256 = sha256_bytes(canonical_json(environment_payload))
    classpath = os.pathsep.join(
        [str(ORACLE_DIR / "target" / "classes"), *(str(path) for path in jars)]
    )
    with tempfile.TemporaryDirectory(prefix="pyowl-projector-golden-runs-") as temporary:
        first = Path(temporary) / "run-1"
        second = Path(temporary) / "run-2"
        generate_corpus(
            first,
            java_home=args.java_home,
            classpath=classpath,
            env=env,
            source_hashes=source_hashes,
            dependency_lock=lock,
            environment_sha256=environment_sha256,
        )
        generate_corpus(
            second,
            java_home=args.java_home,
            classpath=classpath,
            env=env,
            source_hashes=source_hashes,
            dependency_lock=lock,
            environment_sha256=environment_sha256,
        )
        stable_digests, order_differences = compare_runs(first, second)
        replace_output(first, args.output.resolve())
    report = {
        "schema": "pyowl-projector.oracle-regeneration/1",
        "consecutive_runs": 2,
        "stable": True,
        "comparison": "edge counters, canonical derivatives, and contract metadata",
        "incidental_raw_order_differences": order_differences,
        "environment_sha256": environment_sha256,
        "golden_stable_sha256": stable_digests,
    }
    (args.output.resolve() / "regeneration-report.json").write_bytes(
        json.dumps(report, indent=2, sort_keys=True).encode() + b"\n"
    )
    print(f"generated {len(stable_digests)} deterministic goldens in {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OracleFailure as error:
        print(f"oracle regeneration failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
