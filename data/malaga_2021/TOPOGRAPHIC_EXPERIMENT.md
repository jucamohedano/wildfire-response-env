# Complete-support topographic experiment

This is a separate, opt-in research mode, not a calibrated wildfire forecast.
The legacy prepared environment remains unchanged and keeps slope disabled.

## One coherent surface

The offline compiler averages only complete positive finite MDT05 sample blocks
with a supported25m stencil halo. It deterministically selects the largest
fully supported rectangle inside the study mask. The pinned current domain is
37×36 cells at500m, containing1,332 supported height values. Every cell and every
adjacent edge, including the boundary guard, has a height. Missing heights,
inconsistent masks or an unsupported domain fail validation. Nothing is filled,
interpolated, treated as a firebreak, or silently given zero slope.

The rectangle is smaller than the parent37×41 envelope. Other supported cells
are deliberately omitted to keep one rectangular, hole-free simulation domain.
This is a declared modelling-domain choice, not evidence that the omitted cells
are bare ground or water. Coordinates, reports, synthetic population and refuges
are generated consistently in the cropped affine grid. All cases are initial
night-response exercises, not a reconstruction of the real incident.

For each directed neighbouring pair, edge grade is the difference of the same
shared cell heights divided by centre-to-centre distance. Diagonal distance is
500×sqrt(2)m. The experimental probability modifier is
`exp(3 × clip(grade, -0.5, 0.5))`; final eligible ignition probability is capped
at1. Uphill increases and downhill decreases that probability; equal heights
give1. Wind eligibility timing, finite water, population movement and assumed
ground travel are unchanged. These parameters are synthetic, not a measured
spread law or a terrain-based road/walking model.

Fire reaching the outer **two-cell guard** censors the episode before unknown
outside terrain can be treated as containment. Censoring is detected no later
than the end of the current committed action, with the first-contact tick
recorded. It yields no objective credit and sets `suitable_for_training=false`;
do not turn these incomplete outcomes into positive examples or policy-failure
ground truth. The dedicated boundary probe is diagnostic-only. Ordinary horizon
failures are different: they remain valid bounded-task outcomes.

## Reproduce

Restore the pinned ignored input caches described in the operational README.
Compile using the isolated data interpreter; run with dependency-free Python:

```sh
export PYTHONPATH=envs/wildfire_response/core/src
.runtime/malaga-data-venv/bin/python -m eval.compile_topographic_domain
python -m envs.wildfire_response.regional.topographic.run \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --prepared-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --topographic-sha256 e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd \
  --repeat --output .runtime/topographic-new-comparison
```

Both output destinations must be fresh. Python clients import
`wildfire_core.topographic.TopographicEnv`, supply both bundles and caller pins,
and explicitly choose `slope_enabled=True` or `False` plus
`allow_synthetic_conditions=True`. Six opaque development cases and a separate
`topo-boundary-probe` are available. The old observation/action protocol is
retained; static heights and the domain/censoring rules are public. Fire truth and
future events remain private. Same-domain slope-on/off runs use the same seeds
and policies; reactive policies may choose different actions after observations
diverge, which the comparison report records rather than claiming fixed actions.

The optional server is `wildfire_openenv.topographic`. Configure both input paths
and hashes with `WILDFIRE_PREPARED_INPUTS`, `WILDFIRE_PREPARED_SHA256`,
`WILDFIRE_TOPOGRAPHIC_INPUTS`, `WILDFIRE_TOPOGRAPHIC_SHA256`. Start locally with:

```sh
.runtime/openenv-test-venv/bin/python -m wildfire_openenv.topographic \
  --mode simulation --slope-enabled true
```

It defaults to127.0.0.1:8003 and one active episode. The factory instead requires
`WILDFIRE_TOPOGRAPHIC_SLOPE_ENABLED=true|false`. No hidden default enables slope.
`eval/check_topographic_openenv.py` accepts the same four bundle arguments,
`--diagnostic-probe`, `--report` and `--trajectory-dir`; without a slope choice it
compares both settings through real localhost clients and exported JSON replay.

## Watch one simple scenario

Generate a self-contained, local HTML replay of `topo-dev-000` with both the
deterministic protection baseline and a passive comparison:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m eval.render_topographic_replay \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --prepared-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --topographic-sha256 e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd \
  --output .runtime/topographic-replay-new
python -m http.server 8766 --bind 127.0.0.1 --directory .runtime/topographic-replay-new
```

Open `http://127.0.0.1:8766`, press Play, or seek through committed decision
frames. Switch policies to compare the same starting scenario, or switch the map
from evaluator truth to received report/sensor evidence. The complete file is an
**evaluator artifact**, even in evidence view: private fire truth is embedded in
the document. Never send it to a model, retrieval bank or public dataset. Keep
the generated HTML/JSON in ignored local storage; only the renderer and template
are source-controlled. No model, API key, GPU or live messaging is used.

These are verified recorded engine states, not an interactive policy editor,
live forecast or historical reconstruction. Source-derived elevations are real
map data; population, resources, communication and fire dynamics are synthetic.
Vehicle locations are only drawn when the engine confirms arrival, without
invented roads or travel animations. Grounded aircraft remain listed but cannot
be dispatched by the baseline. Playback speed is not simulation time.

## Data readiness after this step

CPU simulation, deterministic rewards/replay, complete terrain support and
external-client transport are working. This is **not yet a frozen training
dataset**. The next release gates are:

1. Generate broader, harder scenario configuration groups, including overlapping
   incidents, scarce/unavailable resources and misleading/late reports. Freeze
   disjoint train/validation/test groups before assigning seeds; this single
   landscape's smoke cases do not establish incident-level generalisation.
2. Freeze a compact model-facing observation/action schema, scenario distribution
   and reward version. Avoid repeatedly placing full static maps into every model
   prompt; validate any map-reference/query or dynamic-summary view for privacy.
   Keep synthetic fire/fuel/moisture, weather conversion, roster and communication
   assumptions explicit. Measured station weather remains context-only, not a
   secretly coupled front-weather forecast. Missing operational logs do not need
   to be fabricated before controlled synthetic RL experiments.
3. Evaluate baselines and reward/safety-priority sensitivity on the held-out
   configurations. Filter censored episodes from labels, and keep evaluator
   annotations separate from actor observations and experience retrieval banks.
   Report censoring rates; do not hide difficult cases to inflate performance.
   Expand the supported domain if severe scenarios systematically reach its guard.
4. Export versioned task manifests/trajectories, data cards, provenance, source
   rights and reproducible commands. Clear redistribution rights before public
   dataset/Space publication; running a private simulator is a different claim.

Official survey-mark comparisons are now recorded in
`terrain_control_validation_v1.json`; they do not certify terrain accuracy.
Reales is inside the active domain; the coastal REDNAP mark is outside its crop.
The producer documents orthometric heights for the MDT05 product, but the exact
WCS vertical realization, local survey edition, native void semantics and
mark-to-bare-ground offsets remain unresolved under issue8. The positive-data
restriction and complete-domain choice do not certify producer NoData or
real-world operational validity. See `SIMULATION_ACCEPTANCE.md` for the current
component gate. Real emergency use requires substantially different validation.
