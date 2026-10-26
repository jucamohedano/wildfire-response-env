"""Fallible shelter effects in abstract exercise units, never medical predictions."""
import copy
import math


class ProtectionWorld:
    def __init__(self, simulation, configuration):
        object.__setattr__(self, '_simulation', simulation)
        object.__setattr__(self, '_configuration', copy.deepcopy(configuration))
        object.__setattr__(self, '_dose', {identifier: 0 for identifier in simulation.groups})
        object.__setattr__(self, '_sheltered', set())
        object.__setattr__(self, '_smoke_present', self._detect_smoke())
        object.__setattr__(self, '_smoke_observed_at', simulation.tick)
        object.__setattr__(self, '_history', [])

    def __getattr__(self, name):
        return getattr(self._simulation, name)

    def __setattr__(self, name, value):
        setattr(self._simulation, name, value)

    def execute_protection(self, recipient, action):
        group = self.groups[recipient]
        if group['burnt']:
            return False, 'population_already_exposed'
        at_home = math.dist([group['x'], group['y']], group['home']) < 0.01
        if action == 'shelter':
            if not at_home or group['status'] in ('evacuating', 'blocked', 'safe'):
                return False, 'not_at_home_no_teleport'
            already_sheltered = recipient in self._sheltered
            self._sheltered.add(recipient)
            group['status'] = 'sheltering'
            return True, 'shelter_continues' if already_sheltered else 'shelter_started'
        if action == 'release':
            if group['status'] in ('evacuating', 'blocked'):
                return False, 'evacuation_movement_continues'
            was_sheltered = recipient in self._sheltered
            self._sheltered.discard(recipient)
            if group['status'] == 'sheltering':
                group['status'] = 'unwarned'
            return True, 'shelter_released_without_movement' if was_sheltered else 'no_shelter_to_release'
        if action == 'evacuate':
            self._sheltered.discard(recipient)
            if group['status'] == 'safe':
                return True, 'already_at_refuge'
            if group['status'] in ('evacuating', 'blocked'):
                return True, 'evacuation_continues'
            group['status'] = 'evacuating'
            return True, 'evacuation_started'
        raise ValueError('Unknown protection action')

    def step(self, count=1):
        for _ in range(count):
            self._simulation.step()
            self._smoke_present.update(self._detect_smoke())
            object.__setattr__(self, '_smoke_observed_at', self.tick)
            for identifier, group in self.groups.items():
                smoke = self._smoke_present[identifier]
                if group['burnt']:
                    self._sheltered.discard(identifier)
                    continue
                if smoke:
                    self._dose[identifier] += self._configuration[
                        'sheltered_dose_per_tick' if identifier in self._sheltered else 'open_dose_per_tick']
                if self._dose[identifier] >= self._configuration['exposure_threshold']:
                    group['burnt'] = group['count']
                    group['status'] = 'burnt'
                    self._sheltered.discard(identifier)
                    self._simulation.log('people', f'{identifier}: synthetic smoke-dose threshold reached')
            self._history.append(dict(minute=self.tick, dose=copy.deepcopy(self._dose),
                                      sheltered=sorted(self._sheltered), smoke=copy.deepcopy(self._smoke_present)))

    def population_state(self):
        return {identifier: dict(smoke_dose_units=self._dose[identifier],
                                shelter_active=identifier in self._sheltered,
                                smoke_present=self._smoke_present[identifier],
                                smoke_observed_at=self._smoke_observed_at) for identifier in self.groups}

    def _detect_smoke(self):
        burning = [(column, row) for row, cells in enumerate(self.cells)
                   for column, cell in enumerate(cells) if self.burning(cell)]
        return {identifier: any(math.dist([group['x'], group['y']], point)
                                <= self._configuration['smoke_radius_cells'] for point in burning)
                for identifier, group in self.groups.items()}

    def population_safe_now(self):
        return all(not group['burnt'] and group['status'] not in ('evacuating', 'blocked')
                   and not self._smoke_present[identifier] for identifier, group in self.groups.items())

    def protection_checkpoint(self):
        return dict(configuration=copy.deepcopy(self._configuration), history=copy.deepcopy(self._history),
                    population=self.population_state(),
                    basis='Synthetic cumulative dose; no medical or building-protection calibration')
