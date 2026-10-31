"""Synthetic-only weather/mixed-fire and real ground movement slice.

Trusted single-driver component, not a complete incident-day API or actor sandbox.
"""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations, _same_position
from eval import incident_weather_v1 as weather
from wildfire_expanded_incident_v1.host import ExpandedIncidentV1, _resource_view
from wildfire_expanded_incident_v1.weather_fire_v1 import WeatherMixedFireDiagnosticV1

VERSION = 'wildfire-expanded-weather-ground-movement-v1'


def _source():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _document(contract):
    return dict(version=api._contract_module.VERSION,
        grid=dict(width=contract.width, height=contract.height, crs=contract.crs,
                  transform=list(contract.transform)),
        domain_mask=[list(row) for row in contract.domain],
        surface=[list(row) for row in contract.surface], fuel=[list(row) for row in contract.fuel],
        elevation=[list(row) for row in contract.elevation], access=[list(row) for row in contract.access],
        source_pins=dict(contract.source_pins))


class WeatherMixedMovementV1(ExpandedIncidentV1):
    """Weather-coupled mixed fire and ground movement, with no suppression.

    Inputs and catalog must describe a synthetic exercise with affirmative access;
    v4 source inputs currently have unknown access and cannot admit a resource.
    All checkpoints, constructor data and Python object internals are private.
    """
    def __init__(self, inputs, drafts, *, expected_drafts_sha256, case_id,
                 network, expected_network_sha256, catalog, expected_catalog_sha256,
                 containment_config, pressure_scale, opt_in=False):
        if opt_in is not True:
            raise ValueError('explicit synthetic movement opt-in required')
        diagnostic = WeatherMixedFireDiagnosticV1(inputs, drafts,
            expected_drafts_sha256=expected_drafts_sha256, case_id=case_id,
            containment_config=containment_config, pressure_scale=pressure_scale,
            opt_in_weather_fire=True)
        case = diagnostic._case
        if any(event.get('kind') != 'wind' for event in case['events']) or case.get('incoming_reports'):
            raise ValueError('non-weather events and incoming reports are unsupported')
        matches = [c for c in catalog.get('cases', []) if c.get('id') == case_id]
        if (len(matches) != 1 or matches[0].get('initial_fire') != case['initial_fire']
                or matches[0].get('wind') != case['wind'] or matches[0].get('seed') != case['seed']
                or not case.get('centers') or matches[0].get('center') != case['centers'][0]):
            raise ValueError('synthetic operations case differs from weather draft')
        document = _document(inputs.contract)
        if api._hash(document) != inputs.contract.document_sha256:
            raise ValueError('mixed contract cannot be reproduced')
        super().__init__(document, inputs.contract.document_sha256,
            expected_source_pins=dict(inputs.contract.source_pins), network=network,
            expected_network_sha256=expected_network_sha256, catalog=catalog,
            expected_catalog_sha256=expected_catalog_sha256, case_id=case_id,
            containment_config=containment_config, pressure_scale=pressure_scale, opt_in=True)
        # Ground units must remain safe throughout the on-scene setup delay.
        self._ops.profile['revalidate_setup_safety'] = True
        self._slice_source = _source()
        self._weather = diagnostic
        while diagnostic.situation()['status'] == 'active' and diagnostic._weather_kind == 'warm':
            diagnostic.step()
        # Weather profiles normalize vectors to floats. Preserve checkpoint
        # compatibility when an initial censor prevents the first weather tick.
        if diagnostic._warmup_done == 0 and diagnostic._minute == 0:
            diagnostic._fire._fire.wind = tuple(weather.conditions(diagnostic._profile, 0)['wind'])
        self._fire = diagnostic._fire
        self._identity.update(version=VERSION, source_slice=self._slice_source,
                              fire=self._fire.identity, weather=copy.deepcopy(diagnostic._identity))
        self._censored = diagnostic.situation()['status'] == 'censored'
        self._complete = False
        self._bus = EventBus(case['seed'], [case['centers'][0]])
        if not self._censored:
            self._emit('handover', 'handover',
                {'text': 'Synthetic exercise handover; fire observations may become stale.',
                 'cells': copy.deepcopy(self._known_fire)}, role='center', delay=0)
            self._weather_report(0)
            changed = self._guard_unsafe_base(self._ops, self._emit)
            for identifier in changed:
                self._emit(identifier, 'resource_status',
                           {'resource': _resource_view(self._ops.resources[identifier])}, actionable=True)
            self._bus.tick(0, {})

    def _ensure(self):
        super()._ensure()
        if self._ops.profile.get('revalidate_setup_safety') is not True:
            self._poisoned = True
            raise RuntimeError('setup safety revalidation is required')
        if self._slice_source != _source():
            self._poisoned = True
            raise RuntimeError('movement slice source changed')
        self._weather._ensure()

    def situation(self):
        result = super().situation()
        result['version'] = VERSION
        return result

    def _weather_report(self, minute):
        for record in self._weather._profile.records:
            if record.minute == minute:
                payload = dict(wind=list(record.wind), temperature_c=record.temperature_c,
                    relative_humidity_pct=record.relative_humidity_pct,
                    rain_mm_per_hour=record.rain_mm_per_hour,
                    text='Synthetic exercise weather; uncalibrated proxy.')
                self._bus.emit('weather', 'weather_update', payload, minute, channel='radio',
                               delay=record.received_at - record.minute)

    def _wind(self):
        return weather.conditions(self._weather._profile, self._minute)['wind']

    def _safe_position(self, position, kind):
        return self._router.safe_position(position, self._fire.burning(), self._wind(),
            mode=kind, excluded_cells=self._excluded)

    def _movement(self, journey, minute, kind):
        return self._router.safe_journey_segment(journey, minute - 1, self._fire.burning(),
            self._wind(), mode=kind, excluded_cells=self._excluded)

    def _guard_unsafe_base(self, ops, emit):
        """Do not advertise ground units at a hazardous base as available."""
        changed = []
        for unit in ops.resources.values():
            state = unit['state']
            if (unit['kind'] in ('crew', 'engine') and state in ('available', 'on_scene', 'working', 'refilling', 'resting', 'unavailable')
                    and _same_position(unit['position'], unit['base_position'])
                    and not self._safe_position(unit['position'], unit['kind'])):
                if state == 'refilling':
                    ops._cancel_refill(unit)
                unit['state'] = 'needs_assistance'
                unit['journey'] = None
                unit.pop('blocked_state', None)
                changed.append(unit['id'])
                if state in ('on_scene', 'working'):
                    emit('operations', 'movement_blocked', {'resource_id': unit['id'],
                         'movement_state': state, 'position': copy.deepcopy(unit['position'])}, actionable=True)
        return changed

    def _tick(self, command=None):
        if not self._fire._check():
            self._weather._status = 'censored'
            self._censored = True
            return
        self._minute += 1
        wind = self._wind()
        if command is not None:
            unit = self._ops.resources[command['resource_id']]
            journey = command['option']['journey']
            hazards = self._router._hazards(self._fire.burning(), wind) | self._excluded
            safe = (all(self._router._safe_line(leg['coordinates'], hazards, unit['kind'])
                        for leg in journey['legs']) and self._safe_position(unit['position'], unit['kind']))
            accepted = bool(safe and self._ops.dispatch(unit['id'], 'incident', command['target'], journey, self._minute))
            self._emit('operations', 'dispatch_result', {'resource_id': unit['id'], 'accepted': accepted})
        before = {key: unit['state'] for key, unit in self._ops.resources.items()}
        self._ops.tick(self._minute, wind=wind, daylight=True,
            hazard_check=self._safe_position, movement_check=self._movement,
            suppress=lambda unit: False, emit=self._emit)
        self._guard_unsafe_base(self._ops, self._emit)
        state = self._weather.step()
        self._guard_unsafe_base(self._ops, self._emit)
        if state['status'] == 'censored':
            self._censored = True
            return
        self._weather_report(self._minute)
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
        self._complete = state['status'] == 'diagnostic_end'

    def checkpoint(self):
        self._ensure()
        try:
            value = dict(version=VERSION, identity=copy.deepcopy(self._identity), minute=self._minute,
                censored=self._censored, complete=self._complete, weather=self._weather.checkpoint(),
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
            keys = {'version','identity','minute','censored','complete','weather','operations',
                    'communications','known','known_at','known_fire','sha256'}
            if (type(value) is not dict or set(value) != keys or value['version'] != VERSION
                or api._encoded(value['identity']) != api._encoded(self._identity)
                or self._slice_source != _source()
                or value['sha256'] != api._hash({k:v for k,v in value.items() if k != 'sha256'})
                or type(value['minute']) is not int or not 0 <= value['minute'] <= 1440
                or type(value['censored']) is not bool or type(value['complete']) is not bool
                or value['complete'] != (value['minute'] == 1440 and not value['censored'])
                or value['operations']['last_minute'] != (value['minute'] if value['minute'] else None)):
                raise ValueError('movement checkpoint identity or clock differs')
            self._weather.restore(value['weather'])
            if (self._weather._minute != value['minute']
                or (self._weather._status == 'censored') != value['censored']
                or (self._weather._status == 'diagnostic_end') != value['complete']):
                raise ValueError('weather and operations clocks differ')
            ops, bus = Operations.restore(value['operations']), EventBus.restore(value['communications'])
            if ops.profile.get('revalidate_setup_safety') is not True:
                raise ValueError('setup safety revalidation is required')
            if bus.seed != self._case['seed'] or bus.checkpoint()['last_tick'] > value['minute']:
                raise ValueError('communication seed or clock differs')
            self._fire, self._ops, self._bus = self._weather._fire, ops, bus
            self._minute, self._censored, self._complete = value['minute'], value['censored'], value['complete']
            self._known, self._known_at, self._known_fire = value['known'], value['known_at'], value['known_fire']
            self._poisoned = False
            if api._encoded(self.checkpoint()) != api._encoded(value):
                raise ValueError('checkpoint restoration differs')
        except BaseException:
            self._poisoned = True
            raise
