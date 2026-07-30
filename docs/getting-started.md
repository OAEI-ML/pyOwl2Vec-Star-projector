# Getting started with pyOwl2Vec-Star-projector

## Install

pyOwl2Vec-Star-projector requires Python 3.10 or newer:

```bash
python -m pip install --upgrade pip
python -m pip install pyowl2vec-star-projector
```

The complete Python backend is compiler-free and Java-free.

## Project a file

Use `project_source` when one component owns loading:

```python
from pyowl2vec_star_projector import ProjectionOptions, project_source

edges = project_source(
    "ontology.owl",
    options=ProjectionOptions(
        backend="python",
        include_literals=True,
        order="canonical",
    ),
)
for edge in edges:
    print(edge.source, edge.relation, edge.destination)
```

Use `Projector.project(view)` when the ontology has already been loaded by
`pyowl-core`. The projector retains that exact view and does not parse or
serialize it again.

## Choose output behavior

`ProjectionOptions` controls the compatibility profile and output:

- `include_literals=True` includes supported literal edges.
- `bidirectional_taxonomy=True` emits reverse taxonomy edges.
- `only_taxonomy=True` selects the historical projector-local taxonomy mode.
- `duplicates="preserve" | "unique"` controls multiplicity.
- `order="canonical" | "encounter"` chooses deterministic global order or
  lower-latency encounter order.
- `backend="auto" | "python" | "native"` controls acceleration.

An explicit native request fails when no compatible extension is available.
`auto` may select Python and reports the reason through projection provenance.

## Stream large projections

Avoid materializing all edges by consuming the iterator:

```python
from pyowl2vec_star_projector import Projector

projector = Projector()
for edge in projector.iter_edges(ontology_view, buffer_edges=100_000):
    consume(edge)
```

For production pipelines, `project_to_sink` delivers bounded immutable batches.
`write_artifact` writes portable JSONL, while `canonical_digest` produces the
matching canonical edge digest. Temporary spill files are private and removed
when iteration completes, closes, is cancelled, or fails.
