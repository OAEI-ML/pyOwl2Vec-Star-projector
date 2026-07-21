//! No-copy compiler kernel for the private structural-columns v1 slice.
//!
//! This module deliberately contains no Python types.  The PyO3 boundary retains
//! immutable `bytes` exporters and lends their slices here while the GIL is
//! released.  The complete input is validated before the output vector is
//! allocated, so unsupported or malformed inputs cannot expose partial edges.

use std::borrow::Cow;
use std::sync::atomic::{AtomicU8, Ordering};

pub(crate) const BUFFER_COUNT: usize = 11;
pub(crate) const BUFFER_NAMES: [&str; BUFFER_COUNT] = [
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
];

pub(crate) const STATE_IDLE: u8 = 0;
pub(crate) const STATE_RUNNING: u8 = 1;
pub(crate) const STATE_FINISHED: u8 = 2;
pub(crate) const STATE_CANCELLED: u8 = 3;
pub(crate) const STATE_FAILED: u8 = 4;

const ROOT_ONTOLOGY_ANNOTATION: u8 = 1;
const ROOT_AXIOM: u8 = 2;
const ROOT_EXTENSION: u8 = 3;

const COMPONENT_NONE: u8 = 0;
const COMPONENT_NODE: u8 = 1;
const COMPONENT_TEXT: u8 = 2;
const COMPONENT_BYTES: u8 = 3;
const COMPONENT_INTEGER: u8 = 4;
const COMPONENT_ENUM: u8 = 5;
const COMPONENT_SET: u8 = 6;
const COMPONENT_SEQUENCE: u8 = 7;

const TAG_IRI: u16 = 1;
const TAG_ENTITY: u16 = 2;
const TAG_ANONYMOUS_INDIVIDUAL: u16 = 3;
const TAG_LITERAL: u16 = 4;
const TAG_ANNOTATION: u16 = 5;
const TAG_OBJECT_INVERSE_OF: u16 = 10;
const TAG_OBJECT_PROPERTY_CHAIN: u16 = 11;
const TAG_FACET_RESTRICTION: u16 = 20;
const TAG_DATA_INTERSECTION_OF: u16 = 21;
const TAG_DATA_UNION_OF: u16 = 22;
const TAG_DATA_COMPLEMENT_OF: u16 = 23;
const TAG_DATA_ONE_OF: u16 = 24;
const TAG_DATATYPE_RESTRICTION: u16 = 25;
const TAG_OBJECT_INTERSECTION_OF: u16 = 30;
const TAG_OBJECT_UNION_OF: u16 = 31;
const TAG_OBJECT_COMPLEMENT_OF: u16 = 32;
const TAG_OBJECT_ONE_OF: u16 = 33;
const TAG_OBJECT_SOME_VALUES_FROM: u16 = 34;
const TAG_OBJECT_ALL_VALUES_FROM: u16 = 35;
const TAG_OBJECT_HAS_VALUE: u16 = 36;
const TAG_OBJECT_HAS_SELF: u16 = 37;
const TAG_OBJECT_MIN_CARDINALITY: u16 = 38;
const TAG_OBJECT_MAX_CARDINALITY: u16 = 39;
const TAG_OBJECT_EXACT_CARDINALITY: u16 = 40;
const TAG_DATA_SOME_VALUES_FROM: u16 = 41;
const TAG_DATA_ALL_VALUES_FROM: u16 = 42;
const TAG_DATA_HAS_VALUE: u16 = 43;
const TAG_DATA_MIN_CARDINALITY: u16 = 44;
const TAG_DATA_MAX_CARDINALITY: u16 = 45;
const TAG_DATA_EXACT_CARDINALITY: u16 = 46;
const TAG_DECLARATION: u16 = 60;
const TAG_SUB_CLASS_OF: u16 = 61;
const TAG_EQUIVALENT_CLASSES: u16 = 62;
const TAG_DISJOINT_CLASSES: u16 = 63;
const TAG_DISJOINT_UNION: u16 = 64;
const TAG_SUB_OBJECT_PROPERTY_OF: u16 = 70;
const TAG_EQUIVALENT_OBJECT_PROPERTIES: u16 = 71;
const TAG_DISJOINT_OBJECT_PROPERTIES: u16 = 72;
const TAG_INVERSE_OBJECT_PROPERTIES: u16 = 73;
const TAG_OBJECT_PROPERTY_DOMAIN: u16 = 74;
const TAG_OBJECT_PROPERTY_RANGE: u16 = 75;
const TAG_FUNCTIONAL_OBJECT_PROPERTY: u16 = 76;
const TAG_INVERSE_FUNCTIONAL_OBJECT_PROPERTY: u16 = 77;
const TAG_REFLEXIVE_OBJECT_PROPERTY: u16 = 78;
const TAG_IRREFLEXIVE_OBJECT_PROPERTY: u16 = 79;
const TAG_SYMMETRIC_OBJECT_PROPERTY: u16 = 80;
const TAG_ASYMMETRIC_OBJECT_PROPERTY: u16 = 81;
const TAG_TRANSITIVE_OBJECT_PROPERTY: u16 = 82;
const TAG_SUB_DATA_PROPERTY_OF: u16 = 90;
const TAG_EQUIVALENT_DATA_PROPERTIES: u16 = 91;
const TAG_DISJOINT_DATA_PROPERTIES: u16 = 92;
const TAG_DATA_PROPERTY_DOMAIN: u16 = 93;
const TAG_DATA_PROPERTY_RANGE: u16 = 94;
const TAG_FUNCTIONAL_DATA_PROPERTY: u16 = 95;
const TAG_DATATYPE_DEFINITION: u16 = 100;
const TAG_HAS_KEY: u16 = 101;
const TAG_SAME_INDIVIDUAL: u16 = 110;
const TAG_DIFFERENT_INDIVIDUALS: u16 = 111;
const TAG_CLASS_ASSERTION: u16 = 112;
const TAG_OBJECT_PROPERTY_ASSERTION: u16 = 113;
const TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION: u16 = 114;
const TAG_DATA_PROPERTY_ASSERTION: u16 = 115;
const TAG_NEGATIVE_DATA_PROPERTY_ASSERTION: u16 = 116;
const TAG_ANNOTATION_ASSERTION: u16 = 120;
const TAG_SUB_ANNOTATION_PROPERTY_OF: u16 = 121;
const TAG_ANNOTATION_PROPERTY_DOMAIN: u16 = 122;
const TAG_ANNOTATION_PROPERTY_RANGE: u16 = 123;
const TAG_VARIABLE: u16 = 140;
const TAG_CLASS_ATOM: u16 = 141;
const TAG_DATA_RANGE_ATOM: u16 = 142;
const TAG_OBJECT_PROPERTY_ATOM: u16 = 143;
const TAG_DATA_PROPERTY_ATOM: u16 = 144;
const TAG_BUILT_IN_ATOM: u16 = 145;
const TAG_SAME_INDIVIDUAL_ATOM: u16 = 146;
const TAG_DIFFERENT_INDIVIDUALS_ATOM: u16 = 147;
const TAG_SWRL_RULE: u16 = 148;

const SUBCLASS_OF: &str = "http://subclassof";
const SUPERCLASS_OF: &str = "http://superclassof";
const RDF_TYPE: &str = "http://type";
const RDF_PLAIN_LITERAL: &str = "http://www.w3.org/1999/02/22-rdf-syntax-ns#PlainLiteral";
const XSD_STRING: &str = "http://www.w3.org/2001/XMLSchema#string";
const XSD_NAMESPACE: &str = "http://www.w3.org/2001/XMLSchema#";

const ANNOTATION_PROPERTIES: &[&str] = &[
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
];

const ENTITY_KINDS: [&[u8]; 6] = [
    b"class",
    b"datatype",
    b"object_property",
    b"data_property",
    b"annotation_property",
    b"named_individual",
];

const SCHEMA_TAGS: &[u16] = &[
    1, 2, 3, 4, 5, 10, 11, 20, 21, 22, 23, 24, 25, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41,
    42, 43, 44, 45, 46, 60, 61, 62, 63, 64, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 90,
    91, 92, 93, 94, 95, 100, 101, 110, 111, 112, 113, 114, 115, 116, 120, 121, 122, 123, 140, 141,
    142, 143, 144, 145, 146, 147, 148,
];

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum KernelError {
    Malformed(String),
    Unsupported(String),
    ReferenceFailure(String),
    Resource(String),
    Cancelled,
}

impl KernelError {
    fn malformed(message: impl Into<String>) -> Self {
        Self::Malformed(message.into())
    }

    fn unsupported(message: impl Into<String>) -> Self {
        Self::Unsupported(message.into())
    }

    fn reference_failure(message: impl Into<String>) -> Self {
        Self::ReferenceFailure(message.into())
    }

    fn resource(message: impl Into<String>) -> Self {
        Self::Resource(message.into())
    }
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct DirectEdge {
    pub(crate) source: String,
    pub(crate) relation: String,
    pub(crate) destination: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct DirectCompileStats {
    pub(crate) roots: usize,
    pub(crate) nodes: usize,
    pub(crate) anonymous_individuals: usize,
    pub(crate) ontology_annotations: usize,
    pub(crate) swrl_rules: usize,
    pub(crate) declarations: usize,
    pub(crate) subclasses: usize,
    pub(crate) restriction_subclasses: usize,
    pub(crate) ignored_subclasses: usize,
    pub(crate) equivalents: usize,
    pub(crate) aggregate_equivalents: usize,
    pub(crate) disjoint_classes: usize,
    pub(crate) disjoint_unions: usize,
    pub(crate) has_keys: usize,
    pub(crate) same_individuals: usize,
    pub(crate) different_individuals: usize,
    pub(crate) class_assertions: usize,
    pub(crate) ignored_class_assertions: usize,
    pub(crate) object_property_assertions: usize,
    pub(crate) negative_object_property_assertions: usize,
    pub(crate) sub_object_properties: usize,
    pub(crate) object_property_chains: usize,
    pub(crate) equivalent_object_properties: usize,
    pub(crate) disjoint_object_properties: usize,
    pub(crate) inverse_object_properties: usize,
    pub(crate) functional_object_properties: usize,
    pub(crate) inverse_functional_object_properties: usize,
    pub(crate) reflexive_object_properties: usize,
    pub(crate) irreflexive_object_properties: usize,
    pub(crate) symmetric_object_properties: usize,
    pub(crate) asymmetric_object_properties: usize,
    pub(crate) transitive_object_properties: usize,
    pub(crate) sub_data_properties: usize,
    pub(crate) equivalent_data_properties: usize,
    pub(crate) disjoint_data_properties: usize,
    pub(crate) data_property_domains: usize,
    pub(crate) data_property_ranges: usize,
    pub(crate) functional_data_properties: usize,
    pub(crate) datatype_definitions: usize,
    pub(crate) data_property_assertions: usize,
    pub(crate) negative_data_property_assertions: usize,
    pub(crate) annotation_assertions: usize,
    pub(crate) sub_annotation_properties: usize,
    pub(crate) annotation_property_domains: usize,
    pub(crate) annotation_property_ranges: usize,
    pub(crate) annotation_edges: usize,
    pub(crate) non_string_literal_renderings: usize,
    pub(crate) skipped_axioms: usize,
    pub(crate) object_property_domains: usize,
    pub(crate) object_property_ranges: usize,
    pub(crate) domain_range_edges: usize,
    pub(crate) role_expansion_edges: usize,
    pub(crate) edges: usize,
    pub(crate) buffer_bytes: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SubclassProjection<'a> {
    Taxonomy {
        source: &'a str,
        destination: &'a str,
    },
    Restriction {
        source: &'a str,
        relation: &'a str,
        destination: &'a str,
    },
    Ignored,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ClassAssertionProjection<'a> {
    Edge { individual: &'a str, class: &'a str },
    Ignored,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum EquivalentProjection<'a> {
    Pair {
        source: &'a str,
        destination: &'a str,
    },
    Aggregate {
        source: &'a str,
        expression_id: usize,
    },
    Ignored,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum AnnotationValue<'a> {
    Borrowed(&'a str),
    Anonymous(usize),
    Typed { lexical: &'a str, datatype: &'a str },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum IndividualValue<'a> {
    Named(&'a str),
    Anonymous(usize),
}

#[derive(Debug, Default, Eq, PartialEq)]
struct AnonymousIds {
    node_ids: Vec<usize>,
}

impl AnonymousIds {
    fn render(&self, node_id: usize) -> Result<String, KernelError> {
        let position = self.node_ids.binary_search(&node_id).map_err(|_| {
            KernelError::malformed("encoded anonymous individual lost its axiom-derived identifier")
        })?;
        let identifier = 2_147_483_648_usize
            .checked_add(position)
            .ok_or_else(|| KernelError::resource("encoded anonymous identifier overflow"))?;
        let mut output = String::new();
        output
            .try_reserve_exact(27)
            .map_err(|_| KernelError::resource("encoded anonymous identifier allocation failed"))?;
        std::fmt::Write::write_fmt(&mut output, format_args!("_:genid{identifier}"))
            .map_err(|_| KernelError::resource("encoded anonymous identifier rendering failed"))?;
        Ok(output)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct AnnotationProjection<'a> {
    source: &'a str,
    relation: &'a str,
    value: AnnotationValue<'a>,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct EquivalentEdgeCounts {
    edges: usize,
    base_role_edges: usize,
    expanded_role_edges: usize,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct AnnotationEdgeCounts {
    edges: usize,
    non_string_literals: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct EquivalentEmitOptions {
    bidirectional: bool,
    only_taxonomy: bool,
    maximum_iri: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct DirectCompileOptions {
    pub(crate) bidirectional: bool,
    pub(crate) asserted_taxonomy_only: bool,
    pub(crate) only_taxonomy: bool,
    pub(crate) include_literals: bool,
    pub(crate) max_edges: usize,
    pub(crate) max_iri_bytes: usize,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct RootCounts {
    ontology_annotations: usize,
    swrl_rules: usize,
    declarations: usize,
    subclasses: usize,
    restriction_subclasses: usize,
    ignored_subclasses: usize,
    equivalents: usize,
    aggregate_equivalents: usize,
    disjoint_classes: usize,
    disjoint_unions: usize,
    has_keys: usize,
    same_individuals: usize,
    different_individuals: usize,
    class_assertions: usize,
    ignored_class_assertions: usize,
    object_property_assertions: usize,
    negative_object_property_assertions: usize,
    sub_object_properties: usize,
    object_property_chains: usize,
    equivalent_object_properties: usize,
    disjoint_object_properties: usize,
    inverse_object_properties: usize,
    functional_object_properties: usize,
    inverse_functional_object_properties: usize,
    reflexive_object_properties: usize,
    irreflexive_object_properties: usize,
    symmetric_object_properties: usize,
    asymmetric_object_properties: usize,
    transitive_object_properties: usize,
    sub_data_properties: usize,
    equivalent_data_properties: usize,
    disjoint_data_properties: usize,
    data_property_domains: usize,
    data_property_ranges: usize,
    functional_data_properties: usize,
    datatype_definitions: usize,
    data_property_assertions: usize,
    negative_data_property_assertions: usize,
    annotation_assertions: usize,
    sub_annotation_properties: usize,
    annotation_property_domains: usize,
    annotation_property_ranges: usize,
    object_property_domains: usize,
    object_property_ranges: usize,
}

impl RootCounts {
    fn role_axioms(self) -> Result<usize, KernelError> {
        self.sub_object_properties
            .checked_add(self.inverse_object_properties)
            .ok_or_else(|| KernelError::resource("encoded role-axiom count overflow"))
    }

    fn skipped_axioms(self) -> Result<usize, KernelError> {
        [
            self.negative_object_property_assertions,
            self.disjoint_classes,
            self.disjoint_unions,
            self.has_keys,
            self.same_individuals,
            self.different_individuals,
            self.equivalent_object_properties,
            self.disjoint_object_properties,
            self.functional_object_properties,
            self.inverse_functional_object_properties,
            self.reflexive_object_properties,
            self.irreflexive_object_properties,
            self.symmetric_object_properties,
            self.asymmetric_object_properties,
            self.transitive_object_properties,
            self.sub_data_properties,
            self.equivalent_data_properties,
            self.disjoint_data_properties,
            self.data_property_domains,
            self.data_property_ranges,
            self.functional_data_properties,
            self.datatype_definitions,
            self.data_property_assertions,
            self.negative_data_property_assertions,
            self.sub_annotation_properties,
            self.annotation_property_domains,
            self.annotation_property_ranges,
        ]
        .into_iter()
        .try_fold(0_usize, |total, count| {
            total
                .checked_add(count)
                .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ObjectPropertyExpression<'a> {
    iri: &'a str,
    owlapi_hash: i32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RoleAxiom<'a> {
    tag: u16,
    first: &'a str,
    second: &'a str,
    spread: u32,
    canonical_order: usize,
}

#[derive(Debug, Default, Eq, PartialEq)]
pub(crate) struct OwnedRoleState {
    subroles: Vec<(String, Vec<String>)>,
    inverses: Vec<(String, String)>,
}

impl OwnedRoleState {
    pub(crate) fn subrole_count(&self) -> usize {
        self.subroles.len()
    }

    pub(crate) fn inverse_count(&self) -> usize {
        self.inverses.len()
    }
}

#[derive(Debug, Default, Eq, PartialEq)]
struct RoleState<'a> {
    subroles: Vec<(Cow<'a, str>, Vec<Cow<'a, str>>)>,
    inverses: Vec<(Cow<'a, str>, Cow<'a, str>)>,
}

impl<'a> RoleState<'a> {
    fn with_capacity(
        retained: Option<&OwnedRoleState>,
        subrole_axioms: usize,
        inverse_axioms: usize,
        maximum_iri: usize,
    ) -> Result<Self, KernelError> {
        let inverse_capacity = inverse_axioms
            .checked_mul(2)
            .ok_or_else(|| KernelError::resource("encoded inverse-role capacity overflow"))?;
        let mut state = Self::default();
        let retained_subroles = retained.map_or(0, |value| value.subroles.len());
        let retained_inverses = retained.map_or(0, |value| value.inverses.len());
        state
            .subroles
            .try_reserve_exact(
                retained_subroles
                    .checked_add(subrole_axioms)
                    .ok_or_else(|| KernelError::resource("encoded subrole capacity overflow"))?,
            )
            .map_err(|_| KernelError::resource("encoded subrole index allocation failed"))?;
        state
            .inverses
            .try_reserve_exact(
                retained_inverses
                    .checked_add(inverse_capacity)
                    .ok_or_else(|| {
                        KernelError::resource("encoded inverse-role capacity overflow")
                    })?,
            )
            .map_err(|_| KernelError::resource("encoded inverse-role index allocation failed"))?;
        if let Some(retained) = retained {
            for (property, subroles) in &retained.subroles {
                let mut values = Vec::new();
                values
                    .try_reserve_exact(subroles.len())
                    .map_err(|_| KernelError::resource("encoded subrole list allocation failed"))?;
                for subrole in subroles {
                    values.push(Cow::Owned(clone_retained_role_iri(subrole, maximum_iri)?));
                }
                state.subroles.push((
                    Cow::Owned(clone_retained_role_iri(property, maximum_iri)?),
                    values,
                ));
            }
            for (property, inverse) in &retained.inverses {
                state.inverses.push((
                    Cow::Owned(clone_retained_role_iri(property, maximum_iri)?),
                    Cow::Owned(clone_retained_role_iri(inverse, maximum_iri)?),
                ));
            }
        }
        Ok(state)
    }

    fn subroles_for(&self, property: &str) -> &[Cow<'a, str>] {
        self.subroles
            .iter()
            .find_map(|(key, values)| (*key == property).then_some(values.as_slice()))
            .unwrap_or_default()
    }

    fn inverse_for(&self, property: &str) -> Option<&str> {
        self.inverses
            .iter()
            .find_map(|(key, inverse)| (*key == property).then_some(inverse.as_ref()))
    }

    fn set_subroles(
        &mut self,
        property: Cow<'a, str>,
        values: Vec<Cow<'a, str>>,
    ) -> Result<(), KernelError> {
        if let Some((_key, current)) = self
            .subroles
            .iter_mut()
            .find(|(key, _values)| key.as_ref() == property.as_ref())
        {
            *current = values;
            return Ok(());
        }
        self.subroles
            .try_reserve(1)
            .map_err(|_| KernelError::resource("encoded subrole index allocation failed"))?;
        self.subroles.push((property, values));
        Ok(())
    }

    fn set_inverse(
        &mut self,
        property: Cow<'a, str>,
        inverse: Cow<'a, str>,
    ) -> Result<(), KernelError> {
        if let Some((_key, current)) = self
            .inverses
            .iter_mut()
            .find(|(key, _inverse)| key.as_ref() == property.as_ref())
        {
            *current = inverse;
            return Ok(());
        }
        self.inverses
            .try_reserve(1)
            .map_err(|_| KernelError::resource("encoded inverse-role index allocation failed"))?;
        self.inverses.push((property, inverse));
        Ok(())
    }

    fn apply(&mut self, axiom: RoleAxiom<'a>) -> Result<(), KernelError> {
        if axiom.tag == TAG_SUB_OBJECT_PROPERTY_OF {
            let previous = self.subroles_for(axiom.first);
            let capacity = previous
                .len()
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded subrole list overflow"))?;
            let mut values = Vec::new();
            values
                .try_reserve_exact(capacity)
                .map_err(|_| KernelError::resource("encoded subrole list allocation failed"))?;
            values.push(Cow::Borrowed(axiom.first));
            values.extend(previous.iter().cloned());
            self.set_subroles(Cow::Borrowed(axiom.second), values)
        } else if axiom.tag == TAG_INVERSE_OBJECT_PROPERTIES {
            self.set_inverse(Cow::Borrowed(axiom.first), Cow::Borrowed(axiom.second))?;
            self.set_inverse(Cow::Borrowed(axiom.second), Cow::Borrowed(axiom.first))
        } else {
            Err(KernelError::malformed(
                "encoded role-state row has the wrong constructor tag",
            ))
        }
    }

    fn edge_count(&self, property: &str) -> Result<usize, KernelError> {
        1_usize
            .checked_add(self.subroles_for(property).len())
            .and_then(|count| count.checked_add(usize::from(self.inverse_for(property).is_some())))
            .ok_or_else(|| KernelError::resource("encoded role-expansion edge-count overflow"))
    }

    fn to_owned(&self) -> Result<OwnedRoleState, KernelError> {
        let mut owned = OwnedRoleState::default();
        owned
            .subroles
            .try_reserve_exact(self.subroles.len())
            .map_err(|_| KernelError::resource("encoded retained subrole allocation failed"))?;
        owned
            .inverses
            .try_reserve_exact(self.inverses.len())
            .map_err(|_| KernelError::resource("encoded retained inverse allocation failed"))?;
        for (property, subroles) in &self.subroles {
            let mut values = Vec::new();
            values
                .try_reserve_exact(subroles.len())
                .map_err(|_| KernelError::resource("encoded retained subrole allocation failed"))?;
            for subrole in subroles {
                values.push(clone_text(subrole.as_ref())?);
            }
            owned
                .subroles
                .push((clone_text(property.as_ref())?, values));
        }
        for (property, inverse) in &self.inverses {
            owned.inverses.push((
                clone_text(property.as_ref())?,
                clone_text(inverse.as_ref())?,
            ));
        }
        Ok(owned)
    }
}

#[derive(Clone, Copy)]
pub(crate) struct DirectColumns<'a> {
    root_kinds: &'a [u8],
    root_ids: &'a [u8],
    node_tags: &'a [u8],
    node_field_offsets: &'a [u8],
    field_kinds: &'a [u8],
    field_values: &'a [u8],
    field_lengths: &'a [u8],
    item_kinds: &'a [u8],
    item_values: &'a [u8],
    item_lengths: &'a [u8],
    scalar_bytes: &'a [u8],
}

impl<'a> DirectColumns<'a> {
    pub(crate) fn from_ordered(buffers: [&'a [u8]; BUFFER_COUNT]) -> Self {
        Self {
            root_kinds: buffers[0],
            root_ids: buffers[1],
            node_tags: buffers[2],
            node_field_offsets: buffers[3],
            field_kinds: buffers[4],
            field_values: buffers[5],
            field_lengths: buffers[6],
            item_kinds: buffers[7],
            item_values: buffers[8],
            item_lengths: buffers[9],
            scalar_bytes: buffers[10],
        }
    }

    fn buffer_bytes(self) -> Result<usize, KernelError> {
        self.buffers()
            .into_iter()
            .try_fold(0_usize, |total, buffer| {
                total
                    .checked_add(buffer.len())
                    .ok_or_else(|| KernelError::resource("encoded buffer-byte total overflow"))
            })
    }

    fn buffers(self) -> [&'a [u8]; BUFFER_COUNT] {
        [
            self.root_kinds,
            self.root_ids,
            self.node_tags,
            self.node_field_offsets,
            self.field_kinds,
            self.field_values,
            self.field_lengths,
            self.item_kinds,
            self.item_values,
            self.item_lengths,
            self.scalar_bytes,
        ]
    }

    fn root_count(self) -> usize {
        self.root_kinds.len()
    }

    fn node_count(self) -> usize {
        self.node_tags.len() / 2
    }

    fn field_count(self) -> usize {
        self.field_kinds.len()
    }

    fn item_count(self) -> usize {
        self.item_kinds.len()
    }

    fn root_kind(self, index: usize) -> Result<u8, KernelError> {
        self.root_kinds
            .get(index)
            .copied()
            .ok_or_else(|| KernelError::malformed("root kind index is out of range"))
    }

    fn root_id(self, index: usize) -> Result<usize, KernelError> {
        let value = read_u32(self.root_ids, index, "root_ids")?;
        let value = usize::try_from(value)
            .map_err(|_| KernelError::malformed("root ID does not fit usize"))?;
        self.checked_node_id(value)
    }

    fn checked_node_id(self, node_id: usize) -> Result<usize, KernelError> {
        if (1..=self.node_count()).contains(&node_id) {
            Ok(node_id)
        } else {
            Err(KernelError::malformed(
                "encoded node reference is out of range",
            ))
        }
    }

    fn node_tag(self, node_id: usize) -> Result<u16, KernelError> {
        self.checked_node_id(node_id)?;
        read_u16(self.node_tags, node_id - 1, "node_tags")
    }

    fn field_range(self, node_id: usize) -> Result<(usize, usize), KernelError> {
        self.checked_node_id(node_id)?;
        let start = read_usize(self.node_field_offsets, node_id - 1, "node_field_offsets")?;
        let end = read_usize(self.node_field_offsets, node_id, "node_field_offsets")?;
        if start <= end && end <= self.field_count() {
            Ok((start, end))
        } else {
            Err(KernelError::malformed(
                "encoded node field range is invalid",
            ))
        }
    }

    fn exact_fields(self, node_id: usize, arity: usize) -> Result<usize, KernelError> {
        let (start, end) = self.field_range(node_id)?;
        if end - start == arity {
            Ok(start)
        } else {
            Err(KernelError::malformed(format!(
                "encoded constructor {node_id} has arity {}; expected {arity}",
                end - start
            )))
        }
    }

    fn field_kind(self, index: usize) -> Result<u8, KernelError> {
        self.field_kinds
            .get(index)
            .copied()
            .ok_or_else(|| KernelError::malformed("field kind index is out of range"))
    }

    fn field_value(self, index: usize) -> Result<usize, KernelError> {
        read_usize(self.field_values, index, "field_values")
    }

    fn field_length(self, index: usize) -> Result<usize, KernelError> {
        read_usize(self.field_lengths, index, "field_lengths")
    }

    fn field_node(self, index: usize) -> Result<usize, KernelError> {
        if self.field_kind(index)? != COMPONENT_NODE || self.field_length(index)? != 0 {
            return Err(KernelError::malformed(
                "encoded constructor field is not a node reference",
            ));
        }
        self.checked_node_id(self.field_value(index)?)
    }

    fn item_node(self, index: usize) -> Result<usize, KernelError> {
        if self
            .item_kinds
            .get(index)
            .copied()
            .ok_or_else(|| KernelError::malformed("item kind index is out of range"))?
            != COMPONENT_NODE
            || read_usize(self.item_lengths, index, "item_lengths")? != 0
        {
            return Err(KernelError::malformed(
                "encoded canonical-set item is not a node reference",
            ));
        }
        self.checked_node_id(read_usize(self.item_values, index, "item_values")?)
    }

    fn node_set_range(self, index: usize, minimum: usize) -> Result<(usize, usize), KernelError> {
        if self.field_kind(index)? != COMPONENT_SET {
            return Err(KernelError::malformed(
                "encoded collection field is not a canonical set",
            ));
        }
        let start = self.field_value(index)?;
        let length = self.field_length(index)?;
        if start > self.item_count() || length > self.item_count().saturating_sub(start) {
            return Err(KernelError::malformed(
                "encoded canonical-set range is out of bounds",
            ));
        }
        if length < minimum {
            return Err(KernelError::malformed(format!(
                "encoded canonical set has {length} items; expected at least {minimum}",
            )));
        }
        let mut previous = 0_usize;
        for item_index in start..start + length {
            let node_id = self.item_node(item_index)?;
            if node_id <= previous {
                return Err(KernelError::malformed(
                    "encoded canonical-set items are not sorted and unique",
                ));
            }
            previous = node_id;
        }
        Ok((start, length))
    }

    fn node_sequence_range(
        self,
        index: usize,
        minimum: usize,
    ) -> Result<(usize, usize), KernelError> {
        if self.field_kind(index)? != COMPONENT_SEQUENCE {
            return Err(KernelError::malformed(
                "encoded collection field is not an ordered sequence",
            ));
        }
        let start = self.field_value(index)?;
        let length = self.field_length(index)?;
        if start > self.item_count() || length > self.item_count().saturating_sub(start) {
            return Err(KernelError::malformed(
                "encoded ordered-sequence range is out of bounds",
            ));
        }
        if length < minimum {
            return Err(KernelError::malformed(format!(
                "encoded ordered sequence has {length} items; expected at least {minimum}",
            )));
        }
        for item_index in start..start + length {
            self.item_node(item_index)?;
        }
        Ok((start, length))
    }

    fn scalar_payload(self, index: usize, kind: u8) -> Result<&'a [u8], KernelError> {
        if self.field_kind(index)? != kind {
            return Err(KernelError::malformed(
                "encoded scalar field has the wrong component kind",
            ));
        }
        let start = self.field_value(index)?;
        let length = self.field_length(index)?;
        let end = start
            .checked_add(length)
            .ok_or_else(|| KernelError::malformed("encoded scalar range overflow"))?;
        self.scalar_bytes
            .get(start..end)
            .ok_or_else(|| KernelError::malformed("encoded scalar range is out of bounds"))
    }

    fn canonical_integer(self, index: usize) -> Result<&'a [u8], KernelError> {
        let payload = self.scalar_payload(index, COMPONENT_INTEGER)?;
        if payload.is_empty() || (payload.len() > 1 && payload.last() == Some(&0)) {
            return Err(KernelError::malformed(
                "encoded integer field is not minimally encoded",
            ));
        }
        Ok(payload)
    }

    fn iri(self, node_id: usize, maximum: usize) -> Result<&'a str, KernelError> {
        if self.node_tag(node_id)? != TAG_IRI {
            return Err(KernelError::malformed(
                "encoded entity does not reference an IRI node",
            ));
        }
        let payload = self.scalar_payload(self.exact_fields(node_id, 1)?, COMPONENT_TEXT)?;
        if payload.len() > maximum {
            return Err(KernelError::resource(format!(
                "encoded IRI contains {} bytes; limit is {maximum}",
                payload.len()
            )));
        }
        std::str::from_utf8(payload)
            .map_err(|_| KernelError::malformed("encoded IRI text is not UTF-8"))
    }

    fn entity(self, node_id: usize) -> Result<(&'a [u8], usize), KernelError> {
        if self.node_tag(node_id)? != TAG_ENTITY {
            return Err(KernelError::malformed(
                "encoded axiom does not reference an entity node",
            ));
        }
        let start = self.exact_fields(node_id, 2)?;
        let kind = self.scalar_payload(start, COMPONENT_ENUM)?;
        if !ENTITY_KINDS.contains(&kind) {
            return Err(KernelError::malformed("encoded entity kind is invalid"));
        }
        let iri_id = self.field_node(start + 1)?;
        if self.node_tag(iri_id)? != TAG_IRI {
            return Err(KernelError::malformed(
                "encoded entity IRI reference has the wrong tag",
            ));
        }
        Ok((kind, iri_id))
    }

    fn validate_anonymous_individual(self, node_id: usize) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_ANONYMOUS_INDIVIDUAL {
            return Err(KernelError::malformed(
                "encoded anonymous-individual cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 2)?;
        let document_scope = self.scalar_payload(start, COMPONENT_BYTES)?;
        let local_key = self.scalar_payload(start + 1, COMPONENT_BYTES)?;
        if document_scope.len() != 32 {
            return Err(KernelError::malformed(
                "encoded anonymous individual document scope is not bytes32",
            ));
        }
        if local_key.is_empty() {
            return Err(KernelError::malformed(
                "encoded anonymous individual local key is empty",
            ));
        }
        Ok(())
    }

    fn anonymous_parts(self, node_id: usize) -> Result<(&'a [u8], &'a [u8]), KernelError> {
        self.validate_anonymous_individual(node_id)?;
        let start = self.exact_fields(node_id, 2)?;
        Ok((
            self.scalar_payload(start, COMPONENT_BYTES)?,
            self.scalar_payload(start + 1, COMPONENT_BYTES)?,
        ))
    }

    fn compare_anonymous_nodes(
        self,
        left: usize,
        right: usize,
    ) -> Result<std::cmp::Ordering, KernelError> {
        let (left_scope, left_key) = self.anonymous_parts(left)?;
        let (right_scope, right_key) = self.anonymous_parts(right)?;
        let scope_order = left_scope.cmp(right_scope);
        if scope_order != std::cmp::Ordering::Equal {
            return Ok(scope_order);
        }
        let length_order = compare_canonical_varints(left_key.len(), right_key.len());
        if length_order != std::cmp::Ordering::Equal {
            return Ok(length_order);
        }
        Ok(left_key.cmp(right_key))
    }

    fn axiom_anonymous_ids(self, state: &AtomicU8) -> Result<AnonymousIds, KernelError> {
        let mut has_anonymous_individual = false;
        for node_id in 1..=self.node_count() {
            check_cancel(state, node_id)?;
            if self.node_tag(node_id)? == TAG_ANONYMOUS_INDIVIDUAL {
                has_anonymous_individual = true;
                break;
            }
        }
        if !has_anonymous_individual {
            return Ok(AnonymousIds::default());
        }
        let reachable_length = self
            .node_count()
            .checked_add(1)
            .ok_or_else(|| KernelError::resource("encoded reachability length overflow"))?;
        let mut reachable = Vec::new();
        reachable
            .try_reserve_exact(reachable_length)
            .map_err(|_| KernelError::resource("encoded reachability allocation failed"))?;
        reachable.resize(reachable_length, false);
        let mut stack = Vec::new();
        for root_index in 0..self.root_count() {
            check_cancel(state, root_index)?;
            if self.root_kind(root_index)? != ROOT_AXIOM {
                continue;
            }
            let root_id = self.root_id(root_index)?;
            queue_reachable_node(&mut stack, &mut reachable, root_id)?;
        }
        while let Some(node_id) = stack.pop() {
            check_cancel(state, node_id)?;
            let (start, end) = self.field_range(node_id)?;
            for field_index in start..end {
                let kind = self.field_kind(field_index)?;
                if kind == COMPONENT_NODE {
                    let child = self.field_node(field_index)?;
                    queue_reachable_node(&mut stack, &mut reachable, child)?;
                    continue;
                }
                if ![COMPONENT_SET, COMPONENT_SEQUENCE].contains(&kind) {
                    continue;
                }
                let item_start = self.field_value(field_index)?;
                let length = self.field_length(field_index)?;
                for item_index in item_start..item_start + length {
                    if self.item_kinds[item_index] != COMPONENT_NODE {
                        continue;
                    }
                    let child = self.item_node(item_index)?;
                    queue_reachable_node(&mut stack, &mut reachable, child)?;
                }
            }
        }

        let mut anonymous_count = 0_usize;
        let mut previous = None;
        for (node_id, is_reachable) in reachable.iter().copied().enumerate().skip(1) {
            check_cancel(state, node_id)?;
            if is_reachable && self.node_tag(node_id)? == TAG_ANONYMOUS_INDIVIDUAL {
                if let Some(previous_id) = previous {
                    if self.compare_anonymous_nodes(previous_id, node_id)?
                        != std::cmp::Ordering::Less
                    {
                        return Err(KernelError::malformed(
                            "encoded axiom-derived anonymous individuals are not canonical and unique",
                        ));
                    }
                }
                previous = Some(node_id);
                anonymous_count = anonymous_count.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded anonymous index length overflow")
                })?;
            }
        }
        let mut node_ids = Vec::new();
        node_ids
            .try_reserve_exact(anonymous_count)
            .map_err(|_| KernelError::resource("encoded anonymous index allocation failed"))?;
        for (node_id, is_reachable) in reachable.iter().copied().enumerate().skip(1) {
            if is_reachable && self.node_tag(node_id)? == TAG_ANONYMOUS_INDIVIDUAL {
                node_ids.push(node_id);
            }
        }
        Ok(AnonymousIds { node_ids })
    }

    fn validate_individual(self, node_id: usize, maximum: usize) -> Result<(), KernelError> {
        match self.node_tag(node_id)? {
            TAG_ENTITY => self.named_individual_iri(node_id, maximum).map(|_iri| ()),
            TAG_ANONYMOUS_INDIVIDUAL => self.validate_anonymous_individual(node_id),
            _ => Err(KernelError::malformed(
                "encoded individual set item is not an individual",
            )),
        }
    }

    fn individual_value(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<IndividualValue<'a>, KernelError> {
        match self.node_tag(node_id)? {
            TAG_ENTITY => Ok(IndividualValue::Named(
                self.named_individual_iri(node_id, maximum)?,
            )),
            TAG_ANONYMOUS_INDIVIDUAL => {
                self.validate_anonymous_individual(node_id)?;
                Ok(IndividualValue::Anonymous(node_id))
            }
            _ => Err(KernelError::malformed(
                "encoded assertion argument is not an individual",
            )),
        }
    }

    fn named_class_iri(self, node_id: usize, maximum: usize) -> Result<&'a str, KernelError> {
        let tag = self.node_tag(node_id)?;
        if tag != TAG_ENTITY {
            if SCHEMA_TAGS.contains(&tag) {
                return Err(KernelError::unsupported(
                    "direct native slice supports only named class expressions",
                ));
            }
            return Err(KernelError::malformed(
                "encoded class expression has an unknown node tag",
            ));
        }
        let (kind, iri_id) = self.entity(node_id)?;
        if kind != b"class" {
            return Err(KernelError::malformed(
                "encoded class expression entity is not a class",
            ));
        }
        self.iri(iri_id, maximum)
    }

    fn named_individual_iri(self, node_id: usize, maximum: usize) -> Result<&'a str, KernelError> {
        let tag = self.node_tag(node_id)?;
        if tag != TAG_ENTITY {
            if SCHEMA_TAGS.contains(&tag) {
                return Err(KernelError::unsupported(
                    "direct native slice supports only named individuals in ABox assertions",
                ));
            }
            return Err(KernelError::malformed(
                "encoded individual has an unknown node tag",
            ));
        }
        let (kind, iri_id) = self.entity(node_id)?;
        if kind != b"named_individual" {
            return Err(KernelError::malformed(
                "encoded individual entity is not a named individual",
            ));
        }
        self.iri(iri_id, maximum)
    }

    fn named_object_property_iri(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<&'a str, KernelError> {
        let tag = self.node_tag(node_id)?;
        if tag != TAG_ENTITY {
            if SCHEMA_TAGS.contains(&tag) {
                return Err(KernelError::unsupported(
                    "direct native slice supports only named object properties",
                ));
            }
            return Err(KernelError::malformed(
                "encoded object property has an unknown node tag",
            ));
        }
        let (kind, iri_id) = self.entity(node_id)?;
        if kind != b"object_property" {
            return Err(KernelError::malformed(
                "encoded object-property expression entity has the wrong kind",
            ));
        }
        self.iri(iri_id, maximum)
    }

    fn named_data_property_iri(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<&'a str, KernelError> {
        let tag = self.node_tag(node_id)?;
        if tag != TAG_ENTITY {
            if SCHEMA_TAGS.contains(&tag) {
                return Err(KernelError::unsupported(
                    "direct native slice supports only named data properties",
                ));
            }
            return Err(KernelError::malformed(
                "encoded data property has an unknown node tag",
            ));
        }
        let (kind, iri_id) = self.entity(node_id)?;
        if kind != b"data_property" {
            return Err(KernelError::malformed(
                "encoded data-property entity has the wrong kind",
            ));
        }
        self.iri(iri_id, maximum)
    }

    fn named_annotation_property_iri(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<&'a str, KernelError> {
        let tag = self.node_tag(node_id)?;
        if tag != TAG_ENTITY {
            if SCHEMA_TAGS.contains(&tag) {
                return Err(KernelError::unsupported(
                    "direct native slice supports only named annotation properties",
                ));
            }
            return Err(KernelError::malformed(
                "encoded annotation property has an unknown node tag",
            ));
        }
        let (kind, iri_id) = self.entity(node_id)?;
        if kind != b"annotation_property" {
            return Err(KernelError::malformed(
                "encoded annotation-property entity has the wrong kind",
            ));
        }
        self.iri(iri_id, maximum)
    }

    fn named_datatype_iri(self, node_id: usize, maximum: usize) -> Result<&'a str, KernelError> {
        let tag = self.node_tag(node_id)?;
        if tag != TAG_ENTITY {
            if SCHEMA_TAGS.contains(&tag) {
                return Err(KernelError::unsupported(
                    "direct native slice supports only named datatypes",
                ));
            }
            return Err(KernelError::malformed(
                "encoded datatype has an unknown node tag",
            ));
        }
        let (kind, iri_id) = self.entity(node_id)?;
        if kind != b"datatype" {
            return Err(KernelError::malformed(
                "encoded datatype entity has the wrong kind",
            ));
        }
        self.iri(iri_id, maximum)
    }

    fn literal_parts(
        self,
        node_id: usize,
        maximum_iri: usize,
    ) -> Result<(&'a str, &'a str), KernelError> {
        if self.node_tag(node_id)? != TAG_LITERAL {
            return Err(KernelError::malformed(
                "encoded value does not reference a Literal",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        let lexical = self.scalar_payload(start, COMPONENT_TEXT)?;
        let lexical = std::str::from_utf8(lexical)
            .map_err(|_| KernelError::malformed("encoded literal lexical form is not UTF-8"))?;
        let datatype = self.named_datatype_iri(self.field_node(start + 1)?, maximum_iri)?;
        match self.field_kind(start + 2)? {
            COMPONENT_NONE => {
                if self.field_value(start + 2)? != 0 || self.field_length(start + 2)? != 0 {
                    return Err(KernelError::malformed(
                        "encoded Literal language none field is not canonical",
                    ));
                }
            }
            COMPONENT_TEXT => {
                let payload = self.scalar_payload(start + 2, COMPONENT_TEXT)?;
                let language = std::str::from_utf8(payload)
                    .map_err(|_| KernelError::malformed("encoded Literal language is not UTF-8"))?;
                let is_lowercase = language
                    .chars()
                    .flat_map(|character| character.to_lowercase())
                    .eq(language.chars());
                if language.is_empty() || !is_lowercase || datatype != RDF_PLAIN_LITERAL {
                    return Err(KernelError::malformed(
                        "encoded Literal language is not canonical",
                    ));
                }
            }
            _ => {
                return Err(KernelError::malformed(
                    "encoded Literal language field kind is invalid",
                ));
            }
        }
        Ok((lexical, datatype))
    }

    fn validate_literal(self, node_id: usize, maximum_iri: usize) -> Result<(), KernelError> {
        self.literal_parts(node_id, maximum_iri).map(|_parts| ())
    }

    fn annotation_value(
        self,
        node_id: usize,
        maximum_iri: usize,
    ) -> Result<AnnotationValue<'a>, KernelError> {
        match self.node_tag(node_id)? {
            TAG_IRI => Ok(AnnotationValue::Borrowed(self.iri(node_id, maximum_iri)?)),
            TAG_ANONYMOUS_INDIVIDUAL => {
                self.validate_anonymous_individual(node_id)?;
                Ok(AnnotationValue::Anonymous(node_id))
            }
            TAG_LITERAL => {
                let (lexical, datatype) = self.literal_parts(node_id, maximum_iri)?;
                if [XSD_STRING, RDF_PLAIN_LITERAL].contains(&datatype) {
                    Ok(AnnotationValue::Borrowed(lexical))
                } else {
                    Ok(AnnotationValue::Typed { lexical, datatype })
                }
            }
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::malformed(
                "encoded annotation value is not an IRI or literal or anonymous individual",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded annotation value tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn validate_annotation_set(self, index: usize) -> Result<(), KernelError> {
        let (item_start, length) = self.node_set_range(index, 0)?;
        for item_index in item_start..item_start + length {
            if self.node_tag(self.item_node(item_index)?)? != TAG_ANNOTATION {
                return Err(KernelError::malformed(
                    "encoded annotation set item does not reference an Annotation",
                ));
            }
        }
        Ok(())
    }

    fn annotation_set_hash(self, index: usize, maximum_iri: usize) -> Result<i32, KernelError> {
        let (item_start, length) = self.node_set_range(index, 0)?;
        let mut result = 0_i32;
        for item_index in item_start..item_start + length {
            result = result
                .wrapping_add(self.annotation_hash(self.item_node(item_index)?, maximum_iri)?);
        }
        Ok(result)
    }

    fn annotation_hash(self, node_id: usize, maximum_iri: usize) -> Result<i32, KernelError> {
        if self.node_tag(node_id)? != TAG_ANNOTATION {
            return Err(KernelError::malformed(
                "encoded annotation set item does not reference an Annotation",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        let property = self.named_annotation_property_iri(self.field_node(start)?, maximum_iri)?;
        let value = self.annotation_value_hash(self.field_node(start + 1)?, maximum_iri)?;
        // OWLAPI 4.5.22 excludes nested annotations from OWLAnnotation.hashCode,
        // but their canonical set still belongs to the validated snapshot.
        self.validate_annotation_set(start + 2)?;
        Ok(combine_hash(6311, &[owlapi_iri_hash(property), value]))
    }

    fn annotation_value_hash(self, node_id: usize, maximum_iri: usize) -> Result<i32, KernelError> {
        match self.node_tag(node_id)? {
            TAG_IRI => Ok(owlapi_iri_hash(self.iri(node_id, maximum_iri)?)),
            TAG_LITERAL => {
                let (lexical, _datatype) = self.literal_parts(node_id, maximum_iri)?;
                Ok(java_string_hash(lexical))
            }
            TAG_ANONYMOUS_INDIVIDUAL => {
                self.validate_anonymous_individual(node_id)?;
                let start = self.exact_fields(node_id, 2)?;
                let local_key = self.scalar_payload(start + 1, COMPONENT_BYTES)?;
                let local_key = std::str::from_utf8(local_key).map_err(|_| {
                    KernelError::unsupported(
                        "direct native role annotation value cannot reproduce scalar hashing",
                    )
                })?;
                Ok(java_string_hash(local_key))
            }
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::unsupported(
                "direct native role annotation hashing supports only IRI, literal, or anonymous-individual values",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded role annotation value tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn validate_metadata_annotation_value(
        self,
        node_id: usize,
        maximum_iri: usize,
    ) -> Result<(), KernelError> {
        match self.node_tag(node_id)? {
            TAG_IRI => self.iri(node_id, maximum_iri).map(|_iri| ()),
            TAG_LITERAL => self.validate_literal(node_id, maximum_iri),
            TAG_ANONYMOUS_INDIVIDUAL => self.validate_anonymous_individual(node_id),
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::unsupported(
                "direct native annotation metadata supports only IRI, literal, or anonymous-individual values",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded annotation metadata value tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn validate_annotation(self, node_id: usize, maximum_iri: usize) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_ANNOTATION {
            return Err(KernelError::malformed(
                "encoded annotation cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        self.named_annotation_property_iri(self.field_node(start)?, maximum_iri)?;
        self.validate_metadata_annotation_value(self.field_node(start + 1)?, maximum_iri)?;
        self.validate_annotation_set(start + 2)
    }

    fn validate_annotation_assertion(
        self,
        node_id: usize,
        maximum_iri: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_ANNOTATION_ASSERTION {
            return Err(KernelError::malformed(
                "encoded annotation-assertion cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 4)?;
        self.named_annotation_property_iri(self.field_node(start)?, maximum_iri)?;
        let subject_id = self.field_node(start + 1)?;
        match self.node_tag(subject_id)? {
            TAG_IRI => {
                self.iri(subject_id, maximum_iri)?;
            }
            TAG_ANONYMOUS_INDIVIDUAL => {
                self.validate_anonymous_individual(subject_id)?;
            }
            _ => {
                return Err(KernelError::malformed(
                    "encoded annotation subject is not an IRI or anonymous individual",
                ));
            }
        }
        self.annotation_value(self.field_node(start + 2)?, maximum_iri)?;
        self.validate_annotation_set(start + 3)
    }

    fn validate_sub_annotation_property_of(
        self,
        node_id: usize,
        maximum_iri: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_SUB_ANNOTATION_PROPERTY_OF {
            return Err(KernelError::malformed(
                "encoded sub-annotation-property cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        self.named_annotation_property_iri(self.field_node(start)?, maximum_iri)?;
        self.named_annotation_property_iri(self.field_node(start + 1)?, maximum_iri)?;
        self.validate_annotation_set(start + 2)
    }

    fn validate_annotation_property_iri_axiom(
        self,
        node_id: usize,
        expected_tag: u16,
        maximum_iri: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != expected_tag
            || ![
                TAG_ANNOTATION_PROPERTY_DOMAIN,
                TAG_ANNOTATION_PROPERTY_RANGE,
            ]
            .contains(&expected_tag)
        {
            return Err(KernelError::malformed(
                "encoded annotation-property IRI axiom cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        self.named_annotation_property_iri(self.field_node(start)?, maximum_iri)?;
        self.iri(self.field_node(start + 1)?, maximum_iri)?;
        self.validate_annotation_set(start + 2)
    }

    fn contains_class_iri(
        self,
        target: &str,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<bool, KernelError> {
        for node_id in 1..=self.node_count() {
            check_cancel(state, node_id)?;
            if self.node_tag(node_id)? != TAG_ENTITY {
                continue;
            }
            let (kind, iri_id) = self.entity(node_id)?;
            if kind == b"class" && self.iri(iri_id, maximum_iri)? == target {
                return Ok(true);
            }
        }
        Ok(false)
    }

    fn annotation_projection(
        self,
        node_id: usize,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<Option<AnnotationProjection<'a>>, KernelError> {
        if self.node_tag(node_id)? != TAG_ANNOTATION_ASSERTION {
            return Err(KernelError::malformed(
                "encoded annotation projection cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 4)?;
        let property = self.named_annotation_property_iri(self.field_node(start)?, maximum_iri)?;
        if !ANNOTATION_PROPERTIES.contains(&property) {
            return Ok(None);
        }
        let subject_id = self.field_node(start + 1)?;
        let source = match self.node_tag(subject_id)? {
            TAG_IRI => self.iri(subject_id, maximum_iri)?,
            TAG_ANONYMOUS_INDIVIDUAL => {
                self.validate_anonymous_individual(subject_id)?;
                return Ok(None);
            }
            _ => {
                return Err(KernelError::malformed(
                    "encoded annotation subject changed after successful preflight",
                ));
            }
        };
        if !self.contains_class_iri(source, maximum_iri, state)? {
            return Ok(None);
        }
        let relation = match property {
            "http://www.w3.org/2000/01/rdf-schema#label" => "rdfs:label",
            "http://www.w3.org/2000/01/rdf-schema#comment" => "rdfs:comment",
            _ => property,
        };
        Ok(Some(AnnotationProjection {
            source,
            relation,
            value: self.annotation_value(self.field_node(start + 2)?, maximum_iri)?,
        }))
    }

    fn validate_binary_data_property_axiom(
        self,
        node_id: usize,
        expected_tag: u16,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != expected_tag || expected_tag != TAG_SUB_DATA_PROPERTY_OF {
            return Err(KernelError::malformed(
                "encoded data-property axiom cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        self.named_data_property_iri(self.field_node(start)?, maximum)?;
        self.named_data_property_iri(self.field_node(start + 1)?, maximum)?;
        self.validate_annotation_set(start + 2)
    }

    fn validate_data_property_set_axiom(
        self,
        node_id: usize,
        expected_tag: u16,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != expected_tag
            || ![TAG_EQUIVALENT_DATA_PROPERTIES, TAG_DISJOINT_DATA_PROPERTIES]
                .contains(&expected_tag)
        {
            return Err(KernelError::malformed(
                "encoded data-property set cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 2)?;
        let (item_start, length) = self.node_set_range(start, 2)?;
        for item_index in item_start..item_start + length {
            self.named_data_property_iri(self.item_node(item_index)?, maximum)?;
        }
        self.validate_annotation_set(start + 1)
    }

    fn validate_data_property_domain(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_DATA_PROPERTY_DOMAIN {
            return Err(KernelError::malformed(
                "encoded data-property domain cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        self.named_data_property_iri(self.field_node(start)?, maximum)?;
        self.class_expression_rank(self.field_node(start + 1)?, maximum)?;
        self.validate_annotation_set(start + 2)
    }

    fn validate_data_property_range(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_DATA_PROPERTY_RANGE {
            return Err(KernelError::malformed(
                "encoded data-property range cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        self.named_data_property_iri(self.field_node(start)?, maximum)?;
        self.validate_data_range_node(self.field_node(start + 1)?, maximum)?;
        self.validate_annotation_set(start + 2)
    }

    fn validate_functional_data_property(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_FUNCTIONAL_DATA_PROPERTY {
            return Err(KernelError::malformed(
                "encoded functional data-property cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 2)?;
        self.named_data_property_iri(self.field_node(start)?, maximum)?;
        self.validate_annotation_set(start + 1)
    }

    fn validate_datatype_definition(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_DATATYPE_DEFINITION {
            return Err(KernelError::malformed(
                "encoded datatype-definition cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        self.named_datatype_iri(self.field_node(start)?, maximum)?;
        self.validate_data_range_node(self.field_node(start + 1)?, maximum)?;
        self.validate_annotation_set(start + 2)
    }

    fn validate_has_key(self, node_id: usize, maximum: usize) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_HAS_KEY {
            return Err(KernelError::malformed(
                "encoded has-key cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 4)?;
        self.class_expression_rank(self.field_node(start)?, maximum)?;
        let (object_start, object_length) = self.node_set_range(start + 1, 0)?;
        for item_index in object_start..object_start + object_length {
            self.object_property_expression(self.item_node(item_index)?, maximum)?;
        }
        let (data_start, data_length) = self.node_set_range(start + 2, 0)?;
        for item_index in data_start..data_start + data_length {
            self.named_data_property_iri(self.item_node(item_index)?, maximum)?;
        }
        if object_length == 0 && data_length == 0 {
            return Err(KernelError::malformed(
                "encoded HasKey requires at least one property",
            ));
        }
        self.validate_annotation_set(start + 3)
    }

    fn validate_individual_set_axiom(
        self,
        node_id: usize,
        expected_tag: u16,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != expected_tag
            || ![TAG_SAME_INDIVIDUAL, TAG_DIFFERENT_INDIVIDUALS].contains(&expected_tag)
        {
            return Err(KernelError::malformed(
                "encoded individual-set cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 2)?;
        let (item_start, length) = self.node_set_range(start, 2)?;
        for item_index in item_start..item_start + length {
            self.validate_individual(self.item_node(item_index)?, maximum)?;
        }
        self.validate_annotation_set(start + 1)
    }

    fn validate_data_property_assertion(
        self,
        node_id: usize,
        expected_tag: u16,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != expected_tag
            || ![
                TAG_DATA_PROPERTY_ASSERTION,
                TAG_NEGATIVE_DATA_PROPERTY_ASSERTION,
            ]
            .contains(&expected_tag)
        {
            return Err(KernelError::malformed(
                "encoded data-property assertion cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 4)?;
        self.named_data_property_iri(self.field_node(start)?, maximum)?;
        self.validate_individual(self.field_node(start + 1)?, maximum)?;
        self.validate_literal(self.field_node(start + 2)?, maximum)?;
        self.validate_annotation_set(start + 3)
    }

    fn object_inverse_iri(self, node_id: usize, maximum: usize) -> Result<&'a str, KernelError> {
        if self.node_tag(node_id)? != TAG_OBJECT_INVERSE_OF {
            return Err(KernelError::malformed(
                "encoded inverse-property cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 1)?;
        self.named_object_property_iri(self.field_node(start)?, maximum)
    }

    fn object_property_expression_iri(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<&'a str, KernelError> {
        match self.node_tag(node_id)? {
            TAG_ENTITY => self.named_object_property_iri(node_id, maximum),
            TAG_OBJECT_INVERSE_OF => self.object_inverse_iri(node_id, maximum),
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::unsupported(
                "direct native slice supports only named or inverse object properties here",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded object-property expression tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn object_property_expression(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<ObjectPropertyExpression<'a>, KernelError> {
        let tag = self.node_tag(node_id)?;
        let iri = self.object_property_expression_iri(node_id, maximum)?;
        let named_hash = combine_hash(4153, &[owlapi_iri_hash(iri)]);
        Ok(ObjectPropertyExpression {
            iri,
            owlapi_hash: if tag == TAG_OBJECT_INVERSE_OF {
                combine_hash(4241, &[named_hash])
            } else {
                named_hash
            },
        })
    }

    fn validate_object_property_chain(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_OBJECT_PROPERTY_CHAIN {
            return Err(KernelError::malformed(
                "encoded object-property-chain cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 1)?;
        let (item_start, length) = self.node_sequence_range(start, 2)?;
        for item_index in item_start..item_start + length {
            self.object_property_expression(self.item_node(item_index)?, maximum)?;
        }
        Ok(())
    }

    fn validate_sub_object_property_of(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<bool, KernelError> {
        if self.node_tag(node_id)? != TAG_SUB_OBJECT_PROPERTY_OF {
            return Err(KernelError::malformed(
                "encoded sub-object-property cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        let first_id = self.field_node(start)?;
        let is_chain = self.node_tag(first_id)? == TAG_OBJECT_PROPERTY_CHAIN;
        if is_chain {
            self.validate_object_property_chain(first_id, maximum)?;
        } else {
            self.object_property_expression(first_id, maximum)?;
        }
        self.object_property_expression(self.field_node(start + 1)?, maximum)?;
        self.annotation_set_hash(start + 2, maximum)?;
        Ok(is_chain)
    }

    fn role_axiom_parts(
        self,
        node_id: usize,
        expected_tag: u16,
        maximum: usize,
    ) -> Result<
        (
            ObjectPropertyExpression<'a>,
            ObjectPropertyExpression<'a>,
            i32,
        ),
        KernelError,
    > {
        if self.node_tag(node_id)? != expected_tag
            || ![TAG_SUB_OBJECT_PROPERTY_OF, TAG_INVERSE_OBJECT_PROPERTIES].contains(&expected_tag)
        {
            return Err(KernelError::malformed(
                "encoded role-axiom cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        let first = self.object_property_expression(self.field_node(start)?, maximum)?;
        let second = self.object_property_expression(self.field_node(start + 1)?, maximum)?;
        let annotation_hash = self.annotation_set_hash(start + 2, maximum)?;
        Ok((first, second, annotation_hash))
    }

    fn validate_object_property_set_axiom(
        self,
        node_id: usize,
        expected_tag: u16,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != expected_tag
            || ![
                TAG_EQUIVALENT_OBJECT_PROPERTIES,
                TAG_DISJOINT_OBJECT_PROPERTIES,
            ]
            .contains(&expected_tag)
        {
            return Err(KernelError::malformed(
                "encoded object-property set cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 2)?;
        let (item_start, length) = self.node_set_range(start, 2)?;
        for item_index in item_start..item_start + length {
            self.object_property_expression(self.item_node(item_index)?, maximum)?;
        }
        self.validate_annotation_set(start + 1)
    }

    fn validate_object_property_characteristic(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        let tag = self.node_tag(node_id)?;
        if !is_object_property_characteristic(tag) {
            return Err(KernelError::malformed(
                "encoded object-property characteristic cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 2)?;
        self.object_property_expression(self.field_node(start)?, maximum)?;
        self.validate_annotation_set(start + 1)
    }

    fn object_property_assertion_parts(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(IndividualValue<'a>, &'a str, IndividualValue<'a>), KernelError> {
        if self.node_tag(node_id)? != TAG_OBJECT_PROPERTY_ASSERTION {
            return Err(KernelError::malformed(
                "encoded object-property assertion cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 4)?;
        let property_id = self.field_node(start)?;
        let relation = match self.node_tag(property_id)? {
            TAG_ENTITY => self.named_object_property_iri(property_id, maximum)?,
            TAG_OBJECT_INVERSE_OF => {
                self.object_inverse_iri(property_id, maximum)?;
                return Err(KernelError::reference_failure(
                    "the pinned mOWL profile fails on inverse object-property assertions",
                ));
            }
            _ => {
                return Err(KernelError::unsupported(
                    "direct native slice requires a named ObjectPropertyAssertion property",
                ));
            }
        };
        let source = self.individual_value(self.field_node(start + 1)?, maximum)?;
        let destination = self.individual_value(self.field_node(start + 2)?, maximum)?;
        self.validate_annotation_set(start + 3)?;
        Ok((source, relation, destination))
    }

    fn validate_negative_object_property_assertion(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION {
            return Err(KernelError::malformed(
                "encoded negative object-property assertion cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 4)?;
        self.object_property_expression_iri(self.field_node(start)?, maximum)?;
        self.validate_individual(self.field_node(start + 1)?, maximum)?;
        self.validate_individual(self.field_node(start + 2)?, maximum)?;
        self.validate_annotation_set(start + 3)?;
        Ok(())
    }

    fn validate_class_expression_reference(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        match self.node_tag(node_id)? {
            TAG_ENTITY => self.named_class_iri(node_id, maximum).map(|_iri| ()),
            tag if is_class_expression_tag(tag) => Ok(()),
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::malformed(
                "encoded class-expression reference has the wrong constructor tag",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded class-expression tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn restriction_projection(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<Option<(&'a str, &'a str)>, KernelError> {
        let (property_index, filler_index) = match self.node_tag(node_id)? {
            TAG_OBJECT_SOME_VALUES_FROM | TAG_OBJECT_ALL_VALUES_FROM => {
                let start = self.exact_fields(node_id, 2)?;
                (start, start + 1)
            }
            TAG_OBJECT_MIN_CARDINALITY | TAG_OBJECT_MAX_CARDINALITY => {
                let start = self.exact_fields(node_id, 3)?;
                self.canonical_integer(start)?;
                (start + 1, start + 2)
            }
            tag if SCHEMA_TAGS.contains(&tag) => {
                return Err(KernelError::unsupported(format!(
                    "direct native slice does not support restriction tag {tag}",
                )));
            }
            tag => {
                return Err(KernelError::malformed(format!(
                    "encoded restriction tag {tag} is outside structural-columns v1",
                )));
            }
        };
        let relation =
            self.object_property_expression_iri(self.field_node(property_index)?, maximum)?;
        let filler_id = self.field_node(filler_index)?;
        self.validate_class_expression_reference(filler_id, maximum)?;
        if self.node_tag(filler_id)? != TAG_ENTITY {
            return Ok(None);
        }
        Ok(Some((relation, self.named_class_iri(filler_id, maximum)?)))
    }

    fn validate_facet_restriction(self, node_id: usize, maximum: usize) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_FACET_RESTRICTION {
            return Err(KernelError::malformed(
                "encoded facet-restriction cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 2)?;
        self.iri(self.field_node(start)?, maximum)?;
        self.validate_literal(self.field_node(start + 1)?, maximum)
    }

    fn validate_data_range_reference(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        match self.node_tag(node_id)? {
            TAG_ENTITY => self.named_datatype_iri(node_id, maximum).map(|_iri| ()),
            tag if is_data_range_tag(tag) => Ok(()),
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::malformed(
                "encoded data-range reference has the wrong constructor tag",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded data-range tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn validate_data_range_node(self, node_id: usize, maximum: usize) -> Result<(), KernelError> {
        match self.node_tag(node_id)? {
            TAG_ENTITY => self.named_datatype_iri(node_id, maximum).map(|_iri| ()),
            TAG_DATA_INTERSECTION_OF | TAG_DATA_UNION_OF => {
                let tag = self.node_tag(node_id)?;
                let start = self.exact_fields(node_id, 1)?;
                let (item_start, length) = self.node_set_range(start, 2)?;
                for item_index in item_start..item_start + length {
                    let operand_id = self.item_node(item_index)?;
                    if self.node_tag(operand_id)? == tag {
                        return Err(KernelError::malformed(
                            "encoded data-range aggregate operands are not flattened",
                        ));
                    }
                    self.validate_data_range_reference(operand_id, maximum)?;
                }
                Ok(())
            }
            TAG_DATA_COMPLEMENT_OF => {
                let start = self.exact_fields(node_id, 1)?;
                self.validate_data_range_reference(self.field_node(start)?, maximum)
            }
            TAG_DATA_ONE_OF => {
                let start = self.exact_fields(node_id, 1)?;
                let (item_start, length) = self.node_set_range(start, 1)?;
                for item_index in item_start..item_start + length {
                    self.validate_literal(self.item_node(item_index)?, maximum)?;
                }
                Ok(())
            }
            TAG_DATATYPE_RESTRICTION => {
                let start = self.exact_fields(node_id, 2)?;
                self.named_datatype_iri(self.field_node(start)?, maximum)?;
                let (item_start, length) = self.node_set_range(start + 1, 1)?;
                for item_index in item_start..item_start + length {
                    self.validate_facet_restriction(self.item_node(item_index)?, maximum)?;
                }
                Ok(())
            }
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::malformed(
                "encoded data-range reference has the wrong constructor tag",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded data-range tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn validate_data_range_graph(
        self,
        maximum: usize,
        state: &AtomicU8,
    ) -> Result<(), KernelError> {
        let mut has_recursive_data_range = false;
        for node_id in 1..=self.node_count() {
            check_cancel(state, node_id)?;
            if is_recursive_data_range_tag(self.node_tag(node_id)?) {
                has_recursive_data_range = true;
                break;
            }
        }
        if !has_recursive_data_range {
            return Ok(());
        }

        let color_length = self
            .node_count()
            .checked_add(1)
            .ok_or_else(|| KernelError::resource("encoded data-range color length overflow"))?;
        let mut colors = Vec::new();
        colors
            .try_reserve_exact(color_length)
            .map_err(|_| KernelError::resource("encoded data-range color allocation failed"))?;
        colors.resize(color_length, 0_u8);
        let mut stack = Vec::new();
        let mut work_index = 0_usize;

        for start_id in 1..=self.node_count() {
            if !is_recursive_data_range_tag(self.node_tag(start_id)?) || colors[start_id] == 2 {
                continue;
            }
            queue_data_range_event(&mut stack, start_id, false)?;
            while let Some((node_id, exiting)) = stack.pop() {
                check_cancel(state, work_index)?;
                work_index = work_index.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded data-range traversal overflow")
                })?;
                if exiting {
                    colors[node_id] = 2;
                    continue;
                }
                match colors[node_id] {
                    2 => continue,
                    1 => {
                        return Err(KernelError::malformed("encoded data-range graph is cyclic"));
                    }
                    _ => {}
                }
                colors[node_id] = 1;
                queue_data_range_event(&mut stack, node_id, true)?;
                match self.node_tag(node_id)? {
                    TAG_DATA_INTERSECTION_OF | TAG_DATA_UNION_OF => {
                        let start = self.exact_fields(node_id, 1)?;
                        let (item_start, length) = self.node_set_range(start, 2)?;
                        for item_index in (item_start..item_start + length).rev() {
                            let child = self.item_node(item_index)?;
                            if !is_recursive_data_range_tag(self.node_tag(child)?) {
                                self.validate_data_range_reference(child, maximum)?;
                                continue;
                            }
                            if colors[child] == 1 {
                                return Err(KernelError::malformed(
                                    "encoded data-range graph is cyclic",
                                ));
                            }
                            if colors[child] == 0 {
                                queue_data_range_event(&mut stack, child, false)?;
                            }
                        }
                    }
                    TAG_DATA_COMPLEMENT_OF => {
                        let child = self.field_node(self.exact_fields(node_id, 1)?)?;
                        if is_recursive_data_range_tag(self.node_tag(child)?) {
                            if colors[child] == 1 {
                                return Err(KernelError::malformed(
                                    "encoded data-range graph is cyclic",
                                ));
                            }
                            if colors[child] == 0 {
                                queue_data_range_event(&mut stack, child, false)?;
                            }
                        } else {
                            self.validate_data_range_reference(child, maximum)?;
                        }
                    }
                    _ => {
                        return Err(KernelError::malformed(
                            "encoded recursive data-range cursor changed after preflight",
                        ));
                    }
                }
            }
        }
        Ok(())
    }

    fn recursive_class_single_child(self, node_id: usize) -> Result<Option<usize>, KernelError> {
        let child_index = match self.node_tag(node_id)? {
            TAG_OBJECT_COMPLEMENT_OF => self.exact_fields(node_id, 1)?,
            TAG_OBJECT_SOME_VALUES_FROM | TAG_OBJECT_ALL_VALUES_FROM => {
                self.exact_fields(node_id, 2)? + 1
            }
            TAG_OBJECT_MIN_CARDINALITY
            | TAG_OBJECT_MAX_CARDINALITY
            | TAG_OBJECT_EXACT_CARDINALITY => self.exact_fields(node_id, 3)? + 2,
            _ => return Ok(None),
        };
        self.field_node(child_index).map(Some)
    }

    fn validate_recursive_class_expression_graph(
        self,
        state: &AtomicU8,
    ) -> Result<(), KernelError> {
        let mut has_recursive_edge = false;
        'nodes: for node_id in 1..=self.node_count() {
            check_cancel(state, node_id)?;
            let tag = self.node_tag(node_id)?;
            if !is_recursive_class_expression_tag(tag) {
                continue;
            }
            if is_aggregate_tag(tag) {
                let (item_start, length) =
                    self.node_set_range(self.exact_fields(node_id, 1)?, 2)?;
                for item_index in item_start..item_start + length {
                    if is_recursive_class_expression_tag(
                        self.node_tag(self.item_node(item_index)?)?,
                    ) {
                        has_recursive_edge = true;
                        break 'nodes;
                    }
                }
            } else if let Some(child) = self.recursive_class_single_child(node_id)? {
                if is_recursive_class_expression_tag(self.node_tag(child)?) {
                    has_recursive_edge = true;
                    break;
                }
            }
        }
        if !has_recursive_edge {
            return Ok(());
        }

        let color_length = self.node_count().checked_add(1).ok_or_else(|| {
            KernelError::resource("encoded recursive class-expression color length overflow")
        })?;
        let mut colors = Vec::new();
        colors.try_reserve_exact(color_length).map_err(|_| {
            KernelError::resource("encoded recursive class-expression color allocation failed")
        })?;
        colors.resize(color_length, 0_u8);
        let mut stack = Vec::new();
        let mut work_index = 0_usize;

        for start_id in 1..=self.node_count() {
            if !is_recursive_class_expression_tag(self.node_tag(start_id)?) || colors[start_id] == 2
            {
                continue;
            }
            queue_recursive_class_event(&mut stack, start_id, false)?;
            while let Some((node_id, exiting)) = stack.pop() {
                check_cancel(state, work_index)?;
                work_index = work_index.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded recursive class-expression traversal overflow")
                })?;
                if exiting {
                    colors[node_id] = 2;
                    continue;
                }
                match colors[node_id] {
                    2 => continue,
                    1 => {
                        return Err(KernelError::malformed(
                            "encoded recursive class-expression graph is cyclic",
                        ));
                    }
                    _ => {}
                }
                colors[node_id] = 1;
                queue_recursive_class_event(&mut stack, node_id, true)?;
                let tag = self.node_tag(node_id)?;
                if is_aggregate_tag(tag) {
                    let (item_start, length) =
                        self.node_set_range(self.exact_fields(node_id, 1)?, 2)?;
                    for item_index in (item_start..item_start + length).rev() {
                        let child = self.item_node(item_index)?;
                        if !is_recursive_class_expression_tag(self.node_tag(child)?) {
                            continue;
                        }
                        if colors[child] == 1 {
                            return Err(KernelError::malformed(
                                "encoded recursive class-expression graph is cyclic",
                            ));
                        }
                        if colors[child] == 0 {
                            queue_recursive_class_event(&mut stack, child, false)?;
                        }
                    }
                } else if let Some(child) = self.recursive_class_single_child(node_id)? {
                    if !is_recursive_class_expression_tag(self.node_tag(child)?) {
                        continue;
                    }
                    if colors[child] == 1 {
                        return Err(KernelError::malformed(
                            "encoded recursive class-expression graph is cyclic",
                        ));
                    }
                    if colors[child] == 0 {
                        queue_recursive_class_event(&mut stack, child, false)?;
                    }
                } else {
                    return Err(KernelError::malformed(
                        "encoded recursive class-expression cursor changed after preflight",
                    ));
                }
            }
        }
        Ok(())
    }

    fn validate_data_class_expression(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        match self.node_tag(node_id)? {
            TAG_DATA_SOME_VALUES_FROM | TAG_DATA_ALL_VALUES_FROM => {
                let start = self.exact_fields(node_id, 2)?;
                let (item_start, length) = self.node_sequence_range(start, 1)?;
                for item_index in item_start..item_start + length {
                    self.named_data_property_iri(self.item_node(item_index)?, maximum)?;
                }
                self.validate_data_range_node(self.field_node(start + 1)?, maximum)
            }
            TAG_DATA_HAS_VALUE => {
                let start = self.exact_fields(node_id, 2)?;
                self.named_data_property_iri(self.field_node(start)?, maximum)?;
                self.validate_literal(self.field_node(start + 1)?, maximum)
            }
            TAG_DATA_MIN_CARDINALITY | TAG_DATA_MAX_CARDINALITY | TAG_DATA_EXACT_CARDINALITY => {
                let start = self.exact_fields(node_id, 3)?;
                self.canonical_integer(start)?;
                self.named_data_property_iri(self.field_node(start + 1)?, maximum)?;
                self.validate_data_range_node(self.field_node(start + 2)?, maximum)
            }
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::malformed(
                "encoded data class-expression cursor has the wrong constructor tag",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded data class-expression tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn validate_nonprojecting_class_expression(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        match self.node_tag(node_id)? {
            TAG_OBJECT_ONE_OF => {
                let start = self.exact_fields(node_id, 1)?;
                let (item_start, length) = self.node_set_range(start, 1)?;
                for item_index in item_start..item_start + length {
                    self.validate_individual(self.item_node(item_index)?, maximum)?;
                }
                Ok(())
            }
            TAG_OBJECT_HAS_VALUE => {
                let start = self.exact_fields(node_id, 2)?;
                self.object_property_expression(self.field_node(start)?, maximum)?;
                self.validate_individual(self.field_node(start + 1)?, maximum)
            }
            TAG_OBJECT_HAS_SELF => {
                let start = self.exact_fields(node_id, 1)?;
                self.object_property_expression(self.field_node(start)?, maximum)?;
                Ok(())
            }
            TAG_OBJECT_EXACT_CARDINALITY => {
                let start = self.exact_fields(node_id, 3)?;
                self.canonical_integer(start)?;
                self.object_property_expression(self.field_node(start + 1)?, maximum)?;
                self.validate_class_expression_reference(self.field_node(start + 2)?, maximum)
            }
            tag if is_data_class_expression_tag(tag) => {
                self.validate_data_class_expression(node_id, maximum)
            }
            TAG_OBJECT_COMPLEMENT_OF => {
                let start = self.exact_fields(node_id, 1)?;
                self.validate_class_expression_reference(self.field_node(start)?, maximum)
            }
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::unsupported(
                "direct native slice does not support this nonprojecting class expression",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded class-expression tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn validate_ignored_subclass_operand(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        match self.node_tag(node_id)? {
            TAG_ENTITY => self.named_class_iri(node_id, maximum).map(|_iri| ()),
            tag if is_restriction_tag(tag) => self
                .restriction_projection(node_id, maximum)
                .map(|_projection| ()),
            tag if is_nonprojecting_class_tag(tag) => {
                self.validate_nonprojecting_class_expression(node_id, maximum)
            }
            tag if is_aggregate_tag(tag) => {
                self.class_expression_rank(node_id, maximum).map(|_rank| ())
            }
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::unsupported(
                "direct native ignored SubClassOf operand is outside the bounded class envelope",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded subclass operand tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn subclass_projection(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<SubclassProjection<'a>, KernelError> {
        let start = self.exact_fields(node_id, 3)?;
        let sub_id = self.field_node(start)?;
        let super_id = self.field_node(start + 1)?;
        let sub_tag = self.node_tag(sub_id)?;
        let super_tag = self.node_tag(super_id)?;
        let projection = if sub_tag == TAG_ENTITY && super_tag == TAG_ENTITY {
            SubclassProjection::Taxonomy {
                source: self.named_class_iri(sub_id, maximum)?,
                destination: self.named_class_iri(super_id, maximum)?,
            }
        } else if sub_tag == TAG_ENTITY && is_restriction_tag(super_tag) {
            let source = self.named_class_iri(sub_id, maximum)?;
            match self.restriction_projection(super_id, maximum)? {
                Some((relation, destination)) => SubclassProjection::Restriction {
                    source,
                    relation,
                    destination,
                },
                None => SubclassProjection::Ignored,
            }
        } else if is_restriction_tag(sub_tag) && super_tag == TAG_ENTITY {
            let source = self.named_class_iri(super_id, maximum)?;
            match self.restriction_projection(sub_id, maximum)? {
                Some((relation, destination)) => SubclassProjection::Restriction {
                    source,
                    relation,
                    destination,
                },
                None => SubclassProjection::Ignored,
            }
        } else if is_nonprojecting_class_tag(sub_tag)
            || is_nonprojecting_class_tag(super_tag)
            || is_aggregate_tag(sub_tag)
            || is_aggregate_tag(super_tag)
            || is_restriction_tag(sub_tag)
            || is_restriction_tag(super_tag)
        {
            self.validate_ignored_subclass_operand(sub_id, maximum)?;
            self.validate_ignored_subclass_operand(super_id, maximum)?;
            SubclassProjection::Ignored
        } else {
            return Err(KernelError::unsupported(
                "direct native slice supports only named taxonomy or named-role SubClassOf",
            ));
        };
        self.validate_annotation_set(start + 2)?;
        Ok(projection)
    }

    fn object_property_class_projection(
        self,
        node_id: usize,
        expected_tag: u16,
        maximum: usize,
    ) -> Result<Option<(&'a str, &'a str)>, KernelError> {
        if self.node_tag(node_id)? != expected_tag
            || ![TAG_OBJECT_PROPERTY_DOMAIN, TAG_OBJECT_PROPERTY_RANGE].contains(&expected_tag)
        {
            return Err(KernelError::malformed(
                "encoded domain/range cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        let property_id = self.field_node(start)?;
        let property = match self.node_tag(property_id)? {
            TAG_ENTITY => Some(self.named_object_property_iri(property_id, maximum)?),
            TAG_OBJECT_INVERSE_OF => {
                self.object_inverse_iri(property_id, maximum)?;
                None
            }
            tag if SCHEMA_TAGS.contains(&tag) => {
                return Err(KernelError::unsupported(
                    "direct native object domain/range property is outside the named/inverse envelope",
                ));
            }
            tag => {
                return Err(KernelError::malformed(format!(
                    "encoded object domain/range property tag {tag} is outside structural-columns v1",
                )));
            }
        };
        let class_id = self.field_node(start + 1)?;
        let class = if self.node_tag(class_id)? == TAG_ENTITY {
            Some(self.named_class_iri(class_id, maximum)?)
        } else {
            self.class_expression_rank(class_id, maximum)?;
            None
        };
        self.validate_annotation_set(start + 2)?;
        Ok(property.zip(class))
    }

    fn validate_aggregate_expression(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(), KernelError> {
        let aggregate_tag = self.node_tag(node_id)?;
        if !is_aggregate_tag(aggregate_tag) {
            return Err(KernelError::malformed(
                "encoded aggregate cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 1)?;
        let (item_start, length) = self.node_set_range(start, 2)?;
        for item_index in item_start..item_start + length {
            let operand_id = self.item_node(item_index)?;
            match self.node_tag(operand_id)? {
                TAG_ENTITY => {
                    self.named_class_iri(operand_id, maximum)?;
                }
                tag if is_restriction_tag(tag) => {
                    self.restriction_projection(operand_id, maximum)?;
                }
                tag if is_nonprojecting_class_tag(tag) => {
                    self.validate_nonprojecting_class_expression(operand_id, maximum)?;
                }
                tag if is_aggregate_tag(tag) => {
                    if tag == aggregate_tag {
                        return Err(KernelError::malformed(
                            "encoded class-aggregate operands are not flattened",
                        ));
                    }
                }
                tag if SCHEMA_TAGS.contains(&tag) => {
                    return Err(KernelError::unsupported(
                        "direct native aggregate operand is outside the bounded class envelope",
                    ));
                }
                tag => {
                    return Err(KernelError::malformed(format!(
                        "encoded aggregate operand tag {tag} is outside structural-columns v1",
                    )));
                }
            }
        }
        Ok(())
    }

    fn class_expression_rank(self, node_id: usize, maximum: usize) -> Result<u16, KernelError> {
        match self.node_tag(node_id)? {
            TAG_ENTITY => {
                self.named_class_iri(node_id, maximum)?;
                Ok(1001)
            }
            TAG_OBJECT_INTERSECTION_OF => {
                self.validate_aggregate_expression(node_id, maximum)?;
                Ok(3001)
            }
            TAG_OBJECT_UNION_OF => {
                self.validate_aggregate_expression(node_id, maximum)?;
                Ok(3002)
            }
            TAG_OBJECT_SOME_VALUES_FROM => {
                self.restriction_projection(node_id, maximum)?;
                Ok(3005)
            }
            TAG_OBJECT_ALL_VALUES_FROM => {
                self.restriction_projection(node_id, maximum)?;
                Ok(3006)
            }
            TAG_OBJECT_MIN_CARDINALITY => {
                self.restriction_projection(node_id, maximum)?;
                Ok(3008)
            }
            TAG_OBJECT_MAX_CARDINALITY => {
                self.restriction_projection(node_id, maximum)?;
                Ok(3010)
            }
            tag if is_nonprojecting_class_tag(tag) => {
                self.validate_nonprojecting_class_expression(node_id, maximum)?;
                Ok(3999)
            }
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::unsupported(
                "direct native class expression is outside the bounded named, aggregate, restriction, or nonprojecting envelope",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded class-expression tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn expression_precedes(
        self,
        left_id: usize,
        right_id: usize,
        maximum: usize,
    ) -> Result<bool, KernelError> {
        let left_rank = self.class_expression_rank(left_id, maximum)?;
        let right_rank = self.class_expression_rank(right_id, maximum)?;
        if left_rank != right_rank {
            return Ok(left_rank < right_rank);
        }
        if left_rank == 1001 {
            let left = self.named_class_iri(left_id, maximum)?;
            let right = self.named_class_iri(right_id, maximum)?;
            return Ok((left.as_bytes(), left_id) < (right.as_bytes(), right_id));
        }
        Ok(left_id < right_id)
    }

    fn equivalent_projection(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<EquivalentProjection<'a>, KernelError> {
        if self.node_tag(node_id)? != TAG_EQUIVALENT_CLASSES {
            return Err(KernelError::malformed(
                "encoded equivalent cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 2)?;
        let (item_start, length) = self.node_set_range(start, 2)?;
        let mut first_id: Option<usize> = None;
        let mut second_id: Option<usize> = None;
        for item_index in item_start..item_start + length {
            let expression_id = self.item_node(item_index)?;
            self.class_expression_rank(expression_id, maximum)?;
            match first_id {
                None => first_id = Some(expression_id),
                Some(current) if self.expression_precedes(expression_id, current, maximum)? => {
                    second_id = first_id;
                    first_id = Some(expression_id);
                }
                _ if match second_id {
                    None => true,
                    Some(current) => self.expression_precedes(expression_id, current, maximum)?,
                } =>
                {
                    second_id = Some(expression_id);
                }
                _ => {}
            }
        }
        self.validate_annotation_set(start + 1)?;
        let (Some(first_id), Some(second_id)) = (first_id, second_id) else {
            return Err(KernelError::malformed(
                "encoded EquivalentClasses has too few expressions",
            ));
        };
        if self.node_tag(first_id)? != TAG_ENTITY {
            return Ok(EquivalentProjection::Ignored);
        }
        let source = self.named_class_iri(first_id, maximum)?;
        match self.node_tag(second_id)? {
            TAG_ENTITY => Ok(EquivalentProjection::Pair {
                source,
                destination: self.named_class_iri(second_id, maximum)?,
            }),
            tag if is_aggregate_tag(tag) => Ok(EquivalentProjection::Aggregate {
                source,
                expression_id: second_id,
            }),
            _ => Ok(EquivalentProjection::Ignored),
        }
    }

    fn validate_disjoint_classes(self, node_id: usize, maximum: usize) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_DISJOINT_CLASSES {
            return Err(KernelError::malformed(
                "encoded disjoint-classes cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 2)?;
        let (item_start, length) = self.node_set_range(start, 2)?;
        for item_index in item_start..item_start + length {
            self.class_expression_rank(self.item_node(item_index)?, maximum)?;
        }
        self.validate_annotation_set(start + 1)
    }

    fn validate_disjoint_union(self, node_id: usize, maximum: usize) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_DISJOINT_UNION {
            return Err(KernelError::malformed(
                "encoded disjoint-union cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        self.named_class_iri(self.field_node(start)?, maximum)?;
        let (item_start, length) = self.node_set_range(start + 1, 2)?;
        for item_index in item_start..item_start + length {
            self.class_expression_rank(self.item_node(item_index)?, maximum)?;
        }
        self.validate_annotation_set(start + 2)
    }

    fn class_assertion_projection(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<ClassAssertionProjection<'a>, KernelError> {
        let start = self.exact_fields(node_id, 3)?;
        let class_id = self.field_node(start)?;
        let individual_id = self.field_node(start + 1)?;
        let named_class = self.node_tag(class_id)? == TAG_ENTITY;
        if named_class {
            self.named_class_iri(class_id, maximum)?;
        } else if is_restriction_tag(self.node_tag(class_id)?)
            || is_nonprojecting_class_tag(self.node_tag(class_id)?)
            || is_aggregate_tag(self.node_tag(class_id)?)
        {
            self.class_expression_rank(class_id, maximum)?;
        } else {
            return Err(KernelError::unsupported(
                "direct native ClassAssertion class is outside the bounded class envelope",
            ));
        }
        let named_individual = self.node_tag(individual_id)? == TAG_ENTITY;
        self.validate_individual(individual_id, maximum)?;
        self.validate_annotation_set(start + 2)?;
        if named_class && named_individual {
            Ok(ClassAssertionProjection::Edge {
                individual: self.named_individual_iri(individual_id, maximum)?,
                class: self.named_class_iri(class_id, maximum)?,
            })
        } else {
            Ok(ClassAssertionProjection::Ignored)
        }
    }

    fn validate_generic(self, state: &AtomicU8) -> Result<(), KernelError> {
        for (name, width, buffer) in [
            ("root_ids", 4, self.root_ids),
            ("node_tags", 2, self.node_tags),
            ("node_field_offsets", 8, self.node_field_offsets),
            ("field_values", 8, self.field_values),
            ("field_lengths", 8, self.field_lengths),
            ("item_values", 8, self.item_values),
            ("item_lengths", 8, self.item_lengths),
        ] {
            if buffer.len() % width != 0 {
                return Err(KernelError::malformed(format!(
                    "encoded buffer {name} length is not divisible by {width}",
                )));
            }
        }
        if self.root_ids.len() / 4 != self.root_count() {
            return Err(KernelError::malformed(
                "encoded root columns differ in length",
            ));
        }
        if self.node_field_offsets.len() / 8 != self.node_count() + 1 {
            return Err(KernelError::malformed(
                "encoded node-field offsets do not match node count",
            ));
        }
        if self.field_values.len() / 8 != self.field_count()
            || self.field_lengths.len() / 8 != self.field_count()
        {
            return Err(KernelError::malformed(
                "encoded field columns differ in length",
            ));
        }
        if self.item_values.len() / 8 != self.item_count()
            || self.item_lengths.len() / 8 != self.item_count()
        {
            return Err(KernelError::malformed(
                "encoded item columns differ in length",
            ));
        }

        let mut previous = 0_usize;
        for index in 0..=self.node_count() {
            check_cancel(state, index)?;
            let offset = read_usize(self.node_field_offsets, index, "node_field_offsets")?;
            if (index == 0 && offset != 0) || offset < previous || offset > self.field_count() {
                return Err(KernelError::malformed(
                    "encoded node-field offsets are not monotone in-range boundaries",
                ));
            }
            previous = offset;
        }
        if previous != self.field_count() {
            return Err(KernelError::malformed(
                "encoded node-field offsets do not cover every field",
            ));
        }

        let mut previous_root: Option<(u8, usize)> = None;
        for index in 0..self.root_count() {
            check_cancel(state, index)?;
            let kind = self.root_kind(index)?;
            if ![ROOT_ONTOLOGY_ANNOTATION, ROOT_AXIOM, ROOT_EXTENSION].contains(&kind) {
                return Err(KernelError::malformed("encoded root kind is invalid"));
            }
            let node_id = self.root_id(index)?;
            let key = (kind, node_id);
            if previous_root.is_some_and(|previous| previous >= key) {
                return Err(KernelError::malformed(
                    "encoded roots are not canonical and unique",
                ));
            }
            previous_root = Some(key);
        }

        let mut item_cursor = 0_usize;
        let mut scalar_cursor = 0_usize;
        for index in 0..self.field_count() {
            check_cancel(state, index)?;
            let kind = self.field_kind(index)?;
            let value = self.field_value(index)?;
            let length = self.field_length(index)?;
            if [COMPONENT_SET, COMPONENT_SEQUENCE].contains(&kind) {
                if value != item_cursor || length > self.item_count().saturating_sub(item_cursor) {
                    return Err(KernelError::malformed(
                        "encoded collection offset or bounds are invalid",
                    ));
                }
                let end = item_cursor + length;
                let mut previous_set_node = 0_usize;
                while item_cursor < end {
                    let item_kind = self.item_kinds[item_cursor];
                    if kind == COMPONENT_SET && item_kind != COMPONENT_NODE {
                        return Err(KernelError::malformed(
                            "encoded canonical-set item is not a node reference",
                        ));
                    }
                    if kind == COMPONENT_SET {
                        let node_id = read_usize(self.item_values, item_cursor, "item_values")?;
                        self.checked_node_id(node_id)?;
                        if node_id <= previous_set_node {
                            return Err(KernelError::malformed(
                                "encoded canonical-set items are not sorted and unique",
                            ));
                        }
                        previous_set_node = node_id;
                    }
                    scalar_cursor = self.validate_leaf(
                        item_kind,
                        read_usize(self.item_values, item_cursor, "item_values")?,
                        read_usize(self.item_lengths, item_cursor, "item_lengths")?,
                        scalar_cursor,
                    )?;
                    item_cursor += 1;
                }
            } else {
                scalar_cursor = self.validate_leaf(kind, value, length, scalar_cursor)?;
            }
        }
        if item_cursor != self.item_count() {
            return Err(KernelError::malformed(
                "encoded collection offsets do not cover every item",
            ));
        }
        if scalar_cursor != self.scalar_bytes.len() {
            return Err(KernelError::malformed(
                "encoded scalar offsets do not cover the byte arena",
            ));
        }
        Ok(())
    }

    fn validate_leaf(
        self,
        kind: u8,
        value: usize,
        length: usize,
        scalar_cursor: usize,
    ) -> Result<usize, KernelError> {
        match kind {
            COMPONENT_NONE => {
                if value != 0 || length != 0 {
                    return Err(KernelError::malformed(
                        "encoded none component is not canonical",
                    ));
                }
                Ok(scalar_cursor)
            }
            COMPONENT_NODE => {
                if length != 0 {
                    return Err(KernelError::malformed(
                        "encoded node component has a nonzero length",
                    ));
                }
                self.checked_node_id(value)?;
                Ok(scalar_cursor)
            }
            COMPONENT_TEXT | COMPONENT_BYTES | COMPONENT_INTEGER | COMPONENT_ENUM => {
                if value != scalar_cursor
                    || length > self.scalar_bytes.len().saturating_sub(scalar_cursor)
                {
                    return Err(KernelError::malformed(
                        "encoded scalar offset or bounds are invalid",
                    ));
                }
                Ok(scalar_cursor + length)
            }
            _ => Err(KernelError::malformed("encoded component kind is invalid")),
        }
    }

    fn validate_variable(self, node_id: usize, maximum_iri: usize) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_VARIABLE {
            return Err(KernelError::malformed(
                "encoded Variable cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 1)?;
        self.iri(self.field_node(start)?, maximum_iri)
            .map(|_iri| ())
    }

    fn validate_swrl_individual_argument(
        self,
        node_id: usize,
        maximum_iri: usize,
    ) -> Result<(), KernelError> {
        if self.node_tag(node_id)? == TAG_VARIABLE {
            self.validate_variable(node_id, maximum_iri)
        } else {
            self.validate_individual(node_id, maximum_iri)
        }
    }

    fn validate_swrl_data_argument(
        self,
        node_id: usize,
        maximum_iri: usize,
    ) -> Result<(), KernelError> {
        match self.node_tag(node_id)? {
            TAG_VARIABLE => self.validate_variable(node_id, maximum_iri),
            TAG_LITERAL => self.validate_literal(node_id, maximum_iri),
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::malformed(
                "encoded SWRL data argument is not a Variable or Literal",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded SWRL data-argument tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn validate_swrl_class_expression(
        self,
        node_id: usize,
        maximum_iri: usize,
    ) -> Result<(), KernelError> {
        self.class_expression_rank(node_id, maximum_iri)
            .map(|_rank| ())
    }

    fn validate_swrl_atom(self, node_id: usize, maximum_iri: usize) -> Result<(), KernelError> {
        match self.node_tag(node_id)? {
            TAG_CLASS_ATOM => {
                let start = self.exact_fields(node_id, 2)?;
                self.validate_swrl_class_expression(self.field_node(start)?, maximum_iri)?;
                self.validate_swrl_individual_argument(self.field_node(start + 1)?, maximum_iri)
            }
            TAG_DATA_RANGE_ATOM => {
                let start = self.exact_fields(node_id, 2)?;
                self.validate_data_range_node(self.field_node(start)?, maximum_iri)?;
                self.validate_swrl_data_argument(self.field_node(start + 1)?, maximum_iri)
            }
            TAG_OBJECT_PROPERTY_ATOM => {
                let start = self.exact_fields(node_id, 3)?;
                self.object_property_expression(self.field_node(start)?, maximum_iri)?;
                self.validate_swrl_individual_argument(self.field_node(start + 1)?, maximum_iri)?;
                self.validate_swrl_individual_argument(self.field_node(start + 2)?, maximum_iri)
            }
            TAG_DATA_PROPERTY_ATOM => {
                let start = self.exact_fields(node_id, 3)?;
                self.named_data_property_iri(self.field_node(start)?, maximum_iri)?;
                self.validate_swrl_individual_argument(self.field_node(start + 1)?, maximum_iri)?;
                self.validate_swrl_data_argument(self.field_node(start + 2)?, maximum_iri)
            }
            TAG_BUILT_IN_ATOM => {
                let start = self.exact_fields(node_id, 2)?;
                self.iri(self.field_node(start)?, maximum_iri)?;
                let (item_start, length) = self.node_sequence_range(start + 1, 0)?;
                for item_index in item_start..item_start + length {
                    self.validate_swrl_data_argument(self.item_node(item_index)?, maximum_iri)?;
                }
                Ok(())
            }
            TAG_SAME_INDIVIDUAL_ATOM | TAG_DIFFERENT_INDIVIDUALS_ATOM => {
                let start = self.exact_fields(node_id, 2)?;
                self.validate_swrl_individual_argument(self.field_node(start)?, maximum_iri)?;
                self.validate_swrl_individual_argument(self.field_node(start + 1)?, maximum_iri)
            }
            tag if SCHEMA_TAGS.contains(&tag) => Err(KernelError::malformed(
                "encoded SWRL atom cursor has the wrong constructor tag",
            )),
            tag => Err(KernelError::malformed(format!(
                "encoded SWRL atom tag {tag} is outside structural-columns v1",
            ))),
        }
    }

    fn validate_swrl_rule(self, node_id: usize) -> Result<(), KernelError> {
        if self.node_tag(node_id)? != TAG_SWRL_RULE {
            return Err(KernelError::malformed(
                "encoded SWRLRule cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        for index in [start, start + 1] {
            let (item_start, length) = self.node_set_range(index, 0)?;
            for item_index in item_start..item_start + length {
                if !is_swrl_atom_tag(self.node_tag(self.item_node(item_index)?)?) {
                    return Err(KernelError::malformed(
                        "encoded SWRLRule body or head contains a non-atom",
                    ));
                }
            }
        }
        self.validate_annotation_set(start + 2)
    }

    fn validate_supported_nodes(
        self,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<(), KernelError> {
        for node_id in 1..=self.node_count() {
            check_cancel(state, node_id)?;
            match self.node_tag(node_id)? {
                TAG_IRI => {
                    self.iri(node_id, maximum_iri)?;
                }
                TAG_ENTITY => {
                    let (_kind, iri_id) = self.entity(node_id)?;
                    self.iri(iri_id, maximum_iri)?;
                }
                TAG_ANONYMOUS_INDIVIDUAL => {
                    self.validate_anonymous_individual(node_id)?;
                }
                TAG_LITERAL => {
                    self.validate_literal(node_id, maximum_iri)?;
                }
                TAG_ANNOTATION => {
                    self.validate_annotation(node_id, maximum_iri)?;
                }
                TAG_OBJECT_INVERSE_OF => {
                    self.object_inverse_iri(node_id, maximum_iri)?;
                }
                TAG_OBJECT_PROPERTY_CHAIN => {
                    self.validate_object_property_chain(node_id, maximum_iri)?;
                }
                TAG_FACET_RESTRICTION => {
                    self.validate_facet_restriction(node_id, maximum_iri)?;
                }
                TAG_DATA_INTERSECTION_OF
                | TAG_DATA_UNION_OF
                | TAG_DATA_COMPLEMENT_OF
                | TAG_DATA_ONE_OF
                | TAG_DATATYPE_RESTRICTION => {
                    self.validate_data_range_node(node_id, maximum_iri)?;
                }
                TAG_OBJECT_INTERSECTION_OF | TAG_OBJECT_UNION_OF => {
                    self.validate_aggregate_expression(node_id, maximum_iri)?;
                }
                TAG_DECLARATION => {
                    let start = self.exact_fields(node_id, 2)?;
                    self.entity(self.field_node(start)?)?;
                    self.validate_annotation_set(start + 1)?;
                }
                TAG_OBJECT_SOME_VALUES_FROM
                | TAG_OBJECT_ALL_VALUES_FROM
                | TAG_OBJECT_MIN_CARDINALITY
                | TAG_OBJECT_MAX_CARDINALITY => {
                    self.restriction_projection(node_id, maximum_iri)?;
                }
                TAG_OBJECT_COMPLEMENT_OF
                | TAG_OBJECT_ONE_OF
                | TAG_OBJECT_HAS_VALUE
                | TAG_OBJECT_HAS_SELF
                | TAG_OBJECT_EXACT_CARDINALITY
                | TAG_DATA_SOME_VALUES_FROM
                | TAG_DATA_ALL_VALUES_FROM
                | TAG_DATA_HAS_VALUE
                | TAG_DATA_MIN_CARDINALITY
                | TAG_DATA_MAX_CARDINALITY
                | TAG_DATA_EXACT_CARDINALITY => {
                    self.validate_nonprojecting_class_expression(node_id, maximum_iri)?;
                }
                TAG_SUB_CLASS_OF => {
                    self.subclass_projection(node_id, maximum_iri)?;
                }
                TAG_EQUIVALENT_CLASSES => {
                    self.equivalent_projection(node_id, maximum_iri)?;
                }
                TAG_DISJOINT_CLASSES => {
                    self.validate_disjoint_classes(node_id, maximum_iri)?;
                }
                TAG_DISJOINT_UNION => {
                    self.validate_disjoint_union(node_id, maximum_iri)?;
                }
                TAG_SUB_OBJECT_PROPERTY_OF => {
                    self.validate_sub_object_property_of(node_id, maximum_iri)?;
                }
                TAG_INVERSE_OBJECT_PROPERTIES => {
                    self.role_axiom_parts(node_id, TAG_INVERSE_OBJECT_PROPERTIES, maximum_iri)?;
                }
                TAG_EQUIVALENT_OBJECT_PROPERTIES | TAG_DISJOINT_OBJECT_PROPERTIES => {
                    self.validate_object_property_set_axiom(
                        node_id,
                        self.node_tag(node_id)?,
                        maximum_iri,
                    )?;
                }
                TAG_OBJECT_PROPERTY_DOMAIN => {
                    self.object_property_class_projection(
                        node_id,
                        TAG_OBJECT_PROPERTY_DOMAIN,
                        maximum_iri,
                    )?;
                }
                TAG_OBJECT_PROPERTY_RANGE => {
                    self.object_property_class_projection(
                        node_id,
                        TAG_OBJECT_PROPERTY_RANGE,
                        maximum_iri,
                    )?;
                }
                TAG_CLASS_ASSERTION => {
                    self.class_assertion_projection(node_id, maximum_iri)?;
                }
                TAG_OBJECT_PROPERTY_ASSERTION => {
                    self.object_property_assertion_parts(node_id, maximum_iri)?;
                }
                TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION => {
                    self.validate_negative_object_property_assertion(node_id, maximum_iri)?;
                }
                TAG_SUB_DATA_PROPERTY_OF => {
                    self.validate_binary_data_property_axiom(
                        node_id,
                        TAG_SUB_DATA_PROPERTY_OF,
                        maximum_iri,
                    )?;
                }
                TAG_EQUIVALENT_DATA_PROPERTIES | TAG_DISJOINT_DATA_PROPERTIES => {
                    self.validate_data_property_set_axiom(
                        node_id,
                        self.node_tag(node_id)?,
                        maximum_iri,
                    )?;
                }
                TAG_DATA_PROPERTY_DOMAIN => {
                    self.validate_data_property_domain(node_id, maximum_iri)?;
                }
                TAG_DATA_PROPERTY_RANGE => {
                    self.validate_data_property_range(node_id, maximum_iri)?;
                }
                TAG_FUNCTIONAL_DATA_PROPERTY => {
                    self.validate_functional_data_property(node_id, maximum_iri)?;
                }
                TAG_DATATYPE_DEFINITION => {
                    self.validate_datatype_definition(node_id, maximum_iri)?;
                }
                TAG_HAS_KEY => {
                    self.validate_has_key(node_id, maximum_iri)?;
                }
                TAG_SAME_INDIVIDUAL | TAG_DIFFERENT_INDIVIDUALS => {
                    self.validate_individual_set_axiom(
                        node_id,
                        self.node_tag(node_id)?,
                        maximum_iri,
                    )?;
                }
                TAG_DATA_PROPERTY_ASSERTION | TAG_NEGATIVE_DATA_PROPERTY_ASSERTION => {
                    self.validate_data_property_assertion(
                        node_id,
                        self.node_tag(node_id)?,
                        maximum_iri,
                    )?;
                }
                TAG_ANNOTATION_ASSERTION => {
                    self.validate_annotation_assertion(node_id, maximum_iri)?;
                }
                TAG_SUB_ANNOTATION_PROPERTY_OF => {
                    self.validate_sub_annotation_property_of(node_id, maximum_iri)?;
                }
                TAG_ANNOTATION_PROPERTY_DOMAIN | TAG_ANNOTATION_PROPERTY_RANGE => {
                    self.validate_annotation_property_iri_axiom(
                        node_id,
                        self.node_tag(node_id)?,
                        maximum_iri,
                    )?;
                }
                TAG_VARIABLE => {
                    self.validate_variable(node_id, maximum_iri)?;
                }
                tag if is_swrl_atom_tag(tag) => {
                    self.validate_swrl_atom(node_id, maximum_iri)?;
                }
                TAG_SWRL_RULE => {
                    self.validate_swrl_rule(node_id)?;
                }
                tag if is_object_property_characteristic(tag) => {
                    self.validate_object_property_characteristic(node_id, maximum_iri)?;
                }
                tag if SCHEMA_TAGS.contains(&tag) => {
                    return Err(KernelError::unsupported(format!(
                        "direct native slice does not support schema tag {tag}",
                    )));
                }
                tag => {
                    return Err(KernelError::malformed(format!(
                        "encoded node tag {tag} is outside structural-columns v1",
                    )));
                }
            }
        }
        self.validate_recursive_class_expression_graph(state)?;
        self.validate_data_range_graph(maximum_iri, state)
    }

    fn classify_roots(
        self,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<RootCounts, KernelError> {
        let mut counts = RootCounts::default();
        for index in 0..self.root_count() {
            check_cancel(state, index)?;
            let kind = self.root_kind(index)?;
            let node_id = self.root_id(index)?;
            let tag = self.node_tag(node_id)?;
            match (kind, tag) {
                (ROOT_AXIOM, TAG_DECLARATION) => counts.declarations += 1,
                (ROOT_AXIOM, TAG_SUB_CLASS_OF) => {
                    counts.subclasses += 1;
                    match self.subclass_projection(node_id, maximum_iri)? {
                        SubclassProjection::Restriction { .. } => {
                            counts.restriction_subclasses += 1;
                        }
                        SubclassProjection::Ignored => counts.ignored_subclasses += 1,
                        SubclassProjection::Taxonomy { .. } => {}
                    }
                }
                (ROOT_AXIOM, TAG_EQUIVALENT_CLASSES) => {
                    counts.equivalents += 1;
                    if matches!(
                        self.equivalent_projection(node_id, maximum_iri)?,
                        EquivalentProjection::Aggregate { .. }
                    ) {
                        counts.aggregate_equivalents += 1;
                    }
                }
                (ROOT_AXIOM, TAG_DISJOINT_CLASSES) => counts.disjoint_classes += 1,
                (ROOT_AXIOM, TAG_DISJOINT_UNION) => counts.disjoint_unions += 1,
                (ROOT_AXIOM, TAG_SUB_OBJECT_PROPERTY_OF) => {
                    counts.sub_object_properties += 1;
                    if self.validate_sub_object_property_of(node_id, maximum_iri)? {
                        counts.object_property_chains += 1;
                    }
                }
                (ROOT_AXIOM, TAG_EQUIVALENT_OBJECT_PROPERTIES) => {
                    counts.equivalent_object_properties += 1;
                }
                (ROOT_AXIOM, TAG_DISJOINT_OBJECT_PROPERTIES) => {
                    counts.disjoint_object_properties += 1;
                }
                (ROOT_AXIOM, TAG_INVERSE_OBJECT_PROPERTIES) => {
                    counts.inverse_object_properties += 1;
                }
                (ROOT_AXIOM, TAG_OBJECT_PROPERTY_DOMAIN) => {
                    counts.object_property_domains += 1;
                }
                (ROOT_AXIOM, TAG_OBJECT_PROPERTY_RANGE) => {
                    counts.object_property_ranges += 1;
                }
                (ROOT_AXIOM, TAG_CLASS_ASSERTION) => {
                    counts.class_assertions += 1;
                    if matches!(
                        self.class_assertion_projection(node_id, maximum_iri)?,
                        ClassAssertionProjection::Ignored
                    ) {
                        counts.ignored_class_assertions += 1;
                    }
                }
                (ROOT_AXIOM, TAG_OBJECT_PROPERTY_ASSERTION) => {
                    counts.object_property_assertions += 1;
                }
                (ROOT_AXIOM, TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION) => {
                    counts.negative_object_property_assertions += 1;
                }
                (ROOT_AXIOM, TAG_FUNCTIONAL_OBJECT_PROPERTY) => {
                    counts.functional_object_properties += 1;
                }
                (ROOT_AXIOM, TAG_INVERSE_FUNCTIONAL_OBJECT_PROPERTY) => {
                    counts.inverse_functional_object_properties += 1;
                }
                (ROOT_AXIOM, TAG_REFLEXIVE_OBJECT_PROPERTY) => {
                    counts.reflexive_object_properties += 1;
                }
                (ROOT_AXIOM, TAG_IRREFLEXIVE_OBJECT_PROPERTY) => {
                    counts.irreflexive_object_properties += 1;
                }
                (ROOT_AXIOM, TAG_SYMMETRIC_OBJECT_PROPERTY) => {
                    counts.symmetric_object_properties += 1;
                }
                (ROOT_AXIOM, TAG_ASYMMETRIC_OBJECT_PROPERTY) => {
                    counts.asymmetric_object_properties += 1;
                }
                (ROOT_AXIOM, TAG_TRANSITIVE_OBJECT_PROPERTY) => {
                    counts.transitive_object_properties += 1;
                }
                (ROOT_AXIOM, TAG_SUB_DATA_PROPERTY_OF) => {
                    counts.sub_data_properties += 1;
                }
                (ROOT_AXIOM, TAG_EQUIVALENT_DATA_PROPERTIES) => {
                    counts.equivalent_data_properties += 1;
                }
                (ROOT_AXIOM, TAG_DISJOINT_DATA_PROPERTIES) => {
                    counts.disjoint_data_properties += 1;
                }
                (ROOT_AXIOM, TAG_DATA_PROPERTY_DOMAIN) => {
                    counts.data_property_domains += 1;
                }
                (ROOT_AXIOM, TAG_DATA_PROPERTY_RANGE) => {
                    counts.data_property_ranges += 1;
                }
                (ROOT_AXIOM, TAG_FUNCTIONAL_DATA_PROPERTY) => {
                    counts.functional_data_properties += 1;
                }
                (ROOT_AXIOM, TAG_DATATYPE_DEFINITION) => {
                    counts.datatype_definitions += 1;
                }
                (ROOT_AXIOM, TAG_HAS_KEY) => counts.has_keys += 1,
                (ROOT_AXIOM, TAG_SAME_INDIVIDUAL) => counts.same_individuals += 1,
                (ROOT_AXIOM, TAG_DIFFERENT_INDIVIDUALS) => {
                    counts.different_individuals += 1;
                }
                (ROOT_AXIOM, TAG_DATA_PROPERTY_ASSERTION) => {
                    counts.data_property_assertions += 1;
                }
                (ROOT_AXIOM, TAG_NEGATIVE_DATA_PROPERTY_ASSERTION) => {
                    counts.negative_data_property_assertions += 1;
                }
                (ROOT_AXIOM, TAG_ANNOTATION_ASSERTION) => {
                    counts.annotation_assertions += 1;
                }
                (ROOT_AXIOM, TAG_SUB_ANNOTATION_PROPERTY_OF) => {
                    counts.sub_annotation_properties += 1;
                }
                (ROOT_AXIOM, TAG_ANNOTATION_PROPERTY_DOMAIN) => {
                    counts.annotation_property_domains += 1;
                }
                (ROOT_AXIOM, TAG_ANNOTATION_PROPERTY_RANGE) => {
                    counts.annotation_property_ranges += 1;
                }
                (ROOT_ONTOLOGY_ANNOTATION, TAG_ANNOTATION) => {
                    counts.ontology_annotations += 1;
                }
                (ROOT_EXTENSION, TAG_SWRL_RULE) => {
                    counts.swrl_rules += 1;
                }
                (ROOT_AXIOM, TAG_ANNOTATION)
                | (ROOT_AXIOM, TAG_SWRL_RULE)
                | (ROOT_ONTOLOGY_ANNOTATION, _)
                | (ROOT_EXTENSION, _) => {
                    return Err(KernelError::malformed(
                        "encoded root kind does not match its constructor tag",
                    ));
                }
                (ROOT_AXIOM, known) if SCHEMA_TAGS.contains(&known) => {
                    return Err(KernelError::unsupported(format!(
                        "direct native slice does not support axiom tag {known}",
                    )));
                }
                _ => {
                    return Err(KernelError::malformed(
                        "encoded root kind does not match its constructor tag",
                    ));
                }
            }
        }
        Ok(counts)
    }

    fn build_role_state(
        self,
        counts: RootCounts,
        maximum_iri: usize,
        state: &AtomicU8,
        retained: Option<&OwnedRoleState>,
    ) -> Result<RoleState<'a>, KernelError> {
        let role_axiom_count = counts.role_axioms()?;
        let mut rows = Vec::new();
        rows.try_reserve_exact(role_axiom_count)
            .map_err(|_| KernelError::resource("encoded role-row allocation failed"))?;
        for canonical_order in 0..self.root_count() {
            check_cancel(state, canonical_order)?;
            let node_id = self.root_id(canonical_order)?;
            let tag = self.node_tag(node_id)?;
            if ![TAG_SUB_OBJECT_PROPERTY_OF, TAG_INVERSE_OBJECT_PROPERTIES].contains(&tag) {
                continue;
            }
            if tag == TAG_SUB_OBJECT_PROPERTY_OF
                && self.validate_sub_object_property_of(node_id, maximum_iri)?
            {
                continue;
            }
            let (first, second, annotation_hash) =
                self.role_axiom_parts(node_id, tag, maximum_iri)?;
            let owlapi_hash = if tag == TAG_SUB_OBJECT_PROPERTY_OF {
                combine_hash(
                    1823,
                    &[first.owlapi_hash, second.owlapi_hash, annotation_hash],
                )
            } else {
                combine_hash(
                    1229,
                    &[
                        first.owlapi_hash.wrapping_add(second.owlapi_hash),
                        annotation_hash,
                    ],
                )
            };
            let unsigned = owlapi_hash as u32;
            rows.push(RoleAxiom {
                tag,
                first: first.iri,
                second: second.iri,
                spread: unsigned ^ (unsigned >> 16),
                canonical_order,
            });
        }
        if rows
            .len()
            .checked_add(counts.object_property_chains)
            .ok_or_else(|| KernelError::resource("encoded role-row count overflow"))?
            != role_axiom_count
        {
            return Err(KernelError::malformed(
                "encoded role-axiom count changed after successful preflight",
            ));
        }
        let mut capacity = 16_usize;
        while role_axiom_count > capacity / 4 * 3 {
            capacity = capacity
                .checked_mul(2)
                .ok_or_else(|| KernelError::resource("encoded role-table capacity overflow"))?;
        }
        rows.sort_unstable_by_key(|row| {
            (
                (row.spread as usize) & (capacity - 1),
                row.spread,
                row.canonical_order,
            )
        });
        let mut role_state = RoleState::with_capacity(
            retained,
            counts
                .sub_object_properties
                .checked_sub(counts.object_property_chains)
                .ok_or_else(|| KernelError::malformed("encoded role counters are inconsistent"))?,
            counts.inverse_object_properties,
            maximum_iri,
        )?;
        for (index, row) in rows.into_iter().enumerate() {
            check_cancel(state, index)?;
            role_state.apply(row)?;
        }
        Ok(role_state)
    }

    fn aggregate_operand_range(self, node_id: usize) -> Result<(usize, usize), KernelError> {
        if !is_aggregate_tag(self.node_tag(node_id)?) {
            return Err(KernelError::malformed(
                "encoded aggregate cursor has the wrong constructor tag",
            ));
        }
        self.node_set_range(self.exact_fields(node_id, 1)?, 2)
    }

    fn equivalent_edge_counts(
        self,
        role_state: &RoleState<'a>,
        directions: usize,
        only_taxonomy: bool,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<EquivalentEdgeCounts, KernelError> {
        let mut counts = EquivalentEdgeCounts::default();
        for root_index in 0..self.root_count() {
            check_cancel(state, root_index)?;
            let node_id = self.root_id(root_index)?;
            if self.node_tag(node_id)? != TAG_EQUIVALENT_CLASSES {
                continue;
            }
            match self.equivalent_projection(node_id, maximum_iri)? {
                EquivalentProjection::Pair { .. } => {
                    counts.edges = counts.edges.checked_add(directions).ok_or_else(|| {
                        KernelError::resource("encoded equivalent edge-count overflow")
                    })?;
                }
                EquivalentProjection::Aggregate { expression_id, .. } => {
                    let (item_start, length) = self.aggregate_operand_range(expression_id)?;
                    for item_index in item_start..item_start + length {
                        check_cancel(state, item_index)?;
                        let operand_id = self.item_node(item_index)?;
                        match self.node_tag(operand_id)? {
                            TAG_ENTITY => {
                                self.named_class_iri(operand_id, maximum_iri)?;
                                counts.edges =
                                    counts.edges.checked_add(directions).ok_or_else(|| {
                                        KernelError::resource(
                                            "encoded aggregate taxonomy edge-count overflow",
                                        )
                                    })?;
                            }
                            tag if is_restriction_tag(tag) && !only_taxonomy => {
                                let Some((relation, _destination)) =
                                    self.restriction_projection(operand_id, maximum_iri)?
                                else {
                                    continue;
                                };
                                let expanded = role_state.edge_count(relation)?;
                                counts.edges =
                                    counts.edges.checked_add(expanded).ok_or_else(|| {
                                        KernelError::resource(
                                            "encoded aggregate restriction edge-count overflow",
                                        )
                                    })?;
                                counts.base_role_edges =
                                    counts.base_role_edges.checked_add(1).ok_or_else(|| {
                                        KernelError::resource(
                                            "encoded aggregate base-role count overflow",
                                        )
                                    })?;
                                counts.expanded_role_edges = counts
                                    .expanded_role_edges
                                    .checked_add(expanded)
                                    .ok_or_else(|| {
                                        KernelError::resource(
                                            "encoded aggregate expanded-role count overflow",
                                        )
                                    })?;
                            }
                            tag if is_restriction_tag(tag) => {}
                            tag if is_nonprojecting_class_tag(tag) || is_aggregate_tag(tag) => {}
                            _ => {
                                return Err(KernelError::malformed(
                                    "encoded aggregate operand changed after successful preflight",
                                ));
                            }
                        }
                    }
                }
                EquivalentProjection::Ignored => {}
            }
        }
        Ok(counts)
    }

    fn annotation_edge_counts(
        self,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<AnnotationEdgeCounts, KernelError> {
        let mut counts = AnnotationEdgeCounts::default();
        for root_index in 0..self.root_count() {
            check_cancel(state, root_index)?;
            let node_id = self.root_id(root_index)?;
            if self.node_tag(node_id)? != TAG_ANNOTATION_ASSERTION {
                continue;
            }
            let Some(projection) = self.annotation_projection(node_id, maximum_iri, state)? else {
                continue;
            };
            counts.edges = counts
                .edges
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded annotation edge-count overflow"))?;
            if matches!(projection.value, AnnotationValue::Typed { .. }) {
                counts.non_string_literals =
                    counts.non_string_literals.checked_add(1).ok_or_else(|| {
                        KernelError::resource(
                            "encoded non-string annotation-literal count overflow",
                        )
                    })?;
            }
        }
        Ok(counts)
    }

    fn next_named_aggregate_operand(
        self,
        expression_id: usize,
        after: Option<(&'a str, usize)>,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<Option<(&'a str, usize)>, KernelError> {
        let (item_start, length) = self.aggregate_operand_range(expression_id)?;
        let mut next: Option<(&str, usize)> = None;
        for item_index in item_start..item_start + length {
            check_cancel(state, item_index)?;
            let operand_id = self.item_node(item_index)?;
            if self.node_tag(operand_id)? != TAG_ENTITY {
                continue;
            }
            let iri = self.named_class_iri(operand_id, maximum_iri)?;
            let key = (iri.as_bytes(), operand_id);
            if after
                .is_some_and(|(previous, previous_id)| key <= (previous.as_bytes(), previous_id))
                || next.is_some_and(|(current, current_id)| key >= (current.as_bytes(), current_id))
            {
                continue;
            }
            next = Some((iri, operand_id));
        }
        Ok(next)
    }

    fn push_equivalent_projection(
        self,
        edges: &mut Vec<DirectEdge>,
        projection: EquivalentProjection<'a>,
        role_state: &RoleState<'a>,
        options: EquivalentEmitOptions,
        state: &AtomicU8,
    ) -> Result<(), KernelError> {
        match projection {
            EquivalentProjection::Pair {
                source,
                destination,
            } => push_taxonomy_edges(edges, source, destination, options.bidirectional),
            EquivalentProjection::Aggregate {
                source,
                expression_id,
            } => {
                let mut previous: Option<(&str, usize)> = None;
                while let Some((destination, node_id)) = self.next_named_aggregate_operand(
                    expression_id,
                    previous,
                    options.maximum_iri,
                    state,
                )? {
                    push_taxonomy_edges(edges, source, destination, options.bidirectional)?;
                    previous = Some((destination, node_id));
                }
                if options.only_taxonomy {
                    return Ok(());
                }
                let (item_start, length) = self.aggregate_operand_range(expression_id)?;
                for tag in [
                    TAG_OBJECT_SOME_VALUES_FROM,
                    TAG_OBJECT_ALL_VALUES_FROM,
                    TAG_OBJECT_MIN_CARDINALITY,
                    TAG_OBJECT_MAX_CARDINALITY,
                ] {
                    for item_index in item_start..item_start + length {
                        check_cancel(state, item_index)?;
                        let operand_id = self.item_node(item_index)?;
                        if self.node_tag(operand_id)? != tag {
                            continue;
                        }
                        let Some((relation, destination)) =
                            self.restriction_projection(operand_id, options.maximum_iri)?
                        else {
                            continue;
                        };
                        push_role_edges(edges, role_state, source, relation, destination)?;
                    }
                }
                Ok(())
            }
            EquivalentProjection::Ignored => Ok(()),
        }
    }

    fn restriction_role_edge_count(
        self,
        role_state: &RoleState<'a>,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<usize, KernelError> {
        let mut count = 0_usize;
        for index in 0..self.root_count() {
            check_cancel(state, index)?;
            let node_id = self.root_id(index)?;
            if self.node_tag(node_id)? != TAG_SUB_CLASS_OF {
                continue;
            }
            if let SubclassProjection::Restriction { relation, .. } =
                self.subclass_projection(node_id, maximum_iri)?
            {
                count = count
                    .checked_add(role_state.edge_count(relation)?)
                    .ok_or_else(|| {
                        KernelError::resource("encoded restriction edge-count overflow")
                    })?;
            }
        }
        Ok(count)
    }

    fn domain_range_edge_count(
        self,
        role_state: &RoleState<'a>,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<(usize, usize), KernelError> {
        let mut products = 0_usize;
        let mut edges = 0_usize;
        for domain_index in 0..self.root_count() {
            check_cancel(state, domain_index)?;
            let domain_id = self.root_id(domain_index)?;
            if self.node_tag(domain_id)? != TAG_OBJECT_PROPERTY_DOMAIN {
                continue;
            }
            let Some((domain_property, _domain)) = self.object_property_class_projection(
                domain_id,
                TAG_OBJECT_PROPERTY_DOMAIN,
                maximum_iri,
            )?
            else {
                continue;
            };
            for range_index in 0..self.root_count() {
                check_cancel(state, range_index)?;
                let range_id = self.root_id(range_index)?;
                if self.node_tag(range_id)? != TAG_OBJECT_PROPERTY_RANGE {
                    continue;
                }
                let Some((range_property, _range)) = self.object_property_class_projection(
                    range_id,
                    TAG_OBJECT_PROPERTY_RANGE,
                    maximum_iri,
                )?
                else {
                    continue;
                };
                if domain_property == range_property {
                    products = products.checked_add(1).ok_or_else(|| {
                        KernelError::resource("encoded domain/range edge-count overflow")
                    })?;
                    edges = edges
                        .checked_add(role_state.edge_count(domain_property)?)
                        .ok_or_else(|| {
                            KernelError::resource("encoded domain/range edge-count overflow")
                        })?;
                }
            }
        }
        Ok((products, edges))
    }

    fn next_paired_property(
        self,
        after: Option<&str>,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<Option<&'a str>, KernelError> {
        let mut next: Option<&str> = None;
        for domain_index in 0..self.root_count() {
            check_cancel(state, domain_index)?;
            let domain_id = self.root_id(domain_index)?;
            if self.node_tag(domain_id)? != TAG_OBJECT_PROPERTY_DOMAIN {
                continue;
            }
            let Some((property, _domain)) = self.object_property_class_projection(
                domain_id,
                TAG_OBJECT_PROPERTY_DOMAIN,
                maximum_iri,
            )?
            else {
                continue;
            };
            if after.is_some_and(|previous| property.as_bytes() <= previous.as_bytes())
                || next.is_some_and(|current| property.as_bytes() >= current.as_bytes())
                || !self.has_range_for_property(property, maximum_iri, state)?
            {
                continue;
            }
            next = Some(property);
        }
        Ok(next)
    }

    fn has_range_for_property(
        self,
        property: &str,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<bool, KernelError> {
        for range_index in 0..self.root_count() {
            check_cancel(state, range_index)?;
            let range_id = self.root_id(range_index)?;
            if self.node_tag(range_id)? != TAG_OBJECT_PROPERTY_RANGE {
                continue;
            }
            let Some((candidate, _range)) = self.object_property_class_projection(
                range_id,
                TAG_OBJECT_PROPERTY_RANGE,
                maximum_iri,
            )?
            else {
                continue;
            };
            if candidate == property {
                return Ok(true);
            }
        }
        Ok(false)
    }
}

pub(crate) fn compile_direct_with_options(
    columns: DirectColumns<'_>,
    options: DirectCompileOptions,
    state: &AtomicU8,
) -> Result<(Vec<DirectEdge>, DirectCompileStats), KernelError> {
    compile_direct_with_retained_role_state(columns, options, state, None)
}

pub(crate) fn compile_direct_with_retained_role_state(
    columns: DirectColumns<'_>,
    options: DirectCompileOptions,
    state: &AtomicU8,
    retained: Option<&mut OwnedRoleState>,
) -> Result<(Vec<DirectEdge>, DirectCompileStats), KernelError> {
    let DirectCompileOptions {
        bidirectional,
        asserted_taxonomy_only,
        only_taxonomy,
        include_literals,
        max_edges,
        max_iri_bytes,
    } = options;
    check_cancel(state, 0)?;
    columns.validate_generic(state)?;
    columns.validate_supported_nodes(max_iri_bytes, state)?;
    let counts = columns.classify_roots(max_iri_bytes, state)?;
    let anonymous_ids = columns.axiom_anonymous_ids(state)?;
    let buffer_bytes = columns.buffer_bytes()?;
    let role_state = if asserted_taxonomy_only {
        RoleState::default()
    } else {
        columns.build_role_state(counts, max_iri_bytes, state, retained.as_deref())?
    };
    let directions = 1_usize + usize::from(bidirectional);
    let direct_subclasses = counts
        .subclasses
        .checked_sub(counts.restriction_subclasses)
        .and_then(|count| count.checked_sub(counts.ignored_subclasses))
        .ok_or_else(|| KernelError::malformed("encoded subclass counters are inconsistent"))?;
    let direct_taxonomy_edges = direct_subclasses
        .checked_mul(directions)
        .ok_or_else(|| KernelError::resource("encoded edge-count overflow"))?;
    let equivalent_counts = if asserted_taxonomy_only {
        EquivalentEdgeCounts::default()
    } else {
        columns.equivalent_edge_counts(
            &role_state,
            directions,
            only_taxonomy,
            max_iri_bytes,
            state,
        )?
    };
    let subclass_restriction_edges = if asserted_taxonomy_only || only_taxonomy {
        0
    } else {
        columns.restriction_role_edge_count(&role_state, max_iri_bytes, state)?
    };
    let class_assertion_edges = if asserted_taxonomy_only {
        0
    } else {
        counts
            .class_assertions
            .checked_sub(counts.ignored_class_assertions)
            .ok_or_else(|| {
                KernelError::malformed("encoded class-assertion counters are inconsistent")
            })?
    };
    let object_assertion_edges = if asserted_taxonomy_only {
        0
    } else {
        counts.object_property_assertions
    };
    let annotation_counts = if asserted_taxonomy_only || !include_literals {
        AnnotationEdgeCounts::default()
    } else {
        columns.annotation_edge_counts(max_iri_bytes, state)?
    };
    let skipped_axioms = if asserted_taxonomy_only {
        0
    } else {
        counts.skipped_axioms()?
    };
    let (domain_range_edges, expanded_domain_range_edges) = if asserted_taxonomy_only {
        (0, 0)
    } else {
        columns.domain_range_edge_count(&role_state, max_iri_bytes, state)?
    };
    let base_role_edges = if asserted_taxonomy_only {
        0
    } else {
        domain_range_edges
            .checked_add(if only_taxonomy {
                0
            } else {
                counts.restriction_subclasses
            })
            .and_then(|count| count.checked_add(equivalent_counts.base_role_edges))
            .ok_or_else(|| KernelError::resource("encoded base role-edge count overflow"))?
    };
    let expanded_role_edges = subclass_restriction_edges
        .checked_add(expanded_domain_range_edges)
        .and_then(|count| count.checked_add(equivalent_counts.expanded_role_edges))
        .ok_or_else(|| KernelError::resource("encoded expanded role-edge count overflow"))?;
    let role_expansion_edges = expanded_role_edges
        .checked_sub(base_role_edges)
        .ok_or_else(|| {
            KernelError::malformed("encoded role-expansion counters are inconsistent")
        })?;
    let projected = direct_taxonomy_edges
        .checked_add(equivalent_counts.edges)
        .and_then(|total| total.checked_add(subclass_restriction_edges))
        .and_then(|total| total.checked_add(annotation_counts.edges))
        .and_then(|total| total.checked_add(class_assertion_edges))
        .and_then(|total| total.checked_add(object_assertion_edges))
        .and_then(|total| total.checked_add(expanded_domain_range_edges))
        .ok_or_else(|| KernelError::resource("encoded edge-count overflow"))?;
    if projected > max_edges {
        return Err(KernelError::resource(format!(
            "encoded direct batch requires {projected} edges; configured limit is {max_edges}",
        )));
    }

    // Output is the first allocation proportional to projected edges.  Every
    // buffer and supported constructor has already passed preflight.
    let mut edges = Vec::new();
    edges
        .try_reserve_exact(projected)
        .map_err(|_| KernelError::resource("encoded direct output allocation failed"))?;
    for index in 0..columns.root_count() {
        check_cancel(state, index)?;
        let node_id = columns.root_id(index)?;
        if columns.node_tag(node_id)? != TAG_SUB_CLASS_OF {
            continue;
        }
        match columns.subclass_projection(node_id, max_iri_bytes)? {
            SubclassProjection::Taxonomy {
                source,
                destination,
            } => {
                edges.push(DirectEdge {
                    source: clone_text(source)?,
                    relation: clone_text(SUBCLASS_OF)?,
                    destination: clone_text(destination)?,
                });
                if bidirectional {
                    edges.push(DirectEdge {
                        source: clone_text(destination)?,
                        relation: clone_text(SUPERCLASS_OF)?,
                        destination: clone_text(source)?,
                    });
                }
            }
            SubclassProjection::Restriction {
                source,
                relation,
                destination,
            } if !asserted_taxonomy_only && !only_taxonomy => {
                push_role_edges(&mut edges, &role_state, source, relation, destination)?;
            }
            SubclassProjection::Restriction { .. } => {}
            SubclassProjection::Ignored => {}
        }
    }

    // The reference compiler emits the class-axiom categories explicitly:
    // asserted subclasses, equivalents, selected annotations, then ABox class
    // assertions. Separate bounded scans preserve that order even for
    // hostile-but-monotone root IDs.
    if !asserted_taxonomy_only {
        for index in 0..columns.root_count() {
            check_cancel(state, index)?;
            let node_id = columns.root_id(index)?;
            if columns.node_tag(node_id)? != TAG_EQUIVALENT_CLASSES {
                continue;
            }
            let projection = columns.equivalent_projection(node_id, max_iri_bytes)?;
            columns.push_equivalent_projection(
                &mut edges,
                projection,
                &role_state,
                EquivalentEmitOptions {
                    bidirectional,
                    only_taxonomy,
                    maximum_iri: max_iri_bytes,
                },
                state,
            )?;
        }

        if include_literals {
            for index in 0..columns.root_count() {
                check_cancel(state, index)?;
                let node_id = columns.root_id(index)?;
                if columns.node_tag(node_id)? != TAG_ANNOTATION_ASSERTION {
                    continue;
                }
                if let Some(projection) =
                    columns.annotation_projection(node_id, max_iri_bytes, state)?
                {
                    push_annotation_edge(&mut edges, projection, &anonymous_ids)?;
                }
            }
        }

        for index in 0..columns.root_count() {
            check_cancel(state, index)?;
            let node_id = columns.root_id(index)?;
            if columns.node_tag(node_id)? != TAG_CLASS_ASSERTION {
                continue;
            }
            if let ClassAssertionProjection::Edge { individual, class } =
                columns.class_assertion_projection(node_id, max_iri_bytes)?
            {
                edges.push(DirectEdge {
                    source: clone_text(individual)?,
                    relation: clone_text(RDF_TYPE)?,
                    destination: clone_text(class)?,
                });
            }
        }

        for index in 0..columns.root_count() {
            check_cancel(state, index)?;
            let node_id = columns.root_id(index)?;
            if columns.node_tag(node_id)? != TAG_OBJECT_PROPERTY_ASSERTION {
                continue;
            }
            let (source, relation, destination) =
                columns.object_property_assertion_parts(node_id, max_iri_bytes)?;
            edges.push(DirectEdge {
                source: render_individual(source, &anonymous_ids)?,
                relation: clone_text(relation)?,
                destination: render_individual(destination, &anonymous_ids)?,
            });
        }

        let mut previous_property: Option<&str> = None;
        while let Some(property) =
            columns.next_paired_property(previous_property, max_iri_bytes, state)?
        {
            for domain_index in 0..columns.root_count() {
                check_cancel(state, domain_index)?;
                let domain_id = columns.root_id(domain_index)?;
                if columns.node_tag(domain_id)? != TAG_OBJECT_PROPERTY_DOMAIN {
                    continue;
                }
                let Some((domain_property, domain)) = columns.object_property_class_projection(
                    domain_id,
                    TAG_OBJECT_PROPERTY_DOMAIN,
                    max_iri_bytes,
                )?
                else {
                    continue;
                };
                if domain_property != property {
                    continue;
                }
                for range_index in 0..columns.root_count() {
                    check_cancel(state, range_index)?;
                    let range_id = columns.root_id(range_index)?;
                    if columns.node_tag(range_id)? != TAG_OBJECT_PROPERTY_RANGE {
                        continue;
                    }
                    let Some((range_property, range)) = columns.object_property_class_projection(
                        range_id,
                        TAG_OBJECT_PROPERTY_RANGE,
                        max_iri_bytes,
                    )?
                    else {
                        continue;
                    };
                    if range_property == property {
                        push_role_edges(&mut edges, &role_state, domain, property, range)?;
                    }
                }
            }
            previous_property = Some(property);
        }
    }
    check_cancel(state, columns.root_count())?;
    if edges.len() != projected {
        return Err(KernelError::malformed(
            "encoded direct output count changed after successful preflight",
        ));
    }
    let stats = DirectCompileStats {
        roots: columns.root_count(),
        nodes: columns.node_count(),
        anonymous_individuals: anonymous_ids.node_ids.len(),
        ontology_annotations: counts.ontology_annotations,
        swrl_rules: counts.swrl_rules,
        declarations: counts.declarations,
        subclasses: counts.subclasses,
        restriction_subclasses: counts.restriction_subclasses,
        ignored_subclasses: counts.ignored_subclasses,
        equivalents: counts.equivalents,
        aggregate_equivalents: counts.aggregate_equivalents,
        disjoint_classes: counts.disjoint_classes,
        disjoint_unions: counts.disjoint_unions,
        has_keys: counts.has_keys,
        same_individuals: counts.same_individuals,
        different_individuals: counts.different_individuals,
        class_assertions: counts.class_assertions,
        ignored_class_assertions: counts.ignored_class_assertions,
        object_property_assertions: counts.object_property_assertions,
        negative_object_property_assertions: counts.negative_object_property_assertions,
        sub_object_properties: counts.sub_object_properties,
        object_property_chains: counts.object_property_chains,
        equivalent_object_properties: counts.equivalent_object_properties,
        disjoint_object_properties: counts.disjoint_object_properties,
        inverse_object_properties: counts.inverse_object_properties,
        functional_object_properties: counts.functional_object_properties,
        inverse_functional_object_properties: counts.inverse_functional_object_properties,
        reflexive_object_properties: counts.reflexive_object_properties,
        irreflexive_object_properties: counts.irreflexive_object_properties,
        symmetric_object_properties: counts.symmetric_object_properties,
        asymmetric_object_properties: counts.asymmetric_object_properties,
        transitive_object_properties: counts.transitive_object_properties,
        sub_data_properties: counts.sub_data_properties,
        equivalent_data_properties: counts.equivalent_data_properties,
        disjoint_data_properties: counts.disjoint_data_properties,
        data_property_domains: counts.data_property_domains,
        data_property_ranges: counts.data_property_ranges,
        functional_data_properties: counts.functional_data_properties,
        datatype_definitions: counts.datatype_definitions,
        data_property_assertions: counts.data_property_assertions,
        negative_data_property_assertions: counts.negative_data_property_assertions,
        annotation_assertions: counts.annotation_assertions,
        sub_annotation_properties: counts.sub_annotation_properties,
        annotation_property_domains: counts.annotation_property_domains,
        annotation_property_ranges: counts.annotation_property_ranges,
        annotation_edges: annotation_counts.edges,
        non_string_literal_renderings: annotation_counts.non_string_literals,
        skipped_axioms,
        object_property_domains: counts.object_property_domains,
        object_property_ranges: counts.object_property_ranges,
        domain_range_edges,
        role_expansion_edges,
        edges: edges.len(),
        buffer_bytes,
    };
    // Retained Scala-instance compatibility state is a transaction outcome,
    // not preflight state.  Commit only after every validation, capacity,
    // cancellation, and output-count check has succeeded so a failed call
    // cannot influence a later independent view.
    if !asserted_taxonomy_only {
        if let Some(retained) = retained {
            *retained = role_state.to_owned()?;
        }
    }
    Ok((edges, stats))
}

#[cfg(test)]
fn compile_direct(
    columns: DirectColumns<'_>,
    bidirectional: bool,
    asserted_taxonomy_only: bool,
    only_taxonomy: bool,
    max_edges: usize,
    max_iri_bytes: usize,
    state: &AtomicU8,
) -> Result<(Vec<DirectEdge>, DirectCompileStats), KernelError> {
    compile_direct_with_options(
        columns,
        DirectCompileOptions {
            bidirectional,
            asserted_taxonomy_only,
            only_taxonomy,
            include_literals: false,
            max_edges,
            max_iri_bytes,
        },
        state,
    )
}

fn is_restriction_tag(tag: u16) -> bool {
    [
        TAG_OBJECT_SOME_VALUES_FROM,
        TAG_OBJECT_ALL_VALUES_FROM,
        TAG_OBJECT_MIN_CARDINALITY,
        TAG_OBJECT_MAX_CARDINALITY,
    ]
    .contains(&tag)
}

fn is_nonprojecting_class_tag(tag: u16) -> bool {
    [
        TAG_OBJECT_COMPLEMENT_OF,
        TAG_OBJECT_ONE_OF,
        TAG_OBJECT_HAS_VALUE,
        TAG_OBJECT_HAS_SELF,
        TAG_OBJECT_EXACT_CARDINALITY,
        TAG_DATA_SOME_VALUES_FROM,
        TAG_DATA_ALL_VALUES_FROM,
        TAG_DATA_HAS_VALUE,
        TAG_DATA_MIN_CARDINALITY,
        TAG_DATA_MAX_CARDINALITY,
        TAG_DATA_EXACT_CARDINALITY,
    ]
    .contains(&tag)
}

fn is_class_expression_tag(tag: u16) -> bool {
    is_aggregate_tag(tag) || is_restriction_tag(tag) || is_nonprojecting_class_tag(tag)
}

fn is_recursive_class_expression_tag(tag: u16) -> bool {
    is_aggregate_tag(tag)
        || is_restriction_tag(tag)
        || [TAG_OBJECT_COMPLEMENT_OF, TAG_OBJECT_EXACT_CARDINALITY].contains(&tag)
}

fn is_data_class_expression_tag(tag: u16) -> bool {
    [
        TAG_DATA_SOME_VALUES_FROM,
        TAG_DATA_ALL_VALUES_FROM,
        TAG_DATA_HAS_VALUE,
        TAG_DATA_MIN_CARDINALITY,
        TAG_DATA_MAX_CARDINALITY,
        TAG_DATA_EXACT_CARDINALITY,
    ]
    .contains(&tag)
}

fn is_data_range_tag(tag: u16) -> bool {
    [
        TAG_DATA_INTERSECTION_OF,
        TAG_DATA_UNION_OF,
        TAG_DATA_COMPLEMENT_OF,
        TAG_DATA_ONE_OF,
        TAG_DATATYPE_RESTRICTION,
    ]
    .contains(&tag)
}

fn is_recursive_data_range_tag(tag: u16) -> bool {
    [
        TAG_DATA_INTERSECTION_OF,
        TAG_DATA_UNION_OF,
        TAG_DATA_COMPLEMENT_OF,
    ]
    .contains(&tag)
}

fn is_aggregate_tag(tag: u16) -> bool {
    [TAG_OBJECT_INTERSECTION_OF, TAG_OBJECT_UNION_OF].contains(&tag)
}

fn is_object_property_characteristic(tag: u16) -> bool {
    [
        TAG_FUNCTIONAL_OBJECT_PROPERTY,
        TAG_INVERSE_FUNCTIONAL_OBJECT_PROPERTY,
        TAG_REFLEXIVE_OBJECT_PROPERTY,
        TAG_IRREFLEXIVE_OBJECT_PROPERTY,
        TAG_SYMMETRIC_OBJECT_PROPERTY,
        TAG_ASYMMETRIC_OBJECT_PROPERTY,
        TAG_TRANSITIVE_OBJECT_PROPERTY,
    ]
    .contains(&tag)
}

fn is_swrl_atom_tag(tag: u16) -> bool {
    [
        TAG_CLASS_ATOM,
        TAG_DATA_RANGE_ATOM,
        TAG_OBJECT_PROPERTY_ATOM,
        TAG_DATA_PROPERTY_ATOM,
        TAG_BUILT_IN_ATOM,
        TAG_SAME_INDIVIDUAL_ATOM,
        TAG_DIFFERENT_INDIVIDUALS_ATOM,
    ]
    .contains(&tag)
}

fn java_string_hash(value: &str) -> i32 {
    value.encode_utf16().fold(0_i32, |result, unit| {
        result.wrapping_mul(31).wrapping_add(i32::from(unit))
    })
}

fn owlapi_iri_hash(value: &str) -> i32 {
    let split_at = value
        .char_indices()
        .rev()
        .find_map(|(index, character)| {
            ['#', '/', ':']
                .contains(&character)
                .then_some(index + character.len_utf8())
        })
        .unwrap_or(0);
    let (namespace, remainder) = value.split_at(split_at);
    java_string_hash(namespace).wrapping_add(java_string_hash(remainder))
}

fn combine_hash(seed: i32, components: &[i32]) -> i32 {
    components.iter().fold(seed, |result, component| {
        result.wrapping_mul(31).wrapping_add(*component)
    })
}

fn compare_canonical_varints(left: usize, right: usize) -> std::cmp::Ordering {
    fn encode(mut value: usize) -> ([u8; 10], usize) {
        let mut output = [0_u8; 10];
        let mut length = 0_usize;
        loop {
            let byte = (value & 0x7f) as u8;
            value >>= 7;
            output[length] = byte | if value == 0 { 0 } else { 0x80 };
            length += 1;
            if value == 0 {
                return (output, length);
            }
        }
    }

    let (left_bytes, left_length) = encode(left);
    let (right_bytes, right_length) = encode(right);
    left_bytes[..left_length].cmp(&right_bytes[..right_length])
}

fn push_role_edges(
    edges: &mut Vec<DirectEdge>,
    role_state: &RoleState<'_>,
    source: &str,
    relation: &str,
    destination: &str,
) -> Result<(), KernelError> {
    edges.push(DirectEdge {
        source: clone_text(source)?,
        relation: clone_text(relation)?,
        destination: clone_text(destination)?,
    });
    for subrole in role_state.subroles_for(relation) {
        edges.push(DirectEdge {
            source: clone_text(source)?,
            relation: clone_text(subrole.as_ref())?,
            destination: clone_text(destination)?,
        });
    }
    if let Some(inverse) = role_state.inverse_for(relation) {
        edges.push(DirectEdge {
            source: clone_text(destination)?,
            relation: clone_text(inverse)?,
            destination: clone_text(source)?,
        });
    }
    Ok(())
}

fn push_taxonomy_edges(
    edges: &mut Vec<DirectEdge>,
    source: &str,
    destination: &str,
    bidirectional: bool,
) -> Result<(), KernelError> {
    edges.push(DirectEdge {
        source: clone_text(source)?,
        relation: clone_text(SUBCLASS_OF)?,
        destination: clone_text(destination)?,
    });
    if bidirectional {
        edges.push(DirectEdge {
            source: clone_text(destination)?,
            relation: clone_text(SUPERCLASS_OF)?,
            destination: clone_text(source)?,
        });
    }
    Ok(())
}

fn clone_text(value: &str) -> Result<String, KernelError> {
    let mut output = String::new();
    output
        .try_reserve_exact(value.len())
        .map_err(|_| KernelError::resource("encoded edge-string allocation failed"))?;
    output.push_str(value);
    Ok(output)
}

fn clone_retained_role_iri(value: &str, maximum: usize) -> Result<String, KernelError> {
    if value.len() > maximum {
        return Err(KernelError::resource(format!(
            "encoded retained role IRI contains {} bytes; limit is {maximum}",
            value.len()
        )));
    }
    clone_text(value)
}

fn render_individual(
    value: IndividualValue<'_>,
    anonymous_ids: &AnonymousIds,
) -> Result<String, KernelError> {
    match value {
        IndividualValue::Named(iri) => clone_text(iri),
        IndividualValue::Anonymous(node_id) => anonymous_ids.render(node_id),
    }
}

fn render_typed_annotation_literal(lexical: &str, datatype: &str) -> Result<String, KernelError> {
    let datatype_capacity = datatype
        .len()
        .checked_add(5)
        .ok_or_else(|| KernelError::resource("encoded annotation rendering size overflow"))?;
    let capacity = lexical
        .len()
        .checked_add(3)
        .and_then(|size| size.checked_add(datatype_capacity))
        .ok_or_else(|| KernelError::resource("encoded annotation rendering size overflow"))?;
    let mut output = String::new();
    output
        .try_reserve_exact(capacity)
        .map_err(|_| KernelError::resource("encoded annotation rendering allocation failed"))?;

    // This is intentionally the pinned mOWL defect: OWLAPI escaping is
    // immediately followed by removal of every backslash.  The surrounding
    // render then loses its first quote and final datatype character.
    for character in lexical.chars() {
        match character {
            '\\' => {}
            '\n' => output.push('n'),
            '\r' => output.push('r'),
            '\t' => output.push('t'),
            _ => output.push(character),
        }
    }
    output.push_str("\"^^");
    if let Some(suffix) = datatype.strip_prefix(XSD_NAMESPACE) {
        output.push_str("xsd:");
        output.push_str(suffix);
        let _ = output.pop();
    } else {
        output.push('<');
        output.push_str(datatype);
    }
    Ok(output)
}

fn push_annotation_edge(
    edges: &mut Vec<DirectEdge>,
    projection: AnnotationProjection<'_>,
    anonymous_ids: &AnonymousIds,
) -> Result<(), KernelError> {
    let destination = match projection.value {
        AnnotationValue::Borrowed(value) => clone_text(value)?,
        AnnotationValue::Anonymous(node_id) => anonymous_ids.render(node_id)?,
        AnnotationValue::Typed { lexical, datatype } => {
            render_typed_annotation_literal(lexical, datatype)?
        }
    };
    edges.push(DirectEdge {
        source: clone_text(projection.source)?,
        relation: clone_text(projection.relation)?,
        destination,
    });
    Ok(())
}

fn queue_reachable_node(
    stack: &mut Vec<usize>,
    reachable: &mut [bool],
    node_id: usize,
) -> Result<(), KernelError> {
    if !reachable[node_id] {
        stack
            .try_reserve(1)
            .map_err(|_| KernelError::resource("encoded reachability stack allocation failed"))?;
        reachable[node_id] = true;
        stack.push(node_id);
    }
    Ok(())
}

fn queue_data_range_event(
    stack: &mut Vec<(usize, bool)>,
    node_id: usize,
    exiting: bool,
) -> Result<(), KernelError> {
    stack
        .try_reserve(1)
        .map_err(|_| KernelError::resource("encoded data-range stack allocation failed"))?;
    stack.push((node_id, exiting));
    Ok(())
}

fn queue_recursive_class_event(
    stack: &mut Vec<(usize, bool)>,
    node_id: usize,
    exiting: bool,
) -> Result<(), KernelError> {
    stack.try_reserve(1).map_err(|_| {
        KernelError::resource("encoded recursive class-expression stack allocation failed")
    })?;
    stack.push((node_id, exiting));
    Ok(())
}

fn check_cancel(state: &AtomicU8, index: usize) -> Result<(), KernelError> {
    if index % 256 == 0 && state.load(Ordering::Acquire) == STATE_CANCELLED {
        Err(KernelError::Cancelled)
    } else {
        Ok(())
    }
}

fn read_u16(buffer: &[u8], index: usize, name: &str) -> Result<u16, KernelError> {
    let start = index
        .checked_mul(2)
        .ok_or_else(|| KernelError::malformed(format!("encoded {name} index overflow")))?;
    let bytes: [u8; 2] = buffer
        .get(start..start + 2)
        .ok_or_else(|| KernelError::malformed(format!("encoded {name} index is out of range")))?
        .try_into()
        .map_err(|_| KernelError::malformed(format!("encoded {name} row width is invalid")))?;
    Ok(u16::from_le_bytes(bytes))
}

fn read_u32(buffer: &[u8], index: usize, name: &str) -> Result<u32, KernelError> {
    let start = index
        .checked_mul(4)
        .ok_or_else(|| KernelError::malformed(format!("encoded {name} index overflow")))?;
    let bytes: [u8; 4] = buffer
        .get(start..start + 4)
        .ok_or_else(|| KernelError::malformed(format!("encoded {name} index is out of range")))?
        .try_into()
        .map_err(|_| KernelError::malformed(format!("encoded {name} row width is invalid")))?;
    Ok(u32::from_le_bytes(bytes))
}

fn read_usize(buffer: &[u8], index: usize, name: &str) -> Result<usize, KernelError> {
    let start = index
        .checked_mul(8)
        .ok_or_else(|| KernelError::malformed(format!("encoded {name} index overflow")))?;
    let bytes: [u8; 8] = buffer
        .get(start..start + 8)
        .ok_or_else(|| KernelError::malformed(format!("encoded {name} index is out of range")))?
        .try_into()
        .map_err(|_| KernelError::malformed(format!("encoded {name} row width is invalid")))?;
    usize::try_from(u64::from_le_bytes(bytes))
        .map_err(|_| KernelError::malformed(format!("encoded {name} value does not fit usize")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Default)]
    struct Fixture {
        root_kinds: Vec<u8>,
        root_ids: Vec<u8>,
        node_tags: Vec<u8>,
        node_field_offsets: Vec<u8>,
        field_kinds: Vec<u8>,
        field_values: Vec<u8>,
        field_lengths: Vec<u8>,
        item_kinds: Vec<u8>,
        item_values: Vec<u8>,
        item_lengths: Vec<u8>,
        scalar_bytes: Vec<u8>,
    }

    impl Fixture {
        fn push_scalar(&mut self, kind: u8, value: &[u8]) {
            self.field_kinds.push(kind);
            self.field_values
                .extend_from_slice(&(self.scalar_bytes.len() as u64).to_le_bytes());
            self.field_lengths
                .extend_from_slice(&(value.len() as u64).to_le_bytes());
            self.scalar_bytes.extend_from_slice(value);
        }

        fn push_node_ref(&mut self, node_id: u64) {
            self.field_kinds.push(COMPONENT_NODE);
            self.field_values.extend_from_slice(&node_id.to_le_bytes());
            self.field_lengths.extend_from_slice(&0_u64.to_le_bytes());
        }

        fn push_none(&mut self) {
            self.field_kinds.push(COMPONENT_NONE);
            self.field_values.extend_from_slice(&0_u64.to_le_bytes());
            self.field_lengths.extend_from_slice(&0_u64.to_le_bytes());
        }

        fn push_empty_set(&mut self) {
            self.field_kinds.push(COMPONENT_SET);
            self.field_values
                .extend_from_slice(&(self.item_kinds.len() as u64).to_le_bytes());
            self.field_lengths.extend_from_slice(&0_u64.to_le_bytes());
        }

        fn push_node_set(&mut self, node_ids: &[u64]) {
            self.field_kinds.push(COMPONENT_SET);
            self.field_values
                .extend_from_slice(&(self.item_kinds.len() as u64).to_le_bytes());
            self.field_lengths
                .extend_from_slice(&(node_ids.len() as u64).to_le_bytes());
            for node_id in node_ids {
                self.item_kinds.push(COMPONENT_NODE);
                self.item_values.extend_from_slice(&node_id.to_le_bytes());
                self.item_lengths.extend_from_slice(&0_u64.to_le_bytes());
            }
        }

        fn push_node_sequence(&mut self, node_ids: &[u64]) {
            self.field_kinds.push(COMPONENT_SEQUENCE);
            self.field_values
                .extend_from_slice(&(self.item_kinds.len() as u64).to_le_bytes());
            self.field_lengths
                .extend_from_slice(&(node_ids.len() as u64).to_le_bytes());
            for node_id in node_ids {
                self.item_kinds.push(COMPONENT_NODE);
                self.item_values.extend_from_slice(&node_id.to_le_bytes());
                self.item_lengths.extend_from_slice(&0_u64.to_le_bytes());
            }
        }

        fn finish_node(&mut self, tag: u16) {
            if self.node_field_offsets.is_empty() {
                self.node_field_offsets
                    .extend_from_slice(&0_u64.to_le_bytes());
            }
            self.node_tags.extend_from_slice(&tag.to_le_bytes());
            self.node_field_offsets
                .extend_from_slice(&(self.field_kinds.len() as u64).to_le_bytes());
        }

        fn columns(&self) -> DirectColumns<'_> {
            DirectColumns::from_ordered([
                &self.root_kinds,
                &self.root_ids,
                &self.node_tags,
                &self.node_field_offsets,
                &self.field_kinds,
                &self.field_values,
                &self.field_lengths,
                &self.item_kinds,
                &self.item_values,
                &self.item_lengths,
                &self.scalar_bytes,
            ])
        }
    }

    fn named_subclass_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        fixture.push_scalar(COMPONENT_TEXT, b"urn:A");
        fixture.finish_node(TAG_IRI); // 1
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 2
        fixture.push_scalar(COMPONENT_TEXT, b"urn:B");
        fixture.finish_node(TAG_IRI); // 3
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY); // 4
        fixture.push_node_ref(2);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DECLARATION); // 5
        fixture.push_node_ref(2);
        fixture.push_node_ref(4);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 6
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        fixture.root_ids.extend_from_slice(&5_u32.to_le_bytes());
        fixture.root_ids.extend_from_slice(&6_u32.to_le_bytes());
        fixture
    }

    fn named_class_axiom_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [b"urn:A".as_slice(), b"urn:B", b"urn:Z", b"urn:i", b"urn:AA"] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=5
        }
        for iri_id in [1_u64, 2, 3, 5] {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 6..=9
        }
        fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
        fixture.push_node_ref(4);
        fixture.finish_node(TAG_ENTITY); // 10

        fixture.push_node_ref(6);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DECLARATION); // 11
        fixture.push_node_ref(8);
        fixture.push_node_ref(6);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 12
        fixture.push_node_set(&[7, 8, 9]);
        fixture.push_empty_set();
        fixture.finish_node(TAG_EQUIVALENT_CLASSES); // 13
        fixture.push_node_ref(6);
        fixture.push_node_ref(10);
        fixture.push_empty_set();
        fixture.finish_node(TAG_CLASS_ASSERTION); // 14

        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 4]);
        for root_id in [11_u32, 12, 13, 14] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn named_role_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"urn:B",
            b"urn:C",
            b"urn:D",
            b"urn:D1",
            b"urn:D2",
            b"urn:QD",
            b"urn:QR",
            b"urn:R1",
            b"urn:R2",
            b"urn:p",
            b"urn:q",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=12
        }
        for iri_id in 1_u64..=10 {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 13..=22
        }
        for iri_id in [11_u64, 12] {
            fixture.push_scalar(COMPONENT_ENUM, b"object_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 23..=24
        }

        fixture.push_node_ref(23);
        fixture.push_node_ref(14);
        fixture.finish_node(TAG_OBJECT_SOME_VALUES_FROM); // 25
        fixture.push_node_ref(23);
        fixture.push_node_ref(15);
        fixture.finish_node(TAG_OBJECT_ALL_VALUES_FROM); // 26
        fixture.push_scalar(COMPONENT_INTEGER, &[0, 1]);
        fixture.push_node_ref(23);
        fixture.push_node_ref(14);
        fixture.finish_node(TAG_OBJECT_MIN_CARDINALITY); // 27
        fixture.push_scalar(COMPONENT_INTEGER, &[3]);
        fixture.push_node_ref(23);
        fixture.push_node_ref(15);
        fixture.finish_node(TAG_OBJECT_MAX_CARDINALITY); // 28

        for (sub, sup) in [(13_u64, 16_u64), (13, 25), (13, 27), (26, 16), (28, 16)] {
            fixture.push_node_ref(sub);
            fixture.push_node_ref(sup);
            fixture.push_empty_set();
            fixture.finish_node(TAG_SUB_CLASS_OF); // 29..=33
        }
        for (property, class) in [(23_u64, 17_u64), (23, 18), (24, 19)] {
            fixture.push_node_ref(property);
            fixture.push_node_ref(class);
            fixture.push_empty_set();
            fixture.finish_node(TAG_OBJECT_PROPERTY_DOMAIN); // 34..=36
        }
        for (property, class) in [(23_u64, 21_u64), (23, 22), (24, 20)] {
            fixture.push_node_ref(property);
            fixture.push_node_ref(class);
            fixture.push_empty_set();
            fixture.finish_node(TAG_OBJECT_PROPERTY_RANGE); // 37..=39
        }

        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 11]);
        for root_id in 29_u32..=39 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn named_object_assertion_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [b"urn:i".as_slice(), b"urn:j", b"urn:p"] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=3
        }
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY); // 4
        for iri_id in [1_u64, 2] {
            fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 5..=6
        }
        fixture.push_node_ref(4);
        fixture.finish_node(TAG_OBJECT_INVERSE_OF); // 7

        for (tag, property, source, destination) in [
            (TAG_OBJECT_PROPERTY_ASSERTION, 4_u64, 5_u64, 6_u64),
            (TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION, 4, 6, 5),
            (TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION, 7, 5, 6),
        ] {
            fixture.push_node_ref(property);
            fixture.push_node_ref(source);
            fixture.push_node_ref(destination);
            fixture.push_empty_set();
            fixture.finish_node(tag); // 8..=10
        }
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 3]);
        for root_id in 8_u32..=10 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn named_role_axiom_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"urn:B",
            b"urn:D",
            b"urn:R",
            b"urn:p",
            b"urn:child",
            b"urn:pinv",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=7
        }
        for iri_id in 1_u64..=4 {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 8..=11
        }
        for iri_id in 5_u64..=7 {
            fixture.push_scalar(COMPONENT_ENUM, b"object_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 12..=14
        }
        fixture.push_node_ref(12);
        fixture.finish_node(TAG_OBJECT_INVERSE_OF); // 15
        fixture.push_node_ref(15);
        fixture.push_node_ref(9);
        fixture.finish_node(TAG_OBJECT_SOME_VALUES_FROM); // 16
        fixture.push_node_ref(8);
        fixture.push_node_ref(16);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 17
        for (tag, first, second) in [
            (TAG_SUB_OBJECT_PROPERTY_OF, 13_u64, 12_u64),
            (TAG_INVERSE_OBJECT_PROPERTIES, 12, 14),
        ] {
            fixture.push_node_ref(first);
            fixture.push_node_ref(second);
            fixture.push_empty_set();
            fixture.finish_node(tag); // 18..=19
        }
        for (tag, class) in [
            (TAG_OBJECT_PROPERTY_DOMAIN, 10_u64),
            (TAG_OBJECT_PROPERTY_RANGE, 11),
        ] {
            fixture.push_node_ref(12);
            fixture.push_node_ref(class);
            fixture.push_empty_set();
            fixture.finish_node(tag); // 20..=21
        }
        for (tag, properties) in [
            (TAG_EQUIVALENT_OBJECT_PROPERTIES, [12_u64, 15_u64]),
            (TAG_DISJOINT_OBJECT_PROPERTIES, [13_u64, 14_u64]),
        ] {
            fixture.push_node_set(&properties);
            fixture.push_empty_set();
            fixture.finish_node(tag); // 22..=23
        }
        for tag in [
            TAG_FUNCTIONAL_OBJECT_PROPERTY,
            TAG_INVERSE_FUNCTIONAL_OBJECT_PROPERTY,
            TAG_REFLEXIVE_OBJECT_PROPERTY,
            TAG_IRREFLEXIVE_OBJECT_PROPERTY,
            TAG_SYMMETRIC_OBJECT_PROPERTY,
            TAG_ASYMMETRIC_OBJECT_PROPERTY,
            TAG_TRANSITIVE_OBJECT_PROPERTY,
        ] {
            fixture.push_node_ref(if tag % 2 == 0 { 12 } else { 15 });
            fixture.push_empty_set();
            fixture.finish_node(tag); // 24..=30
        }
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 14]);
        for root_id in 17_u32..=30 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn named_aggregate_role_fixture() -> Fixture {
        let mut fixture = named_role_axiom_fixture();
        fixture.push_node_set(&[9, 16]);
        fixture.finish_node(TAG_OBJECT_INTERSECTION_OF); // 31
        fixture.push_node_set(&[8, 31]);
        fixture.push_empty_set();
        fixture.finish_node(TAG_EQUIVALENT_CLASSES); // 32
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture.root_ids.extend_from_slice(&32_u32.to_le_bytes());
        fixture
    }

    fn inverse_restriction_and_ignored_domain_fixture() -> Fixture {
        let mut fixture = named_role_axiom_fixture();
        fixture.push_node_set(&[8, 9]);
        fixture.finish_node(TAG_OBJECT_INTERSECTION_OF); // 31
        fixture.push_node_ref(15);
        fixture.push_node_ref(10);
        fixture.push_empty_set();
        fixture.finish_node(TAG_OBJECT_PROPERTY_DOMAIN); // 32
        fixture.push_node_ref(12);
        fixture.push_node_ref(31);
        fixture.push_empty_set();
        fixture.finish_node(TAG_OBJECT_PROPERTY_RANGE); // 33
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        for root_id in [32_u32, 33] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn object_property_chain_fixture() -> Fixture {
        let mut fixture = named_role_axiom_fixture();
        fixture.push_node_sequence(&[15, 13]);
        fixture.finish_node(TAG_OBJECT_PROPERTY_CHAIN); // 31
        fixture.push_node_ref(31);
        fixture.push_node_ref(12);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_OBJECT_PROPERTY_OF); // 32
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture.root_ids.extend_from_slice(&32_u32.to_le_bytes());
        fixture
    }

    fn nonprojecting_class_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"urn:B",
            b"urn:member",
            b"urn:i",
            b"urn:p",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=5
        }
        for iri_id in [1_u64, 2] {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 6..=7
        }
        for iri_id in [3_u64, 4] {
            fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 8..=9
        }
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 10
        fixture.push_scalar(COMPONENT_BYTES, &[3; 32]);
        fixture.push_scalar(COMPONENT_BYTES, b"anonymous");
        fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL); // 11
        fixture.push_node_ref(10);
        fixture.finish_node(TAG_OBJECT_INVERSE_OF); // 12
        fixture.push_node_set(&[8, 11]);
        fixture.finish_node(TAG_OBJECT_ONE_OF); // 13
        fixture.push_node_ref(12);
        fixture.push_node_ref(11);
        fixture.finish_node(TAG_OBJECT_HAS_VALUE); // 14
        fixture.push_node_ref(12);
        fixture.finish_node(TAG_OBJECT_HAS_SELF); // 15

        for (sub, sup) in [(6_u64, 13_u64), (14, 7), (6, 15)] {
            fixture.push_node_ref(sub);
            fixture.push_node_ref(sup);
            fixture.push_empty_set();
            fixture.finish_node(TAG_SUB_CLASS_OF); // 16..=18
        }
        for (class, individual) in [(13_u64, 9_u64), (6, 11), (6, 9)] {
            fixture.push_node_ref(class);
            fixture.push_node_ref(individual);
            fixture.push_empty_set();
            fixture.finish_node(TAG_CLASS_ASSERTION); // 19..=21
        }
        fixture.push_node_ref(6);
        fixture.push_node_ref(7);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 22
        fixture.push_scalar(COMPONENT_INTEGER, &[2]);
        fixture.push_node_ref(12);
        fixture.push_node_ref(7);
        fixture.finish_node(TAG_OBJECT_EXACT_CARDINALITY); // 23
        fixture.push_node_ref(23);
        fixture.finish_node(TAG_OBJECT_COMPLEMENT_OF); // 24
        fixture.push_node_ref(6);
        fixture.push_node_ref(23);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 25
        fixture.push_node_ref(24);
        fixture.push_node_ref(9);
        fixture.push_empty_set();
        fixture.finish_node(TAG_CLASS_ASSERTION); // 26
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 9]);
        for root_id in 16_u32..=22 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        for root_id in [25_u32, 26] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn named_disjoint_class_fixture() -> Fixture {
        let mut fixture = named_aggregate_role_fixture();
        fixture.push_node_set(&[8, 9, 31]);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DISJOINT_CLASSES); // 33
        fixture.push_node_ref(10);
        fixture.push_node_set(&[9, 31]);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DISJOINT_UNION); // 34
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        for root_id in [33_u32, 34] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn named_data_property_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"urn:dp",
            b"urn:dq",
            b"urn:dr",
            b"urn:i",
            RDF_PLAIN_LITERAL.as_bytes(),
            b"http://www.w3.org/2001/XMLSchema#string",
            b"urn:custom",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=8
        }
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 9
        for iri_id in [2_u64, 3, 4] {
            fixture.push_scalar(COMPONENT_ENUM, b"data_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 10..=12
        }
        fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 13
        for iri_id in [6_u64, 7, 8] {
            fixture.push_scalar(COMPONENT_ENUM, b"datatype");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 14..=16
        }
        fixture.push_scalar(COMPONENT_TEXT, b"plain");
        fixture.push_node_ref(14);
        fixture.push_none();
        fixture.finish_node(TAG_LITERAL); // 17
        fixture.push_scalar(COMPONENT_TEXT, b"bonjour");
        fixture.push_node_ref(14);
        fixture.push_scalar(COMPONENT_TEXT, b"fr");
        fixture.finish_node(TAG_LITERAL); // 18

        fixture.push_node_ref(10);
        fixture.push_node_ref(11);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_DATA_PROPERTY_OF); // 19
        fixture.push_node_set(&[10, 11, 12]);
        fixture.push_empty_set();
        fixture.finish_node(TAG_EQUIVALENT_DATA_PROPERTIES); // 20
        fixture.push_node_set(&[10, 11]);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DISJOINT_DATA_PROPERTIES); // 21
        fixture.push_node_ref(10);
        fixture.push_node_ref(9);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DATA_PROPERTY_DOMAIN); // 22
        fixture.push_node_ref(10);
        fixture.push_node_ref(15);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DATA_PROPERTY_RANGE); // 23
        fixture.push_node_ref(10);
        fixture.push_empty_set();
        fixture.finish_node(TAG_FUNCTIONAL_DATA_PROPERTY); // 24
        fixture.push_node_ref(16);
        fixture.push_node_ref(15);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DATATYPE_DEFINITION); // 25
        for (tag, property, literal) in [
            (TAG_DATA_PROPERTY_ASSERTION, 10_u64, 17_u64),
            (TAG_NEGATIVE_DATA_PROPERTY_ASSERTION, 11, 18),
        ] {
            fixture.push_node_ref(property);
            fixture.push_node_ref(13);
            fixture.push_node_ref(literal);
            fixture.push_empty_set();
            fixture.finish_node(tag); // 26..=27
        }

        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 9]);
        for root_id in 19_u32..=27 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn data_class_expression_fixture() -> Fixture {
        let mut fixture = named_data_property_fixture();
        fixture.push_node_set(&[17, 18]);
        fixture.finish_node(TAG_DATA_ONE_OF); // 28
        fixture.push_node_ref(8);
        fixture.push_node_ref(17);
        fixture.finish_node(TAG_FACET_RESTRICTION); // 29
        fixture.push_node_ref(16);
        fixture.push_node_set(&[29]);
        fixture.finish_node(TAG_DATATYPE_RESTRICTION); // 30
        fixture.push_node_ref(28);
        fixture.finish_node(TAG_DATA_COMPLEMENT_OF); // 31
        fixture.push_node_set(&[15, 30, 31]);
        fixture.finish_node(TAG_DATA_INTERSECTION_OF); // 32

        fixture.push_node_sequence(&[10, 11]);
        fixture.push_node_ref(32);
        fixture.finish_node(TAG_DATA_SOME_VALUES_FROM); // 33
        fixture.push_node_sequence(&[12]);
        fixture.push_node_ref(15);
        fixture.finish_node(TAG_DATA_ALL_VALUES_FROM); // 34
        fixture.push_node_ref(10);
        fixture.push_node_ref(18);
        fixture.finish_node(TAG_DATA_HAS_VALUE); // 35
        fixture.push_scalar(COMPONENT_INTEGER, &[2]);
        fixture.push_node_ref(11);
        fixture.push_node_ref(30);
        fixture.finish_node(TAG_DATA_MIN_CARDINALITY); // 36
        fixture.push_scalar(COMPONENT_INTEGER, &[3]);
        fixture.push_node_ref(12);
        fixture.push_node_ref(28);
        fixture.finish_node(TAG_DATA_MAX_CARDINALITY); // 37
        fixture.push_scalar(COMPONENT_INTEGER, &[4]);
        fixture.push_node_ref(10);
        fixture.push_node_ref(31);
        fixture.finish_node(TAG_DATA_EXACT_CARDINALITY); // 38
        fixture.push_node_ref(33);
        fixture.finish_node(TAG_OBJECT_COMPLEMENT_OF); // 39

        for (sub, sup) in [
            (9_u64, 33_u64),
            (34, 9),
            (9, 35),
            (9, 36),
            (37, 9),
            (9, 38),
            (9, 39),
        ] {
            fixture.push_node_ref(sub);
            fixture.push_node_ref(sup);
            fixture.push_empty_set();
            fixture.finish_node(TAG_SUB_CLASS_OF); // 40..=46
        }
        for class in [35_u64, 39] {
            fixture.push_node_ref(class);
            fixture.push_node_ref(13);
            fixture.push_empty_set();
            fixture.finish_node(TAG_CLASS_ASSERTION); // 47..=48
        }
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 9]);
        for root_id in 40_u32..=48 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn expanded_expression_axiom_fixture() -> Fixture {
        let mut fixture = data_class_expression_fixture();
        fixture.push_node_set(&[9, 33, 35]);
        fixture.finish_node(TAG_OBJECT_INTERSECTION_OF); // 49
        for expressions in [[9_u64, 49_u64], [9, 35]] {
            fixture.push_node_set(&expressions);
            fixture.push_empty_set();
            fixture.finish_node(TAG_EQUIVALENT_CLASSES); // 50..=51
        }
        fixture.push_node_ref(9);
        fixture.push_node_ref(49);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 52
        fixture.push_node_ref(49);
        fixture.push_node_ref(13);
        fixture.push_empty_set();
        fixture.finish_node(TAG_CLASS_ASSERTION); // 53
        fixture.push_node_set(&[33, 35, 49]);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DISJOINT_CLASSES); // 54
        fixture.push_node_ref(9);
        fixture.push_node_set(&[33, 35]);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DISJOINT_UNION); // 55
        fixture.push_node_ref(33);
        fixture.push_empty_set();
        fixture.push_node_set(&[10]);
        fixture.push_empty_set();
        fixture.finish_node(TAG_HAS_KEY); // 56
        fixture.push_node_ref(10);
        fixture.push_node_ref(39);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DATA_PROPERTY_DOMAIN); // 57
        fixture.push_node_ref(10);
        fixture.push_node_ref(32);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DATA_PROPERTY_RANGE); // 58
        fixture.push_node_ref(16);
        fixture.push_node_ref(31);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DATATYPE_DEFINITION); // 59
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 10]);
        for root_id in 50_u32..=59 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn named_annotation_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"http://www.w3.org/2000/01/rdf-schema#label",
            b"urn:datatype",
            b"urn:value",
            b"urn:meta",
            XSD_STRING.as_bytes(),
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=6
        }
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 7
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(2);
        fixture.finish_node(TAG_ENTITY); // 8
        fixture.push_scalar(COMPONENT_ENUM, b"datatype");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY); // 9
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 10
        fixture.push_scalar(COMPONENT_ENUM, b"datatype");
        fixture.push_node_ref(6);
        fixture.finish_node(TAG_ENTITY); // 11

        fixture.push_scalar(COMPONENT_TEXT, b"a\\b");
        fixture.push_node_ref(9);
        fixture.push_none();
        fixture.finish_node(TAG_LITERAL); // 12
        fixture.push_scalar(COMPONENT_TEXT, b"duplicate");
        fixture.push_node_ref(11);
        fixture.push_none();
        fixture.finish_node(TAG_LITERAL); // 13
        fixture.push_scalar(COMPONENT_TEXT, b"metadata");
        fixture.push_node_ref(11);
        fixture.push_none();
        fixture.finish_node(TAG_LITERAL); // 14
        fixture.push_node_ref(10);
        fixture.push_node_ref(14);
        fixture.push_empty_set();
        fixture.finish_node(TAG_ANNOTATION); // 15

        fixture.push_node_ref(7);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DECLARATION); // 16
        for (value, annotations) in [
            (4_u64, &[][..]),
            (12, &[][..]),
            (13, &[][..]),
            (13, &[15][..]),
        ] {
            fixture.push_node_ref(8);
            fixture.push_node_ref(1);
            fixture.push_node_ref(value);
            fixture.push_node_set(annotations);
            fixture.finish_node(TAG_ANNOTATION_ASSERTION); // 17..=20
        }
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 5]);
        for root_id in 16_u32..=20 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn annotation_metadata_root_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"urn:B",
            b"urn:annotation-property",
            b"urn:super-annotation-property",
            b"urn:annotation-domain",
            b"urn:annotation-range",
            XSD_STRING.as_bytes(),
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=7
        }
        for iri_id in [1_u64, 2] {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 8..=9
        }
        for iri_id in [3_u64, 4] {
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 10..=11
        }
        fixture.push_scalar(COMPONENT_ENUM, b"datatype");
        fixture.push_node_ref(7);
        fixture.finish_node(TAG_ENTITY); // 12
        fixture.push_scalar(COMPONENT_BYTES, &[17; 32]);
        fixture.push_scalar(COMPONENT_BYTES, b"metadata-anonymous");
        fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL); // 13
        fixture.push_scalar(COMPONENT_TEXT, b"metadata-literal");
        fixture.push_node_ref(12);
        fixture.push_none();
        fixture.finish_node(TAG_LITERAL); // 14
        fixture.push_node_ref(11);
        fixture.push_node_ref(13);
        fixture.push_empty_set();
        fixture.finish_node(TAG_ANNOTATION); // 15
        fixture.push_node_ref(10);
        fixture.push_node_ref(14);
        fixture.push_node_set(&[15]);
        fixture.finish_node(TAG_ANNOTATION); // 16
        fixture.push_node_ref(8);
        fixture.push_node_ref(9);
        fixture.push_node_set(&[16]);
        fixture.finish_node(TAG_SUB_CLASS_OF); // 17
        fixture.push_node_ref(10);
        fixture.push_node_ref(11);
        fixture.push_node_set(&[16]);
        fixture.finish_node(TAG_SUB_ANNOTATION_PROPERTY_OF); // 18
        for (tag, property, iri) in [
            (TAG_ANNOTATION_PROPERTY_DOMAIN, 10_u64, 5_u64),
            (TAG_ANNOTATION_PROPERTY_RANGE, 11, 6),
        ] {
            fixture.push_node_ref(property);
            fixture.push_node_ref(iri);
            fixture.push_node_set(&[16]);
            fixture.finish_node(tag); // 19..=20
        }
        fixture.root_kinds.push(ROOT_ONTOLOGY_ANNOTATION);
        fixture.root_ids.extend_from_slice(&16_u32.to_le_bytes());
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 4]);
        for root_id in 17_u32..=20 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn skipped_logical_fixture() -> Fixture {
        let mut fixture = named_annotation_fixture();
        fixture.push_scalar(COMPONENT_BYTES, &[7; 32]);
        fixture.push_scalar(COMPONENT_BYTES, b"anonymous");
        fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL); // 21
        fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
        fixture.push_node_ref(4);
        fixture.finish_node(TAG_ENTITY); // 22
        fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 23
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 24
        fixture.push_scalar(COMPONENT_ENUM, b"data_property");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY); // 25
        fixture.push_node_ref(24);
        fixture.push_node_ref(7);
        fixture.finish_node(TAG_OBJECT_SOME_VALUES_FROM); // 26

        fixture.push_node_ref(26);
        fixture.push_node_set(&[24]);
        fixture.push_node_set(&[25]);
        fixture.push_node_set(&[15]);
        fixture.finish_node(TAG_HAS_KEY); // 27
        fixture.push_node_set(&[21, 22]);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SAME_INDIVIDUAL); // 28
        fixture.push_node_set(&[21, 23]);
        fixture.push_node_set(&[15]);
        fixture.finish_node(TAG_DIFFERENT_INDIVIDUALS); // 29
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 3]);
        for root_id in 27_u32..=29 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn running_state() -> AtomicU8 {
        AtomicU8::new(STATE_RUNNING)
    }

    #[test]
    fn declarations_are_silent_and_named_subclasses_compile() {
        let fixture = named_subclass_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            4,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            edges,
            vec![DirectEdge {
                source: "urn:A".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:B".into(),
            }]
        );
        assert_eq!(stats.roots, 2);
        assert_eq!(stats.declarations, 1);
        assert_eq!(stats.subclasses, 1);
        assert_eq!(stats.edges, 1);
    }

    #[test]
    fn bidirectional_projection_is_one_bounded_batch() {
        let fixture = named_subclass_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            true,
            false,
            false,
            2,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(edges.len(), 2);
        assert_eq!(edges[1].relation, SUPERCLASS_OF);
        assert_eq!(stats.edges, 2);
        let error = compile_direct(
            fixture.columns(),
            true,
            false,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap_err();
        assert!(matches!(error, KernelError::Resource(_)));
    }

    #[test]
    fn named_equivalents_and_class_assertions_follow_reference_order() {
        let fixture = named_class_axiom_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            true,
            false,
            false,
            5,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            edges,
            vec![
                DirectEdge {
                    source: "urn:Z".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:A".into(),
                },
                DirectEdge {
                    source: "urn:A".into(),
                    relation: SUPERCLASS_OF.into(),
                    destination: "urn:Z".into(),
                },
                DirectEdge {
                    source: "urn:AA".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:B".into(),
                    relation: SUPERCLASS_OF.into(),
                    destination: "urn:AA".into(),
                },
                DirectEdge {
                    source: "urn:i".into(),
                    relation: RDF_TYPE.into(),
                    destination: "urn:A".into(),
                },
            ]
        );
        assert_eq!(stats.declarations, 1);
        assert_eq!(stats.subclasses, 1);
        assert_eq!(stats.equivalents, 1);
        assert_eq!(stats.class_assertions, 1);
        assert_eq!(stats.edges, 5);
    }

    #[test]
    fn named_restrictions_and_domain_range_products_follow_reference_rules() {
        let fixture = named_role_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            true,
            false,
            false,
            11,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            edges,
            vec![
                DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:D".into(),
                },
                DirectEdge {
                    source: "urn:D".into(),
                    relation: SUPERCLASS_OF.into(),
                    destination: "urn:A".into(),
                },
                DirectEdge {
                    source: "urn:A".into(),
                    relation: "urn:p".into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:A".into(),
                    relation: "urn:p".into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:D".into(),
                    relation: "urn:p".into(),
                    destination: "urn:C".into(),
                },
                DirectEdge {
                    source: "urn:D".into(),
                    relation: "urn:p".into(),
                    destination: "urn:C".into(),
                },
                DirectEdge {
                    source: "urn:D1".into(),
                    relation: "urn:p".into(),
                    destination: "urn:R1".into(),
                },
                DirectEdge {
                    source: "urn:D1".into(),
                    relation: "urn:p".into(),
                    destination: "urn:R2".into(),
                },
                DirectEdge {
                    source: "urn:D2".into(),
                    relation: "urn:p".into(),
                    destination: "urn:R1".into(),
                },
                DirectEdge {
                    source: "urn:D2".into(),
                    relation: "urn:p".into(),
                    destination: "urn:R2".into(),
                },
                DirectEdge {
                    source: "urn:QD".into(),
                    relation: "urn:q".into(),
                    destination: "urn:QR".into(),
                },
            ]
        );
        assert_eq!(stats.subclasses, 5);
        assert_eq!(stats.restriction_subclasses, 4);
        assert_eq!(stats.object_property_domains, 3);
        assert_eq!(stats.object_property_ranges, 3);
        assert_eq!(stats.domain_range_edges, 5);
    }

    #[test]
    fn only_taxonomy_and_asserted_taxonomy_keep_distinct_reference_defects() {
        let fixture = named_role_fixture();
        let (only_taxonomy, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            true,
            6,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(only_taxonomy.len(), 6);
        assert_eq!(stats.domain_range_edges, 5);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            true,
            true,
            false,
            2,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(asserted.len(), 2);
        assert!(asserted
            .iter()
            .all(|edge| { edge.relation == SUBCLASS_OF || edge.relation == SUPERCLASS_OF }));
        assert_eq!(stats.domain_range_edges, 0);
    }

    #[test]
    fn named_object_assertions_emit_and_negative_inverse_assertions_are_silent() {
        let fixture = named_object_assertion_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            true,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            edges,
            vec![DirectEdge {
                source: "urn:i".into(),
                relation: "urn:p".into(),
                destination: "urn:j".into(),
            }]
        );
        assert_eq!(stats.object_property_assertions, 1);
        assert_eq!(stats.negative_object_property_assertions, 2);
        assert_eq!(stats.skipped_axioms, 2);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            false,
            true,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(asserted.is_empty());
        assert_eq!(stats.object_property_assertions, 1);
        assert_eq!(stats.negative_object_property_assertions, 2);
        assert_eq!(stats.skipped_axioms, 0);
    }

    #[test]
    fn positive_inverse_object_assertion_preserves_reference_failure() {
        let mut fixture = named_object_assertion_fixture();
        let assertion_field =
            read_usize(&fixture.node_field_offsets, 7, "offset").expect("assertion field offset");
        let assertion_start = assertion_field * 8;
        fixture.field_values[assertion_start..assertion_start + 8]
            .copy_from_slice(&7_u64.to_le_bytes());
        assert!(matches!(
            compile_direct(
                fixture.columns(),
                false,
                false,
                false,
                3,
                1024,
                &running_state(),
            ),
            Err(KernelError::ReferenceFailure(_))
        ));

        let mut malformed = named_object_assertion_fixture();
        let inverse_field =
            read_usize(&malformed.node_field_offsets, 6, "offset").expect("inverse field offset");
        let start = inverse_field * 8;
        malformed.field_values[start..start + 8].copy_from_slice(&5_u64.to_le_bytes());
        assert!(matches!(
            compile_direct(
                malformed.columns(),
                false,
                false,
                false,
                3,
                1024,
                &running_state(),
            ),
            Err(KernelError::Malformed(_))
        ));
    }

    #[test]
    fn named_role_axioms_expand_edges_and_skipped_role_families_are_state_neutral() {
        let fixture = named_role_axiom_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            6,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            edges,
            vec![
                DirectEdge {
                    source: "urn:A".into(),
                    relation: "urn:p".into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:A".into(),
                    relation: "urn:child".into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:B".into(),
                    relation: "urn:pinv".into(),
                    destination: "urn:A".into(),
                },
                DirectEdge {
                    source: "urn:D".into(),
                    relation: "urn:p".into(),
                    destination: "urn:R".into(),
                },
                DirectEdge {
                    source: "urn:D".into(),
                    relation: "urn:child".into(),
                    destination: "urn:R".into(),
                },
                DirectEdge {
                    source: "urn:R".into(),
                    relation: "urn:pinv".into(),
                    destination: "urn:D".into(),
                },
            ]
        );
        assert_eq!(stats.sub_object_properties, 1);
        assert_eq!(stats.inverse_object_properties, 1);
        assert_eq!(stats.equivalent_object_properties, 1);
        assert_eq!(stats.disjoint_object_properties, 1);
        assert_eq!(stats.skipped_axioms, 9);
        assert_eq!(stats.domain_range_edges, 1);
        assert_eq!(stats.role_expansion_edges, 4);

        let (only_taxonomy, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            true,
            3,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(only_taxonomy.len(), 3);
        assert_eq!(stats.role_expansion_edges, 2);
        assert_eq!(stats.skipped_axioms, 9);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            false,
            true,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(asserted.is_empty());
        assert_eq!(stats.skipped_axioms, 0);
        assert_eq!(stats.role_expansion_edges, 0);
    }

    #[test]
    fn retained_role_state_commits_only_after_successful_output_preparation() {
        let role_fixture = named_role_axiom_fixture();
        let mut retained = OwnedRoleState::default();
        let result = compile_direct_with_retained_role_state(
            role_fixture.columns(),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: false,
                max_edges: 5,
                max_iri_bytes: 1024,
            },
            &running_state(),
            Some(&mut retained),
        );
        assert!(matches!(result, Err(KernelError::Resource(_))));
        assert_eq!(retained.subrole_count(), 0);
        assert_eq!(retained.inverse_count(), 0);

        let (edges, _stats) = compile_direct_with_retained_role_state(
            role_fixture.columns(),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: false,
                max_edges: 6,
                max_iri_bytes: 1024,
            },
            &running_state(),
            Some(&mut retained),
        )
        .unwrap();
        assert_eq!(edges.len(), 6);
        assert_eq!(retained.subrole_count(), 1);
        assert_eq!(retained.inverse_count(), 2);

        let mut empty = Fixture::default();
        empty
            .node_field_offsets
            .extend_from_slice(&0_u64.to_le_bytes());
        let limited = compile_direct_with_retained_role_state(
            empty.columns(),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: false,
                max_edges: 1,
                max_iri_bytes: 3,
            },
            &running_state(),
            Some(&mut retained),
        );
        assert!(matches!(limited, Err(KernelError::Resource(_))));
        assert_eq!(retained.subrole_count(), 1);
        assert_eq!(retained.inverse_count(), 2);

        let mut consumer = named_role_axiom_fixture();
        consumer.root_kinds = vec![ROOT_AXIOM; 2];
        consumer.root_ids.clear();
        for root_id in [20_u32, 21] {
            consumer.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        let (edges, stats) = compile_direct_with_retained_role_state(
            consumer.columns(),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: false,
                max_edges: 3,
                max_iri_bytes: 1024,
            },
            &running_state(),
            Some(&mut retained),
        )
        .unwrap();
        assert_eq!(
            edges,
            vec![
                DirectEdge {
                    source: "urn:D".into(),
                    relation: "urn:p".into(),
                    destination: "urn:R".into(),
                },
                DirectEdge {
                    source: "urn:D".into(),
                    relation: "urn:child".into(),
                    destination: "urn:R".into(),
                },
                DirectEdge {
                    source: "urn:R".into(),
                    relation: "urn:pinv".into(),
                    destination: "urn:D".into(),
                },
            ]
        );
        assert_eq!(stats.role_expansion_edges, 2);
        assert_eq!(retained.subrole_count(), 1);
        assert_eq!(retained.inverse_count(), 2);
    }

    #[test]
    fn inverse_restrictions_project_while_complex_domain_range_roots_are_ignored() {
        let fixture = inverse_restriction_and_ignored_domain_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            6,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(edges.len(), 6);
        assert_eq!(edges[0].relation, "urn:p");
        assert_eq!(edges[1].relation, "urn:child");
        assert_eq!(edges[2].relation, "urn:pinv");
        assert_eq!(stats.object_property_domains, 2);
        assert_eq!(stats.object_property_ranges, 2);
        assert_eq!(stats.domain_range_edges, 1);
        assert_eq!(stats.role_expansion_edges, 4);

        let (only_taxonomy, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            true,
            3,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(only_taxonomy.len(), 3);
        assert_eq!(stats.domain_range_edges, 1);
        assert_eq!(stats.role_expansion_edges, 2);
    }

    #[test]
    fn ordered_inverse_property_chains_are_validated_but_do_not_mutate_roles() {
        let fixture = object_property_chain_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            6,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(edges.len(), 6);
        assert_eq!(stats.sub_object_properties, 2);
        assert_eq!(stats.object_property_chains, 1);
        assert_eq!(stats.role_expansion_edges, 4);
        assert_eq!(stats.skipped_axioms, 9);

        let mut malformed = object_property_chain_fixture();
        let chain_field =
            read_usize(&malformed.node_field_offsets, 30, "offset").expect("chain field offset");
        malformed.field_lengths[chain_field * 8..(chain_field + 1) * 8]
            .copy_from_slice(&1_u64.to_le_bytes());
        assert!(matches!(
            compile_direct(
                malformed.columns(),
                false,
                false,
                false,
                6,
                1024,
                &running_state(),
            ),
            Err(KernelError::Malformed(_))
        ));
    }

    #[test]
    fn nonprojecting_object_expressions_are_counted_state_neutral_ignored_shapes() {
        let fixture = nonprojecting_class_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            2,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(edges.len(), 2);
        assert_eq!(stats.subclasses, 5);
        assert_eq!(stats.ignored_subclasses, 4);
        assert_eq!(stats.class_assertions, 4);
        assert_eq!(stats.ignored_class_assertions, 3);
        assert_eq!(stats.role_expansion_edges, 0);
        assert_eq!(stats.skipped_axioms, 0);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            true,
            true,
            false,
            2,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(asserted.len(), 2);
        assert_eq!(stats.ignored_subclasses, 4);
        assert_eq!(stats.ignored_class_assertions, 3);
    }

    #[test]
    fn aggregate_equivalence_emits_named_and_role_expanded_operands() {
        let fixture = named_aggregate_role_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            10,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(edges.len(), 10);
        assert_eq!(stats.equivalents, 1);
        assert_eq!(stats.aggregate_equivalents, 1);
        assert_eq!(stats.role_expansion_edges, 6);
        assert_eq!(
            &edges[3..7],
            &[
                DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:A".into(),
                    relation: "urn:p".into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:A".into(),
                    relation: "urn:child".into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:B".into(),
                    relation: "urn:pinv".into(),
                    destination: "urn:A".into(),
                },
            ]
        );

        let (only_taxonomy, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            true,
            4,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(only_taxonomy.len(), 4);
        assert_eq!(stats.aggregate_equivalents, 1);
        assert_eq!(stats.role_expansion_edges, 2);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            false,
            true,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(asserted.is_empty());
        assert_eq!(stats.aggregate_equivalents, 1);
        assert_eq!(stats.role_expansion_edges, 0);
    }

    #[test]
    fn disjoint_class_families_are_validated_state_neutral_skips() {
        let fixture = named_disjoint_class_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            10,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(edges.len(), 10);
        assert_eq!(stats.disjoint_classes, 1);
        assert_eq!(stats.disjoint_unions, 1);
        assert_eq!(stats.skipped_axioms, 11);
        assert_eq!(stats.role_expansion_edges, 6);

        let (only_taxonomy, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            true,
            4,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(only_taxonomy.len(), 4);
        assert_eq!(stats.skipped_axioms, 11);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            false,
            true,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(asserted.is_empty());
        assert_eq!(stats.disjoint_classes, 1);
        assert_eq!(stats.disjoint_unions, 1);
        assert_eq!(stats.skipped_axioms, 0);
    }

    #[test]
    fn named_data_property_families_validate_literals_and_remain_state_neutral() {
        let fixture = named_data_property_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(edges.is_empty());
        assert_eq!(stats.sub_data_properties, 1);
        assert_eq!(stats.equivalent_data_properties, 1);
        assert_eq!(stats.disjoint_data_properties, 1);
        assert_eq!(stats.data_property_domains, 1);
        assert_eq!(stats.data_property_ranges, 1);
        assert_eq!(stats.functional_data_properties, 1);
        assert_eq!(stats.datatype_definitions, 1);
        assert_eq!(stats.data_property_assertions, 1);
        assert_eq!(stats.negative_data_property_assertions, 1);
        assert_eq!(stats.skipped_axioms, 9);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            false,
            true,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(asserted.is_empty());
        assert_eq!(stats.skipped_axioms, 0);
    }

    #[test]
    fn bounded_data_class_expressions_validate_and_remain_state_neutral() {
        let fixture = data_class_expression_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(edges.is_empty());
        assert_eq!(stats.roots, 18);
        assert_eq!(stats.subclasses, 7);
        assert_eq!(stats.ignored_subclasses, 7);
        assert_eq!(stats.class_assertions, 2);
        assert_eq!(stats.ignored_class_assertions, 2);
        assert_eq!(stats.skipped_axioms, 9);
        assert_eq!(stats.role_expansion_edges, 0);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            true,
            true,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(asserted.is_empty());
        assert_eq!(stats.ignored_subclasses, 7);
        assert_eq!(stats.ignored_class_assertions, 2);
        assert_eq!(stats.skipped_axioms, 0);
    }

    #[test]
    fn bounded_expressions_extend_ignored_and_skipped_axiom_families() {
        let fixture = expanded_expression_axiom_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(edges.len(), 1);
        assert_eq!(stats.roots, 28);
        assert_eq!(stats.subclasses, 8);
        assert_eq!(stats.ignored_subclasses, 8);
        assert_eq!(stats.equivalents, 2);
        assert_eq!(stats.aggregate_equivalents, 1);
        assert_eq!(stats.class_assertions, 3);
        assert_eq!(stats.ignored_class_assertions, 3);
        assert_eq!(stats.disjoint_classes, 1);
        assert_eq!(stats.disjoint_unions, 1);
        assert_eq!(stats.has_keys, 1);
        assert_eq!(stats.data_property_domains, 2);
        assert_eq!(stats.data_property_ranges, 2);
        assert_eq!(stats.datatype_definitions, 2);
        assert_eq!(stats.skipped_axioms, 15);
        assert_eq!(stats.role_expansion_edges, 0);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            false,
            true,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(asserted.is_empty());
        assert_eq!(stats.aggregate_equivalents, 1);
        assert_eq!(stats.ignored_subclasses, 8);
        assert_eq!(stats.ignored_class_assertions, 3);
        assert_eq!(stats.skipped_axioms, 0);
    }

    #[test]
    fn selected_annotations_render_duplicates_and_keep_taxonomy_modes_distinct() {
        let fixture = named_annotation_fixture();
        let (edges, stats) = compile_direct_with_options(
            fixture.columns(),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: true,
                include_literals: true,
                max_edges: 4,
                max_iri_bytes: 1024,
            },
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            edges,
            vec![
                DirectEdge {
                    source: "urn:A".into(),
                    relation: "rdfs:label".into(),
                    destination: "urn:value".into(),
                },
                DirectEdge {
                    source: "urn:A".into(),
                    relation: "rdfs:label".into(),
                    destination: "ab\"^^<urn:datatype".into(),
                },
                DirectEdge {
                    source: "urn:A".into(),
                    relation: "rdfs:label".into(),
                    destination: "duplicate".into(),
                },
                DirectEdge {
                    source: "urn:A".into(),
                    relation: "rdfs:label".into(),
                    destination: "duplicate".into(),
                },
            ]
        );
        assert_eq!(stats.annotation_assertions, 4);
        assert_eq!(stats.annotation_edges, 4);
        assert_eq!(stats.non_string_literal_renderings, 1);

        let (asserted, stats) = compile_direct_with_options(
            fixture.columns(),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: true,
                only_taxonomy: false,
                include_literals: true,
                max_edges: 1,
                max_iri_bytes: 1024,
            },
            &running_state(),
        )
        .unwrap();
        assert!(asserted.is_empty());
        assert_eq!(stats.annotation_assertions, 4);
        assert_eq!(stats.annotation_edges, 0);

        assert!(matches!(
            compile_direct_with_options(
                fixture.columns(),
                DirectCompileOptions {
                    bidirectional: false,
                    asserted_taxonomy_only: false,
                    only_taxonomy: false,
                    include_literals: true,
                    max_edges: 3,
                    max_iri_bytes: 1024,
                },
                &running_state(),
            ),
            Err(KernelError::Resource(_))
        ));
    }

    #[test]
    fn annotation_metadata_roots_are_validated_state_neutral_skips() {
        let mut fixture = annotation_metadata_root_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            edges,
            vec![DirectEdge {
                source: "urn:A".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:B".into(),
            }]
        );
        assert_eq!(stats.roots, 5);
        assert_eq!(stats.ontology_annotations, 1);
        assert_eq!(stats.sub_annotation_properties, 1);
        assert_eq!(stats.annotation_property_domains, 1);
        assert_eq!(stats.annotation_property_ranges, 1);
        assert_eq!(stats.skipped_axioms, 3);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            false,
            true,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(asserted.len(), 1);
        assert_eq!(stats.ontology_annotations, 1);
        assert_eq!(stats.sub_annotation_properties, 1);
        assert_eq!(stats.annotation_property_domains, 1);
        assert_eq!(stats.annotation_property_ranges, 1);
        assert_eq!(stats.skipped_axioms, 0);

        fixture.root_kinds[0] = ROOT_AXIOM;
        assert!(matches!(
            compile_direct(
                fixture.columns(),
                false,
                false,
                false,
                1,
                1024,
                &running_state(),
            ),
            Err(KernelError::Malformed(_))
        ));
    }

    #[test]
    fn key_and_individual_set_axioms_are_validated_state_neutral_skips() {
        let fixture = skipped_logical_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            false,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(edges.is_empty());
        assert_eq!(stats.has_keys, 1);
        assert_eq!(stats.same_individuals, 1);
        assert_eq!(stats.different_individuals, 1);
        assert_eq!(stats.annotation_assertions, 4);
        assert_eq!(stats.skipped_axioms, 3);

        let (asserted, stats) = compile_direct(
            fixture.columns(),
            false,
            true,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(asserted.is_empty());
        assert_eq!(stats.has_keys, 1);
        assert_eq!(stats.same_individuals, 1);
        assert_eq!(stats.different_individuals, 1);
        assert_eq!(stats.skipped_axioms, 0);
    }

    #[test]
    fn nonminimal_cardinality_and_cross_product_limit_fail_preflight() {
        let mut malformed = named_role_fixture();
        let cardinality_offset = malformed
            .scalar_bytes
            .windows(2)
            .position(|value| value == [0, 1])
            .expect("two-byte cardinality");
        malformed.scalar_bytes[cardinality_offset + 1] = 0;
        assert!(matches!(
            compile_direct(
                malformed.columns(),
                false,
                false,
                false,
                20,
                1024,
                &running_state(),
            ),
            Err(KernelError::Malformed(_))
        ));

        let fixture = named_role_fixture();
        assert!(matches!(
            compile_direct(
                fixture.columns(),
                true,
                false,
                false,
                10,
                1024,
                &running_state(),
            ),
            Err(KernelError::Resource(_))
        ));
    }

    #[test]
    fn asserted_taxonomy_mode_preflights_but_suppresses_adjacent_axioms() {
        let fixture = named_class_axiom_fixture();
        let (edges, stats) = compile_direct(
            fixture.columns(),
            false,
            true,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].source, "urn:Z");
        assert_eq!(stats.equivalents, 1);
        assert_eq!(stats.class_assertions, 1);
    }

    #[test]
    fn malformed_columns_and_wrong_constructor_tags_fail_before_output() {
        let mut malformed = named_subclass_fixture();
        malformed.root_ids[0..4].copy_from_slice(&99_u32.to_le_bytes());
        assert!(matches!(
            compile_direct(
                malformed.columns(),
                false,
                false,
                false,
                4,
                1024,
                &running_state()
            ),
            Err(KernelError::Malformed(_))
        ));

        let mut unsupported = named_subclass_fixture();
        unsupported.node_tags[10..12].copy_from_slice(&TAG_SWRL_RULE.to_le_bytes());
        assert!(matches!(
            compile_direct(
                unsupported.columns(),
                false,
                false,
                false,
                4,
                1024,
                &running_state()
            ),
            Err(KernelError::Malformed(_))
        ));
    }

    #[test]
    fn malformed_equivalent_set_is_rejected_during_preflight() {
        let mut fixture = named_class_axiom_fixture();
        fixture.item_values[0..8].copy_from_slice(&8_u64.to_le_bytes());
        fixture.item_values[8..16].copy_from_slice(&7_u64.to_le_bytes());
        assert!(matches!(
            compile_direct(
                fixture.columns(),
                false,
                false,
                false,
                4,
                1024,
                &running_state()
            ),
            Err(KernelError::Malformed(_))
        ));
    }

    #[test]
    fn cancellation_precedes_validation_or_output() {
        let fixture = named_subclass_fixture();
        let state = AtomicU8::new(STATE_CANCELLED);
        assert_eq!(
            compile_direct(fixture.columns(), false, false, false, 4, 1024, &state),
            Err(KernelError::Cancelled)
        );
    }
}
