# Wildfire Response v0.1 — evaluation, 18 October 2026

This release implements an environment and baseline policies. No model was
trained, no live messages were sent, and no GPU or paid inference was used.

## Configuration coverage

One serial evaluation worker completed 360 episodes: passive, random-valid and
deterministic policies on each of 120 configurations. The split is 80 training,
20 validation and 20 test configurations, with all six scenario families in each.
The coverage budget was eight decisions / 64 simulation ticks. All 2,880 actions
were accepted. Runtime was 1,378.1 seconds (about 23 minutes), with peak process
RSS of 54,124 KiB (about 53 MiB). Other acceptance checks shared the host during
parts of the run, so these durations are not an isolated throughput benchmark.

On the 20 held-out test configurations, mean rewards were:

- passive: -4.593935; mean exposed population fraction 0.045920;
- random-valid: -4.053303; mean exposed population fraction 0.040525;
- deterministic: -0.001416; mean exposed population fraction 0.000000.

Higher reward is better; zero is the upper bound of the outcome cost. The
deterministic baseline is useful on this distribution, but still issues
unnecessary warnings in false alarms. These are synthetic simulator results,
not measurements of real emergency response quality.

Natural termination counts across the 120 cases per policy were passive 20,
random-valid 73 and deterministic 90. The others reached the 64-tick budget.
Zero exposure by that horizon does not prove the population stays safe later.

## Longer episodes and experience

The six-family smoke suite completed 18 episodes with the full default budget
of 32 decisions / 256 ticks. Mean rewards were passive -4.971939,
random-valid -0.000030 and deterministic -0.003668. The random baseline's better
score in this small sample illustrates why six smoke tasks are insufficient to
rank policies. Do not combine these means with the 64-tick coverage results.

A separate frozen-memory experiment compared deterministic and retrieval policies
on six held-out scenarios at the 256-tick budget. The bank contained 26 completed
training trajectories. Both policies scored -0.022721 on average, with no exposed
population. The minimal report-location retrieval baseline is integrated, but
this experiment provides no evidence of a performance improvement. It does not
train weights or update memory from test outcomes.

## Acceptance evidence

- Existing repository suite: 264 tests, successful with 19 skips and two expected failures.
- Environment acceptance: 17 tests passed, including privacy, reset isolation,
  delayed warnings, bounded report batches, invalid-action atomicity, reward
  checks, seeded replay, frozen-bank exclusion and cached/uncached state equivalence.
- OpenEnv adapter/client: three tests passed, including a complete external-client
  episode matching the local observations, rewards and lifecycle across satellite delivery.
- A recorded full smoke episode and a held-out coverage episode reproduced their
  rewards and public observations from serialized actions.
- JSONL/Parquet export, portable wheel/source-archive builds and installation
  outside the repository were exercised. The core requires no external packages.
- OpenEnv 0.7.0 static manifest validation passed. No maximum-reward oracle is
  declared, so full semantic registry certification is not claimed.

The optional counterfactual evaluator scores alternatives under the same reward
version, keeps outgoing messages fixed, examines the first interval only, labels
its result best-found and rejects truncated searches as teacher labels.

## Reproduce and inspect

From the repository root:

```sh
python -m envs.wildfire_response.eval.run --split smoke --output .runtime/reproduce-smoke
python -m envs.wildfire_response.eval.run --split all --max-decisions 8 --output .runtime/reproduce-coverage
python -m envs.wildfire_response.eval.report .runtime/reproduce-coverage
python -m envs.wildfire_response.eval.run --replay .runtime/reproduce-coverage/trajectories/test-119-deterministic.json.gz
```

The measured artifacts are under `.runtime/wildfire-response-coverage-v1/`:
`manifest.json`, `summary.json`, `results.jsonl`, compressed public trajectories,
Parquet transitions and separate `private/` evaluator files. The smoke artifacts
are the first six configurations in `.runtime/wildfire-response-validated/`.
The memory comparison is `.runtime/wildfire-response-retrieval/`.
Directories contain hashes and runtime provenance; they are gitignored.

## Release limits

The benchmark uses one illustrated map, a fixed fleet, templated language,
coarse district groups and scripted escalations. Roles and channels are untrusted
report attributes. Unnecessary evacuation cost is conservatively charged only
in no-ignition episodes. Reports and sensor memory are verbose and may need
compaction before economical LLM experiments.

The container specification is supplied, but Docker was unavailable here and
the image was not built. Code-owner licensing and map redistribution terms
remain unresolved. Public hosting and FineEnvs submission should follow those
decisions and a container smoke test. No external repository or Space was created.
