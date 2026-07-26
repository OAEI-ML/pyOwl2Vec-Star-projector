//! No-copy compiler kernel for the private structural-columns v1 slice.
//!
//! This module deliberately contains no Python types.  The PyO3 boundary retains
//! immutable `bytes` exporters and lends their slices here while the GIL is
//! released.  The complete immutable input and exact output count are validated
//! before a resumable output cursor is published, so unsupported or malformed
//! inputs cannot expose partial edges. The legacy coarse call may still request
//! one materialized vector explicitly.

use std::borrow::Cow;
#[cfg(test)]
use std::sync::atomic::AtomicUsize;
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

#[derive(Clone, Debug, Eq, PartialEq)]
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
    pub(crate) equivalent_base_edges: usize,
    pub(crate) ignored_equivalents: usize,
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
    pub(crate) selected_annotation_assertions: usize,
    pub(crate) sub_annotation_properties: usize,
    pub(crate) annotation_property_domains: usize,
    pub(crate) annotation_property_ranges: usize,
    pub(crate) annotation_edges: usize,
    pub(crate) non_string_literal_renderings: usize,
    pub(crate) skipped_axioms: usize,
    pub(crate) object_property_domains: usize,
    pub(crate) object_property_ranges: usize,
    pub(crate) ignored_object_property_domains: usize,
    pub(crate) ignored_object_property_ranges: usize,
    pub(crate) domain_range_edges: usize,
    pub(crate) role_expansion_edges: usize,
    pub(crate) edges: usize,
    pub(crate) buffer_bytes: usize,
    pub(crate) root_provenance_buffer_bytes: usize,
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
    ignored_shapes: usize,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct AnnotationEdgeCounts {
    edges: usize,
    non_string_literals: usize,
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
    ignored_object_property_domains: usize,
    ignored_object_property_ranges: usize,
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
enum SilentObjectPropertyRoot {
    EquivalentSet,
    DisjointSet,
    Functional,
    InverseFunctional,
    Reflexive,
    Irreflexive,
    Symmetric,
    Asymmetric,
    Transitive,
}

impl SilentObjectPropertyRoot {
    fn classify(counts: RootCounts, tag: u16) -> Option<Self> {
        match tag {
            TAG_EQUIVALENT_OBJECT_PROPERTIES
                if counts
                    == (RootCounts {
                        equivalent_object_properties: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::EquivalentSet)
            }
            TAG_DISJOINT_OBJECT_PROPERTIES
                if counts
                    == (RootCounts {
                        disjoint_object_properties: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::DisjointSet)
            }
            TAG_FUNCTIONAL_OBJECT_PROPERTY
                if counts
                    == (RootCounts {
                        functional_object_properties: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Functional)
            }
            TAG_INVERSE_FUNCTIONAL_OBJECT_PROPERTY
                if counts
                    == (RootCounts {
                        inverse_functional_object_properties: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::InverseFunctional)
            }
            TAG_REFLEXIVE_OBJECT_PROPERTY
                if counts
                    == (RootCounts {
                        reflexive_object_properties: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Reflexive)
            }
            TAG_IRREFLEXIVE_OBJECT_PROPERTY
                if counts
                    == (RootCounts {
                        irreflexive_object_properties: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Irreflexive)
            }
            TAG_SYMMETRIC_OBJECT_PROPERTY
                if counts
                    == (RootCounts {
                        symmetric_object_properties: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Symmetric)
            }
            TAG_ASYMMETRIC_OBJECT_PROPERTY
                if counts
                    == (RootCounts {
                        asymmetric_object_properties: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Asymmetric)
            }
            TAG_TRANSITIVE_OBJECT_PROPERTY
                if counts
                    == (RootCounts {
                        transitive_object_properties: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Transitive)
            }
            _ => None,
        }
    }

    fn constructor(self) -> &'static str {
        match self {
            Self::EquivalentSet => "EquivalentObjectProperties",
            Self::DisjointSet => "DisjointObjectProperties",
            Self::Functional => "FunctionalObjectProperty",
            Self::InverseFunctional => "InverseFunctionalObjectProperty",
            Self::Reflexive => "ReflexiveObjectProperty",
            Self::Irreflexive => "IrreflexiveObjectProperty",
            Self::Symmetric => "SymmetricObjectProperty",
            Self::Asymmetric => "AsymmetricObjectProperty",
            Self::Transitive => "TransitiveObjectProperty",
        }
    }

    fn is_set(self) -> bool {
        matches!(self, Self::EquivalentSet | Self::DisjointSet)
    }

    fn statistics_counter(self, statistics: &mut DirectCompileStats) -> &mut usize {
        match self {
            Self::EquivalentSet => &mut statistics.equivalent_object_properties,
            Self::DisjointSet => &mut statistics.disjoint_object_properties,
            Self::Functional => &mut statistics.functional_object_properties,
            Self::InverseFunctional => &mut statistics.inverse_functional_object_properties,
            Self::Reflexive => &mut statistics.reflexive_object_properties,
            Self::Irreflexive => &mut statistics.irreflexive_object_properties,
            Self::Symmetric => &mut statistics.symmetric_object_properties,
            Self::Asymmetric => &mut statistics.asymmetric_object_properties,
            Self::Transitive => &mut statistics.transitive_object_properties,
        }
    }

    fn statistics_count(self, statistics: &DirectCompileStats) -> usize {
        match self {
            Self::EquivalentSet => statistics.equivalent_object_properties,
            Self::DisjointSet => statistics.disjoint_object_properties,
            Self::Functional => statistics.functional_object_properties,
            Self::InverseFunctional => statistics.inverse_functional_object_properties,
            Self::Reflexive => statistics.reflexive_object_properties,
            Self::Irreflexive => statistics.irreflexive_object_properties,
            Self::Symmetric => statistics.symmetric_object_properties,
            Self::Asymmetric => statistics.asymmetric_object_properties,
            Self::Transitive => statistics.transitive_object_properties,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SilentAnnotationPropertyRoot {
    SubProperty,
    Domain,
    Range,
}

impl SilentAnnotationPropertyRoot {
    fn classify(counts: RootCounts, tag: u16) -> Option<Self> {
        match tag {
            TAG_SUB_ANNOTATION_PROPERTY_OF
                if counts
                    == (RootCounts {
                        sub_annotation_properties: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::SubProperty)
            }
            TAG_ANNOTATION_PROPERTY_DOMAIN
                if counts
                    == (RootCounts {
                        annotation_property_domains: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Domain)
            }
            TAG_ANNOTATION_PROPERTY_RANGE
                if counts
                    == (RootCounts {
                        annotation_property_ranges: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Range)
            }
            _ => None,
        }
    }

    fn constructor(self) -> &'static str {
        match self {
            Self::SubProperty => "SubAnnotationPropertyOf",
            Self::Domain => "AnnotationPropertyDomain",
            Self::Range => "AnnotationPropertyRange",
        }
    }

    fn statistics_counter(self, statistics: &mut DirectCompileStats) -> &mut usize {
        match self {
            Self::SubProperty => &mut statistics.sub_annotation_properties,
            Self::Domain => &mut statistics.annotation_property_domains,
            Self::Range => &mut statistics.annotation_property_ranges,
        }
    }

    fn statistics_count(self, statistics: &DirectCompileStats) -> usize {
        match self {
            Self::SubProperty => statistics.sub_annotation_properties,
            Self::Domain => statistics.annotation_property_domains,
            Self::Range => statistics.annotation_property_ranges,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SilentClassDisjointnessRoot {
    Classes,
    Union,
}

impl SilentClassDisjointnessRoot {
    fn classify(counts: RootCounts, tag: u16) -> Option<Self> {
        match tag {
            TAG_DISJOINT_CLASSES
                if counts
                    == (RootCounts {
                        disjoint_classes: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Classes)
            }
            TAG_DISJOINT_UNION
                if counts
                    == (RootCounts {
                        disjoint_unions: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Union)
            }
            _ => None,
        }
    }

    fn constructor(self) -> &'static str {
        match self {
            Self::Classes => "DisjointClasses",
            Self::Union => "DisjointUnion",
        }
    }

    fn statistics_counter(self, statistics: &mut DirectCompileStats) -> &mut usize {
        match self {
            Self::Classes => &mut statistics.disjoint_classes,
            Self::Union => &mut statistics.disjoint_unions,
        }
    }

    fn statistics_count(self, statistics: &DirectCompileStats) -> usize {
        match self {
            Self::Classes => statistics.disjoint_classes,
            Self::Union => statistics.disjoint_unions,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SilentIgnoredClassRoot {
    Subclass,
    Assertion,
}

impl SilentIgnoredClassRoot {
    fn classify(counts: RootCounts, tag: u16) -> Option<Self> {
        match tag {
            TAG_SUB_CLASS_OF
                if counts
                    == (RootCounts {
                        subclasses: 1,
                        ignored_subclasses: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Subclass)
            }
            TAG_CLASS_ASSERTION
                if counts
                    == (RootCounts {
                        class_assertions: 1,
                        ignored_class_assertions: 1,
                        ..RootCounts::default()
                    }) =>
            {
                Some(Self::Assertion)
            }
            _ => None,
        }
    }

    fn constructor(self) -> &'static str {
        match self {
            Self::Subclass => "SubClassOf",
            Self::Assertion => "ClassAssertion",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct SilentIgnoredEquivalentRoot;

impl SilentIgnoredEquivalentRoot {
    fn classify(counts: RootCounts, tag: u16) -> Option<Self> {
        if tag != TAG_EQUIVALENT_CLASSES {
            return None;
        }
        if counts
            == (RootCounts {
                equivalents: 1,
                ..RootCounts::default()
            })
            || counts
                == (RootCounts {
                    equivalents: 1,
                    aggregate_equivalents: 1,
                    ..RootCounts::default()
                })
        {
            Some(Self)
        } else {
            None
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LocalProjectionMode {
    Normal,
    TaxonomyOnly,
    AssertedTaxonomy,
}

impl LocalProjectionMode {
    fn from_options(options: DirectCompileOptions) -> Self {
        if options.asserted_taxonomy_only {
            Self::AssertedTaxonomy
        } else if options.only_taxonomy {
            Self::TaxonomyOnly
        } else {
            Self::Normal
        }
    }

    fn index(self) -> usize {
        match self {
            Self::Normal => 0,
            Self::TaxonomyOnly => 1,
            Self::AssertedTaxonomy => 2,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LocalRuleContext {
    mode: LocalProjectionMode,
    include_literals: bool,
    max_iri_bytes: usize,
    local_scope_remap_available: bool,
}

impl LocalRuleContext {
    fn new(options: DirectCompileOptions, local_scope_remap_available: bool) -> Self {
        Self {
            mode: LocalProjectionMode::from_options(options),
            include_literals: options.include_literals,
            max_iri_bytes: options.max_iri_bytes,
            local_scope_remap_available,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LocalAnonymousScopePolicy {
    ExcludedByGrammar,
    RejectWithoutRemap,
}

impl LocalAnonymousScopePolicy {
    fn validate(
        self,
        columns: DirectColumns<'_>,
        root: usize,
        context: LocalRuleContext,
        root_description: &str,
        state: &AtomicU8,
    ) -> Result<(), KernelError> {
        match self {
            Self::ExcludedByGrammar => Ok(()),
            Self::RejectWithoutRemap
                if !context.local_scope_remap_available
                    && columns.root_contains_anonymous_individual(root, state)? =>
            {
                Err(KernelError::unsupported(format!(
                    "bounded local-overlay {root_description} root requires no anonymous individuals or local scope remap",
                )))
            }
            Self::RejectWithoutRemap => Ok(()),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ObjectPropertyClassRuleKind {
    Domain,
    Range,
}

impl ObjectPropertyClassRuleKind {
    fn matches_single_root(self, counts: RootCounts) -> bool {
        match self {
            Self::Domain => {
                counts
                    == (RootCounts {
                        object_property_domains: 1,
                        ..RootCounts::default()
                    })
                    || counts
                        == (RootCounts {
                            object_property_domains: 1,
                            ignored_object_property_domains: 1,
                            ..RootCounts::default()
                        })
            }
            Self::Range => {
                counts
                    == (RootCounts {
                        object_property_ranges: 1,
                        ..RootCounts::default()
                    })
                    || counts
                        == (RootCounts {
                            object_property_ranges: 1,
                            ignored_object_property_ranges: 1,
                            ..RootCounts::default()
                        })
            }
        }
    }

    fn is_ignored(self, counts: RootCounts) -> bool {
        match self {
            Self::Domain => counts.ignored_object_property_domains == 1,
            Self::Range => counts.ignored_object_property_ranges == 1,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ObjectPropertyClassRule {
    tag: u16,
    kind: ObjectPropertyClassRuleKind,
    constructor: &'static str,
    scope_description: &'static str,
    retain_ignored_by_mode: [bool; 3],
    scope_policy: LocalAnonymousScopePolicy,
}

const OBJECT_PROPERTY_CLASS_RULES: [ObjectPropertyClassRule; 2] = [
    ObjectPropertyClassRule {
        tag: TAG_OBJECT_PROPERTY_DOMAIN,
        kind: ObjectPropertyClassRuleKind::Domain,
        constructor: "ObjectPropertyDomain",
        scope_description: "ignored ObjectPropertyDomain",
        retain_ignored_by_mode: [true, true, true],
        scope_policy: LocalAnonymousScopePolicy::RejectWithoutRemap,
    },
    ObjectPropertyClassRule {
        tag: TAG_OBJECT_PROPERTY_RANGE,
        kind: ObjectPropertyClassRuleKind::Range,
        constructor: "ObjectPropertyRange",
        scope_description: "ignored ObjectPropertyRange",
        retain_ignored_by_mode: [true, true, true],
        scope_policy: LocalAnonymousScopePolicy::RejectWithoutRemap,
    },
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ObjectPropertyClassRulePlan {
    rule: ObjectPropertyClassRule,
    ignored: bool,
    retain_ignored_counter: bool,
}

impl ObjectPropertyClassRulePlan {
    fn from_rule(rule: ObjectPropertyClassRule, ignored: bool, context: LocalRuleContext) -> Self {
        Self {
            rule,
            ignored,
            retain_ignored_counter: rule.retain_ignored_by_mode[context.mode.index()],
        }
    }

    fn for_tag(tag: u16, ignored: bool, context: LocalRuleContext) -> Option<Self> {
        OBJECT_PROPERTY_CLASS_RULES
            .iter()
            .copied()
            .find(|rule| rule.tag == tag)
            .map(|rule| Self::from_rule(rule, ignored, context))
    }

    fn classify(counts: RootCounts, tag: u16, context: LocalRuleContext) -> Option<Self> {
        let rule = OBJECT_PROPERTY_CLASS_RULES
            .iter()
            .copied()
            .find(|rule| rule.tag == tag && rule.kind.matches_single_root(counts))?;
        Some(Self::from_rule(rule, rule.kind.is_ignored(counts), context))
    }

    fn validate<'a>(
        self,
        columns: DirectColumns<'a>,
        root: usize,
        context: LocalRuleContext,
        state: &AtomicU8,
    ) -> Result<Option<(ObjectPropertyClassRuleKind, &'a str, &'a str)>, KernelError> {
        let field_start = columns.exact_fields(root, 3)?;
        let (_annotation_start, annotation_count) = columns.node_set_range(field_start + 2, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {} root must be unannotated",
                self.rule.constructor,
            )));
        }
        let projection =
            columns.object_property_class_projection(root, self.rule.tag, context.max_iri_bytes)?;
        if self.ignored {
            if projection.is_some() {
                return Err(KernelError::malformed(
                    "encoded local object-property class rule changed after classification",
                ));
            }
            self.rule.scope_policy.validate(
                columns,
                root,
                context,
                self.rule.scope_description,
                state,
            )?;
            return Ok(None);
        }
        let (property, class) = projection.ok_or_else(|| {
            KernelError::malformed(
                "encoded local object-property class projection changed after classification",
            )
        })?;
        Ok(Some((self.rule.kind, property, class)))
    }

    fn apply_statistics(self, statistics: &mut DirectCompileStats) -> Result<(), KernelError> {
        match self.rule.kind {
            ObjectPropertyClassRuleKind::Domain => {
                statistics.object_property_domains = statistics
                    .object_property_domains
                    .checked_add(1)
                    .ok_or_else(|| {
                        KernelError::resource("encoded object-property-domain count overflow")
                    })?;
                if self.ignored && self.retain_ignored_counter {
                    statistics.ignored_object_property_domains = statistics
                        .ignored_object_property_domains
                        .checked_add(1)
                        .ok_or_else(|| {
                            KernelError::resource(
                                "encoded ignored-object-property-domain count overflow",
                            )
                        })?;
                }
            }
            ObjectPropertyClassRuleKind::Range => {
                statistics.object_property_ranges = statistics
                    .object_property_ranges
                    .checked_add(1)
                    .ok_or_else(|| {
                        KernelError::resource("encoded object-property-range count overflow")
                    })?;
                if self.ignored && self.retain_ignored_counter {
                    statistics.ignored_object_property_ranges = statistics
                        .ignored_object_property_ranges
                        .checked_add(1)
                        .ok_or_else(|| {
                            KernelError::resource(
                                "encoded ignored-object-property-range count overflow",
                            )
                        })?;
                }
            }
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LocalRoleRuleKind {
    SimpleSubProperty,
    PropertyChain,
    InverseProperties,
}

impl LocalRoleRuleKind {
    fn matches_single_root(self, counts: RootCounts) -> bool {
        match self {
            Self::SimpleSubProperty => {
                counts
                    == (RootCounts {
                        sub_object_properties: 1,
                        ..RootCounts::default()
                    })
            }
            Self::PropertyChain => {
                counts
                    == (RootCounts {
                        sub_object_properties: 1,
                        object_property_chains: 1,
                        ..RootCounts::default()
                    })
            }
            Self::InverseProperties => {
                counts
                    == (RootCounts {
                        inverse_object_properties: 1,
                        ..RootCounts::default()
                    })
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LocalRoleRule {
    tag: u16,
    kind: LocalRoleRuleKind,
    constructor: &'static str,
    mutates_role_state: bool,
    retain_constructor_by_mode: [bool; 3],
    retain_chain_by_mode: [bool; 3],
    scope_policy: LocalAnonymousScopePolicy,
}

const LOCAL_ROLE_RULES: [LocalRoleRule; 3] = [
    LocalRoleRule {
        tag: TAG_SUB_OBJECT_PROPERTY_OF,
        kind: LocalRoleRuleKind::SimpleSubProperty,
        constructor: "SubObjectPropertyOf",
        mutates_role_state: true,
        retain_constructor_by_mode: [true, true, true],
        retain_chain_by_mode: [false, false, false],
        scope_policy: LocalAnonymousScopePolicy::ExcludedByGrammar,
    },
    LocalRoleRule {
        tag: TAG_SUB_OBJECT_PROPERTY_OF,
        kind: LocalRoleRuleKind::PropertyChain,
        constructor: "SubObjectPropertyOf",
        mutates_role_state: false,
        retain_constructor_by_mode: [true, true, true],
        retain_chain_by_mode: [true, true, true],
        scope_policy: LocalAnonymousScopePolicy::ExcludedByGrammar,
    },
    LocalRoleRule {
        tag: TAG_INVERSE_OBJECT_PROPERTIES,
        kind: LocalRoleRuleKind::InverseProperties,
        constructor: "InverseObjectProperties",
        mutates_role_state: true,
        retain_constructor_by_mode: [true, true, true],
        retain_chain_by_mode: [false, false, false],
        scope_policy: LocalAnonymousScopePolicy::ExcludedByGrammar,
    },
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LocalRoleRulePlan {
    rule: LocalRoleRule,
    retain_constructor_counter: bool,
    retain_chain_counter: bool,
}

impl LocalRoleRulePlan {
    fn classify(counts: RootCounts, tag: u16, context: LocalRuleContext) -> Option<Self> {
        LOCAL_ROLE_RULES
            .iter()
            .copied()
            .find(|rule| rule.tag == tag && rule.kind.matches_single_root(counts))
            .map(|rule| Self {
                rule,
                retain_constructor_counter: rule.retain_constructor_by_mode[context.mode.index()],
                retain_chain_counter: rule.retain_chain_by_mode[context.mode.index()],
            })
    }

    fn validate<'a>(
        self,
        columns: DirectColumns<'a>,
        root: usize,
        context: LocalRuleContext,
        state: &AtomicU8,
    ) -> Result<Option<RoleAxiom<'a>>, KernelError> {
        let field_start = columns.exact_fields(root, 3)?;
        let (_annotation_start, annotation_count) = columns.node_set_range(field_start + 2, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {} root must be unannotated",
                self.rule.constructor,
            )));
        }
        self.rule
            .scope_policy
            .validate(columns, root, context, self.rule.constructor, state)?;
        if self.rule.mutates_role_state {
            return columns
                .role_axiom_row(root, self.rule.tag, context.max_iri_bytes, 0, 0)
                .map(Some);
        }
        Ok(None)
    }

    fn apply_statistics(self, statistics: &mut DirectCompileStats) -> Result<(), KernelError> {
        if self.retain_constructor_counter {
            match self.rule.kind {
                LocalRoleRuleKind::SimpleSubProperty | LocalRoleRuleKind::PropertyChain => {
                    statistics.sub_object_properties = statistics
                        .sub_object_properties
                        .checked_add(1)
                        .ok_or_else(|| {
                            KernelError::resource("encoded sub-object-property count overflow")
                        })?;
                }
                LocalRoleRuleKind::InverseProperties => {
                    statistics.inverse_object_properties = statistics
                        .inverse_object_properties
                        .checked_add(1)
                        .ok_or_else(|| {
                            KernelError::resource(
                                "encoded inverse-object-properties count overflow",
                            )
                        })?;
                }
            }
        }
        if self.retain_chain_counter {
            statistics.object_property_chains = statistics
                .object_property_chains
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded object-property-chain count overflow")
                })?;
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LocalAnnotationRuleKind {
    OntologyAnnotation,
    Assertion,
}

impl LocalAnnotationRuleKind {
    fn matches_single_root(self, counts: RootCounts) -> bool {
        match self {
            Self::OntologyAnnotation => {
                counts
                    == (RootCounts {
                        ontology_annotations: 1,
                        ..RootCounts::default()
                    })
            }
            Self::Assertion => {
                counts
                    == (RootCounts {
                        annotation_assertions: 1,
                        ..RootCounts::default()
                    })
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LocalAnnotationRule {
    root_kind: u8,
    tag: u16,
    kind: LocalAnnotationRuleKind,
    constructor: &'static str,
    field_count: usize,
    annotation_field_offset: usize,
    annotation_error: &'static str,
    retain_counter_by_mode: [bool; 3],
    supports_literal_projection: bool,
    scope_policy: LocalAnonymousScopePolicy,
}

const LOCAL_ANNOTATION_RULES: [LocalAnnotationRule; 2] = [
    LocalAnnotationRule {
        root_kind: ROOT_ONTOLOGY_ANNOTATION,
        tag: TAG_ANNOTATION,
        kind: LocalAnnotationRuleKind::OntologyAnnotation,
        constructor: "ontology Annotation",
        field_count: 3,
        annotation_field_offset: 2,
        annotation_error: "must have no nested annotations",
        retain_counter_by_mode: [true, true, true],
        supports_literal_projection: false,
        scope_policy: LocalAnonymousScopePolicy::RejectWithoutRemap,
    },
    LocalAnnotationRule {
        root_kind: ROOT_AXIOM,
        tag: TAG_ANNOTATION_ASSERTION,
        kind: LocalAnnotationRuleKind::Assertion,
        constructor: "AnnotationAssertion",
        field_count: 4,
        annotation_field_offset: 3,
        annotation_error: "must be unannotated",
        retain_counter_by_mode: [true, true, true],
        supports_literal_projection: false,
        scope_policy: LocalAnonymousScopePolicy::RejectWithoutRemap,
    },
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LocalAnnotationRulePlan {
    rule: LocalAnnotationRule,
    retain_counter: bool,
}

impl LocalAnnotationRulePlan {
    fn classify(
        counts: RootCounts,
        root_kind: u8,
        tag: u16,
        context: LocalRuleContext,
    ) -> Option<Self> {
        LOCAL_ANNOTATION_RULES
            .iter()
            .copied()
            .find(|rule| {
                rule.root_kind == root_kind
                    && rule.tag == tag
                    && rule.kind.matches_single_root(counts)
            })
            .map(|rule| Self {
                rule,
                retain_counter: rule.retain_counter_by_mode[context.mode.index()],
            })
    }

    fn validate(
        self,
        columns: DirectColumns<'_>,
        root: usize,
        context: LocalRuleContext,
        state: &AtomicU8,
    ) -> Result<(), KernelError> {
        if context.include_literals && !self.rule.supports_literal_projection {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {} root requires literal projection to remain disabled",
                self.rule.constructor,
            )));
        }
        let field_start = columns.exact_fields(root, self.rule.field_count)?;
        let annotation_field = field_start
            .checked_add(self.rule.annotation_field_offset)
            .ok_or_else(|| {
                KernelError::resource("encoded local annotation field offset overflow")
            })?;
        let (_annotation_start, annotation_count) = columns.node_set_range(annotation_field, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {} root {}",
                self.rule.constructor, self.rule.annotation_error,
            )));
        }
        self.rule
            .scope_policy
            .validate(columns, root, context, self.rule.constructor, state)
    }

    fn apply_statistics(self, statistics: &mut DirectCompileStats) -> Result<(), KernelError> {
        if !self.retain_counter {
            return Ok(());
        }
        match self.rule.kind {
            LocalAnnotationRuleKind::OntologyAnnotation => {
                statistics.ontology_annotations = statistics
                    .ontology_annotations
                    .checked_add(1)
                    .ok_or_else(|| {
                        KernelError::resource("encoded ontology-annotation count overflow")
                    })?;
            }
            LocalAnnotationRuleKind::Assertion => {
                statistics.annotation_assertions = statistics
                    .annotation_assertions
                    .checked_add(1)
                    .ok_or_else(|| {
                        KernelError::resource("encoded annotation-assertion count overflow")
                    })?;
                statistics.selected_annotation_assertions = statistics
                    .selected_annotation_assertions
                    .checked_add(1)
                    .ok_or_else(|| {
                        KernelError::resource(
                            "encoded selected-annotation-assertion count overflow",
                        )
                    })?;
            }
        }
        Ok(())
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
    source_order: u8,
}

#[derive(Debug, Default, Eq, PartialEq)]
pub(crate) struct OwnedRoleState {
    subroles: Vec<(String, Vec<String>)>,
    inverses: Vec<(String, String)>,
}

pub(crate) type OwnedRoleSnapshot = (Vec<(String, Vec<String>)>, Vec<(String, String)>);

impl OwnedRoleState {
    pub(crate) fn subrole_count(&self) -> usize {
        self.subroles.len()
    }

    pub(crate) fn inverse_count(&self) -> usize {
        self.inverses.len()
    }

    pub(crate) fn snapshot(&self) -> Result<OwnedRoleSnapshot, KernelError> {
        let mut subroles = Vec::new();
        subroles
            .try_reserve_exact(self.subroles.len())
            .map_err(|_| KernelError::resource("encoded retained subrole snapshot failed"))?;
        for (property, retained_values) in &self.subroles {
            let mut values = Vec::new();
            values
                .try_reserve_exact(retained_values.len())
                .map_err(|_| KernelError::resource("encoded retained subrole snapshot failed"))?;
            for value in retained_values {
                values.push(clone_text(value)?);
            }
            subroles.push((clone_text(property)?, values));
        }

        let mut inverses = Vec::new();
        inverses
            .try_reserve_exact(self.inverses.len())
            .map_err(|_| KernelError::resource("encoded retained inverse snapshot failed"))?;
        for (property, inverse) in &self.inverses {
            inverses.push((clone_text(property)?, clone_text(inverse)?));
        }
        Ok((subroles, inverses))
    }

    fn try_clone(&self) -> Result<Self, KernelError> {
        let (subroles, inverses) = self.snapshot()?;
        Ok(Self { subroles, inverses })
    }

    fn subroles_for(&self, property: &str) -> &[String] {
        self.subroles
            .iter()
            .find_map(|(key, values)| (key == property).then_some(values.as_slice()))
            .unwrap_or_default()
    }

    fn inverse_for(&self, property: &str) -> Option<&str> {
        self.inverses
            .iter()
            .find_map(|(key, inverse)| (key == property).then_some(inverse.as_str()))
    }

    fn edge_count(&self, property: &str) -> Result<usize, KernelError> {
        1_usize
            .checked_add(self.subroles_for(property).len())
            .and_then(|count| count.checked_add(usize::from(self.inverse_for(property).is_some())))
            .ok_or_else(|| KernelError::resource("encoded role-expansion edge-count overflow"))
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

#[derive(Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd)]
enum EmissionPhase {
    #[default]
    Subclasses,
    Equivalents,
    Annotations,
    ClassAssertions,
    ObjectAssertions,
    DomainRanges,
    Finished,
}

#[derive(Debug, Eq, PartialEq)]
enum PendingExpansion {
    Taxonomy {
        source: String,
        destination: String,
        next_direction: usize,
        bidirectional: bool,
    },
    Role {
        source: String,
        relation: String,
        destination: String,
        next_relation: usize,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum AggregatePhase {
    Named,
    Restrictions {
        tag_index: usize,
        item_offset: usize,
    },
}

#[derive(Debug, Eq, PartialEq)]
struct EquivalentAggregateCursor {
    source: String,
    expression_id: usize,
    phase: AggregatePhase,
    previous_named: Option<(String, usize)>,
}

#[derive(Debug, Default, Eq, PartialEq)]
pub(crate) struct DirectEmissionCursor {
    phase: EmissionPhase,
    scan_index: usize,
    overlay_delta_index: usize,
    pending: Option<PendingExpansion>,
    aggregate: Option<EquivalentAggregateCursor>,
    previous_property: Option<String>,
    active_property: Option<String>,
    current_domain: Option<String>,
    domain_index: usize,
    range_index: usize,
    emitted: usize,
}

#[derive(Debug, Eq, PartialEq)]
enum OwnedOverlayDeltaProjection {
    Taxonomy {
        source: String,
        destination: String,
    },
    Restriction {
        source: String,
        relation: String,
        destination: String,
    },
    ClassAssertion {
        individual: String,
        class: String,
    },
    ObjectPropertyAssertion {
        source: String,
        relation: String,
        destination: String,
    },
}

impl OwnedOverlayDeltaProjection {
    fn phase(&self) -> EmissionPhase {
        match self {
            Self::Taxonomy { .. } | Self::Restriction { .. } => EmissionPhase::Subclasses,
            Self::ClassAssertion { .. } => EmissionPhase::ClassAssertions,
            Self::ObjectPropertyAssertion { .. } => EmissionPhase::ObjectAssertions,
        }
    }

    fn apply_statistics(&self, statistics: &mut DirectCompileStats) -> Result<(), KernelError> {
        match self {
            Self::Taxonomy { .. } => {
                statistics.subclasses = statistics
                    .subclasses
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded subclass-count overflow"))?;
            }
            Self::Restriction { .. } => {
                statistics.subclasses = statistics
                    .subclasses
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded subclass-count overflow"))?;
                statistics.restriction_subclasses = statistics
                    .restriction_subclasses
                    .checked_add(1)
                    .ok_or_else(|| {
                        KernelError::resource("encoded restriction-subclass count overflow")
                    })?;
            }
            Self::ClassAssertion { .. } => {
                statistics.class_assertions =
                    statistics.class_assertions.checked_add(1).ok_or_else(|| {
                        KernelError::resource("encoded class-assertion count overflow")
                    })?;
            }
            Self::ObjectPropertyAssertion { .. } => {
                statistics.object_property_assertions = statistics
                    .object_property_assertions
                    .checked_add(1)
                    .ok_or_else(|| {
                        KernelError::resource("encoded object-property-assertion count overflow")
                    })?;
            }
        }
        Ok(())
    }
}

#[derive(Debug, Eq, PartialEq)]
struct OwnedOverlayDelta {
    projection: OwnedOverlayDeltaProjection,
    insertion_scan_index: usize,
    local_canonical_index: usize,
}

fn canonicalize_overlay_delta_plan(plan: &mut [OwnedOverlayDelta]) {
    plan.sort_unstable_by_key(|delta| {
        (
            delta.projection.phase(),
            delta.insertion_scan_index,
            delta.local_canonical_index,
        )
    });
}

#[derive(Debug, Eq, PartialEq)]
struct OwnedLocalObjectPropertyClass {
    kind: ObjectPropertyClassRuleKind,
    property: String,
    class: String,
    insertion_position: usize,
}

#[derive(Debug)]
struct DirectPreparation {
    role_state: OwnedRoleState,
    anonymous_ids: AnonymousIds,
    selected_annotation_nodes: Option<Vec<usize>>,
    overlay_deltas: Vec<OwnedOverlayDelta>,
    local_object_property_classes: [Option<OwnedLocalObjectPropertyClass>; 2],
    options: DirectCompileOptions,
    statistics: DirectCompileStats,
    #[cfg(test)]
    emission_attempts: AtomicUsize,
}

#[derive(Debug)]
pub(crate) struct PreparedDirectBatches {
    preparation: DirectPreparation,
    cursor: DirectEmissionCursor,
}

#[derive(Clone, Copy)]
pub(crate) struct DirectColumns<'a> {
    root_kinds: &'a [u8],
    root_ids: &'a [u8],
    included_root_ids: &'a [u8],
    excluded_root_ids: &'a [u8],
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
            included_root_ids: &[],
            excluded_root_ids: &[],
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

    pub(crate) fn with_excluded_root_ids(mut self, excluded_root_ids: &'a [u8]) -> Self {
        self.excluded_root_ids = excluded_root_ids;
        self
    }

    pub(crate) fn with_included_root_ids(mut self, included_root_ids: &'a [u8]) -> Self {
        self.included_root_ids = included_root_ids;
        self
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

    fn selected_root_count(self) -> Result<usize, KernelError> {
        if !self.included_root_ids.is_empty() {
            return Ok(self.included_root_ids.len() / 4);
        }
        self.root_count()
            .checked_sub(self.excluded_root_ids.len() / 4)
            .ok_or_else(|| {
                KernelError::malformed("encoded excluded-root postings exceed the root count")
            })
    }

    fn excluded_root_position(self, index: usize) -> Result<usize, KernelError> {
        usize::try_from(read_u32(
            self.excluded_root_ids,
            index,
            "excluded_root_ids",
        )?)
        .map_err(|_| KernelError::malformed("excluded root position does not fit usize"))
    }

    fn included_root_position(self, index: usize) -> Result<usize, KernelError> {
        usize::try_from(read_u32(
            self.included_root_ids,
            index,
            "included_root_ids",
        )?)
        .map_err(|_| KernelError::malformed("included root position does not fit usize"))
    }

    fn root_is_selected(self, index: usize) -> Result<bool, KernelError> {
        let target = index
            .checked_add(1)
            .ok_or_else(|| KernelError::resource("encoded root position overflow"))?;
        let (postings, included) = if self.included_root_ids.is_empty() {
            (self.excluded_root_ids, false)
        } else {
            (self.included_root_ids, true)
        };
        let mut start = 0_usize;
        let mut end = postings.len() / 4;
        while start < end {
            let middle = start + (end - start) / 2;
            let candidate = if included {
                self.included_root_position(middle)?
            } else {
                self.excluded_root_position(middle)?
            };
            if candidate < target {
                start = middle + 1;
            } else {
                end = middle;
            }
        }
        if start == postings.len() / 4 {
            return Ok(!included);
        }
        let found = if included {
            self.included_root_position(start)? == target
        } else {
            self.excluded_root_position(start)? == target
        };
        Ok(if included { found } else { !found })
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

    fn root_contains_anonymous_individual(
        self,
        root: usize,
        state: &AtomicU8,
    ) -> Result<bool, KernelError> {
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
        queue_reachable_node(&mut stack, &mut reachable, root)?;
        while let Some(node_id) = stack.pop() {
            check_cancel(state, node_id)?;
            if self.node_tag(node_id)? == TAG_ANONYMOUS_INDIVIDUAL {
                return Ok(true);
            }
            let (start, end) = self.field_range(node_id)?;
            for field_index in start..end {
                let kind = self.field_kind(field_index)?;
                if kind == COMPONENT_NODE {
                    queue_reachable_node(
                        &mut stack,
                        &mut reachable,
                        self.field_node(field_index)?,
                    )?;
                    continue;
                }
                if ![COMPONENT_SET, COMPONENT_SEQUENCE].contains(&kind) {
                    continue;
                }
                let item_start = self.field_value(field_index)?;
                let length = self.field_length(field_index)?;
                for item_index in item_start..item_start + length {
                    if self.item_kinds[item_index] == COMPONENT_NODE {
                        queue_reachable_node(
                            &mut stack,
                            &mut reachable,
                            self.item_node(item_index)?,
                        )?;
                    }
                }
            }
        }
        Ok(false)
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
            if !self.root_is_selected(root_index)? {
                continue;
            }
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

    /// Reject cycles in nested annotation metadata without recursive Rust calls.
    ///
    /// Structural-columns snapshots originate from immutable OWL values, so an
    /// Annotation can share nested metadata but cannot contain itself, directly
    /// or transitively.  Hostile columns can forge such a cycle while preserving
    /// every local tag and arity invariant; validate the complete graph before
    /// any edge vector is allocated or root-provenance identities are compared.
    fn validate_annotation_graph(self, state: &AtomicU8) -> Result<(), KernelError> {
        let mut has_nested_annotations = false;
        for node_id in 1..=self.node_count() {
            check_cancel(state, node_id)?;
            if self.node_tag(node_id)? != TAG_ANNOTATION {
                continue;
            }
            let start = self.exact_fields(node_id, 3)?;
            let (_item_start, length) = self.node_set_range(start + 2, 0)?;
            if length != 0 {
                has_nested_annotations = true;
                break;
            }
        }
        if !has_nested_annotations {
            return Ok(());
        }

        let color_length = self.node_count().checked_add(1).ok_or_else(|| {
            KernelError::resource("encoded annotation graph color length overflow")
        })?;
        let mut colors = Vec::new();
        colors.try_reserve_exact(color_length).map_err(|_| {
            KernelError::resource("encoded annotation graph color allocation failed")
        })?;
        colors.resize(color_length, 0_u8);
        let mut stack = Vec::new();
        let mut work_index = 0_usize;

        for start_id in 1..=self.node_count() {
            if self.node_tag(start_id)? != TAG_ANNOTATION || colors[start_id] == 2 {
                continue;
            }
            queue_annotation_event(&mut stack, start_id, false)?;
            while let Some((node_id, exiting)) = stack.pop() {
                check_cancel(state, work_index)?;
                work_index = work_index.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded annotation graph traversal overflow")
                })?;
                if exiting {
                    colors[node_id] = 2;
                    continue;
                }
                match colors[node_id] {
                    2 => continue,
                    1 => {
                        return Err(KernelError::malformed(
                            "encoded annotation metadata graph is cyclic",
                        ));
                    }
                    _ => {}
                }
                colors[node_id] = 1;
                queue_annotation_event(&mut stack, node_id, true)?;
                let start = self.exact_fields(node_id, 3)?;
                let (item_start, length) = self.node_set_range(start + 2, 0)?;
                for item_index in (item_start..item_start + length).rev() {
                    let child = self.item_node(item_index)?;
                    if self.node_tag(child)? != TAG_ANNOTATION {
                        return Err(KernelError::malformed(
                            "encoded annotation set item does not reference an Annotation",
                        ));
                    }
                    if colors[child] == 1 {
                        return Err(KernelError::malformed(
                            "encoded annotation metadata graph is cyclic",
                        ));
                    }
                    if colors[child] == 0 {
                        queue_annotation_event(&mut stack, child, false)?;
                    }
                }
            }
        }
        Ok(())
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

    fn role_axiom_row(
        self,
        node_id: usize,
        expected_tag: u16,
        maximum: usize,
        canonical_order: usize,
        source_order: u8,
    ) -> Result<RoleAxiom<'a>, KernelError> {
        let (first, second, annotation_hash) =
            self.role_axiom_parts(node_id, expected_tag, maximum)?;
        let owlapi_hash = if expected_tag == TAG_SUB_OBJECT_PROPERTY_OF {
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
        Ok(RoleAxiom {
            tag: expected_tag,
            first: first.iri,
            second: second.iri,
            spread: unsigned ^ (unsigned >> 16),
            canonical_order,
            source_order,
        })
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
            ("included_root_ids", 4, self.included_root_ids),
            ("excluded_root_ids", 4, self.excluded_root_ids),
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
        if !self.included_root_ids.is_empty() && !self.excluded_root_ids.is_empty() {
            return Err(KernelError::malformed(
                "encoded root selection cannot combine INCLUDE and EXCLUDE postings",
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
        let mut previous_included_root = 0_usize;
        for index in 0..self.included_root_ids.len() / 4 {
            check_cancel(state, index)?;
            let root_position = self.included_root_position(index)?;
            if root_position <= previous_included_root || root_position > self.root_count() {
                return Err(KernelError::malformed(
                    "encoded included-root postings are not sorted unique in-range positions",
                ));
            }
            previous_included_root = root_position;
        }
        let mut previous_excluded_root = 0_usize;
        for index in 0..self.excluded_root_ids.len() / 4 {
            check_cancel(state, self.included_root_ids.len() / 4 + index)?;
            let root_position = self.excluded_root_position(index)?;
            if root_position <= previous_excluded_root || root_position > self.root_count() {
                return Err(KernelError::malformed(
                    "encoded excluded-root postings are not sorted unique in-range positions",
                ));
            }
            previous_excluded_root = root_position;
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
        self.validate_annotation_graph(state)?;
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
            if !self.root_is_selected(index)? {
                continue;
            }
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
                    if self
                        .object_property_class_projection(
                            node_id,
                            TAG_OBJECT_PROPERTY_DOMAIN,
                            maximum_iri,
                        )?
                        .is_none()
                    {
                        counts.ignored_object_property_domains += 1;
                    }
                }
                (ROOT_AXIOM, TAG_OBJECT_PROPERTY_RANGE) => {
                    counts.object_property_ranges += 1;
                    if self
                        .object_property_class_projection(
                            node_id,
                            TAG_OBJECT_PROPERTY_RANGE,
                            maximum_iri,
                        )?
                        .is_none()
                    {
                        counts.ignored_object_property_ranges += 1;
                    }
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
        local_axiom: Option<RoleAxiom<'a>>,
    ) -> Result<RoleState<'a>, KernelError> {
        let role_axiom_count = counts
            .role_axioms()?
            .checked_add(usize::from(local_axiom.is_some()))
            .ok_or_else(|| KernelError::resource("encoded role-axiom count overflow"))?;
        let mut rows = Vec::new();
        rows.try_reserve_exact(role_axiom_count)
            .map_err(|_| KernelError::resource("encoded role-row allocation failed"))?;
        for canonical_order in 0..self.root_count() {
            check_cancel(state, canonical_order)?;
            if !self.root_is_selected(canonical_order)? {
                continue;
            }
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
            rows.push(self.role_axiom_row(node_id, tag, maximum_iri, canonical_order, 1)?);
        }
        if let Some(local_axiom) = local_axiom {
            rows.push(local_axiom);
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
                row.source_order,
            )
        });
        let local_subrole_axioms =
            usize::from(local_axiom.is_some_and(|axiom| axiom.tag == TAG_SUB_OBJECT_PROPERTY_OF));
        let local_inverse_axioms = usize::from(
            local_axiom.is_some_and(|axiom| axiom.tag == TAG_INVERSE_OBJECT_PROPERTIES),
        );
        let mut role_state = RoleState::with_capacity(
            retained,
            counts
                .sub_object_properties
                .checked_sub(counts.object_property_chains)
                .and_then(|count| count.checked_add(local_subrole_axioms))
                .ok_or_else(|| KernelError::malformed("encoded role counters are inconsistent"))?,
            counts
                .inverse_object_properties
                .checked_add(local_inverse_axioms)
                .ok_or_else(|| KernelError::resource("encoded inverse-role capacity overflow"))?,
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
            if !self.root_is_selected(root_index)? {
                continue;
            }
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
                                    counts.ignored_shapes =
                                        counts.ignored_shapes.checked_add(1).ok_or_else(|| {
                                            KernelError::resource(
                                                "encoded equivalent ignored-shape count overflow",
                                            )
                                        })?;
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
                            tag if is_nonprojecting_class_tag(tag) || is_aggregate_tag(tag) => {
                                if !only_taxonomy {
                                    counts.ignored_shapes =
                                        counts.ignored_shapes.checked_add(1).ok_or_else(|| {
                                            KernelError::resource(
                                                "encoded equivalent ignored-shape count overflow",
                                            )
                                        })?;
                                }
                            }
                            _ => {
                                return Err(KernelError::malformed(
                                    "encoded aggregate operand changed after successful preflight",
                                ));
                            }
                        }
                    }
                }
                EquivalentProjection::Ignored => {
                    counts.ignored_shapes =
                        counts.ignored_shapes.checked_add(1).ok_or_else(|| {
                            KernelError::resource("encoded equivalent ignored-shape count overflow")
                        })?;
                }
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
            if !self.root_is_selected(root_index)? {
                continue;
            }
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

    fn selected_annotation_edge_counts(
        self,
        selected_roots: &[usize],
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<AnnotationEdgeCounts, KernelError> {
        let mut counts = AnnotationEdgeCounts::default();
        for (index, node_id) in selected_roots.iter().copied().enumerate() {
            check_cancel(state, index)?;
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

    fn scalar_range(self, start: usize, length: usize) -> Result<&'a [u8], KernelError> {
        let end = start
            .checked_add(length)
            .ok_or_else(|| KernelError::malformed("encoded scalar range overflow"))?;
        self.scalar_bytes
            .get(start..end)
            .ok_or_else(|| KernelError::malformed("encoded scalar range is out of bounds"))
    }

    /// Compare one canonical node across independently encoded direct tables.
    ///
    /// Node IDs and arena offsets are table-local, so provenance cannot compare
    /// integer columns directly.  The iterative walk compares constructor tags,
    /// component kinds, scalar payloads, collection order, and recursively
    /// referenced nodes without recursion or materializing model values.
    fn structurally_equal_node(
        self,
        left_node: usize,
        other: DirectColumns<'_>,
        right_node: usize,
        state: &AtomicU8,
    ) -> Result<bool, KernelError> {
        let mut pending = Vec::new();
        pending
            .try_reserve(1)
            .map_err(|_| KernelError::resource("encoded provenance walk allocation failed"))?;
        pending.push((left_node, right_node));
        let mut visited = Vec::new();

        while let Some((left, right)) = pending.pop() {
            check_cancel(state, visited.len())?;
            if visited.contains(&(left, right)) {
                continue;
            }
            visited
                .try_reserve(1)
                .map_err(|_| KernelError::resource("encoded provenance index allocation failed"))?;
            visited.push((left, right));

            if self.node_tag(left)? != other.node_tag(right)? {
                return Ok(false);
            }
            let (left_start, left_end) = self.field_range(left)?;
            let (right_start, right_end) = other.field_range(right)?;
            if left_end - left_start != right_end - right_start {
                return Ok(false);
            }

            for offset in 0..left_end - left_start {
                let left_field = left_start + offset;
                let right_field = right_start + offset;
                let kind = self.field_kind(left_field)?;
                if kind != other.field_kind(right_field)? {
                    return Ok(false);
                }
                let left_value = self.field_value(left_field)?;
                let right_value = other.field_value(right_field)?;
                let left_length = self.field_length(left_field)?;
                let right_length = other.field_length(right_field)?;
                if left_length != right_length {
                    return Ok(false);
                }
                match kind {
                    COMPONENT_NONE => {
                        if left_value != 0 || right_value != 0 {
                            return Ok(false);
                        }
                    }
                    COMPONENT_NODE => {
                        let pair = (
                            self.checked_node_id(left_value)?,
                            other.checked_node_id(right_value)?,
                        );
                        if !visited.contains(&pair) && !pending.contains(&pair) {
                            pending.try_reserve(1).map_err(|_| {
                                KernelError::resource("encoded provenance walk allocation failed")
                            })?;
                            pending.push(pair);
                        }
                    }
                    COMPONENT_TEXT | COMPONENT_BYTES | COMPONENT_INTEGER | COMPONENT_ENUM => {
                        if self.scalar_range(left_value, left_length)?
                            != other.scalar_range(right_value, right_length)?
                        {
                            return Ok(false);
                        }
                    }
                    COMPONENT_SET | COMPONENT_SEQUENCE => {
                        for item_offset in 0..left_length {
                            let left_item = left_value + item_offset;
                            let right_item = right_value + item_offset;
                            let item_kind = *self.item_kinds.get(left_item).ok_or_else(|| {
                                KernelError::malformed(
                                    "encoded provenance collection item is out of range",
                                )
                            })?;
                            let other_kind =
                                *other.item_kinds.get(right_item).ok_or_else(|| {
                                    KernelError::malformed(
                                        "encoded provenance collection item is out of range",
                                    )
                                })?;
                            if item_kind != other_kind {
                                return Ok(false);
                            }
                            let left_item_value =
                                read_usize(self.item_values, left_item, "item_values")?;
                            let right_item_value =
                                read_usize(other.item_values, right_item, "item_values")?;
                            let left_item_length =
                                read_usize(self.item_lengths, left_item, "item_lengths")?;
                            let right_item_length =
                                read_usize(other.item_lengths, right_item, "item_lengths")?;
                            if left_item_length != right_item_length {
                                return Ok(false);
                            }
                            match item_kind {
                                COMPONENT_NONE => {
                                    if left_item_value != 0 || right_item_value != 0 {
                                        return Ok(false);
                                    }
                                }
                                COMPONENT_NODE => {
                                    let pair = (
                                        self.checked_node_id(left_item_value)?,
                                        other.checked_node_id(right_item_value)?,
                                    );
                                    if !visited.contains(&pair) && !pending.contains(&pair) {
                                        pending.try_reserve(1).map_err(|_| {
                                            KernelError::resource(
                                                "encoded provenance walk allocation failed",
                                            )
                                        })?;
                                        pending.push(pair);
                                    }
                                }
                                COMPONENT_TEXT | COMPONENT_BYTES | COMPONENT_INTEGER
                                | COMPONENT_ENUM => {
                                    if self.scalar_range(left_item_value, left_item_length)?
                                        != other
                                            .scalar_range(right_item_value, right_item_length)?
                                    {
                                        return Ok(false);
                                    }
                                }
                                _ => {
                                    return Err(KernelError::malformed(
                                        "encoded provenance collection item kind is invalid",
                                    ));
                                }
                            }
                        }
                    }
                    _ => {
                        return Err(KernelError::malformed(
                            "encoded provenance component kind is invalid",
                        ));
                    }
                }
            }
        }
        Ok(true)
    }

    /// Resolve root-document annotations to their canonical closure nodes.
    ///
    /// Both root lists are canonical and unique.  Advancing the closure cursor
    /// after each match therefore preserves encounter order while rejecting a
    /// forged root selection that is not a subset of the closure.
    fn select_root_annotation_nodes(
        self,
        root_columns: DirectColumns<'_>,
        state: &AtomicU8,
    ) -> Result<Vec<usize>, KernelError> {
        let mut selected = Vec::new();
        let mut closure_index = 0_usize;
        for root_index in 0..root_columns.root_count() {
            check_cancel(state, root_index)?;
            if !root_columns.root_is_selected(root_index)? {
                continue;
            }
            if root_columns.root_kind(root_index)? != ROOT_AXIOM {
                continue;
            }
            let root_node = root_columns.root_id(root_index)?;
            if root_columns.node_tag(root_node)? != TAG_ANNOTATION_ASSERTION {
                continue;
            }

            let mut matched = None;
            while closure_index < self.root_count() {
                let candidate_index = closure_index;
                closure_index += 1;
                if !self.root_is_selected(candidate_index)? {
                    continue;
                }
                if self.root_kind(candidate_index)? != ROOT_AXIOM {
                    continue;
                }
                let candidate = self.root_id(candidate_index)?;
                if self.node_tag(candidate)? == TAG_ANNOTATION_ASSERTION
                    && self.structurally_equal_node(candidate, root_columns, root_node, state)?
                {
                    matched = Some(candidate);
                    break;
                }
            }
            let candidate = matched.ok_or_else(|| {
                KernelError::malformed(
                    "encoded root annotation assertion is absent from the closure selection",
                )
            })?;
            selected.try_reserve(1).map_err(|_| {
                KernelError::resource("encoded root annotation selection allocation failed")
            })?;
            selected.push(candidate);
        }
        Ok(selected)
    }

    fn next_named_aggregate_operand<'b>(
        self,
        expression_id: usize,
        after: Option<(&'b str, usize)>,
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

    fn restriction_role_edge_count(
        self,
        role_state: &RoleState<'a>,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<usize, KernelError> {
        let mut count = 0_usize;
        for index in 0..self.root_count() {
            check_cancel(state, index)?;
            if !self.root_is_selected(index)? {
                continue;
            }
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
            if !self.root_is_selected(domain_index)? {
                continue;
            }
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
                if !self.root_is_selected(range_index)? {
                    continue;
                }
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
            if !self.root_is_selected(domain_index)? {
                continue;
            }
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
                || !self.has_object_property_class_for_property(
                    TAG_OBJECT_PROPERTY_RANGE,
                    property,
                    maximum_iri,
                    state,
                )?
            {
                continue;
            }
            next = Some(property);
        }
        Ok(next)
    }

    fn has_object_property_class_for_property(
        self,
        expected_tag: u16,
        property: &str,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<bool, KernelError> {
        if ![TAG_OBJECT_PROPERTY_DOMAIN, TAG_OBJECT_PROPERTY_RANGE].contains(&expected_tag) {
            return Err(KernelError::malformed(
                "encoded object-property class match has the wrong constructor tag",
            ));
        }
        for root_index in 0..self.root_count() {
            check_cancel(state, root_index)?;
            if !self.root_is_selected(root_index)? {
                continue;
            }
            let node_id = self.root_id(root_index)?;
            if self.node_tag(node_id)? != expected_tag {
                continue;
            }
            let Some((candidate, _class)) =
                self.object_property_class_projection(node_id, expected_tag, maximum_iri)?
            else {
                continue;
            };
            if candidate == property {
                return Ok(true);
            }
        }
        Ok(false)
    }

    fn object_property_class_match_count(
        self,
        expected_tag: u16,
        property: &str,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<usize, KernelError> {
        if ![TAG_OBJECT_PROPERTY_DOMAIN, TAG_OBJECT_PROPERTY_RANGE].contains(&expected_tag) {
            return Err(KernelError::malformed(
                "encoded object-property class count has the wrong constructor tag",
            ));
        }
        let mut count = 0_usize;
        for root_index in 0..self.root_count() {
            check_cancel(state, root_index)?;
            if !self.root_is_selected(root_index)? {
                continue;
            }
            let node_id = self.root_id(root_index)?;
            if self.node_tag(node_id)? != expected_tag {
                continue;
            }
            let Some((candidate, _class)) =
                self.object_property_class_projection(node_id, expected_tag, maximum_iri)?
            else {
                continue;
            };
            if candidate == property {
                count = count.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded object-property class count overflow")
                })?;
            }
        }
        Ok(count)
    }
}

impl DirectPreparation {
    fn next_paired_property<'a>(
        &'a self,
        columns: DirectColumns<'a>,
        after: Option<&str>,
        state: &AtomicU8,
    ) -> Result<Option<&'a str>, KernelError> {
        let base = columns.next_paired_property(after, self.options.max_iri_bytes, state)?;
        let mut local_candidate = None;
        for local in self.local_object_property_classes.iter().flatten() {
            if after.is_some_and(|previous| local.property.as_bytes() <= previous.as_bytes()) {
                continue;
            }
            let counterpart_kind = match local.kind {
                ObjectPropertyClassRuleKind::Domain => ObjectPropertyClassRuleKind::Range,
                ObjectPropertyClassRuleKind::Range => ObjectPropertyClassRuleKind::Domain,
            };
            let local_counterpart =
                self.local_object_property_classes
                    .iter()
                    .flatten()
                    .any(|candidate| {
                        candidate.kind == counterpart_kind && candidate.property == local.property
                    });
            let base_counterpart = if local_counterpart {
                false
            } else {
                let counterpart_tag = match counterpart_kind {
                    ObjectPropertyClassRuleKind::Domain => TAG_OBJECT_PROPERTY_DOMAIN,
                    ObjectPropertyClassRuleKind::Range => TAG_OBJECT_PROPERTY_RANGE,
                };
                columns.has_object_property_class_for_property(
                    counterpart_tag,
                    &local.property,
                    self.options.max_iri_bytes,
                    state,
                )?
            };
            if (local_counterpart || base_counterpart)
                && local_candidate
                    .is_none_or(|candidate: &str| local.property.as_bytes() < candidate.as_bytes())
            {
                local_candidate = Some(local.property.as_str());
            }
        }
        let local = local_candidate;
        Ok(match (base, local) {
            (Some(base), Some(local)) if local.as_bytes() < base.as_bytes() => Some(local),
            (Some(base), _) => Some(base),
            (None, local) => local,
        })
    }

    fn object_property_class_scan_len(
        &self,
        columns: DirectColumns<'_>,
    ) -> Result<usize, KernelError> {
        columns
            .root_count()
            .checked_add(self.local_object_property_classes.iter().flatten().count())
            .ok_or_else(|| KernelError::resource("encoded local domain/range scan overflow"))
    }

    fn object_property_class_at<'a>(
        &'a self,
        columns: DirectColumns<'a>,
        position: usize,
    ) -> Result<Option<(ObjectPropertyClassRuleKind, &'a str, &'a str)>, KernelError> {
        let base_count = columns.root_count();
        let local_count = self.local_object_property_classes.iter().flatten().count();
        let scan_len = base_count
            .checked_add(local_count)
            .ok_or_else(|| KernelError::resource("encoded local domain/range scan overflow"))?;
        if position >= scan_len {
            return Err(KernelError::malformed(
                "encoded domain/range scan exceeded its merged root table",
            ));
        }
        for local in self.local_object_property_classes.iter().flatten() {
            if local.insertion_position >= scan_len {
                return Err(KernelError::malformed(
                    "encoded local domain/range insertion is out of range",
                ));
            }
            if position == local.insertion_position {
                return Ok(Some((
                    local.kind,
                    local.property.as_str(),
                    local.class.as_str(),
                )));
            }
        }
        let inserted_before = self
            .local_object_property_classes
            .iter()
            .flatten()
            .filter(|local| local.insertion_position < position)
            .count();
        let base_position = position
            .checked_sub(inserted_before)
            .ok_or_else(|| KernelError::malformed("encoded local domain/range scan underflow"))?;
        if base_position >= base_count {
            return Err(KernelError::malformed(
                "encoded local domain/range scan exceeded its merged root table",
            ));
        }
        Self::base_object_property_class_at(columns, base_position, self.options.max_iri_bytes)
    }

    fn base_object_property_class_at<'a>(
        columns: DirectColumns<'a>,
        position: usize,
        maximum_iri: usize,
    ) -> Result<Option<(ObjectPropertyClassRuleKind, &'a str, &'a str)>, KernelError> {
        if !columns.root_is_selected(position)? {
            return Ok(None);
        }
        let node_id = columns.root_id(position)?;
        let tag = columns.node_tag(node_id)?;
        let kind = match tag {
            TAG_OBJECT_PROPERTY_DOMAIN => ObjectPropertyClassRuleKind::Domain,
            TAG_OBJECT_PROPERTY_RANGE => ObjectPropertyClassRuleKind::Range,
            _ => return Ok(None),
        };
        Ok(columns
            .object_property_class_projection(node_id, tag, maximum_iri)?
            .map(|(property, class)| (kind, property, class)))
    }

    fn local_object_property_class_edge_counts(
        &self,
        columns: DirectColumns<'_>,
        state: &AtomicU8,
    ) -> Result<(usize, usize), KernelError> {
        let mut products = 0_usize;
        let mut edges = 0_usize;
        for local in self.local_object_property_classes.iter().flatten() {
            let counterpart_tag = match local.kind {
                ObjectPropertyClassRuleKind::Domain => TAG_OBJECT_PROPERTY_RANGE,
                ObjectPropertyClassRuleKind::Range => TAG_OBJECT_PROPERTY_DOMAIN,
            };
            let base_products = columns.object_property_class_match_count(
                counterpart_tag,
                &local.property,
                self.options.max_iri_bytes,
                state,
            )?;
            products = products.checked_add(base_products).ok_or_else(|| {
                KernelError::resource("encoded local domain/range product-count overflow")
            })?;
            edges = edges
                .checked_add(
                    base_products
                        .checked_mul(self.role_state.edge_count(&local.property)?)
                        .ok_or_else(|| {
                            KernelError::resource("encoded local domain/range edge-count overflow")
                        })?,
                )
                .ok_or_else(|| {
                    KernelError::resource("encoded local domain/range edge-count overflow")
                })?;
        }
        for domain in self
            .local_object_property_classes
            .iter()
            .flatten()
            .filter(|local| local.kind == ObjectPropertyClassRuleKind::Domain)
        {
            for range in self
                .local_object_property_classes
                .iter()
                .flatten()
                .filter(|local| local.kind == ObjectPropertyClassRuleKind::Range)
            {
                if domain.property != range.property {
                    continue;
                }
                products = products.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded local domain/range product-count overflow")
                })?;
                edges = edges
                    .checked_add(self.role_state.edge_count(&domain.property)?)
                    .ok_or_else(|| {
                        KernelError::resource("encoded local domain/range edge-count overflow")
                    })?;
            }
        }
        Ok((products, edges))
    }
}

/// Exact cross-table canonical comparison and two-way root merging.
///
/// The adapter admits this merger only for the bounded local-overlay
/// contract. Every traversal step and allocation remains explicit so the
/// execution path never needs an unbounded canonical arena.
pub(crate) mod canonical_merge {
    use super::*;
    use std::cmp::Ordering as CanonicalOrdering;
    use std::mem::size_of;

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub(crate) struct CanonicalMergeLimits {
        pub(crate) max_work: usize,
        pub(crate) max_workspace_bytes: usize,
    }

    #[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
    pub(crate) struct CanonicalMergeReport {
        pub(crate) work: usize,
        pub(crate) workspace_bytes: usize,
        pub(crate) peak_workspace_bytes: usize,
        pub(crate) canonical_bytes_compared: usize,
        pub(crate) roots_emitted: usize,
        pub(crate) deduplicated_roots: usize,
    }

    #[derive(Debug)]
    struct CanonicalBudget {
        limits: CanonicalMergeLimits,
        report: CanonicalMergeReport,
    }

    impl CanonicalBudget {
        fn new(limits: CanonicalMergeLimits) -> Result<Self, KernelError> {
            if limits.max_work == 0 {
                return Err(KernelError::resource(
                    "encoded canonical comparison work limit must be positive",
                ));
            }
            if limits.max_workspace_bytes == 0 {
                return Err(KernelError::resource(
                    "encoded canonical comparison workspace limit must be positive",
                ));
            }
            Ok(Self {
                limits,
                report: CanonicalMergeReport::default(),
            })
        }

        fn consume(&mut self, amount: usize, state: &AtomicU8) -> Result<(), KernelError> {
            if state.load(Ordering::Acquire) == STATE_CANCELLED {
                return Err(KernelError::Cancelled);
            }
            let next =
                self.report.work.checked_add(amount).ok_or_else(|| {
                    KernelError::resource("encoded canonical work counter overflow")
                })?;
            if next > self.limits.max_work {
                return Err(KernelError::resource(format!(
                    "encoded canonical comparison requires more than {} work units",
                    self.limits.max_work
                )));
            }
            self.report.work = next;
            Ok(())
        }

        fn claim_workspace(&mut self, amount: usize) -> Result<(), KernelError> {
            let next = self
                .report
                .workspace_bytes
                .checked_add(amount)
                .ok_or_else(|| {
                    KernelError::resource("encoded canonical workspace counter overflow")
                })?;
            if next > self.limits.max_workspace_bytes {
                return Err(KernelError::resource(format!(
                    "encoded canonical comparison requires more than {} workspace bytes",
                    self.limits.max_workspace_bytes
                )));
            }
            self.report.workspace_bytes = next;
            self.report.peak_workspace_bytes = self.report.peak_workspace_bytes.max(next);
            Ok(())
        }

        fn release_workspace(&mut self, amount: usize) -> Result<(), KernelError> {
            self.report.workspace_bytes = self
                .report
                .workspace_bytes
                .checked_sub(amount)
                .ok_or_else(|| {
                    KernelError::malformed("encoded canonical workspace accounting underflow")
                })?;
            Ok(())
        }

        fn record_comparison_byte(&mut self) -> Result<(), KernelError> {
            self.report.canonical_bytes_compared = self
                .report
                .canonical_bytes_compared
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded canonical byte counter overflow"))?;
            Ok(())
        }

        fn record_root(&mut self, deduplicated: bool) -> Result<(), KernelError> {
            let roots_emitted = self
                .report
                .roots_emitted
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded merged-root counter overflow"))?;
            let deduplicated_roots = if deduplicated {
                self.report
                    .deduplicated_roots
                    .checked_add(1)
                    .ok_or_else(|| {
                        KernelError::resource("encoded deduplicated-root counter overflow")
                    })?
            } else {
                self.report.deduplicated_roots
            };
            self.report.roots_emitted = roots_emitted;
            self.report.deduplicated_roots = deduplicated_roots;
            Ok(())
        }
    }

    fn allocation_bytes<T>(capacity: usize) -> Result<usize, KernelError> {
        capacity.checked_mul(size_of::<T>()).ok_or_else(|| {
            KernelError::resource("encoded canonical allocation-byte counter overflow")
        })
    }

    fn allocate_vector<T>(
        capacity: usize,
        budget: &mut CanonicalBudget,
        message: &'static str,
    ) -> Result<(Vec<T>, usize), KernelError> {
        let requested_bytes = allocation_bytes::<T>(capacity)?;
        budget.claim_workspace(requested_bytes)?;
        let mut result = Vec::new();
        if result.try_reserve_exact(capacity).is_err() {
            budget.release_workspace(requested_bytes)?;
            return Err(KernelError::resource(message));
        }
        let actual_bytes = allocation_bytes::<T>(result.capacity())?;
        if actual_bytes > requested_bytes {
            if let Err(error) = budget.claim_workspace(actual_bytes - requested_bytes) {
                budget.release_workspace(requested_bytes)?;
                return Err(error);
            }
        } else if requested_bytes > actual_bytes {
            budget.release_workspace(requested_bytes - actual_bytes)?;
        }
        Ok((result, actual_bytes))
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    enum CanonicalComponent<'a> {
        None,
        Node(usize),
        Scalar {
            kind: u8,
            value: &'a [u8],
        },
        Integer(&'a [u8]),
        Collection {
            kind: u8,
            start: usize,
            length: usize,
        },
    }

    fn canonical_component<'a>(
        columns: DirectColumns<'a>,
        index: usize,
        item: bool,
        budget: &mut CanonicalBudget,
        state: &AtomicU8,
    ) -> Result<CanonicalComponent<'a>, KernelError> {
        let (kind, value, length) = if item {
            let kind = columns.item_kinds.get(index).copied().ok_or_else(|| {
                KernelError::malformed("encoded canonical item index is out of range")
            })?;
            (
                kind,
                read_usize(columns.item_values, index, "item_values")?,
                read_usize(columns.item_lengths, index, "item_lengths")?,
            )
        } else {
            (
                columns.field_kind(index)?,
                columns.field_value(index)?,
                columns.field_length(index)?,
            )
        };
        match kind {
            COMPONENT_NONE => {
                if value != 0 || length != 0 {
                    return Err(KernelError::malformed(
                        "encoded canonical none component is not canonical",
                    ));
                }
                Ok(CanonicalComponent::None)
            }
            COMPONENT_NODE => {
                if length != 0 {
                    return Err(KernelError::malformed(
                        "encoded canonical node component has a nonzero length",
                    ));
                }
                Ok(CanonicalComponent::Node(columns.checked_node_id(value)?))
            }
            COMPONENT_TEXT | COMPONENT_BYTES | COMPONENT_ENUM => {
                let payload = columns.scalar_range(value, length)?;
                match kind {
                    COMPONENT_TEXT => {
                        budget.consume(payload.len(), state)?;
                        std::str::from_utf8(payload).map_err(|_| {
                            KernelError::malformed("encoded canonical text component is not UTF-8")
                        })?;
                    }
                    COMPONENT_ENUM => {
                        budget.consume(payload.len(), state)?;
                        if payload.is_empty() || !payload.is_ascii() {
                            return Err(KernelError::malformed(
                                "encoded canonical enum component is not nonempty ASCII",
                            ));
                        }
                    }
                    _ => {}
                }
                Ok(CanonicalComponent::Scalar {
                    kind,
                    value: payload,
                })
            }
            COMPONENT_INTEGER => {
                let payload = columns.scalar_range(value, length)?;
                if payload.is_empty() || (payload.len() > 1 && payload.last() == Some(&0)) {
                    return Err(KernelError::malformed(
                        "encoded canonical integer is not minimally encoded",
                    ));
                }
                Ok(CanonicalComponent::Integer(payload))
            }
            COMPONENT_SET | COMPONENT_SEQUENCE if !item => {
                if value > columns.item_count()
                    || length > columns.item_count().saturating_sub(value)
                {
                    return Err(KernelError::malformed(
                        "encoded canonical collection is out of bounds",
                    ));
                }
                Ok(CanonicalComponent::Collection {
                    kind,
                    start: value,
                    length,
                })
            }
            COMPONENT_SET | COMPONENT_SEQUENCE => Err(KernelError::malformed(
                "encoded canonical sequence contains a nested collection",
            )),
            _ => Err(KernelError::malformed(
                "encoded canonical component kind is invalid",
            )),
        }
    }

    fn varint_width(mut value: usize) -> usize {
        let mut width = 1_usize;
        while value >= 0x80 {
            value >>= 7;
            width += 1;
        }
        width
    }

    fn integer_varint_chunks(value: &[u8]) -> Result<usize, KernelError> {
        let final_byte = value
            .last()
            .copied()
            .ok_or_else(|| KernelError::malformed("encoded canonical integer payload is empty"))?;
        if value.len() > 1 && final_byte == 0 {
            return Err(KernelError::malformed(
                "encoded canonical integer is not minimally encoded",
            ));
        }
        if final_byte == 0 {
            return Ok(1);
        }
        let prefix_bits = value
            .len()
            .checked_sub(1)
            .and_then(|length| length.checked_mul(8))
            .ok_or_else(|| KernelError::resource("encoded canonical integer width overflow"))?;
        let high_bits = 8_usize
            .checked_sub(final_byte.leading_zeros() as usize)
            .ok_or_else(|| KernelError::resource("encoded canonical integer width overflow"))?;
        prefix_bits
            .checked_add(high_bits)
            .and_then(|bits| bits.checked_add(6))
            .map(|bits| bits / 7)
            .ok_or_else(|| KernelError::resource("encoded canonical integer width overflow"))
    }

    fn component_length(
        columns: DirectColumns<'_>,
        lengths: &[usize],
        component: CanonicalComponent<'_>,
        budget: &mut CanonicalBudget,
        state: &AtomicU8,
    ) -> Result<usize, KernelError> {
        budget.consume(1, state)?;
        match component {
            CanonicalComponent::None => Ok(1),
            CanonicalComponent::Node(node_id) => {
                let child = lengths
                    .get(node_id)
                    .copied()
                    .filter(|length| *length != 0)
                    .ok_or_else(|| {
                        KernelError::malformed("encoded canonical child length is unavailable")
                    })?;
                1_usize
                    .checked_add(varint_width(child))
                    .and_then(|length| length.checked_add(child))
                    .ok_or_else(|| KernelError::resource("encoded canonical child length overflow"))
            }
            CanonicalComponent::Scalar { value, .. } => 1_usize
                .checked_add(varint_width(value.len()))
                .and_then(|length| length.checked_add(value.len()))
                .ok_or_else(|| KernelError::resource("encoded canonical scalar length overflow")),
            CanonicalComponent::Integer(value) => 1_usize
                .checked_add(integer_varint_chunks(value)?)
                .ok_or_else(|| KernelError::resource("encoded canonical integer length overflow")),
            CanonicalComponent::Collection {
                kind,
                start,
                length,
            } => {
                let mut total = 1_usize.checked_add(varint_width(length)).ok_or_else(|| {
                    KernelError::resource("encoded canonical collection length overflow")
                })?;
                for item_index in start..start + length {
                    budget.consume(1, state)?;
                    let item = canonical_component(columns, item_index, true, budget, state)?;
                    if kind == COMPONENT_SET {
                        let CanonicalComponent::Node(node_id) = item else {
                            return Err(KernelError::malformed(
                                "encoded canonical set contains a scalar",
                            ));
                        };
                        let child = lengths
                            .get(node_id)
                            .copied()
                            .filter(|child| *child != 0)
                            .ok_or_else(|| {
                                KernelError::malformed(
                                    "encoded canonical set child length is unavailable",
                                )
                            })?;
                        total = total
                            .checked_add(varint_width(child))
                            .and_then(|value| value.checked_add(child))
                            .ok_or_else(|| {
                                KernelError::resource("encoded canonical set length overflow")
                            })?;
                    } else {
                        total = total
                            .checked_add(component_length(columns, lengths, item, budget, state)?)
                            .ok_or_else(|| {
                                KernelError::resource("encoded canonical sequence length overflow")
                            })?;
                    }
                }
                Ok(total)
            }
        }
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    enum LengthEvent {
        Enter(usize),
        Exit(usize),
    }

    fn push_length_event(
        events: &mut Vec<LengthEvent>,
        event: LengthEvent,
    ) -> Result<(), KernelError> {
        if events.len() == events.capacity() {
            return Err(KernelError::resource(
                "encoded canonical traversal exceeded its accounted stack",
            ));
        }
        events.push(event);
        Ok(())
    }

    struct CanonicalTable<'a> {
        columns: DirectColumns<'a>,
        lengths: Vec<usize>,
    }

    impl<'a> CanonicalTable<'a> {
        fn build(
            columns: DirectColumns<'a>,
            budget: &mut CanonicalBudget,
            state: &AtomicU8,
        ) -> Result<Self, KernelError> {
            let validation_work = columns
                .root_count()
                .checked_add(columns.included_root_ids.len() / 4)
                .and_then(|value| value.checked_add(columns.excluded_root_ids.len() / 4))
                .and_then(|value| value.checked_add(columns.node_count()))
                .and_then(|value| value.checked_add(columns.field_count()))
                .and_then(|value| value.checked_add(columns.item_count()))
                .and_then(|value| value.checked_add(columns.scalar_bytes.len()))
                .and_then(|value| value.checked_add(1))
                .ok_or_else(|| {
                    KernelError::resource("encoded canonical validation-work overflow")
                })?;
            budget.consume(validation_work, state)?;
            columns.validate_generic(state)?;

            let map_capacity = columns.node_count().checked_add(1).ok_or_else(|| {
                KernelError::resource("encoded canonical length-map size overflow")
            })?;
            let event_capacity = columns
                .node_count()
                .checked_mul(2)
                .and_then(|value| value.checked_add(columns.field_count()))
                .and_then(|value| value.checked_add(columns.item_count()))
                .and_then(|value| value.checked_add(1))
                .ok_or_else(|| {
                    KernelError::resource("encoded canonical traversal-stack size overflow")
                })?;

            let (mut lengths, length_bytes) = allocate_vector::<usize>(
                map_capacity,
                budget,
                "encoded canonical length-map allocation failed",
            )?;
            let (mut colors, color_bytes) = match allocate_vector::<u8>(
                map_capacity,
                budget,
                "encoded canonical color-map allocation failed",
            ) {
                Ok(result) => result,
                Err(error) => {
                    budget.release_workspace(length_bytes)?;
                    return Err(error);
                }
            };
            let (mut events, event_bytes) = match allocate_vector::<LengthEvent>(
                event_capacity,
                budget,
                "encoded canonical traversal-stack allocation failed",
            ) {
                Ok(result) => result,
                Err(error) => {
                    budget.release_workspace(color_bytes)?;
                    budget.release_workspace(length_bytes)?;
                    return Err(error);
                }
            };
            lengths.resize(map_capacity, 0);
            colors.resize(map_capacity, 0);

            let build_result = (|| {
                for initial in 1..=columns.node_count() {
                    budget.consume(1, state)?;
                    if colors[initial] == 2 {
                        continue;
                    }
                    push_length_event(&mut events, LengthEvent::Enter(initial))?;
                    while let Some(event) = events.pop() {
                        budget.consume(1, state)?;
                        match event {
                            LengthEvent::Enter(node_id) => match colors[node_id] {
                                2 => continue,
                                1 => {
                                    return Err(KernelError::malformed(
                                        "encoded canonical comparator found a cyclic node graph",
                                    ));
                                }
                                _ => {
                                    colors[node_id] = 1;
                                    push_length_event(&mut events, LengthEvent::Exit(node_id))?;
                                    let (start, end) = columns.field_range(node_id)?;
                                    for field_index in (start..end).rev() {
                                        budget.consume(1, state)?;
                                        let component = canonical_component(
                                            columns,
                                            field_index,
                                            false,
                                            budget,
                                            state,
                                        )?;
                                        match component {
                                            CanonicalComponent::Node(child) => {
                                                match colors[child] {
                                                    1 => {
                                                        return Err(KernelError::malformed(
                                                            "encoded canonical comparator found a cyclic node graph",
                                                        ));
                                                    }
                                                    0 => push_length_event(
                                                        &mut events,
                                                        LengthEvent::Enter(child),
                                                    )?,
                                                    _ => {}
                                                }
                                            }
                                            CanonicalComponent::Collection {
                                                kind,
                                                start,
                                                length,
                                            } => {
                                                for item_index in (start..start + length).rev() {
                                                    budget.consume(1, state)?;
                                                    let item = canonical_component(
                                                        columns, item_index, true, budget, state,
                                                    )?;
                                                    if kind == COMPONENT_SET
                                                        && !matches!(
                                                            item,
                                                            CanonicalComponent::Node(_)
                                                        )
                                                    {
                                                        return Err(KernelError::malformed(
                                                            "encoded canonical set contains a scalar",
                                                        ));
                                                    }
                                                    if let CanonicalComponent::Node(child) = item {
                                                        match colors[child] {
                                                            1 => {
                                                                return Err(
                                                                    KernelError::malformed(
                                                                        "encoded canonical comparator found a cyclic node graph",
                                                                    ),
                                                                );
                                                            }
                                                            0 => push_length_event(
                                                                &mut events,
                                                                LengthEvent::Enter(child),
                                                            )?,
                                                            _ => {}
                                                        }
                                                    }
                                                }
                                            }
                                            _ => {}
                                        }
                                    }
                                }
                            },
                            LengthEvent::Exit(node_id) => {
                                let mut length =
                                    varint_width(usize::from(columns.node_tag(node_id)?));
                                let (start, end) = columns.field_range(node_id)?;
                                for field_index in start..end {
                                    length = length
                                        .checked_add(component_length(
                                            columns,
                                            &lengths,
                                            canonical_component(
                                                columns,
                                                field_index,
                                                false,
                                                budget,
                                                state,
                                            )?,
                                            budget,
                                            state,
                                        )?)
                                        .ok_or_else(|| {
                                            KernelError::resource(
                                                "encoded canonical node length overflow",
                                            )
                                        })?;
                                }
                                lengths[node_id] = length;
                                colors[node_id] = 2;
                            }
                        }
                    }
                }
                Ok(())
            })();

            budget.release_workspace(event_bytes)?;
            budget.release_workspace(color_bytes)?;
            if let Err(error) = build_result {
                budget.release_workspace(length_bytes)?;
                return Err(error);
            }
            Ok(Self { columns, lengths })
        }
    }

    #[derive(Clone, Copy, Debug)]
    enum EmitTask<'a> {
        Byte(u8),
        Varint(usize),
        Slice(&'a [u8], usize),
        Integer {
            value: &'a [u8],
            chunk: usize,
            chunks: usize,
        },
        Node(usize),
        Component {
            index: usize,
            item: bool,
        },
        Collection {
            start: usize,
            index: usize,
            length: usize,
            canonical_set: bool,
        },
    }

    struct CanonicalByteCursor<'a> {
        stack: Vec<EmitTask<'a>>,
    }

    impl<'a> CanonicalByteCursor<'a> {
        fn new() -> Self {
            Self { stack: Vec::new() }
        }

        fn reset(
            &mut self,
            node_id: usize,
            budget: &mut CanonicalBudget,
        ) -> Result<(), KernelError> {
            self.stack.clear();
            self.push(EmitTask::Node(node_id), budget)
        }

        fn push(
            &mut self,
            task: EmitTask<'a>,
            budget: &mut CanonicalBudget,
        ) -> Result<(), KernelError> {
            if self.stack.len() == self.stack.capacity() {
                let previous = self.stack.capacity();
                let target = if previous == 0 {
                    8
                } else {
                    previous.checked_mul(2).ok_or_else(|| {
                        KernelError::resource("encoded canonical cursor capacity overflow")
                    })?
                };
                let requested = allocation_bytes::<EmitTask<'a>>(target - previous)?;
                budget.claim_workspace(requested)?;
                if self.stack.try_reserve_exact(target - previous).is_err() {
                    budget.release_workspace(requested)?;
                    return Err(KernelError::resource(
                        "encoded canonical cursor allocation failed",
                    ));
                }
                let actual_growth =
                    self.stack.capacity().checked_sub(previous).ok_or_else(|| {
                        KernelError::malformed("encoded canonical cursor capacity regressed")
                    })?;
                let actual = allocation_bytes::<EmitTask<'a>>(actual_growth)?;
                if actual > requested {
                    if let Err(error) = budget.claim_workspace(actual - requested) {
                        let previous_bytes = allocation_bytes::<EmitTask<'a>>(previous)?;
                        self.stack = Vec::new();
                        budget.release_workspace(requested)?;
                        budget.release_workspace(previous_bytes)?;
                        return Err(error);
                    }
                } else if requested > actual {
                    budget.release_workspace(requested - actual)?;
                }
            }
            self.stack.push(task);
            Ok(())
        }

        fn schedule_node(
            &mut self,
            node_id: usize,
            include_marker: bool,
            lengths: &[usize],
            budget: &mut CanonicalBudget,
        ) -> Result<(), KernelError> {
            let length = lengths
                .get(node_id)
                .copied()
                .filter(|value| *value != 0)
                .ok_or_else(|| {
                    KernelError::malformed("encoded canonical cursor node length is unavailable")
                })?;
            self.push(EmitTask::Node(node_id), budget)?;
            self.push(EmitTask::Varint(length), budget)?;
            if include_marker {
                self.push(EmitTask::Byte(COMPONENT_NODE), budget)?;
            }
            Ok(())
        }

        fn schedule_component(
            &mut self,
            lengths: &[usize],
            component: CanonicalComponent<'a>,
            budget: &mut CanonicalBudget,
        ) -> Result<(), KernelError> {
            match component {
                CanonicalComponent::None => self.push(EmitTask::Byte(COMPONENT_NONE), budget),
                CanonicalComponent::Node(node_id) => {
                    self.schedule_node(node_id, true, lengths, budget)
                }
                CanonicalComponent::Scalar { kind, value } => {
                    if !value.is_empty() {
                        self.push(EmitTask::Slice(value, 0), budget)?;
                    }
                    self.push(EmitTask::Varint(value.len()), budget)?;
                    self.push(EmitTask::Byte(kind), budget)
                }
                CanonicalComponent::Integer(value) => {
                    self.push(
                        EmitTask::Integer {
                            value,
                            chunk: 0,
                            chunks: integer_varint_chunks(value)?,
                        },
                        budget,
                    )?;
                    self.push(EmitTask::Byte(COMPONENT_INTEGER), budget)
                }
                CanonicalComponent::Collection {
                    kind,
                    start,
                    length,
                } => {
                    self.push(
                        EmitTask::Collection {
                            start,
                            index: 0,
                            length,
                            canonical_set: kind == COMPONENT_SET,
                        },
                        budget,
                    )?;
                    self.push(EmitTask::Varint(length), budget)?;
                    self.push(EmitTask::Byte(kind), budget)
                }
            }
        }

        fn next_byte(
            &mut self,
            columns: DirectColumns<'a>,
            lengths: &[usize],
            budget: &mut CanonicalBudget,
            state: &AtomicU8,
        ) -> Result<Option<u8>, KernelError> {
            loop {
                let Some(task) = self.stack.pop() else {
                    return Ok(None);
                };
                budget.consume(1, state)?;
                match task {
                    EmitTask::Byte(byte) => return Ok(Some(byte)),
                    EmitTask::Varint(value) => {
                        let following = value >> 7;
                        if following != 0 {
                            self.push(EmitTask::Varint(following), budget)?;
                        }
                        return Ok(Some(
                            (value as u8 & 0x7f) | if following == 0 { 0 } else { 0x80 },
                        ));
                    }
                    EmitTask::Slice(value, index) => {
                        let byte = value.get(index).copied().ok_or_else(|| {
                            KernelError::malformed(
                                "encoded canonical cursor scalar is out of bounds",
                            )
                        })?;
                        if index + 1 < value.len() {
                            self.push(EmitTask::Slice(value, index + 1), budget)?;
                        }
                        return Ok(Some(byte));
                    }
                    EmitTask::Integer {
                        value,
                        chunk,
                        chunks,
                    } => {
                        if chunk >= chunks {
                            return Err(KernelError::malformed(
                                "encoded canonical integer cursor is out of bounds",
                            ));
                        }
                        let bit_offset = chunk.checked_mul(7).ok_or_else(|| {
                            KernelError::resource("encoded canonical integer cursor overflow")
                        })?;
                        let byte_index = bit_offset / 8;
                        let shift = bit_offset % 8;
                        let low = u16::from(*value.get(byte_index).unwrap_or(&0));
                        let high = u16::from(*value.get(byte_index + 1).unwrap_or(&0));
                        let byte = (((low | (high << 8)) >> shift) & 0x7f) as u8;
                        if chunk + 1 < chunks {
                            self.push(
                                EmitTask::Integer {
                                    value,
                                    chunk: chunk + 1,
                                    chunks,
                                },
                                budget,
                            )?;
                        }
                        return Ok(Some(byte | if chunk + 1 == chunks { 0 } else { 0x80 }));
                    }
                    EmitTask::Node(node_id) => {
                        let (start, end) = columns.field_range(node_id)?;
                        for field_index in (start..end).rev() {
                            self.push(
                                EmitTask::Component {
                                    index: field_index,
                                    item: false,
                                },
                                budget,
                            )?;
                        }
                        self.push(
                            EmitTask::Varint(usize::from(columns.node_tag(node_id)?)),
                            budget,
                        )?;
                    }
                    EmitTask::Component { index, item } => {
                        self.schedule_component(
                            lengths,
                            canonical_component(columns, index, item, budget, state)?,
                            budget,
                        )?;
                    }
                    EmitTask::Collection {
                        start,
                        index,
                        length,
                        canonical_set,
                    } => {
                        if index >= length {
                            continue;
                        }
                        self.push(
                            EmitTask::Collection {
                                start,
                                index: index + 1,
                                length,
                                canonical_set,
                            },
                            budget,
                        )?;
                        let item =
                            canonical_component(columns, start + index, true, budget, state)?;
                        if canonical_set {
                            let CanonicalComponent::Node(node_id) = item else {
                                return Err(KernelError::malformed(
                                    "encoded canonical set contains a scalar",
                                ));
                            };
                            self.schedule_node(node_id, false, lengths, budget)?;
                        } else {
                            self.schedule_component(lengths, item, budget)?;
                        }
                    }
                }
            }
        }
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    enum TableSide {
        Left,
        Right,
    }

    pub(crate) struct CanonicalNodeComparator<'a> {
        left: CanonicalTable<'a>,
        right: CanonicalTable<'a>,
        left_cursor: CanonicalByteCursor<'a>,
        right_cursor: CanonicalByteCursor<'a>,
        budget: CanonicalBudget,
    }

    impl<'a> CanonicalNodeComparator<'a> {
        pub(crate) fn new(
            left: DirectColumns<'a>,
            right: DirectColumns<'a>,
            limits: CanonicalMergeLimits,
            state: &AtomicU8,
        ) -> Result<Self, KernelError> {
            let mut budget = CanonicalBudget::new(limits)?;
            let left = CanonicalTable::build(left, &mut budget, state)?;
            let right = CanonicalTable::build(right, &mut budget, state)?;
            let mut result = Self {
                left,
                right,
                left_cursor: CanonicalByteCursor::new(),
                right_cursor: CanonicalByteCursor::new(),
                budget,
            };
            result.validate_canonical_sets(TableSide::Left, state)?;
            result.validate_canonical_sets(TableSide::Right, state)?;
            Ok(result)
        }

        fn validate_canonical_sets(
            &mut self,
            side: TableSide,
            state: &AtomicU8,
        ) -> Result<(), KernelError> {
            let columns = match side {
                TableSide::Left => self.left.columns,
                TableSide::Right => self.right.columns,
            };
            for field_index in 0..columns.field_count() {
                self.budget.consume(1, state)?;
                if columns.field_kind(field_index)? != COMPONENT_SET {
                    continue;
                }
                let CanonicalComponent::Collection {
                    kind,
                    start,
                    length,
                } = canonical_component(columns, field_index, false, &mut self.budget, state)?
                else {
                    return Err(KernelError::malformed(
                        "encoded canonical set field changed after length preflight",
                    ));
                };
                if kind != COMPONENT_SET {
                    return Err(KernelError::malformed(
                        "encoded canonical set field changed after length preflight",
                    ));
                }
                for offset in 1..length {
                    self.budget.consume(1, state)?;
                    let CanonicalComponent::Node(previous) = canonical_component(
                        columns,
                        start + offset - 1,
                        true,
                        &mut self.budget,
                        state,
                    )?
                    else {
                        return Err(KernelError::malformed(
                            "encoded canonical set contains a scalar",
                        ));
                    };
                    let CanonicalComponent::Node(current) = canonical_component(
                        columns,
                        start + offset,
                        true,
                        &mut self.budget,
                        state,
                    )?
                    else {
                        return Err(KernelError::malformed(
                            "encoded canonical set contains a scalar",
                        ));
                    };
                    if self.compare_sides(side, previous, side, current, state)?
                        != CanonicalOrdering::Less
                    {
                        return Err(KernelError::malformed(
                            "encoded canonical set items are not strictly sorted and unique",
                        ));
                    }
                }
            }
            Ok(())
        }

        #[cfg_attr(not(test), allow(dead_code))]
        pub(crate) fn compare(
            &mut self,
            left_node: usize,
            right_node: usize,
            state: &AtomicU8,
        ) -> Result<CanonicalOrdering, KernelError> {
            self.compare_sides(
                TableSide::Left,
                left_node,
                TableSide::Right,
                right_node,
                state,
            )
        }

        fn compare_sides(
            &mut self,
            left_side: TableSide,
            left_node: usize,
            right_side: TableSide,
            right_node: usize,
            state: &AtomicU8,
        ) -> Result<CanonicalOrdering, KernelError> {
            let (left_columns, left_lengths) = match left_side {
                TableSide::Left => (self.left.columns, self.left.lengths.as_slice()),
                TableSide::Right => (self.right.columns, self.right.lengths.as_slice()),
            };
            let (right_columns, right_lengths) = match right_side {
                TableSide::Left => (self.left.columns, self.left.lengths.as_slice()),
                TableSide::Right => (self.right.columns, self.right.lengths.as_slice()),
            };
            left_columns.checked_node_id(left_node)?;
            right_columns.checked_node_id(right_node)?;
            if left_side == right_side && left_node == right_node {
                return Ok(CanonicalOrdering::Equal);
            }
            self.left_cursor.reset(left_node, &mut self.budget)?;
            self.right_cursor.reset(right_node, &mut self.budget)?;
            loop {
                let left = self.left_cursor.next_byte(
                    left_columns,
                    left_lengths,
                    &mut self.budget,
                    state,
                )?;
                let right = self.right_cursor.next_byte(
                    right_columns,
                    right_lengths,
                    &mut self.budget,
                    state,
                )?;
                match (left, right) {
                    (Some(left), Some(right)) => {
                        self.budget.record_comparison_byte()?;
                        let ordering = left.cmp(&right);
                        if ordering != CanonicalOrdering::Equal {
                            return Ok(ordering);
                        }
                    }
                    (None, None) => return Ok(CanonicalOrdering::Equal),
                    (None, Some(_)) => return Ok(CanonicalOrdering::Less),
                    (Some(_), None) => return Ok(CanonicalOrdering::Greater),
                }
            }
        }

        pub(crate) fn report(&self) -> CanonicalMergeReport {
            self.budget.report
        }
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub(crate) struct CanonicalRootRef {
        pub(crate) index: usize,
        pub(crate) kind: u8,
        pub(crate) node_id: usize,
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub(crate) enum MergedCanonicalRoot {
        Left(CanonicalRootRef),
        Right(CanonicalRootRef),
        Both {
            left: CanonicalRootRef,
            right: CanonicalRootRef,
        },
    }

    pub(crate) struct CanonicalRootMerger<'a> {
        comparator: CanonicalNodeComparator<'a>,
        left_position: usize,
        right_position: usize,
        left_posting_position: usize,
        right_posting_position: usize,
    }

    impl<'a> CanonicalRootMerger<'a> {
        pub(crate) fn new(
            left: DirectColumns<'a>,
            right: DirectColumns<'a>,
            limits: CanonicalMergeLimits,
            state: &AtomicU8,
        ) -> Result<Self, KernelError> {
            let comparator = CanonicalNodeComparator::new(left, right, limits, state)?;
            let mut result = Self {
                comparator,
                left_position: 0,
                right_position: 0,
                left_posting_position: 0,
                right_posting_position: 0,
            };
            result.validate_group(TableSide::Left, state)?;
            result.validate_group(TableSide::Right, state)?;
            Ok(result)
        }

        fn validate_group(&mut self, side: TableSide, state: &AtomicU8) -> Result<(), KernelError> {
            let columns = match side {
                TableSide::Left => self.comparator.left.columns,
                TableSide::Right => self.comparator.right.columns,
            };
            let mut previous = None;
            for index in 0..columns.root_count() {
                self.comparator.budget.consume(1, state)?;
                let current = CanonicalRootRef {
                    index,
                    kind: columns.root_kind(index)?,
                    node_id: columns.root_id(index)?,
                };
                if let Some(previous) = previous {
                    let ordering = self.compare_roots(side, previous, side, current, state)?;
                    if ordering != CanonicalOrdering::Less {
                        return Err(KernelError::malformed(
                            "encoded canonical root group is not strictly sorted and unique",
                        ));
                    }
                }
                previous = Some(current);
            }
            Ok(())
        }

        fn compare_roots(
            &mut self,
            left_side: TableSide,
            left: CanonicalRootRef,
            right_side: TableSide,
            right: CanonicalRootRef,
            state: &AtomicU8,
        ) -> Result<CanonicalOrdering, KernelError> {
            self.comparator.budget.consume(1, state)?;
            let kind_order = left.kind.cmp(&right.kind);
            if kind_order != CanonicalOrdering::Equal {
                return Ok(kind_order);
            }
            self.comparator
                .compare_sides(left_side, left.node_id, right_side, right.node_id, state)
        }

        fn selected_root(
            &mut self,
            side: TableSide,
            state: &AtomicU8,
        ) -> Result<Option<CanonicalRootRef>, KernelError> {
            let (columns, position, posting_position) = match side {
                TableSide::Left => (
                    self.comparator.left.columns,
                    &mut self.left_position,
                    &mut self.left_posting_position,
                ),
                TableSide::Right => (
                    self.comparator.right.columns,
                    &mut self.right_position,
                    &mut self.right_posting_position,
                ),
            };
            let included = !columns.included_root_ids.is_empty();
            let posting_count = if included {
                columns.included_root_ids.len() / 4
            } else {
                columns.excluded_root_ids.len() / 4
            };
            while *position < columns.root_count() {
                self.comparator.budget.consume(1, state)?;
                let root_position = position.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded canonical root position overflow")
                })?;
                let mut matched = false;
                while *posting_position < posting_count {
                    self.comparator.budget.consume(1, state)?;
                    let candidate = if included {
                        columns.included_root_position(*posting_position)?
                    } else {
                        columns.excluded_root_position(*posting_position)?
                    };
                    if candidate < root_position {
                        *posting_position += 1;
                        continue;
                    }
                    if candidate == root_position {
                        if !included {
                            *posting_position += 1;
                        }
                        matched = true;
                    }
                    break;
                }
                let selected = if included { matched } else { !matched };
                if !selected {
                    *position += 1;
                    continue;
                }
                return Ok(Some(CanonicalRootRef {
                    index: *position,
                    kind: columns.root_kind(*position)?,
                    node_id: columns.root_id(*position)?,
                }));
            }
            Ok(None)
        }

        pub(crate) fn next(
            &mut self,
            state: &AtomicU8,
        ) -> Result<Option<MergedCanonicalRoot>, KernelError> {
            let left = self.selected_root(TableSide::Left, state)?;
            let right = self.selected_root(TableSide::Right, state)?;
            let result = match (left, right) {
                (None, None) => return Ok(None),
                (Some(left), None) => {
                    self.left_position += 1;
                    MergedCanonicalRoot::Left(left)
                }
                (None, Some(right)) => {
                    self.right_position += 1;
                    MergedCanonicalRoot::Right(right)
                }
                (Some(left), Some(right)) => match self.compare_roots(
                    TableSide::Left,
                    left,
                    TableSide::Right,
                    right,
                    state,
                )? {
                    CanonicalOrdering::Less => {
                        self.left_position += 1;
                        MergedCanonicalRoot::Left(left)
                    }
                    CanonicalOrdering::Greater => {
                        self.right_position += 1;
                        MergedCanonicalRoot::Right(right)
                    }
                    CanonicalOrdering::Equal => {
                        self.left_position += 1;
                        self.right_position += 1;
                        self.comparator.budget.record_root(true)?;
                        return Ok(Some(MergedCanonicalRoot::Both { left, right }));
                    }
                },
            };
            self.comparator.budget.record_root(false)?;
            Ok(Some(result))
        }

        pub(crate) fn report(&self) -> CanonicalMergeReport {
            self.comparator.report()
        }
    }
}

impl PendingExpansion {
    fn try_clone(&self) -> Result<Self, KernelError> {
        match self {
            Self::Taxonomy {
                source,
                destination,
                next_direction,
                bidirectional,
            } => Ok(Self::Taxonomy {
                source: clone_text(source)?,
                destination: clone_text(destination)?,
                next_direction: *next_direction,
                bidirectional: *bidirectional,
            }),
            Self::Role {
                source,
                relation,
                destination,
                next_relation,
            } => Ok(Self::Role {
                source: clone_text(source)?,
                relation: clone_text(relation)?,
                destination: clone_text(destination)?,
                next_relation: *next_relation,
            }),
        }
    }

    fn next_edge(
        &mut self,
        role_state: &OwnedRoleState,
    ) -> Result<Option<DirectEdge>, KernelError> {
        match self {
            Self::Taxonomy {
                source,
                destination,
                next_direction,
                bidirectional,
            } => {
                let edge = if *next_direction == 0 {
                    Some(DirectEdge {
                        source: clone_text(source)?,
                        relation: clone_text(SUBCLASS_OF)?,
                        destination: clone_text(destination)?,
                    })
                } else if *next_direction == 1 && *bidirectional {
                    Some(DirectEdge {
                        source: clone_text(destination)?,
                        relation: clone_text(SUPERCLASS_OF)?,
                        destination: clone_text(source)?,
                    })
                } else {
                    None
                };
                *next_direction = next_direction.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded taxonomy emission cursor overflow")
                })?;
                Ok(edge)
            }
            Self::Role {
                source,
                relation,
                destination,
                next_relation,
            } => {
                let subroles = role_state.subroles_for(relation);
                let edge = if *next_relation == 0 {
                    Some(DirectEdge {
                        source: clone_text(source)?,
                        relation: clone_text(relation)?,
                        destination: clone_text(destination)?,
                    })
                } else if *next_relation <= subroles.len() {
                    Some(DirectEdge {
                        source: clone_text(source)?,
                        relation: clone_text(&subroles[*next_relation - 1])?,
                        destination: clone_text(destination)?,
                    })
                } else if *next_relation == subroles.len() + 1 {
                    role_state
                        .inverse_for(relation)
                        .map(|inverse| {
                            Ok(DirectEdge {
                                source: clone_text(destination)?,
                                relation: clone_text(inverse)?,
                                destination: clone_text(source)?,
                            })
                        })
                        .transpose()?
                } else {
                    None
                };
                *next_relation = next_relation.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded role emission cursor overflow")
                })?;
                Ok(edge)
            }
        }
    }
}

impl EquivalentAggregateCursor {
    fn try_clone(&self) -> Result<Self, KernelError> {
        Ok(Self {
            source: clone_text(&self.source)?,
            expression_id: self.expression_id,
            phase: self.phase,
            previous_named: self
                .previous_named
                .as_ref()
                .map(|(value, node_id)| Ok((clone_text(value)?, *node_id)))
                .transpose()?,
        })
    }
}

impl DirectEmissionCursor {
    fn try_clone(&self) -> Result<Self, KernelError> {
        Ok(Self {
            phase: self.phase,
            scan_index: self.scan_index,
            overlay_delta_index: self.overlay_delta_index,
            pending: self
                .pending
                .as_ref()
                .map(PendingExpansion::try_clone)
                .transpose()?,
            aggregate: self
                .aggregate
                .as_ref()
                .map(EquivalentAggregateCursor::try_clone)
                .transpose()?,
            previous_property: self
                .previous_property
                .as_deref()
                .map(clone_text)
                .transpose()?,
            active_property: self
                .active_property
                .as_deref()
                .map(clone_text)
                .transpose()?,
            current_domain: self.current_domain.as_deref().map(clone_text).transpose()?,
            domain_index: self.domain_index,
            range_index: self.range_index,
            emitted: self.emitted,
        })
    }

    fn set_taxonomy(
        &mut self,
        source: &str,
        destination: &str,
        bidirectional: bool,
    ) -> Result<(), KernelError> {
        self.pending = Some(PendingExpansion::Taxonomy {
            source: clone_text(source)?,
            destination: clone_text(destination)?,
            next_direction: 0,
            bidirectional,
        });
        Ok(())
    }

    fn set_role(
        &mut self,
        source: &str,
        relation: &str,
        destination: &str,
    ) -> Result<(), KernelError> {
        self.pending = Some(PendingExpansion::Role {
            source: clone_text(source)?,
            relation: clone_text(relation)?,
            destination: clone_text(destination)?,
            next_relation: 0,
        });
        Ok(())
    }

    fn next_overlay_delta_index(
        &self,
        preparation: &DirectPreparation,
    ) -> Result<Option<usize>, KernelError> {
        let Some(delta) = preparation.overlay_deltas.get(self.overlay_delta_index) else {
            return Ok(None);
        };
        let phase = delta.projection.phase();
        if phase < self.phase || phase == self.phase && delta.insertion_scan_index < self.scan_index
        {
            return Err(KernelError::malformed(
                "encoded local-overlay plan is behind its emission cursor",
            ));
        }
        Ok(
            (phase == self.phase && delta.insertion_scan_index == self.scan_index)
                .then_some(self.overlay_delta_index),
        )
    }

    fn publish(
        &mut self,
        edge: DirectEdge,
        _preparation: &DirectPreparation,
    ) -> Result<Option<DirectEdge>, KernelError> {
        #[cfg(test)]
        _preparation
            .emission_attempts
            .fetch_add(1, Ordering::Relaxed);
        self.emitted = self
            .emitted
            .checked_add(1)
            .ok_or_else(|| KernelError::resource("encoded emitted-edge counter overflow"))?;
        Ok(Some(edge))
    }
}

impl DirectEmissionCursor {
    fn next_edge(
        &mut self,
        columns: DirectColumns<'_>,
        preparation: &DirectPreparation,
        state: &AtomicU8,
    ) -> Result<Option<DirectEdge>, KernelError> {
        loop {
            check_cancel(state, self.emitted)?;
            if let Some(pending) = &mut self.pending {
                if let Some(edge) = pending.next_edge(&preparation.role_state)? {
                    return self.publish(edge, preparation);
                }
                self.pending = None;
                continue;
            }

            match self.phase {
                EmissionPhase::Subclasses => {
                    if let Some(delta_index) = self.next_overlay_delta_index(preparation)? {
                        let delta = &preparation.overlay_deltas[delta_index];
                        let handled = match &delta.projection {
                            OwnedOverlayDeltaProjection::Taxonomy {
                                source,
                                destination,
                            } => {
                                self.overlay_delta_index =
                                    self.overlay_delta_index.checked_add(1).ok_or_else(|| {
                                        KernelError::resource(
                                            "encoded local-overlay cursor overflow",
                                        )
                                    })?;
                                self.set_taxonomy(
                                    source,
                                    destination,
                                    preparation.options.bidirectional,
                                )?;
                                true
                            }
                            OwnedOverlayDeltaProjection::Restriction {
                                source,
                                relation,
                                destination,
                            } if !preparation.options.asserted_taxonomy_only
                                && !preparation.options.only_taxonomy =>
                            {
                                self.overlay_delta_index =
                                    self.overlay_delta_index.checked_add(1).ok_or_else(|| {
                                        KernelError::resource(
                                            "encoded local-overlay cursor overflow",
                                        )
                                    })?;
                                self.set_role(source, relation, destination)?;
                                true
                            }
                            OwnedOverlayDeltaProjection::Restriction { .. } => {
                                self.overlay_delta_index =
                                    self.overlay_delta_index.checked_add(1).ok_or_else(|| {
                                        KernelError::resource(
                                            "encoded local-overlay cursor overflow",
                                        )
                                    })?;
                                true
                            }
                            OwnedOverlayDeltaProjection::ClassAssertion { .. }
                            | OwnedOverlayDeltaProjection::ObjectPropertyAssertion { .. } => false,
                        };
                        if handled {
                            continue;
                        }
                    }
                    if self.scan_index == columns.root_count() {
                        self.scan_index = 0;
                        self.phase = if preparation.options.asserted_taxonomy_only {
                            EmissionPhase::Finished
                        } else {
                            EmissionPhase::Equivalents
                        };
                        continue;
                    }
                    let index = self.scan_index;
                    self.scan_index += 1;
                    check_cancel(state, index)?;
                    if !columns.root_is_selected(index)? {
                        continue;
                    }
                    let node_id = columns.root_id(index)?;
                    if columns.node_tag(node_id)? != TAG_SUB_CLASS_OF {
                        continue;
                    }
                    match columns.subclass_projection(node_id, preparation.options.max_iri_bytes)? {
                        SubclassProjection::Taxonomy {
                            source,
                            destination,
                        } => self.set_taxonomy(
                            source,
                            destination,
                            preparation.options.bidirectional,
                        )?,
                        SubclassProjection::Restriction {
                            source,
                            relation,
                            destination,
                        } if !preparation.options.asserted_taxonomy_only
                            && !preparation.options.only_taxonomy =>
                        {
                            self.set_role(source, relation, destination)?;
                        }
                        SubclassProjection::Restriction { .. } | SubclassProjection::Ignored => {}
                    }
                }
                EmissionPhase::Equivalents => {
                    if let Some(mut aggregate) = self.aggregate.take() {
                        if aggregate.phase == AggregatePhase::Named {
                            let after = aggregate
                                .previous_named
                                .as_ref()
                                .map(|(value, node_id)| (value.as_str(), *node_id));
                            if let Some((destination, node_id)) = columns
                                .next_named_aggregate_operand(
                                    aggregate.expression_id,
                                    after,
                                    preparation.options.max_iri_bytes,
                                    state,
                                )?
                            {
                                aggregate.previous_named =
                                    Some((clone_text(destination)?, node_id));
                                self.set_taxonomy(
                                    &aggregate.source,
                                    destination,
                                    preparation.options.bidirectional,
                                )?;
                                self.aggregate = Some(aggregate);
                                continue;
                            }
                            if preparation.options.only_taxonomy {
                                continue;
                            }
                            aggregate.phase = AggregatePhase::Restrictions {
                                tag_index: 0,
                                item_offset: 0,
                            };
                        }

                        let (item_start, length) =
                            columns.aggregate_operand_range(aggregate.expression_id)?;
                        let tags = [
                            TAG_OBJECT_SOME_VALUES_FROM,
                            TAG_OBJECT_ALL_VALUES_FROM,
                            TAG_OBJECT_MIN_CARDINALITY,
                            TAG_OBJECT_MAX_CARDINALITY,
                        ];
                        let AggregatePhase::Restrictions {
                            mut tag_index,
                            mut item_offset,
                        } = aggregate.phase
                        else {
                            return Err(KernelError::malformed(
                                "encoded aggregate emission phase is inconsistent",
                            ));
                        };
                        let mut selected = None;
                        while tag_index < tags.len() && selected.is_none() {
                            while item_offset < length {
                                let item_index = item_start + item_offset;
                                item_offset += 1;
                                check_cancel(state, item_index)?;
                                let operand_id = columns.item_node(item_index)?;
                                if columns.node_tag(operand_id)? != tags[tag_index] {
                                    continue;
                                }
                                if let Some((relation, destination)) = columns
                                    .restriction_projection(
                                        operand_id,
                                        preparation.options.max_iri_bytes,
                                    )?
                                {
                                    selected = Some((relation, destination));
                                    break;
                                }
                            }
                            if selected.is_none() {
                                tag_index += 1;
                                item_offset = 0;
                            }
                        }
                        if let Some((relation, destination)) = selected {
                            aggregate.phase = AggregatePhase::Restrictions {
                                tag_index,
                                item_offset,
                            };
                            self.set_role(&aggregate.source, relation, destination)?;
                            self.aggregate = Some(aggregate);
                        }
                        continue;
                    }
                    if self.scan_index == columns.root_count() {
                        self.scan_index = 0;
                        self.phase = EmissionPhase::Annotations;
                        continue;
                    }
                    let index = self.scan_index;
                    self.scan_index += 1;
                    check_cancel(state, index)?;
                    if !columns.root_is_selected(index)? {
                        continue;
                    }
                    let node_id = columns.root_id(index)?;
                    if columns.node_tag(node_id)? != TAG_EQUIVALENT_CLASSES {
                        continue;
                    }
                    match columns
                        .equivalent_projection(node_id, preparation.options.max_iri_bytes)?
                    {
                        EquivalentProjection::Pair {
                            source,
                            destination,
                        } => self.set_taxonomy(
                            source,
                            destination,
                            preparation.options.bidirectional,
                        )?,
                        EquivalentProjection::Aggregate {
                            source,
                            expression_id,
                        } => {
                            self.aggregate = Some(EquivalentAggregateCursor {
                                source: clone_text(source)?,
                                expression_id,
                                phase: AggregatePhase::Named,
                                previous_named: None,
                            });
                        }
                        EquivalentProjection::Ignored => {}
                    }
                }
                EmissionPhase::Annotations => {
                    if !preparation.options.include_literals {
                        self.scan_index = 0;
                        self.phase = EmissionPhase::ClassAssertions;
                        continue;
                    }
                    let node_id =
                        if let Some(selected) = preparation.selected_annotation_nodes.as_deref() {
                            if self.scan_index == selected.len() {
                                self.scan_index = 0;
                                self.phase = EmissionPhase::ClassAssertions;
                                continue;
                            }
                            let node_id = selected[self.scan_index];
                            self.scan_index += 1;
                            node_id
                        } else {
                            let mut selected = None;
                            while self.scan_index < columns.root_count() {
                                let index = self.scan_index;
                                self.scan_index += 1;
                                check_cancel(state, index)?;
                                if !columns.root_is_selected(index)? {
                                    continue;
                                }
                                let candidate = columns.root_id(index)?;
                                if columns.node_tag(candidate)? == TAG_ANNOTATION_ASSERTION {
                                    selected = Some(candidate);
                                    break;
                                }
                            }
                            let Some(node_id) = selected else {
                                self.scan_index = 0;
                                self.phase = EmissionPhase::ClassAssertions;
                                continue;
                            };
                            node_id
                        };
                    if let Some(projection) = columns.annotation_projection(
                        node_id,
                        preparation.options.max_iri_bytes,
                        state,
                    )? {
                        let edge = annotation_edge(projection, &preparation.anonymous_ids)?;
                        return self.publish(edge, preparation);
                    }
                }
                EmissionPhase::ClassAssertions => {
                    if let Some(delta_index) = self.next_overlay_delta_index(preparation)? {
                        let delta = &preparation.overlay_deltas[delta_index];
                        if let OwnedOverlayDeltaProjection::ClassAssertion { individual, class } =
                            &delta.projection
                        {
                            self.overlay_delta_index =
                                self.overlay_delta_index.checked_add(1).ok_or_else(|| {
                                    KernelError::resource("encoded local-overlay cursor overflow")
                                })?;
                            return self.publish(
                                DirectEdge {
                                    source: clone_text(individual)?,
                                    relation: clone_text(RDF_TYPE)?,
                                    destination: clone_text(class)?,
                                },
                                preparation,
                            );
                        }
                    }
                    if self.scan_index == columns.root_count() {
                        self.scan_index = 0;
                        self.phase = EmissionPhase::ObjectAssertions;
                        continue;
                    }
                    let index = self.scan_index;
                    self.scan_index += 1;
                    check_cancel(state, index)?;
                    if !columns.root_is_selected(index)? {
                        continue;
                    }
                    let node_id = columns.root_id(index)?;
                    if columns.node_tag(node_id)? != TAG_CLASS_ASSERTION {
                        continue;
                    }
                    if let ClassAssertionProjection::Edge { individual, class } = columns
                        .class_assertion_projection(node_id, preparation.options.max_iri_bytes)?
                    {
                        return self.publish(
                            DirectEdge {
                                source: clone_text(individual)?,
                                relation: clone_text(RDF_TYPE)?,
                                destination: clone_text(class)?,
                            },
                            preparation,
                        );
                    }
                }
                EmissionPhase::ObjectAssertions => {
                    if let Some(delta_index) = self.next_overlay_delta_index(preparation)? {
                        let delta = &preparation.overlay_deltas[delta_index];
                        if let OwnedOverlayDeltaProjection::ObjectPropertyAssertion {
                            source,
                            relation,
                            destination,
                        } = &delta.projection
                        {
                            self.overlay_delta_index =
                                self.overlay_delta_index.checked_add(1).ok_or_else(|| {
                                    KernelError::resource("encoded local-overlay cursor overflow")
                                })?;
                            return self.publish(
                                DirectEdge {
                                    source: clone_text(source)?,
                                    relation: clone_text(relation)?,
                                    destination: clone_text(destination)?,
                                },
                                preparation,
                            );
                        }
                    }
                    if self.scan_index == columns.root_count() {
                        self.scan_index = 0;
                        self.phase = EmissionPhase::DomainRanges;
                        continue;
                    }
                    let index = self.scan_index;
                    self.scan_index += 1;
                    check_cancel(state, index)?;
                    if !columns.root_is_selected(index)? {
                        continue;
                    }
                    let node_id = columns.root_id(index)?;
                    if columns.node_tag(node_id)? != TAG_OBJECT_PROPERTY_ASSERTION {
                        continue;
                    }
                    let (source, relation, destination) = columns.object_property_assertion_parts(
                        node_id,
                        preparation.options.max_iri_bytes,
                    )?;
                    return self.publish(
                        DirectEdge {
                            source: render_individual(source, &preparation.anonymous_ids)?,
                            relation: clone_text(relation)?,
                            destination: render_individual(
                                destination,
                                &preparation.anonymous_ids,
                            )?,
                        },
                        preparation,
                    );
                }
                EmissionPhase::DomainRanges => {
                    if self.active_property.is_none() {
                        let next = preparation.next_paired_property(
                            columns,
                            self.previous_property.as_deref(),
                            state,
                        )?;
                        let Some(property) = next else {
                            self.phase = EmissionPhase::Finished;
                            continue;
                        };
                        self.previous_property = Some(clone_text(property)?);
                        self.active_property = Some(clone_text(property)?);
                        self.current_domain = None;
                        self.domain_index = 0;
                        self.range_index = 0;
                    }
                    let property =
                        clone_text(self.active_property.as_deref().ok_or_else(|| {
                            KernelError::malformed("encoded domain/range cursor lost its property")
                        })?)?;
                    let scan_len = preparation.object_property_class_scan_len(columns)?;
                    if self.current_domain.is_none() {
                        let mut selected = None;
                        while self.domain_index < scan_len {
                            let position = self.domain_index;
                            self.domain_index += 1;
                            check_cancel(state, position)?;
                            let Some((kind, candidate, domain)) =
                                preparation.object_property_class_at(columns, position)?
                            else {
                                continue;
                            };
                            if kind == ObjectPropertyClassRuleKind::Domain && candidate == property
                            {
                                selected = Some(clone_text(domain)?);
                                break;
                            }
                        }
                        let Some(domain) = selected else {
                            self.active_property = None;
                            continue;
                        };
                        self.current_domain = Some(domain);
                        self.range_index = 0;
                    }
                    let mut selected_range = None;
                    while self.range_index < scan_len {
                        let position = self.range_index;
                        self.range_index += 1;
                        check_cancel(state, position)?;
                        let Some((kind, candidate, range)) =
                            preparation.object_property_class_at(columns, position)?
                        else {
                            continue;
                        };
                        if kind == ObjectPropertyClassRuleKind::Range && candidate == property {
                            selected_range = Some(range);
                            break;
                        }
                    }
                    if let Some(range) = selected_range {
                        let domain =
                            clone_text(self.current_domain.as_deref().ok_or_else(|| {
                                KernelError::malformed(
                                    "encoded domain/range cursor lost its domain",
                                )
                            })?)?;
                        self.set_role(&domain, &property, range)?;
                    } else {
                        self.current_domain = None;
                    }
                }
                EmissionPhase::Finished => return Ok(None),
            }
        }
    }
}

impl PreparedDirectBatches {
    pub(crate) fn statistics(&self) -> DirectCompileStats {
        self.preparation.statistics
    }

    pub(crate) fn remaining_edges(&self) -> usize {
        self.preparation
            .statistics
            .edges
            .saturating_sub(self.cursor.emitted)
    }

    pub(crate) fn try_clone_role_state(&self) -> Result<OwnedRoleState, KernelError> {
        self.preparation.role_state.try_clone()
    }

    #[cfg(test)]
    pub(crate) fn is_exhausted(&self) -> bool {
        self.remaining_edges() == 0
    }

    #[cfg(test)]
    pub(crate) fn emission_attempts(&self) -> usize {
        self.preparation.emission_attempts.load(Ordering::Relaxed)
    }

    pub(crate) fn prepare_next_batch(
        &self,
        columns: DirectColumns<'_>,
        state: &AtomicU8,
        batch_edges: usize,
    ) -> Result<(Vec<DirectEdge>, DirectEmissionCursor), KernelError> {
        let amount = self.remaining_edges().min(batch_edges);
        let mut edges = Vec::new();
        edges
            .try_reserve_exact(amount)
            .map_err(|_| KernelError::resource("encoded direct batch allocation failed"))?;
        let mut next_cursor = self.cursor.try_clone()?;
        while edges.len() < amount {
            let edge = next_cursor
                .next_edge(columns, &self.preparation, state)?
                .ok_or_else(|| {
                    KernelError::malformed(
                        "encoded direct output ended before its preflight edge count",
                    )
                })?;
            edges.push(edge);
        }
        Ok((edges, next_cursor))
    }

    pub(crate) fn commit_cursor(&mut self, cursor: DirectEmissionCursor) {
        self.cursor = cursor;
    }
}

#[cfg(test)]
pub(crate) fn compile_direct_with_options(
    columns: DirectColumns<'_>,
    options: DirectCompileOptions,
    state: &AtomicU8,
) -> Result<(Vec<DirectEdge>, DirectCompileStats), KernelError> {
    compile_direct_with_retained_role_state(columns, None, options, state, None)
}

fn prepare_direct<'a>(
    columns: DirectColumns<'a>,
    root_annotation_columns: Option<DirectColumns<'_>>,
    options: DirectCompileOptions,
    state: &AtomicU8,
    retained: Option<&OwnedRoleState>,
    local_role_axiom: Option<RoleAxiom<'a>>,
) -> Result<DirectPreparation, KernelError> {
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
    let selected_annotation_nodes = if let Some(root_columns) = root_annotation_columns {
        root_columns.validate_generic(state)?;
        root_columns.validate_supported_nodes(max_iri_bytes, state)?;
        let root_counts = root_columns.classify_roots(max_iri_bytes, state)?;
        let selected = columns.select_root_annotation_nodes(root_columns, state)?;
        if selected.len() != root_counts.annotation_assertions {
            return Err(KernelError::malformed(
                "encoded root annotation selection count changed after preflight",
            ));
        }
        Some(selected)
    } else {
        None
    };
    let selected_annotation_assertions = selected_annotation_nodes
        .as_ref()
        .map_or(counts.annotation_assertions, Vec::len);
    let anonymous_ids = columns.axiom_anonymous_ids(state)?;
    let buffer_bytes = columns.buffer_bytes()?;
    let root_provenance_buffer_bytes = root_annotation_columns
        .map(DirectColumns::buffer_bytes)
        .transpose()?
        .unwrap_or(0);
    let role_state = if asserted_taxonomy_only {
        RoleState::default()
    } else {
        columns.build_role_state(counts, max_iri_bytes, state, retained, local_role_axiom)?
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
    let equivalent_role_expansion_edges = equivalent_counts
        .expanded_role_edges
        .checked_sub(equivalent_counts.base_role_edges)
        .ok_or_else(|| {
            KernelError::malformed("encoded equivalent role-edge counters are inconsistent")
        })?;
    let equivalent_base_edges = equivalent_counts
        .edges
        .checked_sub(equivalent_role_expansion_edges)
        .ok_or_else(|| {
            KernelError::malformed("encoded equivalent edge counters are inconsistent")
        })?;
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
    } else if let Some(selected) = selected_annotation_nodes.as_deref() {
        columns.selected_annotation_edge_counts(selected, max_iri_bytes, state)?
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

    let stats = DirectCompileStats {
        roots: columns.selected_root_count()?,
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
        equivalent_base_edges,
        ignored_equivalents: equivalent_counts.ignored_shapes,
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
        selected_annotation_assertions,
        sub_annotation_properties: counts.sub_annotation_properties,
        annotation_property_domains: counts.annotation_property_domains,
        annotation_property_ranges: counts.annotation_property_ranges,
        annotation_edges: annotation_counts.edges,
        non_string_literal_renderings: annotation_counts.non_string_literals,
        skipped_axioms,
        object_property_domains: counts.object_property_domains,
        object_property_ranges: counts.object_property_ranges,
        ignored_object_property_domains: counts.ignored_object_property_domains,
        ignored_object_property_ranges: counts.ignored_object_property_ranges,
        domain_range_edges,
        role_expansion_edges,
        edges: projected,
        buffer_bytes,
        root_provenance_buffer_bytes,
    };
    let preparation = DirectPreparation {
        role_state: role_state.to_owned()?,
        anonymous_ids,
        selected_annotation_nodes,
        overlay_deltas: Vec::new(),
        local_object_property_classes: [None, None],
        options,
        statistics: stats,
        #[cfg(test)]
        emission_attempts: AtomicUsize::new(0),
    };
    Ok(preparation)
}

pub(crate) fn prepare_direct_batches_uncommitted(
    columns: DirectColumns<'_>,
    root_annotation_columns: Option<DirectColumns<'_>>,
    options: DirectCompileOptions,
    state: &AtomicU8,
    retained: Option<&OwnedRoleState>,
) -> Result<PreparedDirectBatches, KernelError> {
    prepare_direct_batches_with_local_role_uncommitted(
        columns,
        root_annotation_columns,
        options,
        state,
        retained,
        None,
    )
}

fn prepare_direct_batches_with_local_role_uncommitted<'a>(
    columns: DirectColumns<'a>,
    root_annotation_columns: Option<DirectColumns<'_>>,
    options: DirectCompileOptions,
    state: &AtomicU8,
    retained: Option<&OwnedRoleState>,
    local_role_axiom: Option<RoleAxiom<'a>>,
) -> Result<PreparedDirectBatches, KernelError> {
    let preparation = prepare_direct(
        columns,
        root_annotation_columns,
        options,
        state,
        retained,
        local_role_axiom,
    )?;
    // Every column read reachable from the cursor has already passed the
    // immutable structural, semantic, count, and capacity preflight above.
    // Output remains absent until the caller requests a bounded drain.
    Ok(PreparedDirectBatches {
        preparation,
        cursor: DirectEmissionCursor::default(),
    })
}

fn own_local_subclass_projection(
    columns: DirectColumns<'_>,
    root: usize,
    max_iri_bytes: usize,
    state: &AtomicU8,
    workspace: &mut LocalOverlayWorkspace,
) -> Result<OwnedOverlayDeltaProjection, KernelError> {
    if columns.node_tag(root)? != TAG_SUB_CLASS_OF {
        return Err(KernelError::unsupported(
            "bounded local-overlay subclass envelope requires SubClassOf roots",
        ));
    }
    let field_start = columns.exact_fields(root, 3)?;
    validate_local_emitting_annotation_scope(columns, root, field_start + 2, "SubClassOf", state)?;
    match columns.subclass_projection(root, max_iri_bytes)? {
        SubclassProjection::Taxonomy {
            source,
            destination,
        } => Ok(OwnedOverlayDeltaProjection::Taxonomy {
            source: workspace.clone_text(source)?,
            destination: workspace.clone_text(destination)?,
        }),
        SubclassProjection::Restriction {
            source,
            relation,
            destination,
        } => Ok(OwnedOverlayDeltaProjection::Restriction {
            source: workspace.clone_text(source)?,
            relation: workspace.clone_text(relation)?,
            destination: workspace.clone_text(destination)?,
        }),
        SubclassProjection::Ignored => {
            Err(KernelError::unsupported(LOCAL_EMITTING_OVERLAY_REQUIREMENT))
        }
    }
}

fn own_local_class_assertion_projection(
    columns: DirectColumns<'_>,
    root: usize,
    max_iri_bytes: usize,
    state: &AtomicU8,
    workspace: &mut LocalOverlayWorkspace,
) -> Result<OwnedOverlayDeltaProjection, KernelError> {
    if columns.node_tag(root)? != TAG_CLASS_ASSERTION {
        return Err(KernelError::unsupported(
            "bounded local-overlay class-assertion envelope requires ClassAssertion roots",
        ));
    }
    let field_start = columns.exact_fields(root, 3)?;
    validate_local_emitting_annotation_scope(
        columns,
        root,
        field_start + 2,
        "ClassAssertion",
        state,
    )?;
    match columns.class_assertion_projection(root, max_iri_bytes)? {
        ClassAssertionProjection::Edge { individual, class } => {
            Ok(OwnedOverlayDeltaProjection::ClassAssertion {
                individual: workspace.clone_text(individual)?,
                class: workspace.clone_text(class)?,
            })
        }
        ClassAssertionProjection::Ignored => {
            Err(KernelError::unsupported(LOCAL_EMITTING_OVERLAY_REQUIREMENT))
        }
    }
}

fn own_local_object_property_assertion_projection(
    columns: DirectColumns<'_>,
    root: usize,
    max_iri_bytes: usize,
    state: &AtomicU8,
    workspace: &mut LocalOverlayWorkspace,
) -> Result<OwnedOverlayDeltaProjection, KernelError> {
    let field_start = columns.exact_fields(root, 4)?;
    validate_local_emitting_annotation_scope(
        columns,
        root,
        field_start + 3,
        "ObjectPropertyAssertion",
        state,
    )?;
    let (source, relation, destination) =
        columns.object_property_assertion_parts(root, max_iri_bytes)?;
    match (source, destination) {
        (IndividualValue::Named(source), IndividualValue::Named(destination)) => {
            Ok(OwnedOverlayDeltaProjection::ObjectPropertyAssertion {
                source: workspace.clone_text(source)?,
                relation: workspace.clone_text(relation)?,
                destination: workspace.clone_text(destination)?,
            })
        }
        _ => Err(KernelError::unsupported(
            "bounded local-overlay ObjectPropertyAssertion root requires a named property and named individuals",
        )),
    }
}

fn validate_local_emitting_annotation_scope(
    columns: DirectColumns<'_>,
    root: usize,
    annotation_field: usize,
    constructor: &str,
    state: &AtomicU8,
) -> Result<(), KernelError> {
    let (_annotation_start, annotation_count) = columns.node_set_range(annotation_field, 0)?;
    if annotation_count != 0 && columns.root_contains_anonymous_individual(root, state)? {
        return Err(KernelError::unsupported(format!(
            "bounded local-overlay {constructor} root annotations require no anonymous individuals or local scope remap",
        )));
    }
    Ok(())
}

fn own_local_emitting_projection(
    columns: DirectColumns<'_>,
    root: usize,
    max_iri_bytes: usize,
    state: &AtomicU8,
    workspace: &mut LocalOverlayWorkspace,
) -> Result<OwnedOverlayDeltaProjection, KernelError> {
    match columns.node_tag(root)? {
        TAG_SUB_CLASS_OF => {
            own_local_subclass_projection(columns, root, max_iri_bytes, state, workspace)
        }
        TAG_CLASS_ASSERTION => {
            own_local_class_assertion_projection(columns, root, max_iri_bytes, state, workspace)
        }
        TAG_OBJECT_PROPERTY_ASSERTION => own_local_object_property_assertion_projection(
            columns,
            root,
            max_iri_bytes,
            state,
            workspace,
        ),
        _ => Err(KernelError::unsupported(LOCAL_EMITTING_OVERLAY_REQUIREMENT)),
    }
}

const LOCAL_EMITTING_OVERLAY_REQUIREMENT: &str = "bounded local-overlay emitting segment requires only named SubClassOf, ClassAssertion, or ObjectPropertyAssertion roots";

#[derive(Debug)]
struct LocalOverlayWorkspace {
    limit: usize,
    claimed: usize,
}

impl LocalOverlayWorkspace {
    fn new(limit: usize) -> Result<Self, KernelError> {
        if limit == 0 {
            return Err(KernelError::resource(
                "encoded local-overlay workspace limit must be positive",
            ));
        }
        Ok(Self { limit, claimed: 0 })
    }

    fn claim(&mut self, amount: usize) -> Result<(), KernelError> {
        let next = self
            .claimed
            .checked_add(amount)
            .ok_or_else(|| KernelError::resource("encoded local-overlay workspace overflow"))?;
        if next > self.limit {
            return Err(KernelError::resource(format!(
                "encoded local-overlay ownership requires more than {} workspace bytes",
                self.limit
            )));
        }
        self.claimed = next;
        Ok(())
    }

    fn clone_text(&mut self, value: &str) -> Result<String, KernelError> {
        self.claim(value.len())?;
        let owned = clone_text(value)?;
        self.claim(owned.capacity().saturating_sub(value.len()))?;
        Ok(owned)
    }

    fn reserve_overlay_deltas(
        &mut self,
        root_count: usize,
    ) -> Result<Vec<OwnedOverlayDelta>, KernelError> {
        use std::mem::size_of;

        let requested = root_count
            .checked_mul(size_of::<OwnedOverlayDelta>())
            .ok_or_else(|| KernelError::resource("encoded local-overlay workspace overflow"))?;
        self.claim(requested)?;
        let mut deltas = Vec::new();
        deltas.try_reserve_exact(root_count).map_err(|_| {
            KernelError::resource("encoded local-overlay projection allocation failed")
        })?;
        let actual = deltas
            .capacity()
            .checked_mul(size_of::<OwnedOverlayDelta>())
            .ok_or_else(|| KernelError::resource("encoded local-overlay workspace overflow"))?;
        self.claim(actual.saturating_sub(requested))?;
        Ok(deltas)
    }

    fn remaining_for_canonical_merge(&self) -> Result<usize, KernelError> {
        let remaining = self.limit.saturating_sub(self.claimed);
        if remaining == 0 {
            return Err(KernelError::resource(format!(
                "encoded local-overlay ownership requires more than {} workspace bytes",
                self.limit
            )));
        }
        Ok(remaining)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CrossTableDuplicatePolicy {
    Reject,
    Deduplicate,
}

const DEDUPLICATED_OVERLAY_SCAN_INDEX: usize = usize::MAX - 1;

pub(crate) fn prepare_single_overlay_delta_batches_uncommitted(
    base_columns: DirectColumns<'_>,
    delta_columns: DirectColumns<'_>,
    options: DirectCompileOptions,
    state: &AtomicU8,
    retained: Option<&OwnedRoleState>,
    max_canonical_work: usize,
    max_canonical_workspace_bytes: usize,
) -> Result<PreparedDirectBatches, KernelError> {
    prepare_two_table_batches_uncommitted(
        base_columns,
        delta_columns,
        options,
        state,
        retained,
        max_canonical_work,
        max_canonical_workspace_bytes,
        CrossTableDuplicatePolicy::Reject,
    )
}

pub(crate) fn prepare_two_member_composite_batches_uncommitted(
    left_columns: DirectColumns<'_>,
    right_columns: DirectColumns<'_>,
    options: DirectCompileOptions,
    state: &AtomicU8,
    retained: Option<&OwnedRoleState>,
    max_canonical_work: usize,
    max_canonical_workspace_bytes: usize,
) -> Result<PreparedDirectBatches, KernelError> {
    prepare_two_table_batches_uncommitted(
        left_columns,
        right_columns,
        options,
        state,
        retained,
        max_canonical_work,
        max_canonical_workspace_bytes,
        CrossTableDuplicatePolicy::Deduplicate,
    )
}

#[allow(clippy::too_many_arguments)] // The bounded merge contract keeps every limit explicit.
fn prepare_two_table_batches_uncommitted(
    base_columns: DirectColumns<'_>,
    delta_columns: DirectColumns<'_>,
    options: DirectCompileOptions,
    state: &AtomicU8,
    retained: Option<&OwnedRoleState>,
    max_canonical_work: usize,
    max_canonical_workspace_bytes: usize,
    duplicate_policy: CrossTableDuplicatePolicy,
) -> Result<PreparedDirectBatches, KernelError> {
    if options.include_literals {
        return Err(KernelError::unsupported(
            "bounded local-overlay compilation does not support literal projection",
        ));
    }
    if !delta_columns.excluded_root_ids.is_empty() {
        return Err(KernelError::unsupported(
            "bounded local-overlay delta requires ALL root selection",
        ));
    }
    let delta_root_count = delta_columns.root_count();
    if delta_root_count == 0 {
        return Err(KernelError::unsupported(
            "bounded local-overlay compilation requires a nonempty local root segment",
        ));
    }

    delta_columns.validate_generic(state)?;
    delta_columns.validate_supported_nodes(options.max_iri_bytes, state)?;
    let delta_counts = delta_columns.classify_roots(options.max_iri_bytes, state)?;
    let paired_object_property_class = delta_root_count == 2
        && delta_counts
            == (RootCounts {
                object_property_domains: 1,
                object_property_ranges: 1,
                ..RootCounts::default()
            });
    let delta_root = delta_columns.root_id(0)?;
    let delta_root_kind = delta_columns.root_kind(0)?;
    let delta_tag = delta_columns.node_tag(delta_root)?;
    let singular_emitting_delta = delta_root_count == 1
        && delta_root_kind == ROOT_AXIOM
        && (matches!(delta_tag, TAG_SUB_CLASS_OF)
            && (delta_counts
                == (RootCounts {
                    subclasses: 1,
                    ..RootCounts::default()
                })
                || delta_counts
                    == (RootCounts {
                        subclasses: 1,
                        restriction_subclasses: 1,
                        ..RootCounts::default()
                    }))
            || delta_tag == TAG_CLASS_ASSERTION
                && delta_counts
                    == (RootCounts {
                        class_assertions: 1,
                        ..RootCounts::default()
                    })
            || delta_tag == TAG_OBJECT_PROPERTY_ASSERTION
                && delta_counts
                    == (RootCounts {
                        object_property_assertions: 1,
                        ..RootCounts::default()
                    }));
    let emitting_delta =
        !paired_object_property_class && (delta_root_count > 1 || singular_emitting_delta);
    let aggregate_or_emitting_delta = paired_object_property_class || emitting_delta;
    let local_rule_context = LocalRuleContext::new(options, false);
    let local_annotation_rule = LocalAnnotationRulePlan::classify(
        delta_counts,
        delta_root_kind,
        delta_tag,
        local_rule_context,
    );
    if !aggregate_or_emitting_delta
        && delta_root_kind != ROOT_AXIOM
        && local_annotation_rule.is_none()
    {
        return Err(KernelError::unsupported(
            "bounded local-overlay root must be one supported axiom or ontology annotation",
        ));
    }
    let silent_object_property_root = SilentObjectPropertyRoot::classify(delta_counts, delta_tag);
    let silent_annotation_property_root =
        SilentAnnotationPropertyRoot::classify(delta_counts, delta_tag);
    let silent_class_disjointness_root =
        SilentClassDisjointnessRoot::classify(delta_counts, delta_tag);
    let silent_ignored_class_root = SilentIgnoredClassRoot::classify(delta_counts, delta_tag);
    let silent_ignored_equivalent_root =
        SilentIgnoredEquivalentRoot::classify(delta_counts, delta_tag);
    let object_property_class_rule =
        ObjectPropertyClassRulePlan::classify(delta_counts, delta_tag, local_rule_context);
    let local_role_rule = LocalRoleRulePlan::classify(delta_counts, delta_tag, local_rule_context);
    let mut object_property_class_rules = [object_property_class_rule, None];
    let mut local_object_property_classes = [None, None];
    let mut local_workspace = LocalOverlayWorkspace::new(max_canonical_workspace_bytes)?;
    let mut overlay_deltas = if emitting_delta {
        local_workspace.reserve_overlay_deltas(delta_root_count)?
    } else {
        Vec::new()
    };
    if paired_object_property_class {
        for root_index in 0..delta_root_count {
            check_cancel(state, root_index)?;
            let root = delta_columns.root_id(root_index)?;
            if delta_columns.root_kind(root_index)? != ROOT_AXIOM {
                return Err(KernelError::unsupported(
                    "bounded two-root local overlay requires axiom roots",
                ));
            }
            let tag = delta_columns.node_tag(root)?;
            let rule = ObjectPropertyClassRulePlan::for_tag(tag, false, local_rule_context)
                .ok_or_else(|| {
                    KernelError::unsupported(
                        "bounded two-root local overlay requires ObjectPropertyDomain and ObjectPropertyRange roots",
                    )
                })?;
            let (kind, property, class) = rule
                .validate(delta_columns, root, local_rule_context, state)?
                .ok_or_else(|| {
                    KernelError::unsupported(
                        "bounded two-root local overlay requires named object properties and classes",
                    )
                })?;
            object_property_class_rules[root_index] = Some(rule);
            local_object_property_classes[root_index] = Some(OwnedLocalObjectPropertyClass {
                kind,
                property: clone_text(property)?,
                class: clone_text(class)?,
                insertion_position: 0,
            });
        }
        let domain = local_object_property_classes
            .iter()
            .flatten()
            .find(|local| local.kind == ObjectPropertyClassRuleKind::Domain)
            .ok_or_else(|| {
                KernelError::malformed("encoded two-root local overlay lost its domain")
            })?;
        let range = local_object_property_classes
            .iter()
            .flatten()
            .find(|local| local.kind == ObjectPropertyClassRuleKind::Range)
            .ok_or_else(|| {
                KernelError::malformed("encoded two-root local overlay lost its range")
            })?;
        if domain.property != range.property {
            return Err(KernelError::unsupported(
                "bounded two-root local overlay requires its domain and range to use the same named object property",
            ));
        }
    }
    if emitting_delta {
        for root_index in 0..delta_root_count {
            check_cancel(state, root_index)?;
            let root = delta_columns.root_id(root_index)?;
            if delta_columns.root_kind(root_index)? != ROOT_AXIOM {
                return Err(KernelError::unsupported(
                    "bounded local-overlay emitting segment requires axiom roots",
                ));
            }
            let projection = own_local_emitting_projection(
                delta_columns,
                root,
                options.max_iri_bytes,
                state,
                &mut local_workspace,
            )?;
            overlay_deltas.push(OwnedOverlayDelta {
                projection,
                insertion_scan_index: usize::MAX,
                local_canonical_index: root_index,
            });
        }
    }
    let mut local_role_state_axiom = None;
    let _non_emitting_plan: Option<()> = if aggregate_or_emitting_delta {
        None
    } else if let Some(rule) = local_annotation_rule {
        rule.validate(delta_columns, delta_root, local_rule_context, state)?;
        None
    } else if delta_counts
        == (RootCounts {
            declarations: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_DECLARATION
    {
        let field_start = delta_columns.exact_fields(delta_root, 2)?;
        let (_entity_kind, iri_id) =
            delta_columns.entity(delta_columns.field_node(field_start)?)?;
        delta_columns.iri(iri_id, options.max_iri_bytes)?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 1, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay Declaration root must be unannotated",
            ));
        }
        None
    } else if let Some(kind) = silent_ignored_class_root {
        let constructor = kind.constructor();
        let field_start = delta_columns.exact_fields(delta_root, 3)?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 2, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {constructor} root must be unannotated",
            )));
        }
        match kind {
            SilentIgnoredClassRoot::Subclass => {
                if !matches!(
                    delta_columns.subclass_projection(delta_root, options.max_iri_bytes)?,
                    SubclassProjection::Ignored
                ) {
                    return Err(KernelError::malformed(
                        "encoded local-overlay ignored SubClassOf root changed projection",
                    ));
                }
            }
            SilentIgnoredClassRoot::Assertion => {
                if !matches!(
                    delta_columns.class_assertion_projection(delta_root, options.max_iri_bytes,)?,
                    ClassAssertionProjection::Ignored
                ) {
                    return Err(KernelError::malformed(
                        "encoded local-overlay ignored ClassAssertion root changed projection",
                    ));
                }
            }
        }
        if !delta_columns
            .axiom_anonymous_ids(state)?
            .node_ids
            .is_empty()
        {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay ignored {constructor} root requires no anonymous individuals or local scope remap",
            )));
        }
        None
    } else if silent_ignored_equivalent_root.is_some() {
        let field_start = delta_columns.exact_fields(delta_root, 2)?;
        let (_item_start, item_count) = delta_columns.node_set_range(field_start, 2)?;
        if item_count > 3 {
            return Err(KernelError::unsupported(
                "bounded local-overlay EquivalentClasses root requires a canonical binary or ternary ignored class-expression set",
            ));
        }
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 1, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay EquivalentClasses root must be unannotated",
            ));
        }
        if !matches!(
            delta_columns.equivalent_projection(delta_root, options.max_iri_bytes)?,
            EquivalentProjection::Ignored
        ) {
            return Err(KernelError::unsupported(
                "bounded local-overlay EquivalentClasses root requires an ignored complete direct projection",
            ));
        }
        if !delta_columns
            .axiom_anonymous_ids(state)?
            .node_ids
            .is_empty()
        {
            return Err(KernelError::unsupported(
                "bounded local-overlay ignored EquivalentClasses root requires no anonymous individuals or local scope remap",
            ));
        }
        None
    } else if let Some(rule) = object_property_class_rule {
        local_object_property_classes[0] = rule
            .validate(delta_columns, delta_root, local_rule_context, state)?
            .map(|(kind, property, class)| {
                Ok(OwnedLocalObjectPropertyClass {
                    kind,
                    property: clone_text(property)?,
                    class: clone_text(class)?,
                    insertion_position: 0,
                })
            })
            .transpose()?;
        None
    } else if let Some(rule) = local_role_rule {
        local_role_state_axiom =
            rule.validate(delta_columns, delta_root, local_rule_context, state)?;
        None
    } else if delta_counts
        == (RootCounts {
            negative_object_property_assertions: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION
    {
        let field_start = delta_columns.exact_fields(delta_root, 4)?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 3, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay NegativeObjectPropertyAssertion root must be unannotated",
            ));
        }
        delta_columns.object_property_expression_iri(
            delta_columns.field_node(field_start)?,
            options.max_iri_bytes,
        )?;
        let source = delta_columns.individual_value(
            delta_columns.field_node(field_start + 1)?,
            options.max_iri_bytes,
        )?;
        let destination = delta_columns.individual_value(
            delta_columns.field_node(field_start + 2)?,
            options.max_iri_bytes,
        )?;
        if !matches!(
            (source, destination),
            (IndividualValue::Named(_), IndividualValue::Named(_))
        ) {
            return Err(KernelError::unsupported(
                "bounded local-overlay NegativeObjectPropertyAssertion root requires named individuals",
            ));
        }
        None
    } else if let Some(kind) = silent_object_property_root {
        let constructor = kind.constructor();
        let field_start = delta_columns.exact_fields(delta_root, 2)?;
        if kind.is_set() {
            let (item_start, item_count) = delta_columns.node_set_range(field_start, 2)?;
            if item_count > 3 {
                return Err(KernelError::unsupported(format!(
                    "bounded local-overlay {constructor} root requires a canonical binary or ternary object-property-expression set",
                )));
            }
            for item_index in item_start..item_start + item_count {
                delta_columns.object_property_expression(
                    delta_columns.item_node(item_index)?,
                    options.max_iri_bytes,
                )?;
            }
        } else {
            delta_columns.object_property_expression(
                delta_columns.field_node(field_start)?,
                options.max_iri_bytes,
            )?;
        }
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 1, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {constructor} root must be unannotated",
            )));
        }
        None
    } else if let Some(kind) = silent_annotation_property_root {
        let constructor = kind.constructor();
        match kind {
            SilentAnnotationPropertyRoot::SubProperty => {
                delta_columns
                    .validate_sub_annotation_property_of(delta_root, options.max_iri_bytes)?;
            }
            SilentAnnotationPropertyRoot::Domain => {
                delta_columns.validate_annotation_property_iri_axiom(
                    delta_root,
                    TAG_ANNOTATION_PROPERTY_DOMAIN,
                    options.max_iri_bytes,
                )?;
            }
            SilentAnnotationPropertyRoot::Range => {
                delta_columns.validate_annotation_property_iri_axiom(
                    delta_root,
                    TAG_ANNOTATION_PROPERTY_RANGE,
                    options.max_iri_bytes,
                )?;
            }
        }
        let field_start = delta_columns.exact_fields(delta_root, 3)?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 2, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {constructor} root must be unannotated",
            )));
        }
        None
    } else if let Some(kind) = silent_class_disjointness_root {
        let constructor = kind.constructor();
        let (item_count, annotation_field) = match kind {
            SilentClassDisjointnessRoot::Classes => {
                delta_columns.validate_disjoint_classes(delta_root, options.max_iri_bytes)?;
                let field_start = delta_columns.exact_fields(delta_root, 2)?;
                let (_item_start, item_count) = delta_columns.node_set_range(field_start, 2)?;
                (item_count, field_start + 1)
            }
            SilentClassDisjointnessRoot::Union => {
                delta_columns.validate_disjoint_union(delta_root, options.max_iri_bytes)?;
                let field_start = delta_columns.exact_fields(delta_root, 3)?;
                let (_item_start, item_count) = delta_columns.node_set_range(field_start + 1, 2)?;
                (item_count, field_start + 2)
            }
        };
        if item_count > 3 {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {constructor} root requires a canonical binary or ternary class-expression set",
            )));
        }
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(annotation_field, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {constructor} root must be unannotated",
            )));
        }
        None
    } else if (delta_counts
        == (RootCounts {
            data_property_assertions: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_DATA_PROPERTY_ASSERTION)
        || (delta_counts
            == (RootCounts {
                negative_data_property_assertions: 1,
                ..RootCounts::default()
            })
            && delta_tag == TAG_NEGATIVE_DATA_PROPERTY_ASSERTION)
    {
        let field_start = delta_columns.exact_fields(delta_root, 4)?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 3, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                if delta_tag == TAG_DATA_PROPERTY_ASSERTION {
                    "bounded local-overlay DataPropertyAssertion root must be unannotated"
                } else {
                    "bounded local-overlay NegativeDataPropertyAssertion root must be unannotated"
                },
            ));
        }
        delta_columns.named_data_property_iri(
            delta_columns.field_node(field_start)?,
            options.max_iri_bytes,
        )?;
        let source = delta_columns.individual_value(
            delta_columns.field_node(field_start + 1)?,
            options.max_iri_bytes,
        )?;
        if !matches!(source, IndividualValue::Named(_)) {
            return Err(KernelError::unsupported(
                if delta_tag == TAG_DATA_PROPERTY_ASSERTION {
                    "bounded local-overlay DataPropertyAssertion root requires a named individual"
                } else {
                    "bounded local-overlay NegativeDataPropertyAssertion root requires a named individual"
                },
            ));
        }
        delta_columns.validate_literal(
            delta_columns.field_node(field_start + 2)?,
            options.max_iri_bytes,
        )?;
        None
    } else if delta_counts
        == (RootCounts {
            sub_data_properties: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_SUB_DATA_PROPERTY_OF
    {
        let field_start = delta_columns.exact_fields(delta_root, 3)?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 2, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay SubDataPropertyOf root must be unannotated",
            ));
        }
        delta_columns.named_data_property_iri(
            delta_columns.field_node(field_start)?,
            options.max_iri_bytes,
        )?;
        delta_columns.named_data_property_iri(
            delta_columns.field_node(field_start + 1)?,
            options.max_iri_bytes,
        )?;
        None
    } else if delta_counts
        == (RootCounts {
            equivalent_data_properties: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_EQUIVALENT_DATA_PROPERTIES
    {
        let field_start = delta_columns.exact_fields(delta_root, 2)?;
        let (item_start, item_count) = delta_columns.node_set_range(field_start, 2)?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 1, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay EquivalentDataProperties root must be unannotated",
            ));
        }
        for item_index in item_start..item_start + item_count {
            delta_columns.named_data_property_iri(
                delta_columns.item_node(item_index)?,
                options.max_iri_bytes,
            )?;
        }
        None
    } else if delta_counts
        == (RootCounts {
            disjoint_data_properties: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_DISJOINT_DATA_PROPERTIES
    {
        let field_start = delta_columns.exact_fields(delta_root, 2)?;
        let (item_start, item_count) = delta_columns.node_set_range(field_start, 2)?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 1, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay DisjointDataProperties root must be unannotated",
            ));
        }
        for item_index in item_start..item_start + item_count {
            delta_columns.named_data_property_iri(
                delta_columns.item_node(item_index)?,
                options.max_iri_bytes,
            )?;
        }
        None
    } else if delta_counts
        == (RootCounts {
            data_property_domains: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_DATA_PROPERTY_DOMAIN
    {
        let field_start = delta_columns.exact_fields(delta_root, 3)?;
        delta_columns.named_data_property_iri(
            delta_columns.field_node(field_start)?,
            options.max_iri_bytes,
        )?;
        delta_columns.class_expression_rank(
            delta_columns.field_node(field_start + 1)?,
            options.max_iri_bytes,
        )?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 2, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay DataPropertyDomain root must be unannotated",
            ));
        }
        None
    } else if delta_counts
        == (RootCounts {
            data_property_ranges: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_DATA_PROPERTY_RANGE
    {
        let field_start = delta_columns.exact_fields(delta_root, 3)?;
        delta_columns.named_data_property_iri(
            delta_columns.field_node(field_start)?,
            options.max_iri_bytes,
        )?;
        delta_columns.validate_data_range_node(
            delta_columns.field_node(field_start + 1)?,
            options.max_iri_bytes,
        )?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 2, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay DataPropertyRange root must be unannotated",
            ));
        }
        None
    } else if delta_counts
        == (RootCounts {
            functional_data_properties: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_FUNCTIONAL_DATA_PROPERTY
    {
        let field_start = delta_columns.exact_fields(delta_root, 2)?;
        delta_columns.named_data_property_iri(
            delta_columns.field_node(field_start)?,
            options.max_iri_bytes,
        )?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 1, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay FunctionalDataProperty root must be unannotated",
            ));
        }
        None
    } else if delta_counts
        == (RootCounts {
            datatype_definitions: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_DATATYPE_DEFINITION
    {
        let field_start = delta_columns.exact_fields(delta_root, 3)?;
        delta_columns.named_datatype_iri(
            delta_columns.field_node(field_start)?,
            options.max_iri_bytes,
        )?;
        delta_columns.validate_data_range_node(
            delta_columns.field_node(field_start + 1)?,
            options.max_iri_bytes,
        )?;
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 2, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay DatatypeDefinition root must be unannotated",
            ));
        }
        None
    } else if delta_counts
        == (RootCounts {
            has_keys: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_HAS_KEY
    {
        let field_start = delta_columns.exact_fields(delta_root, 4)?;
        delta_columns.class_expression_rank(
            delta_columns.field_node(field_start)?,
            options.max_iri_bytes,
        )?;
        let (object_start, object_count) = delta_columns.node_set_range(field_start + 1, 0)?;
        for item_index in object_start..object_start + object_count {
            delta_columns.object_property_expression(
                delta_columns.item_node(item_index)?,
                options.max_iri_bytes,
            )?;
        }
        let (data_start, data_count) = delta_columns.node_set_range(field_start + 2, 0)?;
        for item_index in data_start..data_start + data_count {
            delta_columns.named_data_property_iri(
                delta_columns.item_node(item_index)?,
                options.max_iri_bytes,
            )?;
        }
        if object_count == 0 && data_count == 0 {
            return Err(KernelError::malformed(
                "encoded HasKey requires at least one property",
            ));
        }
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 3, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(
                "bounded local-overlay HasKey root must be unannotated",
            ));
        }
        None
    } else if (delta_counts
        == (RootCounts {
            same_individuals: 1,
            ..RootCounts::default()
        })
        && delta_tag == TAG_SAME_INDIVIDUAL)
        || (delta_counts
            == (RootCounts {
                different_individuals: 1,
                ..RootCounts::default()
            })
            && delta_tag == TAG_DIFFERENT_INDIVIDUALS)
    {
        let constructor = if delta_tag == TAG_SAME_INDIVIDUAL {
            "SameIndividual"
        } else {
            "DifferentIndividuals"
        };
        let field_start = delta_columns.exact_fields(delta_root, 2)?;
        let (item_start, item_count) = delta_columns.node_set_range(field_start, 2)?;
        if item_count > 3 {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {constructor} root requires a canonical binary or ternary named-individual set",
            )));
        }
        let (_annotation_start, annotation_count) =
            delta_columns.node_set_range(field_start + 1, 0)?;
        if annotation_count != 0 {
            return Err(KernelError::unsupported(format!(
                "bounded local-overlay {constructor} root must be unannotated",
            )));
        }
        for item_index in item_start..item_start + item_count {
            if !matches!(
                delta_columns.individual_value(
                    delta_columns.item_node(item_index)?,
                    options.max_iri_bytes,
                )?,
                IndividualValue::Named(_)
            ) {
                return Err(KernelError::unsupported(format!(
                    "bounded local-overlay {constructor} root requires named individuals",
                )));
            }
        }
        None
    } else {
        return Err(KernelError::unsupported(
            "bounded local-overlay root must be one supported state-neutral ontology Annotation or AnnotationAssertion, unannotated Declaration, supported named or ignored-shape SubClassOf, named or ignored-shape ClassAssertion, ignored-shape EquivalentClasses, named or ignored-shape ObjectPropertyDomain or ObjectPropertyRange, supported local SubObjectPropertyOf or InverseObjectProperties, named ObjectPropertyAssertion, named-individual NegativeObjectPropertyAssertion, supported silent object-property axiom, supported silent annotation-property axiom, supported silent class-disjointness axiom, named-source data-property assertion, named SubDataPropertyOf, named EquivalentDataProperties, named DisjointDataProperties, named-property DataPropertyDomain, named-property DataPropertyRange, named FunctionalDataProperty, named DatatypeDefinition, supported HasKey, named SameIndividual, or named DifferentIndividuals axiom",
        ));
    };

    use canonical_merge::MergedCanonicalRoot;
    let local_sort_work = if emitting_delta {
        let sort_levels =
            usize::BITS as usize - delta_root_count.saturating_sub(1).leading_zeros() as usize;
        delta_root_count
            .checked_mul(sort_levels)
            .and_then(|work| work.checked_add(delta_root_count))
            .ok_or_else(|| KernelError::resource("encoded local-overlay work counter overflow"))?
    } else {
        0
    };
    let canonical_work = max_canonical_work
        .checked_sub(local_sort_work)
        .filter(|remaining| *remaining != 0)
        .ok_or_else(|| {
            KernelError::resource(format!(
                "encoded canonical comparison requires more than {max_canonical_work} work units"
            ))
        })?;
    let mut merger = canonical_merge::CanonicalRootMerger::new(
        base_columns,
        delta_columns,
        canonical_merge::CanonicalMergeLimits {
            max_work: canonical_work,
            max_workspace_bytes: local_workspace.remaining_for_canonical_merge()?,
        },
        state,
    )?;
    let mut left_roots = 0_usize;
    let mut right_roots = 0_usize;
    let mut unique_right_roots = 0_usize;
    let mut deduplicated_right_roots = 0_usize;
    let mut next_insertion_scan = 0_usize;
    let mut next_base_scan_index = 0_usize;
    let mut insertion_positions = [None, None];
    while let Some(root) = merger.next(state)? {
        match root {
            MergedCanonicalRoot::Left(root) => {
                left_roots = left_roots.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded local-overlay root counter overflow")
                })?;
                next_insertion_scan = root
                    .index
                    .checked_add(1)
                    .and_then(|position| position.checked_add(unique_right_roots))
                    .ok_or_else(|| {
                        KernelError::resource("encoded local-overlay root position overflow")
                    })?;
                next_base_scan_index = root.index.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded local-overlay root position overflow")
                })?;
            }
            MergedCanonicalRoot::Right(root) => {
                if emitting_delta {
                    let Some(delta) = overlay_deltas.get_mut(root.index) else {
                        return Err(KernelError::malformed(
                            "encoded local-overlay merge produced an inconsistent local root",
                        ));
                    };
                    if delta.insertion_scan_index != usize::MAX {
                        return Err(KernelError::malformed(
                            "encoded local-overlay merge produced a duplicate local root",
                        ));
                    }
                    delta.insertion_scan_index = next_base_scan_index;
                } else {
                    let Some(position) = insertion_positions.get_mut(root.index) else {
                        return Err(KernelError::malformed(
                            "encoded local-overlay merge produced an inconsistent local root",
                        ));
                    };
                    if position.replace(next_insertion_scan).is_some() {
                        return Err(KernelError::malformed(
                            "encoded local-overlay merge produced a duplicate local root",
                        ));
                    }
                }
                right_roots = right_roots.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded local-overlay root counter overflow")
                })?;
                unique_right_roots = unique_right_roots.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded local-overlay root counter overflow")
                })?;
                next_insertion_scan = next_insertion_scan.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded local-overlay root position overflow")
                })?;
            }
            MergedCanonicalRoot::Both { left, right } => {
                if duplicate_policy == CrossTableDuplicatePolicy::Reject {
                    return Err(KernelError::unsupported(
                        "bounded local-overlay root duplicates its direct source",
                    ));
                }
                if !emitting_delta {
                    return Err(KernelError::unsupported(
                        "bounded two-member composite structural deduplication requires emitting roots",
                    ));
                }
                let Some(delta) = overlay_deltas.get_mut(right.index) else {
                    return Err(KernelError::malformed(
                        "encoded composite merge produced an inconsistent duplicate root",
                    ));
                };
                if delta.insertion_scan_index != usize::MAX {
                    return Err(KernelError::malformed(
                        "encoded composite merge repeated a duplicate root",
                    ));
                }
                delta.insertion_scan_index = DEDUPLICATED_OVERLAY_SCAN_INDEX;
                left_roots = left_roots.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded composite root counter overflow")
                })?;
                right_roots = right_roots.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded composite root counter overflow")
                })?;
                deduplicated_right_roots =
                    deduplicated_right_roots.checked_add(1).ok_or_else(|| {
                        KernelError::resource("encoded deduplicated-root counter overflow")
                    })?;
                next_insertion_scan = left
                    .index
                    .checked_add(1)
                    .and_then(|position| position.checked_add(unique_right_roots))
                    .ok_or_else(|| {
                        KernelError::resource("encoded composite root position overflow")
                    })?;
                next_base_scan_index = left.index.checked_add(1).ok_or_else(|| {
                    KernelError::resource("encoded composite root position overflow")
                })?;
            }
        }
    }
    let lost_local_root = if emitting_delta {
        overlay_deltas
            .iter()
            .any(|delta| delta.insertion_scan_index == usize::MAX)
    } else {
        insertion_positions[..delta_root_count]
            .iter()
            .any(Option::is_none)
    };
    if right_roots != delta_root_count || lost_local_root {
        return Err(KernelError::malformed(
            "encoded local-overlay merge lost a local root",
        ));
    }
    let insertion_position = insertion_positions[0].unwrap_or(0);
    let selected_base_roots = base_columns.selected_root_count()?;
    let merge_report = merger.report();
    if left_roots != selected_base_roots
        || merge_report.deduplicated_roots != deduplicated_right_roots
        || merge_report.roots_emitted
            != selected_base_roots
                .checked_add(unique_right_roots)
                .ok_or_else(|| KernelError::resource("encoded root-count overflow"))?
    {
        return Err(KernelError::malformed(
            "encoded local-overlay merge produced inconsistent root counts",
        ));
    }
    drop(merger);
    overlay_deltas.retain(|delta| delta.insertion_scan_index != DEDUPLICATED_OVERLAY_SCAN_INDEX);
    canonicalize_overlay_delta_plan(&mut overlay_deltas);

    if let Some(axiom) = local_role_state_axiom.as_mut() {
        axiom.canonical_order = insertion_position;
    }
    for (root_index, local) in local_object_property_classes.iter_mut().enumerate() {
        if let Some(local) = local {
            local.insertion_position = insertion_positions[root_index].ok_or_else(|| {
                KernelError::malformed(
                    "encoded local-overlay object-property class lost its insertion position",
                )
            })?;
        }
    }
    let mut prepared = prepare_direct_batches_with_local_role_uncommitted(
        base_columns,
        None,
        options,
        state,
        retained,
        local_role_state_axiom,
    )?;
    prepared.preparation.local_object_property_classes = local_object_property_classes;

    let mut projection_edges = 0_usize;
    let mut projection_role_expansion_edges = 0_usize;
    for local_delta in &overlay_deltas {
        let local_projection = &local_delta.projection;
        let (edges, role_expansion_edges) = match local_projection {
            OwnedOverlayDeltaProjection::Taxonomy { .. } => (
                1_usize
                    .checked_add(usize::from(options.bidirectional))
                    .ok_or_else(|| KernelError::resource("encoded edge-count overflow"))?,
                0,
            ),
            OwnedOverlayDeltaProjection::Restriction { relation, .. }
                if !options.asserted_taxonomy_only && !options.only_taxonomy =>
            {
                let edges = prepared.preparation.role_state.edge_count(relation)?;
                (
                    edges,
                    edges.checked_sub(1).ok_or_else(|| {
                        KernelError::malformed(
                            "encoded local-overlay restriction edge count is inconsistent",
                        )
                    })?,
                )
            }
            OwnedOverlayDeltaProjection::ClassAssertion { .. }
            | OwnedOverlayDeltaProjection::ObjectPropertyAssertion { .. }
                if !options.asserted_taxonomy_only =>
            {
                (1, 0)
            }
            OwnedOverlayDeltaProjection::Restriction { .. }
            | OwnedOverlayDeltaProjection::ClassAssertion { .. }
            | OwnedOverlayDeltaProjection::ObjectPropertyAssertion { .. } => (0, 0),
        };
        projection_edges = projection_edges
            .checked_add(edges)
            .ok_or_else(|| KernelError::resource("encoded edge-count overflow"))?;
        projection_role_expansion_edges = projection_role_expansion_edges
            .checked_add(role_expansion_edges)
            .ok_or_else(|| KernelError::resource("encoded role-expansion edge-count overflow"))?;
    }
    let (additional_domain_range_edges, expanded_local_domain_range_edges) =
        if options.asserted_taxonomy_only {
            (0, 0)
        } else {
            prepared
                .preparation
                .local_object_property_class_edge_counts(base_columns, state)?
        };
    let local_domain_range_role_expansion_edges = expanded_local_domain_range_edges
        .checked_sub(additional_domain_range_edges)
        .ok_or_else(|| {
            KernelError::malformed("encoded local domain/range edge counters are inconsistent")
        })?;
    let additional_edges = projection_edges
        .checked_add(expanded_local_domain_range_edges)
        .ok_or_else(|| KernelError::resource("encoded edge-count overflow"))?;
    let projected = prepared
        .preparation
        .statistics
        .edges
        .checked_add(additional_edges)
        .ok_or_else(|| KernelError::resource("encoded edge-count overflow"))?;
    if projected > options.max_edges {
        return Err(KernelError::resource(format!(
            "encoded local-overlay batch requires {projected} edges; configured limit is {}",
            options.max_edges,
        )));
    }
    let statistics = &mut prepared.preparation.statistics;
    statistics.roots = statistics
        .roots
        .checked_add(unique_right_roots)
        .ok_or_else(|| KernelError::resource("encoded root-count overflow"))?;
    statistics.nodes = statistics
        .nodes
        .checked_add(delta_columns.node_count())
        .ok_or_else(|| KernelError::resource("encoded node-count overflow"))?;
    for local_delta in &overlay_deltas {
        local_delta.projection.apply_statistics(statistics)?;
    }
    statistics.role_expansion_edges = statistics
        .role_expansion_edges
        .checked_add(projection_role_expansion_edges)
        .ok_or_else(|| KernelError::resource("encoded role-expansion edge-count overflow"))?;
    match emitting_delta {
        true => {}
        false if local_annotation_rule.is_some() => {
            let Some(rule) = local_annotation_rule else {
                unreachable!("matched local annotation rule remains available");
            };
            rule.apply_statistics(statistics)?;
        }
        false
            if matches!(
                silent_ignored_class_root,
                Some(SilentIgnoredClassRoot::Subclass)
            ) =>
        {
            statistics.subclasses = statistics
                .subclasses
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded subclass-count overflow"))?;
            statistics.ignored_subclasses = statistics
                .ignored_subclasses
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded ignored-subclass count overflow"))?;
        }
        false
            if matches!(
                silent_ignored_class_root,
                Some(SilentIgnoredClassRoot::Assertion)
            ) =>
        {
            statistics.class_assertions = statistics
                .class_assertions
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded class-assertion count overflow"))?;
            statistics.ignored_class_assertions = statistics
                .ignored_class_assertions
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded ignored-class-assertion count overflow")
                })?;
        }
        false if silent_ignored_equivalent_root.is_some() => {
            statistics.equivalents = statistics
                .equivalents
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded equivalent-class count overflow"))?;
            if !options.asserted_taxonomy_only {
                statistics.ignored_equivalents = statistics
                    .ignored_equivalents
                    .checked_add(1)
                    .ok_or_else(|| {
                        KernelError::resource("encoded ignored-equivalent count overflow")
                    })?;
            }
        }
        false if object_property_class_rules.iter().any(Option::is_some) => {
            for rule in object_property_class_rules.iter().flatten().copied() {
                rule.apply_statistics(statistics)?;
            }
        }
        false if local_role_rule.is_some() => {
            let Some(rule) = local_role_rule else {
                unreachable!("matched local role rule remains available");
            };
            rule.apply_statistics(statistics)?;
        }
        false if delta_tag == TAG_DECLARATION => {
            statistics.declarations = statistics
                .declarations
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded declaration-count overflow"))?;
        }
        false if delta_tag == TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION => {
            statistics.negative_object_property_assertions = statistics
                .negative_object_property_assertions
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource(
                        "encoded negative-object-property-assertion count overflow",
                    )
                })?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_DATA_PROPERTY_ASSERTION => {
            statistics.data_property_assertions = statistics
                .data_property_assertions
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded data-property-assertion count overflow")
                })?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_NEGATIVE_DATA_PROPERTY_ASSERTION => {
            statistics.negative_data_property_assertions = statistics
                .negative_data_property_assertions
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded negative-data-property-assertion count overflow")
                })?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_SUB_DATA_PROPERTY_OF => {
            statistics.sub_data_properties = statistics
                .sub_data_properties
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded sub-data-property count overflow"))?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_EQUIVALENT_DATA_PROPERTIES => {
            statistics.equivalent_data_properties = statistics
                .equivalent_data_properties
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded equivalent-data-property count overflow")
                })?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_DISJOINT_DATA_PROPERTIES => {
            statistics.disjoint_data_properties = statistics
                .disjoint_data_properties
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded disjoint-data-property count overflow")
                })?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_DATA_PROPERTY_DOMAIN => {
            statistics.data_property_domains = statistics
                .data_property_domains
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded data-property-domain count overflow")
                })?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_DATA_PROPERTY_RANGE => {
            statistics.data_property_ranges = statistics
                .data_property_ranges
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded data-property-range count overflow")
                })?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_FUNCTIONAL_DATA_PROPERTY => {
            statistics.functional_data_properties = statistics
                .functional_data_properties
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded functional-data-property count overflow")
                })?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_DATATYPE_DEFINITION => {
            statistics.datatype_definitions = statistics
                .datatype_definitions
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded datatype-definition count overflow")
                })?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_HAS_KEY => {
            statistics.has_keys = statistics
                .has_keys
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded has-key count overflow"))?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if silent_object_property_root.is_some() => {
            let kind = silent_object_property_root.ok_or_else(|| {
                KernelError::malformed(
                    "encoded local-overlay object-property root lost its constructor",
                )
            })?;
            let constructor = kind.constructor();
            let count = kind
                .statistics_count(statistics)
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource(format!("encoded {constructor} count overflow"))
                })?;
            *kind.statistics_counter(statistics) = count;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if silent_annotation_property_root.is_some() => {
            let kind = silent_annotation_property_root.ok_or_else(|| {
                KernelError::malformed(
                    "encoded local-overlay annotation-property root lost its constructor",
                )
            })?;
            let constructor = kind.constructor();
            let count = kind
                .statistics_count(statistics)
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource(format!("encoded {constructor} count overflow"))
                })?;
            *kind.statistics_counter(statistics) = count;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if silent_class_disjointness_root.is_some() => {
            let kind = silent_class_disjointness_root.ok_or_else(|| {
                KernelError::malformed(
                    "encoded local-overlay class-disjointness root lost its constructor",
                )
            })?;
            let constructor = kind.constructor();
            let count = kind
                .statistics_count(statistics)
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource(format!("encoded {constructor} count overflow"))
                })?;
            *kind.statistics_counter(statistics) = count;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_SAME_INDIVIDUAL => {
            statistics.same_individuals = statistics
                .same_individuals
                .checked_add(1)
                .ok_or_else(|| KernelError::resource("encoded same-individual count overflow"))?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false if delta_tag == TAG_DIFFERENT_INDIVIDUALS => {
            statistics.different_individuals = statistics
                .different_individuals
                .checked_add(1)
                .ok_or_else(|| {
                    KernelError::resource("encoded different-individuals count overflow")
                })?;
            if !options.asserted_taxonomy_only {
                statistics.skipped_axioms = statistics
                    .skipped_axioms
                    .checked_add(1)
                    .ok_or_else(|| KernelError::resource("encoded skipped-axiom count overflow"))?;
            }
        }
        false => {
            return Err(KernelError::malformed(
                "encoded local-overlay silent root lost its constructor",
            ));
        }
    }
    statistics.domain_range_edges = statistics
        .domain_range_edges
        .checked_add(additional_domain_range_edges)
        .ok_or_else(|| KernelError::resource("encoded domain/range edge-count overflow"))?;
    statistics.role_expansion_edges = statistics
        .role_expansion_edges
        .checked_add(local_domain_range_role_expansion_edges)
        .ok_or_else(|| KernelError::resource("encoded role-expansion edge-count overflow"))?;
    statistics.edges = projected;
    statistics.buffer_bytes = statistics
        .buffer_bytes
        .checked_add(delta_columns.buffer_bytes()?)
        .ok_or_else(|| KernelError::resource("encoded buffer-byte total overflow"))?;
    prepared.preparation.overlay_deltas = overlay_deltas;
    Ok(prepared)
}

pub(crate) fn prepare_direct_batches_with_retained_role_state(
    columns: DirectColumns<'_>,
    root_annotation_columns: Option<DirectColumns<'_>>,
    options: DirectCompileOptions,
    state: &AtomicU8,
    retained: Option<&mut OwnedRoleState>,
) -> Result<PreparedDirectBatches, KernelError> {
    let prepared = prepare_direct_batches_uncommitted(
        columns,
        root_annotation_columns,
        options,
        state,
        retained.as_deref(),
    )?;
    if !options.asserted_taxonomy_only {
        if let Some(retained) = retained {
            *retained = prepared.try_clone_role_state()?;
        }
    }
    Ok(prepared)
}

#[cfg(test)]
pub(crate) fn compile_direct_with_retained_role_state(
    columns: DirectColumns<'_>,
    root_annotation_columns: Option<DirectColumns<'_>>,
    options: DirectCompileOptions,
    state: &AtomicU8,
    retained: Option<&mut OwnedRoleState>,
) -> Result<(Vec<DirectEdge>, DirectCompileStats), KernelError> {
    let mut prepared = prepare_direct_batches_uncommitted(
        columns,
        root_annotation_columns,
        options,
        state,
        retained.as_deref(),
    )?;
    let statistics = prepared.statistics();
    let mut edges = Vec::new();
    edges
        .try_reserve_exact(statistics.edges)
        .map_err(|_| KernelError::resource("encoded test output allocation failed"))?;
    while prepared.remaining_edges() != 0 {
        let (batch, cursor) =
            prepared.prepare_next_batch(columns, state, prepared.remaining_edges())?;
        edges.extend(batch);
        prepared.commit_cursor(cursor);
    }
    if !options.asserted_taxonomy_only {
        if let Some(retained) = retained {
            *retained = prepared.try_clone_role_state()?;
        }
    }
    Ok((edges, statistics))
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

fn annotation_edge(
    projection: AnnotationProjection<'_>,
    anonymous_ids: &AnonymousIds,
) -> Result<DirectEdge, KernelError> {
    let destination = match projection.value {
        AnnotationValue::Borrowed(value) => clone_text(value)?,
        AnnotationValue::Anonymous(node_id) => anonymous_ids.render(node_id)?,
        AnnotationValue::Typed { lexical, datatype } => {
            render_typed_annotation_literal(lexical, datatype)?
        }
    };
    Ok(DirectEdge {
        source: clone_text(projection.source)?,
        relation: clone_text(projection.relation)?,
        destination,
    })
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

fn queue_annotation_event(
    stack: &mut Vec<(usize, bool)>,
    node_id: usize,
    exiting: bool,
) -> Result<(), KernelError> {
    stack
        .try_reserve(1)
        .map_err(|_| KernelError::resource("encoded annotation graph stack allocation failed"))?;
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

        fn push_mixed_sequence(&mut self, items: &[FixtureItem<'_>]) {
            self.field_kinds.push(COMPONENT_SEQUENCE);
            self.field_values
                .extend_from_slice(&(self.item_kinds.len() as u64).to_le_bytes());
            self.field_lengths
                .extend_from_slice(&(items.len() as u64).to_le_bytes());
            for item in items {
                match item {
                    FixtureItem::None => {
                        self.item_kinds.push(COMPONENT_NONE);
                        self.item_values.extend_from_slice(&0_u64.to_le_bytes());
                        self.item_lengths.extend_from_slice(&0_u64.to_le_bytes());
                    }
                    FixtureItem::Node(node_id) => {
                        self.item_kinds.push(COMPONENT_NODE);
                        self.item_values.extend_from_slice(&node_id.to_le_bytes());
                        self.item_lengths.extend_from_slice(&0_u64.to_le_bytes());
                    }
                    FixtureItem::Scalar(kind, value) => {
                        self.item_kinds.push(*kind);
                        self.item_values
                            .extend_from_slice(&(self.scalar_bytes.len() as u64).to_le_bytes());
                        self.item_lengths
                            .extend_from_slice(&(value.len() as u64).to_le_bytes());
                        self.scalar_bytes.extend_from_slice(value);
                    }
                }
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

    enum FixtureItem<'a> {
        None,
        Node(u64),
        Scalar(u8, &'a [u8]),
    }

    #[derive(Clone, Copy)]
    enum SubclassPairShape {
        Taxonomy,
        Ignored,
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

    fn named_subclass_delta_fixture(source: &[u8], destination: &[u8]) -> Fixture {
        let mut fixture = Fixture::default();
        fixture.push_scalar(COMPONENT_TEXT, source);
        fixture.finish_node(TAG_IRI); // 1
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 2
        fixture.push_scalar(COMPONENT_TEXT, destination);
        fixture.finish_node(TAG_IRI); // 3
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY); // 4
        fixture.push_node_ref(2);
        fixture.push_node_ref(4);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 5
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture.root_ids.extend_from_slice(&5_u32.to_le_bytes());
        fixture
    }

    fn named_subclass_pair_delta_fixture(
        first_source: &[u8],
        first_destination: &[u8],
        second_source: &[u8],
        second_destination: &[u8],
        annotated: bool,
    ) -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            first_source,
            first_destination,
            second_source,
            second_destination,
            b"urn:label",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=5
        }
        for iri_id in [1_u64, 2, 3, 4] {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 6..=9
        }
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 10
        let annotation_ids = if annotated {
            fixture.push_node_ref(10);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION); // 11
            vec![11]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(6);
        fixture.push_node_ref(7);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_SUB_CLASS_OF);
        let first_root = fixture.node_tags.len() as u32 / 2;
        fixture.push_node_ref(8);
        fixture.push_node_ref(9);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF);
        let second_root = fixture.node_tags.len() as u32 / 2;
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        fixture
            .root_ids
            .extend_from_slice(&first_root.to_le_bytes());
        fixture
            .root_ids
            .extend_from_slice(&second_root.to_le_bytes());
        fixture
    }

    fn hostile_subclass_pair_delta_fixture(
        first: SubclassPairShape,
        second: SubclassPairShape,
    ) -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [b"urn:A".as_slice(), b"urn:B", b"urn:C", b"urn:D", b"urn:p"] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=5
        }
        for iri_id in [1_u64, 2, 3, 4] {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 6..=9
        }
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 10

        fn push_root(
            fixture: &mut Fixture,
            shape: SubclassPairShape,
            source: u64,
            destination: u64,
        ) -> u32 {
            let source = match shape {
                SubclassPairShape::Ignored => {
                    fixture.push_node_set(&[source, destination]);
                    fixture.finish_node(TAG_OBJECT_UNION_OF);
                    fixture.node_tags.len() as u64 / 2
                }
                SubclassPairShape::Taxonomy => source,
            };
            fixture.push_node_ref(source);
            fixture.push_node_ref(destination);
            fixture.push_empty_set();
            fixture.finish_node(TAG_SUB_CLASS_OF);
            fixture.node_tags.len() as u32 / 2
        }

        let first_root = push_root(&mut fixture, first, 6, 7);
        let second_root = push_root(&mut fixture, second, 8, 9);
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        fixture
            .root_ids
            .extend_from_slice(&first_root.to_le_bytes());
        fixture
            .root_ids
            .extend_from_slice(&second_root.to_le_bytes());
        fixture
    }

    fn named_restriction_delta_fixture(
        restriction_tag: u16,
        inverse_property: bool,
        restriction_first: bool,
        annotated: bool,
    ) -> Fixture {
        assert!([
            TAG_OBJECT_SOME_VALUES_FROM,
            TAG_OBJECT_ALL_VALUES_FROM,
            TAG_OBJECT_MIN_CARDINALITY,
            TAG_OBJECT_MAX_CARDINALITY,
        ]
        .contains(&restriction_tag));
        let mut fixture = Fixture::default();
        for iri in [b"urn:A".as_slice(), b"urn:B", b"urn:p", b"urn:label"] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=4
        }
        for iri_id in [1_u64, 2] {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 5..=6
        }
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY); // 7
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(4);
        fixture.finish_node(TAG_ENTITY); // 8

        let property_id = if inverse_property {
            fixture.push_node_ref(7);
            fixture.finish_node(TAG_OBJECT_INVERSE_OF); // 9
            9
        } else {
            7
        };
        if [TAG_OBJECT_MIN_CARDINALITY, TAG_OBJECT_MAX_CARDINALITY].contains(&restriction_tag) {
            fixture.push_scalar(COMPONENT_INTEGER, &[1]);
        }
        fixture.push_node_ref(property_id);
        fixture.push_node_ref(6);
        fixture.finish_node(restriction_tag);
        let restriction_id = (fixture.node_tags.len() / 2) as u64;

        let annotation_ids = if annotated {
            fixture.push_node_ref(8);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![(fixture.node_tags.len() / 2) as u64]
        } else {
            Vec::new()
        };
        if restriction_first {
            fixture.push_node_ref(restriction_id);
            fixture.push_node_ref(5);
        } else {
            fixture.push_node_ref(5);
            fixture.push_node_ref(restriction_id);
        }
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_SUB_CLASS_OF);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn overlay_role_base_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [b"urn:p".as_slice(), b"urn:child", b"urn:pinv"] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=3
        }
        for iri_id in 1_u64..=3 {
            fixture.push_scalar(COMPONENT_ENUM, b"object_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 4..=6
        }
        fixture.push_node_ref(5);
        fixture.push_node_ref(4);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_OBJECT_PROPERTY_OF); // 7
        fixture.push_node_ref(4);
        fixture.push_node_ref(6);
        fixture.push_empty_set();
        fixture.finish_node(TAG_INVERSE_OBJECT_PROPERTIES); // 8
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        for root_id in [7_u32, 8] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn local_object_property_class_pair_delta_fixture(
        second_tag: u16,
        same_property: bool,
        annotated: bool,
    ) -> Fixture {
        assert!([TAG_OBJECT_PROPERTY_DOMAIN, TAG_OBJECT_PROPERTY_RANGE].contains(&second_tag));
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:D".as_slice(),
            b"urn:R",
            b"urn:p",
            b"urn:q",
            b"urn:label",
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
            fixture.push_scalar(COMPONENT_ENUM, b"object_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 8..=9
        }
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 10

        let annotation_ids = if annotated {
            fixture.push_node_ref(10);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION); // 11
            vec![11]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(8);
        fixture.push_node_ref(6);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_OBJECT_PROPERTY_DOMAIN);
        let domain_id = (fixture.node_tags.len() / 2) as u32;

        fixture.push_node_ref(if same_property { 8 } else { 9 });
        fixture.push_node_ref(7);
        fixture.push_empty_set();
        fixture.finish_node(second_tag);
        let second_id = (fixture.node_tags.len() / 2) as u32;

        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        for root_id in [domain_id, second_id] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn named_class_assertions_fixture(
        assertions: &[(&[u8], &[u8])],
        annotate_first: bool,
    ) -> Fixture {
        assert!(!assertions.is_empty());
        let mut fixture = Fixture::default();
        for (class_iri, _individual_iri) in assertions {
            fixture.push_scalar(COMPONENT_TEXT, class_iri);
            fixture.finish_node(TAG_IRI);
        }
        for (_class_iri, individual_iri) in assertions {
            fixture.push_scalar(COMPONENT_TEXT, individual_iri);
            fixture.finish_node(TAG_IRI);
        }
        let assertion_count = assertions.len() as u64;
        for iri_id in 1..=assertion_count {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY);
        }
        for iri_id in assertion_count + 1..=assertion_count * 2 {
            fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY);
        }

        let annotation_id = if annotate_first {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI);
            let label_iri = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(label_iri);
            fixture.finish_node(TAG_ENTITY);
            let annotation_property = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(annotation_property);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            Some(fixture.node_tags.len() as u64 / 2)
        } else {
            None
        };
        for index in 0..assertions.len() {
            fixture.push_node_ref(assertion_count * 2 + index as u64 + 1);
            fixture.push_node_ref(assertion_count * 3 + index as u64 + 1);
            let annotation_ids = if index == 0 {
                annotation_id.into_iter().collect::<Vec<_>>()
            } else {
                Vec::new()
            };
            fixture.push_node_set(&annotation_ids);
            fixture.finish_node(TAG_CLASS_ASSERTION);
            fixture.root_kinds.push(ROOT_AXIOM);
            fixture
                .root_ids
                .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        }
        fixture
    }

    fn named_class_assertion_delta_fixture(
        class_iri: &[u8],
        individual_iri: &[u8],
        annotated: bool,
    ) -> Fixture {
        named_class_assertions_fixture(&[(class_iri, individual_iri)], annotated)
    }

    fn named_class_assertion_pair_delta_fixture(
        first_class: &[u8],
        first_individual: &[u8],
        second_class: &[u8],
        second_individual: &[u8],
        annotated: bool,
    ) -> Fixture {
        named_class_assertions_fixture(
            &[
                (first_class, first_individual),
                (second_class, second_individual),
            ],
            annotated,
        )
    }

    fn named_class_assertion_base_fixture() -> Fixture {
        named_class_assertions_fixture(
            &[
                (b"urn:A".as_slice(), b"urn:i".as_slice()),
                (b"urn:C".as_slice(), b"urn:k".as_slice()),
            ],
            false,
        )
    }

    fn ignored_class_assertion_pair_delta_fixture(anonymous: bool) -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [b"urn:B".as_slice(), b"urn:D", b"urn:j", b"urn:l", b"urn:p"] {
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

        fixture.push_node_ref(6);
        fixture.push_node_ref(8);
        fixture.push_empty_set();
        fixture.finish_node(TAG_CLASS_ASSERTION); // 11

        let (class, individual) = if anonymous {
            fixture.push_scalar(COMPONENT_BYTES, &[7; 32]);
            fixture.push_scalar(COMPONENT_BYTES, b"local");
            fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL); // 12
            (7, 12)
        } else {
            fixture.push_node_ref(10);
            fixture.push_node_ref(7);
            fixture.finish_node(TAG_OBJECT_SOME_VALUES_FROM); // 12
            (12, 9)
        };
        fixture.push_node_ref(class);
        fixture.push_node_ref(individual);
        fixture.push_empty_set();
        fixture.finish_node(TAG_CLASS_ASSERTION); // 13
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        for root_id in [11_u32, 13] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn mixed_emitting_delta_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:B".as_slice(),
            b"urn:C",
            b"urn:D",
            b"urn:Y",
            b"urn:Top",
            b"urn:b",
            b"urn:z",
            b"urn:a",
            b"urn:p",
            b"urn:q",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=10
        }
        for iri_id in 1_u64..=5 {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 11..=15
        }
        for iri_id in 6_u64..=8 {
            fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 16..=18
        }
        for iri_id in 9_u64..=10 {
            fixture.push_scalar(COMPONENT_ENUM, b"object_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 19..=20
        }
        fixture.push_node_ref(19);
        fixture.push_node_ref(13);
        fixture.finish_node(TAG_OBJECT_SOME_VALUES_FROM); // 21

        fixture.push_node_ref(11);
        fixture.push_node_ref(15);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 22
        fixture.push_node_ref(12);
        fixture.push_node_ref(21);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 23
        fixture.push_node_ref(14);
        fixture.push_node_ref(15);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 24
        fixture.push_node_ref(11);
        fixture.push_node_ref(16);
        fixture.push_empty_set();
        fixture.finish_node(TAG_CLASS_ASSERTION); // 25
        fixture.push_node_ref(20);
        fixture.push_node_ref(17);
        fixture.push_node_ref(18);
        fixture.push_empty_set();
        fixture.finish_node(TAG_OBJECT_PROPERTY_ASSERTION); // 26
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 5]);
        for root_id in 22_u32..=26 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn mixed_emitting_base_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"urn:Z",
            b"urn:Top",
            b"urn:a",
            b"urn:z",
            b"urn:p",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=6
        }
        for iri_id in 1_u64..=3 {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 7..=9
        }
        for iri_id in 4_u64..=5 {
            fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 10..=11
        }
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(6);
        fixture.finish_node(TAG_ENTITY); // 12

        for source in [7_u64, 8] {
            fixture.push_node_ref(source);
            fixture.push_node_ref(9);
            fixture.push_empty_set();
            fixture.finish_node(TAG_SUB_CLASS_OF); // 13..=14
        }
        for (class, individual) in [(7_u64, 10_u64), (8, 11)] {
            fixture.push_node_ref(class);
            fixture.push_node_ref(individual);
            fixture.push_empty_set();
            fixture.finish_node(TAG_CLASS_ASSERTION); // 15..=16
        }
        fixture.push_node_ref(12);
        fixture.push_node_ref(10);
        fixture.push_node_ref(11);
        fixture.push_empty_set();
        fixture.finish_node(TAG_OBJECT_PROPERTY_ASSERTION); // 17
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 5]);
        for root_id in 13_u32..=17 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn ignored_class_axiom_delta_fixture(
        root_tag: u16,
        recursive: bool,
        annotated: bool,
        anonymous: bool,
    ) -> Fixture {
        assert!([TAG_SUB_CLASS_OF, TAG_CLASS_ASSERTION].contains(&root_tag));
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"urn:B",
            b"urn:C",
            b"urn:i",
            b"urn:label",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=5
        }
        for iri_id in [1_u64, 2, 3] {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 6..=8
        }
        fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
        fixture.push_node_ref(4);
        fixture.finish_node(TAG_ENTITY); // 9
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 10

        fixture.push_node_set(&[7, 8]);
        fixture.finish_node(TAG_OBJECT_INTERSECTION_OF); // 11
        let mut ignored_class_id = 11_u64;
        if recursive {
            fixture.push_node_ref(ignored_class_id);
            fixture.finish_node(TAG_OBJECT_COMPLEMENT_OF); // 12
            ignored_class_id = 12;
        }

        let mut individual_id = 9_u64;
        if anonymous {
            fixture.push_scalar(COMPONENT_BYTES, &[7; 32]);
            fixture.push_scalar(COMPONENT_BYTES, b"local");
            fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL);
            let anonymous_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_set(&[anonymous_id]);
            fixture.finish_node(TAG_OBJECT_ONE_OF);
            ignored_class_id = fixture.node_tags.len() as u64 / 2;
            individual_id = anonymous_id;
        }

        let annotation_ids = if annotated {
            fixture.push_node_ref(10);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        if root_tag == TAG_SUB_CLASS_OF {
            fixture.push_node_ref(6);
            fixture.push_node_ref(ignored_class_id);
        } else {
            fixture.push_node_ref(ignored_class_id);
            fixture.push_node_ref(individual_id);
        }
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(root_tag);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn ignored_equivalent_classes_delta_fixture(
        member_count: usize,
        recursive: bool,
        annotated: bool,
        anonymous: bool,
    ) -> Fixture {
        assert!((2..=4).contains(&member_count));
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"urn:B",
            b"urn:C",
            b"urn:p",
            b"urn:label",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=5
        }
        for iri_id in [1_u64, 2, 3] {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 6..=8
        }
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(4);
        fixture.finish_node(TAG_ENTITY); // 9
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 10

        let filler_id = if recursive {
            fixture.push_node_set(&[7, 8]);
            fixture.finish_node(TAG_OBJECT_INTERSECTION_OF);
            fixture.node_tags.len() as u64 / 2
        } else {
            7
        };
        let mut member_ids = vec![6];

        if member_count >= 3 {
            fixture.push_node_ref(7);
            fixture.finish_node(TAG_OBJECT_COMPLEMENT_OF);
            member_ids.push(fixture.node_tags.len() as u64 / 2);
        }
        if member_count == 4 {
            fixture.push_node_ref(8);
            fixture.finish_node(TAG_OBJECT_COMPLEMENT_OF);
            member_ids.push(fixture.node_tags.len() as u64 / 2);
        }
        fixture.push_node_ref(9);
        fixture.push_node_ref(filler_id);
        fixture.finish_node(TAG_OBJECT_SOME_VALUES_FROM);
        let restriction_id = fixture.node_tags.len() as u64 / 2;
        member_ids.push(restriction_id);
        if anonymous {
            fixture.push_scalar(COMPONENT_BYTES, &[7; 32]);
            fixture.push_scalar(COMPONENT_BYTES, b"local");
            fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL);
            let anonymous_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_set(&[anonymous_id]);
            fixture.finish_node(TAG_OBJECT_ONE_OF);
            *member_ids
                .last_mut()
                .expect("equivalent-class fixture has a non-named member") =
                fixture.node_tags.len() as u64 / 2;
        }

        let annotation_ids = if annotated {
            fixture.push_node_ref(10);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        fixture.push_node_set(&member_ids);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_EQUIVALENT_CLASSES);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn ignored_object_property_class_delta_fixture(
        root_tag: u16,
        inverse_property: bool,
        recursive_class: bool,
        annotated: bool,
        anonymous: bool,
    ) -> Fixture {
        assert!([TAG_OBJECT_PROPERTY_DOMAIN, TAG_OBJECT_PROPERTY_RANGE].contains(&root_tag));
        let mut fixture = Fixture::default();
        for iri in [b"urn:A".as_slice(), b"urn:B", b"urn:p", b"urn:label"] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=4
        }
        for iri_id in [1_u64, 2] {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 5..=6
        }
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY); // 7
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(4);
        fixture.finish_node(TAG_ENTITY); // 8

        let property_id = if inverse_property {
            fixture.push_node_ref(7);
            fixture.finish_node(TAG_OBJECT_INVERSE_OF);
            fixture.node_tags.len() as u64 / 2
        } else {
            7
        };
        let class_id = if anonymous {
            fixture.push_scalar(COMPONENT_BYTES, &[9; 32]);
            fixture.push_scalar(COMPONENT_BYTES, b"local");
            fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL);
            let anonymous_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_set(&[anonymous_id]);
            fixture.finish_node(TAG_OBJECT_ONE_OF);
            fixture.node_tags.len() as u64 / 2
        } else if recursive_class {
            fixture.push_node_set(&[5, 6]);
            fixture.finish_node(TAG_OBJECT_INTERSECTION_OF);
            let aggregate_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(aggregate_id);
            fixture.finish_node(TAG_OBJECT_COMPLEMENT_OF);
            fixture.node_tags.len() as u64 / 2
        } else {
            5
        };
        let annotation_ids = if annotated {
            fixture.push_node_ref(8);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(property_id);
        fixture.push_node_ref(class_id);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(root_tag);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn local_role_delta_fixture(
        root_tag: u16,
        chain_length: usize,
        inverse_member: bool,
        annotated: bool,
    ) -> Fixture {
        assert!([TAG_SUB_OBJECT_PROPERTY_OF, TAG_INVERSE_OBJECT_PROPERTIES].contains(&root_tag));
        if root_tag == TAG_SUB_OBJECT_PROPERTY_OF {
            assert!(chain_length == 0 || (2..=3).contains(&chain_length));
        } else {
            assert_eq!(chain_length, 0);
        }
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:p".as_slice(),
            b"urn:q",
            b"urn:r",
            b"urn:super",
            b"urn:label",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=5
        }
        for iri_id in 1_u64..=4 {
            fixture.push_scalar(COMPONENT_ENUM, b"object_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 6..=9
        }
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 10
        fixture.push_node_ref(7);
        fixture.finish_node(TAG_OBJECT_INVERSE_OF); // 11

        let first_id = if chain_length == 0 {
            6
        } else {
            let mut members = vec![6, if inverse_member { 11 } else { 7 }];
            if chain_length == 3 {
                members.push(8);
            }
            fixture.push_node_sequence(&members);
            fixture.finish_node(TAG_OBJECT_PROPERTY_CHAIN);
            fixture.node_tags.len() as u64 / 2
        };
        let annotation_ids = if annotated {
            fixture.push_node_ref(10);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(first_id);
        fixture.push_node_ref(9);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(root_tag);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn local_object_property_class_base_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:D".as_slice(),
            b"urn:R",
            b"urn:p",
            b"urn:child",
            b"urn:pinv",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=5
        }
        for iri_id in [1_u64, 2] {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 6..=7
        }
        for iri_id in [3_u64, 4, 5] {
            fixture.push_scalar(COMPONENT_ENUM, b"object_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 8..=10
        }
        for (tag, first, second) in [
            (TAG_SUB_OBJECT_PROPERTY_OF, 9_u64, 8_u64),
            (TAG_INVERSE_OBJECT_PROPERTIES, 8, 10),
            (TAG_OBJECT_PROPERTY_DOMAIN, 8, 6),
            (TAG_OBJECT_PROPERTY_RANGE, 8, 7),
        ] {
            fixture.push_node_ref(first);
            fixture.push_node_ref(second);
            fixture.push_empty_set();
            fixture.finish_node(tag); // 11..=14
        }
        fixture.root_kinds.extend_from_slice(&[ROOT_AXIOM; 4]);
        for root_id in 11_u32..=14 {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn local_role_projection_base_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"urn:Top",
            b"urn:C",
            b"urn:D",
            b"urn:super",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=5
        }
        for iri_id in 1_u64..=4 {
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 6..=9
        }
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(5);
        fixture.finish_node(TAG_ENTITY); // 10
        fixture.push_node_ref(10);
        fixture.push_node_ref(9);
        fixture.finish_node(TAG_OBJECT_SOME_VALUES_FROM); // 11
        fixture.push_node_ref(6);
        fixture.push_node_ref(7);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 12
        fixture.push_node_ref(8);
        fixture.push_node_ref(11);
        fixture.push_empty_set();
        fixture.finish_node(TAG_SUB_CLASS_OF); // 13
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        for root_id in [12_u32, 13] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn local_annotation_delta_fixture(
        kind: LocalAnnotationRuleKind,
        literal_value: bool,
        annotated: bool,
        anonymous: bool,
    ) -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:label".as_slice(),
            b"urn:subject",
            b"urn:value",
            XSD_STRING.as_bytes(),
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=4
        }
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 5
        fixture.push_scalar(COMPONENT_ENUM, b"datatype");
        fixture.push_node_ref(4);
        fixture.finish_node(TAG_ENTITY); // 6
        fixture.push_scalar(COMPONENT_TEXT, b"annotation-value");
        fixture.push_node_ref(6);
        fixture.push_none();
        fixture.finish_node(TAG_LITERAL); // 7
        fixture.push_scalar(COMPONENT_BYTES, &[11; 32]);
        fixture.push_scalar(COMPONENT_BYTES, b"local");
        fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL); // 8

        let annotation_ids = if annotated {
            fixture.push_node_ref(5);
            fixture.push_node_ref(3);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION); // 9
            vec![9]
        } else {
            Vec::new()
        };
        let value_id = if anonymous {
            8
        } else if literal_value {
            7
        } else {
            3
        };
        match kind {
            LocalAnnotationRuleKind::OntologyAnnotation => {
                fixture.push_node_ref(5);
                fixture.push_node_ref(value_id);
                fixture.push_node_set(&annotation_ids);
                fixture.finish_node(TAG_ANNOTATION);
                fixture.root_kinds.push(ROOT_ONTOLOGY_ANNOTATION);
            }
            LocalAnnotationRuleKind::Assertion => {
                fixture.push_node_ref(5);
                fixture.push_node_ref(if anonymous { 8 } else { 2 });
                fixture.push_node_ref(if anonymous { 3 } else { value_id });
                fixture.push_node_set(&annotation_ids);
                fixture.finish_node(TAG_ANNOTATION_ASSERTION);
                fixture.root_kinds.push(ROOT_AXIOM);
            }
        }
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn named_object_property_assertion_delta_fixture(
        property_iri: &[u8],
        source_iri: &[u8],
        destination_iri: &[u8],
        annotated: bool,
    ) -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [property_iri, source_iri, destination_iri] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=3
        }
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 4
        for iri_id in [2_u64, 3] {
            fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 5..=6
        }

        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI); // 7
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(7);
            fixture.finish_node(TAG_ENTITY); // 8
            fixture.push_node_ref(8);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION); // 9
            vec![9]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(4);
        fixture.push_node_ref(5);
        fixture.push_node_ref(6);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_OBJECT_PROPERTY_ASSERTION);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn anonymous_annotated_object_property_assertion_delta_fixture() -> Fixture {
        let mut fixture =
            named_object_property_assertion_delta_fixture(b"urn:p", b"urn:j", b"urn:B", true);
        fixture.push_scalar(COMPONENT_BYTES, &[7_u8; 32]);
        fixture.push_scalar(COMPONENT_BYTES, b"annotation-local");
        fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL); // 11

        let annotation_start =
            read_usize(&fixture.node_field_offsets, 8, "annotation offset").unwrap();
        let value_field = annotation_start + 1;
        fixture.field_values[value_field * 8..value_field * 8 + 8]
            .copy_from_slice(&11_u64.to_le_bytes());
        fixture
    }

    fn named_object_property_assertion_base_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [b"urn:p".as_slice(), b"urn:i", b"urn:A", b"urn:k", b"urn:C"] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=5
        }
        fixture.push_scalar(COMPONENT_ENUM, b"object_property");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 6
        for iri_id in [2_u64, 3, 4, 5] {
            fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY); // 7..=10
        }
        for (source, destination) in [(7_u64, 8_u64), (9, 10)] {
            fixture.push_node_ref(6);
            fixture.push_node_ref(source);
            fixture.push_node_ref(destination);
            fixture.push_empty_set();
            fixture.finish_node(TAG_OBJECT_PROPERTY_ASSERTION); // 11..=12
        }
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        for root_id in [11_u32, 12] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn inverse_object_property_assertion_delta_fixture() -> Fixture {
        let mut fixture =
            named_object_property_assertion_delta_fixture(b"urn:p", b"urn:j", b"urn:B", false);
        let assertion_field =
            read_usize(&fixture.node_field_offsets, 6, "offset").expect("assertion field offset");
        fixture.push_node_ref(4);
        fixture.finish_node(TAG_OBJECT_INVERSE_OF); // 8
        let assertion_start = assertion_field * 8;
        fixture.field_values[assertion_start..assertion_start + 8]
            .copy_from_slice(&8_u64.to_le_bytes());
        fixture
    }

    fn negative_object_property_assertion_delta_fixture(
        property_iri: &[u8],
        source_iri: &[u8],
        destination_iri: &[u8],
        inverse_property: bool,
        annotated: bool,
    ) -> Fixture {
        let mut fixture = named_object_property_assertion_delta_fixture(
            property_iri,
            source_iri,
            destination_iri,
            annotated,
        );
        let root_id =
            u32::from_le_bytes(fixture.root_ids[0..4].try_into().expect("delta root id")) as usize;
        let tag_start = (root_id - 1) * 2;
        fixture.node_tags[tag_start..tag_start + 2]
            .copy_from_slice(&TAG_NEGATIVE_OBJECT_PROPERTY_ASSERTION.to_le_bytes());
        if inverse_property {
            let assertion_field = read_usize(&fixture.node_field_offsets, root_id - 1, "offset")
                .expect("negative assertion field offset");
            fixture.push_node_ref(4);
            fixture.finish_node(TAG_OBJECT_INVERSE_OF);
            let inverse_id = fixture.node_tags.len() as u64 / 2;
            let assertion_start = assertion_field * 8;
            fixture.field_values[assertion_start..assertion_start + 8]
                .copy_from_slice(&inverse_id.to_le_bytes());
        }
        fixture
    }

    fn silent_object_property_delta_fixture(
        root_tag: u16,
        property_iris: &[&[u8]],
        inverse_mask: u8,
        annotated: bool,
    ) -> Fixture {
        let is_set = [
            TAG_EQUIVALENT_OBJECT_PROPERTIES,
            TAG_DISJOINT_OBJECT_PROPERTIES,
        ]
        .contains(&root_tag);
        assert!(is_set || is_object_property_characteristic(root_tag));
        assert!((is_set && property_iris.len() >= 2) || (!is_set && property_iris.len() == 1));
        assert!(property_iris.len() <= u8::BITS as usize);
        let mut fixture = Fixture::default();
        let mut property_ids = Vec::with_capacity(property_iris.len());
        for (index, iri) in property_iris.iter().enumerate() {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI);
            let iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"object_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY);
            let named_id = fixture.node_tags.len() as u64 / 2;
            if inverse_mask & (1_u8 << index) != 0 {
                fixture.push_node_ref(named_id);
                fixture.finish_node(TAG_OBJECT_INVERSE_OF);
            }
            property_ids.push(fixture.node_tags.len() as u64 / 2);
        }
        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI);
            let property_iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(property_iri_id);
            fixture.finish_node(TAG_ENTITY);
            let property_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(property_id);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        if is_set {
            fixture.push_node_set(&property_ids);
        } else {
            fixture.push_node_ref(property_ids[0]);
        }
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(root_tag);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn silent_annotation_property_delta_fixture(root_tag: u16, annotated: bool) -> Fixture {
        assert!([
            TAG_SUB_ANNOTATION_PROPERTY_OF,
            TAG_ANNOTATION_PROPERTY_DOMAIN,
            TAG_ANNOTATION_PROPERTY_RANGE,
        ]
        .contains(&root_tag));
        let mut fixture = Fixture::default();
        for iri in [b"urn:annotation-property".as_slice(), b"urn:target"] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=2
        }
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 3
        let target_id = if root_tag == TAG_SUB_ANNOTATION_PROPERTY_OF {
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(2);
            fixture.finish_node(TAG_ENTITY); // 4
            4
        } else {
            2
        };
        let annotation_ids = if annotated {
            fixture.push_node_ref(3);
            fixture.push_node_ref(2);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(3);
        fixture.push_node_ref(target_id);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(root_tag);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn silent_class_disjointness_delta_fixture(
        root_tag: u16,
        member_count: usize,
        recursive_member: bool,
        annotated: bool,
    ) -> Fixture {
        assert!([TAG_DISJOINT_CLASSES, TAG_DISJOINT_UNION].contains(&root_tag));
        assert!(member_count >= 2);
        assert!(member_count <= 4);
        let mut fixture = Fixture::default();
        let mut class_ids = Vec::with_capacity(member_count + 1);
        if root_tag == TAG_DISJOINT_UNION {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:Defined");
            fixture.finish_node(TAG_IRI);
            let iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY);
            class_ids.push(fixture.node_tags.len() as u64 / 2);
        }
        for iri in [b"urn:A".as_slice(), b"urn:B", b"urn:C", b"urn:D"]
            .iter()
            .take(member_count)
        {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI);
            let iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"class");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY);
            class_ids.push(fixture.node_tags.len() as u64 / 2);
        }
        let defined_id = (root_tag == TAG_DISJOINT_UNION).then_some(class_ids[0]);
        let member_start = usize::from(defined_id.is_some());
        let mut member_ids = class_ids[member_start..].to_vec();
        if recursive_member {
            let final_member = member_ids
                .last_mut()
                .expect("disjointness member envelope is nonempty");
            fixture.push_node_ref(*final_member);
            fixture.finish_node(TAG_OBJECT_COMPLEMENT_OF);
            *final_member = fixture.node_tags.len() as u64 / 2;
        }
        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI);
            let property_iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(property_iri_id);
            fixture.finish_node(TAG_ENTITY);
            let property_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(property_id);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        if let Some(defined_id) = defined_id {
            fixture.push_node_ref(defined_id);
        }
        fixture.push_node_set(&member_ids);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(root_tag);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn data_property_assertion_delta_fixture(
        root_tag: u16,
        property_iri: &[u8],
        source_iri: &[u8],
        lexical: &[u8],
        datatype_iri: &[u8],
        annotated: bool,
    ) -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [property_iri, source_iri, datatype_iri] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=3
        }
        fixture.push_scalar(COMPONENT_ENUM, b"data_property");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 4
        fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
        fixture.push_node_ref(2);
        fixture.finish_node(TAG_ENTITY); // 5
        fixture.push_scalar(COMPONENT_ENUM, b"datatype");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY); // 6
        fixture.push_scalar(COMPONENT_TEXT, lexical);
        fixture.push_node_ref(6);
        fixture.push_none();
        fixture.finish_node(TAG_LITERAL); // 7

        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI); // 8
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(8);
            fixture.finish_node(TAG_ENTITY); // 9
            fixture.push_node_ref(9);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION); // 10
            vec![10]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(4);
        fixture.push_node_ref(5);
        fixture.push_node_ref(7);
        fixture.push_node_set(&annotation_ids);
        assert!([
            TAG_DATA_PROPERTY_ASSERTION,
            TAG_NEGATIVE_DATA_PROPERTY_ASSERTION
        ]
        .contains(&root_tag));
        fixture.finish_node(root_tag);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn sub_data_property_delta_fixture(
        sub_property_iri: &[u8],
        super_property_iri: &[u8],
        annotated: bool,
    ) -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [sub_property_iri, super_property_iri] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI);
            let iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"data_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY);
        }
        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI); // 5
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(5);
            fixture.finish_node(TAG_ENTITY); // 6
            fixture.push_node_ref(6);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION); // 7
            vec![7]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(2);
        fixture.push_node_ref(4);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_SUB_DATA_PROPERTY_OF);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn data_property_set_delta_fixture(
        root_tag: u16,
        property_iris: &[&[u8]],
        annotated: bool,
    ) -> Fixture {
        assert!([TAG_EQUIVALENT_DATA_PROPERTIES, TAG_DISJOINT_DATA_PROPERTIES].contains(&root_tag));
        assert!(property_iris.len() >= 2);
        let mut fixture = Fixture::default();
        let mut property_ids = Vec::with_capacity(property_iris.len());
        for iri in property_iris {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI);
            let iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"data_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY);
            property_ids.push(fixture.node_tags.len() as u64 / 2);
        }
        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI);
            let property_iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(property_iri_id);
            fixture.finish_node(TAG_ENTITY);
            let property_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(property_id);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        fixture.push_node_set(&property_ids);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(root_tag);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn data_property_domain_delta_fixture(complex_domain: bool, annotated: bool) -> Fixture {
        let mut fixture = Fixture::default();
        fixture.push_scalar(COMPONENT_TEXT, b"urn:dp");
        fixture.finish_node(TAG_IRI);
        fixture.push_scalar(COMPONENT_ENUM, b"data_property");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY);
        fixture.push_scalar(COMPONENT_TEXT, b"urn:Domain");
        fixture.finish_node(TAG_IRI);
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY);
        let domain_id = if complex_domain {
            fixture.push_node_ref(4);
            fixture.finish_node(TAG_OBJECT_COMPLEMENT_OF);
            5
        } else {
            4
        };
        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI);
            let property_iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(property_iri_id);
            fixture.finish_node(TAG_ENTITY);
            let property_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(property_id);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(2);
        fixture.push_node_ref(domain_id);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_DATA_PROPERTY_DOMAIN);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn data_property_range_delta_fixture(complex_range: bool, annotated: bool) -> Fixture {
        let mut fixture = Fixture::default();
        fixture.push_scalar(COMPONENT_TEXT, b"urn:dp");
        fixture.finish_node(TAG_IRI);
        fixture.push_scalar(COMPONENT_ENUM, b"data_property");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY);
        fixture.push_scalar(COMPONENT_TEXT, b"http://www.w3.org/2001/XMLSchema#string");
        fixture.finish_node(TAG_IRI);
        fixture.push_scalar(COMPONENT_ENUM, b"datatype");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY);
        let range_id = if complex_range {
            fixture.push_node_ref(4);
            fixture.finish_node(TAG_DATA_COMPLEMENT_OF);
            5
        } else {
            4
        };
        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI);
            let property_iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(property_iri_id);
            fixture.finish_node(TAG_ENTITY);
            let property_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(property_id);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(2);
        fixture.push_node_ref(range_id);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_DATA_PROPERTY_RANGE);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn functional_data_property_delta_fixture(annotated: bool) -> Fixture {
        let mut fixture = Fixture::default();
        fixture.push_scalar(COMPONENT_TEXT, b"urn:dp");
        fixture.finish_node(TAG_IRI);
        fixture.push_scalar(COMPONENT_ENUM, b"data_property");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY);
        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI);
            let property_iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(property_iri_id);
            fixture.finish_node(TAG_ENTITY);
            let property_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(property_id);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(2);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_FUNCTIONAL_DATA_PROPERTY);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn datatype_definition_delta_fixture(complex_range: bool, annotated: bool) -> Fixture {
        let mut fixture = Fixture::default();
        fixture.push_scalar(COMPONENT_TEXT, b"urn:custom");
        fixture.finish_node(TAG_IRI);
        fixture.push_scalar(COMPONENT_ENUM, b"datatype");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY);
        fixture.push_scalar(COMPONENT_TEXT, b"http://www.w3.org/2001/XMLSchema#string");
        fixture.finish_node(TAG_IRI);
        fixture.push_scalar(COMPONENT_ENUM, b"datatype");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY);
        let range_id = if complex_range {
            fixture.push_node_ref(4);
            fixture.finish_node(TAG_DATA_COMPLEMENT_OF);
            5
        } else {
            4
        };
        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI);
            let property_iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(property_iri_id);
            fixture.finish_node(TAG_ENTITY);
            let property_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(property_id);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        fixture.push_node_ref(2);
        fixture.push_node_ref(range_id);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_DATATYPE_DEFINITION);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn has_key_delta_fixture(
        object_property_count: usize,
        data_property_count: usize,
        complex_class: bool,
        inverse_object_property: bool,
        annotated: bool,
    ) -> Fixture {
        assert!(object_property_count <= 2);
        assert!(data_property_count <= 2);
        assert!(!complex_class || object_property_count > 0);
        assert!(!inverse_object_property || object_property_count == 1);
        let mut fixture = Fixture::default();

        fixture.push_scalar(COMPONENT_TEXT, b"urn:KeyClass");
        fixture.finish_node(TAG_IRI);
        let key_iri_id = fixture.node_tags.len() as u64 / 2;
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(key_iri_id);
        fixture.finish_node(TAG_ENTITY);
        let key_class_id = fixture.node_tags.len() as u64 / 2;

        fixture.push_scalar(COMPONENT_TEXT, b"urn:Filler");
        fixture.finish_node(TAG_IRI);
        let filler_iri_id = fixture.node_tags.len() as u64 / 2;
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(filler_iri_id);
        fixture.finish_node(TAG_ENTITY);
        let filler_class_id = fixture.node_tags.len() as u64 / 2;

        let mut named_object_ids = Vec::with_capacity(object_property_count);
        for iri in [b"urn:op".as_slice(), b"urn:oq"]
            .into_iter()
            .take(object_property_count)
        {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI);
            let iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"object_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY);
            named_object_ids.push(fixture.node_tags.len() as u64 / 2);
        }
        let mut object_ids = named_object_ids.clone();
        if inverse_object_property {
            fixture.push_node_ref(named_object_ids[0]);
            fixture.finish_node(TAG_OBJECT_INVERSE_OF);
            object_ids[0] = fixture.node_tags.len() as u64 / 2;
        }

        let mut data_ids = Vec::with_capacity(data_property_count);
        for iri in [b"urn:dp".as_slice(), b"urn:dq"]
            .into_iter()
            .take(data_property_count)
        {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI);
            let iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"data_property");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY);
            data_ids.push(fixture.node_tags.len() as u64 / 2);
        }

        let class_id = if complex_class {
            fixture.push_node_ref(named_object_ids[0]);
            fixture.push_node_ref(filler_class_id);
            fixture.finish_node(TAG_OBJECT_SOME_VALUES_FROM);
            fixture.node_tags.len() as u64 / 2
        } else {
            key_class_id
        };

        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI);
            let property_iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(property_iri_id);
            fixture.finish_node(TAG_ENTITY);
            let property_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(property_id);
            fixture.push_node_ref(key_iri_id);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };

        fixture.push_node_ref(class_id);
        fixture.push_node_set(&object_ids);
        fixture.push_node_set(&data_ids);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(TAG_HAS_KEY);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn individual_set_delta_fixture(
        tag: u16,
        individual_iris: &[&[u8]],
        anonymous_member: bool,
        annotated: bool,
    ) -> Fixture {
        assert!([TAG_SAME_INDIVIDUAL, TAG_DIFFERENT_INDIVIDUALS].contains(&tag));
        assert!(individual_iris.len() >= 2);
        let mut fixture = Fixture::default();
        let mut individual_ids =
            Vec::with_capacity(individual_iris.len() + usize::from(anonymous_member));
        for iri in individual_iris {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI);
            let iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"named_individual");
            fixture.push_node_ref(iri_id);
            fixture.finish_node(TAG_ENTITY);
            individual_ids.push(fixture.node_tags.len() as u64 / 2);
        }
        if anonymous_member {
            fixture.push_scalar(COMPONENT_BYTES, &[7; 32]);
            fixture.push_scalar(COMPONENT_BYTES, b"anonymous");
            fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL);
            individual_ids.push(fixture.node_tags.len() as u64 / 2);
        }
        let annotation_ids = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI);
            let property_iri_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(property_iri_id);
            fixture.finish_node(TAG_ENTITY);
            let property_id = fixture.node_tags.len() as u64 / 2;
            fixture.push_node_ref(property_id);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION);
            vec![fixture.node_tags.len() as u64 / 2]
        } else {
            Vec::new()
        };
        fixture.push_node_set(&individual_ids);
        fixture.push_node_set(&annotation_ids);
        fixture.finish_node(tag);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
        fixture
    }

    fn named_declaration_delta_fixture(entity_iri: &[u8], annotated: bool) -> Fixture {
        let mut fixture = Fixture::default();
        fixture.push_scalar(COMPONENT_TEXT, entity_iri);
        fixture.finish_node(TAG_IRI); // 1
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 2
        let declaration_annotations = if annotated {
            fixture.push_scalar(COMPONENT_TEXT, b"urn:label");
            fixture.finish_node(TAG_IRI); // 3
            fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
            fixture.push_node_ref(3);
            fixture.finish_node(TAG_ENTITY); // 4
            fixture.push_node_ref(4);
            fixture.push_node_ref(1);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION); // 5
            &[5_u64][..]
        } else {
            &[]
        };
        fixture.push_node_ref(2);
        fixture.push_node_set(declaration_annotations);
        fixture.finish_node(TAG_DECLARATION); // 3 or 6
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(fixture.node_tags.len() as u32 / 2).to_le_bytes());
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

    fn root_duplicate_annotation_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"http://www.w3.org/2000/01/rdf-schema#label",
            XSD_STRING.as_bytes(),
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=3
        }
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 4
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(2);
        fixture.finish_node(TAG_ENTITY); // 5
        fixture.push_scalar(COMPONENT_ENUM, b"datatype");
        fixture.push_node_ref(3);
        fixture.finish_node(TAG_ENTITY); // 6
        fixture.push_scalar(COMPONENT_TEXT, b"duplicate");
        fixture.push_node_ref(6);
        fixture.push_none();
        fixture.finish_node(TAG_LITERAL); // 7
        fixture.push_node_ref(4);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DECLARATION); // 8
        fixture.push_node_ref(5);
        fixture.push_node_ref(1);
        fixture.push_node_ref(7);
        fixture.push_empty_set();
        fixture.finish_node(TAG_ANNOTATION_ASSERTION); // 9
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        for root_id in [8_u32, 9] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn anonymous_annotation_closure_fixture() -> Fixture {
        let mut fixture = named_annotation_fixture();
        for (scope, key) in [([1_u8; 32], b"imported".as_slice()), ([2_u8; 32], b"root")] {
            fixture.push_scalar(COMPONENT_BYTES, &scope);
            fixture.push_scalar(COMPONENT_BYTES, key);
            fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL); // 21..=22
        }
        for value in [21_u64, 22] {
            fixture.push_node_ref(8);
            fixture.push_node_ref(1);
            fixture.push_node_ref(value);
            fixture.push_empty_set();
            fixture.finish_node(TAG_ANNOTATION_ASSERTION); // 23..=24
        }
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        for root_id in [23_u32, 24] {
            fixture.root_ids.extend_from_slice(&root_id.to_le_bytes());
        }
        fixture
    }

    fn root_anonymous_annotation_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for iri in [
            b"urn:A".as_slice(),
            b"http://www.w3.org/2000/01/rdf-schema#label",
        ] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=2
        }
        fixture.push_scalar(COMPONENT_ENUM, b"class");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 3
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(2);
        fixture.finish_node(TAG_ENTITY); // 4
        fixture.push_scalar(COMPONENT_BYTES, &[2_u8; 32]);
        fixture.push_scalar(COMPONENT_BYTES, b"root");
        fixture.finish_node(TAG_ANONYMOUS_INDIVIDUAL); // 5
        fixture.push_node_ref(3);
        fixture.push_empty_set();
        fixture.finish_node(TAG_DECLARATION); // 6
        fixture.push_node_ref(4);
        fixture.push_node_ref(1);
        fixture.push_node_ref(5);
        fixture.push_empty_set();
        fixture.finish_node(TAG_ANNOTATION_ASSERTION); // 7
        fixture
            .root_kinds
            .extend_from_slice(&[ROOT_AXIOM, ROOT_AXIOM]);
        for root_id in [6_u32, 7] {
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

    fn deep_annotation_metadata_fixture(depth: usize, cyclic: bool) -> Fixture {
        assert!(depth > 0);
        let mut fixture = Fixture::default();
        for iri in [b"urn:annotation-property".as_slice(), XSD_STRING.as_bytes()] {
            fixture.push_scalar(COMPONENT_TEXT, iri);
            fixture.finish_node(TAG_IRI); // 1..=2
        }
        fixture.push_scalar(COMPONENT_ENUM, b"annotation_property");
        fixture.push_node_ref(1);
        fixture.finish_node(TAG_ENTITY); // 3
        fixture.push_scalar(COMPONENT_ENUM, b"datatype");
        fixture.push_node_ref(2);
        fixture.finish_node(TAG_ENTITY); // 4
        fixture.push_scalar(COMPONENT_TEXT, b"metadata");
        fixture.push_node_ref(4);
        fixture.push_none();
        fixture.finish_node(TAG_LITERAL); // 5

        for index in 0..depth {
            fixture.push_node_ref(3);
            fixture.push_node_ref(5);
            if index + 1 < depth {
                fixture.push_node_set(&[(7 + index) as u64]);
            } else if cyclic {
                fixture.push_node_set(&[6]);
            } else {
                fixture.push_empty_set();
            }
            fixture.finish_node(TAG_ANNOTATION); // 6..=5 + depth
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

    fn canonical_component_fixture(
        prefix: Option<&[u8]>,
        integer: &[u8],
        tail: &[u8],
    ) -> (Fixture, usize) {
        let mut fixture = Fixture::default();
        if let Some(prefix) = prefix {
            fixture.push_scalar(COMPONENT_TEXT, prefix);
            fixture.finish_node(TAG_IRI);
        }
        let first_child = fixture.node_tags.len() / 2 + 1;
        fixture.push_scalar(COMPONENT_TEXT, b"child-a");
        fixture.finish_node(TAG_IRI);
        let second_child = fixture.node_tags.len() / 2 + 1;
        fixture.push_scalar(COMPONENT_TEXT, b"child-b");
        fixture.finish_node(TAG_IRI);
        let root = fixture.node_tags.len() / 2 + 1;
        fixture.push_none();
        fixture.push_node_ref(first_child as u64);
        fixture.push_scalar(COMPONENT_TEXT, tail);
        fixture.push_scalar(COMPONENT_BYTES, b"\x00\xff");
        fixture.push_scalar(COMPONENT_INTEGER, integer);
        fixture.push_scalar(COMPONENT_ENUM, b"fixture-enum");
        fixture.push_node_set(&[first_child as u64, second_child as u64]);
        fixture.push_mixed_sequence(&[
            FixtureItem::None,
            FixtureItem::Node(second_child as u64),
            FixtureItem::Scalar(COMPONENT_TEXT, b"sequence-text"),
            FixtureItem::Scalar(COMPONENT_INTEGER, b"\x80\x01\xff\x01"),
            FixtureItem::Scalar(COMPONENT_BYTES, b"sequence-bytes"),
            FixtureItem::Scalar(COMPONENT_ENUM, b"sequence-enum"),
        ]);
        fixture.finish_node(200);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(root as u32).to_le_bytes());
        (fixture, root)
    }

    fn canonical_text_roots(values: &[&[u8]]) -> Fixture {
        let mut fixture = Fixture::default();
        for value in values {
            fixture.push_scalar(COMPONENT_TEXT, value);
            fixture.finish_node(TAG_IRI);
        }
        fixture
            .root_kinds
            .extend(std::iter::repeat_n(ROOT_AXIOM, values.len()));
        for node_id in 1..=values.len() {
            fixture
                .root_ids
                .extend_from_slice(&(node_id as u32).to_le_bytes());
        }
        fixture
    }

    fn canonical_scalar_root(kind: u8, value: &[u8]) -> Fixture {
        let mut fixture = Fixture::default();
        fixture.push_scalar(kind, value);
        fixture.finish_node(200);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture.root_ids.extend_from_slice(&1_u32.to_le_bytes());
        fixture
    }

    fn reversed_canonical_set_fixture() -> Fixture {
        let mut fixture = Fixture::default();
        for value in [b"z".as_slice(), b"a"] {
            fixture.push_scalar(COMPONENT_TEXT, value);
            fixture.finish_node(TAG_IRI);
        }
        fixture.push_node_set(&[1, 2]);
        fixture.finish_node(200);
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture.root_ids.extend_from_slice(&3_u32.to_le_bytes());
        fixture
    }

    fn deep_canonical_fixture(depth: usize, cyclic: bool) -> (Fixture, usize) {
        assert!(depth > 0);
        let mut fixture = Fixture::default();
        fixture.push_none();
        fixture.finish_node(TAG_OBJECT_COMPLEMENT_OF);
        for node_id in 2..=depth {
            fixture.push_node_ref((node_id - 1) as u64);
            fixture.finish_node(TAG_OBJECT_COMPLEMENT_OF);
        }
        if cyclic {
            fixture.field_kinds[0] = COMPONENT_NODE;
            fixture.field_values[0..8].copy_from_slice(&(depth as u64).to_le_bytes());
        }
        fixture.root_kinds.push(ROOT_AXIOM);
        fixture
            .root_ids
            .extend_from_slice(&(depth as u32).to_le_bytes());
        (fixture, depth)
    }

    fn canonical_limits() -> canonical_merge::CanonicalMergeLimits {
        canonical_merge::CanonicalMergeLimits {
            max_work: 5_000_000,
            max_workspace_bytes: 16 * 1024 * 1024,
        }
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
    fn one_root_overlay_delta_merges_into_bounded_taxonomy_batches() {
        let base = named_subclass_fixture();
        let delta = named_subclass_delta_fixture(b"urn:B", b"urn:C");
        let options = DirectCompileOptions {
            bidirectional: true,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 4,
            max_iri_bytes: 1024,
        };
        let state = running_state();
        let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &state,
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        let stats = prepared.statistics();
        assert_eq!(stats.roots, 3);
        assert_eq!(stats.nodes, 11);
        assert_eq!(stats.declarations, 1);
        assert_eq!(stats.subclasses, 2);
        assert_eq!(stats.edges, 4);
        assert_eq!(
            stats.buffer_bytes,
            base.columns().buffer_bytes().unwrap() + delta.columns().buffer_bytes().unwrap()
        );

        let mut edges = Vec::new();
        while prepared.remaining_edges() != 0 {
            let (batch, cursor) = prepared
                .prepare_next_batch(base.columns(), &state, 1)
                .unwrap();
            assert_eq!(batch.len(), 1);
            edges.extend(batch);
            prepared.commit_cursor(cursor);
        }
        assert_eq!(
            edges,
            vec![
                DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:B".into(),
                    relation: SUPERCLASS_OF.into(),
                    destination: "urn:A".into(),
                },
                DirectEdge {
                    source: "urn:B".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:C".into(),
                },
                DirectEdge {
                    source: "urn:C".into(),
                    relation: SUPERCLASS_OF.into(),
                    destination: "urn:B".into(),
                },
            ]
        );
    }

    #[test]
    fn one_root_overlay_delta_uses_canonical_insertion_before_base_subclass() {
        let base = named_subclass_fixture();
        let delta = named_subclass_delta_fixture(b"urn:0", b"urn:B");
        let state = running_state();
        let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: false,
                max_edges: 2,
                max_iri_bytes: 1024,
            },
            &state,
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        let (edges, cursor) = prepared
            .prepare_next_batch(base.columns(), &state, 2)
            .unwrap();
        prepared.commit_cursor(cursor);
        assert_eq!(
            edges,
            vec![
                DirectEdge {
                    source: "urn:0".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                },
            ]
        );
    }

    #[test]
    fn two_root_subclass_overlay_merges_transactionally_across_modes_and_exclusions() {
        let base = named_subclass_fixture();
        let delta =
            named_subclass_pair_delta_fixture(b"urn:0", b"urn:1", b"urn:B", b"urn:C", false);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 6,
            max_iri_bytes: 1024,
        };
        let direct = vec![
            DirectEdge {
                source: "urn:0".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:1".into(),
            },
            DirectEdge {
                source: "urn:A".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:B".into(),
            },
            DirectEdge {
                source: "urn:B".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:C".into(),
            },
        ];
        let bidirectional = vec![
            DirectEdge {
                source: "urn:0".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:1".into(),
            },
            DirectEdge {
                source: "urn:1".into(),
                relation: SUPERCLASS_OF.into(),
                destination: "urn:0".into(),
            },
            DirectEdge {
                source: "urn:A".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:B".into(),
            },
            DirectEdge {
                source: "urn:B".into(),
                relation: SUPERCLASS_OF.into(),
                destination: "urn:A".into(),
            },
            DirectEdge {
                source: "urn:B".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:C".into(),
            },
            DirectEdge {
                source: "urn:C".into(),
                relation: SUPERCLASS_OF.into(),
                destination: "urn:B".into(),
            },
        ];

        for variant in [
            options,
            DirectCompileOptions {
                bidirectional: true,
                ..options
            },
            DirectCompileOptions {
                only_taxonomy: true,
                ..options
            },
            DirectCompileOptions {
                asserted_taxonomy_only: true,
                ..options
            },
        ] {
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                variant,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            let expected = if variant.bidirectional {
                bidirectional.as_slice()
            } else {
                direct.as_slice()
            };
            let statistics = prepared.statistics();
            assert_eq!(statistics.roots, 4);
            assert_eq!(statistics.declarations, 1);
            assert_eq!(statistics.subclasses, 3);
            assert_eq!(statistics.edges, expected.len());
            assert_eq!(
                statistics.buffer_bytes,
                base.columns().buffer_bytes().unwrap() + delta.columns().buffer_bytes().unwrap()
            );
            assert_eq!(prepared.emission_attempts(), 0);
            assert_eq!(prepared.preparation.overlay_deltas.len(), 2);

            let (preview, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            let (retry, _) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            assert_eq!(preview, retry);
            assert_eq!(preview.as_slice(), &expected[..1]);
            assert_eq!(prepared.remaining_edges(), expected.len());
            prepared.commit_cursor(cursor);
            let mut edges = preview;
            while prepared.remaining_edges() != 0 {
                let (batch, cursor) = prepared
                    .prepare_next_batch(base.columns(), &running_state(), 1)
                    .unwrap();
                edges.extend(batch);
                prepared.commit_cursor(cursor);
            }
            assert_eq!(edges, expected);
            assert!(prepared.is_exhausted());
        }

        let excluded_declaration = 1_u32.to_le_bytes();
        let excluded_subclass = 2_u32.to_le_bytes();
        let mut excluded_all = Vec::new();
        excluded_all.extend_from_slice(&excluded_declaration);
        excluded_all.extend_from_slice(&excluded_subclass);
        for (excluded, include_base_subclass, roots, subclasses) in [
            (excluded_declaration.as_slice(), true, 3, 3),
            (excluded_subclass.as_slice(), false, 3, 2),
            (excluded_all.as_slice(), false, 2, 2),
        ] {
            let expected = if include_base_subclass {
                vec![
                    DirectEdge {
                        source: "urn:0".into(),
                        relation: SUBCLASS_OF.into(),
                        destination: "urn:1".into(),
                    },
                    DirectEdge {
                        source: "urn:A".into(),
                        relation: SUBCLASS_OF.into(),
                        destination: "urn:B".into(),
                    },
                    DirectEdge {
                        source: "urn:B".into(),
                        relation: SUBCLASS_OF.into(),
                        destination: "urn:C".into(),
                    },
                ]
            } else {
                vec![
                    DirectEdge {
                        source: "urn:0".into(),
                        relation: SUBCLASS_OF.into(),
                        destination: "urn:1".into(),
                    },
                    DirectEdge {
                        source: "urn:B".into(),
                        relation: SUBCLASS_OF.into(),
                        destination: "urn:C".into(),
                    },
                ]
            };
            let selected_base = base.columns().with_excluded_root_ids(excluded);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                selected_base,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, roots);
            assert_eq!(prepared.statistics().subclasses, subclasses);
            assert_eq!(prepared.statistics().edges, expected.len());
            let mut edges = Vec::new();
            while prepared.remaining_edges() != 0 {
                let (batch, cursor) = prepared
                    .prepare_next_batch(selected_base, &running_state(), 1)
                    .unwrap();
                edges.extend(batch);
                prepared.commit_cursor(cursor);
            }
            assert_eq!(edges, expected);
        }

        let cancellation = AtomicU8::new(STATE_RUNNING);
        let mut cancellable = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &cancellation,
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        let remaining = cancellable.remaining_edges();
        let (preview, cursor) = cancellable
            .prepare_next_batch(base.columns(), &cancellation, 1)
            .unwrap();
        cancellation.store(STATE_CANCELLED, Ordering::Release);
        assert!(matches!(
            cancellable.prepare_next_batch(base.columns(), &cancellation, 1),
            Err(KernelError::Cancelled)
        ));
        assert_eq!(cancellable.remaining_edges(), remaining);
        cancellation.store(STATE_RUNNING, Ordering::Release);
        let (retry, _) = cancellable
            .prepare_next_batch(base.columns(), &cancellation, 1)
            .unwrap();
        assert_eq!(preview, retry);
        cancellable.commit_cursor(cursor);
        assert_eq!(cancellable.remaining_edges(), remaining - 1);
    }

    #[test]
    fn two_root_subclass_overlay_rejects_hostile_envelopes_and_resource_limits() {
        let base = named_subclass_fixture();
        let delta =
            named_subclass_pair_delta_fixture(b"urn:0", b"urn:1", b"urn:B", b"urn:C", false);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 3,
            max_iri_bytes: 1024,
        };
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    max_edges: 2,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Resource(message)) if message.contains("requires 3 edges")
        ));
        let retained = OwnedRoleState {
            subroles: vec![("urn:p".into(), vec!["urn:child".into()])],
            inverses: vec![
                ("urn:p".into(), "urn:pinv".into()),
                ("urn:pinv".into(), "urn:p".into()),
            ],
        };
        let retained_snapshot = retained.snapshot().unwrap();
        let retry = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &running_state(),
            Some(&retained),
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(retry.statistics().edges, 3);
        assert_eq!(retry.emission_attempts(), 0);
        let remaining = retry.remaining_edges();
        let (preview, _) = retry
            .prepare_next_batch(base.columns(), &running_state(), 1)
            .unwrap();
        assert_eq!(retry.remaining_edges(), remaining);
        for (work, workspace, expected) in [
            (1, canonical_limits().max_workspace_bytes, "work"),
            (canonical_limits().max_work, 1, "workspace"),
        ] {
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    work,
                    workspace,
                ),
                Err(KernelError::Resource(message)) if message.contains(expected)
            ));
        }

        let taxonomy_ignored = hostile_subclass_pair_delta_fixture(
            SubclassPairShape::Taxonomy,
            SubclassPairShape::Ignored,
        );
        let mixed_tags = named_subclass_fixture();
        for hostile in [&taxonomy_ignored, &mixed_tags] {
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    hostile.columns(),
                    options,
                    &running_state(),
                    Some(&retained),
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message))
                    if message.contains(LOCAL_EMITTING_OVERLAY_REQUIREMENT)
            ));
            assert_eq!(retained.snapshot().unwrap(), retained_snapshot);
            assert_eq!(retry.remaining_edges(), remaining);
            let (after_failure, _) = retry
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            assert_eq!(after_failure, preview);
            assert_eq!(retry.remaining_edges(), remaining);
        }

        let annotated =
            named_subclass_pair_delta_fixture(b"urn:0", b"urn:1", b"urn:B", b"urn:C", true);
        let annotated_prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            annotated.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(annotated_prepared.statistics().roots, 4);
        assert_eq!(annotated_prepared.statistics().subclasses, 3);
        assert_eq!(annotated_prepared.statistics().edges, 3);
        assert_eq!(annotated_prepared.emission_attempts(), 0);

        let duplicate_base =
            named_subclass_pair_delta_fixture(b"urn:A", b"urn:B", b"urn:C", b"urn:D", false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                duplicate_base.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let mut reversed =
            named_subclass_pair_delta_fixture(b"urn:0", b"urn:1", b"urn:B", b"urn:C", false);
        reversed.root_ids.rotate_left(4);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                reversed.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Malformed(message)) if message.contains("canonical")
        ));
    }

    #[test]
    fn two_root_class_assertion_overlay_merges_across_modes_and_exclusions() {
        let base = named_class_assertions_fixture(
            &[
                (b"urn:A".as_slice(), b"urn:i".as_slice()),
                (b"urn:C".as_slice(), b"urn:k".as_slice()),
                (b"urn:E".as_slice(), b"urn:m".as_slice()),
            ],
            false,
        );
        let delta =
            named_class_assertion_pair_delta_fixture(b"urn:B", b"urn:j", b"urn:D", b"urn:l", false);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 5,
            max_iri_bytes: 1024,
        };
        let expected = vec![
            DirectEdge {
                source: "urn:i".into(),
                relation: RDF_TYPE.into(),
                destination: "urn:A".into(),
            },
            DirectEdge {
                source: "urn:j".into(),
                relation: RDF_TYPE.into(),
                destination: "urn:B".into(),
            },
            DirectEdge {
                source: "urn:k".into(),
                relation: RDF_TYPE.into(),
                destination: "urn:C".into(),
            },
            DirectEdge {
                source: "urn:l".into(),
                relation: RDF_TYPE.into(),
                destination: "urn:D".into(),
            },
            DirectEdge {
                source: "urn:m".into(),
                relation: RDF_TYPE.into(),
                destination: "urn:E".into(),
            },
        ];

        for variant in [
            options,
            DirectCompileOptions {
                bidirectional: true,
                ..options
            },
            DirectCompileOptions {
                only_taxonomy: true,
                ..options
            },
        ] {
            let state = running_state();
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                variant,
                &state,
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 5);
            assert_eq!(prepared.statistics().class_assertions, 5);
            assert_eq!(prepared.statistics().ignored_class_assertions, 0);
            assert_eq!(prepared.statistics().edges, 5);
            assert_eq!(
                prepared
                    .preparation
                    .overlay_deltas
                    .iter()
                    .map(|delta| delta.insertion_scan_index)
                    .collect::<Vec<_>>(),
                vec![1, 2]
            );
            assert_eq!(prepared.emission_attempts(), 0);

            let (preview, cursor) = prepared
                .prepare_next_batch(base.columns(), &state, 1)
                .unwrap();
            let (retry, _) = prepared
                .prepare_next_batch(base.columns(), &state, 1)
                .unwrap();
            assert_eq!(preview, retry);
            assert_eq!(preview, expected[..1]);
            assert_eq!(prepared.remaining_edges(), 5);
            prepared.commit_cursor(cursor);
            let mut edges = preview;
            while prepared.remaining_edges() != 0 {
                let (batch, cursor) = prepared
                    .prepare_next_batch(base.columns(), &state, 1)
                    .unwrap();
                edges.extend(batch);
                prepared.commit_cursor(cursor);
            }
            assert_eq!(edges, expected);
            assert!(prepared.is_exhausted());
        }

        let asserted = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            DirectCompileOptions {
                asserted_taxonomy_only: true,
                ..options
            },
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(asserted.statistics().roots, 5);
        assert_eq!(asserted.statistics().class_assertions, 5);
        assert_eq!(asserted.statistics().edges, 0);
        assert_eq!(asserted.emission_attempts(), 0);
        assert!(asserted.is_exhausted());

        let excluded_middle = 2_u32.to_le_bytes();
        let mut excluded_all = Vec::new();
        for root_id in 1_u32..=3 {
            excluded_all.extend_from_slice(&root_id.to_le_bytes());
        }
        for (excluded, expected_sources) in [
            (
                excluded_middle.as_slice(),
                vec!["urn:i", "urn:j", "urn:l", "urn:m"],
            ),
            (excluded_all.as_slice(), vec!["urn:j", "urn:l"]),
        ] {
            let selected_base = base.columns().with_excluded_root_ids(excluded);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                selected_base,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, expected_sources.len());
            assert_eq!(
                prepared.statistics().class_assertions,
                expected_sources.len()
            );
            let mut edges = Vec::new();
            while prepared.remaining_edges() != 0 {
                let (batch, cursor) = prepared
                    .prepare_next_batch(selected_base, &running_state(), 1)
                    .unwrap();
                edges.extend(batch);
                prepared.commit_cursor(cursor);
            }
            assert_eq!(
                edges
                    .iter()
                    .map(|edge| edge.source.as_str())
                    .collect::<Vec<_>>(),
                expected_sources
            );
        }
    }

    #[test]
    fn two_root_class_assertion_overlay_preserves_delayed_consecutive_and_end_positions() {
        let cases = [
            (
                named_class_assertions_fixture(
                    &[
                        (b"urn:A".as_slice(), b"urn:i".as_slice()),
                        (b"urn:D".as_slice(), b"urn:l".as_slice()),
                    ],
                    false,
                ),
                named_class_assertion_pair_delta_fixture(
                    b"urn:B", b"urn:j", b"urn:C", b"urn:k", false,
                ),
                vec![1, 1],
            ),
            (
                named_class_assertions_fixture(
                    &[
                        (b"urn:A".as_slice(), b"urn:i".as_slice()),
                        (b"urn:B".as_slice(), b"urn:j".as_slice()),
                    ],
                    false,
                ),
                named_class_assertion_pair_delta_fixture(
                    b"urn:C", b"urn:k", b"urn:D", b"urn:l", false,
                ),
                vec![2, 2],
            ),
        ];
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 4,
            max_iri_bytes: 1024,
        };
        for (base, delta, expected_positions) in cases {
            let state = AtomicU8::new(STATE_RUNNING);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &state,
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(
                prepared
                    .preparation
                    .overlay_deltas
                    .iter()
                    .map(|delta| delta.insertion_scan_index)
                    .collect::<Vec<_>>(),
                expected_positions
            );
            let remaining = prepared.remaining_edges();
            let (preview, cursor) = prepared
                .prepare_next_batch(base.columns(), &state, 1)
                .unwrap();
            state.store(STATE_CANCELLED, Ordering::Release);
            assert!(matches!(
                prepared.prepare_next_batch(base.columns(), &state, 1),
                Err(KernelError::Cancelled)
            ));
            assert_eq!(prepared.remaining_edges(), remaining);
            state.store(STATE_RUNNING, Ordering::Release);
            let (retry, _) = prepared
                .prepare_next_batch(base.columns(), &state, 1)
                .unwrap();
            assert_eq!(preview, retry);
            prepared.commit_cursor(cursor);
            let mut edges = preview;
            while prepared.remaining_edges() != 0 {
                let (batch, cursor) = prepared
                    .prepare_next_batch(base.columns(), &state, 1)
                    .unwrap();
                edges.extend(batch);
                prepared.commit_cursor(cursor);
            }
            assert_eq!(
                edges
                    .iter()
                    .map(|edge| edge.source.as_str())
                    .collect::<Vec<_>>(),
                vec!["urn:i", "urn:j", "urn:k", "urn:l"]
            );
        }
    }

    #[test]
    fn two_root_class_assertion_overlay_rejects_hostile_envelopes_preoutput() {
        let base = named_class_assertions_fixture(
            &[
                (b"urn:A".as_slice(), b"urn:i".as_slice()),
                (b"urn:C".as_slice(), b"urn:k".as_slice()),
                (b"urn:E".as_slice(), b"urn:m".as_slice()),
            ],
            false,
        );
        let delta =
            named_class_assertion_pair_delta_fixture(b"urn:B", b"urn:j", b"urn:D", b"urn:l", false);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 5,
            max_iri_bytes: 1024,
        };
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    max_edges: 4,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Resource(message)) if message.contains("requires 5 edges")
        ));
        for (work, workspace, expected) in [
            (1, canonical_limits().max_workspace_bytes, "work"),
            (canonical_limits().max_work, 1, "workspace"),
        ] {
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    work,
                    workspace,
                ),
                Err(KernelError::Resource(message)) if message.contains(expected)
            ));
        }

        let retained = OwnedRoleState {
            subroles: vec![("urn:p".into(), vec!["urn:child".into()])],
            inverses: vec![
                ("urn:p".into(), "urn:pinv".into()),
                ("urn:pinv".into(), "urn:p".into()),
            ],
        };
        let retained_snapshot = retained.snapshot().unwrap();
        let retry = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &running_state(),
            Some(&retained),
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        let remaining = retry.remaining_edges();
        let (preview, _) = retry
            .prepare_next_batch(base.columns(), &running_state(), 1)
            .unwrap();

        let anonymous = ignored_class_assertion_pair_delta_fixture(true);
        let complex = ignored_class_assertion_pair_delta_fixture(false);
        for hostile in [&anonymous, &complex] {
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    hostile.columns(),
                    options,
                    &running_state(),
                    Some(&retained),
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message))
                    if message.contains(LOCAL_EMITTING_OVERLAY_REQUIREMENT)
            ));
            assert_eq!(retained.snapshot().unwrap(), retained_snapshot);
            assert_eq!(retry.remaining_edges(), remaining);
            let (after_failure, _) = retry
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            assert_eq!(after_failure, preview);
        }

        let annotated =
            named_class_assertion_pair_delta_fixture(b"urn:B", b"urn:j", b"urn:D", b"urn:l", true);
        let annotated_prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            annotated.columns(),
            options,
            &running_state(),
            Some(&retained),
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(annotated_prepared.statistics().roots, 5);
        assert_eq!(annotated_prepared.statistics().class_assertions, 5);
        assert_eq!(annotated_prepared.statistics().edges, 5);
        assert_eq!(annotated_prepared.emission_attempts(), 0);
        assert_eq!(retained.snapshot().unwrap(), retained_snapshot);

        let duplicate =
            named_class_assertion_pair_delta_fixture(b"urn:A", b"urn:i", b"urn:B", b"urn:j", false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                duplicate.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let mut reversed =
            named_class_assertion_pair_delta_fixture(b"urn:B", b"urn:j", b"urn:D", b"urn:l", false);
        reversed.root_ids.rotate_left(4);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                reversed.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Malformed(message)) if message.contains("canonical")
        ));
        assert_eq!(retry.remaining_edges(), remaining);
    }

    #[test]
    fn multi_root_emitting_overlay_shares_one_plan_across_phases_modes_and_exclusions() {
        let base = mixed_emitting_base_fixture();
        let delta = mixed_emitting_delta_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 14,
            max_iri_bytes: 1024,
        };
        let direct = vec![
            DirectEdge {
                source: "urn:A".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:Top".into(),
            },
            DirectEdge {
                source: "urn:B".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:Top".into(),
            },
            DirectEdge {
                source: "urn:C".into(),
                relation: "urn:p".into(),
                destination: "urn:D".into(),
            },
            DirectEdge {
                source: "urn:Y".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:Top".into(),
            },
            DirectEdge {
                source: "urn:Z".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:Top".into(),
            },
            DirectEdge {
                source: "urn:a".into(),
                relation: RDF_TYPE.into(),
                destination: "urn:A".into(),
            },
            DirectEdge {
                source: "urn:b".into(),
                relation: RDF_TYPE.into(),
                destination: "urn:B".into(),
            },
            DirectEdge {
                source: "urn:z".into(),
                relation: RDF_TYPE.into(),
                destination: "urn:Z".into(),
            },
            DirectEdge {
                source: "urn:a".into(),
                relation: "urn:p".into(),
                destination: "urn:z".into(),
            },
            DirectEdge {
                source: "urn:z".into(),
                relation: "urn:q".into(),
                destination: "urn:a".into(),
            },
        ];
        let bidirectional = direct
            .iter()
            .flat_map(|edge| {
                let mut expanded = vec![edge.clone()];
                if edge.relation == SUBCLASS_OF {
                    expanded.push(DirectEdge {
                        source: edge.destination.clone(),
                        relation: SUPERCLASS_OF.into(),
                        destination: edge.source.clone(),
                    });
                }
                expanded
            })
            .collect::<Vec<_>>();

        for (variant, expected) in [
            (options, direct.clone()),
            (
                DirectCompileOptions {
                    bidirectional: true,
                    ..options
                },
                bidirectional,
            ),
            (
                DirectCompileOptions {
                    only_taxonomy: true,
                    ..options
                },
                direct
                    .iter()
                    .filter(|edge| {
                        !(edge.source == "urn:C"
                            && edge.relation == "urn:p"
                            && edge.destination == "urn:D")
                    })
                    .cloned()
                    .collect(),
            ),
            (
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                direct
                    .iter()
                    .filter(|edge| edge.relation == SUBCLASS_OF)
                    .cloned()
                    .collect(),
            ),
        ] {
            let state = running_state();
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                variant,
                &state,
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.preparation.overlay_deltas.len(), 5);
            assert_eq!(
                prepared
                    .preparation
                    .overlay_deltas
                    .iter()
                    .map(|delta| (delta.projection.phase(), delta.insertion_scan_index))
                    .collect::<Vec<_>>(),
                vec![
                    (EmissionPhase::Subclasses, 1),
                    (EmissionPhase::Subclasses, 1),
                    (EmissionPhase::Subclasses, 1),
                    (EmissionPhase::ClassAssertions, 3),
                    (EmissionPhase::ObjectAssertions, 5),
                ]
            );
            assert_eq!(prepared.statistics().roots, 10);
            assert_eq!(prepared.statistics().subclasses, 5);
            assert_eq!(prepared.statistics().restriction_subclasses, 1);
            assert_eq!(prepared.statistics().class_assertions, 3);
            assert_eq!(prepared.statistics().object_property_assertions, 2);
            assert_eq!(prepared.statistics().edges, expected.len());
            let mut actual = Vec::new();
            while prepared.remaining_edges() != 0 {
                let (batch, cursor) = prepared
                    .prepare_next_batch(base.columns(), &state, 1)
                    .unwrap();
                actual.extend(batch);
                prepared.commit_cursor(cursor);
            }
            assert_eq!(actual, expected);
        }

        let mut excluded = Vec::new();
        for root_position in [2_u32, 4, 5] {
            excluded.extend_from_slice(&root_position.to_le_bytes());
        }
        let selected_base = base.columns().with_excluded_root_ids(&excluded);
        let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
            selected_base,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(prepared.statistics().roots, 7);
        assert_eq!(prepared.statistics().edges, 7);
        let mut actual = Vec::new();
        while prepared.remaining_edges() != 0 {
            let (batch, cursor) = prepared
                .prepare_next_batch(selected_base, &running_state(), 1)
                .unwrap();
            actual.extend(batch);
            prepared.commit_cursor(cursor);
        }
        assert_eq!(
            actual,
            direct
                .iter()
                .filter(|edge| {
                    !(edge.source == "urn:Z"
                        || edge.source == "urn:z" && edge.destination == "urn:Z"
                        || edge.source == "urn:a"
                            && edge.relation == "urn:p"
                            && edge.destination == "urn:z")
                })
                .cloned()
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn multi_root_emitting_overlay_bounds_resources_and_preserves_retry_state() {
        let base = mixed_emitting_base_fixture();
        let delta = mixed_emitting_delta_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 10,
            max_iri_bytes: 1024,
        };
        let retained = OwnedRoleState {
            subroles: vec![("urn:p".into(), vec!["urn:child".into()])],
            inverses: vec![
                ("urn:p".into(), "urn:pinv".into()),
                ("urn:pinv".into(), "urn:p".into()),
            ],
        };
        let retained_snapshot = retained.snapshot().unwrap();
        let state = AtomicU8::new(STATE_RUNNING);
        let prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            DirectCompileOptions {
                max_edges: 12,
                ..options
            },
            &state,
            Some(&retained),
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        let remaining = prepared.remaining_edges();
        let (preview, _) = prepared
            .prepare_next_batch(base.columns(), &state, 1)
            .unwrap();
        state.store(STATE_CANCELLED, Ordering::Release);
        assert!(matches!(
            prepared.prepare_next_batch(base.columns(), &state, 1),
            Err(KernelError::Cancelled)
        ));
        assert_eq!(prepared.remaining_edges(), remaining);
        state.store(STATE_RUNNING, Ordering::Release);
        let (retry, _) = prepared
            .prepare_next_batch(base.columns(), &state, 1)
            .unwrap();
        assert_eq!(retry, preview);
        assert_eq!(retained.snapshot().unwrap(), retained_snapshot);

        fn succeeds(
            base: &Fixture,
            delta: &Fixture,
            options: DirectCompileOptions,
            work: usize,
            workspace: usize,
        ) -> bool {
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                work,
                workspace,
            )
            .is_ok()
        }
        fn minimum_successful(
            mut low: usize,
            mut high: usize,
            mut succeeds: impl FnMut(usize) -> bool,
        ) -> usize {
            assert!(succeeds(high));
            while low < high {
                let middle = low + (high - low) / 2;
                if succeeds(middle) {
                    high = middle;
                } else {
                    low = middle + 1;
                }
            }
            low
        }

        let minimum_work = minimum_successful(1, canonical_limits().max_work, |work| {
            succeeds(
                &base,
                &delta,
                options,
                work,
                canonical_limits().max_workspace_bytes,
            )
        });
        assert!(minimum_work > 1);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                minimum_work - 1,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Resource(message)) if message.contains("work")
        ));

        let minimum_workspace =
            minimum_successful(1, canonical_limits().max_workspace_bytes, |workspace| {
                succeeds(
                    &base,
                    &delta,
                    options,
                    canonical_limits().max_work,
                    workspace,
                )
            });
        assert!(minimum_workspace > 1);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                minimum_workspace - 1,
            ),
            Err(KernelError::Resource(message)) if message.contains("workspace")
        ));

        let mut duplicate = mixed_emitting_delta_fixture();
        duplicate.root_ids[16..20].copy_from_slice(&25_u32.to_le_bytes());
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                duplicate.columns(),
                options,
                &running_state(),
                Some(&retained),
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Malformed(message)) if message.contains("canonical and unique")
        ));
        assert_eq!(retained.snapshot().unwrap(), retained_snapshot);

        let mut reversed = mixed_emitting_delta_fixture();
        reversed.root_ids[..4].copy_from_slice(&23_u32.to_le_bytes());
        reversed.root_ids[4..8].copy_from_slice(&22_u32.to_le_bytes());
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                reversed.columns(),
                options,
                &running_state(),
                Some(&retained),
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Malformed(message)) if message.contains("canonical")
        ));
        assert_eq!(retained.snapshot().unwrap(), retained_snapshot);
        assert_eq!(prepared.remaining_edges(), remaining);
    }

    #[test]
    fn multi_root_emitting_overlay_canonicalizes_an_artificial_cross_phase_plan() {
        let mut plan = vec![
            OwnedOverlayDelta {
                projection: OwnedOverlayDeltaProjection::ObjectPropertyAssertion {
                    source: "urn:z".into(),
                    relation: "urn:q".into(),
                    destination: "urn:a".into(),
                },
                insertion_scan_index: 0,
                local_canonical_index: 4,
            },
            OwnedOverlayDelta {
                projection: OwnedOverlayDeltaProjection::Taxonomy {
                    source: "urn:Y".into(),
                    destination: "urn:Top".into(),
                },
                insertion_scan_index: 3,
                local_canonical_index: 1,
            },
            OwnedOverlayDelta {
                projection: OwnedOverlayDeltaProjection::ClassAssertion {
                    individual: "urn:b".into(),
                    class: "urn:B".into(),
                },
                insertion_scan_index: 0,
                local_canonical_index: 3,
            },
            OwnedOverlayDelta {
                projection: OwnedOverlayDeltaProjection::Restriction {
                    source: "urn:C".into(),
                    relation: "urn:p".into(),
                    destination: "urn:D".into(),
                },
                insertion_scan_index: 1,
                local_canonical_index: 2,
            },
            OwnedOverlayDelta {
                projection: OwnedOverlayDeltaProjection::Taxonomy {
                    source: "urn:B".into(),
                    destination: "urn:Top".into(),
                },
                insertion_scan_index: 1,
                local_canonical_index: 0,
            },
        ];

        canonicalize_overlay_delta_plan(&mut plan);

        assert_eq!(
            plan.iter()
                .map(|delta| {
                    (
                        delta.projection.phase(),
                        delta.insertion_scan_index,
                        delta.local_canonical_index,
                    )
                })
                .collect::<Vec<_>>(),
            vec![
                (EmissionPhase::Subclasses, 1, 0),
                (EmissionPhase::Subclasses, 1, 2),
                (EmissionPhase::Subclasses, 3, 1),
                (EmissionPhase::ClassAssertions, 0, 3),
                (EmissionPhase::ObjectAssertions, 0, 4),
            ]
        );
        let cursor = DirectEmissionCursor::default();
        assert_eq!(cursor.overlay_delta_index, 0);
        assert_eq!(cursor.try_clone().unwrap().overlay_delta_index, 0);
    }

    #[test]
    fn one_root_overlay_delta_accepts_one_silent_declaration() {
        let base = named_subclass_fixture();
        let delta = named_declaration_delta_fixture(b"urn:C", false);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 2,
            max_iri_bytes: 1024,
        };
        let state = running_state();
        let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &state,
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(prepared.statistics().roots, 3);
        assert_eq!(prepared.statistics().declarations, 2);
        assert_eq!(prepared.statistics().subclasses, 1);
        assert_eq!(prepared.statistics().edges, 1);
        assert_eq!(prepared.emission_attempts(), 0);
        let (edges, cursor) = prepared
            .prepare_next_batch(base.columns(), &state, 1)
            .unwrap();
        assert_eq!(
            edges,
            vec![DirectEdge {
                source: "urn:A".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:B".into(),
            }]
        );
        prepared.commit_cursor(cursor);
        assert!(prepared.is_exhausted());

        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().declarations, 2);
        assert_eq!(silent.statistics().subclasses, 0);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate = named_declaration_delta_fixture(b"urn:A", false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                duplicate.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = named_declaration_delta_fixture(b"urn:C", true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_compiles_named_restrictions_with_selected_role_state() {
        let base = overlay_role_base_fixture();
        for (restriction_tag, inverse_property, restriction_first) in [
            (TAG_OBJECT_SOME_VALUES_FROM, false, false),
            (TAG_OBJECT_ALL_VALUES_FROM, true, false),
            (TAG_OBJECT_MIN_CARDINALITY, false, true),
            (TAG_OBJECT_MAX_CARDINALITY, true, true),
        ] {
            let delta = named_restriction_delta_fixture(
                restriction_tag,
                inverse_property,
                restriction_first,
                false,
            );
            let state = running_state();
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    bidirectional: true,
                    asserted_taxonomy_only: false,
                    only_taxonomy: false,
                    include_literals: false,
                    max_edges: 3,
                    max_iri_bytes: 1024,
                },
                &state,
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            let stats = prepared.statistics();
            assert_eq!(stats.roots, 3);
            assert_eq!(stats.subclasses, 1);
            assert_eq!(stats.restriction_subclasses, 1);
            assert_eq!(stats.sub_object_properties, 1);
            assert_eq!(stats.inverse_object_properties, 1);
            assert_eq!(stats.role_expansion_edges, 2);
            assert_eq!(stats.edges, 3);
            assert_eq!(
                stats.buffer_bytes,
                base.columns().buffer_bytes().unwrap() + delta.columns().buffer_bytes().unwrap()
            );
            assert_eq!(prepared.emission_attempts(), 0);

            let mut edges = Vec::new();
            while prepared.remaining_edges() != 0 {
                let (batch, cursor) = prepared
                    .prepare_next_batch(base.columns(), &state, 1)
                    .unwrap();
                assert_eq!(batch.len(), 1);
                edges.extend(batch);
                prepared.commit_cursor(cursor);
            }
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
                ]
            );
        }
    }

    #[test]
    fn one_root_overlay_restriction_honours_taxonomy_modes_and_base_exclusions() {
        let base = overlay_role_base_fixture();
        let delta =
            named_restriction_delta_fixture(TAG_OBJECT_SOME_VALUES_FROM, false, false, false);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 3,
            max_iri_bytes: 1024,
        };

        for (excluded, expected_relations, expected_expansions) in [
            (Vec::new(), vec!["urn:p", "urn:child", "urn:pinv"], 2),
            (1_u32.to_le_bytes().to_vec(), vec!["urn:p", "urn:pinv"], 1),
            (2_u32.to_le_bytes().to_vec(), vec!["urn:p", "urn:child"], 1),
            (
                [1_u32.to_le_bytes(), 2_u32.to_le_bytes()].concat(),
                vec!["urn:p"],
                0,
            ),
        ] {
            let base_columns = base.columns().with_excluded_root_ids(&excluded);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base_columns,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().edges, expected_relations.len());
            assert_eq!(
                prepared.statistics().role_expansion_edges,
                expected_expansions
            );
            let (edges, cursor) = prepared
                .prepare_next_batch(base_columns, &running_state(), expected_relations.len())
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges
                    .iter()
                    .map(|edge| edge.relation.as_str())
                    .collect::<Vec<_>>(),
                expected_relations
            );
            assert!(prepared.is_exhausted());
        }

        for silent_options in [
            DirectCompileOptions {
                only_taxonomy: true,
                ..options
            },
            DirectCompileOptions {
                asserted_taxonomy_only: true,
                ..options
            },
        ] {
            let silent = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                silent_options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(silent.statistics().subclasses, 1);
            assert_eq!(silent.statistics().restriction_subclasses, 1);
            assert_eq!(silent.statistics().role_expansion_edges, 0);
            assert_eq!(silent.statistics().edges, 0);
            assert_eq!(silent.emission_attempts(), 0);
            assert!(silent.is_exhausted());
        }

        let annotated =
            named_restriction_delta_fixture(TAG_OBJECT_SOME_VALUES_FROM, false, false, true);
        let annotated_prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            annotated.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(annotated_prepared.statistics().roots, 3);
        assert_eq!(annotated_prepared.statistics().subclasses, 1);
        assert_eq!(annotated_prepared.statistics().restriction_subclasses, 1);
        assert_eq!(annotated_prepared.statistics().edges, 3);
        assert_eq!(annotated_prepared.emission_attempts(), 0);
    }

    #[test]
    fn one_root_overlay_delta_inserts_named_class_assertion_in_canonical_phase() {
        let base = named_class_assertion_base_fixture();
        let delta = named_class_assertion_delta_fixture(b"urn:B", b"urn:j", false);
        let options = DirectCompileOptions {
            bidirectional: true,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 3,
            max_iri_bytes: 1024,
        };
        let state = running_state();
        let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &state,
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        let stats = prepared.statistics();
        assert_eq!(stats.roots, 3);
        assert_eq!(stats.nodes, 15);
        assert_eq!(stats.class_assertions, 3);
        assert_eq!(stats.ignored_class_assertions, 0);
        assert_eq!(stats.edges, 3);
        assert_eq!(
            stats.buffer_bytes,
            base.columns().buffer_bytes().unwrap() + delta.columns().buffer_bytes().unwrap()
        );
        assert_eq!(prepared.emission_attempts(), 0);

        let (first, first_cursor) = prepared
            .prepare_next_batch(base.columns(), &state, 1)
            .unwrap();
        let (retry, _) = prepared
            .prepare_next_batch(base.columns(), &state, 1)
            .unwrap();
        assert_eq!(first, retry);
        assert_eq!(prepared.remaining_edges(), 3);
        prepared.commit_cursor(first_cursor);
        let mut edges = first;
        while prepared.remaining_edges() != 0 {
            let (batch, cursor) = prepared
                .prepare_next_batch(base.columns(), &state, 1)
                .unwrap();
            edges.extend(batch);
            prepared.commit_cursor(cursor);
        }
        assert_eq!(
            edges,
            vec![
                DirectEdge {
                    source: "urn:i".into(),
                    relation: RDF_TYPE.into(),
                    destination: "urn:A".into(),
                },
                DirectEdge {
                    source: "urn:j".into(),
                    relation: RDF_TYPE.into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:k".into(),
                    relation: RDF_TYPE.into(),
                    destination: "urn:C".into(),
                },
            ]
        );

        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    max_edges: 2,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Resource(message)) if message.contains("configured limit")
        ));
    }

    #[test]
    fn one_root_overlay_class_assertion_composes_exclusions_modes_and_fallbacks() {
        let base = named_class_assertion_base_fixture();
        let delta = named_class_assertion_delta_fixture(b"urn:B", b"urn:j", false);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 3,
            max_iri_bytes: 1024,
        };
        for (excluded, expected_sources) in [
            (Vec::new(), vec!["urn:i", "urn:j", "urn:k"]),
            (1_u32.to_le_bytes().to_vec(), vec!["urn:j", "urn:k"]),
            (2_u32.to_le_bytes().to_vec(), vec!["urn:i", "urn:j"]),
            (
                [1_u32.to_le_bytes(), 2_u32.to_le_bytes()].concat(),
                vec!["urn:j"],
            ),
        ] {
            let base_columns = base.columns().with_excluded_root_ids(&excluded);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base_columns,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, expected_sources.len());
            assert_eq!(
                prepared.statistics().class_assertions,
                expected_sources.len()
            );
            assert_eq!(prepared.statistics().edges, expected_sources.len());
            let (edges, cursor) = prepared
                .prepare_next_batch(base_columns, &running_state(), expected_sources.len())
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges
                    .iter()
                    .map(|edge| edge.source.as_str())
                    .collect::<Vec<_>>(),
                expected_sources
            );
            assert!(prepared.is_exhausted());
        }

        let mut only_taxonomy = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            DirectCompileOptions {
                only_taxonomy: true,
                ..options
            },
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(only_taxonomy.statistics().class_assertions, 3);
        assert_eq!(only_taxonomy.statistics().edges, 3);
        let (edges, cursor) = only_taxonomy
            .prepare_next_batch(base.columns(), &running_state(), 3)
            .unwrap();
        only_taxonomy.commit_cursor(cursor);
        assert_eq!(edges.len(), 3);
        assert!(only_taxonomy.is_exhausted());

        let asserted = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            DirectCompileOptions {
                asserted_taxonomy_only: true,
                ..options
            },
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(asserted.statistics().class_assertions, 3);
        assert_eq!(asserted.statistics().edges, 0);
        assert_eq!(asserted.emission_attempts(), 0);
        assert!(asserted.is_exhausted());

        let duplicate = named_class_assertion_delta_fixture(b"urn:A", b"urn:i", false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                duplicate.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = named_class_assertion_delta_fixture(b"urn:B", b"urn:j", true);
        let annotated_prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            annotated.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(annotated_prepared.statistics().roots, 3);
        assert_eq!(annotated_prepared.statistics().class_assertions, 3);
        assert_eq!(annotated_prepared.statistics().edges, 3);
        assert_eq!(annotated_prepared.emission_attempts(), 0);
    }

    #[test]
    fn one_root_overlay_delta_inserts_named_object_assertion_in_canonical_phase() {
        let base = named_object_property_assertion_base_fixture();
        let delta =
            named_object_property_assertion_delta_fixture(b"urn:p", b"urn:j", b"urn:B", false);
        let options = DirectCompileOptions {
            bidirectional: true,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 3,
            max_iri_bytes: 1024,
        };
        let state = running_state();
        let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &state,
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        let stats = prepared.statistics();
        assert_eq!(stats.roots, 3);
        assert_eq!(stats.nodes, 19);
        assert_eq!(stats.object_property_assertions, 3);
        assert_eq!(stats.edges, 3);
        assert_eq!(
            stats.buffer_bytes,
            base.columns().buffer_bytes().unwrap() + delta.columns().buffer_bytes().unwrap()
        );
        assert_eq!(prepared.emission_attempts(), 0);

        let (first, first_cursor) = prepared
            .prepare_next_batch(base.columns(), &state, 1)
            .unwrap();
        let (retry, _) = prepared
            .prepare_next_batch(base.columns(), &state, 1)
            .unwrap();
        assert_eq!(first, retry);
        assert_eq!(prepared.remaining_edges(), 3);
        prepared.commit_cursor(first_cursor);
        let mut edges = first;
        while prepared.remaining_edges() != 0 {
            let (batch, cursor) = prepared
                .prepare_next_batch(base.columns(), &state, 1)
                .unwrap();
            edges.extend(batch);
            prepared.commit_cursor(cursor);
        }
        assert_eq!(
            edges,
            vec![
                DirectEdge {
                    source: "urn:i".into(),
                    relation: "urn:p".into(),
                    destination: "urn:A".into(),
                },
                DirectEdge {
                    source: "urn:j".into(),
                    relation: "urn:p".into(),
                    destination: "urn:B".into(),
                },
                DirectEdge {
                    source: "urn:k".into(),
                    relation: "urn:p".into(),
                    destination: "urn:C".into(),
                },
            ]
        );

        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    max_edges: 2,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Resource(message)) if message.contains("configured limit")
        ));
    }

    #[test]
    fn one_root_overlay_object_assertion_composes_exclusions_modes_and_fallbacks() {
        let base = named_object_property_assertion_base_fixture();
        let delta =
            named_object_property_assertion_delta_fixture(b"urn:p", b"urn:j", b"urn:B", false);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 3,
            max_iri_bytes: 1024,
        };
        for (excluded, expected_sources) in [
            (Vec::new(), vec!["urn:i", "urn:j", "urn:k"]),
            (1_u32.to_le_bytes().to_vec(), vec!["urn:j", "urn:k"]),
            (2_u32.to_le_bytes().to_vec(), vec!["urn:i", "urn:j"]),
            (
                [1_u32.to_le_bytes(), 2_u32.to_le_bytes()].concat(),
                vec!["urn:j"],
            ),
        ] {
            let base_columns = base.columns().with_excluded_root_ids(&excluded);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base_columns,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, expected_sources.len());
            assert_eq!(
                prepared.statistics().object_property_assertions,
                expected_sources.len()
            );
            assert_eq!(prepared.statistics().edges, expected_sources.len());
            let (edges, cursor) = prepared
                .prepare_next_batch(base_columns, &running_state(), expected_sources.len())
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges
                    .iter()
                    .map(|edge| edge.source.as_str())
                    .collect::<Vec<_>>(),
                expected_sources
            );
            assert!(prepared.is_exhausted());
        }

        let mut only_taxonomy = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            DirectCompileOptions {
                only_taxonomy: true,
                ..options
            },
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(only_taxonomy.statistics().object_property_assertions, 3);
        assert_eq!(only_taxonomy.statistics().edges, 3);
        let (edges, cursor) = only_taxonomy
            .prepare_next_batch(base.columns(), &running_state(), 3)
            .unwrap();
        only_taxonomy.commit_cursor(cursor);
        assert_eq!(edges.len(), 3);
        assert!(only_taxonomy.is_exhausted());

        let asserted = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            DirectCompileOptions {
                asserted_taxonomy_only: true,
                ..options
            },
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(asserted.statistics().object_property_assertions, 3);
        assert_eq!(asserted.statistics().edges, 0);
        assert_eq!(asserted.emission_attempts(), 0);
        assert!(asserted.is_exhausted());

        let duplicate =
            named_object_property_assertion_delta_fixture(b"urn:p", b"urn:i", b"urn:A", false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                duplicate.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated =
            named_object_property_assertion_delta_fixture(b"urn:p", b"urn:j", b"urn:B", true);
        let annotated_prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            annotated.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(annotated_prepared.statistics().roots, 3);
        assert_eq!(
            annotated_prepared.statistics().object_property_assertions,
            3
        );
        assert_eq!(annotated_prepared.statistics().edges, 3);
        assert_eq!(annotated_prepared.emission_attempts(), 0);

        let anonymous_annotated = anonymous_annotated_object_property_assertion_delta_fixture();
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                anonymous_annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message))
                if message.contains(
                    "ObjectPropertyAssertion root annotations require no anonymous individuals or local scope remap"
                )
        ));

        let inverse = inverse_object_property_assertion_delta_fixture();
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                inverse.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::ReferenceFailure(message)) if message.contains("inverse object-property assertions")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_negative_object_assertions() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for inverse_property in [false, true] {
            let delta = negative_object_property_assertion_delta_fixture(
                b"urn:p",
                b"urn:j",
                b"urn:B",
                inverse_property,
                false,
            );
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(prepared.statistics().negative_object_property_assertions, 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(
                prepared.statistics().buffer_bytes,
                base.columns().buffer_bytes().unwrap() + delta.columns().buffer_bytes().unwrap()
            );
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(asserted.statistics().negative_object_property_assertions, 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = negative_object_property_assertion_delta_fixture(
            b"urn:p", b"urn:j", b"urn:B", false, false,
        );
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().negative_object_property_assertions, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate = negative_object_property_assertion_delta_fixture(
            b"urn:p", b"urn:j", b"urn:i", false, false,
        );
        let duplicate_base = named_object_assertion_fixture();
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                duplicate.columns(),
                DirectCompileOptions {
                    max_edges: 3,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = negative_object_property_assertion_delta_fixture(
            b"urn:p", b"urn:j", b"urn:B", false, true,
        );
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_data_property_assertions() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for (lexical, datatype) in [
            (b"value".as_slice(), XSD_STRING.as_bytes()),
            (
                b"7".as_slice(),
                b"http://www.w3.org/2001/XMLSchema#integer".as_slice(),
            ),
        ] {
            let delta = data_property_assertion_delta_fixture(
                TAG_DATA_PROPERTY_ASSERTION,
                b"urn:dp",
                b"urn:i",
                lexical,
                datatype,
                false,
            );
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(prepared.statistics().data_property_assertions, 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(
                prepared.statistics().buffer_bytes,
                base.columns().buffer_bytes().unwrap() + delta.columns().buffer_bytes().unwrap()
            );
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(asserted.statistics().data_property_assertions, 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = data_property_assertion_delta_fixture(
            TAG_DATA_PROPERTY_ASSERTION,
            b"urn:dp",
            b"urn:i",
            b"value",
            XSD_STRING.as_bytes(),
            false,
        );
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().data_property_assertions, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base = data_property_assertion_delta_fixture(
            TAG_DATA_PROPERTY_ASSERTION,
            b"urn:dp",
            b"urn:i",
            b"value",
            XSD_STRING.as_bytes(),
            false,
        );
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = data_property_assertion_delta_fixture(
            TAG_DATA_PROPERTY_ASSERTION,
            b"urn:dp",
            b"urn:i",
            b"value",
            XSD_STRING.as_bytes(),
            true,
        );
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_negative_data_property_assertions() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for (lexical, datatype) in [
            (b"blocked".as_slice(), XSD_STRING.as_bytes()),
            (
                b"9".as_slice(),
                b"http://www.w3.org/2001/XMLSchema#integer".as_slice(),
            ),
        ] {
            let delta = data_property_assertion_delta_fixture(
                TAG_NEGATIVE_DATA_PROPERTY_ASSERTION,
                b"urn:dp",
                b"urn:i",
                lexical,
                datatype,
                false,
            );
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(prepared.statistics().negative_data_property_assertions, 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(
                prepared.statistics().buffer_bytes,
                base.columns().buffer_bytes().unwrap() + delta.columns().buffer_bytes().unwrap()
            );
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(asserted.statistics().negative_data_property_assertions, 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = data_property_assertion_delta_fixture(
            TAG_NEGATIVE_DATA_PROPERTY_ASSERTION,
            b"urn:dp",
            b"urn:i",
            b"blocked",
            XSD_STRING.as_bytes(),
            false,
        );
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().negative_data_property_assertions, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base = data_property_assertion_delta_fixture(
            TAG_NEGATIVE_DATA_PROPERTY_ASSERTION,
            b"urn:dp",
            b"urn:i",
            b"blocked",
            XSD_STRING.as_bytes(),
            false,
        );
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = data_property_assertion_delta_fixture(
            TAG_NEGATIVE_DATA_PROPERTY_ASSERTION,
            b"urn:dp",
            b"urn:i",
            b"blocked",
            XSD_STRING.as_bytes(),
            true,
        );
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_sub_data_property_axioms() {
        let base = named_subclass_fixture();
        let delta = sub_data_property_delta_fixture(b"urn:dp", b"urn:dq", false);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(prepared.statistics().roots, 3);
        assert_eq!(prepared.statistics().sub_data_properties, 1);
        assert_eq!(prepared.statistics().skipped_axioms, 1);
        assert_eq!(prepared.statistics().edges, 1);
        assert_eq!(prepared.emission_attempts(), 0);
        let (edges, cursor) = prepared
            .prepare_next_batch(base.columns(), &running_state(), 1)
            .unwrap();
        prepared.commit_cursor(cursor);
        assert_eq!(
            edges,
            vec![DirectEdge {
                source: "urn:A".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:B".into(),
            }]
        );
        assert!(prepared.is_exhausted());

        let asserted = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            DirectCompileOptions {
                asserted_taxonomy_only: true,
                ..options
            },
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(asserted.statistics().sub_data_properties, 1);
        assert_eq!(asserted.statistics().skipped_axioms, 0);
        assert_eq!(asserted.statistics().edges, 1);
        assert_eq!(asserted.emission_attempts(), 0);

        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().sub_data_properties, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base = sub_data_property_delta_fixture(b"urn:dp", b"urn:dq", false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = sub_data_property_delta_fixture(b"urn:dp", b"urn:dq", true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_equivalent_data_properties() {
        let base = named_subclass_fixture();
        let binary = [b"urn:dp".as_slice(), b"urn:dq".as_slice()];
        let ternary = [
            b"urn:dp".as_slice(),
            b"urn:dq".as_slice(),
            b"urn:dr".as_slice(),
        ];
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for property_iris in [&binary[..], &ternary[..]] {
            let delta = data_property_set_delta_fixture(
                TAG_EQUIVALENT_DATA_PROPERTIES,
                property_iris,
                false,
            );
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(prepared.statistics().equivalent_data_properties, 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(asserted.statistics().equivalent_data_properties, 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = data_property_set_delta_fixture(TAG_EQUIVALENT_DATA_PROPERTIES, &binary, false);
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().equivalent_data_properties, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base =
            data_property_set_delta_fixture(TAG_EQUIVALENT_DATA_PROPERTIES, &binary, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated =
            data_property_set_delta_fixture(TAG_EQUIVALENT_DATA_PROPERTIES, &binary, true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_disjoint_data_properties() {
        let base = named_subclass_fixture();
        let binary = [b"urn:dp".as_slice(), b"urn:dq".as_slice()];
        let ternary = [
            b"urn:dp".as_slice(),
            b"urn:dq".as_slice(),
            b"urn:dr".as_slice(),
        ];
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for property_iris in [&binary[..], &ternary[..]] {
            let delta =
                data_property_set_delta_fixture(TAG_DISJOINT_DATA_PROPERTIES, property_iris, false);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(prepared.statistics().disjoint_data_properties, 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(asserted.statistics().disjoint_data_properties, 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = data_property_set_delta_fixture(TAG_DISJOINT_DATA_PROPERTIES, &binary, false);
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().disjoint_data_properties, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base =
            data_property_set_delta_fixture(TAG_DISJOINT_DATA_PROPERTIES, &binary, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated =
            data_property_set_delta_fixture(TAG_DISJOINT_DATA_PROPERTIES, &binary, true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_data_property_domains() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for complex_domain in [false, true] {
            let delta = data_property_domain_delta_fixture(complex_domain, false);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(prepared.statistics().data_property_domains, 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(asserted.statistics().data_property_domains, 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = data_property_domain_delta_fixture(false, false);
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().data_property_domains, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base = data_property_domain_delta_fixture(false, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = data_property_domain_delta_fixture(false, true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_data_property_ranges() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for complex_range in [false, true] {
            let delta = data_property_range_delta_fixture(complex_range, false);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(prepared.statistics().data_property_ranges, 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(asserted.statistics().data_property_ranges, 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = data_property_range_delta_fixture(false, false);
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().data_property_ranges, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base = data_property_range_delta_fixture(false, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = data_property_range_delta_fixture(false, true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_functional_data_properties() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        let delta = functional_data_property_delta_fixture(false);
        let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(prepared.statistics().roots, 3);
        assert_eq!(prepared.statistics().functional_data_properties, 1);
        assert_eq!(prepared.statistics().skipped_axioms, 1);
        assert_eq!(prepared.statistics().edges, 1);
        assert_eq!(prepared.emission_attempts(), 0);
        let (edges, cursor) = prepared
            .prepare_next_batch(base.columns(), &running_state(), 1)
            .unwrap();
        prepared.commit_cursor(cursor);
        assert_eq!(
            edges,
            vec![DirectEdge {
                source: "urn:A".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:B".into(),
            }]
        );
        assert!(prepared.is_exhausted());

        let asserted = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            DirectCompileOptions {
                asserted_taxonomy_only: true,
                ..options
            },
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(asserted.statistics().functional_data_properties, 1);
        assert_eq!(asserted.statistics().skipped_axioms, 0);
        assert_eq!(asserted.statistics().edges, 1);
        assert_eq!(asserted.emission_attempts(), 0);

        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().functional_data_properties, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base = functional_data_property_delta_fixture(false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = functional_data_property_delta_fixture(true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_datatype_definitions() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for complex_range in [false, true] {
            let delta = datatype_definition_delta_fixture(complex_range, false);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(prepared.statistics().datatype_definitions, 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(asserted.statistics().datatype_definitions, 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = datatype_definition_delta_fixture(false, false);
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().datatype_definitions, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base = datatype_definition_delta_fixture(false, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = datatype_definition_delta_fixture(false, true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_object_property_axioms() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        let characteristic_tags = [
            TAG_FUNCTIONAL_OBJECT_PROPERTY,
            TAG_INVERSE_FUNCTIONAL_OBJECT_PROPERTY,
            TAG_REFLEXIVE_OBJECT_PROPERTY,
            TAG_IRREFLEXIVE_OBJECT_PROPERTY,
            TAG_SYMMETRIC_OBJECT_PROPERTY,
            TAG_ASYMMETRIC_OBJECT_PROPERTY,
            TAG_TRANSITIVE_OBJECT_PROPERTY,
        ];
        let mut cases: Vec<(u16, Vec<&[u8]>, u8)> = Vec::new();
        for tag in [
            TAG_EQUIVALENT_OBJECT_PROPERTIES,
            TAG_DISJOINT_OBJECT_PROPERTIES,
        ] {
            cases.push((tag, vec![b"urn:op".as_slice(), b"urn:oq"], 0));
            cases.push((tag, vec![b"urn:op".as_slice(), b"urn:oq", b"urn:or"], 0b100));
        }
        for tag in characteristic_tags {
            for inverse_mask in [0, 1] {
                cases.push((tag, vec![b"urn:op".as_slice()], inverse_mask));
            }
        }

        for (tag, property_iris, inverse_mask) in cases {
            let delta =
                silent_object_property_delta_fixture(tag, &property_iris, inverse_mask, false);
            let kind = SilentObjectPropertyRoot::classify(
                delta
                    .columns()
                    .classify_roots(options.max_iri_bytes, &running_state())
                    .unwrap(),
                tag,
            )
            .unwrap();
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(kind.statistics_count(&prepared.statistics()), 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(kind.statistics_count(&asserted.statistics()), 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = silent_object_property_delta_fixture(
            TAG_EQUIVALENT_OBJECT_PROPERTIES,
            &[b"urn:op", b"urn:oq"],
            0,
            false,
        );
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().equivalent_object_properties, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base = silent_object_property_delta_fixture(
            TAG_EQUIVALENT_OBJECT_PROPERTIES,
            &[b"urn:op", b"urn:oq"],
            0,
            false,
        );
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        for tag in [
            TAG_EQUIVALENT_OBJECT_PROPERTIES,
            TAG_DISJOINT_OBJECT_PROPERTIES,
            TAG_FUNCTIONAL_OBJECT_PROPERTY,
            TAG_INVERSE_FUNCTIONAL_OBJECT_PROPERTY,
            TAG_REFLEXIVE_OBJECT_PROPERTY,
            TAG_IRREFLEXIVE_OBJECT_PROPERTY,
            TAG_SYMMETRIC_OBJECT_PROPERTY,
            TAG_ASYMMETRIC_OBJECT_PROPERTY,
            TAG_TRANSITIVE_OBJECT_PROPERTY,
        ] {
            let property_iris: &[&[u8]] = if [
                TAG_EQUIVALENT_OBJECT_PROPERTIES,
                TAG_DISJOINT_OBJECT_PROPERTIES,
            ]
            .contains(&tag)
            {
                &[b"urn:op", b"urn:oq"]
            } else {
                &[b"urn:op"]
            };
            let annotated = silent_object_property_delta_fixture(tag, property_iris, 0, true);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    annotated.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
            ));
        }

        for tag in [
            TAG_EQUIVALENT_OBJECT_PROPERTIES,
            TAG_DISJOINT_OBJECT_PROPERTIES,
        ] {
            let oversized = silent_object_property_delta_fixture(
                tag,
                &[b"urn:op", b"urn:oq", b"urn:or", b"urn:os"],
                0,
                false,
            );
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    oversized.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message))
                    if message.contains("canonical binary or ternary")
            ));
        }
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_annotation_property_axioms() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for tag in [
            TAG_SUB_ANNOTATION_PROPERTY_OF,
            TAG_ANNOTATION_PROPERTY_DOMAIN,
            TAG_ANNOTATION_PROPERTY_RANGE,
        ] {
            let delta = silent_annotation_property_delta_fixture(tag, false);
            let kind = SilentAnnotationPropertyRoot::classify(
                delta
                    .columns()
                    .classify_roots(options.max_iri_bytes, &running_state())
                    .unwrap(),
                tag,
            )
            .unwrap();
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(kind.statistics_count(&prepared.statistics()), 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(kind.statistics_count(&asserted.statistics()), 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = silent_annotation_property_delta_fixture(TAG_SUB_ANNOTATION_PROPERTY_OF, false);
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().sub_annotation_properties, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base =
            silent_annotation_property_delta_fixture(TAG_SUB_ANNOTATION_PROPERTY_OF, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        for tag in [
            TAG_SUB_ANNOTATION_PROPERTY_OF,
            TAG_ANNOTATION_PROPERTY_DOMAIN,
            TAG_ANNOTATION_PROPERTY_RANGE,
        ] {
            let annotated = silent_annotation_property_delta_fixture(tag, true);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    annotated.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
            ));
        }
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_class_disjointness_axioms() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for tag in [TAG_DISJOINT_CLASSES, TAG_DISJOINT_UNION] {
            for (member_count, recursive_member) in [(2, false), (3, false), (2, true)] {
                let delta = silent_class_disjointness_delta_fixture(
                    tag,
                    member_count,
                    recursive_member,
                    false,
                );
                let kind = SilentClassDisjointnessRoot::classify(
                    delta
                        .columns()
                        .classify_roots(options.max_iri_bytes, &running_state())
                        .unwrap(),
                    tag,
                )
                .unwrap();
                let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                )
                .unwrap();
                assert_eq!(prepared.statistics().roots, 3);
                assert_eq!(kind.statistics_count(&prepared.statistics()), 1);
                assert_eq!(prepared.statistics().skipped_axioms, 1);
                assert_eq!(prepared.statistics().edges, 1);
                assert_eq!(prepared.emission_attempts(), 0);
                let (edges, cursor) = prepared
                    .prepare_next_batch(base.columns(), &running_state(), 1)
                    .unwrap();
                prepared.commit_cursor(cursor);
                assert_eq!(
                    edges,
                    vec![DirectEdge {
                        source: "urn:A".into(),
                        relation: SUBCLASS_OF.into(),
                        destination: "urn:B".into(),
                    }]
                );
                assert!(prepared.is_exhausted());

                let asserted = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    DirectCompileOptions {
                        asserted_taxonomy_only: true,
                        ..options
                    },
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                )
                .unwrap();
                assert_eq!(kind.statistics_count(&asserted.statistics()), 1);
                assert_eq!(asserted.statistics().skipped_axioms, 0);
                assert_eq!(asserted.statistics().edges, 1);
                assert_eq!(asserted.emission_attempts(), 0);
            }
        }

        let delta = silent_class_disjointness_delta_fixture(TAG_DISJOINT_CLASSES, 2, false, false);
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().disjoint_classes, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base =
            silent_class_disjointness_delta_fixture(TAG_DISJOINT_CLASSES, 2, false, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        for tag in [TAG_DISJOINT_CLASSES, TAG_DISJOINT_UNION] {
            let annotated = silent_class_disjointness_delta_fixture(tag, 2, false, true);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    annotated.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
            ));

            let oversized = silent_class_disjointness_delta_fixture(tag, 4, false, false);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    oversized.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message))
                    if message.contains("canonical binary or ternary")
            ));
        }
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_ignored_class_axioms() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for root_tag in [TAG_SUB_CLASS_OF, TAG_CLASS_ASSERTION] {
            for recursive in [false, true] {
                let delta = ignored_class_axiom_delta_fixture(root_tag, recursive, false, false);
                let counts = delta
                    .columns()
                    .classify_roots(options.max_iri_bytes, &running_state())
                    .unwrap();
                let kind =
                    SilentIgnoredClassRoot::classify(counts, root_tag).expect("ignored root");
                let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                )
                .unwrap();
                let statistics = prepared.statistics();
                assert_eq!(statistics.roots, 3);
                assert_eq!(
                    statistics.subclasses,
                    1 + usize::from(root_tag == TAG_SUB_CLASS_OF)
                );
                assert_eq!(
                    statistics.ignored_subclasses,
                    usize::from(root_tag == TAG_SUB_CLASS_OF)
                );
                assert_eq!(
                    statistics.class_assertions,
                    usize::from(root_tag == TAG_CLASS_ASSERTION)
                );
                assert_eq!(
                    statistics.ignored_class_assertions,
                    usize::from(root_tag == TAG_CLASS_ASSERTION)
                );
                assert_eq!(statistics.skipped_axioms, 0);
                assert_eq!(statistics.edges, 1);
                assert_eq!(prepared.emission_attempts(), 0);
                assert!(prepared.preparation.overlay_deltas.is_empty());
                assert_eq!(
                    kind.constructor(),
                    if root_tag == TAG_SUB_CLASS_OF {
                        "SubClassOf"
                    } else {
                        "ClassAssertion"
                    }
                );
                let (edges, cursor) = prepared
                    .prepare_next_batch(base.columns(), &running_state(), 1)
                    .unwrap();
                prepared.commit_cursor(cursor);
                assert_eq!(
                    edges,
                    vec![DirectEdge {
                        source: "urn:A".into(),
                        relation: SUBCLASS_OF.into(),
                        destination: "urn:B".into(),
                    }]
                );
                assert!(prepared.is_exhausted());

                for mode in [
                    DirectCompileOptions {
                        only_taxonomy: true,
                        ..options
                    },
                    DirectCompileOptions {
                        asserted_taxonomy_only: true,
                        ..options
                    },
                ] {
                    let prepared = prepare_single_overlay_delta_batches_uncommitted(
                        base.columns(),
                        delta.columns(),
                        mode,
                        &running_state(),
                        None,
                        canonical_limits().max_work,
                        canonical_limits().max_workspace_bytes,
                    )
                    .unwrap();
                    let statistics = prepared.statistics();
                    assert_eq!(
                        statistics.ignored_subclasses,
                        usize::from(root_tag == TAG_SUB_CLASS_OF)
                    );
                    assert_eq!(
                        statistics.ignored_class_assertions,
                        usize::from(root_tag == TAG_CLASS_ASSERTION)
                    );
                    assert_eq!(statistics.skipped_axioms, 0);
                    assert_eq!(statistics.edges, 1);
                    assert_eq!(prepared.emission_attempts(), 0);
                    assert!(prepared.preparation.overlay_deltas.is_empty());
                }
            }
        }

        for root_tag in [TAG_SUB_CLASS_OF, TAG_CLASS_ASSERTION] {
            let delta = ignored_class_axiom_delta_fixture(root_tag, false, false, false);
            let excluded_subclass = 2_u32.to_le_bytes();
            let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
            let prepared = prepare_single_overlay_delta_batches_uncommitted(
                selected_declaration,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 2);
            assert_eq!(prepared.statistics().edges, 0);
            assert_eq!(prepared.statistics().skipped_axioms, 0);
            assert_eq!(prepared.emission_attempts(), 0);
            assert!(prepared.preparation.overlay_deltas.is_empty());
            assert!(prepared.is_exhausted());

            let duplicate_base = ignored_class_axiom_delta_fixture(root_tag, false, false, false);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    duplicate_base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("duplicates")
            ));

            let annotated = ignored_class_axiom_delta_fixture(root_tag, false, true, false);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    annotated.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
            ));

            let anonymous = ignored_class_axiom_delta_fixture(root_tag, false, false, true);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    anonymous.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message))
                    if message.contains("no anonymous individuals or local scope remap")
            ));
        }
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_ignored_equivalent_classes() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for member_count in [2, 3] {
            for recursive in [false, true] {
                let delta =
                    ignored_equivalent_classes_delta_fixture(member_count, recursive, false, false);
                let counts = delta
                    .columns()
                    .classify_roots(options.max_iri_bytes, &running_state())
                    .unwrap();
                assert_eq!(
                    SilentIgnoredEquivalentRoot::classify(counts, TAG_EQUIVALENT_CLASSES),
                    Some(SilentIgnoredEquivalentRoot)
                );
                let root = delta.columns().root_id(0).unwrap();
                assert_eq!(
                    delta
                        .columns()
                        .equivalent_projection(root, options.max_iri_bytes)
                        .unwrap(),
                    EquivalentProjection::Ignored
                );

                let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                )
                .unwrap_or_else(|error| {
                    panic!(
                        "ignored EquivalentClasses member_count={member_count} recursive={recursive}: {error:?}"
                    )
                });
                let statistics = prepared.statistics();
                assert_eq!(statistics.roots, 3);
                assert_eq!(statistics.equivalents, 1);
                assert_eq!(statistics.aggregate_equivalents, 0);
                assert_eq!(statistics.ignored_equivalents, 1);
                assert_eq!(statistics.skipped_axioms, 0);
                assert_eq!(statistics.edges, 1);
                assert_eq!(prepared.emission_attempts(), 0);
                assert!(prepared.preparation.overlay_deltas.is_empty());
                let (edges, cursor) = prepared
                    .prepare_next_batch(base.columns(), &running_state(), 1)
                    .unwrap();
                prepared.commit_cursor(cursor);
                assert_eq!(
                    edges,
                    vec![DirectEdge {
                        source: "urn:A".into(),
                        relation: SUBCLASS_OF.into(),
                        destination: "urn:B".into(),
                    }]
                );
                assert!(prepared.is_exhausted());

                let only_taxonomy = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    DirectCompileOptions {
                        only_taxonomy: true,
                        ..options
                    },
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                )
                .unwrap();
                assert_eq!(only_taxonomy.statistics().equivalents, 1);
                assert_eq!(only_taxonomy.statistics().aggregate_equivalents, 0);
                assert_eq!(only_taxonomy.statistics().ignored_equivalents, 1);
                assert_eq!(only_taxonomy.statistics().skipped_axioms, 0);
                assert_eq!(only_taxonomy.statistics().edges, 1);
                assert_eq!(only_taxonomy.emission_attempts(), 0);
                assert!(only_taxonomy.preparation.overlay_deltas.is_empty());

                let asserted = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    DirectCompileOptions {
                        asserted_taxonomy_only: true,
                        ..options
                    },
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                )
                .unwrap();
                assert_eq!(asserted.statistics().equivalents, 1);
                assert_eq!(asserted.statistics().aggregate_equivalents, 0);
                assert_eq!(asserted.statistics().ignored_equivalents, 0);
                assert_eq!(asserted.statistics().skipped_axioms, 0);
                assert_eq!(asserted.statistics().edges, 1);
                assert_eq!(asserted.emission_attempts(), 0);
                assert!(asserted.preparation.overlay_deltas.is_empty());
            }
        }

        let delta = ignored_equivalent_classes_delta_fixture(2, false, false, false);
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().equivalents, 1);
        assert_eq!(silent.statistics().ignored_equivalents, 1);
        assert_eq!(silent.statistics().skipped_axioms, 0);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.preparation.overlay_deltas.is_empty());
        assert!(silent.is_exhausted());

        let duplicate_base = ignored_equivalent_classes_delta_fixture(2, false, false, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = ignored_equivalent_classes_delta_fixture(2, false, true, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));

        let anonymous = ignored_equivalent_classes_delta_fixture(2, false, false, true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                anonymous.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message))
                if message.contains("no anonymous individuals or local scope remap")
        ));

        let oversized = ignored_equivalent_classes_delta_fixture(4, true, false, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                oversized.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message))
                if message.contains("canonical binary or ternary")
        ));

        let mut pair = ignored_equivalent_classes_delta_fixture(2, false, false, false);
        let second_item = {
            let columns = pair.columns();
            let root = columns.root_id(0).unwrap();
            let fields = columns.exact_fields(root, 2).unwrap();
            let (items, length) = columns.node_set_range(fields, 2).unwrap();
            assert_eq!(length, 2);
            items + 1
        };
        pair.item_values[second_item * 8..second_item * 8 + 8]
            .copy_from_slice(&7_u64.to_le_bytes());
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                pair.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message))
                if message.contains("requires an ignored complete direct projection")
        ));

        let mut aggregate = ignored_equivalent_classes_delta_fixture(2, true, false, false);
        let (second_item, aggregate_id) = {
            let columns = aggregate.columns();
            let root = columns.root_id(0).unwrap();
            let fields = columns.exact_fields(root, 2).unwrap();
            let (items, length) = columns.node_set_range(fields, 2).unwrap();
            assert_eq!(length, 2);
            let aggregate_id = (1..=columns.node_count())
                .find(|node_id| columns.node_tag(*node_id).unwrap() == TAG_OBJECT_INTERSECTION_OF)
                .unwrap();
            (items + 1, aggregate_id)
        };
        aggregate.item_values[second_item * 8..second_item * 8 + 8]
            .copy_from_slice(&(aggregate_id as u64).to_le_bytes());
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                aggregate.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message))
                if message.contains("requires an ignored complete direct projection")
        ));
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_ignored_object_property_class_axioms() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for root_tag in [TAG_OBJECT_PROPERTY_DOMAIN, TAG_OBJECT_PROPERTY_RANGE] {
            for (inverse_property, recursive_class) in [(true, false), (false, true), (true, true)]
            {
                let delta = ignored_object_property_class_delta_fixture(
                    root_tag,
                    inverse_property,
                    recursive_class,
                    false,
                    false,
                );
                let counts = delta
                    .columns()
                    .classify_roots(options.max_iri_bytes, &running_state())
                    .unwrap();
                let plan = ObjectPropertyClassRulePlan::classify(
                    counts,
                    root_tag,
                    LocalRuleContext::new(options, false),
                )
                .unwrap();
                assert_eq!(plan.rule.tag, root_tag);
                assert!(plan.ignored);
                let root = delta.columns().root_id(0).unwrap();
                assert_eq!(
                    delta
                        .columns()
                        .object_property_class_projection(root, root_tag, options.max_iri_bytes,)
                        .unwrap(),
                    None
                );

                for variant in [
                    options,
                    DirectCompileOptions {
                        only_taxonomy: true,
                        ..options
                    },
                    DirectCompileOptions {
                        asserted_taxonomy_only: true,
                        ..options
                    },
                ] {
                    let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                        base.columns(),
                        delta.columns(),
                        variant,
                        &running_state(),
                        None,
                        canonical_limits().max_work,
                        canonical_limits().max_workspace_bytes,
                    )
                    .unwrap_or_else(|error| {
                        panic!(
                            "ignored {} inverse={inverse_property} recursive={recursive_class}: {error:?}",
                            plan.rule.constructor
                        )
                    });
                    let statistics = prepared.statistics();
                    assert_eq!(statistics.roots, 3);
                    assert_eq!(
                        statistics.object_property_domains,
                        usize::from(root_tag == TAG_OBJECT_PROPERTY_DOMAIN)
                    );
                    assert_eq!(
                        statistics.object_property_ranges,
                        usize::from(root_tag == TAG_OBJECT_PROPERTY_RANGE)
                    );
                    assert_eq!(
                        statistics.ignored_object_property_domains,
                        usize::from(root_tag == TAG_OBJECT_PROPERTY_DOMAIN)
                    );
                    assert_eq!(
                        statistics.ignored_object_property_ranges,
                        usize::from(root_tag == TAG_OBJECT_PROPERTY_RANGE)
                    );
                    assert_eq!(statistics.domain_range_edges, 0);
                    assert_eq!(statistics.role_expansion_edges, 0);
                    assert_eq!(statistics.skipped_axioms, 0);
                    assert_eq!(statistics.edges, 1);
                    assert_eq!(prepared.emission_attempts(), 0);
                    assert!(prepared.preparation.overlay_deltas.is_empty());
                    let (edges, cursor) = prepared
                        .prepare_next_batch(base.columns(), &running_state(), 1)
                        .unwrap();
                    prepared.commit_cursor(cursor);
                    assert_eq!(
                        edges,
                        vec![DirectEdge {
                            source: "urn:A".into(),
                            relation: SUBCLASS_OF.into(),
                            destination: "urn:B".into(),
                        }]
                    );
                    assert!(prepared.is_exhausted());
                }
            }

            let delta =
                ignored_object_property_class_delta_fixture(root_tag, true, false, false, false);
            let excluded_subclass = 2_u32.to_le_bytes();
            let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
            let silent = prepare_single_overlay_delta_batches_uncommitted(
                selected_declaration,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(silent.statistics().roots, 2);
            assert_eq!(silent.statistics().edges, 0);
            assert_eq!(silent.statistics().skipped_axioms, 0);
            assert_eq!(silent.emission_attempts(), 0);
            assert!(silent.preparation.overlay_deltas.is_empty());
            assert!(silent.is_exhausted());

            let duplicate_base =
                ignored_object_property_class_delta_fixture(root_tag, true, false, false, false);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    duplicate_base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("duplicates")
            ));

            let projecting =
                ignored_object_property_class_delta_fixture(root_tag, false, false, false, false);
            let counts = projecting
                .columns()
                .classify_roots(options.max_iri_bytes, &running_state())
                .unwrap();
            let plan = ObjectPropertyClassRulePlan::classify(
                counts,
                root_tag,
                LocalRuleContext::new(options, false),
            )
            .unwrap();
            assert!(!plan.ignored);
            let root = projecting.columns().root_id(0).unwrap();
            let projected = plan
                .validate(
                    projecting.columns(),
                    root,
                    LocalRuleContext::new(options, false),
                    &running_state(),
                )
                .unwrap();
            assert!(matches!(
                projected,
                Some((kind, "urn:p", "urn:A"))
                    if kind
                        == if root_tag == TAG_OBJECT_PROPERTY_DOMAIN {
                            ObjectPropertyClassRuleKind::Domain
                        } else {
                            ObjectPropertyClassRuleKind::Range
                        }
            ));

            let annotated =
                ignored_object_property_class_delta_fixture(root_tag, true, false, true, false);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    annotated.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
            ));

            let anonymous =
                ignored_object_property_class_delta_fixture(root_tag, false, false, false, true);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    anonymous.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message))
                    if message.contains("no anonymous individuals or local scope remap")
            ));
        }
    }

    #[test]
    fn one_root_overlay_delta_projects_named_object_property_class_axioms() {
        let base = local_object_property_class_base_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 6,
            max_iri_bytes: 1024,
        };
        for (root_tag, kind) in [
            (
                TAG_OBJECT_PROPERTY_DOMAIN,
                ObjectPropertyClassRuleKind::Domain,
            ),
            (
                TAG_OBJECT_PROPERTY_RANGE,
                ObjectPropertyClassRuleKind::Range,
            ),
        ] {
            let delta =
                ignored_object_property_class_delta_fixture(root_tag, false, false, false, false);
            let expected = if kind == ObjectPropertyClassRuleKind::Domain {
                vec![
                    DirectEdge {
                        source: "urn:A".into(),
                        relation: "urn:p".into(),
                        destination: "urn:R".into(),
                    },
                    DirectEdge {
                        source: "urn:A".into(),
                        relation: "urn:child".into(),
                        destination: "urn:R".into(),
                    },
                    DirectEdge {
                        source: "urn:R".into(),
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
            } else {
                vec![
                    DirectEdge {
                        source: "urn:D".into(),
                        relation: "urn:p".into(),
                        destination: "urn:A".into(),
                    },
                    DirectEdge {
                        source: "urn:D".into(),
                        relation: "urn:child".into(),
                        destination: "urn:A".into(),
                    },
                    DirectEdge {
                        source: "urn:A".into(),
                        relation: "urn:pinv".into(),
                        destination: "urn:D".into(),
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
            };
            for (variant, projects) in [
                (options, true),
                (
                    DirectCompileOptions {
                        only_taxonomy: true,
                        ..options
                    },
                    true,
                ),
                (
                    DirectCompileOptions {
                        asserted_taxonomy_only: true,
                        ..options
                    },
                    false,
                ),
            ] {
                let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    variant,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                )
                .unwrap_or_else(|error| panic!("local {kind:?}: {error:?}"));
                let statistics = prepared.statistics();
                assert_eq!(statistics.roots, 5);
                assert_eq!(statistics.sub_object_properties, 1);
                assert_eq!(statistics.inverse_object_properties, 1);
                assert_eq!(
                    statistics.object_property_domains,
                    1 + usize::from(kind == ObjectPropertyClassRuleKind::Domain)
                );
                assert_eq!(
                    statistics.object_property_ranges,
                    1 + usize::from(kind == ObjectPropertyClassRuleKind::Range)
                );
                assert_eq!(statistics.ignored_object_property_domains, 0);
                assert_eq!(statistics.ignored_object_property_ranges, 0);
                assert_eq!(statistics.domain_range_edges, if projects { 2 } else { 0 });
                assert_eq!(
                    statistics.role_expansion_edges,
                    if projects { 4 } else { 0 }
                );
                assert_eq!(statistics.skipped_axioms, 0);
                assert_eq!(statistics.edges, if projects { 6 } else { 0 });
                assert_eq!(prepared.emission_attempts(), 0);
                assert!(prepared.preparation.overlay_deltas.is_empty());
                assert_eq!(
                    prepared
                        .preparation
                        .local_object_property_classes
                        .iter()
                        .flatten()
                        .count(),
                    1
                );

                let mut edges = Vec::new();
                while prepared.remaining_edges() != 0 {
                    let (batch, cursor) = prepared
                        .prepare_next_batch(base.columns(), &running_state(), 1)
                        .unwrap();
                    assert_eq!(batch.len(), 1);
                    edges.extend(batch);
                    prepared.commit_cursor(cursor);
                }
                let expected_edges = if projects { expected.as_slice() } else { &[] };
                assert_eq!(edges.as_slice(), expected_edges);
                assert!(prepared.is_exhausted());
            }

            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    DirectCompileOptions {
                        max_edges: 5,
                        ..options
                    },
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Resource(message)) if message.contains("requires 6 edges")
            ));
            let retry = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(retry.statistics().edges, 6);
            assert_eq!(retry.emission_attempts(), 0);

            let excluded_same_kind = if kind == ObjectPropertyClassRuleKind::Domain {
                3_u32.to_le_bytes()
            } else {
                4_u32.to_le_bytes()
            };
            let selected_base = base.columns().with_excluded_root_ids(&excluded_same_kind);
            let mut selected = prepare_single_overlay_delta_batches_uncommitted(
                selected_base,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(selected.statistics().roots, 4);
            assert_eq!(selected.statistics().domain_range_edges, 1);
            assert_eq!(selected.statistics().role_expansion_edges, 2);
            assert_eq!(selected.statistics().edges, 3);
            assert_eq!(selected.emission_attempts(), 0);
            let mut selected_edges = Vec::new();
            while selected.remaining_edges() != 0 {
                let (batch, cursor) = selected
                    .prepare_next_batch(selected_base, &running_state(), 1)
                    .unwrap();
                assert_eq!(batch.len(), 1);
                selected_edges.extend(batch);
                selected.commit_cursor(cursor);
            }
            assert_eq!(selected_edges.as_slice(), &expected[..3]);
            assert!(selected.is_exhausted());
            assert_eq!(
                selected
                    .cursor
                    .next_edge(selected_base, &selected.preparation, &running_state(),)
                    .unwrap(),
                None,
            );

            let excluded_counterpart = if kind == ObjectPropertyClassRuleKind::Domain {
                4_u32.to_le_bytes()
            } else {
                3_u32.to_le_bytes()
            };
            let selected_base = base.columns().with_excluded_root_ids(&excluded_counterpart);
            let selected = prepare_single_overlay_delta_batches_uncommitted(
                selected_base,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(selected.statistics().roots, 4);
            assert_eq!(selected.statistics().domain_range_edges, 0);
            assert_eq!(selected.statistics().role_expansion_edges, 0);
            assert_eq!(selected.statistics().edges, 0);
            assert_eq!(selected.emission_attempts(), 0);
            assert!(selected.is_exhausted());
        }
    }

    #[test]
    fn two_root_overlay_delta_projects_local_domain_range_pair_transactionally() {
        let base = overlay_role_base_fixture();
        let delta =
            local_object_property_class_pair_delta_fixture(TAG_OBJECT_PROPERTY_RANGE, true, false);
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 3,
            max_iri_bytes: 1024,
        };
        let expected = vec![
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
        ];

        for (variant, projects) in [
            (options, true),
            (
                DirectCompileOptions {
                    only_taxonomy: true,
                    ..options
                },
                true,
            ),
            (
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                false,
            ),
        ] {
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                variant,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            let statistics = prepared.statistics();
            assert_eq!(statistics.roots, 4);
            assert_eq!(statistics.sub_object_properties, 1);
            assert_eq!(statistics.inverse_object_properties, 1);
            assert_eq!(statistics.object_property_domains, 1);
            assert_eq!(statistics.object_property_ranges, 1);
            assert_eq!(statistics.domain_range_edges, usize::from(projects));
            assert_eq!(
                statistics.role_expansion_edges,
                if projects { 2 } else { 0 }
            );
            assert_eq!(statistics.edges, if projects { 3 } else { 0 });
            assert_eq!(prepared.emission_attempts(), 0);
            assert!(prepared.preparation.overlay_deltas.is_empty());
            assert_eq!(
                prepared
                    .preparation
                    .local_object_property_classes
                    .iter()
                    .flatten()
                    .count(),
                2
            );

            let mut edges = Vec::new();
            if projects {
                let (first, first_cursor) = prepared
                    .prepare_next_batch(base.columns(), &running_state(), 1)
                    .unwrap();
                let (retry, _) = prepared
                    .prepare_next_batch(base.columns(), &running_state(), 1)
                    .unwrap();
                assert_eq!(first, retry);
                assert_eq!(first.as_slice(), &expected[..1]);
                prepared.commit_cursor(first_cursor);
                edges.extend(first);
            }
            while prepared.remaining_edges() != 0 {
                let (batch, cursor) = prepared
                    .prepare_next_batch(base.columns(), &running_state(), 1)
                    .unwrap();
                edges.extend(batch);
                prepared.commit_cursor(cursor);
            }
            assert_eq!(
                edges.as_slice(),
                if projects { expected.as_slice() } else { &[] }
            );
            assert!(prepared.is_exhausted());
        }

        let cancellation = AtomicU8::new(STATE_RUNNING);
        let mut cancellable = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &cancellation,
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        let remaining = cancellable.remaining_edges();
        let (preview, cursor) = cancellable
            .prepare_next_batch(base.columns(), &cancellation, 1)
            .unwrap();
        assert_eq!(cancellable.remaining_edges(), remaining);
        cancellation.store(STATE_CANCELLED, Ordering::Release);
        assert!(matches!(
            cancellable.prepare_next_batch(base.columns(), &cancellation, 1),
            Err(KernelError::Cancelled)
        ));
        assert_eq!(cancellable.remaining_edges(), remaining);
        cancellation.store(STATE_RUNNING, Ordering::Release);
        let (retry, _) = cancellable
            .prepare_next_batch(base.columns(), &cancellation, 1)
            .unwrap();
        assert_eq!(retry, preview);
        cancellable.commit_cursor(cursor);
        assert_eq!(cancellable.remaining_edges(), remaining - 1);

        let excluded_sub = 1_u32.to_le_bytes();
        let excluded_inverse = 2_u32.to_le_bytes();
        let mut excluded_both = Vec::new();
        excluded_both.extend_from_slice(&excluded_sub);
        excluded_both.extend_from_slice(&excluded_inverse);
        for (excluded, expected_edges, expected_expansion) in [
            (excluded_sub.as_slice(), 2_usize, 1_usize),
            (excluded_inverse.as_slice(), 2, 1),
            (excluded_both.as_slice(), 1, 0),
        ] {
            let selected_base = base.columns().with_excluded_root_ids(excluded);
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                selected_base,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 4 - excluded.len() / 4);
            assert_eq!(prepared.statistics().domain_range_edges, 1);
            assert_eq!(
                prepared.statistics().role_expansion_edges,
                expected_expansion
            );
            assert_eq!(prepared.statistics().edges, expected_edges);
            let mut edges = Vec::new();
            while prepared.remaining_edges() != 0 {
                let (batch, cursor) = prepared
                    .prepare_next_batch(selected_base, &running_state(), 1)
                    .unwrap();
                edges.extend(batch);
                prepared.commit_cursor(cursor);
            }
            let expected_selected = match excluded {
                value if value == excluded_sub.as_slice() => vec![
                    DirectEdge {
                        source: "urn:D".into(),
                        relation: "urn:p".into(),
                        destination: "urn:R".into(),
                    },
                    DirectEdge {
                        source: "urn:R".into(),
                        relation: "urn:pinv".into(),
                        destination: "urn:D".into(),
                    },
                ],
                value if value == excluded_inverse.as_slice() => vec![
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
                ],
                _ => vec![DirectEdge {
                    source: "urn:D".into(),
                    relation: "urn:p".into(),
                    destination: "urn:R".into(),
                }],
            };
            assert_eq!(edges, expected_selected);
        }

        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    max_edges: 2,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Resource(message)) if message.contains("requires 3 edges")
        ));
        let retry = prepare_single_overlay_delta_batches_uncommitted(
            base.columns(),
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(retry.statistics().edges, 3);
        assert_eq!(retry.emission_attempts(), 0);

        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                1,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Resource(message)) if message.contains("work")
        ));
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                1,
            ),
            Err(KernelError::Resource(message)) if message.contains("workspace")
        ));
    }

    #[test]
    fn two_root_overlay_delta_rejects_noncanonical_domain_range_envelopes() {
        let base = overlay_role_base_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 3,
            max_iri_bytes: 1024,
        };
        for (delta, expected) in [
            (
                local_object_property_class_pair_delta_fixture(
                    TAG_OBJECT_PROPERTY_DOMAIN,
                    true,
                    false,
                ),
                LOCAL_EMITTING_OVERLAY_REQUIREMENT,
            ),
            (
                local_object_property_class_pair_delta_fixture(
                    TAG_OBJECT_PROPERTY_RANGE,
                    false,
                    false,
                ),
                "same named object property",
            ),
            (
                local_object_property_class_pair_delta_fixture(
                    TAG_OBJECT_PROPERTY_RANGE,
                    true,
                    true,
                ),
                "must be unannotated",
            ),
        ] {
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains(expected)
            ));
        }
    }

    #[test]
    fn one_root_overlay_delta_accepts_state_neutral_property_chains() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for chain_length in [2, 3] {
            for inverse_member in [false, true] {
                let delta = local_role_delta_fixture(
                    TAG_SUB_OBJECT_PROPERTY_OF,
                    chain_length,
                    inverse_member,
                    false,
                );
                let counts = delta
                    .columns()
                    .classify_roots(options.max_iri_bytes, &running_state())
                    .unwrap();
                let plan = LocalRoleRulePlan::classify(
                    counts,
                    TAG_SUB_OBJECT_PROPERTY_OF,
                    LocalRuleContext::new(options, false),
                )
                .unwrap();
                assert_eq!(plan.rule.kind, LocalRoleRuleKind::PropertyChain);
                assert!(!plan.rule.mutates_role_state);
                let root = delta.columns().root_id(0).unwrap();
                assert!(delta
                    .columns()
                    .validate_sub_object_property_of(root, options.max_iri_bytes)
                    .unwrap());

                for variant in [
                    options,
                    DirectCompileOptions {
                        only_taxonomy: true,
                        ..options
                    },
                    DirectCompileOptions {
                        asserted_taxonomy_only: true,
                        ..options
                    },
                ] {
                    let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                        base.columns(),
                        delta.columns(),
                        variant,
                        &running_state(),
                        None,
                        canonical_limits().max_work,
                        canonical_limits().max_workspace_bytes,
                    )
                    .unwrap_or_else(|error| {
                        panic!(
                            "property chain length={chain_length} inverse={inverse_member}: {error:?}"
                        )
                    });
                    let statistics = prepared.statistics();
                    assert_eq!(statistics.roots, 3);
                    assert_eq!(statistics.sub_object_properties, 1);
                    assert_eq!(statistics.object_property_chains, 1);
                    assert_eq!(statistics.role_expansion_edges, 0);
                    assert_eq!(statistics.skipped_axioms, 0);
                    assert_eq!(statistics.edges, 1);
                    assert_eq!(prepared.emission_attempts(), 0);
                    assert!(prepared.preparation.overlay_deltas.is_empty());
                    let (edges, cursor) = prepared
                        .prepare_next_batch(base.columns(), &running_state(), 1)
                        .unwrap();
                    prepared.commit_cursor(cursor);
                    assert_eq!(
                        edges,
                        vec![DirectEdge {
                            source: "urn:A".into(),
                            relation: SUBCLASS_OF.into(),
                            destination: "urn:B".into(),
                        }]
                    );
                    assert!(prepared.is_exhausted());
                }
            }
        }

        let delta = local_role_delta_fixture(TAG_SUB_OBJECT_PROPERTY_OF, 2, true, false);
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().sub_object_properties, 1);
        assert_eq!(silent.statistics().object_property_chains, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.statistics().skipped_axioms, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.preparation.overlay_deltas.is_empty());
        assert!(silent.is_exhausted());

        let duplicate_base = local_role_delta_fixture(TAG_SUB_OBJECT_PROPERTY_OF, 2, true, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = local_role_delta_fixture(TAG_SUB_OBJECT_PROPERTY_OF, 2, false, true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));
    }

    #[test]
    fn one_root_overlay_delta_recomputes_base_projection_for_stateful_role_axioms() {
        let base = local_role_projection_base_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 3,
            max_iri_bytes: 1024,
        };
        for (root_tag, kind) in [
            (
                TAG_SUB_OBJECT_PROPERTY_OF,
                LocalRoleRuleKind::SimpleSubProperty,
            ),
            (
                TAG_INVERSE_OBJECT_PROPERTIES,
                LocalRoleRuleKind::InverseProperties,
            ),
        ] {
            let delta = local_role_delta_fixture(root_tag, 0, false, false);
            let counts = delta
                .columns()
                .classify_roots(options.max_iri_bytes, &running_state())
                .unwrap();
            let plan = LocalRoleRulePlan::classify(
                counts,
                root_tag,
                LocalRuleContext::new(options, false),
            )
            .unwrap();
            assert_eq!(plan.rule.kind, kind);
            assert!(plan.rule.mutates_role_state);

            for (variant, projects_roles) in [
                (options, true),
                (
                    DirectCompileOptions {
                        only_taxonomy: true,
                        ..options
                    },
                    false,
                ),
                (
                    DirectCompileOptions {
                        asserted_taxonomy_only: true,
                        ..options
                    },
                    false,
                ),
            ] {
                let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    variant,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                )
                .unwrap_or_else(|error| panic!("local role kind={kind:?}: {error:?}"));
                let statistics = prepared.statistics();
                assert_eq!(statistics.roots, 3);
                assert_eq!(statistics.subclasses, 2);
                assert_eq!(statistics.restriction_subclasses, 1);
                assert_eq!(
                    statistics.sub_object_properties,
                    usize::from(kind == LocalRoleRuleKind::SimpleSubProperty)
                );
                assert_eq!(
                    statistics.inverse_object_properties,
                    usize::from(kind == LocalRoleRuleKind::InverseProperties)
                );
                assert_eq!(statistics.object_property_chains, 0);
                assert_eq!(statistics.role_expansion_edges, usize::from(projects_roles));
                assert_eq!(statistics.skipped_axioms, 0);
                assert_eq!(statistics.edges, if projects_roles { 3 } else { 1 });
                assert_eq!(prepared.emission_attempts(), 0);
                assert!(prepared.preparation.overlay_deltas.is_empty());

                let mut edges = Vec::new();
                while prepared.remaining_edges() != 0 {
                    let (batch, cursor) = prepared
                        .prepare_next_batch(base.columns(), &running_state(), 1)
                        .unwrap();
                    assert_eq!(batch.len(), 1);
                    edges.extend(batch);
                    prepared.commit_cursor(cursor);
                }
                let mut expected = vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:Top".into(),
                }];
                if projects_roles {
                    expected.push(DirectEdge {
                        source: "urn:C".into(),
                        relation: "urn:super".into(),
                        destination: "urn:D".into(),
                    });
                    expected.push(if kind == LocalRoleRuleKind::SimpleSubProperty {
                        DirectEdge {
                            source: "urn:C".into(),
                            relation: "urn:p".into(),
                            destination: "urn:D".into(),
                        }
                    } else {
                        DirectEdge {
                            source: "urn:D".into(),
                            relation: "urn:p".into(),
                            destination: "urn:C".into(),
                        }
                    });
                }
                assert_eq!(edges, expected);
                assert!(prepared.is_exhausted());
            }

            let excluded_restriction = 2_u32.to_le_bytes();
            let selected_taxonomy = base.columns().with_excluded_root_ids(&excluded_restriction);
            let mut selected = prepare_single_overlay_delta_batches_uncommitted(
                selected_taxonomy,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(selected.statistics().roots, 2);
            assert_eq!(selected.statistics().subclasses, 1);
            assert_eq!(selected.statistics().restriction_subclasses, 0);
            assert_eq!(selected.statistics().role_expansion_edges, 0);
            assert_eq!(selected.statistics().edges, 1);
            let (edges, cursor) = selected
                .prepare_next_batch(selected_taxonomy, &running_state(), 1)
                .unwrap();
            selected.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:Top".into(),
                }]
            );
            assert!(selected.is_exhausted());

            let duplicate_base = local_role_delta_fixture(root_tag, 0, false, false);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    duplicate_base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("duplicates")
            ));

            let annotated = local_role_delta_fixture(root_tag, 0, false, true);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    annotated.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
            ));

            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    DirectCompileOptions {
                        max_edges: 2,
                        ..options
                    },
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Resource(message))
                    if message.contains("requires 3 edges")
            ));
        }
    }

    #[test]
    fn one_root_overlay_delta_accepts_state_neutral_annotation_roots() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for kind in [
            LocalAnnotationRuleKind::OntologyAnnotation,
            LocalAnnotationRuleKind::Assertion,
        ] {
            for literal_value in [false, true] {
                let delta = local_annotation_delta_fixture(kind, literal_value, false, false);
                let counts = delta
                    .columns()
                    .classify_roots(options.max_iri_bytes, &running_state())
                    .unwrap();
                let root_kind = delta.columns().root_kind(0).unwrap();
                let root = delta.columns().root_id(0).unwrap();
                let tag = delta.columns().node_tag(root).unwrap();
                let plan = LocalAnnotationRulePlan::classify(
                    counts,
                    root_kind,
                    tag,
                    LocalRuleContext::new(options, false),
                )
                .unwrap();
                assert_eq!(plan.rule.kind, kind);

                for variant in [
                    options,
                    DirectCompileOptions {
                        only_taxonomy: true,
                        ..options
                    },
                    DirectCompileOptions {
                        asserted_taxonomy_only: true,
                        ..options
                    },
                ] {
                    let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                        base.columns(),
                        delta.columns(),
                        variant,
                        &running_state(),
                        None,
                        canonical_limits().max_work,
                        canonical_limits().max_workspace_bytes,
                    )
                    .unwrap_or_else(|error| {
                        panic!("local annotation kind={kind:?} literal={literal_value}: {error:?}")
                    });
                    let statistics = prepared.statistics();
                    assert_eq!(statistics.roots, 3);
                    assert_eq!(
                        statistics.ontology_annotations,
                        usize::from(kind == LocalAnnotationRuleKind::OntologyAnnotation)
                    );
                    assert_eq!(
                        statistics.annotation_assertions,
                        usize::from(kind == LocalAnnotationRuleKind::Assertion)
                    );
                    assert_eq!(
                        statistics.selected_annotation_assertions,
                        usize::from(kind == LocalAnnotationRuleKind::Assertion)
                    );
                    assert_eq!(statistics.annotation_edges, 0);
                    assert_eq!(statistics.non_string_literal_renderings, 0);
                    assert_eq!(statistics.skipped_axioms, 0);
                    assert_eq!(statistics.edges, 1);
                    assert_eq!(prepared.emission_attempts(), 0);
                    assert!(prepared.preparation.overlay_deltas.is_empty());
                    let (edges, cursor) = prepared
                        .prepare_next_batch(base.columns(), &running_state(), 1)
                        .unwrap();
                    prepared.commit_cursor(cursor);
                    assert_eq!(
                        edges,
                        vec![DirectEdge {
                            source: "urn:A".into(),
                            relation: SUBCLASS_OF.into(),
                            destination: "urn:B".into(),
                        }]
                    );
                    assert!(prepared.is_exhausted());
                }
            }

            let delta = local_annotation_delta_fixture(kind, false, false, false);
            let excluded_subclass = 2_u32.to_le_bytes();
            let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
            let silent = prepare_single_overlay_delta_batches_uncommitted(
                selected_declaration,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(silent.statistics().roots, 2);
            assert_eq!(silent.statistics().edges, 0);
            assert_eq!(silent.statistics().skipped_axioms, 0);
            assert_eq!(silent.emission_attempts(), 0);
            assert!(silent.preparation.overlay_deltas.is_empty());
            assert!(silent.is_exhausted());

            let duplicate_base = local_annotation_delta_fixture(kind, false, false, false);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    duplicate_base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("duplicates")
            ));

            let annotated = local_annotation_delta_fixture(kind, false, true, false);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    annotated.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message))
                    if message.contains(
                        if kind == LocalAnnotationRuleKind::OntologyAnnotation {
                            "must have no nested annotations"
                        } else {
                            "must be unannotated"
                        }
                    )
            ));

            let anonymous = local_annotation_delta_fixture(kind, false, false, true);
            let anonymous_result = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                anonymous.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            );
            assert!(
                matches!(
                    &anonymous_result,
                    Err(KernelError::Unsupported(message))
                        if message.contains("no anonymous individuals or local scope remap")
                ),
                "{anonymous_result:?}",
            );
        }
    }

    #[test]
    fn one_root_overlay_delta_rejects_noncanonical_object_property_sets() {
        let base = named_subclass_fixture();
        for tag in [
            TAG_EQUIVALENT_OBJECT_PROPERTIES,
            TAG_DISJOINT_OBJECT_PROPERTIES,
        ] {
            for duplicate in [false, true] {
                let mut delta =
                    silent_object_property_delta_fixture(tag, &[b"urn:op", b"urn:oq"], 0, false);
                let (first_item, second_item) = {
                    let columns = delta.columns();
                    let root = columns.root_id(0).unwrap();
                    let fields = columns.exact_fields(root, 2).unwrap();
                    let (items, length) = columns.node_set_range(fields, 0).unwrap();
                    assert_eq!(length, 2);
                    (items, items + 1)
                };
                if duplicate {
                    let first = delta.item_values[first_item * 8..first_item * 8 + 8].to_vec();
                    delta.item_values[second_item * 8..second_item * 8 + 8].copy_from_slice(&first);
                } else {
                    for byte_offset in 0..8 {
                        delta
                            .item_values
                            .swap(first_item * 8 + byte_offset, second_item * 8 + byte_offset);
                    }
                }
                let result = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    DirectCompileOptions {
                        bidirectional: false,
                        asserted_taxonomy_only: false,
                        only_taxonomy: false,
                        include_literals: false,
                        max_edges: 1,
                        max_iri_bytes: 1024,
                    },
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                );
                assert!(
                    matches!(
                        &result,
                        Err(KernelError::Malformed(message))
                        if message.contains("canonical-set items are not sorted and unique")
                    ),
                    "{result:?}",
                );
            }
        }
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_has_keys() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for (object_count, data_count, complex_class, inverse_object) in [
            (1, 0, false, false),
            (0, 1, false, false),
            (1, 1, false, false),
            (1, 1, true, true),
            (2, 2, false, false),
        ] {
            let delta = has_key_delta_fixture(
                object_count,
                data_count,
                complex_class,
                inverse_object,
                false,
            );
            let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(prepared.statistics().roots, 3);
            assert_eq!(prepared.statistics().has_keys, 1);
            assert_eq!(prepared.statistics().skipped_axioms, 1);
            assert_eq!(prepared.statistics().edges, 1);
            assert_eq!(prepared.emission_attempts(), 0);
            let (edges, cursor) = prepared
                .prepare_next_batch(base.columns(), &running_state(), 1)
                .unwrap();
            prepared.commit_cursor(cursor);
            assert_eq!(
                edges,
                vec![DirectEdge {
                    source: "urn:A".into(),
                    relation: SUBCLASS_OF.into(),
                    destination: "urn:B".into(),
                }]
            );
            assert!(prepared.is_exhausted());

            let asserted = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    asserted_taxonomy_only: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(asserted.statistics().has_keys, 1);
            assert_eq!(asserted.statistics().skipped_axioms, 0);
            assert_eq!(asserted.statistics().edges, 1);
            assert_eq!(asserted.emission_attempts(), 0);
        }

        let delta = has_key_delta_fixture(1, 1, false, false, false);
        let excluded_subclass = 2_u32.to_le_bytes();
        let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
        let silent = prepare_single_overlay_delta_batches_uncommitted(
            selected_declaration,
            delta.columns(),
            options,
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(silent.statistics().roots, 2);
        assert_eq!(silent.statistics().has_keys, 1);
        assert_eq!(silent.statistics().skipped_axioms, 1);
        assert_eq!(silent.statistics().edges, 0);
        assert_eq!(silent.emission_attempts(), 0);
        assert!(silent.is_exhausted());

        let duplicate_base = has_key_delta_fixture(1, 1, false, false, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                duplicate_base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));

        let annotated = has_key_delta_fixture(1, 1, false, false, true);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                annotated.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
        ));

        let empty = has_key_delta_fixture(0, 0, false, false, false);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                empty.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Malformed(message)) if message.contains("at least one property")
        ));
    }

    #[test]
    fn one_root_overlay_delta_rejects_noncanonical_has_key_property_sets() {
        let base = named_subclass_fixture();
        for object_set in [false, true] {
            for duplicate in [false, true] {
                let mut delta = has_key_delta_fixture(
                    if object_set { 2 } else { 0 },
                    if object_set { 0 } else { 2 },
                    false,
                    false,
                    false,
                );
                let (first_item, second_item) = {
                    let columns = delta.columns();
                    let root = columns.root_id(0).unwrap();
                    let fields = columns.exact_fields(root, 4).unwrap();
                    let set_field = fields + if object_set { 1 } else { 2 };
                    let (items, length) = columns.node_set_range(set_field, 0).unwrap();
                    assert_eq!(length, 2);
                    (items, items + 1)
                };
                if duplicate {
                    let first = delta.item_values[first_item * 8..first_item * 8 + 8].to_vec();
                    delta.item_values[second_item * 8..second_item * 8 + 8].copy_from_slice(&first);
                } else {
                    for byte_offset in 0..8 {
                        delta
                            .item_values
                            .swap(first_item * 8 + byte_offset, second_item * 8 + byte_offset);
                    }
                }
                let result = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    DirectCompileOptions {
                        bidirectional: false,
                        asserted_taxonomy_only: false,
                        only_taxonomy: false,
                        include_literals: false,
                        max_edges: 1,
                        max_iri_bytes: 1024,
                    },
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                );
                assert!(
                    matches!(
                        &result,
                        Err(KernelError::Malformed(message))
                        if message.contains("canonical-set items are not sorted and unique")
                    ),
                    "{result:?}",
                );
            }
        }
    }

    #[test]
    fn one_root_overlay_delta_accepts_silent_individual_sets() {
        let base = named_subclass_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        for tag in [TAG_SAME_INDIVIDUAL, TAG_DIFFERENT_INDIVIDUALS] {
            for individual_iris in [
                &[b"urn:i".as_slice(), b"urn:j".as_slice()][..],
                &[
                    b"urn:i".as_slice(),
                    b"urn:j".as_slice(),
                    b"urn:k".as_slice(),
                ][..],
            ] {
                let delta = individual_set_delta_fixture(tag, individual_iris, false, false);
                let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                )
                .unwrap();
                assert_eq!(prepared.statistics().roots, 3);
                assert_eq!(
                    (
                        prepared.statistics().same_individuals,
                        prepared.statistics().different_individuals,
                    ),
                    if tag == TAG_SAME_INDIVIDUAL {
                        (1, 0)
                    } else {
                        (0, 1)
                    },
                );
                assert_eq!(prepared.statistics().skipped_axioms, 1);
                assert_eq!(prepared.statistics().edges, 1);
                assert_eq!(prepared.emission_attempts(), 0);
                let (edges, cursor) = prepared
                    .prepare_next_batch(base.columns(), &running_state(), 1)
                    .unwrap();
                prepared.commit_cursor(cursor);
                assert_eq!(
                    edges,
                    vec![DirectEdge {
                        source: "urn:A".into(),
                        relation: SUBCLASS_OF.into(),
                        destination: "urn:B".into(),
                    }]
                );
                assert!(prepared.is_exhausted());

                let asserted = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    DirectCompileOptions {
                        asserted_taxonomy_only: true,
                        ..options
                    },
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                )
                .unwrap();
                assert_eq!(
                    (
                        asserted.statistics().same_individuals,
                        asserted.statistics().different_individuals,
                    ),
                    if tag == TAG_SAME_INDIVIDUAL {
                        (1, 0)
                    } else {
                        (0, 1)
                    },
                );
                assert_eq!(asserted.statistics().skipped_axioms, 0);
                assert_eq!(asserted.statistics().edges, 1);
                assert_eq!(asserted.emission_attempts(), 0);
            }
        }

        for tag in [TAG_SAME_INDIVIDUAL, TAG_DIFFERENT_INDIVIDUALS] {
            let delta = individual_set_delta_fixture(tag, &[b"urn:i", b"urn:j"], false, false);
            let excluded_subclass = 2_u32.to_le_bytes();
            let selected_declaration = base.columns().with_excluded_root_ids(&excluded_subclass);
            let silent = prepare_single_overlay_delta_batches_uncommitted(
                selected_declaration,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            )
            .unwrap();
            assert_eq!(silent.statistics().roots, 2);
            assert_eq!(
                (
                    silent.statistics().same_individuals,
                    silent.statistics().different_individuals,
                ),
                if tag == TAG_SAME_INDIVIDUAL {
                    (1, 0)
                } else {
                    (0, 1)
                },
            );
            assert_eq!(silent.statistics().skipped_axioms, 1);
            assert_eq!(silent.statistics().edges, 0);
            assert_eq!(silent.emission_attempts(), 0);
            assert!(silent.is_exhausted());

            let duplicate_base =
                individual_set_delta_fixture(tag, &[b"urn:i", b"urn:j"], false, false);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    duplicate_base.columns(),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("duplicates")
            ));

            let annotated = individual_set_delta_fixture(tag, &[b"urn:i", b"urn:j"], false, true);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    annotated.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("must be unannotated")
            ));

            let anonymous = individual_set_delta_fixture(tag, &[b"urn:i", b"urn:j"], true, false);
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    anonymous.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Unsupported(message)) if message.contains("requires named individuals")
            ));

            let oversized = individual_set_delta_fixture(
                tag,
                &[b"urn:i", b"urn:j", b"urn:k", b"urn:l"],
                false,
                false,
            );
            let oversized_result = prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                oversized.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            );
            assert!(
                matches!(
                    &oversized_result,
                    Err(KernelError::Unsupported(message))
                        if message.contains("canonical binary or ternary")
                ),
                "{oversized_result:?}",
            );
        }
    }

    #[test]
    fn one_root_overlay_delta_rejects_noncanonical_individual_sets() {
        let base = named_subclass_fixture();
        for tag in [TAG_SAME_INDIVIDUAL, TAG_DIFFERENT_INDIVIDUALS] {
            for duplicate in [false, true] {
                let mut delta = individual_set_delta_fixture(
                    tag,
                    &[b"urn:i", b"urn:j", b"urn:k"],
                    false,
                    false,
                );
                let (first_item, second_item) = {
                    let columns = delta.columns();
                    let root = columns.root_id(0).unwrap();
                    let fields = columns.exact_fields(root, 2).unwrap();
                    let (items, length) = columns.node_set_range(fields, 2).unwrap();
                    assert_eq!(length, 3);
                    (items, items + 1)
                };
                if duplicate {
                    let first = delta.item_values[first_item * 8..first_item * 8 + 8].to_vec();
                    delta.item_values[second_item * 8..second_item * 8 + 8].copy_from_slice(&first);
                } else {
                    for byte_offset in 0..8 {
                        delta
                            .item_values
                            .swap(first_item * 8 + byte_offset, second_item * 8 + byte_offset);
                    }
                }
                let result = prepare_single_overlay_delta_batches_uncommitted(
                    base.columns(),
                    delta.columns(),
                    DirectCompileOptions {
                        bidirectional: false,
                        asserted_taxonomy_only: false,
                        only_taxonomy: false,
                        include_literals: false,
                        max_edges: 1,
                        max_iri_bytes: 1024,
                    },
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                );
                assert!(
                    matches!(
                        &result,
                        Err(KernelError::Malformed(message))
                        if message.contains("canonical-set items are not sorted and unique")
                    ),
                    "{result:?}",
                );
            }
        }
    }

    #[test]
    fn one_root_overlay_delta_composes_base_exclusions_transactionally() {
        let base = named_subclass_fixture();
        let delta = named_subclass_delta_fixture(b"urn:B", b"urn:C");
        let excluded_declaration = 1_u32.to_le_bytes();
        let base_columns = base.columns().with_excluded_root_ids(&excluded_declaration);
        let state = running_state();
        let mut prepared = prepare_single_overlay_delta_batches_uncommitted(
            base_columns,
            delta.columns(),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: false,
                max_edges: 2,
                max_iri_bytes: 1024,
            },
            &state,
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        let stats = prepared.statistics();
        assert_eq!(stats.roots, 2);
        assert_eq!(stats.declarations, 0);
        assert_eq!(stats.subclasses, 2);
        assert_eq!(stats.edges, 2);
        assert_eq!(prepared.emission_attempts(), 0);

        let (first, first_cursor) = prepared
            .prepare_next_batch(base_columns, &state, 1)
            .unwrap();
        let (retry, _) = prepared
            .prepare_next_batch(base_columns, &state, 1)
            .unwrap();
        assert_eq!(first, retry);
        assert_eq!(
            first,
            vec![DirectEdge {
                source: "urn:A".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:B".into(),
            }]
        );
        assert_eq!(prepared.remaining_edges(), 2);
        prepared.commit_cursor(first_cursor);
        assert_eq!(prepared.remaining_edges(), 1);

        let (second, second_cursor) = prepared
            .prepare_next_batch(base_columns, &state, 1)
            .unwrap();
        assert_eq!(
            second,
            vec![DirectEdge {
                source: "urn:B".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:C".into(),
            }]
        );
        prepared.commit_cursor(second_cursor);
        assert!(prepared.is_exhausted());

        let delta_before_base = named_subclass_delta_fixture(b"urn:0", b"urn:B");
        let excluded_base_subclass = 2_u32.to_le_bytes();
        let excluded_after_local = base
            .columns()
            .with_excluded_root_ids(&excluded_base_subclass);
        let mut local_only = prepare_single_overlay_delta_batches_uncommitted(
            excluded_after_local,
            delta_before_base.columns(),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: false,
                max_edges: 1,
                max_iri_bytes: 1024,
            },
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(local_only.statistics().roots, 2);
        assert_eq!(local_only.statistics().declarations, 1);
        assert_eq!(local_only.statistics().subclasses, 1);
        let (local_edge, local_cursor) = local_only
            .prepare_next_batch(excluded_after_local, &running_state(), 1)
            .unwrap();
        assert_eq!(local_edge[0].source, "urn:0");
        local_only.commit_cursor(local_cursor);
        assert!(local_only.is_exhausted());

        let excluded_all = [1_u32.to_le_bytes(), 2_u32.to_le_bytes()].concat();
        let no_base_roots = base.columns().with_excluded_root_ids(&excluded_all);
        let all_removed = prepare_single_overlay_delta_batches_uncommitted(
            no_base_roots,
            delta.columns(),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: false,
                max_edges: 1,
                max_iri_bytes: 1024,
            },
            &running_state(),
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        assert_eq!(all_removed.statistics().roots, 1);
        assert_eq!(all_removed.statistics().declarations, 0);
        assert_eq!(all_removed.statistics().subclasses, 1);
        assert_eq!(all_removed.statistics().edges, 1);
    }

    #[test]
    fn one_root_overlay_delta_exclusion_failures_are_bounded_and_preoutput() {
        let base = named_subclass_fixture();
        let delta = named_subclass_delta_fixture(b"urn:B", b"urn:C");
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 2,
            max_iri_bytes: 1024,
        };
        let invalid_postings = [
            b"\x01\x00".to_vec(),
            0_u32.to_le_bytes().to_vec(),
            3_u32.to_le_bytes().to_vec(),
            [1_u32.to_le_bytes(), 1_u32.to_le_bytes()].concat(),
            [2_u32.to_le_bytes(), 1_u32.to_le_bytes()].concat(),
        ];
        for postings in &invalid_postings {
            assert!(matches!(
                prepare_single_overlay_delta_batches_uncommitted(
                    base.columns().with_excluded_root_ids(postings),
                    delta.columns(),
                    options,
                    &running_state(),
                    None,
                    canonical_limits().max_work,
                    canonical_limits().max_workspace_bytes,
                ),
                Err(KernelError::Malformed(message)) if message.contains("excluded")
            ));
        }

        let excluded_declaration = 1_u32.to_le_bytes();
        let selected_base = base.columns().with_excluded_root_ids(&excluded_declaration);
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                selected_base,
                delta.columns().with_excluded_root_ids(&1_u32.to_le_bytes()),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message))
                if message.contains("delta requires ALL root selection")
        ));
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                selected_base,
                delta.columns(),
                options,
                &running_state(),
                None,
                1,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Resource(message)) if message.contains("work units")
        ));
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                selected_base,
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                1,
            ),
            Err(KernelError::Resource(message)) if message.contains("workspace bytes")
        ));
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                selected_base,
                delta.columns(),
                DirectCompileOptions {
                    max_edges: 1,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Resource(message)) if message.contains("configured limit")
        ));
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                selected_base,
                delta.columns(),
                options,
                &AtomicU8::new(STATE_CANCELLED),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Cancelled)
        ));
    }

    #[test]
    fn one_root_overlay_delta_rejects_adjacent_shapes_and_resource_exhaustion() {
        let base = named_subclass_fixture();
        let delta = named_subclass_delta_fixture(b"urn:B", b"urn:C");
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 2,
            max_iri_bytes: 1024,
        };
        let multiple_roots = named_subclass_fixture();
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                multiple_roots.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message))
                if message.contains(LOCAL_EMITTING_OVERLAY_REQUIREMENT)
        ));
        let duplicate = named_subclass_delta_fixture(b"urn:A", b"urn:B");
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                duplicate.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message)) if message.contains("duplicates")
        ));
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                DirectCompileOptions {
                    include_literals: true,
                    ..options
                },
                &running_state(),
                None,
                canonical_limits().max_work,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Unsupported(message))
                if message.contains("literal projection")
        ));
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                1,
                canonical_limits().max_workspace_bytes,
            ),
            Err(KernelError::Resource(message)) if message.contains("work units")
        ));
        assert!(matches!(
            prepare_single_overlay_delta_batches_uncommitted(
                base.columns(),
                delta.columns(),
                options,
                &running_state(),
                None,
                canonical_limits().max_work,
                1,
            ),
            Err(KernelError::Resource(message)) if message.contains("workspace bytes")
        ));
    }

    #[test]
    fn two_member_composite_deduplicates_one_structurally_identical_root() {
        let left = named_subclass_fixture();
        let right = named_subclass_delta_fixture(b"urn:A", b"urn:B");
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 1,
            max_iri_bytes: 1024,
        };
        let state = running_state();
        let mut prepared = prepare_two_member_composite_batches_uncommitted(
            left.columns(),
            right.columns(),
            options,
            &state,
            None,
            canonical_limits().max_work,
            canonical_limits().max_workspace_bytes,
        )
        .unwrap();
        let statistics = prepared.statistics();
        assert_eq!(statistics.roots, 2);
        assert_eq!(statistics.declarations, 1);
        assert_eq!(statistics.subclasses, 1);
        assert_eq!(statistics.edges, 1);
        assert_eq!(
            statistics.buffer_bytes,
            left.columns().buffer_bytes().unwrap() + right.columns().buffer_bytes().unwrap()
        );
        assert!(prepared.preparation.overlay_deltas.is_empty());

        let (first, first_cursor) = prepared
            .prepare_next_batch(left.columns(), &state, 1)
            .unwrap();
        let (retry, _) = prepared
            .prepare_next_batch(left.columns(), &state, 1)
            .unwrap();
        assert_eq!(first, retry);
        assert_eq!(
            first,
            vec![DirectEdge {
                source: "urn:A".into(),
                relation: SUBCLASS_OF.into(),
                destination: "urn:B".into(),
            }]
        );
        prepared.commit_cursor(first_cursor);
        assert!(prepared.is_exhausted());
    }

    #[test]
    fn excluded_root_postings_filter_counts_and_emission_without_flattening() {
        let fixture = named_subclass_fixture();
        let excluded_declaration = 1_u32.to_le_bytes();
        let (edges, stats) = compile_direct(
            fixture
                .columns()
                .with_excluded_root_ids(&excluded_declaration),
            false,
            false,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(edges.len(), 1);
        assert_eq!(stats.roots, 1);
        assert_eq!(stats.declarations, 0);
        assert_eq!(stats.subclasses, 1);

        let excluded_subclass = 2_u32.to_le_bytes();
        let (edges, stats) = compile_direct(
            fixture.columns().with_excluded_root_ids(&excluded_subclass),
            false,
            false,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(edges.is_empty());
        assert_eq!(stats.roots, 1);
        assert_eq!(stats.declarations, 1);
        assert_eq!(stats.subclasses, 0);
    }

    #[test]
    fn included_root_postings_filter_counts_and_emission_without_indexing() {
        let fixture = named_subclass_fixture();
        let included_subclass = 2_u32.to_le_bytes();
        let (edges, stats) = compile_direct(
            fixture.columns().with_included_root_ids(&included_subclass),
            false,
            false,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert_eq!(edges.len(), 1);
        assert_eq!(stats.roots, 1);
        assert_eq!(stats.declarations, 0);
        assert_eq!(stats.subclasses, 1);

        let included_declaration = 1_u32.to_le_bytes();
        let (edges, stats) = compile_direct(
            fixture
                .columns()
                .with_included_root_ids(&included_declaration),
            false,
            false,
            false,
            1,
            1024,
            &running_state(),
        )
        .unwrap();
        assert!(edges.is_empty());
        assert_eq!(stats.roots, 1);
        assert_eq!(stats.declarations, 1);
        assert_eq!(stats.subclasses, 0);
    }

    #[test]
    fn excluded_root_postings_must_be_complete_sorted_unique_in_range_rows() {
        let fixture = named_subclass_fixture();
        for postings in [
            vec![1_u8, 0],
            0_u32.to_le_bytes().to_vec(),
            3_u32.to_le_bytes().to_vec(),
            [1_u32.to_le_bytes(), 1_u32.to_le_bytes()].concat(),
            [2_u32.to_le_bytes(), 1_u32.to_le_bytes()].concat(),
        ] {
            assert!(matches!(
                compile_direct(
                    fixture.columns().with_excluded_root_ids(&postings),
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
    }

    #[test]
    fn included_root_postings_must_be_complete_sorted_unique_in_range_rows() {
        let fixture = named_subclass_fixture();
        for postings in [
            vec![1_u8, 0],
            0_u32.to_le_bytes().to_vec(),
            3_u32.to_le_bytes().to_vec(),
            [1_u32.to_le_bytes(), 1_u32.to_le_bytes()].concat(),
            [2_u32.to_le_bytes(), 1_u32.to_le_bytes()].concat(),
        ] {
            assert!(matches!(
                compile_direct(
                    fixture.columns().with_included_root_ids(&postings),
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
        let included = 1_u32.to_le_bytes();
        let excluded = 2_u32.to_le_bytes();
        assert!(matches!(
            compile_direct(
                fixture
                    .columns()
                    .with_included_root_ids(&included)
                    .with_excluded_root_ids(&excluded),
                false,
                false,
                false,
                1,
                1024,
                &running_state(),
            ),
            Err(KernelError::Malformed(message))
                if message.contains("cannot combine INCLUDE and EXCLUDE")
        ));
    }

    #[test]
    fn excluded_roots_still_receive_complete_source_validation() {
        let mut fixture = named_subclass_fixture();
        fixture.node_tags[10..12].copy_from_slice(&u16::MAX.to_le_bytes());
        let excluded_subclass = 2_u32.to_le_bytes();
        assert!(matches!(
            compile_direct(
                fixture.columns().with_excluded_root_ids(&excluded_subclass),
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
    fn resumable_output_cursor_starts_lazy_and_never_buffers_more_than_the_caller_batch() {
        let fixture = named_role_axiom_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 6,
            max_iri_bytes: 1024,
        };
        let state = running_state();
        let (expected, _) =
            compile_direct_with_retained_role_state(fixture.columns(), None, options, &state, None)
                .unwrap();
        let mut prepared = prepare_direct_batches_with_retained_role_state(
            fixture.columns(),
            None,
            options,
            &state,
            None,
        )
        .unwrap();
        assert_eq!(prepared.remaining_edges(), expected.len());
        assert_eq!(prepared.emission_attempts(), 0);

        let remaining = prepared.remaining_edges();
        let (preview, _) = prepared
            .prepare_next_batch(fixture.columns(), &state, 2)
            .unwrap();
        assert_eq!(prepared.remaining_edges(), remaining);
        assert_eq!(prepared.emission_attempts(), preview.len());
        let (first, cursor) = prepared
            .prepare_next_batch(fixture.columns(), &state, 2)
            .unwrap();
        assert_eq!(first, preview);
        prepared.commit_cursor(cursor);

        let mut actual = first;
        while !prepared.is_exhausted() {
            let (batch, cursor) = prepared
                .prepare_next_batch(fixture.columns(), &state, 2)
                .unwrap();
            assert!(!batch.is_empty());
            assert!(batch.len() <= 2);
            actual.extend(batch);
            prepared.commit_cursor(cursor);
        }
        assert_eq!(prepared.remaining_edges(), 0);
        assert_eq!(prepared.emission_attempts(), expected.len() + preview.len());
        assert_eq!(actual, expected);
    }

    #[test]
    fn uncommitted_cursor_defers_retained_roles_until_complete_drain() {
        let fixture = named_role_axiom_fixture();
        let options = DirectCompileOptions {
            bidirectional: false,
            asserted_taxonomy_only: false,
            only_taxonomy: false,
            include_literals: false,
            max_edges: 6,
            max_iri_bytes: 1024,
        };
        let state = running_state();
        let mut retained = OwnedRoleState::default();
        let mut prepared = prepare_direct_batches_uncommitted(
            fixture.columns(),
            None,
            options,
            &state,
            Some(&retained),
        )
        .unwrap();
        assert_eq!(retained.subrole_count(), 0);
        assert_eq!(retained.inverse_count(), 0);

        while !prepared.is_exhausted() {
            let (batch, cursor) = prepared
                .prepare_next_batch(fixture.columns(), &state, 2)
                .unwrap();
            assert!(!batch.is_empty());
            assert!(batch.len() <= 2);
            prepared.commit_cursor(cursor);
            assert_eq!(retained.subrole_count(), 0);
            assert_eq!(retained.inverse_count(), 0);
        }

        retained = prepared.try_clone_role_state().unwrap();
        assert_eq!(retained.subrole_count(), 1);
        assert_eq!(retained.inverse_count(), 2);
    }

    #[test]
    fn retained_role_state_commits_only_after_successful_output_preparation() {
        let role_fixture = named_role_axiom_fixture();
        let mut retained = OwnedRoleState::default();
        let result = compile_direct_with_retained_role_state(
            role_fixture.columns(),
            None,
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
            None,
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
            None,
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
            None,
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
        assert_eq!(stats.ignored_object_property_domains, 1);
        assert_eq!(stats.ignored_object_property_ranges, 1);
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
        assert_eq!(stats.ignored_object_property_domains, 1);
        assert_eq!(stats.ignored_object_property_ranges, 1);
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
        assert_eq!(stats.equivalent_base_edges, 2);
        assert_eq!(stats.ignored_equivalents, 0);
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
        assert_eq!(stats.equivalent_base_edges, 1);
        assert_eq!(stats.ignored_equivalents, 0);
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
        assert_eq!(stats.equivalent_base_edges, 0);
        assert_eq!(stats.ignored_equivalents, 0);
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
        assert_eq!(stats.equivalent_base_edges, 1);
        assert_eq!(stats.ignored_equivalents, 3);
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
        assert_eq!(stats.equivalent_base_edges, 0);
        assert_eq!(stats.ignored_equivalents, 0);
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
    fn root_annotation_join_selects_independent_table_identity_before_limits() {
        let closure = named_annotation_fixture();
        let root = root_duplicate_annotation_fixture();
        let (edges, stats) = compile_direct_with_retained_role_state(
            closure.columns(),
            Some(root.columns()),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: true,
                max_edges: 1,
                max_iri_bytes: 1024,
            },
            &running_state(),
            None,
        )
        .unwrap();
        assert_eq!(
            edges,
            vec![DirectEdge {
                source: "urn:A".into(),
                relation: "rdfs:label".into(),
                destination: "duplicate".into(),
            }]
        );
        assert_eq!(stats.annotation_assertions, 4);
        assert_eq!(stats.selected_annotation_assertions, 1);
        assert_eq!(stats.annotation_edges, 1);
        assert_eq!(stats.non_string_literal_renderings, 0);
        assert_eq!(
            stats.buffer_bytes,
            closure.columns().buffer_bytes().unwrap()
        );
        assert_eq!(
            stats.root_provenance_buffer_bytes,
            root.columns().buffer_bytes().unwrap()
        );
    }

    #[test]
    fn root_annotation_join_renders_closure_anonymous_identifier_space() {
        let closure = anonymous_annotation_closure_fixture();
        let root = root_anonymous_annotation_fixture();
        let (edges, stats) = compile_direct_with_retained_role_state(
            closure.columns(),
            Some(root.columns()),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: true,
                max_edges: 1,
                max_iri_bytes: 1024,
            },
            &running_state(),
            None,
        )
        .unwrap();
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].destination, "_:genid2147483649");
        assert_eq!(stats.anonymous_individuals, 2);
        assert_eq!(stats.annotation_assertions, 6);
        assert_eq!(stats.selected_annotation_assertions, 1);
    }

    #[test]
    fn root_annotation_join_rejects_nonclosure_identity_before_output() {
        let closure = named_annotation_fixture();
        let mut root = root_duplicate_annotation_fixture();
        let offset = root
            .scalar_bytes
            .windows(b"duplicate".len())
            .position(|value| value == b"duplicate")
            .expect("root annotation literal");
        root.scalar_bytes[offset] = b'x';
        let result = compile_direct_with_retained_role_state(
            closure.columns(),
            Some(root.columns()),
            DirectCompileOptions {
                bidirectional: false,
                asserted_taxonomy_only: false,
                only_taxonomy: false,
                include_literals: true,
                max_edges: 4,
                max_iri_bytes: 1024,
            },
            &running_state(),
            None,
        );
        assert!(
            matches!(result, Err(KernelError::Malformed(message)) if message.contains(
                "root annotation assertion is absent"
            ))
        );
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
    fn deep_annotation_metadata_graph_is_validated_iteratively() {
        let fixture = deep_annotation_metadata_fixture(4096, false);
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
        assert_eq!(stats.roots, 0);
    }

    #[test]
    fn cyclic_annotation_metadata_graphs_fail_before_output() {
        for depth in [1, 2] {
            let fixture = deep_annotation_metadata_fixture(depth, true);
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
                Err(KernelError::Malformed(message))
                    if message.contains("annotation metadata graph is cyclic")
            ));
        }
    }

    #[test]
    fn cyclic_root_annotation_metadata_fails_before_provenance_join() {
        let closure = annotation_metadata_root_fixture();
        let mut root = annotation_metadata_root_fixture();
        let item_start = {
            let columns = root.columns();
            let start = columns.exact_fields(16, 3).unwrap();
            let (item_start, length) = columns.node_set_range(start + 2, 0).unwrap();
            assert_eq!(length, 1);
            item_start
        };
        root.item_values[item_start * 8..(item_start + 1) * 8]
            .copy_from_slice(&16_u64.to_le_bytes());

        assert!(matches!(
            compile_direct_with_retained_role_state(
                closure.columns(),
                Some(root.columns()),
                DirectCompileOptions {
                    bidirectional: false,
                    asserted_taxonomy_only: false,
                    only_taxonomy: false,
                    include_literals: true,
                    max_edges: 1,
                    max_iri_bytes: 1024,
                },
                &running_state(),
                None,
            ),
            Err(KernelError::Malformed(message))
                if message.contains("annotation metadata graph is cyclic")
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
    fn canonical_comparator_streams_every_component_across_independent_tables() {
        let (left, left_root) = canonical_component_fixture(None, b"\x80\x01\xff\x01", b"tail");
        let (right, right_root) =
            canonical_component_fixture(Some(b"unrelated-prefix"), b"\x80\x01\xff\x01", b"tail");
        let mut comparator = canonical_merge::CanonicalNodeComparator::new(
            left.columns(),
            right.columns(),
            canonical_limits(),
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            comparator
                .compare(left_root, right_root, &running_state())
                .unwrap(),
            std::cmp::Ordering::Equal
        );
        let report = comparator.report();
        assert!(report.work > 0);
        assert!(report.workspace_bytes > 0);
        assert!(report.peak_workspace_bytes >= report.workspace_bytes);
        assert!(report.canonical_bytes_compared > 0);

        let (lower, lower_root) = canonical_component_fixture(None, b"\x7f", b"tail");
        let (higher, higher_root) = canonical_component_fixture(None, b"\x80", b"tail");
        let mut comparator = canonical_merge::CanonicalNodeComparator::new(
            lower.columns(),
            higher.columns(),
            canonical_limits(),
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            comparator
                .compare(lower_root, higher_root, &running_state())
                .unwrap(),
            std::cmp::Ordering::Less
        );
    }

    #[test]
    fn canonical_root_cursor_merges_orders_exclusions_and_structural_duplicates() {
        use canonical_merge::MergedCanonicalRoot::{Both, Left, Right};

        let left = canonical_text_roots(&[b"a", b"c"]);
        let right = canonical_text_roots(&[b"b", b"c"]);
        let mut merger = canonical_merge::CanonicalRootMerger::new(
            left.columns(),
            right.columns(),
            canonical_limits(),
            &running_state(),
        )
        .unwrap();
        let unselected_preflight_work = merger.report().work;
        assert!(matches!(
            merger.next(&running_state()).unwrap(),
            Some(Left(root)) if root.index == 0
        ));
        assert!(matches!(
            merger.next(&running_state()).unwrap(),
            Some(Right(root)) if root.index == 0
        ));
        assert!(matches!(
            merger.next(&running_state()).unwrap(),
            Some(Both { left, right }) if left.index == 1 && right.index == 1
        ));
        assert_eq!(merger.next(&running_state()).unwrap(), None);
        let report = merger.report();
        assert_eq!(report.roots_emitted, 3);
        assert_eq!(report.deduplicated_roots, 1);
        assert!(report.canonical_bytes_compared > 0);

        let excluded = 1_u32.to_le_bytes();
        let mut selected = canonical_merge::CanonicalRootMerger::new(
            left.columns().with_excluded_root_ids(&excluded),
            right.columns(),
            canonical_limits(),
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            selected.report().work,
            unselected_preflight_work + excluded.len() / 4
        );
        assert!(matches!(
            selected.next(&running_state()).unwrap(),
            Some(Right(root)) if root.index == 0
        ));
        assert!(matches!(
            selected.next(&running_state()).unwrap(),
            Some(Both { left, right }) if left.index == 1 && right.index == 1
        ));
        assert_eq!(selected.next(&running_state()).unwrap(), None);
        assert_eq!(selected.report().roots_emitted, 2);
        assert_eq!(selected.report().deduplicated_roots, 1);

        let included = 2_u32.to_le_bytes();
        let mut included_left = canonical_merge::CanonicalRootMerger::new(
            left.columns().with_included_root_ids(&included),
            right.columns(),
            canonical_limits(),
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            included_left.report().work,
            unselected_preflight_work + included.len() / 4
        );
        assert!(matches!(
            included_left.next(&running_state()).unwrap(),
            Some(Right(root)) if root.index == 0
        ));
        assert!(matches!(
            included_left.next(&running_state()).unwrap(),
            Some(Both { left, right }) if left.index == 1 && right.index == 1
        ));
        assert_eq!(included_left.next(&running_state()).unwrap(), None);
        assert_eq!(included_left.report().roots_emitted, 2);
        assert_eq!(included_left.report().deduplicated_roots, 1);
    }

    #[test]
    fn canonical_root_cursor_rejects_noncanonical_order_and_local_duplicates() {
        let valid = canonical_text_roots(&[b"x"]);
        for hostile in [
            canonical_text_roots(&[b"z", b"a"]),
            canonical_text_roots(&[b"a", b"a"]),
        ] {
            assert!(matches!(
                canonical_merge::CanonicalRootMerger::new(
                    hostile.columns(),
                    valid.columns(),
                    canonical_limits(),
                    &running_state(),
                ),
                Err(KernelError::Malformed(message))
                    if message.contains("root group is not strictly sorted and unique")
            ));
        }
    }

    #[test]
    fn canonical_comparator_rejects_invalid_scalars_and_actual_set_order() {
        let valid = canonical_scalar_root(COMPONENT_TEXT, b"valid");
        for (hostile, expected) in [
            (
                canonical_scalar_root(COMPONENT_TEXT, b"\xff"),
                "text component is not UTF-8",
            ),
            (
                canonical_scalar_root(COMPONENT_ENUM, b""),
                "enum component is not nonempty ASCII",
            ),
            (
                canonical_scalar_root(COMPONENT_ENUM, b"\xff"),
                "enum component is not nonempty ASCII",
            ),
        ] {
            assert!(matches!(
                canonical_merge::CanonicalNodeComparator::new(
                    hostile.columns(),
                    valid.columns(),
                    canonical_limits(),
                    &running_state(),
                ),
                Err(KernelError::Malformed(message)) if message.contains(expected)
            ));
        }

        let hostile = reversed_canonical_set_fixture();
        assert!(matches!(
            canonical_merge::CanonicalNodeComparator::new(
                hostile.columns(),
                valid.columns(),
                canonical_limits(),
                &running_state(),
            ),
            Err(KernelError::Malformed(message))
                if message.contains("set items are not strictly sorted and unique")
        ));
    }

    #[test]
    fn canonical_comparator_handles_deep_graphs_iteratively_and_rejects_cycles() {
        let (left, left_root) = deep_canonical_fixture(4096, false);
        let (right, right_root) = deep_canonical_fixture(4096, false);
        let mut comparator = canonical_merge::CanonicalNodeComparator::new(
            left.columns(),
            right.columns(),
            canonical_limits(),
            &running_state(),
        )
        .unwrap();
        assert_eq!(
            comparator
                .compare(left_root, right_root, &running_state())
                .unwrap(),
            std::cmp::Ordering::Equal
        );
        assert!(comparator.report().canonical_bytes_compared > 4096);

        let (cyclic, _cyclic_root) = deep_canonical_fixture(3, true);
        assert!(matches!(
            canonical_merge::CanonicalNodeComparator::new(
                cyclic.columns(),
                right.columns(),
                canonical_limits(),
                &running_state(),
            ),
            Err(KernelError::Malformed(message))
                if message.contains("cyclic node graph")
        ));
    }

    #[test]
    fn canonical_comparator_fails_closed_on_work_workspace_and_cancellation_bounds() {
        let (left, left_root) = canonical_component_fixture(None, b"\x01", b"tail");
        let (right, right_root) = canonical_component_fixture(None, b"\x01", b"tail");
        assert!(matches!(
            canonical_merge::CanonicalNodeComparator::new(
                left.columns(),
                right.columns(),
                canonical_merge::CanonicalMergeLimits {
                    max_work: 1,
                    max_workspace_bytes: 1024 * 1024,
                },
                &running_state(),
            ),
            Err(KernelError::Resource(message)) if message.contains("work units")
        ));

        let construction_work = canonical_merge::CanonicalNodeComparator::new(
            left.columns(),
            right.columns(),
            canonical_limits(),
            &running_state(),
        )
        .unwrap()
        .report()
        .work;
        let mut work_bounded = canonical_merge::CanonicalNodeComparator::new(
            left.columns(),
            right.columns(),
            canonical_merge::CanonicalMergeLimits {
                max_work: construction_work,
                max_workspace_bytes: 1024 * 1024,
            },
            &running_state(),
        )
        .unwrap();
        assert!(matches!(
            work_bounded.compare(left_root, right_root, &running_state()),
            Err(KernelError::Resource(message)) if message.contains("work units")
        ));

        assert!(matches!(
            canonical_merge::CanonicalNodeComparator::new(
                left.columns(),
                right.columns(),
                canonical_merge::CanonicalMergeLimits {
                    max_work: 1024 * 1024,
                    max_workspace_bytes: 1,
                },
                &running_state(),
            ),
            Err(KernelError::Resource(message)) if message.contains("workspace bytes")
        ));

        let mut cancelled = canonical_merge::CanonicalNodeComparator::new(
            left.columns(),
            right.columns(),
            canonical_limits(),
            &running_state(),
        )
        .unwrap();
        assert!(matches!(
            cancelled.compare(left_root, right_root, &AtomicU8::new(STATE_CANCELLED)),
            Err(KernelError::Cancelled)
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
