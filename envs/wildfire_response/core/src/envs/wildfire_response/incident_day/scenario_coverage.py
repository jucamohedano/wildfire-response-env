"""Trace actual access and relocation effects without using event proximity as proof."""

import math

from .scenarios import grid_point


def event_coverage(event, scenario, inputs, audit, reports, orders, queries):
    minute = event['minute']
    snapshots = [item for item in audit[minute].get('event_effects', [])
                 if item['kind'] == event['kind'] and item['minute'] == minute]
    if not snapshots:
        return dict(status='event_only_or_no_constraint_evidence', causal_evidence={})
    snapshot = snapshots[0]
    if event['kind'] == 'access_restriction':
        return _access(event, scenario, inputs, audit, reports, queries, snapshot)
    if event['kind'] == 'destination_closed':
        return _destination(event, scenario, audit, reports, orders, snapshot)
    return dict(status='event_only_or_no_constraint_evidence', causal_evidence={})


def _access(event, scenario, inputs, audit, reports, queries, snapshot):
    kind = event['resource_kind']
    deliveries = [report['received_at'] for report in reports
                  if report['kind'] == 'access_restriction'
                  and report['observed_at'] == event['minute']
                  and report['payload'].get('resource_kind') == kind]
    received = min(deliveries, default=None)
    affected = {item['resource_id'] for item in snapshot['resource_access']
                if item['options_before'] > 0 and item['options_after'] == 0}
    refusals = [dict(resource_id=query['arguments']['resource_id'], minute=query['minute'],
                     status=query['public_route_summary']['status'])
                for query in queries
                if received is not None and query['tool'] == 'route_options'
                and query['minute'] >= received and query['arguments'].get('resource_id') in affected
                and query.get('public_route_summary', {}).get('option_count') == 0]
    units = {unit['id']: unit for unit in scenario['resources']}
    center = [inputs['grid']['width'] // 2, inputs['grid']['height'] // 2]
    crew_work, forbidden_work = [], []
    for record in audit[event['minute']:]:
        for work in record['applied_work']:
            if math.dist(grid_point(inputs, work['position']), center) > 5:
                continue
            entry = dict(resource_id=work['resource_id'], minute=record['minute'])
            if units[work['resource_id']]['kind'] == kind:
                forbidden_work.append(entry)
            elif (units[work['resource_id']]['kind'] == 'crew'
                  and received is not None and record['minute'] >= received):
                crew_work.append(entry)
    demonstrated = bool(affected and refusals and crew_work and not forbidden_work)
    return dict(status='constraint_evidence_observed' if demonstrated else 'event_only_or_no_constraint_evidence',
        causal_evidence=dict(method='Same-state before/after route permissions plus delivered-query refusals and actual crew work',
            event_minute=event['minute'], restriction_received_at=received,
            resource_access=snapshot['resource_access'], public_refusals=refusals,
            crew_work_count=len(crew_work), first_crew_work=crew_work[0] if crew_work else None,
            forbidden_work=forbidden_work))


def _destination(event, scenario, audit, reports, orders, snapshot):
    closed = event['destination_id']
    occupants = {item['group_id']: item for item in snapshot['affected_occupants']}
    deliveries = [report['received_at'] for report in reports
                  if report['observed_at'] == event['minute']
                  and ((report['kind'] == 'destination_threatened'
                        and report['payload'].get('destination', {}).get('id') == closed)
                       or (report['kind'] == 'relocation_required'
                           and report['payload'].get('destination_id') == closed))]
    received = min(deliveries, default=None)
    completed = []
    for order in orders:
        if (received is None or order['group_id'] not in occupants or order['action'] != 'evacuate'
                or order['destination_id'] == closed or order['issued_at'] < received
                or order['state'] != 'completed'):
            continue
        group_id = order['group_id']
        start = audit[event['minute']]['population'][group_id]
        arrival = audit[order['completed_at']]['population'][group_id]
        target_status = audit[order['completed_at']]['destinations'].get(order['destination_id'])
        if (start.get('destination_id') == closed and start['status'] == 'blocked'
                and arrival['status'] == 'arrived' and arrival.get('destination_id') == order['destination_id']
                and target_status == 'open' and start.get('position') != arrival.get('position')):
            completed.append(dict(group_id=group_id, count=occupants[group_id]['count'],
                order_id=order['id'], destination_id=order['destination_id'],
                issued_at=order['issued_at'], completed_at=order['completed_at']))
    resolved = {item['group_id'] for item in completed}
    demonstrated = bool(occupants) and set(occupants) <= resolved
    return dict(status='constraint_evidence_observed' if demonstrated else 'event_only_or_no_constraint_evidence',
        causal_evidence=dict(method='Occupied-centre closure, delivered warning, new routed order and actual arrival elsewhere',
            event_minute=event['minute'], warning_received_at=received,
            affected_occupants=list(occupants.values()), completed_relocations=completed,
            unresolved_occupants=sorted(set(occupants) - resolved)))
