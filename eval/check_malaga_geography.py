"""Check cross-CRS terrain coverage; requires the isolated data-tool dependency."""

import argparse
import hashlib
import json
from pathlib import Path

from eval.prepare_malaga_2021 import check_geometry, load_json, terrain_summary


def check_coverage(bundle):
    from pyproj import Transformer, __version__

    bundle = Path(bundle)
    manifest = load_json((bundle / 'manifest.json').read_bytes())
    raw_terrain = (bundle / 'private/terrain_aoi.asc').read_bytes()
    expected = next(source for source in manifest['source_files'] if source['id'] == 'terrain-aoi')
    if (len(raw_terrain) != expected['bytes']
            or hashlib.sha256(raw_terrain).hexdigest() != expected['sha256']):
        raise ValueError('Terrain hash mismatch')
    if terrain_summary(raw_terrain) != manifest['terrain']:
        raise ValueError('Terrain metadata mismatch')
    header = manifest['terrain']['header']
    cellsize = header['cellsize']
    terrain = [header['xllcorner'], header['yllcorner'],
               header['xllcorner'] + cellsize * header['ncols'],
               header['yllcorner'] + cellsize * header['nrows']]
    transform = Transformer.from_crs('OGC:CRS84', 'EPSG:25830', always_xy=True)
    checks = []
    for product in manifest['products']:
        for layer in ('areaOfInterestA', 'observedEventA'):
            metadata = product['layers'][layer]
            filename = metadata['path']
            path = (bundle / filename).resolve()
            if not path.is_relative_to(bundle.resolve()):
                raise ValueError('Layer escapes the bundle')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != metadata['sha256']:
                raise ValueError('Layer hash mismatch')
            value = load_json(data)
            vertices = []

            def collect(coordinates):
                if type(coordinates[0]) in (int, float):
                    vertices.append(coordinates)
                else:
                    for child in coordinates:
                        collect(child)

            for feature in value['features']:
                check_geometry(feature['geometry'])
                collect(feature['geometry']['coordinates'])
            projected = [transform.transform(*vertex) for vertex in vertices]
            inside = all(terrain[0] - 0.001 <= east <= terrain[2] + 0.001
                         and terrain[1] - 0.001 <= north <= terrain[3] + 0.001
                         for east, north in projected)
            checks.append(dict(product=product['id'], layer=layer,
                               vertices=len(vertices), inside_terrain=inside))
    return dict(version='wildfire-malaga-geography-check-v1', pyproj_version=__version__,
                source_crs='OGC:CRS84', terrain_crs='EPSG:25830', always_xy=True,
                terrain_bbox=terrain, checks=checks,
                all_vertices_covered=bool(checks) and all(item['inside_terrain'] for item in checks),
                vertical_metadata_verified=False, historical_terrain_epoch_verified=False,
                actor_input_eligible=False,
                limitations='Horizontal coverage only; no terrain epoch, vertical datum, nodata semantics, road routing, topology, fuel or fire-model calibration claim.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    result = check_coverage(args.bundle)
    print(json.dumps(result, indent=2))
    if not result['all_vertices_covered']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
