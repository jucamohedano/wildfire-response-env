"""Document MDT05 units/encoding and restricted gradients, without enabling physics."""
import argparse
import json
from pathlib import Path

from eval.check_terrain_gradients import horn3x3
from eval.prepare_clean_malaga_terrain import encoded, file_record, screen_values


VERSION = 'wildfire-mdt05-relative-gradient-validation-v1'
DIRECTORY = '.runtime/malaga-terrain-validation-v2/'
PINS = {
    'mdt05-sampled25m-halo.asc': '1b2e4f470bf456b0189cf22e60dddcf09af413617eb9b76f5a2c1650be70e917',
    'mdt05-sampled25m-halo.tif': '8b2e91a5f486358a75a00af8820e65e5b899ecf6a0f909fa39484906eeef65a7',
    'mdt05-description.xml': 'a92a29c071dcdb991fea98a89dd1105d1fd85bc81702b01f787ebffb4b40de04',
    'ign-wcs-2017-slides.pdf': 'af354454dc544a12bd90d60219dde2181bbc1f5689af0c9fbbe6e0fae72f4b61',
}
TRANSFORM = (500, 0, 297000, 0, -500, 4054500)
DESCRIPTION_URL = 'https://www.idee.es/resources/presentaciones/JIIDE17/Apresentacoes_JIIDE2017_WCS_Inspire_modelos_digitales_terreno_IGN_Carolina_Soteres.pdf'


def validate_encoding(heights, integer_heights):
    import numpy
    if heights.shape != (860, 780) or integer_heights.shape != heights.shape:
        raise ValueError('Unexpected MDT05 sample dimensions')
    screen_values(heights.ravel().tolist())
    differences = numpy.abs(heights.astype('float64') - integer_heights)
    if not numpy.all(differences <= .5005):
        raise ValueError('Floating-point and integer encodings do not agree at unit scale')
    return dict(compared_cells=int(heights.size), max_absolute_difference_metres=float(differences.max()),
                within_half_metre_rounding=True, applied_height_scale=1.0,
                interpretation='Encoding consistency, not independent height accuracy or producer correction of band metadata')


def analyse(root, output):
    import numpy
    import rasterio
    from rasterio.warp import reproject, Resampling

    root = Path(root).resolve()
    output = Path(output)
    if not output.is_absolute():
        output = root / output
    if output.exists() or output.is_symlink() or not output.resolve().is_relative_to(root / '.runtime'):
        raise ValueError('Choose a fresh directory inside project .runtime')
    records = [file_record(root, DIRECTORY + name, expected)[1] for name, expected in PINS.items()]
    with rasterio.Env(PROJ_NETWORK='OFF', GDAL_PAM_ENABLED='NO'):
        with rasterio.open(root / DIRECTORY / 'mdt05-sampled25m-halo.asc') as ascii_source:
            heights = ascii_source.read(1)
            ascii_transform = ascii_source.transform
            if ascii_source.nodata is not None or ascii_source.scales != (1.0,) or ascii_source.offsets != (0.0,):
                raise ValueError('Unexpected ASCII scaling or native missing-value declaration')
        with rasterio.open(root / DIRECTORY / 'mdt05-sampled25m-halo.tif') as integer_source:
            integer = integer_source.read(1)
            if (integer_source.crs.to_string() != 'EPSG:25830' or integer_source.nodata is not None
                    or integer_source.scales != (1.0,) or integer_source.offsets != (0.0,)
                    or not numpy.allclose(list(integer_source.transform)[:6], list(ascii_transform)[:6], atol=1e-7, rtol=0)
                    or not numpy.all(integer_source.read_masks(1) == 255)):
                raise ValueError('Inconsistent numeric encodings or coordinate frame')
            encoding = validate_encoding(heights, integer)
            transform = integer_source.transform
            if not numpy.allclose(list(transform)[:6], [25, 0, 296500, 0, -25, 4055000], atol=1e-7, rtol=0):
                raise ValueError('Unexpected sampled geometry')
        positive = numpy.isfinite(heights) & (heights > 0)
        degrees = numpy.full(heights.shape, numpy.nan, dtype='float64')
        valid = numpy.zeros(heights.shape, dtype='uint8')
        for start in range(1, heights.shape[0] - 1, 64):
            stop = min(start + 64, heights.shape[0] - 1)
            result = horn3x3(heights[start - 1:stop + 1].tolist(), cell_size_x_metres=25,
                            cell_size_y_metres=25, vertical_scale_metres=1,
                            valid_mask=positive[start - 1:stop + 1].tolist())
            degrees[start:stop] = numpy.asarray(result.slope_degrees[1:-1], dtype='float64')
            valid[start:stop] = numpy.asarray(result.valid_mask[1:-1], dtype='uint8')
        reference_heights = heights.astype('float64')
        independent = numpy.degrees(numpy.arctan(numpy.hypot(
            ((reference_heights[:-2, 2:] + 2 * reference_heights[1:-1, 2:] + reference_heights[2:, 2:])
             - (reference_heights[:-2, :-2] + 2 * reference_heights[1:-1, :-2] + reference_heights[2:, :-2])) / 200,
            ((reference_heights[2:, :-2] + 2 * reference_heights[2:, 1:-1] + reference_heights[2:, 2:])
             - (reference_heights[:-2, :-2] + 2 * reference_heights[:-2, 1:-1] + reference_heights[:-2, 2:])) / 200)))
        selected = valid[1:-1, 1:-1].astype('bool')
        error = float(numpy.max(numpy.abs(independent[selected] - degrees[1:-1, 1:-1][selected])))
        if error > 1e-10:
            raise ValueError('Independent vectorized gradient check failed')
        complete = numpy.zeros((41, 37), dtype='float32')
        mean_degrees = numpy.full((41, 37), numpy.nan, dtype='float32')
        for values, destination, missing in ((valid.astype('float32'), complete, None),
                                             (degrees, mean_degrees, numpy.nan)):
            reproject(source=values, destination=destination, src_transform=transform, src_crs='EPSG:25830',
                dst_transform=rasterio.Affine(*TRANSFORM), dst_crs='EPSG:25830', src_nodata=missing,
                dst_nodata=numpy.nan, resampling=Resampling.average, num_threads=1)
        full = complete >= 1 - 1e-6
        mean_degrees[~full] = numpy.nan
        output.mkdir(parents=True)
        outputs = []
        specifications = [
            ('diagnostic_slope_degrees_sampled25m.tif', degrees.astype('float32'), transform, numpy.nan),
            ('restricted_gradient_mask_sampled25m.tif', valid, transform, None),
            ('mean_sampled_slope_degrees_500m.tif', mean_degrees, rasterio.Affine(*TRANSFORM), numpy.nan),
            ('gradient_coverage_fraction_500m.tif', complete, rasterio.Affine(*TRANSFORM), None),
        ]
        for name, values, frame, nodata in specifications:
            path = output / name
            with rasterio.open(path, 'w', driver='GTiff', height=values.shape[0], width=values.shape[1],
                count=1, dtype=str(values.dtype), crs='EPSG:25830', transform=frame, nodata=nodata,
                compress='deflate') as destination:
                destination.write(values, 1)
                destination.update_tags(role='restricted_offline_analysis_not_simulator_physics', slope_enabled='false',
                    source_product='MDT05_sampled_at25m_not_native5m', height_units_basis='producer_format_documentation')
            outputs.append(file_record(root, path.relative_to(root))[1])
    report = dict(version=VERSION, checked_on='2026-10-25', role='evaluator_diagnostic',
        status='documented_metre_units_and_encoding_checked_restricted_gradients_only',
        actor_input_eligible=False, simulator_input_eligible=False, slope_enabled=False,
        historical_replay_ready=False, input_files=records,
        implementation_files=[file_record(root, name)[1] for name in
                              ('eval/validate_malaga_terrain_metadata.py', 'eval/check_terrain_gradients.py')],
        output_files=outputs,
        product=dict(coverage='Elevacion25830_5', nominal_resolution_metres=5,
            returned_sample_spacing_metres=25, resampling='WCS nearest neighbor; not native5m or native25m coverage',
            epsg=25830, source_ascii_has_crs=False, crs_basis='Matching request/GeoTIFF bounds and affine transform'),
        height_units=dict(value='metre', basis='Explicit IGN producer MDT05 ASCII-format statement',
            evidence_url=DESCRIPTION_URL, pdf_pages=[10, 19],
            locator='Page10 MDT5 heading and z11/z12 range-value description; page19 identifies WCS products',
            source_band_unit_tag_present=False, live_service_uom_conflict_resolved_by_producer=False,
            previous_MDT25_candidate_relabelled=False), encoding=encoding,
        masks=dict(source_nodata_declared=False, producer_void_semantics_verified=False,
            restricted_analysis_rule='Exclude nonpositive heights and any stencil touching them; keep outer border masked. This is deliberate analysis exclusion, NOT a native NoData claim.',
            excluded_nonpositive_samples=int((~positive).sum()), valid_gradient_samples=int(valid.sum()),
            full_coverage_500m_cells=int(full.sum()), total_500m_cells=int(full.size)),
        checks=dict(independent_vectorized_max_error_degrees=error, analytic_fixture_tests=21,
            gradient_computed_before_500m_aggregation=True, masked_cells_not_filled=True,
            constant_vertical_offset_cancels_in_tests=True, physics_approval=False),
        limitations=['Encoding comparison shares one provider; it is not independent surveying or ground truth.',
            'Heights in metres are documented for MDT05 ASCII; missing native tags and SWE radiance default remain recorded.',
            'The restriction masks genuine sea-level/negative heights as well as possible voids; no values are repaired.',
            'Exact vertical datum, local survey date, live source edition and historical availability are unknown.',
            'A constant height offset cancels in local gradients; varying datum transformations or source mosaics still require care.',
            'Sampled25m slopes omit5m detail; mean local slopes are not the slope of500m mean elevation.',
            'Runtime slope and slope-to-fire/movement coupling remain disabled and uncalibrated.'])
    (output / 'audit.json').write_bytes(encoded(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    report = analyse(options.root, options.output)
    print(json.dumps(dict(status=report['status'], encoding=report['encoding'], masks=report['masks'],
                          checks=report['checks']), indent=2))


if __name__ == '__main__':
    main()
