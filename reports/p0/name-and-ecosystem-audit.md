# P0 name and ecosystem audit

Checked: 2026-07-17. This is evidence, not a reservation or publishing action.

- PyPI search exposes the existing `owl2vec-star` 0.2.0 project. Its published
  metadata targets Python `>=3.7,<3.9` and describes an Owlready-based embedding
  package. It is not the behavior oracle or distribution name for this project.
- The exact normalized candidate `pyowl2vec-star-projector` was not returned by
  the public index search performed for this checkpoint. Search absence is not
  ownership. An authenticated PyPI/TestPyPI reservation and organization recovery
  review remain release blockers under WP-P5.
- The repository/import split is frozen provisionally as distribution
  `pyowl2vec-star-projector`, import `pyowl2vec_star_projector`.
- No source from the existing package was downloaded, inspected, or copied.

The release owner must repeat the authenticated normalized-name check immediately
before publication and rename all metadata atomically if control cannot be proven.

