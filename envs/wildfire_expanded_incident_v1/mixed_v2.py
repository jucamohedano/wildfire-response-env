"""Opt-in, private mixed-surface fire experiment; distinct from v1 physics."""
import copy
from dataclasses import dataclass, field
import hashlib
import importlib
import math
from pathlib import Path
import sys

import adapter_v1 as api
import adapter_fire_v1 as fire_v1
from wildfire_containment_v1.fire import _paths
from envs.wildfire_response.regional.topographic.fire import BOUNDARY_GUARD_CELLS, NEIGHBOURS, edge_multiplier
from simulator.terrain import PROFILES
from wildfire_containment_v1.kernel import ContainmentKernel
from .source_converter_v1 import _road
from . import source_gate_v1

INPUT_VERSION = 'wildfire-interior-mixed-input-v2'
FIRE_VERSION = 'wildfire-interior-mixed-fire-v2'
_VERIFIED = object()


def _file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _compiler():
    root = Path(__file__).resolve().parents[2]
    eval_dir = str(root / 'eval')
    # The map module uses repository-local absolute imports; do not edit it.
    if eval_dir not in sys.path:
        sys.path.insert(0, eval_dir)
    module = importlib.import_module('extended_map_resolved_v1')
    if Path(module.__file__).resolve() != (root / 'eval/extended_map_resolved_v1.py').resolve():
        raise ImportError('unexpected mixed-evidence validator')
    return module


def _validator_source(module):
    common = importlib.import_module('extended_map_common')
    expected = Path(module.__file__).with_name('extended_map_common.py').resolve()
    if Path(common.__file__).resolve() != expected:
        raise ImportError('unexpected mixed-evidence digest implementation')
    paths = {'compiler': module.__file__, 'common': common.__file__,
             'map': module.base.__file__, 'terrain': module.terrain.__file__,
             'access': module.access.__file__,
             'domain': Path(module.__file__).with_name('extended_domain_v1.py'),
             'drafts': Path(module.__file__).with_name('extended_scenario_drafts_v1.py')}
    return api._hash({name: _file_sha(path) for name, path in paths.items()})


@dataclass(frozen=True)
class MixedInputsV2:
    contract: object
    factors: tuple
    identity: tuple
    _verified: object = field(repr=False, compare=False)


def prepare_mixed_inputs(strict, water_v2, audit, hypotheses, *, strict_pin, water_v2_pin,
                         audit_pin, hypothesis_pin, trusted_audit_manifest, road_path, road_pin, scenario,
                         opt_in_mixed_experiment=False):
    """Reconstruct validated hypotheses, preserving every other v2 unknown.

    scenario is exactly low, nominal or high, an explicit uncalibrated model
    assumption. Fuel estimates and operational access remain unsupported.
    """
    if opt_in_mixed_experiment is not True:
        raise ValueError('explicit mixed-experiment opt-in required')
    source_gate_v1.require_validated_mixed_audit(strict_pin, water_v2_pin, audit_pin, trusted_audit_manifest)
    gate_pin = _file_sha(source_gate_v1.__file__)
    field = {'low': 'low_assumption', 'nominal': 'nominal_sample_assumption',
             'high': 'high_assumption'}.get(scenario)
    if field is None:
        raise ValueError('choose low, nominal or high')
    compiler = _compiler()
    compiler_pin = _validator_source(compiler)
    rebuilt = compiler.compile_interior_mixed(strict, water_v2, audit, strict_pin=strict_pin,
                                              v2_pin=water_v2_pin, audit_pin=audit_pin)
    if (_validator_source(compiler) != compiler_pin or _file_sha(source_gate_v1.__file__) != gate_pin
            or type(hypotheses) is not dict
            or hypotheses.get('sha256') != hypothesis_pin
            or api._hash({k: v for k, v in hypotheses.items() if k != 'sha256'}) != hypothesis_pin
            or api._encoded(rebuilt) != api._encoded(hypotheses)):
        raise ValueError('mixed hypotheses differ from independently reconstructed evidence')
    if (water_v2.get('version') != 'extended-map-resolution-evidence-v2'
            or water_v2.get('status') != 'private_evidence_not_simulator_input'):
        raise ValueError('expected strict water-conflict resolution v2')
    road = _road(road_path, road_pin)
    grid = water_v2['grid']
    if grid.get('crs') != road['crs'] or grid.get('bounds') != road['bounds']:
        raise ValueError('road and water grids differ')
    w, h = grid['width'], grid['height']
    selected = {}
    for cell in rebuilt['cells']:
        x, y = cell['column'], cell['row']
        factor = cell['modeled_initial_fuel_factor'][field]
        if type(factor) not in (int, float) or not math.isfinite(factor) or not 0 < factor <= 1:
            raise ValueError('invalid mixed fuel assumption')
        openness = cell['sample_nonwater_fraction']
        if type(openness) not in (int, float) or not math.isfinite(openness) or not 0 < openness < 1:
            raise ValueError('mixed cell requires a positive sampled water share')
        selected[(x, y)] = (float(factor), float(openness))
    surface, fuel, elevation = [], [], []
    for y in range(h):
        ss, fs, es = [], [], []
        for x in range(w):
            cell = water_v2['cells'][y][x]
            status, original_surface = cell['fuel_status'], cell['surface']
            if original_surface not in ('land', 'sea', 'unknown') or status not in ('source', 'estimated', 'unknown'):
                raise ValueError('invalid v2 source classification')
            source = cell['source_fuel_class']
            if status == 'source':
                if type(source) is not int or not 1 <= source <= 10:
                    raise ValueError('invalid source fuel')
            elif source is not None:
                raise ValueError('non-source fuel cannot be promoted')
            if (x, y) in selected and (original_surface != 'unknown' or status != 'source'):
                raise ValueError('mixed cell must have validated source fuel')
            ss.append('mixed' if (x, y) in selected else original_surface)
            fs.append(source if status == 'source' else None)
            es.append(None if original_surface == 'sea' else water_v2['terrain_candidate'][y][x])
        surface.append(ss); fuel.append(fs); elevation.append(es)
    pins = dict(surface=water_v2_pin, fuel=water_v2_pin, elevation=water_v2_pin, access=road_pin)
    doc = dict(version=api._contract_module.VERSION,
               grid={k: copy.deepcopy(grid[k]) for k in ('width', 'height', 'crs', 'transform')},
               domain_mask=[[True] * w for _ in range(h)], surface=surface, fuel=fuel,
               elevation=elevation, access=[['unknown'] * w for _ in range(h)], source_pins=pins)
    contract = api.load_inputs(doc, api._hash(doc), expected_source_pins=pins, opt_in=True)
    identity = dict(version=INPUT_VERSION, strict=strict_pin, water_v2=water_v2_pin,
                    audit=audit_pin, hypotheses=hypothesis_pin, road=road_pin,
                    compiler=compiler_pin, source_gate=source_gate_v1.VERSION, source_gate_sha256=gate_pin,
                    audit_trust_manifest=source_gate_v1.MIXED_AUDIT_MANIFEST_SHA256,
                    scenario=scenario, input_sha256=contract.document_sha256,
                    crossing_rule='mixed-sample-share-product-exercise-v1')
    return MixedInputsV2(contract, tuple((x, y, *selected[(x, y)]) for x, y in sorted(selected)),
                         tuple(sorted(identity.items())), _VERIFIED)


class _MixedCensor(api.Censor):
    def __init__(self, contract, allowed):
        super().__init__(contract)
        self._allowed = frozenset(allowed)

    def _supported(self, x, y):
        c = self._contract
        return (c.fire_support[y][x] or ((x, y) in self._allowed and c.domain[y][x]
                and c.surface[y][x] == 'mixed' and c.fuel[y][x] is not None
                and c.elevation[y][x] is not None))

    def check(self, burning):
        try:
            points = api._contract_module._positions(burning, self._contract)
        except (TypeError, ValueError):
            self._reasons.add('invalid_fire_observation')
            raise
        for x, y in points:
            if not self._supported(x, y) or self._contract.surface[y][x] not in ('land', 'mixed'):
                self._reasons.add('invalid_burning_cell')
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    a, b = x + dx, y + dy
                    if not (0 <= a < self._contract.width and 0 <= b < self._contract.height):
                        self._reasons.add('grid_boundary')
                    elif not self._supported(a, b):
                        self._reasons.add('unknown_fire_state')
        return not self.censored


class _MixedFire(fire_v1._Fire):
    """Pinned spread step plus an explicit mixed-cell crossing assumption."""
    def step(self, count=1):
        if self.groups or self.scouts or self.extinguishers or self.trucks:
            raise RuntimeError('Topographic kernel requires regional-only actors')
        for _ in range(count):
            self.tick += 1
            candidates, attacks = [], {}
            for row, cells in enumerate(self.cells):
                for column, cell in enumerate(cells):
                    wet = cell.get('wet', 0)
                    cell['wet'] = max(0, wet - 1)
                    if not self.burning(cell):
                        continue
                    profile = PROFILES[cell.get('terrain', 'field')]
                    cell['age'] += 1
                    if not wet:
                        cell['heat'] = min(1.0, cell['heat'] + self.rules['intensity_growth_per_step'])
                    if cell['fuel'] < .2:
                        cell['heat'] = min(cell['heat'], max(.05, cell['fuel'] / .2))
                    consumed = min(cell['fuel'], max(.15, cell['heat']) / (profile['duration'] * self.rules['burn_duration_multiplier']))
                    cell['fuel'] = max(0.0, cell['fuel'] - consumed)
                    cell['burned'] = min(1.0, cell.get('burned', 0) + consumed)
                    for dx, dy in NEIGHBOURS:
                        x, y = column + dx, row + dy
                        if not (0 <= x < self.width and 0 <= y < self.height):
                            continue
                        target = self.cells[y][x]
                        if target['fuel'] <= 0 or target['heat'] > 0 or target.get('wet', 0) > 0:
                            continue
                        if dx and dy and (self.cells[row][x]['fuel'] <= 0 or self.cells[y][column]['fuel'] <= 0):
                            continue
                        distance = math.hypot(dx, dy)
                        interval = self.spread_interval(dx / distance, dy / distance)
                        if cell['age'] % interval:
                            continue
                        base = self.rules['ignition_probability_per_eligible_cell'] * PROFILES[target.get('terrain', 'field')]['spread'] * cell['heat'] / distance
                        chance = min(1.0, base * edge_multiplier(self.heights, (column, row), (x, y), enabled=self.slope_enabled))
                        # Explicit exercise proxy, not a measured open edge.
                        touched = {(column, row), (x, y)}
                        if dx and dy:
                            touched.update(((x, row), (column, y)))
                        for point in sorted(touched):
                            chance *= self._mixed_openness.get(point, 1.0)
                        paths = _paths((column, row), (x, y))
                        pressure = cell['heat'] * self._pressure_scale
                        candidates.append(((x, y), chance, paths))
                        for path in paths:
                            for crossing in path:
                                attacks[crossing] = max(attacks.get(crossing, 0.), pressure)
                    if cell['fuel'] <= 0:
                        cell['heat'] = 0.0
            result = self._containment.tick(self._containment._minute + 1,
                assignments=self._assignments, suppressing=self._suppressing,
                attacks=[dict(source=a, target=b, pressure=p) for (a, b), p in sorted(attacks.items())],
                wind=list(self.wind))
            self._last_costs = result['costs']
            blocked = {(tuple(item['source']), tuple(item['target'])): item['blocked'] for item in result['crossings']}
            ignitions = {}
            for target, chance, paths in candidates:
                allowed = any(all(not blocked[edge] for edge in path) for path in paths)
                ignitions[target] = max(ignitions.get(target, 0), chance if allowed else 0)
            for (column, row), chance in sorted(ignitions.items()):
                if self.rng.random() < chance:
                    self.cells[row][column].update(heat=.25, age=0)
            if self.boundary_contact_at is None and any(self.burning(cell) for row, cells in enumerate(self.cells)
                for column, cell in enumerate(cells) if row < BOUNDARY_GUARD_CELLS or row >= self.height - BOUNDARY_GUARD_CELLS
                or column < BOUNDARY_GUARD_CELLS or column >= self.width - BOUNDARY_GUARD_CELLS):
                self.boundary_contact_at = self.tick
            if self.tick % 12 == 0:
                blocks = sorted({(column // 8 * 8, row // 8 * 8) for row, cells in enumerate(self.cells)
                                 for column, cell in enumerate(cells) if self.burning(cell)})
                self.satellite_queue.append(dict(captured_at=self.tick, available_at=self.tick + 12, blocks=blocks))
            while self.satellite_queue and self.satellite_queue[0]['available_at'] <= self.tick:
                self.satellite = self.satellite_queue.pop(0)


class InteriorMixedFireV2(fire_v1.ExpandedContainmentFireV1):
    """Fractional initial-fuel experiment using the pinned containment stencil.

    Water geometry and edge-blocking are not modeled. The selected factor is a
    scenario assumption, not an inferred field measurement or safety guarantee.
    """
    def __init__(self, inputs, *, seed, containment_config, pressure_scale, wind,
                 opt_in_mixed_experiment=False):
        if (opt_in_mixed_experiment is not True or not isinstance(inputs, MixedInputsV2)
                or inputs._verified is not _VERIFIED):
            raise ValueError('validated v2 inputs and explicit opt-in required')
        self._mixed_factors = {(x, y): value for x, y, value, _ in inputs.factors}
        self._mixed_openness = {(x, y): value for x, y, _, value in inputs.factors}
        self._mixed_identity = dict(inputs.identity)
        super().__init__(inputs.contract, seed=seed, containment_config=containment_config,
                         pressure_scale=pressure_scale, wind=wind)
        self._censor = _MixedCensor(self._inputs, self._mixed_factors)
        self._identity.update(version=FIRE_VERSION, mixed=copy.deepcopy(self._mixed_identity),
                              mixed_backend_sha256=_file_sha(__file__))

    def _ensure(self):
        super()._ensure()
        converter_changed = False
        if self._mixed_identity.get('converter_sha256'):
            if self._mixed_identity.get('converter_kind') == 'v4':
                from . import exercise_v4 as converter
            else:
                from . import exercise_v3 as converter
            converter_changed = self._mixed_identity['converter_sha256'] != _file_sha(converter.__file__)
        # Validation is complete before the immutable input is constructed.
        # Retain its validator hash for replay, but later edits to the source
        # evidence compiler cannot change an already-running fire step.
        if self._identity['mixed_backend_sha256'] != _file_sha(__file__) or converter_changed:
            self._poisoned = True
            raise RuntimeError('mixed backend source changed')

    def _is_land(self, x, y):
        return super()._is_land(x, y) or ((x, y) in self._mixed_factors
                and self._inputs.domain[y][x] and self._inputs.surface[y][x] == 'mixed'
                and self._inputs.fuel[y][x] is not None and self._inputs.elevation[y][x] is not None)

    def _new_fire(self, kernel, seed, pressure_scale, wind):
        base = super()._new_fire(kernel, seed, pressure_scale, wind)
        f = _MixedFire()
        f.__dict__.update(base.__dict__)
        f._mixed_openness = copy.deepcopy(self._mixed_openness)
        for (x, y), factor in self._mixed_factors.items():
            cell = f.cells[y][x]
            cell['fuel'] *= factor
            cell['initial_fuel'] *= factor
        return f

    def snapshot(self):
        value = super().snapshot()
        value['version'] = FIRE_VERSION
        return value

    def restore(self, snapshot):
        try:
            v = copy.deepcopy(snapshot)
            if (type(v) is not dict or set(v) != {'version', 'identity', 'reasons', 'tick', 'land', 'rng',
                                                  'containment', 'boundary', 'satellite', 'queue'}
                    or v['version'] != FIRE_VERSION or api._encoded(v['identity']) != api._encoded(self._identity)
                    or fire_v1._source() != self._identity['source']
                    or _file_sha(__file__) != self._identity['mixed_backend_sha256']):
                raise ValueError('mixed checkpoint identity differs')
            reasons = v['reasons']
            if type(reasons) is not list or reasons != sorted(set(reasons)) or not set(reasons) <= api._REASONS:
                raise ValueError('invalid mixed censor state')
            kernel = ContainmentKernel.restore(v['containment'])
            if kernel.config != self._identity['config'] or type(v['tick']) is not int or v['tick'] != kernel._minute:
                raise ValueError('mixed clocks or configuration differ')
            f = self._new_fire(kernel, self._identity['seed'], self._identity['pressure_scale'], self._identity['wind'])
            coords = [(x, y) for y in range(f.height) for x in range(f.width) if self._is_land(x, y)]
            if type(v['land']) is not list or len(v['land']) != len(coords):
                raise ValueError('mixed land state differs')
            for item, (x, y) in zip(v['land'], coords):
                if type(item) is not list or len(item) != 3 or item[:2] != [x, y] or type(item[2]) is not dict:
                    raise ValueError('invalid mixed cell state')
                old, cell = f.cells[y][x], item[2]
                if (set(cell) != set(old) or cell['terrain'] != old['terrain']
                        or cell['initial_fuel'] != old['initial_fuel']
                        or any(type(cell[k]) not in (int, float) or not math.isfinite(cell[k]) or cell[k] < 0
                               for k in ('fuel', 'heat', 'burned'))
                        or cell['fuel'] > old['initial_fuel'] or cell['heat'] > 1 or cell['burned'] > 1
                        or any(type(cell[k]) is not int or cell[k] < 0 for k in ('age', 'wet'))):
                    raise ValueError('invalid mixed cell')
                f.cells[y][x] = copy.deepcopy(cell)
            f.rng.setstate(fire_v1._tuple(v['rng']))
            f.tick, f.boundary_contact_at = v['tick'], v['boundary']
            f.satellite, f.satellite_queue = v['satellite'], v['queue']
            censor = _MixedCensor(self._inputs, self._mixed_factors)
            censor._reasons.update(reasons)
            censor.check([[x, y] for y, row in enumerate(f.cells) for x, cell in enumerate(row) if f.burning(cell)])
            if censor.reasons != tuple(reasons):
                raise ValueError('mixed checkpoint omits censor reasons')
            self._fire, self._censor, self._poisoned = f, censor, False
            if api._encoded(self.snapshot()) != api._encoded(v):
                raise ValueError('mixed checkpoint restoration differs')
        except BaseException:
            self._poisoned = True
            raise
