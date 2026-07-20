# P7 encoded-native compiler checkpoint

Date: 2026-07-20. Projector implementation through `1498fca`. pyOWLCore candidate revision:
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
- IRI and Entity nodes plus silent unannotated Declaration roots;
- unannotated named-to-named `SubClassOf`, named and supported aggregate `EquivalentClasses`, and
  named `ClassAssertion` roots;
- named-or-inverse-property/named-filler `ObjectSomeValuesFrom`, `ObjectAllValuesFrom`,
  `ObjectMinCardinality`, and `ObjectMaxCardinality` on either side of a named class in
  `SubClassOf`; inverse expressions project through their underlying named IRI, and min/max
  integers are minimally encoded and validated but semantically discarded;
- scalar-nonprojecting `ObjectOneOf`, `ObjectHasValue`, and `ObjectHasSelf` expressions in bounded
  `SubClassOf` and `ClassAssertion` roots: canonical named/anonymous individual sets or values and
  named/inverse properties are fully validated, while the containing root is counted and ignored
  without output or role-state mutation;
- scalar-nonprojecting `ObjectExactCardinality` with a minimally encoded cardinality,
  named/inverse property, and named-class filler in the same bounded root positions, likewise
  validated and ignored without output or role-state mutation;
- non-recursive `ObjectComplementOf` over a named class, supported named-property object
  restriction, or one supported nonprojecting object/data class-expression operand in those
  positions, with the complete operand validated before the containing root is counted and ignored;
- all six scalar-nonprojecting data class expressions in bounded `SubClassOf` and `ClassAssertion`
  roots: nonempty ordered named-property sequences for `DataSomeValuesFrom`/`DataAllValuesFrom`,
  named-property/literal `DataHasValue`, and minimally encoded cardinality plus named property for
  `DataMinCardinality`, `DataMaxCardinality`, and `DataExactCardinality`;
- bounded data-range fillers for those expressions: named datatypes, canonical nonempty literal
  `DataOneOf`, named-datatype `DatatypeRestriction` with canonical nonempty facet restrictions,
  non-recursive `DataComplementOf`, and flat `DataIntersectionOf`/`DataUnionOf` over atomic or
  non-recursive operands, all validated and ignored without output or role-state mutation;
- named-class `ClassAssertion` roots over anonymous individuals follow the same scalar ignored-shape
  contract, while named-class/named-individual assertions retain their existing `http://type` edge;
- scalar-selected `ObjectIntersectionOf`/`ObjectUnionOf` expressions in `EquivalentClasses`, with
  flat named-class, supported restriction, and bounded nonprojecting operands; named operands emit
  taxonomy edges, restriction operands use the same direct/subrole/inverse expansion as subclass
  restrictions, and nonprojecting operands are fully validated and ignored;
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
- inverse-property or bounded-complex object domain/range roots are fully validated and counted but
  ignored, so mixed calls exclude them from property pairing and role expansion exactly as the
  scalar profile does;
- named-property `ObjectPropertyAssertion` roots over named individuals, producing direct triples
  without subrole or inverse expansion;
- named-or-inverse `NegativeObjectPropertyAssertion` roots over named individuals, fully validated,
  counted, and silently skipped as in the scalar profile;
- positive inverse object-property assertions mapped to the pinned
  `UnsupportedAxiomShapeError` with `ObjectInverseOf`/`java.lang.ClassCastException` details;
- unannotated named-or-inverse `SubObjectPropertyOf` and `InverseObjectProperties` roots, projected
  through the underlying named IRI while their distinct OWLAPI expression hashes control visitation;
- unannotated `SubObjectPropertyOf` roots whose sub-property is an ordered, minimum-two
  `ObjectPropertyChain` of named/inverse members: the complete sequence and named/inverse
  super-property are validated and counted, but the chain is an exact scalar ignored shape that
  cannot mutate role state;
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
  `DisjointDataProperties` sets, bounded-class-expression `DataPropertyDomain`, bounded-data-range
  `DataPropertyRange`, named `FunctionalDataProperty`, and named-to-bounded-range
  `DatatypeDefinition`, fully validated, counted, skipped, and unable to mutate object-role state;
- named-source `DataPropertyAssertion` and `NegativeDataPropertyAssertion` roots with plain, typed,
  or language-tagged literals, fully validated and counted but always skipped without an edge;
- exact three-field literal validation, including UTF-8 lexical/language text, a named datatype,
  canonical absent-language fields, lowercase nonempty language tags, and the required
  `rdf:PlainLiteral` language/datatype relationship, performed in place without cloning literal
  text;
- selected class `AnnotationAssertion` roots when `include_literals` is enabled: IRI subjects must
  occur in the retained class signature, properties must match the exact 39-entry scalar
  whitelist, full RDFS label/comment IRIs rewrite to their `rdfs:` spellings, and IRI-valued or
  literal-valued objects emit one edge;
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
- allocation-free repeated class-entity scans for annotation subject membership, avoiding an
  ontology-sized class-signature index while retaining deterministic canonical root order;
- `HasKey` roots over the same bounded class-expression envelope, canonical named/inverse object
  properties, canonical named data properties, and supported annotation metadata, fully validated
  and counted but skipped without output or role-state mutation;
- `SameIndividual` and `DifferentIndividuals` roots over canonical sets of named or anonymous
  individuals plus supported annotation metadata, fully validated and counted but likewise
  state-neutral scalar skips; anonymous identifiers retain exact bytes32 document scopes and
  nonempty local keys without text decoding or allocation;
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

Any other valid segment, exporter, constructor, annotation on an axiom outside the explicitly
supported metadata families, nested/unsupported class aggregate operand, complex/unsupported
object restriction, recursive object complement, nested data-range complement/aggregate, complex
object exact-cardinality filler, or other nonprojecting expression outside the bounded root
positions, annotated or structurally unsupported object-property chain, or anonymous individual
outside the supported ontology/axiom-metadata, `SameIndividual`/`DifferentIndividuals`,
OneOf/HasValue, and ignored-ClassAssertion positions (including the subject or projected value of
an `AnnotationAssertion`) is rejected as unsupported before output. N-ary
equivalents beyond the selected first two, non-selected supported aggregate expressions, complete
supported disjoint/property sets, every supported literal and annotation node, and unpaired object
domain/range roots are still fully validated. Malformed supported columns fail closed.
Same-operation isolated role expansion is proven; retained Scala-instance role-state reuse is not
part of this one-shot seam. This is kernel version 17 of a private foundation, not the complete
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
   empty metadata sets on the previously supported axiom families, typed/nested annotation sets
   with IRI/literal/anonymous values on `AnnotationAssertion`, skipped logical,
   ontology-annotation, and annotation-property roots, bounded nonprojecting object/data class
   expressions and data ranges,
   anonymous-individual bytes32/nonempty-key invariants, root kind/tag pairing,
   output/cross-product count, and IRI/output limit;
3. builds exact-capacity borrowed-IRI role rows/indexes only after whole-view validation, computes
   all expanded edge counts, and allocates output only after the complete preflight succeeds; and
4. returns one coarse list of edge tuples plus roots, nodes, declarations, subclasses plus their
   restriction and ignored partitions, equivalents/aggregate equivalents,
   disjoint class/union roots, keys, same/different individual roots, class assertions and their
   ignored partition, positive/negative object-property assertions, sub-object properties and
   ignored property chains, every supported object/data/annotation-property axiom family,
   ontology annotations, annotation assertions, selected annotation edges, non-string literal
   renderings, skipped axioms, object domain/range roots and products, role-expansion edges, edges,
   and retained-buffer-byte counters.

The Python wrapper exposes an exact call ledger for this kernel: eleven input buffers, eleven
detached/zero-copy buffers, zero indexed buffers, zero staging/structural copy bytes, zero per-row
FFI calls, one native boundary call, and GIL release. These counters describe only this private
call. They are not currently attached to `ProjectionReport`, because the production projector
does not dispatch to the private kernel. Aggregate and domain/range counting/ordering use repeated
borrowed scans; selected annotation membership likewise uses repeated borrowed class-entity scans.
The only structural indexes are the projector-private role rows and subrole/inverse maps required
by the pinned rules; they borrow retained IRI text and are reserved to exact root-derived
capacities.

The compiler is one-shot. Atomic idle/running/finished/cancelled/failed transitions allow another
Python thread to cancel detached work. A cancellation racing with successful compilation discards
the result. Unsupported, malformed, pinned reference, resource, cancelled, and panic outcomes cross
the boundary as distinct typed failures; no partial batch is returned. The private v17 ABI returns
its fifty-two counters as an explicitly constructed Python tuple because PyO3's automatic tuple
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
`1498fca`:

| Gate | Result |
|---|---|
| Rust unit tests (`cargo test --no-default-features`) | 28 passed |
| Rust formatting and Clippy with warnings denied | passed |
| Private PyO3 foundation tests | 156 passed |
| Native backend, private foundation, and encoded-dispatch tests | 203 passed |
| Complete projector test suite | 986 passed |
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
defined-class references, and noncanonical literal-language corruption; valid unsupported
annotated property-chain, complex restriction filler, restriction-pair, nested/unsupported class
aggregate, recursive data range, anonymous assertion/projected-annotation, and annotated shapes;
selected annotation IRI/plain/language/XSD/custom-datatype
values; exact malformed typed rendering; full-RDFS relation rewriting; unsupported properties and
non-class subjects; deterministic annotation category/root order; three duplicate edges preserved
across unannotated, annotated, and nested-annotated assertions; `include_literals=false`; intentional
annotation retention under historical `only_taxonomy`; annotation suppression under
asserted-taxonomy; 250 selected annotations rejected at the edge limit; hostile property, subject,
value, and annotation-set references; non-renderable anonymous annotation values; aggregate-aware
annotated `HasKey` validation with named/inverse object and named data properties; annotated
`SameIndividual`/`DifferentIndividuals` validation over named and anonymous members; exact
state-neutral counters in normal, `only_taxonomy`, and asserted-taxonomy modes; a 250-root
same/different one-boundary zero-output call; hostile key-property, individual-member,
annotation-set, and anonymous-scalar corruption; sliced and non-bytes exporters; descriptor mismatch;
two order-distinct property chains over the same named/inverse members; chain state neutrality in
normal, `only_taxonomy`, and asserted-taxonomy modes; exact inclusion of ignored chains in the
OWLAPI role-table capacity boundary; a 250-chain one-boundary zero-output call; hostile sequence
kind, item kind, member type, and nested-chain corruption; annotated-chain whole-root fallback;
`ObjectOneOf`, inverse-property `ObjectHasValue`, and inverse-property `ObjectHasSelf` ignored
subclasses/class assertions; anonymous OneOf/value/assertion members; exact ignored subclass and
class-assertion partitions in normal, `only_taxonomy`, and asserted-taxonomy modes; a 250-root
one-boundary zero-output call; hostile OneOf member, HasValue property/value, and HasSelf property
references; inverse-property exact cardinality, minimally encoded exact cardinalities, bounded
complements over named and supported nonprojecting operands, hostile complement operands and exact
property references, and recursive complement or complex exact-cardinality whole-root fallback;
all six data class-expression constructors over ordered named data properties, canonical integers,
plain/typed/language literals, named datatypes, literal one-of sets, datatype/facet restrictions,
non-recursive data complements, and flat data intersections/unions; exact scalar state neutrality in
normal, `only_taxonomy`, and asserted-taxonomy modes; a 250-root one-boundary data-expression call;
hostile quantifier sequence, property, literal, datatype, facet IRI, and facet-set references;
non-minimal data exact cardinality; and recursive data-complement/nested-aggregate whole-call
fallback;
mixed named/restriction/nonprojecting aggregate equivalence, ignored aggregate subclass and
aggregate/restriction class assertions, direct ignored equivalent selections, expression-aware
disjoint/union/key/data-domain/data-range/datatype-definition skips, and their exact counters in
normal, `only_taxonomy`, and asserted-taxonomy modes; a 250-root one-boundary mixed skipped-family
call; hostile data-domain, data-range, datatype-definition, and HasKey expression references; and
nested aggregate/complement whole-call fallback across subclass, assertion, disjoint, key, data
property, and datatype-definition roots;
inverse-property Some/All/Min/Max projection on both subclass orientations and aggregate
equivalence; exact underlying-IRI direct/subrole/inverse ordering in normal, `only_taxonomy`, and
asserted-taxonomy modes; inverse-property and bounded-complex domain/range state neutrality
alongside a retained named/named pair; 250 inverse-restriction/750-expanded-edge limit failure;
hostile inverse inner, domain class, range property, and range class corruption; and
inverse-complex-filler and nested-domain whole-call fallback;
silent ontology annotations and annotated `SubAnnotationPropertyOf`, `AnnotationPropertyDomain`,
and `AnnotationPropertyRange` scalar parity; anonymous metadata values on ontology,
annotation-property, and selected-annotation roots; exact normal, `only_taxonomy`, and
asserted-taxonomy state neutrality; a 250-root one-boundary zero-output call; hostile sub/super
property, domain/range property and target IRI, annotation-set item, and ontology-root-kind
corruption; and preserved whole-call fallback for an anonymous projected annotation value;
bytes-exporter and exact-owner lifetime across the expanded slice; GIL release; concurrent
cancellation; and continued absence of the production encoded feature.

These are local source-tree checks. They do not replace hosted wheels, sanitizers, fuzzing,
licensed corpora, performance thresholds, or the Exact acceptance matrix.

## Acceptance ledger

| WP-P7 requirement | Current truthful state |
|---|---|
| Public descriptor/owner validation | Python adapter is broad; private Rust seam rechecks its narrow direct envelope and descriptor binding |
| Complete Rust projection rules/options | Open; Rust implements only the direct bounded class-expression and ABox slice, bounded object/data nonprojecting expressions and data ranges across selected ignored/skipped axiom families, selected IRI/literal class annotations, ontology annotations, annotation-property axioms, named/inverse-property plus named-filler object restrictions, named/named projecting or inverse/complex ignored object domains/ranges, same-operation named/inverse role expansion, capacity-exact ignored property chains, and validated disjoint/key/individual-identity/object/data-property skipped families; recursive/remaining class expressions and data ranges, remaining axiom-annotation families, lifecycle reuse, and remaining constructors are unsupported |
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
