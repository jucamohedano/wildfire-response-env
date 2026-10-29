# Wildfire Response Benchmark v1 — private candidate

**Historical night-response environment card.** For current 24-hour development
exercises and exact-pin task selection, use the [incident-day guide](incident-day.md)
and [lifecycle registry](../data/catalog_lifecycle_v1/README.md). The older catalog
is retained for explicit archival comparisons, not the default learning path.

This card describes the frozen two-hour benchmark. For the separately versioned
24-hour prototype, use the [incident-day guide](incident-day.md).

Role: one operations-control-centre agent manages a source-grounded synthetic
wildfire. It protects people and minimizes additional burned footprint while
reports arrive, teams mobilize/travel, resources become unavailable and commands
are delivered or fail. No live calls or external emergency systems are used.

## Interface

`wildfire_core.benchmark_v1.BenchmarkEnv` accepts caller-pinned prepared and
topographic inputs. `reset(task_id, seed)` returns an actor observation; `step`
returns observation, reward, termination, truncation and diagnostics. Frozen tasks
pin their seeds. Production runs use60decisions ×2exercise minutes. An explicit
120×1diagnostic configuration exists for latency tests, not mixed leaderboards.

`regional.benchmark_v1.model.BenchmarkModelPolicy` provides focused compact tools,
plus bounded queries for previously received reports/events. The opening context
contains the latest20 records; received IDs remain legal after a history query.
Dynamic district and resource values may be null until evidence arrives. Static
geography and declared inventory remain visible. No private future schedules,
evaluator checkpoints or prescribed strategies are included.

The UI exposes development cases only. OpenEnv0.7.0 exposes split discovery and a
single active simulation session over WebSocket. The same action sequence and
seed must reproduce the Python episode. Optional evaluator views are explicitly
separate from actor observations.

## Outcomes

Headline ordering: zero-exposure completion rate, protected population fraction,
then lower additional burned footprint. Graded learning reward:
`0.8 × protected population fraction + 0.2 × unburned fraction`.
With no population, only area contributes. Initial burning cells are excluded
from additional footprint; cells are counted once. Exposure is not mortality.

Triage, communication volume, latency and resource use are diagnostics. Active
fire at the horizon is reported but does not automatically eliminate population
credit. Invalid abandonment scores zero; boundary censoring is unscored. Provider
failures do not become simulated outcomes. Incomplete/limited/censored evaluation
matrices cannot qualify for a complete benchmark ranking.

## Execution and data

CPU references require no API keys. Inputs are currently provided from the local
prepared-data pipeline; they are not silently downloaded. Public trajectory
records and private evaluator minute records are stored separately. Exact replay
and independent accounting are both required.

See `data/benchmark_v1/README.md`, `fidelity.md`, `data-storage.md` and
`train/BENCHMARK.md` for manifests, commands, assumptions and gated learning.
The reference evaluation report is produced by `python -m eval.run_benchmark`.
GPU training, public hosting and operational use are not implied by this card.
