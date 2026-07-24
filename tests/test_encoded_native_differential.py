from __future__ import annotations

import pytest

from pyowl2vec_star_projector import probe_native_backend
from tools.differential_encoded_native import generate_case, main, run_campaign

pytestmark = pytest.mark.skipif(
    not probe_native_backend().available,
    reason="optional native extension is not installed",
)


def test_generated_case_is_reproducible_and_bounded() -> None:
    first = generate_case(42)
    second = generate_case(42)

    assert first == second
    assert len(first.source) < 16_384
    assert first.source.startswith(b"Prefix(:=<urn:p7-generated-2a#>)")
    assert len(first.families) == 14


@pytest.mark.parametrize(
    "arguments",
    [
        {"first_seed": -1, "cases": 1, "provider": "python", "buffer_edges": 1},
        {"first_seed": 0, "cases": 0, "provider": "python", "buffer_edges": 1},
        {"first_seed": 0, "cases": 1, "provider": "invalid", "buffer_edges": 1},
        {"first_seed": 0, "cases": 1, "provider": "python", "buffer_edges": 0},
    ],
)
def test_campaign_rejects_unbounded_or_invalid_configuration(
    arguments: dict[str, int | str],
) -> None:
    with pytest.raises(ValueError):
        run_campaign(**arguments)  # type: ignore[arg-type]


def test_small_campaign_matches_both_zero_copy_provider_layouts() -> None:
    report = run_campaign(
        first_seed=0,
        cases=8,
        provider="both",
        buffer_edges=5,
    )

    assert report["schema"] == "pyowl-projector.encoded-native-generated-differential/1"
    assert report["generated_case_count"] == 8
    assert report["executed_case_count"] == 16
    assert report["provider_backends"] == {"native": 8, "python": 8}
    assert report["semantic_option_combinations"] == 8
    assert report["total_edges"] == 398
    assert (
        report["cases_sha256"]
        == "0c599e2d2704990af6fe62c0464a4dec601937801f5789fa0d299c66cceb08a9"
    )
    assert report["passed"] is True
    records = report["cases"]
    assert isinstance(records, list)
    assert all(record["ingestion_path"] == "encoded-native" for record in records)
    assert all(record["encoded_staging_copy_bytes"] == 0 for record in records)
    assert all(record["per_row_ffi_calls"] == 0 for record in records)


def test_cli_failure_is_minimized_and_path_free(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--cases", "0"]) == 1
    captured = capsys.readouterr()
    assert '"passed": false' in captured.out
    assert "cases must be between 1 and 4096" in captured.out
    assert captured.err == ""
