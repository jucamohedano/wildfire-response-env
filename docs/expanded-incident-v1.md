# Expanded incident host v1

`wildfire_expanded_incident_v1.ExpandedIncidentV1` is a separate, synthetic-only
incident host. It combines the expanded containment fire backend with the
existing incident-day `Operations`, `EventBus`, `HazardRouter`, and pinned road
graph. It is a narrow exercise host, not an implementation of the full
incident-day API, an approved map, a benchmark, a historical replay, or a live
emergency service. It does not alter frozen core, weather, scoring, map files,
release pins or existing scenarios.

The separately opted-in, private source-grounded **passive fire diagnostic**
is described in [expanded-incident-source-v1.md](expanded-incident-source-v1.md).
It does not turn the synthetic host into a full source-map incident-day runner.

Add `envs`, `envs/wildfire_extended_inputs_v1`,
`envs/wildfire_response/core/src`, and `envs/wildfire_containment_v1/src` to
`PYTHONPATH`. Enable the explicit bridge in
[extended-inputs-adapter-v1.md](extended-inputs-adapter-v1.md) **before**
importing the host, either public input-package spelling, or either adapter.
This is necessary for `wildfire_extended_inputs_v1` as well as the legacy
`envs.wildfire_extended_inputs_v1` spelling. Then import
`wildfire_expanded_incident_v1`. The bridge keeps contract and adapter types
under one module identity without altering the core's regular `envs` package.

## Identity and setup

The caller supplies independently trusted extended-input document and source
pins, road-network hash, and catalog hash, plus a case ID and explicit
containment configuration and pressure scale. The source artifacts, their
rights and suitability must be verified independently. Hashes detect changes;
they are not signatures. The catalog is a caller-supplied JSON object with
exactly:

```text
version: wildfire-expanded-incident-catalog-v1
role: synthetic_fixture_only
inputs_sha256, network_sha256: independently pinned hashes
cases: [{id, seed, initial_fire, wind, resources, center}, ...]
```

Each resource has exactly `id`, `kind` (`crew` or `engine`), projected metre
`position`, positive integer `work_remaining`, and nonnegative integer
`water_litres` (zero for crews, positive for engines). Each starts available;
its home/base position is its supplied position. The catalog's synthetic role is
an assertion by the trusted caller, not proof of the origin of any input. It
must not be used to relabel source data or estimated fuel as measurements.
The host's private checkpoint separately pins the host source and version,
extended input, network, catalog, scenario hash and case ID, and backend identity
including its source, seed and containment configuration.

The initial fire is passed unchanged to the backend; its complete eight-neighbour
censor runs before ignition and before any minute or warm-up. This host does not
perform warm-up or silently relocate ignitions. If the initial proposal is
unsupported or at the rectangular boundary, the episode starts censored at
minute zero, without an ignition or operations tick. The user's strict-evidence
checks identify remapped drafts with unknown-surface neighbours. Those drafts
must be rejected or censored at initialization unless independently supported evidence resolves the relevant
cells. Synthetic tests exercise unknown-neighbour guards without copying private
map data or claiming to validate the original drafts. The current drafts are
unqualified and are **not** this host's catalog; no draft-to-host conversion,
estimated-fuel contract, or real-map approval is implemented. Passing an initial
check alone does not establish future support or route safety.

## Actor interface

Construction starts an episode. `situation()` returns the minute, status,
initial reported fire cells, last delivered resource views and report cursor.
The initial ignition cells are explicitly disclosed as a synthetic handover;
no further fire observations are generated, so they are stale after minute zero.
`reports()` returns only delivered `EventBus` messages. The host does not expose
true fire, fire RNG, future messages, private containment, private catalog,
checkpoint or audit state through these actor calls. Store checkpoints and
catalogs privately. This is a trusted single-driver interface, not a sandbox.

`route_options(resource_id, target_grid)` uses the last delivered resource
position and the initial handover fire, so an option is a **stale-knowledge
plan**, not a guarantee of safety. `dispatch(resource_id, target_grid,
option_id)` accepts only a currently returned public option and consumes one
minute; the existing operations engine rechecks the complete route and current
private fire hazards before accepting it, then checks every movement segment.
A delayed dispatch result and resource status use the existing communications
bus. `request_status()` queues a delayed request and consumes one minute;
center replies use only delivered resource views and disclose their oldest
observation time. `advance(1..60)` advances one-minute ticks up to 1,440.
Reading situation, reports, or routes does not advance time or consume RNG.
Unlike the full day API, this version does not stop waits early for actionable
reports or deadlines.

All surface cells outside the declared domain, all non-land cells, and every
cell whose `access` is blocked or unknown are explicitly excluded for **both**
crew and engine planning and movement. The router also checks known elevation
and fuel for walking. Only road edges with an explicit `allowed_modes` entry
can be used; a missing mode declaration is not treated as affirmative access.
When unknown coverage exists and no option is found, the host reports
`unmapped` instead of treating that missing evidence as proof of a blockage.
Road geometry and source hashes do not establish legal or physical access,
network completeness, safe travel or tactical suitability. Aircraft,
evacuation, population effects, route returns, hold assignments and suppression
are not implemented. Once an assigned ground unit reaches work, the existing
operations callback reports that work is unavailable; no suppression, duty or
water use is fabricated. There is no outcome or reward model: `outcomes()`
raises, including at completion. Completion means only the synthetic clock
reached 1,440 without censoring.

## Censor, failure and replay

Before any operations or minute changes, the host checks the complete private
burning state. The backend checks again immediately before and after its single
physical fire step, and on the initial ignition. If it censors, the host stops;
`situation()` returns only a censored status and minute, and reports, routes,
orders and further advancement are suppressed. The already-started operations
minute may have changed private state before a post-step censor; it cannot be
scored as a completed day. Unexpected failures poison the host; restore a
previously trusted checkpoint instead of retrying a partial minute.

`checkpoint()` and `restore()` are trusted-driver APIs. They include fire RNG,
fire/containment clocks and state, operations, queued/delivered communications,
known resources, censor state and identities. Restore checks the integrity
digest, all identities and clocks, and exact canonical checkpoint reproduction.
A digest does not authenticate the checkpoint. Synthetic tests compare replay
from a midpoint checkpoint through the end of a 1,440-minute episode, including
communications and operations, and separately exercise ground movement and
censoring. They do not establish full incident-day active-case/policy acceptance,
public-transcript replay, reward verification or physical calibration.

Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_expanded_incident_v1.py -q -p no:cacheprovider
```
