# P7 encoded-native compiler checkpoint

Date: 2026-07-20. Projector implementation through `1a181e3`. pyOWLCore candidate revision:
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
- unannotated named-to-named `SubClassOf`, named-only n-ary `EquivalentClasses`, and named
  `ClassAssertion` roots;
- named-property/named-filler `ObjectSomeValuesFrom`, `ObjectAllValuesFrom`,
  `ObjectMinCardinality`, and `ObjectMaxCardinality` on either side of a named class in
  `SubClassOf`; min/max integers are minimally encoded and validated but semantically discarded;
- paired named `ObjectPropertyDomain`/`ObjectPropertyRange` roots, producing the reference
  domain-by-range products in UTF-8 property order;
- the supported reference category order (`SubClassOf`, `EquivalentClasses`, `ClassAssertion`,
  then domain/range), with the first two equivalent members selected by UTF-8 IRI order;
- forward `http://subclassof`, optional reverse `http://superclassof`, `http://type`, and direct
  named object-property edges;
- the historical `only_taxonomy` behavior: restriction edges are suppressed while named
  equivalence, class assertions, and domain/range products remain enabled;
- an asserted-taxonomy mode that preflights the complete supported input but emits only direct
  named `SubClassOf`; and
- one caller-bounded coarse output batch, with no per-root or per-edge Python call.

Any other valid segment, exporter, constructor, annotation, complex/unsupported restriction,
inverse property, role axiom, object-property assertion, or anonymous individual is rejected as
unsupported before output. N-ary equivalents beyond the selected first two and unpaired
domain/range roots are still fully validated. There is no subrole or inverse-role expansion in
this narrow named-role slice. Malformed supported columns fail closed. This is kernel version 3 of
a private foundation, not the complete compiler described by WP-P7.

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
2. preflights every supported constructor, UTF-8 IRI, entity kind, minimally encoded cardinality,
   empty annotation set, root kind/tag pairing, output/cross-product count, and IRI/output limit;
3. allocates output only after the complete preflight succeeds; and
4. returns one coarse list of edge tuples plus roots, nodes, declarations, subclasses/restriction
   subclasses, equivalents, class assertions, domain/range roots and products, edges, and
   retained-buffer-byte counters.

The Python wrapper exposes an exact call ledger for this kernel: eleven input buffers, eleven
detached/zero-copy buffers, zero indexed buffers, zero staging/structural copy bytes, zero per-row
FFI calls, one native boundary call, and GIL release. These counters describe only this private
call. They are not currently attached to `ProjectionReport`, because the production projector
does not dispatch to the private kernel. Domain/range counting and ordering use repeated borrowed
root scans, not a copied structural graph or hidden property index.

The compiler is one-shot. Atomic idle/running/finished/cancelled/failed transitions allow another
Python thread to cancel detached work. A cancellation racing with successful compilation discards
the result. Unsupported, malformed, resource, cancelled, and panic outcomes cross the boundary as
distinct typed failures; no partial batch is returned.

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
`1a181e3`:

| Gate | Result |
|---|---|
| Rust unit tests (`cargo test --no-default-features`) | 14 passed |
| Rust formatting and Clippy with warnings denied | passed |
| Private PyO3 foundation tests | 25 passed |
| Existing native backend plus private foundation tests | 62 passed |
| Complete projector test suite | 855 passed |
| Focused Python Ruff and mypy checks | passed |

The focused tests cover Python-oracle parity for named class and role edges; both restriction
orientations and all four accepted constructors; cardinality discard; bidirectional projection;
n-ary equivalent lexical selection; class/domain/range category and cross-product ordering; the
distinct `only_taxonomy` and asserted-taxonomy behaviors; declarations as non-emitting roots;
exact family counters; a 250-axiom single boundary call; mixed-family and 20-by-20 cross-product
limit failure; non-minimal integer, canonical-set, and root-reference corruption; valid unsupported
inverse, exact-cardinality, restriction-pair, complex, anonymous, and annotated shapes; sliced and
non-bytes exporters; descriptor mismatch; bytes-exporter and exact-owner lifetime across the
expanded slice; GIL release; concurrent cancellation; and continued absence of the production
encoded feature.

These are local source-tree checks. They do not replace hosted wheels, sanitizers, fuzzing,
licensed corpora, performance thresholds, or the Exact acceptance matrix.

## Acceptance ledger

| WP-P7 requirement | Current truthful state |
|---|---|
| Public descriptor/owner validation | Python adapter is broad; private Rust seam rechecks its narrow direct envelope and descriptor binding |
| Complete Rust projection rules/options | Open; Rust implements only the direct unannotated named class slice plus named-property/filler subclass restrictions and named domain/range products; role expansion and remaining families are unsupported |
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
