"""Synthetic staged Operations tests; no actor or source-route approval."""
import copy
from datetime import datetime,timezone
from types import SimpleNamespace

import adapter_v1 as api
import pytest
from envs.wildfire_response.incident_day.operations import Operations
from eval import incident_weather_v1 as weather
from wildfire_core.mobility_routing import make_journey,make_leg
import wildfire_source_population_tick_v1 as module


def make(monkeypatch,*,estimated=False,restriction=False,missing_route=False):
    group=dict(id='group',count=10,home=[250.,250.],position=[250.,250.],status='at_home',exposed=False,destination_id=None)
    dest=dict(id='destination',position=[1250.,250.],capacity=20,status='open')
    case=dict(original_case_id='original',original_case_sha256='scenario',adapted_groups=[group],adapted_destinations=[dest])
    class Host:
        def __init__(self):
            self._minute=0;self._status='active';self._identity={'synthetic':'host'}
            self._start=datetime(2021,6,1,12,tzinfo=timezone.utc);self._longitude=-4.;self._latitude=36.7;self._air_until=0
            self._selector=SimpleNamespace(_identity={'graph':'graph','source':'source'})
            self._case=dict(id='original',sha256='scenario',events=([dict(kind='access_restriction',resource_kind='engine',minute=1)] if restriction else []))
            self._ops=Operations([], [dest], [group], dict(order_delivery_minutes=1,order_ack_minutes=1,order_execution_minutes=1))
            grid=SimpleNamespace(width=3,height=1,domain=((True,True,True),),surface=(('land','land','land'),),elevation=((10.,10.,10.),))
            mask=((True,not estimated,True),);est=((False,estimated,False),)
            self._domain_inputs=SimpleNamespace(base=SimpleNamespace(contract=grid),source_mask=mask,estimated_mask=est)
            self._fire=SimpleNamespace(burning=lambda:[],_is_land=lambda x,y:True,_check=lambda:True)
            record=dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)
            self._weather=SimpleNamespace(_profile=weather.WeatherProfile.from_records([record]))
        def _ensure(self):pass
        def checkpoint(self):return {'minute':self._minute,'operations':self._ops.checkpoint(),'burning':self._fire.burning()}
        def _return_plan(self,*args):return None
    class Anchor:
        def __init__(self,host):self._selector=host._selector;self._identity={'selector':host._selector._identity,'candidate':'anchor'}
        def _ensure(self):pass
        def private_case(self,case_id):
            if case_id!='adapted':raise ValueError('unknown')
            return copy.deepcopy(case)
    class Router:
        def _hazards(self,burning,wind):return set(map(tuple,burning))
        def _cell(self,position):return (int(position[0]//500),int(position[1]//500))
        def _safe_cell(self,cell,hazards,mode):return cell not in hazards
    class Routes:
        def __init__(self):
            self._identity={'route':{'anchors_sha256':'anchor','graph_sha256':'graph','source_sha256':'source'}}
            self._route=SimpleNamespace(_guard=SimpleNamespace(_router=Router()));self.seen=[]
        def _ensure(self):pass
        def options(self,case_id,gid,did,origin,*,burning,wind,excluded_cells):
            self.seen.append(('options',tuple(burning),frozenset(excluded_cells)))
            if missing_route or burning or (1,0) in excluded_cells:return {'status':'unmapped','options':[]}
            leg=make_leg('road',[[250.,250.],[1250.,250.]],30,'edge','target')
            plan=make_journey([leg],[250.,250.],[1250.,250.],'synthetic test')
            return {'status':'exercise_assumption_route','options':[{'id':'route','journey':plan}]}
        def movement_safe(self,case_id,gid,did,journey,minute,*,current_position,burning,wind,excluded_cells):
            self.seen.append(('movement',tuple(burning),frozenset(excluded_cells)))
            return not burning and (1,0) not in excluded_cells
    monkeypatch.setattr(module,'DomainProfileGroundIncidentV1',Host)
    monkeypatch.setattr(module,'PopulationAnchorAdapterV1',Anchor)
    monkeypatch.setattr(module,'PopulationSegmentMovementV1',Routes)
    monkeypatch.setattr(module,'_BOUND',(Host,Operations,Anchor,Routes,weather.conditions,module.clock_module.solar_daylight))
    host=Host();anchors=Anchor(host);routes=Routes()
    identity=module.configuration_identity(host,anchors,routes,'adapted')
    stage=module.PopulationOperationsStageV1(host,anchors,routes,case_id='adapted',expected_identity=identity,opt_in=True)
    return host,stage,routes


def step(host,stage,command=None):
    return stage.stage_tick(minute=host._minute+1,command=command,suppress=lambda unit:False,
        other_hazard=lambda pos,kind:True,other_movement=lambda journey,minute,kind:True)


def commit(host,ops):host._ops=ops;host._minute+=1


def test_shelter_stages_one_tick_without_mutating_owner(monkeypatch):
    host,stage,_=make(monkeypatch);before=host._ops.checkpoint()
    staged,events=step(host,stage,dict(kind='shelter',group_id='group'))
    assert host._minute==0 and host._ops.checkpoint()==before and staged.last_minute==1
    assert any(k=='request_result' and p['accepted'] for _,k,p,_ in events)
    assert staged.orders and next(iter(staged.orders.values()))['action']=='shelter'


def test_exact_route_is_rechecked_during_movement(monkeypatch):
    host,stage,routes=make(monkeypatch)
    command=dict(kind='evacuate',group_id='group',destination_id='destination',option_id='route')
    staged,events=step(host,stage,command);assert any(k=='request_result' and p['accepted'] for _,k,p,_ in events)
    commit(host,staged)
    for _ in range(3):staged,_=step(host,stage);commit(host,staged)
    start=copy.deepcopy(host._ops.groups['group']['position'])
    host._fire.burning=lambda:[[2,0]]
    staged,_=step(host,stage)
    assert staged.groups['group']['position']==start and staged.groups['group']['status']=='blocked'
    assert any(item[0]=='movement' and item[1] for item in routes.seen)
    commit(host,staged);host._fire.burning=lambda:[]
    staged,_=step(host,stage)
    assert staged.groups['group']['position']!=start


@pytest.mark.parametrize('options',[{'missing_route':True},{'restriction':True},{'estimated':True}])
def test_unavailable_routes_do_not_create_orders(monkeypatch,options):
    host,stage,_=make(monkeypatch,**options)
    staged,events=step(host,stage,dict(kind='evacuate',group_id='group',destination_id='destination',option_id='route'))
    assert not staged.orders and any(k=='request_result' and p['accepted'] is False for _,k,p,_ in events)


def test_lineage_and_anchored_roster_fail_closed(monkeypatch):
    host,stage,routes=make(monkeypatch)
    routes._identity['route']['anchors_sha256']='different'
    with pytest.raises(ValueError,match='lineage differs'):step(host,stage)
    host,stage,_=make(monkeypatch)
    host._ops.groups['group']['home']=[0.,0.]
    with pytest.raises(ValueError,match='identity differs'):step(host,stage)


def test_invalid_command_is_nonmutating_and_external_mutation_poisoned(monkeypatch):
    host,stage,_=make(monkeypatch);before=host.checkpoint()
    with pytest.raises(ValueError):step(host,stage,dict(kind='evacuate',group_id='unknown',destination_id='destination',option_id='route'))
    assert host.checkpoint()==before
    unit=dict(id='crew',kind='crew',state='working',position=[250.,250.],base_position=[250.,250.],
              water_litres=0,capacity_litres=0,work_remaining=10,rest_minutes=1)
    host._ops=Operations([unit],list(host._ops.destinations.values()),list(host._ops.groups.values()),
                         dict(order_delivery_minutes=1,order_ack_minutes=1,order_execution_minutes=1))
    def illicit(_unit):host._minute=99;return True
    with pytest.raises(RuntimeError,match='changed the live owner'):
        stage.stage_tick(minute=1,suppress=illicit,other_hazard=lambda p,k:True,
                         other_movement=lambda j,m,k:True)
    assert host._poisoned is True


def test_same_minute_destination_closure_rejects_evacuation(monkeypatch):
    host,stage,_=make(monkeypatch)
    host._case['events']=[dict(kind='destination_closed',minute=1,destination_id='destination')]
    staged,events=step(host,stage,dict(kind='evacuate',group_id='group',destination_id='destination',option_id='route'))
    assert staged.destinations['destination']['status']=='closed'
    assert not staged.orders and any(k=='request_result' and p['accepted'] is False for _,k,p,_ in events)
    assert host._ops.destinations['destination']['status']=='open'


@pytest.mark.parametrize('event',[dict(kind='access_restriction',resource_kind='engine',minute=5),
                                  dict(kind='destination_closed',destination_id='destination',minute=5)])
def test_same_minute_event_blocks_an_active_evacuation(monkeypatch,event):
    host,stage,_=make(monkeypatch);host._case['events']=[event]
    staged,_=step(host,stage,dict(kind='evacuate',group_id='group',destination_id='destination',option_id='route'))
    commit(host,staged)
    for _ in range(3):staged,_=step(host,stage);commit(host,staged)
    before=copy.deepcopy(host._ops.groups['group']['position'])
    staged,_=step(host,stage)
    assert staged.groups['group']['position']==before and staged.groups['group']['status']=='blocked'


def test_stale_option_and_unanchored_origin_are_not_promoted(monkeypatch):
    host,stage,_=make(monkeypatch)
    staged,events=step(host,stage,dict(kind='evacuate',group_id='group',destination_id='destination',option_id='stale'))
    assert not staged.orders and any(k=='request_result' and p['accepted'] is False for _,k,p,_ in events)
    host,stage,_=make(monkeypatch)
    host._ops.groups['group']['position'][0]+=.05
    with pytest.raises(ValueError,match='handover differs'):step(host,stage)


def test_same_minute_aircraft_outage_is_applied(monkeypatch):
    host,stage,_=make(monkeypatch)
    host._case['events']=[dict(kind='aircraft_unavailable',minute=1,until=5)]
    staged,_=step(host,stage)
    assert staged.air_window_open is False


def test_missing_nonpopulation_guard_fails_without_mutation(monkeypatch):
    host,stage,_=make(monkeypatch);before=host.checkpoint()
    with pytest.raises(ValueError):
        stage.stage_tick(minute=1,suppress=lambda u:False,other_hazard=lambda p,k:True,other_movement=None)
    assert host.checkpoint()==before


def test_unsupported_scheduled_event_stops_before_any_staging(monkeypatch):
    host,stage,routes=make(monkeypatch)
    host._case['events']=[dict(kind='private-future-event',minute=1)]
    def unexpected_check():raise AssertionError('fire checked before event gate')
    host._fire._check=unexpected_check
    before=host.checkpoint();events=copy.deepcopy(host._case['events'])
    with pytest.raises(module.UnsupportedScheduledEvent) as error:
        step(host,stage,dict(kind='shelter',group_id='group'))
    assert str(error.value)=='unsupported scheduled event'
    assert 'private-future-event' not in str(error.value)
    assert host.checkpoint()==before and host._case['events']==events
    assert not routes.seen and not getattr(host,'_poisoned',False)
