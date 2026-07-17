from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyowl_core
import pytest
from pyowl_core import UnresolvedImportError

from pyowl2vec_star_projector import (
    Edge,
    ProjectionOptions,
    Projector,
    UnsupportedAxiomShapeError,
    project_source,
)

from .support.core_views import GOLDENS, fixture_view

INVENTORY = json.loads(
    (Path(__file__).parent / "fixtures" / "oracle" / "inventory.json").read_text("utf-8")
)


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _edge_objects(edges: Iterable[Edge]) -> list[dict[str, str]]:
    return [
        {"source": edge.source, "relation": edge.relation, "destination": edge.destination}
        for edge in edges
    ]


FRESH = [entry for entry in INVENTORY["fixtures"] if entry["id"] != "imports-missing"]


@pytest.mark.parametrize("entry", FRESH, ids=lambda entry: entry["id"])
def test_every_fresh_oracle_case_matches_canonical_bytes(entry: dict[str, Any]) -> None:
    fixture_id = entry["id"]
    view = fixture_view(fixture_id, entry["document"])
    golden = json.loads((GOLDENS / f"{fixture_id}.json").read_text("utf-8"))
    for case in golden["cases"].values():
        flags = case["options"]
        options = ProjectionOptions(
            bidirectional_taxonomy=flags["bidirectional_taxonomy"],
            only_taxonomy=flags["only_taxonomy"],
            include_literals=flags["include_literals"],
            backend="python",
        )
        invocation = case["invocations"][0]
        if invocation["outcome"] == "error":
            with pytest.raises(UnsupportedAxiomShapeError) as raised:
                Projector().project(view, options=options)
            assert raised.value.details["reference_error"] == "java.lang.ClassCastException"
            continue
        objects = _edge_objects(Projector().project(view, options=options))
        assert objects == invocation["canonical_edges"]
        assert (
            hashlib.sha256(_canonical_json(objects)).hexdigest()
            == invocation["canonical_edges_sha256"]
        )


@pytest.mark.parametrize("session", INVENTORY["sessions"], ids=lambda entry: entry["id"])
def test_scala_instance_sessions_match_call_history(session: dict[str, Any]) -> None:
    golden = json.loads((GOLDENS / f"{session['id']}.json").read_text("utf-8"))
    for case in golden["cases"].values():
        flags = case["options"]
        projector = Projector()
        options = ProjectionOptions(
            bidirectional_taxonomy=flags["bidirectional_taxonomy"],
            only_taxonomy=flags["only_taxonomy"],
            include_literals=flags["include_literals"],
            compatibility_state="scala-instance",
            backend="python",
        )
        for document, invocation in zip(session["documents"], case["invocations"], strict=True):
            fixture_id = Path(document).stem
            objects = _edge_objects(
                projector.project(fixture_view(fixture_id, document), options=options)
            )
            assert objects == invocation["canonical_edges"]


def test_all_missing_import_invocations_stop_at_the_core_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    golden = json.loads((GOLDENS / "imports-missing.json").read_text("utf-8"))
    calls = 0

    def missing(source: object, *, options: object, resolver: object) -> object:
        nonlocal calls
        del source, options, resolver
        calls += 1
        raise UnresolvedImportError("missing import")

    monkeypatch.setattr(pyowl_core, "coerce_snapshot", missing, raising=False)
    for case in golden["cases"].values():
        flags = case["options"]
        assert case["invocations"][0]["outcome"] == "error"
        with pytest.raises(UnresolvedImportError):
            project_source(
                "imports/missing-root.ofn",
                options=ProjectionOptions(
                    bidirectional_taxonomy=flags["bidirectional_taxonomy"],
                    only_taxonomy=flags["only_taxonomy"],
                    include_literals=flags["include_literals"],
                    backend="python",
                ),
            )
    assert calls == 8


def test_all_160_flag_cases_and_184_invocations_are_exercised() -> None:
    fresh_invocations = len(INVENTORY["fixtures"]) * 8
    session_invocations = sum(len(entry["documents"]) * 8 for entry in INVENTORY["sessions"])
    assert fresh_invocations == 136
    assert fresh_invocations + session_invocations == 184
