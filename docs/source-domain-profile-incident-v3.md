# Domain-profile source incident v3

`wildfire_source_domain_v3.DomainProfileSourceIncidentV3` is the separately
versioned, opt-in, unscored domain source host. It retains the reviewed v3
profile and pinned weather-input v2 converter, mixed/estimated fuel checks,
censoring and restricted aircraft/event behavior described for
[domain v2](source-domain-profile-incident-v2.md). It changes the
`destination_closed` public report: the private Operations destination is still
closed, but the delayed public event contains only `A destination has closed.`
No destination ID, name, position, capacity or other record field is emitted.

Its host version and `input_identity_sha256` namespace are v3. Fresh v3 input
fingerprints and host identities require independent review; older v1/v2
checkpoints, transcripts and same-run candidate pins must not be silently
reused. The v1/v2 code and profile inputs remain unchanged for reproduction.
Their raw reports remain private and require the
[redacted public facade](source-public-facade-v1.md) before actor exposure.

The focused v3 test uses synthetic profile and lineage fixtures. It verifies the
private destination actually closes, the public event is delayed and generic,
and private destination fields never appear in public situation or reports. It
also exercises estimated and mixed input checks, unknown and sea censoring,
aircraft actions, checkpoint continuation, exact replay and compatibility
rejection. This is not a source-backed v3 full-day run or operational-access
qualification; the v2 source smoke does not establish v3 outcomes.
