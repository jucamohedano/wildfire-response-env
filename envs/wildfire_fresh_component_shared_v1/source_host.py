"""Fresh-component source incident state for a shared operations transaction.

Trusted driver only. This module does not expose a public actor facade.
"""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations
from eval.extended_map_common import digest
from eval.fresh_two_component_operations_v1 import FreshTwoComponentOperationsV1
from eval.two_component_fire_v2 import _unknown
from wildfire_source_domain_ground_v1.host import DomainProfileGroundIncidentV1
from wildfire_expanded_incident_v1.estimated_fuel_v1 import EstimatedMixedFireV1
from wildfire_source_domain_v3 import host as original

VERSION='wildfire-fresh-component-source-host-v1'


def _source():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class _FireView:
    """Read-only projection. Work goes through the fresh owner's tick."""
    def __init__(self,host):self._host=host
    @property
    def _fire(self):return self._host._fresh._model.fire
    def _check(self):
        fresh=self._host._fresh
        return fresh._status=='active' and fresh._model._guard() is None
    def _is_land(self,x,y):
        h=self._host;c=h._inputs.contract;f=h._fresh._model
        if not (0<=x<c.width and 0<=y<c.height and c.domain[y][x]
                and (x,y) in h._legacy_fire_support):return False
        cell=f._components[y][x];z=f.fire.heights[y][x]
        return (not _unknown(cell) and f.transfers[y][x]!='unknown' and f.crossings[y][x]!='unknown'
                and type(z) in (int,float) and math.isfinite(z) and z==c.elevation[y][x]
                and any(type(cell[k]) is dict for k in ('vegetation','built')))
    def burning(self):
        fire=self._fire
        return [[x,y] for y,row in enumerate(fire.cells) for x,cell in enumerate(row)
                if cell['heat']>0 and cell['fuel']>0]


class _WeatherView:
    def __init__(self,host):self._host=host;self._fire=host._fire
    @property
    def _profile(self):return self._host._fresh._profile
    @property
    def _minute(self):return self._host._fresh._minute
    @property
    def _status(self):return self._host._fresh._status
    def checkpoint(self):return self._host._fresh.checkpoint()


class FreshComponentSourceHostV1(original.DomainProfileSourceIncidentV3):
    """Bind a fresh day-handover to already validated source operations data."""
    def __init__(self,base,fresh,*,opt_in=False):
        if opt_in is not True or type(base) is not DomainProfileGroundIncidentV1 or type(fresh) is not FreshTwoComponentOperationsV1:
            raise ValueError('exact source and fresh component owners required')
        base._ensure();fresh._ensure()
        if (base._status!='active' or base._minute!=0 or base._calls or type(base._fire) is not EstimatedMixedFireV1
                or fresh._status!='active'
                or fresh._weather_kind!='day' or fresh._minute!=0 or fresh._warmup_done!=fresh._warmup_target
                or fresh._model.fire.tick-fresh._model._model_start_tick!=fresh._warmup_target
                or fresh._weather_state['minute']!=0 or fresh._model.day_horizon!=1440
                or fresh._model._guard() is not None):raise ValueError('fresh active day handover required')
        if (fresh._identity['case_sha256']!=base._firecase['sha256'] or fresh._identity['parent_sha256']!=base._selector._identity['parent']
                or fresh._identity['catalog_sha256']!=dict(base._inputs.identity)['catalog']):
            raise ValueError('fresh component scenario lineage differs')
        c=base._inputs.contract;grid=fresh._model.fire
        if (grid.width!=c.width or grid.height!=c.height or digest(fresh._private_audit)!=fresh._identity['staging']['provenance_sha256']
                or fresh._private_audit['strict_sha256']!=dict(base._inputs.identity)['strict']
                or fresh._private_audit['water_sha256']!=dict(base._inputs.identity)['water_v2']):
            raise ValueError('component and access source lineage differs')
        self.__dict__=copy.copy(base.__dict__)
        self._case=copy.deepcopy(base._case);self._firecase=copy.deepcopy(base._firecase)
        self._ops=Operations.restore(base._ops.checkpoint(),return_planner=self._return_plan)
        self._fresh=fresh
        self._legacy_fire_support=frozenset(
            {(x,y) for y in range(c.height) for x in range(c.width) if c.fire_support[y][x] and c.surface[y][x]=='land'}
            | {(x,y) for x,y,*_ in base._domain_inputs.base.factors}
            | {(x,y) for x,y,*_ in base._domain_inputs.estimates})
        self._fire=_FireView(self);self._weather=_WeatherView(self)
        self._minute=0;self._status='active';self._reason=None;self._unsupported_kind=None
        self._closed={};self._air_until=0;self._seen=0;self._calls=[];self._drop_audit=[];self._recording=True;self._poisoned=False
        self._dose={k:0 for k in self._ops.groups};self._known={k:original._view(v) for k,v in self._ops.resources.items()}
        self._known_at={k:0 for k in self._known};self._known_wind=None;self._wind_at=-1
        burning=sorted(self._fire.burning());self._handover_fire=copy.deepcopy(burning[:max(1,len(burning)//2)])
        self._bus=EventBus(self._case['seed'],self._case['centers'])
        self._identity=dict(version=VERSION,source=_source(),base=copy.deepcopy(base._identity),fresh=copy.deepcopy(fresh._ops_identity),
            case=base._identity['case'],catalog=copy.deepcopy(base._selector._identity),original=self._case['sha256'],
            expanded=self._firecase['sha256'],domain_input=base._input_sha,domain_source=base._domain_source,
            legacy_fire_support=api._hash(sorted(self._legacy_fire_support)))
        self._identity_seal=api._hash(self._identity);self._own_source=_source()
        t=c.transform
        for group in self._ops.groups.values():
            x=math.floor((group['position'][0]-t[2])/t[0]);y=math.floor((group['position'][1]-t[5])/t[4])
            if not (0<=x<c.width and 0<=y<c.height):raise ValueError('population position outside domain')
            if [x,y] in burning and not group['exposed']:raise ValueError('inherited exposure declaration differs')
        self._bus.emit('handover','handover',{'cells':copy.deepcopy(self._handover_fire),
            'text':'Synthetic component exercise; initial fire observations may be stale.'},0,role='center',delay=0)
        self._weather_reports(0);self._incoming(0);self._bus.tick(0,{});self._sync()
        self._ensure()

    def _ensure(self):
        if self._poisoned:raise RuntimeError('component host indeterminate')
        try:
            self._selector._ensure();self._fresh._ensure();original._check_domain(self._domain_inputs,self._selector,self._input_sha)
            if (_source()!=self._own_source or api._hash(self._identity)!=self._identity_seal
                or self._identity['fresh']!=self._fresh._ops_identity or self._identity['catalog']!=self._selector._identity
                or api._hash(sorted(self._legacy_fire_support))!=self._identity['legacy_fire_support']
                or api._hash({k:v for k,v in self._case.items() if k!='sha256'})!=self._identity['original']
                or api._hash({k:v for k,v in self._firecase.items() if k!='sha256'})!=self._identity['expanded']
                or type(self._fresh) is not FreshTwoComponentOperationsV1
                or digest(self._fresh._private_audit)!=self._fresh._identity['staging']['provenance_sha256']
                or self._fresh._minute!=self._minute or self._ops.last_minute!=(self._minute if self._minute else None)
                or (self._status!='censored' and self._bus._last_tick!=self._minute)
                or type(self._fire) is not _FireView or self._fire._host is not self
                or type(self._weather) is not _WeatherView or self._weather._host is not self or self._weather._fire is not self._fire):
                raise RuntimeError('component host identity or clock changed')
        except BaseException:self._poisoned=True;raise

    def _step(self,*args,**kwargs):
        raise RuntimeError('component source actions require the shared transaction')

    def situation(self):
        value=super().situation();value['version']=VERSION
        if value['status']!='censored':value['scope']='fresh component source metadata; shared transaction required'
        return value

    def checkpoint(self):
        self._ensure()
        if self._status=='censored':raise RuntimeError('checkpoint unavailable after censor')
        value=dict(version=VERSION,identity=copy.deepcopy(self._identity),minute=self._minute,status=self._status,
            reason=self._reason,unsupported_kind=self._unsupported_kind,weather=self._fresh.checkpoint(),operations=self._ops.checkpoint(),
            communications=self._bus.checkpoint(),known=copy.deepcopy(self._known),known_at=copy.deepcopy(self._known_at),
            known_wind=copy.deepcopy(self._known_wind),wind_at=self._wind_at,handover_fire=copy.deepcopy(self._handover_fire),
            closed=copy.deepcopy(self._closed),air_until=self._air_until,seen=self._seen,drop_audit=copy.deepcopy(self._drop_audit),
            dose=copy.deepcopy(self._dose),calls=copy.deepcopy(self._calls))
        value['sha256']=api._hash(value);return value

    def restore(self,checkpoint,*,expected_checkpoint_sha256):
        try:
            v=copy.deepcopy(checkpoint)
            keys={'version','identity','minute','status','reason','unsupported_kind','weather','operations','communications','known','known_at',
                  'known_wind','wind_at','handover_fire','closed','air_until','seen','drop_audit','dose','calls','sha256'}
            if (type(v) is not dict or set(v)!=keys or type(expected_checkpoint_sha256) is not str
                or api._hash(v)!=expected_checkpoint_sha256 or v['sha256']!=api._hash({k:x for k,x in v.items() if k!='sha256'})
                or v['version']!=VERSION or v['identity']!=self._identity or v['handover_fire']!=self._handover_fire
                or type(v['minute']) is not int or not 0<=v['minute']<=1440
                or v['status'] not in ('active','unsupported','end_unscored')
                or (v['status']=='unsupported')!=(v['reason']=='unsupported_scheduled_event' and type(v['unsupported_kind']) is str)
                or (v['status']=='end_unscored')!=(v['minute']==1440)):
                raise ValueError('component host checkpoint differs')
            self._fresh.restore(v['weather'],expected_checkpoint_sha256=v['weather']['sha256'])
            ops=Operations.restore(v['operations'],return_planner=self._return_plan);bus=EventBus.restore(v['communications'])
            aircraft={r['id']:r for r in self._case['resources'] if r['kind']=='aircraft'}
            if (set(ops.resources)!=set(aircraft) or set(ops.groups)!={g['id'] for g in self._case['groups']}
                or set(ops.destinations)!={d['id'] for d in self._case['destinations']} or set(v['dose'])!=set(ops.groups)
                or set(v['known'])!=set(aircraft) or set(v['known_at'])!=set(aircraft)
                or any(ops.resources[k]['base_position']!=aircraft[k]['base_position'] or ops.resources[k]['facility_id']!=aircraft[k]['facility_id'] for k in aircraft)
                or ops.last_minute!=(v['minute'] if v['minute'] else None) or self._fresh._minute!=v['minute']
                or bus.seed!=self._case['seed'] or bus._last_tick!=v['minute'] or v['seen']!=len(bus.reports)
                or type(v['air_until']) is not int or not 0<=v['air_until']<=1440 or type(v['closed']) is not dict
                or not set(v['closed'])<=set(self._case['centers']) or type(v['wind_at']) is not int
                or not -1<=v['wind_at']<=v['minute'] or type(v['calls']) is not list or type(v['drop_audit']) is not list):
                raise ValueError('component host restored state differs')
            self._ops,self._bus=ops,bus;self._minute,self._status=v['minute'],v['status']
            self._reason,self._unsupported_kind=v['reason'],v['unsupported_kind']
            self._known,self._known_at=v['known'],v['known_at'];self._known_wind,self._wind_at=v['known_wind'],v['wind_at']
            self._closed,self._air_until,self._seen=v['closed'],v['air_until'],v['seen']
            self._drop_audit,self._dose,self._calls=v['drop_audit'],v['dose'],v['calls'];self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(v):raise ValueError('component host restore differs')
        except BaseException:self._poisoned=True;raise
