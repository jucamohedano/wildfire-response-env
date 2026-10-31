# Source-day orchestration v2

`eval.qualify_source_day_v2.qualify` orchestrates twelve publicly named cases for
future trusted integration. It does not supply a source host, establish source
access, or make the existing synthetic-only host suitable for source cases.

```python
from eval.qualify_source_day_v2 import qualify

report = qualify(
    public_case_ids,                         # exactly 12 unique public strings
    checkpoint='/private/run-001.json',       # parent directory must exist
    run_id='public-integration-and-policy-version',
    expected_scope=approved_full_environment_scope,
    expected_reward_version=approved_full_environment_reward_version,
    make_environment=trusted_factory,       # (case_id) -> fresh scored host
    make_policy=trusted_policy_factory,      # (case_id) -> fresh policy
    verify_outcome=independent_verifier,    # (case_id, host) -> verification
    wall_seconds=1800,
    max_decisions=3000,
    resume=False,
    state_adapter=trusted_state_adapter,  # optional; omit to restart incomplete cases
)
```

The host exposes `situation()` and the existing shared-clock public methods.
A policy implements `act(public_host, observation)` and performs one bounded
decision using those methods. The facade is an accidental-access guard, not a
sandbox: factory, policy, and verifier must be trusted in-process code. The
verifier must independently pin the source configuration, masks and scoring,
and replay the complete episode. It returns the shared-clock verifier's
`status`, `scope`, `reward_version`, `verified`, `score_eligible` and `score`
fields. The caller must independently approve and supply the public full-environment
scope and reward-version identities, rather than derive them from the episode or
the verifier response being graded. The returned `scope` and `reward_version`
must exactly match those pins. Identifiers with a `restricted` component are
rejected as expected pins. Only `full_day`, verified and eligible finite scores
in [0, 1] for an uncensored 1,440-minute terminal day become `scored`.

These identity checks reject the existing restricted scope and reward even if an
adapter changes its status to `full_day`. They do not establish that arbitrary
identities or an injected verifier are truthful: independently reviewing the pins,
factory, and verifier remains the integration's responsibility. The currently
available restricted scorer is not a full-environment verifier.

Every checkpoint contains twelve rows. `censored` means the host reported the
boundary; `blocked` means the integration or verification could not establish a
score; `incomplete` means work has not finished, including budget, interruption,
and decision-limit stops. A trusted integration can raise `BlockedCase` for a
known blocker; exception details and verifier contents are never recorded.
`scored` is only as trustworthy as the supplied independent verifier and the
checkpoint storage. IDs and run ID must themselves be safe to disclose.

Use `resume=True` with the identical ordered IDs, run ID, expected scope and
expected reward version. The runner records both expected identities in the
checkpoint and binds them into private continuation envelopes. This revised
format has version `wildfire-source-day-orchestration-v2.1`; older unpinned v2
checkpoints cannot be resumed and are left untouched. Start a new checkpoint
rather than promoting their previously scored rows. Completed, blocked
and censored rows are retained. Without a state adapter, incomplete rows restart
at handover with a fresh host and policy. With an adapter, the runner saves the
host and policy together at handover and after every completed decision. A
budget stop or interruption resumes from the last durable pair; an interrupted
in-flight decision is replayed from the previous pair. `max_decisions` limits
each case per invocation, so a decision-limited case can progress on resume.

The optional trusted `state_adapter` implements four methods:

- `capture(host, policy) -> bytes` captures all private host and policy state
  required for exact continuation, without advancing either. `policy` can be
  `None` when the factory returns an already terminal host.
- `seal(plaintext: bytes) -> bytes` authenticates the entire plaintext with an
  integration-managed key. Use authenticated encryption if the deployment also
  requires confidentiality beyond filesystem permissions.
- `open(sealed: bytes) -> bytes` verifies authenticity before returning plaintext
  and raises on any failure. It must not deserialize untrusted unauthenticated data.
- `restore(case_id, state: bytes) -> (host, policy)` reconstructs a paired host
  and policy and checks all independent configuration and checkpoint pins. It
  must not use unsafe deserialization on data it does not trust.

The runner authenticates the run ID, case ID, scope and reward pins, public clock
and host status inside the envelope, checks the restored host's public clock and status, and refuses
resume when authentication fails or the adapter is missing. The adapter is
responsible for completeness and exactness of the private state, key management,
and ensuring the host and policy state match. These properties cannot be inferred
from the generic interface. The independent verifier must still replay and score
the final episode. The overall report is trusted local state, not an authenticated
ledger; rollback or deletion of an entire valid checkpoint is not detected.

The checkpoint's immediate directory must already exist, be owned by the current
user, and have no group or other permissions (for example, mode 0700). Existing
checkpoint files must be user-owned regular files with no group or other access,
and cannot be symlinks or hard-linked. New files are created exclusively at mode
0600 and replaced atomically; the runner returns only sanitized report fields,
not the private continuation. These checks assume the directory is protected from
concurrent modification by other processes running as the same user. Keep
checkpoints out of public outputs, logs and datasets, and protect backups and keys.
Change the run ID and checkpoint whenever inputs, code, policy or verifier change;
the caller must bind and verify those identities. Wall time is checked between
calls; injected calls must enforce their own timeouts. No aggregate score,
calibration claim or training data is produced.
