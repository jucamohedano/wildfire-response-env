"""Private source-evidence staging to synthetic two-component v2 grids.

No source fire/weather owner is constructed, no simulation is run, and no
actor observation or file is emitted. Per-cell assumptions are explicit.
WorldCover sample proportions are classifications, not surveyed footprints.
"""
import copy
import hashlib
import math
from pathlib import Path

from eval import source_two_component_adapter_v1 as stage
from eval import two_component_fire_v2 as model
from eval.extended_map_common import digest

VERSION='wildfire-private-source-two-component-staging-v2'
PROFILE_PIN='a81762b1ace2351af87eba03b8378d7b933a834291b32f1bdd2bb40eb9a50447'
STRICT_PIN='a63ba37b281a0f3317465a6bbac1f7d1b71793e37908a0b9be5579cd9336e86a'
WATER_PIN='a44d432066d6c3039f49032ca21b8a82496dff47c855fee1e07f8e854ab15b87'
WORLDCOVER_PIN='224ffccb57011119e7395a2ff172f3b1b6059add8d12a4280ae1a994d0b71212'
FUEL_MAPPING_PIN='196ac0aad5daa0b654c53e219df7b00b0df75ffb015c2f66a4fb3636b489efbb'
FUEL_MAPPING_CONTENT_PIN='e9870ce2a1b16fa8c17ae3b5f6ba0c6c37898abbf608d8658966efd127824802'
STAGE_MODEL_PIN='204344f4d28ac07de2ddd1e4678bf98e2883947c8921f3cb44a63b83aee66d07'
STAGE_PIN='e44234788d83970110b2b639450f4be653e8f65cb59ae1fd11ecb5b0eab13462'
MODEL_PIN='fd0ca091ccc6f50d0f122b4305ab2d0effa69d61196a714f05cf76032e7027b3'
_STAGE_FUNCTIONS={name:getattr(stage,name) for name in ('adapt','_state','_validate_choice','_check_document','_validate_parameters','_validate_components')}
_MODEL_VERIFY=model._v1_source
_MODEL_VALIDATE_CELLS=model._validate_cells
_MODEL_VALIDATE_ASSUMPTIONS=model._validate_assumptions


def _check_implementation():
    if (hashlib.sha256(Path(stage.__file__).read_bytes()).hexdigest()!=STAGE_PIN
            or hashlib.sha256(Path(model.__file__).read_bytes()).hexdigest()!=MODEL_PIN
            or any(getattr(stage,name) is not original for name,original in _STAGE_FUNCTIONS.items())
            or model._v1_source is not _MODEL_VERIFY or model._validate_cells is not _MODEL_VALIDATE_CELLS
            or model._validate_assumptions is not _MODEL_VALIDATE_ASSUMPTIONS
            or model.V1_SOURCE_PIN!=STAGE_MODEL_PIN
            or (stage.PROFILE_PIN,stage.STRICT_PIN,stage.WATER_PIN,stage.WORLDCOVER_PIN,stage.FUEL_MAPPING_PIN,
                stage.FUEL_MAPPING_CONTENT_PIN,stage.MODEL_PIN)
               !=(PROFILE_PIN,STRICT_PIN,WATER_PIN,WORLDCOVER_PIN,FUEL_MAPPING_PIN,FUEL_MAPPING_CONTENT_PIN,STAGE_MODEL_PIN)):
        raise ValueError('staging or v2 model source differs')
    model._v1_source()


def _unknown_cell():return {'vegetation':'unknown','built':'unknown','inert':'unknown'}


def _component(kind,count,choice):
    if count==0:
        return None if choice[kind+'_absence']=='synthetic_absent' else 'unknown'
    fuel_key='vegetation_initial_fuel' if kind=='vegetation' else 'built_initial_fuel'
    if choice[fuel_key] is None:return 'unknown'
    klass=choice['vegetation_class'] if kind=='vegetation' else None
    if kind=='vegetation' and klass is None:return 'unknown'
    return stage._state(kind,count/2500,choice[fuel_key],klass)


def _inert_share(bare,water,remainder,choice):
    """Keep unknown shares distinct; never promote sample absence into inert."""
    if remainder:return 'unknown'
    bare_mode=choice['bare_assumption'];water_mode=choice['water_assumption']
    # Zero classified samples do not establish physical absence; the caller
    # must explicitly choose the synthetic assumption for both categories.
    if bare_mode=='unknown' or water_mode=='unknown':
        if bare_mode=='unknown' and water_mode=='unknown' and bare+water>0:
            return {'fraction':(bare+water)/2500,'assumption':'unknown'}
        return 'unknown'
    count=bare+water
    return {'fraction':count/2500,'assumption':'synthetic_inert'} if count else None


def adapt(profile,strict,water,*,bbox,selections,sea_options,staging_transfer_anchor,max_cells,opt_in=False):
    """Return private v2-shaped grids and separately held audit evidence.

    `selections` contains v1 staging assumptions keyed by global cell; sea
    options explicitly provide crossing and transfer for mapped-sea inert
    hypotheses. Unselected cells remain entirely unknown. `staging_transfer_anchor`
    is used only to validate the legacy staging interface, never as a v2 default.
    """
    if opt_in is not True:raise ValueError('explicit private source sensitivity opt-in required')
    _check_implementation()
    adapter_pin=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    # Freeze caller-owned data so multi-tile staging cannot combine revisions.
    profile,strict,water=copy.deepcopy((profile,strict,water))
    selections,sea_options,staging_transfer_anchor=copy.deepcopy((selections,sea_options,staging_transfer_anchor))
    stage._validate_parameters(staging_transfer_anchor)
    if (type(bbox) is not tuple or len(bbox)!=4 or any(type(v) is not int for v in bbox)
            or type(max_cells) is not int or not 1<=max_cells<=16384):raise ValueError('explicit bounded grid required')
    x0,y0,width,height=bbox;grid=profile['grid']
    if (width<1 or height<1 or width>256 or height>256 or width*height>max_cells
            or x0<0 or y0<0 or x0+width>grid['width'] or y0+height>grid['height']):
        raise ValueError('crop exceeds prototype bounds')
    if type(selections) is not dict or type(sea_options) is not dict:raise ValueError('explicit private selection maps required')
    if any(type(pos) is not tuple or len(pos)!=2 or any(type(v) is not int for v in pos)
           or not (x0<=pos[0]<x0+width and y0<=pos[1]<y0+height)
           for pos in set(selections)|set(sea_options)):
        raise ValueError('selection outside crop')
    for pos,option in sea_options.items():
        if type(option) is not dict or set(option)!={'crossing_openness','transfer'}:
            raise ValueError('explicit sea crossing and transfer required')
        stage._validate_parameters(option['transfer']);cross=option['crossing_openness']
        if cross is not None and (type(cross) not in (int,float) or not math.isfinite(cross) or not 0<cross<=1):
            raise ValueError('invalid explicit sea crossing')
    if set(selections)&set(sea_options):raise ValueError('conflicting sea and cell assumptions')
    components=[[_unknown_cell() for _ in range(width)] for _ in range(height)]
    heights=[[None]*width for _ in range(height)]
    crossings=[['unknown']*width for _ in range(height)];transfers=[['unknown']*width for _ in range(height)]
    evidence=[];stage_ids=[]
    # v1 staging is independently pinned and bounded at 4,096 cells. Split the
    # requested crop into disjoint tiles; this does not change its classifications.
    tile_width=min(width,128,4096//min(height,128))
    for dy in range(0,height,128):
        th=min(128,height-dy);tw=min(tile_width,4096//th)
        for dx in range(0,width,tw):
            w=min(tw,width-dx);tile=(x0+dx,y0+dy,w,th)
            def within(pos):return tile[0]<=pos[0]<tile[0]+w and tile[1]<=pos[1]<tile[1]+th
            part=stage.adapt(profile,strict,water,bbox=tile,
                selections={pos:copy.deepcopy(value) for pos,value in selections.items() if within(pos)},
                mapped_sea_as_inert=frozenset(pos for pos in sea_options if within(pos)),
                transfer=copy.deepcopy(staging_transfer_anchor),opt_in=True)
            stage_ids.append(copy.deepcopy(part['identity']))
            for r in part['provenance']['cells']:
                x,y=r['column'],r['row'];lx,ly=x-x0,y-y0;point=(x,y)
                heights[ly][lx]=part['heights'][y-tile[1]][x-tile[0]]
                record=copy.deepcopy(r);record['v2_status']='unknown';record['v2_reasons']=[]
                choice=selections.get(point)
                if point in sea_options:
                    option=sea_options[point]
                    components[ly][lx]={'vegetation':None,'built':None,'inert':{'fraction':1.,'assumption':'synthetic_inert'}}
                    crossings[ly][lx]=option['crossing_openness'] if option['crossing_openness'] is not None else 'unknown'
                    transfers[ly][lx]=copy.deepcopy(option['transfer'])
                    record['v2_status']='mapped_sea_synthetic_inert';record['v2_sea_assumptions']=copy.deepcopy(option)
                elif choice is not None:
                    hard={'profile_cell_unknown','use_explicit_mapped_sea_assumption','unrepresented_worldcover_class','terrain_missing'}
                    if any(reason in hard for reason in r['reasons']):
                        record['v2_reasons']=[z for z in r['reasons'] if z in hard]
                    else:
                        hist=r['worldcover_samples'];veg=sum(hist.get(k,0) for k in stage.VEGETATION)
                        built=hist.get('50',0);bare=hist.get('60',0);water_count=hist.get('80',0)
                        veg_state=_component('vegetation',veg,choice);built_state=_component('built',built,choice)
                        if any(z in r['reasons'] for z in ('vegetation_assumption_missing','selected_class_conflicts_with_retained_source','class_not_supported_by_profile_neighbors','vegetation_absence_conflicts_with_samples')):
                            veg_state='unknown'
                        if veg==0 and r['source_fuel_class'] is not None:
                            veg_state='unknown'
                            record['v2_reasons'].append('retained_source_fuel_conflicts_with_vegetation_absence')
                        if any(z in r['reasons'] for z in ('built_fuel_assumption_missing','built_absence_conflicts_with_samples')):
                            built_state='unknown'
                        inert=_inert_share(bare,water_count,0,choice)
                        components[ly][lx]={'vegetation':veg_state,'built':built_state,'inert':inert}
                        crossings[ly][lx]=choice['crossing_openness'] if choice['crossing_openness'] is not None else 'unknown'
                        transfers[ly][lx]=copy.deepcopy(choice['transfer'])
                        if (veg_state=='unknown' or built_state=='unknown' or inert=='unknown'
                                or (type(inert) is dict and inert.get('assumption')=='unknown') or crossings[ly][lx]=='unknown'):
                            record['v2_reasons'].append('explicit_or_unrepresented_component_or_crossing_unknown')
                        else:record['v2_status']='synthetic_two_component_assumption'
                else:record['v2_reasons'].append('no_explicit_cell_assumptions')
                evidence.append(record)
    model._validate_cells(components,width,height);model._validate_assumptions(transfers,crossings,width,height)
    _check_implementation()
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=adapter_pin:
        raise ValueError('adapter source changed during staging')
    stage._check_document(profile,PROFILE_PIN,'wildfire-domain-inland-low-water-sensitivity-profile-v1')
    stage._check_document(strict,STRICT_PIN,'extended-map-resolution-evidence-v1')
    stage._check_document(water,WATER_PIN,'extended-map-resolution-evidence-v2')
    provenance={'version':VERSION,'profile_sha256':stage.PROFILE_PIN,'strict_sha256':stage.STRICT_PIN,
        'water_sha256':stage.WATER_PIN,'worldcover_sha256':stage.WORLDCOVER_PIN,'stage_sha256':STAGE_PIN,
        'model_sha256':MODEL_PIN,'bbox':list(bbox),'stage_identities':stage_ids,'cells':evidence,
        'limitations':'Private source classifications and synthetic assumptions; not surveyed footprints, historical fuel, fire outcomes or actor observations.'}
    identity={'version':VERSION,'profile_sha256':stage.PROFILE_PIN,'model_sha256':MODEL_PIN,'stage_sha256':STAGE_PIN,
        'adapter_sha256':adapter_pin,'bbox':list(bbox),
        'components_sha256':digest(components),'heights_sha256':digest(heights),'crossings_sha256':digest(crossings),
        'transfers_sha256':digest(transfers),'provenance_sha256':digest(provenance)}
    return {'version':VERSION,'role':'private_v2_grid_staging_not_source_fire_owner',
        'grid':{'width':width,'height':height,'crs':grid['crs'],
                'transform':[grid['transform'][0],0,grid['transform'][2]+x0*500,0,grid['transform'][4],grid['transform'][5]-y0*500]},
        'components':components,'heights':heights,'crossings':crossings,'transfers':transfers,
        'identity':identity,'private_audit':provenance}
