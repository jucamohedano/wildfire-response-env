"""Fail-closed v4 small-water-area exercise conversion after reconstruction."""
import copy
import math

import adapter_v1 as api
from .mixed_v2 import MixedInputsV2, _VERIFIED, _compiler, _validator_source, _file_sha
from .source_converter_v1 import _checked_document, _road
from . import source_gate_v1

VERSION = 'wildfire-small-water-mixed-exercise-input-v4'


def prepare_exercise_v4(strict, water, overlay_v3, overlay_v4, parent, catalog, *,
                        strict_pin, water_pin, v3_pin, v4_pin, parent_pin, catalog_pin,
                        road_path, road_pin, coast_path, water_path, worldcover_path,
                        osm_manifest_path, osm_samples_path, dera_rivers_path,
                        scenario, crossing_proxy, opt_in_mixed_experiment=False):
    """Reconstruct v3/v4 from pinned raw evidence before permitting mixed cells.

    An unavailable, mismatched or unvalidated v4 document is rejected entirely;
    it cannot replace any unknown cell with a supported cell.
    """
    if opt_in_mixed_experiment is not True:
        raise ValueError('explicit v4 mixed-experiment opt-in required')
    source_gate_v1.require_validated_maps(strict_pin, water_pin)
    gate_pin=_file_sha(source_gate_v1.__file__)
    if scenario not in ('low','nominal','high'):
        raise ValueError('choose low, nominal or high')
    if type(crossing_proxy) not in (int,float) or not math.isfinite(crossing_proxy) or not 0 < crossing_proxy < 1:
        raise ValueError('explicit crossing proxy strictly between zero and one required')
    strict=_checked_document(strict,strict_pin,'extended-map-resolution-evidence-v1')
    water=_checked_document(water,water_pin,'extended-map-resolution-evidence-v2')
    v3=_checked_document(overlay_v3,v3_pin,'extended-map-inland-mixed-exercise-v3')
    v4=_checked_document(overlay_v4,v4_pin,'extended-map-small-water-exercise-v4')
    parent=_checked_document(parent,parent_pin,'incident-day-inputs-v2')
    catalog=_checked_document(catalog,catalog_pin,'wildfire-incident-eval-catalog-v1')
    if (water.get('status')!='private_evidence_not_simulator_input'
            or v3.get('status')!='private_exercise_hypothesis_not_v1_input'
            or v4.get('status')!='private_exercise_hypothesis_not_v1_input'
            or v3.get('parent_v2_sha256')!=water_pin or v4.get('parent_v2_sha256')!=water_pin
            or v4.get('parent_v3_sha256')!=v3_pin or v4.get('source_catalog_sha256')!=catalog_pin
            or catalog.get('inputs_sha256')!=parent_pin or catalog.get('network_sha256')!=parent.get('network_sha256')):
        raise ValueError('v4 source lineage or status differs')
    compiler=_compiler(); compiler_pin=_validator_source(compiler)
    _,cover,_,interior=compiler.surface_inputs(coast_path,water_path,worldcover_path,
        osm_manifest_path,osm_samples_path,include_interior=True)
    if any(cover[y][x]!=strict['cells'][y][x]['worldcover_samples']
           or cover[y][x]!=water['cells'][y][x]['worldcover_samples']
           for y in range(len(cover)) for x in range(len(cover[y]))):
        raise ValueError('reconstructed raster samples differ from pinned evidence')
    rebuilt_v3=compiler.compile_exercise_v3(strict,water,interior,catalog,parent)
    if api._encoded(rebuilt_v3)!=api._encoded(v3):
        raise ValueError('v3 parent differs from source reconstruction')
    features,_,metadata=compiler.load_osm_water(osm_manifest_path,osm_samples_path,include_way_metadata=True)
    grid=water['grid']; w,h=grid['width'],grid['height']
    rivers=compiler.dera_river_conflicts(dera_rivers_path,w,h,grid['bounds'][0],grid['bounds'][3])
    rebuilt=compiler.compile_exercise_v4(water,rebuilt_v3,interior,features,metadata,rivers,catalog,parent)
    if (_validator_source(compiler)!=compiler_pin or _file_sha(source_gate_v1.__file__)!=gate_pin
            or api._encoded(rebuilt)!=api._encoded(v4)):
        raise ValueError('v4 overlay differs from independent source reconstruction')
    road=_road(road_path,road_pin)
    if grid.get('crs')!=road['crs'] or grid.get('bounds')!=road['bounds']:
        raise ValueError('road and v4 grids differ')
    factors={(r['column'],r['row']):float(r['fuel_factor_assumptions'][scenario]) for r in rebuilt['cells']}
    if len(factors)!=len(rebuilt['cells']) or any(not 0 < value <= 1 for value in factors.values()):
        raise ValueError('invalid v4 mixed factors')
    extra={(r['column'],r['row']) for r in rebuilt['additional_dera_censors']}
    if len(extra)!=len(rebuilt['additional_dera_censors']) or extra.intersection(factors):
        raise ValueError('conflicting v4 censor and mixed cells')
    surface,fuel,elevation=[],[],[]
    for y in range(h):
        ss,fs,es=[],[],[]
        for x in range(w):
            cell=water['cells'][y][x];status,s=cell['fuel_status'],cell['surface']
            if status not in ('source','estimated','unknown') or s not in ('land','sea','unknown'):
                raise ValueError('invalid water v2 classification')
            source=cell['source_fuel_class']
            if status=='source':
                if type(source) is not int or not 1<=source<=10:raise ValueError('invalid source fuel')
            elif source is not None:raise ValueError('estimated fuel cannot be promoted')
            if (x,y) in factors and (s not in ('land','unknown') or status!='source'):
                raise ValueError('v4 mixed cell lacks source-backed fuel')
            effective='mixed' if (x,y) in factors else 'unknown' if (x,y) in extra else s
            ss.append(effective);fs.append(source if status=='source' else None)
            es.append(None if effective=='sea' else water['terrain_candidate'][y][x])
        surface.append(ss);fuel.append(fs);elevation.append(es)
    pins=dict(surface=water_pin,fuel=water_pin,elevation=water_pin,access=road_pin)
    doc=dict(version=api._contract_module.VERSION,
        grid={k:copy.deepcopy(grid[k]) for k in ('width','height','crs','transform')},
        domain_mask=[[True]*w for _ in range(h)],surface=surface,fuel=fuel,elevation=elevation,
        access=[['unknown']*w for _ in range(h)],source_pins=pins)
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=pins,opt_in=True)
    identity=dict(version=VERSION,strict=strict_pin,water_v2=water_pin,overlay_v3=v3_pin,overlay_v4=v4_pin,
        catalog=catalog_pin,parent=parent_pin,road=road_pin,compiler=compiler_pin,
        converter_sha256=_file_sha(__file__),converter_kind='v4',scenario=scenario,
        source_gate_sha256=gate_pin,source_gate=source_gate_v1.VERSION,
        input_sha256=contract.document_sha256,dera_rivers=rebuilt['dera_rivers_response_sha256'],
        crossing_rule='explicit-uniform-watercourse-product-exercise-v1',crossing_proxy=float(crossing_proxy))
    return MixedInputsV2(contract,tuple((x,y,factors[(x,y)],float(crossing_proxy)) for x,y in sorted(factors)),
                         tuple(sorted(identity.items())),_VERIFIED)
