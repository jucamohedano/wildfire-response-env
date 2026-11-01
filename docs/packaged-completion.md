# Local packaged completion workflow

Run these commands from the repository root on Linux with Python 3.12+. Use an
existing local copy of the pinned private package described in
[the runtime guide](hub-runtime.md); the examples call it
`.runtime/wildfire-response-spain`. Install the core and dependencies in an
isolated environment:

```bash
python -m pip install -e envs/wildfire_response/core
python -m pip install pytest==9.1.1 pyarrow==25.0.1
```

## Build a local development bundle

Choose a **new**, nonexistent output directory outside the source package. The
builder verifies the source manifest against `data/hub_runtime_v1/release.json`
and checks its inventory and payload hashes before copying; it verifies the
resulting bundle too. It does not modify or upload the source package.

```bash
python -m eval.build_completion_bundle \
  --source .runtime/wildfire-response-spain \
  --output /tmp/my-wildfire-development-bundle
python -m eval.hub_runtime \
  --data-dir /tmp/my-wildfire-development-bundle --verify-only
```

The bundle is private local development data, tied to the current implementation
and lifecycle. It adds source-map development scenarios and omits old validation
evidence that applies to different scenarios. These synthetic, related exercises
are not historical replays, held-out evaluations, or a training corpus. The
source-map runtime requires explicit acceptance of its unapproved inputs; this
does not approve or calibrate them.

## Public-tool pilot

The pilot uses the actor's public tools and verifies the completed episode and
its evidence gate. Pick a fresh output directory; select an advertised development
case explicitly. The wall limit is at most 1,800 seconds.

```bash
python -m eval.packaged_runtime_pilot \
  --data-dir /tmp/my-wildfire-development-bundle --runtime es-ma-2021 \
  --case-id incident-day-realmap-development-v1-000 --allow-unapproved-data \
  --output-dir /tmp/my-wildfire-pilot --wall-seconds 1800
```

A timeout or command limit is incomplete, not a pass. If a checkpoint was saved,
resume with the same package, case, runtime, code and output directory by adding
`--resume`; `--max-commands` is a total command limit, so increase it if it was
reached. Its 1,800-second evidence gate counts elapsed time across resumed runs.
The pilot exits successfully only if its verification and evidence gate pass. Its output directory contains private checkpoints, results and actor
trajectory; keep it private.

## Full source-development matrix

The source validator crosses the 12 additional source-map development cases
with four policies (48 cells), using one CPU worker. Start with a fresh output
directory on disk-backed storage, check free space and allow a suitable wall budget:

```bash
df -h /var/tmp  # Or check the filesystem containing your chosen output directory.
python -m eval.validate_source_runtime_matrix \
  --data-dir /tmp/my-wildfire-development-bundle --runtime es-ma-2021 \
  --output /tmp/my-wildfire-source-matrix --wall-seconds 3600

# Resume an interrupted or budget-limited run with the same identities.
python -m eval.validate_source_runtime_matrix \
  --data-dir /tmp/my-wildfire-development-bundle --runtime es-ma-2021 \
  --output /tmp/my-wildfire-source-matrix --wall-seconds 3600 --resume
```

Keep the package, lifecycle, implementation and validator unchanged when
resuming. `report.json` and `report.html` contain aggregate progress; the
`private/` directory contains checkpoints and detailed evaluator material and
must remain private. An incomplete, blocked or failed run is not a completed
matrix; inspect the report status and findings. The optional `--max-cells` limits
work for a partial run and must be omitted for the full matrix.

## Local OpenEnv HTTP client test

Install the optional adapter in the same environment, then point the test at the
bundle. The test starts a loopback HTTP server, discovers and resets a new
source-map development case, runs a full day, compares it with the local API,
and checks public responses for private markers. It needs loopback socket access
and can be CPU-intensive; wait for other simulation workloads to finish.

```bash
python -m pip install -e 'envs/wildfire_response/openenv[dev]'
WILDFIRE_LOCAL_RUNTIME=/tmp/my-wildfire-development-bundle \
  python -m pytest -q tests/test_source_development_external.py
```

A missing runtime or optional dependency can cause a skip; a skip is not a pass.
