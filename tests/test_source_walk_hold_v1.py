"""Synthetic single-clock containment exercise, never real access evidence."""
import copy
from types import SimpleNamespace

import adapter_v1 as api
import pytest
from shapely.geometry import Point, box
from shapely.strtree import STRtree
from envs.wildfire_response.incident_day.events import EventBus
from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, InteriorMixedFireV2, _VERIFIED
from wildfire_source_walk_exercise_v1 import SourceWalkExerciseV1, _eligible_fire
import wildfire_source_walk_exercise_v1 as walk_module
from wildfire_source_walk_hold_v1 import SourceWalkHoldV1, configuration_identity


def make_walk(monkeypatch, *, weak=False, budget=100):
    monkeypatch.setattr(walk_module,'_source_identity',lambda:'synthetic-walk-source')
    monkeypatch.setattr(walk_module,'_geometry_identity',lambda water:'synthetic-water')
    n=16;grid=dict(width=n,height=n,crs='EPSG:25830',transform=[500,0,0,0,-500,12000])
    doc=dict(version='wildfire-extended-inputs-v1',grid=grid,domain_mask=[[True]*n for _ in range(n)],
        surface=[['land']*n for _ in range(n)],fuel=[[4]*n for _ in range(n)],elevation=[[100.]*n for _ in range(n)],
        access=[['unknown']*n for _ in range(n)],source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
    inputs=MixedInputsV2(contract,(),tuple(sorted(dict(version='synthetic').items())),_VERIFIED)
    config=dict(work_gain=.01 if weak else 5,max_strength=10,decay=0,wind_factor=0,report_delay=2,wear_factor=0)
    def new_fire():
        fire=InteriorMixedFireV2(inputs,seed=3,containment_config=config,pressure_scale=100 if weak else .01,
                                 wind=[0,0],opt_in_mixed_experiment=True)
        fire._fire.spread_multiplier=1;fire._fire.rules['spread_factor']=10
        fire._fire.rules['ignition_probability_per_eligible_cell']=0
        assert fire.change({'ignite':[[12,12]]})
        return fire
    class Host:
        def __init__(self):
            self._minute=0;self._status='active';self._fire=new_fire();self.before_fire=None
            self._weather=SimpleNamespace(_fire=self._fire)
            self._ops=SimpleNamespace(profile={'ground_setup_minutes':2})
            self._bus=EventBus(5,['center']);self._bus.tick(0,{})
        def _ensure(self):pass
        def advance(self,n):
            assert n==1
            if self.before_fire is not None:self.before_fire();self.before_fire=None
            ok=self._fire.step();self._minute+=1
            if not ok:self._status='censored'
            else:self._bus.tick(self._minute,{})
            return self.situation()
        def situation(self):return dict(status=self._status,minute=self._minute,reports_cursor=len(self._bus.reports))
        def reports(self,*,after_cursor=0,limit=20):return self._bus.get_reports(after_cursor=after_cursor,limit=limit)
        def route_options(self,*args):return {'options':[]}
        def checkpoint(self):return dict(minute=self._minute,status=self._status,fire=self._fire.snapshot(),bus=self._bus.checkpoint())
        def restore(self,v,*,expected_checkpoint_sha256):
            assert api._hash(v)==expected_checkpoint_sha256
            self._fire=new_fire();self._fire.restore(v['fire']);self._fire._fire.spread_multiplier=1
            self._fire._fire.rules['spread_factor']=10;self._fire._fire.rules['ignition_probability_per_eligible_cell']=0
            self._weather._fire=self._fire;self._minute=v['minute'];self._status=v['status'];self._bus=EventBus.restore(v['bus'])
    walk=object.__new__(SourceWalkExerciseV1);walk._host=Host();walk._public_id='crew'
    walk._grid=grid;walk._supported=frozenset((x,y) for y in range(n) for x in range(n))
    walk._heights=[[100.]*n for _ in range(n)]
    walk._water=dict(bounds=box(-100,-100,12100,12100),land=box(-100,-100,12100,12100),
                     osm=STRtree([Point(100,100).buffer(1)]),dera=STRtree([Point(100,100).buffer(1)]))
    y=5750.;walk._initial=[5250.,y];walk._proposal=dict(front_cell=[12,12],site_cell=[11,12],site_position=[5990.,y])
    walk._route=[walk._initial,[5750.,y],[5990.,y]]
    walk._identity=dict(version=walk_module.VERSION,implementation='synthetic-walk-source',water='synthetic-water',
                        walk_configuration=walk._configuration_sha256())
    walk._candidate={'version':'synthetic'};walk._candidate['sha256']=api._hash(walk._candidate)
    walk._candidate_sha=walk._candidate['sha256']
    walk._ground=dict(phase='staged_assumed',position=copy.deepcopy(walk._initial),next_index=1,trail=[],
                      duty_end_at=100,work_remaining=budget,total_work_remaining=budget)
    walk._calls=[];walk._poisoned=False;walk._seal=api._hash(walk._host.checkpoint());walk._ground_seal=api._hash(walk._ground)
    walk._conditions=lambda minute:_eligible_fire(walk._host._fire.burning(),[0,0],walk._supported)
    return walk


def make_episode(monkeypatch, **kwargs):
    candidate=make_walk(monkeypatch,**kwargs)
    identity=configuration_identity(candidate,opt_in=True)
    return SourceWalkHoldV1(make_walk(monkeypatch,**kwargs),expected_identity=identity,opt_in=True)


def arrive(env):
    env.dispatch_crew('crew')
    for _ in range(60):
        if env.situation()['ground'][0]['phase']=='front_no_work':return
        env.advance()
    raise AssertionError('synthetic crew did not arrive')


def test_arrival_setup_work_cost_feedback_and_replay(monkeypatch):
    env=make_episode(monkeypatch)
    before=env.checkpoint()
    for action,args in ((env.start_hold,('crew',)),(env.dispatch_aircraft,('unknown',[0,0])),(env.advance,(0,))):
        with pytest.raises(ValueError):action(*args)
        assert api._encoded(before)==api._encoded(env.checkpoint())
    arrive(env);assert not env._fire._fire._containment._lines
    start=env._walk._ground['work_remaining'];env.start_hold('crew')
    assert env._hold['phase']=='setting_up' and not env._fire._fire._containment._lines
    env.advance();assert env._hold['phase']=='setting_up' and not env._fire._fire._containment._lines
    env.advance();assert env._hold['work_minutes']==1 and env._hold['phase']=='holding'
    assert env._walk._ground['work_remaining']==start-3
    assert env._fire.costs==[dict(resource_id='crew',work_minutes=1,water_litres=0.0)]
    # A rejected aircraft request after staging a hold assignment must not
    # consume the assignment, charge duty, advance time or poison the host.
    current=env.checkpoint()
    with pytest.raises(ValueError):env.dispatch_aircraft('unknown',[0,0])
    assert api._encoded(env.checkpoint())==api._encoded(current)
    assert not any(r['kind']=='synthetic_hold_observation' for r in env.reports(limit=200))
    env.advance(5)
    assert any(r['kind']=='synthetic_hold_observation' and r['payload']['status']=='holding' for r in env.reports(limit=200))
    snapshot=env.checkpoint();clone=make_episode(monkeypatch)
    clone.restore(snapshot,expected_checkpoint_sha256=api._hash(snapshot))
    assert clone.advance(2)==env.advance(2)
    assert api._encoded(clone.checkpoint())==api._encoded(env.checkpoint())
    tape=env.transcript();fresh=make_episode(monkeypatch)
    assert fresh.replay(tape,expected_sha256=api._hash(tape),expected_final_checkpoint_sha256=api._hash(env.checkpoint()))==env.situation()
    assert 'score' not in env.situation() and 'unscored' in env.situation()['scope']
    altered=make_episode(monkeypatch);altered._edge[0][0]+=1
    with pytest.raises(RuntimeError,match='implementation or state'):altered.situation()


def test_breach_stops_work_and_is_delayed(monkeypatch):
    env=make_episode(monkeypatch,weak=True);arrive(env);env.start_hold('crew');env.advance(2)
    assert env._hold['phase']=='breached' and env._hold['work_minutes']==1
    assert not any(r['kind']=='synthetic_hold_observation' and r['payload']['status']=='breached' for r in env.reports(limit=200))
    env.advance(5);assert env._hold['work_minutes']==1
    assert any(r['kind']=='synthetic_hold_observation' and r['payload']['status']=='breached' for r in env.reports(limit=200))


def test_finite_budget_prevents_continued_work(monkeypatch):
    env=make_episode(monkeypatch,budget=19);arrive(env);env.start_hold('crew')
    for _ in range(20):
        env.advance()
        if env._hold['phase']=='stopped':break
    assert env._hold['phase']=='stopped'
    work=env._hold['work_minutes'];assert work>=1
    env.advance(2)
    assert env._hold['work_minutes']==work and env._walk._ground['work_remaining']>=0


def test_pre_and_post_work_hazard_checks(monkeypatch):
    # A newly unsafe setup stops without creating a containment assignment.
    env=make_episode(monkeypatch);arrive(env);env.start_hold('crew')
    env._walk._conditions=lambda minute:({(11,12)}, {})
    env.advance()
    assert env._hold['phase']=='stopped' and env._hold['work_minutes']==0
    assert not env._fire._fire._containment._lines
    # If the fire becomes unsafe during the work tick, the charged physical
    # work remains private and no unsafe line observation is emitted.
    env=make_episode(monkeypatch);arrive(env);env.start_hold('crew');env.advance()
    prior=env._walk._host._minute;normal=env._walk._conditions
    env._walk._conditions=lambda minute:normal(minute) if env._walk._host._minute==prior else ({(11,12)}, {})
    env.advance()
    assert env._hold['work_minutes']==1 and env._hold['phase']=='stopped'
    assert env._walk._ground['phase']=='needs_assistance'
    assert not any(r['kind']=='synthetic_hold_observation' for r in env.reports(limit=200))

    # A drop can extinguish the documented front after preparation and before
    # the fire kernel. The staged assignment must be withdrawn at that seam.
    env=make_episode(monkeypatch);arrive(env);env.start_hold('crew');env.advance()
    before=env._walk._ground['work_remaining']
    env._walk._host.before_fire=lambda:env._fire._fire.cells[12][12].update(heat=0.)
    env.advance()
    assert env._hold['phase']=='stopped' and env._hold['work_minutes']==0
    assert env._fire.work_blocked and env._fire.costs==[]
    assert env._walk._ground['work_remaining']==before-1
    assert not env._fire._fire._containment._lines


def test_private_hold_state_does_not_change_immediate_request(monkeypatch):
    normal=make_episode(monkeypatch);arrive(normal)
    state=normal.checkpoint();hidden=make_episode(monkeypatch)
    hidden.restore(state,expected_checkpoint_sha256=api._hash(state))
    hidden._hold['phase']='stopped';hidden._hold_seal=api._hash(hidden._hold)
    assert normal.situation()==hidden.situation()
    assert normal.start_hold('crew')==hidden.start_hold('crew')
    assert normal.situation()==hidden.situation()
    assert not any(r['kind']=='synthetic_hold_command_result' for r in normal.reports(limit=200))
    assert not any(r['kind']=='synthetic_hold_command_result' for r in hidden.reports(limit=200))
    normal.advance(8);hidden.advance(8)
    def accepted(env):
        return next(r['payload']['accepted'] for r in env.reports(limit=200)
                    if r['kind']=='synthetic_hold_command_result')
    assert accepted(normal) is True and accepted(hidden) is False
    assert 'edge' not in normal.situation()['hold'] and 'strength' not in normal.situation()['hold']


def test_retreat_stops_work_without_inventing_road_return(monkeypatch):
    env=make_episode(monkeypatch);arrive(env);env.start_hold('crew');env.advance(2)
    assert env._hold['phase']=='holding' and env._hold['work_minutes']==1
    env.retreat('crew');assert env._hold['phase']=='stopped'
    for _ in range(40):
        if env._walk._ground['phase']=='needs_assistance':break
        env.advance()
    assert env._walk._ground['phase']=='needs_assistance'
    assert env._walk._ground['position']==env._walk._initial
    work=env._hold['work_minutes'];env.advance(2)
    assert env._hold['work_minutes']==work


def test_delayed_hold_status_does_not_regress_on_late_delivery(monkeypatch):
    env=make_episode(monkeypatch);host=env._walk._host
    host._bus.emit('operations','synthetic_hold_status',{'crew_id':'crew','phase':'idle'},0,delay=5)
    env._walk._seal=api._hash(host.checkpoint())
    env.advance()
    host._bus.emit('operations','synthetic_hold_status',{'crew_id':'crew','phase':'holding'},1,delay=1)
    env._walk._seal=api._hash(host.checkpoint())
    env.advance();assert env.situation()['hold']['phase']=='holding'
    env.advance(3);assert env.situation()['hold']['phase']=='holding'
