# P7 encoded-native compiler checkpoint

Date: 2026-07-20. Projector implementation through `2fca297`. pyOWLCore candidate revision:
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
- named-property/named-filler `ObjectSomeValuesFrom`, `ObjectAllValuesFrom`,
  `ObjectMinCardinality`, and `ObjectMaxCardinality` on either side of a named class in
  `SubClassOf`; min/max integers are minimally encoded and validated but semantically discarded;
- scalar-selected `ObjectIntersectionOf`/`ObjectUnionOf` expressions in `EquivalentClasses`, with
  flat named-class and supported restriction operands; named operands emit taxonomy edges while
  restriction operands use the same direct/subrole/inverse expansion as subclass restrictions;
- exact scalar aggregate ordering: named expressions/operands use UTF-8 lexical IRI order,
  aggregate and restriction kinds use pinned OWLAPI type order, same-kind restrictions retain
  frozen node order, and duplicate projected edges remain duplicated;
- `DisjointClasses` and named-defined-class `DisjointUnion` roots over the same bounded named,
  aggregate, and restriction expression envelope, fully validated and counted but skipped without
  output or role-state mutation;
- paired named `ObjectPropertyDomain`/`ObjectPropertyRange` roots, producing the reference
  domain-by-range products in UTF-8 property order;
- named-property `ObjectPropertyAssertion` roots over named individuals, producing direct triples
  without subrole or inverse expansion;
- named-or-inverse `NegativeObjectPropertyAssertion` roots over named individuals, fully validated,
  counted, and silently skipped as in the scalar profile;
- positive inverse object-property assertions mapped to the pinned
  `UnsupportedAxiomShapeError` with `ObjectInverseOf`/`java.lang.ClassCastException` details;
- unannotated named-or-inverse `SubObjectPropertyOf` and `InverseObjectProperties` roots, projected
  through the underlying named IRI while their distinct OWLAPI expression hashes control visitation;
- exact OWLAPI hash-table capacity/bucket/spread/canonical-tie traversal, including the historical
  subrole sibling overwrite and last-visited inverse overwrite;
- direct/subrole/inverse expansion, in that order, for subclass/aggregate restrictions and
  domain/range edges only; direct object-property assertions deliberately remain unexpanded;
- named-or-inverse canonical `EquivalentObjectProperties` and `DisjointObjectProperties` sets plus
  `FunctionalObjectProperty`, `InverseFunctionalObjectProperty`, `ReflexiveObjectProperty`,
  `IrreflexiveObjectProperty`, `SymmetricObjectProperty`, `AsymmetricObjectProperty`, and
  `TransitiveObjectProperty`, fully validated, counted, skipped, and state-neutral;
- named `SubDataPropertyOf`, canonical named `EquivalentDataProperties` and
  `DisjointDataProperties` sets, named-class `DataPropertyDomain`, named-datatype
  `DataPropertyRange`, named `FunctionalDataProperty`, and named-to-named `DatatypeDefinition`,
  fully validated, counted, skipped, and unable to mutate object-role state;
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
  exact named-property and IRI/literal value envelope but ignored semantically, so structurally
  distinct annotated assertions preserve duplicate projected edges;
- allocation-free repeated class-entity scans for annotation subject membership, avoiding an
  ontology-sized class-signature index while retaining deterministic canonical root order;
- the supported reference category order (`SubClassOf`, `EquivalentClasses`, selected
  `AnnotationAssertion`, `ClassAssertion`, `ObjectPropertyAssertion`, then domain/range), with the
  first two equivalent members selected by the pinned expression order;
- forward `http://subclassof`, optional reverse `http://superclassof`, `http://type`, and direct
  named object-property edges;
- the historical `only_taxonomy` behavior: restriction edges are suppressed while named
  equivalence, enabled selected annotations, class assertions, positive object assertions, skipped
  object/data property families, and role-expanded object domain/range products remain enabled;
- an asserted-taxonomy mode that preflights the complete supported input but emits only direct
  named `SubClassOf`, with no annotation leakage; and
- one caller-bounded coarse output batch, with no per-root or per-edge Python call.

Any other valid segment, exporter, constructor, ontology annotation, annotation on a non-annotation
assertion, nested/unsupported aggregate operand, complex/unsupported restriction or data range,
object-property chain, inverse property in a restriction or object domain/range axiom, or anonymous
individual (including an annotation subject/value) is rejected as unsupported before output. N-ary
equivalents beyond the selected first two, non-selected supported aggregate expressions, complete
supported disjoint/property sets, every supported literal and annotation node, and unpaired object
domain/range roots are still fully validated. Malformed supported columns fail closed.
Same-operation isolated role expansion is proven; retained Scala-instance role-state reuse is not
part of this one-shot seam. This is kernel version 9 of a private foundation, not the complete
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
   node/item/scalar references, canonical-set ordering/uniqueness, and arena coverage;
2. preflights every supported constructor, aggregate member/type/order envelope, UTF-8 IRI/literal
   field, entity kind, literal language/datatype relationship, minimally encoded cardinality,
   empty metadata sets on the previously supported axiom families, typed/nested annotation sets on
   `AnnotationAssertion`, root kind/tag pairing, output/cross-product count, and IRI/output limit;
3. builds exact-capacity borrowed-IRI role rows/indexes only after whole-view validation, computes
   all expanded edge counts, and allocates output only after the complete preflight succeeds; and
4. returns one coarse list of edge tuples plus roots, nodes, declarations, subclasses/restriction
   subclasses, equivalents/aggregate equivalents, disjoint class/union roots, class assertions,
   positive/negative object-property assertions, every supported object/data-property axiom family,
   annotation assertions, selected annotation edges, non-string literal renderings, skipped axioms,
   object domain/range roots and products, role-expansion edges, edges, and retained-buffer-byte
   counters.

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
the boundary as distinct typed failures; no partial batch is returned. The private v9 ABI returns
its forty-two counters as an explicitly constructed Python tuple because PyO3's automatic tuple
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
`2fca297`:

| Gate | Result |
|---|---|
| Rust unit tests (`cargo test --no-default-features`) | 21 passed |
| Rust formatting and Clippy with warnings denied | passed |
| Private PyO3 foundation tests | 83 passed |
| Native backend, private foundation, and encoded-dispatch tests | 130 passed |
| Complete projector test suite | 913 passed |
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
property-chain, inverse
restriction/domain, exact-cardinality, restriction-pair, nested/unsupported aggregate, complex data
range, anonymous, and annotated shapes; selected annotation IRI/plain/language/XSD/custom-datatype
values; exact malformed typed rendering; full-RDFS relation rewriting; unsupported properties and
non-class subjects; deterministic annotation category/root order; three duplicate edges preserved
across unannotated, annotated, and nested-annotated assertions; `include_literals=false`; intentional
annotation retention under historical `only_taxonomy`; annotation suppression under
asserted-taxonomy; 250 selected annotations rejected at the edge limit; hostile property, subject,
value, and annotation-set references; non-renderable anonymous annotation values; sliced and
non-bytes exporters; descriptor mismatch;
bytes-exporter and exact-owner lifetime across the expanded slice; GIL release; concurrent
cancellation; and continued absence of the production encoded feature.

These are local source-tree checks. They do not replace hosted wheels, sanitizers, fuzzing,
licensed corpora, performance thresholds, or the Exact acceptance matrix.

## Acceptance ledger

| WP-P7 requirement | Current truthful state |
|---|---|
| Public descriptor/owner validation | Python adapter is broad; private Rust seam rechecks its narrow direct envelope and descriptor binding |
| Complete Rust projection rules/options | Open; Rust implements only the direct named/aggregate class and ABox slice, selected IRI/literal class annotations, named-property/filler restrictions and object domains/ranges, same-operation named/inverse role expansion, and validated disjoint/object/data-property skipped families; ontology/remaining annotation families, lifecycle reuse, and remaining constructors are unsupported |
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
