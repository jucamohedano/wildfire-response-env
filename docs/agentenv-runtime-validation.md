# AgentEnv runtime and local dataset validation

## Verified integration

The no-model fixture runs the actual pinned AgentEnv framework against a
Docker-deployed MCP server backed by `BenchmarkEnv`. It completes 60 decisions
and 120 simulated minutes, then publishes locally with `agent-env hf publish
--out`. The checker loads native task/episode Parquet and a simulator extension
with `datasets.load_dataset`, links them to the same completed framework
instance, and independently replays the exact public trajectory and reward.

[Actual CI run 40794260407](https://github.com/jucamohedano/wildfire-response-env/actions/runs/40794260407)
passes on commit `14d4280b35b36e647b7d14564602b30014c7aaf9`; #30 is closed.
The native and independently replayed rewards both equal `0.5427068798969625`.
[The retained result](../results/agentenv-runtime-acceptance-20261029.json) records
the canonical trace hash and loaded schemas. No model, API key, GPU, Hub upload
or real source pack is involved.

The optional runtime is pinned independently of the dependency-free core:
AgentEnv framework 0.9.1358, protocol 0.1.373 and HF plugin 0.3.0. The validated
loader versions are datasets 5.1.0 and pyarrow 26.0.0 on Python 3.12. See
`.github/workflows/agentenv-validation.yml` for the bounded, reproducible run.

## Data and replay boundary

The disposable `synthetic-agentenv-fixture-v1` has separate prepared, topographic
and catalog hashes. Its identifier can match a frozen task identifier; the input
and catalog hashes identify the synthetic configuration. This proves integration,
not reconstruction of historical wildfire data or physical accuracy.

The no-model driver stores its public trace as one canonical JSON string. This
is necessary because the pinned framework's context diff uses set semantics for
nested structured lists, which otherwise removes empty lists and deduplicates
repeated values. Source/header hashes, privacy checks and the unchanged exact
replay reject mutation. Tests exercise the actual context-diff operations.

The pinned HF plugin exports prompt responses as native chat trajectories but
omits run-code results. No model means the native chat trajectory is empty. The
checker therefore links a separately named simulator extension to the native
episode ID: episode/transition Parquet plus canonical raw JSONL from that exact
framework run. It does not fabricate model conversations. Native tables remain
byte-identical. Native, MCP-verifier and independently replayed rewards must
agree; replay success is not a reward of one. Development-only records remain
`training_eligible=false`.

The workflow uploads a compact synthetic result report with one-day retention.
On failure it can also retain guarded synthetic tables and a public fixture
diagnostic. It excludes private evaluator audits, credentials and ignored real
source packs. Source-code and geographic-data rights remain separate publication
gates in #42.

## Reproduce

Use the commands in the CI workflow on a Docker-capable Linux host. Core-only
and in-process MCP checks are useful local checks but do not replace the actual
framework deployment. This development wiring task is intentionally passive;
it is not evidence of model quality or learning improvement.

For a separate local export containing a replayable development trace:

```sh
PYTHONPATH=envs/wildfire_response/core/src python eval/check_agentenv_dataset_roundtrip.py \
  --dataset-dir /path/to/local-export \
  --prepared-inputs /path/to/prepared/inputs.json \
  --topographic-inputs /path/to/topographic/inputs.json \
  --episode-id 'exact development episode_id' \
  --report /tmp/agentenv-supplemental-roundtrip.json
```

This checker verifies pins, development-only eligibility, every transition and
independent replay. It reports actual loaded schemas and fails if native table
loading fails. Earlier local exports with no framework execution produced empty
native episode tables that datasets 5.1.0 could not load; those are not completed
native integration results. No checker command fetches or publishes a Hub dataset.
