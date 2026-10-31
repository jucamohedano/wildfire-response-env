# Domain-profile source incident v2

**Privacy:** This legacy host's raw destination-closure report contains the private destination record. Expose actor reports only through the [redacted facade](source-public-facade-v1.md), or use the separately versioned [v3 host](source-domain-profile-incident-v3.md).

`wildfire_source_domain_v2.DomainProfileSourceIncidentV2` is a separate,
opt-in, **unscored synthetic sensitivity exercise**. It consumes the v2 domain
profile converter and weather backend, retaining the independently reviewed v3
profile and its fixed choice. The v2 host also pins the domain agent’s confirmed
converter source SHA-256 `b48d42d3995f0b254959cff089d2d0890c8b24130528f7a99b42739434282d95`.
The v1 converter, v1 source-host prototype, restricted host/scorer and their
pins are unchanged; v1 checkpoints and
identities are not v2 checkpoints or identities.

The v2 converter removes inherited v4 mixed factors where the profile leaves a
cell unknown or without terrain. Such cells remain unsupported and are censored;
no fuel, terrain or access is invented. The host independently checks every
remaining mixed factor before constructing or warming fire and rechecks the
live mixed and estimated inventories, factors, masks and initial fuel at action
and restore boundaries. The expected input digest must be independently approved.
`input_identity_sha256` returns a candidate, not an approval. A changed profile,
converter source, source-case lineage, input or access state fails closed.

The private construction and public actor interface follow
[the v1 integration notes](source-domain-profile-incident-v1.md), substituting
`eval.domain_profile_weather_v2.prepare`,
`wildfire_source_domain_v2.DomainProfileSourceIncidentV2` and the v2
`input_identity_sha256`. Prepare the source selector from the independently
validated original v4 base, not from the domain overlay. Keep the host, inputs,
profile, private cohort state, checkpoint and transcript out of actor access and
public logs. Weather and reports are delivered through the existing EventBus;
only reported aircraft, delivered fire observations, status requests and bounded
advances are exposed. No population, shelter, ground or evacuation action is
claimed. Censoring suppresses reports.

The old restricted scorer and source-walk wrapper reject the exact v2 host type;
the old hold-fire seam rejects the estimated fire backend. The domain profile
can change fire and handover and does not authorize road or walking access.
Ground, hold, scoring, a public population model and complete incident-day
qualification therefore require separately reviewed integration and pins. A v1
source smoke cannot validate this v2 host. No historical or operational claim
follows from these sensitivity assumptions.

The focused synthetic regression includes a v4 mixed cell with missing terrain:
the v2 converter removes its factor, leaves it unknown and the host censors
when fire reaches it. It also checks cross-version rejection, the fixed
converter hash, privacy, aircraft actions, live-factor tampering, checkpoint
continuation and replay. A private source-backed v2 smoke found an active
handover and passed a status request, checkpoint continuation and fresh replay.
That smoke generated its input digest in the same run; it is not an independent
approval. It did not test a full day, source-backed aircraft drop, ground access,
or scoring.
