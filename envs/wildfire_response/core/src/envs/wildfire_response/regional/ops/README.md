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
5. **Authorize:** explicitly submit a live offer ID. Authorization starts the
   modeled mobilization and mission lifecycle. The model can also release assigned
   resources using their public IDs.
6. **Communicate:** submit structured messages to a public recipient. Population
   messages use `inform` or `evacuate`; base messages request `status`. Delivery is
   delayed, and an evacuation affects movement only after delivery.
7. **Advance:** call `advance()` to wait through one decision interval, or submit
   one action through the facade. Under the hood, the core receives an empty
   action to advance time.
8. **Monitor:** inspect the next observation, delivery queue, acknowledgements,
   resource states, and sensor memory. A request, authorization, or message is not
   proof that its intended effect occurred; follow the reported state and outcome.

The current facade has delayed offers and delivery acknowledgements, but no
explicit readback/repeat-back and correction protocol. An acknowledgement confirms
delivery only; it does not prove that a responder understood or executed an
instruction.

Each submitted action advances the configured simulation interval (two assumed
model minutes by default). Checks do not advance time. Free-text explanations
are not part of this ops interface or stored in its public trajectories.

The checked-in `wildfire-ops-task-pack-v0.1.0` supplies objectives for six
development fixtures mapped to the six regional scenario families. It does not
define train/validation/test splits or a held-out benchmark. The public task brief
includes each objective; the model cannot supply or edit its own scoring goals.
These objectives are exercise assignments tied to reported locations, not hidden
fire-truth labels or expert claims about the correct tactical response.

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

Fleet composition, readiness, transit and delivery times, evacuation behavior,
effectiveness, procedures, and deadlines are scenario assumptions unless a source
explicitly establishes otherwise. They are not validated operational protocols or
real-world response guidance. The verifier can check simulator accounting and
lifecycle invariants; it does not establish that a simulated tactical choice is
optimal in a real wildfire.
