# Protection and data-readiness increment

## Objective

Extend the opt-in Spanish development bridge with versioned protection
instructions and explicit data-quality gates, without changing the frozen
regional benchmark, generated tasks or Spanish v1 replay contract.

## Work sequence

1. Preserve v1 default behaviour while adding protected extension hooks for
   task/core factories, trace versions, scoring and replay.
2. Add structured shelter, evacuate and release orders with recipient-specific
   sequence numbers and explicit supersession. Separate delivery, acknowledgement
   and execution. A later-issued instruction cannot cancel a message already
   in flight before its recipient hears the replacement.
3. Add synthetic smoke-dose/shelter effects. Shelter reduces a declared abstract
   dose rate but cannot prevent direct fire exposure or guarantee safety.
   Resource-bound in-person warnings can bypass a broadcast failure only when a
   crew is actually on scene; warning service takes time and occupies that crew.
4. Add an explicitly versioned safety gate: population failure yields zero scalar
   reward regardless of other completed objectives. Keep component diagnostics.
5. Validate declared offline weather/geographic metadata and source-anchor
   readiness separately. Missing context stays missing; no automatic weather
   reconstruction, unit conversion or road routing is claimed.
6. Exercise delays, acknowledgement-versus-execution, conflicting/stale orders,
   failed broadcasts, warning-team occupancy, unsafe shelter, invalid-action
   atomicity, public-observation privacy, full replay and v1 compatibility on CPU.
7. Record baseline results and remaining gates in the existing PR/issue. Preserve
   private anchors/evaluator artifacts locally, not in the published package.

## Not a completion claim

This increment does not establish measured building protection, smoke medicine,
population compliance, operational dispatch doctrine or a calibrated fire model.
All protection/communication constants remain declared exercise assumptions.
Domain review, actual weather/terrain acquisition and verified joins, resource
code interpretation, more incident groups, held-out splits, model/OpenEnv wiring
and publication clearance remain separate gates. No GPU, paid inference, live
messages, training run or publication is authorised by this implementation.
