# P0 dependency inventory

Runtime dependency: `pyowl-core>=0.1,<0.2`, the shared structural ontology layer.
The projector defines no fallback parser or ontology records.

Build dependencies are setuptools and wheel. Test, lint, typing, artifact build,
and upload tools are development-only. The P0 runtime has no Java, JVM bridge,
OWLAPI, ROBOT, DeepOnto, mOWL, reasoner, Exact-OM, network, or native dependency.

The future Rust/PyO3 accelerator and its locked supply-chain inventory belong to
WP-P3/P5. It cannot replace or reduce the Python implementation.

