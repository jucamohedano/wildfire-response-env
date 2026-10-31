"""Synthetic shared clock integration; no source-access claim."""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace

import adapter_v1 as api
import pytest
from test_source_domain_ground_hold_v1 import make_walk
from envs.wildfire_response.incident_day.operations import Operations
from eval import incident_weather_v1 as weather
from wildfire_core.mobility_routing import make_journey, make_leg
import wildfire_source_population_tick_v3 as stage
import wildfire_domain_population_host_v2 as owner
from wildfire_source_domain_ground_v1.hold import DomainSourceWalkHoldV1, configuration_identity


def make(monkeypatch, *, collide=False):
    walk=make_walk(monkeypatch);h=walk._host
    group=dict(id='private-group',count=10,home=[750.,10750.],position=[750.,10750.],status='at_home',exposed=False,destination_id=None)
    dest=dict(id='private-destination',position=[1750.,10750.],capacity=20,status='open')
    if collide:group['id']='cohort-1';dest['id']='destination-1'
    case=dict(original_case_id='original',original_case_sha256='scenario',adapted_groups=[group],adapted_destinations=[dest])
    h._selector=SimpleNamespace(_identity={'graph':'graph','source':'source'})
    h._identity={'case':'adapted'};h._case=dict(id='original',sha256='scenario',events=[],centers=['center'])
    h._start=datetime(2021,6,1,12,tzinfo=timezone.utc);h._longitude=-4.;h._latitude=36.7
    h._air_until=0;h._closed={};h._dose={group['id']:0};h._drop_audit=[];h._seen=0
    h._known={};h._known_wind=[0.,0.];h._handover_fire=h._fire.burning();h._calls=[]
    h._return_plan=lambda *a:None
    h._ops=Operations([], [dest], [group], dict(ground_setup_minutes=2,order_delivery_minutes=1,order_ack_minutes=1,order_execution_minutes=1))
    contract=h._fire._inputs;h._inputs=SimpleNamespace(contract=contract)
    h._domain_inputs=SimpleNamespace(base=SimpleNamespace(contract=contract),source_mask=tuple(tuple((x,y)!=(13,12) for x in range(16)) for y in range(16)),estimated_mask=tuple(tuple((x,y)==(13,12) for x in range(16)) for y in range(16)))
    record=dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)
    h._weather._profile=weather.WeatherProfile.from_records([record]);h._weather._minute=0
    def weather_step():
        ok=h._weather._fire.step();h._weather._minute+=1
        return {'status':'active' if ok else 'censored'}
    h._weather.step=weather_step;h._weather_reports=lambda m:None;h._incoming=lambda m:None;h._sync=lambda:None
    old_checkpoint=h.checkpoint;old_restore=h.restore
    def checkpoint():
        return dict(base=old_checkpoint(),ops=h._ops.checkpoint(),weather_minute=h._weather._minute,dose=copy.deepcopy(h._dose),closed=copy.deepcopy(h._closed),air=h._air_until,drops=copy.deepcopy(h._drop_audit),seen=h._seen)
    def restore(v,*,expected_checkpoint_sha256):
        assert api._hash(v)==expected_checkpoint_sha256
        old_restore(v['base'],expected_checkpoint_sha256=api._hash(v['base']))
        h._ops=Operations.restore(v['ops'],return_planner=h._return_plan);h._weather._minute=v['weather_minute']
        h._dose=copy.deepcopy(v['dose']);h._closed=copy.deepcopy(v['closed']);h._air_until=v['air'];h._drop_audit=copy.deepcopy(v['drops']);h._seen=v['seen']
    h.checkpoint=checkpoint;h.restore=restore;walk._seal=api._hash(h.checkpoint())
    class Anchor:
        def __init__(self):self._selector=h._selector;self._identity={'selector':h._selector._identity,'candidate':'anchor'}
        def _ensure(self):pass
        def private_case(self,key):
            assert key=='adapted';return copy.deepcopy(case)
    class Router:
        def _hazards(self,burning,wind):return set(map(tuple,burning))
        def _cell(self,pos):return (int(pos[0]//500),int((12000-pos[1])//500))
        def _safe_cell(self,cell,hazards,mode):return cell not in hazards
    class Routes:
        def __init__(self):
            self._identity={'route':{'anchors_sha256':'anchor','graph_sha256':'graph','source_sha256':'source'}}
            self._route=SimpleNamespace(_guard=SimpleNamespace(_router=Router()));self.blocked=False;self.private_blocked=False
        def _ensure(self):pass
        def options(self,*args,**kwargs):
            leg=make_leg('road',[group['position'],dest['position']],30,'edge','target')
            plan=make_journey([leg],group['position'],dest['position'],'synthetic')

            if self.private_blocked and kwargs['burning']:return {'status':'blocked','options':[]}
            return {'status':'exercise_assumption_route','options':[{'id':api._hash([kwargs['burning'],kwargs['wind']]),'journey':plan,'travel_minutes':30}]}
        def movement_safe(self,*args,**kwargs):return not self.blocked
    monkeypatch.setattr(stage,'DomainProfileGroundIncidentV1',type(h))
    for mod in (stage,owner):
        monkeypatch.setattr(mod,'PopulationAnchorAdapterV1',Anchor);monkeypatch.setattr(mod,'PopulationSegmentMovementV1',Routes)
    monkeypatch.setattr(stage,'_BOUND',(type(h),Operations,Anchor,Routes,weather.conditions,stage.clock_module.solar_daylight))
    ident=configuration_identity(walk,opt_in=True);hold=DomainSourceWalkHoldV1(walk,expected_identity=ident,opt_in=True)
    a=Anchor();r=Routes();return owner.DomainPopulationGroundV2(hold,a,r,opt_in=True),r


def test_shared_tick_and_private_handover(monkeypatch):
    env,_=make(monkeypatch)
    public=owner.PublicDomainPopulationV2(env)
    with pytest.raises(AttributeError):public.checkpoint
    view=public.situation();assert view['scope']==owner.SCOPE
    assert view['population'][0]['id']=='cohort-1'
    assert 'private-group' not in str(view) and 'private-destination' not in str(view)
    env.dispatch_crew('crew');env.protect('cohort-1','shelter')
    assert env._host._minute==2 and env._host._ops.last_minute==2 and env._host._weather._minute==2
    assert not any(r['kind']=='request_result' for r in public.reports(limit=200))
    env.advance(5)
    assert env._host._ops.groups['private-group']['status']=='sheltering'
    assert 'private-group' not in str(env.reports(limit=200))


def test_rejected_command_preserves_owner(monkeypatch):
    env,_=make(monkeypatch);before=env.checkpoint()
    with pytest.raises(ValueError):env.protect('private-group','shelter')
    with pytest.raises(ValueError):env.advance(0)
    assert env.checkpoint()==before


def test_public_aliases_are_disjoint_from_source_ids(monkeypatch):
    env,_=make(monkeypatch,collide=True);view=env.situation()
    assert view['population'][0]['id']=='public-cohort-1'
    assert view['destinations'][0]['id']=='public-destination-1'
    env.protect('public-cohort-1','shelter');env.advance(5)
    assert 'cohort-1' not in [r['payload'].get('group_id') for r in env.reports(limit=200)]


def test_center_response_is_aliased_only_in_public_reports(monkeypatch):
    env,_=make(monkeypatch);alias=env.situation()['centers'][0]
    assert alias!='center'
    env.request_status(alias)
    assert not any(r['source']=='center' for r in env.reports(limit=200))
    for _ in range(20):
        env.advance()
        if any(r['source']=='center' for r in env._host._bus.reports):break
    else:raise AssertionError('center response was not delivered')
    private=[r for r in env._host._bus.reports if r['source']=='center']
    public=[r for r in env.reports(limit=200) if r['id'] in {p['id'] for p in private}]
    assert public and all(r['source']==alias for r in public)
    assert all(r['source']=='center' for r in private)


def test_population_order_shares_hold_work_tick(monkeypatch):
    env,_=make(monkeypatch);env.dispatch_crew('crew')
    for _ in range(60):
        if env.situation()['ground'][0]['phase']=='front_no_work':break
        env.advance()
    else:raise AssertionError('crew did not arrive')
    env.start_hold('crew')
    for _ in range(10):
        if env._hold._hold['work_minutes']>0:break
        env.advance()
    else:raise AssertionError('hold did not start')
    before=env._hold._hold['work_minutes'];minute=env._host._minute
    env.protect('cohort-1','shelter')
    assert env._host._minute==minute+1==env._host._ops.last_minute
    assert env._hold._hold['work_minutes']==before+1
    assert len(env._hold._fire.costs)==1


def test_route_movement_guard_and_replay(monkeypatch):
    env,routes=make(monkeypatch)
    option=env.evacuation_options('cohort-1','destination-1')['options'][0]['id']
    env.protect('cohort-1','evacuate','destination-1',option);env.advance(3)
    before=copy.deepcopy(env._host._ops.groups['private-group']['position']);routes.blocked=True
    env.advance();assert env._host._ops.groups['private-group']['position']==before
    routes.blocked=False;env.advance();assert env._host._ops.groups['private-group']['position']!=before
    snapshot=env.checkpoint();expected=env.situation();continuation=env.advance(2)
    clone,_=make(monkeypatch)
    clone.restore(snapshot,expected_checkpoint_sha256=api._hash(snapshot))
    assert clone.situation()==expected
    assert clone.advance(2)==continuation


def test_exact_transcript_replay(monkeypatch):
    env,_=make(monkeypatch);env.protect('cohort-1','shelter');env.advance(4)
    transcript=env.transcript();checkpoint=env.checkpoint();expected=env.situation()
    clone,_=make(monkeypatch)
    assert clone.replay(transcript,expected_sha256=api._hash(transcript),
                        expected_final_checkpoint_sha256=api._hash(checkpoint))==expected


def test_unsupported_event_does_not_stage_operations(monkeypatch):
    env,_=make(monkeypatch);env._host._case['events']=[{'minute':1,'kind':'secret-unsupported'}]
    before=env._host._ops.checkpoint();env.advance()
    assert env._host._status=='unsupported' and env._host._minute==0
    assert env._host._ops.checkpoint()==before
    assert 'secret-unsupported' not in str(env.situation())
    stopped=env.checkpoint()
    with pytest.raises(ValueError):env.protect('cohort-1','shelter')
    assert env.checkpoint()==stopped


def test_public_path_identity_survives_unseen_hazard_change(monkeypatch):
    env,routes=make(monkeypatch)
    # The public handover fire is partial; the real backend still burns a
    # remote cell. Both hazard snapshots leave this exact test road open.
    env._host._handover_fire=[]
    group=env._group_ids['cohort-1'];dest=env._destination_ids['destination-1']
    origin=env._stage._groups[group]['position']
    public=routes.options('original',group,dest,origin,burning=[],wind=[0,0],excluded_cells=())
    actual=routes.options('original',group,dest,origin,burning=env._host._fire.burning(),wind=[0,0],excluded_cells=())
    assert public['options'][0]['id']!=actual['options'][0]['id']
    assert public['options'][0]['journey']==actual['options'][0]['journey']
    option=env.evacuation_options('cohort-1','destination-1')['options'][0]['id']
    env.protect('cohort-1','evacuate','destination-1',option)
    assert env._host._ops.orders
    assert not any(r['kind']=='request_result' for r in env.reports(limit=200))


def test_actual_route_hazard_rejects_only_via_delayed_feedback(monkeypatch):
    env,routes=make(monkeypatch);env._host._handover_fire=[]
    option=env.evacuation_options('cohort-1','destination-1')['options'][0]['id']
    routes.private_blocked=True
    result=env.protect('cohort-1','evacuate','destination-1',option)
    assert result['minute']==1 and not env._host._ops.orders
    assert not any(r['kind']=='request_result' for r in env.reports(limit=200))
    env.advance(5)
    assert any(r['kind']=='request_result' and r['payload']['accepted'] is False for r in env.reports(limit=200))


def test_private_revalidation_cannot_substitute_another_path(monkeypatch):
    env,routes=make(monkeypatch);env._host._handover_fire=[]
    option=env.evacuation_options('cohort-1','destination-1')['options'][0]['id']
    original=routes.options
    def different_path(*args,**kwargs):
        result=original(*args,**kwargs)
        if kwargs['burning'] and result['options']:
            result['options'][0]['journey']['resource_mode']='different-path'
        return result
    routes.options=different_path
    env.protect('cohort-1','evacuate','destination-1',option)
    assert not env._host._ops.orders
    env.advance(5)
    assert any(r['kind']=='request_result' and not r['payload']['accepted'] for r in env.reports(limit=200))
