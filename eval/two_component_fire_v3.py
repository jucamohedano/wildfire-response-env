"""Long-horizon version of the synthetic two-component v2 model.

The v2 candidate, crossing, censor and containment implementation is reused
unchanged. This version changes only the independently pinned horizon contract
and checkpoints; it is not calibrated and does not authorize a long run.
"""
import copy
import hashlib
import math
from pathlib import Path

from eval import two_component_fire_v2 as v2
from eval import containment_spread_seam_v1 as seam
from wildfire_containment_v1.fire import ContainmentDayFire
from wildfire_containment_v1.kernel import _digest

VERSION='wildfire-synthetic-two-component-fire-v3'
V2_SOURCE_PIN='fd0ca091ccc6f50d0f122b4305ab2d0effa69d61196a714f05cf76032e7027b3'
_V2_FUNCTIONS={name:getattr(v2,name) for name in ('_v1_source','_validate_cells','_validate_assumptions','_project','_unknown')}
_V2_METHODS={name:getattr(v2.TwoComponentFireV2,name) for name in ('_guard','_candidates','_validate_assignment_support','_sync_projection','step')}


def _v2_source():
    actual=hashlib.sha256(Path(v2.__file__).read_bytes()).hexdigest()
    if (actual!=V2_SOURCE_PIN or any(getattr(v2,name) is not value for name,value in _V2_FUNCTIONS.items())
            or any(getattr(v2.TwoComponentFireV2,name) is not value for name,value in _V2_METHODS.items())):
        raise RuntimeError('v2 component model differs')
    v2._v1_source()
    return actual


class TwoComponentFireV3(v2.TwoComponentFireV2):
    """Explicit warmup/day horizon, with the same v2 fire and censor semantics."""
    def __init__(self,fire,components,*,transfers,crossings,max_cells,warmup_steps,day_horizon,opt_in=False):
        if (type(warmup_steps) is not int or not 0<=warmup_steps<=1440 or
                type(day_horizon) is not int or not 1<=day_horizon<=1440):
            raise ValueError('explicit warmup and day horizon out of bounds')
        _v2_source();self.warmup_steps=warmup_steps;self.day_horizon=day_horizon
        total=warmup_steps+day_horizon
        super().__init__(fire,components,transfers=transfers,crossings=crossings,max_cells=max_cells,
                         max_steps=min(total,240),opt_in=opt_in)
        self.max_steps=total;self._v2_sha256=_v2_source()
        self._source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self._input_sha256=self._input_digest()

    def _input_digest(self):
        return _digest({'components':self._initial,'transfers':self.transfers,'crossings':self.crossings,
            'fire_initial_sha256':self._fire_initial_sha256,'max_steps':self.max_steps,'max_cells':self.max_cells,
            'model_start_tick':self._model_start_tick,'warmup_steps':self.warmup_steps,'day_horizon':self.day_horizon})

    def _ensure(self):
        try:
            if (self.status not in ('active','censored') or _v2_source()!=self._v2_sha256
                    or seam.dependency_identity()!=self._dependencies
                    or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=self._source_sha256
                    or self._input_digest()!=self._input_sha256
                    or self.max_steps!=self.warmup_steps+self.day_horizon
                    or self.fire.tick-self.fire._start_tick!=self.fire._containment._minute
                    or not 0<=self.fire.tick-self._model_start_tick<=self.max_steps
                    or self.fire.cells!=[[v2._project(c) for c in row] for row in self._components]):
                raise RuntimeError('prototype dependencies, horizon or state changed')
            v2.v1._validate_fire_parameters(self.fire);v2._validate_cells(self._components,self.fire.width,self.fire.height)
            v2._validate_assumptions(self.transfers,self.crossings,self.fire.width,self.fire.height)
        except BaseException:self.status='indeterminate';raise

    def checkpoint(self):
        value=super().checkpoint()
        value.update(version=VERSION,v2_sha256=self._v2_sha256,warmup_steps=self.warmup_steps,day_horizon=self.day_horizon)
        value['sha256']=_digest({k:v for k,v in value.items() if k!='sha256'})
        return value

    @classmethod
    def restore(cls,value,*,expected_input_sha256,expected_checkpoint_sha256,opt_in=False):
        if opt_in is not True:raise ValueError('explicit synthetic prototype opt-in required')
        if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)):
            raise ValueError('independently trusted checkpoint digest required')
        v=copy.deepcopy(value);keys={'version','source_sha256','v1_sha256','v2_sha256','dependencies','input_sha256',
            'fire_initial_sha256','max_steps','max_cells','model_start_tick','warmup_steps','day_horizon',
            'initial','transfers','crossings','components','fire','sha256'}
        if (type(v) is not dict or set(v)!=keys or v['version']!=VERSION or v['sha256']!=expected_checkpoint_sha256
                or v['sha256']!=_digest({k:x for k,x in v.items() if k!='sha256'})
                or v['source_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                or v['v2_sha256']!=_v2_source() or v['v1_sha256']!=v2._v1_source()
                or v['dependencies']!=seam.dependency_identity() or v['input_sha256']!=expected_input_sha256):
            raise ValueError('prototype checkpoint identity differs')
        fire=ContainmentDayFire.restore(v['fire']);v2.v1._validate_fire_parameters(fire)
        if (type(v['max_cells']) is not int or not 1<=v['max_cells']<=16384
                or type(v['warmup_steps']) is not int or not 0<=v['warmup_steps']<=1440
                or type(v['day_horizon']) is not int or not 1<=v['day_horizon']<=1440
                or v['max_steps']!=v['warmup_steps']+v['day_horizon']
                or type(v['model_start_tick']) is not int or not 0<=fire.tick-v['model_start_tick']<=v['max_steps']
                or not 1<=fire.width<=256 or not 1<=fire.height<=256 or fire.width*fire.height>v['max_cells']):
            raise ValueError('prototype checkpoint bounds differ')
        v2._validate_cells(v['initial'],fire.width,fire.height);v2._validate_cells(v['components'],fire.width,fire.height)
        v2._validate_assumptions(v['transfers'],v['crossings'],fire.width,fire.height)
        for y,row in enumerate(v['components']):
            for x,cell in enumerate(row):
                if cell['inert']!=v['initial'][y][x]['inert']:raise ValueError('inert support changed')
                for kind in v2.KINDS:
                    initial=v['initial'][y][x][kind];current=cell[kind]
                    if type(initial) is not dict:
                        if current!=initial:raise ValueError('component support changed')
                    elif (type(current) is not dict or any(current[k]!=initial[k] for k in ('terrain','fraction','initial_fuel'))
                          or current['fuel']>initial['fuel'] or current['burned']<initial['burned']
                          or not math.isclose(current['fuel']+current['burned'],initial['fuel']+initial['burned'],abs_tol=1e-9,rel_tol=1e-9)):
                        raise ValueError('component state differs from initial assumptions')
        instance=cls.__new__(cls);instance.fire=fire;instance._initial=v['initial'];instance._components=v['components']
        instance.transfers=v['transfers'];instance.crossings=v['crossings'];instance.max_steps=v['max_steps'];instance.max_cells=v['max_cells']
        instance.warmup_steps=v['warmup_steps'];instance.day_horizon=v['day_horizon'];instance._v2_sha256=v['v2_sha256']
        instance._model_start_tick=v['model_start_tick'];instance._dependencies=v['dependencies'];instance._source_sha256=v['source_sha256']
        instance._v1_sha256=v['v1_sha256'];instance._fire_initial_sha256=v['fire_initial_sha256'];instance._input_sha256=v['input_sha256']
        instance.status='active';instance.reason=None;instance._in_step=False
        if instance._input_digest()!=instance._input_sha256:raise ValueError('prototype input identity differs')
        if [[v2._project(c) for c in row] for row in instance._components]!=fire.cells:raise ValueError('physical projection differs')
        if instance._guard() is not None:raise ValueError('restored prototype is unsupported')
        if _digest(instance.checkpoint())!=_digest(v):raise ValueError('prototype checkpoint restoration differs')
        return instance
