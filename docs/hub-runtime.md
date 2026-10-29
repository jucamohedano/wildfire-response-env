# Download prepared runtime data

The private dataset [jucamohedano/wildfire-response-spain](https://huggingface.co/datasets/jucamohedano/wildfire-response-spain)
contains prepared runtime inputs and standard Parquet tables. Contributors do not
need to regenerate terrain, fuel or roads. The current real source context is
Sierra Bermeja, Málaga, September 2021; the generic repository can gain other
regions in later versioned releases.

Use Python 3.12+ in this code repository. The CPU core has no external runtime
dependencies. Install the Hugging Face `hf` CLI separately if necessary, and
configure `HF_TOKEN` or use `hf auth login` with access to the private dataset.
For example, install the tested CLI with `python -m pip install huggingface_hub==1.17.0`.
The bounded episode CLI is tested on Linux and uses POSIX timers.

```bash
python -m pip install -e envs/wildfire_response/core
hf download jucamohedano/wildfire-response-spain --type dataset \
  --revision c84690243840c6339bf82a3b41e0cea279e49a70 \
  --local-dir .runtime/wildfire-response-spain
python -m eval.hub_runtime --data-dir .runtime/wildfire-response-spain --verify-only
python -m eval.hub_runtime --data-dir .runtime/wildfire-response-spain \
  --runtime fixture --policy passive --wall-seconds 300
python -m eval.hub_runtime --data-dir .runtime/wildfire-response-spain \
  --runtime es-ma-2021 --allow-unapproved-data --policy dispatcher --wall-seconds 300
```

The immutable revision above is also tagged `v0.2.0`; its lock is
`data/hub_runtime_v1/release.json`. The verifier checks the schema, payload hashes,
canonical runtime pins, engine implementation and lifecycle identities. It
rejects stale/incompatible code rather than silently changing a scenario.
Hugging Face transport metadata is excluded from the payload, never used as a
runtime asset. The source-data flag is required independently of manifest claims;
estimated fuel does not become measured or approved by being downloaded.

The default case is deterministic. `--case-id` chooses one of the runtime's six
active IDs. `--output PATH.json` records the compact result; `--max-commands` and
`--wall-seconds` bound smoke execution. Incomplete runs have no successful score.
A completed run independently verifies replay and reward. These are CPU reference
policies, not model evaluations or expert demonstrations. A model uses the same
`IncidentDayTools` interface through a separate bounded provider adapter.

## Dataset configurations

`tasks` has 12 development rows; `landscapes` has two runtime records (one
synthetic fixture and one source-map context). `station_weather` has 384 normalized
source-clock records; `incident_claims` and `municipal_population` each have seven
retrospective records. Weather receipt times are unknown, and municipal totals
are not population-at-risk observations. There is no active train/validation/test
corpus, no model trajectory table and no claim of 12 independent real incidents.

Install the optional table reader with `python -m pip install datasets==5.1.0`.

```python
from datasets import load_dataset
revision = "c84690243840c6339bf82a3b41e0cea279e49a70"
tasks = load_dataset("jucamohedano/wildfire-response-spain", "tasks",
                     split="development", revision=revision, token=True)
```

Hugging Face Datasets loads the tables; `hf download` retrieves the runtime
assets. Only public observations/tools belong in model context. Evaluator audits,
future-event catalogs, checkpoints, provider logs and credentials are excluded.
Mixed source terms remain in the dataset card and provenance records; the
release is private and is not public redistribution clearance. Physical/source
calibration and issue #42's decisions remain separate. The original
`wildfire-response-spain-sierra-bermeja-2021` dataset is retained as an unchanged
historical snapshot.
