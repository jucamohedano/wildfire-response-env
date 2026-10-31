"""Private staging adapter from pinned map evidence to v1 component inputs.

No files are written and no simulator is started. This conservative adapter
never infers inert ground or water from a classification. The prototype cannot
represent third-component inert shares or non-neutral per-cell crossing factors;
those cells remain unknown even when the caller supplies synthetic assumptions.
All coordinates, evidence, assumptions and results are trusted evaluator data.
"""
import copy
import hashlib
import math
from pathlib import Path

from eval.extended_map_common import digest
from eval import two_component_fire_v1 as model
from eval.two_component_fire_v1 import _validate_parameters, _validate_components
from envs.wildfire_response.regional.prepared import data as fuel_data
from envs.wildfire_response.regional.prepared.data import FUEL_MAPPING

VERSION='wildfire-private-source-two-component-staging-v1'
PROFILE_PIN='a81762b1ace2351af87eba03b8378d7b933a834291b32f1bdd2bb40eb9a50447'
STRICT_PIN='a63ba37b281a0f3317465a6bbac1f7d1b71793e37908a0b9be5579cd9336e86a'
WATER_PIN='a44d432066d6c3039f49032ca21b8a82496dff47c855fee1e07f8e854ab15b87'
WORLDCOVER_PIN='224ffccb57011119e7395a2ff172f3b1b6059add8d12a4280ae1a994d0b71212'
FUEL_MAPPING_PIN='196ac0aad5daa0b654c53e219df7b00b0df75ffb015c2f66a4fb3636b489efbb'
MODEL_PIN='204344f4d28ac07de2ddd1e4678bf98e2883947c8921f3cb44a63b83aee66d07'
FUEL_MAPPING_CONTENT_PIN='e9870ce2a1b16fa8c17ae3b5f6ba0c6c37898abbf608d8658966efd127824802'
VEGETATION=('10','20','30','40')
CHOICE_FIELDS=frozenset(('vegetation_class','vegetation_initial_fuel','built_initial_fuel',
                         'crossing_openness','bare_assumption','water_assumption',
                         'vegetation_absence','built_absence','transfer'))


def _check_document(doc,pin,version):
    if (type(doc) is not dict or doc.get('version')!=version or doc.get('sha256')!=pin
            or digest({k:v for k,v in doc.items() if k!='sha256'})!=pin):
        raise ValueError('pinned source document differs')


def _fuel(value):
    return type(value) in (int,float) and math.isfinite(value) and 0<value<=1


def _validate_choice(choice):
    if type(choice) is not dict or set(choice)!=CHOICE_FIELDS:raise ValueError('all per-cell assumptions must be explicit')
    klass=choice['vegetation_class']
    if klass is not None and (type(klass) is not int or klass not in FUEL_MAPPING):raise ValueError('invalid synthetic vegetation class')
    for key in ('vegetation_initial_fuel','built_initial_fuel'):
        if choice[key] is not None and not _fuel(choice[key]):raise ValueError('synthetic fuel must be in (0,1]')
    crossing=choice['crossing_openness']
    if crossing is not None and (type(crossing) not in (int,float) or not math.isfinite(crossing) or not 0<crossing<=1):
        raise ValueError('crossing must be explicit unknown or in (0,1]')
    for key in ('bare_assumption','water_assumption'):
        if choice[key] not in ('unknown','synthetic_inert'):raise ValueError('inert share assumption must be explicit')
    for key in ('vegetation_absence','built_absence'):
        if choice[key] not in ('unknown','synthetic_absent'):
            raise ValueError('component absence must be explicit')
    _validate_parameters(choice['transfer'])


def _state(kind,fraction,fuel,klass=None):
    return dict(terrain=FUEL_MAPPING[klass]['terrain'] if kind=='vegetation' else 'built',
                fraction=fraction,fuel=float(fuel),initial_fuel=float(fuel),heat=0.,age=0,wet=0,burned=0.)


def adapt(profile,strict,water,*,bbox,selections,mapped_sea_as_inert,transfer,opt_in=False):
    """Return a private, bounded v1-compatible component grid and provenance.

    `bbox` is (column,row,width,height) on the pinned grid. `selections` maps
    global coordinates to explicit synthetic assumptions. `mapped_sea_as_inert`
    is an explicit subset of mapped-sea coordinates. All other cells stay
    unknown; mapped sea is never silently classified as inert.
    """
    if opt_in is not True:raise ValueError('explicit private source sensitivity opt-in required')
    _check_document(profile,PROFILE_PIN,'wildfire-domain-inland-low-water-sensitivity-profile-v1')
    _check_document(strict,STRICT_PIN,'extended-map-resolution-evidence-v1')
    _check_document(water,WATER_PIN,'extended-map-resolution-evidence-v2')
    if (hashlib.sha256(Path(model.__file__).read_bytes()).hexdigest()!=MODEL_PIN
            or hashlib.sha256(Path(fuel_data.__file__).read_bytes()).hexdigest()!=FUEL_MAPPING_PIN
            or FUEL_MAPPING is not fuel_data.FUEL_MAPPING or digest(FUEL_MAPPING)!=FUEL_MAPPING_CONTENT_PIN
            or _validate_parameters is not model._validate_parameters or _validate_components is not model._validate_components
            or profile['source_pins'].get('fuel_mapping')!=FUEL_MAPPING_PIN):
        raise ValueError('prototype or fuel mapping source differs')
    if (profile.get('parent_profile_sha256')!='a17d83005df0f88a5b9cd4e635472debf7246bad66aeaf26ac9539252fb31f59'
            or profile['source_pins'].get('strict')!=STRICT_PIN or profile['source_pins'].get('water_v2')!=WATER_PIN
            or strict['source_pins'].get('worldcover')!=WORLDCOVER_PIN or water['source_pins'].get('worldcover')!=WORLDCOVER_PIN
            or water.get('parent_document_sha256')!=STRICT_PIN or profile['grid']!=water['grid'] or strict['grid']!=water['grid']):
        raise ValueError('profile source provenance differs')
    grid=profile['grid'];w,h=grid['width'],grid['height']
    if (type(bbox) is not tuple or len(bbox)!=4 or any(type(n) is not int for n in bbox)):
        raise ValueError('explicit integer crop required')
    x0,y0,width,height=bbox
    if (width<1 or height<1 or width>128 or height>128 or width*height>4096 or
            x0<0 or y0<0 or x0+width>w or y0+height>h):raise ValueError('crop exceeds prototype bounds')
    if type(selections) is not dict or type(mapped_sea_as_inert) not in (set,frozenset):
        raise ValueError('explicit private per-cell selections and sea set required')
    all_positions=set(selections)|set(mapped_sea_as_inert)
    if (set(selections)&set(mapped_sea_as_inert) or any(type(pos) is not tuple or len(pos)!=2 or
            any(type(v) is not int for v in pos) or not(x0<=pos[0]<x0+width and y0<=pos[1]<y0+height)
            for pos in all_positions)):
        raise ValueError('selection outside crop or conflicting')
    _validate_parameters(transfer)
    for choice in selections.values():_validate_choice(choice)
    records={(r['column'],r['row']):r for r in profile['cells']}
    if len(records)!=w*h or len(profile['cells'])!=w*h:raise ValueError('source profile grid is incomplete')
    rows=[];heights=[];evidence=[]
    for y in range(y0,y0+height):
        row=[];height_row=[]
        for x in range(x0,x0+width):
            point=(x,y);r=records[point];s=strict['cells'][y][x];v=water['cells'][y][x]
            hist=v['worldcover_samples'];source=v['source_fuel_class'];choice=selections.get(point)
            if (r['v2_surface']!=v['surface'] or r['source_fuel_class']!=source
                    or s['source_fuel_class']!=source or s['worldcover_samples']!=hist):
                raise ValueError('profile cell differs from pinned source')
            if (type(hist) is not dict or any(type(z) is not int or z<0 for z in hist.values()) or sum(hist.values())!=2500):
                raise ValueError('invalid pinned WorldCover sample counts')
            veg=sum(hist.get(k,0) for k in VEGETATION);built=hist.get('50',0);bare=hist.get('60',0);water_count=hist.get('80',0)
            remainder=2500-veg-built-bare-water_count
            record={'column':x,'row':y,'profile_status':r['status'],'v2_surface':v['surface'],
                'source_fuel_class':source,'source_fuel_status':v['fuel_status'],
                'source_fuel_provenance':copy.deepcopy(v.get('fuel_provenance')),
                'worldcover_epoch':'2021','worldcover_samples':copy.deepcopy(hist),
                'sample_shares':{'vegetation':veg/2500,'built_up':built/2500,'bare':bare/2500,
                                 'water':water_count/2500,'unrepresented':remainder/2500},
                'mapped_water_conflicts':copy.deepcopy(v.get('osm_water_conflicts',[])),
                'profile_evidence':copy.deepcopy(r),'caller_assumptions':copy.deepcopy(choice),
                'sea_inert_assumption':point in mapped_sea_as_inert,'status':'unknown','reasons':[]}
            parts={'vegetation':'unknown','built':'unknown'};height_row.append(water['terrain_candidate'][y][x])
            if point in mapped_sea_as_inert:
                if r['status']!='mapped_sea' or v['surface']!='sea' or source is not None:
                    raise ValueError('synthetic sea inert assumption lacks mapped sea evidence')
                parts={'vegetation':None,'built':None};record['status']='mapped_sea_synthetic_inert'
                height_row[-1]=None
            elif choice is None:
                record['reasons'].append('no_explicit_cell_assumptions')
            else:
                reasons=record['reasons']
                if r['status']=='unknown':reasons.append('profile_cell_unknown')
                if v['surface']=='sea':reasons.append('use_explicit_mapped_sea_assumption')
                if remainder:reasons.append('unrepresented_worldcover_class')
                if bare or water_count:reasons.append('v1_cannot_represent_inert_share')
                if v.get('osm_water_conflicts') or r['status'].startswith(('existing_v4_mixed','new_mixed','new_low_water_mixed')):
                    reasons.append('v1_cannot_represent_mapped_water_crossing')
                if choice['crossing_openness'] is None:reasons.append('crossing_assumption_unknown')
                elif choice['crossing_openness']!=1.:reasons.append('v1_cannot_represent_crossing_factor')
                if choice['transfer']!=transfer:reasons.append('v1_cannot_represent_per_cell_transfer')
                if height_row[-1] is None:reasons.append('terrain_missing')
                for name,count in (('vegetation',veg),('built',built)):
                    absence=choice[name+'_absence']
                    if count==0 and absence=='unknown':reasons.append(name+'_absence_unknown')
                    if count>0 and absence=='synthetic_absent':reasons.append(name+'_absence_conflicts_with_samples')
                if veg and (choice['vegetation_class'] is None or choice['vegetation_initial_fuel'] is None):
                    reasons.append('vegetation_assumption_missing')
                if built and choice['built_initial_fuel'] is None:reasons.append('built_fuel_assumption_missing')
                if veg and source is not None and choice['vegetation_class']!=source:
                    reasons.append('selected_class_conflicts_with_retained_source')
                if veg and source is None:
                    hrec=r.get('fuel_hypothesis') or {};votes=hrec.get('source_votes',{})
                    if str(choice['vegetation_class']) not in votes:reasons.append('class_not_supported_by_profile_neighbors')
                if not veg and not built:reasons.append('no_modeled_combustible_component')
                if not reasons:
                    parts={'vegetation':_state('vegetation',veg/2500,choice['vegetation_initial_fuel'],choice['vegetation_class']) if veg else None,
                           'built':_state('built',built/2500,choice['built_initial_fuel']) if built else None}
                    record['status']='synthetic_two_component_assumption'
            row.append(parts);evidence.append(record)
        rows.append(row);heights.append(height_row)
    _validate_components(rows,width,height)
    provenance={'version':VERSION,'profile_sha256':PROFILE_PIN,'strict_sha256':STRICT_PIN,'water_sha256':WATER_PIN,
        'worldcover_sha256':WORLDCOVER_PIN,'model_sha256':MODEL_PIN,'fuel_mapping_sha256':FUEL_MAPPING_PIN,
        'fuel_mapping_content_sha256':FUEL_MAPPING_CONTENT_PIN,
        'bbox':list(bbox),'transfer':copy.deepcopy(transfer),'cells':evidence,
        'model_contract':'two-component-v1; unrepresented inert or water-crossing shares remain unknown',
        'limitations':'WorldCover sample shares are classifications, not surveyed footprints, fuel loads, access or calibrated burnability.'}
    identity={'version':VERSION,'profile_sha256':PROFILE_PIN,'strict_sha256':STRICT_PIN,'water_sha256':WATER_PIN,
        'worldcover_sha256':WORLDCOVER_PIN,'model_sha256':MODEL_PIN,'fuel_mapping_sha256':FUEL_MAPPING_PIN,
        'fuel_mapping_content_sha256':FUEL_MAPPING_CONTENT_PIN,
        'bbox':list(bbox),'provenance_sha256':digest(provenance),
        'components_sha256':digest(rows),'heights_sha256':digest(heights),
        'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'transfer_sha256':digest(transfer)}
    return {'version':VERSION,'role':'private_source_sensitivity_staging_not_host','grid':{'width':width,'height':height,'crs':grid['crs'],
        'transform':[grid['transform'][0],0,grid['transform'][2]+x0*500,0,grid['transform'][4],grid['transform'][5]-y0*500]},
        'components':rows,'heights':heights,'transfer':copy.deepcopy(transfer),'identity':identity,'provenance':provenance}
