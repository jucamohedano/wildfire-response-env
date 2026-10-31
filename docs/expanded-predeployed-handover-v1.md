# Synthetic predeployed handover proposals v1

This private, separately versioned development tool asks whether an existing
finite synthetic ground unit could be **assumed already predeployed** at an
exact retained source-road node at the unchanged v4 fire handover. This is a
scenario assumption, not a historical observation, expert trajectory, dispatch
route, calibrated access or work-site safety finding. The original case and
draft IDs, results, weather, fire geometry and outputs are not altered. Each
proposed alternative has a new versioned identity and references its original
case, fire draft, ground-post draft and actual reconstructed public handover.

The tool reconstructs and pins the original catalog, full source/graph pair,
original same-node post anchors, v4 warmup and partial public fire/wind handover.
It verifies that handover against the pinned prior staging audit. It does not
use the private complete burning set or future weather to choose a node. A node
must pass the existing bounded sector/public-fire proximity and observed-wind
filter, source-node origin ambiguity guard, v2/v4 fuel and coarse terrain
requirements, and have both an incoming and an outgoing permitted source edge
that passes the unchanged 50 m OSM/DERA mapped-water screen, land/coast/raster
corroboration and the public fire/wind line guard. This retains the previously
authorized low-confidence exact-edge no-unmapped-crossing assumption; it does
not turn an entire cell into land access or create a walking connector.

The selected unit is an initially available original synthetic engine or crew
with finite positive work time, finite remaining duty and appropriate finite
water. Its original base remains unchanged. Its alternative initial position is
exactly the retained source node: the prehandover trip, departure time and
return-to-base route are explicitly unknown, and no travel leg is fabricated.
Alternatives are mutually exclusive proposals, not additional units. Fire,
flood, legal access, water/terrain observations, road condition and 2021 status
are not established by the partial public handover. A node outside that public
fire/wind buffer is **not** a finding that it is safe in the complete fire.

Each proposal begins at handover in `setup_pending` with a five-minute
synthetic setup interval matching the existing ground setup profile. Work
authorization and work target remain unknown: completing setup does not itself
permit work. `SetupGuard` is a test-only, opt-in mechanism for exact stationary
node initialization and revalidation against caller-supplied current fire,
wind and exclusions on every minute. It can neither move nor apply work. Its
self-digest is an integrity check, not an independent review pin or an
operational authorization.

This is **not integrated into the existing host**. The fixture host rejects
source drafts and requires ground resources at base at handover; it also
requires independently supported work-site declarations. The underlying
`Operations` object transitions `on_scene` to `working` automatically when
setup is due. A new reviewed host must explicitly support this versioned
predeployment initialization without claiming a prior journey, separately
bind a trusted candidate and road/water evidence, validate actual current
fire/wind and safe return, prevent any work until setup and an independently
supported work site/target are authorized, and preserve movement and accounting
guards. A source-road node alone supplies none of those missing permissions.

The search considers at most 12 ranked nodes per case, emits at most two
mutually exclusive alternatives per case and screens at most 100 edges across
the run. Incomplete searches are marked unknown; a blocked case means only
that no hypothesis was found within this scope. Eligible nodes also remain
bounded hypotheses, not demonstrated usable simulator operations. The private
summary compares each result with its same-case original staging status; the
two searches answer different questions and their statuses are not a score. Candidate
counts, original case IDs, nodes, source-edge evidence and per-case reasons stay
in a private mode-0600 artifact in a mode-0700 `/tmp` directory. They must not
be committed, served, published or exposed to an actor; stdout reports
completion only. The tool uses a single CPU and does not replace prior outputs.

```sh
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/expanded_predeployed_handover_v1.py \
  --opt-in-synthetic-predeployment --output-dir /tmp/expanded-predeployed-handover-v1-newrun
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF \
  python -m pytest tests/test_expanded_predeployed_handover_v1.py -q -p no:cacheprovider
```

Tests exercise exact-node initialization, unchanged base, finite budget,
50 m screening, incomplete evidence, changed current hazards and the actual
`Operations` setup timer with a rejecting work callback. They do not establish
host integration, a return route or real fireline access. See
[staging feasibility](expanded-road-staging-v1.md) for mapped-source limits.
