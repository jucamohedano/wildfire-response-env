# Extended inputs v1

`envs.wildfire_extended_inputs_v1` is an independent, opt-in input contract and
pre-step censor for a future compatible engine. It is **not an incident-day
input pack, simulator, approved dataset, route planner, or live emergency
service**. It changes no existing versions or release pins. The tests use only
synthetic data; no source-derived grids are distributed here.

## Input and pins

`load_inputs(document_or_path, expected_sha256, *, expected_source_pins,
opt_in=True)` requires explicit opt-in and independently supplied hashes. The
document SHA-256 is over UTF-8 JSON serialized with sorted keys, compact
separators, ASCII escaping, and no NaN or infinity; `canonical_sha256` computes
it. The caller must obtain the expected document hash from a trusted external
manifest, not from the document being checked. `source_pins` has exactly four
lowercase SHA-256 strings, keyed by `surface`, `fuel`, `elevation`, and `access`,
and must match the separately supplied `expected_source_pins`. Each should
identify the exact source artifact or a pinned manifest describing the layer,
including its attribution, coverage, acquisition and derivation. If a layer has
no observations, pin a documented empty-evidence manifest. The validator does
**not** fetch or hash those source files, check rights, establish the truth or
completeness of classifications, or certify the source's suitability. The
caller must verify those artifacts independently. A changed cell, mask, source
pin or metadata requires a new independently trusted document hash.

The JSON object has exactly `version`, `grid`, `domain_mask`, `surface`, `fuel`,
`elevation`, `access`, and `source_pins`. `version` is
`wildfire-extended-inputs-v1`. The grid specifies integer `width` and `height`,
a nonempty `crs`, and a six-number GDAL-style north-up, unrotated, square-cell
`transform` `[cell_width, 0, west, 0, -cell_width, north]`. Bounds and CRS
correctness must be independently established; a string alone is not proof.
All five matrices are rectangular, north to south, and match the grid. Inputs
are limited to 4 MB, at most 1,000 per side and 200,000 cells. Duplicate JSON
keys, non-finite values, unsupported fields and mismatched pins are rejected.

- `domain_mask` is a fixed Boolean matrix. An excluded cell is unknown for
  fire and access, not an impermeable map edge. It is not a dynamic fire mask.
- `surface` is `land`, `sea`, `mixed`, or `unknown`. `land` and `sea` mean
  adequately supported **whole-cell** classifications; shoreline ambiguity is
  `mixed` or `unknown`. Confirmed sea is nonburnable. This contract makes no
  inference about inland water. A sea cell must have null fuel and elevation
  and cannot be marked accessible.
- `fuel` is a source class 1–10 or null; zero and other values are not accepted
  as unknown. The caller must not substitute a synthetic fallback class; the
  validator cannot distinguish one from a source class. A fuel centre sample does not prove
  whole-cell land. `elevation` is a finite value in metres or null; missing
  elevation is not zero or flat terrain. Values do not establish calibration.
- `access` is `accessible`, `blocked`, or `unknown`, independently of fuel.
  `accessible` requires confirmed in-domain land and affirmative evidence for
  the intended use. `blocked` requires evidence of the relevant restriction;
  no observation is `unknown`. `access_status` combines declared cell states,
  returning blocked if any cell is blocked, otherwise unknown if any cell is
  unknown or excluded, otherwise accessible. It does not establish connectivity,
  geometry, legal access, mode, safety, a route, or network completeness.

Returned matrices and the derived `fire_support` mask are immutable tuples.
Support means confirmed sea, or confirmed land with known fuel and elevation,
inside the fixed domain. Support is **not** a burnability mask: confirmed sea
is supported and nonburnable, while unsupported cells remain uncertain. Surface
classification, fuel and elevation must be adequate to the intended model; the
contract cannot verify that a pinned source has sufficient coverage.

## Censor protocol and limits

`Censor(inputs).check(burning)` takes the complete set of current burning
`(column, row)` cells and returns whether it is safe to continue under this
contract. Call it on the initial fire and immediately before **every** spread
step, and after every state change that can introduce burning, including the
final state. It checks each burning cell and all eight neighbouring cells.
It censors on a burning sea or unsupported cell, an unsupported neighbour,
or contact with the rectangular boundary. Invalid coordinates raise and also
make the censor sticky. Once censored, it stays censored, including after
extinction. The caller must stop stepping, suppress or explicitly mark affected
outcomes as censored, and must not treat omitted cells as firebreaks. The helper
does not itself run or stop an engine, validate observations or ignition history,
or censor exported outcomes; the integration must enforce all of those duties.

This conservative protocol is valid only when all influences on the next step
are confined to the burning cells and their one-cell eight-neighbour stencil.
An engine with spotting, longer-range spread, other hidden state, or wider
terrain dependencies needs a wider independently validated censor before use.
An empty burning list is interpreted as no active fire; the caller must provide
the complete, truthful state at every required check. The helper cannot detect
missed checks or a fabricated observation.

## Current map and incident-day limitation

The corrected 84 × 59 candidate described in [extended-map-v1.md](extended-map-v1.md)
has all surface values unknown. Fuel centre samples, inherited parent heights,
the separate unapproved terrain candidate, and partial road observations do
not supply a confirmed whole-cell coast or a complete operational road network.
Thus this contract cannot safely produce a runnable fire on that candidate.
The terrain candidates must not be blended implicitly. Source quality, access,
coastal evidence, licence and release reviews remain outstanding.

The frozen `incident-day-inputs-v2` loader requires full study and height
coverage and resolved fuel. `DayFire` rejects unresolved fuel, while real-map
scenarios pin the old pack and network. Its fire kernel and outputs do not
enforce this new pre-step censor. Accordingly there is deliberately **no adapter
or claim of live incident-day integration**. A separately versioned compatible
engine and scenario, validated spread stencil and outcome suppression, verified
source classifications, routing evidence and release review are required first.

Run the synthetic tests from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_extended_inputs_v1.py -q -p no:cacheprovider
```
