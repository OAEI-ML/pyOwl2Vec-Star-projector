from __future__ import annotations

from dataclasses import replace

import pyowl_core
import pytest

from pyowl2vec_star_projector import ProjectionOptions, Projector, probe_native_backend

pytestmark = pytest.mark.skipif(
    not probe_native_backend().available, reason="optional native extension is not installed"
)


@pytest.mark.parametrize("order", ["canonical", "encounter"])
@pytest.mark.parametrize("duplicates", ["preserve", "unique"])
@pytest.mark.parametrize("batch_size", [1, 2, 7])
def test_native_annotation_membership_preserves_public_semantics(order, duplicates, batch_size):
    source = b"""Prefix(:=<urn:membership#>)
      Prefix(rdfs:=<http://www.w3.org/2000/01/rdf-schema#>)
      Prefix(xsd:=<http://www.w3.org/2001/XMLSchema#>)
      Ontology(<urn:membership>
        Declaration(Class(:A)) Declaration(ObjectProperty(:A))
        Declaration(Class(:B)) Declaration(NamedIndividual(:B))
        Declaration(ObjectProperty(:property)) Declaration(NamedIndividual(:individual))
        SubClassOf(:Referenced :A)
        AnnotationAssertion(rdfs:label :A "Class")
        AnnotationAssertion(rdfs:label :B "Punned"@en)
        AnnotationAssertion(rdfs:label :Referenced "Referenced")
        AnnotationAssertion(rdfs:label :a "Wrong case")
        AnnotationAssertion(rdfs:label :property "Property only")
        AnnotationAssertion(rdfs:label :individual "Individual only")
        AnnotationAssertion(rdfs:label :missing "Missing")
        AnnotationAssertion(:ignored :A "Not whitelisted")
        AnnotationAssertion(rdfs:label :A "7"^^xsd:integer)
        AnnotationAssertion(<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#FULL_SYN>
          :A "Synonym")
        AnnotationAssertion(rdfs:label :A <urn:iri-value>)
      )"""
    view = pyowl_core.load_snapshot(
        source,
        options=pyowl_core.LoadOptions(
            backend=pyowl_core.BackendPreference.NATIVE,
            imports=pyowl_core.ImportPolicy.IGNORE,
        ),
    )
    options = ProjectionOptions(
        backend="python", include_literals=True, order=order, duplicates=duplicates
    )
    reference = Projector()
    expected = list(reference.iter_edges(view, options=options, buffer_edges=batch_size))
    projector = Projector()
    actual = list(
        projector.iter_edges(
            view, options=replace(options, backend="native"), buffer_edges=batch_size
        )
    )
    assert actual == expected
    assert reference.last_report is not None and projector.last_report is not None
    assert projector.last_report.diagnostics == reference.last_report.diagnostics
    assert projector.last_report.provenance.counts == reference.last_report.provenance.counts
    counters = projector.last_report.provenance.ingestion.counters
    assert counters["native_class_index_builds"] == 1
    assert counters["native_class_index_keys"] == 3
    assert counters["native_class_membership_queries"] <= 22
    assert counters["native_class_membership_queries"] > 0
    assert (
        counters["native_class_index_peak_bytes"]
        >= counters["native_class_index_retained_bytes"]
        > 0
    )


@pytest.mark.parametrize("include_literals,only_taxonomy", [(False, False), (True, True)])
def test_suppressed_annotations_do_not_allocate_membership(include_literals, only_taxonomy):
    view = pyowl_core.load_snapshot(
        b"Ontology(<urn:no-index> Declaration(Class(<urn:A>)) "
        b'AnnotationAssertion(<http://www.w3.org/2000/01/rdf-schema#label> <urn:A> "A"))',
        options=pyowl_core.LoadOptions(backend=pyowl_core.BackendPreference.NATIVE),
    )
    projector = Projector()
    projector.project(
        view,
        options=ProjectionOptions(
            backend="native", include_literals=include_literals, only_taxonomy=only_taxonomy
        ),
    )
    assert projector.last_report is not None
    counters = projector.last_report.provenance.ingestion.counters
    assert counters["native_class_index_builds"] == 0
    assert counters["native_class_index_peak_bytes"] == 0
