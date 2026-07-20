from __future__ import annotations

import gc
import sys
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pyowl_core
import pytest

from pyowl2vec_star_projector import (
    Edge,
    ProjectionOptions,
    ProjectionResourceError,
    Projector,
    probe_native_backend,
)
from pyowl2vec_star_projector.compiler import (
    RDF_TYPE,
    SUBCLASS_OF,
    SUPERCLASS_OF,
    iter_asserted_taxonomy,
)
from pyowl2vec_star_projector.encoded import (
    ENCODED_NATIVE_FEATURE,
    EncodedStructuralLease,
    _validate_encoded_view,
)
from pyowl2vec_star_projector.errors import (
    SnapshotCompatibilityError,
    UnsupportedAxiomShapeError,
)
from pyowl2vec_star_projector.native import (
    ENCODED_DIRECT_BUFFER_ORDER,
    NativeEncodedDirectCancelled,
    NativeEncodedDirectCompiler,
    NativeEncodedDirectUnsupported,
    load_native_module,
    prepare_native_encoded_direct,
)

NATIVE_AVAILABLE = probe_native_backend().available

pytestmark = pytest.mark.skipif(
    not NATIVE_AVAILABLE,
    reason="optional native extension is not installed",
)


def _snapshot(body: str) -> object:
    source = f"Prefix(:=<urn:native-direct#>) Ontology(<urn:native-direct> {body})".encode()
    return pyowl_core.load_snapshot(
        source,
        options=pyowl_core.LoadOptions(
            imports=pyowl_core.ImportPolicy.IGNORE,
            backend=pyowl_core.BackendPreference.PYTHON,
        ),
    )


def _lease(view: object) -> EncodedStructuralLease:
    encoded = cast(Any, view).view(
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


def _replace_buffers(
    lease: EncodedStructuralLease,
    replacements: dict[str, memoryview],
) -> EncodedStructuralLease:
    buffers = dict(lease.buffers)
    buffers.update(replacements)
    frozen = MappingProxyType(buffers)
    encoded = replace(cast(Any, lease.encoded_view), buffers=frozen)
    return replace(lease, encoded_view=encoded, buffers=frozen)


@pytest.fixture(autouse=True)
def _require_current_kernel() -> None:
    if NATIVE_AVAILABLE and not hasattr(load_native_module(), "EncodedDirectCompiler"):
        pytest.skip("installed native extension predates the private P7 foundation")


def test_direct_named_subclass_batch_matches_python_and_reports_real_work() -> None:
    view = _snapshot(
        "Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
        "SubClassOf(:A :B) SubClassOf(:B :C)"
    )
    lease = _lease(view)
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )

    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=4,
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected
    assert statistics.roots == 5
    assert statistics.declarations == 3
    assert statistics.subclasses == 2
    assert statistics.restriction_subclasses == 0
    assert statistics.equivalents == 0
    assert statistics.class_assertions == 0
    assert statistics.object_property_assertions == 0
    assert statistics.negative_object_property_assertions == 0
    assert statistics.skipped_axioms == 0
    assert statistics.object_property_domains == 0
    assert statistics.object_property_ranges == 0
    assert statistics.domain_range_edges == 0
    assert statistics.edges == 4
    assert statistics.nodes > statistics.roots
    assert statistics.buffer_bytes == sum(value.nbytes for value in lease.buffers.values())
    assert dict(statistics.ingestion_counters) == {
        "encoded_buffer_bytes": statistics.buffer_bytes,
        "encoded_buffer_count": 11,
        "encoded_compiler_gil_released": True,
        "encoded_detached_buffer_count": 11,
        "encoded_indexed_buffer_count": 0,
        "encoded_staging_copy_bytes": 0,
        "encoded_zero_copy_buffers": 11,
        "native_boundary_calls": 1,
        "per_row_ffi_calls": 0,
        "structural_copy_bytes": 0,
    }
    assert compiler.retained_buffer_count == len(ENCODED_DIRECT_BUFFER_ORDER) == 11
    assert compiler.state == "finished"


def test_many_axioms_cross_one_bounded_call_and_limit_failure_publishes_nothing() -> None:
    axioms = " ".join(f"SubClassOf(:C{index} :Top)" for index in range(250))
    lease = _lease(_snapshot(axioms))
    compiler = prepare_native_encoded_direct(lease)
    edges, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=250,
        max_iri_bytes=1024 * 1024,
    )
    assert len(edges) == statistics.subclasses == statistics.edges == 250

    limited = prepare_native_encoded_direct(lease)
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        limited.compile_batch(
            bidirectional=False,
            max_edges=249,
            max_iri_bytes=1024 * 1024,
        )
    assert limited.state == "failed"


@pytest.mark.parametrize("bidirectional", [False, True])
def test_named_equivalence_and_class_assertion_match_python_oracle(
    bidirectional: bool,
) -> None:
    view = _snapshot(
        "Declaration(Class(:Z)) Declaration(Class(:AA)) Declaration(Class(:B)) "
        "Declaration(Class(:Top)) Declaration(NamedIndividual(:i)) "
        "SubClassOf(:Z :Top) EquivalentClasses(:Z :AA :B) ClassAssertion(:Z :i)"
    )
    lease = _lease(view)
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=bidirectional,
            duplicates="preserve",
            order="encounter",
        ),
    )

    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=bidirectional,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
    )

    assert actual == expected
    assert actual[0] == Edge("urn:native-direct#Z", SUBCLASS_OF, "urn:native-direct#Top")
    equivalent_index = 2 if bidirectional else 1
    assert actual[equivalent_index] == Edge(
        "urn:native-direct#AA",
        SUBCLASS_OF,
        "urn:native-direct#B",
    )
    if bidirectional:
        assert actual[equivalent_index + 1] == Edge(
            "urn:native-direct#B",
            SUPERCLASS_OF,
            "urn:native-direct#AA",
        )
    assert actual[-1] == Edge("urn:native-direct#i", RDF_TYPE, "urn:native-direct#Z")
    assert statistics.roots == 8
    assert statistics.declarations == 5
    assert statistics.subclasses == 1
    assert statistics.equivalents == 1
    assert statistics.class_assertions == 1
    assert statistics.edges == len(expected)
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_asserted_taxonomy_mode_preflights_and_suppresses_adjacent_axioms() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) EquivalentClasses(:A :C :D) ClassAssertion(:A :i)"
    )
    lease = _lease(view)
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == expected
    assert statistics.subclasses == 1
    assert statistics.equivalents == 1
    assert statistics.class_assertions == 1
    assert statistics.edges == 2


@pytest.mark.parametrize(
    ("bidirectional", "only_taxonomy"),
    [(False, False), (True, False), (False, True)],
)
def test_named_restrictions_and_domain_range_products_match_python_oracle(
    bidirectional: bool,
    only_taxonomy: bool,
) -> None:
    view = _snapshot(
        "SubClassOf(:TaxA :TaxB) "
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "SubClassOf(ObjectAllValuesFrom(:p :C) :D) "
        "SubClassOf(:E ObjectMinCardinality(256 :p :F)) "
        "SubClassOf(ObjectMaxCardinality(3 :p :G) :H) "
        "EquivalentClasses(:Y :Z) ClassAssertion(:Y :i) "
        "ObjectPropertyDomain(:p :D2) ObjectPropertyDomain(:p :D1) "
        "ObjectPropertyRange(:p :R2) ObjectPropertyRange(:p :R1) "
        "ObjectPropertyDomain(:q :QD) ObjectPropertyRange(:q :QR)"
    )
    lease = _lease(view)
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            bidirectional_taxonomy=bidirectional,
            only_taxonomy=only_taxonomy,
            duplicates="preserve",
            order="encounter",
        ),
    )

    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=bidirectional,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert statistics.roots == 13
    assert statistics.subclasses == 5
    assert statistics.restriction_subclasses == 4
    assert statistics.equivalents == 1
    assert statistics.class_assertions == 1
    assert statistics.object_property_domains == 3
    assert statistics.object_property_ranges == 3
    assert statistics.domain_range_edges == 5
    assert statistics.edges == len(expected)
    assert actual[-5:] == [
        Edge("urn:native-direct#D1", "urn:native-direct#p", "urn:native-direct#R1"),
        Edge("urn:native-direct#D1", "urn:native-direct#p", "urn:native-direct#R2"),
        Edge("urn:native-direct#D2", "urn:native-direct#p", "urn:native-direct#R1"),
        Edge("urn:native-direct#D2", "urn:native-direct#p", "urn:native-direct#R2"),
        Edge("urn:native-direct#QD", "urn:native-direct#q", "urn:native-direct#QR"),
    ]
    restriction_edges = [
        edge
        for edge in actual
        if edge.relation == "urn:native-direct#p"
        and edge.source in {"urn:native-direct#A", "urn:native-direct#D"}
    ]
    assert len(restriction_edges) == (0 if only_taxonomy else 2)
    assert statistics.ingestion_counters["native_boundary_calls"] == 1


def test_asserted_taxonomy_mode_suppresses_preflighted_role_family() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :C)) "
        "ObjectPropertyDomain(:p :D) ObjectPropertyRange(:p :R)"
    )
    lease = _lease(view)
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == expected
    assert statistics.subclasses == 2
    assert statistics.restriction_subclasses == 1
    assert statistics.object_property_domains == 1
    assert statistics.object_property_ranges == 1
    assert statistics.domain_range_edges == 0


@pytest.mark.parametrize("only_taxonomy", [False, True])
def test_named_object_assertions_and_negative_inverse_skips_match_python_oracle(
    only_taxonomy: bool,
) -> None:
    view = _snapshot(
        "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:r :C)) "
        "ClassAssertion(:A :i) ObjectPropertyAssertion(:p :i :j) "
        "ObjectPropertyAssertion(:q :j :i) NegativeObjectPropertyAssertion(:p :j :i) "
        "NegativeObjectPropertyAssertion(ObjectInverseOf(:q) :i :j) "
        "ObjectPropertyDomain(:r :D) ObjectPropertyRange(:r :R)"
    )
    lease = _lease(view)
    expected = Projector().project(
        view,
        options=ProjectionOptions(
            backend="python",
            only_taxonomy=only_taxonomy,
            duplicates="preserve",
            order="encounter",
        ),
    )
    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=False,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        only_taxonomy=only_taxonomy,
    )

    assert actual == expected
    assert statistics.roots == 9
    assert statistics.object_property_assertions == 2
    assert statistics.negative_object_property_assertions == 2
    assert statistics.skipped_axioms == 2
    assert statistics.edges == len(expected)
    assertion_edges = [
        Edge("urn:native-direct#i", "urn:native-direct#p", "urn:native-direct#j"),
        Edge("urn:native-direct#j", "urn:native-direct#q", "urn:native-direct#i"),
    ]
    assertion_start = 3 if not only_taxonomy else 2
    assert actual[assertion_start : assertion_start + 2] == assertion_edges
    assert actual[-1] == Edge(
        "urn:native-direct#D",
        "urn:native-direct#r",
        "urn:native-direct#R",
    )


def test_asserted_taxonomy_suppresses_preflighted_object_assertions_and_skips() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) ObjectPropertyAssertion(:p :i :j) "
        "NegativeObjectPropertyAssertion(ObjectInverseOf(:p) :j :i)"
    )
    lease = _lease(view)
    expected = list(
        iter_asserted_taxonomy(
            view,
            bidirectional=True,
            duplicates="preserve",
            order="encounter",
        )
    )
    compiler = prepare_native_encoded_direct(lease)
    actual, statistics = compiler.compile_batch(
        bidirectional=True,
        max_edges=len(expected),
        max_iri_bytes=1024 * 1024,
        asserted_taxonomy_only=True,
    )

    assert actual == expected
    assert statistics.object_property_assertions == 1
    assert statistics.negative_object_property_assertions == 1
    assert statistics.skipped_axioms == 0


def test_positive_inverse_object_assertion_preserves_reference_failure() -> None:
    compiler = prepare_native_encoded_direct(
        _lease(_snapshot("ObjectPropertyAssertion(ObjectInverseOf(:p) :i :j)"))
    )
    with pytest.raises(UnsupportedAxiomShapeError, match="inverse object-property") as raised:
        compiler.compile_batch(
            bidirectional=False,
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )
    assert raised.value.details == {
        "constructor": "ObjectInverseOf",
        "reference_error": "java.lang.ClassCastException",
    }
    assert compiler.state == "failed"


def test_unsupported_constructor_and_exporters_are_rejected_before_output() -> None:
    constructor_lease = _lease(_snapshot("DisjointClasses(:A :B)"))
    compiler = prepare_native_encoded_direct(constructor_lease)
    with pytest.raises(NativeEncodedDirectUnsupported, match="schema tag 63"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"

    direct = _lease(_snapshot("SubClassOf(:A :B)"))
    root_kinds = bytes(direct.buffers["root_kinds"])
    sliced_owner = b"x" + root_kinds
    sliced = _replace_buffers(direct, {"root_kinds": memoryview(sliced_owner)[1:]})
    with pytest.raises(
        NativeEncodedDirectUnsupported,
        match="does not cover its complete bytes exporter",
    ):
        prepare_native_encoded_direct(sliced)

    readonly_bytearray = memoryview(bytearray(root_kinds)).toreadonly()
    non_bytes = _replace_buffers(direct, {"root_kinds": readonly_bytearray})
    with pytest.raises(
        NativeEncodedDirectUnsupported,
        match="not backed by exact immutable bytes",
    ):
        prepare_native_encoded_direct(non_bytes)


@pytest.mark.parametrize(
    "body",
    [
        "EquivalentClasses(:A ObjectIntersectionOf(:B :C))",
        "ClassAssertion(:A _:anonymous)",
        'EquivalentClasses(Annotation(<urn:meta> "unsupported") :A :B)',
    ],
    ids=["complex-equivalent", "anonymous-individual", "annotated-equivalent"],
)
def test_valid_but_out_of_slice_class_axioms_are_transactionally_unsupported(body: str) -> None:
    compiler = prepare_native_encoded_direct(_lease(_snapshot(body)))
    with pytest.raises(NativeEncodedDirectUnsupported):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_equivalent_set_corruption_and_mixed_edge_limit_fail_before_publication() -> None:
    lease = _lease(_snapshot("EquivalentClasses(:Z :AA :B)"))
    values = bytearray(lease.buffers["item_values"])
    first = bytes(values[:8])
    values[:8] = values[8:16]
    values[8:16] = first
    hostile = _replace_buffers(lease, {"item_values": memoryview(bytes(values))})
    malformed = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match="sorted and unique"):
        malformed.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed.state == "failed"

    mixed = _lease(
        _snapshot("SubClassOf(:A :B) EquivalentClasses(:A :C) ClassAssertion(:A :i)")
    )
    limited = prepare_native_encoded_direct(mixed)
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        limited.compile_batch(
            bidirectional=True,
            max_edges=4,
            max_iri_bytes=1024 * 1024,
        )
    assert limited.state == "failed"


def test_nonminimal_cardinality_and_domain_range_limit_fail_before_publication() -> None:
    lease = _lease(
        _snapshot("SubClassOf(:A ObjectMinCardinality(256 :p :B))")
    )
    scalar = bytearray(lease.buffers["scalar_bytes"])
    offset = scalar.index(b"\x00\x01")
    scalar[offset + 1] = 0
    hostile = _replace_buffers(lease, {"scalar_bytes": memoryview(bytes(scalar))})
    malformed = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match="minimally encoded"):
        malformed.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed.state == "failed"

    domains = " ".join(f"ObjectPropertyDomain(:p :D{index:02d})" for index in range(20))
    ranges = " ".join(f"ObjectPropertyRange(:p :R{index:02d})" for index in range(20))
    product = prepare_native_encoded_direct(_lease(_snapshot(f"{domains} {ranges}")))
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        product.compile_batch(
            bidirectional=False,
            max_edges=399,
            max_iri_bytes=1024 * 1024,
        )
    assert product.state == "failed"


@pytest.mark.parametrize(
    "body",
    [
        "SubClassOf(:A ObjectSomeValuesFrom(ObjectInverseOf(:p) :B))",
        "SubClassOf(:A ObjectSomeValuesFrom(:p ObjectIntersectionOf(:B :C)))",
        "SubClassOf(:A ObjectExactCardinality(1 :p :B))",
        "SubClassOf(ObjectSomeValuesFrom(:p :A) ObjectAllValuesFrom(:q :B))",
        "ObjectPropertyDomain(:p ObjectIntersectionOf(:A :B))",
        'ObjectPropertyRange(Annotation(<urn:meta> "unsupported") :p :R)',
    ],
    ids=[
        "inverse-property",
        "complex-filler",
        "exact-cardinality",
        "restriction-pair",
        "complex-domain",
        "annotated-range",
    ],
)
def test_valid_but_out_of_slice_role_shapes_are_transactionally_unsupported(body: str) -> None:
    compiler = prepare_native_encoded_direct(_lease(_snapshot(body)))
    with pytest.raises(NativeEncodedDirectUnsupported):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


@pytest.mark.parametrize(
    "body",
    [
        "ObjectPropertyAssertion(:p _:anonymous :i)",
        "NegativeObjectPropertyAssertion(:p :i _:anonymous)",
        'ObjectPropertyAssertion(Annotation(<urn:meta> "unsupported") :p :i :j)',
        'NegativeObjectPropertyAssertion(Annotation(<urn:meta> "unsupported") :p :i :j)',
    ],
    ids=[
        "anonymous-positive",
        "anonymous-negative",
        "annotated-positive",
        "annotated-negative",
    ],
)
def test_out_of_slice_object_assertion_boundaries_are_transactionally_unsupported(
    body: str,
) -> None:
    compiler = prepare_native_encoded_direct(_lease(_snapshot(body)))
    with pytest.raises(NativeEncodedDirectUnsupported):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_hostile_object_assertion_individual_and_edge_limit_fail_before_publication() -> None:
    lease = _lease(_snapshot("ObjectPropertyAssertion(:p :i :j)"))
    tags = lease.buffers["node_tags"]
    assertion_id = next(
        node_id
        for node_id in range(1, tags.nbytes // 2 + 1)
        if int.from_bytes(tags[(node_id - 1) * 2 : node_id * 2], "little") == 113
    )
    offsets = lease.buffers["node_field_offsets"]
    field_start = int.from_bytes(
        offsets[(assertion_id - 1) * 8 : assertion_id * 8],
        "little",
    )
    values = bytearray(lease.buffers["field_values"])
    property_id = bytes(values[field_start * 8 : field_start * 8 + 8])
    source_offset = (field_start + 1) * 8
    values[source_offset : source_offset + 8] = property_id
    hostile = _replace_buffers(lease, {"field_values": memoryview(bytes(values))})
    malformed = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match="named individual"):
        malformed.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert malformed.state == "failed"

    assertions = " ".join(
        f"ObjectPropertyAssertion(:p :i{index:03d} :j{index:03d})" for index in range(250)
    )
    limited = prepare_native_encoded_direct(_lease(_snapshot(assertions)))
    with pytest.raises(ProjectionResourceError, match="configured edge resources"):
        limited.compile_batch(
            bidirectional=False,
            max_edges=249,
            max_iri_bytes=1024 * 1024,
        )
    assert limited.state == "failed"


def test_descriptor_binding_and_hostile_supported_rows_fail_closed() -> None:
    lease = _lease(_snapshot("SubClassOf(:A :B)"))
    mismatched = replace(lease, descriptor_sha256="00" * 32)
    with pytest.raises(SnapshotCompatibilityError, match="descriptor digest differs"):
        prepare_native_encoded_direct(mismatched)

    root_ids = bytearray(lease.buffers["root_ids"])
    root_ids[0:4] = (2**32 - 1).to_bytes(4, "little")
    hostile = _replace_buffers(lease, {"root_ids": memoryview(bytes(root_ids))})
    compiler = prepare_native_encoded_direct(hostile)
    with pytest.raises(SnapshotCompatibilityError, match="node reference is out of range"):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=10,
            max_iri_bytes=1024 * 1024,
        )
    assert compiler.state == "failed"


def test_native_owner_and_exact_bytes_exporters_live_until_handle_drop() -> None:
    view = _snapshot(
        "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "EquivalentClasses(:A :C) ClassAssertion(:A :individual) "
        "ObjectPropertyAssertion(:p :individual :other) "
        "NegativeObjectPropertyAssertion(ObjectInverseOf(:p) :other :individual) "
        "ObjectPropertyDomain(:p :A) ObjectPropertyRange(:p :B)"
    )
    lease = _lease(view)
    exporter = cast(bytes, lease.buffers["scalar_bytes"].obj)
    before = sys.getrefcount(exporter)
    compiler = prepare_native_encoded_direct(lease)
    assert sys.getrefcount(exporter) >= before + 1
    del compiler
    gc.collect()
    assert sys.getrefcount(exporter) == before

    class Owner:
        pass

    def create() -> tuple[NativeEncodedDirectCompiler, weakref.ReferenceType[object]]:
        view = _snapshot(
            "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
            "EquivalentClasses(:A :C) ClassAssertion(:A :individual) "
            "ObjectPropertyAssertion(:p :individual :other) "
            "NegativeObjectPropertyAssertion(ObjectInverseOf(:p) :other :individual) "
            "ObjectPropertyDomain(:p :A) ObjectPropertyRange(:p :B)"
        )
        lease = _lease(view)
        owner = Owner()
        segment = SimpleNamespace(
            role=1,
            owner=owner,
            source=None,
            posting_mode=0,
            root_ids=memoryview(b""),
            anonymous_scope_map=memoryview(b""),
            member_token=None,
        )
        encoded = SimpleNamespace(
            schema_name=lease.schema_name,
            schema_version=lease.schema_version,
            model_schema=lease.model_schema,
            owner=owner,
            descriptor=cast(Any, lease.encoded_view).descriptor,
            buffers=lease.buffers,
            segments=(segment,),
        )
        retained = replace(
            lease,
            encoded_view=encoded,
            owner=owner,
            segments=(segment,),
        )
        return prepare_native_encoded_direct(retained), weakref.ref(owner)

    compiler, owner_ref = create()
    gc.collect()
    assert owner_ref() is not None
    del compiler
    gc.collect()
    assert owner_ref() is None


def test_detached_work_releases_the_gil_and_accepts_concurrent_cancel() -> None:
    lease = _lease(
        _snapshot(
            "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
            "EquivalentClasses(:A :C) ClassAssertion(:A :individual) "
            "ObjectPropertyAssertion(:p :individual :other) "
            "NegativeObjectPropertyAssertion(ObjectInverseOf(:p) :other :individual) "
            "ObjectPropertyDomain(:p :A) ObjectPropertyRange(:p :B)"
        )
    )
    compiler = prepare_native_encoded_direct(lease)
    module = load_native_module()
    kernel = compiler._kernel

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(kernel.test_wait_for_cancel, 100_000_000)
        deadline = time.monotonic() + 5
        while compiler.state != "running" and time.monotonic() < deadline:
            time.sleep(0)
        assert compiler.state == "running"
        assert compiler.cancel() is True
        with pytest.raises(module.EncodedDirectCancelledError):
            future.result(timeout=5)
    assert compiler.state == "cancelled"
    with pytest.raises(NativeEncodedDirectCancelled):
        compiler.compile_batch(
            bidirectional=False,
            max_edges=6,
            max_iri_bytes=1024 * 1024,
        )


def test_encoded_capability_remains_unadvertised() -> None:
    assert ENCODED_NATIVE_FEATURE not in frozenset(load_native_module().FEATURES)
