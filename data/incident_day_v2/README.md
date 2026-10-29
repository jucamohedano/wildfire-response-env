# Incident-day v2 data and quality gate

Prepared inputs and active development tasks can be [downloaded from Hugging Face](../../docs/hub-runtime.md)
without rebuilding map data. The release keeps all source-estimate and lifecycle guards.

This is an ordinary one-minute, 1,440-minute synthetic exercise profile, not a
historical reconstruction or an operational rulebook. The finite roster,
capacities, landloaded fixed-wing allocation, communication delays and 240-minute
work / 60-minute rest default are exercise assumptions, not verified deployment,
labour law or duty evidence. The day compiler must choose explicit finite values
and model arrivals and setup. There is no minimum-fire-area aircraft rule.

## Fuel evidence and approval

The pinned prepared input `ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108`
and topographic input `e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd`
crop to 37 by 36 cells. All 1,332 heights have support, while 80 study cells
have unknown source fuel. Comparison against the pinned landscape rasters shows
78 unknowns inside the EFFIS source footprint and two outside it. Their class
and validity rasters both contain zero; the EFFIS source explicitly declares zero
NoData. It does not establish water, bare ground, nonburnability or a fuel class.
Sampling the original native raster at the transformed 80 cell centres confirms
78 masked source pixels, two outside the raster and zero valid samples.
The retained source raster SHA-256 is
`4cc9b17c7f738af14d1f12302b4e8c9f958608023c600d8668864954d95cd308`;
the prepared class, validity and coverage rasters have hashes
`035f7a3ac07db866dc34b4c55778891055d4b03a790268dda77362c1e1ed400c`,
`44368e111a336e03211f754774fcb1b65396803aab0d85f772da7a8a7a8fef77`, and
`ff67e5d38a9f3765fd1fa1b77e82a1b27d8fda11da308ecb7ec3d62d5e903dd9`.

The official CORINE 2018 product has 44 land-cover classes, a 100 m raster and
25 ha minimum mapping unit. Its categories are not NFFL fuel values; without an
audited, locally applicable fuel mapping and independent coverage, they cannot
repair the 80 values. No replacement data was acquired or asserted. The EFFIS
fuel observation epoch and 2021 applicability remain unverified as well.
**Real source-pack approval is blocked.** An opt-in synthetic class can make a
prototype runnable while preserving all 80 false known-mask cells; it is never
source resolution or approval. Unknown fuel must never become a firebreak.

`prepare_inputs(prepared, domain, network_document, *, fuel_resolution=None)`
checks document hashes, parent linkage, crop alignment, complete heights and
source masks. It returns a pack with a canonical SHA-256 excluding its own
`sha256` field. `load_inputs(value, expected_sha256, *, allow_unapproved=False)`
requires a caller-held pin and rejects unapproved packs by default. Its opt-in is
for prototype use only. A complete explicitly synthetic fixture is accepted for
core tests; that approval does not confer real-world source approval. The compiler
CLI requires separate caller-held input pins and creates a fresh output only.
Pack pins establish identity, not the scientific validity of upstream data.

The coastal road graph is partial to the east; only 62 grid centres are truck
accessible under the existing access rule. Missing road coverage is not evidence
of a traversable footpath. Road data and operations parameters remain synthetic or
temporally mismatched, as documented in `data/coastal_mobility_v1/README.md`.

## Structural review disposition

The prior audit identified 32 flagged variants in 16 groups. This is a structural
disposition, not fabricated individual human review: retain all old IDs and
splits unchanged. For the 28 wind-shift variants, a future adaptation exercise
must place at least one meaningful shift after ignition, or explicitly designate
the case as a pre-ignition weather exercise. For the four initial refuge fires,
future cases need an explicit unsafe-refuge objective and viable alternatives,
or a separately versioned redesign. These warnings do not prove infeasibility.
No cases are automatically deleted. Per-case evaluator labels and curation
records remain private and must not enter actor observations or public packs.

The additive `incident-day-egress-v1-*` fixtures pair the finite-shift seeds and
initial state with an explicit return-only protective-buffer exit assumption.
Their scenario/profile identities differ; existing scenario IDs and hashes are
unchanged. This is a mechanics comparison, not new historical evidence or a
larger held-out dataset. See `docs/incident-day.md` for the constraints and command.

Two further additive IDs, `incident-day-coverage-v1-002` and `004`, provide direct
access/relocation evidence. The access case retains the paired restriction and
records actual loss of engine approaches plus crew work. The destination case
declares an initial cohort at the centre that will close, rather than closing an
unused facility. Its occupancy and prior arrival are synthetic handover
assumptions. No new historical data or expert trajectories are implied.

`eval/review_incident_day_variants.py` materializes and inspects every flagged
variant from the existing pinned catalog. The first review covered all32 cases:
28 remain frozen pre-ignition-weather exercises, explicitly not post-ignition
adaptation evidence; four require destination/relocation redesign before day
training. No old scenario was changed or silently copied into day mode. The
private curation digest was
`9f7df7e0f715b226206581ab7ce4f403f3ec9b4b8b9d89e667c10d2472494aae`.
This is a structural per-case review, not operational expert approval or a
held-out policy evaluation.
