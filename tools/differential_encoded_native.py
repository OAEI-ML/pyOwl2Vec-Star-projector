#!/usr/bin/env python3
"""Run a deterministic generated differential campaign against the hidden P7 cursor."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast

import pyowl_core

from pyowl2vec_star_projector import ProjectionOptions, Projector
from pyowl2vec_star_projector.artifact import edge_json_record
from pyowl2vec_star_projector.native import (
    ENCODED_DIRECT_KERNEL_VERSION,
    native_runtime_metadata,
)
from pyowl2vec_star_projector.provenance import ProjectionReport

_MASK64 = (1 << 64) - 1
_MAX_CASES = 4_096
_PROVIDERS = ("python", "native", "both")
_FAMILIES = (
    "annotation-assertion",
    "class-assertion",
    "declaration",
    "disjoint-classes",
    "equivalent-classes",
    "has-key",
    "negative-object-property-assertion",
    "object-property-assertion",
    "object-property-domain-range",
    "object-property-role-state",
    "property-chain",
    "same-individual",
    "skipped-data-property",
    "subclass",
)
_FORBIDDEN_NATIVE_COUNTERS = (
    "base_flattening_bytes",
    "encoded_staging_copy_bytes",
    "materialized_scalar_rows",
    "parser_calls",
    "per_row_ffi_calls",
    "resolver_calls",
    "scalar_axiom_materializations",
    "scalar_term_materializations",
    "structural_copy_bytes",
    "wire_decoder_calls",
    "wire_encoder_calls",
)


class DifferentialMismatch(RuntimeError):
    """A generated case did not retain the exact hidden-native contract."""


class _SplitMix64:
    """Small version-independent generator used only to build reproducible cases."""

    def __init__(self, seed: int) -> None:
        self._state = seed & _MASK64

    def next(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _MASK64
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK64
        return value ^ (value >> 31)

    def index(self, length: int) -> int:
        if length < 1:
            raise ValueError("generator choice requires at least one value")
        return self.next() % length

    def shuffle(self, values: list[str]) -> None:
        for index in range(len(values) - 1, 0, -1):
            selected = self.index(index + 1)
            values[index], values[selected] = values[selected], values[index]


@dataclass(frozen=True, slots=True)
class GeneratedCase:
    """One bounded ontology plus its scalar semantic options."""

    seed: int
    source: bytes
    options: ProjectionOptions
    families: tuple[str, ...] = _FAMILIES


def generate_case(seed: int) -> GeneratedCase:
    """Generate one deterministic mixed-rule direct-view case."""
    if type(seed) is not int or seed < 0 or seed > _MASK64:
        raise ValueError("seed must be an unsigned 64-bit integer")
    generator = _SplitMix64(seed)

    def name(prefix: str, count: int) -> str:
        return f":{prefix}{generator.index(count)}"

    def distinct(prefix: str, count: int) -> tuple[str, str]:
        first_index = generator.index(count)
        second_index = (first_index + 1 + generator.index(count - 1)) % count
        return f":{prefix}{first_index}", f":{prefix}{second_index}"

    tax_sub, tax_super = distinct("C", 8)
    pair_first, pair_second = distinct("C", 8)
    assertion_class = name("C", 8)
    annotation_class = name("C", 8)
    role = name("p", 4)
    restriction_role = name("p", 4)
    source_individual = name("i", 4)
    target_individual = name("i", 4)
    min_cardinality = 1 + generator.index(7)
    max_cardinality = 1 + generator.index(7)
    metadata_value = generator.next() & 0xFFFF

    declarations = [
        *(f"Declaration(Class(:C{index}))" for index in range(8)),
        *(f"Declaration(ObjectProperty(:p{index}))" for index in range(4)),
        "Declaration(DataProperty(:dp))",
        *(f"Declaration(NamedIndividual(:i{index}))" for index in range(4)),
        "Declaration(AnnotationProperty(:meta))",
    ]
    axioms = [
        "SubObjectPropertyOf(:p1 :p0)",
        "InverseObjectProperties(:p0 :p2)",
        "SubObjectPropertyOf(ObjectPropertyChain(:p2 ObjectInverseOf(:p3)) :p0)",
        f"SubClassOf({tax_sub} {tax_super})",
        (f'SubClassOf(Annotation(:meta "variant-{metadata_value}") {tax_sub} {tax_super})'),
        f"SubClassOf(:C2 ObjectSomeValuesFrom({restriction_role} :C3))",
        f"SubClassOf(ObjectAllValuesFrom(ObjectInverseOf({role}) :C4) :C5)",
        (f"SubClassOf(:C6 ObjectMinCardinality({min_cardinality} {restriction_role} :C7))"),
        (f"SubClassOf(ObjectMaxCardinality({max_cardinality} {restriction_role} :C0) :C1)"),
        f"SubClassOf(:C3 ObjectHasSelf({role}))",
        f"EquivalentClasses({pair_first} {pair_second} :C7)",
        (
            "EquivalentClasses(:C4 ObjectIntersectionOf("
            f":C5 ObjectSomeValuesFrom({role} :C6) ObjectHasSelf(:p1)))"
        ),
        f"EquivalentClasses(:C7 ObjectSomeValuesFrom({restriction_role} :C0))",
        f"ClassAssertion({assertion_class} {source_individual})",
        f"ClassAssertion(:C1 _:anonymous-{seed:x})",
        (f"ObjectPropertyAssertion({role} {source_individual} _:target-{generator.next():x})"),
        (
            "NegativeObjectPropertyAssertion("
            f"ObjectInverseOf(:p2) _:negative-{generator.next():x} {target_individual})"
        ),
        "ObjectPropertyDomain(:p0 :C2)",
        "ObjectPropertyDomain(:p0 :C3)",
        "ObjectPropertyRange(:p0 :C4)",
        "ObjectPropertyRange(:p0 :C5)",
        (
            "AnnotationAssertion("
            "<http://www.w3.org/2000/01/rdf-schema#label> "
            f'{annotation_class} "label-{generator.next():x}")'
        ),
        (
            "AnnotationAssertion("
            "<http://www.w3.org/2000/01/rdf-schema#comment> "
            f"{annotation_class} <urn:generated-value-{generator.next():x}>)"
        ),
        f'AnnotationAssertion(<urn:unsupported> {annotation_class} "ignored")',
        (
            "AnnotationAssertion("
            "<http://www.w3.org/2000/01/rdf-schema#label> "
            f'<urn:not-a-class-{seed:x}> "ignored")'
        ),
        f"FunctionalObjectProperty({role})",
        (f"SameIndividual({source_individual} {target_individual} _:same-{generator.next():x})"),
        f'DataPropertyAssertion(:dp {source_individual} "value-{generator.next():x}")',
        f"DisjointClasses({tax_sub} {tax_super})",
        f"HasKey({assertion_class} (:p0) (:dp))",
    ]
    generator.shuffle(declarations)
    generator.shuffle(axioms)
    ontology_annotation = f'Annotation(:meta "ontology-{generator.next():x}")'
    body = " ".join((ontology_annotation, *declarations, *axioms))
    source = (
        f"Prefix(:=<urn:p7-generated-{seed:x}#>) Ontology(<urn:p7-generated-{seed:x}> {body})"
    ).encode()
    options = ProjectionOptions(
        backend="python",
        bidirectional_taxonomy=bool(seed & 1),
        only_taxonomy=bool(seed & 2),
        include_literals=bool(seed & 4),
        duplicates="unique" if seed & 8 else "preserve",
        order="encounter" if seed & 16 else "canonical",
        compatibility_state="isolated",
    )
    return GeneratedCase(seed=seed, source=source, options=options)


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


def _edge_digest(edges: list[Any]) -> str:
    digest = hashlib.sha256()
    for edge in edges:
        digest.update(edge_json_record(edge))
    return digest.hexdigest()


def _completed_report(projector: Projector) -> ProjectionReport:
    report = projector.last_report
    if report is None:
        raise DifferentialMismatch("projector completed without publishing a report")
    return report


def _assert_semantic_parity(
    expected: ProjectionReport,
    actual: ProjectionReport,
    *,
    seed: int,
    provider: str,
) -> None:
    expected_provenance = expected.provenance
    actual_provenance = actual.provenance
    pairs = (
        ("diagnostics", actual.diagnostics, expected.diagnostics),
        ("core provenance", actual_provenance.core, expected_provenance.core),
        ("counts", actual_provenance.counts, expected_provenance.counts),
        (
            "diagnostics digest",
            actual_provenance.diagnostics_digest,
            expected_provenance.diagnostics_digest,
        ),
        (
            "invocation count",
            actual_provenance.invocation_count,
            expected_provenance.invocation_count,
        ),
        (
            "call history digest",
            actual_provenance.call_history_digest,
            expected_provenance.call_history_digest,
        ),
    )
    for label, observed, wanted in pairs:
        if observed != wanted:
            raise DifferentialMismatch(f"seed {seed} provider {provider}: {label} differs")


def _run_case(
    case: GeneratedCase,
    *,
    provider: pyowl_core.BackendPreference,
    buffer_edges: int,
) -> dict[str, object]:
    view = pyowl_core.load_snapshot(
        case.source,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.IGNORE,
            backend=provider,
        ),
    )
    expected_projector = Projector()
    expected = expected_projector.project(view, options=case.options)
    expected_report = _completed_report(expected_projector)

    native_projector = Projector()
    actual = list(
        native_projector._iter_native_encoded_edges(
            view,
            options=replace(case.options, backend="native"),
            buffer_edges=buffer_edges,
        )
    )
    actual_report = _completed_report(native_projector)
    provider_name = provider.value
    if actual != expected:
        raise DifferentialMismatch(
            f"seed {case.seed} provider {provider_name}: ordered edges differ"
        )
    _assert_semantic_parity(
        expected_report,
        actual_report,
        seed=case.seed,
        provider=provider_name,
    )
    ingestion = actual_report.provenance.ingestion
    if ingestion.path != "encoded-native":
        raise DifferentialMismatch(
            f"seed {case.seed} provider {provider_name}: hidden cursor selected {ingestion.path}"
        )
    counters = dict(ingestion.counters)
    nonzero_forbidden = {
        name: counters.get(name, 0)
        for name in _FORBIDDEN_NATIVE_COUNTERS
        if counters.get(name, 0) != 0
    }
    if nonzero_forbidden:
        labels = ",".join(sorted(nonzero_forbidden))
        raise DifferentialMismatch(
            f"seed {case.seed} provider {provider_name}: forbidden counters nonzero: {labels}"
        )
    if counters.get("encoded_compiler_gil_released") is not True:
        raise DifferentialMismatch(
            f"seed {case.seed} provider {provider_name}: released-GIL proof absent"
        )
    semantic_options = case.options.to_dict()
    semantic_options.pop("backend")
    return {
        "seed": case.seed,
        "provider_backend": provider_name,
        "source_bytes": len(case.source),
        "source_sha256": hashlib.sha256(case.source).hexdigest(),
        "semantic_options": semantic_options,
        "buffer_edges": buffer_edges,
        "edge_count": len(actual),
        "ordered_edge_sha256": _edge_digest(actual),
        "counts": asdict(actual_report.provenance.counts),
        "diagnostics_digest": actual_report.provenance.diagnostics_digest,
        "native_compiled_edges": counters["native_compiled_edges"],
        "native_edge_batches": counters["native_edge_batches"],
        "native_peak_buffered_edges": counters["native_peak_buffered_edges"],
        "encoded_buffer_count": counters["encoded_buffer_count"],
        "encoded_zero_copy_buffers": counters["encoded_zero_copy_buffers"],
        "encoded_staging_copy_bytes": counters["encoded_staging_copy_bytes"],
        "per_row_ffi_calls": counters["per_row_ffi_calls"],
        "ingestion_path": ingestion.path,
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
    cases: int,
    provider: str,
    buffer_edges: int,
) -> dict[str, object]:
    """Run a bounded campaign and return deterministic path-free evidence."""
    if type(first_seed) is not int or first_seed < 0 or first_seed > _MASK64:
        raise ValueError("first_seed must be an unsigned 64-bit integer")
    if type(cases) is not int or cases < 1 or cases > _MAX_CASES:
        raise ValueError(f"cases must be between 1 and {_MAX_CASES}")
    if first_seed + cases - 1 > _MASK64:
        raise ValueError("seed range exceeds unsigned 64-bit space")
    if type(buffer_edges) is not int or buffer_edges < 1:
        raise ValueError("buffer_edges must be a positive integer")
    providers = _provider_values(provider)

    records: list[dict[str, object]] = []
    option_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    total_edges = 0
    for seed in range(first_seed, first_seed + cases):
        case = generate_case(seed)
        option_key = _json_sha256(
            {name: value for name, value in case.options.to_dict().items() if name != "backend"}
        )
        for selected_provider in providers:
            selected_buffer_edges = 1 + ((buffer_edges - 1 + seed) % buffer_edges)
            record = _run_case(
                case,
                provider=selected_provider,
                buffer_edges=selected_buffer_edges,
            )
            records.append(record)
            option_counts[option_key] += 1
            provider_counts[selected_provider.value] += 1
            total_edges += cast(int, record["edge_count"])

    native_version, features = native_runtime_metadata()
    cases_sha256 = _json_sha256(records)
    return {
        "schema": "pyowl-projector.encoded-native-generated-differential/1",
        "generator": "splitmix64-v1",
        "first_seed": first_seed,
        "generated_case_count": cases,
        "executed_case_count": len(records),
        "provider_backends": dict(sorted(provider_counts.items())),
        "semantic_option_combinations": len(option_counts),
        "families": list(_FAMILIES),
        "buffer_edges_upper_bound": buffer_edges,
        "total_edges": total_edges,
        "cases_sha256": cases_sha256,
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
    parser.add_argument("--cases", type=int, default=32)
    parser.add_argument("--provider", choices=_PROVIDERS, default="both")
    parser.add_argument("--buffer-edges", type=int, default=7)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_campaign(
            first_seed=args.first_seed,
            cases=args.cases,
            provider=args.provider,
            buffer_edges=args.buffer_edges,
        )
    except (DifferentialMismatch, ValueError) as error:
        failure = {
            "schema": "pyowl-projector.encoded-native-generated-differential/1",
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
