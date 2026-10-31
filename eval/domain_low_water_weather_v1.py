"""Opt-in low-water source sensitivity inputs; unscored and uncalibrated.

Caller explicitly selects one source-supported class across all newly admitted
cells and a documented crossing factor. Unknowns remain censored. This is a
separate evaluator-side input identity, not a measurement or an old release.
"""
import copy
import math
from pathlib import Path

import adapter_v1 as api
from eval import domain_profile_weather_v2 as parent_backend
from eval import domain_inland_low_water_profile_v1 as compiler
from eval.extended_map_common import file_sha256, serialize
from wildfire_expanded_incident_v1 import estimated_fuel_v1 as estimated
from wildfire_expanded_incident_v1.estimated_weather_v1 import EstimatedWeatherFireV1
from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED, _file_sha

VERSION='wildfire-domain-low-water-weather-input-v1'
PROFILE_PIN='a81762b1ace2351af87eba03b8378d7b933a834291b32f1bdd2bb40eb9a50447'
PARENT_PIN='a17d83005df0f88a5b9cd4e635472debf7246bad66aeaf26ac9539252fb31f59'


def convert_verified(profile,parent_profile,parent_inputs,strict,water,*,candidate_class,crossing_proxy,opt_in=False):
    """Convert pinned reconstructed evidence, preserving all parent unknowns."""
    if opt_in is not True:raise ValueError('explicit low-water sensitivity opt-in required')
    if (type(candidate_class) is not int or candidate_class not in estimated.fire_v1.FUEL_MAPPING
            or type(crossing_proxy) not in (int,float) or not math.isfinite(crossing_proxy)
            or float(crossing_proxy) not in compiler.CROSSING_SCENARIOS.values()):
        raise ValueError('select an explicit valid candidate class and documented crossing factor')
    if (profile.get('version')!=compiler.VERSION or profile.get('sha256')!=PROFILE_PIN
            or api._hash({k:v for k,v in profile.items() if k!='sha256'})!=PROFILE_PIN
            or profile.get('parent_profile_sha256')!=PARENT_PIN
            or parent_profile.get('sha256')!=PARENT_PIN
            or api._hash({k:v for k,v in parent_profile.items() if k!='sha256'})!=PARENT_PIN
            or dict(parent_inputs.identity).get('profile_sha256')!=PARENT_PIN
            or profile.get('grid')!=parent_profile.get('grid')
            or profile.get('source_pins')!=parent_profile.get('source_pins')):
        raise ValueError('low-water profile or parent pin differs')
    records=profile['cells'];parents=parent_profile['cells'];c=parent_inputs.base.contract
    if len(records)!=len(parents) or len(records)!=c.width*c.height:raise ValueError('profile dimensions differ')
    old={(r['column'],r['row']):r for r in parents}
    if len(old)!=len(parents) or len({(r['column'],r['row']) for r in records})!=len(records):
        raise ValueError('duplicate profile cell')
    matrices={k:[list(row) for row in getattr(c,k)] for k in ('surface','fuel','elevation','access')}
    factors=list(parent_inputs.base.factors);estimates=list(parent_inputs.estimates)
    mask=[list(row) for row in parent_inputs.estimated_mask];changes=[]
    for record in records:
        x,y=record['column'],record['row'];previous=old.get((x,y))
        if previous is None or not (0<=x<c.width and 0<=y<c.height):raise ValueError('invalid profile coordinates')
        if not record['status'].startswith('new_low_water_'):
            # Failed screen notes do not admit a cell and do not change the parent input.
            compare={k:v for k,v in record.items() if k!='low_water_screen'}
            if compare!=previous:raise ValueError('non-low-water parent cell changed')
            continue
        if (previous['status']!='unknown' or record['reason'] is not None
                or record['v2_surface']!='unknown' or record['surface_assumption']!='mixed_synthetic_sensitivity'
                or strict['cells'][y][x]['surface']!='unknown' or water['cells'][y][x]['surface']!='unknown'
                or c.surface[y][x]!='unknown' or c.elevation[y][x] is None
                or record['source_fuel_class']!=previous['source_fuel_class']
                or record['source_fuel_class']!=water['cells'][y][x]['source_fuel_class']
                or record['source_fuel_class']!=c.fuel[y][x]):
            raise ValueError('new low-water cell fails parent, source or terrain checks')
        ev=record['low_water_evidence'];hist=water['cells'][y][x]['worldcover_samples']
        if (ev['both_independent_mapped_land_faces_cover_cell'] is not True
                or ev['outside_both_250m_coast_buffers'] is not True
                or ev['dera_water_polygon_intersection'] is not False
                or ev['osm_water_area_intersection'] is not False
                or ev['worldcover_samples']!=hist
                or compiler.screen_low_water(strict['cells'][y][x],water['cells'][y][x],True,c.elevation[y][x],False,False) is not None):
            raise ValueError('low-water land-cover or water-area evidence differs')
        cross=record['cross_water_sensitivity']
        if (cross['crossing_openness_scenarios']!=compiler.CROSSING_SCENARIOS
                or cross['default'] is not None or cross['strict_censor_alternative'] is not True):
            raise ValueError('crossing sensitivity evidence differs')
        alternatives=record['synthetic_fuel_class_alternatives']
        classes=[item['assumed_fuel_class'] for item in alternatives]
        if classes!=sorted(set(classes)) or candidate_class not in classes:
            raise ValueError('selected class is not supported for every low-water cell')
        for item in alternatives:
            k=item['assumed_fuel_class'];mapped=estimated.fire_v1.FUEL_MAPPING.get(k)
            if (mapped is None or item['assumed_terrain']!=mapped['terrain']
                    or item['exercise_base_initial_fuel']!=mapped['initial_fuel']
                    or item['fuel_factor_assumptions']!=dict(compiler.prior.source.EXERCISE_FACTORS)):
                raise ValueError('fuel class assumption mapping differs')
        if record['status']=='new_low_water_mixed_source':
            if record['source_fuel_class']!=candidate_class or matrices['fuel'][y][x]!=candidate_class:
                raise ValueError('selected class differs from retained source')
            matrices['surface'][y][x]='mixed';factors.append((x,y,.9,float(crossing_proxy)))
        elif record['status'] in ('new_low_water_mixed_fuel_estimated_hypothesis',
                                  'new_low_water_mixed_fuel_ambiguous_sensitivity_hypothesis',
                                  'new_low_water_mixed_fuel_tied_sensitivity_only'):
            if record['source_fuel_class'] is not None or matrices['fuel'][y][x] is not None:
                raise ValueError('fuel estimate cannot replace source fuel')
            h=record['fuel_hypothesis'];votes={str(k):v for k,v in sorted(__import__('collections').Counter(s['source_class'] for s in record['source_neighbor_support']).items())}
            if h['status']!=record['status'].removeprefix('new_low_water_mixed_fuel_') or h.get('source_votes')!=votes:
                raise ValueError('source neighbor votes differ')
            estimates.append((x,y,candidate_class,.9,float(crossing_proxy)));mask[y][x]=True
        else:raise ValueError('unsupported low-water profile status')
        changes.append((x,y,candidate_class,float(crossing_proxy)))
    if not changes:raise ValueError('empty low-water extension')
    doc=dict(version=api._contract_module.VERSION,grid=dict(width=c.width,height=c.height,crs=c.crs,
        transform=list(c.transform)),domain_mask=[list(row) for row in c.domain],source_pins=dict(c.source_pins),**matrices)
    changed=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
    base_id=dict(parent_inputs.base.identity);base_id.update(parent_input_sha256=c.document_sha256,
        input_sha256=changed.document_sha256,low_water_profile_sha256=PROFILE_PIN,
        low_water_converter_sha256=_file_sha(__file__),candidate_class=candidate_class,crossing_proxy_low_water=float(crossing_proxy))
    mixed=MixedInputsV2(changed,tuple(factors),tuple(sorted(base_id.items())),_VERIFIED)
    mask=tuple(tuple(row) for row in mask)
    identity=dict(version=VERSION,base=base_id,parent_profile_sha256=PARENT_PIN,profile_sha256=PROFILE_PIN,
        converter_sha256=_file_sha(__file__),candidate_class=candidate_class,crossing_proxy=float(crossing_proxy),
        added_choices_sha256=api._hash(changes),source_mask_sha256=api._hash(parent_inputs.source_mask),
        estimated_mask_sha256=api._hash(mask),fuel_factor=.9)
    return estimated.EstimatedInputsV1(mixed,tuple(estimates),parent_inputs.source_mask,mask,
                                      tuple(sorted(identity.items())),estimated._VERIFIED_ESTIMATE)


def prepare(profile,parent_profile,strict,water,v3,v4,parent,catalog,*,fuel_archive,candidate_class,low_water_crossing,opt_in=False,**options):
    """Independently rebuild low-water evidence from locally pinned source layers."""
    if opt_in is not True:raise ValueError('explicit low-water sensitivity opt-in required')
    if profile.get('sha256')!=PROFILE_PIN or api._hash({k:v for k,v in profile.items() if k!='sha256'})!=PROFILE_PIN:
        raise ValueError('independently pinned low-water profile differs')
    compiler_pin=file_sha256(compiler.__file__)
    if compiler_pin!=profile.get('extension_code_sha256'):raise ValueError('low-water compiler source differs')
    prior=compiler.prior;mapping=prior.mapping_from_pinned_source()
    # Parent converter independently verifies parent pins, water-v4 geometry,
    # v4 provenance and the fixed original assumptions before any extension.
    parent_inputs=parent_backend.prepare(parent_profile,strict,water,v3,v4,parent,catalog,opt_in=True,**options)
    _,cover,_,interior=prior.source.surface_inputs(options['coast_path'],options['water_path'],options['worldcover_path'],
        options['osm_manifest_path'],options['osm_samples_path'],include_interior=True)
    if any(cover[y][x]!=water['cells'][y][x]['worldcover_samples']
           for y in range(len(cover)) for x in range(len(cover[y]))):raise ValueError('source land-cover differs')
    features,_,_=prior.load_pinned_osm_water(options['osm_manifest_path'],options['osm_samples_path'])
    water_geoms=prior.read_pinned_collection(options['water_path'],prior.source.PINS['water'],94,'DERA water')
    river_features,river_geoms=prior.read_pinned_collection(options['dera_rivers_path'],prior.source.DERA_RIVER_PIN,410,'DERA rivers',include_features=True)
    grid=water['grid'];rivers=prior.river_cells_from_verified_features(river_features,river_geoms,
        grid['width'],grid['height'],grid['transform'][2],grid['transform'][5])
    archive=prior.source.base.sample_fuel(fuel_archive,parent,width=grid['width'],height=grid['height'],
        west=grid['transform'][2],north=grid['transform'][5],parent_row=prior.source.base.ROW,parent_col=prior.source.base.COL)
    rebuilt=compiler.extend_profile(parent_profile,strict,water,v4,interior,rivers,archive,features,water_geoms,river_geoms,mapping,compiler_pin)
    if file_sha256(compiler.__file__)!=compiler_pin or serialize(rebuilt)!=serialize(profile):
        raise ValueError('low-water profile differs from independent source reconstruction')
    return convert_verified(profile,parent_profile,parent_inputs,strict,water,candidate_class=candidate_class,
                            crossing_proxy=low_water_crossing,opt_in=True)


class LowWaterWeatherFireV1(EstimatedWeatherFireV1):
    """Same fire/weather engine with separately versioned input and provenance."""
    def __init__(self,inputs,*args,**kwargs):
        if dict(inputs.identity).get('version')!=VERSION:raise ValueError('low-water inputs required')
        super().__init__(inputs,*args,**kwargs)
        self._identity['low_water_backend']={'version':VERSION,'sha256':_file_sha(__file__)}

    def _ensure(self):
        super()._ensure()
        if self._identity['low_water_backend']['sha256']!=_file_sha(__file__):
            self._poisoned=True;raise RuntimeError('low-water backend changed')
