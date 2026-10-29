# Synthetic operational calibration probes

Run `python eval/check_incident_day_calibration.py --output /tmp/NEW-DIRECTORY --wall-budget 120`.
The output directory must not exist. The versioned JSON contains the full small
mechanism traces and paired development-case observations; HTML summarizes the
episodes. Private minute audits and trajectories are kept under `private/`.
The wall budget covers simulation and verification; a worker that exceeds it is
terminated and recorded as censored. A completed episode has exact replay and
independent reward agreement. Overall status is `complete` only when all
episodes complete against the same implementation; censoring gives `incomplete`,
and errors or implementation changes give `failed`. The CLI exits nonzero for
incomplete or failed reports. The report contains no success labels.

The probes use hand-constructed routes, resources, stocks, facilities, a toy
location-dependent hazard and a toy clock. Comparisons change one mechanic at a
time: travel/setup/work, blocked movement, initial and facility water stocks,
rest and duty, aircraft fuel, current daylight/wind at drop, destination capacity,
relocation and order timing. The focused tests validate the specified synthetic
mechanisms and comparisons. The exercise is not calibrated to real-world physics,
operational doctrine, a historical reconstruction, or an estimate of real
exposure. Fixture values are not profile tuning. Six pinned cases are
development cases, not held out; this runner defaults to 001 and 004 only.

Uncalibrated exercise assumptions include fire spread and suppression, the
240/60-minute work/rest defaults and duty limits, travel speeds, setup and
refill delays, resource capacities and stocks, communication delays, the
synthetic solar and aircraft wind window, recovery assumptions, destination
capacity, hazard/exposure effects and incomplete road coverage. The real
source-pack approval remains blocked. Existing primary-source links and their
limited claims are documented in the parent profile and README; none establish
numerical bounds for these probe settings. No new source evidence is asserted.

In the pre-fix same-seed 001 private audit, the dispatcher selected the first
listed destination on a nearby-smoke report at minute 191, then issued another
order at minute 192. The first trip blocked at minute 197; the group became
exposed at minute 1157. Passive exposure was zero. The public route options
showed a shorter route to the other destination. The dispatcher chooses the
shortest publicly offered route to an open destination with sufficient reported
capacity and avoids duplicate orders. The routing guide uses public observations;
unobserved fire is never an input to the policy. Offered routes are not a
guarantee of safety, since observations and capacity reports can be delayed or
incomplete. These observations motivate a policy correction; they do not
establish that the simulator or a real evacuation route is safe.
