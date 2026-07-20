from __future__ import annotations

import gc
import hashlib
import unittest
import weakref
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


class _EncodedStructuralView:
    def __init__(self, owner: object, *, writable: bool) -> None:
        self.schema_name = ENCODED_SCHEMA_NAME
        self.schema_version = 1
        self.model_schema = 1
        self.owner = owner
        self.descriptor = ENCODED_STRUCTURAL_DESCRIPTOR_V1
        self.descriptor_digest = hashlib.sha256(self.descriptor).digest()
        self.structural_fingerprint = "encoded-fingerprint"
        self.buffers = {
            name: memoryview(b"\x00" * (8 if name == "node_field_offsets" else 0))
            for name in ENCODED_BUFFER_WIDTHS
        }
        if writable:
            self.buffers["root_kinds"] = memoryview(bytearray(b"\x00"))


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
        AxiomScope=SimpleNamespace(CLOSURE="closure-token"),
    )


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
