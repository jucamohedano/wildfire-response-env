# Domain-profile source ground bridge v1

This is an opt-in, private, **unscored synthetic prototype** for the one
previously documented source-walk candidate case. It is not a full incident
environment, an operational-access decision, a historical deployment or a
qualification of the other eleven cases. The original domain host, source walk,
hold lifecycle, source inputs, candidate and low-water converter are unchanged.

The bridge uses narrow subclasses of the existing domain host, source-walk and
hold lifecycle. They retain the existing single clock, Operations and EventBus,
movement logic, command privacy, setup, budget accounting and delayed feedback.
New identity and checkpoint envelopes reject cross-version reuse. The small
separate estimated-fire containment seam is necessary because the existing hold
seam intentionally accepts only the exact non-estimated backend. The new seam
requires the exact estimated backend, retains its censor, stages one validated
assignment batch in the existing fire/kernel tick, and checks work costs. It does
not add a second fire step or substitute missing fuel, terrain, water or access.

The original v4 candidate and its pinned parent remain unchanged. Walking is
restricted to the intersection of the original source-backed land inventory and
the domain contract's source-backed land, excluding estimated, mixed and unknown
cells. The candidate is reconstructed against the domain host's delivered
handover fire and wind and must match exactly; it is rejected rather than moved
or silently repinned if the new profile changes the route or front. Current
modeled fire and wind, the supported-cell mask, mapped-water evidence and coarse
grade are revalidated during movement, setup and work. The containment seam
rechecks immediately before work, including after any aircraft drop, and the
hold lifecycle checks again after spread. These are synthetic assumptions and do
not establish a safe route or work site. The crew is assumed predeployed; return
is limited to the verified reverse walk and requires assistance at staging because
road return remains unknown.

A trusted evaluator must independently approve the domain input digest, the
original private candidate digest and the new bridge identities before use.
Candidate-identity helpers are review aids, not approvals. The hold identity
helper is read-only: it does not install the hold seam or consume the walk.
It requires a fresh, active walk with the original unwrapped estimated backend;
a walk already attached to a hold host is rejected. Host, walk and hold
checkpoints, transcripts, source evidence and future schedules are private.
`PublicDomainGroundV1` supplies a narrow actor allowlist and redacts destination
closure reports; it is not an in-process sandbox. There is no score, population
protection or evacuation API in this ground prototype.

The focused tests use synthetic data and test-only lineage stand-ins. They check
domain host/clock binding, estimated-fire containment, conservative walking
support, candidate reconstruction, delayed observations, finite budgets,
no-premature work, breach, hazard revalidation, private-state boundaries,
checkpoint continuation and replay. They do not establish source access or
physical calibration.

A bounded source-backed probe constructed the host, documented candidate and
hold bridge. The delivered dispatch result was accepted; after five further
minutes the episode remained active and the latest delivered crew phase was
walking. That observation may be stale. Input and bridge identities were
generated in the same run and are not independent approvals. Arrival, setup,
holding, return and full-day behavior were not established by this probe, and
the other eleven cases remain outside this ground-access scope.
