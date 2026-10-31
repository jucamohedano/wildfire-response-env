# Extended incident-day scoring v1

This directory provides **standalone evaluator helpers**, not an integrated
incident-day environment or a released benchmark. The existing core, reward,
verifier, observation and checkpoint contracts are unchanged. The extended-map
candidate still has unresolved surface, fuel and terrain coverage; no mask or
run derived from it is approved for scoring. See [extended-map-v1.md](extended-map-v1.md).

## Accounting contract

The reward version is `wildfire-extended-incident-day-outcomes-v1`; its verifier
is `wildfire-extended-incident-day-verifier-v1`. On an uncensored, unabandoned
1440-minute terminal day the score is 0.8 times the fraction of the population
not already exposed at handover that avoids new exposure, plus 0.2 times the
fraction of fixed-mask burnable cells not already in the handover footprint that
are not newly burned. If either denominator is zero, that fraction is one. This
matches incident-day outcomes v2 when the mask contains all initial burnable
cells. Exposure is a simulated outcome, not a mortality estimate. Inherited
burned hectares count the whole handover footprint, as in v2; additional
burned hectares count only new footprint cells in the eligible fixed mask.
Unresolved evacuation counts include groups with status `evacuating`, `blocked`
or `unable_to_comply`, or a pending protection order. `safe_success` additionally
requires no new exposure and no unresolved group.

The trusted evaluation configuration supplies a **fixed** set of nonnegative,
row-major integer cell IDs that are burnable at handover. Its domain and grid
must be pinned independently of the policy, run and audit. The helper checks
that this set is a subset of the audit's initial burnable cells, excludes the
initial footprint from its denominator and never adds cells during the episode.
It hashes the sorted unique IDs as compact ASCII JSON using SHA-256; duplicate,
boolean, negative and non-integer IDs are invalid. Empty masks are permitted
mathematically and give retained fraction one, but release policy must decide
whether an empty or partial mask is appropriate. A mask cannot convert unknown
fuel or surface into safe terrain, justify omitting uncertain harm, or replace
conservative boundary/unknown-state censoring. Preventing selective or
unrepresentative masks is a responsibility of release configuration and data
review, not something the hash establishes.

A full day is scored only after exact replay and independent accounting agree.
As in the existing verifier, invalid abandonment is a verified zero; a censored
run, including simultaneous abandonment, is unscored. Timeout, unfinished and
other truncated runs are also unscored. The private outcome retains the v2
convention of zero `graded_score` for non-completions and `score_eligible` for
abandonment even when also censored; the external gate applies censoring first.
A driver timeout is unscored even if an in-flight action has subsequently
terminated. The verifier returns only version, status, verified, eligibility and
score; audit, identity details, outcome diagnostics and exception text are not
returned. Treat `recompute` inputs and outputs as evaluator-private.

## Adapter and integration requirements

`recompute(records, scoring_mask=..., terminal=..., abandoned=..., censored=...)`
independently reads the existing v2 private audit shape: minute zero has
`footprint`, `burnable`, `cell_area_hectares` and population; every record has
`minute`, `new_footprint` and population (`count`, cumulative `exposed`, `status`
and optional `protection_pending`). Minutes must be contiguous from zero, cell
IDs unique, and the roster, counts and cumulative exposure consistent. The
helper computes these metrics from the audit and does not use a production
counter. It does not reconstruct physics or prove audit authenticity.

`verify_outcome(env, expected_identity=..., scoring_mask=...,
infrastructure_timeout=False)` expects a **trusted future adapter**, whose
`trajectory()` includes the existing identity fields (`interface_version`,
`task_id`, `seed`, `inputs_sha256`, `network_sha256`, `scenario_sha256`,
`implementation_sha256`) plus `scoring_mask_sha256`. The expected identity and
mask must come from trusted evaluation configuration, never from the run being
graded. The adapter supplies `terminated`, `truncated`, `minute`, `abandoned`,
`censored` and `reward`, and `verify_extended_episode()`, returning a private
mapping with `passed`, `exact_replay`, `audit` and production `outcomes`. The
adapter must actually replay from pinned inputs, actions and seed and compare
all relevant private state and audit; the boolean is a trust boundary, not a
replay implementation. The gate checks identity before and after replay,
recomputes all v1 outcome fields and compares them and the reward with the
production values. Verification must be read-only and must not advance time or
randomness; the gate checks the exposed state and identity but cannot prove all
hidden state remained unchanged.

Before enabling this in an extended engine:

1. Resolve and validate map, fuel, terrain, land/sea, roads and unknown-boundary
   handling; pin the grid and a policy-independent mask in a versioned release.
2. Add a separately versioned engine/counter and audit that implements this
   accounting, maintains a fixed mask across checkpoint/restore and censors
   uncertainty conservatively. Preserve existing reward and scenario IDs.
3. Implement the trusted exact-replay adapter, pin code and data identities,
   keep audit and diagnostics private, and ensure no observation or actor-facing
   transport exposes hidden state or intermediate reward.
4. Test real completed, abandoned, censored, truncated, timeout and resumed
   episodes, replay, determinism, counter/audit agreement, mask pinning and
   observation/export isolation against the complete runtime validation matrix.

Run the standalone checks from the repository root with
`python -m pytest tests/test_containment_score_v1.py -q`. They use synthetic
audits and a stub adapter; they do not establish integration, model calibration,
source completeness or replay correctness in a real extended engine.
