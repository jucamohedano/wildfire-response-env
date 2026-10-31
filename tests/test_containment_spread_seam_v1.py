"""Single-compartment parity, containment aggregation, censor and replay."""
import copy
import json
import random
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
for path in ('eval','envs/wildfire_response/core/src','envs/wildfire_containment_v1/src'):
    sys.path.insert(0,str(ROOT/path))
from containment_spread_seam_v1 import SpreadSeam,Candidate,CandidateBatch,resolve_containment,apply_ignitions
from wildfire_containment_v1.fire import ContainmentDayFire
from wildfire_containment_v1.kernel import ContainmentKernel
from envs.wildfire_response.incident_day.scenarios import DayFire
from wildfire_core.incident_day import fixture_inputs


def make_fire(seed):
    original=DayFire(fixture_inputs()[0],seed);original.set_wind(x=1,y=0);original.add_fire(8,8)
    kernel=ContainmentKernel(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    return ContainmentDayFire.from_day_fire(original,containment=kernel,pressure_scale=1)


@pytest.mark.parametrize('seed',[0,37,802])
def test_single_compartment_matches_legacy_and_seeded_checkpoint(seed):
    legacy=make_fire(seed);seamed=ContainmentDayFire.restore(json.loads(json.dumps(legacy.checkpoint())))
    seam=SpreadSeam(seamed,support_guard=lambda f:True,guard_id='synthetic-all-supported')
    for minute in range(35):
        if minute==12:
            legacy.set_wind(x=-1,y=2);seamed.set_wind(x=-1,y=2)
        assignment=[] if minute!=3 else [dict(resource_id='crew',edge=[[8,8],[9,8]],kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)]
        legacy.step_with_containment(assignments=assignment)
        assert seam.step(assignments=assignment)
        assert seamed._assignments==[] and seamed._suppressing==[]
        assert json.dumps(legacy.checkpoint(),sort_keys=True)==json.dumps(seamed.checkpoint(),sort_keys=True)
        if minute==15:
            saved=json.loads(json.dumps(seam.checkpoint()))
            resumed=SpreadSeam.restore(saved,fire_restore=ContainmentDayFire.restore,support_guard=lambda f:True,expected_guard_id='synthetic-all-supported');restored=resumed.fire
            with pytest.raises(ValueError):SpreadSeam.restore(saved,fire_restore=ContainmentDayFire.restore,support_guard=lambda f:True,expected_guard_id='different')
            for _ in range(3):
                assert seam.step() and resumed.step()
                assert json.dumps(seamed.checkpoint(),sort_keys=True)==json.dumps(restored.checkpoint(),sort_keys=True)
            # Advance legacy by the same three minutes before comparing again.
            for _ in range(3):legacy.step_with_containment()
            assert json.dumps(legacy.checkpoint(),sort_keys=True)==json.dumps(seamed.checkpoint(),sort_keys=True)


def test_batches_share_one_tick_and_blocked_destinations_consume_draws():
    class Kernel:
        _minute=0
        def tick(self,minute,**kwargs):
            assert minute==1;self._minute=minute;self.calls=kwargs
            return {'costs':[],'crossings':[{'source':[0,0],'target':[1,0],'blocked':True}]}
    class Fire:
        _containment=Kernel();_assignments=[];_suppressing=[];wind=(0,0);rng=random.Random(2);width=3;height=3
    f=Fire();edge=((0,0),(1,0));path=((edge,),)
    b1=CandidateBatch((Candidate((1,0,'built'),1.,path),),(((0,0),(1,0),.2),))
    b2=CandidateBatch((Candidate((1,0,'vegetation'),1.,path),),(((0,0),(1,0),.8),))
    blocked=resolve_containment(f,(b1,b2));assert f._containment._minute==1
    assert f._containment.calls['attacks']==[{'source':(0,0),'target':(1,0),'pressure':.8}]
    expected=random.Random(2);expected.random();expected.random();ignited=[]
    apply_ignitions(f,(b1,b2),blocked,ignited.append)
    assert ignited==[] and f.rng.getstate()==expected.getstate()


def test_guard_censors_before_and_after_and_never_turns_unknown_into_firebreak():
    fire=make_fire(4);before=fire.checkpoint();seam=SpreadSeam(fire,support_guard=lambda f:False,guard_id='synthetic')
    assert seam.step() is False and seam.status=='censored' and fire.checkpoint()==before
    with pytest.raises(RuntimeError):seam.step()
    with pytest.raises(RuntimeError):seam.checkpoint()
    fire=make_fire(4);calls=[]
    def guard(f):calls.append(f.tick);return len(calls)==1
    seam=SpreadSeam(fire,support_guard=guard,guard_id='synthetic')
    assert not seam.step() and seam.status=='censored' and calls==[0,1]
    with pytest.raises(RuntimeError):seam.step()


def test_mixed_water_crossing_factor_matches_existing_step():
    sys.path.insert(0,str(ROOT/'envs'))
    sys.path.insert(0,str(ROOT/'envs/wildfire_extended_inputs_v1'))
    from wildfire_expanded_incident_v1.mixed_v2 import _MixedFire
    legacy=make_fire(91);candidate=make_fire(91);openness={(8,8):.8,(9,8):.9,(9,9):.7}
    legacy._mixed_openness=copy.deepcopy(openness)
    seam=SpreadSeam(candidate,support_guard=lambda f:True,guard_id='synthetic',crossing_openness=openness)
    for minute in range(22):
        if minute==7:legacy.set_wind(x=-1,y=2);candidate.set_wind(x=-1,y=2)
        _MixedFire.step(legacy,1);assert seam.step()
        assert legacy.cells==candidate.cells
        assert legacy.rng.getstate()==candidate.rng.getstate()
        assert legacy._containment.checkpoint()==candidate._containment.checkpoint()
        assert (legacy.tick,legacy.boundary_contact_at,legacy.satellite,legacy.satellite_queue)==(candidate.tick,candidate.boundary_contact_at,candidate.satellite,candidate.satellite_queue)


def test_invalid_batch_rejected_before_containment_tick():
    fire=make_fire(2);before=fire._containment.checkpoint()
    edge=((8,8),(9,8));batch=CandidateBatch((Candidate((9,8,''),.5,((edge,),)),),())
    with pytest.raises(ValueError,match='missing a physical-edge attack'):
        resolve_containment(fire,(batch,))
    assert fire._containment.checkpoint()==before
    with pytest.raises(ValueError):SpreadSeam(fire,support_guard=lambda f:True,guard_id='x',crossing_openness={(8,8):0})
    # Only literal True authorizes a fire step; truthy values cannot open a gap.
    before=fire.checkpoint();seam=SpreadSeam(fire,support_guard=lambda f:1,guard_id='x')
    assert seam.step() is False and fire.checkpoint()==before


def test_checkpoint_rejects_tamper_and_unsupported_restore():
    seam=SpreadSeam(make_fire(3),support_guard=lambda f:True,guard_id='fixture',crossing_openness={(8,8):.8})
    saved=json.loads(json.dumps(seam.checkpoint()))
    changed=copy.deepcopy(saved);changed['guard_id']='other'
    with pytest.raises(ValueError):SpreadSeam.restore(changed,fire_restore=ContainmentDayFire.restore,support_guard=lambda f:True,expected_guard_id='fixture')
    with pytest.raises(ValueError,match='unsupported'):
        SpreadSeam.restore(saved,fire_restore=ContainmentDayFire.restore,support_guard=lambda f:False,expected_guard_id='fixture')


def test_actual_unknown_stencil_is_censored_after_new_ignition():
    fire=make_fire(9)
    for row in fire.cells:
        for cell in row:cell.update(fuel=0.,heat=0.,age=0,wet=0)
    source=fire.cells[8][8];target=fire.cells[8][9]
    source.update(fuel=1.,heat=1.,age=0,terrain='field')
    target.update(fuel=1.,heat=0.,age=0,terrain='field')
    fire.rules['ignition_probability_per_eligible_cell']=100
    source['age']=fire.spread_interval(1,0)-1
    unknown=(10,8)
    def whole_stencil_supported(f):
        for y,row in enumerate(f.cells):
            for x,cell in enumerate(row):
                if f.burning(cell):
                    for dx in (-1,0,1):
                        for dy in (-1,0,1):
                            if (x+dx,y+dy)==unknown:return False
        return True
    seam=SpreadSeam(fire,support_guard=whole_stencil_supported,guard_id='synthetic-unknown-stencil-v1')
    assert whole_stencil_supported(fire)
    assert seam.step() is False and seam.status=='censored'
    assert target['heat']>0 and fire.tick==1
    with pytest.raises(RuntimeError):seam.step()


def test_blocked_diagonal_preserves_rng_and_legacy_state():
    legacy=make_fire(14);seamed=ContainmentDayFire.restore(json.loads(json.dumps(legacy.checkpoint())))
    for fire in (legacy,seamed):
        fire.set_wind(x=0,y=0);fire.spread_multiplier=1
        fire.rules['spread_factor']=4;fire.rules['ignition_probability_per_eligible_cell']=100
        for row in fire.cells:
            for cell in row:cell.update(fuel=0.,heat=0.,age=0,wet=0)
        for x,y in ((8,8),(9,8),(8,9),(9,9)):fire.cells[y][x].update(fuel=1.,terrain='field')
        fire.cells[8][8].update(heat=1.,age=fire.spread_interval(2**-.5,2**-.5)-1)
    assignments=[dict(resource_id=name,edge=edge,kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)
                 for name,edge in [('a',[[8,8],[9,8]]),('b',[[8,8],[8,9]])]]
    initial_rng=legacy.rng.getstate();legacy.step_with_containment(assignments=assignments)
    seam=SpreadSeam(seamed,support_guard=lambda f:True,guard_id='synthetic')
    assert seam.step(assignments=assignments)
    assert seamed._assignments==[] and seamed._suppressing==[]
    assert legacy.cells[9][9]['heat']==0 and legacy.rng.getstate()!=initial_rng
    assert json.dumps(legacy.checkpoint(),sort_keys=True)==json.dumps(seamed.checkpoint(),sort_keys=True)


def test_dependency_identity_rejects_tamper_and_live_mutation(monkeypatch):
    from simulator.terrain import PROFILES
    from wildfire_containment_v1.kernel import _digest
    seam=SpreadSeam(make_fire(5),support_guard=lambda f:True,guard_id='fixture')
    saved=json.loads(json.dumps(seam.checkpoint()));saved['dependencies']['files']['terrain']='0'*64
    saved['sha256']=_digest({k:v for k,v in saved.items() if k!='sha256'})
    with pytest.raises(ValueError,match='identity'):
        SpreadSeam.restore(saved,fire_restore=ContainmentDayFire.restore,support_guard=lambda f:True,expected_guard_id='fixture')
    monkeypatch.setitem(PROFILES['field'],'spread',PROFILES['field']['spread']+.01)
    with pytest.raises(RuntimeError,match='dependencies changed'):seam.step()
    assert seam.status=='indeterminate'


def test_distinct_component_candidates_share_edge_decision_but_use_separate_draws():
    class Kernel:
        _minute=0
        def tick(self,minute,**kwargs):
            self._minute=minute;self.attacks=kwargs['attacks']
            return {'costs':[],'crossings':[{'source':[0,0],'target':[1,0],'blocked':False}]}
    class Draws:
        def __init__(self):self.values=iter([.2,.9]);self.count=0
        def random(self):self.count+=1;return next(self.values)
    class Fire:
        width=3;height=3;_containment=Kernel();_assignments=[];_suppressing=[];wind=(0,0);rng=Draws()
    fire=Fire();edge=((0,0),(1,0));paths=((edge,),)
    # Two sources for one compartment collapse to one draw; the other has its own draw.
    b1=CandidateBatch((Candidate((1,0,'built'),.5,paths),Candidate((1,0,'built'),.3,paths)),(((0,0),(1,0),.4),))
    b2=CandidateBatch((Candidate((1,0,'vegetation'),.8,paths),),(((0,0),(1,0),.7),))
    blocked=resolve_containment(fire,(b1,b2));ignited=[];apply_ignitions(fire,(b1,b2),blocked,ignited.append)
    assert fire._containment._minute==1 and fire._containment.attacks[0]['pressure']==.7
    assert fire.rng.count==2 and ignited==[(1,0,'built')]


def test_invalid_assignment_rejected_before_fire_or_rng_mutation():
    fire=make_fire(8);before=fire.checkpoint()
    invalid=[dict(resource_id='crew',edge=[[8,8],[9,8]],kind='crew',ready=True,
                  work_remaining=0,water_litres=0,water_cost=0)]
    seam=SpreadSeam(fire,support_guard=lambda f:True,guard_id='synthetic')
    with pytest.raises(ValueError,match='Insufficient'):seam.step(assignments=invalid)
    assert fire.checkpoint()==before and seam.status=='active'
    assert fire._assignments==[] and fire._suppressing==[]
    assert seam.step() and seam.checkpoint()['fire']==fire.checkpoint()


def make_estimated_backend(seed=12):
    import adapter_v1 as api
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_expanded_incident_v1.estimated_fuel_v1 import EstimatedInputsV1,EstimatedMixedFireV1,_VERIFIED_ESTIMATE
    n=7;grid=dict(width=n,height=n,crs='EPSG:25830',transform=[500,0,0,0,-500,3500]);pins={k:'a'*64 for k in ('surface','fuel','elevation','access')}
    doc=dict(version=api._contract_module.VERSION,grid=grid,domain_mask=[[True]*n for _ in range(n)],
      surface=[['land']*n for _ in range(n)],fuel=[[4]*n for _ in range(n)],elevation=[[100.]*n for _ in range(n)],
      access=[['unknown']*n for _ in range(n)],source_pins=pins)
    doc['surface'][3][3]='mixed';doc['surface'][3][4]='unknown';doc['fuel'][3][4]=None
    doc['surface'][1][1]='sea';doc['fuel'][1][1]=doc['elevation'][1][1]=None
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=pins,opt_in=True)
    mixed=MixedInputsV2(contract,((3,3,.9,.8),),tuple(sorted({'version':'synthetic'}.items())),_VERIFIED)
    source=tuple(tuple(doc['fuel'][y][x] is not None for x in range(n)) for y in range(n))
    mask=tuple(tuple((x,y)==(4,3) for x in range(n)) for y in range(n))
    inp=EstimatedInputsV1(mixed,((4,3,4,.9,.9),),source,mask,tuple(sorted({'version':'synthetic-estimate'}.items())),_VERIFIED_ESTIMATE)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    return EstimatedMixedFireV1(inp,seed=seed,containment_config=config,pressure_scale=1,wind=[1,0],opt_in_estimated_fuel=True)


def test_actual_mixed_backend_auto_binds_factors_and_validated_snapshot_replays():
    import adapter_v1 as api
    legacy=make_estimated_backend();backend=make_estimated_backend()
    assert legacy.change({'ignite':[[3,3]]}) and backend.change({'ignite':[[3,3]]})
    # A raw _MixedFire cannot accidentally use a non-JSON generic checkpoint.
    with pytest.raises(TypeError,match='validated mixed backend'):
        SpreadSeam(backend._fire,support_guard=lambda f:True,guard_id='synthetic')
    with pytest.raises(ValueError,match='crossing factors differ'):
        SpreadSeam(backend._fire,support_guard=lambda f:True,guard_id='synthetic',mixed_backend=backend,crossing_openness={(3,3):1.})
    seam=SpreadSeam(backend._fire,support_guard=lambda f:True,guard_id='synthetic',mixed_backend=backend)
    assert seam.crossing_openness==backend._fire._mixed_openness and seam.crossing_openness[(3,3)]==.8
    for _ in range(5):
        assert legacy.step()==seam.step()
        assert api._encoded(legacy.snapshot())==api._encoded(backend.snapshot())
    saved=json.loads(json.dumps(seam.checkpoint()))
    assert saved['snapshot_kind']=='validated-mixed-backend-v1'
    with pytest.raises(ValueError,match='mixed backend factory'):
        SpreadSeam.restore(saved,fire_restore=ContainmentDayFire.restore,support_guard=lambda f:True,expected_guard_id='synthetic')
    restored=SpreadSeam.restore(saved,mixed_backend_factory=make_estimated_backend,support_guard=lambda f:True,expected_guard_id='synthetic')
    for _ in range(8):
        assert seam.step()==restored.step()
        assert api._encoded(backend.snapshot())==api._encoded(restored._mixed_backend.snapshot())
    backend._fire._mixed_openness[(3,3)]=1.
    with pytest.raises(RuntimeError,match='crossing factors changed'):seam.step()
    assert seam.status=='indeterminate'


def test_assignments_clear_on_success_and_step_failure(monkeypatch):
    import containment_spread_seam_v1 as module
    legacy=make_fire(21);candidate=ContainmentDayFire.restore(json.loads(json.dumps(legacy.checkpoint())))
    seam=SpreadSeam(candidate,support_guard=lambda f:True,guard_id='synthetic')
    assignment=[dict(resource_id='crew',edge=[[8,8],[9,8]],kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)]
    legacy.step_with_containment(assignments=assignment);assert seam.step(assignments=assignment)
    assert candidate._assignments==[] and candidate._suppressing==[]
    assert seam.checkpoint()['fire']==candidate.checkpoint()
    legacy.step_with_containment();assert seam.step()
    assert json.dumps(legacy.checkpoint(),sort_keys=True)==json.dumps(candidate.checkpoint(),sort_keys=True)
    def fail(*args,**kwargs):raise RuntimeError('injected post-preflight failure')
    monkeypatch.setattr(module,'apply_ignitions',fail)
    with pytest.raises(RuntimeError,match='injected'):seam.step(assignments=assignment)
    assert candidate._assignments==[] and candidate._suppressing==[] and seam.status=='indeterminate'


def test_mixed_backend_crossing_factor_changes_actual_ignition_probability():
    from simulator.terrain import PROFILES
    backend=make_estimated_backend(seed=13)
    assert backend.change({'ignite':[[2,3]]})
    fire=backend._fire;fire.cells[3][2].update(heat=1.,age=119)
    fire.spread_interval=lambda dx,dy:1
    # This draw would ignite the scrub target without its 0.8 water proxy,
    # but must fail once the derived target factor is applied.
    class Fixed:
        def random(self):return .9*.5*PROFILES['scrub']['spread']
    fire.rng=Fixed()
    seam=SpreadSeam(fire,support_guard=lambda f:True,guard_id='synthetic',mixed_backend=backend)
    assert seam.step() and fire.cells[3][3]['heat']==0


def test_mutated_mixed_crossing_map_is_rejected_before_binding():
    backend=make_estimated_backend()
    backend._fire._mixed_openness[(3,3)]=1.
    with pytest.raises(ValueError,match='backend inputs'):
        SpreadSeam(backend._fire,support_guard=lambda f:True,guard_id='synthetic',mixed_backend=backend)


def test_explicit_crossing_factors_cannot_change_after_binding():
    seam=SpreadSeam(make_fire(29),support_guard=lambda f:True,guard_id='synthetic',crossing_openness={(8,8):.8})
    seam.crossing_openness[(8,8)]=1.
    with pytest.raises(RuntimeError,match='crossing factors changed'):seam.step()
    assert seam.status=='indeterminate'
