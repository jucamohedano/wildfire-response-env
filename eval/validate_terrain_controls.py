"""Offline, pinned survey diagnostics; never correct terrain from monument heights."""
import argparse
import json
import math
from pathlib import Path

from eval.build_malaga_provenance import artifact, encoded
from eval.compile_topographic_domain import INPUT_PINS, PREPARED_INPUTS, PREPARED_INPUTS_SHA256, TERRAIN_ASCII
from wildfire_core.prepared import load_inputs
from wildfire_core.topographic import load_domain


DIRECTORY = '.runtime/terrain-control-evidence-v1/'
DOMAIN_PATH = '.runtime/topographic-inputs-v1/inputs.json'
DOMAIN_SHA256 = 'e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd'
SOURCES = {
    'pnoa-products.html': ('219ef7153c42be33a96c81ec4130476f03dbe94090dce5eb9dcedb0257ae8e3d',
        'https://pnoa.ign.es/web/portal/pnoa-lidar/productos-a-descarga'),
    'roi.geojson': ('f0673c77b10eeb4eccbea4c9e6ea5f620f3ecf3b5ccfd550b3411e4c8dbac22e',
        'https://api-features.ign.es/collections/red_roi/items?f=json&bbox=-5.28,36.42,-5.05,36.63&limit=100'),
    'rednap.geojson': ('1b1faa6a38b8c4d1d974bc2c1a7671d91f979d749f5f11f5cc8d85dc309d8387',
        'https://api-features.ign.es/collections/red_nap/items?f=json&bbox=-5.28,36.42,-5.05,36.63&limit=100'),
    'reales.pdf': ('8fb67fbf21958422ebf97696ba33711634876c24d017253649846d4cdea9e7e7',
        'https://datos-geodesia.ign.es/Red_Geodesica/Hoja1071/107199.pdf'),
    'ssk163.pdf': ('3511fe06d26f8a5f0fc8af353e5f0dcb6b8d2909a419fa8ab739649e030a3d14',
        'https://datos-geodesia.ign.es/REDNAP/Lin10636/10636010.pdf'),
    'reales-mdt05-5m.asc': ('38658bd4f67c9e247f7f6c4957f3223e7a1e96a3e469aa28fea07fbf31e56056',
        'https://servicios.idee.es/wcs-inspire/mdt?SERVICE=WCS&VERSION=1.0.0&REQUEST=GetCoverage&COVERAGE=Elevacion25830_5&CRS=EPSG%3A25830&RESPONSE_CRS=EPSG%3A25830&BBOX=302150%2C4039910%2C302210%2C4039970&RESX=5&RESY=5&FORMAT=ArcGrid&INTERPOLATION=nearest+neighbor'),
}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def survey_point(collection, identifier, network):
    if (network, identifier) not in (('roi', 107199), ('rednap', 10636010)) or collection.get('type') != 'FeatureCollection':
        raise ValueError('Expected an IGN ROI or REDNAP feature collection')
    features = [feature for feature in collection['features']
                if finite(feature.get('id')) and feature['id'] == identifier]
    if len(features) != 1:
        raise ValueError('Expected exactly one pinned survey point')
    feature = features[0]
    coordinates = feature['geometry']['coordinates']
    properties = feature['properties']
    height = properties['alt_orto' if network == 'roi' else 'ortometrica']
    ellipsoidal = properties['alt_elip' if network == 'roi' else 'altitud_elipsoidal']
    if (feature['geometry']['type'] != 'Point' or len(coordinates) != 2
            or not all(finite(value) for value in (*coordinates, height, ellipsoidal))
            or not -5.28 <= coordinates[0] <= -5.05 or not 36.42 <= coordinates[1] <= 36.63):
        raise ValueError('Invalid survey coordinates or heights')
    return dict(id=identifier, name=properties['nombre'], longitude=coordinates[0], latitude=coordinates[1],
        orthometric_height_metres=height, ellipsoidal_height_metres=ellipsoidal,
        height_reference_point='base_of_pillar' if network == 'roi' else 'pavement_levelling_nail',
        mark_to_bare_ground_offset_metres=None, exact_vertical_realization_verified=False)


def sample(values, transform, east, north):
    spacing_x, rotation_x, west, rotation_y, spacing_y, top = transform
    if rotation_x != 0 or rotation_y != 0 or spacing_x <= 0 or spacing_y >= 0:
        raise ValueError('Expected a north-up metre grid')
    if not finite(east) or not finite(north):
        raise ValueError('Expected finite metre coordinates')
    column, row = math.floor((east - west) / spacing_x), math.floor((north - top) / spacing_y)
    if not 0 <= row < len(values) or not 0 <= column < len(values[0]):
        return None
    height = values[row][column]
    if not finite(height) or height <= 0:
        raise ValueError('Do not compare unsupported terrain with a survey mark')
    center = [west + (column + .5) * spacing_x, top + (row + .5) * spacing_y]
    return dict(column=column, row=row, height_metres=height, center_easting_northing=center,
                offset_from_mark_metres=math.dist(center, [east, north]),
                sample_spacing_metres=[spacing_x, -spacing_y])


def compare(point, values, transform, east, north):
    result = sample(values, transform, east, north)
    if result is not None:
        result['raster_minus_survey_mark_metres'] = result['height_metres'] - point['orthometric_height_metres']
        result['accuracy_pass'] = None
        result['interpretation'] = 'Diagnostic difference only: datum realization and mark-to-ground offset unverified'
    return result


def analyse(root):
    import rasterio
    from pyproj import Transformer, network

    root = Path(root).resolve()
    evidence = []
    for filename, (checksum, url) in SOURCES.items():
        record = artifact(root, DIRECTORY + filename)
        if record['sha256'] != checksum:
            raise ValueError('Missing or changed official terrain evidence: ' + filename)
        evidence.append(dict(record, source_url=url))
    product = (root / DIRECTORY / 'pnoa-products.html').read_text()
    block = product.split('productos/mdt05.png', 1)[1].split('</li>', 1)[0]
    if not all(text in block for text in ('MDT05', 'ETRS89', 'Alturas ortométricas')):
        raise ValueError('Pinned MDT05 product-level reference statement is absent')
    prepared = load_inputs(root / PREPARED_INPUTS, expected_sha256=PREPARED_INPUTS_SHA256)
    domain = load_domain(root / DOMAIN_PATH, prepared, DOMAIN_SHA256)
    raster_record = artifact(root, TERRAIN_ASCII)
    if raster_record['sha256'] != INPUT_PINS[TERRAIN_ASCII]:
        raise ValueError('Source raster differs from the compiled terrain pin')
    network.set_network_enabled(False)
    projector = Transformer.from_crs('EPSG:4326', 'EPSG:25830', always_xy=True, allow_ballpark=False)
    with rasterio.open(root / TERRAIN_ASCII) as source:
        if source.nodata is not None:
            raise ValueError('Native nil declaration changed; reassess, do not silently reinterpret')
        source_values, source_transform = source.read(1).tolist(), list(source.transform)[:6]
    with rasterio.open(root / DIRECTORY / 'reales-mdt05-5m.asc') as source:
        patch, patch_transform = source.read(1).tolist(), list(source.transform)[:6]
        if source.shape != (12, 12) or not math.isclose(source.transform.a, 5, abs_tol=1e-6):
            raise ValueError('Unexpected 5m diagnostic patch geometry')
    points = []
    for filename, identifier, network_name in (('roi.geojson', 107199, 'roi'),
                                               ('rednap.geojson', 10636010, 'rednap')):
        collection = json.loads((root / DIRECTORY / filename).read_text())
        point = survey_point(collection, identifier, network_name)
        east, north = projector.transform(point['longitude'], point['latitude'], errcheck=True)
        if network_name == 'roi':
            feature = next(feature for feature in collection['features'] if feature['id'] == identifier)
            supplied = [feature['properties']['x_etrs89'], feature['properties']['y_etrs89']]
            if math.dist(supplied, [east, north]) > .1:
                raise ValueError('Official geographic and UTM point coordinates disagree')
            east, north = supplied
        point['easting_northing'] = [east, north]
        point['inside_active_domain'] = sample(domain['elevations_metres'], domain['grid']['transform'], east, north) is not None
        point['sampled25m_comparison'] = compare(point, source_values, source_transform, east, north)
        point['sampled5m_comparison'] = compare(point, patch, patch_transform, east, north)
        points.append(point)
    return dict(version='wildfire-terrain-survey-diagnostics-v1', checked_on='2026-10-25',
        role='operator_evaluator_reference', actor_input_eligible=False, historical_replay_ready=False,
        status='survey_diagnostics_completed_not_accuracy_certified', input_files=evidence,
        source_raster=raster_record, runtime_domain=artifact(root, DOMAIN_PATH),
        runtime_payload_sha256=DOMAIN_SHA256, implementation_files=[artifact(root, 'eval/validate_terrain_controls.py')],
        vertical_reference=dict(product_height_type='orthometric', product_statement_verified=True,
            extract_specific_vertical_realization_verified=False, native_void_semantics_verified=False,
            survey_edition_of_extract_verified=False),
        checks=dict(complete_positive_runtime_domain=True, unsupported_edge_fallback=False,
            terrain_values_modified=False, monument_offset_applied=False, ellipsoidal_heights_used_as_terrain=False),
        points=points,
        limitations=['Survey marks are not necessarily bare ground; no correction or accuracy threshold is inferred.',
            'One in-domain ROI mark and one out-of-domain coastal REDNAP mark are spot checks, not domain-wide certification.',
            'The 500m means are not compared with point heights; only source samples are compared.',
            'Survey evidence is separate from encoding checks but independence from original production control is unverified.',
            'Product-level orthometric documentation does not authenticate the exact WCS vertical realization, nil values or survey edition.',
            'Complete positive support is an explicit analysis restriction, not a source NoData declaration or physical calibration.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    report = analyse(options.root)
    with options.output.open('xb') as stream:
        stream.write(encoded(report))
    print(json.dumps(dict(status=report['status'], points=report['points']), indent=2))


if __name__ == '__main__':
    main()
