# Pinned mOWL reference behavior

## 1. Reference identity

The normative compatibility target is the Scala source at mOWL commit
`d9935369144f9a618ece38b7b2a8f4293afe8c26`:

- file: `gateway/src/main/scala/org/mowl/Projectors/OWL2VecStarProjector.scala`;
- Git blob: `a7f7584bbe687ae341cf0547bc0492ada87cf4b8`;
- toolchain: Java 11, Scala 2.11.12, OWLAPI 4.5.22, ELK 0.4.3, HermiT
  1.3.8.413; and
- Python wrapper:
  `mowl/projection/owl2vec_star/model.py` at the same commit.

The source and golden oracle take precedence over the broader mOWL documentation table when
they differ. The original OWL2Vec* paper remains design context, not an executable oracle.

Normative observations are indexed as `RB-001` through `RB-047` in
[`reference-rules.json`](reference-rules.json). That machine-readable catalogue maps every
claim to at least one isolated fixture. Fixture IDs are also embedded in each golden so a rule
cannot become an untraceable prose assertion.

## 2. General traversal

The upstream abstract projector traverses the imports closure (`Imports.fromBoolean(true)`) for
TBox, RBox, ABox, signatures, and all axioms. The core snapshot supplies that already-resolved
closure; the compatibility compiler must not resolve imports itself.

Upstream output is a Python list. The wrapper removes only triples whose destination is the
empty string. It neither sorts nor value-deduplicates them. Scala's final `.distinct` is
ineffective because `Triple` is an ordinary reference-equality class without `equals` or
`hashCode`. The compatibility result is therefore an edge **bag**, not a set.

The Scala concatenation order is subclass edges, equivalence edges, annotations, class
assertions, object-property assertions, then domain/range edges. The new `encounter` order keeps
that category order and uses the profile's deterministic compatibility order inside a category;
`canonical` subsequently sorts the complete bag.

OWLAPI set iteration is not a stable public ordering. For shapes where the upstream selects
members by incidental iteration, `mowl-d993536-v1` uses a projector-private compatibility
comparator reproducing OWLAPI 4.5.22/Java hash traversal where the golden corpus establishes it.
This comparator must never become a core axiom order or fingerprint rule. Strict differential
comparison uses edge counters unless an ordering assertion is specifically under test.

## 3. Vocabulary

Named taxonomy edges use exactly:

```text
http://subclassof
http://superclassof
```

Class assertions use `http://type`. Restriction, property assertion, annotation, domain, and
range edges use the full object/annotation property IRI. These historical strings are not
expanded to RDF/RDFS vocabulary in the compatibility profile.

## 4. Subclass axioms

For `SubClassOf(sub, super)`:

- named class to named class emits `(sub, http://subclassof, super)`;
- `bidirectional_taxonomy=True` additionally emits
  `(super, http://superclassof, sub)`;
- a supported restriction on one side is processed only when the other side is a named class;
- supported object restrictions are `some`, `only`, `min`, and `max`;
- exact cardinality is ignored;
- min/max numeric cardinalities are discarded, so only property and filler influence an edge;
- unqualified min/max use `owl:Thing` as the filler;
- the filler must be a named class; union/intersection fillers are rejected by the private
  quantified-expression helper and yield no edge; and
- top-level union/intersection operands in an ordinary subclass axiom are otherwise ignored.

When the subclass is the named class and the superclass is a restriction, expansion emits from
that class to the filler. When the subclass is a restriction and the superclass is named, the
historical implementation emits from the named superclass to the filler. This counterintuitive
direction is required.

Every accepted restriction edge is expanded using the historical inverse-role and subrole maps
described below. `only_taxonomy=True` suppresses these restriction edges, but does **not** turn
the whole projector into taxonomy-only output.

## 5. Equivalent classes

The upstream code destructures only its first two encountered class expressions and ignores all
remaining members of an n-ary `EquivalentClasses` axiom. The second expression controls the
branch:

- if it is named, process the first/second pair;
- if it is a top-level intersection, process the first against each operand;
- if it is a top-level union, process the first against each operand; and
- if it is directly a restriction, emit nothing.

Operand order therefore affects historical results. The compatibility compiler's private
ordering reproduces the pinned oracle without changing the shared snapshot. Fixtures must cover
permutations, three-or-more members, named/restriction orientation, and union/intersection
branches.

## 6. RBox rules and defects

### Subproperties

For `SubObjectPropertyOf(sub, super)`, upstream attempts to store `sub` under `super`, but builds
the new list by looking up the prior value under `sub` rather than under `super`:

```text
subRoles(super) = sub :: subRoles.getOrElse(sub, Nil)
```

Siblings can overwrite one another; no transitive closure is computed. Because input iteration
comes from an OWLAPI hash set, the surviving sibling may be hash-order-dependent. The
compatibility compiler emulates this in private state. A future corrected semantic profile must
use a new ID.

### Inverses

Inverse-object-property axioms populate a bidirectional mapping. When a property appears in
multiple inverse declarations, the last visited mapping wins. Restriction and domain/range
rules expand through this map. Direct object-property assertions do not.

### Domain and range

Only named object properties with named domain/range classes participate. For each property, the
projector emits a cross-product of all matching named domains and ranges, then applies
inverse/subrole expansion. The source has collection branches in both its RBox loop and general
all-axiom loop, but OWLAPI 4.5.22 does not return domain/range axioms from `getRBoxAxioms`; the
pinned combination therefore makes one effective collection pass. Distinct annotated
domain/range axioms with the same structural tuple still produce equal edges through the
cross-product, and those counts are preserved when `duplicates="preserve"` (RB-020–RB-023).

Property chains, symmetric-property semantics, and other RBox shapes are ignored.

## 7. ABox rules

A class assertion emits `(individual, http://type, class)` only when both the individual and
class expression are named. Anonymous individuals and complex class expressions are ignored.

A named object-property assertion emits its direct triple. Historical inverse/subrole expansion
for assertions is commented out and must remain absent. An anonymous subject is converted through
OWLAPI's `toStringID` and therefore emits a direct triple whose source is the generated blank-node
identifier. An inverse-property assertion reaches the unchecked property cast and raises
`java.lang.ClassCastException`. These WP-P1 outcomes are pinned by `abox` and
`abox-unsupported-property`; implementations must not silently replace them with a different edge
or ignore policy (RB-026–RB-028).

Data-property assertions are not emitted, even with literals enabled.

## 8. Selected class annotations

Despite its name, `include_literals=True` enables selected annotations on classes and may emit an
IRI-valued annotation. It does not visit ontology, individual, object-property, or data-property
annotations, and it does not enable data-property assertions.

The class-signature loop includes the imports closure, but `EntitySearcher.getAnnotations` is
called against the root ontology rather than each declaring ontology. Consequently, an annotation
assertion present only in an imported document is not emitted, even though its class participates
in closure traversal. `imports-one-level` pins this source/OWLAPI interaction (RB-029).

The **annotation property**, not datatype, is allowlisted. The exact upstream membership set is:

```text
http://www.w3.org/2000/01/rdf-schema#label
http://www.w3.org/2004/02/skos/core#prefLabel
rdfs:label
rdfs:comment
http://purl.obolibrary.org/obo/IAO_0000111
http://purl.obolibrary.org/obo/IAO_0000589
http://www.geneontology.org/formats/oboInOwl#hasRelatedSynonym
http://www.geneontology.org/formats/oboInOwl#hasExactSynonym
http://www.geneontology.org/formats/oboInOWL#hasExactSynonym
http://purl.bioontology.org/ontology/SYN#synonym
http://scai.fraunhofer.de/CSEO#Synonym
http://purl.obolibrary.org/obo/synonym
http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#FULL_SYN
http://www.ebi.ac.uk/efo/alternative_term
http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#Synonym
http://bioontology.org/projects/ontologies/fma/fmaOwlDlComponent_2_0#Synonym
http://www.geneontology.org/formats/oboInOwl#hasDefinition
http://bioontology.org/projects/ontologies/birnlex#preferred_label
http://bioontology.org/projects/ontologies/birnlex#synonyms
http://www.w3.org/2004/02/skos/core#altLabel
https://cfpub.epa.gov/ecotox#latinName
https://cfpub.epa.gov/ecotox#commonName
https://www.ncbi.nlm.nih.gov/taxonomy#scientific_name
https://www.ncbi.nlm.nih.gov/taxonomy#synonym
https://www.ncbi.nlm.nih.gov/taxonomy#equivalent_name
https://www.ncbi.nlm.nih.gov/taxonomy#genbank_synonym
https://www.ncbi.nlm.nih.gov/taxonomy#common_name
http://purl.obolibrary.org/obo/IAO_0000118
http://www.w3.org/2000/01/rdf-schema#comment
http://www.geneontology.org/formats/oboInOwl#hasDbXref
http://purl.org/dc/elements/1.1/description
http://purl.org/dc/terms/description
http://purl.org/dc/elements/1.1/title
http://purl.org/dc/terms/title
http://purl.obolibrary.org/obo/IAO_0000115
http://purl.obolibrary.org/obo/IAO_0000600
http://purl.obolibrary.org/obo/IAO_0000602
http://purl.obolibrary.org/obo/IAO_0000601
http://www.geneontology.org/formats/oboInOwl#hasOBONamespace
```

The source list repeats `hasRelatedSynonym`; membership behavior is unchanged by that duplicate.
The similarly named `oboInOWL#hasExactSynonym` spelling with uppercase `OWL` is deliberately
distinct and retained.

String and RDF plain literals use OWLAPI's raw lexical form. For every other datatype, upstream
prints a warning and applies `stripValue(value.toString)`: remove every backslash, then remove
the first and last character if the result begins with `"`, or remove angle brackets if it begins
with `<`. This can preserve datatype syntax incorrectly or truncate its last character; the
compatibility profile reproduces the resulting destination exactly but records a diagnostic
instead of stdout. IRI-valued annotations use the same bracket-stripping branch. Golden vectors
pin language tags, escaped strings, numeric/boolean/date datatypes, IRIs, empty values, and
unsupported annotation properties.

## 9. The `only_taxonomy` defect

The upstream flag suppresses restriction projection but does not suppress named class
assertions, object-property assertions, domain/range edges, or class annotations. The profile
preserves that behavior. Consumers that genuinely need asserted taxonomy use
`project_taxonomy`, which is a separate compiler and output contract.

## 10. Ignored constructs

The pinned implementation produces no edges for, among others:

- exact cardinality restrictions;
- `hasValue` and self restrictions;
- data restrictions and data-property assertions;
- complex restriction fillers;
- general concept inclusions whose accepted named/restriction pattern is absent;
- property chains (counted once during the role scan, without a grouped diagnostic) and most
  property characteristics; and
- annotations outside named classes.

Ignored shapes are not normalized into accepted ones. A diagnostic count is allowed, but no
warning flood or stdout print is part of the public contract.

`EquivalentObjectProperties`, `DisjointObjectProperties`, `FunctionalObjectProperty`,
`InverseFunctionalObjectProperty`, `ReflexiveObjectProperty`, `IrreflexiveObjectProperty`,
`SymmetricObjectProperty`, `AsymmetricObjectProperty`, and `TransitiveObjectProperty` are not part
of the pinned RBox scan at all. Each is counted as a skipped axiom with a grouped
`MOWL_SKIPPED_AXIOM` diagnostic and cannot mutate retained role state.
`SubDataPropertyOf`, `EquivalentDataProperties`, and `DisjointDataProperties` follow the same
skipped-axiom contract and cannot mutate object-role state.
`DataPropertyDomain` is also skipped rather than projected and cannot populate object domains or
mutate object-role state.
`DataPropertyRange` follows the same contract and cannot populate object ranges.
`FunctionalDataProperty` is likewise skipped and cannot mutate object-role state.
`DatatypeDefinition` is skipped and cannot affect projected datatype or role state.
`HasKey` is skipped and cannot affect projected class, data-property, or object-role state.

## 11. Mutable instance state

The Scala object's `subRoles` and `inverseRoles` maps are fields and are not reset by
`project(...)`. Reusing one object across ontologies can leak role mappings. Production calls
use `compatibility_state="isolated"` and intentionally fix this lifecycle defect. The explicit
`"scala-instance"` mode reproduces it for differential tests and forensic replay. The option is
compiler-local, call-history-sensitive, non-concurrent, and fully recorded in provenance.

## 12. Non-normative ecosystem comparison

The PyPI project `owl2vec-star` `0.2.0` advertises a Python/Owlready implementation and an
interpreter range below Python 3.9. It is not the distribution name or behavior oracle for this
project. WP-P0 must audit its public API, dependency/license metadata, source provenance, and
name-confusion risk without copying code. Results belong in the release evidence. The behavior
target remains the pinned mOWL Scala file plus the original paper as explanatory context.
