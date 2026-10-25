"""Prepare local evaluator-only fuel and quarantined terrain diagnostics offline.

Run with ``python -m eval.prepare_malaga_landscape``. Raster dependencies are
optional until preparation is requested; no downloads or simulator writes occur.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path


VERSION = 'wildfire-malaga-landscape-preparation-v1'
STUDY_BBOX = (297125, 4034450, 315325, 4054275)
RESOLUTION = 500
CRS = 'EPSG:25830'
OUTPUT_DIRECTORY = Path('.runtime/malaga-landscape-prepared-v1')
SOURCE_DIRECTORY = Path('.runtime/malaga-terrain-fuel-search')
METADATA = Path('data/malaga_2021/terrain_fuel_search_v1.json')
SUPPORTED_FUEL_CODES = tuple(range(1, 11))
GATES = {
    'terrain_nodata_and_overview_semantics':
        'Authenticate producer nodata and overview/averaging semantics; plausibility masks cannot release quarantine.',
    'terrain_vertical_reference':
        'Verify vertical units and datum from authoritative metadata.',
    'terrain_uncontaminated_elevation':
        'Reacquire and validate uncontaminated terrain before supplying any physics-ready elevation.',
    'fuel_historical_state':
        'Verify vegetation observation epoch and applicability to September 2021 local fuel state.',
    'fuel_physics_parameters':
        'Validate fuel model mapping, loads and live/dead moisture; class codes alone do not supply these.',
    'event_time_actor_availability':
        'Establish information availability at event time and authorize actor inputs.',
    'runtime_and_replay_validation':
        'Explicitly integrate and validate simulator inputs and historical replay in separate work.',
}


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def file_record(path, root):
    path, root = Path(path), Path(root)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular, non-symlink input/output file')
    relative = path.resolve().relative_to(root.resolve())
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(path=relative.as_posix(), bytes=path.stat().st_size, sha256=digest)


def pinned_source(root, source_directory, descriptor):
    filename = descriptor['filename']
    if Path(filename).name != filename or filename in ('.', '..'):
        raise ValueError('Expected a flat source filename')
    path = root / source_directory / filename
    record = file_record(path, root)
    if record['bytes'] != descriptor['bytes'] or record['sha256'] != descriptor['sha256']:
        raise ValueError('Source size or SHA256 mismatch: ' + filename)
    return path, record


def common_grid(bbox=STUDY_BBOX):
    if (len(bbox) != 4 or any(type(value) not in (int, float) or not math.isfinite(value)
                              for value in bbox)
            or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
        raise ValueError('Expected finite, ordered metric bounds')
    west, south = (math.floor(value / RESOLUTION) * RESOLUTION for value in bbox[:2])
    east, north = (math.ceil(value / RESOLUTION) * RESOLUTION for value in bbox[2:])
    width, height = (east - west) // RESOLUTION, (north - south) // RESOLUTION
    if width * height > 1_000_000:
        raise ValueError('Prepared grid exceeds cell cap')
    return dict(crs=CRS, resolution_metres=RESOLUTION, width=width, height=height,
                bounds=[west, south, east, north], study_bbox=list(bbox),
                transform=[RESOLUTION, 0, west, 0, -RESOLUTION, north],
                alignment='Outward rounding to EPSG:25830 coordinate multiples of 500 m',
                envelope_origin='Retained terrain acquisition bbox, not an exact episode AOI polygon',
                envelope_mask_rule='Cell centre inside retained study bbox; padding remains explicit')


def classify_fuel(values, valid):
    if len(values) != len(valid):
        raise ValueError('Fuel values and source mask differ in length')
    classes, mask = [], []
    for value, accepted in zip(values, valid):
        known = bool(accepted) and value in SUPPORTED_FUEL_CODES
        classes.append(int(value) if known else 0)
        mask.append(int(known))
    return classes, mask


def terrain_diagnostics(values, valid, nodata):
    if len(values) != len(valid) or not values:
        raise ValueError('Expected matching nonempty terrain values and mask')
    flags = []
    finite_values = []
    counts = dict(cells=len(values), source_mask_invalid=0, nonfinite=0,
                  equal_declared_nodata=0, equal_minus32767=0, below_minus1000=0)
    for value, accepted in zip(values, valid):
        finite = math.isfinite(value)
        declared = nodata is not None and (value == nodata or
                                          (math.isnan(nodata) and math.isnan(value)))
        counts['source_mask_invalid'] += int(not accepted)
        counts['nonfinite'] += int(not finite)
        counts['equal_declared_nodata'] += int(declared)
        counts['equal_minus32767'] += int(value == -32767)
        counts['below_minus1000'] += int(finite and value < -1000)
        flags.append(int(not accepted or not finite or declared or value < -1000))
        if finite:
            finite_values.append(float(value))
    counts['finite_numeric_range'] = ([min(finite_values), max(finite_values)]
                                      if finite_values else None)
    counts['diagnostically_flagged'] = sum(flags)
    counts['flag_rule'] = 'Source-invalid, nonfinite, declared nodata or below -1000; generated triage only'
    counts['unflagged_is_verified_elevation'] = False
    counts['quarantine_released'] = False
    return flags, counts


def raster_dependencies():
    try:
        import numpy
        import rasterio
        from rasterio.warp import reproject, Resampling
    except ImportError as error:
        raise RuntimeError('Preparation needs optional numpy and rasterio; use an isolated environment') from error
    return numpy, rasterio, reproject, Resampling


def describe_raster(source):
    if (source.count != 1 or source.crs is None or source.width * source.height > 2_000_000
            or source.transform.b != 0 or source.transform.d != 0
            or source.transform.a <= 0 or source.transform.e >= 0
            or source.scales != (1.0,) or source.offsets != (0.0,)):
        raise ValueError('Unsupported source raster layout, scale or CRS')
    return dict(crs=source.crs.to_string(), shape=[source.height, source.width],
                bounds=list(source.bounds), transform=list(source.transform)[:6],
                resolution=list(source.res), dtype=source.dtypes[0], nodata=source.nodata,
                units=list(source.units), scales=list(source.scales), offsets=list(source.offsets),
                tags=source.tags(), band_tags=source.tags(1))


def prepare_landscape(root):
    root = Path(root).resolve()
    metadata_path = root / METADATA
    metadata_record = file_record(metadata_path, root)
    metadata = json.loads(metadata_path.read_bytes())
    if (metadata.get('version') != 'wildfire-malaga-terrain-fuel-search-v1'
            or metadata.get('actor_input_eligible') is not False
            or metadata.get('historical_replay_ready') is not False):
        raise ValueError('Unexpected evaluator-only terrain/fuel source contract')
    terrain_path, terrain_record = pinned_source(root, SOURCE_DIRECTORY, metadata['terrain'])
    fuel_path, fuel_record = pinned_source(root, SOURCE_DIRECTORY, metadata['fuel'])
    output = root / OUTPUT_DIRECTORY
    if output.resolve() != output.absolute() or output.is_symlink():
        raise ValueError('Output directory must not traverse symlinks')
    names = ('fuel_classes.tif', 'fuel_valid_mask.tif', 'fuel_source_coverage_mask.tif',
             'study_envelope_mask.tif', 'terrain_valid_mask.tif', 'terrain_suspect_mask.tif')
    if any((output / name).exists() or (output / name).is_symlink()
           for name in (*names, 'audit.json')):
        raise ValueError('Refusing to overwrite prepared landscape outputs')
    numpy, rasterio, reproject, resampling = raster_dependencies()
    grid = common_grid()
    shape = (grid['height'], grid['width'])
    transform = rasterio.Affine(*grid['transform'])

    def warp(values, source, method, fill=0):
        destination = numpy.full(shape, fill, dtype='uint8')
        reproject(source=values, destination=destination, src_transform=source.transform,
                  src_crs=source.crs, dst_transform=transform, dst_crs=CRS,
                  src_nodata=None, dst_nodata=fill, resampling=method, num_threads=1,
                  init_dest_nodata=True)
        return destination

    with rasterio.Env(GDAL_PAM_ENABLED='NO', PROJ_NETWORK='OFF'):
        with rasterio.open(fuel_path) as source:
            fuel_source = describe_raster(source)
            if (source.crs.to_string() != 'EPSG:3035'
                    or not all(math.isclose(value, 250, rel_tol=0, abs_tol=1e-7) for value in source.res)
                    or source.nodata != 0 or source.dtypes[0] != 'uint8'):
                raise ValueError('Unexpected native EFFIS fuel raster')
            values, source_mask = source.read(1), source.read_masks(1) > 0
            classes, mask = classify_fuel(values.ravel().tolist(), source_mask.ravel().tolist())
            clean = numpy.asarray(classes, dtype='uint8').reshape(values.shape)
            known = numpy.asarray(mask, dtype='uint8').reshape(values.shape)
            fuel = warp(clean, source, resampling.nearest)
            fuel_valid = warp(known, source, resampling.nearest)
            fuel_coverage = warp(numpy.ones(values.shape, dtype='uint8'), source, resampling.nearest)
            fuel[fuel_valid == 0] = 0
            source_codes, source_counts = numpy.unique(values, return_counts=True)
            fuel_source['cell_values_and_counts'] = {str(int(code)): int(count)
                                                     for code, count in zip(source_codes, source_counts)}
            fuel_source['source_mask_invalid_cells'] = int((~source_mask).sum())
            fuel_source['unrecognized_unmasked_cells'] = int(((clean == 0) & source_mask & (values != 0)).sum())
        with rasterio.open(terrain_path) as source:
            terrain_source = describe_raster(source)
            if (source.crs.to_string() != CRS
                    or not all(math.isclose(value, 25, rel_tol=0, abs_tol=1e-7) for value in source.res)):
                raise ValueError('Unexpected retained terrain candidate grid')
            values = source.read(1)
            flags, diagnostics = terrain_diagnostics(values.ravel().tolist(),
                                                      (source.read_masks(1) > 0).ravel().tolist(),
                                                      source.nodata)
            terrain_suspect = warp(numpy.asarray(flags, dtype='uint8').reshape(values.shape),
                                   source, resampling.max, fill=255)

        columns = grid['bounds'][0] + (numpy.arange(grid['width']) + 0.5) * RESOLUTION
        rows = grid['bounds'][3] - (numpy.arange(grid['height']) + 0.5) * RESOLUTION
        study_mask = ((rows[:, None] >= STUDY_BBOX[1]) & (rows[:, None] <= STUDY_BBOX[3]) &
                      (columns[None, :] >= STUDY_BBOX[0]) & (columns[None, :] <= STUDY_BBOX[2])).astype('uint8')
        terrain_valid = numpy.zeros(shape, dtype='uint8')
        artifacts = {
            'fuel_classes.tif': (fuel, 0, 'Derived native NFFL classes; zero is unknown, never measured fuel'),
            'fuel_valid_mask.tif': (fuel_valid, None, '1=recognized source class at nearest sample; 0=unknown'),
            'fuel_source_coverage_mask.tif': (fuel_coverage, None, '1=nearest sample inside cached source footprint'),
            'study_envelope_mask.tif': (study_mask, None, '1=cell centre inside retained metric study bbox'),
            'terrain_valid_mask.tif': (terrain_valid, None, 'All zero: terrain quarantined, no approved elevation'),
            'terrain_suspect_mask.tif': (terrain_suspect, 255, '1=any contributing input flagged; 0=unverified; 255=no coverage'),
        }
        output.mkdir(parents=True, exist_ok=True)
        output_records = []
        shared_grid = True
        for name, (values, nodata, meaning) in artifacts.items():
            path = output / name
            with rasterio.open(path, 'w', driver='GTiff', width=grid['width'], height=grid['height'],
                               count=1, dtype='uint8', crs=CRS, transform=transform,
                               nodata=nodata, compress='deflate') as destination:
                destination.write(values, 1)
                destination.update_tags(version=VERSION, meaning=meaning, actor_input_eligible='false',
                                        simulator_input_eligible='false', physics_input_eligible='false',
                                        historical_replay_ready='false')
            with rasterio.open(path) as prepared:
                shared_grid &= (prepared.crs.to_string() == CRS and prepared.transform == transform
                                and prepared.shape == shape and prepared.nodata == nodata
                                and numpy.array_equal(prepared.read(1), values))
            output_records.append(file_record(path, root))

    output_codes, output_counts = numpy.unique(fuel, return_counts=True)
    fuel_counts = {str(int(code)): int(count) for code, count in zip(output_codes, output_counts)}
    checks = dict(
        source_hashes_match=True,
        source_files_unchanged=(file_record(terrain_path, root) == terrain_record
                                and file_record(fuel_path, root) == fuel_record
                                and file_record(metadata_path, root) == metadata_record),
        outputs_reopened_on_common_grid=bool(shared_grid),
        fuel_codes_preserved=set(output_codes.tolist()) <= set(source_codes.tolist()) | {0},
        fuel_unknown_matches_mask=bool(numpy.array_equal(fuel != 0, fuel_valid != 0)),
        fuel_valid_samples_have_source_coverage=bool(numpy.all(fuel_valid <= fuel_coverage)),
        terrain_valid_mask_all_zero=bool(numpy.all(terrain_valid == 0)),
        physics_ready_elevation_emitted=False,
        measured_repairs_generated=False,
    )
    if not all(checks[key] for key in checks if key not in
               ('physics_ready_elevation_emitted', 'measured_repairs_generated')):
        raise ValueError('Prepared landscape integrity checks failed')
    audit = dict(
        version=VERSION, status='fuel_prepared_terrain_quarantined',
        role='retrospective_evaluator_candidate_preparation', actor_input_eligible=False,
        simulator_input_eligible=False, physics_input_eligible=False, historical_replay_ready=False,
        runtime_usage=dict(actor=False, simulator=False, physics=False, historical_replay=False),
        input_files=[metadata_record, terrain_record, fuel_record,
                     file_record(root / 'eval/prepare_malaga_landscape.py', root)],
        output_files=output_records,
        output_inventory_scope='Six raster artifacts; audit.json excludes its own recursive hash',
        grid=grid, software=dict(rasterio=rasterio.__version__, numpy=numpy.__version__,
                                gdal=rasterio.__gdal_version__, proj=rasterio.__proj_version__),
        fuel=dict(
            status='categorical_candidate_prepared', source=fuel_source,
            source_sha256=fuel_record['sha256'], source_member=metadata['fuel']['member'],
            citation_year=metadata['fuel']['citation_year'], vegetation_observation_epoch_verified=False,
            resampling='GDAL NEAREST independently on classes, source validity and footprint; no categorical smoothing',
            nodata_policy='Source zero and masked/unrecognized classes remain unknown; never substitute measured fuel',
            supported_codes=list(SUPPORTED_FUEL_CODES), destination_nodata=0,
            cell_values_and_counts=fuel_counts, valid_cells=int(fuel_valid.sum()),
            unknown_cells=int((fuel_valid == 0).sum()),
            uncovered_cells=int((fuel_coverage == 0).sum()),
            values_derived_from_observed_source=True, generated_class_values=False,
            measured_local_2021_fuel=False, actor_input_eligible=False, physics_input_eligible=False),
        terrain=dict(
            status='quarantined_pending_authentic_nodata_vertical_reference_and_resampling_review',
            source=terrain_source, source_sha256=terrain_record['sha256'], diagnostics=diagnostics,
            collection_epoch_claim=metadata['terrain']['source_epoch_evidence'],
            publication_is_pre_incident=metadata['terrain']['publication_is_pre_incident'],
            vertical_units_verified=False, vertical_datum_verified=False, nodata_semantics_verified=False,
            resampling='GDAL MAX on generated binary diagnostic flags only; no elevation resampling or interpolation',
            mask_policy='terrain_valid_mask is zero everywhere regardless of plausibility or source mask',
            diagnostic_grid_flagged_cells=int((terrain_suspect == 1).sum()),
            diagnostic_grid_uncovered_cells=int((terrain_suspect == 255).sum()),
            approved_elevation_cells=0,
            values_derived_from_observed_source=True, generated_diagnostic_masks=True,
            generated_elevation=False, quarantine_released=False,
            actor_input_eligible=False, physics_input_eligible=False),
        generated_assumptions=[
            '500 m metric grid with outward alignment from retained bbox; no exact episode-polygon clipping',
            'Study envelope mask uses cell centres; aligned padding is retained in raster outputs',
            'Below -1000 is a diagnostic triage threshold, not authenticated nodata or an elevation repair',
            'All terrain cells remain ineligible, including cells without diagnostic flags',
        ],
        checks=checks, remaining_gates=GATES,
        limitations=[
            'No terrain elevation, slope or aspect is emitted. Earlier 25 m averaging may have spread unknown sentinels.',
            'Zero terrain suspect flags do not establish valid heights; MAX considers overlapping source pixels only.',
            'Pre-fire collection claim is retained from cached metadata; authentic nodata, units and datum remain unresolved.',
            'Later terrain publication does not establish information available to actors during the incident.',
            'EFFIS citation 2017 and source filename FuelMap2000 do not verify vegetation observation epoch.',
            'Nearest sampling at 500 m is not area-majority fuel or subcell composition; unsampled source gaps may be missed.',
            'Fuel classes are not measured loads, moisture, treatments or validated engine parameters.',
            'All actor, simulator, physics and historical replay usage remains false; no runtime integration occurs.',
        ],
    )
    (output / 'audit.json').write_bytes(encoded(audit))
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    audit = prepare_landscape(args.root)
    print(json.dumps(dict(status=audit['status'], audit=str(OUTPUT_DIRECTORY / 'audit.json'),
                         remaining_gates=list(GATES)), indent=2))


if __name__ == '__main__':
    main()
