"""Opt-in coastal road/flight exercises; existing benchmark implementations are untouched."""
import copy
from functools import lru_cache
import hashlib
import math
from pathlib import Path

from envs.wildfire_response.regional.grounded.environment import replay_development_trajectory
from envs.wildfire_response.regional.incident.environment import (
    ReactiveOpsEnv, ReactiveScenarioEnv, get_task as incident_task,
    implementation_digest as incident_digest,
)
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.regional.protection.environment import ProtectionBaseline
from .mobility_routing import RoadNetwork, journey_position, make_journey, make_leg


VERSION = 'wildfire-coastal-mobility-development-v1'
CASE_IDS = ('coastal-dev-000', 'coastal-dev-001', 'coastal-dev-002')
AIRFIELD = [366040.059, 4059866.949]
STAGING = {'valverde': [307629.249, 4033428.189], 'cabezudos': [296199.169, 4035654.952]}
STAGING_NAMES = {'valverde': 'Estepona exercise staging', 'cabezudos': 'Casares exercise staging',
                 'las-arenas': 'Málaga airport · exercise aircraft staging'}


def implementation_digest():
    root = Path(__file__).parent
    return digest(dict(parent=incident_digest(), files={name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                  for name in ('mobility.py', 'mobility_routing.py')}))


class MobilityScenarioEnv(ReactiveScenarioEnv):
    def __init__(self, inputs, domain, *, network, base_nodes=None, **kwargs):
        self.network = network
        self._outgoing = lru_cache(maxsize=512)(self._plan_outgoing)
        self.base_nodes = ({key: network.nearest(position, connected=True) for key, position in STAGING.items()}
                           if base_nodes is None else dict(base_nodes))
        if set(self.base_nodes) != set(STAGING) or any(node not in network.nodes for node in self.base_nodes.values()):
            raise ValueError('Both exercise ground staging nodes must exist in the road snapshot')
        super().__init__(inputs, domain, **kwargs)

    def reset(self, scenario_id, seed=None, *, scenario=None):
        self._outgoing.cache_clear()
        self.journeys, self.telemetry, self.telemetry_queue = {}, {}, []
        self.movement_serial = 0
        super().reset(scenario_id, seed, scenario=scenario)
        self._sample_movement(initial=True)
        return self.observe(), self._info()

    def _native(self, target):
        affine = self.inputs['grid']['transform']
        return [affine[2] + (target[0] + .5) * affine[0], affine[5] + (target[1] + .5) * affine[4]]

    def _origin(self, unit):
        return AIRFIELD[:] if unit['kind'] == 'aircraft' else self.network.nodes[self.base_nodes[unit['home_base']]][:]

    def _plan_outgoing(self, identifier, column, row):
        unit = self.resources[identifier]
        target = self._native([column, row])
        if unit['kind'] == 'aircraft':
            return make_journey([make_leg('flight', [AIRFIELD, target], 240)], AIRFIELD, target,
                                'Synthetic direct flight at 240 km/h; no airspace or flight-planning claim')
        return self.network.approach(self.base_nodes[unit['home_base']], target,
                                     walk=unit['kind'] == 'crew', max_access_m=1500 if unit['kind'] == 'crew' else 150)

    def _returning(self, unit):
        previous = self.journeys.get(unit['id'])
        if previous is None:
            return make_journey([], self._origin(unit), self._origin(unit), 'Already at exercise staging')
        if unit['kind'] == 'aircraft':
            position = journey_position(previous, self.sim.tick)[0]
            return make_journey([make_leg('flight', [position, AIRFIELD], 240)], position, AIRFIELD,
                                'Synthetic direct return to exercise airfield at 240 km/h')
        return self.network.return_journey(previous, self.sim.tick, self.base_nodes[unit['home_base']])

    def _travel(self, unit, target):
        plan = (self._outgoing(unit['id'], *target) if unit['state'] in ('available', 'reserved', 'mobilizing')
                else self._returning(unit))
        return plan['duration_minutes']

    def _validate(self, action):
        super()._validate(action)
        for request in action.get('requests', []):
            target = self._sectors()[request['sector_id']]['center']
            self._outgoing(request['resource_id'], *target)
        for identifier in action.get('releases', []):
            if self.resources[identifier]['state'] != 'reserved':
                self._returning(self.resources[identifier])

    def _launch(self, identifier, plan, direction):
        self.movement_serial += 1
        self.journeys[identifier] = dict(copy.deepcopy(plan), route_id=f'route-{self.movement_serial}',
            departed_at=self.sim.tick, arrives_at=self.sim.tick + plan['duration_minutes'], direction=direction)

    def _apply(self, action):
        returning = {identifier: self._returning(self.resources[identifier])
                     for identifier in action.get('releases', []) if self.resources[identifier]['state'] != 'reserved'}
        super()._apply(action)
        for identifier, plan in returning.items():
            self.resources[identifier]['due_at'] = self.sim.tick + plan['duration_minutes']
            self._launch(identifier, plan, 'return')

    def _resources(self):
        before = {identifier: unit['state'] for identifier, unit in self.resources.items()}
        outbound = {identifier: self._outgoing(identifier, *unit['target'])
                    for identifier, unit in self.resources.items()
                    if unit['state'] == 'mobilizing' and unit['due_at'] <= self.sim.tick}
        returning = {identifier: self._returning(unit) for identifier, unit in self.resources.items()
                     if unit['state'] in ('en_route', 'on_scene')}
        super()._resources()
        for identifier, unit in self.resources.items():
            if unit['state'] == 'en_route' and before[identifier] != 'en_route':
                self._launch(identifier, outbound[identifier], 'outbound')
            elif unit['state'] == 'returning' and before[identifier] != 'returning':
                self._launch(identifier, returning[identifier], 'return')
        self._sample_movement()

    def _sample_movement(self, *, initial=False):
        for identifier, unit in self.resources.items():
            route = self.journeys.get(identifier)
            position = journey_position(route, self.sim.tick)[0] if route else self._origin(unit)
            record = copy.deepcopy(unit)
            record.update(map_position=position, observed_at=self.sim.tick,
                received_at=self.sim.tick if initial else self.sim.tick + 1,
                travel_time_basis='Pinned road geometry and assumed speeds; timestamped exercise telemetry')
            record['movement'] = None if route is None else dict(copy.deepcopy(route),
                position=position, observed_at=record['observed_at'], received_at=record['received_at'],
                network_sha256=self.network.sha256, position_basis='Reported simulation position; not real GPS')
            record['arrival_at'] = (unit['due_at'] + self._travel(unit, unit['target'])
                                    if unit['state'] == 'mobilizing' else unit['due_at']
                                    if unit['state'] in ('en_route', 'returning') else None)
            record['arrival_in_minutes'] = (max(0, record['arrival_at'] - self.sim.tick)
                                            if record['arrival_at'] is not None else None)
            if initial:
                self.telemetry[identifier] = record
            else:
                self.telemetry_queue.append(record)

    def _receive_movement(self):
        for record in list(self.telemetry_queue):
            if record['received_at'] <= self.sim.tick:
                self.telemetry[record['id']] = record
                self.telemetry_queue.remove(record)

    def _receive(self):
        super()._receive()
        self._receive_movement()

    def _deliver(self):
        super()._deliver()
        self._receive_movement()

    def observe(self):
        public = super().observe()
        public['resources'] = [copy.deepcopy(self.telemetry.get(unit['id'], unit)) for unit in public['resources']]
        for base in public['bases']:
            base.update(name=STAGING_NAMES[base['id']], position=AIRFIELD[:] if base['id'] == 'las-arenas'
                        else self.network.nodes[self.base_nodes[base['id']]][:],
                        basis='Fictional staging allocation on real geography, not a historical deployment')
        options = []
        for sector in public['sectors']:
            for unit in public['resources']:
                try:
                    plan = self._outgoing(unit['id'], *sector['center'])
                    options.append(dict(resource_id=unit['id'], sector_id=sector['id'],
                        distance_m=round(plan['distance_m'], 2), travel_minutes=plan['duration_minutes']))
                except ValueError:
                    continue
        public['mobility'] = dict(version=VERSION, network_sha256=self.network.sha256,
            crs='EPSG:25830', telemetry_delay_minutes=1, route_options=options,
            basis='Source-grounded geography; synthetic staging, speeds, readiness and direct flight paths')
        public['assumptions']['travel'] = ('Ground units use a pinned directed road graph. Engines stop within150m '
            'of the assigned cell centre; crews may finish with an assumed straight walking leg up to1500m. '
            'Aircraft use synthetic direct flight legs. No real emergency access, traffic or airspace validation.')
        return public


class CoastalMobilityEnv(ReactiveOpsEnv):
    interface_version = VERSION
    task_version = VERSION + '-tasks'
    scenario_version = VERSION + '-scenario'

    def __init__(self, prepared_inputs, topographic_inputs, network, *, expected_network_sha256,
                 base_nodes=None, **kwargs):
        self.network = RoadNetwork(network, expected_network_sha256)
        self.base_nodes = base_nodes
        super().__init__(prepared_inputs, topographic_inputs, **kwargs)

    def _resolve_task(self, identifier):
        if identifier not in CASE_IDS:
            raise ValueError('Unknown coastal mobility development task')
        variant = CASE_IDS.index(identifier)
        task = incident_task(self._active_inputs, 'incident-dev-001' if variant == 2 else 'incident-dev-000')
        task.update(id=identifier, scenario_id=identifier, generator_version=self.task_version,
                    task_set='coastal_mobility_development')
        scenario = task['scenario']
        scenario.update(id=identifier, aircraft_ready_at=0, seed=881100 + variant)
        task['seed'] = scenario['seed']
        scenario['grounded']['local_clock_minute'] = 600 if variant == 0 else 1260
        scenario['transport']['resource_unavailable_until'] = {}
        grid = self._active_inputs['grid']
        candidates = []
        for row in range(3, grid['height'] - 3):
            for column in range(3, grid['width'] - 3):
                if not self._active_inputs['fuel_valid_mask'][row][column]:
                    continue
                position = [grid['transform'][2] + (column + .5) * 500,
                            grid['transform'][5] - (row + .5) * 500]
                node = self.network.nearest(position, connected=True)
                gap = math.dist(position, self.network.nodes[node])
                if gap <= 150:
                    candidates.append((math.dist([column, row], [grid['width']*.4, grid['height']*.5]), column, row))
        if not candidates:
            raise ValueError('The road snapshot has no validated interior truck-accessible exercise cells')
        _, column, row = min(candidates)
        location = [column, row]
        if variant != 2:
            scenario['events'] = [dict(tick=0, kind='ignition', location=location)]
        scenario['reports'][0]['location'] = location[:]
        return task

    def _task_manifest(self):
        return dict(version=self.task_version, split='development', network_sha256=self.network.sha256,
                    cases=list(CASE_IDS), base_nodes=self.base_nodes,
                    conditions='Synthetic daytime in000, night in001/002; historical weather is separate proxy context')

    def _new_core(self):
        return MobilityScenarioEnv(self._active_inputs, self._domain, network=self.network,
            base_nodes=self.base_nodes, slope_enabled=self._slope_enabled,
            max_decisions=self._max_decisions, ticks_per_action=self._ticks_per_action)

    def reset(self, task_id=CASE_IDS[0], seed=None):
        observation, info = super().reset(task_id, seed)
        self._ops_trajectory.update(implementation_sha256=implementation_digest(),
            mobility_version=VERSION, road_network_sha256=self.network.sha256,
            staging_nodes=copy.deepcopy(self._core.base_nodes))
        return observation, info

    def _present_episode(self, observation):
        public = super()._present_episode(observation)
        public['capabilities'].update(incident_specific_roads=True, reported_resource_positions=True)
        public['exercise_mode'] = VERSION
        public['assumptions']['inventory'] = ('Fictional exercise allocations at declared staging nodes. Málaga '
            'airport ARP is a geographic reference, not evidence of firefighting aircraft stationed there.')
        public['clock']['basis'] += '; daylight is an explicit exercise assumption; historical weather remains separate context'
        return public

    def _verify_public_trace(self, trajectory):
        return verify_mobility_trajectory(trajectory, self._inputs, self._domain, self.network.document,
                                          base_nodes=self.base_nodes)


def verify_mobility_trajectory(trajectory, prepared, domain, network, *, base_nodes=None):
    def create(unused_pack, **kwargs):
        return CoastalMobilityEnv(prepared, domain, network, expected_network_sha256=network['sha256'],
            expected_prepared_sha256=prepared['sha256'], expected_topographic_sha256=domain['sha256'],
            slope_enabled=trajectory['slope_enabled'], allow_synthetic_conditions=True, base_nodes=base_nodes, **kwargs)
    result = replay_development_trajectory(trajectory, prepared['episode_pack'], create)
    censored = trajectory['transitions'][-1]['observation']['topographic_status']['boundary_censored']
    result.update(boundary_censored=censored, suitable_for_training=not censored,
                  scope='Reproducible synthetic routed exercise, not validated emergency routing or historical operations')
    return result


class MobilityBaseline:
    def __init__(self, *, prefer_aircraft=False):
        self.control = ProtectionBaseline(prefer_shelter=True)
        self.prefer_aircraft = prefer_aircraft

    def __call__(self, observation):
        action = self.control(observation)
        options = {(item['resource_id'], item['sector_id']): item for item in observation['mobility']['route_options']}
        legal = set(options)
        roster = {unit['id']: unit for unit in observation['resources']}
        released = {ack['resource_id'] for ack in observation['acknowledgements']
                    if ack['kind'] == 'release_ordered' and ack.get('resource_id') in roster
                    and ack['minute'] >= (roster[ack['resource_id']].get('observed_at') or 0)}
        action['releases'] = [identifier for identifier in action.get('releases', []) if identifier not in released]
        held = set(observation['pending_resource_requests']) | set(action.get('releases', []))
        held.update(offer['resource_id'] for offer in observation['offers']
                    if offer['status'] == 'offered' and offer['expires_at'] > observation['minute'])
        held.update(offer['resource_id'] for offer in observation['offers']
                    if offer['id'] in action.get('authorizations', []))
        requests, selected = [], set()
        for request in action.get('requests', []):
            eligible = [unit for unit in observation['resources']
                        if unit['state'] == 'available' and unit['kind'] == roster[request['resource_id']]['kind']
                        and unit['id'] not in held and unit['id'] not in selected
                        and (unit['id'], request['sector_id']) in legal]
            if eligible:
                best = min(eligible, key=lambda unit: (options[unit['id'], request['sector_id']]['travel_minutes'], unit['id']))
                requests.append({**request, 'resource_id': best['id']})
                selected.add(best['id'])
        action['requests'] = requests
        available = [unit for unit in observation['resources'] if unit['kind'] == 'aircraft'
                     and unit['state'] == 'available' and observation['aircraft_operable']
                     and unit['id'] not in held]
        if self.prefer_aircraft and available and observation['reports'] and not any(
                item['resource_id'] == available[0]['id'] for item in action['requests']):
            report = observation['reports'][0]
            sector = 'report:' + report['id']
            fresh = [sample for sample in observation['sensor_memory'] if observation['minute'] - sample['observed_at'] <= 4]
            cleared = any([sample['x'], sample['y']] == report.get('location') and not sample['burning']
                          and sample['observed_at'] >= report['observed_at'] for sample in fresh)
            cleared = cleared or any(assessment['report_id'] == report['id']
                and assessment['assessment'] == 'no_fire_observed'
                and assessment['evidence_observed_at'] >= report['observed_at']
                for assessment in observation['report_assessments'])
            burning = any(sample['burning'] and report.get('location') is not None
                          and math.dist([sample['x'], sample['y']], report['location']) <= 3 for sample in fresh)
            if (not cleared or burning) and (available[0]['id'], sector) in legal:
                action['requests'].append(dict(resource_id=available[0]['id'], sector_id=sector,
                                               mission='suppress' if burning else 'recon'))
        return action
