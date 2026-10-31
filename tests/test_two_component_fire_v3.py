"""Long-horizon model preserves v2 physics and bounded checkpoint identity."""
import copy
import json
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
for path in ('eval','tests','envs/wildfire_response/core/src','envs/wildfire_containment_v1/src'):
    sys.path.insert(0,str(ROOT/path))
from two_component_fire_v3 import TwoComponentFireV3
from two_component_fire_v2 import TwoComponentFireV2
from test_two_component_fire_v2 import empty,grids
from test_two_component_fire_v1 import fire,component


def test_explicit_full_horizon_and_short_exhaustion():
    f=fire();c=empty(f);t,o=grids(f)
    long=TwoComponentFireV3(f,c,transfers=t,crossings=o,max_cells=4096,warmup_steps=60,day_horizon=1440,opt_in=True)
    assert long.max_steps==1500
    saved=json.loads(json.dumps(long.checkpoint()))
    restored=TwoComponentFireV3.restore(saved,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=saved['sha256'],opt_in=True)
    assert restored.max_steps==1500 and restored.warmup_steps==60 and restored.day_horizon==1440
    short=TwoComponentFireV3(f,c,transfers=t,crossings=o,max_cells=4096,warmup_steps=2,day_horizon=3,opt_in=True)
    for _ in range(5):assert short.step()
    before=short.checkpoint()
    with pytest.raises(RuntimeError,match='bound'):short.step()
    assert short.checkpoint()==before
    with pytest.raises(ValueError):TwoComponentFireV3(f,c,transfers=t,crossings=o,max_cells=4096,warmup_steps=0,day_horizon=1441,opt_in=True)
    with pytest.raises(ValueError):TwoComponentFireV3(f,c,transfers=t,crossings=o,max_cells=4096,warmup_steps=-1,day_horizon=4,opt_in=True)


def test_same_total_different_phase_horizons_have_distinct_identity():
    f=fire();c=empty(f);t,o=grids(f)
    a=TwoComponentFireV3(f,c,transfers=t,crossings=o,max_cells=4096,warmup_steps=1,day_horizon=4,opt_in=True)
    b=TwoComponentFireV3(f,c,transfers=t,crossings=o,max_cells=4096,warmup_steps=2,day_horizon=3,opt_in=True)
    assert a.max_steps==b.max_steps and a._input_sha256!=b._input_sha256
    saved=a.checkpoint()
    with pytest.raises(ValueError):TwoComponentFireV3.restore(saved,expected_input_sha256=b._input_sha256,expected_checkpoint_sha256=saved['sha256'],opt_in=True)


def test_v3_matches_v2_physics_with_identical_parameters():
    f=fire(12);c=empty(f);c[8][8]={'vegetation':component('vegetation',fraction=1.,heat=1.),'built':None,'inert':None}
    t,o=grids(f);a=TwoComponentFireV2(f,c,transfers=t,crossings=o,max_cells=4096,max_steps=20,opt_in=True)
    b=TwoComponentFireV3(f,c,transfers=t,crossings=o,max_cells=4096,warmup_steps=2,day_horizon=18,opt_in=True)
    for _ in range(8):
        assert a.step()==b.step()
        assert json.dumps(a.fire.checkpoint(),sort_keys=True)==json.dumps(b.fire.checkpoint(),sort_keys=True)
        assert a._components==b._components


def test_full_grid_boundary_guard_and_checkpoint():
    f=fire();f.width=84;f.height=59
    template=dict(terrain='field',fuel=0.,initial_fuel=0.,heat=0.,age=0,wet=0,burned=0.)
    f.cells=[[copy.deepcopy(template) for _ in range(f.width)] for _ in range(f.height)]
    f.heights=[[100.]*f.width for _ in range(f.height)];c=empty(f);t,o=grids(f)
    a=TwoComponentFireV3(f,c,transfers=t,crossings=o,max_cells=5000,warmup_steps=60,day_horizon=1440,opt_in=True)
    assert a.step();saved=json.loads(json.dumps(a.checkpoint()))
    b=TwoComponentFireV3.restore(saved,expected_input_sha256=saved['input_sha256'],expected_checkpoint_sha256=saved['sha256'],opt_in=True)
    assert a.step() and b.step() and a.checkpoint()['sha256']==b.checkpoint()['sha256']
    c[8][0]={'vegetation':component('vegetation',fraction=1.,heat=1.),'built':None,'inert':None}
    boundary=TwoComponentFireV3(f,c,transfers=t,crossings=o,max_cells=5000,warmup_steps=60,day_horizon=1440,opt_in=True)
    before=boundary.fire.checkpoint();assert not boundary.step() and boundary.reason=='grid_boundary'
    assert boundary.fire.checkpoint()==before
