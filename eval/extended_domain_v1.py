"""Derive a buffered candidate domain from pinned MITECO protected sites.

Protected-area geometry is a reproducible reference, not proof that it equals
the geographical massif. The resulting rectangle alone is not a runnable map.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

VERSION = 'wildfire-extended-domain-v1'
SOURCE_URL = ('https://www.miteco.gob.es/content/dam/miteco/es/biodiversidad/'
              'servicios/banco-datos-naturaleza/3-rn2000/n2000_2025_shp.zip')
SOURCE_SHA256 = '0b35e4f6ec7ab198f51cd66705a090a496d5b98f9203c825b499a9f8ebbb2cbd'
MEMBER = 'n2000_spatial_es_pibal_proy_end25.shp'
SITES = ('ES6170004', 'ES6170010')
PARENT_BOUNDS = (297000, 4036500, 315500, 4054500)


def domain_from_bounds(bounds, *, parent=PARENT_BOUNDS, buffer_m=5000, cell_m=500):
    if (len(bounds) != 4 or len(parent) != 4 or any(type(x) not in (int, float)
            or not math.isfinite(x) for x in (*bounds, *parent))
            or type(buffer_m) is not int or buffer_m < 0 or type(cell_m) is not int or cell_m <= 0
            or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]
            or parent[0] >= parent[2] or parent[1] >= parent[3]):
        raise ValueError('Invalid domain bounds or buffer')
    west = math.floor(min(bounds[0] - buffer_m, parent[0]) / cell_m) * cell_m
    south = math.floor(min(bounds[1], parent[1]) / cell_m) * cell_m
    east = math.ceil(max(bounds[2] + buffer_m, parent[2]) / cell_m) * cell_m
    north = math.ceil(max(bounds[3] + buffer_m, parent[3]) / cell_m) * cell_m
    return {'crs': 'EPSG:25830', 'bounds': [west, south, east, north],
            'width': (east - west) // cell_m, 'height': (north - south) // cell_m,
            'transform': [cell_m, 0, west, 0, -cell_m, north], 'cell_metres': cell_m}


def derive(archive):
    archive = Path(archive).resolve()
    with archive.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != SOURCE_SHA256:
        raise ValueError('Protected-area source archive hash differs')
    import pyogrio  # Optional GIS dependency; imported only when deriving from source.
    path = f'/vsizip/{archive}/{MEMBER}'
    info = pyogrio.read_info(path)
    if info['crs'] != 'EPSG:25830':
        raise ValueError('Unexpected source CRS')
    frame = pyogrio.read_dataframe(path, where="site_code IN ('ES6170004','ES6170010')")
    if len(frame) != len(SITES) or set(frame['site_code']) != set(SITES):
        raise ValueError('Required protected-area sites are missing or duplicated')
    if frame.geometry.is_empty.any() or frame.geometry.isna().any() or not frame.geometry.is_valid.all():
        raise ValueError('Invalid protected-area geometry')
    bounds = [float(value) for value in frame.total_bounds]
    return {'version': VERSION, 'status': 'candidate_extent_not_an_approved_map',
            'source': {'title': 'MITECO Natura 2000 end-2025 protected sites',
                       'url': SOURCE_URL, 'sha256': SOURCE_SHA256, 'member': MEMBER,
                       'site_codes': list(SITES), 'source_crs': info['crs'],
                       'source_reference_bounds_metres': bounds,
                       'interpretation': 'Protected areas are a geographic proxy, not the full geological massif.'},
            'rule': {'north_east_west_buffer_metres': 5000, 'southern_buffer_metres': 0,
                     'minimum_parent_bounds_metres': list(PARENT_BOUNDS)},
            'grid': domain_from_bounds(bounds),
            'limitations': ['No claim of complete massif, coastal or source-data coverage.',
                            '2025 designated areas are not a historical 2021 incident boundary.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = derive(args.archive)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result['grid'], sort_keys=True))


if __name__ == '__main__':
    main()
