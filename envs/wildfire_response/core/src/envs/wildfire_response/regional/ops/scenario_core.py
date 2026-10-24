"""Scenario overlays for transport and initial commitments over the frozen kernel."""
import copy
import hashlib

from ..data import PACK
from ..environment import RegionalResponseEnv, VERSION, REWARD_VERSION
from ..scenarios import get_scenario
from .generator import digest


SCENARIO_ENGINE_VERSION = 'wildfire-ops-scenario-core-v1'


class ScenarioResponseEnv(RegionalResponseEnv):
    def reset(self, scenario_id='regional-000', seed=None, *, scenario=None):
        configuration = copy.deepcopy(scenario) if scenario is not None else get_scenario(scenario_id)
        self._transport = configuration.get('transport', {})
        self._deliveries = {}
        self._attempts = {}
        held = self._transport.get('resource_unavailable_until', {})
        configuration['aircraft_ready_at'] = max(configuration['aircraft_ready_at'],
                                                held.get('aircraft-las-arenas', 0))
        return super().reset(scenario_id, seed, scenario=configuration)

    def _events(self, minute):
        super()._events(minute)
        for identifier, ready_at in self._transport.get('resource_unavailable_until', {}).items():
            unit = self.resources[identifier]
            if unit['kind'] == 'aircraft':
                continue
            if minute == 0 and ready_at > 0:
                unit['state'] = 'unavailable'
            elif minute >= ready_at and unit['state'] == 'unavailable':
                unit['state'] = 'available'
                self._ack(unit['home_base'], 'director', 'availability_update', resource_id=identifier)

    def _apply(self, action):
        before = len(self.queue)
        super()._apply(action)
        for item in self.queue[before:]:
            if item['kind'] == 'request':
                item['due'] += self._transport.get('request_extra_delay_minutes', 0)
            elif item['kind'] == 'message':
                item['due'] += self._transport.get('message_extra_delay_minutes', 0)
                message = item['data']
                key = (message['recipient'], message['purpose'])
                attempt = self._attempts.get(key, 0) + 1
                self._attempts[key] = attempt
                identifier = f'delivery-{len(self._deliveries) + 1}'
                item['message_id'] = identifier
                item['will_fail'] = (self._transport.get('fail_first_evacuation', False)
                                     and message['purpose'] == 'evacuate' and attempt == 1)
                self._deliveries[identifier] = dict(id=identifier, **message, attempt=attempt,
                    issued_at=self.sim.tick, due_at=item['due'], status='pending', completed_at=None)

    def _deliver(self):
        successful = []
        for item in [entry for entry in self.queue if entry['due'] <= self.sim.tick
                     and entry['kind'] == 'message']:
            record = self._deliveries[item['message_id']]
            if item['will_fail']:
                self.queue.remove(item)
                record.update(status='failed', completed_at=self.sim.tick)
                self._ack(record['recipient'], 'director', 'message_failed', purpose=record['purpose'],
                          message_id=record['id'], reason='simulated_link_failure')
            else:
                successful.append(record)
        before = len(self.acks)
        super()._deliver()
        acknowledgements = [item for item in self.acks[before:] if item['kind'] == 'message_delivered']
        if len(acknowledgements) != len(successful):
            raise RuntimeError('Message delivery accounting mismatch')
        for record, acknowledgement in zip(successful, acknowledgements):
            record.update(status='delivered', completed_at=self.sim.tick)
            acknowledgement['message_id'] = record['id']

    def observe(self):
        observation = super().observe()
        observation['message_deliveries'] = copy.deepcopy(list(self._deliveries.values()))
        observation['pending_resource_requests'] = sorted(self.pending_requests)
        return observation


def replay_core(trajectory, task):
    if (trajectory['version'] != VERSION or trajectory['reward_version'] != REWARD_VERSION
            or trajectory['scenario_engine_version'] != SCENARIO_ENGINE_VERSION
            or trajectory['pack_sha256'] != hashlib.sha256(PACK.read_bytes()).hexdigest()):
        raise ValueError('Ops core replay version or data mismatch')
    env = ScenarioResponseEnv(max_decisions=trajectory['max_decisions'],
                              ticks_per_action=trajectory['ticks_per_action'])
    observation, _ = env.reset(task['scenario_id'], trajectory['seed'], scenario=task.get('scenario'))
    if observation != trajectory['initial_observation'] or digest(env.task) != trajectory['scenario_sha256']:
        raise ValueError('Ops core initial scenario differs')
    total = 0.0
    for recorded in trajectory['transitions']:
        result = env.step(recorded['action'])
        actual = dict(observation=result[0], reward=result[1], terminated=result[2], truncated=result[3], info=result[4])
        if actual != {key: recorded[key] for key in actual}:
            raise ValueError('Ops core trajectory differs')
        total += result[1]
    if abs(total - trajectory['total_reward']) > 1e-9:
        raise ValueError('Ops core total reward differs')
    return total
