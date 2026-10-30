# Experimental containment agent tools v1

`wildfire-containment-agent-tools-v1` is a small provider-independent tool
adapter for an already-reset `ContainmentIncidentDayEnv`. It follows the
function-definition and `execute(name, arguments)` conventions of
`IncidentDayTools`; it does not invoke a model or require an API key. The host
constructs and resets the episode with approved inputs and controls its lifecycle.
The tool set does not offer reset or accept a seed. Install the core,
`envs/wildfire_containment_v1`, and `envs/wildfire_containment_agent_v1`, or add
their `src` directories to `PYTHONPATH`.

```python
from wildfire_containment_agent_v1 import ContainmentAgentTools, VERSION, tools

# env is a ContainmentIncidentDayEnv already reset by the trusted host.
agent = ContainmentAgentTools(env)
definitions = tools()  # fresh function definitions with JSON schemas
situation = agent.execute('get_situation', {})
routes = agent.execute('route_options', {'resource_id': 'engine-1'})
# Choose a returned route ID, then submit a resource request.
result = agent.execute('request_resource', {
    'resource_id': 'engine-1', 'sector_id': 'incident',
    'approach_id': routes['options'][0]['id'],
})
```

The host should choose a resource and route actually present in the public
situation and options. Wait with `advance_time` and inspect delivered offers in
`get_situation`. Use `propose_hold` with a live ground offer ID, select a
returned proposal ID and call `authorize_hold` with both IDs. Proposals describe
public approach geometry and two possible protected endpoints; they do not
assert that a line is safe or effective. Inspect `get_reports` for delivered
field reports, which can be delayed and are not continuous line-state feeds.

The tools are `get_situation`, `route_options`, `evacuation_options`,
`request_resource`, `propose_hold`, `authorize_hold`, `check_action`,
`submit_action`, `request_status`, `advance_time`, and `get_reports`. `check_action` and
`submit_action` advertise the same incident-day action schema: requests,
ordinary authorizations, releases, protection orders, status requests and
bounded waits. A wait cannot be combined with other fields. A valid check is
not a commit. `submit_action` delegates directly to `step`; ordinary
authorizations dispatch the resource to its usual suppression mission, while
`authorize_hold` binds a public proposal to a hold. `request_resource` is a
convenience that translates its three fields to a single request in `step`.
Invalid committed orders still consume a minute. A wait takes 1–60 minutes and
may stop earlier for an actionable delivery or operational deadline. State-changing calls return
`observation`, `reward`, `terminated`, `truncated`, and `info`; reads return the
public method's result. The schemas describe valid tool inputs; dispatch checks
only the tool name and top-level argument names, leaving value and action
validation to the episode. Dispatch errors raise exceptions; public-method
exceptions propagate unchanged, including cases where the underlying episode
may require a trusted restore or reset.
`request_status` directly calls the public method with a centre ID and an
optional subject (`situation` by default or `resources`); it commits one minute.

For population protection, use public group and destination IDs with
`evacuation_options`, then submit an evacuation order using a returned approach
ID. Shelter orders use null destination and approach IDs. For example:

```python
options = agent.execute('evacuation_options', {
    'group_id': 'population-0', 'destination_id': 'destination-0',
})
action = {'protection_orders': [{
    'group_id': 'population-0', 'action': 'evacuate',
    'destination_id': 'destination-0', 'approach_id': options['options'][0]['id'],
}, {
    'group_id': 'population-1', 'action': 'shelter',
    'destination_id': None, 'approach_id': None,
}]}
if agent.execute('check_action', {'action': action})['valid']:
    result = agent.execute('submit_action', {'action': action})
```

Choose IDs from the current public situation and available route options; the
example assumes those groups and a route exist. Orders and requests are subject
to normal delays and execution conditions, even when a draft check succeeds.

Each dispatched operation calls exactly one outermost public episode method,
so the episode's own transcript retains its original arguments, results,
errors, and timing. Schema/name failures occur before the episode is called and
do not appear in that transcript. The host may handle the episode transcript
through the existing trusted API; it is not an agent tool. The allowlist does
not expose hidden line strength, checkpoints,
private verification, seed, or future events. The Python object is an interface
for a trusted host, not a sandbox against arbitrary Python code; only publish
its definitions and dispatcher to an agent, never the underlying environment.

The trusted host can separately call `agent.host_log()` and
`agent.host_log_sha256()` to save a `wildfire-containment-agent-call-log-v1`
record and its digest. In order, this records each tool name, finite JSON
arguments, public result or actor-visible error, including rejected names and
top-level arguments. Inputs that are non-JSON, non-finite, or exceed 64 KiB are
rejected before dispatch and represented by an `input_redacted` marker and a
fixed error, without serializing or printing the invalid value. The log records
only calls through this adapter; it does not include episode reset or direct
calls by the host. It contains actor-supplied data and public responses, so the
host should protect it accordingly. Returned logs are detached copies.

The log is capped at 256 calls and 8 MiB. An attempt beyond its call or reserved
byte capacity is recorded as a terminal capacity error before episode access,
and closes the adapter. Later attempts are refused and cannot be added to that
closed log. If a public result or error unexpectedly cannot be recorded within
the byte bound, or a call is interrupted, the log becomes invalid and cannot be
exported or replayed. The episode may already have changed; the trusted host
must discard it or use its supported restore/reset workflow. The adapter is for
a single sequential host, not concurrent calls.

There is one Python boundary the adapter cannot detect itself: a debugger or
trace hook can raise while receiving the function's `return` event, after the
method's own exception handling has finished. The caller then sees an
interruption even though the log contains a completed call and may still be
exportable. After any interruption observed by the host, discard the tool
adapter and recover the episode through the trusted restore/reset workflow,
even if the log still appears valid. Do not replay or publish that log as a
complete record of what the caller observed.

For reproducibility, create a fresh episode using independently trusted inputs,
configuration, scenario and reset, then create a fresh tool adapter and call
`replay_host_log(saved_log, expected_sha256=trusted_digest)`. It checks the
canonical JSON digest and compares each replayed public result or error,
including redacted input rejections and a terminal capacity rejection. Discard
the replay adapter on a mismatch. The digest must come from a trusted source;
it detects changes, not malicious replacement or authentication. Host-log
replay does not replace the episode's independent transcript and private-pin
verification, nor does it by itself establish hidden-state equivalence.

This remains experimental. Expanded map coverage, weather coupling and
integration/acceptance gates have not passed; this tool adapter establishes no
operational calibration or benchmark acceptance. See
[containment-v1.md](containment-v1.md) for the episode's public and private
contracts, unsupported inputs, and lifecycle constraints.
