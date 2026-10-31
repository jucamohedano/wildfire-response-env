# Synthetic source walk-and-hold prototype v1

`wildfire_source_walk_hold_v1.SourceWalkHoldV1` is an opt-in, private,
**unscored and restricted synthetic exercise** around the exact source-walk v2
host. It models one assumed predeployed crew and the separately approved walk
candidate. It does not establish operational access, a historical deployment,
physical safety, a complete incident environment or a source-case score.

The original walk candidate explicitly authorizes no work. This new version
requires a separate explicit opt-in and independently approved hold identity; it
does not alter the candidate or upgrade its evidence. Its synthetic work edge is
the orthogonal boundary between the candidate's observed fire-front cell and
its adjacent site cell, protecting the site cell. Both must be supported by the
pinned fire backend. The existing containment kernel applies the assignment in
the same physical tick as fire spread; gain, decay, pressure, wind and breach
are the pinned model's abstract exercise assumptions, not measured line
productivity. There is no engine support, off-site line selection or arbitrary
work location.

The public `start_hold(crew_id)` command is available only after the crew's
arrival has been delivered to the actor. A request consumes a minute; the
private actual phase, front and safety checks determine an asynchronously
reported acceptance. An accepted request enters setup for the independently
pinned scenario's `ground_setup_minutes` before any work. The walk lifecycle
charges one existing crew work/duty minute per active setup, walk, hold or
retreat tick; a holding assignment consumes that same minute, not an additional
minute, and no crew water. The wrapper checks the containment kernel's returned
cost against the actual crew budget change. Exhaustion stops work.

The current modeled fire and wind, supported terrain, mapped water and
candidate-front relationship are checked before each work tick, again at the
containment seam after any aircraft drop, and after fire spread. If the front
becomes unsuitable before the kernel runs, the staged assignment is withdrawn;
the elapsed crew duty minute remains charged without a containment work cost. Loss of safety stops the hold; the walk host then retreats only along
its previously traversed, revalidated path or requires assistance. A modeled
breach stops the hold. Holding/breach observations and request/status feedback
are delivered through the existing delayed EventBus; true line strength, private
fire, coordinates, candidate data, work audit and checkpoints are not actor
observations. If a censor occurs, reports are suppressed. These checks implement
an explicitly weaker synthetic front profile than the ordinary road hazard
buffer and do not establish real-world safety. Road return from the staging
point remains unknown; reaching it requires assistance.

The wrapper supports private trusted checkpoints, continuation and exact public
command replay. Checkpoint and transcript digests must be stored independently;
these Python objects are not an actor sandbox. Only documented public methods
(`situation`, `reports`, `advance`, `dispatch_crew`, `retreat`, `start_hold`,
`clear_aircraft`, `dispatch_aircraft`, `release`, `request_status`) should be
exposed through a reviewed facade. No score, outcome, qualification gate or
training record is supplied. The existing walk, restricted host and scorer
versions are not modified.

A trusted driver first constructs a fresh, independently approved
`SourceWalkExerciseV1`. `configuration_identity(walk, opt_in=True)` produces a
**candidate** hold identity for independent review, not approval; it consumes
that walk instance by installing the hold-fire adapter. Construct a second fresh
approved walk and pass the separately approved identity:

```python
from wildfire_source_walk_hold_v1 import SourceWalkHoldV1

env = SourceWalkHoldV1(release.make_fresh_approved_walk(),
                       expected_identity=release.approved_hold_identity,
                       opt_in=True)
# Dispatch the approved public crew, wait for delivered arrival, then request
# start_hold. Keep env, its factory, pins, checkpoints and transcript private.
```

The user-authorized exercise assumes the crew can work the documented synthetic
front after reaching it; the original road, water and terrain evidence does not
prove that assumption. A source candidate can censor, retreat or require
assistance before arrival. It must not be moved or repinned to force work.

## Validation performed

The focused synthetic suite exercises actual containment-kernel work and breach,
no work before arrival and setup, single charging of finite budgets, pre- and
post-step hazard interruption, delayed and out-of-order feedback, private-state
non-disclosure, trusted checkpoint continuation, exact replay, and retreat to
assistance without an invented road return. It uses synthetic fire and geometry;
it is not source access evidence.

A private development smoke of the earlier build (before the additional
post-drop seam check) reconstructed the pinned nominal v4 inputs and one
corrected source candidate on the restricted v2 host. Handover fire and modeled
and delivered wind matched; the hold wrapper constructed, the crew's arrival was
delivered, and modeled hold work occurred after setup. The crew remained in the
holding phase at the end of the short twelve-minute post-request window. This
smoke generated its candidate walk and hold identities in the same run; it did
not independently approve them. It did not test the other alternative, a full
incident day, source-backed breach or retreat, or operational access. No source
score or physical safety conclusion follows.
