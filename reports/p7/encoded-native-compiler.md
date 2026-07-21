# P7 encoded-native compiler checkpoint

Date: 2026-07-21. Projector implementation through `86b055d`. pyOWLCore candidate revision:
`6750aa0`. Exact-OM integration revision: `fe46141`.

## Outcome

P7 is **not accepted, publicly integrated, advertised, or promoted**. Production projection still
uses the Python semantic compiler. When the native backend is selected through a public API, that
Python compiler materializes projector `Edge` values and the established Rust
`EdgeBatchProcessor` receives owned string tuples only to apply edge policy. The broad decoder in
`encoded_compiler.py` is Python code; its tests and counter ledger are not evidence of a Rust
ontology compiler.

The extension feature ledger remains exactly `abi3-py310` and `bounded-batches`.
`encoded-structural-compiler-v1` is absent, so normal backend negotiation cannot select an
encoded-native production compiler.

An explicitly private candidate, `Projector._iter_native_encoded_edges(...)`, now exercises one
production-adjacent iterator integration without changing that ledger. It privately requests the
unadvertised structural-columns candidate and selects Rust only for isolated or explicitly
stateful direct views whose
roots are declarations; direct named or supported named-filler Some/All/Min/Max restriction
`SubClassOf`, plus structurally validated nonprojecting `SubClassOf`; named-pair, aggregate, or
nonprojecting `EquivalentClasses`; named or structurally validated nonprojecting `ClassAssertion`;
and named-property `ObjectPropertyAssertion` axioms over named or anonymous individuals; plus
arbitrary named-property object domain/range products, unpaired named roots, and inverse/complex
ignored roots. Direct named/inverse
`SubObjectPropertyOf` and
`InverseObjectProperties` roots may build same-call isolated role maps; property chains remain on
the exact ignored-shape path without mutating those maps, and the native `role_expansion_edges`
count participates in the exact raw-edge proof. Validated `AnnotationAssertion` roots remain silent
when `include_literals=False`; when enabled, the exact difference between total roots and selected
native edges is admitted as scalar ignored shapes with a grouped `AnnotationAssertion` diagnostic.
Selected anonymous annotation values and anonymous assertion operands use the kernel's exact
axiom-derived blank-ID order, including anonymous axiom metadata. Native ignored-subclass and
ignored-class-assertion partitions are admitted with exact constructor-grouped diagnostics and
counts. Kernel v30 retains the v29 aggregate-aware equivalence base-edge and ignored-shape counts, so
aggregate role expansions and operand-level ignores are admitted without inferring either from the
total output. It also partitions projecting and ignored object domain/range roots; the adapter
bounds the native product count by the projectable cross-product while preserving exact
multi-property matching, unpaired silence, role expansion, and grouped diagnostics. The native
non-string rendering count produces the
exact grouped warning diagnostic. Ontology annotations and SWRL rules are admitted as validated
silent roots. Every supported scalar-skipped family is admitted only when the exact sum of its 27
per-constructor native counters equals the native skipped total; the adapter synthesizes the same
constructor-sorted grouped diagnostics as the scalar compiler. Under historical `only_taxonomy`,
admitted restrictions are suppressed and publish
the same grouped ignored-shape diagnostic/count as the scalar compiler.
Candidate-unavailable inputs and successfully preflighted views outside that narrow adapter subset
select the complete scalar-native compiler for the whole call before an edge is yielded; malformed,
resource, cancellation, and pinned-reference failures retain their typed error paths. A successful
candidate drain feeds the existing encounter/canonical and duplicate policy machinery, honors the
public edge limit and cancellation token, and records exact encoded-buffer and native-batch
counters in its ordinary `ProjectionReport`. The normal `Projector.iter_edges(...)`, sink, digest,
and artifact entry points cannot select this path.

The load-excluded benchmark now has a separately labelled
`private-native-candidate` execution surface. It hashes every complete ingestion-counter and
post-view core-operation ledger, binds the runner plus loaded package/native artifacts by SHA-256,
records distribution `RECORD` hashes, native features and kernel version, and accepts only full
40-character projector/core revisions. `private_candidate_boundary_ready` describes only the
hidden counter contract. The stronger `private_candidate_evidence_ready` additionally requires
installed distribution payloads and both revisions. Neither field can set public
`acceptance_ready`; private mode is rejected if combined with the public
`--require-encoded-native` gate. The harness also emits the direct-bytes-only,
unwired-public-surfaces, and scalar-public-lifecycle blockers in every result. Kernel v32 removes
the former whole-output-vector blocker for the hidden iterator and binds that fact to compiled,
vector-backed, and peak-buffered edge counters.
No installed-wheel or corpus result is inferred from the source-tree harness checks below.

This checkpoint adds a real but deliberately private Rust foundation. It proves one useful
semantic slice across the actual PyO3 boundary without changing the production claim:

- one canonical direct structural-columns v1 segment;
- exact full immutable-`bytes` exporters for all eleven columns, or the canonical ordered
  eleven-column packed layout over one exact immutable-`bytes` arena;
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
- an optional private retained role-state handle for ordered Scala-instance compatibility calls:
  it copies out only normalized subrole/inverse IRI strings, reuses them across otherwise
  independent one-shot compiler/view owners, applies the next view's exact OWLAPI visitation and
  overwrite order before emission, and rejects overlapping use for the complete PyO3 call; the
  hidden Projector iterator now owns this handle under its existing non-concurrent lifecycle lock,
  mirrors successful maps into its scalar-compatible shadow, and never re-enters native state
  after a whole-operation scalar selection;
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
- the original one-call coarse output plus a private iterator/sink session that drains exact-order
  caller-bounded batches with one PyO3 call per batch and no per-root or per-edge call inside a
  batch; and
- one hidden iterator adapter that admits only the named taxonomy, supported direct
  named-filler restriction, pair/aggregate/nonprojecting equivalence, class-assertion, and direct
  object-property-assertion
  subset plus direct named/inverse role maps and exactly partitioned arbitrary domain/range roots,
  closes a
  declined session before scalar fallback, routes accepted batches through the existing streaming
  policy, admits anonymous positive-assertion operands, nonprojecting subclass/class-assertion
  shapes, selected or exactly partitioned annotation roots including anonymous values/metadata,
  ignored property chains, silent
  ontology annotations and SWRL rules, and all 27
  exactly counted scalar-skipped axiom constructors; synthesizes exact grouped ignored-shape,
  non-string-rendering, and skipped-axiom diagnostics; and publishes exact
  batch/copy/materialization provenance after complete
  consumption. Public entry points still keep
  `compatibility_state="scala-instance"` on the scalar compiler; only the explicitly hidden
  iterator reaches this native lifecycle checkpoint.

Any other valid segment, exporter, constructor, nonprojecting expression outside the supported root
positions, or structurally unsupported object-property chain is rejected as unsupported before
output. N-ary
equivalents beyond the selected first two, non-selected supported aggregate expressions, complete
supported disjoint/property sets, and every supported literal and annotation node are still fully
validated. A role annotation whose anonymous local-key bytes are
not valid UTF-8 remains an exact whole-call fallback because the scalar hash path raises while
encoding its surrogateescaped value. Malformed supported columns fail closed.
Same-operation isolated role expansion and ordered retained role-state reuse across supported
direct views are proven. The retained handle remains a private seam, but the hidden Projector
iterator binds it to the same lock, invocation history, and ingestion provenance used by
`compatibility_state="scala-instance"`. A scalar-compatible shadow supports an exact one-way
fallback transition; no public lifecycle or dispatch claim follows from it. This is kernel version
31 of a private foundation, not the complete compiler described by WP-P7.

## What the private kernel actually does

`EncodedDirectCompiler` receives the already validated public encoded view, its exact retained
owner, and the descriptor digest bound by the Python adapter. Its constructor rechecks the frozen
schema/model version, descriptor binding, direct-segment envelope, exact buffer names, memoryview
readonly/shape metadata, and exporter coverage. It retains the encoded view, owner, and one owned
reference to each immutable bytes exporter.

`compile_batch()` and the private `iter_batches()` preparation path lend those stable byte slices
to the Python-free Rust kernel and release the GIL with `Python::detach`. An optional
`EncodedDirectRoleState` handle is independent of every view owner. Its mutex/atomic use guard
spans validation, detached compilation, statistics construction, output-session installation, and
final compiler-state publication, so a second overlapping call is rejected instead of raced. Rust
then:

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
3. builds a compact axiom-derived anonymous-node index and exact-capacity role rows/indexes only
   after whole-view validation; isolated calls keep current-view IRIs borrowed, while explicit
   stateful calls clone only the prior/current normalized role maps, enforce the current IRI limit,
   and commit Scala-compatible state only after every output-limit, cancellation, kernel-allocation,
   and output-count check succeeds; it computes all expanded edge counts before emission, while the
   legacy coarse call allocates its output only after complete preflight; and
4. either returns one coarse list or, for the hidden iterator, dry-runs the exact ordered emission
   one edge at a time and installs a resumable cursor behind a private caller-bounded drain,
   together with roots, nodes, axiom-derived anonymous individuals,
   declarations, subclasses plus their
   restriction and ignored partitions, equivalents/aggregate equivalents,
   disjoint class/union roots, keys, same/different individual roots, class assertions and their
   ignored partition, positive/negative object-property assertions, sub-object properties and
   ignored property chains, every supported object/data/annotation-property axiom family,
   ontology annotations, SWRL rules, annotation assertions, selected annotation edges, non-string
   literal renderings, skipped axioms, object domain/range roots and products, role-expansion edges,
   edges, and retained-buffer-byte counters.

The Python wrapper exposes an exact call ledger for the coarse call: eleven input buffers, eleven
detached/zero-copy buffers, zero indexed buffers, zero staging/structural copy bytes, zero per-row
FFI calls, one native boundary call, and GIL release. The private batch wrapper separately reports
the configured bound, compiled-plus-drain boundary calls, nonempty edge batches, published edges,
and zero per-row FFI calls. Each `next_batch()` first allocates every Python tuple/list object for
that bounded slice and advances the native cursor only after construction succeeds. The hidden
named-edge iterator attaches the corresponding encoded-buffer count/bytes, detached and
zero-copy buffer count, configured batch bound, compile-plus-drain boundary calls, nonempty batch
count, complete compiled-edge count, zero vector-backed output edges, largest bounded native batch,
and zero forbidden-work values to `ProjectionReport.provenance.ingestion`. These counters describe
only explicitly private candidate calls; public production dispatch still does not select the
kernel. Aggregate and domain/range
counting/ordering use repeated
borrowed scans; selected annotation membership likewise uses repeated borrowed class-entity scans.
When anonymous nodes exist, one bit-packed reachability vector, depth-bounded traversal stack, and
exact-sized sorted node-ID vector reproduce the scalar axiom-derived identifier space. When
recursive data-range or recursively owned class-expression nodes exist, compact color vectors and
grow-checked iterative event stacks validate arbitrary nesting through aggregates, complements,
object-restriction fillers, exact-cardinality fillers, and data ranges, rejecting cycles without
using the native call stack. The other
structural indexes are the projector-private role rows and subrole/inverse maps required by the
pinned rules; isolated calls borrow retained IRI text, while stateful calls retain only owned role
IRI strings across owners, with every vector grown through checked allocation.

Each compiler handle is one-shot; the optional role-state handle is reusable. Atomic
idle/running/finished/cancelled/failed transitions allow another Python thread to cancel detached
work. A cancellation racing with successful compilation discards the result. Reusable role state
commits only with a successfully prepared native output and is released from its use guard on
success, rejection, resource failure, cancellation, or panic. Closing, cancelling, collecting, or
encountering a sink exception clears the remaining cursor; a close racing with a detached drain
discards its unpublished bounded batch and releases the retained view/owner when the wrapper is its
last reference. Unsupported, malformed, pinned reference,
resource, cancelled, and panic outcomes cross the boundary as distinct typed failures; no output
session is published by a failed compile. The private v26 ABI returns
its fifty-eight counters as an explicitly constructed Python tuple because PyO3's automatic tuple
conversion is bounded below that arity.

The hidden batch seam now bounds native projected-output storage as well as Python transfer. Its
cursor clones only small transaction state, builds at most the configured batch with the GIL
detached and no held output mutex, then commits after Python list construction. Preparation still
performs a full bounded-memory dry emission so malformed/count-divergent input cannot publish a
partial session, and the legacy private coarse call still materializes one list. The cursor is
connected only to the hidden named-edge iterator, which reuses the canonical spill machinery; it
is not connected to public `Projector.iter_edges`, protocol sink, digest, or artifact publication.
It therefore closes the private whole-output-vector gap without satisfying P7 deliverable 3 or
the production memory acceptance criterion.

## No-copy boundary and exact blocker

PyO3 0.28.3 gates its safe generic `PyBuffer` API out at the project's `abi3-py310` floor. The
private kernel therefore accepts exact memoryviews that cover an entire immutable `bytes` exporter
and one additional exact pattern emitted by the native provider: all eleven schema-order columns
must share one exact immutable `bytes` arena, their lengths must sum to the complete arena, and each
view's content must equal its canonical contiguous schema-order range. Rust retains the immutable
arena and records the verified ranges; detached compilation borrows those ranges without an
ontology-sized input copy.

Arbitrary sliced exporters, readonly bytearrays, mmap-backed views, gaps, overlaps whose content
differs from the canonical range, reordered columns, and other otherwise valid public buffer
providers are reported as unsupported. The kernel does not copy them and does not count them as
detached. A future general mmap path needs a reviewed safe lifetime mechanism compatible with the
3.10 stable ABI (or a coordinated ABI-floor/design change). Until then, exact full bytes and the
canonical packed direct-bytes arena are the only no-copy Rust inputs proven here.

## Installed-wheel private checkpoint

An isolated CPython 3.12 environment installed exact wheels built from projector revision
`cedfc9134d34c713eab9290dfc263c443c778805` and pyOWLCore revision
`6750aa0d9a9fc50c0d6931f7ac8f6310623bc7cf`. The projector wheel SHA-256 is
`f73fde9d6bd6999de4a048d55749b3cd97948bdf3b718508516b86bb5ccefb38`; its installed
kernel-v29 extension SHA-256 is
`a24678191942747fd777410cb936e68c9fd2d644559fe5de737e6fd656a1eb7c`. The core wheel
SHA-256 is `a9f9f984309907a1c678ace589b8e039e036767cbd90adcc617c68334356a2fd`.

Both Python-provider and native-provider loads passed the private installed-evidence gate over the
697-byte, 18-axiom kitchen-sink fixture. Each of seven measured repetitions produced 11 edges with
SHA-256 `e82ae3c53c44bfe7b6963ff34e6d6edc9cb7e5156fc7a03451d596db58ffd332`,
11 detached zero-copy buffers, 2,378 encoded bytes, four caller-bounded edge batches, five native
boundary calls including compilation, zero staging/copy/materialization/codec/parser/resolver
work, and a released GIL. Both lanes set `private_candidate_boundary_ready` and
`private_candidate_evidence_ready`; both necessarily leave public `acceptance_ready=false`.

This is checkpoint-only evidence, not an acceptance or performance result. The fixture is tiny,
the host is not approved release infrastructure, and the projector build used wheel 0.47.0 with
`--no-isolation --skip-dependency-check` while `pyproject.toml` pins wheel 0.46.3. The complete
artifact, counter, timing-vector, pre-fix fallback, and limitation record is committed as
[`installed-private-checkpoint.json`](evidence/installed-private-checkpoint.json).

## Imported-annotation provenance guard

Revisions `50ad5ab61196fa5504cb65469a3446ea5c35a286` and
`a71dca0d261366db6faaa25d26673bd38a146610` close a hidden-candidate correctness hole at the
private adapter boundary. The Rust kernel compiles a closure-scoped direct table, while the scalar
profile exposes selected annotation assertions only from the root ontology. A bounded borrowed
root-ID/node-tag scan detects whether annotation provenance is relevant before native compilation.
When it is, the adapter requests and validates the root-scoped table, requires that table to satisfy
the same exact full-`bytes` or canonical packed-arena contract, and compares all eleven borrowed
columns without copying. A byte-identical selection remains native; an unavailable, non-direct,
sliced, mmap-backed, segmented, or different selection selects one whole-operation scalar compiler
before closure compilation or its edge limit. Annotation-free imported closures do not request the
root table and remain eligible for native compilation.

An isolated installed probe binds the latest exact projector revision above and core revision
`6750aa0d9a9fc50c0d6931f7ac8f6310623bc7cf`. On a two-document closure, the imported subclass and
root label matched scalar output while the imported label was suppressed; ingestion truthfully
reported `scalar-native` with the root-provenance fallback reason, including when the scalar edge
count was below the rejected closure's native edge count. With annotations hidden, the same
closure stayed `encoded-native`; an annotation-free imported closure also stayed native with
`include_literals=True`. Single-document visible annotations stayed native for both
the Python provider's independent exact-byte exporters and the native provider's one-exporter
canonical packed arena. The capability ledger remains only `abi3-py310` and `bounded-batches`.

This is correctness-only checkpoint evidence. The host lacked cargo and rustc, so the installed
wheel was assembled from an exact-revision fallback wheel plus the hash-bound kernel-v29 binary
from `cedfc91`; the Rust tree is unchanged between the two revisions. It is not a source-built
release artifact and carries no performance claim. Exact artifact hashes, construction caveats,
edge digests, provider identities, and remaining blockers are recorded in
[`installed-annotation-provenance-checkpoint.json`](evidence/installed-annotation-provenance-checkpoint.json).

## Native root-provenance join

Revision `86fd23b8ff9d522fde45c2793b84f4877b418039` advances the fail-closed guard to a
native exact-direct join. Kernel v30 can retain a second validated root-scoped table alongside the
closure. Before edge limits or output allocation, an iterative no-copy structural comparison maps
each canonical root `AnnotationAssertion` to its closure node. The selected closure nodes drive
annotation counts and emission, while closure totals still prove the complete root partition.
Consequently imported-only annotations cannot affect output, ignored-shape diagnostics, or the
native edge limit. Emitting the closure node also preserves the scalar closure-wide anonymous-ID
space when imported anonymous individuals precede a selected root value.

The Python seam supplies the auxiliary table only when visible annotations need it. Byte-identical
single-document selections retain the existing eleven-buffer boundary; an unequal exact-direct
selection retains 22 zero-copy buffers and reports both table sizes, two direct segments, and one
referenced view. Annotation-free closures still skip root acquisition. Unavailable, non-direct, or
sliced root providers keep the earlier whole-call scalar fallback. The feature ledger remains
exactly `abi3-py310` and `bounded-batches`.

An isolated CPython 3.12 environment installed a release-profile native wheel built from the exact
projector revision above and the previously bound pyOWLCore revision `6750aa0`. Eight installed
multi-document/single-document provenance cases passed, including selected root labels,
imported-only suppression, the selected-edge limit, anonymous-value parity, independent and packed
single-document tables, annotation-free import bypass, and sliced-root fallback. This remains a
small correctness checkpoint: dependency validation was skipped because the environment has wheel
0.47.0 rather than the pinned 0.46.3, and it is not performance or release evidence. Exact hashes,
commands, counters, and blockers are recorded in
[`installed-root-provenance-join-checkpoint.json`](evidence/installed-root-provenance-join-checkpoint.json).

## Iterative annotation-metadata graph preflight

Revision `2ccbffef3519a372075ed8528f4e2c20dd073e5c` advances the private contract to
kernel v31. Earlier preflight validated every `Annotation` property, value, arity, and nested-set
item locally, but a forged table could still point nested annotations back to themselves or an
ancestor. Such a graph cannot originate from immutable OWL model values and must not be accepted as
canonical structural input.

The Rust decoder now performs a color-marked iterative graph walk after local node validation and
before root classification, provenance matching, edge-limit calculation, or output allocation.
Shared acyclic metadata remains valid, while self and transitive cycles fail with a stable malformed
snapshot error. The same preflight runs independently for the closure and retained root-provenance
table, preventing a cyclic forged root identity from reaching the structural join. A 4,096-level
acyclic Rust fixture proves that validation does not consume the call stack.

An isolated CPython 3.12 environment installed a release-profile native wheel built from an exact
archive of the revision above alongside the previously bound pyOWLCore revision `6750aa0`. Both
installed hostile cases passed: a cyclic closure and a cyclic retained root table each failed before
the otherwise projectable taxonomy edge was published. Kernel metadata reported v31 and the feature
ledger remained exactly `abi3-py310` and `bounded-batches`. Dependency validation was skipped because
the environment has wheel 0.47.0 rather than the pinned 0.46.3, so this remains correctness-only
checkpoint evidence with no release or performance claim. Exact hashes and caveats are recorded in
[`installed-annotation-cycle-checkpoint.json`](evidence/installed-annotation-cycle-checkpoint.json).

### Broad decoder parity

Revision `8d925638caf54158f203de40e6cf8a835fb31112` closes the corresponding hostile-input
gap in the broad Python structural decoder. Its local `Annotation` validation already rejected
wrong properties, values, arities, and set members, but could admit a graph whose locally valid
nested references formed a cycle. `_EncodedColumns.inspect()` now runs a color-marked iterative
walk before data-range/class-expression graph validation and root classification. Shared acyclic
metadata remains valid; a gray-node encounter raises `SnapshotCompatibilityError` before any root
selection, retained-root identity comparison, or edge publication.

The closure case forges a self-reference in annotated `SubClassOf` metadata. The root-provenance
case keeps the closure valid and independently forges the retained ontology annotation, proving the
auxiliary table is preflighted before the join. An isolated CPython 3.12 environment installed the
release-profile v31 wheel built from an exact archive of that revision plus bound pyOWLCore
`6750aa0`; both hostile cases and all 1,112 projector tests passed from installed payloads. The
feature ledger stayed exactly `abi3-py310` and `bounded-batches`. Dependency checking was skipped
because wheel 0.47.0 was installed instead of pinned 0.46.3, so the hash-bound record remains a
correctness-only checkpoint:
[`installed-broad-annotation-cycle-checkpoint.json`](evidence/installed-broad-annotation-cycle-checkpoint.json).

### Stack-safe broad class/data graphs

Revision `b9ca9a558b4f99ac495d3ae350bab18123ee8307` removes the remaining Python
call-stack dependency from the broad decoder's supported recursive class-expression and data-range
graph preflights. The accepted constructor envelope and fallback decisions are unchanged. Compact
color arrays and enter/exit event stacks now traverse aggregate/complement edges, retain completed
shared subgraphs, and raise the same typed cyclic-graph errors on gray-node references.

Two flat public-core snapshots were rewritten only at the retained structural-column boundary into
acyclic chains of 1,200 `ObjectComplementOf` and 1,200 `DataComplementOf` nodes. Both now complete
preflight rather than being rejected at Python's recursion limit; the existing forged cycle cases
still fail before output. An isolated CPython 3.12 environment installed an exact-archive native
wheel plus bound pyOWLCore `6750aa0`; the four focused cases and all 1,114 projector tests passed
from installed payloads. Native source and the hidden v31 feature ledger were unchanged. The
hash-bound correctness record is
[`installed-recursive-graph-checkpoint.json`](evidence/installed-recursive-graph-checkpoint.json).

### Stack-safe canonical identity cursor

Revision `5b7799ad59de6596712a31d12ab9943b8a10b522` removes graph recursion from both
stages of `_CanonicalCursor`. Node lengths are computed child-first through explicit iterator
frames and memoized by node ID. Canonical bytes are then yielded from lazy node/component
frames; no whole-node canonical byte string is constructed. Generic active-node detection retains
the typed cycle failure before root merging, retained-root intersection, or output.

Installed exact-byte cases match public pyOWLCore canonical bytes for the executable constructor
mix, sequence encounter order, and anonymous scope remapping exercised by the full suite. The
1,200-node class and data-range fixtures now pass length calculation and complete byte iteration,
and a separately forged self-cycle fails in the cursor itself. The exact-archive wheel passed all
five focused cases and the complete 1,115-test suite on CPython 3.12 with bound pyOWLCore
`6750aa0`. Native source and the unadvertised v31 feature ledger were unchanged. Hashes and caveats
are recorded in
[`installed-canonical-cursor-checkpoint.json`](evidence/installed-canonical-cursor-checkpoint.json).

### Stack-safe segment resolution

Revision `b5bf5f37bfe562e37c80d5a4469eb302e9e78960` removes the Python call-stack
dependency from overlay and composite segment resolution. Explicit frames suspend each parent
while a referenced view resolves, then apply that boundary's anonymous-scope map and source-local
postings on return. Per-view validation/cache reuse, per-occurrence counters, member-token order,
lease retention, and active-path cycle detection retain their existing semantics.

An installed exact-archive case resolves 1,100 nested overlay-base manifests to one source-local
root, retains all 1,100 referenced leases, and reports the exact segment/source-root counters.
Nested overlay and composite-source cases preserve their edge and lifetime behavior; forged
overlay and composite cycles still fail before output. The five focused cases, all 669 broad
encoded-compiler cases, and the complete 1,116-test suite passed on CPython 3.12 with bound
pyOWLCore `6750aa0`. Native source and the unadvertised v31 feature ledger were unchanged. The
hash-bound correctness record is
[`installed-segment-resolution-checkpoint.json`](evidence/installed-segment-resolution-checkpoint.json).

### Segmented annotation provenance

Revision `924f944586774a25bddc1b62f762d970cef31477` removes the broad compiler's
conservative multi-document fallback for segmented closure and root tables. Both sides now pass
through the same fully validated resolver and canonical root merge before annotation-assertion
identity intersection. Root-only selection, closure-wide class membership and anonymous IDs,
source-local postings/scope maps, exact counters, and every resolved owner lifetime remain intact.

Installed hidden-path cases cover overlay and composite closure manifests plus an independently
segmented root selection. Each matches the scalar report and edges, retains only the root
document's label, and crosses no scalar traversal; unavailable root scope still selects one
whole-operation fallback. All four focused cases, 670 broad encoded-compiler cases, and the full
1,117-test suite passed with bound pyOWLCore `6750aa0`. Native source and the unadvertised v31
feature ledger were unchanged. Hashes and limitations are recorded in
[`installed-segmented-provenance-checkpoint.json`](evidence/installed-segmented-provenance-checkpoint.json).

### Hidden Scala-instance lifecycle binding

Revision `eabb8c856bd55baf72bbd36b41bfc54bf11f2029` connects the private retained
Rust role-state handle to the hidden Projector iterator. Explicit `scala-instance` calls now share
one handle under the Projector's existing non-concurrent lock. Successful preparations copy a
strictly validated subrole/inverse snapshot into the scalar-compatible shadow; if a later call
selects any whole-operation scalar compiler, the Projector permanently disables native state and
continues from that shadow. This prevents a mixed lifecycle from silently re-entering stale native
maps.

The installed three-call sequence covers initial role acquisition, later restriction and
domain/range consumption, and a conflicting subrole/inverse overwrite. Its edge counts are 0, 6,
and 3; all calls report encoded-native, the final invocation count is three, and retained-map
counters report one subrole property and three inverse properties. A separate injected-decline
sequence proves the first scalar-native transition and zero later native preparation calls. The
direct foundation also retains edge-limit failure atomicity before a subsequent successful state
commit.

An isolated CPython 3.12 environment installed a release-profile wheel built from the exact
implementation archive plus bound pyOWLCore `6750aa0`. The four focused lifecycle cases, 11 audit
and benchmark cases, and all 1,118 projector tests passed from installed payloads. Rust's 36 unit
tests, rustfmt, Clippy with warnings denied, Ruff, and mypy passed on the same exact source. Kernel
metadata remains v31 and the public feature ledger remains exactly `abi3-py310` and
`bounded-batches`. Artifact hashes and limitations are recorded in
[`installed-scala-lifecycle-checkpoint.json`](evidence/installed-scala-lifecycle-checkpoint.json).

### Bounded native output cursor

Revision `86b055d8715e288e93ac77edf0519412d1324037` removes the hidden iterator's
ontology-sized `Vec<DirectEdge>`. After the existing structural and count preflight, kernel v32
dry-runs the exact ordered emission one edge at a time. Only a successful dry run publishes a
cursor and commits retained role state. Each drain clones that cursor transactionally, allocates no
more than the caller bound, releases the GIL without holding the output mutex, constructs the
Python list, and only then commits cursor movement. A raced close discards the unpublished bounded
batch. The legacy private coarse call remains materialized and public dispatch remains unchanged.

The additive hidden ledger distinguishes `native_compiled_edges`,
`native_output_vector_edges`, and `native_peak_buffered_edges`. An exact installed benchmark over
the 11-edge taxonomy/restriction fixture with a bound of two reported six nonempty drains, seven
compile-plus-drain calls, zero vector-backed edges, and a peak of two. It retained zero parser,
resolver, wire, scalar-materialization, structural-copy, and per-row-FFI work. This is bounded-output
correctness evidence; the full dry traversal before first publication still needs labelled corpus
timing and RSS work.

An isolated CPython 3.12 environment installed a release-profile wheel built from the exact
implementation archive plus bound pyOWLCore `6750aa0`. Eleven focused bounded-drain cases, 11 audit
and benchmark cases, and all 1,118 projector tests passed from installed payloads. Rust's 37 unit
tests, rustfmt, Clippy with warnings denied, Ruff, and mypy passed on the same exact source. The
feature ledger remains exactly `abi3-py310` and `bounded-batches`; the encoded compiler is still
unadvertised. Artifact hashes, counter bindings, and limitations are recorded in
[`installed-bounded-cursor-checkpoint.json`](evidence/installed-bounded-cursor-checkpoint.json).

## Diamond and cyclic import provenance

Revision `18ed10e4a9bd48ae6c8b23e6a6d85f1a60ebcee7` extends the installed root-join
proof beyond a one-level import. The diamond fixture has two branches importing one shared member;
the cyclic fixture has two ontologies importing each other. In both cases the public core resolves
the complete closure before the private compiler receives its canonical closure and root tables.

The hidden native iterator matched scalar encounter order and report semantics. The diamond emitted
four unique taxonomy edges plus the root label, proving that the shared member was visited once and
all three imported labels were suppressed. The cycle emitted both taxonomy edges plus the root
label and suppressed the imported label. Each run used its exact selected output count as the native
edge limit, retained 22 zero-copy buffers across two direct table segments, and reported zero scalar
axiom or term materialization.

An isolated CPython 3.12 environment installed a release-profile native wheel built from an exact
archive of the revision above and the bound pyOWLCore `6750aa0` wheel. Both topology cases passed;
kernel metadata remained v31 and the feature ledger remained only `abi3-py310` and
`bounded-batches`. As with the adjacent checkpoints, dependency validation was skipped because the
environment has wheel 0.47.0 rather than the pinned 0.46.3. This is correctness evidence, not a
release or performance claim. Exact artifact hashes are recorded in
[`installed-import-topology-checkpoint.json`](evidence/installed-import-topology-checkpoint.json).

## Verification at this checkpoint

The following source-tree and exact-installed checks passed for the implementation sequence
`39a5656` through `86b055d`:

| Gate | Result |
|---|---|
| Rust unit tests (`cargo test --no-default-features`) | 37 passed |
| Rust formatting and Clippy with warnings denied | passed |
| Private PyO3 foundation tests | 219 passed |
| Foundation plus private-integration tests | 273 passed |
| Broad encoded-compiler tests | 670 passed |
| Complete projector test suite | 1,118 passed |
| Exact installed native-wheel annotation-provenance cases | 8 passed |
| Exact installed native-wheel annotation-cycle cases | 2 passed |
| Exact installed native-wheel import-topology cases | 2 passed |
| Exact installed broad-decoder annotation-cycle cases | 2 passed |
| Exact installed broad-decoder recursive-graph cases | 4 passed |
| Exact installed canonical-cursor cases | 5 passed |
| Exact installed segment-resolution cases | 5 passed |
| Exact installed segmented-provenance cases | 4 passed |
| Exact installed Scala-instance lifecycle cases | 4 passed |
| Exact installed bounded-cursor cases | 11 passed |
| Exact installed bounded-cursor audit/benchmark cases | 11 passed |
| Focused Python Ruff and mypy checks | passed |

The focused tests cover Python-oracle parity for named class, role, and object-assertion edges;
both restriction orientations and all four accepted constructors; cardinality discard;
bidirectional projection; n-ary equivalent lexical/expression selection; named intersection/union
operand ordering; mixed named/restriction aggregate emission; duplicate aggregate edges; aggregate
role expansion; class/assertion/domain/range category and cross-product ordering; conflicting
subrole/inverse OWLAPI hash-set visitation and overwrite behavior; named and inverse role operands;
direct/subrole/inverse ordering; ordered three-view Scala-instance parity through a retained
owner-independent role-state handle, including later domain/range expansion and conflicting-map
overwrite; edge-limit failure without retained-state mutation followed by successful state commit;
retained-IRI limit enforcement; whole-call overlap rejection and use-guard release after failure;
direct-assertion non-expansion;
every supported skipped
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
annotation-set, and anonymous-scalar corruption; canonical packed-arena admission plus gap,
overlap, reordered, arbitrary-slice, and mutable-exporter rejection; descriptor mismatch;
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
exact-order multi-batch differential output across mixed taxonomy, aggregate, ABox, retained-role,
and domain/range edges; caller bounds of three and four; exact compile-plus-batch FFI accounting;
empty retained-role calls without a drain; synchronous callable sinks; sink-failure and explicit
close cleanup after partial consumption; batch-wrapper ownership until close; failed edge-limit
state atomicity; bytes-exporter and exact-owner lifetime across the expanded slice; GIL release;
concurrent cancellation; reusable role-state exclusion/release; and continued absence of the
production encoded feature; exact root-scope annotation selection for independent and canonical
packed direct bytes; whole-call scalar fallback for unequal imported-closure selections and
arbitrary sliced root evidence before compilation and native edge-limit enforcement;
annotation-free imported closure admission; imported-annotation suppression when literals are
hidden; plus hidden-iterator differential parity for encounter/preserve and
canonical/unique/bidirectional calls, annotated duplicate taxonomy roots, exact report and
zero-forbidden-work counters, unchanged public dispatch, named n-ary equivalence/class assertion/
object-property assertion parity under normal, bidirectional, canonical-unique, historical
`only_taxonomy`, and `include_literals` combinations, exact aggregate-equivalence base-edge and
operand-level ignored-shape ledgers across normal, bidirectional/canonical, and `only_taxonomy`
modes,
direct Some/All/Min/Max restriction parity in both orientations with named/inverse properties,
cardinality-discard duplicates, normal/bidirectional/canonical-unique behavior, exact historical
`only_taxonomy` suppression and grouped ignored-shape diagnostics, complete three-by-two named
domain/range cross-products with annotated duplicate roots under encounter, canonical-unique,
bidirectional, and historical `only_taxonomy` options, exact multi-property and unpaired named
domain/range admission, inverse/complex ignored partitions and grouped diagnostics, forced
inconsistent-ledger fallback cleanup, partial iterator close, cancellation between native
batches, same-call named/inverse subrole expansion across restrictions and domain/range products,
direct assertion non-expansion, normal/bidirectional/canonical-unique/`only_taxonomy` report parity,
exact ignored property-chain admission without diagnostics or role-map mutation, explicit
Scala-instance native lifecycle retention across role acquisition, later consumers, and
conflicting overwrites, strict scalar-compatible map snapshots, one-way scalar transition after a
native decline, retained-map provenance counters, selected
IRI/plain/language/typed class annotations, annotated duplicate preservation, malformed non-string
rendering and grouped warning parity, combined warning/ignored-diagnostic order, option-dependent
silent versus exactly counted ignored admission for unselected annotation properties, exact mixed
ignored/warning/skipped diagnostic ordering, exact anonymous positive
assertion and selected-annotation value IDs including metadata-only blank-ID participation across
encounter/canonical/include/suppress options, exact ignored subclass and class-assertion partitions,
constructor-grouped diagnostics, and raw-edge accounting across normal, bidirectional/canonical,
and historical `only_taxonomy` options, exact grouped skip diagnostics across all 27 supported
scalar-skipped constructors, silent ontology-annotation and SWRL admission, and resource failure
without a partial report.

The benchmark-specific cases additionally prove that public scalar runs retain their existing
surface and rejection behavior; the private candidate selects encoded-native with exact
compile-plus-drain counters; its complete counter/core-operation ledgers and loaded native binary
are hash-bound; it remains unable to satisfy public acceptance; and invalid backend, gate, or
revision combinations fail before ontology loading.

These are local source-tree checks. They do not replace hosted wheels, sanitizers, fuzzing,
licensed corpora, performance thresholds, or the Exact acceptance matrix.

## Acceptance ledger

| WP-P7 requirement | Current truthful state |
|---|---|
| Public descriptor/owner validation | Python adapter is broad; private Rust seam rechecks its narrow direct envelope and descriptor binding |
| Complete Rust projection rules/options | Open; the report above enumerates the bounded direct ABox/taxonomy/restriction, recursive validation, role-state, skipped/silent, annotation, diagnostic, and compatibility slices. Kernel v30 joins unequal exact-direct root annotation identities to closure nodes before counting/output, including closure-wide anonymous IDs; v31 rejects cyclic nested annotation metadata in both tables; v32 emits the hidden iterator through a resumable bounded cursor. The hidden Projector binds retained Scala-instance maps with an exact one-way scalar transition, but public binding and remaining projecting rules/options/surfaces are unsupported |
| Bounded batches without per-row FFI | The hidden iterator's Rust and Python output are caller-bounded, use one FFI call per batch, preserve exact order, and report compiled/vector/peak counters. Preparation still dry-runs full emission, the legacy private coarse call is materialized, and public iterator/sink/digest/artifact integration remains open |
| Production dispatch and provenance | Open; public dispatch remains unchanged and the capability is absent. An explicitly hidden named-edge iterator selects the private kernel and reports its exact ingestion counters after complete consumption |
| Direct/mmap/overlay/composite support | Exact full bytes and the canonical eleven-column packed direct-bytes arena are supported; arbitrary slices, mmap, overlay/composite, and segmented families are unsupported |
| Lifetime/GIL/cancel/failure safety | Focused private bytes-path, batch close/collection/sink-failure/state-atomicity, and hidden iterator close/cancel/fallback/resource tests pass; full production iterator/fork/shutdown/fuzz/sanitizer matrix remains open |
| Zero forbidden-work ledger | Reported by the successful hidden exact named-edge candidate; no public production-path claim |
| Corpus performance/RSS gates | Private load-excluded harness now binds execution surface, exact ledgers, runtime artifacts, and revisions; installed NCIT/DOID/GO/large-corpus measurements and thresholds remain open |
| Exact shared-stack acceptance | Open for this kernel |
| Wheels/SBOM/platform matrix | Open for this kernel |

## Promotion decision and next work

Public `auto` and explicit native negotiation remain unchanged. Before advertising
`encoded-structural-compiler-v1`, P7 still needs:

1. promote the proven hidden retained-role lifecycle, bounded cursor, locking, invocation history,
   counters, and one-way scalar transition into public encoded selection only after the remaining
   gates pass;
2. connect the cursor to the public iterator, protocol sink, digest, artifact, and cancellation
   surfaces, and measure or optimize the full dry emission before first publication;
3. safe no-copy direct/mmap/overlay/composite ownership and segment traversal;
4. production provenance wired only after it describes actual Rust work;
5. full oracle/generated/hostile/fuzz/sanitizer/thread/fork/shutdown/platform verification;
6. labelled NCIT, DOID, GO, million-axiom, licensed-corpus, RSS, and copy evidence; and
7. Exact-OM shared-stack and release packaging/SBOM/compatibility review.

No compatibility, completeness, or performance claim is inferred from this private checkpoint.
