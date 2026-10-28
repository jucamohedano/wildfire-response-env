# Working in this repository

- The 120-minute benchmark IDs, splits, seeds, scoring and replay contracts are
  frozen. Version behavioral changes separately; do not silently rescore runs.
- Keep actor observations separate from private evaluator audits, checkpoints,
  future schedules and curation labels. Do not tune on held-out cases or publish
  private artifacts. Preserve source attribution and redistribution gates.
- Mark synthetic and estimated inputs as such. Unknown fuel is not a firebreak;
  missing road coverage does not establish a traversable road or footpath. Do
  not invent measurements, routes, historical outcomes or calibration claims.
- Treat simulated exposure as an exercise outcome, not a mortality count.
  Preserve independently verified replay and safety checks when changing behavior.
- Python 3.12+: `python -m pip install -e envs/wildfire_response/core`.
  Run relevant tests with `python -m pytest`; see `docs/incident-day.md` and
  `data/benchmark_v1/README.md` for CPU acceptance and pinned inputs.
- Obtain explicit approval before paid inference, GPU spending or publication.
  Check `docs/license-status.md` and source rights before any distribution.
