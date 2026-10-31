"""Opt-in private domain-profile fire/weather inputs; no operations or scoring.

This layer does not modify the source map or the independently fixed profile.
Weak unique votes use their documented nominal class; tied votes use the lowest
numeric tied class as an arbitrary reproducible sensitivity choice, not a vote
winner. Estimated fuel and mixed fuel use factor 0.9; mixed crossings use 0.9.
Unknown profile cells remain unsupported and mapped sea stays nonburnable.
"""
import copy
from pathlib import Path

import adapter_v1 as api
from wildfire_expanded_incident_v1.exercise_v4 import prepare_exercise_v4
from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED, _file_sha
from wildfire_expanded_incident_v1 import estimated_fuel_v1 as estimated
from wildfire_expanded_incident_v1.estimated_weather_v1 import EstimatedWeatherFireV1

VERSION='wildfire-domain-profile-weather-input-v1'
PROFILE_PIN='a17d83005df0f88a5b9cd4e635472debf7246bad66aeaf26ac9539252fb31f59'
CHOICE='unique-nominal-tied-lowest-class-factor-0.9-crossing-0.9-v1'


def convert_profile(profile,base,*,expected_profile_sha256, opt_in=False):
    if opt_in is not True:raise ValueError('explicit domain-profile opt-in required')
    if (profile.get('version')!='wildfire-domain-fuel-surface-sensitivity-profile-v3'
            or profile.get('status')!='private_synthetic_evidence_not_runtime_input'
            or profile.get('sha256')!=expected_profile_sha256
            or api._hash({k:v for k,v in profile.items() if k!='sha256'})!=expected_profile_sha256):
        raise ValueError('independently pinned profile differs')
    c=base.contract; grid=profile['grid']; ident=dict(base.identity); pins=profile['source_pins']
    if (grid['width']!=c.width or grid['height']!=c.height or grid['crs']!=c.crs
            or tuple(grid['transform'])!=c.transform
            or any(pins.get(k)!=ident[v] for k,v in (('strict','strict'),('water_v2','water_v2'),
                ('exercise_v3','overlay_v3'),('exercise_v4','overlay_v4'),('dera_rivers','dera_rivers')))):
        raise ValueError('profile and source lineage differ')
    cells=profile['cells']; positions={(r['column'],r['row']) for r in cells}
    if (len(cells)!=c.width*c.height or len(positions)!=len(cells)
            or positions!={(x,y) for y in range(c.height) for x in range(c.width)}):
        raise ValueError('profile is not a full unique grid')
    mats={k:[list(row) for row in getattr(c,k)] for k in ('surface','fuel','elevation','access')}
    factors=list(base.factors);estimates=[];choices=[]
    mask=[[False]*c.width for _ in range(c.height)]
    source_mask=[[False]*c.width for _ in range(c.height)]
    for record in cells:
        x,y=record['column'],record['row'];status=record['status'];source=record['source_fuel_class']
        if source != c.fuel[y][x]:
            raise ValueError('profile source fuel differs from source contract')
        source_mask[y][x]=source is not None
        if status=='unknown':
            # Even if fuel evidence exists, no unsupported surface is admitted.
            mats['surface'][y][x]='unknown'
        elif status=='mapped_sea':
            if c.surface[y][x]!='sea' or source is not None:raise ValueError('sea classification differs')
        elif status=='mapped_land_source':
            if c.surface[y][x]!='land' or source is None or c.elevation[y][x] is None:raise ValueError('land source differs')
        elif status=='existing_v4_mixed_source':
            if c.surface[y][x]!='mixed' or source is None or c.elevation[y][x] is None:raise ValueError('v4 mixed source differs')
        elif status=='new_mixed_source':
            if c.surface[y][x]!='unknown' or source is None or c.elevation[y][x] is None:raise ValueError('new mixed source differs')
            mats['surface'][y][x]='mixed';factors.append((x,y,.9,.9))
        elif status.startswith(('new_land_fuel_','new_mixed_fuel_')):
            h=record['fuel_hypothesis'];hs=h['status']
            if source is not None or c.elevation[y][x] is None or c.surface[y][x] not in ('land','unknown'):
                raise ValueError('estimated cell lacks supported source conditions')
            if hs=='tied_sensitivity_only':
                options=h['scenario_classes']
                if len(options)<2 or options!=sorted(set(options)):raise ValueError('invalid tied alternatives')
                klass=options[0]
            elif hs in ('estimated_hypothesis','ambiguous_sensitivity_hypothesis'):
                klass=h['scenarios']['nominal']['assumed_fuel_class']
                if klass!=h['nominal_class_assumption']:raise ValueError('nominal class differs')
            else:raise ValueError('unsupported fuel hypothesis')
            if type(klass) is not int or klass not in estimated.fire_v1.FUEL_MAPPING:raise ValueError('invalid chosen class')
            mixed=status.startswith('new_mixed_fuel_')
            if record['surface_assumption']!=('mixed' if mixed else 'land'):raise ValueError('surface assumption differs')
            mats['surface'][y][x]='unknown';mats['fuel'][y][x]=None
            estimates.append((x,y,klass,.9,.9 if mixed else 1.0));mask[y][x]=True
            choices.append((x,y,hs,klass,'mixed' if mixed else 'land'))
        else:raise ValueError('unknown profile status')
    doc=dict(version=api._contract_module.VERSION,grid=dict(width=c.width,height=c.height,crs=c.crs,
        transform=list(c.transform)),domain_mask=[list(row) for row in c.domain],source_pins=dict(c.source_pins),**mats)
    changed=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
    ident.update(parent_input_sha256=c.document_sha256,input_sha256=changed.document_sha256,
        domain_profile_sha256=expected_profile_sha256,domain_choice=CHOICE,
        domain_backend_sha256=_file_sha(__file__))
    mixed=MixedInputsV2(changed,tuple(factors),tuple(sorted(ident.items())),_VERIFIED)
    mask=tuple(tuple(row) for row in mask);source_mask=tuple(tuple(row) for row in source_mask)
    identity=dict(version=VERSION,base=ident,profile_sha256=expected_profile_sha256,choice=CHOICE,
        backend_sha256=_file_sha(__file__),choices_sha256=api._hash(choices),
        source_mask_sha256=api._hash(source_mask),estimated_mask_sha256=api._hash(mask))
    return estimated.EstimatedInputsV1(mixed,tuple(estimates),source_mask,mask,
                                      tuple(sorted(identity.items())),estimated._VERIFIED_ESTIMATE)


def check_v4_provenance(profile,v4):
    provenance_keys=('dera_rivers_response_sha256','dera_rivers_acquired','dera_rivers_source_epoch',
                     'worldcover_epoch','osm_water_epoch','source_catalog_sha256')
    if (any(k not in v4 for k in provenance_keys) or profile.get('inherited_v4_provenance')!=
            {'document_sha256':v4.get('sha256'),**{k:v4[k] for k in provenance_keys}}):
        raise ValueError('v4 document provenance differs')
    v4_cells={(r['column'],r['row']):r for r in v4['cells']}
    if len(v4_cells)!=len(v4['cells']):raise ValueError('duplicate v4 mixed evidence')
    for record in profile['cells']:
        if record['status']=='existing_v4_mixed_source':
            if (record.get('inherited_v4_document_sha256')!=v4.get('sha256') or
                    record.get('inherited_v4_evidence')!=v4_cells.get((record['column'],record['row']))):
                raise ValueError('inherited v4 mixed provenance differs')

def prepare(profile,strict,water,v3,v4,parent,catalog,*,opt_in=False,**options):
    if opt_in is not True:raise ValueError('explicit domain-profile opt-in required')
    if options.get('scenario')!='nominal' or options.get('crossing_proxy')!=.9:
        raise ValueError('fixed synthetic surface and crossing assumptions required')
    check_v4_provenance(profile,v4)
    base=prepare_exercise_v4(strict,water,v3,v4,parent,catalog,**options)
    return convert_profile(profile,base,expected_profile_sha256=PROFILE_PIN,opt_in=True)


class DomainProfileWeatherFireV1(EstimatedWeatherFireV1):
    """Weather driver with private domain input identity and existing censor."""
    def __init__(self,inputs,*args,**kwargs):
        if dict(inputs.identity).get('version')!=VERSION:raise ValueError('domain-profile inputs required')
        super().__init__(inputs,*args,**kwargs)
        self._identity['domain_profile_backend']={'version':VERSION,'sha256':_file_sha(__file__)}

    def _ensure(self):
        super()._ensure()
        if self._identity['domain_profile_backend']['sha256']!=_file_sha(__file__):
            self._poisoned=True;raise RuntimeError('domain profile backend changed')
