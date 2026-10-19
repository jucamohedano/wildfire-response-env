# Frozen regional task metadata

These files are materialized exports from the unchanged generator and benchmark
lock. There are 24 training, six validation and six test configurations. Each split
contains all six synthetic families; groups and seeds are disjoint, not real
incident/map holdouts. Public rows contain identifiers/seeds/configuration hashes,
not future events, generation truth labels or private checkpoints.

Regenerate into a fresh directory with `tools/build_dataset.py` from the project
README. Its `private/` configurations are evaluator-only and ignored by Git. Do not
add private exports to model prompts or treat old observed test results as unseen.
