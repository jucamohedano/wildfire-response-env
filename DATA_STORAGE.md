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

The first36-episode stress batch retained16.9MB of canonical compressed replays.
Its compact transition view is approximately463KB plus one25KB shared landscape;
all1,302 observations were reconstructed exactly. This is map deduplication, not
discarding reports, actions or rewards. Private compressed verification is kept
separately. See `data/topographic_stress_v1/provenance.json` for exact byte counts.

## Persistence

Benchmark reference runs add a public ID/split manifest, checksum-pinned
preregistration for held-out evaluation, compressed actor trajectories, private
per-minute audits and independent verification. The JSON/HTML report records
safety-first ordering separately from the graded learning score and includes
incomplete/censored attempts. Training preparation writes sealed train-only
demonstrations and frozen retrieval banks; CPU dry runs cannot satisfy model
readiness gates. See `data/benchmark_v1/README.md` and `train/BENCHMARK.md`.

Model evaluation writes request records to each case's `requests.json`. Rejected
tool responses now include `response_diagnostics`: the validation stage, exception
type/message, finish reason, tool name, rejected arguments and ordinary response
content when present. Missing/unexpected action fields and invalid nested values
have field-specific errors. A malformed response still follows the existing
invalid-action rules; diagnostics do not repair it or change the reward.

Diagnostics use an allowlist that omits provider headers and reasoning fields.
Known transport credentials and recognizable provider-key patterns are redacted.
Captured text has a shared 65,536-character budget, with bounded nesting and
collection sizes; `redacted` and `truncated` make changes explicit. The excerpts
are debugging evidence, not byte-exact provider responses or training targets.
They remain in ignored runtime logs and are not supplied to the policy. Transport
failures without a response still record the exception type only. Earlier runs
without these diagnostics cannot recover their discarded replies.

### DeepSeek benchmark trial, 2026-10-26

The current-contract run is `.runtime/deepseek-benchmark-20261026/`, separate
from the older `cloudrift-ops-eval-*` runs. It retains all three model trajectories,
six complete CPU-reference trajectories, private scoring audits, verification,
request diagnostics and the pre-run plan. Three model episodes ended in
three-strike abandonment; none reached the120-minute horizon. Do not relabel
these as successful demonstrations or natural-horizon wildfire outcomes.

The compact public report under `results/deepseek-benchmark-20261026.{html,json}`
contains metrics and tool diagnostics, not terrain maps or private audits.
The complete runtime directory is also backed up with mode0600 at:
`/home/juancm/hackspain/wildfire-response-env/.runtime/deepseek-benchmark-20261026/trajectories-and-diagnostics.tar.gz`.
Archive size:5,127,828bytes; SHA256:
`d4d454566904bbf34bd62e20e4a3b8d3eb81be51bebceeea76f5d491823d9c7c`.
Pinned input packs remain in the earlier benchmark-release backup. No credential
is stored in this archive, and no raw runtime artifact was uploaded.

### GPT-6 Luna subagent trial, 2026-10-26

`.runtime/luna-subagent-benchmark-20261026/` retains three complete120-minute
model trajectories, six matched reference trajectories, private audits,
verification and180named-action records. All model traces replay exactly. The
actor manifest records the requested model/effort, fresh child ID, instruction
isolation and its self-reported compliance; that self-report is not a security
audit. Development cases remain ineligible for training-bank updates.

The public report is `results/luna-subagent-benchmark-20261026.{html,json}`.
It includes the corrected invalid triage action, active-fire outcome and a
scenario/physics-pin-checked comparison with the differently hosted DeepSeek
trial. Repeated wait actions are collapsed visually, not removed from records.

A mode0600 local archive is retained at
`/home/juancm/hackspain/wildfire-response-env/.runtime/luna-subagent-benchmark-20261026/trajectories-and-diagnostics.tar.gz`.
Archive size:7,268,047bytes; SHA256:
`48b4cddc7876c26b3a163f0548b670ab87e320a2f777c2070336a9372d4a6ef1`.
No CloudRift requests were made; Codex usage is not cost-metered by this harness.

Active development is on `codex/wildfire-ops-control-center` in the standalone
environment repository. Changes are pushed there; raw data is not committed.
Local ignored backups and Git bundles are preserved under
`/home/juancm/hackspain/wildfire-response-env/.runtime/`; the temporary working
checkout is not the only retained copy. Backups are local, not off-machine disaster
recovery, and private directories are not an encryption guarantee. Checkpoints,
large datasets and source assets need an explicit retention/rights decision before
any remote upload. No model weights, training jobs or paid services are needed
for this CPU batch.
