# Wildfire Ops Control Center interface v1

This interface represents one learned operations desk in a simulated wildfire
control center. The ops operator reviews public information, requests and
authorizes resources, sends structured messages, and follows execution. It is
not the incident commander and does not replace base or field-unit behavior.

The facade wraps the existing `RegionalResponseEnv`. It does not copy its fire
engine, change the regional v0.2 task pack, or revise the frozen
`regional-outcome-cost-v2` reward. This slice changes the model-facing role and
tool interface only.

## Tools

- `get_situation()` returns a deep-copied public observation.
- `get_reports(report_ids=None)` returns received reports only.
- `check_orders(action)` checks the existing action contract without advancing
  the clock; the core allows at most three checks between committed actions.
- `request_resources(requests)` sends resource requests to their owning bases.
- `authorize_resources(offer_ids)` authorizes only currently valid offers.
- `release_resources(resource_ids)` releases assigned resources.
- `send_messages(messages)` sends the existing structured recipient/purpose
  messages.
- `advance()` advances the existing simulator with no new orders.
- `submit_action(action)` remains available for a caller that needs to combine
  compatible order fields in one core decision.

Every committed tool action and `advance()` makes exactly one call to
`RegionalResponseEnv.step`. The existing action clock, delayed deliveries,
validation, atomicity, transition limits and rewards therefore remain the
source of simulator behavior. Read-only lookups and checks do not advance time.

## Authority and evidence

The adapter uses `ops` as the public sender/recipient label for the learned
control-center role. Base and field-unit identities remain the simulated
counterparts from the underlying environment. Reports remain untrusted claims;
the facade does not add or expose private report annotations, future events,
scenario truth, or evaluator checkpoints.

The current core has delivery acknowledgements and delayed base offers, but it
does not implement an explicit readback/repeat-back and correction protocol.
Delivery acknowledgement is not evidence of understanding or execution.

Resource availability, transit times, wind gates, population behavior and
fire effects retain their existing synthetic/assumed labels. Nothing in this
interface certifies real dispatch authority, response doctrine, flight safety,
or emergency-message wording.

## Scoring boundary

The facade returns the existing `regional-outcome-cost-v2` step reward. It does
not treat a check, acknowledgement, message, or action count as success. A
separately versioned coordination-objective reward requires a declared task
objective schema, event-backed completion evidence, and an independent scorer;
that work is outside this interface-only slice.
