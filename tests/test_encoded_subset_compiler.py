from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from unittest.mock import patch

import pyowl_core
import pytest
from pyowl_core import BackendPreference, ImportPolicy, LoadOptions

from pyowl2vec_star_projector import Edge, ProjectionOptions, Projector
from pyowl2vec_star_projector import api as api_module
from pyowl2vec_star_projector.backend import BackendSelection
from pyowl2vec_star_projector.compiler import prepare_streaming_compilation as scalar_compilation
from pyowl2vec_star_projector.encoded import (
    ENCODED_NATIVE_FEATURE,
    EncodedNegotiation,
    EncodedStructuralLease,
    _validate_encoded_view,
)
from pyowl2vec_star_projector.encoded_compiler import (
    EncodedSubsetCounters,
    prepare_encoded_subset_compilation,
)
from pyowl2vec_star_projector.errors import SnapshotCompatibilityError

ROOT = Path(__file__).resolve().parents[1]


def _snapshot(body: str) -> object:
    source = f"Prefix(:=<urn:slice#>) Ontology(<urn:slice> {body})".encode()
    return pyowl_core.load_snapshot(
        source,
        options=LoadOptions(
            imports=ImportPolicy.IGNORE,
            backend=BackendPreference.PYTHON,
        ),
    )


def _lease(view: object) -> EncodedStructuralLease:
    encoded = view.view(  # type: ignore[attr-defined]
        pyowl_core.EncodedStructuralView,
        schema_version=1,
        scope=pyowl_core.AxiomScope.CLOSURE,
    )
    return _validate_encoded_view(
        view,
        encoded,
        pyowl_core.EncodedStructuralView,
        pyowl_core.AxiomScope.CLOSURE,
    )


def _semantic_report(report: dict[str, object]) -> dict[str, object]:
    normalized = cast(dict[str, Any], json.loads(json.dumps(report)))
    provenance = normalized["provenance"]
    provenance.pop("selected_backend")
    provenance.pop("native_implementation_version")
    provenance.pop("ingestion")
    provenance["options"].pop("backend")
    return cast(dict[str, object], normalized)


@contextmanager
def _forced_encoded(lease: EncodedStructuralLease) -> Iterator[None]:
    selection = BackendSelection("native", "native")

    def passthrough(edges: Iterable[Edge], *, batch_edges: int) -> Iterator[Edge]:
        assert batch_edges > 0
        return iter(edges)

    with (
        patch.object(api_module, "select_backend", return_value=selection),
        patch.object(
            api_module,
            "_activate_selection",
            return_value=(
                selection,
                "test-native",
                frozenset({ENCODED_NATIVE_FEATURE}),
            ),
        ),
        patch.object(
            api_module,
            "select_ingestion",
            return_value=EncodedNegotiation("encoded-native", lease=lease),
        ),
        patch.object(api_module, "iter_native_passthrough", side_effect=passthrough),
    ):
        yield


def test_simple_subset_matches_scalar_without_scalar_axiom_traversal() -> None:
    view = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
        "SubClassOf(:A :B) SubClassOf(:B :C)"
    )
    lease = _lease(view)
    cases = (
        ProjectionOptions(backend="python", order="encounter"),
        ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=True,
            duplicates="unique",
            order="canonical",
        ),
        ProjectionOptions(
            backend="python",
            only_taxonomy=True,
            include_literals=True,
            order="canonical",
        ),
    )
    expected: list[tuple[list[Edge], dict[str, object]]] = []
    for options in cases:
        scalar = Projector()
        edges = scalar.project(view, options=options)
        assert scalar.last_report is not None
        expected.append((edges, scalar.last_report.to_dict()))

    with _forced_encoded(lease), patch.object(
        api_module,
        "prepare_streaming_compilation",
        side_effect=AssertionError("encoded slice crossed scalar axiom traversal"),
    ):
        for options, (scalar_edges, scalar_report) in zip(cases, expected, strict=True):
            projector = Projector()
            encoded_options = replace(options, backend="native")
            actual = list(projector.iter_edges(view, options=encoded_options, buffer_edges=1))

            assert actual == scalar_edges
            assert projector.last_report is not None
            assert projector.last_report.provenance.ingestion.path == "encoded-native"
            assert projector.last_report.provenance.counts.edges == len(actual)
            assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(
                scalar_report
            )
            assert projector.last_encoded_counters == EncodedSubsetCounters(
                roots_inspected=5,
                nodes_inspected=11,
                declaration_axioms=3,
                subclass_axioms=2,
                scalar_bytes_checked=48,
                edge_batches=len(actual),
                raw_edges=len(actual),
                scalar_fallbacks=0,
            )


def test_unsupported_constructor_selects_one_whole_operation_scalar_fallback() -> None:
    view = _snapshot("SubClassOf(:A ObjectSomeValuesFrom(:p :B))")
    lease = _lease(view)
    python_options = ProjectionOptions(backend="python", order="encounter")
    scalar = Projector()
    expected = scalar.project(view, options=python_options)
    assert scalar.last_report is not None
    scalar_report = scalar.last_report.to_dict()
    real_scalar = scalar_compilation

    with _forced_encoded(lease), patch.object(
        api_module,
        "prepare_streaming_compilation",
        wraps=real_scalar,
    ) as scalar_prepare:
        projector = Projector()
        actual = projector.project(
            view,
            options=replace(python_options, backend="native"),
        )

    assert actual == expected
    assert scalar_prepare.call_count == 1
    assert projector.last_report is not None
    assert _semantic_report(projector.last_report.to_dict()) == _semantic_report(scalar_report)
    ingestion = projector.last_report.provenance.ingestion
    assert ingestion.path == "scalar-native"
    assert "whole-operation scalar compiler" in (ingestion.reason or "")
    assert projector.last_encoded_counters is not None
    assert projector.last_encoded_counters.scalar_fallbacks == 1
    assert projector.last_encoded_counters.edge_batches == 0
    assert projector.last_encoded_counters.raw_edges == 0


def test_asserted_taxonomy_api_uses_the_same_bounded_slice() -> None:
    view = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
        "SubClassOf(:A :B) SubClassOf(:B :C)"
    )
    lease = _lease(view)
    expected = Projector().project_taxonomy(
        view,
        bidirectional=True,
        duplicates="unique",
        order="canonical",
        backend="python",
        buffer_edges=2,
    )

    with _forced_encoded(lease):
        projector = Projector()
        actual = projector.project_taxonomy(
            view,
            bidirectional=True,
            duplicates="unique",
            order="canonical",
            backend="native",
            buffer_edges=2,
        )

    assert actual == expected
    assert projector.last_encoded_counters is not None
    assert projector.last_encoded_counters.edge_batches == 2
    assert projector.last_encoded_counters.raw_edges == 4


@pytest.mark.parametrize(
    "corruption",
    ["arity", "entity-kind", "unknown-tag", "root-order"],
)
def test_supported_tag_corruption_fails_before_edge_output(corruption: str) -> None:
    view = _snapshot("Declaration(Class(:A)) SubClassOf(:A :B)")
    lease = _lease(view)
    buffers = dict(lease.buffers)
    if corruption == "arity":
        tags = buffers["node_tags"]
        subclass_id = next(
            index
            for index in range(1, tags.nbytes // 2 + 1)
            if int.from_bytes(tags[(index - 1) * 2 : index * 2], "little") == 61
        )
        offsets = bytearray(buffers["node_field_offsets"])
        end_offset = subclass_id * 8
        end = int.from_bytes(offsets[end_offset : end_offset + 8], "little")
        offsets[end_offset : end_offset + 8] = (end - 1).to_bytes(8, "little")
        buffers["node_field_offsets"] = memoryview(bytes(offsets))
    elif corruption == "entity-kind":
        scalars = bytes(buffers["scalar_bytes"])
        assert b"class" in scalars
        buffers["scalar_bytes"] = memoryview(scalars.replace(b"class", b"xxxxx", 1))
    elif corruption == "unknown-tag":
        tag_bytes = bytearray(buffers["node_tags"])
        tag_bytes[:2] = (999).to_bytes(2, "little")
        buffers["node_tags"] = memoryview(bytes(tag_bytes))
    else:
        root_ids = bytes(buffers["root_ids"])
        assert len(root_ids) == 8
        buffers["root_ids"] = memoryview(root_ids[4:] + root_ids[:4])
    hostile = replace(lease, buffers=MappingProxyType(buffers))

    with pytest.raises(
        SnapshotCompatibilityError,
        match=r"arity|entity kind|frozen schema|canonical",
    ):
        prepare_encoded_subset_compilation(
            view,
            ProjectionOptions(backend="native"),
            EncodedNegotiation("encoded-native", lease=hostile),
            batch_edges=1,
        )


def test_incomplete_slice_is_not_advertised_by_the_native_feature_ledger() -> None:
    native_source = (ROOT / "native" / "src" / "lib.rs").read_text("utf-8")
    assert ENCODED_NATIVE_FEATURE not in native_source
