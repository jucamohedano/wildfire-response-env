"""Prepare display-only terrain from the pinned expanded IGN WCS sample."""

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.runtime/display-terrain-expanded-v2/ign-mdt05-expanded-25m.asc'
ASSET = ROOT / 'ui/public/assets/map-mesh-expanded-v2.json'
PROVENANCE = ROOT / 'data/ui_v2/expanded_terrain_provenance.json'
SOURCE_SHA256 = '3a17740cbfd1d40ef2194ee684a9b2cd23d0caa2876f47ab935c5389c1d506aa'
SOURCE_URL = ('https://servicios.idee.es/wcs-inspire/mdt?SERVICE=WCS&VERSION=1.0.0'
              '&REQUEST=GetCoverage&COVERAGE=Elevacion25830_5&CRS=EPSG%3A25830'
              '&RESPONSE_CRS=EPSG%3A25830&BBOX=290225%2C4029475%2C322275%2C4061525'
              '&RESX=25&RESY=25&FORMAT=ArcGrid&INTERPOLATION=nearest+neighbor')
EXTENT = [290250, 4029500, 322250, 4061500]
SIMULATION_EXTENT = [297000, 4036500, 315500, 4054500]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare():
    raw = SOURCE.read_bytes()
    if len(raw) > 20_000_000 or digest(raw) != SOURCE_SHA256:
        raise ValueError('Expanded IGN sample does not match its acquisition pin')
    lines = raw.decode('ascii').splitlines()
    if len(lines) != 1287:
        raise ValueError('Unexpected ASC row count')
    header = dict(line.split() for line in lines[:5])
    if (int(header['ncols']) != 1282 or int(header['nrows']) != 1282
            or abs(float(header['xllcorner']) - 290225) > .01
            or abs(float(header['yllcorner']) - 4029475) > .01
            or float(header['cellsize']) != 25):
        raise ValueError('Expanded IGN grid does not match the requested registration')
    source = []
    for line in lines[5:]:
        row = [float(value) for value in line.split()]
        if len(row) != 1282 or any(not math.isfinite(value) or value > 5000 for value in row):
            raise ValueError('Invalid expanded elevation row')
        source.append(row)
    heights, support, supported = [], [], 0
    for row in range(641):
        upper, lower = source[row * 2], source[row * 2 + 1]
        height_row, mask_row = [], []
        for column in range(641):
            left = column * 2
            samples = (upper[left], upper[left + 1], lower[left], lower[left + 1])
            valid = all(sample > 0 for sample in samples)
            height_row.append(round(sum(samples) / 4, 3) if valid else 0)
            mask_row.append('1' if valid else '0')
            supported += valid
        heights.append(height_row)
        support.append(''.join(mask_row))
    for row in range(140, 501):
        if '0' in support[row][135:506]:
            raise ValueError('Expanded source cannot support the full simulation footprint')
    mesh = dict(version='wildfire-display-mesh-v2', role='display_only', crs='EPSG:25830',
        extent=EXTENT, simulation_extent=SIMULATION_EXTENT, columns=641, rows=641,
        eastings=[EXTENT[0] + column * 50 for column in range(641)],
        northings=[EXTENT[3] - row * 50 for row in range(641)],
        heights_metres=heights, support_mask=support, source_sha256=SOURCE_SHA256,
        source_label='IGN MDT05, 25 m ASC sampled to a 50 m display mesh; nonpositive samples masked')
    encoded = (json.dumps(mesh, separators=(',', ':'), allow_nan=False) + '\n').encode()
    ASSET.parent.mkdir(parents=True, exist_ok=True)
    ASSET.write_bytes(encoded)
    provenance = dict(version='wildfire-expanded-display-terrain-v2', role='display_only',
        prepared_at_utc=datetime.now(timezone.utc).isoformat(),
        source=dict(provider='Instituto Geográfico Nacional, MDT05 WCS', url=SOURCE_URL,
                    local_path=str(SOURCE.relative_to(ROOT)), sha256=SOURCE_SHA256,
                    bytes=len(raw), crs='EPSG:25830', spacing_metres=25,
                    note='ASC response declares no NoData value; acquisition is not an independent survey.'),
        display=dict(extent=EXTENT, simulation_extent=SIMULATION_EXTENT,
                     area_ratio=round(32000 * 32000 / (18500 * 18000), 6),
                     columns=641, rows=641, spacing_metres=50,
                     supported_vertices=supported, unsupported_vertices=641 * 641 - supported,
                     method='Each valid vertex averages its four adjacent source cell centres. No nonpositive source value is used as an elevation.',
                     mask='Any stencil with a nonpositive value is unsupported; zero is a transport placeholder, not a measured height. Underlying flat imagery is context only.',
                     output_path=str(ASSET.relative_to(ROOT)), output_sha256=digest(encoded), bytes=len(encoded)),
        limitations=['The larger WCS request differs from the previous pinned 25 m sample on overlapping pixels; it is display-only and does not replace simulator terrain.',
                     'Nonpositive values can be water, legitimate near-sea-level land or missing data; no class is inferred.',
                     'Source edition, capture date and precise vertical realization are not established.',
                     'The expanded area is not a simulated fire domain, routing network or real-time observation.'])
    PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    PROVENANCE.write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
    print(json.dumps(provenance['display'], indent=2))


if __name__ == '__main__':
    prepare()
