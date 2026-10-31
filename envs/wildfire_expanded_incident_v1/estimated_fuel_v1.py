"""Private opt-in estimated fuel physics layered on independently gated v4.

The original v1 input contract is never modified: estimated classes live only
in this version's private layer and are not source measurements or actor output.
"""
import copy
from dataclasses import dataclass, field
import math
from pathlib import Path

import adapter_v1 as api
import adapter_fire_v1 as fire_v1
from wildfire_containment_v1.kernel import ContainmentKernel
from .mixed_v2 import InteriorMixedFireV2, MixedInputsV2, _MixedCensor, _VERIFIED, _file_sha
from .exercise_v4 import prepare_exercise_v4

VERSION = 'wildfire-mixed-estimated-fuel-input-v1'
FIRE_VERSION = 'wildfire-mixed-estimated-fuel-fire-v1'
# Independently supplied canonical identity of the full-source-reconstructed
# private document; a caller cannot admit a forged self-hashed replacement.
HYPOTHESIS_SHA256 = '341e10b987522f8e5e86b2afa092d0ffc2ccdc2fc248a0478c8bfa61fe50be3d'
_VERIFIED_ESTIMATE = object()


@dataclass(frozen=True)
class EstimatedInputsV1:
    base: MixedInputsV2
    estimates: tuple  # (x, y, class, fuel factor, crossing proxy)
    source_mask: tuple
    estimated_mask: tuple
    identity: tuple
    _verified: object = field(repr=False, compare=False)


def prepare_estimated_inputs(strict, water, overlay_v3, overlay_v4, parent, catalog, hypothesis, *,
                             fuel_scenario, opt_in_estimated_fuel=False, **v4_options):
    """Run the v4 geographic gate, then admit only the pinned private hypothesis.

    v4_options is exactly prepare_exercise_v4's keyword interface; its mixed
    scenario may be fixed while fuel_scenario varies for paired sensitivity.
    """
    if opt_in_estimated_fuel is not True:
        raise ValueError('explicit estimated-fuel experiment opt-in required')
    if fuel_scenario not in ('low', 'nominal', 'high'):
        raise ValueError('choose low, nominal or high fuel assumption')
    if (type(hypothesis) is not dict or hypothesis.get('version') != 'mixed-estimated-fuel-hypotheses-v1'
            or hypothesis.get('status') != 'private_sensitivity_evidence_not_runtime_input'
            or hypothesis.get('sha256') != HYPOTHESIS_SHA256
            or api._hash({k:v for k,v in hypothesis.items() if k != 'sha256'}) != HYPOTHESIS_SHA256):
        raise ValueError('estimated-fuel hypothesis not admitted by independent pin')
    base = prepare_exercise_v4(strict, water, overlay_v3, overlay_v4, parent, catalog, **v4_options)
    pins=hypothesis['source_pins']
    identity=dict(base.identity)
    if (any(pins.get(k)!=identity[v] for k,v in (('strict','strict'),('water_v2','water_v2'),
            ('exercise_v3','overlay_v3'),('exercise_v4','overlay_v4'),('dera_rivers_response_sha256','dera_rivers')))
            or hypothesis['grid'] != water['grid']):
        raise ValueError('estimated hypothesis source lineage differs from independently validated maps')
    c=base.contract; estimates={}
    for record in hypothesis['cells']:
        x,y=record['column'],record['row']
        if (type(x) is not int or type(y) is not int or not 0<=x<c.width or not 0<=y<c.height
                or (x,y) in estimates):
            raise ValueError('invalid estimated-cell coordinates')
        original=strict['cells'][y][x]; current=water['cells'][y][x]
        evidence=record['surface_evidence']; h=record['fuel_hypothesis']
        chosen=h['scenarios'][fuel_scenario]
        klass=chosen['assumed_fuel_class']; factor=chosen['fuel_factor_assumption']
        if (original['surface']!='land' or current['surface']!='unknown'
                or current['source_fuel_class'] is not None or original['source_fuel_class'] is not None
                or current['fuel_status']!='unknown' or c.surface[y][x]!='unknown' or c.fuel[y][x] is not None
                or c.elevation[y][x] is None or c.elevation[y][x]!=record['terrain_candidate']
                or record['source_fuel_class'] is not None or record['surface']!='mixed_exercise_assumption'
                or not evidence['both_land_faces_and_coastal_water_polygon_screen']
                or evidence['osm_water_area_conflicts'] != 0 or not evidence['watercourse_evidence_present']
                or current['worldcover_samples'] != record['worldcover_samples']
                or h['confidence']!='low_uncalibrated' or h['status'] not in ('estimated_hypothesis','ambiguous_sensitivity_hypothesis')
                or type(klass) is not int or klass not in fire_v1.FUEL_MAPPING
                or type(factor) not in (float,int) or not math.isfinite(factor) or not 0<factor<=1
                or chosen['assumed_terrain']!=fire_v1.FUEL_MAPPING[klass]['terrain']
                or chosen['exercise_base_initial_fuel']!=fire_v1.FUEL_MAPPING[klass]['initial_fuel']
                or not math.isclose(chosen['modeled_initial_fuel'],factor*chosen['exercise_base_initial_fuel'],abs_tol=1e-12)):
            raise ValueError('estimated cell fails source or physical assumption checks')
        estimates[(x,y)]=(klass,float(factor),identity['crossing_proxy'])
    if not estimates:raise ValueError('empty estimated-fuel hypothesis')
    source=tuple(tuple(water['cells'][y][x]['fuel_status']=='source' for x in range(c.width)) for y in range(c.height))
    estimated=tuple(tuple((x,y) in estimates for x in range(c.width)) for y in range(c.height))
    ident=dict(version=VERSION,base=identity,hypothesis_sha256=HYPOTHESIS_SHA256,
               fuel_scenario=fuel_scenario,converter_sha256=_file_sha(__file__),
               source_mask_sha256=api._hash(source),estimated_mask_sha256=api._hash(estimated))
    return EstimatedInputsV1(base,tuple((x,y,*estimates[(x,y)]) for x,y in sorted(estimates)),
                             source,estimated,tuple(sorted(ident.items())),_VERIFIED_ESTIMATE)


class _EstimatedCensor(_MixedCensor):
    def __init__(self, contract, mixed, estimated):
        super().__init__(contract,mixed)
        self._estimated=frozenset(estimated)

    def _supported(self,x,y):
        return super()._supported(x,y) or ((x,y) in self._estimated and self._contract.domain[y][x]
               and self._contract.surface[y][x]=='unknown' and self._contract.fuel[y][x] is None
               and self._contract.elevation[y][x] is not None)

    def check(self,burning):
        try: points=api._contract_module._positions(burning,self._contract)
        except (TypeError,ValueError):
            self._reasons.add('invalid_fire_observation');raise
        for x,y in points:
            if not self._supported(x,y) or (self._contract.surface[y][x] not in ('land','mixed')
                                             and (x,y) not in self._estimated):
                self._reasons.add('invalid_burning_cell')
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    a,b=x+dx,y+dy
                    if not (0<=a<self._contract.width and 0<=b<self._contract.height):
                        self._reasons.add('grid_boundary')
                    elif not self._supported(a,b):self._reasons.add('unknown_fire_state')
        return not self.censored


class EstimatedMixedFireV1(InteriorMixedFireV2):
    """Unscored private fire with explicit low-confidence input hypotheses."""
    def __init__(self, inputs, *, seed, containment_config, pressure_scale, wind, opt_in_estimated_fuel=False):
        if (opt_in_estimated_fuel is not True or not isinstance(inputs,EstimatedInputsV1)
                or inputs._verified is not _VERIFIED_ESTIMATE):
            raise ValueError('validated estimated inputs and explicit opt-in required')
        self._estimated={(x,y):(klass,factor,openness) for x,y,klass,factor,openness in inputs.estimates}
        self._building_base=False
        self._estimate_identity=dict(inputs.identity)
        super().__init__(inputs.base,seed=seed,containment_config=containment_config,
                         pressure_scale=pressure_scale,wind=wind,opt_in_mixed_experiment=True)
        self._censor=_EstimatedCensor(self._inputs,self._mixed_factors,self._estimated)
        self._identity.update(version=FIRE_VERSION,estimated=copy.deepcopy(self._estimate_identity),
                              estimated_backend_sha256=_file_sha(__file__))

    def _ensure(self):
        super()._ensure()
        if _file_sha(__file__)!=self._identity['estimated_backend_sha256']:
            self._poisoned=True;raise RuntimeError('estimated-fuel backend source changed')

    def _is_land(self,x,y):
        return super()._is_land(x,y) or (not self._building_base and (x,y) in self._estimated)

    def _new_fire(self,kernel,seed,pressure_scale,wind):
        self._building_base=True
        try: f=super()._new_fire(kernel,seed,pressure_scale,wind)
        finally: self._building_base=False
        for (x,y),(klass,factor,openness) in self._estimated.items():
            mapped=fire_v1.FUEL_MAPPING[klass]
            value=mapped['initial_fuel']*factor
            f.cells[y][x].update(terrain=mapped['terrain'],fuel=value,initial_fuel=value)
            f.heights[y][x]=self._inputs.elevation[y][x]
            f._mixed_openness[(x,y)]=openness
        return f

    def snapshot(self):
        value=super().snapshot();value['version']=FIRE_VERSION;return value

    def restore(self,snapshot):
        # Validate via the existing v2 restore using an equivalent censor class;
        # no global patching and no alteration of the v2 implementation.
        try:
            v=copy.deepcopy(snapshot)
            expected={'version','identity','reasons','tick','land','rng','containment','boundary','satellite','queue'}
            if (type(v) is not dict or set(v)!=expected or v['version']!=FIRE_VERSION
                    or api._encoded(v['identity'])!=api._encoded(self._identity)):
                raise ValueError('estimated checkpoint identity differs')
            # A trusted checkpoint is the recovery path after an indeterminate
            # mutation; still verify every source pin before installing it.
            self._poisoned=False
            self._ensure()
            reasons=v['reasons']
            if type(reasons) is not list or reasons!=sorted(set(reasons)) or not set(reasons)<=api._REASONS:
                raise ValueError('invalid estimated censor state')
            kernel=ContainmentKernel.restore(v['containment'])
            if kernel.config!=self._identity['config'] or type(v['tick']) is not int or v['tick']!=kernel._minute:
                raise ValueError('estimated fire clock differs')
            f=self._new_fire(kernel,self._identity['seed'],self._identity['pressure_scale'],self._identity['wind'])
            coords=[(x,y) for y in range(f.height) for x in range(f.width) if self._is_land(x,y)]
            if type(v['land']) is not list or len(v['land'])!=len(coords):raise ValueError('estimated land state differs')
            for item,(x,y) in zip(v['land'],coords):
                if type(item) is not list or len(item)!=3 or item[:2]!=[x,y] or type(item[2]) is not dict:
                    raise ValueError('invalid estimated cell state')
                old,cell=f.cells[y][x],item[2]
                if (set(cell)!=set(old) or cell['terrain']!=old['terrain'] or cell['initial_fuel']!=old['initial_fuel']
                    or any(type(cell[k]) not in (int,float) or not math.isfinite(cell[k]) or cell[k]<0 for k in ('fuel','heat','burned'))
                    or cell['fuel']>old['initial_fuel'] or cell['heat']>1 or cell['burned']>1
                    or any(type(cell[k]) is not int or cell[k]<0 for k in ('age','wet'))):
                    raise ValueError('invalid estimated cell')
                f.cells[y][x]=copy.deepcopy(cell)
            f.rng.setstate(fire_v1._tuple(v['rng']));f.tick=v['tick'];f.boundary_contact_at=v['boundary']
            f.satellite,f.satellite_queue=v['satellite'],v['queue']
            censor=_EstimatedCensor(self._inputs,self._mixed_factors,self._estimated);censor._reasons.update(reasons)
            censor.check([[x,y] for y,row in enumerate(f.cells) for x,cell in enumerate(row) if f.burning(cell)])
            if censor.reasons!=tuple(reasons):raise ValueError('estimated checkpoint omits censor reasons')
            self._fire,self._censor,self._poisoned=f,censor,False
            if api._encoded(self.snapshot())!=api._encoded(v):raise ValueError('estimated restore differs')
        except BaseException:
            self._poisoned=True;raise
