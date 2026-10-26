"""CPU benchmark over the reactive engine with a separate delivered-evidence boundary."""
import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

from ..environment import RegionalResponseEnv
from ..incident.environment import ReactiveOpsEnv, ReactiveScenarioEnv
from ..ops.communications import PROTOCOL_FIELDS, ReadbackProtocol
from ..ops.environment import ACTION_ERRORS, CORE_ACTION_FIELDS, OpsControlCenterEnv
from ..ops.evidence import EVIDENCE_FIELDS, EvidenceLedger
from ..ops.generator import digest
from ..protection.protocol import ProtectionProtocol
from .observations import DeliveredEvidence, VERSION as OBSERVATION_VERSION
from .outcomes import RULES, VERSION as REWARD_VERSION, recompute_outcomes, score_outcomes, verify_outcomes


VERSION = 'wildfire-response-benchmark-v1'
SCENARIO_VERSION = 'wildfire-benchmark-reactive-scenario-v1'
TASK_VERSION = 'wildfire-benchmark-tasks-v1'


def implementation_digest():
    source = Path(__file__).resolve().parents[4]
    regional = source / 'envs/wildfire_response/regional'
    modules = {
        '': ('data', 'environment', 'fire', 'scenarios', 'stress'),
        'grounded': ('anchors', 'environment', 'scenario', 'tasks'),
        'malaga': ('environment', 'pack', 'tasks'),
        'ops': ('environment', 'communications', 'evidence', 'generator', 'scenario_core', 'tasks', 'objectives'),
        'prepared': ('data', 'environment', 'tasks'),
        'protection': ('environment', 'protocol', 'scenario', 'tasks', 'world'),
        'topographic': ('data', 'environment', 'fire'),
        'incident': ('environment', 'events'),
        'benchmark_v1': ('__init__', 'environment', 'observations', 'outcomes', 'catalog', 'calibration'),
    }
    paths = sorted([*(regional / directory / (name + '.py')
                       for directory, names in modules.items() for name in names),
                    *(source / 'simulator').rglob('*.py'), source / 'wildfire_core/benchmark_v1.py'])
    return digest({str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in paths})


def _catalog_digest(catalog):
    return digest({key: value for key, value in catalog.items() if key != 'sha256'})


class BenchmarkProtectionProtocol(ProtectionProtocol):
    def issue(self, orders, core):
        known = {record['id']: record for record in core.observe()['protection_orders']}
        for order in orders:
            previous = known.get(self.latest.get(order['recipient']))
            if (previous and previous['action'] == order['action']
                    and previous['status'] not in ('failed', 'unable_to_comply')):
                identifier = f'protection-{len(self.orders) + 1}'
                self.orders[identifier] = dict(copy.deepcopy(order), id=identifier,
                    sequence=previous['sequence'], issued_at=core.sim.tick, due_at=None,
                    status='duplicate_ignored', delivered_at=None, acknowledged_at=None,
                    executed_at=None, attempted_at=None, effect=None, superseded_by=None,
                    superseded_at_recipient=None, duplicate_of=previous['id'],
                    text=f"EXERCISE. DUPLICATE IGNORED. ORIGINAL ORDER {previous['id']}.")
                self.redundant_orders += 1
                core._ack('director', order['recipient'], 'duplicate_protection_ignored',
                          order_id=identifier, original_order_id=previous['id'])
            else:
                super().issue([order], core)


class BenchmarkScenarioEnv(ReactiveScenarioEnv):
    def reset(self, scenario_id, seed=None, *, scenario=None):
        self.evidence = None
        self.records = []
        self.ever_burned = set()
        observation, info = super().reset(scenario_id, seed, scenario=scenario)
        self.protection = BenchmarkProtectionProtocol(self.task['protection'])
        self.evidence = DeliveredEvidence(observation)
        self._record(initial=True)
        self.evidence.capture(observation)
        return self.observe(), info

    def _cost(self):
        return 0.0

    def _footprint(self):
        self.ever_burned.update((column, row) for row, cells in enumerate(self.sim.cells)
                               for column, cell in enumerate(cells)
                               if self.sim.burning(cell) or cell.get('burned', 0) > 0)

    def _events(self, minute):
        super()._events(minute)
        self._burning_before_suppression = [[column, row] for row, cells in enumerate(self.sim.cells)
                                            for column, cell in enumerate(cells) if self.sim.burning(cell)]
        self._footprint()

    def _record(self, *, initial=False):
        self._footprint()
        record = dict(minute=self.sim.tick,
                      burning_cells=[[column, row] for row, cells in enumerate(self.sim.cells)
                                     for column, cell in enumerate(cells) if self.sim.burning(cell)],
                      burning_before_suppression=copy.deepcopy(self._burning_before_suppression),
                      burned_cells=[[column, row] for row, cells in enumerate(self.sim.cells)
                                    for column, cell in enumerate(cells) if cell.get('burned', 0) > 0],
                      population={identifier: dict(population=group['count'], exposed=group['burnt'],
                                                   status=group['status'])
                                  for identifier, group in self.sim.groups.items()})
        if initial:
            record.update(burnable_cells=[[column, row] for row, cells in enumerate(self.sim.cells)
                                          for column, cell in enumerate(cells) if cell['initial_fuel'] > 0],
                          cell_area_hectares=self.inputs['grid']['cell_km'] ** 2 * 100)
        self.records.append(record)

    def _sense(self):
        super()._sense()
        if self.evidence is not None:
            self._record()
            self.evidence.capture(super().observe())

    def _ack(self, source, recipient, kind, **fields):
        super()._ack(source, recipient, kind, **fields)
        if self.evidence is not None:
            self.evidence.acknowledge(self.acks[-1])

    def _deliver(self):
        if self.evidence is not None:
            self.evidence.deliver(self.sim.tick)
        super()._deliver()

    def observe(self):
        observation = super().observe()
        return self.evidence.present(observation) if self.evidence is not None else observation

    def _validate(self, action):
        if not isinstance(action, dict):
            raise ValueError('Expected a structured action')
        public = self.observe()
        resources = {unit['id']: unit for unit in public['resources']}
        offers = {offer['id']: offer for offer in public['offers']}
        groups = {group['id']: group for group in public['districts']}
        proxy = SimpleNamespace(resources=resources, offers=offers,
                                pending_requests=set(public['pending_resource_requests']),
                                sim=SimpleNamespace(tick=self.sim.tick, groups=groups), pack=self.pack,
                                _sectors=lambda: {sector['id']: sector for sector in public['sectors']},
                                observe=lambda: public)
        legacy = {key: value for key, value in action.items()
                  if key not in ('clarifications', 'protection_orders')}
        RegionalResponseEnv._validate(proxy, legacy)
        if any(message['recipient'] in groups and message['purpose'] != 'inform'
               for message in action.get('messages', [])):
            raise ValueError('Population protection requires protection_orders')
        requests = action.get('clarifications', [])
        if not isinstance(requests, list) or len(requests) > 4:
            raise ValueError('At most four report clarification IDs per action')
        received = {report['id']: report for report in public['reports']}
        asked = {record['report_id'] for record in public['clarification_requests']}
        selected = set()
        for identifier in requests:
            if (not isinstance(identifier, str) or identifier not in received or identifier in selected
                    or identifier in asked or received[identifier].get('location') is not None):
                raise ValueError('Clarify each received unlocated report at most once')
            selected.add(identifier)
        protocol = copy.copy(self.protection)
        protocol.latest = copy.deepcopy(public['latest_protection_orders'])
        protocol.warning_busy = copy.deepcopy(public['warning_team_busy_until'])
        protocol.validate(action.get('protection_orders', []), proxy, action)

    def _apply(self, action):
        previous = set(self.protection.orders)
        physical = copy.deepcopy(action)
        physical['releases'] = []
        for identifier in action.get('releases', []):
            unit = self.resources[identifier]
            if (unit['state'] in ('reserved', 'mobilizing', 'en_route', 'on_scene')
                    and (unit['state'] == 'reserved' or unit.get('target') is not None)):
                physical['releases'].append(identifier)
            else:
                self._ack(unit['home_base'], 'director', 'release_unavailable', resource_id=identifier,
                          reason='No releasable assignment at execution')
        super()._apply(physical)
        for record in self.protection.orders.values():
            if (record['id'] in previous or record['channel'] != 'in_person'
                    or record['status'] == 'duplicate_ignored'):
                continue
            unit = self.resources[record['resource_id']]
            home = self.sim.groups[record['recipient']]['home']
            if unit['state'] != 'on_scene' or math.dist(unit.get('target') or home, home) > 2:
                self.protection.warning_busy.pop(record['resource_id'], None)
                for item in self.queue:
                    if item['kind'] == 'protection_delivery' and item['data']['order_id'] == record['id']:
                        item['will_fail'] = True
        self.evidence.issued(super().observe())

    def step(self, action):
        _, _, _, _, info = super().step(action)
        self.evidence.deliver(self.sim.tick)
        abandoned = self.invalid_streak >= 3
        self.terminated = self.sim.tick >= RULES['horizon_minutes'] and not self.boundary_censored and not abandoned
        self.truncated = self.boundary_censored or abandoned
        return self.observe(), 0.0, self.terminated, self.truncated, self._info(info['error'])

    def evaluator_state(self):
        return copy.deepcopy(dict(scenario=self.task, fire_checkpoint=self.sim.checkpoint(),
            protection_checkpoint=self.sim.protection_checkpoint(), resources=self.resources,
            offers=self.offers, events=self.events, reports=self.report_events, queue=self.queue,
            protection_orders=self.protection.orders, latest_protection_orders=self.protection.latest,
            effective_protection_orders=self.protection.effective, warning_team_busy_until=self.protection.warning_busy,
            received_reports=self.ledger, sensor_memory=list(self.memory.values()), counters=self.counters,
            incident_state=vars(self.incidents),
            audit_minutes=self.audit_minutes, communications=self.acks, minute_records=self.records,
            pending_evidence=self.evidence.pending))


class BenchmarkEnv(ReactiveOpsEnv):
    interface_version = VERSION
    task_version = TASK_VERSION
    scenario_version = SCENARIO_VERSION

    def __init__(self, prepared_inputs, topographic_inputs, *, expected_prepared_sha256,
                 expected_topographic_sha256, slope_enabled=True, allow_synthetic_conditions=True,
                 catalog=None, expected_catalog_sha256=None, profile='central',
                 max_decisions=60, ticks_per_action=2):
        if (type(max_decisions) is not int or type(ticks_per_action) is not int
                or not 1 <= max_decisions <= 120 or not 1 <= ticks_per_action <= 10
                or max_decisions * ticks_per_action != RULES['horizon_minutes']):
            raise ValueError('Benchmark v1 requires a 120-minute horizon within supported decision limits')
        self._catalog = copy.deepcopy(catalog)
        self._expected_catalog_sha256 = expected_catalog_sha256
        self._profile = profile
        from .calibration import public_profile
        self._public_profile = public_profile(profile)
        super().__init__(prepared_inputs, topographic_inputs,
                         expected_prepared_sha256=expected_prepared_sha256,
                         expected_topographic_sha256=expected_topographic_sha256,
                         slope_enabled=slope_enabled, allow_synthetic_conditions=allow_synthetic_conditions,
                         max_decisions=max_decisions, ticks_per_action=ticks_per_action)

    def _task_manifest(self):
        from .catalog import build_catalog, validate_catalog
        if self._catalog is None:
            self._catalog = build_catalog(self._inputs, self._domain)
        self._catalog = validate_catalog(self._catalog, self._inputs, self._domain)
        fingerprint = _catalog_digest(self._catalog)
        if self._catalog.get('sha256', fingerprint) != fingerprint:
            raise ValueError('Catalog content digest mismatch')
        if self._expected_catalog_sha256 is not None and self._expected_catalog_sha256 != fingerprint:
            raise ValueError('Catalog does not match its expected digest')
        self._catalog_sha256 = fingerprint
        return copy.deepcopy(self._catalog)

    def _resolve_task(self, task_id):
        from .calibration import apply_profile
        from .catalog import get_task
        task = copy.deepcopy(get_task(self._catalog, self._active_inputs, task_id))
        scenario = apply_profile(copy.deepcopy(task['scenario']), self._profile)
        if scenario is not None:
            task['scenario'] = scenario
        task['scenario']['minimum_ticks'] = RULES['horizon_minutes']
        return task

    def _new_core(self):
        return BenchmarkScenarioEnv(self._active_inputs, self._domain, slope_enabled=self._slope_enabled,
                                    max_decisions=self._max_decisions, ticks_per_action=self._ticks_per_action)

    def reset(self, task_id=None, seed=None):
        from .calibration import VERSION as PROFILE_VERSION
        if task_id is None:
            from .catalog import task_ids
            task_id = task_ids(self._catalog, 'train')[0]
        task = self._resolve_task(task_id)
        if seed is not None and seed != task['seed']:
            raise ValueError('Benchmark tasks pin their seed; choose another variant ID')
        candidate = self._new_core()
        observation, _ = candidate.reset(task['scenario_id'], seed, scenario=task['scenario'])
        self._apply_runtime_profile(candidate)
        self._core, self._task = candidate, task
        self._communications = ReadbackProtocol(**task['communication'])
        self._evidence = EvidenceLedger()
        self._communications.receive(observation)
        self._objective_score = 0.0
        self._completed_ids, self._objectives = set(), []
        observation = self._present_episode(observation)
        self._ops_trajectory = dict(interface_version=VERSION, task_version=TASK_VERSION,
            scenario_engine_version=SCENARIO_VERSION, reward_version=REWARD_VERSION,
            evidence_version=OBSERVATION_VERSION, rules=copy.deepcopy(RULES),
            rules_sha256=digest(RULES), catalog_sha256=self._catalog_sha256,
            catalog_version=self._catalog['version'],
            calibration_version=PROFILE_VERSION, profile=self._profile,
            profile_sha256=digest(self._public_profile), public_profile=copy.deepcopy(self._public_profile),
            implementation_sha256=implementation_digest(), prepared_inputs_sha256=self._inputs['sha256'],
            topographic_inputs_sha256=self._domain['sha256'], slope_enabled=self._slope_enabled,
            runtime_pack_sha256=digest(self._core.pack), scenario_sha256=digest(task['scenario']),
            task_id=task_id, seed=self._core.seed, task_set=task['task_set'],
            max_decisions=self._max_decisions, ticks_per_action=self._ticks_per_action,
            initial_observation=copy.deepcopy(observation), transitions=[])
        return observation, self._public_info(None)

    def _apply_runtime_profile(self, core):
        from .calibration import apply_runtime_profile
        apply_runtime_profile(core, self._profile)

    @property
    def catalog(self):
        return copy.deepcopy(self._catalog)

    def _present_episode(self, observation):
        public = OpsControlCenterEnv._present({key: value for key, value in observation.items() if key != 'landscape'})
        public['landscape'] = observation['landscape']
        public.update(task_id=self._task['id'], task_version=TASK_VERSION, task_set=self._task['task_set'],
                      split=self._task.get('split'),
                      ops_interface_version=VERSION, scenario_engine_version=SCENARIO_VERSION,
                      exercise_mode=VERSION, task_objectives=[], completed_objective_ids=[],
                      profile=copy.deepcopy(self._public_profile), reward_version=REWARD_VERSION,
                      clock=dict(minute=public['minute'], horizon_minutes=120,
                                 decision_minutes=self._ticks_per_action, unit='simulation_minutes'))
        public.update(self._evidence.public_state())
        communications = self._communications.public_state()
        public.update({key: value for key, value in communications.items() if key != 'pending_readbacks'})
        public['capabilities'] = dict(reactive_incident_events=True, location_clarification=True,
            shelter_in_place=True, in_person_warnings=True, versioned_protection_orders=True,
            slope_effects=self._slope_enabled, measured_terrain_in_physics=self._slope_enabled,
            measured_fuel_classes=True, measured_weather_context=True, live_messaging=False,
            historical_weather_replay=False, incident_specific_roads=False)
        public['travel_estimates'] = {
            unit['id']: {sector['id']: self._core.pack['assumptions']['mobilization_minutes'][unit['kind']]
                         + self._core._travel(unit, sector['center']) for sector in public['sectors']}
            for unit in public['resources']}
        public['rules'].update(objective=(
            'Terminal graded score is 0.8 unexposed population fraction plus 0.2 unburned fraction; '
            'without population only area counts. Headline ranking sorts safety before additional burned area. '
            'Active fire alone is not failure. Exposure is synthetic, never death. '
            'Boundary-censored episodes are unscored; invalid abandonment scores zero.'),
            evidence='Dynamic fields are delayed snapshots. Null and null timestamps mean unknown.',
            dispatch='Request, receive offer and readback, correct or confirm, authorize; results arrive later.')
        return public

    def _validated_core_action(self, action):
        allowed = CORE_ACTION_FIELDS | PROTOCOL_FIELDS | EVIDENCE_FIELDS | {'clarifications', 'protection_orders'}
        if not isinstance(action, dict) or set(action) - allowed:
            raise ValueError('Unknown ops action fields')
        if len(json.dumps(action, allow_nan=False).encode()) > 65536:
            raise ValueError('Action exceeds 64 KiB')
        core_action = {key: copy.deepcopy(value) for key, value in action.items()
                       if key in CORE_ACTION_FIELDS | {'clarifications', 'protection_orders'}}
        self._core._validate(core_action)
        public = self.get_situation()
        self._communications.validate(action, public['offers'], public['minute'])
        return core_action, self._evidence.validate(action, public)

    def _commit(self, action):
        self._require_live()
        previous = self.get_situation()
        error, assessments = None, []
        try:
            core_action, assessments = self._validated_core_action(action)
        except ACTION_ERRORS as exception:
            error = str(exception)[:200]
            core_action = {'_invalid_ops_action': True}
        observation, _, terminated, truncated, info = self._core.step(core_action)
        valid = error is None and info['error'] is None
        if valid:
            self._communications.apply(action, previous['minute'])
            self._evidence.apply(action, previous, assessments)
        self._communications.receive(observation)
        terminal = terminated or truncated
        outcomes = self.outcomes()
        reward = outcomes['graded_score'] if terminal else 0.0
        self._objective_score += reward
        public = self._present_episode(observation)
        self._ops_trajectory['transitions'].append(dict(
            action=copy.deepcopy(action) if valid else {'_invalid_ops_action': True},
            action_valid=valid, observation=copy.deepcopy(public), objective_reward=reward,
            terminated=terminated, truncated=truncated))
        return public, reward, terminated, truncated, self._public_info(error or info['error'])

    def _public_info(self, error):
        info = dict(schema_version=VERSION, reward_version=REWARD_VERSION,
                    ops_interface_version=VERSION, ops_task_version=TASK_VERSION,
                    minute=self._core.sim.tick, decision=self._core.decisions, error=error,
                    objective_score=self._objective_score, boundary_censored=self._core.boundary_censored)
        if self._core.terminated or self._core.truncated:
            info.update(outcomes=self.outcomes(), suitable_for_training=self.outcomes()['suitable_for_training'])
        return info

    def outcomes(self):
        if self._core.sim is None:
            raise RuntimeError('reset required')
        return score_outcomes(self._core.records[0], self._core.records[-1], self._core.ever_burned,
                              terminal=self._core.terminated or self._core.truncated,
                              abandoned=self._core.invalid_streak >= 3,
                              boundary_censored=self._core.boundary_censored)

    def private_audit(self):
        """Evaluator-only material, intentionally absent from the actor trajectory."""
        if not hasattr(self, '_ops_trajectory'):
            raise RuntimeError('reset required')
        return copy.deepcopy(dict(records=self._core.records, outcomes=self.outcomes(),
                                  ever_burned_cells=[list(point) for point in sorted(self._core.ever_burned)],
                                  evaluator_state=self._core.evaluator_state()))

    def get_reports(self, report_ids=None, *, limit=100):
        _query_limit(limit)
        if report_ids is not None and (not isinstance(report_ids, list) or len(report_ids) > 100):
            raise ValueError('Query at most 100 received report IDs')
        return super().get_reports(report_ids)[:limit]

    def get_events(self, event_ids=None, *, after_cursor=0, limit=100):
        _query_limit(limit)
        events = self.get_situation()['incident_events']
        if type(after_cursor) is not int or not 0 <= after_cursor <= len(events):
            raise ValueError('Cursor must name a received event boundary')
        if event_ids is not None:
            if not isinstance(event_ids, list) or len(event_ids) > 100:
                raise ValueError('Query at most 100 received event IDs')
            known = {event['id'] for event in events}
            if any(not isinstance(identifier, str) or identifier not in known for identifier in event_ids):
                raise ValueError('Only received event IDs may be queried')
            events = [event for event in events if event['id'] in event_ids]
        return copy.deepcopy(events[after_cursor:after_cursor + limit])

    def verify_episode(self):
        self._require_finished()
        return verify_benchmark_trajectory(self.trajectory(), self._inputs, self._domain,
                                           catalog=self._catalog, private_audit=self.private_audit())

    def _require_finished(self):
        if not hasattr(self, '_ops_trajectory') or not (self._core.terminated or self._core.truncated):
            raise RuntimeError('Finish an episode before verification')


def _query_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Query limit must be from 1 to 100')


def verify_benchmark_trajectory(trajectory, prepared, domain, *, catalog=None, private_audit=None):
    if not isinstance(trajectory, dict):
        raise ValueError('Expected a benchmark trajectory')
    environment = BenchmarkEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
        expected_topographic_sha256=domain['sha256'], slope_enabled=trajectory.get('slope_enabled'),
        catalog=catalog, expected_catalog_sha256=trajectory.get('catalog_sha256'),
        profile=trajectory.get('profile'), max_decisions=trajectory.get('max_decisions'),
        ticks_per_action=trajectory.get('ticks_per_action'))
    environment.reset(trajectory.get('task_id'), trajectory.get('seed'))
    header = {key: value for key, value in trajectory.items() if key != 'transitions'}
    expected = {key: value for key, value in environment.trajectory().items() if key != 'transitions'}
    if digest(header) != digest(expected):
        raise ValueError('Benchmark header or implementation pins differ')
    transitions = trajectory.get('transitions')
    if not isinstance(transitions, list) or not 1 <= len(transitions) <= environment._max_decisions:
        raise ValueError('Expected a bounded complete benchmark episode')
    for index, recorded in enumerate(transitions):
        if not isinstance(recorded, dict) or not isinstance(recorded.get('action'), dict):
            raise ValueError('Expected structured benchmark transitions')
        try:
            environment.step(recorded['action'])
        except RuntimeError as exception:
            raise ValueError('Action follows episode completion') from exception
        if digest(recorded) != digest(environment._ops_trajectory['transitions'][-1]):
            raise ValueError(f'Benchmark transition {index} does not replay exactly')
    environment._require_finished()
    audit = environment.private_audit()
    if private_audit is not None and digest(private_audit) != digest(audit):
        raise ValueError('Private evaluator audit differs from replay')
    outcomes = recompute_outcomes(audit['records'], terminal=True,
        abandoned=environment._core.invalid_streak >= 3, boundary_censored=environment._core.boundary_censored)
    verify_outcomes(environment.outcomes(), outcomes)
    if not math.isclose(environment.reward, outcomes['graded_score'], rel_tol=0, abs_tol=1e-12):
        raise ValueError('Terminal reward differs from independent recomputation')
    by_kind = dict(population_safety=outcomes['safe_success'],
                   burned_area=outcomes['unburned_fraction'])
    result = dict(verified=True, core_replayed=True, total_score=outcomes['graded_score'],
                  by_kind=by_kind, outcomes=outcomes,
                  boundary_censored=outcomes['boundary_censored'],
                  suitable_for_training=outcomes['suitable_for_training'])
    return dict(**result, passed=True, objective_score=result['total_score'],
                objective_result=copy.deepcopy(result), reward_version=REWARD_VERSION)
