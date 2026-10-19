# Regional v2 acceptance — 19 October 2026

These are the initial `regional-outcome-cost-v1` alpha results at `8e562cf`.
The current contract/reward and grouped-task audit are described in
[QUALITY.md](QUALITY.md); historical scores here are not mixed with reward v2.

Implementation: `codex/regional-wildfire-response-v2`, initial checkpoint `8e562cf`.
The source/data pack is an alpha prototype, not an operational fire forecast.

## Evidence preparation

Fresh downloads verified all four reviewed official sources: INFOCA 2026 resource
catalogue, MITECO aerial roles, MITECO coordination and CEMS EMSR913 mapping metadata.
HTML raw-byte hashes and stable normalized-prose hashes are recorded separately:
MITECO changed generated element IDs between requests while the page text stayed
identical. Factual text changes still fail the preparation gate for human review.
CEMS activation, year, AOI identity, reviewed bytes and bbox were verified.
An offline rebuild also succeeds.

The pack contains **3 documented facilities** and **5 assumed scenario resources**.
The latter are four ground units and one fixed-wing resource, not a historical
roster. Coordinates of real bases remain null. The 53×41 grid derives from CEMS
coverage, with synthetic terrain, settlements and incident placement.

Snapshots/pack/provenance: `.runtime/huelva-v2-reviewed-fresh-2026-10-19/`.
The earlier raw review snapshots remain in `.runtime/regional-v2-sources-2026-10-19/`.
These directories are ignored by Git; preserve them separately before cleanup.

## Correctness and compatibility

- The full core/data/director/readiness/regional acceptance suite passed **57 tests**;
  all **13 regional tests** also passed after final observer/checkpoint refinements.
- Checks include source text versus markup changes, no invented coordinates,
  partial-observation privacy, hidden-future-event independence, two-base allocation,
  delayed mutual-aid notices, offer expiry, atomic invalid actions and no double dispatch.
- Aircraft make one finite drop, return and refill. Preflight grounding cancels a
  sortie without a flight; an airborne resource returns without dropping. Ground
  water is finite; crews and units return to their owning bases.
- Informational messages do not evacuate. Movement begins only after an evacuation
  delivery. Reset isolation includes stable private fire checkpoints and unchanged v1.
- A wheel built successfully and installed outside the repository without dependency
  downloads. Both v1 and v2 import; an installed v2 client finishes a false-alarm
  episode without model/API access. Setuptools emits an existing namespace warning
  about the shared map-data directory, but the map is included and imports were tested.
- The original v1 engine and core environment files have no diff from the v1 branch.
  The existing v1 OpenEnv/model adapters are not presented as v2 adapters.

## CPU smoke suite

Ran six derived coordination scenarios with passive and cooperative scripted
policies: **12 episodes, zero invalid actions**, all **12 public trajectories
replayed exactly**. The full episode cap is 60 decisions / 120 assumed model minutes.
Outcome components account for total rewards.

- Passive: **1/6** natural completions, mean reward **-16.667255920**.
- Cooperative: **5/6** natural completions, mean reward **-0.011601531**.
- Growing-fire exposure: passive **200/350** synthetic people; cooperative **0/350**.
- Competing-incident exposure: passive **150/350**; cooperative **0/350**.
- The competing-incident cooperative episode still reaches the budget without
  extinguishing every fire. It is not labeled a successful resolution.
- False alarm: passive scores **0**, cooperative **-0.027573810** because it
  over-mobilizes and evacuates. Small non-threatening cases also expose resource
  overuse. The cooperative policy demonstrates protocol execution, not optimality.

The evaluation plus exact replays took **11.529 seconds**; peak process RSS
**28,432 KiB** (about 28 MiB). Host activity overlapped some package/test checks;
these are planning measurements, not an isolated throughput guarantee. No GPU,
model inference, live messaging, paid provider or public deployment was used.

Canonical smoke artifacts:
`.runtime/regional-v2-release-check-2026-10-19/summary.json` and its twelve rollout
directories. Public trajectories and private evaluator checkpoints are separate.
Earlier prototype/calibration artifacts have different observation/pack versions
and must not be mixed with this final check.

## Calibration boundaries

Initial exploratory scenarios placed all incidents too far from people to threaten
them within the chosen horizon, and refuges required longer movement than that
horizon. The pilot assumptions were adjusted to four-cell refuges and closer
ignitions in growing/competing scenarios, rather than changing reward weights or
injecting fires directly into occupied settlements. This makes prevention testable;
it is not empirical validation of spread or evacuation rates.

Further work before model training or publication: sourced base coordinates,
road/land-cover integration, measured timing where available, more seeds and
incident variants, uncertainty/noisy reports, reserve-capacity and task-difficulty
checks, expert review of action effects, and a dedicated v2 OpenEnv/model adapter.
Suppression and population exposure remain coarse proxies. Legal clearance for
code and source assets is unresolved. Do not compare these rewards with v1 or
describe these synthetic rollouts as historical 2026 incident replays.
