"""Private synthetic population composition over the pinned mixed-fire hold host.

This is a bounded exercise slice, not a complete day API or scoring system.
"""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.operations import Operations
from eval import incident_weather_v1 as weather
from .hold import WeatherMixedHoldV1

VERSION = 'wildfire-expanded-population-protection-v1'
CATALOG_VERSION = 'wildfire-expanded-synthetic-population-v1'
PROFILE = dict(order_delivery_minutes=2, order_ack_minutes=2, order_execution_minutes=2)


def _source():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _view(group):
    return {k: copy.deepcopy(group[k]) for k in ('id', 'count', 'position', 'status', 'exposed')}


class ExpandedPopulationV1:
    """Single trusted driver; Python object and checkpoints are not actor APIs.

    A separate Operations instance advances population in lockstep with the
    unchanged hold host. The public methods only return delivered knowledge.
    """
    def __init__(self, host, catalog, *, expected_catalog_sha256, opt_in=False):
        if opt_in is not True or type(host) is not WeatherMixedHoldV1:
            raise ValueError('explicit opt-in and exact hold host required')
        host._ensure()
        if host._minute != 0 or host._complete or host._ops.groups or host._ops.destinations:
            raise ValueError('a fresh hold host without population is required')
        catalog = copy.deepcopy(catalog)
        if (type(catalog) is not dict or set(catalog) != {'version','role','case_id','inputs_sha256',
                'network_sha256','groups','destinations'} or catalog['version'] != CATALOG_VERSION
                or catalog['role'] != 'synthetic_fixture_only' or api._hash(catalog) != expected_catalog_sha256
                or catalog['case_id'] != host._case['id'] or catalog['inputs_sha256'] != host._inputs.document_sha256
                or catalog['network_sha256'] != host._network.sha256
                or type(catalog['groups']) is not list or not catalog['groups']
                or type(catalog['destinations']) is not list or not catalog['destinations']):
            raise ValueError('population catalog or independently trusted pins differ')
        groups, destinations = [], []
        for group in catalog['groups']:
            if (type(group) is not dict or set(group) != {'id','count','position'}
                or type(group['id']) is not str or not group['id']
                or type(group['count']) is not int or group['count'] <= 0):
                raise ValueError('invalid synthetic cohort')
            self._validate_position(host, group['position'])
            groups.append(dict(group, home=copy.deepcopy(group['position']), status='at_home', exposed=False))
        for dest in catalog['destinations']:
            if (type(dest) is not dict or set(dest) != {'id','capacity','position'}
                or type(dest['id']) is not str or not dest['id']
                or type(dest['capacity']) is not int or dest['capacity'] < 0):
                raise ValueError('invalid synthetic destination')
            self._validate_position(host, dest['position'])
            destinations.append(dict(dest, status='open'))
        self._host = host
        self._ops = Operations([], destinations, groups, PROFILE)
        self._catalog = catalog
        self._source = _source()
        self._identity = dict(version=VERSION, source=self._source, host=copy.deepcopy(host._identity),
                              catalog=expected_catalog_sha256)
        self._known = {k:_view(v) for k,v in self._ops.groups.items()}
        self._known_at = {k:0 for k in self._known}
        # Separate the ordering of position/status snapshots from the first
        # delivered evidence of irreversible exposure.
        self._known_rank = {k:-1 for k in self._known}
        self._exposed_at = {k:None for k in self._known}
        self._destinations = copy.deepcopy(self._ops.destinations)
        self._dose = {k:0 for k in self._known}
        self._reports_seen = len(host._bus.reports)
        self._serial = 0
        self._poisoned = False

    @staticmethod
    def _validate_position(host, position):
        if (type(position) is not list or len(position) != 2
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in position)):
            raise ValueError('invalid projected position')
        cell = host._router._cell(position)
        if not host._router._inside(cell) or cell in host._excluded:
            raise ValueError('population location requires confirmed synthetic access')

    def _ensure(self):
        if self._poisoned:
            raise RuntimeError('population host indeterminate; restore a trusted checkpoint')
        self._host._ensure()
        if self._source != _source() or self._identity['host'] != self._host._identity:
            self._poisoned = True
            raise RuntimeError('population or hold identity changed')

    def _active(self):
        self._ensure()
        self._host._active()

    def situation(self):
        self._ensure()
        result = self._host.situation()
        result['version'] = VERSION
        if result['status'] != 'censored':
            result['population'] = copy.deepcopy(list(self._known.values()))
            result['destinations'] = copy.deepcopy(list(self._destinations.values()))
        return result

    def reports(self, *, after_cursor=0, limit=20):
        self._ensure()
        return self._host.reports(after_cursor=after_cursor, limit=limit)

    def route_options(self, resource_id, target_grid):
        self._active()
        return self._host.route_options(resource_id, target_grid)

    def evacuation_options(self, group_id, destination_id):
        self._active()
        if group_id not in self._known or destination_id not in self._destinations:
            raise ValueError('choose a reported cohort and destination')
        origin = self._known[group_id]['position']
        target = self._destinations[destination_id]['position']
        cell = self._host._router._cell(target)
        result = self._host._router.options(origin, cell, mode='engine',
            burning=self._host._known_fire, wind=self._host._case['wind'],
            excluded_cells=self._host._excluded, target_metric=target, limit=32)
        # A road edge is not evidence of full-cell access. The existing synthetic
        # host requires both declared engine modes and affirmative cell access.
        result['options'] = [x for x in result['options'] if x['journey']['legs']
            and all(leg['mode'] == 'road' and leg.get('edge_id') in self._host._router.allowed_edge_ids
                    and 'engine' in self._host._network.edges[leg['edge_id']]['allowed_modes']
                    for leg in x['journey']['legs'])
            and math.dist(x['journey']['destination'], target) < .01][:3]
        if not result['options']:
            result['status'] = 'unmapped' if self._host._unknown_access or result['status'] == 'reachable' else result['status']
        return copy.deepcopy(result)

    def protect(self, group_id, action, *, destination_id=None, option_id=None):
        self._active()
        if group_id not in self._known or action not in ('evacuate','shelter'):
            raise ValueError('choose a reported cohort and protection action')
        journey = None
        if action == 'evacuate':
            if destination_id not in self._destinations or type(option_id) is not str:
                raise ValueError('choose a reported destination and route')
            options = self.evacuation_options(group_id, destination_id)['options']
            chosen = next((x for x in options if x['id'] == option_id), None)
            if chosen is None:
                raise ValueError('unknown or stale public evacuation route')
            journey = copy.deepcopy(chosen['journey'])
        elif destination_id is not None or option_id is not None:
            raise ValueError('shelter takes no destination or route')
        self._serial += 1
        request = 'protection-request-' + str(self._serial)
        self._tick(lambda: self._host.advance(1), (group_id, action, destination_id, journey, request))
        return {'request_id':request, 'status':'submitted'} if not self._host._censored else self.situation()

    def dispatch_hold(self, resource_id, target_grid, option_id, edge, protected_cell):
        self._active()
        # Match the hold host's public validation before starting either tick.
        # Once the tick begins, any error still requires a trusted restore.
        self._host._validate_mission(target_grid, edge, protected_cell)
        options = self._host.route_options(resource_id, target_grid)['options']
        if not any(item['id'] == option_id for item in options):
            raise ValueError('unknown or stale public route option')
        self._tick(lambda: self._host.dispatch_hold(resource_id, target_grid, option_id, edge, protected_cell))
        return self.situation()

    def advance(self, minutes=1):
        self._active()
        if type(minutes) is not int or not 1 <= minutes <= 60:
            raise ValueError('advance requires 1..60 minutes')
        for _ in range(minutes):
            self._tick(lambda: self._host.advance(1))
            if self._host._censored or self._host._complete:
                break
        return self.situation()

    def _sync(self):
        reports = self._host._bus.reports
        for record in reports[self._reports_seen:]:
            group = record.get('payload', {}).get('group')
            if type(group) is not dict or group.get('id') not in self._known:
                continue
            identifier = group['id']
            observed = record['observed_at']
            # Resident updates are emitted after movement and exposure for the
            # minute; command-result snapshots were taken before those effects.
            rank = 1 if record['kind'] == 'resident_update' else 0
            if group.get('exposed') is True:
                previous = self._exposed_at[identifier]
                self._exposed_at[identifier] = observed if previous is None else min(previous, observed)
            if (observed, rank) > (self._known_at[identifier], self._known_rank[identifier]):
                self._known[identifier] = copy.deepcopy(group)
                self._known_at[identifier], self._known_rank[identifier] = observed, rank
            # An earlier or same-minute snapshot may arrive after exposure was
            # reported. Exposure is irreversible; it has its own timestamp.
            self._known[identifier]['exposed'] = self._exposed_at[identifier] is not None
        self._reports_seen = len(reports)

    def _tick(self, run_host, command=None):
        try:
            host = self._host
            next_minute = host._minute + 1
            wind = weather.conditions(host._weather._profile, next_minute)['wind']
            burning = host._fire.burning()
            ops = Operations.restore(self._ops.checkpoint())
            dose = copy.deepcopy(self._dose)
            pending = []
            def emit(source, kind, payload, **kwargs):
                payload = copy.deepcopy(payload)
                identifier = payload.get('group_id')
                if identifier in ops.groups:
                    payload['group'] = _view(ops.groups[identifier])
                pending.append((source,kind,payload,copy.deepcopy(kwargs)))
            if command:
                group, action, destination, journey, request = command
                accepted = True
                try:
                    ops.protect(group, action, destination, journey, next_minute)
                except ValueError:
                    accepted = False
                emit('operations','protection_request_result',
                     dict(request_id=request, group_id=group, accepted=accepted))
            before = {k:_view(v) for k,v in ops.groups.items()}
            def safe(position, kind):
                return host._router.safe_position(position, burning, wind, mode='engine', excluded_cells=host._excluded)
            def movement(journey, minute, kind):
                return host._router.safe_journey_segment(journey, minute-1, burning, wind,
                                                        mode='engine', excluded_cells=host._excluded)
            ops.tick(next_minute, wind=wind, daylight=True, hazard_check=safe, movement_check=movement,
                     suppress=lambda unit:False, emit=emit)
            run_host()
            if host._minute == next_minute - 1 and host._censored:
                return
            if host._minute != next_minute:
                raise RuntimeError('host and population clocks differ')
            if not host._censored:
                burning_after = host._fire.burning()
                for identifier, group in ops.groups.items():
                    point = host._router._cell(group['position'])
                    threatened = any(math.dist(point, cell) <= 1.5 for cell in burning_after)
                    if threatened:
                        dose[identifier] += 2 if group['status'] == 'sheltering' else 10
                    if list(point) in burning_after or dose[identifier] >= 180:
                        group['exposed'] = True
                    if _view(group) != before[identifier]:
                        emit(identifier,'resident_update',{'group':_view(group)},role='resident',channel='radio')
            self._ops, self._dose = ops, dose
            if host._censored:
                return
            for source, kind, payload, kwargs in pending:
                host._bus.emit(source,kind,payload,next_minute,**kwargs)
            self._sync()
        except BaseException:
            self._poisoned = True
            raise

    def checkpoint(self):
        self._ensure()
        value = dict(version=VERSION, identity=copy.deepcopy(self._identity), host=self._host.checkpoint(),
            population=self._ops.checkpoint(), dose=copy.deepcopy(self._dose), known=copy.deepcopy(self._known),
            known_at=copy.deepcopy(self._known_at), known_rank=copy.deepcopy(self._known_rank),
            exposed_at=copy.deepcopy(self._exposed_at), destinations=copy.deepcopy(self._destinations),
            reports_seen=self._reports_seen, serial=self._serial)
        value['sha256'] = api._hash(value)
        return value

    def restore(self, checkpoint):
        try:
            value = copy.deepcopy(checkpoint)
            keys = {'version','identity','host','population','dose','known','known_at','known_rank','exposed_at','destinations',
                    'reports_seen','serial','sha256'}
            if (type(value) is not dict or set(value) != keys or value['version'] != VERSION
                or api._encoded(value['identity']) != api._encoded(self._identity) or _source() != self._source
                or value['sha256'] != api._hash({k:v for k,v in value.items() if k != 'sha256'})):
                raise ValueError('population checkpoint identity or integrity differs')
            ops = Operations.restore(value['population'])
            roster = {g['id']:g for g in self._catalog['groups']}
            destinations = {d['id']:dict(d,status='open') for d in self._catalog['destinations']}
            if (ops.profile != PROFILE or ops.resources or ops.destinations != destinations
                or set(ops.groups) != set(roster) or any(ops.groups[k]['count'] != roster[k]['count']
                    or ops.groups[k]['home'] != roster[k]['position'] for k in roster)
                or set(value['dose']) != set(roster) or any(type(x) is not int or x < 0 for x in value['dose'].values())
                or set(value['known']) != set(roster) or set(value['known_at']) != set(roster)
                or set(value['known_rank']) != set(roster) or set(value['exposed_at']) != set(roster)
                or any(type(rank) is not int or rank not in (-1,0,1) for rank in value['known_rank'].values())
                or any(type(value['known'][k].get('exposed')) is not bool
                       or value['known'][k]['exposed'] != (value['exposed_at'][k] is not None) for k in roster)
                or value['destinations'] != destinations or type(value['serial']) is not int or value['serial'] < 0):
                raise ValueError('population checkpoint roster or configuration differs')
            self._host.restore(value['host'])
            minute = self._host._minute
            if (ops.last_minute != (minute if minute else None) or type(value['reports_seen']) is not int
                or value['reports_seen'] != len(self._host._bus.reports)
                or any(type(t) is not int or not 0 <= t <= minute for t in value['known_at'].values())
                or any(t is not None and (type(t) is not int or not 0 <= t <= minute)
                       for t in value['exposed_at'].values())):
                raise ValueError('population and host clocks or reports differ')
            self._ops, self._dose = ops, value['dose']
            self._known, self._known_at, self._destinations = value['known'], value['known_at'], value['destinations']
            self._known_rank, self._exposed_at = value['known_rank'], value['exposed_at']
            self._reports_seen, self._serial = value['reports_seen'], value['serial']
            self._poisoned = False
            if api._encoded(self.checkpoint()) != api._encoded(value):
                raise ValueError('population checkpoint restoration differs')
        except BaseException:
            self._poisoned = True
            raise
