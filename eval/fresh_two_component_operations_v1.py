"""Fresh two-component weather/fire with one synthetic operations transaction.

Trusted private component, not an incident host or a calibrated drop model.
The prior fresh owner and component model are unmodified.
"""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from eval import fresh_two_component_weather_v2 as legacy
from eval import incident_weather_v1 as weather
from eval import containment_spread_seam_v1 as seam
from eval.two_component_fire_v3 import TwoComponentFireV3

VERSION='wildfire-fresh-two-component-operations-v1'
DROP_POLICY='synthetic-target-component-heat-minus-015-wet-five-v1'
STAGING_MODEL_PIN=legacy.STAGING_MODEL_PIN
STAGING_ADAPTER_PIN=legacy.STAGING_ADAPTER_PIN
staging_adapter=legacy.staging_adapter
_MODEL_CLASS=TwoComponentFireV3
_MODEL_RESTORE=TwoComponentFireV3.restore.__func__
_MODEL_STEP=TwoComponentFireV3.step
_MODEL_ENSURE=TwoComponentFireV3._ensure


def _source():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _check_model_binding():
    if (TwoComponentFireV3 is not _MODEL_CLASS or legacy.model.TwoComponentFireV3 is not _MODEL_CLASS
            or getattr(_MODEL_CLASS.restore,'__func__',None) is not _MODEL_RESTORE
            or _MODEL_CLASS.step is not _MODEL_STEP or _MODEL_CLASS._ensure is not _MODEL_ENSURE):
        raise RuntimeError('component clone binding changed')


class FreshTwoComponentOperationsV1(legacy.FreshTwoComponentWeatherV2):
    """Stage on a checkpoint clone; commit weather and model only after success.

    Drops explicitly target one known burning vegetation or built component.
    Each synthetic drop subtracts 0.15 heat (floor zero) and sets wetness to at
    least five minutes. It does not infer which component an aircraft targeted.
    Resource costs and delayed feedback belong to a separate shared host.
    """
    def __init__(self,*args,**kwargs):
        _check_model_binding()
        super().__init__(*args,**kwargs)
        self._ops_source=_source()
        self._ops_identity=dict(version=VERSION,source=self._ops_source,policy=DROP_POLICY,
                                fresh=copy.deepcopy(self._identity))
        self._ops_identity_seal=api._hash(self._ops_identity)
        self._last_costs=[];self._restoring_fresh=False

    def _ensure(self):
        _check_model_binding()
        super()._ensure()
        if (_source()!=self._ops_source or api._hash(self._ops_identity)!=self._ops_identity_seal
                or self._ops_identity['fresh']!=self._identity
                or self._last_costs!=self._model.fire._last_costs):
            self._poisoned=True;raise RuntimeError('operations contract identity changed')

    def situation(self):
        value=super().situation();value['version']=VERSION
        value['scope']='private_synthetic_component_operations_unscored'
        return value

    def step(self,*,assignments=(),suppressing=(),drops=()):
        self._ensure()
        if self._status!='active':raise RuntimeError('diagnostic cannot advance')
        if (type(assignments) not in (list,tuple) or type(suppressing) not in (list,tuple)
                or type(drops) not in (list,tuple)):
            raise ValueError('explicit operations sequences required')
        if self._weather_kind=='warm' and (assignments or suppressing or drops):
            raise ValueError('operations unavailable during warmup')
        # Validate request shape before any physical state or clock changes.
        seen=set();resources=set();normalized=[]
        for item in drops:
            if (type(item) is not dict or set(item)!={'resource_id','cell','component'}
                    or type(item['resource_id']) is not str or not item['resource_id'].strip()
                    or type(item['cell']) is not list
                    or len(item['cell'])!=2 or any(type(v) is not int for v in item['cell'])
                    or item['component'] not in ('vegetation','built')):
                raise ValueError('invalid synthetic component drop')
            x,y=item['cell'];key=(x,y,item['component']);resource=item['resource_id']
            if key in seen or resource in resources or not (0<=x<self._model.fire.width and 0<=y<self._model.fire.height):
                raise ValueError('duplicate resource, component, or out-of-grid drop')
            seen.add(key);resources.add(resource);normalized.append((x,y,item['component']))
        reason=self._model._guard()
        if reason:
            self._status='censored';self._reason=reason;return self.situation()
        # A trusted clone contains the exact RNG, component and containment state.
        snapshot=self._model.checkpoint()
        staged=TwoComponentFireV3.restore(snapshot,expected_input_sha256=self._identity['model_input_sha256'],
                                          expected_checkpoint_sha256=snapshot['sha256'],opt_in=True)
        staged._validate_assignment_support(assignments)
        for x,y,kind in normalized:
            cell=staged._components[y][x]
            if legacy.model.v2._unknown(cell) or staged.transfers[y][x]=='unknown' or staged.crossings[y][x]=='unknown':
                raise ValueError('drop touches unknown component support')
            target=cell[kind]
            if type(target) is not dict or target['fuel']<=0 or target['heat']<=0:
                raise ValueError('drop requires a known burning component')
        warming=self._weather_kind=='warm'
        if warming:
            next_weather=weather.advance(self._warm,self._weather_state,self._warmup_done+1)
            conditions=weather.conditions(self._warm,0)
        else:
            minute=self._minute+1;conditions=weather.conditions(self._profile,minute)
            row=copy.deepcopy(conditions);row.update(minute=0,received_at=0)
            selected=weather.WeatherProfile.from_records([row])
            previous=weather.initial_state(selected,moisture_proxy=self._weather_state['moisture_proxy'])
            updated=weather.advance(selected,previous,1)
            next_weather=weather.initial_state(self._profile,moisture_proxy=updated['moisture_proxy']);next_weather['minute']=minute
        staged.fire.wind=tuple(conditions['wind'])
        staged.fire.rules['spread_factor']=weather.spread_factor(next_weather,base_factor=.5)
        active_suppression=list(suppressing)+sorted(resources)
        seam.preflight_assignments(staged.fire,assignments,active_suppression)
        for x,y,kind in normalized:
            target=staged._components[y][x][kind]
            target['heat']=max(0.,target['heat']-.15);target['wet']=max(target['wet'],5)
        staged._sync_projection()
        # Censor post-step without exposing or committing the unsupported state.
        safe=staged.step(assignments=assignments,suppressing=active_suppression)
        if not safe:
            self._status='censored';self._reason=staged.reason;return self.situation()
        self._model=staged;self._weather_state=next_weather;self._last_costs=copy.deepcopy(staged.fire._last_costs)
        if warming:
            self._warmup_done+=1
            if self._warmup_done==self._warmup_target:
                self._weather_state=weather.initial_state(self._profile,moisture_proxy=next_weather['moisture_proxy']);self._weather_kind='day'
        else:
            self._minute+=1
            if self._minute==staged.day_horizon:self._status='diagnostic_end'
        return self.situation()

    def checkpoint(self):
        if self._restoring_fresh:return super().checkpoint()
        value=dict(version=VERSION,identity=copy.deepcopy(self._ops_identity),fresh=super().checkpoint(),last_costs=copy.deepcopy(self._last_costs))
        if value['last_costs']!=value['fresh']['model']['fire']['last_costs']:
            raise RuntimeError('component and operations costs differ')
        value['sha256']=api._hash(value);return value

    def restore(self,value,*,expected_checkpoint_sha256):
        try:
            v=copy.deepcopy(value)
            if (type(v) is not dict or set(v)!={'version','identity','fresh','last_costs','sha256'}
                or type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                or v['sha256']!=expected_checkpoint_sha256 or v['sha256']!=api._hash({k:x for k,x in v.items() if k!='sha256'})
                or v['version']!=VERSION or v['identity']!=self._ops_identity or _source()!=self._ops_source
                or type(v['last_costs']) is not list or v['last_costs']!=v['fresh']['model']['fire']['last_costs']):
                raise ValueError('trusted operations checkpoint differs')
            self._last_costs=v['last_costs'];self._restoring_fresh=True
            try:super().restore(v['fresh'],expected_checkpoint_sha256=v['fresh']['sha256'])
            finally:self._restoring_fresh=False
            if api._encoded(self.checkpoint())!=api._encoded(v):raise ValueError('operations checkpoint continuation differs')
        except BaseException:self._poisoned=True;raise
