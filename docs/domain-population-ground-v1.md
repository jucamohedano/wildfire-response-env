# Domain, ground and population shared-clock prototype v1

`wildfire_domain_population_host_v1.DomainPopulationGroundV1` is an opt-in,
unscored synthetic exercise for the single documented domain-ground walk/hold
candidate. It composes a fresh `DomainSourceWalkHoldV1`, the pinned
`PopulationAnchorAdapterV1`, and the pinned `PopulationSegmentMovementV1`.
It is not a full incident environment, an operational access determination, or
qualification of the twelve source cases. Prior versions and identities are
unchanged.

At handover it installs the adapter's source-anchored synthetic groups and
destinations. The actor handover roster is derived before the episode from
those synthetic inputs: numbered cohort and destination aliases, cohort counts
and initial synthetic statuses. It contains no original identifiers, positions,
route geometry, live exposure or destination capacity. Center and route option
identifiers are also aliases. This candidate disclosure is not an independent
review or evidence of real population locations. The public facade is an
accidental-access guard, not a Python sandbox; keep the owner, source objects,
checkpoints, transcript, paths and identity away from actors.

A command stages one detached Operations tick and the ground/hold assignment
for the same incident minute, then performs one fire/weather step and commits
operations, ground, fire and delayed reports together. Population status and
exposure updates are delivered through the event bus rather than read directly
from live state. Destination closure reports are generic. Route options are
previewed from delivered wind and handover fire; the private movement guard
rechecks actual wind, fire, original edge path, current position and the swept
segment each minute. Unknown, estimated, mixed or otherwise unsupported route
cells fail closed. The conservative preview can be stale, and a submitted
option can be rejected later through delayed feedback. Existing access evidence
is an exercise assumption, not proof of safe or legal travel. An unsupported
scheduled event stops the host before the affected operations tick, and the
standalone stage rejects it without changing its owner.
If a failure occurs after staging begins, the owner is marked indeterminate;
the trusted driver must restore a previously saved checkpoint. The transaction
does not promise automatic rollback of a partially applied physical tick.

Synthetic tests use test-only source lineage and routing stand-ins. A bounded
source-backed probe also constructed this owner with the pinned domain profile,
documented walk/hold candidate, anchor adapter and segment guard. It submitted
a shelter order at minute 1, checked the population origin against current
modeled fire, wind and exclusions, and remained active through minute 3. One
handover route was returned as a low-confidence exercise assumption. Same-run
implementation identities are not independent approvals. This probe did not
exercise evacuation, arrival, hold work or a full day, and does not establish
real-world access. The documented ground candidate covers only one case; the
other eleven have no demonstrated ground access. The host is unscored; the
separate scorer prototype is described in `domain-population-scored-v1.md`.
