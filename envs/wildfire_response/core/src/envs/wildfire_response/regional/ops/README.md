# Wildfire Ops Control Center interface

The learned model represents the **ops desk/controller**. It gathers and checks
information, coordinates simulated resources, communicates instructions, and
monitors execution. It is not the incident commander. In the initial setup, bases
and field units are deterministic simulated counterparts; they return offers,
acknowledgements, status, and mission outcomes according to the scenario.

The regional v0.2 implementation keeps its existing `director` package, names,
action contract, and weighted reward for compatibility. `OpsControlCenterEnv`
adds an operations-facing Python facade and a separate objective reward over the
same simulator; it does not change the fire engine or regional scenario pack.

## Decision lifecycle

1. **Read and inspect:** review the public observation, including timestamped
   reports, weather, resources, offers, acknowledgements, and pending deliveries.
   Query only reports already received when more detail is needed.
2. **Check:** optionally call `check_orders` up to three times per decision. It
   checks the current public action contract, does not advance time, and cannot
   predict a base response or future conditions.
3. **Request:** submit a resource ID, public sector ID, and `recon` or `suppress`
   mission. A request is not a dispatch or authorization.
4. **Receive an offer:** after the simulated delay, an available owning base may
   offer or deny the request. Offers reserve a resource and can expire.
5. **Check the readback and authorize:** compare the base readback's resource,
   sector and mission with the offer. Send a correction for any mismatch, wait
   for the corrected readback, then confirm it and authorize the live offer.
   Confirmation and authorization may share one action. Authorization starts
   mobilization. Assigned resources can also be released using their public IDs.
6. **Communicate:** submit structured messages to a public recipient. Population
   messages use `inform` or `evacuate`; base messages request `status`. Delivery is
   delayed, and an evacuation affects movement only after delivery.
7. **Advance:** call `advance()` to wait through one decision interval, or submit
   one action through the facade. Under the hood, the core receives an empty
   action to advance time.
8. **Monitor:** inspect the next observation, delivery queue, acknowledgements,
   resource states, and sensor memory. A request, authorization, or message is not
   proof that its intended effect occurred; follow the reported state and outcome.

The facade exposes `confirm_readbacks` and `correct_readbacks` in addition to the
resource tools. A base repeats the allocation in a structured readback, while a
correction produces a new readback after one model minute. The controller must
check the resource, sector and mission before confirming. Neither delivery nor
confirmation establishes execution; on-scene resource state establishes arrival.

Each submitted action advances the configured simulation interval (two assumed
model minutes by default). Checks do not advance time. Free-text explanations
are not part of this ops interface or stored in its public trajectories.

The checked-in `wildfire-ops-task-pack-v0.2.0` supplies objectives for six
development fixtures mapped to the six regional scenario families. It does not
define train/validation/test splits or a held-out benchmark. The public task brief
includes each objective; the model cannot supply or edit its own scoring goals.
These objectives are exercise assignments tied to reported locations, not hidden
fire-truth labels or expert claims about the correct tactical response.

`ops-001` and `ops-003` contain one synthetic mission swap in the first base
readback; other initial readbacks match. This private generation setting is not
included in agent observations. Corrected readbacks restate the intended offer.
These are deterministic communication exercises, not measured error rates.

The new structured action fields are `readback_confirmations` (a list of readback
IDs) and `readback_corrections` (records with `readback_id`, `resource_id`,
`sector_id`, and `mission`). Each list allows at most twelve entries. All action
fields are validated before any effect. An invalid action still advances time
and contributes to the core invalid-action limit, but receives no extra readback
reward. For a matching readback, a combined authorization looks like:

```python
env.submit_action({
    "readback_confirmations": [readback["id"]],
    "authorizations": [offer["id"]],
})
```

Use `trajectory()` to export the public trace and `verify_episode()` after
termination or truncation to recompute the objective score, bind it to the
canonical task manifest, reward formula, simulator version and scenario-pack
hash, then replay the underlying simulator. Malformed action payloads are
redacted in public traces and represented by a replayable invalid-action marker;
the public trace intentionally does not preserve arbitrary rejected input.

## Objective reward and scenario limits

The ops facade uses `wildfire-ops-objective-fraction-v1`: at each committed
decision it awards newly completed task objectives divided by the fixed objective
count. The first objective type is `resource_arrival`: an acceptable resource
must be on scene in the declared sector by the declared deadline. Objective
specifications come from the checked-in canonical task manifest and are included
in the public task brief; neither the model nor reset caller can set them.
Deadlines must fit the episode horizon and align with decision intervals.

`OpsControlCenterEnv.verify_episode()` recomputes objective credit from the
recorded public observations, then independently replays and checks the underlying
regional simulator trace. This verifies completion of the declared simulated
mission; it does not establish that dispatching that resource was the best real
world decision. The original `WildfireResponseEnv` still uses
`regional-outcome-cost-v2`.

Fire spread, exposed population, resource effort, and message counts remain
separate diagnostics. They do not add to or subtract from objective reward in
this version.

## CPU baselines and replay

Run `python -m envs.wildfire_response.regional.ops.run` after installing the core,
or set `PYTHONPATH=envs/wildfire_response/core/src` from the repository root.
By default it runs all six development tasks with three policies on one worker:

- `passive` waits without new actions.
- `dispatch` follows the public assigned goals, compares and corrects readbacks,
  confirms and authorizes allocations, and releases resources after completion.
- `random_valid` samples subsets of the dispatch policy's legal candidates,
  keeping confirmation and authorization bundles together. It is a constrained
  random control, not a uniform sample of the full legal action space.

Use `--output .runtime/ops-development-smoke` for a fresh output directory,
`--task-id ops-001 --policy dispatch` for one episode, or `--replay PATH` for an
exported `trajectory.json.gz`. Public traces contain actions, readbacks, timing,
observations and objective rewards. A separate `evaluator/task-manifest.json`
contains task generation settings. Per-episode summaries include component
diagnostics, invalid actions, corrections, elapsed seconds including verification,
and process peak resident memory including previous runs in that process.

The dispatch baseline sends no population messages because the current objectives
only assign reconnaissance arrivals. It is a communication/dispatch execution
baseline; evidence triage and population protection need additional objectives
and a new held-out evaluation before any training claims.

Interface v2 traces require the readback protocol. Older interface-v1 traces can
be replayed from commit `fdc2a10`.

See [the development smoke results](SMOKE.md) for measured CPU costs, baseline
scores, replay checks, and the remaining task-design gate before training.

Fleet composition, readiness, transit and delivery times, evacuation behavior,
effectiveness, procedures, and deadlines are scenario assumptions unless a source
explicitly establishes otherwise. They are not validated operational protocols or
real-world response guidance. The verifier can check simulator accounting and
lifecycle invariants; it does not establish that a simulated tactical choice is
optimal in a real wildfire.
