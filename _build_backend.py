"""PEP 517 adapter that keeps Rust tooling out of fallback builds.

The normal source-distribution and wheel paths delegate to setuptools with only
the static build requirements in ``pyproject.toml``.  An explicitly requested
native build advertises setuptools-rust as an additional, isolated build
requirement before setup.py is evaluated.  Cargo itself remains an external
native-build prerequisite and is never probed for fallback artifacts.
"""

from __future__ import annotations

import gzip
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from setuptools import build_meta as _setuptools

_NATIVE_REQUIREMENT = "setuptools-rust==1.13.0"
_VALID_MODES = frozenset({"auto", "0", "1"})


def _native_requested() -> bool:
    value = os.environ.get("PYOWL2VEC_BUILD_NATIVE", "auto")
    if value not in _VALID_MODES:
        expected = "auto, 0, or 1"
        raise RuntimeError(f"PYOWL2VEC_BUILD_NATIVE must be {expected}; got {value!r}")
    return value == "1"


def get_requires_for_build_wheel(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    if _native_requested():
        return [_NATIVE_REQUIREMENT]
    return _setuptools.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_editable(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    if _native_requested():
        return [_NATIVE_REQUIREMENT]
    return _setuptools.get_requires_for_build_editable(config_settings)


build_wheel = _setuptools.build_wheel
build_editable = _setuptools.build_editable
prepare_metadata_for_build_wheel = _setuptools.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _setuptools.prepare_metadata_for_build_editable


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Build and normalize archive headers when SOURCE_DATE_EPOCH is supplied."""
    filename = _setuptools.build_sdist(sdist_directory, config_settings)
    epoch_text = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch_text is None:
        return filename
    try:
        epoch = int(epoch_text)
    except ValueError as error:
        raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer") from error
    if epoch < 0:
        raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer")
    _normalize_sdist(Path(sdist_directory) / filename, epoch)
    return filename


def _normalize_sdist(path: Path, epoch: int) -> None:
    """Make gzip and tar metadata deterministic without extracting archive paths."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            with tarfile.open(path, "r:gz") as source:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=output, mtime=epoch
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                    ) as target:
                        for member in source.getmembers():
                            member.mtime = epoch
                            member.uid = 0
                            member.gid = 0
                            member.uname = ""
                            member.gname = ""
                            if member.isdir():
                                member.mode = 0o755
                            elif member.isfile():
                                member.mode = 0o755 if member.mode & 0o111 else 0o644
                            else:
                                member.mode = 0o777
                            member.pax_headers = {
                                key: value
                                for key, value in member.pax_headers.items()
                                if key not in {"atime", "ctime", "mtime"}
                            }
                            stream = source.extractfile(member) if member.isfile() else None
                            target.addfile(member, stream)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
