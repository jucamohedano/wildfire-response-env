# Frozen benchmark protocol

`regional-benchmark-v0.2.0` freezes the existing `regional-task-pack-v0.2`
configurations and `regional-outcome-cost-v2` objective. It does not generate a
new, easier task pack. `benchmark_lock.json` records every task's split, seed,
group and configuration hash, reward weights, the 60-decision/two-minute contract,
four baseline names and input hashes for the evidence/assumption pack, simulator,
scenario generator, baselines and independent verifier.

Every task export or baseline evaluation checks this lock before creating output.
An input change fails closed: do not regenerate the lock to make a failed test
pass. Deliberately version a new benchmark, document the reason and preserve the
old revision/results. The lock ships with the optional portable wheel.

## Held-out evaluation, decided before execution

1. Pull and commit the locked inputs before running either held-out split.
2. Run all six validation tasks and then all six test tasks, once each against
   passive, seeded random-valid, blanket-warning and cooperative policies.
3. Use one sequential CPU worker, the frozen 60-decision budget, identical seeds
   per task across policies, independent accounting verification and exact replay.
4. Report all 48 episodes, all outcome components, natural completion, invalid
   actions, difficulty flags, time and peak memory. Retain every case, including
   failures or cases where weak policies outperform the reference.
5. Do not change reward weights, scenarios, baselines or the model prompt using
   these held-out outcomes. Report limitations instead. These splits become an
   observed baseline audit, not a fresh secret test set for subsequent iterations.

```sh
python -m envs.wildfire_response.regional.evaluate \
  --output .runtime/regional-v02-validation-audit --split validation
python -m envs.wildfire_response.regional.evaluate \
  --output .runtime/regional-v02-test-audit --split test
```

Models receive public observations only, never the lock's private task generator,
full scenario configurations, checkpoints or evaluator labels. A model adapter
must not select a policy or prompt from held-out outcomes. Its mechanical tests
use training/legacy fixtures and injected transports, without API calls.

## Scope

The 24/6/6 configuration groups and seeds are disjoint; the region, synthetic
population, templates and fire kernel are shared. This small alpha is not a
real-incident holdout, real dispatch simulator, optimality benchmark, RL training
certificate or public release. Independent accounting is not independent physics.
Provider trials, actual training and publication require separate execution.
