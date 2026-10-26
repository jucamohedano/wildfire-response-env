"""Screen a bounded IGN numeric elevation extract, without authorizing slope physics."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ElementTree


VERSION = 'wildfire-clean-terrain-candidate-v1'
SOURCE = '.runtime/malaga-clean-elevation-v1/ign-mdt25-halo.tif'
SOURCE_SHA256 = '3a41db0c46f8b3b03c9a2e3def3feee6b7994d4ba335d3670658803335493e73'
DESCRIPTION = '.runtime/malaga-clean-elevation-v1/coverage-v2.xml'
DESCRIPTION_SHA256 = 'fadb886035d9438581bf0a1663b503f7b3394ea19e6e8e0d737504a94934b301'
SERVICE = '.runtime/malaga-clean-elevation-metadata/wcs-service.xml'
PRODUCT = '.runtime/malaga-clean-elevation-metadata/mdt25-first-coverage.xml'
SERVICE_SHA256 = '3a031406ca46907ade8b2b7c343da232b3b75a6858f6c1157ecd88b2f9523a2d'
PRODUCT_SHA256 = '4d68ecb771b85639cd44fdfae07c29fdf6586a0a952354f9e4055172e62ae42c'
SOURCE_BOUNDS = (296500, 4033500, 316000, 4055000)
GRID_TRANSFORM = (500, 0, 297000, 0, -500, 4054500)
SHAPE = (41, 37)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def file_record(root, relative, expected=None, limit=8_000_000):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Expected a project-relative artifact')
    path = Path(root) / relative
    if path.is_symlink() or not path.resolve().is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError('Expected a regular artifact inside root')
    if path.stat().st_size > limit:
        raise ValueError('Artifact exceeds size cap')
    data = path.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    if expected is not None and checksum != expected:
        raise ValueError('Artifact SHA256 mismatch')
    return data, dict(path=relative.as_posix(), bytes=len(data), sha256=checksum)


def describe_vertical(data):
    if len(data) > 1_000_000 or b'<!ENTITY' in data or b'<!DOCTYPE' in data:
        raise ValueError('Expected bounded XML without external declarations')
    namespace = dict(wcs='http://www.opengis.net/wcs/2.0', swe='http://www.opengis.net/swe/2.0')
    document = ElementTree.fromstring(data)
    identifier = document.find('.//wcs:CoverageId', namespace)
    units = document.findall('.//swe:uom', namespace)
    if identifier is None or identifier.text != 'Elevacion25830_25' or len(units) != 1:
        raise ValueError('Unexpected coverage identifier or band metadata')
    return dict(coverage_id=identifier.text, reported_band_uom=units[0].get('code'),
                vertical_units_verified=False, vertical_datum_verified=False,
                interpretation='Product/service describe orthometric terrain; the range UOM is inconsistent. No automatic metre relabelling.',
                slope_enabled=False)


def screen_values(values):
    if not values:
        raise ValueError('Empty elevation array')
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError('Nonfinite or nonnumeric elevation')
    if any(value in (-32767, -32768, -9999) or not -500 <= value <= 4000 for value in values):
        raise ValueError('Suspect sentinel or out-of-screen-range elevation; do not repair')
    return dict(cells=len(values), numeric_min=min(values), numeric_max=max(values),
                zero_values=sum(value == 0 for value in values),
                sentinel_values=0, nonfinite_values=0,
                zero_policy='Zero is retained, not inferred missing or replaced. This coastal subset can contain sea-level values.',
                scope='Broad numeric screening only, not independent accuracy or NoData authentication')


def prepare(root, output):
    import numpy
    import rasterio
    from rasterio.warp import reproject, Resampling

    root, output = Path(root).resolve(), Path(output)
    if not output.is_absolute():
        output = root / output
    if not output.resolve().is_relative_to(root / '.runtime') or output.exists() or output.is_symlink():
        raise ValueError('Choose a fresh directory inside repository .runtime')
    _, source_record = file_record(root, SOURCE, SOURCE_SHA256)
    description, description_record = file_record(root, DESCRIPTION, DESCRIPTION_SHA256)
    _, service_record = file_record(root, SERVICE, SERVICE_SHA256)
    _, product_record = file_record(root, PRODUCT, PRODUCT_SHA256)
    vertical = describe_vertical(description)
    with rasterio.Env(GDAL_PAM_ENABLED='NO', PROJ_NETWORK='OFF'):
        with rasterio.open(root / SOURCE) as source:
            if (source.driver != 'GTiff' or source.count != 1 or source.shape != (860, 780)
                    or source.crs.to_string() != 'EPSG:25830' or source.dtypes != ('int16',)
                    or source.scales != (1.0,) or source.offsets != (0.0,)
                    or source.nodata is not None or source.transform.b != 0 or source.transform.d != 0
                    or any(not math.isclose(actual, expected, abs_tol=1e-7, rel_tol=0)
                           for actual, expected in zip(source.bounds, SOURCE_BOUNDS))
                    or any(not math.isclose(value, 25, abs_tol=1e-7, rel_tol=0) for value in source.res)):
                raise ValueError('Unexpected source layout, scale, bounds or mask declaration')
            values = source.read(1)
            if not numpy.all(source.read_masks(1) == 255):
                raise ValueError('Unexpected source invalid pixels')
            numeric = screen_values(values.ravel().tolist())
            averages = numpy.full(SHAPE, numpy.nan, dtype='float32')
            coverage = numpy.zeros(SHAPE, dtype='float32')
            for source_values, destination in ((values.astype('float32'), averages),
                                                (numpy.ones(values.shape, dtype='float32'), coverage)):
                reproject(source=source_values, destination=destination, src_transform=source.transform,
                    src_crs=source.crs, dst_transform=rasterio.Affine(*GRID_TRANSFORM), dst_crs=source.crs,
                    src_nodata=None, dst_nodata=numpy.nan, resampling=Resampling.average, num_threads=1)
            if not numpy.all(numpy.isfinite(averages)) or not numpy.allclose(coverage, 1):
                raise ValueError('Incomplete coarse-grid source coverage')
            source_metadata = dict(crs=source.crs.to_string(), transform=list(source.transform)[:6],
                bounds=list(source.bounds), shape=list(source.shape), dtype=source.dtypes[0], nodata=source.nodata,
                units=list(source.units), scales=list(source.scales), offsets=list(source.offsets),
                quantization='Integer source output; do not borrow product 0.001 m accuracy/resolution claims')
        output.mkdir(parents=True)
        artifacts = [('elevation_source_values_500m.tif', averages, 'float32', float('nan')),
                     ('numeric_coverage_mask_500m.tif', numpy.ones(SHAPE, dtype='uint8'), 'uint8', None),
                     ('physics_approved_mask_500m.tif', numpy.zeros(SHAPE, dtype='uint8'), 'uint8', None)]
        outputs = []
        for name, array, dtype, nodata in artifacts:
            path = output / name
            with rasterio.open(path, 'w', driver='GTiff', width=SHAPE[1], height=SHAPE[0],
                count=1, dtype=dtype, crs='EPSG:25830', transform=rasterio.Affine(*GRID_TRANSFORM),
                nodata=nodata, compress='deflate') as destination:
                destination.write(array, 1)
                destination.update_tags(role='evaluator_candidate_not_active_physics', slope_enabled='false',
                    vertical_unit_status='unresolved_service_uom_conflict', source_sha256=SOURCE_SHA256)
            _, record = file_record(root, path.relative_to(root))
            outputs.append(record)
    report = dict(version=VERSION, checked_on='2026-10-25', role='evaluator_candidate',
        status='numeric_screen_passed_vertical_metadata_pending', actor_input_eligible=False,
        simulator_input_eligible=False, slope_enabled=False, historical_replay_ready=False,
        source_url='https://servicios.idee.es/wcs-inspire/mdt', source_metadata=source_metadata,
        input_files=[source_record, description_record, service_record, product_record,
                     file_record(root, 'eval/prepare_clean_malaga_terrain.py')[1]], output_files=outputs,
        vertical=vertical, numeric_screen=numeric,
        grid=dict(crs='EPSG:25830', width=37, height=41, resolution_metres=500, transform=list(GRID_TRANSFORM)),
        preparation='GDAL average of the bounded returned numeric raster, no regional overviews, no gap filling; masks distinguish numeric coverage from physics approval.',
        checks=dict(full_numeric_coverage=True, no_extreme_negative_values=True,
                    zero_values_preserved=True, source_files_unchanged=True,
                    approved_physics_cells=0, previous_quarantined_source_replaced=False),
        remaining_gates=['Coverage-specific height scale/unit confirmation despite default-looking SWE radiance UOM.',
            'Native NoData/void semantics and exact vertical datum are not supplied in returned GeoTIFF.',
            'Exact local survey date and historical product availability are unverified.',
            'Validate slope estimation and its synthetic fire/movement coupling in a separate version.'],
        limitations=['Numeric screening is not independent terrain truth or producer QA certification.',
            'Service-level orthometric evidence and product-level metric resolution do not repair missing band metadata.',
            'Do not assign REDIAM2020-21 epoch or second-coverage EPSG5782 to this live WCS extract.',
            'The OGC API identified a first-coverage collection but failed data requests; no OGC COG was acquired.',
            'No terrain or slope values enter the existing prepared runtime or reward.'])
    (output / 'audit.json').write_bytes(encoded(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    result = prepare(options.root, options.output)
    print(json.dumps(dict(status=result['status'], numeric_screen=result['numeric_screen'],
                          slope_enabled=False), indent=2))


if __name__ == '__main__':
    main()
