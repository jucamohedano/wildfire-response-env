# Synthetic coverage expansion v1

Opt-in expansion: **960 physical configuration groups / 1,920 seeded tasks**,
up from the frozen benchmark's 120 groups / 240 primary tasks. Its 12 development
probes are unchanged. Eight families each contribute 80 training, 20 validation
and 20 test groups: **1,280 / 320 / 320 tasks** overall. Two wording/seed variants
per group are repetitions, not independent historical incidents.

The sampler independently draws ignition offsets/times, five wind vectors,
report delays, duplicate bursts, resource holds, second-base availability,
request delays, readback mismatch, broadcast delay/failure and wind-shift timing.
Family-specific constraints retain meaningful unavailable-resource,
reinforcement-delay and failed-protection challenges. Wind-shift timing only
affects families with wind events. This is marginal coverage, **not** exhaustive
Cartesian/pairwise coverage or guaranteed difficulty. Realized locations can snap
to the same supported cell; configuration hashes deduplicate actual scenarios.

## Integrity and provenance

- Deterministic bounded sampling; full configurations deduplicated against all
  v1 groups and development probes. Old files and reward remain unchanged.
- Matching ignition/claimed-location layouts share a split across weather,
  delays, families and variants. Configuration hashes ignore report wording and
  seeds. Splits are assigned before any policy rollouts or outcome inspection.
- Private configuration holds future events, factors and seeds. The public
  manifest exposes only opaque IDs and splits, plus catalog/input metadata.
- `catalog_lock.json` pins the source-grounded prepared/terrain inputs and
  compiler output. `provenance.json` records source registry and compiler hashes.
  Full private catalog, trajectories and evaluator audits stay under `.runtime/`.
- No new real-world incidents were collected. Geography/fuel/weather context
  retains the existing Málaga source provenance and source-specific terms;
  operational calls, readiness, report variants and decisions are synthetic.

Old v1 outcomes already informed development. This new catalog is **not** a claim
of an entirely research-blind benchmark. Its own validation/test outcomes remain
untouched by the smoke run. Location layouts within this catalog are split, but
one previously known landscape cannot establish geographic generalization.
Confidence intervals in future evaluation should resample location-layout groups,
not treat configurations sharing a layout or the two variants as independent.

## Build and first simulation

From repository root, with the existing validated input bundles:

```bash
export PYTHONPATH=envs/wildfire_response/core/src
python -m eval.build_expansion \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --prepared-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --topographic-sha256 e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd \
  --output .runtime/synthetic-expansion-v1 --smoke --seconds 600
```

Use a fresh output directory. Omit `--smoke` to compile without generating
trajectories. The bounded single-worker smoke attempts one training configuration
per family with passive and dispatcher policies (16 episodes). It retains failures
and censoring, records time/RSS, replays each completed episode and independently
recomputes reward. A budget stop is incomplete, never a passing leaderboard.
Verified validity is not an expert demonstration label.

Python agents use `wildfire_core.expansion.ExpandedEnv`, with the same constructor,
reset/step, observation and action contract as `BenchmarkEnv`. Each expanded
trajectory pins the expansion implementation and catalog. `verify_episode()`
replays this catalog, not the frozen v1 catalog. AgentEnv/OpenEnv exporters and
existing v1 training/evaluation commands still default to **v1**; this expansion
does not silently replace their locked inputs or their verifier.

## What is deliberately not scaled yet

One landscape; fixed two settlements and resource roster; night response;
120-minute horizon; existing response/capacity assumptions. Aircraft remain
daylight-grounded, so readiness timing is not advertised as extra variety.
Five wind vectors are synthetic model-unit perturbations, not additional measured
weather. New regions/population layouts require aligned source packs; daytime
aviation, larger fleets and longer horizons need separate mechanics/acceptance.
Do not scale all of these at once or relabel generated calls as historical data.

The 80% population / 20% terrain reward is unchanged. Issue #27 remains open;
more tasks do not resolve reward sensitivity or establish firefighting realism.
No paid inference, training, Hub visibility change or automatic publication runs
as part of compilation. AgentEnv bundle support and wider rollouts follow after
this coverage candidate is reviewed.
