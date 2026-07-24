from __future__ import annotations

from typing import Any, cast

import pyowl_core
import pytest

import tools.hostile_encoded_native as hostile_module
from pyowl2vec_star_projector import probe_native_backend
from pyowl2vec_star_projector.encoded import (
    EncodedStructuralLease,
    select_private_direct_ingestion,
)
from tools.differential_encoded_native import generate_case
from tools.hostile_encoded_native import (
    HostileCampaignFailure,
    build_mutations,
    main,
    run_campaign,
)

pytestmark = pytest.mark.skipif(
    not probe_native_backend().available,
    reason="optional native extension is not installed",
)


def _lease() -> EncodedStructuralLease:
    generated = generate_case(0)
    view = pyowl_core.load_snapshot(
        generated.source,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.IGNORE,
            backend=pyowl_core.BackendPreference.PYTHON,
        ),
    )
    negotiation = select_private_direct_ingestion(
        view,
        selected_backend="native",
    )
    assert negotiation.lease is not None
    return negotiation.lease


def test_mutation_plan_is_deterministic_unique_and_covers_every_column() -> None:
    first = build_mutations(_lease())
    second = build_mutations(_lease())

    assert first == second
    assert len(first) == 29
    assert len({mutation.name for mutation in first}) == len(first)
    assert {column for mutation in first for column in mutation.columns} == {
        "root_kinds",
        "root_ids",
        "node_tags",
        "node_field_offsets",
        "field_kinds",
        "field_values",
        "field_lengths",
        "item_kinds",
        "item_values",
        "item_lengths",
        "scalar_bytes",
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"first_seed": -1, "sources": 1, "provider": "python"},
        {"first_seed": 0, "sources": 0, "provider": "python"},
        {"first_seed": 0, "sources": 1, "provider": "invalid"},
    ],
)
def test_campaign_rejects_invalid_or_unbounded_configuration(
    arguments: dict[str, int | str],
) -> None:
    with pytest.raises(ValueError):
        run_campaign(**arguments)  # type: ignore[arg-type]


def test_small_campaign_fails_closed_for_both_provider_layouts() -> None:
    report = run_campaign(first_seed=0, sources=1, provider="both")

    assert report["schema"] == "pyowl-projector.encoded-native-hostile-columns/1"
    assert report["generated_source_count"] == 1
    assert report["mutation_count_per_source"] == 29
    assert report["executed_case_count"] == 58
    assert report["cases_sha256"] == (
        "e180ae485b16c8290a02cc016c5acc71e5534122b1e05d18b46d7adc2c0c6df1"
    )
    assert report["provider_backends"] == {"native": 29, "python": 29}
    assert report["mutation_categories"] == {
        "canonicality": 12,
        "offset": 16,
        "reference": 6,
        "scalar": 2,
        "shape": 14,
        "tag": 8,
    }
    assert report["column_executions"] == {
        "field_kinds": 2,
        "field_lengths": 6,
        "field_values": 10,
        "item_kinds": 2,
        "item_lengths": 4,
        "item_values": 6,
        "node_field_offsets": 8,
        "node_tags": 4,
        "root_ids": 8,
        "root_kinds": 6,
        "scalar_bytes": 6,
    }
    assert report["all_columns_exercised"] is True
    assert report["failure_categories"] == {"validation": 58}
    assert report["typed_validation_failure_every_case"] is True
    assert report["terminal_failed_compiler_every_case"] is True
    assert report["absent_batch_session_every_case"] is True
    assert report["zero_output_every_case"] is True
    assert report["zero_report_publication_every_case"] is True
    assert report["provider_failure_parity_every_case"] is True
    assert report["passed"] is True


def test_campaign_closes_native_view_when_mutation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed_views: list[object] = []
    original_close = hostile_module._close_view

    def tracked_close(view: object) -> None:
        original_close(view)
        closed_views.append(view)

    def fail_mutation(**_arguments: object) -> dict[str, object]:
        raise HostileCampaignFailure("injected mutation failure")

    monkeypatch.setattr(hostile_module, "_close_view", tracked_close)
    monkeypatch.setattr(hostile_module, "_exercise_mutation", fail_mutation)

    with pytest.raises(HostileCampaignFailure, match="injected mutation failure"):
        run_campaign(first_seed=0, sources=1, provider="native")

    assert len(closed_views) == 1
    assert cast(Any, closed_views[0]).closed is True


def test_cli_failure_is_minimized_and_path_free(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--sources", "0"]) == 1
    captured = capsys.readouterr()
    assert '"passed": false' in captured.out
    assert "sources must be between 1 and 256" in captured.out
    assert captured.err == ""
