# Standalone extraction acceptance

Source: `Los_Panaderos` revision `ba9cf90a4f4834d76e30a3e97f87d420e4cabdee`.
No source files, branches or runtime artifacts were removed from that checkout.

This repository has a separately installable CPU core and OpenEnv adapter,
public task-pack exports, dataset tools,
evaluation, source provenance and results. Training recipes are kept separately.
It does not claim a hosted Space or trained wildfire director.

Validation in the extracted checkout:

- Inherited suite: 92 tests, 83 passed and nine optional SDK tests skipped,
  75.007 seconds. Three additional migration tests passed.
- Frozen benchmark fingerprint unchanged:
  `e882e0f8ba58246ef3e4c3d271d32a6d54a1756d980be52bcccb524ff84ad840`.
- Regional training case 001 naturally completed at minute 42 with reward
  `-0.007678798411122146`, independently verified and replayed exactly.
- Core and OpenEnv wheels build successfully. Installed wheels passed a complete
  local/adapter observation-reward parity check outside the source checkout.
- A real localhost server launched through `wildfire_openenv.server` completed a
  matching external-client episode through `WildfireClient`, including a
  non-advancing check. No model/provider or live communication calls were made.
- Editable installation from repository root was tested. A namespace collision
  caused by the organizing `envs/` folder was fixed with a standard core `src/`
  layout, not runtime path injection or duplicated simulator sources.
- The old contact helper's environment-file loading and real destination fallback
  were removed. A dedicated test confirms injected contact variables are ignored.

The generated task export retains 24/6/6 public metadata rows; private full task
configs remain ignored. Model weights, checkpoints and private evaluator traces
were not migrated. Docker was not available for an image build; publication and
licensing/asset review remain pending. No additional GPU training was launched
during extraction. Existing GPU pilot limitations are unchanged.
