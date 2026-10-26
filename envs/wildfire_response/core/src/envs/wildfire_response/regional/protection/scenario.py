"""Protection extension; the regional kernel and Spanish v1 remain unchanged."""
import copy

from ..grounded.scenario import GroundedScenarioEnv
from .protocol import ProtectionProtocol
from .world import ProtectionWorld


VERSION = 'wildfire-protection-scenario-v1'


class ProtectionScenarioEnv(GroundedScenarioEnv):
    def reset(self, scenario_id, seed=None, *, scenario=None):
        if not isinstance(scenario, dict) or 'protection' not in scenario:
            raise ValueError('Expected a trusted protection development scenario')
        configuration = copy.deepcopy(scenario['protection'])
        self.protection = ProtectionProtocol(configuration)
        observation, info = super().reset(scenario_id, seed, scenario=scenario)
        self.sim = ProtectionWorld(self.sim, configuration)
        return self.observe(), info

    def _validate(self, action):
        if not isinstance(action, dict):
            raise ValueError('Expected a structured action')
        legacy = {key: value for key, value in action.items() if key != 'protection_orders'}
        super()._validate(legacy)
        if any(message['recipient'] in self.sim.groups and message['purpose'] != 'inform'
               for message in action.get('messages', [])):
            raise ValueError('Population protection needs versioned protection_orders, not legacy evacuation messages')
        self.protection.validate(action.get('protection_orders', []), self, action)

    def _apply(self, action):
        super()._apply({key: value for key, value in action.items() if key != 'protection_orders'})
        self.protection.issue(action.get('protection_orders', []), self)

    def _deliver(self):
        self.protection.deliver(self)
        super()._deliver()

    def _resources(self):
        occupied = [unit for identifier, unit in self.resources.items()
                    if self.protection.warning_busy.get(identifier, -1) > self.sim.tick]
        for unit in occupied:
            unit['state'] = 'warning_service'
        try:
            super()._resources()
        finally:
            for unit in occupied:
                unit['state'] = 'on_scene'

    def observe(self):
        public = super().observe()
        public.update(self.protection.public_state())
        if isinstance(self.sim, ProtectionWorld):
            state = self.sim.population_state()
            for district in public['districts']:
                district.update(state[district['id']])
        return public
