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

The checked-in `wildfire-ops-task-pack-v0.3.0` supplies six control tasks
(`control-*`) and six dispatch exercises (`ops-*`), all development-only.
Control tasks cover evidence triage, population safety, incident resolution and
communication discipline without prescribing targets. Dispatch exercises retain
their explicit arrival assignments. The model cannot supply or edit its scoring
goals. Control scenarios reuse existing regional training configurations, so this
pack does not provide a fresh held-out benchmark.

`ops-001`, `ops-003`, `control-001` and `control-003` contain one synthetic mission swap in the first base
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

Use `triage_reports([{"report_ids": ["report-0"], "assessment": "fire_observed"}])`
to assess a received report with fresh matching sensor evidence. Other assessments
are `no_fire_observed` and `defer`. Matching location and observation time are
required to link multiple report IDs as duplicates. Public observations include
the assessment ledger and issued notices; private fire counts and future events
remain evaluator-only. See [OBJECTIVES.md](OBJECTIVES.md) for exact rules.

Use `trajectory()` to export the public trace and `verify_episode()` after
termination or truncation to recompute the objective score, bind it to the
canonical task manifest, reward formula, simulator version and scenario-pack
hash, then replay the underlying simulator. Malformed action payloads are
redacted in public traces and represented by a replayable invalid-action marker;
the public trace intentionally does not preserve arbitrary rejected input.

## Objective reward and scenario limits

The ops facade uses `wildfire-ops-objective-fraction-v2`: newly completed
objectives divided by the fixed count. The four broader control objectives are
evaluated at episode end only, with one quarter credit each. Scheduled incident
events/reports and deliveries must be complete; invalid-action abandonment earns
no terminal credit. Population safety and fire resolution use realised outcomes,
not message or arrival counts. The equal fraction is a transparent engineering
rubric, not a calibrated safety utility; always report the components separately.
Arrival-only objectives remain available in dispatch exercises. Objective specs
come from the canonical manifest. Deadlines must fit the configured horizon and
align with decision intervals.

`OpsControlCenterEnv.verify_episode()` recomputes objective credit from the
replayed public evidence and final simulator state, then checks the underlying
regional accounting. This verifies completion in the documented simulator; it
does not establish a real-world optimal response. The original `WildfireResponseEnv` still uses
`regional-outcome-cost-v2`.

Burned terrain, population exposure, resource effort and message counts remain
separate diagnostics alongside the four pass/fail control components. Exposure
and unfinished movement fail the population-safety goal; active fire fails the
incident-resolution goal. Damage and effort are not separately weighted here.

## CPU baselines and replay

Run `python -m envs.wildfire_response.regional.ops.run` after installing the core,
or set `PYTHONPATH=envs/wildfire_response/core/src` from the repository root.
By default it runs all six control tasks with five policies on one worker:

- `passive` waits without new actions.
- `dispatch` follows the public assigned goals, compares and corrects readbacks,
  confirms and authorizes allocations, and releases resources after completion.
  On broader tasks it visits report locations but neither triages nor suppresses.
- `control` uses public evidence to triage received claims, target conditional
  suppression, warn threatened districts and release units once local fire clears.
- `blanket_warning` evacuates every unwarned district once, without investigating.
- `random_valid` samples subsets of the selected task set's informed baseline candidates,
  keeping confirmation and authorization bundles together. It is a constrained
  random control, not a uniform sample of the full legal action space.

Use `--output .runtime/ops-development-smoke` for a fresh output directory,
`--task-id control-001 --policy control` for one episode, or `--replay PATH` for an
exported `trajectory.json.gz`. Public traces contain actions, readbacks, timing,
observations and objective rewards. A separate `evaluator/task-manifest.json`
contains task generation settings. Per-episode summaries include component
diagnostics, invalid actions, corrections, elapsed seconds including verification,
and process peak resident memory including previous runs in that process.

Use `--task-set dispatch --policy dispatch` for the legacy dispatch exercises,
or `--task-set all` for both task sets. `wildfire_core.ops` exposes the baseline
classes for an external client. These are deterministic public-observation
heuristics and a constrained stochastic control, not expert demonstrations.

Interface v3 traces require the evidence and readback contracts. Interface-v2
traces replay from `3202d49`; interface-v1 traces replay from `fdc2a10`.

See [the earlier arrival-only smoke results](SMOKE.md) for the prior v2 run and
[the broader control results](CONTROL_SMOKE.md) for the current CPU run, component
scores and acceptance checks. The [objective contract](OBJECTIVES.md) specifies
the semantics and remaining evaluation/calibration gates before training.

Fleet composition, readiness, transit and delivery times, evacuation behavior,
effectiveness, procedures, and deadlines are scenario assumptions unless a source
explicitly establishes otherwise. They are not validated operational protocols or
real-world response guidance. The verifier can check simulator accounting and
lifecycle invariants; it does not establish that a simulated tactical choice is
optimal in a real wildfire.
