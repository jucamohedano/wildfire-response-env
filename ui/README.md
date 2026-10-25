# Wildfire Ops: local 2D control centre

A small React/TypeScript/Vite application with one OpenLayers map and four
operational tabs. It uses the verified Python simulator, not a second JavaScript
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
- **Play/step:** manual mode waits without new orders; scripted evacuation,
  shelter or passive policies can run the current episode. These are deterministic
  baselines, not language models. Clear/apply drafts before playback. Reset before
  comparing policies; switching policy in mid-episode creates a mixed-controller
  trace, not a fair standalone policy benchmark. Speed controls presentation pace,
  not physics or the model-minute conversion.
- **History:** the slider and decision list select recorded past states only.
  Past/imported frames disable all live mutations. Return to live refreshes the
  current server state; it does not rewind the simulator.
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

The default background is an offline display-only PNG rendered from the exact
pinned IGN MDT05 ASC sampled at25m, cropped with complete stencil support to the
same EPSG:25830 domain. Elevation appears only as colour and hillshade on a flat
plane. When that optional raw ASC is absent, an explicitly labelled coarse500m
domain-height image is used. Present corrupt/changed raw data fails closed; no
unknown elevations are filled. Neither image modifies physics or the input bundle.

Optional `IGNBaseSimplificado` labels/roads are requested directly from the
[official IGN service](https://www.ign.es/web/ign/portal/ide-area-nodo-ide-ign),
using the documented transparent TMS layer with a flipped tile-row convention.
Attribution is visible. These are current cartographic context, not authenticated
2021 roads, simulated routes or resource positions. Network failure affects only
that optional layer; turning it off leaves terrain and simulation fully local.
No bulk tile download, Google Maps billing, key provisioning or tile proxy is used.

Clicking a received report centres its claimed location. Pointing at a cell reads
its mean elevation/fuel class from the pinned public500m grid, never from colours
or remotely sampled map pixels. Population movement uses recorded public positions;
responders appear only when confirmed on scene. En-route units stay abstract.
The inner dashed rectangle marks the engine's two-cell censor guard, not a road,
perimeter or containment line. Outside the terrain rectangle is map context only.

## Privacy and local scope

The server serves only the built UI, one terrain image and allowlisted API routes;
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
need pinned map context or disabled live labels to avoid time-dependent map inputs.

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
