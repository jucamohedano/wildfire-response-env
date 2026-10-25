# Data storage and reproducibility

We currently use local files, not a database or remote object store. Source code,
small audit/provenance JSON and catalog locks are versioned in Git. Original
downloads, compiled input bundles, full trajectories, scoring evidence and model
checkpoints stay in ignored local storage. Nothing is uploaded automatically.

## Layers

1. **Original evidence:** provider files, URLs, retrieval dates, byte hashes and
   source limitations. Preserve originals; quarantined rasters remain excluded.
   `data/malaga_2021/provenance.json` indexes the source-grounded preparation.
2. **Shared simulator inputs:** validated terrain/fuel/weather JSON bundles with
   caller-pinned SHA256 values. These are loaded once per environment, not fetched
   from public services during an episode. They contain source-derived context
   and explicitly labelled assumptions, not a verified historical reconstruction.
3. **Scenario configuration:** versioned configuration-group membership, seeds and
   hashes. Small catalog locks belong in Git. Compiled factors/future events are
   evaluator configuration, not model observations. Split by configuration group
   before evaluating policies; variants never cross train/validation/test.
4. **Episode data:** canonical replay, compact transitions and private scoring
   records are separate artifacts. Keep outcomes even when a policy fails.

## Stress-run layout

```text
.runtime/topographic-stress-<run>/
  manifest.json                         # revision, pins, seeds, split, policy, hashes, status
  summary.json                          # aggregate diagnostics, elapsed time and peak RSS
  public/catalog_manifest.json          # IDs, group membership and hashes; no scenario factors
  public/trajectories/<case>/<policy>.json.gz
  public/transitions/<case>/<policy>.jsonl.gz
  shared/maps/<sha256>.json              # static public landscape, content-addressed once
  private/catalog.json                  # generator factors and future scenario configuration
  private/evaluator/<case>/<policy>.json.gz
```

“Public” means model-observable trajectory content, **not cleared for Internet
publication**. The run manifest and reset-record episode header carry replay
metadata outside the observation. Do not put seeds, configuration IDs, evaluator
annotations or future-event catalogs into a model prompt simply because they
exist on disk. Only the `observation` field is the actor's view.

The canonical trajectory preserves the complete original public contract for
exact replay. Compact JSONL has a reset record and one record per committed
action, including observation, action, incremental objective reward and terminal
flags. Static `landscape` is replaced by a relative `landscape_ref`; resolving it
reconstructs the original observation losslessly. Resolve that reference before
using an existing adapter that expects an inline landscape. A model-facing map
query tool is not provided by this storage change.

Maps are shared between policies and decisions when their complete content is
identical. Canonical/compact files use deterministic gzip with finite JSON values;
the manifest records file SHA256 and byte sizes. Private verification includes
reward components, synthetic truth and scoring evidence and must never enter an
actor prompt, demonstration input or experience-retrieval bank. Do not serve the
whole run root to an agent; expose only an allowlisted observation/tool API.

`suitable_for_training` records verifier validity. `training_eligible` additionally
requires the training split and no boundary censoring. Validation/test and
censored episodes are retained with `training_eligible=false`; this is a guard,
not approval to start training. Failed policy decisions are not silently filtered
to inflate scores. A storage hash proves byte integrity, not real-world truth,
optimal decisions or safety.

## Persistence

Active development is on `codex/wildfire-ops-control-center` in the standalone
environment repository. Changes are pushed there; raw data is not committed.
Local ignored backups and Git bundles are preserved under
`/home/juancm/hackspain/wildfire-response-env/.runtime/`; the temporary working
checkout is not the only retained copy. Backups are local, not off-machine disaster
recovery, and private directories are not an encryption guarantee. Checkpoints,
large datasets and source assets need an explicit retention/rights decision before
any remote upload. No model weights, training jobs or paid services are needed
for this CPU batch.
