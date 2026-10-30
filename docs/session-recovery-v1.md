# Trusted local session recovery v1

`eval.session_recovery_v1.SessionRecoveryV1` is a separate trusted-driver API for one reset v3 control and one deterministic local policy. It does not modify the engine, v3 tools, previous reports, or matrix results. This is a session primitive, not a restartable matrix runner or a training job recovery system.

Use a unique secret run identity and a newly created owner-only private directory for each episode. Keep sources, dependencies, inputs, scenario, policy code/configuration, and evaluator code/configuration immutable from before import throughout the run. The driver's source hashes are captured after import; they cannot prove what ran during import. The underlying control checkpoint pins the engine, interface, inputs, network and scenario. The caller must provide a version/configuration identity for a custom policy or evaluator and is responsible for its correctness.

```python
from eval.session_recovery_v1 import SessionRecoveryV1

# control is an already reset, dedicated ControlCenterV3 with artifact_dir=None.
driver = SessionRecoveryV1(control, private_dir, run_identity=unique_run_id,
                           policy_name='conservative', evaluation={'steps': 0},
                           evaluator=local_evaluator,
                           evaluator_identity='my-evaluator-code-and-config-v1')
driver.initialize()                 # new session only; durably saves sequence 0
result = driver.step(1)            # select, persist, execute, persist, deliver
result = driver.step(1)            # redeliver latest result without executing
result = driver.step(2)

# After process death, construct a new identically reset control and driver
# with the same initial evaluation, policy, identities, and evaluator:
result = driver.recover()          # latest result, or None at sequence 0
# Continue with the next sequence; repeat the current sequence to redeliver.
```

Built-in names are `passive`, `dispatcher`, `conservative`, and `stress`. Alternatively pass `policy_name`, `policy_factory=...` and a nonempty `policy_identity=...`. Each call of the no-argument factory must construct a fresh, independent, deterministic, purely local policy with `action(public, observation)`, `checkpoint()`, and `restore(state)` methods. Its JSON checkpoint must contain every state element that can affect decisions, including any RNG state. The factory, policy and optional evaluator must not perform I/O or have effects outside their owned session. `evaluator(evaluation, action, result)` may update only the supplied JSON evaluation state using deterministic local computations; an evaluator may close over read-only trusted control state, for example to compute a verifier result. Pin its implementation and configuration with `evaluator_identity`. Application code must arrange and validate any final verifier/reward handling; this driver does not claim a particular evaluation or score.

All policy selection and its public queries form one transaction. During selection the facade allows only situation, report, route/evacuation option, briefing, report-page and draft-check reads. It rejects every mutating or unknown tool; even if policy code catches the rejection, selection fails without applying that tool. This is an interface contract for trusted code, not a Python sandbox. Before selection, the previous complete state is durable. After selection, the selected action, policy, control including query guards, public observation, call ledger, movement observer and private evaluation state are durable together. Execution then uses the recorded action without selecting again, and commits the complete post-state and returned result atomically. If interruption occurs before the selected state is durable, selection and its queries may execute again from the prior complete state. If it occurs during execution or evaluation, those computations may also execute again. Control restore replaces the engine; the driver attaches exactly one movement observer to the restored engine and restores its private counters.

The control checkpoint contains the private simulation trajectory, audit and other hidden state; policy checkpoints, observations, call records and evaluator state may also be sensitive. Store and process the journal only within the trusted boundary. Never expose journal data, run identities, exceptions or tracebacks to actors. All actions, calls, policy and observer changes must go through the driver; do not mutate or operate on the owned objects between transactions. New wrappers must recover from a fresh identically reset state. Sequence checks and locking reject stale in-process writers and unexpected state changes. If a failure leaves non-JSON state that cannot be compared safely, the instance is poisoned and cannot recover or step; use a new identically reset instance to recover the durable journal. No in-process rollback is attempted in that case. Only the most recent result is retained. Atomic same-directory replacement, file and directory fsync, and an advisory lock require a filesystem that honors those operations.

Checksums detect accidental corruption, not hostile modification. Replacing a journal with an older valid journal after a process restart cannot be detected without an independent trusted monotonic anchor. An owner must prevent such rollback externally. A lost response can be redelivered for the latest committed sequence, but the caller must coordinate its own delivery acknowledgements and external history. An external agent's transcript or provider request/response is not automatically included or made durable. A model request whose response was not durably recorded is **ambiguous/incomplete**: it may already have run or incurred cost. Do not retry or claim exactly-once provider calls, external actions, payments, delivery, or model training from this protocol. Learner state and training jobs are unsupported. A custom policy is safe only if its owner satisfies the local deterministic contract; identity strings do not enforce it.
