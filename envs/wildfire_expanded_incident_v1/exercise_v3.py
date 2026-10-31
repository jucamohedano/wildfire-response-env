"""Independently reconstruct and prepare a v3 inland-watercourse exercise overlay."""
import copy
import math
from pathlib import Path

import adapter_v1 as api
from .mixed_v2 import MixedInputsV2, _VERIFIED, _compiler, _validator_source, _file_sha
from .source_converter_v1 import _checked_document, _road
from . import source_gate_v1

VERSION = 'wildfire-inland-mixed-exercise-input-v3'


def prepare_exercise_v3(strict, water, overlay, parent, catalog, *, strict_pin, water_pin,
                        overlay_pin, parent_pin, catalog_pin, road_path, road_pin,
                        coast_path, water_path, worldcover_path, osm_manifest_path, osm_samples_path,
                        scenario, crossing_proxy, opt_in_mixed_experiment=False):
    """Verify sources, rebuild v3 eligibility and keep all other v2 unknown.

    ``crossing_proxy`` is a separate, explicit exercise assumption, not a
    measurement. All eligible mixed cells use it for each stencil crossing.
    """
    if opt_in_mixed_experiment is not True:
        raise ValueError('explicit v3 mixed-experiment opt-in required')
    source_gate_v1.require_validated_maps(strict_pin, water_pin)
    gate_pin = _file_sha(source_gate_v1.__file__)
    if scenario not in ('low','nominal','high'):
        raise ValueError('choose low, nominal or high')
    if type(crossing_proxy) not in (int,float) or not math.isfinite(crossing_proxy) or not 0 < crossing_proxy < 1:
        raise ValueError('explicit crossing proxy strictly between zero and one required')
    strict = _checked_document(strict, strict_pin, 'extended-map-resolution-evidence-v1')
    water = _checked_document(water, water_pin, 'extended-map-resolution-evidence-v2')
    overlay = _checked_document(overlay, overlay_pin, 'extended-map-inland-mixed-exercise-v3')
    parent = _checked_document(parent, parent_pin, 'incident-day-inputs-v2')
    catalog = _checked_document(catalog, catalog_pin, 'wildfire-incident-eval-catalog-v1')
    if (water.get('status') != 'private_evidence_not_simulator_input'
            or overlay.get('status') != 'private_exercise_hypothesis_not_v1_input'
            or overlay.get('parent_v2_sha256') != water_pin or overlay.get('source_catalog_sha256') != catalog_pin
            or catalog.get('inputs_sha256') != parent_pin or catalog.get('network_sha256') != parent.get('network_sha256')):
        raise ValueError('v3 lineage or source status differs')
    compiler = _compiler()
    compiler_pin = _validator_source(compiler)
    _, cover, _, interior = compiler.surface_inputs(coast_path, water_path, worldcover_path,
        osm_manifest_path, osm_samples_path, include_interior=True)
    if any(cover[y][x] != strict['cells'][y][x]['worldcover_samples']
           or cover[y][x] != water['cells'][y][x]['worldcover_samples']
           for y in range(len(cover)) for x in range(len(cover[y]))):
        raise ValueError('reconstructed raster samples differ from pinned evidence')
    rebuilt = compiler.compile_exercise_v3(strict, water, interior, catalog, parent)
    if (_validator_source(compiler) != compiler_pin or _file_sha(source_gate_v1.__file__) != gate_pin
            or api._encoded(rebuilt) != api._encoded(overlay)):
        raise ValueError('v3 overlay differs from independent source reconstruction')
    road = _road(road_path, road_pin)
    grid = water['grid']
    if grid.get('crs') != road['crs'] or grid.get('bounds') != road['bounds']:
        raise ValueError('road and v3 grids differ')
    selected = {(item['column'],item['row']): float(item['fuel_factor_assumptions'][scenario])
                for item in rebuilt['cells']}
    if len(selected) != len(rebuilt['cells']) or any(not 0 < factor <= 1 for factor in selected.values()):
        raise ValueError('invalid v3 mixed factors')
    w,h=grid['width'],grid['height']
    surface,fuel,elevation=[],[],[]
    for y in range(h):
        ss,fs,es=[],[],[]
        for x in range(w):
            cell=water['cells'][y][x]
            status,s=cell['fuel_status'],cell['surface']
            if status not in ('source','estimated','unknown') or s not in ('land','sea','unknown'):
                raise ValueError('invalid water v2 classification')
            source=cell['source_fuel_class']
            if status == 'source':
                if type(source) is not int or not 1 <= source <= 10: raise ValueError('invalid source fuel')
            elif source is not None:
                raise ValueError('estimated fuel cannot be promoted')
            if (x,y) in selected and (s != 'unknown' or status != 'source'):
                raise ValueError('v3 hypotheses require source-backed unknown cells')
            ss.append('mixed' if (x,y) in selected else s)
            fs.append(source if status == 'source' else None)
            es.append(None if s == 'sea' else water['terrain_candidate'][y][x])
        surface.append(ss);fuel.append(fs);elevation.append(es)
    pins=dict(surface=water_pin,fuel=water_pin,elevation=water_pin,access=road_pin)
    doc=dict(version=api._contract_module.VERSION,
        grid={k:copy.deepcopy(grid[k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*w for _ in range(h)],surface=surface,fuel=fuel,elevation=elevation,
        access=[['unknown']*w for _ in range(h)],source_pins=pins)
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=pins,opt_in=True)
    identity=dict(version=VERSION,strict=strict_pin,water_v2=water_pin,overlay=overlay_pin,
        catalog=catalog_pin,parent=parent_pin,road=road_pin,compiler=compiler_pin,
        converter_sha256=_file_sha(__file__),source_gate_sha256=gate_pin,source_gate=source_gate_v1.VERSION,
        scenario=scenario,input_sha256=contract.document_sha256,
        crossing_rule='explicit-uniform-watercourse-product-exercise-v1',crossing_proxy=float(crossing_proxy))
    factors=tuple((x,y,selected[(x,y)],float(crossing_proxy)) for x,y in sorted(selected))
    return MixedInputsV2(contract,factors,tuple(sorted(identity.items())),_VERIFIED)
