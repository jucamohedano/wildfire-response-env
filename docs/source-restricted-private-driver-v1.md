# Restricted source private driver v1

`eval/run_source_restricted_private_v1.py` reconstructs the existing nominal v4
source terrain and reviewed source catalog from the locally pinned artifacts,
then tries the twelve cases serially with the restricted host. Its transparent
policy waits exactly one simulated minute per decision. It makes no ground,
evacuation, aircraft or shelter decisions. This is a restricted synthetic
exercise, not a full incident qualification or historical reconstruction.

Use the GIS Python environment that can run `prepare_exercise_v4`. All supplied
paths in the driver and the original source snapshot must exist. The command
requires the frozen host and scorer SHA-256s, obtained after coordinating with
anyone editing those files. The driver checks both hashes at setup, case creation,
policy calls and verification. It forces numerical libraries to one thread and
uses one simulation worker. For example, choose an unused private 0700 directory:

```bash
mkdir -m 700 /tmp/my-private-source-run
HOST_SHA=$(sha256sum envs/wildfire_source_restricted_v1/host.py | cut -d' ' -f1)
SCORER_SHA=$(sha256sum envs/wildfire_source_restricted_scored_v1/__init__.py | cut -d' ' -f1)
/home/juancm/miniconda3/envs/detectree/bin/python eval/run_source_restricted_private_v1.py \
  --checkpoint /tmp/my-private-source-run/progress.json \
  --key-file /tmp/my-private-source-run/continuation.key \
  --host-sha256 "$HOST_SHA" --scorer-sha256 "$SCORER_SHA" \
  --wall-seconds 300 --max-decisions 1
```

With no approval file, the driver uses the raw restricted host and its terminal
verifier always refuses a score. The checkpoint reports censors, blocked setup or
integration errors, and incomplete cases separately. Its case labels are public
aliases. The private checkpoint contains authenticated encoded host checkpoints;
they contain sensitive simulator state and must never be published. On the first
invocation the driver exclusively creates a private 32-byte HMAC key. With
`--resume`, it reads that same key and restores the last durable host checkpoint
and stateless passive-policy version for each incomplete case. The runner binds
the state to the run, case, clock and host status. An interrupted in-flight
decision returns to its previous durable checkpoint. Protect the key, checkpoint,
and any backups together; HMAC authentication does not encrypt the state. The
independently trusted state is limited to the source host's supported checkpoint
contract. Completed rows are preserved. Private error details, if any, are stored
in a mode-0600 journal beside the checkpoint; the console prints aggregate counts
only. Each invocation may use `--resume` with the same checkpoint, key, frozen
source files and approval. Wall time is cooperative and source validation or a single
host call can exceed the limit. An incomplete row marked `not_started` was not
attempted. A setup failure cannot be treated as an evaluation result.

For scoring, the caller must separately review and approve all source-case masks,
public population disclosures, and identities. Pass `--approval FILE` together
with a separately obtained `--approval-sha256` for its exact bytes. The private,
user-owned, mode-0600 regular JSON file must have exactly:

```json
{"version":"wildfire-restricted-evaluation-approval-v1", "cases": {
  "case-01": {"case_id":"PRIVATE_SELECTOR_ID", "mask":[0],
              "public_population":[], "identity":{}}
}}
```

The `cases` object must contain all twelve aliases; each entry must carry the
independently approved case selector, fixed mask, public roster, and full identity
for the frozen code and handover. The single displayed case is only a schema
example, not a valid approval. This driver never creates approvals or treats a
candidate derived from the episode as independently approved. With approval it
constructs `RestrictedSourceScoredV1` and invokes the restricted scorer's fresh
host replay verifier. Its `restricted_scored` results are scoped to no-ground,
no-evacuation operations and cannot be full-environment scores. Keep approvals,
checkpoints and hashes private when they reveal private case configuration.
