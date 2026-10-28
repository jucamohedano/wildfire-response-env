"""Logical contradictions are errors; plausible challenges are not automatically errors."""
import copy
from datetime import datetime
import math

from ..regional.malaga.pack import engine_pack
from ..regional.prepared.data import FUEL_MAPPING, load_inputs
from ..regional.prepared.environment import PreparedFire
from ..regional.topographic.data import crop_inputs, load_domain
from ..regional.topographic.fire import BOUNDARY_GUARD_CELLS


VERSION = 'wildfire-scenario-quality-v1'
RULES = {
    'schema': ('error', 'Bounded structured scenario, lists, identities, seeds and explicit synthetic label'),
    'timeline': ('error', 'Integer nonnegative clocks; observation <= receipt; scheduled inputs inside horizon'),
    'clock_alignment': ('error', 'Local daylight clock must agree with the pinned weather/incident clock'),
    'geography': ('error', 'Representable locations; ignitions on supported fuel outside censor guard'),
    'parameters': ('error', 'Finite engine-supported winds and nonnegative delays; coherent protection parameters'),
    'references': ('error', 'Known resources and unlocated report references'),
    'family': ('error', 'Declared family has the actual incident/report/failure conditions it promises'),
    'duplicate_lineage': ('error', 'Declared forwards retain original source, role, observation time and location'),
    'catalog_integrity': ('error', 'Unique IDs/seeds/configurations, correct hashes/counts, no cross-split layouts'),
    'compile_failure': ('error', 'Every scheduled concrete variant must materialize'),
    'preignition_wind_shift': ('warning', 'A wind-shift exercise should test a change after ignition'),
    'short_response_window': ('warning', 'Late incidents may have no useful response period before cutoff'),
    'ground_arrival_outside_horizon': ('warning', 'Even optimistic ground arrival is outside the episode'),
    'population_at_ignition': ('warning', 'A settlement/refuge starts burning before any decision can act'),
    'night_aircraft': ('info', 'Night/daylight and wind gates still apply; readiness alone is not operability'),
    'input_assumptions': ('error', 'Population, inventory and timing defaults must be structurally coherent'),
    'unmeasured_fuel_fallback': ('warning', 'Unsupported fuel cells use an exercise fallback, not measured vegetation'),
}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def clock(value):
    return type(value) is int and value >= 0


def make_context(prepared, domain):
    prepared = load_inputs(prepared, expected_sha256=prepared['sha256'])
    domain = load_domain(domain, prepared, domain['sha256'])
    inputs = crop_inputs(prepared, domain)
    pack = engine_pack(inputs['episode_pack'])
    population = PreparedFire(pack, 0, inputs).groups
    return dict(inputs=inputs, domain=domain, pack=pack, population=copy.deepcopy(population))


def point_valid(point, context, *, ignition=False):
    grid = context['inputs']['grid']
    if (not isinstance(point, list) or len(point) != 2 or any(type(value) is not int for value in point)
            or not 0 <= point[0] < grid['width'] or not 0 <= point[1] < grid['height']):
        return False
    column, row = point
    if (not context['inputs']['study_mask'][row][column]
            or context['domain']['height_support_mask'][row][column] != 1
            or not finite(context['domain']['elevations_metres'][row][column])
            or context['domain']['elevations_metres'][row][column] <= 0):
        return False
    if ignition:
        guard = BOUNDARY_GUARD_CELLS
        return (guard <= column < grid['width'] - guard and guard <= row < grid['height'] - guard
                and context['inputs']['fuel_valid_mask'][row][column] == 1
                and context['inputs']['fuel_classes'][row][column] in FUEL_MAPPING)
    return True


def wind_valid(wind):
    return isinstance(wind, list) and len(wind) == 2 and all(finite(value) and abs(value) <= 3 for value in wind)


def validate_context(context):
    findings = []
    pack, inputs = context['pack'], context['inputs']
    units = pack['scenario_inventory']
    bases = {base['id'] for base in pack['facilities']}
    assumptions = pack['assumptions']
    bad = len({unit['id'] for unit in units}) != len(units)
    for unit in units:
        kind = unit['kind']
        bad |= kind not in ('engine', 'crew', 'aircraft') or unit['home_base'] not in bases
        bad |= not clock(assumptions['mobilization_minutes'].get(kind)) or assumptions['mobilization_minutes'].get(kind, 0) == 0
        if kind in ('engine', 'aircraft'):
            bad |= not finite(unit.get('capacity_litres')) or unit.get('capacity_litres', 0) <= 0
        else:
            bad |= not clock(unit.get('people')) or unit.get('people') == 0
    for group in context['population'].values():
        bad |= (not clock(group['count']) or group['count'] == 0 or group['home'] == group['refuge']
                or not point_valid(group['home'], context) or not point_valid(group['refuge'], context))
    bad |= not finite(assumptions.get('population_speed_cells_per_tick')) or assumptions.get('population_speed_cells_per_tick', 0) <= 0
    if bad:
        findings.append(dict(code='input_assumptions', severity='error', location='context',
                             message='Population/inventory/mobilization defaults are inconsistent'))
    missing = sum(inputs['study_mask'][row][column] and not value
                  for row, values in enumerate(inputs['fuel_valid_mask']) for column, value in enumerate(values))
    if missing:
        findings.append(dict(code='unmeasured_fuel_fallback', severity='warning', location='context.fuel_valid_mask',
                             message=f'{missing} simulated cells use unmeasured synthetic scrub fallback'))
    return findings


def validate_task(task, context, *, family=None, horizon=120):
    findings = []

    def add(code, location, message):
        findings.append(dict(code=code, severity=RULES[code][0], location=location, message=message))

    if not clock(horizon) or not 1 <= horizon <= 1440:
        raise ValueError('Audit horizon must be an integer from one minute to one day')
    if not isinstance(task, dict) or not isinstance(task.get('scenario'), dict):
        add('schema', 'scenario', 'Task needs a structured scenario')
        return findings
    scenario = task['scenario']
    if (not isinstance(task.get('id'), str) or not task['id'] or scenario.get('id') != task['id']
            or task.get('scenario_id') != task['id'] or not clock(task.get('seed'))
            or type(scenario.get('seed')) is not int or scenario['seed'] != task['seed']
            or scenario.get('synthetic') is not True):
        add('schema', 'scenario', 'IDs/seeds must agree and synthetic generation must be explicit')
    communication = task.get('communication')
    if (not isinstance(communication, dict) or set(communication) != {'mission_swap_once'}
            or type(communication.get('mission_swap_once')) is not bool):
        add('schema', 'communication', 'Readback configuration needs an explicit boolean mission-swap flag')
    events, reports = scenario.get('events'), scenario.get('reports')
    if (not isinstance(events, list) or len(events) > 64
            or not isinstance(reports, list) or len(reports) > 100
            or any(not isinstance(entry, dict) for entry in [*(events or []), *(reports or [])])):
        add('schema', 'scenario', 'Expected at most 64 scheduled events and 100 structured initial reports')
        return findings
    if not wind_valid(scenario.get('wind')) or not clock(scenario.get('aircraft_ready_at')):
        add('parameters', 'scenario.wind', 'Wind must satisfy the engine [-3,3] model-unit contract; readiness is a clock')
    ignitions, shifts = [], []
    for index, event in enumerate(events):
        path = f'scenario.events[{index}]'
        if not clock(event.get('tick')) or event['tick'] >= horizon:
            add('timeline', path, 'Scheduled event must fall inside the episode')
        kind = event.get('kind')
        if kind == 'ignition':
            if not point_valid(event.get('location'), context, ignition=True):
                add('geography', path, 'Ignition escapes supported burnable terrain or starts in the censor guard')
            elif clock(event.get('tick')):
                ignitions.append(event)
        elif kind == 'wind':
            if not wind_valid(event.get('wind')):
                add('parameters', path, 'Wind event exceeds engine bounds or contains a non-finite value')
            elif clock(event.get('tick')):
                shifts.append(event)
        elif kind == 'daylight':
            if type(event.get('value')) is not bool:
                add('parameters', path, 'Daylight event needs a boolean')
        else:
            add('schema', path, 'Unknown incident event kind')
    by_id = {}
    for index, report in enumerate(reports):
        path = f'scenario.reports[{index}]'
        identifier = report.get('id')
        if (not isinstance(identifier, str) or not identifier or identifier in by_id or 'location' not in report
                or any(not isinstance(report.get(key), str) or not report[key]
                       for key in ('source', 'source_role', 'channel', 'text'))):
            add('schema', path, 'Reports need unique IDs and explicit source, role, channel and text')
        else:
            by_id[identifier] = report
        if (not clock(report.get('observed_at')) or not clock(report.get('received_at'))
                or not 0 <= report['observed_at'] <= report['received_at'] < horizon):
            add('timeline', path, 'Report observation must precede receipt, inside the episode')
        if report.get('location') is not None and not point_valid(report['location'], context):
            add('geography', path, 'Location claims must be representable even when deliberately incorrect')
    for identifier, report in by_id.items():
        if identifier.startswith(('copy-', 'forward-')):
            original = by_id.get('claim-0')
            if (original is None or any(report.get(key) != original.get(key)
                    for key in ('source', 'source_role', 'observed_at', 'location'))
                    or not clock(report.get('received_at')) or not clock(original.get('received_at'))
                    or report['received_at'] < original['received_at']):
                add('duplicate_lineage', 'scenario.reports.' + identifier,
                    'Frozen-generator forward no longer matches the original claim or arrives before it')
    transport, protection, grounded = (scenario.get(key) for key in ('transport', 'protection', 'grounded'))
    if not all(isinstance(value, dict) for value in (transport, protection, grounded)):
        add('schema', 'scenario', 'Transport, protection and grounded assumptions must be explicit')
        return findings
    for name in ('message_extra_delay_minutes', 'request_extra_delay_minutes'):
        if not clock(transport.get(name)):
            add('parameters', 'scenario.transport.' + name, 'Delay must be an integer nonnegative minute count')
    held = transport.get('resource_unavailable_until')
    units = {unit['id']: unit for unit in context['pack']['scenario_inventory']}
    if not isinstance(held, dict) or any(identifier not in units or not clock(value) for identifier, value in held.items()):
        add('references', 'scenario.transport.resource_unavailable_until', 'Availability needs known resource IDs and nonnegative clocks')
        held = {}
    if (not clock(grounded.get('ground_transit_minutes')) or grounded['ground_transit_minutes'] == 0
            or not clock(grounded.get('clarification_delay_minutes')) or grounded['clarification_delay_minutes'] == 0
            or not clock(grounded.get('local_clock_minute')) or grounded['local_clock_minute'] >= 1440):
        add('parameters', 'scenario.grounded', 'Transit/clarification must be positive minutes and local clock within one day')
    clarifications = grounded.get('clarification_locations', {})
    if not isinstance(clarifications, dict) or any(identifier not in by_id or by_id[identifier].get('location') is not None
            or not point_valid(point, context) for identifier, point in clarifications.items()):
        add('references', 'scenario.grounded.clarification_locations', 'Clarification must reference an unlocated report and a representable claim')
    for name in ('smoke_radius_cells', 'exposure_threshold', 'open_dose_per_tick', 'sheltered_dose_per_tick'):
        if not finite(protection.get(name)) or protection[name] <= 0:
            add('parameters', 'scenario.protection.' + name, 'Positive finite exposure assumptions are required')
    if (finite(protection.get('open_dose_per_tick')) and finite(protection.get('sheltered_dose_per_tick'))
            and protection['sheltered_dose_per_tick'] > protection['open_dose_per_tick']):
        add('parameters', 'scenario.protection', 'This shelter model cannot increase dose above open-air exposure')
    for name in ('broadcast_delay_minutes', 'first_broadcast_extra_delay_minutes', 'broadcast_failures'):
        if not clock(protection.get(name)) or name == 'broadcast_delay_minutes' and protection[name] == 0:
            add('parameters', 'scenario.protection.' + name, 'Delivery delay must be positive; extra delay/failure counts nonnegative')
    known_families = {'uncertain_smoke', 'false_alarm', 'conflicting_reports', 'wind_shift',
                      'overlapping_fires', 'delayed_reinforcement', 'unavailable_resources',
                      'failed_protection', 'smoke', 'duplicate_burst', 'conflicting_locations',
                      'multiple_fires', 'failed_protection_delivery'}
    expected = family if family is not None else scenario.get('family')
    if not isinstance(expected, str) or expected not in known_families:
        if family is not None:
            add('family', 'family', 'Unknown declared family; register its intent before auditing it')
        expected = None
    actual_shifts = []
    current_wind = [round(value, 2) for value in scenario['wind']] if wind_valid(scenario.get('wind')) else None
    for event in sorted(shifts, key=lambda entry: entry['tick']):
        next_wind = [round(value, 2) for value in event['wind']]
        if next_wind != current_wind:
            actual_shifts.append(event)
        current_wind = next_wind
    multiple = expected in ('overlapping_fires', 'unavailable_resources', 'multiple_fires')
    if expected == 'false_alarm' and ignitions:
        add('family', 'scenario.events', 'False-alarm family contains a scheduled fire')
    if expected is not None and expected != 'false_alarm' and not ignitions:
        add('family', 'scenario.events', 'Fire family contains no supported ignition')
    if multiple and len({tuple(event['location']) for event in ignitions}) < 2:
        add('family', 'scenario.events', 'Multiple-fire family needs distinct ignition locations')
    if expected in ('conflicting_reports', 'conflicting_locations'):
        locations = {tuple(report['location']) for report in reports if point_valid(report.get('location'), context)}
        if len(locations) < 2:
            add('family', 'scenario.reports', 'Conflicting-location family has no distinct location claims')
    if expected == 'wind_shift' and not actual_shifts:
        add('family', 'scenario.events', 'Wind-shift family has no effective wind change at engine precision')
    if expected in ('failed_protection', 'failed_protection_delivery') and not protection.get('broadcast_failures'):
        add('family', 'scenario.protection', 'Failed-protection family has no programmed delivery failure')
    if expected == 'delayed_reinforcement' and not (transport.get('request_extra_delay_minutes') and any(held.values())):
        add('family', 'scenario.transport', 'Delayed-reinforcement family needs request delay and a held resource')
    if expected == 'unavailable_resources' and len({units[identifier]['home_base'] for identifier, value in held.items() if value}) < 2:
        add('family', 'scenario.transport', 'Unavailable-resource family needs holds affecting more than one base')
    if ignitions:
        first = min(event['tick'] for event in ignitions)
        if expected == 'wind_shift' and actual_shifts and not any(event['tick'] > first for event in actual_shifts):
            add('preignition_wind_shift', 'scenario.events', 'Wind changes at or before ignition; this does not test adaptation to a growing fire')
        for event in ignitions:
            if horizon - event['tick'] < 6:
                add('short_response_window', 'scenario.events', 'Ignition is within six minutes of cutoff; review truncation/continuation intent')
            if event['tick'] == 0 and any(event['location'] in (group['home'], group['refuge'])
                                         for group in context['population'].values()):
                add('population_at_ignition', 'scenario.events', 'A settlement or refuge starts on fire before the agent can intervene')
        transit = grounded.get('ground_transit_minutes')
        if clock(transit) and transit > 0:
            minimum = min((held.get(unit['id'], 0) + context['pack']['assumptions']['mobilization_minutes'][unit['kind']]
                           + transit + (0 if unit['home_base'] == 'valverde' else 6)
                           for unit in units.values() if unit['kind'] in ('engine', 'crew')), default=horizon)
            if minimum >= horizon:
                add('ground_arrival_outside_horizon', 'scenario.transport', 'Even a request at minute zero cannot place any ground unit before cutoff; not proof of an unsolvable protection task')
    local = grounded.get('local_clock_minute')
    source = datetime.fromisoformat(context['inputs']['episode_pack']['detection_local'])
    if local != source.hour * 60 + source.minute:
        add('clock_alignment', 'scenario.grounded.local_clock_minute', 'Daylight clock differs from the pinned episode/weather clock; a clock transplant needs a new declared input contract')
    if clock(local):
        for event in events:
            if event.get('kind') == 'daylight' and clock(event.get('tick')) and type(event.get('value')) is bool:
                expected_daylight = 7 <= ((local + event['tick']) // 60) % 24 < 20
                if event['value'] != expected_daylight:
                    add('clock_alignment', 'scenario.events', 'Daylight event contradicts the grounded clock gate and will be overwritten by it')
    if clock(local) and not any(7 <= ((local + minute) // 60) % 24 < 20 for minute in range(horizon)):
        add('night_aircraft', 'scenario.aircraft_ready_at', 'No daylight window; aircraft readiness is not a usable air-response capability')
    return findings
