# P7 encoded-native compiler checkpoint

Date: 2026-07-20. Projector implementation through `567562e`. pyOWLCore candidate revision:
`cb86ab1`. Exact-OM integration revision: `fe46141`.

## Outcome

P7 is **not accepted, integrated, advertised, or promoted**. Production projection still uses the
Python semantic compiler. When the native backend is selected, that Python compiler materializes
projector `Edge` values and the established Rust `EdgeBatchProcessor` receives owned string tuples
only to apply edge policy. The broad decoder in `encoded_compiler.py` is Python code; its tests and
counter ledger are not evidence of a Rust ontology compiler.

The extension feature ledger remains exactly `abi3-py310` and `bounded-batches`.
`encoded-structural-compiler-v1` is absent, so normal backend negotiation cannot select an
encoded-native production compiler.

This checkpoint adds a real but deliberately private Rust foundation. It proves one useful
semantic slice across the actual PyO3 boundary without changing the production claim:

- one canonical direct structural-columns v1 segment;
- exact full immutable-`bytes` exporters for all eleven columns;
- IRI and Entity nodes plus silent Declaration roots with supported annotation metadata;
- named-to-named `SubClassOf`, named and supported aggregate `EquivalentClasses`, and named
  `ClassAssertion` roots;
- named-or-inverse-property/named-filler `ObjectSomeValuesFrom`, `ObjectAllValuesFrom`,
  `ObjectMinCardinality`, and `ObjectMaxCardinality` on either side of a named class in
  `SubClassOf`; inverse expressions project through their underlying named IRI, and min/max
  integers are minimally encoded and validated but semantically discarded; recursively complex
  fillers are fully validated but make the restriction nonprojecting, including when a supported
  sibling in a selected aggregate still emits;
- scalar-nonprojecting `ObjectOneOf`, `ObjectHasValue`, and `ObjectHasSelf` expressions in bounded
  `SubClassOf` and `ClassAssertion` roots: canonical named/anonymous individual sets or values and
  named/inverse properties are fully validated, while the containing root is counted and ignored
  without output or role-state mutation;
- scalar-nonprojecting `ObjectExactCardinality` with a minimally encoded cardinality,
  named/inverse property, and recursively validated class-expression filler in the same root
  positions, likewise validated and ignored without output or role-state mutation;
- recursively nested `ObjectComplementOf` over the full supported class-expression graph, with the
  complete operand validated before the containing root is counted and ignored;
- all six scalar-nonprojecting data class expressions in bounded `SubClassOf` and `ClassAssertion`
  roots: nonempty ordered named-property sequences for `DataSomeValuesFrom`/`DataAllValuesFrom`,
  named-property/literal `DataHasValue`, and minimally encoded cardinality plus named property for
  `DataMinCardinality`, `DataMaxCardinality`, and `DataExactCardinality`;
- recursive data-range fillers for those expressions: named datatypes, canonical nonempty literal
  `DataOneOf`, named-datatype `DatatypeRestriction` with canonical nonempty facet restrictions,
  and arbitrarily nested `DataComplementOf`, `DataIntersectionOf`, and `DataUnionOf` graphs over
  those leaves; aggregates retain their canonical same-constructor flattening invariant, cycles
  fail before output, and valid ranges are ignored without output or role-state mutation;
- named-class `ClassAssertion` roots over anonymous individuals follow the same scalar ignored-shape
  contract, while named-class/named-individual assertions retain their existing `http://type` edge;
- scalar-selected `ObjectIntersectionOf`/`ObjectUnionOf` expressions in `EquivalentClasses`, with
  direct named-class, supported named-filler restriction, or recursively nested class-expression
  operands; direct named operands emit taxonomy edges, direct named-filler restrictions use the
  same direct/subrole/inverse expansion as subclass restrictions, and recursive/nonprojecting
  operands are fully validated and ignored;
- aggregate or supported restriction `ClassAssertion`, aggregate `SubClassOf`, and direct
  nonprojecting/restriction `EquivalentClasses` selections are fully validated and their roots
  counted while emitting no output, matching scalar ignored-shape behavior;
- exact scalar aggregate ordering: named expressions/operands use UTF-8 lexical IRI order,
  aggregate and restriction kinds use pinned OWLAPI type order, same-kind restrictions retain
  frozen node order, and duplicate projected edges remain duplicated;
- `DisjointClasses` and named-defined-class `DisjointUnion` roots over the same bounded named,
  aggregate, restriction, and nonprojecting expression envelope, fully validated and counted but
  skipped without output or role-state mutation;
- paired named `ObjectPropertyDomain`/`ObjectPropertyRange` roots, producing the reference
  domain-by-range products in UTF-8 property order;
- inverse-property or recursively complex object domain/range roots are fully validated and counted
  but ignored, so mixed calls exclude them from property pairing and role expansion exactly as the
  scalar profile does;
- named-property `ObjectPropertyAssertion` roots over named or anonymous individuals, producing
  direct triples without subrole or inverse expansion;
- named-or-inverse `NegativeObjectPropertyAssertion` roots over named or anonymous individuals,
  fully validated, counted, and silently skipped as in the scalar profile;
- positive inverse object-property assertions mapped to the pinned
  `UnsupportedAxiomShapeError` with `ObjectInverseOf`/`java.lang.ClassCastException` details;
- named-or-inverse `SubObjectPropertyOf` and `InverseObjectProperties` roots, including bounded
  annotation metadata, projected through the underlying named IRI while their distinct OWLAPI
  expression and annotation-set hashes control visitation;
- exact OWLAPI 4.5.22 role-annotation hashing: order-independent wrapping set sums, annotation seed
  6311, named annotation-property IRI hashes, IRI value hashes, literal lexical-form Java-string
  hashes (independent of datatype/language), and valid-UTF-8 anonymous local-key Java-string hashes;
  nested annotations are structurally validated but excluded from `OWLAnnotation.hashCode`;
- `SubObjectPropertyOf` roots whose sub-property is an ordered, minimum-two
  `ObjectPropertyChain` of named/inverse members: the complete sequence and named/inverse
  super-property plus bounded annotation metadata are validated and counted, but the chain is an
  exact scalar ignored shape that cannot mutate role state;
- property-chain roots still contribute to the OWLAPI hash-table capacity used to visit unrelated
  named/inverse role rows, preserving the capacity-boundary overwrite behavior even though the
  chains themselves never enter the role map;
- exact OWLAPI hash-table capacity/bucket/spread/canonical-tie traversal, including the historical
  subrole sibling overwrite and last-visited inverse overwrite;
- direct/subrole/inverse expansion, in that order, for subclass/aggregate restrictions and
  domain/range edges only; direct object-property assertions deliberately remain unexpanded;
- named-or-inverse canonical `EquivalentObjectProperties` and `DisjointObjectProperties` sets plus
  `FunctionalObjectProperty`, `InverseFunctionalObjectProperty`, `ReflexiveObjectProperty`,
  `IrreflexiveObjectProperty`, `SymmetricObjectProperty`, `AsymmetricObjectProperty`, and
  `TransitiveObjectProperty`, fully validated, counted, skipped, and state-neutral;
- named `SubDataPropertyOf`, canonical named `EquivalentDataProperties` and
  `DisjointDataProperties` sets, recursive-class-expression `DataPropertyDomain`,
  recursive-data-range `DataPropertyRange`, named `FunctionalDataProperty`, and named-to-recursive-range
  `DatatypeDefinition`, fully validated, counted, skipped, and unable to mutate object-role state;
- named- or anonymous-source `DataPropertyAssertion` and `NegativeDataPropertyAssertion` roots with
  plain, typed, or language-tagged literals, fully validated and counted but always skipped without
  an edge;
- exact three-field literal validation, including UTF-8 lexical/language text, a named datatype,
  canonical absent-language fields, lowercase nonempty language tags, and the required
  `rdf:PlainLiteral` language/datatype relationship, performed in place without cloning literal
  text;
- selected class `AnnotationAssertion` roots when `include_literals` is enabled: IRI subjects must
  occur in the retained class signature, properties must match the exact 39-entry scalar
  whitelist, full RDFS label/comment IRIs rewrite to their `rdfs:` spellings, and IRI-, literal-,
  or anonymous-individual-valued objects emit one edge;
- anonymous `AnnotationAssertion` subjects are fully validated but cannot pass class-IRI selection;
  anonymous selected values and positive object-assertion arguments use the exact shared scalar
  identifier space;
- exact annotation literal behavior for plain/language-tagged and explicit XSD-string values, plus
  the pinned malformed non-string rendering for XSD and arbitrary datatypes, including escape
  removal and Unicode-codepoint-safe removal of the final rendered datatype character;
- nonempty and nested `Annotation` metadata on `AnnotationAssertion` roots, validated over the
  exact named-property and IRI/literal/anonymous-individual value envelope but ignored
  semantically, so structurally distinct annotated assertions preserve duplicate projected edges;
- ontology `Annotation` roots over that same bounded metadata envelope, fully validated and
  counted but silent and excluded from the skipped-axiom total;
- annotated named `SubAnnotationPropertyOf`, `AnnotationPropertyDomain`, and
  `AnnotationPropertyRange` roots, with exact annotation-property and target-IRI fields, fully
  validated, counted, skipped, and unable to mutate object-role state;
- bounded annotation metadata on every otherwise-supported axiom family, including
  projecting class/ABox/domain-range roots and state-neutral logical, object-property, and
  data-property skips; metadata is fully validated and ignored semantically except where the exact
  annotation hash participates in role-state visitation;
- extension-root `SWRLRule` nodes with canonical body/head atom sets and supported annotation
  metadata, including empty rule sides, fully validated and counted but silent, excluded from the
  skipped-axiom total, and unable to mutate role state;
- the complete structural-columns v1 SWRL constructor family: IRI-backed `Variable` nodes;
  `ClassAtom` over the full recursive class-expression envelope; `DataRangeAtom` over the full
  recursive data-range envelope; named/inverse `ObjectPropertyAtom`; named `DataPropertyAtom`; IRI-backed
  `BuiltInAtom` with ordered, possibly-empty data arguments; and
  `SameIndividualAtom`/`DifferentIndividualsAtom`, with named, anonymous, variable, and literal
  argument kinds validated in their exact positions;
- allocation-free repeated class-entity scans for annotation subject membership, avoiding an
  ontology-sized class-signature index while retaining deterministic canonical root order;
- `HasKey` roots over the same recursive class-expression envelope, canonical named/inverse object
  properties, canonical named data properties, and supported annotation metadata, fully validated
  and counted but skipped without output or role-state mutation;
- `SameIndividual` and `DifferentIndividuals` roots over canonical sets of named or anonymous
  individuals plus supported annotation metadata, fully validated and counted but likewise
  state-neutral scalar skips; anonymous identifiers retain exact bytes32 document scopes and
  nonempty local keys without text decoding or allocation;
- exact axiom-derived blank-ID assignment: all anonymous nodes reachable from axiom roots,
  including silent/skipped axioms and axiom annotation metadata, are ordered by canonical model
  bytes and numbered from `_:genid2147483648`; ontology-annotation, SWRL-only, and unreachable
  anonymous nodes are excluded and cannot shift projected IDs;
- the supported reference category order (`SubClassOf`, `EquivalentClasses`, selected
  `AnnotationAssertion`, `ClassAssertion`, `ObjectPropertyAssertion`, then domain/range), with the
  first two equivalent members selected by the pinned expression order;
- forward `http://subclassof`, optional reverse `http://superclassof`, `http://type`, and direct
  named object-property edges;
- the historical `only_taxonomy` behavior: restriction edges are suppressed while named
  equivalence, enabled selected annotations, class assertions, positive object assertions, skipped
  logical/object/data property families, and role-expanded object domain/range products remain
  enabled;
- an asserted-taxonomy mode that preflights the complete supported input but emits only direct
  named `SubClassOf`, with no annotation leakage; and
- one caller-bounded coarse output batch, with no per-root or per-edge Python call.

Any other valid segment, exporter, constructor, nonprojecting expression outside the supported root
positions, or structurally unsupported object-property chain is rejected as unsupported before
output. N-ary
equivalents beyond the selected first two, non-selected supported aggregate expressions, complete
supported disjoint/property sets, every supported literal and annotation node, and unpaired object
domain/range roots are still fully validated. A role annotation whose anonymous local-key bytes are
not valid UTF-8 remains an exact whole-call fallback because the scalar hash path raises while
encoding its surrogateescaped value. Malformed supported columns fail closed.
Same-operation isolated role expansion is proven; retained Scala-instance role-state reuse is not
part of this one-shot seam. This is kernel version 24 of a private foundation, not the complete
compiler described by WP-P7.

## What the private kernel actually does

`EncodedDirectCompiler` receives the already validated public encoded view, its exact retained
owner, and the descriptor digest bound by the Python adapter. Its constructor rechecks the frozen
schema/model version, descriptor binding, direct-segment envelope, exact buffer names, memoryview
readonly/shape metadata, and exporter coverage. It retains the encoded view, owner, and one owned
reference to each immutable bytes exporter.

`compile_batch()` lends those stable byte slices to the Python-free Rust kernel and releases the
GIL with `Python::detach`. Rust then:

1. validates paired column widths, counts, monotone offsets, canonical roots, component kinds,
   node/item/scalar references, canonical-set ordering/uniqueness, ordered-sequence bounds, and
   arena coverage;
2. preflights every supported constructor, aggregate member/type/order envelope, UTF-8 IRI/literal
   field, entity kind, literal language/datatype relationship, minimally encoded cardinality,
   exact OWLAPI-compatible role annotation hashes, typed/nested annotation sets with
   IRI/literal/anonymous values on every supported axiom, ontology annotation, and
   annotation-property root, every SWRL rule/variable/atom constructor over its recursive class/data
   and bounded property/individual/data-argument envelope, the complete recursive class-expression
   and data-range graphs with cycle detection,
   anonymous-individual bytes32/nonempty-key invariants,
   axiom-only reachability and canonical blank-ID order, root kind/tag pairing,
   output/cross-product count, and IRI/output limit;
3. builds a compact axiom-derived anonymous-node index and exact-capacity borrowed-IRI role
   rows/indexes only after whole-view validation, computes all expanded edge counts, and allocates
   output only after the complete preflight succeeds; and
4. returns one coarse list of edge tuples plus roots, nodes, axiom-derived anonymous individuals,
   declarations, subclasses plus their
   restriction and ignored partitions, equivalents/aggregate equivalents,
   disjoint class/union roots, keys, same/different individual roots, class assertions and their
   ignored partition, positive/negative object-property assertions, sub-object properties and
   ignored property chains, every supported object/data/annotation-property axiom family,
   ontology annotations, SWRL rules, annotation assertions, selected annotation edges, non-string
   literal renderings, skipped axioms, object domain/range roots and products, role-expansion edges,
   edges, and retained-buffer-byte counters.

The Python wrapper exposes an exact call ledger for this kernel: eleven input buffers, eleven
detached/zero-copy buffers, zero indexed buffers, zero staging/structural copy bytes, zero per-row
FFI calls, one native boundary call, and GIL release. These counters describe only this private
call. They are not currently attached to `ProjectionReport`, because the production projector
does not dispatch to the private kernel. Aggregate and domain/range counting/ordering use repeated
borrowed scans; selected annotation membership likewise uses repeated borrowed class-entity scans.
When anonymous nodes exist, one bit-packed reachability vector, depth-bounded traversal stack, and
exact-sized sorted node-ID vector reproduce the scalar axiom-derived identifier space. When
recursive data-range or recursively owned class-expression nodes exist, compact color vectors and
grow-checked iterative event stacks validate arbitrary nesting through aggregates, complements,
object-restriction fillers, exact-cardinality fillers, and data ranges, rejecting cycles without
using the native call stack. The other
structural indexes are the projector-private role rows and subrole/inverse maps required by the
pinned rules; they borrow retained IRI text and are reserved to exact root-derived capacities.

The compiler is one-shot. Atomic idle/running/finished/cancelled/failed transitions allow another
Python thread to cancel detached work. A cancellation racing with successful compilation discards
the result. Unsupported, malformed, pinned reference, resource, cancelled, and panic outcomes cross
the boundary as distinct typed failures; no partial batch is returned. The private v24 ABI returns
its fifty-four counters as an explicitly constructed Python tuple because PyO3's automatic tuple
conversion is bounded below that arity.

## No-copy boundary and exact blocker

PyO3 0.28.3 gates its safe generic `PyBuffer` API out at the project's `abi3-py310` floor. The
private kernel therefore accepts only exact memoryviews that cover an entire immutable `bytes`
exporter. This path is safe to lend while detached because the compiler retains both the Python
owner graph and immutable exporter references; it makes no ontology-sized input copy.

Sliced exporters, readonly bytearrays, mmap-backed views, and other otherwise valid public buffer
providers are reported as unsupported. The kernel does not copy them and does not count them as
detached. A future general mmap path needs a reviewed safe lifetime mechanism compatible with the
3.10 stable ABI (or a coordinated ABI-floor/design change). Until then, direct bytes are the only
no-copy Rust input proven here.

## Verification at this checkpoint

The following source-tree checks passed for the implementation sequence `39a5656` through
`567562e`:

| Gate | Result |
|---|---|
| Rust unit tests (`cargo test --no-default-features`) | 28 passed |
| Rust formatting and Clippy with warnings denied | passed |
| Private PyO3 foundation tests | 211 passed |
| Native backend, private foundation, and encoded-dispatch tests | 258 passed |
| Complete projector test suite | 1,041 passed |
| Focused Python Ruff and mypy checks | passed |

The focused tests cover Python-oracle parity for named class, role, and object-assertion edges;
both restriction orientations and all four accepted constructors; cardinality discard;
bidirectional projection; n-ary equivalent lexical/expression selection; named intersection/union
operand ordering; mixed named/restriction aggregate emission; duplicate aggregate edges; aggregate
role expansion; class/assertion/domain/range category and cross-product ordering; conflicting
subrole/inverse OWLAPI hash-set visitation and overwrite behavior; named and inverse role operands;
direct/subrole/inverse ordering; direct-assertion non-expansion; every supported skipped
class/object/data-property family and exact counters; aggregate-aware `DisjointClasses` and
`DisjointUnion` state neutrality; plain, typed, and language-tagged literal validation; the distinct
`only_taxonomy` and asserted-taxonomy behaviors; negative named/inverse object and positive/negative
data assertion skips; exact positive-inverse failure details; declarations as non-emitting roots; a
250-axiom single boundary call, a 250-object-assertion output-limit failure, a 250-data-assertion
zero-output call, a 250-disjoint-root zero-output call, a 250-aggregate-operand output-limit failure,
and a 250-restriction/750-expanded-edge limit failure; mixed-family, datatype-IRI, and 20-by-20
cross-product limit failure; non-minimal integer, canonical class/object/data-property/disjoint set,
root-reference, hostile assertion-reference, hostile aggregate arity/type, hostile disjoint
defined-class references, and noncanonical literal-language corruption; valid unsupported exporter,
role-annotation-hash, and remaining out-of-slice shapes;
selected annotation IRI/plain/language/XSD/custom-datatype
values; exact malformed typed rendering; full-RDFS relation rewriting; unsupported properties and
non-class subjects; deterministic annotation category/root order; three duplicate edges preserved
across unannotated, annotated, and nested-annotated assertions; `include_literals=false`; intentional
annotation retention under historical `only_taxonomy`; annotation suppression under
asserted-taxonomy; 250 selected annotations rejected at the edge limit; hostile property, subject,
value, and annotation-set references; aggregate-aware
annotated `HasKey` validation with named/inverse object and named data properties; annotated
`SameIndividual`/`DifferentIndividuals` validation over named and anonymous members; exact
state-neutral counters in normal, `only_taxonomy`, and asserted-taxonomy modes; a 250-root
same/different one-boundary zero-output call; hostile key-property, individual-member,
annotation-set, and anonymous-scalar corruption; sliced and non-bytes exporters; descriptor mismatch;
two order-distinct property chains over the same named/inverse members; chain state neutrality in
normal, `only_taxonomy`, and asserted-taxonomy modes; exact inclusion of ignored chains in the
OWLAPI role-table capacity boundary; a 250-annotated-chain one-boundary zero-output call; hostile
sequence kind, item kind, member type, and nested-chain corruption; annotated-chain hashability,
nested-annotation exclusion from the hash, and state neutrality;
`ObjectOneOf`, inverse-property `ObjectHasValue`, and inverse-property `ObjectHasSelf` ignored
subclasses/class assertions; anonymous OneOf/value/assertion members; exact ignored subclass and
class-assertion partitions in normal, `only_taxonomy`, and asserted-taxonomy modes; a 250-root
one-boundary zero-output call; hostile OneOf member, HasValue property/value, and HasSelf property
references; inverse-property exact cardinality, minimally encoded exact cardinalities, recursively
nested complements and recursively complex exact-cardinality fillers, hostile complement operands
and exact property references, and exact state-neutral behavior for their containing roots;
all six data class-expression constructors over ordered named data properties, canonical integers,
plain/typed/language literals, named datatypes, literal one-of sets, datatype/facet restrictions,
and recursively nested data complements/intersections/unions; exact scalar state neutrality in
normal, `only_taxonomy`, and asserted-taxonomy modes; recursive `DataPropertyRange`,
`DatatypeDefinition`, and SWRL `DataRangeAtom` parity; a 250-root one-boundary data-expression call;
a 200-level iterative data-range call; hostile quantifier sequence, property, literal, datatype,
facet IRI, and facet-set references; non-minimal data exact cardinality; and cyclic recursive
data-range rejection before output;
mixed named/restriction/nonprojecting aggregate equivalence, ignored aggregate subclass and
aggregate/restriction class assertions, direct ignored equivalent selections, expression-aware
disjoint/union/key/data-domain/data-range/datatype-definition skips, and their exact counters in
normal, `only_taxonomy`, and asserted-taxonomy modes; recursively nested aggregate, complement, and
complex-restriction equivalence with direct named and role-expanded sibling output across all three
modes and annotated axioms; recursive-expression state neutrality in subclass, assertion, disjoint,
key, data-domain, and object-domain consumers; separate 200-level iterative aggregate and
complement/restriction one-boundary output calls; same-constructor flattening and cyclic graph
rejection before output; a 250-root one-boundary mixed skipped-family call; and hostile data-domain,
data-range, datatype-definition, and HasKey expression references;
inverse-property Some/All/Min/Max projection on both subclass orientations and aggregate
equivalence; exact underlying-IRI direct/subrole/inverse ordering in normal, `only_taxonomy`, and
asserted-taxonomy modes; inverse-property and recursively complex domain/range state neutrality
alongside a retained named/named pair; 250 inverse-restriction/750-expanded-edge limit failure;
hostile inverse inner, domain class, range property, and range class corruption; and complex
restriction-filler state neutrality;
silent ontology annotations and annotated `SubAnnotationPropertyOf`, `AnnotationPropertyDomain`,
and `AnnotationPropertyRange` scalar parity; anonymous metadata values on ontology,
annotation-property, and selected-annotation roots; exact normal, `only_taxonomy`, and
asserted-taxonomy state neutrality; a 250-root one-boundary zero-output call; hostile sub/super
property, domain/range property and target IRI, annotation-set item, and ontology-root-kind
corruption;
annotated projecting and state-neutral axiom families across normal, `only_taxonomy`, and
asserted-taxonomy modes; exhaustive annotated data-property and disjoint-class skips; a 250-root
annotated-data-assertion one-boundary zero-output call; hostile annotation-set items on subclass,
object range, object characteristic, data assertion, sub-object-property, and inverse-property
roots; exact role overwrite order for IRI, typed, language-tagged, multiple, nested, and
inverse-expression annotation variants; valid-UTF-8 anonymous local-key parity against the encoded
scalar oracle; and preserved whole-call fallback for unhashable anonymous metadata on annotated
sub-object-property, property-chain, and inverse-property roots;
extension-root SWRL parity across all seven atom constructors, variables, named/anonymous
individual arguments, literals, inverse object properties, recursive class/data predicates,
zero-argument built-ins, empty rule sides, and annotated rules; silence and state neutrality in
normal and asserted-taxonomy modes; a 250-rule one-boundary zero-output call; full recursive
class-predicate silence before output; and hostile corruption of every SWRL arity, root kind, rule
body kind, and variable target;
exact anonymous object-assertion source/target numbering against the scalar oracle; anonymous
selected-annotation values and silent anonymous annotation subjects; anonymous positive/negative
data and negative object assertion skips; one shared ID space covering silent axioms and axiom
metadata; explicit exclusion of ontology-annotation and SWRL-only nodes; parity in normal,
historical `only_taxonomy`, and asserted-taxonomy modes; a 250-anonymous-edge one-boundary call with
contiguous IDs; and hostile canonical anonymous-order corruption before output;
bytes-exporter and exact-owner lifetime across the expanded slice; GIL release; concurrent
cancellation; and continued absence of the production encoded feature.

These are local source-tree checks. They do not replace hosted wheels, sanitizers, fuzzing,
licensed corpora, performance thresholds, or the Exact acceptance matrix.

## Acceptance ledger

| WP-P7 requirement | Current truthful state |
|---|---|
| Public descriptor/owner validation | Python adapter is broad; private Rust seam rechecks its narrow direct envelope and descriptor binding |
| Complete Rust projection rules/options | Open; Rust implements only the direct ABox/taxonomy/restriction slice with fully recursive structural class-expression and data-range validation across selected projecting, ignored, skipped, and silent consumers, selected IRI/literal/anonymous class annotations, ontology annotations, annotation-property axioms, metadata on supported axioms, exact axiom-derived anonymous identifiers, named/inverse-property plus named-filler object-restriction emission, named/named projecting or inverse/complex ignored object domains/ranges, exact annotated role-axiom hashes, same-operation named/inverse role expansion, capacity-exact ignored property chains, structurally validated silent SWRL extensions, and validated disjoint/key/individual-identity/object/data-property families; lifecycle reuse and remaining option/surface integration are unsupported |
| Bounded batches without per-row FFI | Proven for one caller-bounded private coarse batch; streaming multi-batch integration remains open |
| Production dispatch and provenance | Open; private kernel is not selected and its counters are not reported by `ProjectionReport` |
| Direct/mmap/overlay/composite support | Exact full bytes direct views only; mmap and segmented families are unsupported |
| Lifetime/GIL/cancel/failure safety | Focused private bytes-path tests pass; full iterator/fork/shutdown/fuzz/sanitizer matrix remains open |
| Zero forbidden-work ledger | Proven only for the private bytes call; no production-path claim |
| Corpus performance/RSS gates | Open |
| Exact shared-stack acceptance | Open for this kernel |
| Wheels/SBOM/platform matrix | Open for this kernel |

## Promotion decision and next work

`auto` and explicit native negotiation remain unchanged. Before advertising
`encoded-structural-compiler-v1`, P7 still needs:

1. complete Rust rule, option, multiplicity, order, diagnostic, error, and lifecycle parity;
2. bounded streaming batches integrated into iterator, sink, digest, artifact, and cancellation
   surfaces without the current whole-batch limitation;
3. safe no-copy direct/mmap/overlay/composite ownership and segment traversal;
4. production provenance wired only after it describes actual Rust work;
5. full oracle/generated/hostile/fuzz/sanitizer/thread/fork/shutdown/platform verification;
6. labelled NCIT, DOID, GO, million-axiom, licensed-corpus, RSS, and copy evidence; and
7. Exact-OM shared-stack and release packaging/SBOM/compatibility review.

No compatibility, completeness, or performance claim is inferred from this private checkpoint.
