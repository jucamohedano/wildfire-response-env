# Domain-profile source incident v1

**Privacy:** This legacy host's raw destination-closure report contains the private destination record. Expose actor reports only through the [redacted facade](source-public-facade-v1.md); checkpoints and transcripts remain private.

`wildfire_source_domain_v1.DomainProfileSourceIncidentV1` is an opt-in,
**unscored synthetic sensitivity exercise** using the pinned domain-profile
`EstimatedInputsV1`, the source-case lineage selector, one weather/fire clock,
one Operations state and one EventBus. It is separate from the frozen restricted
host and scorer. Estimated fuel classes, mixed surface and crossing factors are
exercise assumptions, not source measurements or physical calibration. Tied
fuel hypotheses use the profile's explicitly declared deterministic tie choice;
unknown cells remain censored, and mapped sea remains nonburnable.

The trusted caller must prepare the domain input using
`eval.domain_profile_weather_v1.prepare` with its independently reconstructed
v4 source and required raw evidence. Construct `SourceCaseCatalogV1` separately
from that original v4 base and its pinned original/anchored catalogs, expanded
fire drafts and reviewed graph/source pair. The host verifies that the domain
input descends from the selector's exact base and has the fixed profile and
choice admitted by this version. It requires a separately reviewed
`expected_input_sha256` covering the complete effective input, estimates, masks
and factors. `input_identity_sha256(inputs)` generates a candidate for review;
a digest generated from the run being assessed is not independent approval.
Changing the profile requires a new reviewed version, not a replacement pin.
The code-owned digest is an integrity gate, not a signature or evidence of
operational approval.

```python
from wildfire_source_domain_v1 import DomainProfileSourceIncidentV1

env = DomainProfileSourceIncidentV1(
    release.domain_inputs, release.original_v4_selector, release.parent_inputs,
    case_id=release.private_case_id,
    containment_config=release.containment_config,
    pressure_scale=release.pressure_scale,
    expected_input_sha256=release.approved_domain_input_sha256, opt_in=True)
```

The actor interface can expose only `situation`, `reports`, `route_options`,
`clear_aircraft`, `dispatch`, `release`, `request_status` and `advance` through
a reviewed facade. Aircraft decisions use the existing synthetic clearance,
flight, fuel, water, drop and return logic. Weather and incoming reports are
available only after delivery; aircraft targets must be delivered fire reports
or the explicitly time-stamped handover. Initial population and inherited
exposure remain private. Raw `protect` rejects every request uniformly; no
population observation, shelter or evacuation API is claimed. Ground dispatch,
road routes and walking remain unavailable because access is unknown.

The complete fire is censored before and after the physical step. Censored runs
suppress reports; unknown source events stop before their scheduled minute and
expose only a generic public reason. The private input inventory is bound to the
live estimated and mixed fuel factors, censor masks and initial fuel on action
boundaries and restoration. The trusted host, source materials, private cohort
state, profile, identities, checkpoints, transcripts and any source case labels
must never be exposed to actors or public logs. Checkpoints require an
independently stored digest; replay uses a fresh host, a trusted transcript and
final checkpoint digest. Hashes do not sandbox a hostile Python process.

## Ground and scoring compatibility

The frozen restricted scorer accepts only its original exact host and does not
score this one. The existing source-walk wrapper also rejects this exact host
type; it binds a different v4 handover and independently supported land cells.
The hold-fire seam additionally requires the original exact mixed-fire backend,
whereas this host uses `EstimatedMixedFireV1` and its estimated censor. Reusing
those wrappers or their pins would be unsafe: the estimated profile can change
fire, handover and censor support, and does not establish road or walking
access. A new independently reviewed ground policy, source-specific candidates,
estimated-fire hold seam, public population model, scoring and full replay
verification would be required for broader integration. No claim of full
incident-day qualification or historical reconstruction is made here.

Focused tests use a synthetic profile and test-only lineage stand-ins. They
exercise delayed reports, real aircraft dispatch/drop, unknown-cell censoring,
input and live-factor tampering, private checkpoint continuation, replay and the
legacy scorer/walk exact-type guards. They do not approve the private profile or
establish performance or physical validity on source data.

A private development smoke of an earlier host revision reconstructed the pinned
v4 inputs, selector and then-current domain profile, then found an active source
episode. It preceded the added pre-construction mixed-factor guard. A status request,
checkpoint continuation and fresh transcript replay matched. The input digest
for this smoke was generated in the same run and was not an independent approval.
The smoke did not establish a full day, source-backed aircraft drop, ground
access, scoring or physical calibration.
