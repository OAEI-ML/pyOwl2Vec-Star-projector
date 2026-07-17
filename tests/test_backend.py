from __future__ import annotations

import unittest
import warnings
from types import SimpleNamespace
from unittest.mock import patch

from pyowl2vec_star_projector import (
    InvalidProjectionOptionsError,
    NativeBackendFallbackWarning,
    NativeBackendStatus,
    NativeBackendUnavailableError,
    backend,
    select_backend,
)


class BackendSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        backend._fallback_warning_emitted = False

    def test_python_is_explicit_and_does_not_probe(self) -> None:
        def fail_probe() -> NativeBackendStatus:
            raise AssertionError("explicit Python must not probe native")

        selected = select_backend("python", probe=fail_probe)
        self.assertEqual(selected.selected, "python")
        self.assertIsNone(selected.fallback_reason)

    def test_auto_selects_available_native(self) -> None:
        selected = select_backend("auto", probe=lambda: NativeBackendStatus(True, "test-native"))
        self.assertEqual(selected.selected, "native")

    def test_auto_respects_experimental_opt_in_policy(self) -> None:
        selected = select_backend(
            "auto",
            probe=lambda: NativeBackendStatus(
                True,
                "test-native",
                reason="performance gate pending",
                auto_preferred=False,
            ),
        )
        self.assertEqual(selected.selected, "python")
        self.assertEqual(selected.fallback_reason, "performance gate pending")
        explicit = select_backend(
            "native",
            probe=lambda: NativeBackendStatus(True, auto_preferred=False),
        )
        self.assertEqual(explicit.selected, "native")

    def test_explicit_native_fails_closed(self) -> None:
        with self.assertRaises(NativeBackendUnavailableError):
            select_backend("native", probe=lambda: NativeBackendStatus(False, reason="absent"))

    def test_invalid_backend_is_rejected_before_a_probe(self) -> None:
        with self.assertRaises(InvalidProjectionOptionsError):
            select_backend("gpu")  # type: ignore[arg-type]

    def test_native_import_and_metadata_failures_are_typed(self) -> None:
        import pyowl2vec_star_projector.native as native

        with patch.object(native.importlib, "import_module", side_effect=RuntimeError("broken")):
            with self.assertRaises(NativeBackendUnavailableError) as raised:
                native.load_native_module()
        self.assertEqual(raised.exception.details["cause"], "RuntimeError")

        malformed = SimpleNamespace(
            NATIVE_API_VERSION="one",
            EdgeBatchProcessor=lambda: None,
        )
        with patch.object(native.importlib, "import_module", return_value=malformed):
            with self.assertRaises(NativeBackendUnavailableError) as raised:
                native.load_native_module()
        self.assertEqual(raised.exception.details["actual_native_api"], -1)

    def test_native_runtime_policy_blocks_unsupported_interpreters_before_import(self) -> None:
        import pyowl2vec_star_projector.backend as backend
        import pyowl2vec_star_projector.native as native

        reason = "PyO3 native extension does not support CPython subinterpreters"
        with (
            patch.object(backend, "native_runtime_policy_reason", return_value=reason),
            patch.object(backend.importlib_util, "find_spec") as find_spec,
        ):
            status = backend.probe_native_backend()
        self.assertFalse(status.available)
        self.assertEqual(status.reason, reason)
        find_spec.assert_not_called()

        with (
            patch.object(native, "native_runtime_policy_reason", return_value=reason),
            patch.object(native.importlib, "import_module") as import_module,
        ):
            with self.assertRaisesRegex(NativeBackendUnavailableError, "subinterpreters"):
                native.load_native_module()
        import_module.assert_not_called()

    def test_auto_warning_is_deferred_and_once(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            selected = select_backend(
                "auto", probe=lambda: NativeBackendStatus(False, reason="absent")
            )
            self.assertEqual(caught, [])
            backend.warn_if_auto_fallback(selected)
            backend.warn_if_auto_fallback(selected)
        self.assertEqual(len(caught), 1)
        self.assertIs(caught[0].category, NativeBackendFallbackWarning)


if __name__ == "__main__":
    unittest.main()
