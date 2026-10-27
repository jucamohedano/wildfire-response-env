# Wildfire Ops: local 2D control centre

A small React/TypeScript/Vite application with one OpenLayers map and four
inline operational sections. It uses the verified Python simulator, not a second JavaScript
simulation. No model, API key, GPU, live dispatch or HappyRobot account is needed.

## Build and run

From the repository root, with Python3.12+ and Node24:

```sh
python -m venv .runtime/ui-venv
uv pip install --python .runtime/ui-venv/bin/python \
  -r ui/requirements-dev.txt -e envs/wildfire_response/core
cd ui
npm ci
npm run build
cd ..
PYTHONPATH=envs/wildfire_response/core/src .runtime/ui-venv/bin/python -m eval.ui_server \
  --prepared-inputs .runtime/malaga-prepared-inputs-v1/inputs.json \
  --prepared-sha256 ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108 \
  --topographic-inputs .runtime/topographic-inputs-v1/inputs.json \
  --topographic-sha256 e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd \
  --catalog-sha256 a38970528b7119d598bee4a51038f01e9e4f6ec7948a8c7e55b3ec1bf2743444 \
  --port 8767
```

Open `http://127.0.0.1:8767/`. Restore the pinned ignored input bundles first;
their provenance and durable local backups are documented in `DATA_STORAGE.md`.
The UI's Python dependencies are isolated: it does not require OpenEnv, Gradio,
Torch, rasterio, a JavaScript CDN, or a hosted mapping SDK. For frontend iteration,
run `npm run dev` in `ui/`; Vite proxies `/api` to the local Python server.

The tested JavaScript dependency tree and direct Python dependencies are pinned
in `package-lock.json` and the UI requirements files respectively. The map uses
OpenLayers Canvas2D and Proj4 transforms, with
no globe, camera pitch, rotation, 3D terrain mesh or extrusions.

## Controls

- **Scenario:** six existing development cases and12 registered training-smoke
  cases. Load/reset starts a fresh episode; current unsaved state is in memory.
  Validation/test cases cannot be reset or imported through this interface.
- **Reports:** inspect observation/receipt times, source and location claims;
  stage clarification or evidence-backed fire/no-fire/defer triage. Claims do not
  become confirmed merely because they appear on the map.
- **Resources:** request a public target/mission; inspect base offers/readbacks,
  stage corrections, confirm and authorize together or separately, then release
  units. Readback confirmation itself does not dispatch a vehicle.
- **Orders:** issue shelter/evacuate/release through broadcast or an eligible
  on-scene warning crew. New instructions reference the latest issued sequence.
  Delivery, acknowledgement and execution are distinct. Movement starts only
  after execution. Other messages support status requests and inform-only notices.
- **Draft:** basic controls stage orders without changing time. Check validates
  the combined draft, subject to the engine's three-check budget. Apply commits
  once and advances two assumed model minutes. Advanced JSON exposes all current
  action fields, including combined and grouped triage operations.
- **Run live/step:** manual mode waits without new orders; scripted evacuation,
  shelter or passive policies can run the current episode. These are deterministic
  baselines, not language models. Clear/apply drafts before playback. Reset before
  comparing policies; switching policy in mid-episode creates a mixed-controller
  trace, not a fair standalone policy benchmark. Speed controls presentation pace,
  not physics or the model-minute conversion.
- **Full trajectory:** **Run to end** explicitly computes the remaining real
  decisions with the selected policy, stops at natural completion/truncation or
  the decision limit, and opens the finished run at its first frame. It resumes
  the existing episode rather than silently resetting it. **Stop generation**
  stops after any in-flight decision is recorded. Clear/apply drafts first;
  changing the policy mid-episode still creates a mixed-controller trace.
- **History/replay:** the thin footer inside the map component, below the image
  (never over it), combines the slider with **Play**, **Pause**, **Reset** and
  **Finish** icon buttons. Hover/focus gives full labels. Reset rewinds the
  recorded replay; it never resets the live simulation. **Play replay** animates those
  recorded frames at the selected speed, without calling the simulator. Scrubbing
  pauses replay. In-progress runs expose only recorded frames, not guessed future
  states. Past/imported frames disable live mutations and private fire truth.
  Return to live refreshes the current server state; it does not rewind the engine.
- **Map:** the app opens at a tighter mainland Spain/Balearic overview, then
  automatically fits the incident domain after a short introductory pause. Manual
  map interaction cancels the transition; reduced-motion users get no animated
  zoom. New/reset episodes fit the domain immediately. **Spain** returns to that
  overview; **Fit domain** returns to the incident. Zoom-out is limited to the
  opening country scale rather than a world view. The overview intentionally
  omits the Canaries to avoid a large Africa/ocean frame for this Malaga episode;
  they remain reachable by panning the worldwide street layer. A domain locator
  remains visible at national scale; operational markers/grid are hidden there
  to avoid overlapping labels. Simulation coverage is unchanged.
- **Fire evolution:** fresh burning observations use orange markers at their
  original cell centers, not giant filled squares or an invented continuous fire
  perimeter. Older observations
  become muted/dashed after four model minutes. The frame time and cell counts
  update with stepping, playback and history. A disappeared/stale observation
  does not establish containment, and new evidence does not establish ignition
  time. Public observations have no heat or burned-fraction measurements, so the
  UI does not invent them. Only the explicitly enabled live evaluator overlay
  colours actual simulator heat red-to-yellow and shows burned inactive cells.
  Observation markers are not a measured continuous fire perimeter. There is no cosmetic
  spread interpolation, flickering flame or continuous animation/render loop.
- **Export/open:** export a complete public canonical JSON trace, or import a
  completed `.json`/`.json.gz` trace. Imports are bounded, independently replayed
  against current caller-pinned inputs/settings, and read-only. In-progress exports
  are useful for inspection but cannot be imported as completed episodes. Private
  evaluator artifacts are not accepted as public traces.
  Browser exports and imports transfer the original JSON text unchanged,
  preserving numeric tokens such as14.0 rather than silently normalizing them
  to14 before hashing. A number-normalized old preview export is rejected; it
  must be re-exported from the original episode, not silently repaired.
- **Verify:** after completion/truncation, recompute reward and show only the
  public outcome summary. This is synthetic accounting/replay verification, not
  real-world firefighting validation or an optimality certificate.

Invalid committed actions retain engine behaviour: time advances, old missions
remain in force, errors are shown, and three consecutive invalid actions truncate
the episode. Malformed transport requests and stale episode/decision tokens are
rejected without a hidden retry or extra time step. Check requests do not advance
time, but they consume the public preview budget. Export before resetting or
stopping the server; exploratory UI runs are not automatically training examples,
held-out evaluations or entries in an experience bank.

## Terrain and map sources

### Terrain-focused control center

The map-first layout keeps incoming updates and submitted controller decisions
together in one operations panel, without Reports/Resources/Orders/History tabs.
It includes acknowledgements and causally available wind/sensor snapshots; orders
sent are distinct from confirmed delivery/execution. Repeated waits and unchanged
sensor footprints are summarized, not discarded from the underlying trajectory.
Resource/population summaries and inline sections retain every existing control.
Shortcuts only open the existing detailed sections;
allocation, readback, protection, advanced JSON and check/apply controls remain
available. Arrival stages use delivered resource status, never a guessed GPS
position. Unknown population status remains unknown rather than counted as safe.
The recorded-trajectory footer is below the image within the map component,
separate from live stepping. Run to end remains in the live simulation controls.

Three saved development trajectories informed the design: Luna's
`bench-dev-71f777cb5ff8c9f2` (4 action-bearing decisions, 56 waits; a shelter order
issued at minute 2, delivered at minute 4, executed at minute 6), Luna's
`bench-dev-d9c78dfb35a04dff` (10 action-bearing decisions, 37 incident updates),
and the same multi-fire case's passive reference (60 waits, no orders).
These are recorded synthetic actions/observations, not private reasoning traces
or new training labels. The multi-fire Luna replay was also opened through the
UI's existing independent verifier to check chronology and read-only behavior.

**Game symbols** enlarge illustrative flame, house, responder and mission-target
glyphs in screen pixels only. Source imagery, geometry, distances, wind, travel,
fire resolution and reward stay unchanged. Fire sightings, unconfirmed calls,
and mission targets differ in shape as well as color; stale sightings are grey.
Legend explanations use full time units and appear on hover, focus or touch.
**Fit activity** zooms to public groups, received reports, observed fire and known
assignments; it never reads private future events. **Fit domain** is still
available for the complete study area. Neither changes any world coordinates.

**Enhanced contrast** adjusts only the original imagery/terrain layer colors.
Turn it off to compare the
unfiltered basemap. The official tile URL, EPSG:25830 view, affine grid, geographic
extent, roads, cell centers, group/refuge coordinates and source-height samples
are unchanged. An extent/CRS mismatch withholds the overlay rather than stretching
it onto the wrong landscape. Label offsets/decluttering do not relocate markers.

**Terrain shading** adds a transparent, edge-feathered hillshade from the same
validated elevation samples (`/api/map-relief.png`, separately cached). A 2.5×
lighting exaggeration emphasizes ridges without changing heights, coordinates,
distances or physics. Flat ground is transparent; no tinted domain rectangle is
added. A missing/invalid source follows the existing coarse-fallback/fail-closed
rules, independently of the imagery. Disable shading for an exact basemap comparison.

**Fire effects** add deterministic decorative flames and wind-directed smoke
behind operational markers. They use only delivered burning observations no
older than four simulation minutes, never future or evaluator-only fire state.
Smoke needs known nonzero public wind; it is illustrative, not a dispersion
forecast. Cells retain their 500 metre resolution; no fine perimeter, measured
intensity, sub-cell fire spread or vehicle GPS route is invented. Effects stop
when playback pauses, the page/map is hidden, or reduced motion is requested.
National views and views without visible fresh sightings do not animate.
Rendering is capped at 30 requests/second, 40 visible cells and six particles
per cell, with device pixel ratio capped at 1.5. Scrubbing rebuilds effects from
the selected frame. Both toggles are display-only; reward and actions are unchanged.

Prototype validation (2026-10-27): 49 frontend and 43 Python UI/terrain tests
passed, as did the production build. CPU-only `topo-dev-000` shelter and
`bench-dev-d9c78dfb35a04dff` cooperative-reference episodes produced 18 and 61
replay frames. Browser checks covered layer toggles, scrubbing, pause, the
below-map replay footer and a narrow viewport. A 65-draw sample (zero to four
visible cells) averaged 0.31 ms in the effects drawing callback, maximum 1.70 ms;
this excludes overall frame/GPU cost and is not a 40-cell stress benchmark.
The existing large-bundle build warning remains. No paid model inference ran.

The generated design image is not used as a map or image layer. Real roads and
place names come from the source basemap. **Exercise settlement** markers retain
the synthetic scenario's original population positions: they do not identify
verified historical towns. Off-map bases remain abstract exercise desks. Source
imagery dates vary and must not be presented as a reconstructed 2021 street map.
This is a visual redesign, not a finer fire model, new routing or additional data.

Validation: `npm test` covers source-coordinate alignment, unchanged marker
geometry, public/evaluator separation, delivered-only summaries and unknown
population state. `npm run build` checks the full UI. Existing Python transport
and terrain tests cover core-action parity, clocks, checks, stale requests,
read-only exports and pinned 25 m terrain crops; run those with the UI optional
dependencies installed. Browser checks additionally cover draft/check/apply,
authorization, replay, quick-action navigation and a 390 px mobile layout.

The terrain overlay is an offline display-only PNG rendered from the exact
pinned IGN MDT05 ASC sampled at25m, cropped with complete stencil support to the
same EPSG:25830 domain. Elevation appears only as colour and hillshade on a flat
plane. When that optional raw ASC is absent, an explicitly labelled coarse500m
domain-height image is used. Present corrupt/changed raw data fails closed; no
unknown elevations are filled. Neither image modifies physics or the input bundle.

The **Map style** selector defaults to aerial imagery from the official
[IGN PNOA/Sentinel-2 service](https://pnoa.ign.es/web/portal/pnoa-imagen/visualizadores-y-servicios-web).
It uses Sentinel-2 at small scales and PNOA aerial orthophotos at larger scales,
up to tile level19. Source dates and resolution vary; these are not live images,
not a verified2021 RGB mosaic and not new simulator measurements. The local
rasters checked for this update are elevation/fuel/mask products, not such an RGB
mosaic. No fine-scale flames or temperatures are inferred from photographic
colour. The fire/sensor grid stays500m and the model-facing observation is still
structured JSON, not a satellite screenshot.

**Street map** remains available as OpenStreetMap's familiar worldwide raster
style, not Google's proprietary tiles or the dense IGN chart. Interface labels
are English; place names retain the provider's local names. **Basemap** toggles
remote layers; disabling it shows local terrain. The cropped local elevation PNG
is never blended over imagery or streets: that made the study area appear as a
tinted rectangular patch around the settlements. It is shown only as a standalone
local elevation view when the basemap is off. The optional transparent hillshade
is a separate neutral shadow/highlight layer, not this colored crop. No source elevations are changed.
The map view uses native
EPSG:25830, with rotation disabled, so domain/fire/grid edges meet at90degrees;
the basemap is reprojected to the terrain, not the reverse. Grid north is not
exactly geographic true north. The national view is cartographic context, not a
geodetic measurement outside this projection's area of use.

Attribution is always visible. Streets are current cartographic context, not
authenticated2021 roads, simulated routes or resource positions. The browser
requests only visible tiles and uses normal HTTP caching; no tile proxy, bulk
download, offline map archive, training-image harvesting, key or paid Google SDK
is used. This local interactive demo must follow the
[OSMF tile policy](https://operations.osmfoundation.org/policies/tiles/).
Use an appropriately licensed/self-hosted provider for scaled deployments or
offline cartography; the community service is best-effort. Turning streets off
leaves source-derived terrain and simulation local. Terrain hillshade retains
higher display contrast. Display filters do not apply to operational overlays
or alter simulation physics.

The **Map key** uses SVG examples of the actual markers and short labels,
with hover/focus explanations and an expandable display-assumptions guide. It leaves artificial
simulation rectangles off the map; the automatic zoom shows the study area.
Residents, evacuation destinations, exposed people and responders have separate
entries. Recent sightings and older observations remain distinct. An empty
sensor record does not certify no fire. Counts refer to grid cells, not
independent incidents. The raw cell/elevation/fuel readout and source details
are under a collapsed **Map data and technical details** disclosure. The edge
buffer remains an uncertainty rule, not a visible firebreak. The explicit
evaluator key is separate and never labels private simulated heat as public
observations.

Clicking a received report centres its claimed location. Pointing at a cell reads
its mean elevation/fuel class from the pinned public500m grid, never from colours
or remotely sampled map pixels. Population movement uses recorded public positions;
responders appear as green markers when on scene. Mobilizing or en-route units
show an amber ring at their assigned target, with an ETA when available; this is
not the vehicle's current position or a road route. The Resources panel tracks
Reserved → Mobilizing → En route → On scene and shows the estimated arrival time.
An ETA that has passed remains pending until a status update confirms arrival.
Roads and the simulation edge remain context only.

## Privacy and local scope

The server serves only the built UI, terrain/transparent-relief images and allowlisted API routes;
it does not expose repository files, `.runtime`, scenario factors, contacts or
private scoring checkpoints. Public observations/exports remain separate from
evaluator-only fire truth. To enable the optional operator debug overlay, restart
with `--enable-evaluator`; its checkbox is off by default. Without the flag, the
truth endpoint returns404. It is deliberately unavailable in historical/imported
views, because current private state is not historical evidence.

This is a loopback-only, single-session research app, not an authenticated
multi-user production service. Host/Origin/body bounds and stale-token checks
reduce accidental cross-site/stale operations; they are not a security boundary
between untrusted local users. Do not expose it publicly. For agent-facing use,
omit the evaluator flag, whitelist only public operations, and keep the agent away
from the evaluator configuration/filesystem. Visual-policy experiments would also
need pinned map context or disabled live streets to avoid time-dependent map inputs.

## Tests

```sh
cd ui && npm test && npm run build && cd ..
PYTHONPATH=envs/wildfire_response/core/src .runtime/ui-venv/bin/python \
  -m unittest tests.test_ui_server tests.test_ui_terrain -v
```

Backend tests compare live actions to direct core calls, test readback/action
fields, reset/stale request isolation, import and export, private-state separation,
body/Origin guards and default truth denial. Terrain tests verify geometry, crop,
analytical shading, deterministic PNG, finite supported data and labelled fallback.
The physics, reward and existing trajectory digests are unchanged by this UI.

The recorded first validation passed38 isolated Python UI/terrain tests and7
frontend tests, plus282 core and239 root regression methods (optional skips are
reported separately). Browser checks exercised manual dispatch/readbacks and the
protection lifecycle, a complete two-fire scripted episode with verified score1,
read-only history/import, malformed drafts, numeric-token-preserving archive
transport, and narrow/desktop layouts. See `../data/ui_v1/validation.json` for the
source/artifact hashes and exact scope; passing UI checks is not physical validation.
