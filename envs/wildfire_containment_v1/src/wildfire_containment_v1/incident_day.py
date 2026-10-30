"""Narrow opt-in incident-day adapter, pinned to the reviewed core."""
import copy
import hashlib
from pathlib import Path

from envs.wildfire_response.incident_day import environment as day
from envs.wildfire_response.incident_day.environment import IncidentDayEnv, implementation_digest
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.scenarios import DayFire
from envs.wildfire_response.incident_day.scenarios import grid_point
from envs.wildfire_response.regional.ops.generator import digest

from .fire import ContainmentDayFire
from .kernel import ContainmentKernel, _edge, _number

VERSION = 'wildfire-containment-incident-adapter-v4'
CORE = 'af4adc0b2fb92282246411dbac83c1eeac23eb3b4730200c0b34dc288b59323a'


def _check_runtime():
    # The weather runtime installs process-wide class hooks without changing
    # source-file hashes. Their combined lifecycle is not supported here.
    if (day.DayFire is not DayFire or
            any(getattr(method, '__module__', None) != module
                or getattr(method, '__qualname__', None) != name
                for method, module, name in (
                    (IncidentDayEnv.reset, day.__name__, 'IncidentDayEnv.reset'),
                    (IncidentDayEnv._tick, day.__name__, 'IncidentDayEnv._tick'),
                    (IncidentDayEnv.restore, day.__name__, 'IncidentDayEnv.restore'),
                    (EventBus.tick, EventBus.__module__, 'EventBus.tick'),
                    (EventBus.emit, EventBus.__module__, 'EventBus.emit')))):
        raise RuntimeError('Patched incident-day or weather runtime is unsupported')


def _source_digest():
    root = Path(__file__).parent
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(root.glob('*.py'))})


class ContainmentIncidentDayEnv(IncidentDayEnv):
    """Small explicit adapter; private checkpoints only, no artifact export."""
    def __init__(self, *args, containment_config, pressure_scale, **kwargs):
        _check_runtime()
        if implementation_digest() != CORE:
            raise RuntimeError('Unreviewed incident-day core implementation')
        if kwargs.get('artifact_dir') is not None:
            raise ValueError('Artifact export is unsupported by this adapter')
        super().__init__(*args, **kwargs)
        if self._uncertain_land:
            raise ValueError('Unresolved-land censoring is unsupported by this adapter')
        kernel = ContainmentKernel(**containment_config)
        self._config = copy.deepcopy(kernel.config)
        self._scale = _number(pressure_scale, 'pressure_scale')
        if self._scale <= 0 or self._config['wear_factor'] <= 0 or self._config['wind_factor'] <= 0:
            raise ValueError('Positive pressure, wind and wear factors are required')
        self._source_pin = _source_digest()
        self._missions = {}
        self._requested = {}
        self._proposal_cache = {}
        self._pending_hold = None
        self._tick_work = []
        self._observed = {}
        self._containment_audit = []

    def reset(self, scenario_id=None, seed=None):
        _check_runtime()
        if implementation_digest() != CORE or self._source_pin != _source_digest():
            raise RuntimeError('Adapter or incident-day core source changed')
        self._missions, self._proposal_cache, self._observed, self._requested = {}, {}, {}, {}
        self._pending_hold, self._tick_work = None, []
        self._containment_audit = []
        observation, info = super().reset(scenario_id, seed)
        self.fire = ContainmentDayFire.from_day_fire(self.fire,
            containment=ContainmentKernel(**self._config), pressure_scale=self._scale)
        return observation, info

    def propose_hold(self, offer_id):
        """Propose both protected endpoints for each public approach edge."""
        _check_runtime()
        if self._scenario is None or self.terminated or self.truncated:
            raise RuntimeError('A live episode is required')
        offer = self._offers.get(offer_id)
        if not offer or offer['status'] != 'offered' or offer['expires_at'] <= self.minute:
            raise ValueError('Choose a live delivered offer')
        resource = self._known_resources.get(offer['resource_id'])
        approach = self._approaches.get(offer['approach_id'])
        if not resource or resource['kind'] not in ('crew', 'engine') or not approach:
            raise ValueError('A ground offer with a public approach is required')
        x, y = approach['approach_grid']
        result = []
        for dx, dy in ((-1, 0), (0, -1), (0, 1), (1, 0)):
            if not (0 <= x + dx < self.fire.width and 0 <= y + dy < self.fire.height):
                continue
            edge = [list(point) for point in _edge(((x, y), (x + dx, y + dy)))]
            for protected in (edge[1], edge[0]):
                identifier = digest(dict(version=VERSION, offer=offer_id, edge=edge,
                                         protected_cell=protected))[:24]
                proposal = dict(id=identifier, offer_id=offer_id, resource_id=offer['resource_id'],
                                edge=copy.deepcopy(edge), protected_cell=protected[:])
                self._proposal_cache[identifier] = copy.deepcopy(proposal)
                result.append(proposal)
        return result

    def authorize_hold(self, offer_id, proposal_id):
        """Authorize the existing offer and bind its proposed line to dispatch."""
        _check_runtime()
        proposal = self._proposal_cache.get(proposal_id)
        if not proposal or proposal['offer_id'] != offer_id:
            raise ValueError('Choose a line proposed for this offer')
        offer = self._offers.get(offer_id)
        if not offer or offer['status'] != 'offered' or offer['expires_at'] <= self.minute:
            raise ValueError('Offer is no longer live')
        if any(item['edge'] == proposal['edge'] for items in self._requested.values() for item in items):
            raise ValueError('Line already has a hold mission')
        if not self.check_action({'authorizations': [offer_id]})['valid']:
            raise ValueError('Authorization is invalid')
        self._pending_hold = copy.deepcopy(proposal)
        try:
            return self.step({'authorizations': [offer_id]})
        finally:
            self._pending_hold = None

    def step(self, action):
        _check_runtime()
        # Reservations are retired only when relevant public evidence arrives.
        if (self._scenario is not None and not self.terminated and not self.truncated
                and self.check_action(action)['valid'] and isinstance(action, dict)
                and self._pending_hold):
            identifier = self._pending_hold['resource_id']
            self._requested.setdefault(identifier, []).append(dict(
                edge=copy.deepcopy(self._pending_hold['edge']), requested_at=self.minute,
                dispatch_at=self.minute + 1, offer_id=self._pending_hold['offer_id']))
        return super().step(action)

    def _receive(self, records):
        super()._receive(records)
        # Act only on delivered evidence and its observation time. Reports can
        # arrive late and out of order; never retire a later request.
        retired_states = {'returning', 'refilling', 'resting', 'available',
                          'unavailable', 'off_duty', 'needs_assistance'}
        for record in records:
            kind = record['kind']
            payload = record['payload']
            if kind in ('resource_status', 'telemetry'):
                resource = payload.get('resource')
                if not isinstance(resource, dict) or resource.get('state') not in retired_states:
                    continue
                identifier = resource.get('id')
                cutoff = resource.get('observed_at')
                if type(cutoff) is not int or cutoff > record['observed_at']:
                    continue
            elif kind in ('release_accepted', 'dispatch_accepted', 'dispatch_declined'):
                identifier = payload.get('resource_id')
                cutoff = record['observed_at']
            else:
                continue
            if identifier not in self._requested:
                continue
            if kind == 'dispatch_declined':
                # The core's decline report has no offer ID. One resource can
                # have at most one authorization due in a minute, so matching
                # resource and execution minute identifies the submitted hold.
                keep = [item for item in self._requested[identifier]
                        if item['dispatch_at'] != cutoff]
            elif kind == 'dispatch_accepted':
                # A confirmed replacement ends earlier missions but not the
                # hold authorized at this same minute or later submissions.
                keep = [item for item in self._requested[identifier]
                        if item['dispatch_at'] >= cutoff]
            else:
                keep = [item for item in self._requested[identifier]
                        if item['requested_at'] >= cutoff]
            if keep:
                self._requested[identifier] = keep
            else:
                del self._requested[identifier]

    def _execute_commands(self):
        due = [copy.deepcopy(c) for c in self._commands if c['due'] <= self.minute]
        if 'dispatch' in self.ops.__dict__ or 'release' in self.ops.__dict__:
            raise RuntimeError('Unsupported operations override')
        outcomes = {}
        dispatch, release = self.ops.dispatch, self.ops.release

        def capture_dispatch(resource_id, *args, **kwargs):
            accepted = dispatch(resource_id, *args, **kwargs)
            outcomes['authorize', resource_id] = accepted
            return accepted

        def capture_release(resource_id, *args, **kwargs):
            accepted = release(resource_id, *args, **kwargs)
            outcomes['release', resource_id] = accepted
            return accepted

        self.ops.dispatch, self.ops.release = capture_dispatch, capture_release
        try:
            super()._execute_commands()
        finally:
            del self.ops.dispatch
            del self.ops.release
        for command in due:
            if command['kind'] not in ('authorize', 'release'):
                continue
            identifier = (self._offers[command['data']]['resource_id']
                          if command['kind'] == 'authorize' else command['data'])
            if outcomes.get((command['kind'], identifier)) is not True:
                continue
            self._missions.pop(identifier, None)
            self._observed.pop(identifier, None)
            if (command['kind'] == 'authorize' and self._pending_hold
                    and self._pending_hold['offer_id'] == command['data']):
                proposal = copy.deepcopy(self._pending_hold)
                edge = _edge(proposal['edge'])
                line = self.fire._containment._lines.get(edge)
                if line and line['breached']:
                    self.fire._containment.restart_line(edge)
                self._missions[identifier] = proposal

    def _suppress(self, unit):
        mission = self._missions.get(unit['id'])
        if mission is None:
            return super()._suppress(unit)
        edge = _edge(mission['edge'])
        position = grid_point(self.inputs, unit['position'])
        line = self.fire._containment._lines.get(edge)
        # A line which will expire at the start of this tick requires a new mission.
        unavailable = line and (line['breached'] or
            (line['strength'] > 0 and line['strength'] <= self._config['decay']))
        if (unit['work_remaining'] < 1 or (unit.get('total_work_remaining') is not None
                and unit['total_work_remaining'] < 1)):
            return False
        if unavailable or any(abs(position[0]-x) + abs(position[1]-y) > 1 for x, y in edge):
            self.bus.emit(unit['id'], 'containment_status',
                          dict(resource_id=unit['id'], proposal_id=mission['id'], status='unavailable'),
                          self.minute, actionable=True)
            self._missions.pop(unit['id'], None)
            return False
        cost = 0 if unit['kind'] == 'crew' else min(unit['water_litres'], self.ops.profile.get('engine_litres_per_minute', 20))
        if unit['kind'] == 'engine' and cost <= 0:
            return False
        self._tick_work.append(dict(resource_id=unit['id'], edge=mission['edge'], kind=unit['kind'],
            ready=True, protected_cell=mission['protected_cell'], work_remaining=unit['work_remaining'],
            water_litres=unit['water_litres'], water_cost=cost))
        return True

    def _tick(self):
        _check_runtime()
        if 'step' in self.fire.__dict__:
            raise RuntimeError('Unsupported fire-step override')
        self._tick_work = []
        fire = self.fire
        stepped = False
        def contained_step(count=1):
            nonlocal stepped
            if count != 1 or stepped:
                raise RuntimeError('Unsupported incident-day fire stepping')
            stepped = True
            costs = fire.step_with_containment(assignments=self._tick_work)
            if {item['resource_id'] for item in costs} != {item['resource_id'] for item in self._tick_work}:
                raise RuntimeError('Containment and resource accounting disagree')
            for item in self._tick_work:
                mission = self._missions[item['resource_id']]
                actual = self.ops.resources[item['resource_id']]
                if (actual['work_remaining'] != item['work_remaining'] - 1
                        or actual['water_litres'] != item['water_litres'] - item['water_cost']):
                    raise RuntimeError('Actual duty or water charge disagrees')
                self._containment_audit.append(dict(minute=self.minute, resource_id=item['resource_id'],
                    edge=copy.deepcopy(item['edge']), work_minutes=1, water_litres=item['water_cost']))
                line = fire._containment._lines[_edge(item['edge'])]
                status = 'breached' if line['breached'] else 'holding'
                if self._observed.get(item['resource_id']) != status:
                    self.bus.emit(item['resource_id'], 'containment_status',
                        dict(resource_id=item['resource_id'], proposal_id=mission['id'], status=status),
                        self.minute, actionable=True)
                    self._observed[item['resource_id']] = status
        fire.step = contained_step
        try:
            wake = super()._tick()
            if not stepped:
                raise RuntimeError('Censored fire step is unsupported')
        finally:
            del fire.step
            self._tick_work = []
        for identifier in list(self._missions):
            if self.ops.resources[identifier]['state'] not in ('mobilizing', 'travel', 'on_scene', 'working', 'blocked'):
                self._missions.pop(identifier)
                self._observed.pop(identifier, None)
        return wake

    def checkpoint(self):
        _check_runtime()
        if (self._pending_hold or self._tick_work or self._source_pin != _source_digest()
                or implementation_digest() != CORE):
            raise RuntimeError('Checkpoint requires a stable adapter boundary')
        base = super().checkpoint()
        fire = base['state']['fire']
        base['state']['fire'] = copy.deepcopy(fire['fire'])
        base['sha256'] = digest({k: v for k, v in base.items() if k != 'sha256'})
        value = dict(version=VERSION, source=self._source_pin, core=CORE, base=base, fire=fire,
                     config=copy.deepcopy(self._config), pressure_scale=self._scale,
                     missions=copy.deepcopy(self._missions), proposals=copy.deepcopy(self._proposal_cache),
                     observed=copy.deepcopy(self._observed), requested=copy.deepcopy(self._requested),
                     containment_audit=copy.deepcopy(self._containment_audit))
        value['sha256'] = digest(value)
        return value

    def restore(self, checkpoint):
        _check_runtime()
        value = copy.deepcopy(checkpoint)
        if (not isinstance(value, dict) or value.get('version') != VERSION
                or value.get('source') != _source_digest() or self._source_pin != _source_digest()
                or implementation_digest() != CORE or value.get('core') != CORE
                or value.get('config') != self._config or value.get('pressure_scale') != self._scale
                or value.get('sha256') != digest({k: v for k, v in value.items() if k != 'sha256'})):
            raise ValueError('Adapter checkpoint identity or integrity differs')
        fire = ContainmentDayFire.restore(value['fire'])
        if (digest(value['fire']['fire']) != digest(value['base']['state']['fire'])
                or fire._containment._minute != value['base']['state']['minute']):
            raise ValueError('Adapter checkpoint fire or clock differs')
        super().restore(value['base'])
        self.fire = fire
        self._missions = value['missions']
        self._proposal_cache = value['proposals']
        self._observed = value['observed']
        self._requested = value['requested']
        self._containment_audit = value['containment_audit']
        self._pending_hold, self._tick_work = None, []
        return self.get_situation()

    def verify_episode(self):
        raise RuntimeError('Containment episode verification is not implemented')

    def trajectory(self):
        raise RuntimeError('Containment trajectory export is not implemented')
