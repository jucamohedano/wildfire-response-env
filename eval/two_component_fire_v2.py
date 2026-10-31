"""Synthetic v2 two-component prototype with explicit inert and crossing data.

Trusted opt-in evaluator fixtures only. Inert is a separately supplied synthetic
assumption, not a measured footprint; unknown is never converted to inert.
Fractions do not scale physical coefficients. Empty area must be explicitly
represented as synthetic inert or unknown. No source host or actor API.
"""
import copy
import hashlib
import math
from pathlib import Path

from eval import two_component_fire_v1 as v1
from eval import containment_spread_seam_v1 as seam
from wildfire_containment_v1.fire import ContainmentDayFire
from wildfire_containment_v1.kernel import _digest, _edge

VERSION='wildfire-synthetic-two-component-fire-v2'
V1_SOURCE_PIN='204344f4d28ac07de2ddd1e4678bf98e2883947c8921f3cb44a63b83aee66d07'
UNKNOWN=v1.UNKNOWN
KINDS=v1.KINDS
_V1_FUNCTIONS={name:getattr(v1,name) for name in ('_validate_parameters','_validate_fire_parameters',
    '_validate_components','_burning','_fuel_present','_project','edge_multiplier','_paths')}
_V1_STEP=v1.TwoComponentFireV1.step


def _v1_source():
    actual=hashlib.sha256(Path(v1.__file__).read_bytes()).hexdigest()
    if (actual!=V1_SOURCE_PIN or v1.TwoComponentFireV1.step is not _V1_STEP
            or any(getattr(v1,name) is not original for name,original in _V1_FUNCTIONS.items())
            or v1.KINDS!=KINDS or v1.UNKNOWN!=UNKNOWN):
        raise RuntimeError('v1 helper source differs')
    return actual


def _validate_grid(grid,width,height,label):
    if type(grid) is not list or len(grid)!=height or any(type(row) is not list or len(row)!=width for row in grid):
        raise ValueError('invalid '+label+' grid')


def _validate_cells(cells,width,height):
    _validate_grid(cells,width,height,'component')
    for row in cells:
        for cell in row:
            if type(cell) is not dict or set(cell)!={'vegetation','built','inert'}:
                raise ValueError('vegetation, built, and inert states must be explicit')
            inert=cell['inert'];total=0.;unknown=inert==UNKNOWN
            if inert is not None and inert!=UNKNOWN:
                if (type(inert) is not dict or set(inert)!={'fraction','assumption'}
                        or inert['assumption'] not in ('synthetic_inert','unknown') or type(inert['fraction']) not in (int,float)
                        or not math.isfinite(inert['fraction']) or not 0<inert['fraction']<=1):
                    raise ValueError('invalid explicit synthetic inert state')
                total+=inert['fraction']
            combust={k:copy.deepcopy(cell[k]) for k in KINDS};fractions=[]
            for kind in KINDS:
                item=combust[kind]
                if item==UNKNOWN:unknown=True
                elif type(item) is dict:
                    f=item.get('fraction')
                    if type(f) not in (int,float) or not math.isfinite(f) or not 0<f<=1:
                        raise ValueError('invalid component fraction')
                    total+=f;fractions.append((kind,f))
            if total>1+1e-12 or (not unknown and not math.isclose(total,1.,abs_tol=1e-12,rel_tol=0)):
                raise ValueError('unrepresented cell fraction requires explicit unknown')
            # Reuse v1's state and terrain validation. Normalize only the
            # temporary validation view; the real sample assumptions stay intact.
            if not any(combust[k]==UNKNOWN for k in KINDS) and fractions:
                denom=sum(f for _,f in fractions)
                for kind,f in fractions:combust[kind]['fraction']=f/denom
            v1._validate_components([[combust]],1,1)


def _validate_assumptions(transfers,crossings,width,height):
    _validate_grid(transfers,width,height,'transfer');_validate_grid(crossings,width,height,'crossing')
    for row in transfers:
        for value in row:
            if value!=UNKNOWN:v1._validate_parameters(value)
    for row in crossings:
        for value in row:
            if value!=UNKNOWN and (type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=1):
                raise ValueError('crossing factor must be explicit unknown or in (0,1]')


def _project(cell):
    value=v1._project({k:cell[k] for k in KINDS})
    if cell['inert'] is not None:value['terrain']=None
    return value


def _unknown(cell):
    inert=cell['inert']
    return any(cell[k]==UNKNOWN for k in KINDS) or inert==UNKNOWN or (type(inert) is dict and inert['assumption']=='unknown')


class TwoComponentFireV2(v1.TwoComponentFireV1):
    """One shared containment tick; all parameter grids are explicit.

    Inert may be an explicitly synthetic inert fraction or an explicitly
    unknown fraction; either unknown form is censored near burning fire.
    A source cell's four transfer factors apply to its outgoing component
    candidates. Adjacent candidates multiply the explicit openness factors of
    both endpoint cells and, for diagonals, both corner cells; same-cell
    transfer uses the source-cell factor. These are synthetic assumptions.
    """
    def __init__(self,fire,components,*,transfers,crossings,max_cells,max_steps=120,opt_in=False):
        if opt_in is not True:raise ValueError('explicit synthetic prototype opt-in required')
        if not isinstance(fire,ContainmentDayFire):raise TypeError('validated containment-day fire required')
        if (type(max_cells) is not int or not 1<=max_cells<=16384 or type(max_steps) is not int or not 1<=max_steps<=240
                or type(fire.width) is not int or type(fire.height) is not int or not 1<=fire.width<=256
                or not 1<=fire.height<=256 or fire.width*fire.height>max_cells):
            raise ValueError('synthetic fixture bounds exceeded')
        if fire._assignments or fire._suppressing or fire.groups or fire.scouts or fire.extinguishers or fire.trucks:
            raise ValueError('prototype does not own pending work or actors')
        _v1_source();v1._validate_fire_parameters(fire);_validate_cells(components,fire.width,fire.height)
        _validate_assumptions(transfers,crossings,fire.width,fire.height)
        initial_fire=fire.checkpoint();self.fire=ContainmentDayFire.restore(copy.deepcopy(initial_fire))
        self._components=copy.deepcopy(components);self._initial=copy.deepcopy(components)
        self.transfers=copy.deepcopy(transfers);self.crossings=copy.deepcopy(crossings)
        self.max_steps=max_steps;self.max_cells=max_cells;self._model_start_tick=self.fire.tick
        self._dependencies=seam.dependency_identity();self._source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self._v1_sha256=_v1_source();self._fire_initial_sha256=_digest(initial_fire)
        self._input_sha256=self._input_digest();self.status='active';self.reason=None;self._in_step=False
        self._sync_projection()

    def _input_digest(self):
        return _digest({'components':self._initial,'transfers':self.transfers,'crossings':self.crossings,
            'fire_initial_sha256':self._fire_initial_sha256,'max_steps':self.max_steps,'max_cells':self.max_cells,
            'model_start_tick':self._model_start_tick})

    def _sync_projection(self):self.fire.cells=[[_project(c) for c in row] for row in self._components]

    def _ensure(self):
        try:
            if (self.status not in ('active','censored') or _v1_source()!=self._v1_sha256
                    or seam.dependency_identity()!=self._dependencies
                    or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=self._source_sha256
                    or self._input_digest()!=self._input_sha256
                    or self.fire.tick-self.fire._start_tick!=self.fire._containment._minute
                    or not 0<=self.fire.tick-self._model_start_tick<=self.max_steps
                    or self.fire.cells!=[[_project(c) for c in row] for row in self._components]):
                raise RuntimeError('prototype dependencies or state changed')
            v1._validate_fire_parameters(self.fire);_validate_cells(self._components,self.fire.width,self.fire.height)
            _validate_assumptions(self.transfers,self.crossings,self.fire.width,self.fire.height)
        except BaseException:
            self.status='indeterminate';raise

    def _guard(self):
        f=self.fire
        for y,row in enumerate(self._components):
            for x,cell in enumerate(row):
                if not any(v1._burning(cell[k]) for k in KINDS):continue
                for dy in (-1,0,1):
                    for dx in (-1,0,1):
                        a,b=x+dx,y+dy
                        if not (0<=a<f.width and 0<=b<f.height):return 'grid_boundary'
                        other=self._components[b][a]
                        if _unknown(other):return 'unknown_component_or_inert'
                        if self.transfers[b][a]==UNKNOWN:return 'unknown_transfer'
                        if self.crossings[b][a]==UNKNOWN:return 'unknown_crossing'
                        if v1._fuel_present(other):
                            z=f.heights[b][a]
                            if type(z) not in (int,float) or not math.isfinite(z):return 'unknown_terrain'
        return None

    def _validate_assignment_support(self,assignments):
        if not isinstance(assignments,(list,tuple)):raise ValueError('assignments must be a sequence')
        for assignment in assignments:
            if not isinstance(assignment,dict):raise ValueError('invalid assignment')
            for x,y in _edge(assignment.get('edge')):
                if not (0<=x<self.fire.width and 0<=y<self.fire.height):raise ValueError('assignment edge outside grid')
                cell=self._components[y][x];height=self.fire.heights[y][x]
                if (_unknown(cell) or self.transfers[y][x]==UNKNOWN or self.crossings[y][x]==UNKNOWN
                        or not any(type(cell[k]) is dict for k in KINDS)
                        or type(height) not in (int,float) or not math.isfinite(height)):
                    raise ValueError('assignment edge touches unknown or unsupported cell')

    def _candidates(self):
        f=self.fire;f.tick+=1;candidates=[];attacks={}
        for y,row in enumerate(self._components):
            for x,cell in enumerate(row):
                for kind in KINDS:
                    source=cell[kind]
                    if type(source) is not dict:continue
                    wet=source['wet'];source['wet']=max(0,wet-1)
                    if not v1._burning(source):continue
                    terrain=v1.PROFILES[source['terrain']];source['age']+=1
                    if not wet:source['heat']=min(1.,source['heat']+f.rules['intensity_growth_per_step'])
                    if source['fuel']<.2:source['heat']=min(source['heat'],max(.05,source['fuel']/.2))
                    consumed=min(source['fuel'],max(.15,source['heat'])/(terrain['duration']*f.rules['burn_duration_multiplier']))
                    source['fuel']=max(0.,source['fuel']-consumed);source['burned']=min(1.,source['burned']+consumed)
                    for target_kind in KINDS:
                        target=cell[target_kind];factor=self.transfers[y][x][kind+'>'+target_kind]
                        if target_kind==kind or type(target) is not dict or target['fuel']<=0 or target['heat']>0 or target['wet']>0:continue
                        chance=min(1.,f.rules['ignition_probability_per_eligible_cell']*v1.PROFILES[target['terrain']]['spread']*source['heat']*factor)
                        chance*=self.crossings[y][x]
                        candidates.append(seam.Candidate((x,y,target_kind),chance,((),)))
                    for dx,dy in v1.NEIGHBOURS:
                        a,b=x+dx,y+dy
                        if not (0<=a<f.width and 0<=b<f.height):continue
                        if dx and dy and (not v1._fuel_present(self._components[y][a]) or not v1._fuel_present(self._components[b][x])):continue
                        distance=math.hypot(dx,dy);interval=f.spread_interval(dx/distance,dy/distance)
                        if source['age']%interval:continue
                        for target_kind in KINDS:
                            target=self._components[b][a][target_kind];factor=self.transfers[y][x][kind+'>'+target_kind]
                            if type(target) is not dict or target['fuel']<=0 or target['heat']>0 or target['wet']>0:continue
                            base=f.rules['ignition_probability_per_eligible_cell']*v1.PROFILES[target['terrain']]['spread']*source['heat']/distance
                            chance=min(1.,base*v1.edge_multiplier(f.heights,(x,y),(a,b),enabled=f.slope_enabled)*factor)
                            touched={(x,y),(a,b)}
                            if dx and dy:touched.update(((a,y),(x,b)))
                            for tx,ty in sorted(touched):chance*=self.crossings[ty][tx]
                            paths=v1._paths((x,y),(a,b));candidates.append(seam.Candidate((a,b,target_kind),chance,tuple(tuple(path) for path in paths)))
                            pressure=source['heat']*f._pressure_scale
                            for path in paths:
                                for edge in path:attacks[edge]=max(attacks.get(edge,0.),pressure)
                    if source['fuel']<=0:source['heat']=0.
        return seam.CandidateBatch(tuple(candidates),tuple((a,b,p) for (a,b),p in sorted(attacks.items())))

    def checkpoint(self):
        if self.status!='active' or self._in_step:raise RuntimeError('checkpoint unavailable')
        self._ensure();reason=self._guard()
        if reason:self.status='censored';self.reason=reason;raise RuntimeError('checkpoint unsupported')
        value=dict(version=VERSION,source_sha256=self._source_sha256,v1_sha256=self._v1_sha256,
            dependencies=copy.deepcopy(self._dependencies),input_sha256=self._input_sha256,
            fire_initial_sha256=self._fire_initial_sha256,max_steps=self.max_steps,max_cells=self.max_cells,
            model_start_tick=self._model_start_tick,initial=copy.deepcopy(self._initial),transfers=copy.deepcopy(self.transfers),
            crossings=copy.deepcopy(self.crossings),components=copy.deepcopy(self._components),fire=self.fire.checkpoint())
        value['sha256']=_digest(value);return value

    @classmethod
    def restore(cls,value,*,expected_input_sha256,expected_checkpoint_sha256,opt_in=False):
        if opt_in is not True:raise ValueError('explicit synthetic prototype opt-in required')
        if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)):
            raise ValueError('independently trusted checkpoint digest required')
        v=copy.deepcopy(value);keys={'version','source_sha256','v1_sha256','dependencies','input_sha256','fire_initial_sha256',
          'max_steps','max_cells','model_start_tick','initial','transfers','crossings','components','fire','sha256'}
        if (type(v) is not dict or set(v)!=keys or v['version']!=VERSION or v['sha256']!=expected_checkpoint_sha256
                or v['sha256']!=_digest({k:x for k,x in v.items() if k!='sha256'})
                or v['source_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                or v['v1_sha256']!=_v1_source() or v['dependencies']!=seam.dependency_identity()
                or v['input_sha256']!=expected_input_sha256):raise ValueError('prototype checkpoint identity differs')
        fire=ContainmentDayFire.restore(v['fire']);v1._validate_fire_parameters(fire)
        if (type(v['max_cells']) is not int or not 1<=v['max_cells']<=16384 or type(v['max_steps']) is not int
                or not 1<=v['max_steps']<=240 or type(v['model_start_tick']) is not int
                or not 0<=fire.tick-v['model_start_tick']<=v['max_steps'] or not 1<=fire.width<=256
                or not 1<=fire.height<=256 or fire.width*fire.height>v['max_cells']):raise ValueError('prototype bounds differ')
        _validate_cells(v['initial'],fire.width,fire.height);_validate_cells(v['components'],fire.width,fire.height)
        _validate_assumptions(v['transfers'],v['crossings'],fire.width,fire.height)
        for y,row in enumerate(v['components']):
            for x,cell in enumerate(row):
                if cell['inert']!=v['initial'][y][x]['inert']:raise ValueError('inert support changed')
                for kind in KINDS:
                    initial=v['initial'][y][x][kind];current=cell[kind]
                    if type(initial) is not dict:
                        if current!=initial:raise ValueError('component support changed')
                    elif (type(current) is not dict or any(current[k]!=initial[k] for k in ('terrain','fraction','initial_fuel'))
                          or current['fuel']>initial['fuel'] or current['burned']<initial['burned']
                          or not math.isclose(current['fuel']+current['burned'],initial['fuel']+initial['burned'],abs_tol=1e-9,rel_tol=1e-9)):
                        raise ValueError('component state differs from initial assumptions')
        instance=cls.__new__(cls);instance.fire=fire;instance._initial=v['initial'];instance._components=v['components']
        instance.transfers=v['transfers'];instance.crossings=v['crossings'];instance.max_steps=v['max_steps'];instance.max_cells=v['max_cells']
        instance._model_start_tick=v['model_start_tick'];instance._dependencies=v['dependencies'];instance._source_sha256=v['source_sha256']
        instance._v1_sha256=v['v1_sha256'];instance._fire_initial_sha256=v['fire_initial_sha256'];instance._input_sha256=v['input_sha256']
        instance.status='active';instance.reason=None;instance._in_step=False
        if instance._input_digest()!=instance._input_sha256:raise ValueError('prototype input identity differs')
        if [[_project(c) for c in row] for row in instance._components]!=fire.cells:raise ValueError('physical projection differs')
        if instance._guard() is not None:raise ValueError('restored prototype is unsupported')
        if _digest(instance.checkpoint())!=_digest(v):raise ValueError('prototype checkpoint restoration differs')
        return instance
