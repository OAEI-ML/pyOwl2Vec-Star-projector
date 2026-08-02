"""Pure-Python compiler for the pinned mOWL OWL2Vec* profile.

This module consumes only public immutable ``pyowl-core`` structural values.
All compatibility ordering and the historical mutable role maps stay private to
the projector; none of them alter core equality, fingerprints, or view state.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, fields
from typing import Any, cast

from pyowl_core.model import (
    IRI,
    RDF_PLAIN_LITERAL_IRI,
    XSD_STRING_IRI,
    Annotation,
    AnnotationAssertion,
    AnonymousIndividual,
    AxiomNode,
    ClassAssertion,
    Declaration,
    EntityKind,
    EquivalentClasses,
    InverseObjectProperties,
    Literal,
    NamedIndividual,
    ObjectAllValuesFrom,
    ObjectIntersectionOf,
    ObjectInverseOf,
    ObjectMaxCardinality,
    ObjectMinCardinality,
    ObjectProperty,
    ObjectPropertyAssertion,
    ObjectPropertyDomain,
    ObjectPropertyRange,
    ObjectSomeValuesFrom,
    ObjectUnionOf,
    StructuralNode,
    SubClassOf,
    SubObjectPropertyOf,
    canonical_bytes,
)
from pyowl_core.model import (
    Class as OWLClass,
)

from ._version import (
    CORE_ADAPTER_PROTOCOL_VERSION,
    CORE_API_VERSION,
    CORE_MODEL_SCHEMA_VERSION,
    CORE_WIRE_FORMAT_VERSION,
)
from .diagnostics import ProjectionDiagnostic
from .errors import SnapshotCompatibilityError, UnsupportedAxiomShapeError
from .model import Edge
from .options import ProjectionOptions
from .protocols import OntologyViewLike

SUBCLASS_OF = "http://subclassof"
SUPERCLASS_OF = "http://superclassof"
RDF_TYPE = "http://type"
OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
RDFS_NAMESPACE = "http://www.w3.org/2000/01/rdf-schema#"

_ANNOTATION_PROPERTIES = frozenset(
    {
        "http://www.w3.org/2000/01/rdf-schema#label",
        "http://www.w3.org/2004/02/skos/core#prefLabel",
        "rdfs:label",
        "rdfs:comment",
        "http://purl.obolibrary.org/obo/IAO_0000111",
        "http://purl.obolibrary.org/obo/IAO_0000589",
        "http://www.geneontology.org/formats/oboInOwl#hasRelatedSynonym",
        "http://www.geneontology.org/formats/oboInOwl#hasExactSynonym",
        "http://www.geneontology.org/formats/oboInOWL#hasExactSynonym",
        "http://purl.bioontology.org/ontology/SYN#synonym",
        "http://scai.fraunhofer.de/CSEO#Synonym",
        "http://purl.obolibrary.org/obo/synonym",
        "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#FULL_SYN",
        "http://www.ebi.ac.uk/efo/alternative_term",
        "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#Synonym",
        "http://bioontology.org/projects/ontologies/fma/fmaOwlDlComponent_2_0#Synonym",
        "http://www.geneontology.org/formats/oboInOwl#hasDefinition",
        "http://bioontology.org/projects/ontologies/birnlex#preferred_label",
        "http://bioontology.org/projects/ontologies/birnlex#synonyms",
        "http://www.w3.org/2004/02/skos/core#altLabel",
        "https://cfpub.epa.gov/ecotox#latinName",
        "https://cfpub.epa.gov/ecotox#commonName",
        "https://www.ncbi.nlm.nih.gov/taxonomy#scientific_name",
        "https://www.ncbi.nlm.nih.gov/taxonomy#synonym",
        "https://www.ncbi.nlm.nih.gov/taxonomy#equivalent_name",
        "https://www.ncbi.nlm.nih.gov/taxonomy#genbank_synonym",
        "https://www.ncbi.nlm.nih.gov/taxonomy#common_name",
        "http://purl.obolibrary.org/obo/IAO_0000118",
        "http://www.w3.org/2000/01/rdf-schema#comment",
        "http://www.geneontology.org/formats/oboInOwl#hasDbXref",
        "http://purl.org/dc/elements/1.1/description",
        "http://purl.org/dc/terms/description",
        "http://purl.org/dc/elements/1.1/title",
        "http://purl.org/dc/terms/title",
        "http://purl.obolibrary.org/obo/IAO_0000115",
        "http://purl.obolibrary.org/obo/IAO_0000600",
        "http://purl.obolibrary.org/obo/IAO_0000602",
        "http://purl.obolibrary.org/obo/IAO_0000601",
        "http://www.geneontology.org/formats/oboInOwl#hasOBONamespace",
    }
)


def _int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _java_string_hash(value: str) -> int:
    # Java hashes UTF-16 code units, not Unicode scalar values.
    encoded = value.encode("utf-16-be")
    result = 0
    for index in range(0, len(encoded), 2):
        unit = (encoded[index] << 8) | encoded[index + 1]
        result = _int32(31 * result + unit)
    return result


def _owlapi_iri_hash(value: str) -> int:
    """OWLAPI 4.5 IRI namespace-plus-remainder hash."""
    split_at = max(value.rfind("#"), value.rfind("/"), value.rfind(":"))
    namespace = value[: split_at + 1] if split_at >= 0 else ""
    remainder = value[split_at + 1 :] if split_at >= 0 else value
    return _int32(_java_string_hash(namespace) + _java_string_hash(remainder))


def _combine(seed: int, *components: int) -> int:
    result = seed
    for component in components:
        result = _int32(31 * result + component)
    return result


def _set_hash(values: Iterable[object]) -> int:
    result = 0
    for value in values:
        result = _int32(result + _owlapi_hash(value))
    return result


def _annotation_hash(annotation: Annotation) -> int:
    # HashCode.primes[81] == 6311. Nested annotations are not part of the
    # OWLAnnotation hash in OWLAPI 4.5.22.
    return _combine(6311, _owlapi_hash(annotation.property), _owlapi_hash(annotation.value))


def _owlapi_hash(value: object) -> int:
    """Relevant subset of OWLAPI 4.5.22 structural hashCode."""
    if isinstance(value, IRI):
        return _owlapi_iri_hash(value.value)
    if isinstance(value, OWLClass):
        return _combine(2293, _owlapi_iri_hash(value.iri.value))
    if isinstance(value, ObjectProperty):
        return _combine(4153, _owlapi_iri_hash(value.iri.value))
    if isinstance(value, ObjectInverseOf):
        return _combine(4241, _owlapi_hash(value.property))
    if isinstance(value, ObjectAllValuesFrom):
        return _combine(2833, _owlapi_hash(value.property), _owlapi_hash(value.filler))
    if isinstance(value, ObjectSomeValuesFrom):
        return _combine(3517, _owlapi_hash(value.property), _owlapi_hash(value.filler))
    if isinstance(value, ObjectMinCardinality):
        return _combine(
            3259,
            _owlapi_hash(value.property),
            value.cardinality,
            _owlapi_hash(value.filler),
        )
    if isinstance(value, ObjectMaxCardinality):
        return _combine(
            3187,
            _owlapi_hash(value.property),
            value.cardinality,
            _owlapi_hash(value.filler),
        )
    if isinstance(value, ObjectIntersectionOf):
        return _combine(3083, _set_hash(value.operands))
    if isinstance(value, ObjectUnionOf):
        return _combine(3581, _set_hash(value.operands))
    if isinstance(value, Annotation):
        return _annotation_hash(value)
    if isinstance(value, SubObjectPropertyOf):
        return _combine(
            1823,
            _owlapi_hash(value.sub_property),
            _owlapi_hash(value.super_property),
            _set_hash(value.annotations),
        )
    if isinstance(value, InverseObjectProperties):
        first = _owlapi_hash(value.first)
        second = _owlapi_hash(value.second)
        return _combine(1229, _int32(first + second), _set_hash(value.annotations))
    if isinstance(value, Literal):
        # Literal hashes are only used as a deterministic collision tie here;
        # the compatibility-sensitive collections contain class expressions.
        return _java_string_hash(value.lexical_form)
    if isinstance(value, AnonymousIndividual):
        return _java_string_hash(value.local_key.decode("utf-8", "surrogateescape"))
    iri = getattr(value, "iri", None)
    if isinstance(iri, IRI):
        return _owlapi_iri_hash(iri.value)
    return _java_string_hash(type(value).__name__)


def _canonical_key(value: AxiomNode | object) -> bytes:
    try:
        return canonical_bytes(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return repr(value).encode("utf-8", "backslashreplace")


def _hashset_order(values: Iterable[Any]) -> list[Any]:
    """Projector-private OWLAPI/Java hash traversal compatibility order."""
    items = list(values)
    capacity = 16
    while len(items) > int(capacity * 0.75):
        capacity *= 2

    def key(value: object) -> tuple[int, int, bytes]:
        unsigned = _owlapi_hash(value) & 0xFFFFFFFF
        spread = unsigned ^ (unsigned >> 16)
        return spread & (capacity - 1), spread, _canonical_key(value)

    return sorted(items, key=key)


def _expression_order(values: Iterable[Any]) -> list[Any]:
    """OWLAPI 4.5 ``sortOptionally`` order for class expressions.

    N-ary class expressions are stored as sorted lists by OWLAPI, unlike the
    RBox set whose hash traversal is emulated above.  The type indexes are the
    public OWLAPI 4.5 expression indexes relevant to this profile.
    """

    indexes = {
        OWLClass: 1001,
        ObjectIntersectionOf: 3001,
        ObjectUnionOf: 3002,
        ObjectSomeValuesFrom: 3005,
        ObjectAllValuesFrom: 3006,
        ObjectMinCardinality: 3008,
        ObjectMaxCardinality: 3010,
    }

    def key(value: object) -> tuple[int, bytes]:
        if isinstance(value, OWLClass):
            return indexes[OWLClass], value.iri.value.encode("utf-8")
        return indexes.get(type(value), 3999), _canonical_key(value)

    return sorted(values, key=key)


@dataclass(slots=True)
class RoleState:
    """Compiler-private state reproducing the Scala field lifecycle."""

    subroles: dict[str, tuple[str, ...]]
    inverse_roles: dict[str, str]

    @classmethod
    def empty(cls) -> RoleState:
        return cls({}, {})


@dataclass(slots=True)
class CompileStatistics:
    raw_edges: int = 0
    distinct_edges: int = 0
    duplicate_edges: int = 0
    skipped_axioms: int = 0
    ignored_shapes: int = 0


class _DiagnosticBag:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str, str | None], int] = {}

    def add(
        self,
        code: str,
        message: str,
        *,
        severity: str = "info",
        constructor: str | None = None,
    ) -> None:
        key = (code, message, severity, constructor)
        self._items[key] = self._items.get(key, 0) + 1

    def freeze(self) -> tuple[ProjectionDiagnostic, ...]:
        result: list[ProjectionDiagnostic] = []
        for (code, message, severity, constructor), count in sorted(self._items.items()):
            result.append(
                ProjectionDiagnostic(
                    code=code,
                    message=message,
                    severity="warning" if severity == "warning" else "info",
                    count=count,
                    constructor=constructor,
                )
            )
        return tuple(result)


@dataclass(slots=True)
class Compilation:
    """Prepared invocation retaining references to the original core nodes."""

    view: OntologyViewLike
    options: ProjectionOptions
    role_state: RoleState
    subclasses: list[SubClassOf]
    equivalents: list[EquivalentClasses]
    annotations: list[AnnotationAssertion]
    class_assertions: list[ClassAssertion]
    object_assertions: list[ObjectPropertyAssertion]
    domains: list[ObjectPropertyDomain]
    ranges: list[ObjectPropertyRange]
    class_iris: frozenset[str]
    blank_ids: dict[AnonymousIndividual, str]
    statistics: CompileStatistics
    diagnostic_bag: _DiagnosticBag
    lazy: bool = False
    roles_prepared: bool = True
    closure_metadata_scanned: bool = True

    def iter_raw_edges(self) -> Iterator[Edge]:
        """Yield deterministic encounter-order edges category by category."""
        if self.lazy:
            yield from self._iter_lazy_raw_edges()
            return
        for subclass_axiom in self.subclasses:
            yield from self._subclass_edges(subclass_axiom)
        for equivalent_axiom in self.equivalents:
            yield from self._equivalent_edges(equivalent_axiom)
        if self.options.include_literals:
            for annotation_axiom in self.annotations:
                edge = self._annotation_edge(annotation_axiom)
                if edge is not None:
                    yield edge
        for class_axiom in self.class_assertions:
            if isinstance(class_axiom.individual, NamedIndividual) and isinstance(
                class_axiom.class_expression, OWLClass
            ):
                yield Edge(
                    class_axiom.individual.iri.value,
                    RDF_TYPE,
                    class_axiom.class_expression.iri.value,
                )
            else:
                self._ignore(class_axiom)
        for object_axiom in self.object_assertions:
            if isinstance(object_axiom.property, ObjectInverseOf):
                raise UnsupportedAxiomShapeError(
                    "the pinned mOWL profile fails on inverse object-property assertions",
                    details={
                        "constructor": type(object_axiom.property).__name__,
                        "reference_error": "java.lang.ClassCastException",
                    },
                )
            if not isinstance(object_axiom.property, ObjectProperty):
                self._ignore(object_axiom)
                continue
            yield Edge(
                self._individual_id(object_axiom.source),
                object_axiom.property.iri.value,
                self._individual_id(object_axiom.target),
            )
        yield from self._domain_range_edges()

    def _iter_lazy_raw_edges(self) -> Iterator[Edge]:
        """Traverse canonical core categories only as consumers reach them."""
        for axiom in _iter_axioms(self.view, root=False, axiom_type=SubClassOf):
            if isinstance(axiom, SubClassOf):
                yield from self._subclass_edges(axiom)
        for axiom in _iter_axioms(self.view, root=False, axiom_type=EquivalentClasses):
            if isinstance(axiom, EquivalentClasses):
                yield from self._equivalent_edges(axiom)
        if self.options.include_literals:
            self.class_iris = frozenset(_class_signature(self.view))
            for axiom in _iter_axioms(self.view, root=True, axiom_type=AnnotationAssertion):
                if isinstance(axiom, AnnotationAssertion):
                    edge = self._annotation_edge(axiom)
                    if edge is not None:
                        yield edge
        for axiom in _iter_axioms(self.view, root=False, axiom_type=ClassAssertion):
            if not isinstance(axiom, ClassAssertion):
                continue
            if isinstance(axiom.individual, NamedIndividual) and isinstance(
                axiom.class_expression, OWLClass
            ):
                yield Edge(
                    axiom.individual.iri.value,
                    RDF_TYPE,
                    axiom.class_expression.iri.value,
                )
            else:
                self._ignore(axiom)
        for axiom in _iter_axioms(self.view, root=False, axiom_type=ObjectPropertyAssertion):
            if not isinstance(axiom, ObjectPropertyAssertion):
                continue
            if isinstance(axiom.property, ObjectInverseOf):
                raise UnsupportedAxiomShapeError(
                    "the pinned mOWL profile fails on inverse object-property assertions",
                    details={
                        "constructor": type(axiom.property).__name__,
                        "reference_error": "java.lang.ClassCastException",
                    },
                )
            if not isinstance(axiom.property, ObjectProperty):
                self._ignore(axiom)
                continue
            yield Edge(
                self._individual_id(axiom.source),
                axiom.property.iri.value,
                self._individual_id(axiom.target),
            )
        self.domains = [
            axiom
            for axiom in _iter_axioms(self.view, root=False, axiom_type=ObjectPropertyDomain)
            if isinstance(axiom, ObjectPropertyDomain)
        ]
        self.ranges = [
            axiom
            for axiom in _iter_axioms(self.view, root=False, axiom_type=ObjectPropertyRange)
            if isinstance(axiom, ObjectPropertyRange)
        ]
        yield from self._domain_range_edges()
        # These scans are semantically observable through state and diagnostics,
        # but can follow all edges in isolated low-latency operation.
        self._ensure_roles()
        self._scan_closure_metadata()

    def _subclass_edges(self, axiom: SubClassOf) -> Iterator[Edge]:
        sub = axiom.sub_class
        sup = axiom.super_class
        if isinstance(sub, OWLClass) and isinstance(sup, OWLClass):
            yield Edge(sub.iri.value, SUBCLASS_OF, sup.iri.value)
            if self.options.bidirectional_taxonomy:
                yield Edge(sup.iri.value, SUPERCLASS_OF, sub.iri.value)
            return
        if self.options.only_taxonomy:
            self._ignore(axiom)
            return
        if isinstance(sub, OWLClass):
            edges = self._restriction_edges(sub.iri.value, sup)
            yield from edges
            if not edges:
                self._ignore(axiom)
            return
        if isinstance(sup, OWLClass):
            edges = self._restriction_edges(sup.iri.value, sub)
            yield from edges
            if not edges:
                self._ignore(axiom)
            return
        self._ignore(axiom)

    def _restriction_edges(self, subject: str, expression: object) -> tuple[Edge, ...]:
        if not isinstance(
            expression,
            (
                ObjectSomeValuesFrom,
                ObjectAllValuesFrom,
                ObjectMinCardinality,
                ObjectMaxCardinality,
            ),
        ):
            return ()
        if not isinstance(expression.filler, OWLClass):
            return ()
        relation = _named_property(expression.property)
        if relation is None:
            return ()
        self._ensure_roles()
        return tuple(self._role_edges(subject, relation, expression.filler.iri.value))

    def _role_edges(self, source: str, relation: str, destination: str) -> Iterator[Edge]:
        yield Edge(source, relation, destination)
        for subrole in self.role_state.subroles.get(relation, ()):
            yield Edge(source, subrole, destination)
        inverse = self.role_state.inverse_roles.get(relation)
        if inverse is not None:
            yield Edge(destination, inverse, source)

    def _equivalent_edges(self, axiom: EquivalentClasses) -> Iterator[Edge]:
        expressions = _expression_order(axiom.expressions)
        if len(expressions) < 2:  # guarded by core, retained for foreign adapters
            self._ignore(axiom)
            return
        first, second = expressions[0], expressions[1]
        if isinstance(second, OWLClass):
            if isinstance(first, OWLClass):
                yield Edge(first.iri.value, SUBCLASS_OF, second.iri.value)
                if self.options.bidirectional_taxonomy:
                    yield Edge(second.iri.value, SUPERCLASS_OF, first.iri.value)
            else:
                self._ignore(axiom)
            return
        if isinstance(second, (ObjectIntersectionOf, ObjectUnionOf)) and isinstance(
            first, OWLClass
        ):
            for operand in _expression_order(second.operands):
                if isinstance(operand, OWLClass):
                    yield Edge(first.iri.value, SUBCLASS_OF, operand.iri.value)
                    if self.options.bidirectional_taxonomy:
                        yield Edge(operand.iri.value, SUPERCLASS_OF, first.iri.value)
                elif not self.options.only_taxonomy:
                    edges = self._restriction_edges(first.iri.value, operand)
                    yield from edges
                    if not edges:
                        self._ignore(axiom)
            return
        self._ignore(axiom)

    def _annotation_edge(self, axiom: AnnotationAssertion) -> Edge | None:
        if not isinstance(axiom.subject, IRI) or axiom.subject.value not in self.class_iris:
            self._ignore(axiom)
            return None
        property_iri = axiom.property.iri.value
        if property_iri not in _ANNOTATION_PROPERTIES:
            self._ignore(axiom)
            return None
        relation = (
            "rdfs:" + property_iri.removeprefix(RDFS_NAMESPACE)
            if property_iri.startswith(RDFS_NAMESPACE)
            else property_iri
        )
        destination = self._annotation_value(axiom.value)
        return Edge(axiom.subject.value, relation, destination)

    def _annotation_value(self, value: IRI | Literal | AnonymousIndividual) -> str:
        if isinstance(value, IRI):
            return value.value
        if isinstance(value, AnonymousIndividual):
            return self._individual_id(value)
        datatype = value.datatype.iri.value
        if datatype in (XSD_STRING_IRI, RDF_PLAIN_LITERAL_IRI):
            return value.lexical_form
        rendered_datatype = _render_datatype(datatype)
        lexical = _owlapi_escape_literal(value.lexical_form)
        rendered = f'"{lexical}"^^{rendered_datatype}'
        stripped = rendered.replace("\\", "")
        if stripped.startswith('"'):
            stripped = stripped[1:-1]
        elif stripped.startswith("<"):
            stripped = stripped[1:-1]
        self.diagnostic_bag.add(
            "MOWL_NON_STRING_LITERAL_RENDERING",
            "pinned mOWL rendering preserves malformed datatype syntax",
            severity="warning",
            constructor="Literal",
        )
        return stripped

    def _individual_id(self, value: NamedIndividual | AnonymousIndividual) -> str:
        if isinstance(value, NamedIndividual):
            return value.iri.value
        if self.lazy and not self.closure_metadata_scanned:
            self._scan_closure_metadata()
        return self.blank_ids.get(value, _core_blank_id(value))

    def _domain_range_edges(self) -> Iterator[Edge]:
        self._ensure_roles()
        domains: dict[str, list[str]] = {}
        ranges: dict[str, list[str]] = {}
        for domain_axiom in self.domains:
            if isinstance(domain_axiom.property, ObjectProperty) and isinstance(
                domain_axiom.domain, OWLClass
            ):
                domains.setdefault(domain_axiom.property.iri.value, []).append(
                    domain_axiom.domain.iri.value
                )
            else:
                self._ignore(domain_axiom)
        for range_axiom in self.ranges:
            if isinstance(range_axiom.property, ObjectProperty) and isinstance(
                range_axiom.range, OWLClass
            ):
                ranges.setdefault(range_axiom.property.iri.value, []).append(
                    range_axiom.range.iri.value
                )
            else:
                self._ignore(range_axiom)
        properties = sorted(domains.keys() & ranges.keys(), key=lambda item: item.encode("utf-8"))
        for property_iri in properties:
            for domain in domains[property_iri]:
                for range_iri in ranges[property_iri]:
                    yield from self._role_edges(domain, property_iri, range_iri)

    def _ensure_roles(self) -> None:
        if self.roles_prepared:
            return
        rbox: list[SubObjectPropertyOf | InverseObjectProperties] = []
        for axiom_type in (SubObjectPropertyOf, InverseObjectProperties):
            for axiom in _iter_axioms(self.view, root=False, axiom_type=axiom_type):
                if isinstance(axiom, (SubObjectPropertyOf, InverseObjectProperties)):
                    rbox.append(axiom)
        _update_role_state(rbox, self.role_state, self.statistics)
        self.roles_prepared = True

    def prepare_role_state(self) -> None:
        """Eagerly apply role-map lifecycle for stateful compatibility calls."""
        self._ensure_roles()

    def _scan_closure_metadata(self) -> None:
        if self.closure_metadata_scanned:
            return
        anonymous: set[AnonymousIndividual] = set()
        selected = (
            SubClassOf,
            EquivalentClasses,
            ClassAssertion,
            ObjectPropertyAssertion,
            ObjectPropertyDomain,
            ObjectPropertyRange,
            SubObjectPropertyOf,
            InverseObjectProperties,
        )
        for axiom in _iter_axioms(self.view, root=False):
            anonymous.update(_anonymous_values(axiom))
            if isinstance(axiom, (Declaration, AnnotationAssertion, *selected)):
                continue
            if isinstance(axiom, AxiomNode):
                self.statistics.skipped_axioms += 1
                self.diagnostic_bag.add(
                    "MOWL_SKIPPED_AXIOM",
                    "axiom category is not visited by the pinned profile",
                    constructor=type(axiom).__name__,
                )
            else:
                raise SnapshotCompatibilityError(
                    "iter_axioms() yielded a non-core structural axiom",
                    details={"constructor": type(axiom).__name__},
                )
        ordered = sorted(anonymous, key=canonical_bytes)
        self.blank_ids = {
            item: f"_:genid{2_147_483_648 + index}" for index, item in enumerate(ordered)
        }
        self.closure_metadata_scanned = True

    def _ignore(self, value: object) -> None:
        self.statistics.ignored_shapes += 1
        constructor = type(value).__name__
        self.diagnostic_bag.add(
            "MOWL_IGNORED_SHAPE",
            "constructor does not emit an edge in the pinned profile",
            constructor=constructor,
        )

    @property
    def diagnostics(self) -> tuple[ProjectionDiagnostic, ...]:
        return self.diagnostic_bag.freeze()


def validate_view(view: object) -> OntologyViewLike:
    """Validate the frozen core adapter/model handshake without materializing."""
    if not callable(getattr(view, "iter_axioms", None)):
        raise SnapshotCompatibilityError(
            "projection requires a pyowl-core OntologyView with iter_axioms()"
        )
    if not callable(getattr(view, "signature", None)):
        raise SnapshotCompatibilityError(
            "projection requires a pyowl-core OntologyView with signature()"
        )
    try:
        capabilities = view.capabilities  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError) as error:
        raise SnapshotCompatibilityError(
            "projection requires the pyowl-core capabilities handshake"
        ) from error
    adapter = getattr(capabilities, "adapter_protocol", None)
    model = getattr(capabilities, "model_schema", None)
    wire = getattr(capabilities, "wire_format", None)
    core = importlib.import_module("pyowl_core")
    api = getattr(core, "API_VERSION", None)
    versions_are_typed = type(adapter) is int and type(model) is int
    api_is_typed = (
        isinstance(api, tuple)
        and len(api) == 2
        and all(type(item) is int and item >= 0 for item in api)
    )
    wire_is_typed = (
        isinstance(wire, tuple)
        and len(wire) == 2
        and all(type(item) is int and item >= 0 for item in wire)
    )
    actual_api = cast(tuple[int, int], api) if api_is_typed else (-1, -1)
    actual_wire = cast(tuple[int, int], wire) if wire_is_typed else (-1, -1)
    if (
        not versions_are_typed
        or actual_api != CORE_API_VERSION
        or adapter != CORE_ADAPTER_PROTOCOL_VERSION
        or model != CORE_MODEL_SCHEMA_VERSION
        or actual_wire != CORE_WIRE_FORMAT_VERSION
    ):
        raise SnapshotCompatibilityError(
            "incompatible pyowl-core API/adapter/model/wire schema",
            details={
                "expected_api_major": CORE_API_VERSION[0],
                "expected_api_minor": CORE_API_VERSION[1],
                "expected_adapter_protocol": CORE_ADAPTER_PROTOCOL_VERSION,
                "expected_model_schema": CORE_MODEL_SCHEMA_VERSION,
                "expected_wire_major": CORE_WIRE_FORMAT_VERSION[0],
                "expected_wire_minor": CORE_WIRE_FORMAT_VERSION[1],
                "actual_api_major": actual_api[0],
                "actual_api_minor": actual_api[1],
                "actual_adapter_protocol": adapter if type(adapter) is int else -1,
                "actual_model_schema": model if type(model) is int else -1,
                "actual_wire_major": actual_wire[0],
                "actual_wire_minor": actual_wire[1],
            },
        )
    return view  # type: ignore[return-value]


def prepare_compilation(
    view: object,
    options: ProjectionOptions,
    role_state: RoleState,
) -> Compilation:
    """Scan a view once into lightweight identity-preserving category indexes."""
    checked = validate_view(view)
    closure_axioms = list(_iter_axioms(checked, root=False))
    statistics = CompileStatistics()
    diagnostic_bag = _DiagnosticBag()

    subclasses: list[SubClassOf] = []
    equivalents: list[EquivalentClasses] = []
    class_assertions: list[ClassAssertion] = []
    object_assertions: list[ObjectPropertyAssertion] = []
    domains: list[ObjectPropertyDomain] = []
    ranges: list[ObjectPropertyRange] = []
    rbox: list[SubObjectPropertyOf | InverseObjectProperties] = []

    selected = (
        SubClassOf,
        EquivalentClasses,
        ClassAssertion,
        ObjectPropertyAssertion,
        ObjectPropertyDomain,
        ObjectPropertyRange,
        SubObjectPropertyOf,
        InverseObjectProperties,
    )
    for axiom in closure_axioms:
        if isinstance(axiom, SubClassOf):
            subclasses.append(axiom)
        elif isinstance(axiom, EquivalentClasses):
            equivalents.append(axiom)
        elif isinstance(axiom, ClassAssertion):
            class_assertions.append(axiom)
        elif isinstance(axiom, ObjectPropertyAssertion):
            object_assertions.append(axiom)
        elif isinstance(axiom, ObjectPropertyDomain):
            domains.append(axiom)
        elif isinstance(axiom, ObjectPropertyRange):
            ranges.append(axiom)
        elif isinstance(axiom, (SubObjectPropertyOf, InverseObjectProperties)):
            rbox.append(axiom)
        elif isinstance(axiom, (Declaration, AnnotationAssertion)):
            continue
        elif isinstance(axiom, AxiomNode):
            statistics.skipped_axioms += 1
            diagnostic_bag.add(
                "MOWL_SKIPPED_AXIOM",
                "axiom category is not visited by the pinned profile",
                constructor=type(axiom).__name__,
            )
        elif not isinstance(axiom, selected):
            raise SnapshotCompatibilityError(
                "iter_axioms() yielded a non-core structural axiom",
                details={"constructor": type(axiom).__name__},
            )

    _update_role_state(rbox, role_state, statistics)

    annotations = [
        axiom
        for axiom in _iter_axioms(checked, root=True, axiom_type=AnnotationAssertion)
        if isinstance(axiom, AnnotationAssertion)
    ]
    class_iris = frozenset(_class_signature(checked))
    anonymous = sorted(
        {item for axiom in closure_axioms for item in _anonymous_values(axiom)},
        key=canonical_bytes,
    )
    blank_ids = {item: f"_:genid{2_147_483_648 + index}" for index, item in enumerate(anonymous)}

    sort_key = _canonical_key
    return Compilation(
        view=checked,
        options=options,
        role_state=role_state,
        subclasses=sorted(subclasses, key=sort_key),
        equivalents=sorted(equivalents, key=sort_key),
        annotations=sorted(annotations, key=sort_key),
        class_assertions=sorted(class_assertions, key=sort_key),
        object_assertions=sorted(object_assertions, key=sort_key),
        domains=sorted(domains, key=sort_key),
        ranges=sorted(ranges, key=sort_key),
        class_iris=class_iris,
        blank_ids=blank_ids,
        statistics=statistics,
        diagnostic_bag=diagnostic_bag,
    )


def prepare_streaming_compilation(
    view: object,
    options: ProjectionOptions,
    role_state: RoleState,
) -> Compilation:
    """Create a traversal-lazy isolated compilation over the exact core view."""
    checked = validate_view(view)
    return Compilation(
        view=checked,
        options=options,
        role_state=role_state,
        subclasses=[],
        equivalents=[],
        annotations=[],
        class_assertions=[],
        object_assertions=[],
        domains=[],
        ranges=[],
        class_iris=frozenset(),
        blank_ids={},
        statistics=CompileStatistics(),
        diagnostic_bag=_DiagnosticBag(),
        lazy=True,
        roles_prepared=False,
        closure_metadata_scanned=False,
    )


def _update_role_state(
    rbox: Iterable[SubObjectPropertyOf | InverseObjectProperties],
    role_state: RoleState,
    statistics: CompileStatistics,
) -> None:
    for axiom in _hashset_order(rbox):
        if isinstance(axiom, SubObjectPropertyOf):
            sub = _named_property(axiom.sub_property)
            sup = _named_property(axiom.super_property)
            if sub is None or sup is None:
                statistics.ignored_shapes += 1
                continue
            # Historical bug: prior children are read under `sub`, not `sup`.
            role_state.subroles[sup] = (sub, *role_state.subroles.get(sub, ()))
        else:
            first = _named_property(axiom.first)
            second = _named_property(axiom.second)
            if first is None or second is None:
                statistics.ignored_shapes += 1
                continue
            role_state.inverse_roles[first] = second
            role_state.inverse_roles[second] = first


def iter_projected_edges(compilation: Compilation) -> Iterator[Edge]:
    """Apply duplicate policy and output ordering to one prepared compilation."""
    options = compilation.options
    if options.order == "canonical":
        raw = list(compilation.iter_raw_edges())
        compilation.statistics.raw_edges = len(raw)
        distinct_count = len(set(raw))
        compilation.statistics.distinct_edges = distinct_count
        compilation.statistics.duplicate_edges = len(raw) - distinct_count
        if options.duplicates == "unique":
            raw = list(dict.fromkeys(raw))
        raw.sort(key=Edge.canonical_key)
        yield from raw
        return

    seen: set[Edge] = set()
    for edge in compilation.iter_raw_edges():
        compilation.statistics.raw_edges += 1
        duplicate = edge in seen
        if duplicate:
            compilation.statistics.duplicate_edges += 1
        else:
            seen.add(edge)
            compilation.statistics.distinct_edges += 1
        if options.duplicates == "preserve" or not duplicate:
            yield edge


def iter_asserted_taxonomy(
    view: object,
    *,
    bidirectional: bool,
    duplicates: str,
    order: str,
) -> Iterator[Edge]:
    """Compile only named asserted SubClassOf axioms."""
    checked = validate_view(view)
    axioms = _iter_axioms(checked, root=False, axiom_type=SubClassOf)
    if duplicates == "preserve" and order == "encounter":
        for axiom in axioms:
            if (
                isinstance(axiom, SubClassOf)
                and isinstance(axiom.sub_class, OWLClass)
                and isinstance(axiom.super_class, OWLClass)
            ):
                yield Edge(
                    axiom.sub_class.iri.value,
                    SUBCLASS_OF,
                    axiom.super_class.iri.value,
                )
                if bidirectional:
                    yield Edge(
                        axiom.super_class.iri.value,
                        SUPERCLASS_OF,
                        axiom.sub_class.iri.value,
                    )
        return
    edges: list[Edge] = []
    for axiom in axioms:
        if (
            isinstance(axiom, SubClassOf)
            and isinstance(axiom.sub_class, OWLClass)
            and isinstance(axiom.super_class, OWLClass)
        ):
            edges.append(Edge(axiom.sub_class.iri.value, SUBCLASS_OF, axiom.super_class.iri.value))
            if bidirectional:
                edges.append(
                    Edge(axiom.super_class.iri.value, SUPERCLASS_OF, axiom.sub_class.iri.value)
                )
    if duplicates == "unique":
        edges = list(dict.fromkeys(edges))
    if order == "canonical":
        edges.sort(key=Edge.canonical_key)
    yield from edges


def _core_scope(name: str) -> object | None:
    try:
        import pyowl_core

        scope = getattr(pyowl_core, "AxiomScope", None)
        return getattr(scope, name, None) if scope is not None else None
    except ImportError:  # pragma: no cover - dependency import fails earlier
        return None


def _iter_axioms(
    view: OntologyViewLike,
    *,
    root: bool,
    axiom_type: type[object] | None = None,
) -> Iterable[object]:
    scope = _core_scope("ROOT" if root else "CLOSURE")
    if scope is not None:
        return view.iter_axioms(axiom_type, scope=scope)
    if root:
        root_document = getattr(view, "root", None)
        root_iterator = getattr(root_document, "iter_axioms", None)
        if callable(root_iterator):
            return cast(Iterable[object], root_iterator(axiom_type))
        # Activation seam used until core WP03 exports AxiomScope. Conforming
        # adapters may accept its frozen string value without copying records.
        return view.iter_axioms(axiom_type, scope="root")
    return view.iter_axioms(axiom_type)


def _class_signature(view: OntologyViewLike) -> Iterable[str]:
    signature = view.signature
    scope = _core_scope("CLOSURE")
    if scope is None:
        entities = signature(EntityKind.CLASS)
    else:
        entities = signature(EntityKind.CLASS, scope=scope)
    for entity in entities:
        if isinstance(entity, OWLClass):
            yield entity.iri.value


def _named_property(value: object) -> str | None:
    if isinstance(value, ObjectProperty):
        return value.iri.value
    if isinstance(value, ObjectInverseOf):
        return value.property.iri.value
    return None


def _core_blank_id(value: AnonymousIndividual) -> str:
    try:
        key = value.local_key.decode("utf-8")
    except UnicodeDecodeError:
        key = value.local_key.hex()
    if key.startswith("_:"):
        return key
    return "_:" + key


def _anonymous_values(value: object) -> Iterator[AnonymousIndividual]:
    if isinstance(value, AnonymousIndividual):
        yield value
        return
    if isinstance(value, StructuralNode):
        for item in fields(cast(Any, value)):
            yield from _anonymous_values(getattr(value, item.name))
        return
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _anonymous_values(item)


def _render_datatype(datatype: str) -> str:
    xsd = "http://www.w3.org/2001/XMLSchema#"
    if datatype.startswith(xsd):
        return "xsd:" + datatype.removeprefix(xsd)
    return f"<{datatype}>"


def _owlapi_escape_literal(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def structural_field_identities(value: object) -> tuple[int, ...]:
    """Test instrumentation proving that no shadow structural records are built."""
    try:
        return tuple(id(getattr(value, item.name)) for item in fields(cast(Any, value)))
    except TypeError:
        return ()


__all__ = [
    "OWL_THING",
    "RDF_TYPE",
    "SUBCLASS_OF",
    "SUPERCLASS_OF",
    "Compilation",
    "CompileStatistics",
    "RoleState",
    "iter_asserted_taxonomy",
    "iter_projected_edges",
    "prepare_compilation",
    "prepare_streaming_compilation",
    "structural_field_identities",
    "validate_view",
]
