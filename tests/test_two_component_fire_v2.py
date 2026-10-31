"""Synthetic v2: explicit inert, per-cell assumptions, boundary and replay."""
import copy
import json
import random
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
for path in ('eval','tests','envs/wildfire_response/core/src','envs/wildfire_containment_v1/src'):
    sys.path.insert(0,str(ROOT/path))
from two_component_fire_v2 import TwoComponentFireV2
from test_two_component_fire_v1 import fire,component,params
from wildfire_containment_v1.fire import ContainmentDayFire


def empty(f):return [[{'vegetation':None,'built':None,'inert':{'fraction':1.,'assumption':'synthetic_inert'}} for _ in range(f.width)] for _ in range(f.height)]


def grids(f,transfer=None,crossing=1.):
    tr=transfer or params()
    return ([[copy.deepcopy(tr) for _ in range(f.width)] for _ in range(f.height)],
            [[crossing for _ in range(f.width)] for _ in range(f.height)])


def make(f,c,*,transfer=None,crossing=1.,max_cells=4096,max_steps=120):
    t,o=grids(f,transfer,crossing)
    return TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=max_cells,max_steps=max_steps,opt_in=True)


@pytest.mark.parametrize('kind',['vegetation','built'])
@pytest.mark.parametrize('seed',[0,37])
def test_one_component_exact_legacy_parity_and_replay(kind,seed):
    legacy=fire(seed)
    for row in legacy.cells:
        for c in row:c.update(terrain='field' if kind=='vegetation' else 'built',fuel=1.,initial_fuel=1.,heat=0.,age=0,wet=0,burned=0.)
    legacy.cells[8][8].update(heat=1.,age=1)
    other=ContainmentDayFire.restore(json.loads(json.dumps(legacy.checkpoint())))
    c=[[{'vegetation':dict(z,fraction=1.) if kind=='vegetation' else None,
         'built':dict(z,fraction=1.) if kind=='built' else None,'inert':None} for z in row] for row in other.cells]
    model=make(other,c);other=model.fire
    for minute in range(15):
        if minute==7:legacy.set_wind(x=1,y=0);other.set_wind(x=1,y=0)
        legacy.step_with_containment();assert model.step()
        assert json.dumps(legacy.checkpoint(),sort_keys=True)==json.dumps(other.checkpoint(),sort_keys=True)
    saved=json.loads(json.dumps(model.checkpoint()))
    replay=TwoComponentFireV2.restore(saved,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=saved['sha256'],opt_in=True)
    for _ in range(4):
        assert model.step() and replay.step()
        assert json.dumps(model.checkpoint(),sort_keys=True)==json.dumps(replay.checkpoint(),sort_keys=True)


def test_inert_is_explicit_and_unknown_never_becomes_sea():
    f=fire();c=empty(f);c[8][8]['inert']=None;c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.)
    c[8][9]['inert']={'fraction':1.,'assumption':'synthetic_inert'}
    model=make(f,c);before=model.fire.rng.getstate();assert model.step()
    assert model._components[8][9]['inert']['fraction']==1. and model.fire.cells[8][9]['fuel']==0
    assert model.fire.rng.getstate()==before
    c[8][9]['inert']='unknown';model=make(f,c);before=model.fire.checkpoint()
    assert not model.step() and model.reason=='unknown_component_or_inert' and model.fire.checkpoint()==before
    with pytest.raises(RuntimeError):model.step()
    c[8][9]['inert']=None;c[8][9]['vegetation']=component('vegetation',fraction=.4)
    with pytest.raises(ValueError,match='unrepresented'):make(f,c)


@pytest.mark.parametrize('what',['crossing','transfer'])
def test_unknown_assumptions_in_burning_stencil_censor(what):
    f=fire();c=empty(f);c[8][8]['inert']=None;c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.)
    t,o=grids(f)
    if what=='crossing':o[8][9]='unknown'
    else:t[8][9]='unknown'
    model=TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=4096,opt_in=True);before=model.fire.checkpoint()
    assert not model.step() and model.reason=='unknown_'+what and model.fire.checkpoint()==before


def test_per_cell_transfer_crossing_and_one_shared_containment():
    class Fixed:
        def random(self):return .15
    def make_pair(source_cross,target_cross):
        f=fire();f.spread_multiplier=1;f.rules['ignition_probability_per_eligible_cell']=2.
        c=empty(f);c[8][8]['inert']=None;c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.,age=f.spread_interval(1,0)-1)
        c[8][9]['inert']=None;c[8][9]['built']=component('built',fraction=1.)
        t,o=grids(f,params(vb=.5));o[8][8]=source_cross;o[8][9]=target_cross
        model=TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=4096,opt_in=True)
        model.fire.rng=Fixed();return model
    attenuated=make_pair(.5,.8);assert attenuated.step()
    assert attenuated._components[8][9]['built']['heat']==0
    unattenuated=make_pair(1.,1.);assert unattenuated.step()
    assert unattenuated._components[8][9]['built']['heat']>0
    blocked=make_pair(1.,1.);line=[dict(resource_id='crew',edge=[[8,8],[9,8]],kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)]
    assert blocked.step(assignments=line)
    assert blocked._components[8][9]['built']['heat']==0 and blocked.fire._containment._minute==1
    assert len(blocked.fire._last_costs)==1


def test_zero_transfer_still_draws_and_attacks_physical_edge():
    f=fire(81);f._pressure_scale=3.;f.spread_multiplier=1;f.rules['ignition_probability_per_eligible_cell']=.5
    c=empty(f);c[8][8]={'vegetation':component('vegetation',heat=1.),'built':component('built'),'inert':None}
    c[8][9]['inert']=None;c[8][9]['built']=component('built',fraction=1.);c[8][8]['vegetation']['age']=f.spread_interval(1,0)-1
    m=make(f,c,transfer=params(vb=0.));f=m.fire
    expected=random.Random();expected.setstate(f.rng.getstate());expected.random();expected.random()
    line=[dict(resource_id='crew',edge=[[8,8],[9,8]],kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)]
    assert m.step(assignments=line)
    assert f.rng.getstate()==expected.getstate() and f._containment._lines[((8,8),(9,8))]['breached']
    assert m._components[8][8]['built']['heat']==0 and m._components[8][9]['built']['heat']==0


def test_grid_can_exceed_4956_cells_and_boundary_censors():
    f=fire();f.width=84;f.height=59
    template=dict(terrain='field',fuel=0.,initial_fuel=0.,heat=0.,age=0,wet=0,burned=0.)
    f.cells=[[copy.deepcopy(template) for _ in range(f.width)] for _ in range(f.height)]
    f.heights=[[100.]*f.width for _ in range(f.height)]
    c=empty(f);t,o=grids(f)
    with pytest.raises(ValueError,match='bounds'):
        TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=4096,opt_in=True)
    m=TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=5000,max_steps=2,opt_in=True)
    assert m.step() and m.fire._containment._minute==1
    saved=json.loads(json.dumps(m.checkpoint()))
    restored=TwoComponentFireV2.restore(saved,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=saved['sha256'],opt_in=True)
    assert restored.step() and restored.fire._containment._minute==2
    c[8][0]['inert']=None;c[8][0]['vegetation']=component('vegetation',fraction=1.,heat=1.)
    boundary=TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=5000,opt_in=True)
    before=boundary.fire.checkpoint();assert not boundary.step() and boundary.reason=='grid_boundary'
    assert boundary.fire.checkpoint()==before


def test_checkpoint_pins_inert_crossings_transfers_and_rng():
    from wildfire_containment_v1.kernel import _digest
    f=fire(31);c=empty(f);c[8][8]={'vegetation':component('vegetation',heat=1.),'built':component('built'),'inert':None}
    m=make(f,c,transfer=params(vb=.35,bv=.2));assert m.step()
    saved=json.loads(json.dumps(m.checkpoint()));trusted=saved['sha256']
    replay=TwoComponentFireV2.restore(saved,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=trusted,opt_in=True)
    for _ in range(8):assert m.step()==replay.step();assert json.dumps(m.checkpoint(),sort_keys=True)==json.dumps(replay.checkpoint(),sort_keys=True)
    changed=copy.deepcopy(saved);changed['fire']['fire']['state']['rng_state'][1][0]^=1
    changed['fire']['sha256']=_digest({k:v for k,v in changed['fire'].items() if k!='sha256'})
    changed['sha256']=_digest({k:v for k,v in changed.items() if k!='sha256'})
    with pytest.raises(ValueError,match='identity'):
        TwoComponentFireV2.restore(changed,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=trusted,opt_in=True)


def test_zero_components_with_explicit_inert_matches_legacy_and_no_rng():
    f=fire();m=make(f,empty(f));legacy=ContainmentDayFire.restore(json.loads(json.dumps(m.fire.checkpoint())))
    before=m.fire.rng.getstate()
    for _ in range(3):
        legacy.step_with_containment();assert m.step()
        assert json.dumps(legacy.checkpoint(),sort_keys=True)==json.dumps(m.fire.checkpoint(),sort_keys=True)
    assert m.fire.rng.getstate()==before


def test_omitted_inert_cannot_silently_create_empty_firebreak():
    f=fire();c=empty(f);c[8][9]={'vegetation':None,'built':None,'inert':None}
    with pytest.raises(ValueError,match='unrepresented'):make(f,c)
    c[8][9]['inert']='unknown';c[8][8]={'vegetation':component('vegetation',fraction=1.,heat=1.),'built':None,'inert':None}
    model=make(f,c);assert not model.step() and model.reason=='unknown_component_or_inert'


def test_partial_inert_share_stays_distinct_and_does_not_relabel_vegetation():
    f=fire();c=empty(f)
    c[8][8]={'vegetation':component('vegetation',fraction=.7,heat=1.),'built':None,
             'inert':{'fraction':.3,'assumption':'synthetic_inert'}}
    model=make(f,c);assert model.fire.cells[8][8]['terrain'] is None
    assert model.step();saved=model.checkpoint()
    assert saved['components'][8][8]['inert']=={'fraction':.3,'assumption':'synthetic_inert'}
    assert saved['components'][8][8]['vegetation']['fraction']==.7


def test_diagonal_crossing_uses_both_endpoints_and_physical_corners():
    class Fixed:
        def random(self):return .6
    def run(corner):
        f=fire();f.spread_multiplier=1;f.rules['ignition_probability_per_eligible_cell']=100
        c=empty(f);c[8][8]={'vegetation':component('vegetation',fraction=1.,heat=1.,age=f.spread_interval(2**-.5,2**-.5)-1),'built':None,'inert':None}
        c[9][9]={'vegetation':component('vegetation',fraction=1.),'built':None,'inert':None}
        c[8][9]={'vegetation':None,'built':component('built',fraction=1.),'inert':None}
        c[9][8]={'vegetation':None,'built':component('built',fraction=1.),'inert':None}
        t,o=grids(f);o[8][8]=.9;o[8][9]=corner;o[9][8]=corner
        m=TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=4096,opt_in=True);m.fire.rng=Fixed()
        assert m.step();return m._components[9][9]['vegetation']['heat']
    assert run(.8)==0 and run(1.)>0


def test_distant_unknown_inert_assignment_rejected_without_mutation():
    f=fire();c=empty(f);c[8][8]={'vegetation':component('vegetation',fraction=1.,heat=1.),'built':None,'inert':None}
    c[20][20]['inert']='unknown';c[20][21]={'vegetation':component('vegetation',fraction=1.),'built':None,'inert':None}
    m=make(f,c);before=m.checkpoint();line=[dict(resource_id='crew',edge=[[20,20],[21,20]],kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)]
    with pytest.raises(ValueError,match='unknown or unsupported'):m.step(assignments=line)
    assert m.status=='active' and m.checkpoint()==before


def test_distinct_source_cells_use_their_own_transfer_assumptions():
    f=fire(17);f.spread_multiplier=1;f.rules['ignition_probability_per_eligible_cell']=100
    c=empty(f)
    for y in (8,10):
        c[y][8]={'vegetation':component('vegetation',fraction=1.,heat=1.,age=f.spread_interval(1,0)-1),'built':None,'inert':None}
        c[y][9]={'vegetation':None,'built':component('built',fraction=1.),'inert':None}
    t,o=grids(f,params(vb=1.));t[8][8]['vegetation>built']=0.
    m=TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=4096,opt_in=True);assert m.step()
    assert m._components[8][9]['built']['heat']==0 and m._components[10][9]['built']['heat']>0
    assert m.fire._containment._minute==1


def test_crossing_mutation_is_detected_before_fire_time_changes():
    f=fire();c=empty(f);c[8][8]={'vegetation':component('vegetation',fraction=1.,heat=1.),'built':None,'inert':None}
    m=make(f,c);before=m.fire.checkpoint();m.crossings[8][8]=.8
    with pytest.raises(RuntimeError,match='dependencies or state'):m.step()
    assert m.status=='indeterminate' and m.fire.checkpoint()==before


def test_private_only_and_requires_explicit_assumption_grids():
    f=fire();c=empty(f);t,o=grids(f);m=TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=4096,opt_in=True)
    assert not hasattr(m,'situation') and not hasattr(m,'outcomes')
    with pytest.raises(ValueError,match='opt-in'):TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=4096)
    with pytest.raises(ValueError,match='crossing'):TwoComponentFireV2(f,c,transfers=t,crossings=[],max_cells=4096,opt_in=True)


def test_replaced_v1_primitive_is_rejected_before_fire_mutation(monkeypatch):
    from eval import two_component_fire_v1 as v1
    f=fire();c=empty(f);c[8][8]={'vegetation':component('vegetation',fraction=1.,heat=1.),'built':None,'inert':None}
    m=make(f,c);before=m.fire.checkpoint();monkeypatch.setattr(v1,'_fuel_present',lambda cell:False)
    with pytest.raises(RuntimeError,match='v1 helper source differs'):m.step()
    assert m.status=='indeterminate' and m.fire.checkpoint()==before


def test_explicit_unknown_area_fraction_is_preserved_and_censored():
    f=fire();c=empty(f)
    c[8][8]={'vegetation':component('vegetation',fraction=.7,heat=1.),
             'built':component('built',fraction=.2),
             'inert':{'fraction':.1,'assumption':'unknown'}}
    m=make(f,c);before=m.fire.checkpoint()
    assert m._components[8][8]['inert']=={'fraction':.1,'assumption':'unknown'}
    assert not m.step() and m.reason=='unknown_component_or_inert' and m.fire.checkpoint()==before
    c[8][8]['inert']={'fraction':.05,'assumption':'unknown'}
    with pytest.raises(ValueError,match='unrepresented'):make(f,c)
