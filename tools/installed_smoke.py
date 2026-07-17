#!/usr/bin/env python3
"""Installed-package smoke that needs no fixture files, compiler, Java, or network."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import warnings
from dataclasses import dataclass

import pyowl_core

from pyowl2vec_star_projector import (
    CONSUMER_CONFORMANCE_SCHEMA,
    NativeBackendFallbackWarning,
    ProjectionOptions,
    Projector,
    __version__,
    consumer_conformance_cases,
    consumer_conformance_fixture,
    consumer_conformance_fixture_metadata,
    probe_native_backend,
)

_FORBIDDEN_EXECUTABLES = ("cargo", "java", "javac", "robot")
_FORBIDDEN_MODULES = ("deeponto", "jpype", "mowl", "owlapi")


@dataclass(frozen=True, slots=True)
class _Capabilities:
    adapter_protocol: int = 1
    model_schema: int = 1
    wire_format: tuple[int, int] = (1, 0)
    features: frozenset[str] = frozenset({"complete-model"})
    backend: str = "python"


class _EmptyView:
    capabilities = _Capabilities()
    structural_fingerprint = "0" * 64
    logical_fingerprint = "0" * 64
    signature_fingerprint = "0" * 64

    def iter_axioms(self, axiom_type: object = None, *, scope: object = "closure") -> object:
        del axiom_type, scope
        return iter(())

    def signature(
        self,
        kind: object = None,
        *,
        scope: object = "closure",
        include_builtins: bool = True,
    ) -> tuple[object, ...]:
        del kind, scope, include_builtins
        return ()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-tools-absent", action="store_true")
    parser.add_argument("--require-native", action="store_true")
    args = parser.parse_args()
    if args.require_tools_absent:
        present = [name for name in _FORBIDDEN_EXECUTABLES if shutil.which(name)]
        if present:
            raise RuntimeError(f"forbidden executables visible to smoke process: {present}")
    importable = [name for name in _FORBIDDEN_MODULES if importlib.util.find_spec(name)]
    if importable:
        raise RuntimeError(f"forbidden Java-facing modules installed: {importable}")
    view = _EmptyView()
    projector = Projector()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert projector.project(view, options=ProjectionOptions(backend="python")) == []
    assert not caught
    assert projector.last_view is view
    assert hasattr(pyowl_core, "__version__")
    assert __version__
    fixture = consumer_conformance_fixture()
    fixture_metadata = consumer_conformance_fixture_metadata()
    assert len(fixture) > 100
    assert fixture_metadata.resource == "consumer.ofn"
    assert len(consumer_conformance_cases()) == 3
    assert CONSUMER_CONFORMANCE_SCHEMA.endswith("/1")
    if args.require_native:
        status = probe_native_backend()
        if not status.available:
            raise RuntimeError(f"native backend unavailable: {status.reason}")
        assert Projector().project(view, options=ProjectionOptions(backend="native")) == []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert Projector().project(view, options=ProjectionOptions(backend="auto")) == []
    fallback = [item for item in caught if item.category is NativeBackendFallbackWarning]
    assert len(fallback) == 1
    print(f"installed smoke OK: projector={__version__}, core={pyowl_core.__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
