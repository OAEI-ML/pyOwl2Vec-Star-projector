from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError
from typing import cast

from pyowl2vec_star_projector import (
    BATCH_SINK_PROTOCOL_VERSION,
    COMPILER_CACHE_SCHEMA,
    EDGE_ARTIFACT_SCHEMA,
    INGESTION_PROVENANCE_SCHEMA,
    PROJECTOR_API_VERSION,
    REFERENCE_PROFILE,
    CoreProvenance,
    Edge,
    IngestionProvenance,
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
        with self.assertRaises(UnsupportedProfileError):
            ProjectionOptions(profile=[])  # type: ignore[arg-type]
        with self.assertRaises(InvalidProjectionOptionsError):
            ProjectionOptions(backend=[])  # type: ignore[arg-type]

    def test_normalized_record(self) -> None:
        record = ProjectionOptions(duplicates="unique", backend="python").to_dict()
        self.assertEqual(record["duplicates"], "unique")
        self.assertEqual(record["backend"], "python")


class ProvenanceTests(unittest.TestCase):
    def test_json_compatible_versioned_record(self) -> None:
        core = CoreProvenance(
            package_version="0.1.1",
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
        ingestion = cast(dict[str, object], record["ingestion"])
        self.assertEqual(ingestion["schema"], INGESTION_PROVENANCE_SCHEMA)
        self.assertEqual(ingestion["path"], "scalar-python")

    def test_ingestion_provenance_requires_consistent_encoded_metadata(self) -> None:
        with self.assertRaises(ValueError):
            IngestionProvenance(path="encoded-native")
        encoded = IngestionProvenance(
            path="encoded-native",
            encoded_schema_name="pyowl-core/structural-columns",
            encoded_schema_version=1,
            encoded_descriptor_sha256="ab" * 32,
        )
        self.assertEqual(encoded.encoded_schema_version, 1)

    def test_ingestion_diagnostics_are_bounded_immutable_and_json_safe(self) -> None:
        provenance = IngestionProvenance(
            path="encoded-native",
            encoded_schema_name="pyowl-core/structural-columns",
            encoded_schema_version=1,
            encoded_descriptor_sha256="ab" * 32,
            encoded_view_publication_seconds=0.125,
            consumer_compile_seconds=0.25,
            counters={
                "encoded_buffer_bytes": 64,
                "encoded_compiler_gil_released": False,
                "encoded_staging_copy_bytes": 0,
                "materialized_scalar_rows": 0,
            },
        )

        record = provenance.to_dict()
        json.dumps(record, sort_keys=True)
        self.assertEqual(record["encoded_view_publication_seconds"], 0.125)
        self.assertEqual(record["consumer_compile_seconds"], 0.25)
        self.assertEqual(
            record["counters"],
            {
                "encoded_buffer_bytes": 64,
                "encoded_compiler_gil_released": False,
                "encoded_staging_copy_bytes": 0,
                "materialized_scalar_rows": 0,
            },
        )
        with self.assertRaises(TypeError):
            provenance.counters["encoded_buffer_bytes"] = 1  # type: ignore[index]

        for value in (True, -1, float("inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    IngestionProvenance(consumer_compile_seconds=value)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            IngestionProvenance(counters={"private_pointer": 1})
        with self.assertRaises(ValueError):
            IngestionProvenance(counters={"encoded_buffer_bytes": True})
        with self.assertRaises(ValueError):
            IngestionProvenance(counters={"encoded_compiler_gil_released": 0})
        for path in ("scalar-python", "scalar-native"):
            with self.subTest(path=path, diagnostic="publication"):
                with self.assertRaisesRegex(ValueError, "encoded-view publication"):
                    IngestionProvenance(
                        path=path,
                        encoded_view_publication_seconds=0.125,
                    )
            for name, value in (
                ("encoded_buffer_count", 1),
                ("encoded_compiler_gil_released", True),
            ):
                with self.subTest(path=path, diagnostic=name):
                    with self.assertRaisesRegex(ValueError, "nonzero encoded resources"):
                        IngestionProvenance(path=path, counters={name: value})

    def test_counts_reject_bool_negative_and_float(self) -> None:
        for value in (True, -1, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ProjectionCounts(edges=value)  # type: ignore[arg-type]

    def test_native_batch_ingestion_counters_are_bounded_public_integers(self) -> None:
        metadata = {
            "path": "encoded-native",
            "encoded_schema_name": "pyowl-core/structural-columns",
            "encoded_schema_version": 1,
            "encoded_descriptor_sha256": "ab" * 32,
        }
        names = (
            "native_batch_edges",
            "native_boundary_calls",
            "native_compiled_edges",
            "native_edge_batches",
            "native_output_vector_edges",
            "native_peak_buffered_edges",
            "native_retained_inverse_properties",
            "native_retained_subrole_properties",
        )
        for name in names:
            with self.subTest(name=name, case="accepted"):
                provenance = IngestionProvenance(
                    **metadata,  # type: ignore[arg-type]
                    counters={name: 1},
                )
                self.assertEqual(provenance.counters[name], 1)
            for value in (True, -1):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        IngestionProvenance(
                            **metadata,  # type: ignore[arg-type]
                            counters={name: value},
                        )


if __name__ == "__main__":
    unittest.main()
