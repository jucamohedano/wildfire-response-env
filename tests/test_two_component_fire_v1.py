"""Synthetic-only two-component prototype parity, censor, RNG and privacy."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
for path in ('eval','envs/wildfire_response/core/src','envs/wildfire_containment_v1/src'):
    sys.path.insert(0,str(ROOT/path))
from two_component_fire_v1 import TwoComponentFireV1
from wildfire_containment_v1.fire import ContainmentDayFire
from wildfire_containment_v1.kernel import ContainmentKernel
from envs.wildfire_response.incident_day.scenarios import DayFire
from wildfire_core.incident_day import fixture_inputs


def fire(seed=4):
    original=DayFire(fixture_inputs()[0],seed);original.set_wind(x=0,y=0)
    kernel=ContainmentKernel(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    return ContainmentDayFire.from_day_fire(original,containment=kernel,pressure_scale=1)


def component(kind,*,fraction=.5,heat=0.,age=0):
    return dict(terrain='field' if kind=='vegetation' else 'built',fraction=fraction,
                fuel=1.,initial_fuel=1.,heat=heat,age=age,wet=0,burned=0.)


def empty(f):return [[{'vegetation':None,'built':None} for _ in range(f.width)] for _ in range(f.height)]


def params(vb=0.,bv=0.):return {'vegetation>vegetation':1.,'built>built':1.,'vegetation>built':vb,'built>vegetation':bv}


def prototype(f,c,**kwargs):return TwoComponentFireV1(f,c,transfer=kwargs.get('transfer',params()),opt_in=True)


def test_zero_components_do_not_ignite_or_consume_randomness():
    f=fire();before=f.rng.getstate();model=prototype(f,empty(f));f=model.fire
    legacy=ContainmentDayFire.restore(json.loads(json.dumps(f.checkpoint())))
    for _ in range(3):
        legacy.step_with_containment();assert model.step()
        assert json.dumps(legacy.checkpoint(),sort_keys=True)==json.dumps(f.checkpoint(),sort_keys=True)
    assert f._containment._minute==3 and f.rng.getstate()==before
    assert all(not any(isinstance(c[k],dict) and c[k]['heat']>0 for k in ('vegetation','built')) for row in model._components for c in row)


@pytest.mark.parametrize('seed',[0,37])
@pytest.mark.parametrize('kind',['vegetation','built'])
def test_one_component_matches_existing_fire_and_replays(seed,kind):
    legacy=fire(seed)
    for row in legacy.cells:
        for c in row:c.update(terrain='field' if kind=='vegetation' else 'built',fuel=1.,initial_fuel=1.,heat=0.,age=0,wet=0,burned=0.)
    legacy.cells[8][8].update(heat=1.,age=1)
    other=ContainmentDayFire.restore(json.loads(json.dumps(legacy.checkpoint())))
    components=[[{'vegetation':dict(c,fraction=1.) if kind=='vegetation' else None,
                  'built':dict(c,fraction=1.) if kind=='built' else None} for c in row] for row in other.cells]
    model=prototype(other,components);other=model.fire
    for minute in range(20):
        if minute==8:legacy.set_wind(x=1,y=0);other.set_wind(x=1,y=0)
        legacy.step_with_containment();assert model.step()
        assert json.dumps(legacy.checkpoint(),sort_keys=True)==json.dumps(other.checkpoint(),sort_keys=True)
        if minute==10:
            saved=json.loads(json.dumps(model.checkpoint()))
            replay=TwoComponentFireV1.restore(saved,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=saved['sha256'],opt_in=True)
            for _ in range(2):assert model.step() and replay.step();assert model.checkpoint()==replay.checkpoint()
            for _ in range(2):legacy.step_with_containment()
            assert json.dumps(legacy.checkpoint(),sort_keys=True)==json.dumps(other.checkpoint(),sort_keys=True)


def test_explicit_transfer_and_shared_physical_containment():
    f=fire();c=empty(f)
    c[8][8]={'vegetation':component('vegetation',heat=1.),'built':component('built',heat=1.)}
    c[8][9]={'vegetation':component('vegetation'),'built':component('built')}
    f.rules['ignition_probability_per_eligible_cell']=100;f.spread_multiplier=1
    for kind in ('vegetation','built'):c[8][8][kind]['age']=f.spread_interval(1,0)-1
    model=prototype(f,c,transfer=params(vb=1.,bv=1.));f=model.fire
    assignment=[dict(resource_id='crew',edge=[[8,8],[9,8]],kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)]
    assert model.step(assignments=assignment)
    assert all(model._components[8][9][k]['heat']==0 for k in ('vegetation','built'))
    assert f._containment._minute==1 and len(f._last_costs)==1 and f._assignments==[]
    # Without a line both target compartments may ignite under explicit factors.
    other=fire();other.rules['ignition_probability_per_eligible_cell']=100;other.spread_multiplier=1
    for kind in ('vegetation','built'):c[8][8][kind]['age']=other.spread_interval(1,0)-1
    m=prototype(other,c,transfer=params(vb=1.,bv=1.));assert m.step()
    assert all(m._components[8][9][k]['heat']>0 for k in ('vegetation','built'))


def test_zero_transfer_and_mixed_physical_corners_are_explicit():
    f=fire();f.rules['ignition_probability_per_eligible_cell']=100;f.spread_multiplier=1;c=empty(f)
    c[8][8]={'vegetation':component('vegetation',heat=1.),'built':component('built')}
    c[8][9]['built']=component('built',fraction=1.);c[9][8]['built']=component('built',fraction=1.)
    c[9][9]['vegetation']=component('vegetation',fraction=1.)
    c[8][8]['vegetation']['age']=f.spread_interval(2**-.5,2**-.5)-1
    model=prototype(f,c,transfer=params());f=model.fire;assert model.step()
    assert model._components[8][8]['built']['heat']==0
    assert model._components[8][9]['built']['heat']==0 and model._components[9][8]['built']['heat']==0
    assert model._components[9][9]['vegetation']['heat']>0
    assert f.cells[8][8]['terrain'] is None


def test_unknown_component_censors_without_mutation_or_fake_firebreak():
    f=fire();c=empty(f);c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.)
    c[8][9]['built']='unknown';model=prototype(f,c,transfer=params(vb=1.));f=model.fire;before=f.checkpoint()
    assert model.step() is False and model.status=='censored' and model.reason=='unknown_component'
    assert f.checkpoint()==before
    with pytest.raises(RuntimeError):model.step()
    with pytest.raises(RuntimeError):model.checkpoint()


def test_synthetic_only_explicit_configuration_and_private_state():
    f=fire();c=empty(f)
    with pytest.raises(ValueError):TwoComponentFireV1(f,c,transfer=params(),opt_in=False)
    with pytest.raises(ValueError):TwoComponentFireV1(f,c,transfer={'vegetation>built':1},opt_in=True)
    model=prototype(f,c)
    assert not hasattr(model,'situation') and not hasattr(model,'outcomes')
    saved=model.checkpoint();bad=copy.deepcopy(saved);bad['transfer']['vegetation>built']=.7
    with pytest.raises(ValueError):TwoComponentFireV1.restore(bad,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=saved['sha256'],opt_in=True)


def test_unknown_remainder_is_not_converted_to_empty_space():
    f=fire();c=empty(f);c[8][8]['vegetation']=component('vegetation',fraction=.4,heat=1.)
    with pytest.raises(ValueError,match='unrepresented'):prototype(f,c)
    c[8][8]['built']='unknown';model=prototype(f,c)
    assert not model.step() and model.reason=='unknown_component'


def test_positive_intra_cell_transfer_is_explicit_and_keeps_components_separate():
    f=fire();f.rules['ignition_probability_per_eligible_cell']=100;c=empty(f)
    c[8][8]={'vegetation':component('vegetation',heat=1.),'built':component('built')}
    model=prototype(f,c,transfer=params(vb=1.));f=model.fire;assert model.step()
    assert model._components[8][8]['built']['heat']>0
    assert model._components[8][8]['vegetation']['terrain']=='field'
    assert model._components[8][8]['built']['terrain']=='built' and f.cells[8][8]['terrain'] is None


def test_mixed_state_seeded_checkpoint_replay_and_identity():
    f=fire(31);c=empty(f)
    c[8][8]={'vegetation':component('vegetation',heat=1.),'built':component('built')}
    c[8][9]={'vegetation':component('vegetation'),'built':component('built')}
    f.rules['ignition_probability_per_eligible_cell']=.8
    model=prototype(f,c,transfer=params(vb=.35,bv=.2))
    for _ in range(4):assert model.step()
    saved=json.loads(json.dumps(model.checkpoint()))
    restored=TwoComponentFireV1.restore(saved,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=saved['sha256'],opt_in=True)
    for _ in range(20):
        assert model.step()==restored.step()
        assert json.dumps(model.checkpoint(),sort_keys=True)==json.dumps(restored.checkpoint(),sort_keys=True)
    assert model._components[8][8]['built']['heat']>0
    with pytest.raises(ValueError,match='identity'):
        TwoComponentFireV1.restore(saved,expected_input_sha256='0'*64,expected_checkpoint_sha256=saved['sha256'],opt_in=True)


def test_unknown_component_has_no_single_terrain_label():
    f=fire();c=empty(f);c[8][8]['vegetation']=component('vegetation',fraction=.5)
    c[8][8]['built']='unknown';model=prototype(f,c)
    assert model.fire.cells[8][8]['terrain'] is None


def test_component_dependency_replacement_poisoned_before_time_advances(monkeypatch):
    import two_component_fire_v1 as module
    f=fire();c=empty(f);c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.)
    model=prototype(f,c);f=model.fire;before=f.checkpoint()
    monkeypatch.setattr(module,'edge_multiplier',lambda *a,**k:1.)
    with pytest.raises(RuntimeError,match='dependencies'):model.step()
    assert model.status=='indeterminate' and f.checkpoint()==before


def test_missing_terrain_censors_before_fire_mutation():
    f=fire();c=empty(f);c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.)
    c[8][9]['vegetation']=component('vegetation',fraction=1.);f.heights[8][9]=None
    model=prototype(f,c);f=model.fire;before=f.checkpoint()
    assert not model.step() and model.reason=='unknown_terrain' and f.checkpoint()==before


def test_new_fire_near_unknown_censors_after_step_and_stays_sticky():
    f=fire();f.rules['ignition_probability_per_eligible_cell']=100;f.spread_multiplier=1;c=empty(f)
    c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.,age=f.spread_interval(1,0)-1)
    c[8][9]['vegetation']=component('vegetation',fraction=1.)
    c[8][10]['built']='unknown'
    model=prototype(f,c);f=model.fire
    assert not model.step() and model.reason=='unknown_component'
    assert f.tick==1 and model._components[8][9]['vegetation']['heat']>0
    with pytest.raises(RuntimeError):model.step()
    with pytest.raises(RuntimeError):model.checkpoint()


def test_checkpoint_rejects_changed_component_support_even_if_rehashed():
    from wildfire_containment_v1.kernel import _digest
    f=fire();c=empty(f);c[8][8]={'vegetation':component('vegetation',heat=1.),'built':component('built')}
    model=prototype(f,c,transfer=params(vb=.2,bv=.2));assert model.step()
    saved=json.loads(json.dumps(model.checkpoint()));saved['components'][8][8]['built']=None
    saved['sha256']=_digest({k:v for k,v in saved.items() if k!='sha256'})
    with pytest.raises(ValueError,match='unrepresented|component state differs'):
        TwoComponentFireV1.restore(saved,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=saved['sha256'],opt_in=True)


def test_missing_component_field_and_invalid_transfer_are_rejected():
    f=fire();c=empty(f);del c[8][8]['built']
    with pytest.raises(ValueError,match='both component states'):prototype(f,c)
    c=empty(f);bad=params();bad['vegetation>built']=float('nan')
    with pytest.raises(ValueError,match='transfer factors'):prototype(f,c,transfer=bad)


def test_explicit_step_bound_is_checkpointable():
    f=fire();model=TwoComponentFireV1(f,empty(f),transfer=params(),max_steps=2,opt_in=True)
    assert model.step() and model.step()
    before=model.checkpoint()
    with pytest.raises(RuntimeError,match='bound'):model.step()
    assert model.checkpoint()==before
    with pytest.raises(ValueError,match='opt-in'):
        TwoComponentFireV1.restore(before,expected_input_sha256=before['input_sha256'],expected_checkpoint_sha256=before['sha256'])
    with pytest.raises(ValueError,match='bounds'):
        TwoComponentFireV1(f,empty(f),transfer=params(),max_steps=241,opt_in=True)


def test_preflight_is_atomic_and_post_mutation_failure_poisoned(monkeypatch):
    import two_component_fire_v1 as module
    f=fire();c=empty(f);c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.)
    c[8][9]['vegetation']=component('vegetation',fraction=1.)
    model=prototype(f,c);f=model.fire;before=model.checkpoint()
    invalid=[dict(resource_id='crew',edge=[[8,8],[9,8]],kind='crew',ready=True,
                  work_remaining=0,water_litres=0,water_cost=0)]
    with pytest.raises(ValueError,match='Insufficient'):model.step(assignments=invalid)
    assert model.status=='active' and model.checkpoint()==before
    good=[dict(resource_id='crew',edge=[[8,8],[9,8]],kind='crew',ready=True,
               work_remaining=5,water_litres=0,water_cost=0)]
    def failure(*args,**kwargs):raise RuntimeError('injected failure')
    monkeypatch.setattr(module.seam,'apply_ignitions',failure)
    with pytest.raises(RuntimeError,match='injected'):model.step(assignments=good)
    assert model.status=='indeterminate' and f._assignments==[] and f._suppressing==[]
    with pytest.raises(RuntimeError):model.step()
    with pytest.raises(RuntimeError):model.checkpoint()


def test_exhausted_hot_component_does_not_ignite_cold_fuel_in_projection():
    f=fire();c=empty(f);vegetation=component('vegetation',heat=1.)
    vegetation.update(fuel=0.,burned=1.)
    c[8][8]={'vegetation':vegetation,'built':component('built')}
    model=prototype(f,c,transfer=params(vb=1.));f=model.fire;before=f.rng.getstate()
    assert f.cells[8][8]['terrain'] is None and f.cells[8][8]['heat']==0
    assert model.step() and model._components[8][8]['built']['heat']==0
    assert f.rng.getstate()==before


def test_prototype_owns_fire_and_does_not_mutate_legacy_input():
    original=fire();before=json.dumps(original.checkpoint(),sort_keys=True)
    c=empty(original);c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.)
    model=prototype(original,c)
    assert model.fire is not original and json.dumps(original.checkpoint(),sort_keys=True)==before
    assert model.step() and json.dumps(original.checkpoint(),sort_keys=True)==before


def test_zero_transfer_preserves_candidates_rng_and_physical_edge_pressure():
    import random
    f=fire(81);f._pressure_scale=3.;f.spread_multiplier=1
    f.rules['ignition_probability_per_eligible_cell']=.5;c=empty(f)
    c[8][8]={'vegetation':component('vegetation',heat=1.),'built':component('built')}
    c[8][9]['built']=component('built',fraction=1.)
    c[8][8]['vegetation']['age']=f.spread_interval(1,0)-1
    model=prototype(f,c,transfer=params(vb=0.));f=model.fire
    expected=random.Random();expected.setstate(f.rng.getstate());expected.random();expected.random()
    assignment=[dict(resource_id='crew',edge=[[8,8],[9,8]],kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)]
    assert model.step(assignments=assignment)
    assert model._components[8][8]['built']['heat']==0 and model._components[8][9]['built']['heat']==0
    assert f.rng.getstate()==expected.getstate()
    assert f._containment._lines[((8,8),(9,8))]['breached'] is True


@pytest.mark.parametrize('unsupported',['unknown','empty','terrain'])
def test_distant_unsupported_assignment_fails_before_time_or_state_changes(unsupported):
    f=fire();c=empty(f);c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.)
    c[20][21]['vegetation']=component('vegetation',fraction=1.)
    if unsupported=='unknown':
        c[20][20]['vegetation']=component('vegetation',fraction=.5);c[20][20]['built']='unknown'
    elif unsupported=='terrain':
        c[20][20]['vegetation']=component('vegetation',fraction=1.);f.heights[20][20]=None
    model=prototype(f,c);before=model.checkpoint()
    line=[dict(resource_id='crew',edge=[[20,20],[21,20]],kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)]
    with pytest.raises(ValueError,match='unknown or unsupported'):model.step(assignments=line)
    assert model.status=='active' and model.checkpoint()==before


def test_rehashed_rng_tamper_rejected_by_independent_checkpoint_pin():
    from wildfire_containment_v1.kernel import _digest
    f=fire(19);c=empty(f);c[8][8]['vegetation']=component('vegetation',fraction=1.,heat=1.)
    model=prototype(f,c);assert model.step();saved=json.loads(json.dumps(model.checkpoint()))
    trusted=saved['sha256'];tampered=copy.deepcopy(saved)
    state=tampered['fire']['fire']['state']['rng_state'];state[1][0]^=1
    fire_checkpoint=tampered['fire'];fire_checkpoint['sha256']=_digest({k:v for k,v in fire_checkpoint.items() if k!='sha256'})
    tampered['sha256']=_digest({k:v for k,v in tampered.items() if k!='sha256'})
    assert tampered['sha256']!=trusted
    with pytest.raises(ValueError,match='identity'):
        TwoComponentFireV1.restore(tampered,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=trusted,opt_in=True)
    restored=TwoComponentFireV1.restore(saved,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=trusted,opt_in=True)
    assert restored.checkpoint()['sha256']==trusted
