# Extended inputs containment seam v1

`envs.wildfire_extended_inputs_v1.adapter_v1` defines a separately versioned,
experimental **interface seam**, not an incident-day adapter or a supported
simulator. No existing incident-day, containment, weather, scoring, scenario,
map, or release code is changed. The included backend in the tests is synthetic.
Do not use this seam to claim an incident-day evaluation or operational result.

The core distribution owns a regular `envs` package, so its presence on
`PYTHONPATH` hides the repository's extended-input namespace. A process that
combines **either** public input package spelling with the top-level fire or
seam adapter must enable the bridge to avoid incompatible `Contract` types.
Add `envs/wildfire_extended_inputs_v1`, `envs`,
`envs/wildfire_response/core/src`, and `envs/wildfire_containment_v1/src` to
`PYTHONPATH`, then call the explicit bridge **before importing the input
package, the host package, or either adapter**:

```python
from adapter_bootstrap_v1 import enable
enable()

from wildfire_extended_inputs_v1 import load_inputs
from adapter_fire_v1 import ExpandedContainmentFireV1
# The legacy envs.wildfire_extended_inputs_v1 spelling is also shared.
```

The bridge extends the core package's search path and aliases the existing
adapter and contract modules within that process. When the optional fire
dependencies are already available, it also aliases all supported fire-adapter
import spellings to one module. Set those paths before calling `enable()`; if
they become available later, call `enable()` again before importing the fire
adapter. It does not change the core, fire behavior, or source pins. It refuses an already-loaded conflicting copy
instead of silently creating incompatible class identities; call it at process
startup. A distributable integration still needs a proper shared packaging
interface. For repository tests, the new pytest bootstrap applies the same
bridge before collection. For example, this runs both contracts in one process:

```sh
PYTHONPATH="$PWD/envs/wildfire_response/core/src:$PWD/envs/wildfire_containment_v1/src:$PWD" \
  PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_extended_inputs_adapter_v1.py \
  tests/test_extended_inputs_v1.py tests/test_extended_inputs_unified_imports_v1.py -q -p no:cacheprovider
```

## Contract

`CensoredContainmentSeamV1` validates the extended input document with explicit
opt-in and caller-supplied independent document and source pins. It constructs a
backend through the caller's factory and checks the backend's identity against
an independently supplied expected identity. Implementations must subclass
`ExtendedContainmentBackendV1` and follow the obligations below. A backend's
identity must pin its actual source, dependencies, configuration and scenario;
the seam only compares the supplied JSON identity and cannot establish its truth.
The factory gets the immutable validated contract, not a fabricated rectangular
incident-day pack. The driver must be the exclusive owner of all backend
mutations and must not share the backend with actors or mutate it out of band.

The factory must initialize an unignited, unadvanced state, without warm-up or
other state changes that introduce burning. The seam checks the initial fire is
empty, but cannot prove that the factory did not previously advance and hide it.
Use `change(command)` for ignition and each other non-spread state change;
`step(command)` performs exactly one spread step. Both perform a complete fire
check immediately before and immediately after the backend call, including the
last state, and discard backend results. Unknown or excluded cells are never
converted to fuel, flat height or barriers. Confirmed sea is supported but
nonburnable: a backend must exclude sea from ignition and spread, including
transient ignition during a step. The seam detects sea in each complete burning
observation, but cannot prove that a backend did not ignite and extinguish sea
within a call. Surface, fuel, elevation, access and domain classifications
remain as provided by the contract.

The censor's one-cell eight-neighbour condition applies only to an independently
reviewed engine whose entire spread influence, including terrain and containment
candidate evaluation, is within that stencil. A wider stencil, spotting, hidden
ignition or an incomplete burning observation invalidates this protocol. The
backend must handle sea and unknowns explicitly without reading nonexistent
land values. The seam cannot verify these behavioral obligations merely from the
class or identity. Routing, connectivity, access and operational safety are not
provided by this seam; `access` values are not route evidence.

The censor is sticky. On censor, subsequent `step`, `change`, and `outcomes`
raise `CensoredError`; backend result values are never returned by mutation
calls. `outcomes()` is available only while uncensored and must itself be a
read-only, actor-appropriate backend operation. A censor is not a completed
incident day. No public observations, reports, transcript, scoring, reward,
artifact exports or external backend handles are implemented here. A real host
must suppress or clearly mark all affected outputs and enforce that no other API
can continue the episode or leak censored outcomes. These APIs are a trusted
single-driver interface, not an adversarial sandbox.

## Private state and continuation

`checkpoint()` records a JSON snapshot, censor reasons, extended-input document
hash, backend identity, and hashes of this seam and its extended-input contract.
Snapshots are private: they can include the fire, containment, RNGs, future
events and other hidden state. The backend must snapshot and restore **all**
relevant state, including operations, communications, clocks, containment,
scenario, RNGs and pending events if present. Restore checks identities,
integrity and exact canonical JSON equality of the backend snapshot after
restoration, and verifies the restored burning state is consistent with the
sticky censor reasons. A failed restore or backend/check failure poisons the
driver; recover only by restoring a previously trusted checkpoint. A hash is
not authentication: obtain checkpoints and expected identities from a trusted
source. Exact state comparison cannot detect a state variable the backend omitted.

The synthetic tests restore a joint fire/containment/RNG checkpoint and replay
the same commands, comparing complete checkpoints after every step. That is
not the existing containment adapter's public transcript replay or a full-day
incident-day verification. A concrete backend needs independent replay tests for
all of its public and private lifecycle state, as well as parity and validation
against the intended engine.

## Why existing incident-day integration remains blocked

The existing `IncidentDayEnv` validates `incident-day-inputs-v2`, which demands
complete study and height coverage, and `DayFire` requires resolved fuel on
every cell. The extended contract requires null land values for confirmed sea
and allows unknown or excluded cells. Constructing the old rectangle would
therefore invent heights or fuel, or turn unknowns into firebreaks. Reset creates
and warms the old `DayFire` before the containment handover, so intercepting
only the ordinary containment tick would miss initial and warm-up checks.
Containment's minute lifecycle also assumes the fire stepped, while its restore
and fresh transcript verification hard-code the existing fire and adapter
classes. Its existing private checkpoint does not include this censor state.
Simply wrapping the published `step` or replacing the fire after reset cannot
satisfy those requirements.

A real integration requires a separately versioned host and fire implementation
that represent sea and unknowns without invented land inputs, owns construction,
ignition and each warm-up and minute step, enforces the complete censor and
output policy, and provides joint checkpoint/restore and fresh exact replay for
fire, containment and all host state. It also requires validated routing/access,
source classifications, scenario and source pins, the relevant active-case and
policy matrix, and release review. The current candidate map has no confirmed
whole-cell surface classification, so it cannot safely support a runnable fire
under this contract even after that engineering is complete.

Run the synthetic checks from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_extended_inputs_adapter_v1.py tests/test_extended_inputs_v1.py -q -p no:cacheprovider
```
