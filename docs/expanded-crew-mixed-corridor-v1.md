# Synthetic crew walking corridors through mixed cells v1

This separate, opt-in diagnostic examines every retained staging variant from
[the wider staging search](expanded-road-wider-staging-v1.md). It changes no
prior scenario, access mask, road, host or bridge. A v4 mixed cell with source
fuel, finite pinned coarse terrain and no additional v4 censor may be considered
**only for the exact proposed walking corridor**. This is a domain-wide
synthetic assumption; it does not classify the whole cell as walkable or prove
any field access. Original v2 source-fuel land cells use the same corridor rule.

The unchanged synthetic fire profile uses the partial public handover fire and
wind, excludes observed burning and immediately downwind cells, and permits an
upwind or lateral adjacent front. This is weaker than the existing router's
one-cell buffer. Four-connected segments retain the existing 2 km one-way limit,
30% coarse center-to-center grade limit and 10 m front standoff. The exact
centerline's supercover, including grid boundaries and endpoints, must remain in
supported cells and outside the observed hazard guard. Initial and final
within-cell grades are not measured; coarse elevation cannot establish local
terrain or passability.

Each proposed segment, including a zero-length point, has a 5 m round-ended
corridor. It must lie inside the pinned OSM/DERA corroborated land face and
avoid both complete pinned mapped-water layers. An intersection, invalid water
geometry, missing layer or uncertain land face is **unknown**, not a proven
impassable crossing. It is excluded from the proposed route and causes an
otherwise unsuccessful search to report `search_incomplete_unknown`. A found
route can coexist with unknown rejected alternatives. The buffer is an
uncalibrated exercise filter; it does not measure flooding, road condition,
legal access, local slope, unmapped water or actual safety. No bridge, ford,
water crossing, road, footpath or historical journey is invented.

All retained crew and engine nodes receive a geometry search. Engine records
are explicitly `geometry_only_engine_cannot_walk`: they are not crew
predeployments and do not authorize engine walking. A separate inventory lists
original synthetic crew resources whose `available_at` is after handover and
whose reported positive work time and duty endpoint extend beyond availability.
This is not a later dispatch or a readiness claim: position at availability,
travel to staging, current fire/wind, recovery and work authorization remain
unknown. Candidate variants from a case are alternatives, not additional
units. The staging search itself was bounded, so this profile is not a complete
search of all possible road nodes.

Candidate evidence, case-level statuses, nodes and geometry remain private.
The tool verifies the pinned staging candidate, public handover audit, post
catalog and v2/v4 inputs. It records implementation hashes, verifies they do
not change during the run, binds to one CPU and writes a fresh mode-0600 file
inside a mode-0700 dedicated `/tmp` directory. Its self-hash is integrity, not
independent approval. A future reviewed host would need its own current-state
checks, supported work-site policy, walk and road return, resource accounting,
and explicitly versioned integration. This output is incompatible with the
frozen walking/hold bridge and provides no authorization for that bridge.

```sh
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python eval/expanded_crew_mixed_corridor_v1.py --opt-in-synthetic-mixed-corridors \
  --output-dir /tmp/expanded-crew-mixed-corridor-v1-newrun
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python -m pytest tests/test_expanded_crew_mixed_corridor_v1.py \
  tests/test_expanded_crew_walk_front_v1.py -q -p no:cacheprovider
```
