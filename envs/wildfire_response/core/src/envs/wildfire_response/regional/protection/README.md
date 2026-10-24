# Protection instructions and data readiness

This opt-in development mode extends the Spanish source bridge. It does not
rewrite Spanish v1, the frozen regional benchmark, generated held-out cases or
their reward contracts. No learned model, live messaging or operational deployment
is included. See [the increment plan](PLAN.md).

## Instruction lifecycle

Population protection uses structured `protection_orders`, not legacy evacuation
messages. Each order names one public district and carries:

```python
{
    "recipient": "settlement-west",
    "action": "shelter",
    "channel": "broadcast",
    "resource_id": None,
    "supersedes": None,
}
```

The engine supplies the ID, recipient sequence and readable EXERCISE template.
Text does not earn reward and is not certified ASD-STE100 wording. At most four
orders are accepted per action, one per district, with atomic validation across
all resource, clarification and communication fields.

1. **Issue:** the new ID references the last issued ID for that district.
2. **Delivery:** broadcast takes two assumed minutes; variant v1 adds six to the
   first broadcast. Variant v2 fails the first broadcast per district. In-person
   service takes four. Future failures are not exposed in observations.
3. **Acknowledgement:** a simulated readback arrives one minute after successful
   delivery. It is not proof of human comprehension or population movement.
4. **Execution:** two minutes after successful delivery, the population attempts
   the action. `attempted_at` and `executed_at` differ if it cannot comply.
5. **Physical outcome:** position, abstract exposure and protection state evolve
   independently. `last_executed_protection_orders` is history, not proof of safety.

`superseded_by` records central intent; it does not unsend an in-flight message.
An old order can act before its replacement reaches the district. Once a newer
sequence is received, older pending executions are cancelled and late older
deliveries are ignored. The old physical state persists until a new action
executes. First supersession receipt time is retained, not overwritten by later
corrections. Failed broadcasts can be retried through a new linked ID. Other
same-action repeats are recorded as duplicate attempts, not transmitted anew,
and do not advance the issued sequence or change the latest issued ID. They
cannot bypass a slow queue and count against communication discipline.

`messages` may still inform populations or request base status. Information does
not move people. `release` ends shelter without teleportation; it cannot stop an
evacuation already in motion. A shelter instruction to a moving group reports
`unable_to_comply`, leaves movement in force and has no `executed_at` timestamp.

## Fallible physical effects and warning resources

Shelter keeps an at-home group stationary and lowers an **abstract nearby-fire
smoke proxy**. It is not a smoke-dispersion, medical or building model. The declared
exercise defaults are a two-cell radius, dose threshold 120, unsheltered rate
10/tick and sheltered rate 2/tick. Dose accumulates rather than resetting on an
instruction change. Reaching the threshold marks the group exposed. Direct fire
exposure is unchanged and can expose a sheltering group immediately. The legacy
internal `burnt` counter represents simulated exposure here, not observed deaths.

In-person orders require one existing, unoccupied crew actually on scene within
two grid cells of the district home and fresh clear public home-cell evidence.
The crew is occupied until its four-minute service completes; it cannot be
released, reassigned or simultaneously suppress fire during that service. This
is an assumed service area/time, not validated walking or road travel. It bypasses
the synthetic broadcast-link fault, not all possible real communication failures.
Crew/aviation casualty and route safety are not fully modelled or certified.

Tick order is protection delivery/readback/execution (delivery before execution
when times coincide), caller follow-ups, ordinary core delivery, resources, then
fire/population movement/smoke. Public logs separate intended orders from actual
actions and exposure. Ordinary fleet readbacks remain mandatory before dispatch.

## Safety-gated reward

Reward version: `wildfire-protection-safety-gate-v1`.

The existing four component goals remain: evidence triage, population safety,
incident resolution and communication discipline. Population safety additionally
requires no current synthetic smoke hazard. Unsafe population outcomes yield
**zero scalar reward even if other goals are complete**; otherwise reward is the
completed fraction. Component flags, exposure counts and truncation are reported
separately. Readbacks, sending messages, acknowledgement and tool volume alone
earn no credit. Supported protection is judged from public reports, sensors or
current synthetic smoke telemetry, never hidden historical truth.

This is an engineering safety gate, not calibrated real-world utility. The old
private weighted cost is retained for lifecycle/accounting checks only; its legacy
`unnecessary_evacuation` diagnostic also counts non-unwarned shelter states in
false-alarm exercises and must not be interpreted as a true evacuation rate here.
No reference policy is labelled optimal or an expert demonstration.

## Separate offline data gate

`context.py` validates small declared weather/geography records without fetching
data or putting them into physics/actor observations. It checks units, coordinate
frame/bounds, source references, aware observation/availability times, duplicates,
finite values and size limits. Credential-bearing URLs are rejected. Future or
outside-coverage rows are excluded from an as-of assessment.

Missing input stays missing. Source hashes, `verified` CRS and availability times
are declarations, not independently authenticated facts: the tool does not verify
raw cached bytes, row lineage, station-to-incident distance or freshness. A
`readiness_for_join` result only identifies metadata-eligible candidate rows;
`historical_replay_ready` and `actor_input_eligible` remain false. The four current
anchors omit geography/weather and remain not ready. No random weather is passed
off as observation, no source time zone is guessed, and no engine wind conversion
is silently applied. Use the module docstring for the exact schema.

## CPU commands and Python API

Use the existing private anchor pack, with a fresh output directory:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.protection.context \
  --pack .runtime/spanish-development/private/anchors.json \
  --output .runtime/protection-source-quality.json
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.regional.protection.run \
  --pack .runtime/spanish-development/private/anchors.json \
  --output .runtime/protection-smoke --smoke
```

Omit `--smoke` for twelve development cases and passive, evacuation-first and
shelter-first baselines. The source groups and seeds match across policies; the
new protection physics/reward means scores must not be compared directly with
Spanish v1. The runner saves public traces separately from private accounting and
source-readiness artifacts. An external declared context can be assessed with
`context --context FILE --incident-time AWARE_ISO`; it is still not engine input.

```python
from wildfire_core.protection import ProtectionDevelopmentEnv, ProtectionBaseline

env = ProtectionDevelopmentEnv(".runtime/spanish-development/private/anchors.json")
observation, info = env.reset("protect-dev-002-v1")
policy = ProtectionBaseline(prefer_shelter=True)
while True:
    observation, reward, terminated, truncated, info = env.step(policy(observation))
    if terminated or truncated:
        break
result = env.verify_episode()
```

`issue_protection_orders`, existing resource/clarification tools and read-only
checks remain available. Exact replay uses `verify_protection_trajectory(trace,
trusted_pack)` or the rollout CLI's `--replay` option. A replay pass proves trace
consistency, not task success or operational realism. Spanish v1 replay remains
separate and supported. The twelve cases are development fixtures, not a held-out
test set, and no real data pack is bundled or publicly published.

Actual weather/terrain acquisition and verified joins, official resource-code
interpretation, richer route/compliance models, domain calibration, larger
incident-grouped held-out splits, training/server adapters and publication rights
remain later gates. No GPU, paid inference or real emergency communication is used.
