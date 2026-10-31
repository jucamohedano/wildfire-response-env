# Private transactional fast-digest experiment v2

`eval/run_source_restricted_fast_transaction_v2.py` supersedes the earlier
experimental fast launchers for new runs. It offers separately identified
`minute` and `hourly` unscored modes. **Do not use the earlier fast launchers
for recoverable or source-verified runs**: their detached seal and bytecode
handling do not provide these guarantees. Their checkpoints are not accepted
by this version, and no migration or scoring approval is implied.

The parent checks caller-supplied source hashes, verifies the frozen core and
captures a source manifest. Its digest is included in the run identity and
authenticated checkpoint. The manifest conservatively covers Python sources
under `envs` and `eval` outside directories named `tests`; changes to unrelated
covered sources can therefore prevent resume. It passes verified bootstrap bytes to a fresh
isolated Python child. Before repository imports, the child installs a loader
that compiles repository modules from source bytes, verifies them against the
manifest, and rejects sourceless repository modules. It checks already loaded
source bytes and the pinned bindings again during the run. This is a trusted
private process, not a sandbox against arbitrary Python code.

After every legacy runner save, the subprocess publishes a single atomically
replaced `checkpoint.auth`: it contains the entire progress report and an HMAC
binding the mode, launcher, bootstrap, transaction code, digest launcher,
prototype, private driver, runner, adapter, containment module, host, scorer,
and, in hourly mode, hourly driver, plus the source-manifest digest. Resume authenticates that bundle before
atomically restoring `progress.json`. If a process dies after a progress write
but before the bundle replacement, the previous committed bundle is restored.
The initial handover report is published as an authenticated bundle before
the driver creates `progress.json` or starts source setup; a kill after that
publication can resume even if `progress.json` was never created. A kill before
the initial bundle is published has no resumable checkpoint. If initialization
stops with only `run.lock`, `continuation.key`, or temporary transaction files
and no `checkpoint.auth`, first ensure that no worker is still running. Then
create a new, empty mode-0700 directory and start a fresh invocation without
`--resume`; do not reuse the old key or treat the old directory as a checkpoint.
No simulation decisions have been lost in this window. A per-directory lock prevents concurrent use. Replacing the whole
bundle and key with an older authentic set is not prevented; the key and
private data must be protected together. HMAC provides no encryption.

Run the launcher by its source path, not `python -m`, in the GIS environment.
Use a new, empty, user-owned mode-0700 directory and independently reviewed
SHA-256 pins for each named file. `--mode hourly` additionally requires
`--hourly-driver-sha256`; `--mode minute` forbids it. For example:

```sh
python eval/run_source_restricted_fast_transaction_v2.py \
  --opt-in-fast-digest --mode hourly --run-dir /private/new-empty-directory \
  --launcher-sha256 "$LAUNCHER_PIN" --bootstrap-sha256 "$BOOTSTRAP_PIN" \
  --transaction-sha256 "$TRANSACTION_PIN" --digest-launcher-sha256 "$DIGEST_LAUNCHER_PIN" \
  --prototype-sha256 "$PROTOTYPE_PIN" --driver-sha256 "$BASE_DRIVER_PIN" \
  --runner-sha256 "$RUNNER_PIN" --hourly-driver-sha256 "$HOURLY_DRIVER_PIN" \
  --adapter-sha256 "$ADAPTER_PIN" --containment-sha256 "$CONTAINMENT_PIN" \
  --host-sha256 "$HOST_PIN" --scorer-sha256 "$SCORER_PIN" \
  --wall-seconds 300 --max-decisions 1
```

Repeat the same command and pins with `--resume`. Hourly waits remain one
60-minute policy decision and are distinct from 60 one-minute decisions.
Wall limits are cooperative. This is an unscored experiment, not a completed
twelve-case qualification.
