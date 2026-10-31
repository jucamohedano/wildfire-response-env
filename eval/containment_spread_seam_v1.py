"""Experimental reusable containment spread seam, preserving the old kernel.

Trusted evaluator-side building blocks; not a calibrated model, input adapter,
actor API or mixed-urban implementation. The existing containment fire step is
unchanged. A caller must supply a fail-closed whole-stencil guard and may only
checkpoint after a completed, supported step. Exceptions poison the seam.
"""
from dataclasses import dataclass
import copy
import hashlib
import math
from pathlib import Path

from envs.wildfire_response.regional.topographic import fire as topographic_fire
from envs.wildfire_response.regional.topographic.fire import BOUNDARY_GUARD_CELLS, NEIGHBOURS, edge_multiplier
from simulator import engine as simulator_engine, terrain as simulator_terrain
from simulator.terrain import PROFILES
from wildfire_containment_v1 import fire as containment_fire, kernel as containment_kernel
from wildfire_containment_v1.fire import _paths
from wildfire_containment_v1.kernel import ContainmentKernel, _digest, _edge

VERSION='wildfire-containment-spread-seam-v1'


def dependency_identity():
    """Pin imported fire primitives as well as this experimental seam."""
    if (edge_multiplier is not topographic_fire.edge_multiplier or _paths is not containment_fire._paths
            or PROFILES is not simulator_terrain.PROFILES or NEIGHBOURS!=topographic_fire.NEIGHBOURS
            or BOUNDARY_GUARD_CELLS!=topographic_fire.BOUNDARY_GUARD_CELLS):
        raise RuntimeError('spread dependencies were replaced')
    files={'seam':__file__,'topographic':topographic_fire.__file__,'terrain':simulator_terrain.__file__,
           'simulator':simulator_engine.__file__,'containment_fire':containment_fire.__file__,
           'containment_kernel':containment_kernel.__file__}
    return {'files':{k:hashlib.sha256(Path(v).read_bytes()).hexdigest() for k,v in files.items()},
            'terrain_profiles':_digest(PROFILES),'neighbours':_digest(NEIGHBOURS),
            'boundary_guard_cells':BOUNDARY_GUARD_CELLS}


@dataclass(frozen=True)
class Candidate:
    # The string component is empty in single-compartment parity mode. A
    # future backend can give distinct components at one physical cell keys.
    destination: tuple  # (column, row, component_string)
    chance: float
    paths: tuple  # alternative sequences of physical directed containment edges


@dataclass(frozen=True)
class CandidateBatch:
    candidates: tuple
    attacks: tuple  # (physical source, physical target, pressure)


def preflight_assignments(fire,assignments,suppressing):
    """Mirror the legacy host's pre-mutation containment assignment check."""
    if not isinstance(assignments,(list,tuple)):
        raise ValueError('assignments must be a sequence')
    for item in assignments:
        if not isinstance(item,dict):raise ValueError('invalid assignment')
        for x,y in _edge(item.get('edge')):
            if not (0<=x<fire.width and 0<=y<fire.height):raise ValueError('line edge outside fire grid')
    probe=ContainmentKernel.restore(fire._containment.checkpoint())
    probe.tick(probe._minute+1,assignments=assignments,attacks=[],
               wind=list(fire.wind),suppressing=suppressing)


def generate_candidates(fire, *, crossing_openness=None):
    """Advance cell-local state and produce a single-compartment batch.

    Like the original step this mutates cell heat/fuel and advances fire.tick,
    but does not touch containment, RNG, or target ignition. Call only behind a
    pre-checked support guard; a failure makes the surrounding seam indeterminate.
    """
    if fire.groups or fire.scouts or fire.extinguishers or fire.trucks:
        raise RuntimeError('Topographic kernel requires regional-only actors')
    fire.tick+=1
    candidates=[];attacks={}
    for row,cells in enumerate(fire.cells):
        for column,cell in enumerate(cells):
            wet=cell.get('wet',0);cell['wet']=max(0,wet-1)
            if not fire.burning(cell):continue
            profile=PROFILES[cell.get('terrain','field')]
            cell['age']+=1
            if not wet:cell['heat']=min(1.0,cell['heat']+fire.rules['intensity_growth_per_step'])
            if cell['fuel']<.2:cell['heat']=min(cell['heat'],max(.05,cell['fuel']/.2))
            consumed=min(cell['fuel'],max(.15,cell['heat'])/(profile['duration']*fire.rules['burn_duration_multiplier']))
            cell['fuel']=max(0.0,cell['fuel']-consumed);cell['burned']=min(1.0,cell.get('burned',0)+consumed)
            for dx,dy in NEIGHBOURS:
                x,y=column+dx,row+dy
                if not (0<=x<fire.width and 0<=y<fire.height):continue
                target=fire.cells[y][x]
                if target['fuel']<=0 or target['heat']>0 or target.get('wet',0)>0:continue
                if dx and dy and (fire.cells[row][x]['fuel']<=0 or fire.cells[y][column]['fuel']<=0):continue
                distance=math.hypot(dx,dy);interval=fire.spread_interval(dx/distance,dy/distance)
                if cell['age']%interval:continue
                base=fire.rules['ignition_probability_per_eligible_cell']*PROFILES[target.get('terrain','field')]['spread']*cell['heat']/distance
                chance=min(1.0,base*edge_multiplier(fire.heights,(column,row),(x,y),enabled=fire.slope_enabled))
                if crossing_openness is not None:
                    touched={(column,row),(x,y)}
                    if dx and dy:touched.update(((x,row),(column,y)))
                    for point in sorted(touched):chance*=crossing_openness.get(point,1.0)
                paths=_paths((column,row),(x,y))
                candidates.append(Candidate((x,y,''),chance,tuple(tuple(path) for path in paths)))
                pressure=cell['heat']*fire._pressure_scale
                for path in paths:
                    for crossing in path:attacks[crossing]=max(attacks.get(crossing,0.),pressure)
            if cell['fuel']<=0:cell['heat']=0.0
    return CandidateBatch(tuple(candidates),tuple((a,b,p) for (a,b),p in sorted(attacks.items())))


def resolve_containment(fire,batches):
    """Aggregate batches and advance the shared physical-edge kernel once."""
    if (type(batches) not in (tuple,list) or not batches or any(
            not isinstance(batch,CandidateBatch) or type(batch.candidates) is not tuple
            or type(batch.attacks) is not tuple for batch in batches)):
        raise ValueError('one or more immutable candidate batches required')
    attacks={}
    for batch in batches:
        for source,target,pressure in batch.attacks:
            if (type(source) is not tuple or type(target) is not tuple or len(source)!=2 or len(target)!=2
                    or any(type(v) is not int for v in (*source,*target))
                    or abs(source[0]-target[0])+abs(source[1]-target[1])!=1
                    or any(not (0<=x<fire.width and 0<=y<fire.height) for x,y in (source,target))
                    or type(pressure) not in (int,float) or not math.isfinite(pressure) or pressure<0):
                raise ValueError('invalid directed physical-edge attack')
            key=(source,target);attacks[key]=max(attacks.get(key,0.),pressure)
    for batch in batches:
        for candidate in batch.candidates:
            if (not isinstance(candidate,Candidate) or type(candidate.destination) is not tuple
                    or len(candidate.destination)!=3 or type(candidate.destination[0]) is not int
                    or type(candidate.destination[1]) is not int or type(candidate.destination[2]) is not str
                    or not (0<=candidate.destination[0]<fire.width and 0<=candidate.destination[1]<fire.height)
                    or type(candidate.chance) not in (int,float) or not math.isfinite(candidate.chance)
                    or not 0<=candidate.chance<=1 or not candidate.paths):
                raise ValueError('invalid ignition candidate')
            for path in candidate.paths:
                if any(edge not in attacks for edge in path):
                    raise ValueError('candidate path missing a physical-edge attack')
                if path and (path[-1][1] != candidate.destination[:2]
                             or any(a[1]!=b[0] for a,b in zip(path,path[1:]))):
                    raise ValueError('candidate path does not reach its destination')
    result=fire._containment.tick(fire._containment._minute+1,
        assignments=fire._assignments,suppressing=fire._suppressing,
        attacks=[dict(source=a,target=b,pressure=p) for (a,b),p in sorted(attacks.items())],wind=list(fire.wind))
    fire._last_costs=result['costs']
    return {(tuple(item['source']),tuple(item['target'])):item['blocked'] for item in result['crossings']}


def apply_ignitions(fire,batches,blocked,ignite):
    """One sorted RNG draw per destination, even if all its paths are blocked."""
    destinations={}
    for batch in batches:
        for candidate in batch.candidates:
            allowed=any(all(not blocked[edge] for edge in path) for path in candidate.paths)
            key=candidate.destination
            destinations[key]=max(destinations.get(key,0),candidate.chance if allowed else 0)
    for destination,chance in sorted(destinations.items()):
        if fire.rng.random()<chance:ignite(destination)


def finish_minute(fire):
    """Preserve the legacy boundary and delayed-satellite bookkeeping."""
    if fire.boundary_contact_at is None and any(fire.burning(cell) for row,cells in enumerate(fire.cells)
            for column,cell in enumerate(cells) if row<BOUNDARY_GUARD_CELLS or row>=fire.height-BOUNDARY_GUARD_CELLS
            or column<BOUNDARY_GUARD_CELLS or column>=fire.width-BOUNDARY_GUARD_CELLS):
        fire.boundary_contact_at=fire.tick
    if fire.tick%12==0:
        blocks=sorted({(column//8*8,row//8*8) for row,cells in enumerate(fire.cells)
                       for column,cell in enumerate(cells) if fire.burning(cell)})
        fire.satellite_queue.append(dict(captured_at=fire.tick,available_at=fire.tick+12,blocks=blocks))
    while fire.satellite_queue and fire.satellite_queue[0]['available_at']<=fire.tick:
        fire.satellite=fire.satellite_queue.pop(0)


def _mixed_backend_openness(backend):
    """Recover the factors represented by the backend's verified inputs."""
    values=copy.deepcopy(backend._mixed_openness)
    for pos,estimate in getattr(backend,'_estimated',{}).items():
        values[pos]=estimate[2]
    return values


class SpreadSeam:
    """Guarded step with validated checkpoints and no retained assignments.

    The trusted caller must independently pin the guard behind guard_id. For a
    mixed fire, pass its validated InteriorMixedFireV2/EstimatedMixedFireV1
    backend; raw mixed simulator checkpoints are deliberately unsupported.
    """
    def __init__(self,fire,*,support_guard,guard_id,crossing_openness=None,mixed_backend=None):
        if not callable(support_guard) or type(guard_id) is not str or not guard_id:
            raise ValueError('explicit whole-stencil support guard and identity required')
        self._mixed_backend=mixed_backend
        if mixed_backend is not None:
            from wildfire_expanded_incident_v1.mixed_v2 import InteriorMixedFireV2, _MixedFire
            if (not isinstance(mixed_backend,InteriorMixedFireV2) or not isinstance(fire,_MixedFire)
                    or mixed_backend._fire is not fire):
                raise TypeError('validated mixed backend must own the exact fire object')
            mixed_backend._ensure()
            if not hasattr(fire,'_mixed_openness'):
                raise ValueError('mixed backend lacks water crossing factors')
            actual=fire._mixed_openness
            if actual!=_mixed_backend_openness(mixed_backend):
                raise ValueError('mixed fire crossing factors differ from backend inputs')
            if crossing_openness is None:crossing_openness=actual
            elif crossing_openness!=actual:raise ValueError('mixed crossing factors differ from backend')
            self._snapshot_kind='validated-mixed-backend-v1'
        else:
            if hasattr(fire,'_mixed_openness'):
                raise TypeError('raw mixed fire requires a validated mixed backend adapter')
            if not isinstance(fire,containment_fire.ContainmentDayFire):
                raise TypeError('unsupported raw fire checkpoint contract')
            self._snapshot_kind='containment-day-fire-v1'
        if crossing_openness is not None:
            if type(crossing_openness) is not dict or any(
                type(k) is not tuple or len(k)!=2 or any(type(v) is not int for v in k)
                or not (0<=k[0]<fire.width and 0<=k[1]<fire.height)
                or type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=1
                for k,value in crossing_openness.items()):
                raise ValueError('crossing factors must be explicit finite values in (0,1]')
        self.fire=fire;self.support_guard=support_guard;self.guard_id=guard_id
        self.crossing_openness=copy.deepcopy(crossing_openness)
        self._crossing_pin=_digest(None if self.crossing_openness is None else
                                    [[x,y,v] for (x,y),v in sorted(self.crossing_openness.items())])
        self.status='active';self._in_step=False;self._dependencies=dependency_identity()

    def _ensure_bound(self):
        if dependency_identity()!=self._dependencies:raise RuntimeError('spread dependencies changed')
        current=None if self.crossing_openness is None else [[x,y,v] for (x,y),v in sorted(self.crossing_openness.items())]
        if _digest(current)!=self._crossing_pin:raise RuntimeError('crossing factors changed')
        if self._mixed_backend is not None:
            self._mixed_backend._ensure()
            if (self._mixed_backend._fire is not self.fire or
                    getattr(self.fire,'_mixed_openness',None)!=self.crossing_openness or
                    _mixed_backend_openness(self._mixed_backend)!=self.crossing_openness):
                raise RuntimeError('mixed backend or crossing factors changed')
        elif hasattr(self.fire,'_mixed_openness'):
            raise RuntimeError('unbound mixed crossing factors appeared')

    def _supported(self):
        if self._mixed_backend is not None and self._mixed_backend._check() is not True:
            return False
        return self.support_guard(self.fire) is True

    def step(self,*,assignments=(),suppressing=()):
        if self.status!='active' or self._in_step:raise RuntimeError('seam cannot advance')
        if self.fire._assignments or self.fire._suppressing:
            raise ValueError('fire already has pending assignments or suppression')
        try:
            self._ensure_bound()
            if not self._supported():self.status='censored';return False
        except BaseException:
            self.status='indeterminate';raise
        if self._mixed_backend is not None and (assignments or suppressing):
            raise ValueError('mixed backend does not support assignments or suppression')
        # Legacy step_with_containment validates before installing assignments;
        # validation failures do not mutate fire, RNG, or the seam status.
        preflight_assignments(self.fire,assignments,suppressing)
        self.fire._assignments=copy.deepcopy(assignments)
        self.fire._suppressing=copy.deepcopy(suppressing)
        self._in_step=True
        try:
            batch=generate_candidates(self.fire,crossing_openness=self.crossing_openness)
            blocked=resolve_containment(self.fire,(batch,))
            def ignite(destination):
                x,y,component=destination
                if component!='':raise ValueError('unexpected single-compartment destination')
                self.fire.cells[y][x].update(heat=.25,age=0)
            apply_ignitions(self.fire,(batch,),blocked,ignite)
            finish_minute(self.fire)
            if not self._supported():self.status='censored';return False
            return True
        except BaseException:
            self.status='indeterminate';raise
        finally:
            self.fire._assignments=[];self.fire._suppressing=[];self._in_step=False

    def checkpoint(self):
        """Capture a supported boundary using the validated owner snapshot."""
        if self.status!='active' or self._in_step:raise RuntimeError('seam checkpoint unavailable')
        try:
            self._ensure_bound()
            if not self._supported():
                self.status='censored';raise RuntimeError('seam checkpoint is unsupported')
            openness=None if self.crossing_openness is None else [[x,y,v] for (x,y),v in sorted(self.crossing_openness.items())]
            state=(self._mixed_backend.snapshot() if self._mixed_backend is not None else self.fire.checkpoint())
            value={'version':VERSION,'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'dependencies':copy.deepcopy(self._dependencies),'snapshot_kind':self._snapshot_kind,
                   'guard_id':self.guard_id,'crossing_openness':openness,'fire':state}
            value['sha256']=_digest(value);return value
        except BaseException:
            if self.status!='censored':self.status='indeterminate'
            raise

    @classmethod
    def restore(cls,value,*,support_guard,expected_guard_id,fire_restore=None,mixed_backend_factory=None):
        """Restore through the exact validated snapshot owner and guard identity."""
        v=copy.deepcopy(value)
        if (type(v) is not dict or set(v)!={'version','source_sha256','dependencies','snapshot_kind','guard_id','crossing_openness','fire','sha256'}
                or v['version']!=VERSION or v['sha256']!=_digest({k:x for k,x in v.items() if k!='sha256'})
                or v['source_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                or v['dependencies']!=dependency_identity()
                or type(expected_guard_id) is not str or not expected_guard_id or v['guard_id']!=expected_guard_id):
            raise ValueError('seam checkpoint identity differs')
        values=v['crossing_openness'];openness=None
        if values is not None:
            if type(values) is not list or any(type(z) is not list or len(z)!=3 for z in values):
                raise ValueError('invalid crossing checkpoint')
            openness={(x,y):factor for x,y,factor in values}
            if len(openness)!=len(values):raise ValueError('duplicate crossing checkpoint')
        kind=v['snapshot_kind']
        if kind=='validated-mixed-backend-v1':
            if not callable(mixed_backend_factory) or fire_restore is not None:
                raise ValueError('validated mixed backend factory required')
            backend=mixed_backend_factory()
            from wildfire_expanded_incident_v1.mixed_v2 import InteriorMixedFireV2
            if not isinstance(backend,InteriorMixedFireV2):raise TypeError('invalid mixed backend factory')
            backend.restore(v['fire']);fire=backend._fire
        elif kind=='containment-day-fire-v1':
            if not callable(fire_restore) or mixed_backend_factory is not None:
                raise ValueError('validated containment fire restore required')
            backend=None;fire=fire_restore(v['fire'])
        else:raise ValueError('unsupported seam snapshot kind')
        instance=cls(fire,support_guard=support_guard,guard_id=expected_guard_id,
                     crossing_openness=openness,mixed_backend=backend)
        if not instance._supported():raise ValueError('restored fire is unsupported')
        if _digest(instance.checkpoint())!=_digest(v):raise ValueError('seam checkpoint restoration differs')
        return instance
