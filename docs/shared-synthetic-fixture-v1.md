# Shared synthetic fixture incident v1

`wildfire_source_joint_v1.fixture_host.SharedSyntheticIncidentV1` is an opt-in,
unscored synthetic exercise host. It accepts only a separately labeled
`synthetic_fixture_only` fixture and fire draft. It is not a source-case host,
12-case run, historical reconstruction or score. The caller independently pins
the complete fixture, the synthetic directed road graph, and the synthetic
work-site declarations. A road edge does not imply a work site, a walking
surface or access to its whole cell.

One owner advances a single `WeatherMixedFireDiagnosticV1` fire and weather
clock, one incident-day `Operations`, and one `EventBus`. There is no advancing
of the older incident, hold, aircraft, population or event host slices. The
host uses the existing mixed hold-fire wrapper and Operations resource and
population lifecycles. At each minute it applies declared fixture events,
handles an optional command, ticks Operations once under current weather and
hazards, validates and applies aircraft drops and staged ground hold work,
advances weather/fire once, checks costs and censor, applies population
exposure, then queues and delivers reports. An exception after mutation
poisons the host until a trusted checkpoint is restored.

Synthetic roads require exact unique graph-node endpoints, explicitly permitted
full directed edges, supported accessible fixture cells and a current fire and
restriction check. Walking, partial-edge replanning and invented connectors
are disabled; an unavailable return produces `needs_assistance`. Work sites
require separate declarations, supported cells and the applicable resource
kind. Setup safety is revalidated, and an unsafe unit at its base is moved to
`needs_assistance`. Aircraft use the existing exercise clearance, flight,
drop, return, fuel and refill lifecycle with the weather and solar-day proxy;
this is not a flight-safety model. Population orders use Operations delivery,
acknowledgment, capacity and travel; inherited exposure can be independently
unknown to the actor. Observations are delivered through EventBus rather than
exposing the live physical state.

The separate fixture timeline supports `center_unavailable`,
`aircraft_unavailable`, `destination_closed`, and cell-scoped
`access_restriction`; weather and wind changes remain in the pinned fire draft.
Incoming fixture reports are separately pinned and delayed. Unsupported event
kinds, source-labeled fixtures and source drafts fail closed. Handover weather
must be received at minute zero, and every weather report delay is validated
against EventBus's 60-minute limit before the fire is constructed. Public wind
is populated from delivered reports. Overlapping center and aircraft outages
retain the latest expiry. The actor methods
are `dispatch_hold`, `clear_aircraft`, `dispatch_aircraft`, `protect`,
`release`, `request_status`, `advance`, `situation`, and `reports`. Commands
consume one minute; aircraft targets are limited to delivered handover fire
cells. Dispatch/protection acceptance is reported asynchronously and route
geometry remains private. There is no score or outcome method.

Checkpoints and transcripts contain private physical state, source-like
coordinates and undelivered events. `restore` requires an independently trusted
digest of the entire checkpoint; `replay` additionally requires trusted
transcript, identity and final-checkpoint digests. These hashes are integrity
checks, not authentication of an untrusted Python process. A caller must keep
the pinned inputs and implementation trusted and must not expose private
checkpoints, fixture data or road plans to actors.

The focused synthetic regression exercises concurrent ground work, aircraft
drops, evacuation, weather, incoming reports and events; finite water, duty and
fuel; no movement before order delivery; dynamic restriction and setup
hazards; safe mid-edge return failure; hidden inherited exposure; censoring;
poisoning and recovery; checkpoint continuation; and replay. It uses a
synthetic graph and synthetic mixed-fire fixture, without overriding the
production source-pair verifier. It does not establish source access, evaluate
all 12 source cases, verify a fixed scorer, or perform the active-case/policy
matrix. Source integration still needs a separately reviewed versioned route
and work-site policy and an independently verified scorer.
