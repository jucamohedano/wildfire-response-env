"""Evidence-limited checks for synthetic incident-day exercises."""

import copy
import math
from datetime import datetime, timedelta

from .clock import solar_daylight
from .scenarios import grid_point


def finding(code, detail, minute=None, resource=None, category='contradiction'):
    return dict(code=code, category=category, detail=detail, minute=minute, resource=resource)


def counterfactual_case(env):
    scenario = env._scenario
    family = scenario['family']
    burning = env._burning()
    center = scenario.get('sector_grid', [env.inputs['grid']['width'] // 2, env.inputs['grid']['height'] // 2])
    events = scenario['events']
    details = {}
    demonstrated = False
    if family == 'quiet_handover':
        staged = [unit for unit in scenario['resources'] if unit['state'] == 'on_scene' and unit['kind'] != 'aircraft']
        useful = [unit['id'] for unit in staged if env.router.safe_position(unit['position'], burning, scenario['wind'], mode=unit['kind'])
                  and any(math.dist(grid_point(env.inputs, unit['position']), cell) <= 3 and math.dist(center, cell) <= 3 for cell in burning)]
        recovery = []
        for unit in scenario['resources']:
            if unit['state'] != 'returning' or unit['kind'] not in ('engine', 'crew'):
                continue
            journey = unit.get('journey')
            if not journey or journey.get('origin') != unit['position'] or journey.get('destination') != unit['base_position']:
                continue
            options = env.router.options(unit['position'], grid_point(env.inputs, unit['base_position']), mode=unit['kind'],
                burning=burning, wind=scenario['wind'], target_metric=unit['base_position'], limit=1)['options']
            if options and options[0]['journey']['duration_minutes'] == journey['duration_minutes']:
                recovery.append(unit['id'])
        details = dict(useful_safe_inherited_assignments=useful, legal_inherited_recoveries=recovery,
                       recovery_does_not_establish_suppression_access=True)
        demonstrated = bool(useful or recovery)
    elif family == 'wind_access_loss':
        event = next(item for item in events if item['kind'] == 'wind' and item['minute'] == 180)
        before, after = scenario['wind'], event['wind']
        before_safe = {identifier for identifier, position in env.network.nodes.items()
                       if env.router.safe_position(position, burning, before, mode='crew')}
        after_safe = {identifier for identifier, position in env.network.nodes.items()
                      if env.router.safe_position(position, burning, after, mode='crew')}
        routes = []
        for unit in scenario['resources']:
            if unit['kind'] not in ('engine', 'crew') or unit['available_at'] != 0:
                continue
            before_options = env.router.options(unit['base_position'], center, mode=unit['kind'], burning=burning, wind=before, limit=32)['options']
            after_options = env.router.options(unit['base_position'], center, mode=unit['kind'], burning=burning, wind=after, limit=32)['options']
            before_goals = {tuple(option['approach_grid']) for option in before_options}
            after_goals = {tuple(option['approach_grid']) for option in after_options}
            routes.append(dict(resource=unit['id'], before=len(before_goals), after=len(after_goals),
                               lost_approach_cells=[list(point) for point in sorted(before_goals - after_goals)]))
        details = dict(old_wind=before, new_wind=after, safe_nodes_before=len(before_safe), safe_nodes_after=len(after_safe),
                       nodes_losing_safety=sorted(before_safe - after_safe), candidate_routes=routes)
        demonstrated = bool(before_safe - after_safe)
    elif family == 'crew_only_access':
        event = next(item for item in events if item['kind'] == 'access_restriction')
        excluded = env._excluded(event['resource_kind'], {event['resource_kind']})
        candidates = [identifier for identifier, position in env.network.nodes.items()
                      if env.router.safe_position(position, burning, scenario['wind'], mode='engine')
                      and not env.router.safe_position(position, burning, scenario['wind'], mode='engine', excluded_cells=excluded)
                      and env.router.safe_position(position, burning, scenario['wind'], mode='crew')]
        routes = {}
        for kind in ('engine', 'crew'):
            unit = next(item for item in scenario['resources'] if item['id'] == kind + '-1')
            options_before = env.router.options(unit['base_position'], center, mode=kind, burning=burning, wind=scenario['wind'], limit=32)['options']
            options_after = env.router.options(unit['base_position'], center, mode=kind, burning=burning, wind=scenario['wind'], limit=32,
                excluded_cells=excluded if kind == 'engine' else ())['options']
            routes[kind] = dict(before=len(options_before), after=len(options_after))
        details = dict(engine_excluded_crew_safe_nodes=sorted(candidates), candidate_routes=routes)
        demonstrated = bool(candidates) and routes['crew']['after'] > 0
    elif family == 'aircraft_window':
        event = next(item for item in events if item['kind'] == 'aircraft_unavailable')
        wind = scenario['wind']
        for item in events:
            if item['kind'] == 'wind' and item['minute'] <= event['minute']:
                wind = item['wind']
        initial_minute = env.minute
        env.minute = event['minute']
        daylight = env._calendar_daylight()
        env.minute = initial_minute
        open_without_event = daylight and math.hypot(*wind) <= scenario['profile']['aircraft_max_wind']
        closed_with_event = event['until'] > event['minute']
        details = dict(minute=event['minute'], daylight=daylight, wind=wind,
                       otherwise_open=open_without_event, closed_by_event=closed_with_event,
                       no_claim_about_actual_aircraft_dispatch=True)
        demonstrated = open_without_event and closed_with_event
    elif family == 'destination_threat':
        from .operations import Operations

        event = next(item for item in events if item['kind'] == 'destination_closed')
        destination = next(item for item in scenario['destinations'] if item['id'] == event['destination_id'])
        fits = [group['id'] for group in scenario['groups'] if group['count'] <= destination['capacity']]
        movement = []
        for group in scenario['groups']:
            if (group.get('destination_id') == destination['id']
                    and group['position'] == destination['position']):
                movement.append(dict(group=group['id'], already_at_destination=True))
                continue
            route = env.router.options(group['position'], grid_point(env.inputs, destination['position']), mode='crew',
                burning=burning, wind=scenario['wind'], limit=1, target_metric=destination['position'])
            if not route['options']:
                movement.append(dict(group=group['id'], route_status=route['status']))
                continue
            journey = route['options'][0]['journey']
            operations = Operations([], copy.deepcopy(scenario['destinations']), copy.deepcopy(scenario['groups']),
                                    copy.deepcopy(scenario['profile']))
            try:
                operations.protect(group['id'], 'evacuate', destination['id'], journey, 0)
                open_accepted = True
            except ValueError:
                open_accepted = False
            operations = Operations([], copy.deepcopy(scenario['destinations']), copy.deepcopy(scenario['groups']),
                                    copy.deepcopy(scenario['profile']))
            operations.destinations[destination['id']]['status'] = 'closed'
            try:
                operations.protect(group['id'], 'evacuate', destination['id'], journey, 0)
                closed_rejected = False
            except ValueError:
                closed_rejected = True
            oversized_groups = copy.deepcopy(scenario['groups'])
            next(item for item in oversized_groups if item['id'] == group['id'])['count'] = destination['capacity'] + 1
            operations = Operations([], copy.deepcopy(scenario['destinations']), oversized_groups,
                                    copy.deepcopy(scenario['profile']))
            try:
                operations.protect(group['id'], 'evacuate', destination['id'], journey, 0)
                oversized_rejected = False
            except ValueError:
                oversized_rejected = True
            movement.append(dict(group=group['id'], route_status=route['status'],
                                 travel_minutes=journey['duration_minutes'], open_accepted=open_accepted,
                                 closed_rejected=closed_rejected, oversized_rejected=oversized_rejected))
        details = dict(destination=destination['id'], initial_status=destination['status'],
                       hypothetical_status_after='closed', capacity=destination['capacity'], groups_fitting_empty_destination=fits,
                       hypothetical_routed_orders=movement, live_occupancy_not_proven=True)
        demonstrated = any(item.get('open_accepted') and item.get('closed_rejected') and item.get('oversized_rejected') for item in movement)
    elif family == 'regional_shortage':
        event = next(item for item in events if item['kind'] == 'center_unavailable')
        delayed = [dict(id=unit['id'], available_at=unit['available_at']) for unit in scenario['resources']
                   if unit['home_base'] == event['center_id'] and unit['available_at'] > 0]
        details = dict(center=event['center_id'], initially_available=event['center_id'] in scenario['centers'],
                       event_minute=event['minute'], unavailable_until=event['until'], predeclared_delayed_slots=delayed)
        demonstrated = bool(delayed) and event['center_id'] in scenario['centers'] and event['until'] > event['minute']
    return dict(status='demonstrated' if demonstrated else 'not_demonstrated', details=details,
                qualification='Counterfactual exercise mechanics under specified conditions, not observed event causation or physical validation.')


def static_case(scenario, inputs, network, burning, bounds, land_mask=None):
    center = scenario.get('sector_grid', [inputs['grid']['width'] // 2, inputs['grid']['height'] // 2])
    findings = []
    resources = []
    for unit in scenario['resources']:
        point = grid_point(inputs, unit['position'])
        nearest = min((math.dist(point, cell) for cell in burning
                       if math.dist(cell, center) <= bounds['ground_work_reach_cells']), default=None)
        resources.append(dict(id=unit['id'], kind=unit['kind'], initial_state=unit['state'],
                              grid=point, center_distance_cells=round(math.dist(point, center), 3),
                              nearest_eligible_burning_cells=nearest, available_at=unit['available_at']))
        if unit['kind'] != 'aircraft' and unit['state'] in ('on_scene', 'working'):
            if nearest is None or nearest > bounds['ground_work_reach_cells']:
                findings.append(finding('initial_assignment_out_of_reach',
                    'Inherited ground assignment has no eligible burning cell within the modeled work reach at handover.',
                    0, unit['id']))
    for point in scenario['initial_fire']:
        if land_mask is not None and land_mask[point[1]][point[0]] == 0:
            findings.append(finding('initial_fire_on_water', 'Initial ignition is outside supplied land mask.', 0))
        elif land_mask is not None and land_mask[point[1]][point[0]] is None:
            findings.append(finding('initial_fire_land_unknown', 'Land or water classification is unresolved at an initial ignition.', 0, category='unsupported'))
    if land_mask is None:
        findings.append(finding('land_mask_missing', 'Sea and water exclusion cannot be assessed without an estimated land mask.', category='unsupported'))
    findings.append(finding('synthetic_physics', 'Grid, fuel, rates, capacity and safety distances are exercise assumptions, not validated physics.', category='unsupported'))
    edges = list(network.edges.values())
    return dict(case=scenario['id'], family=scenario['family'], events=scenario['events'],
                initial_burning_cells=len(burning), resources=resources, findings=findings,
                network=dict(edge_count=len(edges), max_road_speed_kph=max((edge['speed_kph'] for edge in edges), default=0),
                             road_length_m=sum(edge['length_m'] for edge in edges)))


def recovery_overrun_finding(identifier, unit, minute, audit, reports, profile):
    deadline = unit['duty_end_at']
    if profile.get('require_return_within_shift'):
        for report in reversed(list(reports)):
            observed = report.get('observed_at')
            if (report.get('kind') != 'egress_unavailable' or report.get('payload', {}).get('resource_id') != identifier
                    or type(observed) is not int or not 0 <= observed <= deadline < minute):
                continue
            if all(record['resources'][identifier]['state'] == 'needs_assistance'
                   for record in audit[observed:minute]):
                return finding('recovery_after_shift_end_egress_unavailable',
                    f'Egress was reported unavailable at minute {observed}; the unit remained in assistance until late recovery. '
                    'This is an unresolved modeled access exception, not proof of an unexpected fire or real-world impossibility.',
                    minute, identifier, category='unsupported')
    return finding('recovery_after_shift_end', 'Return travel continues beyond the declared shift; safe recovery must still continue.', minute, identifier)


def audit_case(scenario, inputs, network, audit, bounds, reports=(), orders=(), land_mask=None, queries=()):
    if not audit or any(record['minute'] != index for index, record in enumerate(audit)):
        raise ValueError('Audit must start at minute zero and cover consecutive minutes')
    units = {unit['id']: unit for unit in scenario['resources']}
    profile = scenario['profile']
    max_road = max((edge['speed_kph'] for edge in network.edges.values()), default=0)
    findings = []
    timeline = {identifier: [] for identifier in units}
    state_since = {identifier: 0 for identifier in units}
    work_counts = {identifier: 0 for identifier in units}
    water_without_applied_work = {identifier: 0 for identifier in units}
    first_work = {}
    max_front_speed = 0.0
    initial_cells = [(cell % inputs['grid']['width'], cell // inputs['grid']['width']) for cell in audit[0]['footprint']]
    if land_mask is not None:
        for column, row in initial_cells:
            if land_mask[row][column] == 0:
                findings.append(finding('initial_footprint_on_water', f'Initial footprint cell {(column, row)} is outside supplied land mask.', 0))
            elif land_mask[row][column] is None:
                findings.append(finding('initial_footprint_land_unknown', f'Land or water classification is unresolved at {(column, row)}.', 0, category='unsupported'))
    grid_km = inputs['grid']['cell_km']
    for record_index, record in enumerate(audit):
        minute = record['minute']
        previous = audit[record_index - 1] if record_index else None
        for cell in record['new_footprint']:
            point = (cell % inputs['grid']['width'], cell // inputs['grid']['width'])
            if land_mask is not None and land_mask[point[1]][point[0]] == 0:
                findings.append(finding('fire_on_water', f'New footprint cell {point} is outside supplied land mask.', minute))
            elif land_mask is not None and land_mask[point[1]][point[0]] is None:
                findings.append(finding('fire_land_unknown', f'Land or water classification is unresolved at {point}.', minute, category='unsupported'))
            if minute and initial_cells:
                radial_kph = min(math.dist(point, initial) for initial in initial_cells) * grid_km * 60 / minute
                max_front_speed = max(max_front_speed, radial_kph)
        for identifier, current in record['resources'].items():
            unit = units[identifier]
            prior = previous['resources'][identifier] if previous else None
            if current['state'] in ('mobilizing', 'travel', 'on_scene', 'working') and minute < unit['available_at']:
                findings.append(finding('active_before_availability', 'Resource is active before its declared availability.', minute, identifier))
            if current['state'] in ('mobilizing', 'travel', 'on_scene', 'working') and minute >= unit['duty_end_at']:
                findings.append(finding('active_after_shift_end', 'Resource is active after its declared shift end.', minute, identifier))
            if (current['state'] == 'returning' and minute > unit['duty_end_at']
                    and (prior is None or prior['state'] != 'returning' or minute == unit['duty_end_at'] + 1)):
                findings.append(recovery_overrun_finding(identifier, unit, minute, audit, reports, profile))
            if prior is None or prior['state'] != current['state']:
                if prior is not None:
                    elapsed = minute - state_since[identifier]
                    minimum = {'resting': unit.get('rest_minutes', profile['rest_minutes']),
                               'refilling': profile.get('air_refill_minutes', 15) if unit['kind'] == 'aircraft' else profile['ground_refill_minutes'],
                               'on_scene': profile['aircraft_setup_minutes'] if unit['kind'] == 'aircraft' else profile['ground_setup_minutes']}.get(prior['state'])
                    inherited = state_since[identifier] == 0 and prior['state'] == 'on_scene'
                    if minimum is not None and not inherited and prior['state'] == 'on_scene' and current['state'] != 'working':
                        minimum = None
                    if minimum is not None and not inherited and elapsed < minimum and current['state'] in ('working', 'available'):
                        findings.append(finding('duration_too_short', f"{prior['state']} lasted {elapsed} min; exercise minimum {minimum} min.", minute, identifier))
                state_since[identifier] = minute
                timeline[identifier].append(dict(minute=minute, state=current['state'], position=current['position'],
                    water_litres=current['water_litres'], work_remaining=current['work_remaining'],
                    total_work_remaining=current['total_work_remaining'], fuel_minutes=current['fuel_minutes'], due_at=current['due_at']))
            if prior is not None:
                distance = math.dist(current['position'], prior['position'])
                max_speed = profile['flight_speed_kph'] if unit['kind'] == 'aircraft' else max(max_road, 3)
                if distance > max_speed * 1000 / 60 + 1e-5:
                    findings.append(finding('movement_too_fast', f'{distance:.1f} m/min exceeds declared {max_speed} km/h upper speed.', minute, identifier))
                if current['water_litres'] > prior['water_litres'] and prior['state'] != 'refilling':
                    findings.append(finding('instant_water_increase', 'Water increased without a preceding refill state.', minute, identifier))
                if current['water_litres'] < prior['water_litres'] and not any(item['resource_id'] == identifier for item in record['applied_work']):
                    water_without_applied_work[identifier] += prior['water_litres'] - current['water_litres']
                if current['work_remaining'] > prior['work_remaining'] and prior['state'] != 'resting':
                    findings.append(finding('instant_duty_restoration', 'Work allowance increased without a preceding rest state.', minute, identifier))
        for work in record['applied_work']:
            identifier = work['resource_id']
            unit = units[identifier]
            work_counts[identifier] += 1
            first_work.setdefault(identifier, minute)
            if work['state'] != 'working' or previous is None:
                findings.append(finding('work_without_working_state', 'Applied work lacks a working state.', minute, identifier))
            elif previous['resources'][identifier]['state'] not in ('on_scene', 'working'):
                findings.append(finding('work_before_arrival', 'Applied work follows neither setup nor working state.', minute, identifier))
            elif previous['resources'][identifier]['state'] == 'on_scene':
                due_at = previous['resources'][identifier].get('due_at')
                if due_at is not None and minute < due_at:
                    findings.append(finding('work_before_setup_due', 'Applied work before the recorded setup due time.', minute, identifier))
            if unit['kind'] != 'aircraft' and math.dist(grid_point(inputs, work['position']), work['location']) > bounds['ground_work_reach_cells']:
                findings.append(finding('work_out_of_reach', 'Applied ground work exceeds modeled reach.', minute, identifier))
            if previous and unit['kind'] in ('engine', 'aircraft') and previous['resources'][identifier]['water_litres'] <= 0:
                findings.append(finding('work_without_water', 'Applied water work with no preceding stock.', minute, identifier))
            if previous and (previous['resources'][identifier]['work_remaining'] <= 0 or minute >= unit['duty_end_at']):
                findings.append(finding('work_outside_duty', 'Applied work after exercise duty exhausted.', minute, identifier))
            if unit['kind'] == 'aircraft':
                lonlat = inputs['grid'].get('centre_lon_lat')
                longitude, latitude = lonlat[scenario.get('sector_grid', [inputs['grid']['width']//2, inputs['grid']['height']//2])[1]][scenario.get('sector_grid', [inputs['grid']['width']//2, inputs['grid']['height']//2])[0]] if lonlat else (-5.15, 36.55)
                daylight = solar_daylight(datetime.fromisoformat(scenario['start_utc']) + timedelta(minutes=minute), longitude, latitude)
                if not daylight or math.hypot(*record['wind']) > profile['aircraft_max_wind']:
                    findings.append(finding('air_work_outside_exercise_window', 'Aircraft work outside modeled daylight or wind window.', minute, identifier))
    if max_front_speed > bounds['maximum_screening_fire_front_kph']:
        findings.append(finding('fire_front_screen', f'Coarse radial activation estimate {max_front_speed:.2f} km/h exceeds assumed screening bound; not a measured front speed.', category='unsupported'))
    for report in reports:
        if not report['observed_at'] <= report.get('reported_at', report['observed_at']) <= report['received_at']:
            findings.append(finding('communication_order', 'Report delivered before observation or reporting.', report['received_at']))
        if report['received_at'] - report['observed_at'] > 60:
            findings.append(finding('communication_delay', 'Report exceeds assumed 60 minute communication bound.', report['received_at']))
    for order in orders:
        times = [order[key] for key in ('issued_at', 'delivered_at', 'acknowledged_at', 'started_at', 'completed_at') if key in order]
        if times != sorted(times):
            findings.append(finding('protection_order_sequence', f"Protection order {order['id']} has noncausal timestamps."))
        journey = order.get('journey')
        if journey and order.get('state') == 'completed' and 'started_at' in order and 'completed_at' in order and order['completed_at'] - order['started_at'] < journey['duration_minutes']:
            findings.append(finding('protection_travel_too_short', f"Protection order {order['id']} completed before route duration."))
    event_evidence = []
    for event in scenario['events']:
        if event['minute'] >= len(audit):
            event_evidence.append(dict(event=event, status='not_observed_censored'))
            continue
        event_minute = event['minute']
        nearby = [report for report in reports if report['observed_at'] >= event_minute and report['observed_at'] <= event_minute + 60]
        relevant = {'access_restriction': ('access_restriction', 'movement_blocked'), 'wind': ('wind_update', 'movement_blocked', 'aircraft_aborted'),
                    'aircraft_unavailable': ('aircraft_window', 'aircraft_aborted'), 'destination_closed': ('destination_threatened', 'movement_blocked'),
                    'center_unavailable': ('request_unavailable', 'status_response')}.get(event['kind'], ())
        matching = [dict(kind=item['kind'], observed_at=item['observed_at'], received_at=item['received_at']) for item in nearby if item['kind'] in relevant]
        consequence = [item for item in nearby if item['kind'] in ('movement_blocked', 'aircraft_aborted', 'request_unavailable')
                       or (event['kind'] == 'center_unavailable' and item['kind'] == 'status_response'
                           and item['source'] == event['center_id'] and item['payload'].get('available') is False)]
        event_evidence.append(dict(event=event, status='constraint_evidence_observed' if consequence else 'event_only_or_no_constraint_evidence', reports=matching))
        if (scenario['profile'].get('causal_coverage')
                and event['kind'] in ('access_restriction', 'destination_closed')):
            from .scenario_coverage import event_coverage

            event_evidence[-1].update(event_coverage(event, scenario, inputs, audit, reports, orders, queries))
            forbidden = event_evidence[-1].get('causal_evidence', {}).get('forbidden_work', [])
            for work in forbidden:
                findings.append(finding('work_despite_access_restriction',
                    'Restricted vehicle applied work inside its exclusion zone.',
                    work['minute'], work['resource_id']))
    return dict(minutes_observed=audit[-1]['minute'], complete=audit[-1]['minute'] == scenario['horizon_minutes'],
                findings=findings, resource_timeline=timeline, applied_work_counts=work_counts, first_applied_work=first_work,
                water_litres_spent_without_applied_work=water_without_applied_work,
                event_evidence=event_evidence, protection_orders=list(orders), communication_count=len(reports),
                population_initial=audit[0]['population'], population_final=audit[-1]['population'],
                destinations_final=audit[-1]['destinations'], max_coarse_radial_activation_kph=round(max_front_speed, 3),
                limitations=['Per-minute audit omits clearance and journey plans; no proof of route reachability or full path duration.',
                             'Inherited setup precedes audit; no inferred setup duration.',
                             'Event/report correlation is not proof of causation; unobserved constraints need targeted interventions.',
                             'Coarse activation and synthetic physics are not physical validation.'])


def route_checks(approaches, network):
    findings = []
    summaries = []
    for identifier, approach in approaches.items():
        journey = approach['journey']
        legs = journey['legs']
        duration = sum(leg['duration_minutes'] for leg in legs)
        distance = sum(leg['distance_m'] for leg in legs)
        for leg in legs:
            edge = network.edges.get(leg.get('edge_id')) if leg.get('edge_id') else None
            if leg.get('mode') == 'road' and edge is None:
                findings.append(finding('route_unknown_road', f'Approach {identifier} uses an unknown road edge.'))
            if edge and leg['duration_minutes'] + 1e-8 < leg['distance_m'] * 60 / (edge['speed_kph'] * 1000):
                findings.append(finding('route_faster_than_road', f'Approach {identifier} exceeds the edge speed.'))
        if journey['duration_minutes'] + 1e-8 < duration:
            findings.append(finding('route_duration_too_short', f'Approach {identifier} is shorter than its leg durations.'))
        for first, second in zip(legs, legs[1:]):
            if math.dist(first['coordinates'][-1], second['coordinates'][0]) > 0.01:
                findings.append(finding('route_disconnected', f'Approach {identifier} has disconnected legs.'))
        if legs and (math.dist(legs[0]['coordinates'][0], journey['origin']) > 0.01 or
                     math.dist(legs[-1]['coordinates'][-1], journey['destination']) > 0.01):
            findings.append(finding('route_endpoint_mismatch', f'Approach {identifier} does not connect its origin and destination.'))
        summaries.append(dict(id=identifier, resource=approach.get('resource_id'), group=approach.get('group_id'),
                              distance_m=round(distance, 1), leg_minutes=round(duration, 3),
                              scheduled_minutes=journey['duration_minutes'], destination=journey['destination']))
    return dict(findings=findings, approaches=summaries,
                limitation='Offered paths are checked; audit does not identify every actual return journey or clearance.')
