"""Reuse the existing seeded fire kernel, without demo vehicles or completion rules."""
import math

from simulator.engine import Simulation
from .data import geometry


class RegionalFire(Simulation):
    def __init__(self, pack, seed):
        super().__init__(seed=seed, incident_id=f'regional-v2:{seed}',
                         fleet_counts=dict(scouts=0, extinguishers=0, trucks=1))
        self.scouts, self.extinguishers, self.trucks = [], [], []
        self.base = (0, 0)
        self.roads = set()
        grid = geometry(pack)
        self.width, self.height = grid['width'], grid['height']
        self.cells = [[dict(terrain='scrub', fuel=1.0, initial_fuel=1.0, heat=0.0, age=0, wet=0, burned=0.0)
                       for column in range(self.width)] for row in range(self.height)]
        self.memory, self.observation, self.groups = {}, [], {}
        self.drone.update(x=0.0, y=0.0)
        self.truck.update(x=0.0, y=0.0)
        self.spread_multiplier = pack['assumptions']['spread_interval_multiplier']
        self.population_speed = pack['assumptions']['population_speed_cells_per_tick']
        self.ignited = self.called = True
        for identifier, fraction in [('settlement-west', .4), ('settlement-east', .7)]:
            home = [int(self.width*fraction), self.height//2]
            population = pack['assumptions']['synthetic_population_counts'][identifier]
            self.groups[identifier] = dict(name=identifier, kind='town', count=population, burnt=0,
                x=float(home[0]), y=float(home[1]), home=home,
                refuge=[home[0], min(self.height-2,home[1]+pack['assumptions']['synthetic_refuge_offset_cells'])], status='unwarned')

    def update_completion(self):
        pass

    def observe(self):
        return self.observation

    def spread_interval(self, dx, dy):
        return super().spread_interval(dx, dy) * self.spread_multiplier

    def step(self, count=1):
        for _ in range(count):
            groups, self.groups = self.groups, {}
            try:
                super().step()
            finally:
                self.groups = groups
            self.update_people_exposure()
            for group in self.groups.values():
                if group['status'] not in ('evacuating', 'blocked'):
                    continue
                distance = math.dist([group['x'], group['y']], group['refuge'])
                fraction = min(1.0, self.population_speed/max(distance, 1e-9))
                column = group['x'] + fraction*(group['refuge'][0]-group['x'])
                row = group['y'] + fraction*(group['refuge'][1]-group['y'])
                if self.burning(self.cells[round(row)][round(column)]):
                    group['status'] = 'blocked'
                else:
                    group.update(x=column, y=row, status='safe' if distance <= self.population_speed else 'evacuating')
            self.update_people_exposure()
