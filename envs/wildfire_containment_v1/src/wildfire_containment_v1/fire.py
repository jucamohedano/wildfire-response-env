"""Opt-in derivative of the incident-day topographic fire step.

The frozen upstream implementation has no candidate hook; this version copies
its step and filters eligible source/target candidates before aggregation.
"""
import copy
import math

from envs.wildfire_response.incident_day.scenarios import DayFire
from envs.wildfire_response.regional.topographic.fire import (
    BOUNDARY_GUARD_CELLS, NEIGHBOURS, EdgeFireStep, edge_multiplier,
)
from simulator.engine import Simulation
from simulator.terrain import PROFILES

from .kernel import ContainmentKernel, _digest, _edge, _number

FIRE_VERSION = 'wildfire-containment-day-fire-v1'


def _paths(source, target):
    x, y = source
    a, b = target
    if x == a or y == b:
        return [[(source, target)]]
    horizontal = (a, y)
    vertical = (x, b)
    return [[(source, horizontal), (horizontal, target)],
            [(source, vertical), (vertical, target)]]


class ContainmentEdgeFireStep(EdgeFireStep):
    """Versioned EdgeFireStep variant; called through RegionalFire's step."""
    def step(self, count=1):
        if self.groups or self.scouts or self.extinguishers or self.trucks:
            raise RuntimeError('Topographic kernel requires regional-only actors')
        for _ in range(count):
            self.tick += 1
            candidates = []
            attacks = {}
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
            blocked = {(tuple(item['source']), tuple(item['target'])): item['blocked']
                       for item in result['crossings']}
            # Keep all targets so blocked candidates still consume their original draw.
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


class ContainmentDayFire(DayFire, ContainmentEdgeFireStep):
    """DayFire with an explicitly provided containment kernel and pressure scale."""
    def __init__(self, inputs, seed, *, containment, pressure_scale):
        super().__init__(inputs, seed)
        self._install(containment, pressure_scale)

    def _install(self, containment, pressure_scale):
        if not isinstance(containment, ContainmentKernel) or containment._minute != 0:
            raise ValueError('A fresh containment kernel is required')
        self._pressure_scale = _number(pressure_scale, 'pressure_scale')
        self._containment = ContainmentKernel.restore(containment.checkpoint())
        self._start_tick = self.tick
        self._assignments = []
        self._suppressing = []
        self._last_costs = []

    @classmethod
    def from_day_fire(cls, fire, *, containment, pressure_scale):
        """Copy a DayFire at a trusted handover, including its RNG state."""
        if type(fire) is not DayFire:
            raise ValueError('Expected an unmodified DayFire')
        instance = Simulation.restore.__func__(cls, fire.checkpoint())
        instance._install(containment, pressure_scale)
        return instance

    def step_with_containment(self, *, assignments=(), suppressing=()):
        # Validate before fire, fuel, or RNG state changes.
        if not isinstance(assignments, (list, tuple)):
            raise ValueError('assignments must be a sequence')
        for item in assignments:
            if not isinstance(item, dict):
                raise ValueError('Invalid assignment')
            for x, y in _edge(item.get('edge')):
                if not (0 <= x < self.width and 0 <= y < self.height):
                    raise ValueError('Line edge outside fire grid')
        probe = ContainmentKernel.restore(self._containment.checkpoint())
        probe.tick(probe._minute + 1, assignments=assignments, attacks=[],
                   wind=list(self.wind), suppressing=suppressing)
        self._assignments = copy.deepcopy(assignments)
        self._suppressing = copy.deepcopy(suppressing)
        try:
            super().step(1)
        finally:
            self._assignments = []
            self._suppressing = []
        return copy.deepcopy(self._last_costs)

    def step(self, count=1):
        if type(count) is not int or count < 0:
            raise ValueError('count must be a nonnegative integer')
        for _ in range(count):
            self.step_with_containment()

    def observe_line(self, edge, *, status):
        """Queue a trusted driver's field observation; no truth is exposed."""
        for x, y in _edge(edge):
            if not (0 <= x < self.width and 0 <= y < self.height):
                raise ValueError('Line edge outside fire grid')
        self._containment.observe(edge, status=status)

    def reports(self):
        return self._containment.reports()

    def checkpoint(self):
        if self._assignments or self._suppressing:
            raise RuntimeError('Checkpoint only at a completed step boundary')
        state = super().checkpoint()
        for key in ('_containment', '_pressure_scale', '_start_tick', '_assignments', '_suppressing', '_last_costs'):
            state['state'].pop(key)
        value = dict(version=FIRE_VERSION, fire=state, containment=self._containment.checkpoint(),
                     pressure_scale=self._pressure_scale, start_tick=self._start_tick,
                     last_costs=copy.deepcopy(self._last_costs))
        value['sha256'] = _digest(value)
        return value

    @classmethod
    def restore(cls, checkpoint):
        value = copy.deepcopy(checkpoint)
        if (not isinstance(value, dict) or value.get('version') != FIRE_VERSION
                or value.get('sha256') != _digest({k: v for k, v in value.items() if k != 'sha256'})):
            raise ValueError('Fire checkpoint identity or integrity differs')
        instance = Simulation.restore.__func__(cls, value['fire'])
        instance._containment = ContainmentKernel.restore(value['containment'])
        instance._pressure_scale = _number(value['pressure_scale'], 'pressure_scale')
        instance._start_tick = value['start_tick']
        if instance.tick - instance._start_tick != instance._containment._minute:
            raise ValueError('Fire and containment clocks differ')
        instance._assignments, instance._suppressing = [], []
        instance._last_costs = value['last_costs']
        return instance
