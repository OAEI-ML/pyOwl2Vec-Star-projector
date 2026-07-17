"""Backend discovery and selection seam; no projection engine lives here."""

from __future__ import annotations

import importlib
import importlib.util as importlib_util
import platform
import sysconfig
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from .errors import (
    InvalidProjectionOptionsError,
    NativeBackendFallbackWarning,
    NativeBackendUnavailableError,
)
from .options import BACKENDS, Backend

SelectedBackend = Literal["native", "python"]


class _Subinterpreters(Protocol):
    def get_current(self) -> int: ...

    def get_main(self) -> int: ...


# P3 ships the exact native engine as opt-in until the documented multi-corpus
# 2x end-to-end threshold is independently reproduced. Availability and auto
# preference are deliberately separate facts.
NATIVE_AUTO_PREFERRED = False
NATIVE_EXPERIMENTAL_REASON = (
    "native accelerator is installed but remains opt-in pending the P3 throughput gate"
)


@dataclass(frozen=True, slots=True)
class NativeBackendStatus:
    available: bool
    implementation_version: str | None = None
    reason: str | None = None
    auto_preferred: bool = True


@dataclass(frozen=True, slots=True)
class BackendSelection:
    requested: Backend
    selected: SelectedBackend
    fallback_reason: str | None = None


def probe_native_backend() -> NativeBackendStatus:
    """Inspect availability without importing or executing the extension."""
    policy_reason = native_runtime_policy_reason()
    if policy_reason is not None:
        return NativeBackendStatus(False, reason=policy_reason, auto_preferred=False)
    try:
        spec = importlib_util.find_spec("pyowl2vec_star_projector._native")
    except (ImportError, AttributeError, ValueError) as exc:
        return NativeBackendStatus(False, reason=f"probe failed: {exc}")
    if spec is None:
        return NativeBackendStatus(False, reason="native extension is not installed")
    return NativeBackendStatus(
        True,
        reason=None if NATIVE_AUTO_PREFERRED else NATIVE_EXPERIMENTAL_REASON,
        auto_preferred=NATIVE_AUTO_PREFERRED,
    )


def native_runtime_policy_reason() -> str | None:
    """Return why this interpreter must not import the PyO3 accelerator."""
    if platform.python_implementation() != "CPython":
        return "native extension is supported only on approved CPython builds"
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        return "native extension is not approved for free-threaded CPython"
    try:
        interpreters = cast(
            _Subinterpreters,
            importlib.import_module("_xxsubinterpreters"),
        )
        current = int(interpreters.get_current())
        main = int(interpreters.get_main())
    except (ImportError, AttributeError, RuntimeError, ValueError):
        return None
    if current != main:
        return "PyO3 native extension does not support CPython subinterpreters"
    return None


def select_backend(
    requested: Backend,
    *,
    probe: Callable[[], NativeBackendStatus] | None = None,
) -> BackendSelection:
    """Select a whole-operation backend without warning or doing semantic work."""
    if not isinstance(requested, str) or requested not in BACKENDS:
        raise InvalidProjectionOptionsError(
            f"backend must be one of auto, native, python; got {requested!r}"
        )
    if requested == "python":
        return BackendSelection(requested, "python")
    status = (probe or probe_native_backend)()
    if status.available and (requested == "native" or status.auto_preferred):
        return BackendSelection(requested, "native")
    if requested == "native":
        raise NativeBackendUnavailableError(status.reason or "native backend is unavailable")
    return BackendSelection(
        requested,
        "python",
        status.reason or NATIVE_EXPERIMENTAL_REASON,
    )


_warning_lock = threading.Lock()
_fallback_warning_emitted = False


def warn_if_auto_fallback(selection: BackendSelection) -> None:
    """Warn once when an actual projection is about to use automatic fallback.

    The future engine calls this at the first projection boundary. Merely importing
    this module or inspecting backend status remains quiet.
    """
    global _fallback_warning_emitted
    if (
        selection.requested != "auto"
        or selection.selected != "python"
        or selection.fallback_reason is None
    ):
        return
    with _warning_lock:
        if _fallback_warning_emitted:
            return
        warnings.warn(
            "native projector not selected; using the complete Python backend "
            f"({selection.fallback_reason}). Select backend='python' to make this "
            "choice explicit and quiet.",
            NativeBackendFallbackWarning,
            stacklevel=2,
        )
        _fallback_warning_emitted = True
