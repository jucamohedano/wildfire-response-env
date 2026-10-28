# Incident-day data readiness

[readiness-v1.json](readiness-v1.json) records an offline, source-pinned audit of
the full estimated pack and a separately pinned six-case run report. The audit
rebuilds the pack from its inputs and checks that the default loader rejects it.
The estimates remain assumptions and the 80 source-unknown cells remain unknown;
27 have unresolved land/water context. The runtime's unresolved-land guard and
explicit opt-in remain necessary.

The static route probe uses the six egress handovers and the first-wave declared
bases, including an unavailable resource's declared base. It compares current
walking assumptions with a stricter source-known walking mask. A router result
of `unmapped` is a bounded-search result, not proof that a physical road does
not exist. Graph endpoint counts do not establish road continuity, legal access,
truck suitability or historic conditions.

The attached run summary records the separate report's original implementation
hash, even if the current audit uses a different core. It includes only aggregate
and case-level findings, not private trajectories or evaluator state. The six
estimated exercises completed and passed replay and reward checks, but this does
not establish coverage, source approval or physical validation. The 20 × 26
candidate rectangle is not an admissible current large-fire scenario: both
dimensions must be at least 24.

The current saved report binds the measured run to the same implementation as
the audit. All six estimated-map days finish and replay, yet no applied ground
work is recorded and intended effects are missing in four cases. Completing a
day does not approve its source data or establish representative task coverage.

An additional read-only cached-road probe found provisional foot topology, but
pedestrian permissions/difficulty metadata were not retained. Its tested paths
did not fit the declared eight-hour shift, and one declared base was outside the
active grid. Keeping omitted motor-road components alone did not establish a
usable approach. These are reasons to redesign source-specific staging/access
with retained permissions, not to invent connectors or increase speeds until a
test passes. Display-road bundles omit node tags and restriction relations; they
cannot substitute for those access records.

To reproduce locally, provide independently held canonical document pins for
`--pack`, `--prepared`, `--domain`, `--network`, and `--estimates`, together
with the corresponding `--NAME-sha256` arguments. The ignored prepared and
domain bundles and estimated pack are not included in a fresh clone. Run:

```sh
PYTHONPATH=envs/wildfire_response/core/src python3 eval/check_incident_day_data_readiness.py \
  --pack PACK --pack-sha256 PACK_PIN \
  --prepared PREPARED --prepared-sha256 PREPARED_PIN \
  --domain DOMAIN --domain-sha256 DOMAIN_PIN \
  --network NETWORK --network-sha256 NETWORK_PIN \
  --estimates ESTIMATES --estimates-sha256 ESTIMATES_PIN
```

Optionally add `--run-report FILE --run-report-sha256 RAW_FILE_SHA256
--run-implementation-sha256 RUN_CORE_PIN` to summarize an existing plausibility
report. All three are required together; the report's input, network and run-core
identities must match the caller pins. The raw-file pin is a byte hash, unlike
the canonical JSON document pins. The run and current audit core identities are
reported separately. The checker does not run a full episode, approve a pack,
or publish private run artifacts.
