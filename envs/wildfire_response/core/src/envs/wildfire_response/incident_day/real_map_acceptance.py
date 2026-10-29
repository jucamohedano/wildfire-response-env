"""Private, evidence-based probes for additive real-map exercise variants."""

import math

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
        journey = unit.get('journey')
        active = (unit['state'] in ('travel', 'on_scene', 'working', 'blocked')
                  and isinstance(journey, dict))
        destination = journey.get('destination') if active else None
        segment_checks = []
        for wind in (before, after):
            if active and destination is not None:
                segment_checks.append(environment.router.safe_journey_segment(
                    journey, environment.minute - 1, burning, wind, mode=unit['kind'],
                    excluded_cells=environment._excluded(unit['kind'], environment._restrictions)))
            else:
                segment_checks.append(None)
        effects.append(dict(resource_id=unit['id'], state=unit['state'], position=position,
            active_destination=destination[:] if destination is not None else None,
            active_approach_grid=grid_point(environment.inputs, destination) if destination is not None else None,
            active_segment_before_safe=segment_checks[0], active_segment_after_safe=segment_checks[1],
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
                if item['state'] in ('travel', 'on_scene', 'working')
                and item['position_before_safe'] and not item['position_after_safe']
                and item.get('active_segment_before_safe') is True
                and item.get('active_segment_after_safe') is False
                and item.get('active_approach_grid') is not None
                and tuple(item['active_approach_grid']) in set(map(tuple, item['before_approaches']))
                and tuple(item['active_approach_grid']) not in set(map(tuple, item['after_approaches']))
                and item['after_approaches']]
    actions = []
    for item in affected:
        identifier = item['resource_id']
        alternatives = set(map(tuple, item['after_approaches']))
        immediate = audit[minute]['resources'][identifier]
        stopped = (minute if immediate['state'] in ('blocked', 'returning', 'needs_assistance')
                   and math.dist(immediate['position'], item['position']) < .01 else None)
        dispatches = sorted((report for report in reports if report['kind'] == 'dispatch_accepted'
                             and report.get('payload', {}).get('resource_id') == identifier),
                            key=lambda report: report['observed_at'])
        original_dispatches = [report for report in dispatches
                               if report['observed_at'] < minute
                               and report['payload'].get('approach_grid') == item['active_approach_grid']
                               and report['payload'].get('approach_point') == item['active_destination']
                               and report['payload'].get('offer_id')
                               and report['payload'].get('approach_id')]
        evidence = None
        for index, dispatch in enumerate(dispatches):
            payload = dispatch['payload']
            target = payload.get('approach_grid')
            target_point = payload.get('approach_point')
            authorized = payload.get('authorized_at')
            when = dispatch['observed_at']
            if (not original_dispatches or received is None or stopped is None
                    or type(authorized) is not int or authorized < received
                    or type(when) is not int or when <= authorized or not payload.get('offer_id')
                    or not payload.get('approach_id') or not isinstance(target, list) or len(target) != 2
                    or any(payload['offer_id'] == previous['payload']['offer_id']
                           or payload['approach_id'] == previous['payload']['approach_id']
                           for previous in original_dispatches)
                    or not isinstance(target_point, list) or len(target_point) != 2
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in target_point)
                    or grid_point(inputs, target_point) != target
                    or math.dist(target_point, item['active_destination']) < .01
                    or tuple(target) not in alternatives or target == item['active_approach_grid']):
                continue
            following = dispatches[index + 1]['observed_at'] if index + 1 < len(dispatches) else len(audit)
            travelled = None
            for record in audit[when:min(following, len(audit))]:
                state = record['resources'][identifier]['state']
                if travelled is None and state == 'travel':
                    travelled = record['minute']
                if (travelled is not None and record['minute'] > travelled
                        and state in ('on_scene', 'working')
                        and math.dist(record['resources'][identifier]['position'], target_point) < .01):
                    evidence = dict(offer_id=payload['offer_id'], approach_id=payload['approach_id'],
                        authorized_at=authorized, dispatched_at=when, target=target,
                        travel_at=travelled, alternate_reached_at=record['minute'])
                    break
            if evidence:
                break
        actions.append(dict(resource_id=identifier, stopped_or_returned_at=stopped,
                            alternate_reached_at=evidence['alternate_reached_at'] if evidence else None,
                            redispatch=evidence))
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

    environment = IncidentDayEnv(source.inputs, source.network.document,
        expected_inputs_sha256=source.inputs['sha256'], expected_network_sha256=source.network.sha256,
        allow_unapproved_data=source.inputs['approval_status'] != 'approved_synthetic_fixture')
    observation, unused = environment.reset(source._scenario['id'], source._scenario['seed'])
    dispatcher = DayDispatcher()
    worker = None
    actions = 0
    while environment.minute < 160 and actions < 200 and not (environment.terminated or environment.truncated):
        action = dispatcher.action(environment, observation)
        if 'advance_minutes' in action:
            action = dict(advance_minutes=min(action['advance_minutes'], 160 - environment.minute))
        observation, unused_reward, unused_done, unused_truncated, unused_info = environment.step(
            action)
        actions += 1
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
    if actions >= 200:
        return dict(status='not_demonstrated', reason='Action budget exhausted before release', minute=work_minute)
    environment.step({'releases': [worker]})
    actions += 1
    while environment.minute < 200 and actions < 200 and not (environment.terminated or environment.truncated):
        unit = environment.ops.resources[worker]
        if unit['position'] == unit['base_position'] and unit['state'] in ('available', 'resting', 'refilling'):
            return dict(status='demonstrated', resource_id=worker, work_minute=work_minute,
                        return_minute=environment.minute, method='Bounded independent dispatcher and release probe')
        environment.step({'advance_minutes': min(20, 200 - environment.minute)})
        actions += 1
    return dict(status='not_demonstrated', reason='No observed return within bounded probe', minute=environment.minute)
