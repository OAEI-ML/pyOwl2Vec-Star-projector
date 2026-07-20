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
const TAG_DECLARATION: u16 = 60;
const TAG_SUB_CLASS_OF: u16 = 61;
const TAG_EQUIVALENT_CLASSES: u16 = 62;
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
    pub(crate) equivalents: usize,
    pub(crate) class_assertions: usize,
    pub(crate) edges: usize,
    pub(crate) buffer_bytes: usize,
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
                TAG_SUB_CLASS_OF => {
                    let start = self.exact_fields(node_id, 3)?;
                    self.named_class_iri(self.field_node(start)?, maximum_iri)?;
                    self.named_class_iri(self.field_node(start + 1)?, maximum_iri)?;
                    self.empty_annotation_set(start + 2)?;
                }
                TAG_EQUIVALENT_CLASSES => {
                    self.equivalent_pair(node_id, maximum_iri)?;
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

    fn classify_roots(self, state: &AtomicU8) -> Result<(usize, usize, usize, usize), KernelError> {
        let mut declarations = 0_usize;
        let mut subclasses = 0_usize;
        let mut equivalents = 0_usize;
        let mut class_assertions = 0_usize;
        for index in 0..self.root_count() {
            check_cancel(state, index)?;
            let kind = self.root_kind(index)?;
            let node_id = self.root_id(index)?;
            let tag = self.node_tag(node_id)?;
            match (kind, tag) {
                (ROOT_AXIOM, TAG_DECLARATION) => declarations += 1,
                (ROOT_AXIOM, TAG_SUB_CLASS_OF) => subclasses += 1,
                (ROOT_AXIOM, TAG_EQUIVALENT_CLASSES) => equivalents += 1,
                (ROOT_AXIOM, TAG_CLASS_ASSERTION) => class_assertions += 1,
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
        Ok((declarations, subclasses, equivalents, class_assertions))
    }
}

pub(crate) fn compile_direct(
    columns: DirectColumns<'_>,
    bidirectional: bool,
    asserted_taxonomy_only: bool,
    max_edges: usize,
    max_iri_bytes: usize,
    state: &AtomicU8,
) -> Result<(Vec<DirectEdge>, DirectCompileStats), KernelError> {
    check_cancel(state, 0)?;
    columns.validate_generic(state)?;
    columns.validate_supported_nodes(max_iri_bytes, state)?;
    let (declarations, subclasses, equivalents, class_assertions) =
        columns.classify_roots(state)?;
    let buffer_bytes = columns.buffer_bytes()?;
    let directions = 1_usize + usize::from(bidirectional);
    let taxonomy_axioms = subclasses
        .checked_add(if asserted_taxonomy_only {
            0
        } else {
            equivalents
        })
        .ok_or_else(|| KernelError::resource("encoded taxonomy edge-count overflow"))?;
    let taxonomy_edges = taxonomy_axioms
        .checked_mul(directions)
        .ok_or_else(|| KernelError::resource("encoded edge-count overflow"))?;
    let projected = taxonomy_edges
        .checked_add(if asserted_taxonomy_only {
            0
        } else {
            class_assertions
        })
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
        let start = columns.exact_fields(node_id, 3)?;
        let source = columns.named_class_iri(columns.field_node(start)?, max_iri_bytes)?;
        let destination = columns.named_class_iri(columns.field_node(start + 1)?, max_iri_bytes)?;
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
    }
    check_cancel(state, columns.root_count())?;
    let stats = DirectCompileStats {
        roots: columns.root_count(),
        nodes: columns.node_count(),
        declarations,
        subclasses,
        equivalents,
        class_assertions,
        edges: edges.len(),
        buffer_bytes,
    };
    Ok((edges, stats))
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

    fn running_state() -> AtomicU8 {
        AtomicU8::new(STATE_RUNNING)
    }

    #[test]
    fn declarations_are_silent_and_named_subclasses_compile() {
        let fixture = named_subclass_fixture();
        let (edges, stats) =
            compile_direct(fixture.columns(), false, false, 4, 1024, &running_state()).unwrap();
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
        let (edges, stats) =
            compile_direct(fixture.columns(), true, false, 2, 1024, &running_state()).unwrap();
        assert_eq!(edges.len(), 2);
        assert_eq!(edges[1].relation, SUPERCLASS_OF);
        assert_eq!(stats.edges, 2);
        let error =
            compile_direct(fixture.columns(), true, false, 1, 1024, &running_state()).unwrap_err();
        assert!(matches!(error, KernelError::Resource(_)));
    }

    #[test]
    fn named_equivalents_and_class_assertions_follow_reference_order() {
        let fixture = named_class_axiom_fixture();
        let (edges, stats) =
            compile_direct(fixture.columns(), true, false, 5, 1024, &running_state()).unwrap();
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
    fn asserted_taxonomy_mode_preflights_but_suppresses_adjacent_axioms() {
        let fixture = named_class_axiom_fixture();
        let (edges, stats) =
            compile_direct(fixture.columns(), false, true, 1, 1024, &running_state()).unwrap();
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
            compile_direct(malformed.columns(), false, false, 4, 1024, &running_state()),
            Err(KernelError::Malformed(_))
        ));

        let mut unsupported = named_subclass_fixture();
        unsupported.node_tags[10..12].copy_from_slice(&63_u16.to_le_bytes());
        assert!(matches!(
            compile_direct(
                unsupported.columns(),
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
            compile_direct(fixture.columns(), false, false, 4, 1024, &running_state()),
            Err(KernelError::Malformed(_))
        ));
    }

    #[test]
    fn cancellation_precedes_validation_or_output() {
        let fixture = named_subclass_fixture();
        let state = AtomicU8::new(STATE_CANCELLED);
        assert_eq!(
            compile_direct(fixture.columns(), false, false, 4, 1024, &state),
            Err(KernelError::Cancelled)
        );
    }
}
