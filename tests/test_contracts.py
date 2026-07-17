from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError

from pyowl2vec_star_projector import (
    BATCH_SINK_PROTOCOL_VERSION,
    COMPILER_CACHE_SCHEMA,
    EDGE_ARTIFACT_SCHEMA,
    PROJECTOR_API_VERSION,
    REFERENCE_PROFILE,
    CoreProvenance,
    Edge,
    InvalidProjectionOptionsError,
    ProjectionCounts,
    ProjectionOptions,
    ProjectionProvenance,
    UnsupportedProfileError,
)


class EdgeTests(unittest.TestCase):
    def test_value_identity_and_utf8_key(self) -> None:
        edge = Edge("http://e/é", "http://subclassof", "http://e/z")
        self.assertEqual(edge, Edge(*edge.as_tuple()))
        self.assertEqual(edge.canonical_key()[0], "http://e/é".encode())

    def test_frozen_and_typed(self) -> None:
        edge = Edge("s", "r", "d")
        with self.assertRaises(FrozenInstanceError):
            edge.source = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            Edge("s", "r", 4)  # type: ignore[arg-type]

    def test_batch_sink_protocol_version_is_frozen(self) -> None:
        self.assertEqual(BATCH_SINK_PROTOCOL_VERSION, 1)


class OptionTests(unittest.TestCase):
    def test_defaults_are_the_frozen_profile(self) -> None:
        options = ProjectionOptions()
        self.assertEqual(options.profile, REFERENCE_PROFILE)
        self.assertEqual(options.duplicates, "preserve")
        self.assertEqual(options.order, "canonical")
        self.assertEqual(options.compatibility_state, "isolated")

    def test_all_historical_booleans_are_strict(self) -> None:
        for name in (
            "bidirectional_taxonomy",
            "only_taxonomy",
            "include_literals",
        ):
            with self.subTest(name=name):
                with self.assertRaises(InvalidProjectionOptionsError):
                    ProjectionOptions(**{name: 1})  # type: ignore[arg-type]

    def test_invalid_choices_and_profile_fail(self) -> None:
        with self.assertRaises(InvalidProjectionOptionsError):
            ProjectionOptions(order="hash")  # type: ignore[arg-type]
        with self.assertRaises(UnsupportedProfileError):
            ProjectionOptions(profile="latest")

    def test_normalized_record(self) -> None:
        record = ProjectionOptions(duplicates="unique", backend="python").to_dict()
        self.assertEqual(record["duplicates"], "unique")
        self.assertEqual(record["backend"], "python")


class ProvenanceTests(unittest.TestCase):
    def test_json_compatible_versioned_record(self) -> None:
        core = CoreProvenance(
            package_version="0.1.0",
            api_version=(0, 1),
            model_schema_version=1,
            wire_format_version=(1, 0),
            adapter_protocol_version=1,
            structural_fingerprint="s",
            logical_fingerprint="l",
            signature_fingerprint="g",
            import_manifest_digest="m",
        )
        provenance = ProjectionProvenance(
            options=ProjectionOptions(backend="python"),
            selected_backend="python",
            source_kind="direct",
            core=core,
            counts=ProjectionCounts(edges=3, duplicates=1),
        )
        record = provenance.to_dict()
        json.dumps(record, sort_keys=True)
        self.assertEqual(record["projector_api_version"], PROJECTOR_API_VERSION)
        self.assertEqual(record["edge_artifact_schema"], EDGE_ARTIFACT_SCHEMA)
        self.assertEqual(record["compiler_cache_schema"], COMPILER_CACHE_SCHEMA)

    def test_counts_reject_bool_negative_and_float(self) -> None:
        for value in (True, -1, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ProjectionCounts(edges=value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
