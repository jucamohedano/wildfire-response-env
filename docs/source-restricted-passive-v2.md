# Restricted source passive diagnostic v2

`eval/run_source_restricted_passive_v2.py` is a separately versioned, opt-in,
**unscored passive diagnostic** on the existing pinned v4 terrain and 12 source
cases. It uses a single CPU and never issues aircraft, ground, shelter or
population commands. Each policy decision calls `advance(60)` once and does not
react to reports delivered inside that wait. The host stops the wait early on
censoring or an unsupported event; this is not an adaptive control policy or a
full-environment evaluation. No scores are produced, including for a full day.

The driver pins its own source, the v1 input-loader implementation, restricted
runner, host and scorer. It reconstructs the existing source inputs, never
promotes unsupported cells, and authenticates private host/policy continuation
state with an HMAC key. A checkpoint is saved at handover and after each
completed wait. Interrupting a wait resumes from its previous durable boundary;
the policy has no hidden state. The HMAC does not encrypt sensitive host state:
keep checkpoint, key, and any error journal in a private user-owned directory.
A censored row is `censored`; an unsupported scheduled event is a `blocked`
runner row and is identified in the private `.errors.jsonl` journal. Other
setup, runtime, budget, and not-started cases remain explicit in the checkpoint.

Use the GIS environment and the existing local source artifacts documented in
[source-restricted-private-driver-v1.md](source-restricted-private-driver-v1.md).
Create a new mode-0700 directory; do not reuse a v1 checkpoint or key. For a
first bounded pass, `--max-decisions 1` gives each case up to one 60-minute wait:

```bash
mkdir -m 700 /tmp/my-passive-v2
timeout --signal=INT --kill-after=10s 600s \
  /home/juancm/miniconda3/envs/detectree/bin/python eval/run_source_restricted_passive_v2.py \
  --opt-in-passive-60 --checkpoint /tmp/my-passive-v2/progress.json \
  --key-file /tmp/my-passive-v2/continuation.key \
  --host-sha256 APPROVED_FROZEN_HOST_SHA256 --scorer-sha256 APPROVED_FROZEN_SCORER_SHA256 \
  --wall-seconds 600 --max-decisions 1
```

The internal wall check is cooperative between injected calls; a single wait or
source setup may overrun it. The outer `timeout` requests interruption at ten
minutes and, after a ten-second grace period, forcibly terminates it if needed.
An interrupted call is incomplete, never scored. Use `--resume` with the same
checkpoint, key, pinned sources and opt-in flag; another invocation continues
incomplete cases from their saved wait boundary. A fresh run never overwrites
an existing checkpoint or key. The 60-minute policy is not comparable to v1's
one-minute decision cadence if a policy responds to reports.
