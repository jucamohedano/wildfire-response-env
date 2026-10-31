# Restricted source incident prototype v1

`wildfire_source_restricted_v1.RestrictedSourceIncidentV1` is an opt-in private
prototype using the pinned source-case selector, its original synthetic roster,
profile and event schedule, and the v4 weather/fire input. One weather/fire,
Operations and EventBus advance together. Aircraft, shelter orders, population
exposure, scheduled restrictions, destination closures, center and aircraft
outages and delayed reports share that clock. Ground resources are not simulated;
ground dispatch and evacuation are unavailable because the source access remains
unknown. Shelter and aircraft exercise assumptions are not historical evidence.

The runtime identity is `wildfire-source-restricted-incident-v2`: the second
prototype revision corrects aircraft abort and return-fuel timing. Checkpoints
from other host identities cannot be resumed with this revision.

The actor situation omits private population and exposure, and exposes no score;
only delivered reports are available. Host objects, selected source cases,
transcripts and checkpoints are trusted evaluator material. Checkpoints require
an independent digest; replay compares public results and the final checkpoint.
Unknown event types stop the run before their minute, and fire censoring stops
observations. A population position outside the grid or an undeclared exposed
cohort at a burning handover cell is rejected.

This is **not a complete or scored incident host**. Population observations,
ground lifecycles, fixed-mask accounting and an independently reviewed scoring
and replay verifier remain to be integrated. No source case is qualified and no
full-day score is claimed. The focused test uses real pinned scenario lineage
but **synthetic all-land fire support**, not the real source fire input; it is
only a lifecycle test and does not validate source terrain or access.

The separate [restricted scorer and runner](source-restricted-scored-v1.md)
provide evaluator-only scoring for this limited scope. They do not change the
unscored host or qualify the complete incident environment.

Direct public population orders are disabled on the raw host; the separately
versioned scorer requires an independently approved public roster before it
accepts shelter orders.

The host checkpoint identity is `wildfire-source-restricted-incident-v2`.
It preserves an aircraft's return-reserve fuel when an automatic abort charges
no movement: the abort occurs on the current tick, and the return flight and
fuel charge begin on the next tick. This is a new behavior identity; checkpoints and scorer identities from any other host hash, including v1, must
not be reused or silently repinned. An aircraft aborting at its base with full
tanks also does not occupy a refill slot.
