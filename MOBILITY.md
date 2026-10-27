# Coastal mobility development v1

An opt-in extension, not a replacement for benchmark v1. Three new development
cases use the existing validated fire domain and source-grounded roads around
Sierra Bermeja, Estepona, Casares and Manilva. The wider **Coast** map also shows
Marbella. Geography outside the validated height/fuel domain is transport and map
context, not additional fire physics. **Routes** fits complete journeys, including
the aircraft staging reference near Málaga.

## What is real and what is assumed

- IGN PNOA imagery remains georeferenced and unchanged. Town labels are actual
  named OSM settlement nodes, not the synthetic population groups.
- The pinned OSM road snapshot has 4,298 nodes and 8,878 directed arcs. It respects
  shared-node junctions, explicit directions and the preparation access filters.
  Official IGN acquisition failed; no official road geometry is claimed.
- Road data were acquired on 27 October 2026, not in September 2021. Availability,
  traffic, closures, turn restrictions, truck dimensions and emergency access
  have not been independently validated. This is not operational navigation.
- Ground staging is fictional: closest mutually connected road nodes near the
  declared Estepona/Casares anchors. Compatibility IDs retain their original
  names, but the UI uses the new exercise staging labels.
- Aircraft use Málaga airport's geographic ARP, **36°40′30″N, 004°29′57″W**, from
  [ENAIRE AIP AD 2-LEMG](https://aip.enaire.es/aip/contenido_AMDT/LE_Amdt_A_2026_09_AD_2_LEMG_es.html).
  Assigning an aircraft there is fictional, not evidence of a firefighting base.
- Ground speeds are synthetic road-class defaults. An engine stops at road
  access within 150 metres of its assigned cell centre; unreachable requests
  fail validation. Crews can finish with an explicitly assumed straight walking
  leg up to 1,500 metres at 3 km/h. No traversable footpath is established by that
  Euclidean gap. Aircraft fly illustrative direct legs at 240 km/h, without an
  airspace, altitude or certified flight model.
- Case `000` assumes daytime, `001` night, and `002` is a false alarm. Historical
  weather stays separate proxy context; assumed daylight is not a reconstruction
  of the original incident clock. Fire, population and protection effects remain
  synthetic. The safety-first reward formula is unchanged, but new travel times
  mean scores are not directly comparable to legacy tasks.

Source hashes, acquisition attempts, filtering, coverage and ODbL attribution:
[`data/coastal_mobility_v1/`](data/coastal_mobility_v1/README.md).
Runtime parameter provenance, including the aircraft ARP and synthetic values:
[`simulation_assumptions.json`](data/coastal_mobility_v1/simulation_assumptions.json).

## Movement contract

Road distances determine travel duration after authorization and mobilization.
Rounding to whole simulation minutes slows rather than accelerates travel.
Mid-route release starts at the current position and completes the current
directed road arc before routing home; it does not teleport or reverse through
a one-way link. Aircraft return after a finite sortie and refill at staging;
weather grounding can abort an in-progress sortie.

Public resource records add `map_position` in EPSG:25830 and `movement`, containing
the declared journey, route ID, segments, distance, departure/arrival estimates
and `observed_at`/`received_at`. Positions are sampled each minute and delivered
one minute later. `mobility.route_options` lists static reachable public targets;
it does not reveal future incidents. Checks and read-only queries do not advance
time. Animation interpolates only between already delivered fixes; pause and
scrubbing show the selected recorded position, with no future extrapolation.

Canonical public trajectories pin the network, staging nodes, implementation and
new interface version. The verifier reconstructs the episode and reward. Existing
regional/topographic/benchmark source files and their replay hashes are untouched.
The old compact model-policy adapter remains a legacy contract; use the new full
Python observation/tool API when experimenting with these development cases.

## Run

Append these options to the existing local UI command:

```sh
--mobility-network data/coastal_mobility_v1/network.json \
--mobility-sha256 12782cb4ccb4bbfd2d59eaaa7380ad6b1a807320cf5a15b6d01e44d7ec89fc16
```

The server starts in `coastal-dev-000`. Choose **Routed ground + aircraft**, then
**Run to end** and **Play**. **Coast**, **Fit activity**, **Routes** and the slider
are view controls, not simulation actions. Without the mobility options, legacy
case selection and mechanics are unchanged.

CPU smoke runner, using the same caller-pinned prepared/terrain arguments:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m eval.run_mobility_smoke \
  --prepared-inputs <prepared.json> --prepared-sha256 <prepared-pin> \
  --topographic-inputs <domain.json> --topographic-sha256 <terrain-pin> \
  --mobility-network data/coastal_mobility_v1/network.json \
  --mobility-sha256 12782cb4ccb4bbfd2d59eaaa7380ad6b1a807320cf5a15b6d01e44d7ec89fc16 \
  --output .runtime/coastal-mobility-smoke
```

This writes replay-verified compressed public trajectories and a JSON summary with
elapsed time and peak RSS, using one CPU worker. It requires no model, API key,
GPU, paid service or live routing call. Three development cases do not establish
real-world effectiveness or provide a new held-out training benchmark.

## Validation on 27 October 2026

- 15 routing/movement tests, 15 road-preparation tests, 44 UI/API/terrain tests and
  54 frontend tests passed. Production build passed with the existing bundle-size warning.
- Independent review identified cancellation-ETA and delayed-order edge cases;
  fixes are covered through complete one-minute decision episodes as well as the
  default two-minute interface. Caches are per-instance and garbage-collection tested.
- The three real-snapshot CPU episodes replayed exactly, without invalid actions
  or boundary censoring: 22.731 seconds elapsed, 158.02 MiB peak process RSS.
  Daytime showed road and flight movement; night showed ground movement only;
  the passive false alarm showed no resource movement.
- Both fire exercises reached the 120-minute horizon with resources still
  committed/returning; the false alarm terminated at 24 minutes. These are not
  three full historical incidents or model-training results. The existing longer-
  session follow-up remains separate.
