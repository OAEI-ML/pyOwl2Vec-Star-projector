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

from pyowl2vec_star_projector import ProjectionResourceError, probe_native_backend
from pyowl2vec_star_projector.compiler import iter_asserted_taxonomy
from pyowl2vec_star_projector.encoded import (
    ENCODED_NATIVE_FEATURE,
    EncodedStructuralLease,
    _validate_encoded_view,
)
from pyowl2vec_star_projector.errors import SnapshotCompatibilityError
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


def test_unsupported_constructor_and_exporters_are_rejected_before_output() -> None:
    constructor_lease = _lease(_snapshot("EquivalentClasses(:A :B)"))
    compiler = prepare_native_encoded_direct(constructor_lease)
    with pytest.raises(NativeEncodedDirectUnsupported, match="schema tag 62"):
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
    view = _snapshot("SubClassOf(:A :B)")
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
        view = _snapshot("SubClassOf(:A :B)")
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
    lease = _lease(_snapshot("SubClassOf(:A :B)"))
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
            max_edges=1,
            max_iri_bytes=1024 * 1024,
        )


def test_encoded_capability_remains_unadvertised() -> None:
    assert ENCODED_NATIVE_FEATURE not in frozenset(load_native_module().FEATURES)
