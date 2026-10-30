# The two unscored passive-policy exercises

The original evaluation and scores remain unchanged. These are synthetic
development exercises, not historical fire reconstructions.

- `incident-eval-v1-000` stopped at minute **1021** because fire approached an
  unresolved land/water cell. The guard checks proximity; it does not say the
  cell itself burned. The reviewed cell is old `(column 26, row 32)`, or
  `(40, 52)` in the candidate grid. Aerial review suggests its centre is rural
  land, but this is neither a whole-cell shoreline classification nor a fuel
  survey. Enlarging the rectangle does not resolve that uncertainty.
- `incident-eval-v1-007` stopped at minute **900** at the original domain
  guard. The corrected candidate extends the southern boundary by 1.5 km,
  while also extending the other sides. This may leave more room, but the
  old edge is not proof of a coastline, and a fire can still reach a new
  edge or unresolved cell.

The original passive run finished 10 of 12 days and was therefore incomplete;
the qualification output also reports exact replay and independent accounting
for both interrupted runs. Those checks do not turn a censored day into a
score. A new candidate must separately pass terrain, fuel, land/water and road
checks and complete a 24-hour run before it can receive a grade. A changed
wind, seed, map or intervention means it is a **new exercise**, not a repair
to the historical result.
