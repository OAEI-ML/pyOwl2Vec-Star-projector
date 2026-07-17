from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from pathlib import Path

from pyowl_core import (
    BackendPreference,
    DocumentFormat,
    LoadOptions,
    OntologyDocument,
    parse_document,
)
from pyowl_core.model import AxiomNode, Entity, EntityKind, canonical_bytes

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "oracle"
GOLDENS = ROOT / "tests" / "goldens" / "mowl-d993536-v1"


@dataclass(frozen=True, slots=True)
class Capabilities:
    adapter_protocol: int = 1
    model_schema: int = 1
    wire_format: tuple[int, int] = (1, 0)
    features: frozenset[str] = frozenset({"complete-model"})
    backend: str = "python"


class ConformingView:
    """Identity view over parsed documents implementing the frozen WP03 protocol."""

    capabilities = Capabilities()

    def __init__(
        self,
        documents: tuple[OntologyDocument, ...],
        *,
        wire_verified: bool = False,
    ) -> None:
        self.documents = documents
        self.root = documents[0]
        self.wire_verified = wire_verified
        digest = hashlib.sha256(
            b"".join(document.document_fingerprint.digest for document in documents)
        ).hexdigest()
        self.structural_fingerprint = digest
        self.logical_fingerprint = digest
        self.signature_fingerprint = digest
        self.iterated_identities: list[int] = []

    def iter_axioms(
        self,
        axiom_type: type[AxiomNode] | None = None,
        *,
        scope: object = "closure",
    ) -> object:
        selected = (self.root,) if _scope_value(scope) == "root" else self.documents
        unique: dict[bytes, AxiomNode] = {}
        for document in selected:
            for axiom in document.iter_axioms(axiom_type):
                unique[canonical_bytes(axiom)] = axiom
        ordered = tuple(unique[key] for key in sorted(unique))
        self.iterated_identities.extend(id(axiom) for axiom in ordered)
        return iter(ordered)

    def signature(
        self,
        kind: EntityKind | None = None,
        *,
        scope: object = "closure",
        include_builtins: bool = True,
    ) -> tuple[Entity, ...]:
        selected = (self.root,) if _scope_value(scope) == "root" else self.documents
        unique: dict[bytes, Entity] = {}
        for document in selected:
            for entity in document.signature(kind, include_builtins=include_builtins):
                unique[canonical_bytes(entity)] = entity
        return tuple(unique[key] for key in sorted(unique))


class Provider:
    def __init__(self, view: ConformingView) -> None:
        self.view = view
        self.calls = 0

    def owl_snapshot(self) -> ConformingView:
        self.calls += 1
        return self.view


_DOCUMENT_CACHE: dict[str, OntologyDocument] = {}


def parse_fixture(relative: str) -> OntologyDocument:
    cached = _DOCUMENT_CACHE.get(relative)
    if cached is not None:
        return cached
    path = FIXTURES / relative
    format = DocumentFormat.TURTLE if path.suffix == ".ttl" else DocumentFormat.FUNCTIONAL
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = parse_document(
            path,
            format=format,
            options=LoadOptions(backend=BackendPreference.PYTHON),
        )
    _DOCUMENT_CACHE[relative] = parsed
    return parsed


_CLOSURES: dict[str, tuple[str, ...]] = {
    "imports-one-level": ("imports/one-root.ofn", "imports/one-leaf.ofn"),
    "imports-diamond": (
        "imports/diamond-root.ofn",
        "imports/diamond-left.ofn",
        "imports/diamond-right.ofn",
        "imports/diamond-common.ofn",
    ),
    "imports-cycle": ("imports/cycle-a.ofn", "imports/cycle-b.ofn"),
}


def fixture_view(fixture_id: str, document: str | None = None) -> ConformingView:
    relative = _CLOSURES.get(fixture_id, (document or f"{fixture_id}.ofn",))
    return ConformingView(tuple(parse_fixture(path) for path in relative))


def _scope_value(scope: object) -> str:
    value = getattr(scope, "value", scope)
    return str(value).lower()


__all__ = [
    "FIXTURES",
    "GOLDENS",
    "Capabilities",
    "ConformingView",
    "Provider",
    "fixture_view",
    "parse_fixture",
]
