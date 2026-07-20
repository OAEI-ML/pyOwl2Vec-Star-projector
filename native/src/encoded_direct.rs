//! No-copy compiler kernel for the private structural-columns v1 slice.
//!
//! This module deliberately contains no Python types.  The PyO3 boundary retains
//! immutable `bytes` exporters and lends their slices here while the GIL is
//! released.  The complete input is validated before the output vector is
//! allocated, so unsupported or malformed inputs cannot expose partial edges.

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
const TAG_ANNOTATION: u16 = 5;
const TAG_OBJECT_SOME_VALUES_FROM: u16 = 34;
const TAG_OBJECT_ALL_VALUES_FROM: u16 = 35;
const TAG_OBJECT_MIN_CARDINALITY: u16 = 38;
const TAG_OBJECT_MAX_CARDINALITY: u16 = 39;
const TAG_DECLARATION: u16 = 60;
const TAG_SUB_CLASS_OF: u16 = 61;
const TAG_EQUIVALENT_CLASSES: u16 = 62;
const TAG_OBJECT_PROPERTY_DOMAIN: u16 = 74;
const TAG_OBJECT_PROPERTY_RANGE: u16 = 75;
const TAG_CLASS_ASSERTION: u16 = 112;
const TAG_SWRL_RULE: u16 = 148;

const SUBCLASS_OF: &str = "http://subclassof";
const SUPERCLASS_OF: &str = "http://superclassof";
const RDF_TYPE: &str = "http://type";

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
    pub(crate) declarations: usize,
    pub(crate) subclasses: usize,
    pub(crate) restriction_subclasses: usize,
    pub(crate) equivalents: usize,
    pub(crate) class_assertions: usize,
    pub(crate) object_property_domains: usize,
    pub(crate) object_property_ranges: usize,
    pub(crate) domain_range_edges: usize,
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
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct RootCounts {
    declarations: usize,
    subclasses: usize,
    restriction_subclasses: usize,
    equivalents: usize,
    class_assertions: usize,
    object_property_domains: usize,
    object_property_ranges: usize,
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

    fn empty_annotation_set(self, index: usize) -> Result<(), KernelError> {
        if self.field_kind(index)? != COMPONENT_SET {
            return Err(KernelError::malformed(
                "encoded annotation field is not a canonical set",
            ));
        }
        if self.field_length(index)? != 0 {
            return Err(KernelError::unsupported(
                "direct native slice does not yet support axiom annotations",
            ));
        }
        Ok(())
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
                    "direct native slice supports only named individuals in ClassAssertion",
                ));
            }
            return Err(KernelError::malformed(
                "encoded ClassAssertion individual has an unknown node tag",
            ));
        }
        let (kind, iri_id) = self.entity(node_id)?;
        if kind != b"named_individual" {
            return Err(KernelError::malformed(
                "encoded ClassAssertion entity is not a named individual",
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

    fn restriction_parts(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(&'a str, &'a str), KernelError> {
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
        let relation = self.named_object_property_iri(self.field_node(property_index)?, maximum)?;
        let destination = self.named_class_iri(self.field_node(filler_index)?, maximum)?;
        Ok((relation, destination))
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
            let (relation, destination) = self.restriction_parts(super_id, maximum)?;
            SubclassProjection::Restriction {
                source: self.named_class_iri(sub_id, maximum)?,
                relation,
                destination,
            }
        } else if is_restriction_tag(sub_tag) && super_tag == TAG_ENTITY {
            let (relation, destination) = self.restriction_parts(sub_id, maximum)?;
            SubclassProjection::Restriction {
                source: self.named_class_iri(super_id, maximum)?,
                relation,
                destination,
            }
        } else {
            return Err(KernelError::unsupported(
                "direct native slice supports only named taxonomy or named-role SubClassOf",
            ));
        };
        self.empty_annotation_set(start + 2)?;
        Ok(projection)
    }

    fn property_class_pair(
        self,
        node_id: usize,
        expected_tag: u16,
        maximum: usize,
    ) -> Result<(&'a str, &'a str), KernelError> {
        if self.node_tag(node_id)? != expected_tag {
            return Err(KernelError::malformed(
                "encoded domain/range cursor has the wrong constructor tag",
            ));
        }
        let start = self.exact_fields(node_id, 3)?;
        let property = self.named_object_property_iri(self.field_node(start)?, maximum)?;
        let class = self.named_class_iri(self.field_node(start + 1)?, maximum)?;
        self.empty_annotation_set(start + 2)?;
        Ok((property, class))
    }

    fn equivalent_pair(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(&'a str, &'a str), KernelError> {
        let start = self.exact_fields(node_id, 2)?;
        let (item_start, length) = self.node_set_range(start, 2)?;
        let mut first: Option<&str> = None;
        let mut second: Option<&str> = None;
        for item_index in item_start..item_start + length {
            let iri = self.named_class_iri(self.item_node(item_index)?, maximum)?;
            match first {
                None => first = Some(iri),
                Some(current) if iri.as_bytes() < current.as_bytes() => {
                    second = first;
                    first = Some(iri);
                }
                _ if second.is_none_or(|current| iri.as_bytes() < current.as_bytes()) => {
                    second = Some(iri);
                }
                _ => {}
            }
        }
        self.empty_annotation_set(start + 1)?;
        match (first, second) {
            (Some(first), Some(second)) => Ok((first, second)),
            _ => Err(KernelError::malformed(
                "encoded EquivalentClasses has too few named expressions",
            )),
        }
    }

    fn class_assertion_pair(
        self,
        node_id: usize,
        maximum: usize,
    ) -> Result<(&'a str, &'a str), KernelError> {
        let start = self.exact_fields(node_id, 3)?;
        let class = self.named_class_iri(self.field_node(start)?, maximum)?;
        let individual = self.named_individual_iri(self.field_node(start + 1)?, maximum)?;
        self.empty_annotation_set(start + 2)?;
        Ok((individual, class))
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
                TAG_DECLARATION => {
                    let start = self.exact_fields(node_id, 2)?;
                    self.entity(self.field_node(start)?)?;
                    self.empty_annotation_set(start + 1)?;
                }
                TAG_OBJECT_SOME_VALUES_FROM
                | TAG_OBJECT_ALL_VALUES_FROM
                | TAG_OBJECT_MIN_CARDINALITY
                | TAG_OBJECT_MAX_CARDINALITY => {
                    self.restriction_parts(node_id, maximum_iri)?;
                }
                TAG_SUB_CLASS_OF => {
                    self.subclass_projection(node_id, maximum_iri)?;
                }
                TAG_EQUIVALENT_CLASSES => {
                    self.equivalent_pair(node_id, maximum_iri)?;
                }
                TAG_OBJECT_PROPERTY_DOMAIN => {
                    self.property_class_pair(node_id, TAG_OBJECT_PROPERTY_DOMAIN, maximum_iri)?;
                }
                TAG_OBJECT_PROPERTY_RANGE => {
                    self.property_class_pair(node_id, TAG_OBJECT_PROPERTY_RANGE, maximum_iri)?;
                }
                TAG_CLASS_ASSERTION => {
                    self.class_assertion_pair(node_id, maximum_iri)?;
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
        Ok(())
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
                    if matches!(
                        self.subclass_projection(node_id, maximum_iri)?,
                        SubclassProjection::Restriction { .. }
                    ) {
                        counts.restriction_subclasses += 1;
                    }
                }
                (ROOT_AXIOM, TAG_EQUIVALENT_CLASSES) => counts.equivalents += 1,
                (ROOT_AXIOM, TAG_OBJECT_PROPERTY_DOMAIN) => {
                    counts.object_property_domains += 1;
                }
                (ROOT_AXIOM, TAG_OBJECT_PROPERTY_RANGE) => {
                    counts.object_property_ranges += 1;
                }
                (ROOT_AXIOM, TAG_CLASS_ASSERTION) => counts.class_assertions += 1,
                (ROOT_ONTOLOGY_ANNOTATION, TAG_ANNOTATION) | (ROOT_EXTENSION, TAG_SWRL_RULE) => {
                    return Err(KernelError::unsupported(
                        "direct native slice does not support ontology annotations or extensions",
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

    fn domain_range_edge_count(
        self,
        maximum_iri: usize,
        state: &AtomicU8,
    ) -> Result<usize, KernelError> {
        let mut count = 0_usize;
        for domain_index in 0..self.root_count() {
            check_cancel(state, domain_index)?;
            let domain_id = self.root_id(domain_index)?;
            if self.node_tag(domain_id)? != TAG_OBJECT_PROPERTY_DOMAIN {
                continue;
            }
            let (domain_property, _domain) =
                self.property_class_pair(domain_id, TAG_OBJECT_PROPERTY_DOMAIN, maximum_iri)?;
            for range_index in 0..self.root_count() {
                check_cancel(state, range_index)?;
                let range_id = self.root_id(range_index)?;
                if self.node_tag(range_id)? != TAG_OBJECT_PROPERTY_RANGE {
                    continue;
                }
                let (range_property, _range) =
                    self.property_class_pair(range_id, TAG_OBJECT_PROPERTY_RANGE, maximum_iri)?;
                if domain_property == range_property {
                    count = count.checked_add(1).ok_or_else(|| {
                        KernelError::resource("encoded domain/range edge-count overflow")
                    })?;
                }
            }
        }
        Ok(count)
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
            let (property, _domain) =
                self.property_class_pair(domain_id, TAG_OBJECT_PROPERTY_DOMAIN, maximum_iri)?;
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
            let (candidate, _range) =
                self.property_class_pair(range_id, TAG_OBJECT_PROPERTY_RANGE, maximum_iri)?;
            if candidate == property {
                return Ok(true);
            }
        }
        Ok(false)
    }
}

pub(crate) fn compile_direct(
    columns: DirectColumns<'_>,
    bidirectional: bool,
    asserted_taxonomy_only: bool,
    only_taxonomy: bool,
    max_edges: usize,
    max_iri_bytes: usize,
    state: &AtomicU8,
) -> Result<(Vec<DirectEdge>, DirectCompileStats), KernelError> {
    check_cancel(state, 0)?;
    columns.validate_generic(state)?;
    columns.validate_supported_nodes(max_iri_bytes, state)?;
    let counts = columns.classify_roots(max_iri_bytes, state)?;
    let buffer_bytes = columns.buffer_bytes()?;
    let directions = 1_usize + usize::from(bidirectional);
    let direct_subclasses = counts
        .subclasses
        .checked_sub(counts.restriction_subclasses)
        .ok_or_else(|| KernelError::malformed("encoded subclass counters are inconsistent"))?;
    let direct_taxonomy_edges = direct_subclasses
        .checked_mul(directions)
        .ok_or_else(|| KernelError::resource("encoded edge-count overflow"))?;
    let equivalent_edges = if asserted_taxonomy_only {
        0
    } else {
        counts
            .equivalents
            .checked_mul(directions)
            .ok_or_else(|| KernelError::resource("encoded edge-count overflow"))?
    };
    let restriction_edges = if asserted_taxonomy_only || only_taxonomy {
        0
    } else {
        counts.restriction_subclasses
    };
    let class_assertion_edges = if asserted_taxonomy_only {
        0
    } else {
        counts.class_assertions
    };
    let domain_range_edges = if asserted_taxonomy_only {
        0
    } else {
        columns.domain_range_edge_count(max_iri_bytes, state)?
    };
    let projected = direct_taxonomy_edges
        .checked_add(equivalent_edges)
        .and_then(|total| total.checked_add(restriction_edges))
        .and_then(|total| total.checked_add(class_assertion_edges))
        .and_then(|total| total.checked_add(domain_range_edges))
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
                edges.push(DirectEdge {
                    source: clone_text(source)?,
                    relation: clone_text(relation)?,
                    destination: clone_text(destination)?,
                });
            }
            SubclassProjection::Restriction { .. } => {}
        }
    }

    // The reference compiler emits the class-axiom categories explicitly:
    // asserted subclasses, equivalents, then ABox class assertions.  Separate
    // bounded scans preserve that order even for hostile-but-monotone root IDs.
    if !asserted_taxonomy_only {
        for index in 0..columns.root_count() {
            check_cancel(state, index)?;
            let node_id = columns.root_id(index)?;
            if columns.node_tag(node_id)? != TAG_EQUIVALENT_CLASSES {
                continue;
            }
            let (source, destination) = columns.equivalent_pair(node_id, max_iri_bytes)?;
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

        for index in 0..columns.root_count() {
            check_cancel(state, index)?;
            let node_id = columns.root_id(index)?;
            if columns.node_tag(node_id)? != TAG_CLASS_ASSERTION {
                continue;
            }
            let (individual, class) = columns.class_assertion_pair(node_id, max_iri_bytes)?;
            edges.push(DirectEdge {
                source: clone_text(individual)?,
                relation: clone_text(RDF_TYPE)?,
                destination: clone_text(class)?,
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
                let (domain_property, domain) = columns.property_class_pair(
                    domain_id,
                    TAG_OBJECT_PROPERTY_DOMAIN,
                    max_iri_bytes,
                )?;
                if domain_property != property {
                    continue;
                }
                for range_index in 0..columns.root_count() {
                    check_cancel(state, range_index)?;
                    let range_id = columns.root_id(range_index)?;
                    if columns.node_tag(range_id)? != TAG_OBJECT_PROPERTY_RANGE {
                        continue;
                    }
                    let (range_property, range) = columns.property_class_pair(
                        range_id,
                        TAG_OBJECT_PROPERTY_RANGE,
                        max_iri_bytes,
                    )?;
                    if range_property == property {
                        edges.push(DirectEdge {
                            source: clone_text(domain)?,
                            relation: clone_text(property)?,
                            destination: clone_text(range)?,
                        });
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
        declarations: counts.declarations,
        subclasses: counts.subclasses,
        restriction_subclasses: counts.restriction_subclasses,
        equivalents: counts.equivalents,
        class_assertions: counts.class_assertions,
        object_property_domains: counts.object_property_domains,
        object_property_ranges: counts.object_property_ranges,
        domain_range_edges,
        edges: edges.len(),
        buffer_bytes,
    };
    Ok((edges, stats))
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

fn clone_text(value: &str) -> Result<String, KernelError> {
    let mut output = String::new();
    output
        .try_reserve_exact(value.len())
        .map_err(|_| KernelError::resource("encoded edge-string allocation failed"))?;
    output.push_str(value);
    Ok(output)
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
    fn malformed_columns_and_unsupported_tags_fail_before_output() {
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
        unsupported.node_tags[10..12].copy_from_slice(&63_u16.to_le_bytes());
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
            Err(KernelError::Unsupported(_))
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
