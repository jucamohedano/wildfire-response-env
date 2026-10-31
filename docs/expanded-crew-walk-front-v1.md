# Synthetic crew walk to an observed front v1

`eval/expanded_crew_walk_front_v1.py` examines the crew variants in the pinned
private wider-staging run. The output is a private, separately versioned
exercise hypothesis; it does not change scenarios, the host, the original
staging candidates, or any road. Engine variants are explicitly ineligible for
this walking profile. Two alternatives from the same case are not extra units.

The search uses only the pinned *partial public handover* fire and wind. It
excludes observed burning cells and immediately downwind neighboring cells. It
allows an adjacent upwind or lateral cell, and treats a point 10 m inside that
cell, opposite an observed burning cell's shared face, as a hypothetical
front approach. **This is much weaker than the existing road router's full
one-cell fire buffer** and is an explicit synthetic exercise assumption, not a
safety assessment. No complete fire, current live observations, fire growth,
future wind or ability to escape a changing fire is established. A later host
would need its own trusted current observations and safety policy; this profile
is not compatible with the existing buffer.

Walking uses four-connected cell-center segments and a final segment within
one cell, from the exact staged node, for at most 2,000 m one way. Every
traversed cell must be explicit v2 land, with source fuel, finite coarse
terrain and no v4 additional censor. Mixed v4 cells are excluded even when
otherwise eligible for road staging. Consecutive cell-center elevations must
have absolute average grade at most 0.30. The coarse 500 m elevations do not
measure local slope, cliffs, fences, vegetation or passability; the initial and
final within-cell segments have no measured local grade. Straight connectors,
a walkable land cell, the 30% threshold and the 2 km bound are synthetic
assumptions, not observed paths or walking-speed evidence.

A 5 m corridor around every nonzero walk segment must be inside the intersected
OSM/DERA land face and must not intersect either pinned mapped-water layer. It
is an uncalibrated exclusion filter; unmapped or intermittent water, bridges,
flood, stream conditions, permissions and land-cover errors remain unknown.
No water crossing is assumed and no road or walking trail is invented. The
profile does not sample WorldCover along the walk. Return along the walk must
be revalidated against current observations, and the road return to base is
unknown. A site and a front point are planning geometry, not historical or
field-verified work locations; work authorization remains absent.

An exhaustive search within the stated distance and rasterized profile without
an accepted site is `no_hypothesis_in_profile`, not proof of impassability.
Unavailable water evidence is `unknown`. The parent staging search was bounded,
so these results do not cover all possible staging nodes. Private per-case
results, geometries, and source identifiers must not be published or served.
The output is a fresh mode-0700 direct child of `/tmp` containing a mode-0600
file. Run with one CPU; local pinned private inputs are required.

```sh
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python eval/expanded_crew_walk_front_v1.py --opt-in-synthetic-front-walk \
  --output-dir /tmp/expanded-crew-walk-front-v1-newrun
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python -m pytest tests/test_expanded_crew_walk_front_v1.py -q -p no:cacheprovider
```
