# Wildfire Ops Control Center interface v2

This interface represents one learned operations desk in a simulated wildfire
control center. The ops operator reviews public information, requests and
authorizes resources, sends structured messages, and follows execution. It is
not the incident commander and does not replace base or field-unit behavior.

The facade wraps the existing `RegionalResponseEnv`. It does not copy its fire
engine or change the regional v0.2 task pack. The legacy `WildfireResponseEnv`
keeps `regional-outcome-cost-v2`; the ops facade returns the separately versioned
`wildfire-ops-objective-fraction-v1` reward.

## Tools

- `get_situation()` returns a deep-copied public observation.
- `get_reports(report_ids=None)` returns received reports only.
- `check_orders(action)` checks the existing action contract without advancing
  the clock; the core allows at most three checks between committed actions.
- `request_resources(requests)` sends resource requests to their owning bases.
- `authorize_resources(offer_ids)` authorizes only currently valid offers.
- `confirm_readbacks(readback_ids)` confirms matching live readbacks.
- `correct_readbacks(corrections)` restates an offered resource, sector and mission
  when a readback differs. A new readback arrives after one model minute.
- `release_resources(resource_ids)` releases assigned resources.
- `send_messages(messages)` sends the existing structured recipient/purpose
  messages.
- `advance()` advances the existing simulator with no new orders.
- `submit_action(action)` remains available for a caller that needs to combine
  compatible order fields in one core decision.
- `trajectory()` exports the public action/observation trace.
- `verify_episode()` recomputes the objective score, checks core accounting, and
  replays the underlying regional actions after episode end.

Every committed tool action and `advance()` makes exactly one call to
`RegionalResponseEnv.step`. The existing action clock, delayed deliveries,
validation, atomicity and transition limits remain the source of simulator
behavior. The core outcome reward is retained privately for accounting and
replay; the facade returns the new objective reward. Read-only lookups and
checks do not advance time.

## Authority and evidence

The adapter uses `ops` as the public sender/recipient label for the learned
control-center role. Base and field-unit identities remain the simulated
counterparts from the underlying environment. Reports remain untrusted claims;
the facade does not add or expose private report annotations, future events,
scenario truth, or evaluator checkpoints.

The ops layer adds `wildfire-ops-readback-v1` over the core's delayed offers.
Each visible live offer includes a base readback of resource, sector and mission.
Authorization requires its matching readback to be confirmed. A correction must
restate the original offer exactly; changing the allocation requires releasing
and making a new request. Confirmation and authorization may share one action.
An invalid combined action changes neither readback nor mission state and still
advances the core clock. Expired, closed, or superseded readbacks cannot authorize
an allocation. Delivery and confirmation remain separate from arrival evidence.

The task pack contains deterministic synthetic mission swaps in two fixtures.
It does not expose the fault schedule in observations. The agent compares the
public offer and readback instead of receiving a correctness label. Message text
uses fixed readable templates; this is not certified ASD-STE100 compliance or a
validated emergency radio protocol.

Resource availability, transit times, wind gates, population behavior and
fire effects retain their existing synthetic/assumed labels. Nothing in this
interface certifies real dispatch authority, response doctrine, flight safety,
or emergency-message wording.

## Scoring boundary

The versioned task manifest supplies a fixed, non-empty list of public objective
records. Reset accepts a task ID, not caller-provided scoring goals.
The first supported objective is `resource_arrival`: an accepted resource kind
and mission must be on scene in the declared sector at a post-action observation
no later than the deadline. Deadlines align with decision intervals. Each
objective counts once; per-step reward is newly completed objectives divided by
the fixed objective count. Checks, acknowledgements, messages, and explanations
do not earn credit by themselves.

`verify_episode()` binds the recorded task ID and objective list to the canonical
manifest, reward formula, simulator version, and scenario-pack hash; reconstructs
the objective score from replayed public observations; and invokes the regional
accounting verifier and exact simulator replay. Malformed action payloads are
redacted from public traces and represented by an invalid-action marker. The
score certifies completion of the declared synthetic task, not the operational
wisdom of its mission objectives or real-world fire effectiveness.

The task pack has six development fixtures and no held-out splits. Do not
use it to claim model generalization; build incident-grouped splits in a later
version before training or model selection.

The default decision interval is two model minutes. A configured interval must
leave time for one correction before offer expiry (at most four minutes with the
current eight-minute offer lifetime), fit objective deadlines, and divide them
exactly. Goal references to later reports are task assignments only; those report
locations and contents enter the public observation only when received.

Interface v2 and task pack v0.2.0 change the ops action/replay contract. Use commit
`fdc2a10` to replay interface-v1 traces. The regional v0.2 environment is unchanged.
