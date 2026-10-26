# Fidelity and limits

This is a programmatic wildfire coordination benchmark, not an operational
forecast, dispatch authority or mortality model. Its outcome grader is verifiable
inside the simulator. That does not establish physical accuracy.

## Measured or source-derived context

- The Málaga study domain has a validated, complete MDT05-derived height surface,
  georeferencing and declared grid resolution. Missing elevations are not treated
  as flat ground.
- Fuel classes come from the prepared categorical source; their epoch and the
  class-to-behaviour mapping have documented limitations.
- Coastal station weather is delivered historical context. It is not automatically
  mountain fire-front weather and does not drive the synthetic wind vector.
- Source URLs, transformations and hashes remain in `data/malaga_2021/provenance.json`.

## Synthetic mechanics

Ignitions, fleet readiness, household groups, local smoke observations, report
wording, transmission delays, aircraft gates, movement and firefighting effects
are exercise assumptions. The current source clock represents night response.
Display roads are not travel routes; inherited base identifiers are compatibility
handles rather than measured Málaga station deployments.

Ground transit is an assumed decomposition of historical response duration plus
declared offsets. There are no dispatch-to-departure logs proving this split.
Sensitivity profiles vary the assumed transit base and suppression probabilities;
they are not fitted empirical confidence intervals.

The support-request heuristic assigns three reachable observed cells to a loaded
engine and one to a crew. These slots determine a simulated request for help;
they do not measure hectares a real team can extinguish. Occupied, travelling,
empty-water and out-of-range units do not count.

Population exposure is a cohort-level direct-fire/abstract-dose outcome. It must
never be reported as deaths or a medical risk estimate. Whole-cell burned
footprint is coarse simulated area; the grid does not resolve a real fire front.

## What is verified

- Delivered-evidence boundaries, unknown values and observation/receipt times.
- Command, acknowledgement, execution and arrival separation.
- Public-only draft checks and bounded historical queries.
- Independent outcome recomputation and exact seeded replay.
- Catalog grouping, seed separation, preregistration and incomplete-run reporting.
- Local Python/OpenEnv parity and client-state isolation.

The benchmark does not prove an optimal wildfire policy. CPU references are
causal heuristics. Real-world validity requires separate operational evidence
and expert assessment. A private implementation candidate is not a published or
trained model; the learning experiment remains gated on compute and its results.
