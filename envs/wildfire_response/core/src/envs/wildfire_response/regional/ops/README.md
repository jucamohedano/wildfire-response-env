# Wildfire Ops Control Center interface

The learned model represents the **ops desk/controller**. It gathers and checks
information, coordinates simulated resources, communicates instructions, and
monitors execution. It is not the incident commander. In the initial setup, bases
and field units are deterministic simulated counterparts; they return offers,
acknowledgements, status, and mission outcomes according to the scenario.

The regional v0.2 implementation keeps its existing `director` package, names,
and action contract for compatibility. `OpsControlCenterEnv` adds an
operations-facing Python facade over that simulator; it does not change the
fire engine, scenario pack, action validation, or reward.

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
model minutes by default). Checks do not advance time. An optional explanation
field is validated by the core but is not persisted or scored.

## Reward and scenario limits

The weighted reward is unchanged in this documentation slice. The implementation
continues to use `regional-outcome-cost-v2`, which computes reward as the reduction
in a weighted cost covering population exposure, burned terrain, unnecessary
evacuation, committed resource time, redundant notices, invalid actions, and
unresolved incidents. This is not a coordination-goal fraction, and its weights
have not been recalibrated here.

Fleet composition, readiness, transit and delivery times, evacuation behavior,
effectiveness, procedures, and deadlines are scenario assumptions unless a source
explicitly establishes otherwise. They are not validated operational protocols or
real-world response guidance. The verifier can check simulator accounting and
lifecycle invariants; it does not establish that a simulated tactical choice is
optimal in a real wildfire.
