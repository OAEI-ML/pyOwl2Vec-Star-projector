#!/usr/bin/env python3
"""Run deterministic malformed-column checks against the hidden P7 Rust cursor."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from unittest.mock import patch

import pyowl_core

import pyowl2vec_star_projector.api as api_module
from pyowl2vec_star_projector import ProjectionOptions, Projector
from pyowl2vec_star_projector.encoded import (
    EncodedNegotiation,
    EncodedStructuralLease,
    select_private_direct_ingestion,
)
from pyowl2vec_star_projector.errors import SnapshotCompatibilityError
from pyowl2vec_star_projector.native import (
    ENCODED_DIRECT_BUFFER_ORDER,
    ENCODED_DIRECT_KERNEL_VERSION,
    NativeEncodedDirectCompiler,
    native_runtime_metadata,
    prepare_native_encoded_direct,
)
from tools.differential_encoded_native import generate_case

_MASK64 = (1 << 64) - 1
_MAX_SOURCES = 256
_PROVIDERS = ("python", "native", "both")
_FIXED_WIDTHS: Mapping[str, int] = MappingProxyType(
    {
        "root_ids": 4,
        "node_tags": 2,
        "node_field_offsets": 8,
        "field_values": 8,
        "field_lengths": 8,
        "item_values": 8,
        "item_lengths": 8,
    }
)


class HostileCampaignFailure(RuntimeError):
    """A malformed case escaped or crossed the wrong failure boundary."""


@dataclass(frozen=True, slots=True)
class BufferMutation:
    """One deterministic malformed-column rewrite."""

    name: str
    category: str
    changes: tuple[tuple[str, bytes], ...]

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(name for name, _payload in self.changes)


def _replace_uint(payload: bytes, index: int, width: int, value: int) -> bytes:
    start = index * width
    end = start + width
    if start < 0 or end > len(payload):
        raise HostileCampaignFailure("mutation integer row is out of range")
    maximum = (1 << (width * 8)) - 1
    if value < 0 or value > maximum:
        raise HostileCampaignFailure("mutation integer value is out of range")
    return payload[:start] + value.to_bytes(width, "little") + payload[end:]


def _uint(payload: bytes, index: int, width: int) -> int:
    start = index * width
    end = start + width
    if start < 0 or end > len(payload):
        raise HostileCampaignFailure("mutation integer read is out of range")
    return int.from_bytes(payload[start:end], "little")


def _swap_rows(payload: bytes, first: int, second: int, width: int) -> bytes:
    if first == second:
        raise HostileCampaignFailure("mutation rows must be distinct")
    rows = [payload[offset : offset + width] for offset in range(0, len(payload), width)]
    rows[first], rows[second] = rows[second], rows[first]
    return b"".join(rows)


def build_mutations(lease: EncodedStructuralLease) -> tuple[BufferMutation, ...]:
    """Build guaranteed-invalid rewrites over every structural column."""
    payloads = {name: bytes(lease.buffers[name]) for name in ENCODED_DIRECT_BUFFER_ORDER}
    field_kinds = payloads["field_kinds"]
    item_kinds = payloads["item_kinds"]
    node_count = len(payloads["node_tags"]) // 2
    field_count = len(field_kinds)
    scalar_count = len(payloads["scalar_bytes"])
    if (
        len(payloads["root_kinds"]) < 2
        or node_count < 2
        or field_count < 2
        or len(item_kinds) < 2
        or scalar_count < 2
    ):
        raise HostileCampaignFailure("generated source is too small for the hostile matrix")

    def field_index(*kinds: int) -> int:
        try:
            return next(index for index, kind in enumerate(field_kinds) if kind in kinds)
        except StopIteration as error:
            raise HostileCampaignFailure(
                f"generated source lacks required field kinds {kinds}"
            ) from error

    node_field = field_index(1)
    none_field = field_index(0)
    scalar_field = field_index(2, 3, 4, 5)
    collection_field = field_index(6, 7)
    set_field = field_index(6)
    set_start = _uint(payloads["field_values"], set_field, 8)
    set_length = _uint(payloads["field_lengths"], set_field, 8)
    if set_length < 2:
        try:
            set_field = next(
                index
                for index, kind in enumerate(field_kinds)
                if kind == 6 and _uint(payloads["field_lengths"], index, 8) >= 2
            )
        except StopIteration as error:
            raise HostileCampaignFailure(
                "generated source lacks a multi-item canonical set"
            ) from error
        set_start = _uint(payloads["field_values"], set_field, 8)

    mutations: list[BufferMutation] = []

    def add(
        name: str,
        category: str,
        changes: Mapping[str, bytes],
    ) -> None:
        if not changes:
            raise HostileCampaignFailure("mutation must alter at least one column")
        ordered: list[tuple[str, bytes]] = []
        for column in ENCODED_DIRECT_BUFFER_ORDER:
            if column not in changes:
                continue
            changed = changes[column]
            if changed == payloads[column]:
                raise HostileCampaignFailure(f"mutation {name} does not change {column}")
            ordered.append((column, changed))
        if len(ordered) != len(changes):
            raise HostileCampaignFailure(f"mutation {name} names an unknown column")
        mutations.append(BufferMutation(name, category, tuple(ordered)))

    add(
        "root-kind-invalid",
        "tag",
        {"root_kinds": bytes([0]) + payloads["root_kinds"][1:]},
    )
    add(
        "root-reference-zero",
        "reference",
        {"root_ids": _replace_uint(payloads["root_ids"], 0, 4, 0)},
    )
    add(
        "node-tag-invalid",
        "tag",
        {"node_tags": _replace_uint(payloads["node_tags"], 0, 2, 0)},
    )
    add(
        "node-offset-origin-nonzero",
        "offset",
        {"node_field_offsets": _replace_uint(payloads["node_field_offsets"], 0, 8, 1)},
    )
    add(
        "field-kind-invalid",
        "tag",
        {"field_kinds": bytes([255]) + payloads["field_kinds"][1:]},
    )
    add(
        "field-node-reference-out-of-range",
        "reference",
        {"field_values": _replace_uint(payloads["field_values"], node_field, 8, node_count + 1)},
    )
    add(
        "field-node-length-nonzero",
        "canonicality",
        {"field_lengths": _replace_uint(payloads["field_lengths"], node_field, 8, 1)},
    )
    add(
        "item-kind-invalid",
        "tag",
        {"item_kinds": bytes([255]) + payloads["item_kinds"][1:]},
    )
    add(
        "item-node-reference-zero",
        "reference",
        {"item_values": _replace_uint(payloads["item_values"], 0, 8, 0)},
    )
    add(
        "item-node-length-nonzero",
        "canonicality",
        {"item_lengths": _replace_uint(payloads["item_lengths"], 0, 8, 1)},
    )
    add(
        "scalar-arena-uncovered-tail",
        "offset",
        {"scalar_bytes": payloads["scalar_bytes"] + b"\x00"},
    )

    for column, width in _FIXED_WIDTHS.items():
        if not payloads[column] or len(payloads[column]) % width:
            raise HostileCampaignFailure(
                f"generated source has misaligned fixed-width column {column}"
            )
        add(
            f"{column}-partial-width",
            "shape",
            {column: payloads[column][:-1]},
        )

    add(
        "duplicate-final-root",
        "canonicality",
        {
            "root_kinds": payloads["root_kinds"] + payloads["root_kinds"][-1:],
            "root_ids": payloads["root_ids"] + payloads["root_ids"][-4:],
        },
    )
    add(
        "reversed-leading-roots",
        "canonicality",
        {
            "root_kinds": _swap_rows(payloads["root_kinds"], 0, 1, 1),
            "root_ids": _swap_rows(payloads["root_ids"], 0, 1, 4),
        },
    )
    add(
        "node-offset-terminal-does-not-cover-fields",
        "offset",
        {
            "node_field_offsets": _replace_uint(
                payloads["node_field_offsets"], node_count, 8, field_count - 1
            )
        },
    )
    add(
        "node-offset-interior-out-of-range",
        "offset",
        {
            "node_field_offsets": _replace_uint(
                payloads["node_field_offsets"], 1, 8, field_count + 1
            )
        },
    )
    add(
        "collection-offset-skips-item",
        "offset",
        {
            "field_values": _replace_uint(
                payloads["field_values"],
                collection_field,
                8,
                _uint(payloads["field_values"], collection_field, 8) + 1,
            )
        },
    )
    add(
        "none-component-has-value",
        "canonicality",
        {"field_values": _replace_uint(payloads["field_values"], none_field, 8, 1)},
    )
    add(
        "scalar-component-offset-skips-byte",
        "offset",
        {
            "field_values": _replace_uint(
                payloads["field_values"],
                scalar_field,
                8,
                _uint(payloads["field_values"], scalar_field, 8) + 1,
            )
        },
    )
    add(
        "scalar-component-length-out-of-range",
        "offset",
        {
            "field_lengths": _replace_uint(
                payloads["field_lengths"],
                scalar_field,
                8,
                scalar_count + 1,
            )
        },
    )
    add(
        "canonical-set-duplicate-item",
        "canonicality",
        {
            "item_values": _replace_uint(
                payloads["item_values"],
                set_start + 1,
                8,
                _uint(payloads["item_values"], set_start, 8),
            )
        },
    )
    add(
        "scalar-arena-truncated",
        "offset",
        {"scalar_bytes": payloads["scalar_bytes"][:-1]},
    )
    add(
        "scalar-leading-byte-invalid",
        "scalar",
        {"scalar_bytes": b"\xff" + payloads["scalar_bytes"][1:]},
    )

    names = [mutation.name for mutation in mutations]
    if len(set(names)) != len(names):
        raise HostileCampaignFailure("mutation names are not unique")
    exercised = {column for mutation in mutations for column in mutation.columns}
    if exercised != set(ENCODED_DIRECT_BUFFER_ORDER):
        missing = sorted(set(ENCODED_DIRECT_BUFFER_ORDER) - exercised)
        raise HostileCampaignFailure(f"mutation plan misses columns: {missing}")
    return tuple(mutations)


def _provider_values(provider: str) -> tuple[pyowl_core.BackendPreference, ...]:
    if provider == "python":
        return (pyowl_core.BackendPreference.PYTHON,)
    if provider == "native":
        return (pyowl_core.BackendPreference.NATIVE,)
    if provider == "both":
        return (
            pyowl_core.BackendPreference.PYTHON,
            pyowl_core.BackendPreference.NATIVE,
        )
    raise ValueError("provider must be 'python', 'native', or 'both'")


def _close_view(view: object) -> None:
    close = getattr(view, "close", None)
    if callable(close):
        close()


def _lease_for_source(
    source: bytes,
    provider: pyowl_core.BackendPreference,
) -> tuple[object, EncodedStructuralLease]:
    view = pyowl_core.load_snapshot(
        source,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.IGNORE,
            backend=provider,
        ),
    )
    try:
        negotiation = select_private_direct_ingestion(
            view,
            selected_backend="native",
        )
        if negotiation.path != "encoded-native" or negotiation.lease is None:
            raise HostileCampaignFailure(
                f"provider {provider.value} did not publish the private direct lease"
            )
        return view, negotiation.lease
    except BaseException:
        _close_view(view)
        raise


def _apply_mutation(
    lease: EncodedStructuralLease,
    mutation: BufferMutation,
    *,
    packed: bool,
) -> EncodedStructuralLease:
    payloads = {name: bytes(lease.buffers[name]) for name in ENCODED_DIRECT_BUFFER_ORDER}
    payloads.update(dict(mutation.changes))
    buffers: dict[str, memoryview] = {}
    if packed:
        arena = b"".join(payloads[name] for name in ENCODED_DIRECT_BUFFER_ORDER)
        owner = memoryview(arena)
        start = 0
        for name in ENCODED_DIRECT_BUFFER_ORDER:
            end = start + len(payloads[name])
            buffers[name] = owner[start:end]
            start = end
    else:
        buffers = {name: memoryview(payloads[name]) for name in ENCODED_DIRECT_BUFFER_ORDER}
    frozen: Mapping[str, memoryview] = MappingProxyType(buffers)
    encoded_view = replace(cast(Any, lease.encoded_view), buffers=frozen)
    return replace(lease, encoded_view=encoded_view, buffers=frozen)


def _expect_validation_failure(
    operation: Callable[[], object],
    *,
    label: str,
) -> tuple[str, str]:
    try:
        operation()
    except SnapshotCompatibilityError as error:
        message = str(error)
        return type(error).__name__, hashlib.sha256(message.encode()).hexdigest()
    except Exception as error:
        raise HostileCampaignFailure(
            f"{label}: expected SnapshotCompatibilityError, got {type(error).__name__}"
        ) from error
    raise HostileCampaignFailure(f"{label}: malformed input was accepted")


def _assert_failed_compiler(compiler: NativeEncodedDirectCompiler, *, label: str) -> None:
    kernel = compiler._kernel
    observed = {
        "compiler_state": compiler.state,
        "batch_state": getattr(kernel, "batch_state", None),
        "remaining_edges": getattr(kernel, "remaining_batch_edges", None),
        "boundary_calls": getattr(kernel, "batch_boundary_calls", None),
        "edge_batches": getattr(kernel, "emitted_edge_batches", None),
    }
    expected = {
        "compiler_state": "failed",
        "batch_state": "absent",
        "remaining_edges": 0,
        "boundary_calls": 0,
        "edge_batches": 0,
    }
    if observed != expected:
        raise HostileCampaignFailure(
            f"{label}: malformed compiler published cursor state {observed}"
        )


def _exercise_mutation(
    *,
    seed: int,
    provider: pyowl_core.BackendPreference,
    view: object,
    mutation: BufferMutation,
    lease: EncodedStructuralLease,
) -> dict[str, object]:
    packed = provider is pyowl_core.BackendPreference.NATIVE
    hostile = _apply_mutation(lease, mutation, packed=packed)
    compiler = prepare_native_encoded_direct(hostile)
    direct_type, direct_message_sha256 = _expect_validation_failure(
        lambda: compiler.iter_batches(
            bidirectional=False,
            max_edges=1_000_000,
            max_iri_bytes=1_000_000,
            batch_edges=3,
            include_literals=False,
        ),
        label=f"seed {seed} provider {provider.value} mutation {mutation.name} direct",
    )
    _assert_failed_compiler(
        compiler,
        label=f"seed {seed} provider {provider.value} mutation {mutation.name}",
    )

    projector = Projector()
    observed_output: list[object] = []

    def consume_projector() -> None:
        iterator = projector._iter_native_encoded_edges(
            view,
            options=ProjectionOptions(
                backend="native",
                order="encounter",
                compatibility_state="isolated",
            ),
            buffer_edges=3,
        )
        try:
            observed_output.extend(iterator)
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()

    with patch.object(
        api_module,
        "select_private_direct_ingestion",
        return_value=EncodedNegotiation("encoded-native", lease=hostile),
    ):
        projector_type, projector_message_sha256 = _expect_validation_failure(
            consume_projector,
            label=(f"seed {seed} provider {provider.value} mutation {mutation.name} projector"),
        )
    if observed_output:
        raise HostileCampaignFailure(
            f"seed {seed} provider {provider.value} mutation {mutation.name}: "
            "malformed projector published output"
        )
    if projector.last_report is not None:
        raise HostileCampaignFailure(
            f"seed {seed} provider {provider.value} mutation {mutation.name}: "
            "malformed projector published a report"
        )
    if direct_type != projector_type or direct_message_sha256 != projector_message_sha256:
        raise HostileCampaignFailure(
            f"seed {seed} provider {provider.value} mutation {mutation.name}: "
            "direct and Projector failure boundaries differ"
        )
    return {
        "seed": seed,
        "provider_backend": provider.value,
        "provider_layout": "packed-bytes" if packed else "independent-bytes",
        "mutation": mutation.name,
        "mutation_category": mutation.category,
        "columns": list(mutation.columns),
        "failure_category": "validation",
        "failure_type": direct_type,
        "failure_message_sha256": direct_message_sha256,
        "compiler_state": "failed",
        "batch_state": "absent",
        "remaining_edges": 0,
        "boundary_calls": 0,
        "edge_batches": 0,
        "published_edges": 0,
        "report_published": False,
    }


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def run_campaign(
    *,
    first_seed: int,
    sources: int,
    provider: str,
) -> dict[str, object]:
    """Execute a bounded path-free malformed-column campaign."""
    if type(first_seed) is not int or first_seed < 0 or first_seed > _MASK64:
        raise ValueError("first_seed must be an unsigned 64-bit integer")
    if type(sources) is not int or sources < 1 or sources > _MAX_SOURCES:
        raise ValueError(f"sources must be between 1 and {_MAX_SOURCES}")
    if first_seed + sources - 1 > _MASK64:
        raise ValueError("seed range exceeds unsigned 64-bit space")
    providers = _provider_values(provider)

    records: list[dict[str, object]] = []
    mutation_names: tuple[str, ...] | None = None
    category_counts: Counter[str] = Counter()
    column_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    provider_results: dict[tuple[int, str], dict[str, tuple[str, str]]] = {}
    for seed in range(first_seed, first_seed + sources):
        generated = generate_case(seed)
        for selected_provider in providers:
            view, lease = _lease_for_source(generated.source, selected_provider)
            try:
                mutations = build_mutations(lease)
                names = tuple(mutation.name for mutation in mutations)
                if mutation_names is None:
                    mutation_names = names
                elif names != mutation_names:
                    raise HostileCampaignFailure(
                        f"seed {seed} provider {selected_provider.value}: mutation plan drifted"
                    )
                for mutation in mutations:
                    record = _exercise_mutation(
                        seed=seed,
                        provider=selected_provider,
                        view=view,
                        mutation=mutation,
                        lease=lease,
                    )
                    records.append(record)
                    category_counts[mutation.category] += 1
                    column_counts.update(mutation.columns)
                    provider_counts[selected_provider.value] += 1
                    key = (seed, mutation.name)
                    result = (
                        cast(str, record["failure_type"]),
                        cast(str, record["failure_message_sha256"]),
                    )
                    provider_name = selected_provider.value
                    indexed = provider_results.setdefault(key, {})
                    if provider_name in indexed:
                        raise HostileCampaignFailure(
                            f"seed {seed} mutation {mutation.name}: "
                            f"provider {provider_name} result is duplicated"
                        )
                    indexed[provider_name] = result
            finally:
                _close_view(view)

    if mutation_names is None:  # pragma: no cover - configuration requires one source
        raise HostileCampaignFailure("campaign generated no mutation plan")
    expected_providers = {provider.value for provider in providers}
    for seed in range(first_seed, first_seed + sources):
        for mutation_name in mutation_names:
            pair = provider_results.get((seed, mutation_name))
            if pair is None or set(pair) != expected_providers:
                raise HostileCampaignFailure("campaign provider pairing is incomplete")
            if len(pair) == 2 and len(set(pair.values())) != 1:
                raise HostileCampaignFailure(
                    f"seed {seed} mutation {mutation_name}: provider failure parity differs"
                )

    native_version, features = native_runtime_metadata()
    return {
        "schema": "pyowl-projector.encoded-native-hostile-columns/1",
        "first_seed": first_seed,
        "generated_source_count": sources,
        "mutation_count_per_source": len(mutation_names),
        "executed_case_count": len(records),
        "provider_backends": dict(sorted(provider_counts.items())),
        "mutation_categories": dict(sorted(category_counts.items())),
        "column_executions": {name: column_counts[name] for name in ENCODED_DIRECT_BUFFER_ORDER},
        "all_columns_exercised": set(column_counts) == set(ENCODED_DIRECT_BUFFER_ORDER),
        "mutation_names": list(mutation_names),
        "failure_categories": {"validation": len(records)},
        "typed_validation_failure_every_case": True,
        "terminal_failed_compiler_every_case": True,
        "absent_batch_session_every_case": True,
        "zero_output_every_case": True,
        "zero_report_publication_every_case": True,
        "provider_failure_parity_every_case": len(providers) == 2,
        "cases_sha256": _json_sha256(records),
        "runtime": {
            "core_version": str(getattr(pyowl_core, "__version__", "unknown")),
            "native_implementation_version": native_version,
            "encoded_direct_kernel_version": ENCODED_DIRECT_KERNEL_VERSION,
            "native_features": sorted(features),
        },
        "cases": records,
        "passed": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--sources", type=int, default=4)
    parser.add_argument("--provider", choices=_PROVIDERS, default="both")
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_campaign(
            first_seed=args.first_seed,
            sources=args.sources,
            provider=args.provider,
        )
    except (HostileCampaignFailure, ValueError) as error:
        failure = {
            "schema": "pyowl-projector.encoded-native-hostile-columns/1",
            "passed": False,
            "error": str(error),
        }
        print(json.dumps(failure, indent=2, sort_keys=True))
        return 1
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
