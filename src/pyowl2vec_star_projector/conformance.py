"""Published consumer-conformance kit for shared-snapshot integrations.

The kit intentionally operates at the provider boundary.  A consumer receives a provider that
can return the expected ontology view but raises on path, stream, or origin access.  Successful
verification therefore proves one coercion, one provider call, exact view identity, no source
reparse attempt, unchanged ontology state, fixture-golden parity, and provenance agreement.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Literal, NoReturn, Protocol, cast

from ._version import CONSUMER_CONFORMANCE_SCHEMA, REFERENCE_PROFILE
from .api import Projector, _coerce_once, _core_provenance
from .artifact import edge_json_record
from .errors import ConsumerConformanceError
from .model import Edge
from .options import BACKENDS, Backend, ProjectionOptions
from .provenance import CoreProvenance, ProjectionReport

ConsumerOperation = Literal["owl2vec-star", "asserted-taxonomy"]
IdentityProbe = Callable[[], object]

_RESOURCE_DIRECTORY = "conformance_data"
_GOLDEN_RESOURCE = "goldens.json"


class _Resource(Protocol):
    def joinpath(self, *descendants: str) -> _Resource: ...

    def read_bytes(self) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ConsumerFixture:
    """Identity and content contract for the packaged CC0 fixture."""

    resource: str
    document_iri: str
    sha256: str
    axiom_count: int
    signature_count: int
    structural_fingerprint: str
    logical_fingerprint: str
    signature_fingerprint: str

    def __post_init__(self) -> None:
        if not self.resource or not self.document_iri:
            raise ConsumerConformanceError("fixture resource and document IRI must be nonempty")
        for name in (
            "sha256",
            "structural_fingerprint",
            "logical_fingerprint",
            "signature_fingerprint",
        ):
            _require_sha256(getattr(self, name), name)
        for name in ("axiom_count", "signature_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ConsumerConformanceError(f"fixture {name} must be a nonnegative int")


@dataclass(frozen=True, slots=True)
class ConsumerConformanceCase:
    """One immutable Exact-compatible semantic golden."""

    case_id: str
    operation: ConsumerOperation
    include_literals: bool
    bidirectional: bool
    edges: tuple[Edge, ...]
    canonical_edges_sha256: str

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ConsumerConformanceError("consumer case ID must be nonempty")
        if self.operation not in ("owl2vec-star", "asserted-taxonomy"):
            raise ConsumerConformanceError(f"unsupported consumer operation {self.operation!r}")
        if type(self.include_literals) is not bool or type(self.bidirectional) is not bool:
            raise ConsumerConformanceError("consumer case booleans must be bool")
        if self.operation == "asserted-taxonomy" and self.include_literals:
            raise ConsumerConformanceError("taxonomy cases cannot include literals")
        if self.edges != tuple(sorted(self.edges, key=Edge.canonical_key)):
            raise ConsumerConformanceError(f"case {self.case_id!r} edges are not canonical")
        if len(set(self.edges)) != len(self.edges):
            raise ConsumerConformanceError(f"case {self.case_id!r} edges are not unique")
        _require_sha256(self.canonical_edges_sha256, "canonical_edges_sha256")
        if canonical_edges_sha256(self.edges) != self.canonical_edges_sha256:
            raise ConsumerConformanceError(f"case {self.case_id!r} edge digest is inconsistent")

    def projection_options(self, backend: Backend) -> ProjectionOptions:
        if self.operation != "owl2vec-star":
            raise ConsumerConformanceError("taxonomy cases do not have ProjectionOptions")
        return ProjectionOptions(
            profile=REFERENCE_PROFILE,
            include_literals=self.include_literals,
            duplicates="unique",
            order="canonical",
            compatibility_state="isolated",
            backend=backend,
        )


@dataclass(frozen=True, slots=True)
class ConsumerConformanceResult:
    """Serializable evidence returned only after every assertion passes."""

    case: ConsumerConformanceCase
    requested_backend: Backend
    provider_calls: int
    source_accesses: int
    axiom_count_before: int
    axiom_count_after: int
    signature_count_before: int
    signature_count_after: int
    canonical_edges_sha256: str
    core_before: CoreProvenance
    core_after: CoreProvenance
    preserved_identity_probes: tuple[str, ...]
    report: ProjectionReport | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": CONSUMER_CONFORMANCE_SCHEMA,
            "passed": True,
            "case_id": self.case.case_id,
            "operation": self.case.operation,
            "requested_backend": self.requested_backend,
            "provider_calls": self.provider_calls,
            "source_accesses": self.source_accesses,
            "snapshot_identity_preserved": True,
            "axiom_count_before": self.axiom_count_before,
            "axiom_count_after": self.axiom_count_after,
            "signature_count_before": self.signature_count_before,
            "signature_count_after": self.signature_count_after,
            "edge_count": len(self.case.edges),
            "canonical_edges_sha256": self.canonical_edges_sha256,
            "core_before": asdict(self.core_before),
            "core_after": asdict(self.core_after),
            "preserved_identity_probes": list(self.preserved_identity_probes),
            "report": None if self.report is None else self.report.to_dict(),
        }


class SnapshotProviderProbe:
    """One-view provider that instruments and forbids source-level fallback.

    Pass this object where the consumer normally accepts its ontology source.  ``owl_snapshot``
    is the only successful access.  Path, stream, and origin access are counted and fail with a
    typed assertion, making an accidental source reparse visible immediately.
    """

    __slots__ = ("_lock", "_provider_calls", "_snapshot", "_source_accesses")

    def __init__(self, snapshot: object) -> None:
        self._snapshot = snapshot
        self._lock = threading.Lock()
        self._provider_calls = 0
        self._source_accesses = 0

    @property
    def snapshot(self) -> object:
        return self._snapshot

    @property
    def provider_calls(self) -> int:
        with self._lock:
            return self._provider_calls

    @property
    def source_accesses(self) -> int:
        with self._lock:
            return self._source_accesses

    def owl_snapshot(self) -> object:
        with self._lock:
            self._provider_calls += 1
        return self._snapshot

    def __fspath__(self) -> str:
        self._deny_source_access("path protocol")

    def read(self, *_args: object, **_kwargs: object) -> bytes:
        self._deny_source_access("stream read")

    def read_bytes(self) -> bytes:
        self._deny_source_access("path read_bytes")

    def open(self, *_args: object, **_kwargs: object) -> object:
        self._deny_source_access("path open")

    @property
    def path(self) -> str:
        self._deny_source_access("path attribute")

    @property
    def origin(self) -> str:
        self._deny_source_access("origin attribute")

    def _deny_source_access(self, channel: str) -> NoReturn:
        with self._lock:
            self._source_accesses += 1
        raise ConsumerConformanceError(
            "consumer attempted to access an ontology source instead of the shared snapshot",
            details={"channel": channel},
        )


@dataclass(frozen=True, slots=True)
class _ConformanceBundle:
    fixture: ConsumerFixture
    cases: tuple[ConsumerConformanceCase, ...]


def consumer_conformance_fixture() -> bytes:
    """Return the packaged CC0 Functional Syntax fixture bytes."""
    bundle = _bundle()
    payload = _resource(bundle.fixture.resource).read_bytes()
    if hashlib.sha256(payload).hexdigest() != bundle.fixture.sha256:
        raise ConsumerConformanceError("packaged conformance fixture digest changed")
    return payload


def consumer_conformance_fixture_metadata() -> ConsumerFixture:
    return _bundle().fixture


def consumer_conformance_cases() -> tuple[ConsumerConformanceCase, ...]:
    return _bundle().cases


def consumer_conformance_case(case_id: str) -> ConsumerConformanceCase:
    if not isinstance(case_id, str) or not case_id:
        raise ConsumerConformanceError("case_id must be a nonempty str")
    for case in _bundle().cases:
        if case.case_id == case_id:
            return case
    raise ConsumerConformanceError(
        f"unknown consumer conformance case {case_id!r}",
        details={"case_id": case_id},
    )


def canonical_edges_sha256(edges: Sequence[Edge]) -> str:
    """Hash an already ordered edge sequence using portable artifact records."""
    digest = hashlib.sha256()
    for edge in edges:
        if not isinstance(edge, Edge):
            raise TypeError("edges must contain Edge values")
        digest.update(edge_json_record(edge))
    return digest.hexdigest()


def verify_consumer_conformance(
    view: object,
    *,
    case_id: str = "exact-owl2vec",
    backend: Backend = "python",
    identity_probes: Mapping[str, IdentityProbe] | None = None,
) -> ConsumerConformanceResult:
    """Verify one shared view against a packaged Exact-compatible golden.

    ``identity_probes`` may expose consumer-owned lazy indexes or hierarchy/label views. Each
    callable is evaluated immediately before and after projection and must return the same object.
    The mapping is diagnostic only; object IDs are never serialized.
    """
    if backend not in BACKENDS:
        raise ConsumerConformanceError(
            f"unsupported conformance backend {backend!r}",
            details={"backend": str(backend)},
        )
    case = consumer_conformance_case(case_id)
    fixture = consumer_conformance_fixture_metadata()
    probes = _normalize_identity_probes(identity_probes)
    state_before = _capture_view_state(view)
    observed_before = tuple((name, probe()) for name, probe in probes)
    provider = SnapshotProviderProbe(view)
    projector = Projector()
    checked, source_kind = _coerce_once(provider, load_options=None, resolver=None)
    report: ProjectionReport | None
    if case.operation == "owl2vec-star":
        options = case.projection_options(backend)
        edges = tuple(projector._iter_view(checked, options, source_kind=source_kind))
        report = projector.last_report
    else:
        edges = tuple(
            projector.iter_taxonomy_edges(
                checked,
                bidirectional=case.bidirectional,
                duplicates="unique",
                order="canonical",
                backend=backend,
            )
        )
        report = None
    state_after = _capture_view_state(view)
    observed_after = tuple((name, probe()) for name, probe in probes)
    digest = canonical_edges_sha256(edges)

    failures: list[str] = []
    if checked is not view or projector.last_view is not view:
        failures.append("projector did not retain the exact supplied view identity")
    if source_kind != "provider":
        failures.append(f"source kind was {source_kind!r}, expected 'provider'")
    if provider.provider_calls != 1:
        failures.append(f"provider called {provider.provider_calls} times, expected once")
    if provider.source_accesses:
        failures.append(f"source accessed {provider.source_accesses} times")
    if state_before.core != state_after.core:
        failures.append("core fingerprints or versions changed during projection")
    if state_before.axiom_count != state_after.axiom_count:
        failures.append("axiom count changed during projection")
    if state_before.signature_count != state_after.signature_count:
        failures.append("signature count changed during projection")
    if state_before.axiom_count != fixture.axiom_count:
        failures.append(f"fixture axiom count {state_before.axiom_count} != {fixture.axiom_count}")
    if state_before.signature_count != fixture.signature_count:
        failures.append(
            f"fixture signature count {state_before.signature_count} != {fixture.signature_count}"
        )
    expected_fingerprints = (
        fixture.structural_fingerprint,
        fixture.logical_fingerprint,
        fixture.signature_fingerprint,
    )
    observed_fingerprints = (
        state_before.core.structural_fingerprint,
        state_before.core.logical_fingerprint,
        state_before.core.signature_fingerprint,
    )
    if observed_fingerprints != expected_fingerprints:
        failures.append("fixture core fingerprints differ from the packaged contract")
    if edges != case.edges:
        failures.append("ordered edge sequence differs from the packaged golden")
    if digest != case.canonical_edges_sha256:
        failures.append("canonical edge digest differs from the packaged golden")
    for (before_name, before_value), (after_name, after_value) in zip(
        observed_before,
        observed_after,
        strict=True,
    ):
        if before_name != after_name or before_value is not after_value:
            failures.append(f"identity probe {before_name!r} changed object")
    if case.operation == "owl2vec-star":
        if report is None:
            failures.append("OWL2Vec* projection completed without provenance")
        else:
            provenance = report.provenance
            if provenance.source_kind != "provider":
                failures.append("provenance did not record provider handoff")
            if provenance.core != state_before.core:
                failures.append("provenance core record differs from the supplied view")
            if provenance.options != case.projection_options(backend):
                failures.append("provenance options differ from the Exact-compatible case")
            counts = provenance.counts
            if counts.edges != len(case.edges):
                failures.append("provenance edge count differs from the golden")
            if counts.duplicates or counts.skipped_axioms or counts.ignored_shapes:
                failures.append("fixture provenance contains unexpected compiler counts")
    if failures:
        raise ConsumerConformanceError(
            f"consumer conformance failed for {case.case_id}: " + "; ".join(failures),
            details={"case_id": case.case_id, "failure_count": len(failures)},
        )
    return ConsumerConformanceResult(
        case=case,
        requested_backend=backend,
        provider_calls=provider.provider_calls,
        source_accesses=provider.source_accesses,
        axiom_count_before=state_before.axiom_count,
        axiom_count_after=state_after.axiom_count,
        signature_count_before=state_before.signature_count,
        signature_count_after=state_after.signature_count,
        canonical_edges_sha256=digest,
        core_before=state_before.core,
        core_after=state_after.core,
        preserved_identity_probes=tuple(name for name, _value in observed_before),
        report=report,
    )


@dataclass(frozen=True, slots=True)
class _ViewState:
    core: CoreProvenance
    axiom_count: int
    signature_count: int


def _capture_view_state(view: object) -> _ViewState:
    iter_axioms = getattr(view, "iter_axioms", None)
    signature = getattr(view, "signature", None)
    if not callable(iter_axioms) or not callable(signature):
        raise ConsumerConformanceError("view does not expose iterable axioms and signature")
    return _ViewState(
        _core_provenance(view),
        sum(1 for _item in iter_axioms()),
        len(tuple(signature())),
    )


def _normalize_identity_probes(
    value: Mapping[str, IdentityProbe] | None,
) -> tuple[tuple[str, IdentityProbe], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise TypeError("identity_probes must be a mapping or None")
    result: list[tuple[str, IdentityProbe]] = []
    for name, probe in value.items():
        if not isinstance(name, str) or not name:
            raise ConsumerConformanceError("identity probe names must be nonempty strings")
        if not callable(probe):
            raise ConsumerConformanceError(f"identity probe {name!r} is not callable")
        result.append((name, probe))
    return tuple(sorted(result, key=lambda item: item[0]))


@lru_cache(maxsize=1)
def _bundle() -> _ConformanceBundle:
    payload = _resource(_GOLDEN_RESOURCE).read_bytes()
    raw = _mapping(json.loads(payload), "golden document")
    schema = _string(raw.get("schema"), "schema")
    if schema != CONSUMER_CONFORMANCE_SCHEMA:
        raise ConsumerConformanceError(
            f"unsupported consumer conformance schema {schema!r}",
            details={"schema": schema},
        )
    fixture_raw = _mapping(raw.get("fixture"), "fixture")
    fixture = ConsumerFixture(
        resource=_string(fixture_raw.get("resource"), "fixture.resource"),
        document_iri=_string(fixture_raw.get("document_iri"), "fixture.document_iri"),
        sha256=_string(fixture_raw.get("sha256"), "fixture.sha256"),
        axiom_count=_integer(fixture_raw.get("axiom_count"), "fixture.axiom_count"),
        signature_count=_integer(fixture_raw.get("signature_count"), "fixture.signature_count"),
        structural_fingerprint=_string(
            fixture_raw.get("structural_fingerprint"), "fixture.structural_fingerprint"
        ),
        logical_fingerprint=_string(
            fixture_raw.get("logical_fingerprint"), "fixture.logical_fingerprint"
        ),
        signature_fingerprint=_string(
            fixture_raw.get("signature_fingerprint"), "fixture.signature_fingerprint"
        ),
    )
    fixture_payload = _resource(fixture.resource).read_bytes()
    if hashlib.sha256(fixture_payload).hexdigest() != fixture.sha256:
        raise ConsumerConformanceError("consumer fixture does not match its recorded digest")
    cases_raw = _list(raw.get("cases"), "cases")
    cases: list[ConsumerConformanceCase] = []
    for index, item in enumerate(cases_raw):
        case_raw = _mapping(item, f"cases[{index}]")
        operation = _string(case_raw.get("operation"), f"cases[{index}].operation")
        if operation not in ("owl2vec-star", "asserted-taxonomy"):
            raise ConsumerConformanceError(f"unsupported consumer operation {operation!r}")
        edge_values = _list(case_raw.get("edges"), f"cases[{index}].edges")
        edges: list[Edge] = []
        for edge_index, edge_value in enumerate(edge_values):
            parts = _list(edge_value, f"cases[{index}].edges[{edge_index}]")
            if len(parts) != 3:
                raise ConsumerConformanceError("consumer golden edge must have three strings")
            edges.append(
                Edge(
                    _string(parts[0], "edge source"),
                    _string(parts[1], "edge relation"),
                    _string(parts[2], "edge destination"),
                )
            )
        expected_count = _integer(case_raw.get("edge_count"), f"cases[{index}].edge_count")
        if expected_count != len(edges):
            raise ConsumerConformanceError("consumer golden edge_count is inconsistent")
        cases.append(
            ConsumerConformanceCase(
                case_id=_string(case_raw.get("case_id"), f"cases[{index}].case_id"),
                operation=cast(ConsumerOperation, operation),
                include_literals=_boolean(
                    case_raw.get("include_literals"), f"cases[{index}].include_literals"
                ),
                bidirectional=_boolean(
                    case_raw.get("bidirectional"), f"cases[{index}].bidirectional"
                ),
                edges=tuple(edges),
                canonical_edges_sha256=_string(
                    case_raw.get("canonical_edges_sha256"),
                    f"cases[{index}].canonical_edges_sha256",
                ),
            )
        )
    identifiers = tuple(case.case_id for case in cases)
    if identifiers != tuple(sorted(set(identifiers))):
        raise ConsumerConformanceError("consumer conformance case IDs must be sorted and unique")
    return _ConformanceBundle(fixture, tuple(cases))


def _resource(name: str) -> _Resource:
    selected = files("pyowl2vec_star_projector").joinpath(_RESOURCE_DIRECTORY).joinpath(name)
    return cast(_Resource, selected)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ConsumerConformanceError(f"{name} must be a string-keyed object")
    return cast(Mapping[str, object], value)


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ConsumerConformanceError(f"{name} must be a list")
    return cast(list[object], value)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConsumerConformanceError(f"{name} must be a nonempty string")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ConsumerConformanceError(f"{name} must be a nonnegative int")
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ConsumerConformanceError(f"{name} must be bool")
    return value


def _require_sha256(value: object, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ConsumerConformanceError(f"{name} must be a lowercase SHA-256 hex digest")
    try:
        decoded = bytes.fromhex(value)
    except ValueError as error:
        raise ConsumerConformanceError(f"{name} must be a lowercase SHA-256 hex digest") from error
    if len(decoded) != 32 or value != value.lower():
        raise ConsumerConformanceError(f"{name} must be a lowercase SHA-256 hex digest")


__all__ = [
    "CONSUMER_CONFORMANCE_SCHEMA",
    "ConsumerConformanceCase",
    "ConsumerConformanceResult",
    "ConsumerFixture",
    "IdentityProbe",
    "SnapshotProviderProbe",
    "canonical_edges_sha256",
    "consumer_conformance_case",
    "consumer_conformance_cases",
    "consumer_conformance_fixture",
    "consumer_conformance_fixture_metadata",
    "verify_consumer_conformance",
]
