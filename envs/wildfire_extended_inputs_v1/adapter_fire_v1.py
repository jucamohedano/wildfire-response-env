"""Synthetic-first expanded-grid backend for the pinned containment fire step.

Private snapshots and results contain no sea/unknown placeholder cells. This
is a fire/containment backend, not an incident-day host or route planner.
"""
import copy
import hashlib
import math
from pathlib import Path
import random

if __package__:
    from . import adapter_v1 as seam
else:
    import adapter_v1 as seam

from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.scenarios import PROFILE
from envs.wildfire_response.regional.prepared.data import FUEL_MAPPING
from simulator.engine import Simulation
from wildfire_containment_v1.fire import ContainmentEdgeFireStep
from wildfire_containment_v1.incident_day import CORE, _check_runtime, _source_digest
from wildfire_containment_v1.kernel import ContainmentKernel

VERSION = 'wildfire-expanded-containment-fire-v1'
_SPREAD_STEP = ContainmentEdgeFireStep.step
_SPREAD_INTERVAL = Simulation.spread_interval
_BURNING = Simulation.burning


def _source():
    _check_runtime()
    if (ContainmentEdgeFireStep.step is not _SPREAD_STEP or Simulation.spread_interval is not _SPREAD_INTERVAL
            or Simulation.burning is not _BURNING or
            _SPREAD_STEP.__module__ != 'wildfire_containment_v1.fire' or
            _SPREAD_STEP.__qualname__ != 'ContainmentEdgeFireStep.step'):
        raise RuntimeError('patched fire kernel is unsupported')
    if implementation_digest() != CORE:
        raise RuntimeError('unreviewed incident-day core')
    return seam._hash(dict(core=CORE, containment=_source_digest(),
        adapter=seam._source_pin(), fire=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))


def _tuple(value):
    return tuple(_tuple(x) for x in value) if isinstance(value, (list, tuple)) else value


class _Fire(ContainmentEdgeFireStep):
    """Minimal state expected by the existing containment spread implementation."""
    burning = staticmethod(Simulation.burning)

    def spread_interval(self, dx, dy):
        return Simulation.spread_interval(self, dx, dy) * self.spread_multiplier


class ExpandedContainmentFireV1(seam.ExtendedContainmentBackendV1):
    """Private trusted-driver backend. Every step has a sticky censor guard.

    Direct ``step`` and ``change`` return False if their post-check censors;
    subsequent mutations and outcomes raise. The seam subclass below propagates
    those reasons to the outer guard and raises on the same call.
    """
    def __init__(self, inputs, *, seed, containment_config, pressure_scale, wind):
        if not isinstance(inputs, seam._contract_module.Contract):
            raise TypeError('validated extended inputs required')
        if inputs.transform[0] != 500:
            raise ValueError('the pinned slope kernel requires 500-metre cells')
        if type(seed) is not int:
            raise ValueError('integer seed required')
        if type(pressure_scale) not in (int, float) or not math.isfinite(pressure_scale) or pressure_scale <= 0:
            raise ValueError('positive finite pressure scale required')
        if (type(wind) not in (list, tuple) or len(wind) != 2 or
                any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 3 for x in wind)):
            raise ValueError('wind must have two finite components in [-3, 3]')
        kernel = ContainmentKernel(**containment_config)
        self._inputs = inputs
        self._identity = dict(version=VERSION, source=_source(), input=inputs.document_sha256,
                              seed=seed, config=copy.deepcopy(kernel.config),
                              pressure_scale=float(pressure_scale), wind=list(wind))
        self._censor = seam.Censor(inputs)
        self._poisoned = False
        self._fire = self._new_fire(kernel, seed, pressure_scale, wind)

    def _new_fire(self, kernel, seed, pressure_scale, wind):
        f = _Fire()
        f.width, f.height = self._inputs.width, self._inputs.height
        f.cells, f.heights = [], []
        for y in range(f.height):
            cells, heights = [], []
            for x in range(f.width):
                supported_land = self._is_land(x, y)
                if supported_land:
                    mapped = FUEL_MAPPING[self._inputs.fuel[y][x]]
                    fuel, terrain = mapped['initial_fuel'], mapped['terrain']
                    height = self._inputs.elevation[y][x]
                else:
                    # The pinned kernel only reads heat/wet globally; no burning
                    # source can approach unknown cells. Confirmed sea is truly
                    # nonburnable. Heights remain None and are never passed to
                    # edge_multiplier for any sentinel target.
                    fuel, terrain, height = 0., None, None
                cells.append(dict(terrain=terrain, fuel=fuel, initial_fuel=fuel,
                                  heat=0., age=0, wet=0, burned=0.))
                heights.append(height)
            f.cells.append(cells)
            f.heights.append(heights)
        f.tick = 0
        f.rng = random.Random(seed)
        f.wind = tuple(wind)
        f.rules = dict(spread_factor=.5, intensity_growth_per_step=.04,
                       burn_duration_multiplier=PROFILE['burn_duration_multiplier'],
                       ignition_probability_per_eligible_cell=.5)
        f.spread_multiplier = PROFILE['spread_interval_multiplier']
        f.slope_enabled = True
        f.groups, f.scouts, f.extinguishers, f.trucks = {}, [], [], []
        f._containment = kernel
        f._pressure_scale = float(pressure_scale)
        f._assignments, f._suppressing, f._last_costs = [], [], []
        f.boundary_contact_at, f.satellite, f.satellite_queue = None, None, []
        return f

    def _is_land(self, x, y):
        i = self._inputs
        return i.fire_support[y][x] and i.surface[y][x] == 'land'

    @property
    def identity(self):
        return copy.deepcopy(self._identity)

    @property
    def reasons(self):
        return self._censor.reasons

    @property
    def censored(self):
        return self._censor.censored

    def _ensure(self):
        if self._poisoned:
            raise RuntimeError('backend indeterminate; restore a trusted checkpoint')
        try:
            if _source() != self._identity['source']:
                raise RuntimeError('fire implementation changed')
        except BaseException:
            self._poisoned = True
            raise

    def burning(self):
        self._ensure()
        return [[x, y] for y, row in enumerate(self._fire.cells)
                for x, cell in enumerate(row) if self._fire.burning(cell)]

    def _check(self):
        # Sentinels must never become burning or enter physics through mutation.
        for y, row in enumerate(self._fire.cells):
            for x, cell in enumerate(row):
                if not self._is_land(x, y) and cell != dict(terrain=None, fuel=0., initial_fuel=0.,
                                                            heat=0., age=0, wet=0, burned=0.):
                    raise RuntimeError('non-land sentinel changed')
        return self._censor.check(self.burning())

    def change(self, command):
        self._ensure()
        try:
            if not self._check():
                raise seam.CensoredError('episode already censored')
            if (type(command) is not dict or set(command) != {'ignite'} or
                    type(command['ignite']) is not list):
                raise ValueError('change requires an ignite coordinate list')
            points = command['ignite']
            # Check the proposed entire fire before writing even one cell.
            if not self._censor.check(self.burning() + points):
                return False
            for x, y in points:
                cell = self._fire.cells[y][x]
                if cell['fuel'] <= 0:
                    raise ValueError('cannot ignite exhausted fuel')
            for x, y in points:
                self._fire.cells[y][x].update(heat=.25, age=0)
            return self._check()
        except seam.CensoredError:
            raise
        except BaseException:
            self._poisoned = True
            raise

    def step(self, command=None):
        self._ensure()
        try:
            if not self._check():
                raise seam.CensoredError('episode already censored')
            if command is not None and command != {}:
                raise ValueError('this backend does not support assignments or suppression')
            # No operational ledger is present: only empty containment ticks are
            # accepted until a host can apply and verify resource costs atomically.
            self._fire._assignments, self._fire._suppressing = [], []
            self._fire.step(1)
            if self._fire.tick != self._fire._containment._minute:
                raise RuntimeError('fire and containment clocks differ')
            return self._check()
        except seam.CensoredError:
            raise
        except BaseException:
            self._poisoned = True
            raise

    def outcomes(self):
        self._ensure()
        try:
            safe = self._check()
            result = dict(tick=self._fire.tick, burning=self.burning()) if safe else None
        except BaseException:
            self._poisoned = True
            raise
        if not safe:
            raise seam.CensoredError('outcomes suppressed for censored episode')
        return result

    def snapshot(self):
        self._ensure()
        try:
            self._check()
            f = self._fire
            # Unsupported and sea cells are represented only by the input
            # contract, never serialized as invented fuel, terrain or height.
            land = [[x, y, copy.deepcopy(f.cells[y][x])] for y in range(f.height)
                    for x in range(f.width) if self._is_land(x, y)]
            return dict(version=VERSION, identity=self.identity, reasons=list(self.reasons),
                        tick=f.tick, land=land, rng=copy.deepcopy(f.rng.getstate()),
                        containment=f._containment.checkpoint(), boundary=f.boundary_contact_at,
                        satellite=copy.deepcopy(f.satellite), queue=copy.deepcopy(f.satellite_queue))
        except BaseException:
            self._poisoned = True
            raise

    def restore(self, snapshot):
        try:
            value = copy.deepcopy(snapshot)
            if (type(value) is not dict or set(value) != {'version', 'identity', 'reasons', 'tick', 'land', 'rng',
                                                          'containment', 'boundary', 'satellite', 'queue'}
                    or value['version'] != VERSION or seam._encoded(value['identity']) != seam._encoded(self._identity)
                    or _source() != self._identity['source']):
                raise ValueError('backend snapshot identity differs')
            reasons = value['reasons']
            if (type(reasons) is not list or reasons != sorted(set(reasons))
                    or not set(reasons) <= seam._REASONS):
                raise ValueError('invalid censor reasons')
            kernel = ContainmentKernel.restore(value['containment'])
            if kernel.config != self._identity['config'] or type(value['tick']) is not int or value['tick'] != kernel._minute:
                raise ValueError('backend clocks or configuration differ')
            fresh = self._new_fire(kernel, self._identity['seed'], self._identity['pressure_scale'], self._identity['wind'])
            expected = [(x, y) for y in range(fresh.height) for x in range(fresh.width) if self._is_land(x, y)]
            land = value['land']
            if type(land) is not list or len(land) != len(expected):
                raise ValueError('invalid land state')
            for entry, point in zip(land, expected):
                if type(entry) is not list or len(entry) != 3 or entry[:2] != list(point) or type(entry[2]) is not dict:
                    raise ValueError('invalid land state')
                x, y = point
                original, cell = fresh.cells[y][x], entry[2]
                if (set(cell) != set(original) or cell['terrain'] != original['terrain']
                        or cell['initial_fuel'] != original['initial_fuel']
                        or any(type(cell[k]) not in (int, float) or not math.isfinite(cell[k]) or cell[k] < 0
                               for k in ('fuel', 'heat', 'burned'))
                        or cell['fuel'] > original['initial_fuel'] or cell['heat'] > 1 or cell['burned'] > 1
                        or any(type(cell[k]) is not int or cell[k] < 0 for k in ('age', 'wet'))):
                    raise ValueError('invalid land cell')
                fresh.cells[y][x] = copy.deepcopy(cell)
            fresh.rng.setstate(_tuple(value['rng']))
            fresh.tick, fresh.boundary_contact_at = value['tick'], value['boundary']
            fresh.satellite, fresh.satellite_queue = value['satellite'], value['queue']
            censor = seam.Censor(self._inputs)
            censor._reasons.update(reasons)
            censor.check([[x, y] for y, row in enumerate(fresh.cells) for x, cell in enumerate(row) if fresh.burning(cell)])
            if censor.reasons != tuple(reasons):
                raise ValueError('snapshot omits censor reasons')
            self._fire, self._censor, self._poisoned = fresh, censor, False
        except BaseException:
            self._poisoned = True
            raise


class ExpandedContainmentSeamV1(seam.CensoredContainmentSeamV1):
    """Propagate backend censor reasons, including rejected proposed ignition."""
    def _check(self):
        self._censor._reasons.update(self._backend.reasons)
        return super()._check()
