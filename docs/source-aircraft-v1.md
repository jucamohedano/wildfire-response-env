# Source-catalog synthetic aircraft slice v1

`wildfire_source_air_v1.SourceAircraftV1` is an opt-in aircraft-only exercise
over `SourceCaseCatalogV1` and `WeatherMixedFireDiagnosticV1`. It selects the
unchanged aircraft roster, facilities, stocks, duty/fuel budgets, start time and
profile from a pinned synthetic source case. It is not a historical replay,
aviation clearance service, calibrated fire model, full control-center host or
score. Population, evacuation and ground operations are unavailable; no group,
destination or incident is snapped to a road node.

The caller provides a validated v4 mixed input, a verified source-case catalog,
the independently pinned original parent inputs and a selected post-anchor case
ID. The host validates those identities and uses the original sector's pinned
longitude/latitude and UTC start with the existing offline `solar_daylight`
proxy. The source case's aircraft base, fuel and duty are preserved; the
expanded fire draft retains its separately versioned grid, seed and initial
weather assumptions. These are synthetic exercise inputs, not observed
historical resources or measured fire behavior.

The public aircraft interface is `situation`, `reports`, `route_options`,
`clear_aircraft`, `dispatch`, `release`, `request_status` and `advance`.
The handover reports the first half of the current burning cells in sorted
order, matching the existing core's synthetic partial-observation rule; the
private full ignition list is not published. Routes are straight-line synthetic
flights to one of those reported handover cells, with the pinned exercise speed; they are not claims of
legal airspace or operational flight safety. Orders consume one minute. The
existing `Operations` engine governs clearance, mobilization, flight, daylight
and wind windows, fuel, setup, duty, return, reload queue and finite facility
water. Aircraft return is the existing abstract direct recovery-to-base
assumption. A zero-leg plan is used only when an aircraft is already at its
base, never to move it. A release starts at the preceding decision boundary:
the first fuel-charged tick advances the recovery position, including a
one-minute return with exactly one minute of fuel.

At work, the existing aircraft drop proxy reduces the heat of one currently
burning supported cell within three cells of the requested target by 0.15 and
sets wetness to at least five ticks. The selected drop and Operations' water and
duty charges are staged and reconciled before the physical weather/fire step.
Unknown or non-land sentinels are never mutated. The complete fire is checked
before the operations tick, after a drop, and by the mixed-fire backend before
and after spread. An exception after mutation poisons the host until a prior
trusted checkpoint is restored. A post-step censor retains the matching private
state but suppresses reports and outcomes. `drop_completed`, resource and
weather observations are delivered through `EventBus`; the true fire, RNG,
future schedule, dose/proxy state and drop audit are not actor-facing.

The host implements synthetic weather changes, center outages, incoming reports
and the scenario's aircraft-unavailable events. It uses the same EventBus and
scenario event conventions as the separate event timeline, but does not alter
or bypass the committed `EventTimelineV1` aircraft stop. Access restrictions,
destination closures and any other non-aircraft event remain unsupported here:
the host stops explicitly before their event minute instead of silently
ignoring them. This is a partial aircraft lifecycle, not a completed source
case. The independent road-only component and its unknown-access constraints
are untouched.

Private checkpoints include source/implementation identities, weather/fire,
Operations, communications, delivered knowledge, event state, drop audit and
public action transcript. The identity pins the loaded Operations, EventBus,
mobility and solar-clock implementations and checks their runtime bindings. `restore(checkpoint, expected_checkpoint_sha256=...)`
requires a separately trusted digest of the **entire** checkpoint, including its
embedded integrity digest. It must not be taken from that same untrusted file.
`replay` requires independently trusted transcript, identity and final
checkpoint hashes and compares action results and the joint final state. Hashes
are not authentication when an attacker can choose the trusted pin; these APIs
and Python objects are for a trusted driver only.

`tests/test_source_aircraft_v1.py` uses the real pinned synthetic roster and
source-case lineage with a test-only synthetic all-land fire input; it does not
validate the actual v4 surface or produce a full-day score. It exercises a
clearance refusal, a real Operations sortie/drop/return/reload and water/fuel/
duty accounting, delayed reports, night denial, checkpoint continuation and
replay, and controlled fire censoring. The same test covers the source case's scheduled strong-wind abort, aircraft-unavailable event, low-fuel refusal,
and explicit stop at an unsupported ground restriction.
The full active-case/policy matrix and twelve-source-scenario qualification
remain outstanding.
