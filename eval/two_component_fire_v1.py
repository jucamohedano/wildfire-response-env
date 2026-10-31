"""Synthetic two-compartment fire prototype using the shared spread seam.

Trusted evaluator-side fixtures only. Vegetation and built compartments each
own fuel, heat, age and wetness. Transfer coefficients are explicit synthetic
inputs; fractions are bookkeeping and are not measured burnability. None means
an explicitly known-absent compartment, while 'unknown' must be used for any
unrepresented component. Two absent compartments explicitly mean known empty,
not missing data. This is not an urban model, operational host, or physically
calibrated simulator.
"""
import copy
import hashlib
import math
from pathlib import Path

from envs.wildfire_response.regional.topographic import fire as topographic_fire
from envs.wildfire_response.regional.topographic.fire import NEIGHBOURS,edge_multiplier
from simulator import terrain as simulator_terrain
from simulator.terrain import PROFILES
from wildfire_containment_v1 import fire as containment_fire
from wildfire_containment_v1.fire import ContainmentDayFire,_paths
from wildfire_containment_v1.kernel import _digest, _edge
from eval import containment_spread_seam_v1 as seam

VERSION='wildfire-synthetic-two-component-fire-v1'
KINDS=('vegetation','built')
TRANSFER_KEYS=tuple(a+'>'+b for a in KINDS for b in KINDS)
UNKNOWN='unknown'


def _validate_parameters(transfer):
    if (type(transfer) is not dict or set(transfer)!=set(TRANSFER_KEYS)
            or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in transfer.values())):
        raise ValueError('provide four finite synthetic transfer factors in [0,1]')


def _validate_fire_parameters(fire):
    if (type(fire.heights) is not list or len(fire.heights)!=fire.height or
            any(type(row) is not list or len(row)!=fire.width for row in fire.heights)):
        raise ValueError('invalid terrain dimensions')
    rules=fire.rules
    for key in ('spread_factor','intensity_growth_per_step','burn_duration_multiplier',
                'ignition_probability_per_eligible_cell'):
        value=rules.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:
            raise ValueError('invalid synthetic fire parameter')
    if rules['spread_factor']==0 or rules['burn_duration_multiplier']==0:
        raise ValueError('spread and burn duration must be positive')
    if (type(fire._pressure_scale) not in (int,float) or not math.isfinite(fire._pressure_scale)
            or fire._pressure_scale<=0 or type(fire.spread_multiplier) not in (int,float)
            or not math.isfinite(fire.spread_multiplier) or fire.spread_multiplier<=0
            or type(fire.slope_enabled) is not bool or type(fire.wind) not in (tuple,list)
            or len(fire.wind)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>3 for x in fire.wind)):
        raise ValueError('invalid synthetic fire state')


def _validate_components(cells,width,height):
    if (type(cells) is not list or len(cells)!=height or
            any(type(row) is not list or len(row)!=width for row in cells)):
        raise ValueError('invalid component grid')
    for row in cells:
        for cell in row:
            if type(cell) is not dict or set(cell)!=set(KINDS):raise ValueError('both component states must be explicit')
            fraction=0.
            for kind in KINDS:
                c=cell[kind]
                if c is None or c==UNKNOWN:continue
                if type(c) is not dict or set(c)!={'terrain','fraction','fuel','initial_fuel','heat','age','wet','burned'}:
                    raise ValueError('invalid component state')
                if ((kind=='built' and c['terrain']!='built') or
                    (kind=='vegetation' and c['terrain'] not in ('field','scrub','woodland'))):
                    raise ValueError('component terrain differs from component kind')
                if (any(type(c[k]) not in (int,float) or not math.isfinite(c[k]) for k in ('fraction','fuel','initial_fuel','heat','burned'))
                    or not 0<c['fraction']<=1 or not 0<c['initial_fuel']<=1
                    or not 0<=c['fuel']<=c['initial_fuel'] or not 0<=c['heat']<=1 or not 0<=c['burned']<=1
                    or c['fuel']+c['burned']>1+1e-12
                    or type(c['age']) is not int or c['age']<0 or type(c['wet']) is not int or c['wet']<0):
                    raise ValueError('invalid synthetic component values')
                fraction+=c['fraction']
            if fraction>1+1e-12:raise ValueError('component fractions exceed one cell')
            if (not any(cell[k]==UNKNOWN for k in KINDS) and fraction>0
                    and not math.isclose(fraction,1.,abs_tol=1e-12,rel_tol=0)):
                raise ValueError('unrepresented cell fraction requires an explicit unknown component')


def _burning(c):
    return type(c) is dict and c['heat']>0 and c['fuel']>0


def _fuel_present(cell):
    return any(type(cell[k]) is dict and cell[k]['fuel']>0 for k in KINDS)


def _project(cell):
    """Private fire-clock projection, never an actor-facing terrain label."""
    states=[cell[k] for k in KINDS if type(cell[k]) is dict]
    if len(states)==1:
        value={k:v for k,v in states[0].items() if k!='fraction'}
        if any(cell[k]==UNKNOWN for k in KINDS):value['terrain']=None
        return value
    if not states:return dict(terrain=None,fuel=0.,initial_fuel=0.,heat=0.,age=0,wet=0,burned=0.)
    # Mixed terrain is deliberately not mislabeled as vegetation or built.
    return dict(terrain=None,fuel=min(1.,sum(c['fuel'] for c in states)),
        initial_fuel=min(1.,sum(c['initial_fuel'] for c in states)),
        heat=max(c['heat'] if c['fuel']>0 else 0. for c in states),age=max(c['age'] for c in states),
        wet=max(c['wet'] for c in states),burned=min(1.,sum(c['burned'] for c in states)))


class TwoComponentFireV1:
    """Synthetic only: exactly one physical containment tick per minute.

    Components are processed vegetation then built, in row/column order. Each
    source uses the target terrain's existing exercise spread factor; explicit
    transfer factors multiply the probability before clamping. Same-cell
    inter-component events are considered each minute without a physical edge;
    adjacent events use the existing wind interval and shared edge paths.
    Fractions do not automatically scale fuel or crossing probabilities.
    Zero transfer retains the candidate and physical pressure with zero chance,
    so it does not remove the destination's RNG draw or imply no edge attack.
    Candidates aggregate by destination and consume one sorted RNG draw each.
    The synthetic grid is capped at 4,096 cells and 240 steps. No implicit
    water, third fuel category, operational resource or population is modeled.
    """
    def __init__(self,fire,components,*,transfer,max_steps=120,opt_in=False):
        if opt_in is not True:raise ValueError('explicit synthetic prototype opt-in required')
        if not isinstance(fire,ContainmentDayFire):raise TypeError('validated containment-day fire required')
        if (type(max_steps) is not int or not 1<=max_steps<=240 or type(fire.width) is not int
                or type(fire.height) is not int or not 1<=fire.width<=128 or not 1<=fire.height<=128
                or fire.width*fire.height>4096):raise ValueError('synthetic fixture bounds exceeded')
        if (type(fire.heights) is not list or len(fire.heights)!=fire.height or
                any(type(row) is not list or len(row)!=fire.width for row in fire.heights)):
            raise ValueError('invalid terrain dimensions')
        if fire._assignments or fire._suppressing:raise ValueError('fire has pending work')
        if fire.groups or fire.scouts or fire.extinguishers or fire.trucks:
            raise ValueError('prototype does not simulate operational actors')
        _validate_fire_parameters(fire);_validate_parameters(transfer);_validate_components(components,fire.width,fire.height)
        initial_fire=fire.checkpoint()
        # Own a validated clone; the caller's legacy object is never mutated.
        self.fire=ContainmentDayFire.restore(copy.deepcopy(initial_fire))
        self._components=copy.deepcopy(components);self.transfer=copy.deepcopy(transfer)
        self.max_steps=max_steps;self._model_start_tick=self.fire.tick
        self._initial=copy.deepcopy(components);self._dependencies=seam.dependency_identity()
        self._source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self._fire_initial_sha256=_digest(initial_fire)
        self._input_sha256=_digest({'components':self._initial,'transfer':self.transfer,
                                    'fire_initial_sha256':self._fire_initial_sha256,'max_steps':self.max_steps,
                                    'model_start_tick':self._model_start_tick})
        self.status='active';self.reason=None;self._in_step=False
        self._sync_projection()

    def _ensure(self):
        try:
            if (self.status not in ('active','censored') or PROFILES is not simulator_terrain.PROFILES
                    or edge_multiplier is not topographic_fire.edge_multiplier or NEIGHBOURS!=topographic_fire.NEIGHBOURS
                    or _paths is not containment_fire._paths or seam.dependency_identity()!=self._dependencies
                    or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=self._source_sha256
                    or _digest({'components':self._initial,'transfer':self.transfer,
                                'fire_initial_sha256':self._fire_initial_sha256,'max_steps':self.max_steps,
                            'model_start_tick':self._model_start_tick})!=self._input_sha256
                    or self.fire.tick-self.fire._start_tick!=self.fire._containment._minute
                    or not 0<=self.fire.tick-self._model_start_tick<=self.max_steps
                    or self.fire.cells!=[[_project(cell) for cell in row] for row in self._components]):
                raise RuntimeError('prototype dependencies or state changed')
            _validate_fire_parameters(self.fire);_validate_parameters(self.transfer)
            _validate_components(self._components,self.fire.width,self.fire.height)
        except BaseException:
            self.status='indeterminate';raise

    def _sync_projection(self):
        self.fire.cells=[[_project(cell) for cell in row] for row in self._components]

    def _guard(self):
        f=self.fire
        for y,row in enumerate(self._components):
            for x,cell in enumerate(row):
                if not any(_burning(cell[k]) for k in KINDS):continue
                for dy in (-1,0,1):
                    for dx in (-1,0,1):
                        a,b=x+dx,y+dy
                        if not (0<=a<f.width and 0<=b<f.height):return 'grid_boundary'
                        other=self._components[b][a]
                        if any(other[k]==UNKNOWN for k in KINDS):return 'unknown_component'
                        if _fuel_present(other):
                            height=f.heights[b][a]
                            if type(height) not in (int,float) or not math.isfinite(height):return 'unknown_terrain'
        return None

    def _validate_assignment_support(self,assignments):
        """Reject unsupported line endpoints before fire or containment changes."""
        if not isinstance(assignments,(list,tuple)):
            raise ValueError('assignments must be a sequence')
        for assignment in assignments:
            if not isinstance(assignment,dict):raise ValueError('invalid assignment')
            for x,y in _edge(assignment.get('edge')):
                if not (0<=x<self.fire.width and 0<=y<self.fire.height):
                    raise ValueError('assignment edge outside grid')
                cell=self._components[y][x];height=self.fire.heights[y][x]
                if (any(cell[k]==UNKNOWN for k in KINDS)
                        or not any(type(cell[k]) is dict for k in KINDS)
                        or type(height) not in (int,float) or not math.isfinite(height)):
                    raise ValueError('assignment edge touches unknown or unsupported cell')

    def _candidates(self):
        """Generate synthetic source/target candidates; no containment or draws."""
        f=self.fire;f.tick+=1;candidates=[];attacks={}
        for y,row in enumerate(self._components):
            for x,cell in enumerate(row):
                for kind in KINDS:
                    source=cell[kind]
                    if type(source) is not dict:continue
                    wet=source['wet'];source['wet']=max(0,wet-1)
                    if not _burning(source):continue
                    profile=PROFILES[source['terrain']];source['age']+=1
                    if not wet:source['heat']=min(1.,source['heat']+f.rules['intensity_growth_per_step'])
                    if source['fuel']<.2:source['heat']=min(source['heat'],max(.05,source['fuel']/.2))
                    consumed=min(source['fuel'],max(.15,source['heat'])/(profile['duration']*f.rules['burn_duration_multiplier']))
                    source['fuel']=max(0.,source['fuel']-consumed);source['burned']=min(1.,source['burned']+consumed)
                    for target_kind in KINDS:
                        target=cell[target_kind];factor=self.transfer[kind+'>'+target_kind]
                        if target_kind==kind or type(target) is not dict or target['fuel']<=0 or target['heat']>0 or target['wet']>0:continue
                        chance=min(1.,f.rules['ignition_probability_per_eligible_cell']*PROFILES[target['terrain']]['spread']*source['heat']*factor)
                        candidates.append(seam.Candidate((x,y,target_kind),chance,((),)))
                    for dx,dy in NEIGHBOURS:
                        a,b=x+dx,y+dy
                        if not (0<=a<f.width and 0<=b<f.height):continue
                        if dx and dy and (not _fuel_present(self._components[y][a]) or not _fuel_present(self._components[b][x])):continue
                        distance=math.hypot(dx,dy);interval=f.spread_interval(dx/distance,dy/distance)
                        if source['age']%interval:continue
                        for target_kind in KINDS:
                            target=self._components[b][a][target_kind];factor=self.transfer[kind+'>'+target_kind]
                            if type(target) is not dict or target['fuel']<=0 or target['heat']>0 or target['wet']>0:continue
                            base=f.rules['ignition_probability_per_eligible_cell']*PROFILES[target['terrain']]['spread']*source['heat']/distance
                            chance=min(1.,base*edge_multiplier(f.heights,(x,y),(a,b),enabled=f.slope_enabled)*factor)
                            paths=_paths((x,y),(a,b));candidates.append(seam.Candidate((a,b,target_kind),chance,tuple(tuple(path) for path in paths)))
                            pressure=source['heat']*f._pressure_scale
                            for path in paths:
                                for edge in path:attacks[edge]=max(attacks.get(edge,0.),pressure)
                    if source['fuel']<=0:source['heat']=0.
        return seam.CandidateBatch(tuple(candidates),tuple((a,b,p) for (a,b),p in sorted(attacks.items())))

    def step(self,*,assignments=(),suppressing=()):
        if self.status!='active' or self._in_step:raise RuntimeError('prototype cannot advance')
        if self.fire.tick-self._model_start_tick>=self.max_steps:raise RuntimeError('synthetic step bound reached')
        if self.fire._assignments or self.fire._suppressing:raise ValueError('fire has pending work')
        self._ensure();reason=self._guard()
        if reason:self.status='censored';self.reason=reason;return False
        self._validate_assignment_support(assignments)
        seam.preflight_assignments(self.fire,assignments,suppressing)
        self.fire._assignments=copy.deepcopy(assignments);self.fire._suppressing=copy.deepcopy(suppressing);self._in_step=True
        try:
            batch=self._candidates();blocked=seam.resolve_containment(self.fire,(batch,))
            def ignite(destination):
                x,y,kind=destination;c=self._components[y][x][kind]
                if type(c) is not dict:raise ValueError('ignition targets an unsupported component')
                c.update(heat=.25,age=0)
            seam.apply_ignitions(self.fire,(batch,),blocked,ignite)
            self._sync_projection();seam.finish_minute(self.fire)
            reason=self._guard()
            if reason:self.status='censored';self.reason=reason;return False
            return True
        except BaseException:
            self.status='indeterminate';raise
        finally:
            self.fire._assignments=[];self.fire._suppressing=[];self._in_step=False

    def checkpoint(self):
        """Private trusted checkpoint; no checkpoint or outcome for censored fire."""
        if self.status!='active' or self._in_step:raise RuntimeError('checkpoint unavailable')
        self._ensure();reason=self._guard()
        if reason:self.status='censored';self.reason=reason;raise RuntimeError('checkpoint unsupported')
        value=dict(version=VERSION,source_sha256=self._source_sha256,dependencies=copy.deepcopy(self._dependencies),
            input_sha256=self._input_sha256,fire_initial_sha256=self._fire_initial_sha256,
            max_steps=self.max_steps,model_start_tick=self._model_start_tick,
            initial=copy.deepcopy(self._initial),transfer=copy.deepcopy(self.transfer),
            components=copy.deepcopy(self._components),fire=self.fire.checkpoint())
        value['sha256']=_digest(value);return value

    @classmethod
    def restore(cls,value,*,expected_input_sha256,expected_checkpoint_sha256,opt_in=False):
        """Restore only with an independently trusted whole-checkpoint digest.

        The expected checkpoint digest must come from a trusted channel or
        authenticated envelope, not from the untrusted checkpoint itself.
        """
        if opt_in is not True:raise ValueError('explicit synthetic prototype opt-in required')
        if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)):
            raise ValueError('independently trusted checkpoint digest required')
        v=copy.deepcopy(value)
        if (type(v) is not dict or set(v)!={'version','source_sha256','dependencies','input_sha256','fire_initial_sha256','max_steps','model_start_tick','initial','transfer','components','fire','sha256'}
                or v['version']!=VERSION or v['sha256']!=expected_checkpoint_sha256
                or v['sha256']!=_digest({k:x for k,x in v.items() if k!='sha256'})
                or v['source_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                or v['dependencies']!=seam.dependency_identity() or v['input_sha256']!=expected_input_sha256
                or v['input_sha256']!=_digest({'components':v['initial'],'transfer':v['transfer'],
                                            'fire_initial_sha256':v['fire_initial_sha256'],'max_steps':v['max_steps'],
                                            'model_start_tick':v['model_start_tick']})):
            raise ValueError('prototype checkpoint identity differs')
        fire=ContainmentDayFire.restore(v['fire']);_validate_fire_parameters(fire);_validate_parameters(v['transfer'])
        if (type(v['max_steps']) is not int or not 1<=v['max_steps']<=240
                or type(v['model_start_tick']) is not int or not 0<=fire.tick-v['model_start_tick']<=v['max_steps']
                or not 1<=fire.width<=128 or not 1<=fire.height<=128 or fire.width*fire.height>4096):
            raise ValueError('prototype checkpoint bounds differ')
        _validate_components(v['initial'],fire.width,fire.height);_validate_components(v['components'],fire.width,fire.height)
        for y,row in enumerate(v['components']):
            for x,cell in enumerate(row):
                for kind in KINDS:
                    initial=v['initial'][y][x][kind];current=cell[kind]
                    if type(initial) is not dict:
                        if current!=initial:raise ValueError('component support changed')
                    elif (type(current) is not dict or any(current[k]!=initial[k] for k in ('terrain','fraction','initial_fuel'))
                          or current['fuel']>initial['fuel'] or current['burned']<initial['burned']
                          or not math.isclose(current['fuel']+current['burned'],initial['fuel']+initial['burned'],
                                              abs_tol=1e-9,rel_tol=1e-9)):
                        raise ValueError('component state differs from initial assumptions')
        instance=cls.__new__(cls);instance.fire=fire;instance._initial=v['initial'];instance._components=v['components'];instance.transfer=v['transfer']
        instance._dependencies=v['dependencies'];instance._source_sha256=v['source_sha256'];instance._input_sha256=v['input_sha256'];instance._fire_initial_sha256=v['fire_initial_sha256']
        instance.max_steps=v['max_steps'];instance._model_start_tick=v['model_start_tick']
        instance.status='active';instance.reason=None;instance._in_step=False
        projected=[[_project(cell) for cell in row] for row in instance._components]
        if projected!=fire.cells:raise ValueError('component and physical projection differ')
        if instance._guard() is not None:raise ValueError('restored prototype is unsupported')
        if _digest(instance.checkpoint())!=_digest(v):raise ValueError('prototype checkpoint restoration differs')
        return instance
