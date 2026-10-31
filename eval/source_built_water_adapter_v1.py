"""Private staging adapter for a pinned domain-wide built/water sensitivity.

Only explicitly selected cells receive synthetic component assumptions. The
parent source staging, unknowns, source classes and private audit remain intact.
This module creates no fire/weather owner, observations, or files.
"""
import copy
import hashlib
from pathlib import Path

from eval import source_two_component_adapter_v2 as base
from eval import two_component_fire_v2 as model
from eval.extended_map_common import digest

VERSION='wildfire-private-built-water-two-component-staging-v1'
PROFILE_PIN='7229fdd31e5f0a30ce4c4ef1cddebfc72c847886e8f8bb25362c60794a92abc9'
PARENT_PIN='a81762b1ace2351af87eba03b8378d7b933a834291b32f1bdd2bb40eb9a50447'
STAGE_PIN='45084a5a68a7111b834514996049b60c6eb374f00b72ee2f895bab3151bb4216'
MODEL_PIN='fd0ca091ccc6f50d0f122b4305ab2d0effa69d61196a714f05cf76032e7027b3'
COMPILER_PIN='24e00998a658dfa801fb1dd07cc9c1af31bdcc8974506f8b3587fd07b8067bab'
_BASE_ADAPT=base.adapt


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _check_implementation():
    if (_sha(base.__file__)!=STAGE_PIN or _sha(model.__file__)!=MODEL_PIN or base.adapt is not _BASE_ADAPT
            or base.PROFILE_PIN!=PARENT_PIN or base.MODEL_PIN!=MODEL_PIN):
        raise ValueError('parent staging or model source differs')
    base._check_implementation()


def adapt(profile,parent,strict,water,*,bbox,selections,sea_options,staging_transfer_anchor,max_cells,opt_in=False):
    if opt_in is not True:raise ValueError('explicit built-water sensitivity opt-in required')
    _check_implementation();own_pin=_sha(__file__)
    profile,parent,strict,water=copy.deepcopy((profile,parent,strict,water))
    selections,sea_options,staging_transfer_anchor=copy.deepcopy((selections,sea_options,staging_transfer_anchor))
    if (profile.get('version')!='wildfire-domain-built-water-source-sensitivity-profile-v1'
            or profile.get('sha256')!=PROFILE_PIN or digest({k:v for k,v in profile.items() if k!='sha256'})!=PROFILE_PIN
            or profile.get('parent_profile_sha256')!=PARENT_PIN or profile.get('extension_code_sha256')!=COMPILER_PIN
            or parent.get('sha256')!=PARENT_PIN or digest({k:v for k,v in parent.items() if k!='sha256'})!=PARENT_PIN
            or profile['grid']!=parent['grid'] or profile['source_pins']!=parent['source_pins']):
        raise ValueError('independently pinned built-water profile differs')
    records={(r['column'],r['row']):r for r in profile['cells']};old={(r['column'],r['row']):r for r in parent['cells']}
    if len(records)!=len(profile['cells']) or set(records)!=set(old):raise ValueError('profile cell coverage differs')
    extras={pos for pos,r in records.items() if r['status']=='new_built_watercourse_source_sensitivity'}
    if type(selections) is not dict or type(sea_options) is not dict:raise ValueError('explicit selections required')
    for pos,r in records.items():
        if pos not in extras and r!=old[pos]:raise ValueError('parent profile cell changed')
        if pos in extras and old[pos]['status']!='unknown':raise ValueError('extension replaced a supported parent cell')
    for pos in set(selections)&extras:base.stage._validate_choice(selections[pos])
    ordinary={pos:choice for pos,choice in selections.items() if pos not in extras}
    staged=base.adapt(parent,strict,water,bbox=bbox,selections=ordinary,sea_options=sea_options,
        staging_transfer_anchor=staging_transfer_anchor,max_cells=max_cells,opt_in=True)
    x0,y0,w,h=bbox
    if any(not(x0<=pos[0]<x0+w and y0<=pos[1]<y0+h) for pos in set(selections)&extras):
        raise ValueError('extension selection outside crop')
    audits={(r['column'],r['row']):r for r in staged['private_audit']['cells']}
    selected=0
    for x,y in sorted(set(selections)&extras):
        pos=(x,y);rec=records[pos];previous=old[pos];cell=water['cells'][y][x];original=strict['cells'][y][x]
        choice=selections[pos];ev=rec['evidence'];hist=cell['worldcover_samples'];source=cell['source_fuel_class']
        if (original['surface']!='land' or cell['surface']!='unknown' or cell['fuel_status']!='source'
                or source is None or previous['source_fuel_class']!=source or rec['source_fuel_class']!=source
                or ev['source_fuel_class']!=source or ev['source_fuel_provenance']!=cell['fuel_provenance']
                or ev['worldcover_samples']!=hist or ev['osm_water_conflicts']!=cell.get('osm_water_conflicts',[])
                or ev['strict_inland_land'] is not True
                or ev['both_land_faces_and_coastal_water_screen'] is not True
                or ev['mapped_water_area_intersection'] is not False
                or water['terrain_candidate'][y][x] is None or choice['vegetation_class']!=source):
            raise ValueError('extension source evidence or selected class differs')
        veg=sum(hist.get(k,0) for k in ('10','20','30','40'));built=hist.get('50',0);bare=hist.get('60',0)
        if (sum(hist.values())!=2500 or not veg or not built or veg+built+bare!=2500
                or rec['sample_proportions']!={'vegetation':veg/2500,'built_up':built/2500,'bare':bare/2500}):
            raise ValueError('mixed sample proportions differ')
        if (choice['crossing_openness'] is not None and choice['crossing_openness'] not in rec['sensitivity']['crossing_openness']):
            raise ValueError('selected crossing is outside explicit scenarios')
        if choice['vegetation_absence']=='synthetic_absent' or choice['built_absence']=='synthetic_absent':
            raise ValueError('selected absence conflicts with mapped samples')
        vx=base._component('vegetation',veg,choice);bx=base._component('built',built,choice)
        inert=base._inert_share(bare,0,0,choice);lx,ly=x-x0,y-y0
        staged['components'][ly][lx]={'vegetation':vx,'built':bx,'inert':inert}
        staged['crossings'][ly][lx]=choice['crossing_openness'] if choice['crossing_openness'] is not None else 'unknown'
        staged['transfers'][ly][lx]=copy.deepcopy(choice['transfer'])
        unknown=(vx=='unknown' or bx=='unknown' or inert=='unknown' or
                 (type(inert) is dict and inert.get('assumption')=='unknown') or staged['crossings'][ly][lx]=='unknown')
        audits[pos].update(v3_status='unknown' if unknown else 'synthetic_built_water_sensitivity',
                           extension_evidence=copy.deepcopy(rec),extension_assumptions=copy.deepcopy(choice))
        selected+=1
    model._validate_cells(staged['components'],w,h);model._validate_assumptions(staged['transfers'],staged['crossings'],w,h)
    _check_implementation()
    if _sha(__file__)!=own_pin:raise ValueError('adapter changed during staging')
    audit={'version':VERSION,'bbox':list(bbox),'parent_audit':staged['private_audit'],'extension_profile_sha256':PROFILE_PIN,
           'selected_extension_cells':selected,'limitations':'Synthetic sensitivities; samples are not surveyed footprints or calibration.'}
    identity={'version':VERSION,'profile_sha256':PROFILE_PIN,'parent_profile_sha256':PARENT_PIN,
      'model_sha256':MODEL_PIN,'stage_sha256':STAGE_PIN,'adapter_sha256':own_pin,'bbox':list(bbox),
      'components_sha256':digest(staged['components']),'heights_sha256':digest(staged['heights']),
      'crossings_sha256':digest(staged['crossings']),'transfers_sha256':digest(staged['transfers']),'provenance_sha256':digest(audit)}
    return {'version':VERSION,'role':'private_v2_grid_staging_not_source_fire_owner','grid':copy.deepcopy(staged['grid']),
       'components':staged['components'],'heights':staged['heights'],'crossings':staged['crossings'],
       'transfers':staged['transfers'],'identity':identity,'private_audit':audit}
