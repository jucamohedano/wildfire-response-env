"""Source-informed transit assumptions and simulated caller location follow-ups."""
import copy

from ..ops.scenario_core import ScenarioResponseEnv


VERSION = 'wildfire-grounded-scenario-v1'


class GroundedScenarioEnv(ScenarioResponseEnv):
    def reset(self, scenario_id='es-dev-000-v0', seed=None, *, scenario=None):
        if not isinstance(scenario, dict) or 'grounded' not in scenario:
            raise ValueError('A trusted grounded development configuration is required')
        self._grounded = copy.deepcopy(scenario['grounded'])
        self._clarifications = {}
        return super().reset(scenario_id, seed, scenario=scenario)

    def _travel(self, unit, target):
        if unit['kind'] == 'aircraft':
            return super()._travel(unit, target)
        offset = 0 if unit['home_base'] == 'valverde' else 6
        return self._grounded['ground_transit_minutes'] + offset + abs(target[0] - self.sim.width // 2) // 4

    def _events(self, minute):
        super()._events(minute)
        hour = ((self._grounded['local_clock_minute'] + minute) // 60) % 24
        self.daylight = 7 <= hour < 20

    def _validate(self, action):
        if not isinstance(action, dict):
            raise ValueError('Expected a structured action')
        super()._validate({key: value for key, value in action.items() if key != 'clarifications'})
        requests = action.get('clarifications', [])
        if not isinstance(requests, list) or len(requests) > 4:
            raise ValueError('At most four report clarification IDs per action')
        seen = set()
        for report_id in requests:
            if (not isinstance(report_id, str) or report_id in seen or report_id in self._clarifications
                    or report_id not in self.ledger or self.ledger[report_id].get('location') is not None):
                raise ValueError('Clarify each received unlocated report at most once')
            seen.add(report_id)

    def _apply(self, action):
        super()._apply({key: value for key, value in action.items() if key != 'clarifications'})
        for report_id in action.get('clarifications', []):
            due = self.sim.tick + self._grounded['clarification_delay_minutes']
            self._clarifications[report_id] = dict(report_id=report_id, issued_at=self.sim.tick,
                due_at=due, status='pending', completed_at=None, response_report_id=None)
            self.queue.append(dict(kind='clarification', due=due, data={'report_id': report_id}))
            self._ack('director', self.ledger[report_id]['source'], 'clarification_requested', report_id=report_id)

    def _deliver(self):
        for item in [entry for entry in self.queue if entry['kind'] == 'clarification'
                     and entry['due'] <= self.sim.tick]:
            self.queue.remove(item)
            report_id = item['data']['report_id']
            record = self._clarifications[report_id]
            point = self._grounded['clarification_locations'].get(report_id)
            record.update(status='unresolved' if point is None else 'answered', completed_at=self.sim.tick)
            if point is not None:
                original = self.ledger[report_id]
                identifier = 'followup-' + report_id
                self.report_events.append(dict(id=identifier, observed_at=original['observed_at'],
                    received_at=self.sim.tick, location=point[:], source=original['source'],
                    source_role=original.get('source_role', 'resident'), channel='synthetic_phone',
                    text='La ubicación que recuerdo es aproximada. Mi aviso sigue sin verificar.'))
                record['response_report_id'] = identifier
            self._ack(self.ledger[report_id]['source'], 'director', 'clarification_' + record['status'],
                      report_id=report_id, response_report_id=record['response_report_id'])
        super()._deliver()

    def observe(self):
        public = super().observe()
        public['clarification_requests'] = copy.deepcopy(list(self._clarifications.values()))
        public['assumptions']['travel'] = (
            'Ground transit is an assumed fraction of a historical response duration, not measured routing. '
            'Authorization, mobilization and policy delay still determine actual arrival.')
        return public
