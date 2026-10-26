"""Directional edge-height modifier for a complete, bounded experimental surface."""
import math

from simulator.engine import Simulation
from simulator.terrain import PROFILES
from ..prepared.environment import PreparedFire


KERNEL_VERSION = 'wildfire-topographic-probability-v1'
COUPLING = dict(beta=3.0, grade_clip=.5, physical_calibration=False,
                modifies='eligible ignition probability, not wind interval or ground transit')
NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
BOUNDARY_GUARD_CELLS = 2


def edge_grade(heights, source, target, cell_metres=500):
    if any(type(coordinate) is not int for point in (source, target) for coordinate in point):
        raise ValueError('Expected integer cell coordinates')
    horizontal = math.dist(source, target)
    if not 0 < horizontal <= math.sqrt(2) or cell_metres <= 0:
        raise ValueError('Choose distinct adjacent cells')
    for column, row in (source, target):
        if not 0 <= row < len(heights) or not 0 <= column < len(heights[row]):
            raise ValueError('Edge escapes supported terrain')
        value = heights[row][column]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Both edge heights are required')
    return (heights[target[1]][target[0]] - heights[source[1]][source[0]]) / (cell_metres * horizontal)


def edge_multiplier(heights, source, target, *, enabled):
    grade = edge_grade(heights, source, target)
    if type(enabled) is not bool:
        raise ValueError('Choose slope enabled/disabled explicitly')
    return math.exp(COUPLING['beta'] * max(-COUPLING['grade_clip'], min(COUPLING['grade_clip'], grade))) if enabled else 1.0


class EdgeFireStep(Simulation):
    """Cooperative kernel: RegionalFire.step still owns population movement.

    TopographicFire's MRO places this between RegionalFire and Simulation. The
    inherited regional step temporarily removes groups before calling this
    method. Demo vehicles are absent; operational units remain managed by the
    existing scenario protocol, never moved or suppressed here.
    """

    def step(self, count=1):
        if self.groups or self.scouts or self.extinguishers or self.trucks:
            raise RuntimeError('Topographic kernel requires regional-only actors')
        for iteration in range(count):
            self.tick += 1
            ignitions = {}
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
                    for delta_column, delta_row in NEIGHBOURS:
                        target_column, target_row = column + delta_column, row + delta_row
                        if not (0 <= target_column < self.width and 0 <= target_row < self.height):
                            continue
                        target = self.cells[target_row][target_column]
                        if target['fuel'] <= 0 or target['heat'] > 0 or target.get('wet', 0) > 0:
                            continue
                        if delta_column and delta_row and (self.cells[row][target_column]['fuel'] <= 0 or self.cells[target_row][column]['fuel'] <= 0):
                            continue
                        distance = math.hypot(delta_column, delta_row)
                        interval = self.spread_interval(delta_column / distance, delta_row / distance)
                        if cell['age'] % interval:
                            continue
                        base = self.rules['ignition_probability_per_eligible_cell'] * PROFILES[target.get('terrain', 'field')]['spread'] * cell['heat'] / distance
                        chance = min(1.0, base * edge_multiplier(self.heights, (column, row), (target_column, target_row), enabled=self.slope_enabled))
                        ignitions[(target_column, target_row)] = max(ignitions.get((target_column, target_row), 0), chance)
                    if cell['fuel'] <= 0:
                        cell['heat'] = 0.0
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


class TopographicFire(PreparedFire, EdgeFireStep):
    def __init__(self, pack, seed, inputs, heights, *, slope_enabled):
        super().__init__(pack, seed, inputs)
        self.heights = heights
        self.slope_enabled = slope_enabled
        self.boundary_contact_at = None
