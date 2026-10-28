"""Private, evidence-based probes for additive real-map exercise variants."""

import time

from .scenarios import grid_point


def capture_wind_effect(environment, event):
    """Capture one same-state comparison immediately before the wind is changed."""
    burning = environment._burning()
    before = list(environment.fire.wind)
    after = event['wind'][:]
    sector = environment._sector()
    effects = []
    for unit in environment.ops.resources.values():
        if unit['kind'] not in ('engine', 'crew') or unit['available_at'] > environment.minute:
            continue
        options = []
        for wind in (before, after):
            result = environment.router.options(unit['base_position'], sector, mode=unit['kind'],
                burning=burning, wind=wind, limit=32,
                excluded_cells=environment._excluded(unit['kind'], environment._restrictions))
            options.append(sorted({tuple(option['approach_grid']) for option in result['options']}))
        position = unit['position'][:]
        effects.append(dict(resource_id=unit['id'], state=unit['state'], position=position,
            position_before_safe=environment.router.safe_position(position, burning, before, mode=unit['kind'],
                excluded_cells=environment._excluded(unit['kind'], environment._restrictions)),
            position_after_safe=environment.router.safe_position(position, burning, after, mode=unit['kind'],
                excluded_cells=environment._excluded(unit['kind'], environment._restrictions)),
            before_approaches=[list(point) for point in options[0]],
            after_approaches=[list(point) for point in options[1]]))
    return dict(kind='wind', minute=environment.minute, before_wind=before, after_wind=after,
                resource_access=effects)


def wind_event_coverage(event, inputs, audit, reports):
    minute = event['minute']
    snapshots = [item for item in audit[minute].get('event_effects', [])
                 if item.get('kind') == 'wind' and item.get('minute') == minute]
    if not snapshots:
        return dict(status='event_only_or_no_constraint_evidence', causal_evidence={})
    snapshot = snapshots[0]
    deliveries = [report['received_at'] for report in reports
                  if report['kind'] == 'wind_update' and report['observed_at'] == minute
                  and report['payload'].get('wind') == event['wind']]
    received = min(deliveries, default=None)
    affected = [item for item in snapshot['resource_access']
                if item['state'] in ('travel', 'on_scene', 'working', 'blocked')
                and item['position_before_safe'] and not item['position_after_safe']
                and set(map(tuple, item['before_approaches'])) - set(map(tuple, item['after_approaches']))
                and item['after_approaches']]
    actions = []
    for item in affected:
        identifier = item['resource_id']
        alternatives = set(map(tuple, item['after_approaches']))
        stopped = next((record['minute'] for record in audit[minute:]
                        if record['resources'][identifier]['state'] in ('blocked', 'returning', 'needs_assistance')), None)
        reached = next((record['minute'] for record in audit[minute:]
                        if received is not None and record['minute'] >= received
                        and record['resources'][identifier]['state'] in ('on_scene', 'working')
                        and tuple(grid_point(inputs, record['resources'][identifier]['position'])) in alternatives), None)
        actions.append(dict(resource_id=identifier, stopped_or_returned_at=stopped, alternate_reached_at=reached))
    demonstrated = bool(received is not None and received > minute and any(
        action['stopped_or_returned_at'] is not None and action['alternate_reached_at'] is not None
        and action['stopped_or_returned_at'] <= action['alternate_reached_at'] for action in actions))
    return dict(status='constraint_evidence_observed' if demonstrated else 'event_only_or_no_constraint_evidence',
        causal_evidence=dict(method='Same-state wind comparison, delayed report, actual stop or return and alternate arrival',
            event_minute=minute, warning_received_at=received, resource_access=snapshot['resource_access'],
            affected_actions=actions))


def ready_route_work_return_probe(source):
    """Run a bounded independent dispatcher probe; report only observed evidence."""
    from .baselines import DayDispatcher
    from .environment import IncidentDayEnv

    deadline = time.monotonic() + 15
    environment = IncidentDayEnv(source.inputs, source.network.document,
        expected_inputs_sha256=source.inputs['sha256'], expected_network_sha256=source.network.sha256,
        allow_unapproved_data=source.inputs['approval_status'] != 'approved_synthetic_fixture')
    observation, unused = environment.reset(source._scenario['id'], source._scenario['seed'])
    dispatcher = DayDispatcher()
    worker = None
    while environment.minute < 160 and time.monotonic() < deadline and not (environment.terminated or environment.truncated):
        observation, unused_reward, unused_done, unused_truncated, unused_info = environment.step(
            dispatcher.action(environment, observation))
        worker = next((item['resource_id'] for item in environment._work_log
                       if item['resource_id'].startswith(('crew-', 'engine-'))), None)
        if worker:
            break
    if worker is None:
        return dict(status='not_demonstrated', reason='No actual ground work within bounded probe', minute=environment.minute)
    states = [record['resources'][worker]['state'] for record in environment._audit]
    work_minute = environment.minute
    unit = environment.ops.resources[worker]
    if not ('travel' in states and 'on_scene' in states and 'working' in states
            and states.index('travel') < states.index('on_scene') < states.index('working')
            and unit['position'] != unit['base_position']):
        return dict(status='not_demonstrated', reason='Work lacks travel and setup evidence', minute=work_minute)
    environment.step({'releases': [worker]})
    while environment.minute < 200 and time.monotonic() < deadline and not (environment.terminated or environment.truncated):
        unit = environment.ops.resources[worker]
        if unit['position'] == unit['base_position'] and unit['state'] in ('available', 'resting', 'refilling'):
            return dict(status='demonstrated', resource_id=worker, work_minute=work_minute,
                        return_minute=environment.minute, method='Bounded independent dispatcher and release probe')
        environment.step({'advance_minutes': 20})
    return dict(status='not_demonstrated', reason='No observed return within bounded probe', minute=environment.minute)
