# Design and scope

The agent is a simulated regional director, not an operator connected to any
external platform. HappyRobot integrations, workflow synchronization, Telegram
bots, voice services, web application and credentials are outside this repository.
All incoming reports, base replies and population deliveries are simulated.

## Core and transport

The independent `wildfire-response-core` distribution owns the simulator, task
generation, frozen benchmark, public observation boundary, deterministic replay,
independent accounting checker and optional training bridge. `wildfire_core`
exposes the regional API; the path-compatible implementation is bundled once
inside the same distribution. It never imports code from the Hackspain checkout.

`wildfire-openenv` depends on that core and OpenEnv 0.7.0. It forwards the existing
tested typed action/observation classes and single-active-episode server. It does
not implement another simulator, reward function or automatic dispatch policy.
The core uses a standard `src/` layout so its retained `envs` package does not
collide with the repository's organizing `envs/` directory in editable installs.
Core CPU use requires only the standard library. GPU libraries belong to a
separate installation and are imported only by the explicit training runner.

## Frozen behavior

The initial extraction retains the exact benchmark implementation paths and
critical source bytes. The benchmark lock, scenario IDs, seeds, reward version
and weights are unchanged. An inherited contact-directory helper is replaced
with synthetic identities/null destinations; it no longer loads `.env`, reads
contact variables or carries a real default chat identifier. Environment
observations and physics did not use that helper; regression and replay verify
the extraction rather than assuming compatibility.

The implementation remains coarse: a source-grounded Huelva coverage envelope,
synthetic scrub/population/relocation, assumed resource inventories and timings,
finite aircraft sorties, and delayed base requests/offers/authorizations. These
are not measured fleet rosters, roads, flight safety, suppression effectiveness
or historical incident dynamics. Warning routes can increase simulated exposure.

## What carries over from PortSimEnv

Organization: `data/`, `envs/<environment>/core/`, `openenv/`, `tasks/`, `tools/`,
`eval/`, `results/`, `design.md` and `project.yaml`. `train/` adds our measured
Qwen3 recipe. We use separate source and hosted-artifact lifecycles.

We do not copy its code, datasets or viewer, and do not claim its proven optimum,
public deployment or AgentEnv capabilities. Publication scripts and a new model
evaluation set should be added only when justified and tested.
