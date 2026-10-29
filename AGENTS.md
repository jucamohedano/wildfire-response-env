# AGENTS.md

## Project and entry points

This repository builds a CPU wildfire operations-control-centre environment for
agent evaluation and future training. The current API is the 24-hour incident-day
environment. Its source-grounded synthetic exercises are not historical replays
or a live emergency service. The coastal dashboard uses the archived benchmark
interface; do not assume its action contract is the current day contract.

Start with [README.md](README.md). Use [docs/incident-day.md](docs/incident-day.md)
for the API and [docs/hub-runtime.md](docs/hub-runtime.md) for prepared inputs.
Read the relevant guide before changing that component; do not load every document.

## Where work belongs

- `envs/wildfire_response/core/src/envs/wildfire_response/incident_day/`:
  current episodes, tools, events, operations, scenarios and outcome accounting.
- `envs/wildfire_response/core/src/wildfire_core/`: public imports, shared routing
  and catalog lifecycle. Reuse the packaged fire kernel and router, not a second
  simulator or code imported from sibling checkouts.
- `envs/wildfire_response/openenv/`: optional typed client/server transport.
- `eval/` and `tests/`: runners, validators and root regression tests.
- `data/`: versioned fixtures, manifests, provenance and release locks.
- `ui/`: React/TypeScript dashboard; its setup is in `ui/README.md`.
- `docs/` and `results/`: reference documentation and recorded validation evidence.
  Keep temporary data and private run artifacts in ignored `.runtime/` or a
  dedicated temporary directory, outside served/public output.

## Setup and useful checks

Use an isolated Python 3.12+ environment. Run from the repository root:

```sh
python -m pip install -e envs/wildfire_response/core
python -m pip install pytest==9.2.0 pyarrow==26.0.0

# Operation contracts, replay helpers and fault controls.
python -m pytest tests/test_incident_day_action_checks.py tests/test_incident_day_movement_checks.py tests/test_incident_day_runtime_matrix.py -q

# Downloaded-package and export guards.
python -m pytest tests/test_hub_runtime.py tests/test_build_spain_hub_dataset.py -q
```

For transport work, repeat the core/test setup in a separate environment, then
install the optional adapter: `python -m pip install -e 'envs/wildfire_response/openenv[dev]'`.
For UI work, use Node 24 and `npm ci --prefix ui`, then
`npm --prefix ui test` and `npm --prefix ui run build`.

Choose checks for the files and behavior changed. Run targeted tests first;
clock, operations, routing, outcome or checkpoint changes also need the complete
active-case/policy matrix described in
[docs/runtime-validation-plan.md](docs/runtime-validation-plan.md).
Use the required OpenEnv/GIS runtime for applicable integration tests; report
missing dependencies and skips rather than counting them as passes.
Documentation-only edits need link/render checks, not full simulation batches.

## Simulator and data contracts

- Preserve seeded replay, reset isolation and checkpoint continuation. Additional
  reads must not advance time or alter future fire/weather randomness.
- Check every affected operation: prerequisites, time cost, side effects,
  capacity limits and downstream effects. Keep
  `eval/validate_incident_day_runtime.py` and the operation checks in sync when
  adding tools or cases. Missing, blocked or unexercised checks are not passes.
- Keep actor observations and exports separate from hidden fire state, future
  schedules, evaluator audits, checkpoints and curation labels. Held-out outcomes
  must not update training data, retrieval banks or policy tuning.
- Preserve source/estimate/assumption distinctions and attribution. Unknown fuel
  is not a firebreak, missing elevation is not flat ground, and missing road
  coverage is not an accessible route. Do not invent historical measurements or
  deployments. Replay consistency does not establish physical calibration.
- Version behavior, scenario, input and reward changes. Preserve archived
  120-minute IDs, splits, seeds, scores and recorded results. Investigate pin
  mismatches; do not replace hashes merely to make a check pass. Treat exposure
  as a simulated outcome, not a mortality estimate.

## Data, reports and releases

- Resolve active cases through `wildfire_core.catalog_lifecycle`; read current
  release pins from `data/hub_runtime_v1/release.json`. Keep changing counts,
  release hashes, test results and issue status in their manifests/results/docs,
  not in this file. Do not regenerate GIS inputs when the pinned pack suffices.
- Keep secrets, provider logs and private evaluator artifacts out of commits and
  dataset exports. For authorized Hub uploads, preserve old release revisions
  and verify remote privacy before and after uploading; a creation flag alone
  does not establish an existing repository's visibility.
- Edit `docs/project-report.html`, then rebuild its companion PDF with
  `node eval/export_project_report_pdf.cjs`. Inspect the rendered PDF, pagination
  and portable links. The README links to the PDF. Keep other reference docs
  under `docs/`; use existing issues/plans rather than adding root status files.

## Working agreements

- Inspect the worktree and fetch the target branch before editing. Start new
  work on `codex/<short-topic>` from the requested base; preserve unrelated user
  changes. Do not reset or rewrite work to obtain a clean checkout.
- Keep changes focused and related issues/PRs current. State what changed, the
  checks actually run and what remains unresolved. Keep calibration and training
  follow-ups open when only runtime consistency has been established.
- Paid inference, GPU jobs and public publication need explicit authorization
  and any applicable budget. Honor authorization already given in the session;
  do not repeatedly ask for routine work. Consult
  [docs/license-status.md](docs/license-status.md) before distribution.
