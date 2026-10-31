"""Fixed domain-wide source plus documented v3 fuel sensitivities.

Private evaluator-side policy: no case, frontier, outcome or actor data enter
selection. Source-known cells retain their source class. Eligible v3 estimates
use the documented unique nominal class, or the lowest numeric tied alternative;
that tie-break is a reproducible exercise choice, not a measured winner. All
fuel, water, absence and transfer values below are uncalibrated assumptions.
"""
import copy
import hashlib
from collections import Counter
from pathlib import Path

from eval.extended_map_common import digest
from envs.wildfire_response.regional.prepared import data as fuel_data

VERSION='wildfire-domain-component-fixed-policy-v2'
PROFILE_PIN='a81762b1ace2351af87eba03b8378d7b933a834291b32f1bdd2bb40eb9a50447'
WATER_PIN='a44d432066d6c3039f49032ca21b8a82496dff47c855fee1e07f8e854ab15b87'
MAPPING_FILE_PIN='196ac0aad5daa0b654c53e219df7b00b0df75ffb015c2f66a4fb3636b489efbb'
MAPPING_CONTENT_PIN='e9870ce2a1b16fa8c17ae3b5f6ba0c6c37898abbf608d8658966efd127824802'
SOURCE_STATUSES=frozenset(('mapped_land_source','existing_v4_mixed_source','new_mixed_source'))
ESTIMATE_PREFIXES=('new_land_fuel_','new_mixed_fuel_')
TRANSFER={'vegetation>vegetation':1.,'built>built':1.,'vegetation>built':.2,'built>vegetation':.2}


def _check(doc,pin,version):
    if (type(doc) is not dict or doc.get('version')!=version or doc.get('sha256')!=pin
            or digest({k:v for k,v in doc.items() if k!='sha256'})!=pin):
        raise ValueError('independently pinned policy source differs')


def _estimate(record):
    h=record['fuel_hypothesis'];kind=h['status'];votes=h['source_votes']
    if h['confidence']!='low_uncalibrated':raise ValueError('fuel hypothesis confidence differs')
    if kind=='tied_sensitivity_only':
        options=h['scenario_classes']
        if (type(options) is not list or len(options)<2 or options!=sorted(set(options))
                or h['nominal_class'] is not None):raise ValueError('invalid tied alternatives')
        klass=options[0]
        if any(str(k) not in votes for k in options):raise ValueError('tied classes lack source votes')
        base=fuel_data.FUEL_MAPPING[klass]['initial_fuel'];fuel=round(base*.9,6)
    elif kind in ('estimated_hypothesis','ambiguous_sensitivity_hypothesis'):
        item=h['scenarios']['nominal'];klass=item['assumed_fuel_class'];fuel=item['modeled_initial_fuel']
        if (klass!=h['nominal_class_assumption'] or klass not in fuel_data.FUEL_MAPPING
                or item['fuel_factor_assumption']!=.9
                or item['assumed_terrain']!=fuel_data.FUEL_MAPPING[klass]['terrain']
                or item['exercise_base_initial_fuel']!=fuel_data.FUEL_MAPPING[klass]['initial_fuel']
                or abs(fuel-round(item['exercise_base_initial_fuel']*.9,6))>1e-9):
            raise ValueError('documented nominal fuel differs')
    else:raise ValueError('unsupported profile hypothesis')
    if type(klass) is not int or klass not in fuel_data.FUEL_MAPPING or str(klass) not in votes:
        raise ValueError('selected class lacks source-neighbor support')
    return klass,float(fuel),kind


def build_policy(profile,water):
    """Return explicit private selections and a privacy-safe aggregate identity."""
    _check(profile,PROFILE_PIN,'wildfire-domain-inland-low-water-sensitivity-profile-v1')
    _check(water,WATER_PIN,'extended-map-resolution-evidence-v2')
    if (profile['source_pins']['water_v2']!=WATER_PIN or profile['grid']!=water['grid']
            or profile['source_pins']['fuel_mapping']!=MAPPING_FILE_PIN
            or hashlib.sha256(Path(fuel_data.__file__).read_bytes()).hexdigest()!=MAPPING_FILE_PIN
            or digest(fuel_data.FUEL_MAPPING)!=MAPPING_CONTENT_PIN):raise ValueError('policy source lineage differs')
    w,h=profile['grid']['width'],profile['grid']['height'];records=profile['cells']
    if len(records)!=w*h or {(r['column'],r['row']) for r in records}!={(x,y) for y in range(h) for x in range(w)}:
        raise ValueError('policy profile must cover the full grid once')
    selections={};sea={};summary=Counter()
    for r in records:
        x,y=r['column'],r['row'];cell=water['cells'][y][x];status=r['status'];hist=cell['worldcover_samples'];pos=(x,y)
        if r['source_fuel_class']!=cell['source_fuel_class'] or r['v2_surface']!=cell['surface']:
            raise ValueError('policy source cell differs')
        if status=='mapped_sea':
            sea[pos]={'crossing_openness':1.,'transfer':copy.deepcopy(TRANSFER)};summary['mapped_sea']+=1;continue
        if status not in SOURCE_STATUSES and not status.startswith(ESTIMATE_PREFIXES):continue
        if (type(hist) is not dict or any(type(n) is not int or n<0 for n in hist.values()) or sum(hist.values())!=2500
                or any(k not in ('10','20','30','40','50','60','80') for k in hist)
                or not sum(hist.get(k,0) for k in ('10','20','30','40'))):
            summary['excluded_sample_screen']+=1;continue
        if status in SOURCE_STATUSES:
            klass=cell['source_fuel_class']
            if type(klass) is not int or klass not in fuel_data.FUEL_MAPPING:raise ValueError('source fuel class missing')
            fuel=.8;summary['source_selected']+=1
        else:
            if cell['source_fuel_class'] is not None:raise ValueError('estimated class cannot replace source')
            klass,fuel,kind=_estimate(r);summary['estimated_'+kind]+=1
        selections[pos]={'vegetation_class':klass,'vegetation_initial_fuel':fuel,
          'built_initial_fuel':.4 if hist.get('50',0) else None,
          'crossing_openness':1. if status=='mapped_land_source' or status.startswith('new_land_fuel_') else .9,
          'bare_assumption':'synthetic_inert','water_assumption':'synthetic_inert',
          'vegetation_absence':'unknown','built_absence':'unknown' if hist.get('50',0) else 'synthetic_absent',
          'transfer':copy.deepcopy(TRANSFER)}
    selection_payload=[{'column':x,'row':y,'assumptions':selections[(x,y)]} for x,y in sorted(selections)]
    sea_payload=[{'column':x,'row':y,'assumptions':sea[(x,y)]} for x,y in sorted(sea)]
    manifest={'version':VERSION,'profile_sha256':PROFILE_PIN,'water_sha256':WATER_PIN,
        'policy_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'selection_sha256':digest(selection_payload),'sea_sha256':digest(sea_payload),'counts':dict(sorted(summary.items())),
        'fixed_assumptions':{'source_fuel':.8,'estimated_fuel':'documented nominal or lowest tied class at factor 0.9',
            'built_fuel':.4,'land_crossing':1.,'mixed_crossing':.9,'transfer':copy.deepcopy(TRANSFER),
            'absence_and_inert':'explicit sample-based synthetic assumptions; never measured absence or footprint'},
        'limitations':'Unscored synthetic exercise; no physical calibration or source-fuel substitution.'}
    manifest['sha256']=digest(manifest)
    return selections,sea,manifest
