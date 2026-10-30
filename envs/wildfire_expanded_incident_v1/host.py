"""Bounded synthetic incident-day host; not an incident-day benchmark."""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from adapter_fire_v1 import ExpandedContainmentFireV1
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations
from envs.wildfire_response.incident_day.routing import HazardRouter
from wildfire_core.mobility_routing import RoadNetwork

HOST_VERSION = 'wildfire-expanded-incident-host-v1'
CATALOG_VERSION = 'wildfire-expanded-incident-catalog-v1'
PROFILE = dict(mobilization_minutes=1, ground_setup_minutes=5, duty_minutes=240,
               rest_minutes=60, engine_litres_per_minute=500)


def _source():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _resource_view(unit):
    fields = ('id', 'kind', 'position', 'base_position', 'home_base', 'state',
              'water_litres', 'capacity_litres', 'work_remaining', 'available_at')
    return {k: copy.deepcopy(unit[k]) for k in fields if k in unit}


def _catalog(value, expected, inputs_hash, network_hash):
    value = copy.deepcopy(value)
    if (type(value) is not dict or set(value) != {'version', 'role', 'inputs_sha256', 'network_sha256', 'cases'}
            or value['version'] != CATALOG_VERSION or value['role'] != 'synthetic_fixture_only'
            or type(expected) is not str or api._hash(value) != expected
            or value['inputs_sha256'] != inputs_hash or value['network_sha256'] != network_hash
            or type(value['cases']) is not list or not value['cases']):
        raise ValueError('catalog identity, role, or source pins differ')
    ids = set()
    for case in value['cases']:
        if (type(case) is not dict or set(case) != {'id', 'seed', 'initial_fire', 'wind', 'resources', 'center'}
                or type(case['id']) is not str or not case['id'] or case['id'] in ids
                or type(case['seed']) is not int or type(case['initial_fire']) is not list
                or type(case['resources']) is not list or not case['resources']
                or type(case['center']) is not str or not case['center']):
            raise ValueError('invalid catalog case')
        ids.add(case['id'])
        resource_ids = set()
        for unit in case['resources']:
            if (type(unit) is not dict or set(unit) != {'id', 'kind', 'position', 'work_remaining', 'water_litres'}
                    or type(unit['id']) is not str or not unit['id'] or unit['id'] in resource_ids
                    or unit['kind'] not in ('crew', 'engine')
                    or type(unit['position']) is not list or len(unit['position']) != 2
                    or any(type(n) not in (int, float) or not math.isfinite(n) for n in unit['position'])
                    or type(unit['work_remaining']) is not int or unit['work_remaining'] <= 0
                    or type(unit['water_litres']) is not int or unit['water_litres'] < 0
                    or (unit['kind'] == 'crew' and unit['water_litres'] != 0)
                    or (unit['kind'] == 'engine' and unit['water_litres'] <= 0)):
                raise ValueError('invalid synthetic ground resource')
            resource_ids.add(unit['id'])
    return value


class ExpandedIncidentV1:
    """One trusted-driver episode with ground movement, delayed reports and no reward."""
    def __init__(self, inputs, expected_inputs_sha256, *, expected_source_pins,
                 network, expected_network_sha256, catalog, expected_catalog_sha256,
                 case_id, containment_config, pressure_scale, opt_in=False):
        self._inputs = api.load_inputs(inputs, expected_inputs_sha256,
            expected_source_pins=expected_source_pins, opt_in=opt_in)
        if self._inputs.crs != 'EPSG:25830':
            raise ValueError('routing requires EPSG:25830')
        self._network = RoadNetwork(network, expected_network_sha256)
        catalog = _catalog(catalog, expected_catalog_sha256, self._inputs.document_sha256, self._network.sha256)
        cases = [item for item in catalog['cases'] if item['id'] == case_id]
        if len(cases) != 1:
            raise ValueError('case must be in the independently pinned catalog')
        self._case = cases[0]
        self._excluded = frozenset((x, y) for y in range(self._inputs.height) for x in range(self._inputs.width)
            if not (self._inputs.domain[y][x] and self._inputs.surface[y][x] == 'land'
                    and self._inputs.access[y][x] == 'accessible'))
        self._unknown_access = any(not self._inputs.domain[y][x]
            or self._inputs.surface[y][x] in ('unknown', 'mixed')
            or self._inputs.access[y][x] == 'unknown'
            for y in range(self._inputs.height) for x in range(self._inputs.width))
        mask = [[int((x, y) not in self._excluded) for x in range(self._inputs.width)]
                for y in range(self._inputs.height)]
        grid = dict(width=self._inputs.width, height=self._inputs.height, crs=self._inputs.crs,
                    transform=list(self._inputs.transform))
        router_inputs = dict(grid=grid, fuel_classes=[list(r) for r in self._inputs.fuel],
                             study_mask=mask, fuel_known_mask=mask)
        # An omitted allowed_modes field is not affirmative access evidence.
        declared_edges = [key for key, edge in self._network.edges.items()
                          if 'allowed_modes' in edge]
        self._router = HazardRouter(self._network, router_inputs,
                                    [list(r) for r in self._inputs.elevation], walk_mask=mask,
                                    allowed_edge_ids=declared_edges)
        resources = []
        for resource in self._case['resources']:
            unit = copy.deepcopy(resource)
            cell = self._router._cell(unit['position'])
            if not self._router._inside(cell) or cell in self._excluded:
                raise ValueError('resource must start on confirmed accessible land')
            unit.update(home_base=self._case['center'], base_position=unit['position'][:],
                        capacity_litres=unit['water_litres'], state='available', available_at=0)
            resources.append(unit)
        self._fire = ExpandedContainmentFireV1(self._inputs, seed=self._case['seed'],
            containment_config=containment_config, pressure_scale=pressure_scale, wind=self._case['wind'])
        self._identity = dict(version=HOST_VERSION, source=_source(), inputs=self._inputs.document_sha256,
            network=self._network.sha256, catalog=expected_catalog_sha256, case=case_id,
            scenario=api._hash(self._case), fire=self._fire.identity)
        self._ops = Operations(resources, [], [], PROFILE)
        self._bus = EventBus(self._case['seed'], [self._case['center']])
        self._known = {u['id']: _resource_view(u) for u in resources}
        self._known_at = {u['id']: 0 for u in resources}
        self._known_fire = copy.deepcopy(self._case['initial_fire'])
        self._minute = 0
        self._censored = False
        self._complete = False
        self._poisoned = False
        try:
            if not self._fire.change({'ignite': self._case['initial_fire']}):
                self._censored = True
            else:
                self._bus.emit('handover', 'handover',
                    {'text': 'Synthetic exercise handover; fire observations may become stale.',
                     'cells': copy.deepcopy(self._known_fire)}, 0, role='center', delay=0)
                self._bus.tick(0, {})
        except BaseException:
            self._poisoned = True
            raise

    def _ensure(self):
        if self._poisoned:
            raise RuntimeError('host indeterminate; restore a trusted checkpoint')
        if self._identity['source'] != _source() or self._fire.identity != self._identity['fire']:
            self._poisoned = True
            raise RuntimeError('host identity changed')

    def _active(self):
        self._ensure()
        if self._censored:
            raise api.CensoredError('episode censored')
        if self._complete:
            raise RuntimeError('episode complete')

    def situation(self):
        self._ensure()
        if self._censored:
            return dict(version=HOST_VERSION, minute=self._minute, status='censored',
                        model_limit='fire reached unsupported or boundary state')
        return dict(version=HOST_VERSION, minute=self._minute,
                    status='complete' if self._complete else 'active',
                    resources=copy.deepcopy(list(self._known.values())),
                    known_fire=copy.deepcopy(self._known_fire), fire_observed_at=0,
                    reports_cursor=len(self._bus.reports))

    def reports(self, *, after_cursor=0, limit=20):
        self._ensure()
        if self._censored:
            raise api.CensoredError('reports suppressed after censoring')
        return self._bus.get_reports(after_cursor=after_cursor, limit=limit)

    def route_options(self, resource_id, target_grid):
        self._active()
        if resource_id not in self._known:
            raise ValueError('unknown resource')
        unit = self._known[resource_id]
        result = self._router.options(unit['position'], target_grid, mode=unit['kind'],
            burning=self._known_fire, wind=self._case['wind'], excluded_cells=self._excluded)
        # The frozen router calls excluded cells blocked. Missing coverage is
        # not evidence of a restriction, even if this loses diagnostic detail.
        if not result['options'] and self._unknown_access:
            result['status'] = 'unmapped'
        return result

    def request_status(self):
        self._active()
        try:
            request = self._bus.request_status(self._case['center'], 'resources', self._minute)
            self._tick()
            return request if not self._censored else None
        except BaseException:
            self._poisoned = True
            raise

    def dispatch(self, resource_id, target_grid, option_id):
        self._active()
        options = self.route_options(resource_id, target_grid)['options']
        chosen = next((item for item in options if item['id'] == option_id), None)
        if chosen is None:
            raise ValueError('unknown or stale public route option')
        try:
            self._tick(dict(resource_id=resource_id, target=list(target_grid), option=chosen))
        except BaseException:
            self._poisoned = True
            raise
        return self.situation()

    def advance(self, minutes=1):
        self._active()
        if type(minutes) is not int or not 1 <= minutes <= 60:
            raise ValueError('advance requires 1..60 minutes')
        try:
            for _ in range(minutes):
                self._tick()
                if self._censored or self._complete:
                    break
        except BaseException:
            self._poisoned = True
            raise
        return self.situation()

    def _safe_position(self, position, kind):
        return self._router.safe_position(position, self._fire.burning(), self._case['wind'],
            mode=kind, excluded_cells=self._excluded)

    def _movement(self, journey, minute, kind):
        return self._router.safe_journey_segment(journey, minute - 1, self._fire.burning(),
            self._case['wind'], mode=kind, excluded_cells=self._excluded)

    def _emit(self, source, kind, payload, **kwargs):
        self._bus.emit(source, kind, payload, self._minute, **kwargs)

    def _tick(self, command=None):
        # Check before operations or clocks can change. The backend checks both
        # immediately around the physical step as well.
        if not self._fire._check():
            self._censored = True
            return
        self._minute += 1
        if command is not None:
            unit = self._ops.resources[command['resource_id']]
            journey = command['option']['journey']
            hazards = self._router._hazards(self._fire.burning(), self._case['wind']) | self._excluded
            safe = all(self._router._safe_line(leg['coordinates'], hazards, unit['kind'])
                       for leg in journey['legs']) and self._safe_position(unit['position'], unit['kind'])
            accepted = bool(safe and self._ops.dispatch(unit['id'], 'incident', command['target'], journey, self._minute))
            self._emit('operations', 'dispatch_result', {'resource_id': unit['id'], 'accepted': accepted})
        before = {key: unit['state'] for key, unit in self._ops.resources.items()}
        self._ops.tick(self._minute, wind=self._case['wind'], daylight=True,
            hazard_check=self._safe_position, movement_check=self._movement,
            suppress=lambda unit: False, emit=self._emit)
        if not self._fire.step():
            self._censored = True
            return
        for key, unit in self._ops.resources.items():
            if before[key] != unit['state'] or self._minute % 30 == 0:
                self._emit(key, 'resource_status', {'resource': _resource_view(unit)}, actionable=True)
        center_view = {self._case['center']: {'resources': {'observed_at': min(self._known_at.values(), default=0),
                                        'resources': copy.deepcopy(list(self._known.values()))}}}
        for record in self._bus.tick(self._minute, center_view):
            if record['kind'] == 'resource_status':
                resource = record['payload']['resource']
                identifier = resource['id']
                if record['observed_at'] >= self._known_at[identifier]:
                    self._known[identifier] = resource
                    self._known_at[identifier] = record['observed_at']
        self._complete = self._minute == 1440

    def outcomes(self):
        raise NotImplementedError('this host has no validated outcome or reward model')

    def checkpoint(self):
        self._ensure()
        try:
            value = dict(version=HOST_VERSION, identity=copy.deepcopy(self._identity), minute=self._minute,
                censored=self._censored, complete=self._complete, fire=self._fire.snapshot(),
                operations=self._ops.checkpoint(), communications=self._bus.checkpoint(),
                known=copy.deepcopy(self._known), known_at=copy.deepcopy(self._known_at),
                known_fire=copy.deepcopy(self._known_fire))
            value['sha256'] = api._hash(value)
            return value
        except BaseException:
            self._poisoned = True
            raise

    def restore(self, checkpoint):
        try:
            value = copy.deepcopy(checkpoint)
            if (type(value) is not dict or set(value) != {'version', 'identity', 'minute', 'censored', 'complete',
                    'fire', 'operations', 'communications', 'known', 'known_at', 'known_fire', 'sha256'}
                    or value['version'] != HOST_VERSION or api._encoded(value['identity']) != api._encoded(self._identity)
                    or self._identity['source'] != _source()
                    or value['sha256'] != api._hash({k: v for k, v in value.items() if k != 'sha256'})):
                raise ValueError('checkpoint identity or integrity differs')
            minute = value['minute']
            if (type(minute) is not int or not 0 <= minute <= 1440 or type(value['censored']) is not bool
                    or type(value['complete']) is not bool or value['complete'] != (minute == 1440 and not value['censored'])
                    or value['operations']['last_minute'] != (minute if minute else None)):
                raise ValueError('checkpoint host and operations clocks differ')
            fire = ExpandedContainmentFireV1(self._inputs, seed=self._case['seed'],
                containment_config=self._identity['fire']['config'],
                pressure_scale=self._identity['fire']['pressure_scale'], wind=self._case['wind'])
            fire.restore(value['fire'])
            if fire.snapshot()['tick'] != minute or fire.censored != value['censored']:
                raise ValueError('checkpoint fire, censor or clock differs')
            ops = Operations.restore(value['operations'])
            bus = EventBus.restore(value['communications'])
            if bus.checkpoint()['last_tick'] > minute:
                raise ValueError('checkpoint communication clock differs')
            self._fire, self._ops, self._bus = fire, ops, bus
            self._minute, self._censored, self._complete = minute, value['censored'], value['complete']
            self._known, self._known_at, self._known_fire = value['known'], value['known_at'], value['known_fire']
            self._poisoned = False
            if api._encoded(self.checkpoint()) != api._encoded(value):
                raise ValueError('checkpoint restoration is not exact')
        except BaseException:
            self._poisoned = True
            raise


SOURCE_HOST_VERSION = 'wildfire-expanded-source-fire-diagnostic-v1'


class SourceGroundedDiagnosticV1(ExpandedIncidentV1):
    """Private, passive source-grounded projection; not the full draft lifecycle.

    Only initial ignition, warm-up and unchanged initial wind are simulated.
    Resources, access, population and all future events are unavailable; stop
    before the first scheduled event instead of silently discarding it.
    """
    def __init__(self, document, *, expected_inputs_sha256, expected_source_pins,
                 evidence, expected_evidence_sha256, drafts, expected_drafts_sha256,
                 case_id, containment_config, pressure_scale, opt_in_source_grounded=False):
        if opt_in_source_grounded is not True:
            raise ValueError('explicit private source-grounded opt-in required')
        from . import source_converter_v1 as converter
        if (type(evidence) is not dict or api._hash(evidence) != expected_evidence_sha256
                or evidence.get('version') != converter.VERSION
                or evidence.get('inputs_sha256') != expected_inputs_sha256
                or evidence.get('drafts_sha256') != expected_drafts_sha256
                or evidence.get('road', {}).get('status') != 'source_candidate_not_operational_access'
                or type(drafts) is not dict or drafts.get('version') != 'wildfire-extended-scenario-drafts-v1'
                or drafts.get('status') != 'unqualified-not-runnable'
                or drafts.get('sha256') != expected_drafts_sha256
                or api._hash({k: v for k, v in drafts.items() if k != 'sha256'}) != expected_drafts_sha256):
            raise ValueError('source conversion or draft identity differs')
        self._inputs = api.load_inputs(document, expected_inputs_sha256,
                                       expected_source_pins=expected_source_pins, opt_in=True)
        if (self._inputs.document_sha256 != evidence['inputs_sha256']
                or any(item != 'unknown' for row in self._inputs.access for item in row)
                or dict(self._inputs.source_pins).get('access') != evidence['road']['sha256']):
            raise ValueError('source access must remain unknown')
        matches = [x for x in drafts['cases'] if x['id'] == case_id]
        if len(matches) != 1:
            raise ValueError('unknown source draft')
        draft = copy.deepcopy(matches[0])
        if draft.get('sha256') != api._hash({k: v for k, v in draft.items() if k != 'sha256'}):
            raise ValueError('source draft hash differs')
        if (type(draft.get('warmup_minutes')) is not int or not 0 <= draft['warmup_minutes'] <= 1440
                or not draft.get('centers') or not draft.get('weather_schedule')):
            raise ValueError('invalid draft lifecycle')
        changes = [item['minute'] for item in draft['weather_schedule'] if item['minute'] > 0]
        changes += [item['minute'] for item in draft['events'] if item['minute'] > 0]
        if any(type(x) is not int or not 1 <= x <= 1440 for x in changes):
            raise ValueError('invalid future event time')
        self._first_event = min(changes, default=1441)
        self._case = dict(id=case_id, seed=draft['seed'], initial_fire=copy.deepcopy(draft['initial_fire']),
                          wind=copy.deepcopy(draft['wind']), center=draft['centers'][0])
        self._fire = ExpandedContainmentFireV1(self._inputs, seed=draft['seed'],
            containment_config=containment_config, pressure_scale=pressure_scale, wind=draft['wind'])
        self._identity = dict(version=SOURCE_HOST_VERSION, source=_source(),
            converter=hashlib.sha256(Path(converter.__file__).read_bytes()).hexdigest(),
            inputs=expected_inputs_sha256, evidence=expected_evidence_sha256,
            drafts=expected_drafts_sha256, scenario=draft['sha256'], case=case_id,
            road=evidence['road']['sha256'], fire=self._fire.identity)
        self._ops = Operations([], [], [], PROFILE)
        self._bus = EventBus(draft['seed'], [draft['centers'][0]])
        self._known, self._known_at = {}, {}
        self._known_fire = copy.deepcopy(draft['initial_fire'])
        self._minute = 0
        self._warmup_target = draft['warmup_minutes']
        self._warmup_done = 0
        self._censored = self._complete = self._blocked = self._poisoned = False
        try:
            if not self._fire.change({'ignite': draft['initial_fire']}):
                self._censored = True
                return
            for _ in range(self._warmup_target):
                safe = self._fire.step()
                self._warmup_done += 1
                if not safe:
                    self._censored = True
                    return
        except BaseException:
            self._poisoned = True
            raise

    def _ensure(self):
        super()._ensure()
        from . import source_converter_v1 as converter
        if self._identity['converter'] != hashlib.sha256(Path(converter.__file__).read_bytes()).hexdigest():
            self._poisoned = True
            raise RuntimeError('converter source changed')

    def situation(self):
        self._ensure()
        status = ('censored' if self._censored else 'blocked' if self._blocked else
                  'diagnostic_end' if self._complete else 'active')
        return dict(version=SOURCE_HOST_VERSION, minute=self._minute, status=status,
                    mode='private_passive_fire_only', warmup_steps=self._warmup_done)

    def reports(self, **kwargs):
        raise NotImplementedError('source diagnostic has no actor report lifecycle')

    def route_options(self, *args, **kwargs):
        raise NotImplementedError('source road candidate is not validated for operational routing')

    def dispatch(self, *args, **kwargs):
        raise NotImplementedError('source diagnostic does not dispatch resources')

    def request_status(self):
        raise NotImplementedError('source diagnostic does not simulate source communications')

    def advance(self, minutes=1):
        self._active()
        if self._blocked:
            raise RuntimeError('diagnostic stopped before unsupported scheduled event')
        if type(minutes) is not int or not 1 <= minutes <= 60:
            raise ValueError('advance requires 1..60 minutes')
        try:
            for _ in range(minutes):
                if self._minute + 1 >= self._first_event:
                    self._blocked = True
                    break
                self._tick()
                if self._censored or self._complete:
                    break
        except BaseException:
            self._poisoned = True
            raise
        return self.situation()

    def checkpoint(self):
        value = super().checkpoint()
        value.pop('sha256')
        value.update(version=SOURCE_HOST_VERSION, warmup_target=self._warmup_target,
                     warmup_done=self._warmup_done, first_event=self._first_event, blocked=self._blocked)
        value['sha256'] = api._hash(value)
        return value

    def restore(self, checkpoint):
        try:
            value = copy.deepcopy(checkpoint)
            expected = {'version', 'identity', 'minute', 'censored', 'complete', 'fire', 'operations',
                        'communications', 'known', 'known_at', 'known_fire', 'sha256', 'warmup_target',
                        'warmup_done', 'first_event', 'blocked'}
            if (type(value) is not dict or set(value) != expected or value['version'] != SOURCE_HOST_VERSION
                    or api._encoded(value['identity']) != api._encoded(self._identity)
                    or self._identity['source'] != _source()
                    or value['sha256'] != api._hash({k: v for k, v in value.items() if k != 'sha256'})
                    or value['warmup_target'] != self._warmup_target or value['first_event'] != self._first_event
                    or type(value['warmup_done']) is not int or not 0 <= value['warmup_done'] <= self._warmup_target
                    or type(value['minute']) is not int or not 0 <= value['minute'] <= 1440
                    or type(value['censored']) is not bool or type(value['blocked']) is not bool
                    or type(value['complete']) is not bool
                    or (not value['censored'] and value['warmup_done'] != self._warmup_target)
                    or value['operations']['last_minute'] != (value['minute'] if value['minute'] else None)):
                raise ValueError('source checkpoint identity or clock differs')
            fire = ExpandedContainmentFireV1(self._inputs, seed=self._case['seed'],
                containment_config=self._identity['fire']['config'], pressure_scale=self._identity['fire']['pressure_scale'],
                wind=self._case['wind'])
            fire.restore(value['fire'])
            if (fire.snapshot()['tick'] != value['minute'] + value['warmup_done']
                    or fire.censored != value['censored']):
                raise ValueError('source fire and censor clocks differ')
            ops, bus = Operations.restore(value['operations']), EventBus.restore(value['communications'])
            if bus.checkpoint()['last_tick'] > value['minute']:
                raise ValueError('source communication clock differs')
            self._fire, self._ops, self._bus = fire, ops, bus
            self._minute, self._censored, self._complete = value['minute'], value['censored'], value['complete']
            self._warmup_done, self._blocked = value['warmup_done'], value['blocked']
            self._known, self._known_at, self._known_fire = value['known'], value['known_at'], value['known_fire']
            self._poisoned = False
            if api._encoded(self.checkpoint()) != api._encoded(value):
                raise ValueError('source checkpoint restoration is not exact')
        except BaseException:
            self._poisoned = True
            raise
