"""Synthetic weather, ground operations and mixed-fire hold missions."""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations
from wildfire_containment_v1.kernel import _edge, _point
from wildfire_expanded_incident_v1.host import _resource_view
from .hold_fire import MixedHoldFireV1, VERSION as FIRE_VERSION, source_sha256
from .movement import WeatherMixedMovementV1

VERSION = 'wildfire-expanded-weather-ground-hold-v1'


def _source():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class WeatherMixedHoldV1(WeatherMixedMovementV1):
    """Synthetic hold slice; no population, aircraft, outcome or score.

    State changes are single-driver transactions. Operations and messages are
    staged until the physical step and its resource charges agree; any exception
    poisons the episode and requires restoration of a trusted checkpoint.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hold_source = _source()
        self._hold_fire_source = source_sha256()
        self._identity.update(version=VERSION, hold_source=self._hold_source,
                              hold_fire_version=FIRE_VERSION, hold_fire_source=self._hold_fire_source)
        self._missions = {}
        self._install_fire()

    def _install_fire(self):
        wrapper = MixedHoldFireV1(self._weather._fire)
        self._weather._fire = self._fire = wrapper

    def _ensure(self):
        super()._ensure()
        if self._hold_source != _source() or self._hold_fire_source != source_sha256():
            self._poisoned = True
            raise RuntimeError('hold implementation changed')

    def situation(self):
        result = super().situation()
        result['version'] = VERSION
        return result

    def _validate_mission(self, target, edge, protected):
        target = _point(target)
        edge = _edge(edge)
        protected = _point(protected)
        if protected not in edge or any(abs(target[0]-x)+abs(target[1]-y)>1 for x,y in edge):
            raise ValueError('hold target must be adjacent to both edge cells and protect an endpoint')
        for point in (*edge, target):
            if not self._router._inside(point) or point in self._excluded:
                raise ValueError('hold requires confirmed accessible land')
        if any(not self._fire._is_land(x,y) for x,y in edge):
            raise ValueError('hold edge needs supported fire cells')
        return dict(target=list(target), edge=[list(p) for p in edge], protected_cell=list(protected))

    def dispatch_hold(self, resource_id, target_grid, option_id, edge, protected_cell):
        """Dispatch a ground resource to an explicitly selected synthetic hold."""
        self._active()
        mission = self._validate_mission(target_grid, edge, protected_cell)
        options = self.route_options(resource_id, target_grid)['options']
        chosen = next((item for item in options if item['id'] == option_id), None)
        if chosen is None:
            raise ValueError('unknown or stale public route option')
        try:
            self._tick(dict(resource_id=resource_id, target=list(target_grid), option=chosen, mission=mission))
        except BaseException:
            self._poisoned = True
            raise
        return self.situation()

    def _tick(self, command=None):
        if not self._fire._check():
            self._weather._status = 'censored'
            self._censored = True
            return
        self._minute += 1
        wind = self._wind()
        # Work on a detached copy: no Ops charges become committed until the
        # actual fire kernel returns the same costs, including on post-censor.
        ops = Operations.restore(self._ops.checkpoint())
        missions = copy.deepcopy(self._missions)
        pending_reports = []
        def emit(source, kind, payload, **kwargs):
            pending_reports.append((source, kind, copy.deepcopy(payload), copy.deepcopy(kwargs)))
        if command is not None:
            unit = ops.resources[command['resource_id']]
            journey = command['option']['journey']
            hazards = self._router._hazards(self._fire.burning(), wind) | self._excluded
            safe = (all(self._router._safe_line(leg['coordinates'], hazards, unit['kind'])
                        for leg in journey['legs']) and self._safe_position(unit['position'], unit['kind']))
            accepted = bool(safe and ops.dispatch(unit['id'], 'incident', command['target'], journey, self._minute))
            if accepted:
                missions.pop(unit['id'], None)
                if 'mission' in command:
                    missions[unit['id']] = copy.deepcopy(command['mission'])
            emit('operations', 'dispatch_result', {'resource_id': unit['id'], 'accepted': accepted})
        before = {key: unit['state'] for key, unit in ops.resources.items()}
        assignments = []
        total_before = {}
        def work(unit):
            mission = missions.get(unit['id'])
            if mission is None:
                return False
            edge = _edge(mission['edge'])
            position = self._router._cell(unit['position'])
            line = self._fire._fire._containment._lines.get(edge)
            decay = self._fire._fire._containment.config['decay']
            unavailable = line and (line['breached'] or 0 < line['strength'] <= decay
                                      or (line.get('protected_cell') is not None
                                          and tuple(line['protected_cell']) != tuple(mission['protected_cell'])))
            conflict = any(_edge(a['edge']) == edge and a['protected_cell'] != mission['protected_cell']
                           for a in assignments)
            if (unavailable or conflict or any(abs(position[0]-x)+abs(position[1]-y)>1 for x,y in edge)
                    or unit['work_remaining'] < 1
                    or (unit.get('total_work_remaining') is not None and unit['total_work_remaining'] < 1)):
                missions.pop(unit['id'], None)
                return False
            cost = 0 if unit['kind'] == 'crew' else min(unit['water_litres'], ops.profile['engine_litres_per_minute'])
            if unit['kind'] == 'engine' and cost <= 0:
                return False
            total_before[unit['id']] = unit.get('total_work_remaining')
            assignments.append(dict(resource_id=unit['id'], edge=copy.deepcopy(mission['edge']),
                protected_cell=copy.deepcopy(mission['protected_cell']), kind=unit['kind'], ready=True,
                work_remaining=unit['work_remaining'], water_litres=unit['water_litres'], water_cost=cost))
            return True
        ops.tick(self._minute, wind=wind, daylight=True, hazard_check=self._safe_position,
                 movement_check=self._movement, suppress=work, emit=emit)
        self._guard_unsafe_base(ops, emit)
        self._fire.stage(assignments)
        result = self._weather.step()
        self._guard_unsafe_base(ops, emit)
        costs = self._fire.costs
        expected = [dict(resource_id=a['resource_id'], work_minutes=1, water_litres=float(a['water_cost']))
                    for a in sorted(assignments, key=lambda a:a['resource_id'])]
        if costs != expected:
            raise RuntimeError('fire and operations costs differ')
        for a in assignments:
            unit = ops.resources[a['resource_id']]
            if (unit['work_remaining'] != a['work_remaining'] - 1
                    or unit['water_litres'] != a['water_litres'] - a['water_cost']
                    or (total_before[a['resource_id']] is not None
                        and unit.get('total_work_remaining') != total_before[a['resource_id']] - 1)):
                raise RuntimeError('operations resource charge differs')
        for identifier in list(missions):
            if ops.resources[identifier]['state'] not in ('mobilizing','travel','on_scene','working','blocked'):
                del missions[identifier]
        self._ops, self._missions = ops, missions
        if result['status'] == 'censored':
            self._censored = True
            return
        for source, kind, payload, kwargs in pending_reports:
            self._emit(source, kind, payload, **kwargs)
        self._weather_report(self._minute)
        for key, unit in ops.resources.items():
            if before[key] != unit['state'] or self._minute % 30 == 0:
                self._emit(key, 'resource_status', {'resource': _resource_view(unit)}, actionable=True)
        center_view = {self._case['center']: {'resources': {'observed_at': min(self._known_at.values(), default=0),
                       'resources': copy.deepcopy(list(self._known.values()))}}}
        for record in self._bus.tick(self._minute, center_view):
            if record['kind'] == 'resource_status':
                resource = record['payload']['resource']
                identifier = resource['id']
                if record['observed_at'] >= self._known_at[identifier]:
                    self._known[identifier], self._known_at[identifier] = resource, record['observed_at']
        self._complete = result['status'] == 'diagnostic_end'

    def checkpoint(self):
        self._ensure()
        if self._fire._pending is not None:
            raise RuntimeError('cannot checkpoint during a hold step')
        value = dict(version=VERSION, base=WeatherMixedMovementV1.checkpoint(self),
                     missions=copy.deepcopy(self._missions))
        value['sha256'] = api._hash(value)
        return value

    def restore(self, checkpoint):
        try:
            value = copy.deepcopy(checkpoint)
            if (type(value) is not dict or set(value) != {'version','base','missions','sha256'}
                or value['version'] != VERSION or self._hold_source != _source()
                or self._hold_fire_source != source_sha256()
                or value['sha256'] != api._hash({k:v for k,v in value.items() if k != 'sha256'})):
                raise ValueError('hold checkpoint identity or integrity differs')
            base = value['base']
            keys = {'version','identity','minute','censored','complete','weather','operations',
                    'communications','known','known_at','known_fire','sha256'}
            if (type(base) is not dict or set(base) != keys or base['version'] != 'wildfire-expanded-weather-ground-movement-v1'
                or api._encoded(base['identity']) != api._encoded(self._identity)
                or base['sha256'] != api._hash({k:v for k,v in base.items() if k != 'sha256'})
                or type(base['minute']) is not int or not 0 <= base['minute'] <= 1440
                or type(base['censored']) is not bool or type(base['complete']) is not bool
                or base['complete'] != (base['minute'] == 1440 and not base['censored'])
                or base['operations']['last_minute'] != (base['minute'] if base['minute'] else None)):
                raise ValueError('hold base identity or clocks differ')
            self._weather.restore(base['weather'])
            if (self._weather._minute != base['minute']
                or (self._weather._status == 'censored') != base['censored']
                or (self._weather._status == 'diagnostic_end') != base['complete']):
                raise ValueError('hold weather clocks differ')
            ops, bus = Operations.restore(base['operations']), EventBus.restore(base['communications'])
            if ops.profile.get('revalidate_setup_safety') is not True:
                raise ValueError('setup safety revalidation is required')
            if bus.seed != self._case['seed'] or bus.checkpoint()['last_tick'] > base['minute']:
                raise ValueError('hold communications clock differs')
            self._install_fire()
            if type(value['missions']) is not dict:
                raise ValueError('invalid hold missions')
            for identifier, mission in value['missions'].items():
                if (identifier not in ops.resources or type(mission) is not dict
                    or set(mission) != {'target','edge','protected_cell'}
                    or self._validate_mission(mission['target'],mission['edge'],mission['protected_cell']) != mission
                    or ops.resources[identifier]['state'] not in ('mobilizing','travel','on_scene','working','blocked')):
                    raise ValueError('invalid checkpoint mission')
            self._ops, self._bus = ops, bus
            self._minute, self._censored, self._complete = base['minute'], base['censored'], base['complete']
            self._known, self._known_at, self._known_fire = base['known'], base['known_at'], base['known_fire']
            self._missions = value['missions']
            self._poisoned = False
            if api._encoded(self.checkpoint()) != api._encoded(value):
                raise ValueError('hold checkpoint restoration differs')
        except BaseException:
            self._poisoned = True
            raise
