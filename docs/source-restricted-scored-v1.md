# Restricted source scoring v1

`wildfire_source_restricted_scored_v1.RestrictedSourceScoredV1` wraps the exact
restricted host. This is a **no-ground, no-evacuation synthetic exercise**,
not the complete incident environment or a calibrated benchmark. Ground
requests fail; shelter and aircraft remain available. The public situation
contains only the independently approved public handover population roster, with
observation time zero and exposure unknown. Each row must have exactly `id`,
`count`, `position`, `status`, `exposed: null` and `observed_at: 0`; its public
fields must match the pinned scenario. The caller explicitly supplies and pins
this disclosure; it is never assembled from live Operations state. It remains a stale handover observation: order acknowledgements and
other later reports come from the delayed EventBus. `known_fire` likewise means
only the handover observation (`fire_observed_at` is zero). A delivered incoming
report with explicit category `fire` and a valid expanded-grid location can
also be targeted by aircraft; a generic report is never promoted to a fire
observation. Wind is unknown until delivered. The current weather input contract
additionally requires the first observation to arrive at minute zero.

A trusted evaluator independently approves a fixed row-major mask and expected
identity **before** a run. The identity binds the host, private handover
checkpoint, mask, effective mixed factors, terrain profiles, static fire and
Operations configuration, scoring implementation and reward. A freshly generated
candidate from the episode to be scored is not independent approval. The mask
must contain only supported, initially fuel-bearing cells. Inherited burning and
inherited exposure are excluded from the respective denominators. At 1,440
uncensored minutes, the private outcome is 80% the fraction of initially
unexposed people not newly exposed and 20% the eligible mask fraction not newly
burned. Empty denominators contribute one; exposure is a synthetic proxy.
The restricted reward identity is `wildfire-restricted-no-ground-no-evac-outcomes-v1`.
The verifier returns `restricted_full_day` with scope `restricted_no_ground_no_evac`,
never the unrestricted `full_day` status. The verifier reconstructs a fresh host, replays every command, compares the
complete private checkpoint and independently recomputes the minute audit.
Censored, unfinished, timed-out, identity-mismatched and replay-failed cases do
not produce a score. The wrapper, factory, pins, masks, checkpoints and audit
must remain evaluator-private. The public facade is not a Python sandbox.

## Twelve-case integration

There is no stand-alone CLI that can discover or approve private inputs. A trusted
caller must prepare and independently pin all of these before invoking the
orchestrator:

1. Construct `MixedInputsV2` with
   `wildfire_expanded_incident_v1.exercise_v4.prepare_exercise_v4`: supply the
   strict and water-v2 resolution documents, v3 and v4 overlays, parent input
   and original 12-case catalog, their independently approved hashes, the pinned
   compressed road candidate and its hash, the raw coast, water and WorldCover
   sources, OSM manifest and samples, DERA rivers source, and explicit
   `scenario` (`low`, `nominal`, or `high`) and `crossing_proxy` in `(0, 1)`.
   The converter independently reconstructs the overlays and enforces the
   repository's immutable source-validation gate. It retains unknown access.
2. Construct `SourceCaseCatalogV1(inputs, original_catalog, parent_inputs,
   anchored_catalog, fire_drafts, graph, source, *, expected_original_sha256,
   expected_parent_sha256, expected_anchored_sha256,
   expected_fire_drafts_sha256, expected_graph_sha256,
   expected_source_sha256, opt_in=True)`. The anchored catalog, graph and full
   source must satisfy its reviewed-pair verifier, and the original snapshot
   required by that selector must be present at its pinned path. The selector
   reconstructs the drafts and checks lineage; do not bypass it with test-only
   `_VERIFIED` inputs.
3. Provide a separately reviewed fixed mask, explicitly approved public handover population and `expected_identity` for each
   case, an approved containment configuration and pressure scale, and a pinned
   mapping between twelve public, non-sensitive IDs and the private selector
   case IDs. Independently verify the expected identity against a fresh
   handover and approve it outside the run being graded. A host can reject an
   invalid source roster, census handover, or unsupported input; fire can censor
   during warm-up or the day. These are not scores.
4. In a trusted Python driver, with `envs`, `envs/wildfire_extended_inputs_v1`,
   `envs/wildfire_response/core/src`, `envs/wildfire_containment_v1/src`,
   `envs/wildfire_containment_score_v1/src`, and the repository root on
   `PYTHONPATH`, connect the prevalidated objects as follows:

```python
from eval.qualify_source_restricted_day_v1 import qualify
from wildfire_source_restricted_v1 import RestrictedSourceIncidentV1
from wildfire_source_restricted_scored_v1 import RestrictedSourceScoredV1, verify_outcome

# release is trusted private caller-supplied data: the validated inputs, selector,
# parent, public_to_private mapping, independently approved identities and masks,
# containment settings, and a reproducible policy factory.
def fresh(public_id):
    return RestrictedSourceIncidentV1(
        release.inputs, release.selector, release.parent,
        case_id=release.public_to_private[public_id],
        containment_config=release.containment_config,
        pressure_scale=release.pressure_scale, opt_in=True)

def make_environment(public_id):
    return RestrictedSourceScoredV1(
        fresh(public_id), scoring_mask=release.masks[public_id],
        expected_identity=release.identities[public_id],
        public_population=release.public_population[public_id], opt_in=True)

def verify(public_id, env):
    return verify_outcome(env, fresh_host=lambda: fresh(public_id),
        expected_identity=release.identities[public_id],
        scoring_mask=release.masks[public_id])

report = qualify(list(release.public_to_private),
    checkpoint='/private/existing-0700-directory/new-run.json',
    run_id='public-release-and-policy-version',
    make_environment=make_environment, make_policy=release.make_policy,
    verify_outcome=verify, wall_seconds=1800, max_decisions=3000)
```

Each policy implements `act(public_host, observation)` and must use only the
facade's methods. For a passive policy, `public_host.advance(60)` is one bounded
choice. The checkpoint path must not exist on a fresh run; its directory must be
owned by the user and mode 0700. Resume with the identical IDs and run ID and
`resume=True`; without an authenticated state adapter, incomplete cases restart
from handover. The wrapper supports restoring an independently digested scorer checkpoint, but
it does not supply an authenticated host-and-policy state adapter. Wall-clock
limits are cooperative between calls. This separately versioned runner writes
`scope: restricted_no_ground_no_evac` and `restricted_scored` rows. The
existing `qualify_source_day_v2` rejects restricted verifier results and does
not count them as unrestricted full-day qualification. Keep the checkpoint private, because the
orchestrator report includes scores. No twelve-case source run or validation is
implied by the synthetic test, and actual source cases may censor or block.

`EstimatedInputsV1` and `EstimatedMixedFireV1` are distinct types and are not
accepted by this version or the underlying v4 weather host. Supporting that
variant needs a separately reviewed integration and pins; it is not silently
converted to the v4 source-only input.

The raw host rejects every public `protect` call with the same message. The
restricted wrapper accepts shelter requests only for IDs in the explicitly
approved public roster. Rejected commands leave the episode usable when the
wrapper verifies the entire host and scorer accounting are unchanged. Unknown
scheduled event kinds appear only in private checkpoints; the public reason is
`unsupported_scheduled_event`.
