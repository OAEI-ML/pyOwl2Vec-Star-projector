from __future__ import annotations

import gc
import hashlib
import unittest
import weakref
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from pyowl_core.backends.native_views import ENCODED_STRUCTURAL_DESCRIPTOR_V1

from pyowl2vec_star_projector import ProjectionOptions, Projector
from pyowl2vec_star_projector.backend import BackendSelection
from pyowl2vec_star_projector.encoded import (
    ENCODED_BUFFER_WIDTHS,
    ENCODED_DESCRIPTOR_SHA256,
    ENCODED_NATIVE_FEATURE,
    ENCODED_SCHEMA_NAME,
    select_ingestion,
)
from pyowl2vec_star_projector.errors import SnapshotCompatibilityError

from .support.core_views import fixture_view

_CLOSURE_SCOPE = "closure-token"


class _EncodedStructuralView:
    def __init__(self, owner: object, *, writable: bool) -> None:
        self.schema_name = ENCODED_SCHEMA_NAME
        self.schema_version = 1
        self.model_schema = 1
        self.owner = owner
        self.descriptor = ENCODED_STRUCTURAL_DESCRIPTOR_V1
        self.descriptor_digest = hashlib.sha256(self.descriptor).digest()
        self.structural_fingerprint = "encoded-fingerprint"
        self.scope = _CLOSURE_SCOPE
        self.document_key = None
        self.buffers = {
            name: memoryview(b"\x00" * (8 if name == "node_field_offsets" else 0))
            for name in ENCODED_BUFFER_WIDTHS
        }
        if writable:
            self.buffers["root_kinds"] = memoryview(bytearray(b"\x00"))
        self.segments = (_EncodedSegment(owner),)


class _EncodedSegment:
    def __init__(self, owner: object) -> None:
        self.role = 1
        self.owner = owner
        self.source: object | None = None
        self.posting_mode = 0
        self.root_ids = memoryview(b"")
        self.anonymous_scope_map = memoryview(b"")
        self.member_token = None


class _View:
    def __init__(
        self,
        *,
        schemas: object,
        writable: bool = False,
    ) -> None:
        self.capabilities = SimpleNamespace(
            model_schema=1,
            encoded_view_schemas=schemas,
        )
        self.structural_fingerprint = "snapshot-fingerprint"
        self.calls: list[tuple[type[object], dict[str, object]]] = []
        self._writable = writable

    def view(self, view_type: type[object], **options: object) -> object:
        self.calls.append((view_type, options))
        return _EncodedStructuralView(self, writable=self._writable)


def _core() -> object:
    return SimpleNamespace(
        EncodedStructuralView=_EncodedStructuralView,
        AxiomScope=SimpleNamespace(CLOSURE=_CLOSURE_SCOPE),
    )


def _uint_rows(width: int, *values: int) -> memoryview:
    return memoryview(b"".join(value.to_bytes(width, "little") for value in values))


def _one_node_buffers() -> dict[str, memoryview]:
    buffers = {name: memoryview(b"") for name in ENCODED_BUFFER_WIDTHS}
    buffers.update(
        {
            "root_kinds": _uint_rows(1, 2),
            "root_ids": _uint_rows(4, 1),
            "node_tags": _uint_rows(2, 1),
            "node_field_offsets": _uint_rows(8, 0, 0),
        }
    )
    return buffers


class EncodedNativeDispatchTests(unittest.TestCase):
    def test_python_path_never_requests_encoded_buffers(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        decision = select_ingestion(
            view,
            selected_backend="python",
            native_features=frozenset({ENCODED_NATIVE_FEATURE}),
            core_module=_core(),
        )
        self.assertEqual(decision.path, "scalar-python")
        self.assertEqual(view.calls, [])

    def test_scalar_native_compatibility_does_not_probe_core_without_feature(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        decision = select_ingestion(
            view,
            selected_backend="native",
            native_features=frozenset({"bounded-batches"}),
            core_module=_core(),
        )
        self.assertEqual(decision.path, "scalar-native")
        self.assertIn("does not advertise", decision.reason or "")
        self.assertEqual(view.calls, [])

    def test_scalar_native_fallback_accepts_scalar_only_provider(self) -> None:
        view = _View(schemas={})
        decision = select_ingestion(
            view,
            selected_backend="native",
            native_features=frozenset({ENCODED_NATIVE_FEATURE}),
            core_module=_core(),
        )
        self.assertEqual(decision.path, "scalar-native")
        self.assertIn(ENCODED_SCHEMA_NAME, decision.reason or "")
        self.assertEqual(view.calls, [])

    def test_exact_public_schema_acquires_one_identity_preserving_lease(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        decision = select_ingestion(
            view,
            selected_backend="native",
            native_features=frozenset({ENCODED_NATIVE_FEATURE}),
            core_module=_core(),
        )
        self.assertEqual(decision.path, "encoded-native")
        self.assertIsNotNone(decision.lease)
        assert decision.lease is not None
        self.assertIs(decision.lease.owner, view)
        self.assertEqual(decision.lease.buffer_names, tuple(ENCODED_BUFFER_WIDTHS))
        self.assertEqual(
            decision.lease.descriptor_sha256,
            ENCODED_DESCRIPTOR_SHA256.hex(),
        )
        self.assertEqual(len(view.calls), 1)
        _, options = view.calls[0]
        self.assertEqual(options, {"schema_version": 1, "scope": "closure-token"})

    def test_malformed_advertised_buffer_fails_without_scalar_fallback(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1}, writable=True)
        with self.assertRaisesRegex(SnapshotCompatibilityError, "writable buffer"):
            select_ingestion(
                view,
                selected_backend="native",
                native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                core_module=_core(),
            )

    def test_encoded_fingerprint_is_distinct_from_the_owner_fingerprint(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        decision = select_ingestion(
            view,
            selected_backend="native",
            native_features=frozenset({ENCODED_NATIVE_FEATURE}),
            core_module=_core(),
        )

        self.assertEqual(decision.path, "encoded-native")
        self.assertNotEqual(
            view.structural_fingerprint,
            cast(_EncodedStructuralView, view.view(_EncodedStructuralView)).structural_fingerprint,
        )

    def test_encoded_fingerprint_wrong_type_fails_before_native_compilation(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        encoded = _EncodedStructuralView(view, writable=False)
        cast(Any, encoded).structural_fingerprint = None
        view.view = lambda _view_type, **_options: encoded  # type: ignore[method-assign]
        with self.assertRaisesRegex(SnapshotCompatibilityError, "fingerprint"):
            select_ingestion(
                view,
                selected_backend="native",
                native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                core_module=_core(),
            )

    def test_descriptor_digest_mismatch_fails_before_native_compilation(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        encoded = _EncodedStructuralView(view, writable=False)
        encoded.descriptor_digest = b"x" * 32
        view.view = lambda _view_type, **_options: encoded  # type: ignore[method-assign]
        with self.assertRaisesRegex(SnapshotCompatibilityError, "descriptor digest"):
            select_ingestion(
                view,
                selected_backend="native",
                native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                core_module=_core(),
            )

    def test_descriptor_digest_is_derived_from_the_minimal_public_surface(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        encoded = _EncodedStructuralView(view, writable=False)
        del encoded.descriptor_digest
        view.view = lambda _view_type, **_options: encoded  # type: ignore[method-assign]

        decision = select_ingestion(
            view,
            selected_backend="native",
            native_features=frozenset({ENCODED_NATIVE_FEATURE}),
            core_module=_core(),
        )

        assert decision.lease is not None
        self.assertEqual(
            decision.lease.descriptor_sha256,
            hashlib.sha256(encoded.descriptor).hexdigest(),
        )

    def test_self_consistent_descriptor_drift_fails_before_native_compilation(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        encoded = _EncodedStructuralView(view, writable=False)
        encoded.descriptor += b" "
        encoded.descriptor_digest = hashlib.sha256(encoded.descriptor).digest()
        view.view = lambda _view_type, **_options: encoded  # type: ignore[method-assign]

        with self.assertRaisesRegex(SnapshotCompatibilityError, "frozen"):
            select_ingestion(
                view,
                selected_backend="native",
                native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                core_module=_core(),
            )

    def test_buffer_ledger_and_scalar_widths_fail_before_native_compilation(self) -> None:
        cases = {
            "missing": {
                name: memoryview(b"") for name in ENCODED_BUFFER_WIDTHS if name != "root_ids"
            },
            "extra": {
                **{name: memoryview(b"") for name in ENCODED_BUFFER_WIDTHS},
                "private_layout": memoryview(b""),
            },
            "partial": {
                **{name: memoryview(b"") for name in ENCODED_BUFFER_WIDTHS},
                "root_ids": memoryview(b"\x00"),
            },
        }
        for label, buffers in cases.items():
            with self.subTest(label):
                view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
                encoded = _EncodedStructuralView(view, writable=False)
                encoded.buffers = buffers
                view.view = (  # type: ignore[method-assign]
                    lambda _view_type, _encoded=encoded, **_options: _encoded
                )

                with self.assertRaisesRegex(SnapshotCompatibilityError, "buffer"):
                    select_ingestion(
                        view,
                        selected_backend="native",
                        native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                        core_module=_core(),
                    )

    def test_borrowed_columns_require_exact_unsigned_byte_shape(self) -> None:
        cases = {
            "signed-format": memoryview(b"\x00").cast("b"),
            "multidimensional": memoryview(b"\x00").cast("B", shape=(1, 1)),
            "strided": memoryview(b"\x00\x00\x00\x00")[::2],
        }
        for label, column in cases.items():
            with self.subTest(label):
                view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
                encoded = _EncodedStructuralView(view, writable=False)
                encoded.buffers["root_kinds"] = column
                view.view = (  # type: ignore[method-assign]
                    lambda _view_type, _encoded=encoded, **_options: _encoded
                )

                with self.assertRaisesRegex(
                    SnapshotCompatibilityError,
                    "one-dimensional unsigned-byte C-contiguous",
                ):
                    select_ingestion(
                        view,
                        selected_backend="native",
                        native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                        core_module=_core(),
                    )

    def test_scope_and_direct_segment_manifest_fail_closed(self) -> None:
        cases: dict[str, Callable[[_EncodedStructuralView], None]] = {
            "scope": lambda encoded: setattr(encoded, "scope", "root-token"),
            "segments": lambda encoded: setattr(encoded, "segments", ()),
            "owner": lambda encoded: setattr(encoded.segments[0], "owner", object()),
            "postings": lambda encoded: setattr(encoded.segments[0], "root_ids", _uint_rows(4, 1)),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
                encoded = _EncodedStructuralView(view, writable=False)
                mutate(encoded)
                view.view = (  # type: ignore[method-assign]
                    lambda _view_type, _encoded=encoded, **_options: _encoded
                )
                with self.assertRaisesRegex(
                    SnapshotCompatibilityError,
                    "scope|segment|posting|owner|reference",
                ):
                    select_ingestion(
                        view,
                        selected_backend="native",
                        native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                        core_module=_core(),
                    )

    def test_column_offsets_bounds_and_references_fail_closed(self) -> None:
        valid_view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        valid = _EncodedStructuralView(valid_view, writable=False)
        valid.buffers = _one_node_buffers()
        valid_view.view = lambda _view_type, **_options: valid  # type: ignore[method-assign]
        self.assertEqual(
            select_ingestion(
                valid_view,
                selected_backend="native",
                native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                core_module=_core(),
            ).path,
            "encoded-native",
        )

        cases: dict[str, dict[str, memoryview]] = {}
        root_reference = _one_node_buffers()
        root_reference["root_ids"] = _uint_rows(4, 2)
        cases["root-reference"] = root_reference

        offsets = _one_node_buffers()
        offsets["node_field_offsets"] = _uint_rows(8, 1, 1)
        cases["offsets"] = offsets

        node_reference = _one_node_buffers()
        node_reference.update(
            {
                "node_field_offsets": _uint_rows(8, 0, 1),
                "field_kinds": _uint_rows(1, 1),
                "field_values": _uint_rows(8, 2),
                "field_lengths": _uint_rows(8, 0),
            }
        )
        cases["node-reference"] = node_reference

        scalar_bounds = _one_node_buffers()
        scalar_bounds.update(
            {
                "node_field_offsets": _uint_rows(8, 0, 1),
                "field_kinds": _uint_rows(1, 2),
                "field_values": _uint_rows(8, 0),
                "field_lengths": _uint_rows(8, 2),
                "scalar_bytes": memoryview(b"x"),
            }
        )
        cases["scalar-bounds"] = scalar_bounds

        item_reference = _one_node_buffers()
        item_reference.update(
            {
                "node_field_offsets": _uint_rows(8, 0, 1),
                "field_kinds": _uint_rows(1, 7),
                "field_values": _uint_rows(8, 0),
                "field_lengths": _uint_rows(8, 1),
                "item_kinds": _uint_rows(1, 1),
                "item_values": _uint_rows(8, 2),
                "item_lengths": _uint_rows(8, 0),
            }
        )
        cases["item-reference"] = item_reference

        for label, buffers in cases.items():
            with self.subTest(label):
                view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
                encoded = _EncodedStructuralView(view, writable=False)
                encoded.buffers = buffers
                view.view = (  # type: ignore[method-assign]
                    lambda _view_type, _encoded=encoded, **_options: _encoded
                )
                with self.assertRaisesRegex(
                    SnapshotCompatibilityError,
                    "offset|bound|reference|range",
                ):
                    select_ingestion(
                        view,
                        selected_backend="native",
                        native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                        core_module=_core(),
                    )

    def test_referenced_segment_postings_are_bounded_by_source_roots(self) -> None:
        source_owner = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        source = _EncodedStructuralView(source_owner, writable=False)
        source.buffers = _one_node_buffers()
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        encoded = _EncodedStructuralView(view, writable=False)
        segment = encoded.segments[0]
        segment.role = 2
        segment.owner = source_owner
        segment.source = source
        segment.posting_mode = 2
        segment.root_ids = _uint_rows(4, 1)
        view.view = lambda _view_type, **_options: encoded  # type: ignore[method-assign]

        self.assertEqual(
            select_ingestion(
                view,
                selected_backend="native",
                native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                core_module=_core(),
            ).path,
            "encoded-native",
        )
        segment.root_ids = _uint_rows(4, 2)

        with self.assertRaisesRegex(SnapshotCompatibilityError, "in-range references"):
            select_ingestion(
                view,
                selected_backend="native",
                native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                core_module=_core(),
            )

    def test_public_owner_limits_bound_column_and_segment_scans(self) -> None:
        cases = {
            "column-rows": ("max_index_rows", 0, _one_node_buffers()),
            "segment-metadata": (
                "max_index_bytes",
                8,
                _EncodedStructuralView(
                    _View(schemas={ENCODED_SCHEMA_NAME: 1}), writable=False
                ).buffers,
            ),
        }
        for label, (limit_name, allowed, buffers) in cases.items():
            with self.subTest(label):
                view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
                view.load_options = SimpleNamespace(  # type: ignore[attr-defined]
                    limits=SimpleNamespace(**{limit_name: allowed})
                )
                encoded = _EncodedStructuralView(view, writable=False)
                encoded.buffers = buffers
                view.view = (  # type: ignore[method-assign]
                    lambda _view_type, _encoded=encoded, **_options: _encoded
                )

                with self.assertRaisesRegex(
                    SnapshotCompatibilityError,
                    f"public {limit_name}",
                ):
                    select_ingestion(
                        view,
                        selected_backend="native",
                        native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                        core_module=_core(),
                    )

    def test_encoded_lease_keeps_the_exact_owner_alive(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        reference = weakref.ref(view)
        decision = select_ingestion(
            view,
            selected_backend="native",
            native_features=frozenset({ENCODED_NATIVE_FEATURE}),
            core_module=_core(),
        )
        del view
        gc.collect()
        self.assertIsNotNone(reference())
        del decision
        gc.collect()
        self.assertIsNone(reference())

    def test_advertised_schema_requires_the_public_core_type(self) -> None:
        view = _View(schemas={ENCODED_SCHEMA_NAME: 1})
        with self.assertRaisesRegex(SnapshotCompatibilityError, "public view type"):
            select_ingestion(
                view,
                selected_backend="native",
                native_features=frozenset({ENCODED_NATIVE_FEATURE}),
                core_module=SimpleNamespace(AxiomScope=SimpleNamespace(CLOSURE="closure")),
            )

    def test_projector_fails_before_scalar_traversal_on_broken_advertisement(self) -> None:
        view = fixture_view("equivalence-ordering")
        cast(Any, view).capabilities = SimpleNamespace(
            adapter_protocol=1,
            model_schema=1,
            wire_format=(1, 1),
            encoded_view_schemas={ENCODED_SCHEMA_NAME: 1},
        )
        selection = BackendSelection("native", "native")
        with (
            patch(
                "pyowl2vec_star_projector.api.select_backend",
                return_value=selection,
            ),
            patch(
                "pyowl2vec_star_projector.api._activate_selection",
                return_value=(
                    selection,
                    "test-native",
                    frozenset({ENCODED_NATIVE_FEATURE}),
                ),
            ),
        ):
            iterator = Projector().iter_edges(
                view,
                options=ProjectionOptions(backend="native"),
            )
            with self.assertRaisesRegex(SnapshotCompatibilityError, "OntologyView.view"):
                next(iterator)
        self.assertEqual(view.iterated_identities, [])


if __name__ == "__main__":
    unittest.main()
