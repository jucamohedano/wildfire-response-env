"""Synthetic walk profile and finite lifecycle; no real-world access claim."""
import copy
from shapely.geometry import Point, box
from shapely.strtree import STRtree
from wildfire_source_walk_exercise_v1 import SourceWalkExerciseV1, _eligible_fire, _segment_safe


def fixture():
    obj=object.__new__(SourceWalkExerciseV1)
    obj._grid=dict(transform=[500,0,0,0,-500,1500])
    obj._supported=frozenset((x,y) for x in range(4) for y in range(3))
    obj._heights=[[0.]*4 for _ in range(3)]
    obj._water=dict(bounds=box(0,0,2000,1500),land=box(0,0,2000,1500),
                    osm=STRtree([Point(1900,100).buffer(1)]),dera=STRtree([Point(1900,100).buffer(1)]))
    obj._proposal=dict(front_cell=[2,1],site_cell=[1,1])
    obj._route=[[250.,750.],[750.,750.],[990.,750.]]
    ground=dict(phase='staged_assumed',position=[250.,750.],next_index=1,trail=[],
                duty_end_at=100,work_remaining=100,total_work_remaining=100)
    return obj,ground


def test_walk_front_retreat_and_finite_budget():
    obj,g=fixture();hazards,front=_eligible_fire([[2,1]],[0,0],obj._supported)
    for minute in range(1,30):
        g,_=obj._ground_tick(g,minute,hazards,front,'dispatch' if minute==1 else None)
        if g['phase']=='front_no_work':break
    assert g['phase']=='front_no_work' and g['position']==[990.,750.]
    assert g['work_remaining']<100
    for minute in range(minute+1,70):
        g,_=obj._ground_tick(g,minute,hazards,front,'retreat' if g['phase']=='front_no_work' else None)
        if g['phase']=='needs_assistance':break
    assert g['phase']=='needs_assistance' and g['position']==[250.,750.]
    # The final state explicitly requires help because road return is unknown.
    exhausted=copy.deepcopy(g);exhausted.update(phase='walking',work_remaining=0)
    result,_=obj._ground_tick(exhausted,1,hazards,front,None)
    assert result['phase']=='needs_assistance'


def test_actual_fire_wind_water_and_grade_revalidation():
    obj,g=fixture();hazards,front=_eligible_fire([[2,1]],[0,0],obj._supported)
    g,_=obj._ground_tick(g,1,hazards,front,'dispatch')
    # If actual modeled fire occupies the current cell, do not invent egress.
    blocked,front2=_eligible_fire([[0,1]],[0,0],obj._supported)
    stopped,_=obj._ground_tick(g,2,blocked,front2,None)
    assert stopped['phase']=='needs_assistance' and stopped['position']==g['position']
    # A fire shift that invalidates the site starts a guarded retreat.
    nofire,newfront=_eligible_fire([], [0,0],obj._supported)
    returning,_=obj._ground_tick(g,2,nofire,newfront,None)
    assert returning['phase'] in ('retreating','needs_assistance')
    obj._heights[1][1]=200.
    assert not _segment_safe([490.,750.],[510.,750.],grid=obj._grid,heights=obj._heights,
                             supported=obj._supported,hazards=set(),water=obj._water)
    obj._heights[1][1]=0.
    obj._water['osm']=STRtree([Point(500,750).buffer(10)])
    assert not _segment_safe([490.,750.],[510.,750.],grid=obj._grid,heights=obj._heights,
                             supported=obj._supported,hazards=set(),water=obj._water)


def test_shared_clock_delayed_reports_checkpoint_and_replay(monkeypatch):
    import adapter_v1 as api
    from envs.wildfire_response.incident_day.events import EventBus
    import wildfire_source_walk_exercise_v1 as module
    monkeypatch.setattr(module,'_source_identity',lambda:'test-source')
    monkeypatch.setattr(module,'_geometry_identity',lambda water:'test-water')
    class Host:
        def __init__(self):
            from types import SimpleNamespace
            self._fire=object();self._weather=SimpleNamespace(_fire=self._fire)
            self._minute=0;self._status='active';self._bus=EventBus(1,['center']);self._bus.tick(0,{})
        def _ensure(self):pass
        def advance(self,n):
            assert n==1
            self._minute+=1;self._bus.tick(self._minute,{})
            return self.situation()
        def situation(self):return dict(status=self._status,minute=self._minute,reports_cursor=len(self._bus.reports))
        def route_options(self,*args):return {'options':[]}
        def reports(self,*,after_cursor=0,limit=20):return self._bus.get_reports(after_cursor=after_cursor,limit=limit)
        def checkpoint(self):return dict(minute=self._minute,status=self._status,bus=self._bus.checkpoint())
        def restore(self,v,*,expected_checkpoint_sha256):
            assert api._hash(v)==expected_checkpoint_sha256
            self._minute=v['minute'];self._status=v['status'];self._bus=EventBus.restore(v['bus'])
    def fresh():
        obj,g=fixture();obj._ground=g;obj._host=Host();obj._public_id='approved-crew'
        obj._initial=[250.,750.];obj._identity=dict(version=module.VERSION,implementation='test-source',water='test-water',walk_configuration=obj._configuration_sha256())
        obj._candidate={'version':'synthetic'};obj._candidate['sha256']=api._hash(obj._candidate)
        obj._candidate_sha=obj._candidate['sha256'];obj._calls=[];obj._poisoned=False
        obj._seal=api._hash(obj._host.checkpoint());obj._ground_seal=api._hash(g)
        obj._conditions=lambda minute:_eligible_fire([[2,1]],[0,0],obj._supported)
        return obj
    a=fresh()
    import pytest
    before=a.checkpoint()
    for action,args in ((a.dispatch_aircraft,('unknown',[0,0])),
                        (a.advance,(0,)),(a.advance,(True,)),
                        (a.dispatch_crew,('not-approved',)),(a.retreat,('approved-crew',))):
        with pytest.raises(ValueError):action(*args)
        assert api._encoded(a.checkpoint())==api._encoded(before)
        assert a.situation()['minute']==0
    view=a.dispatch_crew('approved-crew')
    assert view['minute']==1 and view['ground'][0]['phase']=='staged_assumed'
    assert set(view['ground'][0])=={'id','phase'} and 'no_ground_work' in view['scope']
    # A stale public staged phase can submit again without disclosing the
    # private walking phase; its rejection is delivered asynchronously.
    duplicate=a.dispatch_crew('approved-crew')
    assert duplicate['minute']==2
    assert not any(r['kind']=='synthetic_ground_command_result' and r['observed_at']==2 for r in a.reports(limit=100))
    a.advance(5)
    assert a.situation()['ground'][0]['phase']=='walking'
    inflight=a.checkpoint()
    with pytest.raises(ValueError):a.dispatch_crew('approved-crew')
    assert api._encoded(a.checkpoint())==api._encoded(inflight)
    saved=a.checkpoint();b=fresh();b.restore(saved,expected_checkpoint_sha256=api._hash(saved))
    old=copy.deepcopy(saved);old['version']='wildfire-source-walk-exercise-v1'
    old['sha256']=api._hash({k:v for k,v in old.items() if k!='sha256'})
    with pytest.raises(ValueError):fresh().restore(old,expected_checkpoint_sha256=api._hash(old))
    assert a.advance(3)==b.advance(3)
    assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
    transcript=a.transcript();replayed=fresh()
    assert replayed.replay(transcript,expected_sha256=api._hash(transcript),
        expected_final_checkpoint_sha256=api._hash(a.checkpoint()))==a.situation()
    # A spread after the movement tick invalidates the current position before
    # another command, while the public status remains delayed.
    danger=fresh()
    clear=_eligible_fire([[2,1]],[0,0],danger._supported)
    danger._conditions=lambda minute: clear if danger._host._minute==0 else ({(0,1)}, {})
    public=danger.dispatch_crew('approved-crew')
    assert danger._ground['phase']=='needs_assistance'
    assert public['ground'][0]['phase']=='staged_assumed'
    # Identical delivered knowledge produces identical immediate responses
    # despite a private hazard transition; only delayed results can differ.
    normal=fresh();hidden=fresh()
    hidden._ground['phase']='needs_assistance';hidden._ground_seal=api._hash(hidden._ground)
    hidden._host._bus.emit('operations','synthetic_ground_status',
        {'crew_id':'approved-crew','phase':'needs_assistance'},0,delay=5)
    hidden._seal=api._hash(hidden._host.checkpoint())
    assert normal.situation()==hidden.situation()
    assert normal.dispatch_crew('approved-crew')==hidden.dispatch_crew('approved-crew')
    assert normal.situation()==hidden.situation()
    assert not any(r['kind']=='synthetic_ground_command_result' for r in normal.reports(limit=100))
    assert not any(r['kind']=='synthetic_ground_command_result' for r in hidden.reports(limit=100))
    normal.advance(10);hidden.advance(10)
    def result(episode):
        return next(r['payload']['accepted'] for r in episode.reports(limit=100)
                    if r['kind']=='synthetic_ground_command_result')
    assert result(normal) is True and result(hidden) is False

    # The same privacy rule applies to a retreat request based on a delivered
    # walking observation after a hidden transition to assistance.
    normal=fresh();hidden=fresh()
    for episode,phase in ((normal,'walking'),(hidden,'needs_assistance')):
        episode._ground['phase']=phase;episode._ground_seal=api._hash(episode._ground)
        episode._host._bus.emit('operations','synthetic_ground_status',
            {'crew_id':'approved-crew','phase':'walking'},0,delay=0)
        episode._host._bus.tick(0,{})
        episode._seal=api._hash(episode._host.checkpoint())
    assert normal.situation()==hidden.situation()
    assert normal.retreat('approved-crew')==hidden.retreat('approved-crew')
    assert normal.situation()==hidden.situation()
    normal.advance(10);hidden.advance(10)
    assert result(normal) is True and result(hidden) is False

    # A report observed later wins even if an older observation arrives last.
    ordered=fresh();bus=ordered._host._bus
    bus.emit('operations','synthetic_ground_status',{'crew_id':'approved-crew','phase':'staged_assumed'},0,delay=5)
    ordered._host.advance(1)
    bus.emit('operations','synthetic_ground_status',{'crew_id':'approved-crew','phase':'walking'},1,delay=1)
    ordered._host.advance(1);ordered._seal=api._hash(ordered._host.checkpoint())
    assert ordered.situation()['ground'][0]['phase']=='walking'
    for _ in range(3):ordered._host.advance(1)
    ordered._seal=api._hash(ordered._host.checkpoint())
    assert ordered.situation()['ground'][0]['phase']=='walking'
    before=ordered.checkpoint()
    with pytest.raises(ValueError):ordered.dispatch_crew('approved-crew')
    assert api._encoded(ordered.checkpoint())==api._encoded(before)
    # Equal observation times use event ID, irrespective of delivery order.
    tied=fresh();bus=tied._host._bus
    for phase in ('walking','needs_assistance'):
        bus.emit('operations','synthetic_ground_status',{'crew_id':'approved-crew','phase':phase},0,delay=0)
    bus.tick(0,{})
    expected=max(bus.reports,key=lambda r:r['id'])['payload']['phase']
    tied._seal=api._hash(tied._host.checkpoint())
    assert tied.situation()['ground'][0]['phase']==expected
    bus.reports.reverse();tied._seal=api._hash(tied._host.checkpoint())
    assert tied.situation()['ground'][0]['phase']==expected
    # Runtime changes to the private movement state or approved route are
    # detected instead of being treated as a new authorized exercise.
    altered=fresh();altered._ground['work_remaining']-=1
    with pytest.raises(RuntimeError,match='identity or host state'):altered.situation()
    altered=fresh();altered._route[1][0]+=1
    with pytest.raises(RuntimeError,match='identity or host state'):altered.situation()


def test_other_case_hypotheses_are_rejected_before_dispatch(monkeypatch):
    import hashlib
    import pytest
    from pathlib import Path
    import wildfire_source_walk_exercise_v1 as module
    from wildfire_source_restricted_v1.host import RestrictedSourceIncidentV1
    monkeypatch.setattr(module,'_document',lambda *args:None)
    host=object.__new__(RestrictedSourceIncidentV1)
    host._ensure=lambda:None;host._minute=0;host._calls=[];host._status='active'
    parent={'cases':[{'original_case_id':str(i),'variants':[]} for i in range(12)]}
    cases=[{'original_case_id':str(i),'candidates':[]} for i in range(12)]
    cases[0]['candidates']=[{'status':'synthetic_walk_front_hypothesis'} for _ in range(2)]
    candidate={'version':module.walk.VERSION,'status':'private_synthetic_hypotheses_not_host_integrated',
        'pins':{'parent':module.walk.PARENT_PIN,
                'source':hashlib.sha256(Path(module.walk.__file__).read_bytes()).hexdigest(),
                'water_v2':module.staging.V2_PIN,'mixed_v4':module.staging.V4_PIN},'cases':cases}
    with pytest.raises(ValueError,match='single-case'):
        SourceWalkExerciseV1(host,candidate=candidate,expected_candidate_sha256='a'*64,
            parent=parent,v2={},v4={},candidate_index=0,public_crew_id='public',expected_identity={},opt_in=True)
