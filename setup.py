"""Build the optional Rust accelerator without making Cargo a runtime requirement."""

from __future__ import annotations

import os
from pathlib import Path

from setuptools import setup

_VALID_MODES = frozenset({"auto", "0", "1"})


def _native_mode() -> str:
    value = os.environ.get("PYOWL2VEC_BUILD_NATIVE", "auto")
    if value not in _VALID_MODES:
        expected = "auto, 0, or 1"
        raise RuntimeError(f"PYOWL2VEC_BUILD_NATIVE must be {expected}; got {value!r}")
    return value


mode = _native_mode()
manifest = Path("native/Cargo.toml")
rust_extensions = []

if mode == "1" and manifest.is_file():
    from setuptools_rust import Binding, RustExtension

    rust_extensions.append(
        RustExtension(
            "pyowl2vec_star_projector._native",
            path=str(manifest),
            binding=Binding.PyO3,
            optional=False,
            py_limited_api=True,
            cargo_manifest_args=("--locked",),
        )
    )
elif mode == "1":
    raise RuntimeError("native build requested but native/Cargo.toml is missing")

wheel_options = {"bdist_wheel": {"py_limited_api": "cp310"}} if mode == "1" else {}
setup(rust_extensions=rust_extensions, options=wheel_options, zip_safe=False)
