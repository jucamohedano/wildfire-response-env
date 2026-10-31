# Expanded road exercise policy v1

> **Superseded:** An independent review identified crossing-origin and split-validation gaps in these earlier artifacts. Use the separately pinned replacements and reproduction steps in [road review fixes](expanded-road-review-fixes-v3.md). The figures below document the earlier run; its reproduction commands no longer describe the current scripts.

This optional policy overlays synthetic ground-vehicle permissions on the pinned
[source-only expanded graph](expanded-road-runtime-v1.md). It is intended for a
caller that explicitly opts in to the assumptions below and enforces edge
`allowed_modes`, cell-access rules and hazard checks. These are exercise
assumptions; they do not establish legal permissions, current conditions,
vehicle suitability or a historical reconstruction. OSM attribution and rights
limitations in the source-only documentation still apply. Keep both graphs and
all derived geography private under `/tmp`.

The source-only graph remains unchanged, with empty modes. The opt-in graph is
separately versioned as `expanded-road-exercise-policy-v1` and pins that graph,
the original candidate and manifest. It preserves every node, edge, source arc,
direction, geometry, source segment range, speed and weak component. An empty
mode list marks a segment unavailable under this policy; it is not a claim that
no physical road exists. Edge `exercise_access_basis` and the graph's
`exercise_assumptions` document the distinction between positive source tags,
assumed use, exclusion and unknowns.

## Policy

- Crew and engine denote ground vehicles. For motorway, trunk, primary,
  secondary, tertiary, unclassified, residential and living-street classes,
  including their supported link classes, assume normal public motor-road use
  when no contrary or ambiguous source tag is present. Both modes are allowed.
  This assumes vehicle suitability; no specific crew or engine dimensions,
  turning radius or operational performance has been verified.
- A positive `access`, `vehicle`, `motor_vehicle` or `motorcar` tag is retained
  as source-tag evidence, not proof of legal access or vehicle suitability.
  A way with a positive general access tag still relies on assumptions for
  particular vehicles. Any explicit restrictive value on those keys or `hgv`
  is excluded, even if a more specific positive tag overrides it under the
  existing `effective_access` policy. Conditional tags are excluded.
- Untagged service-road access remains unknown. A service road may carry both
  exercise modes only with a positive access tag and after the existing
  `way_reason` filter; driveways and parking aisles remain excluded.
- A track must pass the existing grade and surface filters and have an explicit
  positive `vehicle`, `motor_vehicle` or `motorcar` tag before crew mode is
  admitted. Engine additionally requires a positive `hgv` tag. General
  `access=yes` alone leaves a track unknown. These positive tags still do not
  verify suitability.
- Any tagged gate, cattle grid or other barrier stays unknown, even when tagged
  open to traffic. Restricted or conditional node access stays unknown. A
  tagged width or incline stays unknown because the source cases do not specify
  vehicle dimensions or grade limits; rough or unspecified unpaved surfaces
  also remain unknown. The whole compact edge is withheld when any of its source
  nodes is ambiguous. The policy does not manufacture a detour around it.
- Existing `way_reason`, `effective_access` and direction rules are applied.
  Ways referenced by retrieved turn-restriction relations and arcs incident
  to via nodes are excluded in the pinned source graph and checked again here.
  Three retrieved relations have missing members; excluded known members do not
  prove that the acquisition captured every restriction. Missing OSM tags and
  the non-atomic source do not establish the absence of restrictions.

The assumption does not classify any map cell or authorize an off-network
connector. The expanded source access mask remains unknown and the existing
host rejects ground resources on unknown cells. Original scenario road-edge
whitelists refer to a different network. They cannot simply be carried over.
`RoadNetwork.path` does not check `allowed_modes`; use the existing
mode-enforcing `HazardRouter` or the diagnostic `mode_path` in the policy
module. A graph consumer must explicitly opt in and pin this exercise graph;
loading the JSON alone does not enforce opt-in or cell and hazard safety.

## Results for the pinned source

The private graph `/tmp/expanded-road-exercise-v1/network.json` is mode 0600,
under a mode-0700 directory. Its graph SHA-256 is
`91683eaa97128e7dc25383baa9a6cba92f4db628db23effcda662fb91b64fca7`;
it has 15,325 nodes and 27,284 directed edges. Of these, 17,032 edges permit
exercise crew-vehicle use and 17,008 permit exercise engine use. For 16,970
edges the ordinary public-road assumption is the access basis; 21 public-road,
17 service-road and 24 track edges have positive access tags under the rules
above. These are edge counts, including both directions where represented, not
road lengths or coverage estimates.

The original catalog has two ground-resource base positions. For each mode,
the nearest permitted network nodes are about 102.697 m and 0.033 m away. The
mode-aware graph search finds **no path in either direction between those two
nearest nodes** under this policy. The earlier source-only diagnostic found a
three-edge path, but one edge has a `width=5` tag and remains unknown because
scenario vehicle widths and clearance requirements are unspecified. This does
not establish that the physical road is impassable. Other unknown width or
incline edges can also disconnect possible alternatives.

The real post coordinates produce zero matches in `HazardRouter`'s exact road
origin test for either mode (tolerance 0.000001 m); the nearest-node comparison
therefore is not an actual route from either post. No snapping or connector is
invented. The origin check uses a synthetic all-passable raster only to inspect
geometric matching; it does not promote source cells to accessible. Resolving
these cases requires separate evidence or authorized, well-specified vehicle,
access-cell and connector assumptions, plus scenario and route validation.

## Validation and reproduction

The converter requires `--opt-in`, checks the source-only graph pin, revalidates
its full source-arc expansion, and applies the policy to each compact edge.
The exercise validator checks edge-by-edge equality with the source graph
except for the permission and basis fields, and checks mode decisions against
source tags, all segment nodes and restriction records. `RoadNetwork` validates
the resulting geometry and size limits. Tests cover private and conditional
tags, mode-specific track assumptions, barriers, widths, grade, one-way and
turn restrictions, distinct crossing nodes, mutations and full-source post
checks. The independent raw-acquisition audit described in the source-only
page should also be retained; policy validation cannot establish OSM
completeness.

For the current write-once commands and fresh output paths, see [road re-review fixes](expanded-road-review-fixes-v3.md).
