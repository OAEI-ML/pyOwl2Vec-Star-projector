from __future__ import annotations

import unittest
import warnings

from pyowl2vec_star_projector import (
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
