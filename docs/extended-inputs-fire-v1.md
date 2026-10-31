# Expanded-grid containment fire backend v1

`adapter_fire_v1.ExpandedContainmentFireV1` is a separately versioned,
synthetic-first backend for the existing `ContainmentEdgeFireStep`. It does not
construct an incident day, manage resources or routes, or validate real inputs.
It is not a benchmark, live emergency service, or physically calibrated model.
The previous interface design remains in [extended-inputs-adapter-v1.md](extended-inputs-adapter-v1.md).

Add `envs/wildfire_extended_inputs_v1`, `envs/wildfire_response/core/src`, and
`envs/wildfire_containment_v1/src` to `PYTHONPATH`. Enable the bridge described
in [extended-inputs-adapter-v1.md](extended-inputs-adapter-v1.md) **before**
importing a public input package, the host, or `adapter_v1`/`adapter_fire_v1`.
In particular, a `Contract` from the top-level `wildfire_extended_inputs_v1`
package also requires this bridge; importing that package and the fire adapter
independently produces different class identities.

## Interface and ownership

The backend implements `ExtendedContainmentBackendV1`. Construct it with a
validated extended `Contract`, integer `seed`, explicit `containment_config`,
positive `pressure_scale`, and a two-component `wind` in [-3, 3]. It starts
unignited and at tick zero. Grid spacing must be 500 metres: the pinned
upstream slope function hard-codes that distance. The caller must independently
establish that the CRS and transform use metres and that the declared terrain
and sources suit the model; a CRS string does not establish this.

`change({'ignite': [[column, row], ...]})` checks the proposed whole burning
state before writing any ignition and checks again afterward. `step()` or
`step({})` advances exactly one fire and containment tick and checks immediately
before and after. Both return `True` on a safe state and `False` when their
post-check or proposed-ignition check has censored the episode. Read `censored`
and `reasons`; once censored, further mutations and `outcomes()` raise
`CensoredError`. Invalid input or any unexpected backend exception poisons the
instance: restore a previously trusted snapshot rather than retrying. The
returned `outcomes()` is only a private tick and burning-cell listing, not an
incident-day score or actor observation.

For the outer guard, use `ExpandedContainmentSeamV1` with a factory returning
this backend and an **independently trusted** `expected_backend_identity`.
It propagates backend censor reasons, including rejected ignition proposals,
to the outer censor. Its `change` and `step` raise on the same call that
censors; its `checkpoint` and `restore` include both censor states and backend
state. Construct fresh from independently pinned extended inputs and backend
identity before restoring. As with the earlier seam, the caller must exclusively
own the backend and route all fire-changing actions through these methods. The
underlying fire object and snapshots are trusted-driver private state.

`step` currently rejects assignments and suppression. The reused containment
kernel advances with no assignments and retains its fire-driven crossing and
line state. A host must not enable assignments until it owns and atomically
checks dispatch, eligibility, water and duty accounting, and recovery from
partial host failures. The interface does not produce containment reports,
public transcripts, observations, or score exports. Those require a separate
versioned host and output suppression policy.

## Sentinel safety and kernel limits

For supported land, the backend maps source fuel classes using the pinned
incident-day `FUEL_MAPPING` and uses the exact supplied elevation. It makes no
inference from access to fuel or vice versa. The mapping and fire rules are
exercise model assumptions, not measured vegetation or calibration. Confirmed
sea and unsupported cells retain null terrain height internally. To use the
unchanged rectangular spread loop, each has a private zero-fuel, zero-heat cell
sentinel, with no terrain. Unsupported and sea sentinels are never included in
snapshots or outcomes; snapshots serialize supported land state only.

The following conditions make the unsupported sentinel inert for the pinned
step: the censor checks all burning cells and all eight neighbours before each
step; hence no unsupported cell can be a target or diagonal corner of a
burning cell in a step that runs. The loop's global scan reads its zero heat
and updates zero wetness but does not consult its fuel, terrain or height.
The backend verifies sentinel invariants at both boundaries. A confirmed sea
target has zero fuel and is skipped before height or terrain lookup; it cannot
ignite. The original kernel treats nonburnable diagonal corner cells as blocking
a diagonal, including confirmed sea. No unknown cell is treated as such a
firebreak in a step: proximity censors first. Synthetic tests put throwing
sentinels on unsupported fuel and sea elevation to exercise those properties.

The proof applies only to the pinned kernel's one-cell eight-neighbour
stencil, including its diagonal corners and containment attack paths. It does
not apply to spotting, hidden ignitions, longer-range physics, extra terrain
reads or another kernel version. The backend checks the existing incident-day
core source pin, containment package source hash, and key runtime hooks; its
identity includes these, this source, input hash, seed, configuration and wind.
The code assumes a trusted, single driver; source hashes are not authentication
or a defense against arbitrary process monkey-patching.

## Checkpoint and replay

`snapshot()` contains supported-land fire cells, fire RNG, tick, containment
checkpoint, satellite state, boundary marker, censor reasons and backend
identity. It contains no incident-day host state. `restore()` rebuilds sentinels
from the immutable contract, validates the identity, clocks, land coordinates
and cell state, restores the RNG and containment state, and verifies censor
consistency. On restore failure the backend is poisoned. The outer seam adds an
integrity digest and verifies canonical equality of the restored snapshot.
Neither digest authenticates an untrusted checkpoint. A caller must protect
checkpoints and independently trust their identities and integrity.

Synthetic tests demonstrate real kernel spread, parity of cells, RNG and
containment state with the existing containment fire through a spread event on
an all-land fixture, exact continuation after a JSON checkpoint, sea
nonburnability, unknown and boundary censoring, sentinel non-reads, seam
checkpointing and fail-closed errors. They do not demonstrate
incident-day warm-up, host event/operation clocks, routing, resource accounting,
public transcript replay, full-day acceptance or real-map readiness. The
existing candidate's unclassified surfaces still cannot support a runnable fire.

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_extended_inputs_fire_v1.py tests/test_extended_inputs_adapter_v1.py tests/test_extended_inputs_v1.py -q -p no:cacheprovider
```
