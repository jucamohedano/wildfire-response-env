#!/usr/bin/env python3
"""Compile a separate unapproved terrain candidate on the corrected grid."""
import argparse
from pathlib import Path

from extended_map_common import digest, file_sha256, read_json, serialize, write_private_json
OLD_PIN = '1b2e4f470bf456b0189cf22e60dddcf09af413617eb9b76f5a2c1650be70e917'
from extended_map_v2 import BOUNDS, HEIGHT, WIDTH

WCS_REQUEST_URI = 'https://servicios.idee.es/wcs-inspire/mdt?SERVICE=WCS&VERSION=1.0.0&REQUEST=GetCoverage&COVERAGE=Elevacion25830_5&CRS=EPSG%3A25830&RESPONSE_CRS=EPSG%3A25830&BBOX=289975%2C4034975%2C332025%2C4064525&RESX=25&RESY=25&FORMAT=GEOTIFFINT16&INTERPOLATION=nearest%20neighbor'
NEW_PIN = '299b0cf337786e666fdbdeb32df86531d84a490da8d00dc91678bfe136d909a2'


def screen_and_aggregate(values):
    import numpy as np
    if values.shape != (HEIGHT * 20 + 2, WIDTH * 20 + 2) or not np.isfinite(values).all():
        raise ValueError('invalid expanded 25 m sample or halo')
    if (values < 0).any() or (values > 4000).any():
        raise ValueError('suspicious terrain values; do not fill')
    inner = values[1:-1, 1:-1]
    cells = inner.reshape(HEIGHT, 20, WIDTH, 20).transpose(0, 2, 1, 3)
    zeros = (cells == 0).sum(axis=(2, 3))
    sums = cells.astype('int64').sum(axis=(2, 3))
    averages = [[None if int(zeros[r, c]) else int(sums[r, c]) / 400
                 for c in range(WIDTH)] for r in range(HEIGHT)]
    return averages, zeros.astype(int).tolist(), {
        'source_pixels_including_halo': int(values.size),
        'interior_pixels': int(inner.size), 'halo_pixels': int(values.size - inner.size),
        'pixels_per_cell': 400, 'zero_samples': int((values == 0).sum()),
        'zero_interior_samples': int((inner == 0).sum()),
        'zero_halo_samples': int((values == 0).sum() - (inner == 0).sum()),
        'cells_with_zero': int((zeros > 0).sum()),
        'candidate_means': int((zeros == 0).sum()),
        'numeric_min': int(values.min()), 'numeric_max': int(values.max())}


def build(new_path, old_path):
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds

    if file_sha256(new_path) != NEW_PIN or file_sha256(old_path) != OLD_PIN:
        raise ValueError('terrain source hash mismatch')
    with rasterio.Env(PROJ_NETWORK='OFF', GDAL_PAM_ENABLED='NO'):
        with rasterio.open(new_path) as src, rasterio.open(old_path) as old:
            if (src.driver != 'GTiff' or src.count != 1 or src.crs.to_string() != 'EPSG:25830'
                or src.shape != (1182, 1682) or src.dtypes != ('int16',) or src.nodata is not None
                or src.scales != (1.0,) or src.offsets != (0.0,)
                or not np.allclose(tuple(src.transform)[:6], (25, 0, 289975, 0, -25, 4064525), atol=1e-7, rtol=0)
                or not np.all(src.read_masks(1) == 255)):
                raise ValueError('unexpected corrected sample metadata or mask')
            if (old.shape != (860, 780) or old.nodata is not None or
                not np.allclose(tuple(old.transform)[:6], (25, 0, 296500, 0, -25, 4055000), atol=1e-7, rtol=0)):
                raise ValueError('unexpected parent sample grid')
            values = src.read(1)
            averages, zeros, stats = screen_and_aggregate(values)
            bounds = (296500, 4034975, 316000, 4055000)
            new_win = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
            old_win = from_bounds(*bounds, transform=old.transform).round_offsets().round_lengths()
            newer, previous = src.read(1, window=new_win), old.read(1, window=old_win)
            if newer.shape != (801, 780) or previous.shape != newer.shape:
                raise ValueError('terrain overlap alignment mismatch')
            differences = np.abs(newer.astype('float64') - previous.astype('float64'))
            if not np.isfinite(differences).all():
                raise ValueError('invalid parent comparison')
            comparison = {'overlap_pixels': int(differences.size), 'identical_pixels': int((differences == 0).sum()),
                          'max_absolute_difference': float(differences.max()),
                          'mean_absolute_difference': float(differences.mean()), 'bounds': list(bounds),
                          'method': 'direct comparison integer new sample against floating point parent sample, no rounding'}
    result = {'version': 'extended-map-terrain-candidate-v2', 'status': 'unapproved_numeric_candidate_no_physics',
              'grid': {'crs': 'EPSG:25830', 'width': WIDTH, 'height': HEIGHT,
                       'transform': [500, 0, BOUNDS[0], 0, -500, BOUNDS[3]]},
              'source': {'expanded_file_sha256': NEW_PIN, 'parent_sample_sha256': OLD_PIN,
                         'expanded_sample': 'IGN WCS Elevacion25830_5, requested at 25 m using nearest neighbor',
                         'acquired_on': '2026-10-30',
                         'request_uri': WCS_REQUEST_URI,
                         'declared_nodata': None, 'band_unit': None,
                         'datum_native_void_semantics_and_epoch': 'unverified',
                         'parent_sample_crs': 'EPSG:25830 by parent provenance; ASC has no CRS metadata'},
              'method': 'independent 20 x 20 arithmetic mean per cell; any zero nulls the mean; one 25 m pixel halo screened but not averaged',
              'seam_policy': 'corrected sample only for every cell; no blending with parent; no physics approval',
              'sample_statistics': stats, 'parent_sample_comparison': comparison,
              'elevation_candidate': averages, 'zero_samples_per_cell': zeros}
    result['sha256'] = digest(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['compile', 'validate'])
    parser.add_argument('--expanded', type=Path, required=True)
    parser.add_argument('--parent-sample', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.expanded, args.parent_sample)
    if args.command == 'compile':
        write_private_json(args.output, result)
    elif serialize(read_json(args.output)) != serialize(result):
        raise ValueError('terrain candidate differs from source reconstruction')
    print({k: result[k] for k in ('sha256', 'sample_statistics', 'parent_sample_comparison')})


if __name__ == '__main__':
    main()
