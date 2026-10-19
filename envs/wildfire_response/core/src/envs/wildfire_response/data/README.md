# Spain 2026: official-source wildfire evidence

Reviewed on 18 October 2026. `spain_2026.json` contains manually reviewed facts
from official government reports, with field-level source references. The
collector downloads public Copernicus Emergency Management Service metadata.
It needs only Python's standard library and no account or API key.

```sh
python -m envs.wildfire_response.data.collect --output .runtime/spain-fires-2026
python -m envs.wildfire_response.data.collect --output .runtime/spain-fires-2026 --offline
python -m unittest envs.wildfire_response.tests.test_real_data
```

## Initial cases

- **Los Gallardos, Almería:** 9 July 2026; CEMS EMSR892, AOI 1.
  The [EMA 112 retrospective chronology](https://www.juntadeandalucia.es/presidencia/portavoz/emergencias112/217472/incendioLosGallardos/emergencia/fasedepreemergencia/situacionoperativa0)
  supplies the first-call and emergency-level transition times.
- **Villanueva de los Castillejos, Huelva:** 8 June 2026; CEMS EMSR881, AOI 1,
  named Gibraleon. The [EMA 112 report](https://www.juntadeandalucia.es/presidencia/portavoz/emergencias112/216605/Emergencias/112/Andalucia/Huelva)
  supplies a response timeline and a deployed-resource snapshot. A later
  [BOJA assessment](https://www.juntadeandalucia.es/boja/2026/128/3) provides the
  affected municipality list and a regional area estimate.
- **Sierra Oeste / Villa del Prado, Madrid:** regional July 2026 complex;
  CEMS EMSR900 AOIs 2, 5 and 6 only. The [regional recovery report](https://www.comunidad.madrid/noticias/2026/07/29/diaz-ayuso-avanza-plan-recuperacion-paisaje-17-municipios-afectados-incendios-sierra-oeste)
  and [national de-escalation statement](https://www.lamoncloa.gob.es/presidente/actividades/Paginas/2026/300726-sanchez-pma-navaluenga-.aspx)
  supply administrative context. This complex is not assigned an invented
  single ignition time. EMSR900 includes Segovia and other regions: its
  activation-wide centroid and eventTime cannot identify Madrid's ignition.

## What has been collected

The initial live fetch produced three incident records, five selected mapping
areas and thirteen product metadata records. `raw/` keeps original API JSON;
`provenance.json` records URLs, retrieval time, byte counts and SHA-256 digests.
`incidents.json` links curated facts to providers; `products.json` retains
individual versions, image times, delivery times, statistics and download links.
`mapping_coverage.geojson` contains the five AOI polygons.

AOI polygons describe mapping coverage, **not observed fire boundaries**. Neither
activation centroids nor municipal names are exact ignition coordinates.
Product statistics are separate estimates for different observation times,
product types and scopes; never sum them as if they were disjoint fires.
The Madrid AOI list is explicitly filtered and checked against expected names.
The collector rejects another year, another country or unexpected AOI identity.

The [official API documentation](https://mapping.emergency.copernicus.eu/about/how-to-harvest-cems-mapping-data/emergency-response-data/)
describes the source endpoint. A published web-news date can disagree with an
activation's eventTime; source-specific dates are preserved, not silently merged.

## Availability and simulation boundaries

Official retrospective reports are evaluator evidence. A first-call timestamp
described several days later is not a recovered call transcript. All imported
facts are marked `actor_input_eligible: false`. `facts_as_of()` checks a
conservative end-of-publication-day policy, but does not certify that today's
page contents were unchanged on that historical day. Archived releases or
independent publication timestamps are needed for actual historical actor inputs.

CEMS timestamps lacking offsets are preserved verbatim. Image acquisition and
product delivery are separate; do not give an agent a final perimeter at image
acquisition time. A later deployment snapshot is not initially available fleet
inventory, and an affected-perimeter area is not automatically burned vegetation.

Still missing: downloaded fire-perimeter vectors, local historical weather,
verified road/settlement geometry, initial inventories and actual communications.
The initial pack was extended with selected vector products below. Historical
weather and regional scenario construction remain outstanding. Reuse is subject to each
provider's terms; this pack asserts no new blanket license over source material.

The existing 120 Brunete training tasks remain synthetic. This evidence pack
does not rename those tasks as historical replays or project another province
onto the Brunete grid. Later report noise, fleet assumptions and counterfactual
actions must retain their synthetic/derived labels.

## Expanded collection: 18 October 2026

Three parallel source reviews produced `research_andalucia.json`,
`research_regions.json` and `research_geodata.json`. These retain official URLs,
page-body verification, snapshot hashes, access failures and unresolved rights.
Search snippets were discovery aids, not admitted evidence.

The local bundle is `.runtime/spain-wildfire-dataset-2026-10-18/` (about 50 MiB):

- `national_large_fires.jsonl`: **47 provisional MITECO large-fire rows** in
  **11 autonomous communities and 23 provinces**. This is not the national census
  of all fires: the dashboard also reports smaller incidents in aggregates.
  The embedded dashboard was generated on 16 October from an advance dated
  4 October; these dates must remain distinct. Forest area is not automatically
  the same metric as total burned or affected perimeter area.
- `incidents.jsonl` and `facts.jsonl`: **7 reviewed incident records and 41 distinct
  field/value combinations**, with references and qualifiers. Existing cases are
  joined by Niebla, El Cerro de Andévalo, Las Peñas de Riglos and Sierra de Algairén.
  Two inaccessible Galicia pages are leads only, with no admitted incident facts.
- `activations.jsonl` and `products.jsonl`: **15 provider-reported CEMS activation
  candidates, 23 AOIs and 70 product metadata records**. Every page of the public
  Rapid Mapping inventory was scanned. That inventory is not a complete Spanish
  fire register; one activation can cover several fires or include Portugal.
- `layers.jsonl`: **18 actual observed-event polygon layers, 4,318 features**,
  spanning 12 activation codes. These are not AOI outlines. Dates and versions
  overlap; feature counts are not incident counts and areas must not be summed.
  Sampling is round-robin by activation, capped at 28 attempts and 10 MB per
  response; ten attempts did not yield included layers. No large raster archive
  was fetched.
- `evidence/` and `manifest.json`: copied local inputs, original-to-bundle path
  mapping and SHA256 digests. The national rows, narrative cases and activations
  overlap: **do not add their counts together**. No automatic spatial/name join
  is asserted between these three populations.

**Quarantine:** EMSR932's API reports September 2026 while its official news body
says September 2025. Both are preserved; the activation remains a candidate with
`quarantined_date_conflict` status and its vectors were not imported. Other
candidate dates are provider-reported, not independently certified for every AOI.

**Other source findings:** EFFIS service schemas and blank WMS samples were
retrieved, but no nonempty 2026 EFFIS features were verified. IGN supplied one small
terrain raster: a terrain reference, not a 2026 observation.
IGN's service metadata has inconsistent range units; elevation values, nodata,
vertical reference and acquisition date still require verification before use.
SIOSE's inspected
land-cover vintage is 2017, not 2026. AEMET historical daily data requires a free
API key; **no weather observations were downloaded**. Final EGIF records are
distinct from provisional MITECO advances. Details and exact sample URLs are in
`research_geodata.json`.

### Reproduce discovery and assembly

From the repository root, with no API key or GPU:

```sh
python -m envs.wildfire_response.data.discover \
  --output .runtime/spain-cems-2026-balanced --max-layers 28
python -m envs.wildfire_response.data.discover \
  --output .runtime/spain-cems-2026-balanced --max-layers 28 --offline
python -m envs.wildfire_response.data.miteco \
  --snapshot .runtime/spain-research-geodata/miteco_estimate.html
python -m envs.wildfire_response.data.assemble \
  --output .runtime/spain-wildfire-dataset-next \
  --inventory .runtime/spain-cems-2026-balanced \
  --national .runtime/spain-miteco-2026 \
  --evidence envs/wildfire_response/data/spain_2026.json \
    envs/wildfire_response/data/research_andalucia.json \
    envs/wildfire_response/data/research_regions.json \
  --attachment envs/wildfire_response/data/research_geodata.json \
  --raw-root .runtime/spain-research-andalucia \
  --raw-root .runtime/spain-research-regions \
  --raw-root .runtime/spain-research-geodata \
  --raw-root .runtime/spain-cems-inventory
python -m unittest envs.wildfire_response.tests.test_real_data \
  envs.wildfire_response.tests.test_discovery \
  envs.wildfire_response.tests.test_assembly \
  envs.wildfire_response.tests.test_miteco
```

The curated research snapshots are local inputs, not automatically recrawled by
these commands. Their URLs and hashes are recorded in the research JSON files.
The assembler requires a new output directory to preserve collection versions.
`--reuse-cache` can resume discovery without refetching saved files; cached bytes
retain their hash but assembly time does not certify their original retrieval time.
Offline rebuilds report unavailable layers as cache misses and do not recover
the original network error. Original failed attempts included oversized responses.

Raw downloads and bundles live in ignored `.runtime/`, so a Git commit alone
would not back them up. Preserve the local bundle before cleaning that directory.
No data has been published. Current pages do not certify their historical contents;
all consolidated facts and imported layers remain evaluator-only. Licensing,
incident grouping, temporal alignment and scenario generation must be resolved
before freezing training splits or claiming historical replay fidelity.
