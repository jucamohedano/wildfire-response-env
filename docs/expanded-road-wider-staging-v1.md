# Wider synthetic road staging sensitivity v1

This private diagnostic revisits all twelve pinned development cases using
`eval/expanded_road_wider_staging_v1.py`. It changes only the synthetic node
selection radius: up to eight 500 m cells from the sector and six cells from
an observed fire cell, instead of four and three. These distances are exercise
assumptions, not evidence that a unit can reach a fire or perform useful work.
It does not alter the original scenarios or the original staging results.

As in the predeployment proposal, the unit is assumed already at an exact
retained source-road node before handover, without inventing a journey. The
same partial public fire/wind observation, source-node ambiguity guard, source
fuel and coarse terrain filter, directed incoming and outgoing road edges,
50 m mapped-water screen and land/raster screening apply. The retained
no-unmapped-crossings assumption applies only to screened edges. A selected
node has no verified route to the original base, work target, off-road access,
work authorization or demonstrated safety. A wider radius can make the work
reach problem worse; this is an exploration of staging availability, not a fix
for all ground operations.

The existing predeployment search bounds still apply: at most twelve ranked
nodes per case, two variants per case and 100 distinct edge screens shared
across the run. Bounded or unavailable evidence is unknown, and blocked means
only that the bounded search found no proposal. Candidate output uses a new
version and is deliberately incompatible with the old setup guard. A future
host would need a separately reviewed integration, current hazard and route
revalidation, return and work-site evidence. A self-digest is not approval.

The tool reconstructs the original catalog, fire and post drafts and checks the partial public handover reconstructed from the existing pinned private staging audit, and verifies
implementation hashes before and after the run. It writes case outcomes and
source evidence exclusively to a fresh mode-0700 directory with a mode-0600
file. Keep that output private; it is not an actor observation or a published
result. The local pinned GIS inputs are required.

```sh
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python eval/expanded_road_wider_staging_v1.py --opt-in-wider-synthetic-staging \
  --output-dir /tmp/expanded-wider-road-staging-v1-newrun
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python -m pytest tests/test_expanded_road_wider_staging_v1.py -q -p no:cacheprovider
```

See [predeployed handover v1](expanded-predeployed-handover-v1.md) and
[road staging feasibility v1](expanded-road-staging-v1.md) for source,
privacy, model and practical limitations.
