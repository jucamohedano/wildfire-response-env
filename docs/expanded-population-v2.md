# Experimental expanded population handover v2

`wildfire_expanded_population_v2.ExpandedPopulationV2` is a separately pinned,
opt-in synthetic population composition. It wraps the existing
`ExpandedPopulationV1` and leaves its source, catalog version, behavior and
pins unchanged. It adds an explicit handover exposure declaration for each
whole cohort. This is an exercise assumption, not a historical measurement,
health assessment, mortality estimate, source-case qualification or score.

Supply the v1 population catalog shape but use version
`wildfire-expanded-synthetic-population-v2` and add required `initial_exposed` and `handover_exposure` fields to every
group. The role remains `synthetic_fixture_only`;
case, input and network identities must match the fresh hold host. The caller
must independently pin the complete v2 catalog hash, including every flag.
`initial_exposed` is private and must be a boolean. `handover_exposure` is a
separately declared public synthetic report: use `null` for unknown or a boolean
for explicitly disclosed knowledge. A disclosed boolean must agree with the
private flag. Missing fields, invalid values, inconsistent disclosure and
duplicate groups fail validation. The existing
Operations model tracks exposure as a cohort-wide boolean: `True` means the
entire cohort's `count` is initially exposed. Partial counts are not supported.

The declared exposure is authoritative at minute zero. As a consistency rule,
a cohort located on a cell burning **after the fire warm-up at handover** must
be declared initially exposed. An exposed declaration away from burning cells
is allowed: it is an explicit synthetic handover assumption, not inferred fire
history. A cohort adjacent to a burning cell may start unexposed and acquire
exposure during the day under the existing dose proxy. A fire-censored handover
remains censored; population views and scoring are suppressed and no inference
is made from an unsupported fire state.

The private `initial_exposed` flag is never itself actor-facing. The handover
view exposes only `handover_exposure`, with `null` meaning unknown; its
`exposure_observed_at` is `0` for an explicit boolean handover report and
`null` when unknown. Later delivered population reports may establish a
boolean; its observation timestamp is the earliest delivered evidence of that
value. `observed_at` separately reports the last delivered position/status
observation. As in v1,
subsequent observations and protection decisions are delayed by the event bus;
exposure knowledge never regresses. Checkpoints and their identities include
the v2 catalog, source and wrapped v1 checkpoint. Restore verifies declared
inherited exposure cannot be removed, checks the v1 lifecycle identities and
clocks, and reproduces the joint state. Checkpoints are private and integrity
hashes are not authentication for untrusted artifacts.

```python
from wildfire_expanded_population_v2 import ExpandedPopulationV2

pop = ExpandedPopulationV2(fresh_hold_host, trusted_v2_catalog,
    expected_catalog_sha256=trusted_v2_catalog_sha256, opt_in=True)
handover = pop.situation()
# Trusted scorer only: fixed inherited whole-cohort counts from the pinned catalog.
inherited = pop.private_handover_exposure()
```

Use the same actor method allowlist and synthetic-access restrictions as
[population v1](expanded-population-v1.md). The Python object, `_inner`, catalog,
checkpoints and `private_handover_exposure()` are evaluator-private. No new
source-road access, population routing assumptions, or reward are introduced.

## Scorer integration contract

The existing experimental `ExpandedScoredV1` explicitly requires the exact
`ExpandedPopulationV1` type and therefore **does not accept v2**. Its original
source pin is not changed by this package, which lives outside the directory it
hashes. A separately versioned scorer must explicitly support and independently
pin v2's catalog and implementation; it must read the private
`initial_exposed` flags for inherited harm and never infer them from actor
`handover_exposure` knowledge; it must not weaken the old exact-class
gate or relabel v1. At minute zero it can read the live whole-cohort booleans
from the trusted wrapped v1 Operations state (`pop._inner._ops.groups`) or use
`private_handover_exposure()` to cross-check inherited counts against that
state. Freeze this handover baseline before advancing: later exposure is new
only for cohorts whose handover flag was false. Include the v2 package source in
the new scorer's implementation identity and verify a fresh v2 replay and
checkpoint. The v1 scorer's directory hash does not include this package.

`tests/test_expanded_population_v2.py` uses synthetic access and a test-only
mixed-input trust anchor. It distinguishes initially exposed cohorts on and
off the burning cell from a cohort first exposed after handover, checks
handover/observation timestamps, independent inherited/new accounting, fresh
replay, checkpoint continuation and input/censor consistency. A privacy
equivalence test compares different private exposure flags with the same
undisclosed handover until a population report is delivered. It is not a
full-day score or validation of the twelve source scenarios. Run with
`PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_expanded_population_v2.py
-q -p no:cacheprovider` after installing the project dependencies.
