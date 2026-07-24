# P7 encoded-native compiler checkpoint

Date: 2026-07-24. Projector product through `e531a02`, benchmark harness through `5ac8ef3`,
deterministic generated verification through `4e0f54d`, and encoded-column validation through
`bf24a90`. pyOWLCore candidate revision: `6750aa0`. Exact-OM integration revision: `fe46141`.

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
and artifact entry points cannot select this path. Explicitly hidden Projector adapters now
exercise the same cursor through each stream consumer without weakening that dispatch boundary.

The load-excluded benchmark now has a separately labelled
`private-native-candidate` execution surface. It hashes every complete ingestion-counter and
post-view core-operation ledger, binds the runner plus loaded package/native artifacts by SHA-256,
records distribution `RECORD` hashes, native features and kernel version, and accepts only full
40-character projector/core revisions. `private_candidate_boundary_ready` describes only the
hidden counter contract. The stronger `private_candidate_evidence_ready` additionally requires
installed distribution payloads and both revisions. Neither field can set public
`acceptance_ready`; private mode is rejected if combined with the public
`--require-encoded-native` gate. The harness also emits the direct-bytes-only,
unwired-public-surfaces, and scalar-public-lifecycle blockers in every result. Harness revision
`5ac8ef3` independently labels iterator, sink, digest, and artifact consumers and binds the chosen
surface plus consumer metrics into the evidence hash. Iterator and sink expose time to first
output; aggregate digest and artifact calls explicitly record that metric as unobservable.
Kernel v32 removes
the former whole-output-vector blocker for the hidden iterator and binds that fact to compiled,
vector-backed, and peak-buffered edge counters; v33 starts the cursor without a pre-publication
emission replay. Kernel v34 removes the legacy coarse call's duplicate complete Rust output vector
and emitter: its required Python list is populated through fixed 256-edge cursor chunks, and its
retained role-state transaction commits only after complete Python result construction. Kernel
v35 constructs the final `Edge` and statistics objects within that transaction, removing the
wrapper's second complete tuple-edge list. Kernel v36 extends final-object construction to each
bounded iterator drain and commits its cursor only after the final `Edge` tuple exists. Kernel v37
constructs final statistics before installing the batch session or committing retained role state.
Kernel v38 also constructs the final iterator wrapper, with its compiler owner and statistics
references, before that publication boundary.
Kernel v39 validates the canonical factory identities and exact result types before committing the
same transaction.
Kernel v40 extends exact-result and canonical-factory validation to every bounded-drain `Edge`
before cursor/counter commit and to every legacy coarse-call `Edge` plus final statistics before
retained-role/output-counter commit.
Kernel v41 pins the corresponding post-native Python envelope checks to the same import-time
canonical identities, rather than mutable module-level factory names.
Kernel v42 validates the contents of every final edge and statistics object, plus the final
iterator's owner, statistics, batch bound, and initial yielded count, before the enclosing native
transaction can commit.
Kernel v43 revalidates every bounded or coarse edge object after the last constructor callback,
requires all final edge identities to be distinct, and revalidates statistics after iterator
construction before the same native transaction can commit.
Kernel v44 removes production `Edge` factory and constructor callbacks from both paths. It validates
the canonical type's exact slotted object layout, allocates each final object directly through the
CPython stable ABI, assigns its three strings through the validated member descriptors, and
rechecks the complete layout, canonical factory identity, type, payload, and distinct identities
before commit.
Kernel v45 applies the same validated exact-slot allocation to the 60-field statistics result on
both coarse and batch-session paths. It creates no 60-field argument tuple and invokes no Python
statistics factory or constructor callback, while retaining complete layout, factory-identity,
exact-type, and integer-payload validation before the same commit boundaries.
Kernel v46 directly allocates the final eight-slot batch iterator as well. Its compatible
`collections.abc.Iterator` base, ordered slot layout, canonical factory identity, exact
owner/statistics identities, and every initial state field are validated before session or
retained-role publication, with no argument tuple or Python iterator factory/constructor callback.
Projector revision `e531a02` then exercises the cursor through hidden protocol-sink,
canonical-digest, and portable-artifact adapters. They reuse the existing ordering, duplicate,
spill, cancellation, report, sink-validation, and artifact implementations; they neither advertise
the capability nor change ordinary public dispatch.
Verification revision `4e0f54d` adds a bounded SplitMix64 generated differential campaign. It
combines fourteen rule/diagnostic families, all 32 semantic-boolean/duplicate/order combinations,
both supported direct exporter layouts, and batch bounds one through seven. Each exact installed
execution compares ordered edges and semantic report fields to the scalar compiler and fails on
fallback, staging copy, per-row FFI, or a missing released-GIL record.
Validation revision `bf24a90` adds 29 deterministic invalid cases spanning every one of the eleven
repository-owned structural columns. It checks both supported direct exporter layouts and requires
the same typed rejection through the direct compiler and hidden Projector before output, terminal
failed state without a batch session or output counters, equal provider-layout failures, no report,
and closeable-view cleanup.
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
4. either returns one coarse list or, for the hidden iterator, installs a zero-emission resumable
   cursor behind a private caller-bounded drain, together with roots, nodes, axiom-derived anonymous
   individuals,
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
session is published by a failed compile. At this checkpoint, the statistics tuple introduced with
private v26 returns sixty fields through explicit Python construction because PyO3's automatic
tuple conversion is bounded below that arity; kernel v45 later replaces that tuple and constructor
call with direct exact-slot allocation.

The hidden batch seam now bounds native projected-output storage as well as Python transfer. Its
cursor clones only small transaction state, builds at most the configured batch with the GIL
detached and no held output mutex, then constructs the final `Edge` tuple with the GIL and commits
after that tuple is complete. Preparation still performs exhaustive immutable structural,
semantic, exact-count, and capacity preflight, but v33
no longer replays ordered emission before publication. Kernel v34 also routes the legacy private
coarse call through that cursor in fixed 256-edge native chunks. The call still returns its required
materialized Python list, but no complete Rust edge vector exists beside it; cursor movement is
local until each chunk is appended, and retained Scala role state commits only after the list and
statistics tuple are complete. The caller-bounded iterator is connected only to the hidden
named-edge path, which reuses the canonical spill machinery; it is not connected to public
`Projector.iter_edges`, protocol sink, digest, or artifact publication. These changes close the
private Rust-output-vector and pre-publication-replay gaps without satisfying P7 deliverable 3 or
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

### Lazy cursor start after exhaustive preflight

Revision `d6a5ba3df5cb9e2122241b33590081735f473f7f` removes the v32 cursor's
redundant full emission replay before publication. The underlying input exporters are immutable;
the existing preparation already validates every structural reference, supported semantic shape,
projected count, edge/IRI capacity, anonymous-ID dependency, root-provenance identity, and retained
role-state transition. Kernel v33 therefore publishes a default cursor immediately after that
preflight and state commit. Ordered edge traversal and string allocation begin only when the caller
requests a bounded batch. Per-batch clone/build/Python-list/commit transactions and close behavior
are unchanged.

Exact-source test instrumentation records zero emission attempts after preparation, two attempts
for an uncommitted two-edge preview, unchanged remaining edges before commit, and final exact-order
parity with the materialized reference. The exact installed v33 wheel retained the 11-edge fixture's
six drains, seven boundary calls, zero vector-backed edges, two-edge peak, semantic digest, and zero
forbidden-work ledger. Eleven focused cases, 11 audit/benchmark cases, and all 1,118 tests passed
from the installed wheel plus bound core `6750aa0`; all 37 Rust tests and static gates passed from
the exact archive. This is correctness evidence, not a corpus timing or RSS claim. Hashes and
remaining gates are recorded in
[`installed-lazy-cursor-checkpoint.json`](evidence/installed-lazy-cursor-checkpoint.json).

### Bounded legacy coarse result

Revision `70c8623b6f8265074d6d7522c6b1e4cd8a942419` removes the production
coarse compiler's separate full-vector emitter. Kernel v34 performs the same exhaustive preflight
as the hidden iterator, then drains the shared resumable cursor through fixed 256-edge Rust vectors
while appending directly to the one Python list required by the legacy return contract. Cursor
movement commits after a complete chunk append. Any emission, Python allocation, statistics, or
retained-role clone failure discards the unpublished Python list and leaves reusable role maps
unchanged. On success, the role mutex is held across the compiler's running-to-finished transition,
making cancellation and retained-state commit indivisible from the visible result.

The exact archived release wheel compiled 600 ordered subclass edges through three native chunks,
reported zero complete Rust output-vector edges and a 256-edge peak, and matched the scalar result.
Six focused lifecycle/bound/capability cases, 11 audit/benchmark cases, and all 1,119 tests passed
from that wheel plus exact core `6750aa0`; all 38 Rust tests and the static gates passed from the
archive. The output list itself remains O(E), as required by this private compatibility call, so
this checkpoint is neither public streaming acceptance nor an RSS/performance claim. Exact hashes
and remaining gates are recorded in
[`installed-coarse-cursor-checkpoint.json`](evidence/installed-coarse-cursor-checkpoint.json).

### Single-allocation coarse result transaction

Revision `969c62eeaf34a5be7ce834156d16a62cb834581b` closes the remaining coarse
result handoff gap. Kernel v35 invokes the final `Edge` factory for each cursor-produced edge and
the final statistics factory before committing retained role state, then returns that one final
Python list directly. The wrapper no longer holds a complete tuple-edge list while allocating a
second complete `Edge` list. This does not remove the required O(E) final list or the per-edge
Python constructor calls inside the single coarse PyO3 invocation at this checkpoint; kernel v44
later removes those constructor calls while retaining the required final list.

Injected `MemoryError` failures on the second final `Edge` and on the final statistics object both
leave the compiler failed, publish zero output chunks, release exclusive role-state use, and leave
the retained subrole/inverse maps empty. The exact archived release wheel compiled 600 ordered
subclass edges into 600 final `Edge` instances, reported zero intermediate-list and complete Rust
vector edges, used three native chunks with a 256-edge peak, and matched the scalar digest. Seven
focused transaction/bound/capability cases, 11 audit/benchmark cases, and all 1,120 tests passed
from that wheel plus exact core `6750aa0`; all 38 Rust tests and static gates passed from the exact
archive. This remains private structural correctness evidence, not a performance or RSS claim.
Exact hashes and blockers are recorded in
[`installed-final-result-checkpoint.json`](evidence/installed-final-result-checkpoint.json).

### Final bounded-batch transaction

Revision `714fe9c02d34fbd1c3e63072b5661f538a8b03fa` closes the equivalent handoff
gap in the resumable iterator. Kernel v36 constructs each final bounded tuple of `Edge` objects
inside the native drain transaction and returns it directly. The wrapper no longer holds a Python
list of string triples while allocating a second tuple of final edges. Cursor position,
boundary/batch counters, remaining-edge count, and the peak-buffer counter commit only after the
final tuple has been constructed.

An injected `MemoryError` on the second final `Edge` leaves the iterator active with zero yielded
edges, its original three remaining edges, one boundary call, zero committed batches, and a zero
peak; removing the fault retries the identical first batch and exhausts exact output normally. The
exact archived release wheel compiled 600 edges into 600 final `Edge` instances in three tuple
batches, reported zero intermediate Python-list edges and a 256-edge peak, and preserved the
ordered edge-record digest. Eight focused transaction/capability cases, 11 audit/benchmark cases,
and all 1,121 tests passed from that wheel plus exact core `6750aa0`; all 38 Rust tests and static
gates passed from the exact archive. This remains private correctness evidence, not a performance
or RSS claim. Exact hashes and blockers are recorded in
[`installed-final-batch-checkpoint.json`](evidence/installed-final-batch-checkpoint.json).

### Atomic batch-session publication

Revision `199ecc6cf2b1ea1fbeee056172edca24d7456327` closes the iterator's
preparation-side publication gap. Kernel v37 prepares retained role changes without committing
them, constructs the final `NativeEncodedDirectStatistics` object, clones the next role state, and
only then finishes the compiler while installing the session and retained transition under their
locks. The wrapper receives the final statistics object directly instead of constructing it after
the cursor was already visible.

An injected `MemoryError` in the final statistics factory leaves the compiler failed and batch
session absent, with zero remaining edges, boundary calls, batches, and peak-buffer entries. The
exclusive role-use guard is released and both retained maps remain empty. The exact archived wheel
also compiled 600 edges through three 256-edge-bounded final tuple batches with a final statistics
instance, zero intermediate Python-list edges, and the same edge-record digest. Nine focused
session/result/capability cases, 11 audit/benchmark cases, and all 1,122 tests passed from that wheel
plus exact core `6750aa0`; all 38 Rust tests and static gates passed from the exact archive. This is
private correctness evidence only. Exact hashes and blockers are recorded in
[`installed-batch-session-checkpoint.json`](evidence/installed-batch-session-checkpoint.json).

### Atomic iterator-wrapper publication

Revision `604dedf10e2d878d8ea577caf9e11e7f3c014e8a` closes the remaining
preparation-side allocation gap. Kernel v38 constructs the final
`NativeEncodedDirectBatchIterator` inside the native transaction after final statistics exist but
before the compiler is finished, the batch session is installed, or retained role state commits.
The wrapper therefore receives the already-final iterator instead of allocating an owner-holding
object after those states became visible.

An injected `MemoryError` in the iterator factory observes the exact compiler owner, final
statistics object, and configured batch size, then leaves the compiler failed and batch session
absent, with zero remaining edges, boundary calls, batches, and peak-buffer entries. Exclusive role
use is released and both retained maps remain empty. The exact archived wheel also compiled 600
edges through a final iterator and statistics object in three 256-edge-bounded tuple batches, with
zero intermediate Python-list edges and the unchanged edge-record digest. Ten focused
publication/result/capability cases, 11 audit/benchmark cases, and all 1,123 tests passed from that
wheel plus exact core `6750aa0`; all 38 Rust tests and static gates passed from the exact archive.
This is private correctness evidence only. Exact hashes and blockers are recorded in
[`installed-iterator-publication-checkpoint.json`](evidence/installed-iterator-publication-checkpoint.json).

### Transactional final-factory validation

Revision `c18341797e3756482237d0a3aa21b2cf4540a453` closes the corresponding
malformed-result publication gap. Kernel v39 retains the canonical statistics and iterator type
identities separately from their replaceable call sites. After each factory returns, the native
transaction requires both the canonical factory identity and its exact result type before it may
clone retained role state, finish the compiler, or install the batch session.

Injected statistics and iterator factories that return plain objects each run once and fail inside
that transaction. Both leave the compiler failed and session absent, with zero remaining edges,
boundary calls, batches, and peak-buffer entries; exclusive role use is released and both retained
maps remain empty. The iterator-return case additionally proves that the final statistics object
already existed at validation. The exact archived wheel preserved the 600-edge, three-batch,
256-edge-peak result and ordered digest. Twelve focused publication/result/capability cases, 11
audit/benchmark cases, and all 1,125 tests passed from that wheel plus exact core `6750aa0`; all 38
Rust tests and static gates passed from the exact archive. This remains private correctness
evidence only. Exact hashes and blockers are recorded in
[`installed-factory-validation-checkpoint.json`](evidence/installed-factory-validation-checkpoint.json).

### Transactional edge-factory validation

Revision `442353f8855942064315fa5822785c26c0d83519` closes the remaining final-result
validation gaps on bounded drains and the legacy coarse call. Kernel v40 retains the canonical
`Edge` identity separately from its replaceable call site. Every bounded result must have that
exact type and the factory itself must remain canonical before cursor position or counters commit.
The coarse transaction applies the same requirements to every `Edge` and its final statistics
result before it may publish output counters or clone retained role maps.

Injected bounded factories returning either a plain object or canonical `Edge` objects from a
replaced call site fail with the cursor active, all three edges remaining, and zero yielded edges,
edge batches, or peak-buffer entries. Removing the injection drains the exact three edges, proving
that both failures are retryable. The four corresponding coarse cases cover malformed and
canonical results from replaced edge/statistics factories; each leaves the compiler failed, all
coarse output counters zero, exclusive role use released, and both retained maps empty. The exact
archived wheel preserved the 600-edge, three-batch, 256-edge-peak result and ordered digest.
Eighteen focused transaction/capability cases, 11 audit/benchmark cases, and all 1,131 tests passed
from that wheel plus exact core `6750aa0`; all 38 Rust tests and static, runtime-boundary, and wheel
audits passed from the exact archive. This remains private correctness evidence only. Exact hashes
and blockers are recorded in
[`installed-edge-factory-validation-checkpoint.json`](evidence/installed-edge-factory-validation-checkpoint.json).

### Canonical post-commit wrapper identities

Revision `b10c5bba2f4dd1d9d35593a059c1b293d446296e` closes a post-transaction
wrapper seam. Kernel v41 keeps the Python coarse, session-preparation, and bounded-drain envelope
checks anchored to the import-time canonical `Edge`, statistics, and iterator types already passed
into the native transaction. Previously, a canonical constructor could mutate its replaceable
module-level factory name while Rust held the original callable; native code would validate and
commit exact canonical objects, after which Python reread the changed global and rejected the
successful result.

Five exact installed-wheel cases mutate those globals from canonical constructors themselves. The
bounded case publishes its two exact edges, advances one batch, and then drains the remaining edge
to exhaustion. Coarse edge/statistics cases return all three scalar-equivalent edges and preserve
the committed one-subrole/two-inverse maps. Batch statistics/iterator cases publish an exact active
session with all three edges remaining, preserve the same role maps, and drain to exhaustion.
The 600-edge control still completes in three batches with a 256-edge peak and unchanged ordered
digest. Twenty-three focused transaction/capability cases, 11 audit/benchmark cases, and all 1,136
tests passed from the exact wheel plus core `6750aa0`; all 38 Rust tests and static,
runtime-boundary, and wheel audits passed from the exact archive. This remains private correctness
evidence only. Exact hashes and blockers are recorded in
[`installed-canonical-wrapper-checkpoint.json`](evidence/installed-canonical-wrapper-checkpoint.json).

### Transactional final-payload validation

Revision `5b960d1b053efe7461d8dc20e4758718d523ff6e` closes the remaining malformed-payload
publication seam. Kernel v42 validates each final `Edge` object's source, relation, and destination
against the native edge before list construction or cursor commit. It validates all 60 fields of
the final statistics object against the native statistics ledger and validates the final
iterator's compiler owner, statistics identity, batch bound, and initial yielded-edge count before
publishing the batch session or retained role transition.

Five exact installed-wheel corruption cases cover a bounded edge with a changed source, a coarse
edge with a changed source, coarse statistics with a changed roots count, batch statistics with a
changed final provenance-buffer field, and an iterator with a changed compiler owner. The bounded
failure leaves all three edges retryable with zero cursor/counter movement; its retry drains the
exact three edges. Every coarse or batch-session failure leaves all output/session counters zero,
releases exclusive role use, and leaves both retained maps empty. The 600-edge control still
completes in three batches with a 256-edge peak, zero intermediate Python-list edges, and the
unchanged ordered digest. Twenty-eight focused transaction/capability cases, 11 audit/benchmark
cases, and all 1,141 tests passed from the exact wheel plus core `6750aa0`; all 38 Rust tests and
static, runtime-boundary, and wheel audits passed from the exact archive. This remains private
correctness evidence only. Exact hashes and blockers are recorded in
[`installed-final-payload-validation-checkpoint.json`](evidence/installed-final-payload-validation-checkpoint.json).

### Complete final-batch validation

Revision `05549655b5b75f6deefc56f3cfb3cbf7c0d1efd9` closes the remaining later-callback
mutation seam. Kernel v43 retains every final bounded or coarse edge until the whole native chunk
has been constructed, then validates exact type, all three strings, and distinct object identity
for the complete chunk before tuple/list construction or cursor and retained-state commit. It also
revalidates the final statistics object after the iterator constructor returns, preventing that
last callback from changing the already-validated 60-field ledger before session publication.

Three exact installed-wheel cases make a later edge constructor corrupt the first edge on both
bounded and coarse surfaces and make the iterator constructor corrupt its statistics. The bounded
failure keeps all three edges retryable with zero cursor/counter movement and its retry drains the
exact result. The coarse failure publishes zero output counters. The iterator failure publishes no
session or counters, releases role use, and leaves both retained maps empty. All 1,144 tests passed
from the exact release-profile wheel plus core `6750aa0`; all 38 Rust tests, rustfmt, Clippy with
warnings denied, Ruff, mypy, the runtime dependency audit, and the 30-member wheel audit passed.
This remains private correctness evidence only. Exact hashes and blockers are recorded in
[`installed-complete-batch-validation-checkpoint.json`](evidence/installed-complete-batch-validation-checkpoint.json).

### Direct exact-edge allocation

Revision `f2230e447c60eefc63adf83289d11d42e1a98f4d` removes the remaining production
per-edge Python call from the hidden output transaction. Kernel v44 requires the canonical `Edge`
type to inherit directly from `object`, retain `object.__new__`, expose exactly the ordered
`source`, `relation`, and `destination` slots as member descriptors, and have no instance
dictionary or weak-reference offset. It then uses stable-ABI generic allocation and descriptor
assignment to create the exact public `Edge` object without invoking its Python factory,
`__init__`, or `__post_init__`. The retained factory identity remains a canonical identity marker,
not a production call site.

The whole chunk is still one native transaction: exact type, all three strings, distinct object
identity, canonical factory identity, and the complete allocation layout are revalidated before
tuple/list construction and cursor, counters, or retained-role commit. The private allocation
probe is `None` in production and exists only to inject adversarial allocation failure or mutation
from tests. Eight exact installed-wheel cases cover constructor bypass on bounded and coarse
surfaces, pre-commit malformed-layout rejection on both, a layout mutation during a bounded drain,
complete-chunk corruption, and exact retry after an injected allocation failure. All 1,149 tests
passed from the exact release-profile wheel plus core `6750aa0`; all 38 Rust tests, rustfmt, Clippy
with warnings denied, Ruff, mypy, the runtime dependency audit, and the 30-member wheel audit
passed. This remains private correctness evidence only and makes no timing or RSS claim. Exact
hashes and blockers are recorded in
[`installed-direct-edge-allocation-checkpoint.json`](evidence/installed-direct-edge-allocation-checkpoint.json).

### Direct exact-statistics allocation

Revision `bb2845fbeb53ad49e80168d8e763affc5b578480` removes the remaining Python
statistics construction call from hidden coarse and batch-session preparation. Kernel v45
validates that the canonical `NativeEncodedDirectStatistics` type inherits directly from `object`,
retains `object.__new__`, exposes exactly its 60 ordered slots as member descriptors, and has no
instance dictionary or weak-reference offset. Stable-ABI generic allocation then assigns each
native integer directly through its validated descriptor. It creates no 60-field argument tuple
and invokes neither the Python statistics factory nor `__init__`/`__post_init__`; the retained
factory remains only a canonical identity marker.

Coarse preparation validates all 60 exact integers and the complete layout before output counters
or retained role state can commit. Batch preparation performs the same validation after its final
iterator callback and before session publication. The private statistics allocation probe is
`None` in production and exists only to inject resource failure, payload corruption, factory-name
mutation, or layout mutation from tests. Seven focused exact installed-wheel cases cover
constructor bypass, malformed layouts, both transaction surfaces, injected allocation failure, and
post-allocation layout mutation. All 1,155 tests passed from the exact release-profile wheel plus
core `6750aa0`; all 38 Rust tests, rustfmt, Clippy with warnings denied, Ruff, mypy, the runtime
dependency audit, and the 30-member wheel audit passed. This remains private correctness evidence
only and makes no timing or RSS claim. Exact hashes and blockers are recorded in
[`installed-direct-statistics-allocation-checkpoint.json`](evidence/installed-direct-statistics-allocation-checkpoint.json).

### Direct exact-iterator allocation

Revision `3860399842385993f58b756062d0e489be0eb160` removes the last final-object Python
construction call from hidden batch-session preparation. Kernel v46 requires the exact
`collections.abc.Iterator` base to be object-sized, inherit `object.__new__`, and have no instance
dictionary or weak-reference offset. The canonical `NativeEncodedDirectBatchIterator` must expose
exactly its eight ordered slots as member descriptors. Stable-ABI generic allocation then assigns
the compiler and direct-allocated statistics identities, caller batch bound, zero yielded/batch/peak
counters, initial boundary count one, and terminal state `active`. It creates no three-field
argument tuple and invokes neither the Python iterator factory nor constructor.

The exact type, factory marker, compatible base and complete layout, owner/statistics identities,
and every initial state field are revalidated before session or retained-role publication. The
private iterator allocation probe is `None` in production and exists only to inject resource
failure, payload/statistics corruption, factory-name mutation, or layout mutation. Five focused
exact installed-wheel cases cover constructor bypass with a complete exact drain, malformed and
changed layouts, injected allocation failure, and revalidation of statistics after the probe. All
1,158 tests passed from the exact release-profile wheel plus core `6750aa0`; all 38 Rust tests,
rustfmt, Clippy with warnings denied, Ruff, mypy, the runtime dependency audit, and the 30-member
wheel audit passed. This remains private correctness evidence only and makes no timing or RSS
claim. Exact hashes and blockers are recorded in
[`installed-direct-iterator-allocation-checkpoint.json`](evidence/installed-direct-iterator-allocation-checkpoint.json).

### Hidden stream-surface integration

Revision `e531a02c5526a60b1d488dd835977394f3c50a9e` routes the proven cursor through
hidden Projector-level protocol-sink, canonical-digest, and portable-artifact adapters. The sink
adapter shares the public version/batch validation and finishing contract. Digest and artifact
adapters share the public single-traversal policy, spill, cancellation, report, hashing, metadata,
atomic-write, and cleanup implementations. No alternate semantic or artifact implementation was
introduced, and the ordinary public methods still follow the absent feature gate.

The exact installed-wheel parity case uses canonical unique output with one duplicated taxonomy
edge and a non-ASCII annotation value. Native compilation produces four raw edges in two
caller-bounded batches with a two-edge peak; policy output contains three edges and one duplicate.
The hidden protocol sink matches scalar batches and report semantics, the canonical digest is
`5f9a126f177f43ca759779294851cfb82661cc0b8bcbec73a03765c4cbe1ede5`, and the
2,049-byte portable artifact is byte-identical to scalar output with artifact digest
`b974d6821c48a41aa6beef0dba6f08158311f31c8fbecc30dd9d4f356fa1e3b1`. A separate
injected sink failure cancels the retained native cursor, leaves zero native edges, and publishes
no report.

Both focused cases and all 1,160 tests passed from the exact release-profile wheel plus core
`6750aa0`; all 38 Rust tests, rustfmt, Clippy with warnings denied, Ruff, mypy, the runtime
dependency audit, and the 30-member wheel audit passed. This closes only the hidden integration
proof: it makes no public-dispatch, packed-record, performance, RSS, or release claim. Exact hashes
and blockers are recorded in
[`installed-stream-surfaces-checkpoint.json`](evidence/installed-stream-surfaces-checkpoint.json).

### Hidden stream-surface measurements

Harness revision `5ac8ef3283bbc4ff1df8be3b3de5b0aaa03705e6` adds four independent private
consumer modes: iterator, protocol sink, canonical digest, and portable artifact. Every sample
records the selected consumer, surface-specific metrics and their SHA-256, the existing counter
and post-view core-operation ledgers, output digest, runtime artifacts, and exact source revisions
in one evidence binding. Invalid surface/public-gate combinations fail before ontology loading.

An exact archived harness ran against the already validated `e531a02` installed product wheel;
the complete `src`, `native`, and build-input trees are identical between those revisions. On the
693-byte packaged consumer fixture, all four surfaces produced eight edges with canonical digest
`ed09a9e55221a6ef533c2f61a05628741946b6fdf61a0c1b45ed051d7660ed76`,
four two-edge native drains, a two-edge peak, and zero per-row FFI. Iterator and sink recorded
observable first-output latency; digest and artifact recorded `null` rather than inventing it.
The artifact contained 2,821 bytes with digest
`c739dd7cee798588088f8425174872f410cb099c11db12e64c687b2cbb49e1bd`.
All four surfaces satisfied the private installed-artifact/counter boundary and remained
`acceptance_ready=false`.

Eleven focused benchmark/configuration cases and all 1,166 exact-archive tests passed; Ruff, mypy,
38 Rust tests, rustfmt, and Clippy with warnings denied also passed. These three-repetition local
numbers are correctness smoke evidence only, not corpus, comparative-performance, RSS, threshold,
or release evidence. Exact hashes, timings, bindings, and limitations are recorded in
[`installed-stream-surface-benchmark-checkpoint.json`](evidence/installed-stream-surface-benchmark-checkpoint.json).

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
`39a5656` through `5ac8ef3`:

| Gate | Result |
|---|---|
| Rust unit tests (`cargo test --no-default-features`) | 38 passed |
| Rust formatting and Clippy with warnings denied | passed |
| Private PyO3 foundation tests | 259 passed |
| Foundation plus private-integration tests | 315 passed |
| Broad encoded-compiler tests | 718 passed |
| Complete projector test suite | 1,166 passed |
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
| Exact-source lazy-cursor invariant | zero preparation emission attempts |
| Exact installed lazy-cursor cases | 11 passed |
| Exact installed lazy-cursor audit/benchmark cases | 11 passed |
| Exact installed coarse-cursor cases | 6 passed |
| Exact installed coarse-cursor audit/benchmark cases | 11 passed |
| Exact installed final-result transaction cases | 7 passed |
| Exact installed final-result audit/benchmark cases | 11 passed |
| Exact installed final-batch transaction cases | 8 passed |
| Exact installed final-batch audit/benchmark cases | 11 passed |
| Exact installed batch-session transaction cases | 9 passed |
| Exact installed batch-session audit/benchmark cases | 11 passed |
| Exact installed iterator-publication transaction cases | 10 passed |
| Exact installed iterator-publication audit/benchmark cases | 11 passed |
| Exact installed final-factory validation cases | 12 passed |
| Exact installed final-factory audit/benchmark cases | 11 passed |
| Exact installed edge-factory validation cases | 18 passed |
| Exact installed edge-factory audit/benchmark cases | 11 passed |
| Exact installed canonical-wrapper cases | 23 passed |
| Exact installed canonical-wrapper audit/benchmark cases | 11 passed |
| Exact installed final-payload validation cases | 28 passed |
| Exact installed final-payload audit/benchmark cases | 11 passed |
| Exact installed complete-batch validation cases | 3 passed |
| Exact installed direct-edge allocation cases | 8 passed |
| Exact installed direct-statistics allocation cases | 7 passed |
| Exact installed direct-iterator allocation cases | 5 passed |
| Exact installed hidden stream-surface cases | 2 passed |
| Exact installed stream-surface benchmark/configuration cases | 11 passed |
| Focused Python Ruff and mypy checks | passed |
| Runtime dependency-boundary audit | passed |
| Exact native-wheel release audit | passed; 30 members |

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

The coarse-result cases additionally cover a 600-edge three-chunk final list, zero complete Rust
vector and intermediate tuple-list counters, and injected final `Edge` and statistics allocation
failures before retained role-state commit. The bounded-iterator cases cover direct final `Edge`
tuple construction and exact retry after a final-edge allocation failure without cursor or counter
movement. Batch-session preparation cases inject final-statistics failure and require an absent
session, zero counters, a released role-use guard, and unchanged retained maps. Iterator-publication
cases inject failure after the final statistics object reaches the iterator factory and require the
same absent session, zero counters, released guard, and unchanged retained maps. Final-factory
validation cases return malformed statistics or iterator objects and require those same
transactional outcomes before any session or retained transition is visible. Edge-factory
validation cases return malformed objects or canonical objects from replaced factories on both
bounded and coarse paths. They require exact bounded retry without cursor/counter movement and
zero coarse counters, released role use, and unchanged retained maps before failure is visible.
Canonical-wrapper cases mutate each replaceable module global from the matching canonical
constructor and require the wrapper to preserve exact bounded progress, coarse/session results,
and committed retained maps by validating against its retained import-time identity.
Final-payload cases instead return exact canonical objects whose fields were changed after
construction. They require exact bounded retry after edge corruption and zero coarse/session
publication, released role use, and unchanged retained maps after edge, statistics, or iterator
payload corruption.
Complete-batch cases make a later edge constructor mutate an earlier exact result or make the
iterator constructor mutate its previously validated statistics object. They require distinct
edge identities, one final whole-chunk validation, exact bounded retry, and zero coarse/session
publication with unchanged retained state.

The benchmark-specific cases additionally prove that public scalar runs retain their existing
surface and rejection behavior; the private candidate selects encoded-native with exact
compile-plus-drain counters; its complete counter/core-operation ledgers and loaded native binary
are hash-bound; it remains unable to satisfy public acceptance; and invalid backend, gate, or
revision combinations fail before ontology loading.

The lifecycle-specific exact-archive cases move an active hidden iterator from its creating thread
to a worker, submit twelve isolated calls on one `Projector` across four workers, drain eleven
remaining edges independently in the parent and child after a quiescent POSIX fork, and leave an
unfinished iterator plus owner retained through clean normal interpreter shutdown. The focused
installed matrix also reruns owner/exporter lifetime, released-GIL concurrent cancellation,
close/sink-failure/fallback cleanup, panic conversion, and legacy native shutdown. This proves the
focused macOS cases, not multithreaded-fork or cross-platform/interpreter acceptance.

The generated differential exact-archive campaign executes 128 distinct mixed-rule sources through
both the independent-bytes and canonical packed-bytes core providers. Its 256 executions produce
6,264 post-policy edges, cover all 32 semantic option combinations and batch bounds one through
seven, and retain scalar ordered-edge, counts, diagnostics, and call-history parity. Every provider
pair has the same edge/count/diagnostic result; every execution selects encoded-native with eleven
zero-copy buffers, zero staging copy, and zero per-row FFI. The case ledger is bound by
`9ada45a1272f351de232876080e0fee0b7be3ccb7183f00e622afd0018070824`.
This is finite valid-input generation, not malformed or coverage-guided fuzzing.

The invalid encoded-column exact-archive campaign executes the maximum 256 generated sources
through both supported direct providers. Its 29 predefined cases per provider/source produce
14,848 validation executions across tag, reference, canonicality, offset, shape, and scalar
categories while exercising all eleven structural columns. Every execution raises
`SnapshotCompatibilityError` before output through both the direct compiler and hidden Projector,
leaves the compiler in terminal failed state without a batch session, output counters, edges, or
report, and matches the other provider layout. Each loaded native view is closed after its
provider case set, including injected-failure cleanup. The case ledger is bound by
`c2ff74c6d1168198cf5cab7483d09b585b08c5611322bcc436c2e61e939c3fd4`.
This is finite deterministic invalid-fixture evidence, not mutational or coverage-guided fuzzing,
sanitizer evidence, or an exhaustive invalid-input proof.

Revision `fc15ea747ac63135c3476e85c721e549ebb7f833` connects one canonical zero-delta
overlay alias to the hidden direct kernel. The adapter accepts only a one-segment
`OVERLAY_BASE`/`ALL` container with canonical empty local columns, postings, and anonymous-scope
map; it independently revalidates the referenced exact-direct source and passes only that source's
eleven borrowed buffers to Rust. The compilation retains both source and container leases,
reporting 22 retained zero-copy buffers, 11 detached native inputs, two segments, one referenced
view, 370 encoded bytes, and zero posting, staging, structural-copy, flattening,
scalar-materialization, or per-row-FFI work for the installed fixture. Both independent- and
packed-bytes providers produce the same one edge and semantic report as scalar Python. Edited
overlays and aliases requiring root-scoped annotation provenance select one whole-operation
scalar-native compiler; a malformed referenced column fails with `SnapshotCompatibilityError`
before output and report publication.

The exact `fc15ea7` archive built a fresh release-profile abi3 wheel and passed all 1,189 tests
from the isolated installed projector/core payload, including the five focused alias,
fallback, and invalid-source cases. All 38 Rust tests, rustfmt, Clippy with warnings denied, Ruff,
mypy, the runtime dependency boundary, and the 30-member native-wheel audit passed. The public
feature ledger remains exactly `abi3-py310` and `bounded-batches`; ordinary public native
projection still reports scalar-native with zero native counters. Exact hashes, build facts, and
limitations are recorded in
[`installed-empty-overlay-alias-checkpoint.json`](evidence/installed-empty-overlay-alias-checkpoint.json).
This is focused private correctness evidence, not recursive overlay, composite, mmap, corpus,
performance, or public-acceptance evidence.

Revision `3ef4f15b13758582c05fde228c1274947a636aee` generalizes that alias seam to a
bounded iterative chain. Every retained container must independently satisfy the same canonical
empty `OVERLAY_BASE`/`ALL` contract, and the terminal source must independently satisfy the full
exact-direct contract. Only the terminal eleven buffers enter Rust; all container leases remain
owned by the prepared compilation. A three-alias installed fixture reports 44 retained zero-copy
buffers, 11 detached native inputs, four segments, three referenced views, 390 encoded bytes, and
zero posting, staging, structural-copy, flattening, scalar-materialization, or per-row-FFI work for
both direct exporter layouts. Exact scalar edge and report parity is unchanged.

Resolution uses explicit iteration and an active identity set. The top owner's public
`max_overlay_depth` and cumulative `max_canonical_work` limits cover the entire retained chain.
Focused installed tests prove that every owner remains alive while a cursor is active and is
released after close; reduced depth and work budgets and a transitive cycle fail with
`SnapshotCompatibilityError` before output or report publication. A separate installed probe
admits the default maximum of 32 aliases and rejects 33 before report publication.

The exact `3ef4f15` archive built a fresh release-profile abi3 wheel and passed all 1,195 tests
from the isolated installed projector/core payload, including all six focused recursive alias
cases. The source implementation had already passed the 1,195-test complete suite, 71 private
integration cases, Ruff, mypy over all 17 source files, 38 Rust tests, rustfmt, and Clippy with
warnings denied. Runtime dependency and 30-member native-wheel audits passed. The public feature
ledger remains exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still
reports scalar-native with zero native counters. Exact hashes, build facts, and limitations are
recorded in
[`installed-recursive-empty-overlay-alias-checkpoint.json`](evidence/installed-recursive-empty-overlay-alias-checkpoint.json).
This remains focused private correctness evidence, not posting, local-delta, composite, mmap,
corpus, performance, public-dispatch, or release-acceptance evidence.

Revision `c8b675587a364e3deb9b0f2e05725487aea7df56` admits one canonical nonempty
`EXCLUDE` posting table on the terminal-adjacent alias in the bounded empty-local chain. The
resolver retains the container owning that exact immutable exporter and passes its complete
read-only byte view beside the terminal source's eleven buffers. Kernel v47 validates complete
little-endian `u32` rows as strictly increasing, unique, 1-based positions within the terminal
source's root count, then binary-searches that table in place. It applies the selection to root
classification, role-state construction, anonymous-ID reachability, equivalence and annotation
scans, restriction and domain/range counting, and every cursor emission scan. The complete
terminal source remains structurally and semantically validated, including excluded roots. No
root-selection index, flattened base, staging buffer, scalar ontology value, or per-row FFI call
is created.

Posting positions are local to their immediate referenced view. In an empty-local alias chain,
only the alias directly referencing the terminal direct view can therefore carry a nonempty valid
posting table. Corrective revision `5ada96a0751f7a1267fa42136e30761b94dbd8a9` records that
referenced identity and requires it to be the fully revalidated terminal source before selecting
the native cursor. Real adapter validation rejects a nonterminal posting as an out-of-range
source-local reference; if a forged prevalidated lease bypasses that boundary, the internal
resolver selects whole-operation fallback with zero native counters.

The focused installed matrix covers both independent- and packed-bytes direct exporters. Its
fourteen provider/removal cases exercise taxonomy and restriction roots, subrole and inverse
state, domain and range products, silent annotations, and nonadjacent projecting roots. Additional
cases prove scalar-compatible anonymous-ID recomputation from retained roots, encoded-native
zero-output removal, and one terminal-adjacent exclusion through a three-container owner chain.
A nonterminal or second exclusion layer selects one whole-operation scalar compiler. Eight direct
foundation cases require a complete immutable posting exporter and typed pre-output rejection of
partial, zero, out-of-range, duplicate, and descending posting rows plus sliced and mutable
exporters. One-container provenance reports 22 retained structural buffers, 12 native inputs
including the posting exporter, one referenced view, two segments, exact posting bytes, and zero
indexing, flattening, staging copy, scalar materialization, or per-row FFI.

The exact `5ada96a` archive built a fresh release-profile abi3 wheel and passed all 1,223 tests
from the isolated installed projector/core payload. The installed focused runs passed 21
excluding-overlay integration cases, including all three scope-correction cases, and eight
direct-foundation posting cases. The source implementation passed the 1,223-test complete suite,
all 91 private integration cases, all 267 native-foundation cases, Ruff, mypy over all 17 source
files, 41 Rust tests, rustfmt, and Clippy with warnings denied. Runtime dependency and 30-member
native-wheel audits passed. The public feature ledger remains exactly `abi3-py310` and
`bounded-batches`; ordinary public native projection still reports scalar-native with zero native
counters. Exact hashes, build facts, and limitations are recorded in
[`installed-excluding-overlay-alias-checkpoint.json`](evidence/installed-excluding-overlay-alias-checkpoint.json).
This remains focused private correctness evidence, not multiple-selection composition, `INCLUDE`,
local-delta, annotation-sensitive alias, composite, mmap, corpus, performance, public-dispatch, or
release-acceptance evidence.

### Bounded canonical root-merger foundation

Revision `aa10c619befcf199980e0b45f1eaa680be62072f` adds the dormant Rust foundation
required to compare and merge independently encoded root tables. It memoizes canonical node
lengths through an iterative graph walk, streams canonical-model bytes through two reusable
cursors, validates each root group as strictly ordered and structurally unique, and merges selected
roots in exact kind-then-node order. Equal roots advance both inputs, retain the left identity, and
increment one structural-deduplication counter. A monotone per-side cursor consumes sorted
`EXCLUDE` rows without constructing a selection index.

The streamed representation covers every structural component: unsigned-varint node tags; none;
length-framed node references; UTF-8 text, bytes, and nonempty ASCII enums; arbitrary-width
minimal little-endian integers rendered as unsigned varints; canonical sets of length-framed
nodes; and ordered sequences of fully framed leaves. Node IDs and scalar offsets remain
table-local. Consequently, every canonical set is independently checked by streamed same-table
comparison of adjacent children; numeric item order alone is not accepted as proof of canonical
order. Generic columns, scalar kinds, actual root order, and arbitrary direct or transitive node
cycles all fail closed before a merged root can be returned.

Construction requires explicit positive work and workspace limits. Table validation, scalar
validation, iterative graph frames, component-length passes, every emitted cursor task, root
comparison, and root/posting selection are charged with checked arithmetic. Dense length maps,
temporary color maps and pre-sized graph frames, and incrementally grown byte-cursor stacks use
fallible, capacity-accounted allocation. Temporary workspace is released from the live ledger
after preflight while its peak remains recorded. The foundation neither reconstructs OWL values
nor allocates a canonical byte arena.

The source-bound matrix passes independent offsets and node IDs, all component kinds,
arbitrary-width integer equality and ordering, selected-root merge order, cross-table
deduplication, invalid text and enum payloads, reversed or equal root groups, a forged set whose
numeric IDs increase while its actual bytes decrease, a 4,096-node chain, transitive-cycle
rejection, work exhaustion before and during comparison, workspace exhaustion, and cancellation.
All 47 Rust tests, rustfmt, and Clippy with warnings denied pass. The unchanged Python surface
passes all 1,223 tests, Ruff, and strict mypy over 17 source files.

This is source-only foundation evidence. It does not enter adapter negotiation or kernel
execution, increment kernel v47, admit `OVERLAY_DELTA`, or alter the feature ledger. Exact source
hashes, gate commands, resource semantics, and open limits are recorded in
[`source-canonical-root-merger-foundation.json`](evidence/source-canonical-root-merger-foundation.json).

### Bounded one-root local overlay delta

Revision `b4b808a3991b38e143d9b2238c9cccf356c0cdd5` connects the reviewed merger to
one exact local `OVERLAY_DELTA` slice in kernel v48. The adapter admits only a two-segment top
manifest containing `OVERLAY_BASE` and `OVERLAY_DELTA`, both with `ALL` postings, empty anonymous
scope maps, no member tokens, and exactly one local root. The referenced same-scope source is
independently revalidated as exact-direct. Rust then rebinds both owners, roles, sources, posting
metadata, descriptors, and complete eleven-column tables before semantic preflight.

The one local root must be an unannotated named-to-named `SubClassOf`. The bounded canonical merger
validates both tables and derives one scalar absolute insertion position; it does not construct a
root-selection index, canonical-byte arena, flattened base, or copied structural graph. Duplicate
base/local identity and every adjacent local shape select one whole-operation fallback.
`include_literals` and retained Scala-instance state also remain outside this slice. Canonical work
and workspace exhaustion fail before output, and the existing cursor clone/publish transaction
keeps the insertion and native counters retry-safe.

The exact installed matrix uses both independent- and packed-bytes core exporters. A base with
three declarations plus `SubClassOf(A, B)` and one local `SubClassOf(B, C)` produces the exact two
scalar-ordered edges and report under both providers. Each execution reports 22 retained and 22
native-input zero-copy buffers, 795 encoded bytes, three segments, one referenced view, two
one-edge batches, and zero posting/index, flattening, staging, structural-copy, scalar
materialization, output-vector, or per-row-FFI work. The focused matrix also proves whole-call
fallback for multiple local roots, a local restriction, a local declaration, and literal-sensitive
projection, plus typed pre-output work and workspace failures.

The exact `b4b808a` archive built a fresh release-profile abi3 wheel and passed all 1,231 tests from
the isolated installed projector/core payload. All eight focused local-delta cases, 15 benchmark
cases, 50 Rust tests, rustfmt, Clippy with warnings denied, Ruff, strict mypy over 17 source files,
the runtime dependency boundary, and the 30-member native-wheel audit passed. The public feature
ledger remains exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still
uses scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-overlay-delta-checkpoint.json`](evidence/installed-one-root-overlay-delta-checkpoint.json).
This remains focused private correctness evidence, not nested-delta, local-deletion, composite,
mmap, corpus, performance, public-dispatch, or release-acceptance evidence.

### Bounded mixed exclusion and local delta

Revision `f9af12a6779663cf89eac89e3f522e6f265188d0` extends the same one-root
kernel slice to a base segment carrying one nonempty `EXCLUDE` posting table. The top manifest
remains exactly `OVERLAY_BASE` followed by `OVERLAY_DELTA`; the delta posting mode remains `ALL`.
The adapter retains the exact immutable base posting exporter, and the PyO3 seam rejects an
equal-bytes replacement with different object identity. Rust independently revalidates the
complete direct source and both top-local segments before applying the selection.

The canonical merger advances selected roots directly from the sorted posting cursor. It compares
the local root against the selected base sequence and derives one scalar absolute scan insertion
position from the preceding selected base root. Excluded gaps, a trailing excluded root, and an
exclude-all base therefore preserve exact canonical output without a selected-root vector,
selection index, flattened base, copied structural graph, or canonical-byte arena. The existing
cursor transaction also keeps an uncommitted mixed-overlay batch exactly retryable. Invalid
posting rows, canonical work or workspace exhaustion, and cancellation fail before publication;
multiple local roots continue to select whole-operation fallback.

The exact installed matrix covers independent- and packed-bytes core exporters crossed with
exclusion before the local root, after the local root, and of every base root. The representative
three-root fixture excludes `C`, inserts `D`, and emits `A`, `D`, `E` in three one-edge batches
under both providers. Each execution reports 22 structural zero-copy buffers, 23 detached native
inputs and retained buffers including the posting exporter, 884 structural bytes, four posting
bytes, three segments, one referenced view, and zero indexing, flattening, staging,
structural-copy, scalar materialization, output-vector, or per-row-FFI work.

The exact `f9af12a` archive built a fresh release-profile abi3 wheel and passed all 1,239 tests
from the isolated installed projector/core payload. All 107 private integration cases, the ten
focused mixed-overlay cases, 15 benchmark cases, 52 Rust tests, rustfmt, Clippy with warnings
denied, Ruff, strict mypy over 17 source files, the runtime dependency boundary, and the 30-member
native-wheel audit passed. The public feature ledger remains exactly `abi3-py310` and
`bounded-batches`; ordinary public native projection still uses scalar-native with zero native
counters. Exact archive, wheel, installed-payload, counter, and gate hashes are recorded in
[`installed-mixed-one-root-overlay-delta-checkpoint.json`](evidence/installed-mixed-one-root-overlay-delta-checkpoint.json).
This remains focused private correctness evidence, not multi-root delta, local deletion,
`INCLUDE`, nested-delta, composite, mmap, corpus, performance, public-dispatch, or
release-acceptance evidence.

### Bounded one-root declaration delta

Revision `0999fc559eb4f8a64385e0ab351bd39c0b2cdc55` admits one unannotated local
`Declaration` through kernel v50. The declaration may name a class, object property, data
property, annotation property, named individual, or datatype. The top manifest remains exactly
`OVERLAY_BASE` plus `OVERLAY_DELTA`; its base may use `ALL` or the existing one exact nonempty
`EXCLUDE` posting table, while the delta remains `ALL`. Annotated declarations, duplicate
base/local identity, and every second local root still select whole-operation fallback.

Rust fully validates the declaration entity, IRI bound, empty annotation set, complete local
columns, and canonical position through the same bounded merger. It adds exactly one root,
the local node count and buffer bytes, and one declaration to the statistics. Because declarations
are silent and state-neutral in the pinned profile, it adds no edge or role state and retains no
emitting overlay-delta record. The base cursor therefore drains unchanged, including a clean
already-exhausted result when the base exclusion removes every projecting root. No selected-root
index, flattened base, copied structural graph, canonical-byte arena, scalar ontology value, or
per-row FFI call is created.

The exact installed matrix crosses all six named entity kinds with both independent- and
packed-bytes exporters and base `ALL`, partial `EXCLUDE`, and exclude-all selection: 36 supported
executions with exact scalar edge/report parity. Three adjacent cases retain whole-call fallback
for multiple roots, a local restriction, and an annotated declaration. The representative partial
exclusion retains 23 native inputs including the posting exporter, reports 599 structural bytes,
one declaration, one subclass and one edge, and uses two boundary calls. Excluding both base roots
reports the same one declaration with zero subclasses, edges, edge batches, or peak buffered
edges, and publishes after one boundary call. Both layouts have identical counters.

The exact `0999fc5` archive built a fresh release-profile abi3 wheel and passed all 1,275 tests
from the isolated installed projector/core payload. All 143 private integration cases, the 39
focused declaration/fallback cases, 15 benchmark cases, 53 Rust tests, rustfmt, Clippy with
warnings denied, Ruff, strict mypy over 17 source files, the runtime dependency boundary, and the
30-member native-wheel audit passed. The public feature ledger remains exactly `abi3-py310` and
`bounded-batches`; ordinary public native projection still uses scalar-native with zero native
counters. Exact archive, wheel, installed-payload, counter, and gate hashes are recorded in
[`installed-one-root-declaration-delta-checkpoint.json`](evidence/installed-one-root-declaration-delta-checkpoint.json).
This remains focused private correctness evidence, not local restriction, annotated or multi-root
delta, local deletion, `INCLUDE`, nested-delta, composite, mmap, corpus, performance,
public-dispatch, or release-acceptance evidence.

### Bounded one-root restriction delta

Revision `d8cc9f70a08ef66df08054d4b2bcd53f717027da` admits one unannotated local
named-role restriction `SubClassOf` through kernel v51. The restriction may be
`ObjectSomeValuesFrom`, `ObjectAllValuesFrom`, `ObjectMinCardinality`, or
`ObjectMaxCardinality`; either subclass operand may carry it, its property may be named or
inverse, and its other operand and filler must be named classes. The top manifest remains exactly
`OVERLAY_BASE` plus `OVERLAY_DELTA`; its base may use `ALL` or the existing one exact nonempty
`EXCLUDE` posting table, while the delta remains `ALL`. An annotation, complex filler, second
local root, unsupported restriction constructor, or other local logical shape still selects one
whole-operation fallback.

Rust fully validates both tables and derives the local root's scalar absolute insertion position
through the existing bounded canonical merger. The prepared local record retains only its three
owned IRI strings and insertion scalar. At emission, it uses the already selected base role state:
the direct relation is followed by retained subroles and then the retained inverse edge, exactly
matching the pinned scalar behavior. Excluding the subproperty or inverse root removes only that
expansion from the local edge. `only_taxonomy` and asserted-taxonomy mode preserve the local
subclass/restriction counts but emit no local edge. Exact edge and role-expansion totals are
checked against the caller bound before publication. No selected-root index, flattened base,
copied structural graph, canonical-byte arena, scalar ontology value, complete output vector, or
per-row FFI call is created.

The exact installed matrix covers both independent- and packed-bytes exporters; all four
constructors; named and inverse properties; both restriction orientations; an `ALL` base;
subproperty and inverse `EXCLUDE` selection; and zero-edge taxonomy-only projection. Four adjacent
cases retain whole-call fallback for multiple local roots, an annotated restriction, a complex
restriction filler, and an annotated declaration. The representative full-role case reports 905
structural bytes, 22 retained zero-copy buffers, one restriction subclass, two role-expansion
edges, and three one-edge batches. Excluding the subproperty retains the exact four-byte posting
exporter as the twenty-third native input and emits only the direct and inverse edges. The
taxonomy-only case reports the same validated roots and nodes with zero edges, batches, or peak
buffered edges. Both provider layouts have identical ordered edges, reports, statistics, and
counters.

The exact `d8cc9f7` archive built a fresh release-profile abi3 wheel and passed all 1,290 tests
from the isolated installed projector/core payload. All 158 private integration cases, the 18
focused restriction/fallback cases, 15 benchmark cases, 55 Rust tests, rustfmt, Clippy with
warnings denied, Ruff, strict mypy over 17 source files, the runtime dependency boundary, and the
30-member native-wheel audit passed. The public feature ledger remains exactly `abi3-py310` and
`bounded-batches`; ordinary public native projection still uses scalar-native with zero native
counters. Exact archive, wheel, installed-payload, counter, and gate hashes are recorded in
[`installed-one-root-restriction-delta-checkpoint.json`](evidence/installed-one-root-restriction-delta-checkpoint.json).
This remains focused private correctness evidence, not annotated or multi-root delta, local
deletion, `INCLUDE`, nested-delta, composite, mmap, corpus, performance, public-dispatch, or
release-acceptance evidence.

### Bounded one-root class-assertion delta

Revision `0f4c7cc336a2bf5e1125fbd1563c216bfd651c7f` admits one unannotated local
`ClassAssertion` over a named class and named individual through kernel v52. The top manifest
remains exactly `OVERLAY_BASE` plus `OVERLAY_DELTA`; its base may use `ALL` or the existing one
exact nonempty `EXCLUDE` posting table, while the delta remains `ALL`. An annotated assertion,
anonymous individual, complex asserted class, duplicate base/local identity, second local root,
or other unsupported local shape still selects whole-operation fallback or typed pre-output
rejection.

Rust fully validates both tables and uses the bounded canonical merger to derive the local root's
absolute insertion position. The cursor retains the local individual and class IRI strings until
the canonical class-assertion phase, then emits exactly one `http://type` edge. This preserves the
pinned historical behavior in which `only_taxonomy` still emits class assertions; the separate
asserted-taxonomy mode suppresses the edge while retaining the root and class-assertion counts.
The complete edge total is checked against the caller bound before publication, and cursor retry
before commit preserves identical output. No selected-root index, flattened base, copied
structural graph, canonical-byte arena, scalar ontology value, complete output vector, or per-row
FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters with an `ALL` base,
exclusion before the local root, exclusion after it, exclusion of every base root, and
`only_taxonomy`: ten supported executions with exact scalar ordered-edge/report parity. Seven
adjacent cases retain whole-call fallback for multiple roots, annotated or complex restrictions,
annotated/anonymous/complex class assertions, and an annotated declaration. The representative
`ALL` case reports 847 structural bytes, 22 retained zero-copy buffers, three class assertions,
and three one-edge batches. A partial exclusion retains the exact four-byte posting exporter as
the twenty-third native input and emits two edges; excluding both base roots retains an eight-byte
posting table and emits only the local edge. Both provider layouts have identical results,
statistics, and counters.

The exact `0f4c7cc` archive built a fresh release-profile abi3 wheel and passed all 1,303 tests
from the isolated installed projector/core payload. All 171 private integration cases, the 17
focused class-assertion/fallback cases, 15 benchmark cases, 57 Rust tests, rustfmt, Clippy with
warnings denied, Ruff, strict mypy over 17 source files, the installed smoke, runtime dependency
boundary, and 30-member native-wheel audit passed. The public feature ledger remains exactly
`abi3-py310` and `bounded-batches`; ordinary public native projection still uses scalar-native
with zero native counters. Exact archive, wheel, installed-payload, counter, and gate hashes are
recorded in
[`installed-one-root-class-assertion-delta-checkpoint.json`](evidence/installed-one-root-class-assertion-delta-checkpoint.json).
This remains focused private correctness evidence, not annotated or multi-root delta, local
object-property assertion, local deletion, `INCLUDE`, nested-delta, composite, mmap, corpus,
performance, public-dispatch, or release-acceptance evidence.

### Bounded one-root object-property-assertion delta

Revision `651e9d59bc7449a424a7769408851293586a5259` admits one unannotated local positive
`ObjectPropertyAssertion` over a named property and named source/destination individuals through
kernel v53. The top manifest remains exactly `OVERLAY_BASE` plus `OVERLAY_DELTA`; its base may use
`ALL` or the existing one exact nonempty `EXCLUDE` posting table, while the delta remains `ALL`.
An annotated assertion, anonymous individual, duplicate base/local identity, second local root, or
other unsupported local shape still selects whole-operation fallback or typed pre-output
rejection. An inverse local property is explicitly rejected as `ReferenceFailure` before output
rather than being interpreted as a named property.

Rust fully validates both tables and uses the bounded canonical merger to derive the local root's
absolute insertion position. The cursor retains the local subject, property, and object IRI
strings until the canonical positive-object-assertion phase, then emits exactly one asserted edge.
This preserves the pinned historical behavior in which `only_taxonomy` still emits positive
object assertions; the separate asserted-taxonomy mode suppresses the edge while retaining the
root and object-property-assertion counts. The complete edge total is checked against the caller
bound before publication, and cursor retry before commit preserves identical output. No
selected-root index, flattened base, copied structural graph, canonical-byte arena, scalar
ontology value, complete output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters with an `ALL` base,
exclusion before the local root, exclusion after it, exclusion of every base root, and
`only_taxonomy`: ten supported executions with exact scalar ordered-edge/report parity. Nine
adjacent integration cases retain whole-call fallback, including annotated and anonymous local
object assertions; a separate Rust boundary case pins inverse-property rejection before output.
The representative `ALL` case reports 1,151 structural bytes, 22 retained zero-copy buffers,
three object-property assertions, and three one-edge batches. A partial exclusion retains the
exact four-byte posting exporter as the twenty-third native input and emits two edges; excluding
both base roots retains an eight-byte posting table and emits only the local edge. Both provider
layouts have identical results, statistics, and counters.

The exact `651e9d5` archive built a fresh release-profile abi3 wheel and passed all 1,315 tests
from the isolated installed projector/core payload. All 183 private integration cases, the 19
focused object-assertion/adjacent-fallback cases, 15 benchmark cases, 59 Rust tests, rustfmt,
Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke, runtime
dependency boundary, and 30-member native-wheel audit passed. The public feature ledger remains
exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-object-assertion-delta-checkpoint.json`](evidence/installed-one-root-object-assertion-delta-checkpoint.json).
This remains focused private correctness evidence, not negative/annotated/multi-root delta, local
deletion, `INCLUDE`, nested-delta, composite, mmap, corpus, performance, public-dispatch, or
release-acceptance evidence.

### Bounded one-root negative-object-property-assertion delta

Revision `a9c3b033f7282984d1302a4fd7ff1d3b75f6ca81` admits one unannotated local
`NegativeObjectPropertyAssertion` over named source/destination individuals and either a named or
inverse named property through kernel v54. The top manifest remains exactly `OVERLAY_BASE` plus
`OVERLAY_DELTA`; its base may use `ALL` or the existing one exact nonempty `EXCLUDE` posting
table, while the delta remains `ALL`. An annotated or anonymous-individual assertion, duplicate
base/local identity, second local root, or other unsupported local shape still selects
whole-operation fallback or typed pre-output rejection.

Rust fully validates both tables and uses the bounded canonical merger to derive the silent local
root's absolute insertion position. The negative assertion emits no edge and therefore retains no
owned emitting delta record. Normal and `only_taxonomy` projection preserve the exact
negative-object-property-assertion count and skipped-axiom diagnostic; asserted-taxonomy preserves
the constructor count but suppresses the skip diagnostic, matching the pinned scalar behavior.
The base edge total is checked against the caller bound before publication. No selected-root
index, flattened base, copied structural graph, canonical-byte arena, scalar ontology value,
complete output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters, named and inverse
named local properties, and an `ALL` base, one partial base exclusion, exclusion of every base
root, and `only_taxonomy`: sixteen supported executions with exact scalar ordered-edge/report
parity. Eleven adjacent integration cases retain whole-call fallback, including annotated and
anonymous-individual local negative assertions. The representative named-property `ALL` case
reports 865 structural bytes, 22 retained zero-copy buffers, one skipped negative assertion, and
two one-edge base batches; the inverse-property variant reports 892 bytes and identical projected
edges. A partial exclusion retains the exact four-byte posting exporter as the twenty-third
native input and emits one base edge. Excluding both base roots retains an eight-byte posting
table, publishes the local root and diagnostic with zero edges, and performs only the initial
native boundary call. Both provider layouts have identical results, statistics, and counters.

The exact `a9c3b03` archive built a fresh release-profile abi3 wheel and passed all 1,333 tests
from the isolated installed projector/core payload. All 201 private integration cases, the 27
focused negative-object-assertion/adjacent-fallback cases, 15 benchmark cases, 60 Rust tests,
rustfmt, Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke,
runtime dependency boundary, and 30-member native-wheel audit passed. The public feature ledger
remains exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-negative-object-assertion-delta-checkpoint.json`](evidence/installed-one-root-negative-object-assertion-delta-checkpoint.json).
This remains focused private correctness evidence, not local data-property assertions,
annotated/anonymous/multi-root delta, local deletion, `INCLUDE`, nested-delta, composite, mmap,
corpus, performance, public-dispatch, or release-acceptance evidence.

### Bounded one-root data-property-assertion delta

Revision `56f4e045654d89cfe060255d1db64e5add581ced` admits one unannotated local
`DataPropertyAssertion` over a named data property, named source individual, and fully validated
literal through kernel v55. The top manifest remains exactly `OVERLAY_BASE` plus
`OVERLAY_DELTA`; its base may use `ALL` or the existing one exact nonempty `EXCLUDE` posting
table, while the delta remains `ALL`. An annotated or anonymous-source assertion, duplicate
base/local identity, second local root, or other unsupported local shape still selects
whole-operation fallback or typed pre-output rejection.

Rust fully validates both tables, including the literal lexical form, datatype, and language
components, and uses the bounded canonical merger to derive the silent local root's absolute
insertion position. The assertion emits no edge because literal projection is excluded from this
slice, and therefore retains no owned emitting delta record. Normal and `only_taxonomy`
projection preserve the exact data-property-assertion count and skipped-axiom diagnostic;
asserted-taxonomy preserves the constructor count but suppresses that diagnostic, matching the
pinned scalar behavior. The base edge total is checked against the caller bound before
publication. No selected-root index, flattened base, copied structural graph, canonical-byte
arena, scalar ontology value, complete output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters, string and typed
integer literals, and an `ALL` base, one partial base exclusion, exclusion of every base root, and
`only_taxonomy`: sixteen supported executions with exact scalar ordered-edge/report parity.
Thirteen adjacent integration cases retain whole-call fallback, including annotated and
anonymous-source local data assertions. The representative string-literal `ALL` case reports 953
structural bytes, 22 retained zero-copy buffers, one skipped data assertion, and two one-edge base
batches; the typed-integer variant reports 934 bytes and identical projected edges. A partial
exclusion retains the exact four-byte posting exporter as the twenty-third native input and emits
one base edge. Excluding both base roots retains an eight-byte posting table, publishes the local
root and diagnostic with zero edges, and performs only the initial native boundary call. Both
provider layouts have identical results, statistics, and counters.

The exact `56f4e04` archive built a fresh release-profile abi3 wheel and passed all 1,351 tests
from the isolated installed projector/core payload. All 219 private integration cases, the 29
focused data-assertion/adjacent-fallback cases, 15 benchmark cases, 61 Rust tests, rustfmt, Clippy
with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke, runtime
dependency boundary, and 30-member native-wheel audit passed. The public feature ledger remains
exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-data-assertion-delta-checkpoint.json`](evidence/installed-one-root-data-assertion-delta-checkpoint.json).
This remains focused private correctness evidence, not local negative-data-property assertions,
annotated/anonymous/multi-root delta, literal-emitting projection, local deletion, `INCLUDE`,
nested-delta, composite, mmap, corpus, performance, public-dispatch, or release-acceptance
evidence.

### Bounded one-root negative-data-property-assertion delta

Revision `8b2450028a3013e6917c4c931f087fe40d52a516` admits one unannotated local
`NegativeDataPropertyAssertion` over a named data property, named source individual, and fully
validated literal through kernel v56. The top manifest remains exactly `OVERLAY_BASE` plus
`OVERLAY_DELTA`; its base may use `ALL` or the existing one exact nonempty `EXCLUDE` posting
table, while the delta remains `ALL`. An annotated or anonymous-source assertion, duplicate
base/local identity, second local root, or other unsupported local shape still selects
whole-operation fallback or typed pre-output rejection.

Rust fully validates both tables, including the literal lexical form, datatype, and language
components, and requires the root tag and exact negative-data-assertion count to agree before
using the bounded canonical merger. The assertion emits no edge and therefore retains no owned
emitting delta record. Normal and `only_taxonomy` projection preserve the exact
negative-data-property-assertion count and skipped-axiom diagnostic; asserted-taxonomy preserves
the constructor count but suppresses that diagnostic, matching the pinned scalar behavior. The
base edge total is checked against the caller bound before publication. No selected-root index,
flattened base, copied structural graph, canonical-byte arena, scalar ontology value, complete
output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters, string and typed
integer literals, and an `ALL` base, one partial base exclusion, exclusion of every base root, and
`only_taxonomy`: sixteen supported executions with exact scalar ordered-edge/report parity.
Fifteen adjacent integration cases retain whole-call fallback, including annotated and
anonymous-source local negative data assertions. The representative string-literal `ALL` case
reports 955 structural bytes, 22 retained zero-copy buffers, one skipped negative data assertion,
and two one-edge base batches; the typed-integer variant reports 934 bytes and identical
projected edges. A partial exclusion retains the exact four-byte posting exporter as the
twenty-third native input and emits one base edge. Excluding both base roots retains an eight-byte
posting table, publishes the local root and diagnostic with zero edges, and performs only the
initial native boundary call. Both provider layouts have identical results, statistics, and
counters.

The exact `8b24500` archive built a fresh release-profile abi3 wheel and passed all 1,369 tests
from the isolated installed projector/core payload. All 237 private integration cases, the 31
focused negative-data-assertion/adjacent-fallback cases, 15 benchmark cases, 62 Rust tests,
rustfmt, Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke,
runtime dependency boundary, and 30-member native-wheel audit passed. The public feature ledger
remains exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-negative-data-assertion-delta-checkpoint.json`](evidence/installed-one-root-negative-data-assertion-delta-checkpoint.json).
This remains focused private correctness evidence, not annotated/anonymous/multi-root delta,
literal-emitting projection, local deletion, `INCLUDE`, nested-delta, composite, mmap, corpus,
performance, public-dispatch, or release-acceptance evidence.

### Bounded one-root sub-data-property delta

Revision `9e069e05c0cc3f32e564beb6a7c1000f3d179258` admits one unannotated local
`SubDataPropertyOf` over two named data properties through kernel v57. The top manifest remains
exactly `OVERLAY_BASE` plus `OVERLAY_DELTA`; its base may use `ALL` or the existing one exact
nonempty `EXCLUDE` posting table, while the delta remains `ALL`. An annotated axiom, duplicate
base/local identity, second local root, or other unsupported local shape still selects
whole-operation fallback or typed pre-output rejection.

Rust fully validates both tables, requires the root tag and exact sub-data-property count to
agree, and validates both data-property entities before using the bounded canonical merger. The
axiom emits no edge and therefore retains no owned emitting delta record. Normal and
`only_taxonomy` projection preserve the exact sub-data-property count and skipped-axiom
diagnostic; asserted-taxonomy preserves the constructor count but suppresses that diagnostic,
matching the pinned scalar behavior. The base edge total is checked against the caller bound
before publication. No selected-root index, flattened base, copied structural graph,
canonical-byte arena, scalar ontology value, complete output vector, or per-row FFI call is
created.

The exact installed matrix crosses independent- and packed-bytes exporters with an `ALL` base,
one partial base exclusion, exclusion of every base root, and `only_taxonomy`: eight supported
executions with exact scalar ordered-edge/report parity. Sixteen adjacent integration cases
retain whole-call fallback, including an annotated local sub-data-property axiom. The
representative `ALL` case reports 734 structural bytes, 22 retained zero-copy buffers, one skipped
sub-data-property axiom, and two one-edge base batches. A partial exclusion retains the exact
four-byte posting exporter as the twenty-third native input and emits one base edge. Excluding
both base roots retains an eight-byte posting table, publishes the local root and diagnostic with
zero edges, and performs only the initial native boundary call. Both provider layouts have
identical results, statistics, and counters.

The exact `9e069e0` archive built a fresh release-profile abi3 wheel and passed all 1,378 tests
from the isolated installed projector/core payload. All 246 private integration cases, the 24
focused sub-data-property/adjacent-fallback cases, 15 benchmark cases, 63 Rust tests, rustfmt,
Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke, runtime
dependency boundary, and 30-member native-wheel audit passed. The public feature ledger remains
exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-sub-data-property-delta-checkpoint.json`](evidence/installed-one-root-sub-data-property-delta-checkpoint.json).
This remains focused private correctness evidence, not other local data-property axioms,
annotated/multi-root delta, local deletion, `INCLUDE`, nested-delta, composite, mmap, corpus,
performance, public-dispatch, or release-acceptance evidence.

### Bounded one-root equivalent data properties delta

Revision `285a46a054dbbcf522719b9042dda1c1c914f4b4` admits one unannotated local
`EquivalentDataProperties` over a canonical binary or ternary set of named data properties
through kernel v58. The top manifest remains exactly `OVERLAY_BASE` plus `OVERLAY_DELTA`; its
base may use `ALL` or the existing one exact nonempty `EXCLUDE` posting table, while the delta
remains `ALL`. An annotated axiom, duplicate base/local identity, second local root, noncanonical
or duplicate property set, or other unsupported local shape still selects whole-operation
fallback or typed pre-output rejection.

Rust fully validates both tables, requires the root tag and exact equivalent-data-properties
count to agree, validates every named data property, and rechecks the canonical set order before
using the bounded canonical merger. The axiom emits no edge and therefore retains no owned
emitting delta record. Normal and `only_taxonomy` projection preserve the exact
equivalent-data-properties count and skipped-axiom diagnostic; asserted-taxonomy preserves the
constructor count but suppresses that diagnostic, matching the pinned scalar behavior. The base
edge total is checked against the caller bound before publication. No selected-root index,
flattened base, copied structural graph, canonical-byte arena, scalar ontology value, complete
output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters, binary and ternary
sets, and an `ALL` base, one partial base exclusion, exclusion of every base root, and
`only_taxonomy`: sixteen supported executions with exact scalar ordered-edge/report parity.
Seventeen adjacent integration cases retain whole-call fallback, including an annotated local
equivalent-data-properties axiom. The representative binary `ALL` case reports 751 structural
bytes, 22 retained zero-copy buffers, one skipped equivalent-data-properties axiom, and two
one-edge base batches; the ternary case reports 877 bytes with identical projected edges. A
partial exclusion retains the exact four-byte posting exporter as the twenty-third native input
and emits one base edge. Excluding both base roots retains an eight-byte posting table, publishes
the local root and diagnostic with zero edges, and performs only the initial native boundary
call. Both provider layouts have identical results, statistics, and counters.

The exact `285a46a` archive built a fresh release-profile abi3 wheel and passed all 1,395 tests
from the isolated installed projector/core payload. All 263 private integration cases, the 33
focused equivalent-data-properties/adjacent-fallback cases, 15 benchmark cases, 64 Rust tests,
rustfmt, Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke,
runtime dependency boundary, and 30-member native-wheel audit passed. The public feature ledger
remains exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-equivalent-data-properties-delta-checkpoint.json`](evidence/installed-one-root-equivalent-data-properties-delta-checkpoint.json).
This remains focused private correctness evidence, not disjoint or other local data-property
axioms, annotated/multi-root delta, local deletion, `INCLUDE`, nested-delta, composite, mmap,
corpus, performance, public-dispatch, or release-acceptance evidence.

### Bounded one-root disjoint data properties delta

Revision `6f11bff3758841c905751f0ad4ceb5370e262516` admits one unannotated local
`DisjointDataProperties` over a canonical binary or ternary set of named data properties through
kernel v59. The top manifest remains exactly `OVERLAY_BASE` plus `OVERLAY_DELTA`; its base may use
`ALL` or the existing one exact nonempty `EXCLUDE` posting table, while the delta remains `ALL`.
An annotated axiom, duplicate base/local identity, second local root, noncanonical or duplicate
property set, or other unsupported local shape still selects whole-operation fallback or typed
pre-output rejection.

Rust fully validates both tables, requires the root tag and exact disjoint-data-properties count
to agree, validates every named data property, and rechecks the canonical set order before using
the bounded canonical merger. The axiom emits no edge and therefore retains no owned emitting
delta record. Normal and `only_taxonomy` projection preserve the exact
disjoint-data-properties count and skipped-axiom diagnostic; asserted-taxonomy preserves the
constructor count but suppresses that diagnostic, matching the pinned scalar behavior. The base
edge total is checked against the caller bound before publication. No selected-root index,
flattened base, copied structural graph, canonical-byte arena, scalar ontology value, complete
output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters, binary and ternary
sets, and an `ALL` base, one partial base exclusion, exclusion of every base root, and
`only_taxonomy`: sixteen supported executions with exact scalar ordered-edge/report parity.
Eighteen adjacent integration cases retain whole-call fallback, including an annotated local
disjoint-data-properties axiom. The representative binary `ALL` case reports 751 structural
bytes, 22 retained zero-copy buffers, one skipped disjoint-data-properties axiom, and two
one-edge base batches; the ternary case reports 877 bytes with identical projected edges. A
partial exclusion retains the exact four-byte posting exporter as the twenty-third native input
and emits one base edge. Excluding both base roots retains an eight-byte posting table, publishes
the local root and diagnostic with zero edges, and performs only the initial native boundary
call. Both provider layouts have identical results, statistics, and counters.

The exact `6f11bff` archive built a fresh release-profile abi3 wheel and passed all 1,412 tests
from the isolated installed projector/core payload. All 280 private integration cases, the 34
focused disjoint-data-properties/adjacent-fallback cases, 15 benchmark cases, 65 Rust tests,
rustfmt, Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke,
runtime dependency boundary, and 30-member native-wheel audit passed. The public feature ledger
remains exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-disjoint-data-properties-delta-checkpoint.json`](evidence/installed-one-root-disjoint-data-properties-delta-checkpoint.json).
This remains focused private correctness evidence, not other local data-property axioms,
annotated/multi-root delta, local deletion, `INCLUDE`, nested-delta, composite, mmap, corpus,
performance, public-dispatch, or release-acceptance evidence.

### Bounded one-root data property domain delta

Revision `453d468fb669940f8436e70e588de0a9bccd0edd` admits one unannotated local
`DataPropertyDomain` over a named data property and the existing recursive class-expression
envelope through kernel v60. The top manifest remains exactly `OVERLAY_BASE` plus
`OVERLAY_DELTA`; its base may use `ALL` or the existing one exact nonempty `EXCLUDE` posting
table, while the delta remains `ALL`. An annotated axiom, duplicate base/local identity, second
local root, malformed expression graph, or other unsupported local shape still selects
whole-operation fallback or typed pre-output rejection.

Rust fully validates both tables, requires the root tag and exact data-property-domain count to
agree, validates the named data property, and recursively validates the complete domain class
expression before using the bounded canonical merger. The axiom emits no edge and therefore
retains no owned emitting delta record. Normal and `only_taxonomy` projection preserve the exact
data-property-domain count and skipped-axiom diagnostic; asserted-taxonomy preserves the
constructor count but suppresses that diagnostic, matching the pinned scalar behavior. The base
edge total is checked against the caller bound before publication. No selected-root index,
flattened base, copied structural graph, canonical-byte arena, scalar ontology value, complete
output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters, named and recursively
nested domains, and an `ALL` base, one partial base exclusion, exclusion of every base root, and
`only_taxonomy`: sixteen supported executions with exact scalar ordered-edge/report parity.
Nineteen adjacent integration cases retain whole-call fallback, including an annotated local
data-property-domain axiom. The representative named-domain `ALL` case reports 730 structural
bytes, 22 retained zero-copy buffers, one skipped data-property-domain axiom, and two one-edge
base batches; the recursive aggregate-domain case reports 1,060 bytes with identical projected
edges. A partial exclusion retains the exact four-byte posting exporter as the twenty-third
native input and emits one base edge. Excluding both base roots retains an eight-byte posting
table, publishes the local root and diagnostic with zero edges, and performs only the initial
native boundary call. Both provider layouts have identical results, statistics, and counters.

The exact `453d468` archive built a fresh release-profile abi3 wheel and passed all 1,429 tests
from the isolated installed projector/core payload. All 297 private integration cases, the 35
focused data-property-domain/adjacent-fallback cases, 15 benchmark cases, 66 Rust tests, rustfmt,
Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke, runtime
dependency boundary, and 30-member native-wheel audit passed. The public feature ledger remains
exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-data-property-domain-delta-checkpoint.json`](evidence/installed-one-root-data-property-domain-delta-checkpoint.json).
This remains focused private correctness evidence, not local data-property range, functional
data-property, datatype-definition, or other axioms, annotated/multi-root delta, local deletion,
`INCLUDE`, nested-delta, composite, mmap, corpus, performance, public-dispatch, or
release-acceptance evidence.

### Bounded one-root data property range delta

Revision `d19e3db9b11d300d3c32236f26e714cb5288682c` admits one unannotated local
`DataPropertyRange` over a named data property and the existing recursive data-range envelope
through kernel v61. The top manifest remains exactly `OVERLAY_BASE` plus `OVERLAY_DELTA`; its
base may use `ALL` or the existing one exact nonempty `EXCLUDE` posting table, while the delta
remains `ALL`. An annotated axiom, duplicate base/local identity, second local root, malformed
data-range graph, or other unsupported local shape still selects whole-operation fallback or
typed pre-output rejection.

Rust fully validates both tables, requires the root tag and exact data-property-range count to
agree, validates the named data property, and recursively validates the complete range before
using the bounded canonical merger. The axiom emits no edge and therefore retains no owned
emitting delta record. Normal and `only_taxonomy` projection preserve the exact
data-property-range count and skipped-axiom diagnostic; asserted-taxonomy preserves the
constructor count but suppresses that diagnostic, matching the pinned scalar behavior. The base
edge total is checked against the caller bound before publication. No selected-root index,
flattened base, copied structural graph, canonical-byte arena, scalar ontology value, complete
output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters, named and recursively
nested ranges, and an `ALL` base, one partial base exclusion, exclusion of every base root, and
`only_taxonomy`: sixteen supported executions with exact scalar ordered-edge/report parity.
Twenty adjacent integration cases retain whole-call fallback, including an annotated local
data-property-range axiom. The representative named-range `ALL` case reports 743 structural
bytes, 22 retained zero-copy buffers, one skipped data-property-range axiom, and two one-edge
base batches; the recursive union/complement range reports 950 bytes with identical projected
edges. A partial exclusion retains the exact four-byte posting exporter as the twenty-third
native input and emits one base edge. Excluding both base roots retains an eight-byte posting
table, publishes the local root and diagnostic with zero edges, and performs only the initial
native boundary call. Both provider layouts have identical results, statistics, and counters.

The exact `d19e3db` archive built a fresh release-profile abi3 wheel and passed all 1,446 tests
from the isolated installed projector/core payload. All 314 private integration cases, the 36
focused data-property-range/adjacent-fallback cases, 15 benchmark cases, 67 Rust tests, rustfmt,
Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke, runtime
dependency boundary, and 30-member native-wheel audit passed. The public feature ledger remains
exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-data-property-range-delta-checkpoint.json`](evidence/installed-one-root-data-property-range-delta-checkpoint.json).
This remains focused private correctness evidence, not local functional data properties,
datatype definitions, or other axioms, annotated/multi-root delta, local deletion, `INCLUDE`,
nested-delta, composite, mmap, corpus, performance, public-dispatch, or release-acceptance
evidence.

### Bounded one-root functional data property delta

Revision `447445b45f7847eb02379c38b330a4b81913ed72` admits one unannotated local
`FunctionalDataProperty` over a named data property through kernel v62. The top manifest remains
exactly `OVERLAY_BASE` plus `OVERLAY_DELTA`; its base may use `ALL` or the existing one exact
nonempty `EXCLUDE` posting table, while the delta remains `ALL`. An annotated axiom, duplicate
base/local identity, second local root, or other unsupported local shape still selects
whole-operation fallback or typed pre-output rejection.

Rust fully validates both tables, requires the root tag and exact functional-data-property count
to agree, validates the named data property, and requires the annotation set to be empty before
using the bounded canonical merger. The axiom emits no edge and therefore retains no owned
emitting delta record. Normal and `only_taxonomy` projection preserve the exact
functional-data-property count and skipped-axiom diagnostic; asserted-taxonomy preserves the
constructor count but suppresses that diagnostic, matching the pinned scalar behavior. The base
edge total is checked against the caller bound before publication. No selected-root index,
flattened base, copied structural graph, canonical-byte arena, scalar ontology value, complete
output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters with an `ALL` base,
one partial base exclusion, exclusion of every base root, and `only_taxonomy`: eight supported
executions with exact scalar ordered-edge/report parity. Twenty-one adjacent integration cases
retain whole-call fallback, including an annotated local functional-data-property axiom. The
representative `ALL` case reports 608 structural bytes, 22 retained zero-copy buffers, one skipped
functional-data-property axiom, and two one-edge base batches. A partial exclusion retains the
exact four-byte posting exporter as the twenty-third native input and emits one base edge.
Excluding both base roots retains an eight-byte posting table, publishes the local root and
diagnostic with zero edges, and performs only the initial native boundary call. Both provider
layouts have identical results, statistics, and counters.

The exact `447445b` archive built a fresh release-profile abi3 wheel and passed all 1,455 tests
from the isolated installed projector/core payload. All 323 private integration cases, the 29
focused functional-data-property/adjacent-fallback cases, 15 benchmark cases, 68 Rust tests,
rustfmt, Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke,
runtime dependency boundary, and 30-member native-wheel audit passed. The public feature ledger
remains exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-functional-data-property-delta-checkpoint.json`](evidence/installed-one-root-functional-data-property-delta-checkpoint.json).
This remains focused private correctness evidence, not local datatype definitions or other
axioms, annotated/multi-root delta, local deletion, `INCLUDE`, nested-delta, composite, mmap,
corpus, performance, public-dispatch, or release-acceptance evidence.

### Bounded one-root datatype definition delta

Revision `6639831f594335aac3db9a1fa1eeaf2a4c7f263d` admits one unannotated local
`DatatypeDefinition` from a named datatype to the existing recursive data-range envelope through
kernel v63. The top manifest remains exactly `OVERLAY_BASE` plus `OVERLAY_DELTA`; its base may use
`ALL` or the existing one exact nonempty `EXCLUDE` posting table, while the delta remains `ALL`.
An annotated axiom, duplicate base/local identity, second local root, malformed data-range graph,
or other unsupported local shape still selects whole-operation fallback or typed pre-output
rejection.

Rust fully validates both tables, requires the root tag and exact datatype-definition count to
agree, validates the named datatype, and recursively validates the complete defining data range
before using the bounded canonical merger. The axiom emits no edge and therefore retains no owned
emitting delta record. Normal and `only_taxonomy` projection preserve the exact
datatype-definition count and skipped-axiom diagnostic; asserted-taxonomy preserves the
constructor count but suppresses that diagnostic, matching the pinned scalar behavior. The base
edge total is checked against the caller bound before publication. No selected-root index,
flattened base, copied structural graph, canonical-byte arena, scalar ontology value, complete
output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters, named and recursively
nested defining ranges, and an `ALL` base, one partial base exclusion, exclusion of every base
root, and `only_taxonomy`: sixteen supported executions with exact scalar ordered-edge/report
parity. Twenty-two adjacent integration cases retain whole-call fallback, including an annotated
local datatype definition. The representative named-range `ALL` case reports 742 structural
bytes, 22 retained zero-copy buffers, one skipped datatype-definition axiom, and two one-edge base
batches; the recursive union/complement range reports 949 bytes with identical projected edges. A
partial exclusion retains the exact four-byte posting exporter as the twenty-third native input
and emits one base edge. Excluding both base roots retains an eight-byte posting table, publishes
the local root and diagnostic with zero edges, and performs only the initial native boundary call.
Both provider layouts have identical results, statistics, and counters.

The exact `6639831` archive built a fresh release-profile abi3 wheel and passed all 1,472 tests
from the isolated installed projector/core payload. All 340 private integration cases, the 38
focused datatype-definition/adjacent-fallback cases, 15 benchmark cases, 69 Rust tests, rustfmt,
Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke, runtime
dependency boundary, and 30-member native-wheel audit passed. The public feature ledger remains
exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-datatype-definition-delta-checkpoint.json`](evidence/installed-one-root-datatype-definition-delta-checkpoint.json).
This remains focused private correctness evidence, not other local axiom families,
annotated/multi-root delta, local deletion, `INCLUDE`, nested-delta, composite, mmap, corpus,
performance, public-dispatch, or release-acceptance evidence.

### Bounded one-root same individual delta

Revision `01f083cc8f2b491ccf60b326f2aaf5b6dc480d07` admits one unannotated local
`SameIndividual` with a canonical binary or ternary set of named individuals through kernel v64.
The top manifest remains exactly `OVERLAY_BASE` plus `OVERLAY_DELTA`; its base may use `ALL` or
the existing one exact nonempty `EXCLUDE` posting table, while the delta remains `ALL`. An
annotated axiom, anonymous member, fourth member, duplicate base/local identity, second local root,
noncanonical or duplicate set item, or other unsupported local shape still selects
whole-operation fallback or typed pre-output rejection.

Rust fully validates both tables, requires the root tag and exact same-individual count to agree,
requires two or three named individuals and an empty annotation set, and rechecks strict canonical
set order and uniqueness before using the bounded canonical merger. The axiom emits no edge and
therefore retains no owned emitting delta record. Normal and `only_taxonomy` projection preserve
the exact same-individual count and skipped-axiom diagnostic; asserted-taxonomy preserves the
constructor count but suppresses that diagnostic, matching the pinned scalar behavior. The base
edge total is checked against the caller bound before publication. No selected-root index,
flattened base, copied structural graph, canonical-byte arena, scalar ontology value, complete
output vector, or per-row FFI call is created.

The exact installed matrix crosses independent- and packed-bytes exporters, canonical binary and
ternary named-individual sets, and an `ALL` base, one partial base exclusion, exclusion of every
base root, and `only_taxonomy`: sixteen supported executions with exact scalar
ordered-edge/report parity. Twenty-five adjacent integration cases retain whole-call fallback,
including annotated, anonymous-member, and four-member local same-individual axioms. The
representative binary-set `ALL` case reports 755 structural bytes, 22 retained zero-copy buffers,
one skipped same-individual axiom, and two one-edge base batches; the ternary-set case reports 883
bytes with identical projected edges. A partial exclusion retains the exact four-byte posting
exporter as the twenty-third native input and emits one base edge. Excluding both base roots
retains an eight-byte posting table, publishes the local root and diagnostic with zero edges, and
performs only the initial native boundary call. Both provider layouts have identical results,
statistics, and counters.

The exact `01f083c` archive built a fresh release-profile abi3 wheel and passed all 1,491 tests
from the isolated installed projector/core payload. All 359 private integration cases, the 41
focused same-individual/adjacent-fallback cases, 15 benchmark cases, 71 Rust tests, rustfmt,
Clippy with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke, runtime
dependency boundary, and 30-member native-wheel audit passed. The public feature ledger remains
exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-same-individual-delta-checkpoint.json`](evidence/installed-one-root-same-individual-delta-checkpoint.json).
This remains focused private correctness evidence, not local different-individual or other axiom
families, annotated/multi-root delta, local deletion, `INCLUDE`, nested-delta, composite, mmap,
corpus, performance, public-dispatch, or release-acceptance evidence.

### Bounded one-root different individuals delta

Revision `d7397c4f8ab1ab12d23a5dd415ec164884aea88d` admits one unannotated local
`DifferentIndividuals` with a canonical binary or ternary set of named individuals through kernel
v65. The top manifest remains exactly `OVERLAY_BASE` plus `OVERLAY_DELTA`; its base may use `ALL`
or the existing one exact nonempty `EXCLUDE` posting table, while the delta remains `ALL`. An
annotated axiom, anonymous member, fourth member, duplicate base/local identity, second local root,
noncanonical or duplicate set item, or other unsupported local shape still selects
whole-operation fallback or typed pre-output rejection.

Rust shares the exact individual-set validator with `SameIndividual`, while still requiring the
root tag and matching constructor count. It requires two or three named individuals and an empty
annotation set, and rechecks strict canonical set order and uniqueness before using the bounded
canonical merger. The axiom emits no edge and retains no owned emitting delta record. Normal and
`only_taxonomy` projection preserve the exact different-individuals count and skipped-axiom
diagnostic; asserted-taxonomy preserves the constructor count but suppresses that diagnostic,
matching the pinned scalar behavior. The base edge total is checked against the caller bound
before publication. No selected-root index, flattened base, copied structural graph,
canonical-byte arena, scalar ontology value, complete output vector, or per-row FFI call is
created.

The exact installed matrix crosses independent- and packed-bytes exporters, canonical binary and
ternary named-individual sets, and an `ALL` base, one partial base exclusion, exclusion of every
base root, and `only_taxonomy`: sixteen new supported executions with exact scalar
ordered-edge/report parity. The combined individual-set integration matrix has 32 supported
executions, while 28 adjacent cases retain whole-call fallback, including annotated,
anonymous-member, and four-member local different-individuals axioms. The representative
binary-set `ALL` case reports 755 structural bytes, 22 retained zero-copy buffers, one skipped
different-individuals axiom, and two one-edge base batches; the ternary-set case reports 883 bytes
with identical projected edges. A partial exclusion retains the exact four-byte posting exporter
as the twenty-third native input and emits one base edge. Excluding both base roots retains an
eight-byte posting table, publishes the local root and diagnostic with zero edges, and performs
only the initial native boundary call. Both provider layouts have identical results, statistics,
and counters.

The exact `d7397c4` archive built a fresh release-profile abi3 wheel and passed all 1,510 tests
from the isolated installed projector/core payload. All 378 private integration cases, the 60
focused individual-set/adjacent-fallback cases, 15 benchmark cases, 71 Rust tests, rustfmt, Clippy
with warnings denied, Ruff, strict mypy over 17 source files, the installed smoke, runtime
dependency boundary, and 30-member native-wheel audit passed. The public feature ledger remains
exactly `abi3-py310` and `bounded-batches`; ordinary public native projection still uses
scalar-native with zero native counters. Exact archive, wheel, installed-payload, counter, and
gate hashes are recorded in
[`installed-one-root-different-individuals-delta-checkpoint.json`](evidence/installed-one-root-different-individuals-delta-checkpoint.json).
This remains focused private correctness evidence, not local `HasKey` or other axiom families,
annotated/multi-root delta, local deletion, `INCLUDE`, nested-delta, composite, mmap, corpus,
performance, public-dispatch, or release-acceptance evidence.

These are local source-tree checks. They do not replace hosted wheels, sanitizers, fuzzing,
licensed corpora, performance thresholds, or the Exact acceptance matrix.

## Acceptance ledger

| WP-P7 requirement | Current truthful state |
|---|---|
| Public descriptor/owner validation | Python adapter is broad; private Rust seam rechecks its narrow direct envelope and descriptor binding |
| Complete Rust projection rules/options | Open; the report above enumerates the bounded direct ABox/taxonomy/restriction, recursive validation, role-state, skipped/silent, annotation, diagnostic, and compatibility slices. Kernel v30 joins unequal exact-direct root annotation identities to closure nodes before counting/output, including closure-wide anonymous IDs; v31 rejects cyclic nested annotation metadata in both tables; v32 emits the hidden iterator through a resumable bounded cursor; v33 starts that cursor without replay; v34 removes the coarse call's duplicate Rust emitter/vector; v35 removes its duplicate complete Python edge list and extends the role transaction through final object construction; v36 returns each final iterator `Edge` tuple directly and commits the cursor afterwards; v37 publishes the session and retained role transition only after final statistics construction; v38 constructs the final owner-holding iterator before that publication; v39 validates canonical final factories and exact result types before commit; v40 extends that validation to bounded-drain and coarse edge results plus coarse statistics before their respective commits; v41 pins post-native wrapper validation to those retained canonical identities; v42 validates final object payloads before commit; v43 revalidates complete edge chunks and statistics after their last callback; v44 allocates exact slotted final edges directly without Python factory or constructor callbacks; v45 does the same for the exact 60-slot statistics result without a 60-field argument tuple; v46 directly allocates the exact eight-slot iterator without its argument tuple or Python factory/constructor callback; v47 retains, validates, and binary-searches one exact terminal-adjacent `EXCLUDE` posting table without a selection index; v48 uses the bounded canonical merger to insert one exact local unannotated named-subclass root without indexing or flattening either table; v49 composes that insertion with one exact base `EXCLUDE` posting table and derives a scalar absolute insertion point from the selected predecessor; v50 admits one local unannotated named-entity declaration, updates exact silent-root statistics, and publishes a clean zero-edge result without retaining an emitting delta record; v51 admits one supported local named-role restriction, expands it through the already selected base role state, and preserves suppression/statistics parity; v52 admits one named-class/named-individual local `ClassAssertion`, inserts it in the canonical class-assertion phase, and preserves the distinct historical taxonomy-mode behavior; v53 admits one named-property/named-individual positive `ObjectPropertyAssertion`, inserts it in the canonical object-assertion phase, and fails closed for an inverse local property before output; v54 validates and canonical-merges one named-individual local `NegativeObjectPropertyAssertion` with a named or inverse named property, updates exact option-specific skipped-root statistics, and retains no emitting delta record; v55 validates and canonical-merges one named-source local `DataPropertyAssertion` with a fully validated literal, updates exact option-specific skipped-root statistics, and retains no emitting delta record; v56 applies that exact validation and silent transaction to the local negative data-assertion counterpart; v57 admits one named `SubDataPropertyOf` through the same silent transaction; v58 admits one canonical binary or ternary named-property `EquivalentDataProperties` set through that transaction; v59 admits the corresponding `DisjointDataProperties` set; v60 admits one named-property `DataPropertyDomain` over the existing recursive class-expression envelope; v61 admits one named-property `DataPropertyRange` over the existing recursive data-range envelope; v62 admits one named `FunctionalDataProperty`; v63 admits one named-to-recursive-range `DatatypeDefinition`; v64 admits one canonical binary or ternary named-individual `SameIndividual` set; v65 admits the corresponding `DifferentIndividuals` set. The hidden Projector binds retained Scala-instance maps with an exact one-way scalar transition and now exercises sink/digest/artifact consumers, but public binding and remaining projecting rules/options/surfaces are unsupported |
| Bounded batches without per-row FFI | The hidden iterator's Rust and Python outputs are caller-bounded, start with zero emission attempts, use one PyO3 entry per batch, preserve exact order, and report compiled/vector/peak counters. Each final bounded `Edge` tuple is allocated as the exact canonical slotted type through the stable ABI and its layout, canonical identity marker, three string fields, exact type, and distinct identity are validated before cursor commit. There is no intermediate Python tuple-edge list and no Python `Edge` factory or constructor callback; each edge still requires one Python object and three Python Unicode field objects. The 60-field statistics result and eight-slot batch iterator are likewise allocated directly without argument tuples or Python factory/constructor callbacks. The legacy private coarse call must still return one whole Python list, but builds and validates its final `Edge` objects through 256-edge native chunks with no complete Rust output vector or intermediate complete tuple-edge list. Hidden Projector sink/digest/artifact integration is exact-wheel proven through the existing policy machinery; ordinary public iterator/sink/digest/artifact selection remains open |
| Production dispatch and provenance | Open; public dispatch remains unchanged and the capability is absent. Explicitly hidden named-edge iterator, sink, digest, and artifact adapters select the private kernel and report its exact ingestion counters after complete consumption |
| Direct/mmap/overlay/composite support | Exact full bytes and the canonical eleven-column packed direct-bytes arena are supported. A bounded chain of canonical empty-local `OVERLAY_BASE` aliases to an exact-direct source is installed-wheel proven without flattening, with every owner retained and public depth/work bounds enforced. Only the terminal-adjacent segment may use one nonempty sorted `EXCLUDE` table, retained and binary-searched without indexing; public validation and an internal identity guard reject or fall back from nonterminal postings. One exact two-segment `OVERLAY_BASE`/`OVERLAY_DELTA` view may add one unannotated named-to-named or supported named-role restriction `SubClassOf`, one unannotated named-entity declaration, one unannotated named-class/named-individual `ClassAssertion`, one unannotated named-property/named-individual positive `ObjectPropertyAssertion`, one unannotated named-individual negative object assertion with a named or inverse named property, one unannotated named-property/named-individual positive or negative data assertion with a fully validated literal, one unannotated named `SubDataPropertyOf`, one unannotated canonical binary or ternary named-property `EquivalentDataProperties` or `DisjointDataProperties` set, one unannotated named-property `DataPropertyDomain` over the existing recursive class-expression envelope, one unannotated named-property `DataPropertyRange` over the existing recursive data-range envelope, one unannotated named `FunctionalDataProperty`, one unannotated named-to-recursive-range `DatatypeDefinition`, or one unannotated canonical binary or ternary named-individual `SameIndividual` or `DifferentIndividuals` set through the bounded canonical merger while retaining both tables and deriving only a scalar insertion position; the base segment may also carry one exact nonempty `EXCLUDE` table. Multiple local roots, other local logical axioms, annotated roots, anonymous or complex local assertions, unsupported restriction fillers, positive inverse local object properties, literal-emitting projection, local deletion, nested deltas, multiple `EXCLUDE` layers, `INCLUDE`, annotation-sensitive aliases, mmap, composite, and other segmented families remain unsupported by the Rust path |
| Generated differential parity | Exact installed campaign passes 128 deterministic mixed-rule sources through both supported direct exporter layouts: 256 executions, every one of the 32 semantic-boolean/duplicate/order combinations, batch bounds 1–7, 6,264 post-policy edges, exact ordered/report/diagnostic parity, and zero staging-copy/per-row-FFI counters. Broader generated, segmented-provider, independent Scala-oracle, and corpus matrices remain open |
| Invalid encoded-column rejection | Exact installed campaign passes 29 predefined cases over all eleven columns, 256 generated sources, and both direct provider layouts: 14,848 typed pre-output failures with equal direct/Projector and provider results, terminal failed state, no batch session/output counters/edges/report, and explicit native-view cleanup. Coverage-guided and mutational fuzzing, sanitizers, broader protocol/resource failures, and exhaustive invalid-input proof remain open |
| Lifetime/GIL/cancel/failure safety | Focused private bytes-path, owner lifetime, released-GIL concurrent cancellation, active-cursor thread handoff, isolated re-entrancy, Scala-instance exclusion, batch close/collection/sink-failure/fallback cleanup, state atomicity, panic conversion, a quiescent POSIX fork, and normal shutdown with an unfinished cursor pass against the installed wheels. Exact retry after malformed/replaced factory identities, payload corruption, allocation-probe failure, edge/statistics/iterator-layout mutation, and direct final-object validation also pass. Multithreaded-fork, cross-platform/free-threaded/subinterpreter, fuzz, and sanitizer acceptance remain open |
| Zero forbidden-work ledger | Reported by the successful hidden exact named-edge candidate; no public production-path claim |
| Corpus performance/RSS gates | Private load-excluded harness now independently measures iterator/sink/digest/artifact consumers and binds each surface, its consumer metrics, exact ledgers, runtime artifacts, and revisions. The packaged-fixture smoke passes; installed NCIT/DOID/GO/large-corpus measurements and thresholds remain open |
| Exact shared-stack acceptance | Open for this kernel |
| Wheels/SBOM/platform matrix | Open for this kernel |

## Promotion decision and next work

Public `auto` and explicit native negotiation remain unchanged. Before advertising
`encoded-structural-compiler-v1`, P7 still needs:

1. promote the proven hidden retained-role lifecycle, bounded cursor, locking, invocation history,
   counters, and one-way scalar transition into public encoded selection only after the remaining
   gates pass;
2. promote the proven hidden iterator, protocol-sink, digest, artifact, and cancellation
   integration into public feature-gated selection, and collect corpus-scale labelled
   time-to-first-output evidence;
3. expand the proven bounded one-root local-delta merge to the next separately reviewed no-copy
   segment slice, then composite-member selection, mmap, and general traversal;
4. production provenance wired only after it describes actual Rust work;
5. extend the focused lifecycle, finite generated, and invalid-column results to full
   independent-oracle/broader-malformed/mutational-fuzz/sanitizer/platform/free-threaded/
   subinterpreter verification, including the supported fork policy;
6. labelled NCIT, DOID, GO, million-axiom, licensed-corpus, RSS, and copy evidence; and
7. Exact-OM shared-stack and release packaging/SBOM/compatibility review.

No compatibility, completeness, or performance claim is inferred from this private checkpoint.
