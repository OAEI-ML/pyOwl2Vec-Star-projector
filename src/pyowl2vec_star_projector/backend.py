"""Backend discovery and selection seam; no projection engine lives here."""

from __future__ import annotations

import importlib.util
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .errors import NativeBackendFallbackWarning, NativeBackendUnavailableError
from .options import Backend

SelectedBackend = Literal["native", "python"]

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
    try:
        spec = importlib.util.find_spec("pyowl2vec_star_projector._native")
    except (ImportError, AttributeError, ValueError) as exc:
        return NativeBackendStatus(False, reason=f"probe failed: {exc}")
    if spec is None:
        return NativeBackendStatus(False, reason="native extension is not installed")
    return NativeBackendStatus(
        True,
        reason=None if NATIVE_AUTO_PREFERRED else NATIVE_EXPERIMENTAL_REASON,
        auto_preferred=NATIVE_AUTO_PREFERRED,
    )


def select_backend(
    requested: Backend,
    *,
    probe: Callable[[], NativeBackendStatus] | None = None,
) -> BackendSelection:
    """Select a whole-operation backend without warning or doing semantic work."""
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
