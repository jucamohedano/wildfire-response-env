"""Expand display-only road geometry from the existing pinned OSM cache."""

import json
from pathlib import Path

from eval.prepare_display_roads_v2 import build_asset, canonical, digest, read_cached_sources


ROOT = Path(__file__).resolve().parents[1]
EXTENT = (290250, 4029500, 322250, 4061500)
ASSET = ROOT / 'ui/public/assets/map-roads-expanded-v2.json'
PROVENANCE = ROOT / 'data/ui_v2/expanded_roads_provenance.json'


def prepare():
    acquisition, nodes, ways, records, duplicates = read_cached_sources(ROOT)
    asset, statistics = build_asset(nodes, ways, extent=EXTENT,
                                    max_vertices=600000, max_asset_bytes=15000000)
    encoded = canonical(asset) + b'\n'
    ASSET.parent.mkdir(parents=True, exist_ok=True)
    ASSET.write_bytes(encoded)
    provenance = dict(version='wildfire-expanded-display-roads-v2', role='display_only_not_routing',
        source='Existing pinned OSM cache; © OpenStreetMap contributors, ODbL-1.0',
        acquisition_manifest='data/coastal_mobility_v1/acquisition.json',
        acquisition_completed_at=acquisition['acquisition_completed_at'],
        source_files=len(records), duplicates=duplicates,
        extent=list(EXTENT), statistics=statistics,
        output=dict(path=str(ASSET.relative_to(ROOT)), bytes=len(encoded), sha256=digest(encoded)),
        limitations=['The existing source cache does not cover the full northern edge of the expanded terrain.',
                     'Displayed roads are cartography, not a complete or verified vehicle-routing network.',
                     'Road conditions, accessibility, closures and historical 2021 geometry are not established.'])
    PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    PROVENANCE.write_text(json.dumps(provenance, sort_keys=True, indent=2) + '\n')
    print(json.dumps(dict(output=provenance['output'], statistics=statistics), indent=2))


if __name__ == '__main__':
    prepare()
