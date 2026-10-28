# Wildfire Response Environment

A programmatic, turn-based environment for an **operations-control-centre agent**.
The agent receives delayed calls, reports and sensor updates; investigates claims;
coordinates fire crews across bases; and issues simulated population instructions.
The simulator runs offline on CPU. No real dispatch, messaging or emergency
system is connected.

The repository follows [PortSimEnv v1](https://github.com/adithya-s-k/FineEnvs/tree/main/07-simulation-environments/portsim-v1)
in separating data, simulator, adapters and evaluation. Its control-centre flow
also draws on the [AirOps incident-mode review](envs/wildfire_response/core/src/envs/wildfire_response/regional/incident/README.md).
This project has its own simulator, tasks and assets.

## Environment

The current candidate is `wildfire_core.benchmark_v1.BenchmarkEnv`. On each
two-minute turn, the agent sees delivered evidence and chooses operational tools
through `BenchmarkModelPolicy`; the episode lasts up to 120 simulated minutes.
The policy adapter exposes 13 named tools for triage, dispatch, protection,
communication and evidence-history queries.
Unknown or not-yet-delivered information stays unknown. The grader independently
replays trajectories and recomputes outcomes.

An opt-in [coastal mobility development mode](MOBILITY.md) adds real road geometry,
reported truck/crew movement and illustrative aircraft sorties. It has separate
task IDs and replay pins; it does not replace or rescore benchmark v1.

The separate [incident-day v2 prototype](INCIDENT_DAY.md) starts from a large-fire
handover and runs 24 simulated hours with sparse decisions, delayed status
requests, hazard-aware approaches and finite duty/refill cycles. Six CPU-only
development scenarios are included. Its real-data approval gate remains closed;
the runnable fixture is explicitly synthetic, not a new historical dataset.

The catalog contains **120 configuration groups** on one Málaga study area:
80 train, 20 validation and 20 test groups, with two variants each, plus 12
development probes. Families include uncertain smoke, conflicting reports,
wind changes, overlapping fires, delayed reinforcement, unavailable resources
and failed protection delivery. These are synthetic exercises, not 120 historical
wildfires.

The terminal learning score is `0.8 × protected population fraction + 0.2 × unburned fraction`.
Headline ranking puts zero-exposure completion first, then protected population,
then additional burned area. Exposure is a synthetic
exercise outcome, **not a death count**. The score does not certify the best
firefighting decision or physical accuracy.

## Run

Requires Python 3.12+, `uv`, and the locally prepared, checksum-pinned input
bundles. Runtime inputs are not downloaded automatically; their preparation and
provenance are described in [DATA_STORAGE.md](DATA_STORAGE.md). These ignored
`.runtime` bundles are not part of a fresh clone; restore the pinned source pack
before running the benchmark commands below.

```sh
uv venv --python 3.12 .runtime/core-venv
uv pip install --python .runtime/core-venv/bin/python -e envs/wildfire_response/core

export WILDFIRE_PREPARED_INPUTS=.runtime/malaga-prepared-inputs-v1/inputs.json
export WILDFIRE_PREPARED_SHA256=ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108
export WILDFIRE_TOPOGRAPHIC_INPUTS=.runtime/topographic-inputs-v1/inputs.json
export WILDFIRE_TOPOGRAPHIC_SHA256=e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd

PYTHONPATH=envs/wildfire_response/core/src .runtime/core-venv/bin/python -m eval.run_benchmark \
  --prepared-inputs "$WILDFIRE_PREPARED_INPUTS" --prepared-sha256 "$WILDFIRE_PREPARED_SHA256" \
  --topographic-inputs "$WILDFIRE_TOPOGRAPHIC_INPUTS" --topographic-sha256 "$WILDFIRE_TOPOGRAPHIC_SHA256" \
  --split development --limit 1 --output .runtime/benchmark-smoke
```

For a direct Python API example, a scenario inventory, UI, OpenEnv client/server,
and training recipe, see [benchmark commands](data/benchmark_v1/README.md),
[UI guide](ui/README.md), [OpenEnv adapter](envs/wildfire_response/openenv/README.md)
and [training recipe](train/BENCHMARK.md). The optional [AgentEnv/HF adapter](envs/wildfire_response/core/src/envs/wildfire_response/agentenv/README.md)
exports split-specific bundles and Parquet; it does not upload them.

## Data and limits

Terrain, fuel and station-weather context are source-grounded and documented in
[`data/malaga_2021/provenance.json`](data/malaga_2021/provenance.json).
Calls, report wording, ignition timing, fleet availability, delays, fire effects
and counterfactual decisions are synthetic. The benchmark covers one landscape
and a night response; it does not establish performance across Spain or real
incidents. See the [dataset card](DATASET_CARD.md) and [fidelity notes](FIDELITY.md).

CPU references completed 428 verified episodes; their report is
[here](results/benchmark-validation-20261026.html). Luna completed three
development probes, but the multiple-fire run still left fire active and burned
more area than the dispatcher reference. There is no trained policy whose
improvement has been verified.

```text
envs/wildfire_response/core/     Simulator, observations and grader
envs/wildfire_response/openenv/  Optional OpenEnv adapter
envs/wildfire_response/tasks/    Versioned task metadata
data/                            Provenance, manifests and source notes
eval/                            Runners, replay and reports
train/                           Gated SFT and GRPO recipe
ui/                              Local 2D control-centre interface
```

The repository is a private development candidate. The license and asset rights
are still under review; see [LICENSE_STATUS.md](LICENSE_STATUS.md) before sharing
or publishing it.
