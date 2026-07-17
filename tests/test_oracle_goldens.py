from __future__ import annotations

import hashlib
import itertools
import json
import unittest
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar, cast

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "oracle"
GOLDENS = ROOT / "tests" / "goldens" / "mowl-d993536-v1"


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def edge_tuple(edge: dict[str, str]) -> tuple[str, str, str]:
    return edge["source"], edge["relation"], edge["destination"]


def stable_corpus_view(value: dict[str, Any]) -> dict[str, Any]:
    value = cast(dict[str, Any], json.loads(json.dumps(value)))
    for case in value["cases"].values():
        case.pop("non_contract_diagnostics")
        for invocation in case["invocations"]:
            invocation.pop("raw_edges")
            invocation.pop("raw_order_sha256")
    return value


class OracleGoldenTests(unittest.TestCase):
    inventory: ClassVar[dict[str, Any]]
    rules: ClassVar[dict[str, Any]]
    report: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = cast(
            dict[str, Any],
            json.loads((FIXTURES / "inventory.json").read_text(encoding="utf-8")),
        )
        cls.rules = cast(
            dict[str, Any],
            json.loads((ROOT / "specs" / "reference-rules.json").read_text(encoding="utf-8")),
        )
        cls.report = cast(
            dict[str, Any],
            json.loads((GOLDENS / "regeneration-report.json").read_text(encoding="utf-8")),
        )

    def test_every_rule_and_fixture_is_cross_referenced(self) -> None:
        fixture_ids = {
            entry["id"]
            for group in (self.inventory["fixtures"], self.inventory["sessions"])
            for entry in group
        }
        rule_rows = {entry["id"]: entry for entry in self.rules["rules"]}
        self.assertEqual(sorted(rule_rows), [f"RB-{number:03d}" for number in range(1, 48)])
        inventory_rules = {
            rule
            for group in (self.inventory["fixtures"], self.inventory["sessions"])
            for entry in group
            for rule in entry["rules"]
        }
        self.assertEqual(inventory_rules, set(rule_rows))
        for row in rule_rows.values():
            self.assertTrue(row["claim"].endswith("."))
            self.assertTrue(set(row["fixtures"]).issubset(fixture_ids))

    def test_fixture_documents_exist_and_are_cc0(self) -> None:
        self.assertEqual(self.inventory["license"], "CC0-1.0")
        documents = {entry["document"] for entry in self.inventory["fixtures"]} | {
            document for session in self.inventory["sessions"] for document in session["documents"]
        }
        for document in documents:
            self.assertTrue((FIXTURES / document).is_file(), document)

    def test_every_fixture_has_all_eight_flag_cases(self) -> None:
        expected_cases = {
            "b{}-t{}-l{}".format(*(int(value) for value in values))
            for values in itertools.product((False, True), repeat=3)
        }
        expected_ids = {
            entry["id"]
            for group in (self.inventory["fixtures"], self.inventory["sessions"])
            for entry in group
        }
        golden_paths = {
            path.stem: path
            for path in GOLDENS.glob("*.json")
            if path.name != "regeneration-report.json"
        }
        self.assertEqual(set(golden_paths), expected_ids)
        for fixture_id, path in golden_paths.items():
            golden = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(golden["schema"], "pyowl-projector.scala-golden/1")
            self.assertEqual(golden["profile"], "mowl-d993536-v1")
            self.assertEqual(golden["fixture"]["id"], fixture_id)
            self.assertEqual(set(golden["cases"]), expected_cases)

    def test_edge_counters_canonical_derivatives_and_metadata_hashes(self) -> None:
        for path in sorted(GOLDENS.glob("*.json")):
            if path.name == "regeneration-report.json":
                continue
            golden = json.loads(path.read_text(encoding="utf-8"))
            for case in golden["cases"].values():
                expected_case_metadata = {
                    "options": case["options"],
                    "invocation_history": case["invocation_history"],
                    "invocations": [
                        {
                            "metadata_sha256": invocation["metadata_sha256"],
                            "edge_counter_sha256": invocation["edge_counter_sha256"],
                            "canonical_edges_sha256": invocation["canonical_edges_sha256"],
                        }
                        for invocation in case["invocations"]
                    ],
                }
                self.assertEqual(case["case_metadata_sha256"], digest(expected_case_metadata))
                self.assertEqual(case["non_contract_diagnostics"]["contract"], "non-contract")
                for invocation in case["invocations"]:
                    raw = [edge_tuple(edge) for edge in invocation["raw_edges"]]
                    canonical = sorted(raw, key=lambda edge: tuple(part.encode() for part in edge))
                    counter = Counter(raw)
                    expected_counter = [
                        {
                            "edge": {
                                "source": edge[0],
                                "relation": edge[1],
                                "destination": edge[2],
                            },
                            "count": count,
                        }
                        for edge, count in sorted(
                            counter.items(),
                            key=lambda item: tuple(part.encode() for part in item[0]),
                        )
                    ]
                    self.assertEqual(invocation["raw_edge_count"], len(raw))
                    self.assertEqual(invocation["distinct_edge_count"], len(counter))
                    self.assertEqual(invocation["duplicate_edge_count"], len(raw) - len(counter))
                    self.assertEqual(invocation["edge_counter"], expected_counter)
                    self.assertEqual(invocation["edge_counter_sha256"], digest(expected_counter))
                    canonical_objects = [
                        {"source": edge[0], "relation": edge[1], "destination": edge[2]}
                        for edge in canonical
                    ]
                    self.assertEqual(invocation["canonical_edges"], canonical_objects)
                    self.assertEqual(
                        invocation["canonical_edges_sha256"], digest(canonical_objects)
                    )
                    metadata = {
                        key: value
                        for key, value in invocation.items()
                        if key
                        not in {
                            "raw_edges",
                            "raw_order_sha256",
                            "canonical_edges",
                            "edge_counter",
                            "metadata_sha256",
                        }
                    }
                    self.assertEqual(invocation["metadata_sha256"], digest(metadata))

    def test_typed_outcomes_are_deliberate(self) -> None:
        for path in sorted(GOLDENS.glob("*.json")):
            if path.name == "regeneration-report.json":
                continue
            golden = json.loads(path.read_text(encoding="utf-8"))
            fixture_id = golden["fixture"]["id"]
            outcomes = {
                invocation["outcome"]
                for case in golden["cases"].values()
                for invocation in case["invocations"]
            }
            if fixture_id in {"abox-unsupported-property", "imports-missing"}:
                self.assertEqual(outcomes, {"error"})
                error_types = {
                    invocation["error"]["type"]
                    for case in golden["cases"].values()
                    for invocation in case["invocations"]
                }
                expected_error = {
                    "abox-unsupported-property": "java.lang.ClassCastException",
                    "imports-missing": (
                        "org.semanticweb.owlapi.model.OWLOntologyFactoryNotFoundException"
                    ),
                }[fixture_id]
                self.assertEqual(error_types, {expected_error})
            else:
                self.assertEqual(outcomes, {"success"}, fixture_id)

    def test_consecutive_run_report_matches_committed_contract_views(self) -> None:
        self.assertEqual(self.report["consecutive_runs"], 2)
        self.assertTrue(self.report["stable"])
        expected: dict[str, str] = {}
        for path in sorted(GOLDENS.glob("*.json")):
            if path.name == "regeneration-report.json":
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            expected[path.name] = digest(stable_corpus_view(value))
        self.assertEqual(self.report["golden_stable_sha256"], expected)

    def test_goldens_contain_no_checkout_path(self) -> None:
        checkout = str(ROOT.resolve())
        for path in GOLDENS.glob("*.json"):
            self.assertNotIn(checkout, path.read_text(encoding="utf-8"), path.name)

    def test_toolchain_dependency_lock_is_self_consistent(self) -> None:
        lock = json.loads(
            (ROOT / "tools" / "java-oracle" / "dependency-lock.json").read_text(encoding="utf-8")
        )
        recorded = lock.pop("dependency_set_sha256")
        self.assertEqual(recorded, digest(lock))
        artifacts = lock["maven_runtime_artifacts"]
        self.assertEqual(len(artifacts), len({entry["artifact"] for entry in artifacts}))
        self.assertTrue(all(len(entry["sha256"]) == 64 for entry in artifacts))

    def test_oracle_is_pruned_from_distributions(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("prune tools/java-oracle", manifest)
        self.assertIn("exclude tests/test_oracle_goldens.py", manifest)
        self.assertIn("include specs/reference-rules.json", manifest)
        self.assertNotIn("recursive-include tools *.py", manifest)


if __name__ == "__main__":
    unittest.main()
