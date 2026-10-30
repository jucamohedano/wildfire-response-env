"""Pure, opt-in reconciliation of public incident-day group knowledge.

``reconcile_situation_v3(situation, delivered_reports)`` returns a deep copy of
``situation``. Each existing district gets an additive ``knowledge_v3`` mapping:
``status``, ``destination_id``, ``position``, ``observed_at``, ``received_at``,
``report_id``, and ``basis``. Unknown values are None, never synthetic times.
Legacy district fields are unchanged. A matching, same-minute delivered arrival
can associate an otherwise orderless district snapshot with its public order;
conflicting order IDs remain unknown. A timestamped district snapshot or a
received resident/relocation group snapshot is evidence. An operations
``evacuation_arrived`` report is evidence only when it is an operations report,
its embedded completed order matches a delivered public order and the group,
and it identifies an evacuation destination. The report
establishes arrival, not a measured position. Later movement/relocation evidence
or a different delivered protection order can make that inference uncertain.

Times are nonnegative integer incident minutes. Reports require observed,
reported and received times in order, and receipt no later than situation
minute. Situation district timestamps also require receipt by that minute.
The latest *observation*, not latest delivery, wins; conflicting observations
at the same minute are unknown. Input reports must be public reports delivered
to the caller, not private journals or future events. This function cannot
verify that provenance and does not access or consult the simulator. Supply a
complete relevant delivered history for reliable reconciliation; missing reports
and an order without a corresponding arrival do not establish arrival. This is
last-known knowledge, not a guarantee of present safety or location.
"""

from copy import deepcopy
from . import _capture_source

_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = _capture_source(__name__, __file__, __loader__)


def _minute(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _candidate(status, destination, position, observed, received, report_id, basis, order_id=None):
    return dict(status=status, destination_id=deepcopy(destination), position=deepcopy(position),
                observed_at=observed, received_at=received, report_id=report_id,
                basis=basis, _order_id=order_id)


def reconcile_situation_v3(situation: dict, delivered_reports: list[dict]) -> dict:
    result = deepcopy(situation)
    now = situation.get('minute')
    districts = result.get('districts')
    if not isinstance(districts, list):
        return result
    candidates = {}
    hazards = {}
    delivered_orders = {}
    ambiguous_orders = set()
    for order in situation.get('protection_orders', []):
        if not isinstance(order, dict) or not _minute(now):
            continue
        observed, received = order.get('observed_at'), order.get('received_at')
        if (isinstance(order.get('id'), str) and _minute(observed) and _minute(received)
                and observed <= received <= now):
            identifier = order['id']
            previous = delivered_orders.get(identifier)
            if previous is not None and any(previous.get(key) != order.get(key)
                    for key in ('id', 'group_id', 'action', 'destination_id')):
                ambiguous_orders.add(identifier)
            elif previous is None or received < previous['received_at']:
                delivered_orders[identifier] = order
    for district in districts:
        if not isinstance(district, dict) or not isinstance(district.get('id'), str):
            continue
        group_id = district['id']
        candidates.setdefault(group_id, [])
        hazards.setdefault(group_id, [])
        observed, received = district.get('observed_at'), district.get('received_at')
        if (_minute(now) and _minute(observed) and _minute(received)
                and observed <= received <= now and isinstance(district.get('status'), str)):
            position = district.get('position')
            # Population v3 supplies field-level provenance. A position from an
            # older observation is not part of a later status snapshot.
            provenance = district.get('field_provenance')
            if isinstance(provenance, dict):
                position_source = provenance.get('position')
                if not (isinstance(position_source, dict) and _minute(position_source.get('observed_at'))
                        and position_source['observed_at'] == observed and _minute(position_source.get('received_at'))
                        and observed <= position_source['received_at'] <= received <= now):
                    position = None
            candidates[group_id].append(_candidate(district['status'], district.get('destination_id'),
                position, observed, received, None, 'district_snapshot', district.get('order_id')))

    for report in delivered_reports:
        if not isinstance(report, dict) or not _minute(now):
            continue
        observed, reported, received = (report.get(k) for k in ('observed_at', 'reported_at', 'received_at'))
        if not (all(_minute(t) for t in (observed, reported, received))
                and observed <= reported <= received <= now):
            continue
        payload = report.get('payload')
        if not isinstance(payload, dict):
            continue
        kind, rid = report.get('kind'), report.get('id')
        group = payload.get('group')
        if kind in ('resident_update', 'relocation_required') and isinstance(group, dict):
            group_id = group.get('id')
            if isinstance(group_id, str) and group_id in candidates and isinstance(group.get('status'), str):
                candidates[group_id].append(_candidate(group['status'], group.get('destination_id'),
                    group.get('position'), observed, received, rid, 'group_report', group.get('order_id')))
        group_id, order = payload.get('group_id'), payload.get('order')
        if not isinstance(group_id, str) or group_id not in candidates:
            continue
        if kind == 'evacuation_arrived' and isinstance(order, dict):
            known_order = (delivered_orders.get(order.get('id'))
                           if isinstance(order.get('id'), str) and order['id'] not in ambiguous_orders else None)
            if (report.get('source') == 'operations' and report.get('role') == 'responder'
                    and isinstance(known_order, dict)
                    and all(known_order.get(key) == order.get(key)
                            for key in ('id', 'group_id', 'action', 'destination_id'))
                    and order.get('id') == payload.get('order_id') and isinstance(order.get('id'), str)
                    and order.get('group_id') == group_id and order.get('action') == 'evacuate'
                    and order.get('state') == 'completed' and order.get('completed_at') == observed
                    and isinstance(order.get('destination_id'), str)):
                candidates[group_id].append(_candidate('arrived', order['destination_id'], None,
                    observed, max(received, known_order['received_at']), rid, 'evacuation_arrived', order['id']))
        elif kind in ('movement_blocked', 'movement_resumed'):
            hazards[group_id].append((observed, received, rid, payload.get('order_id'), kind))
        elif kind in ('protection_accepted', 'order_delivered', 'order_acknowledged'):
            order_id = payload.get('order_id')
            if isinstance(order_id, str):
                hazards[group_id].append((observed, received, rid, order_id, kind))

    # The public situation may retain order snapshots older than its report window.
    for order in situation.get('protection_orders', []):
        if not isinstance(order, dict):
            continue
        group_id = order.get('group_id')
        observed, received = order.get('observed_at'), order.get('received_at')
        if (isinstance(group_id, str) and group_id in hazards and isinstance(order.get('id'), str)
                and _minute(now) and _minute(observed) and _minute(received)
                and observed <= received <= now):
            hazards[group_id].append((observed, received, None, order['id'], 'order_snapshot'))

    for district in districts:
        if not isinstance(district, dict) or not isinstance(district.get('id'), str) or district.get('id') not in candidates:
            continue
        group_id = district['id']
        evidence = candidates[group_id]
        unknown = _candidate(None, None, None, None, None, None, 'unknown')
        if evidence:
            latest = max(item['observed_at'] for item in evidence)
            top = [item for item in evidence if item['observed_at'] == latest]
            # Ties are compared by substantive state, not input/delivery order.
            facts = {(item['status'], repr(item['destination_id'])) for item in top}
            if len(facts) == 1:
                chosen = min(top, key=lambda item: (item['received_at'], str(item['report_id']), item['basis']))
                chosen = deepcopy(chosen)
                positions = {repr(item['position']) for item in top if item['position'] is not None}
                if len(positions) == 1:
                    position_evidence = [item for item in top if item['position'] is not None]
                    chosen['position'] = deepcopy(position_evidence[0]['position'])
                    # A matching position supplied by a later delivery was
                    # not known when an earlier arrival report was received.
                    chosen['received_at'] = max(chosen['received_at'],
                                                min(item['received_at'] for item in position_evidence))
                elif len(positions) > 1:
                    chosen['position'] = None
                if chosen['status'] == 'arrived':
                    # The v3 district view intentionally omits internal order
                    # bookkeeping. Only a validated arrival at this same
                    # observation time can bind it to a public order. Do not
                    # choose between conflicting IDs or use a later arrival.
                    invalid_order = any(item['_order_id'] is not None and not isinstance(item['_order_id'], str)
                                        for item in top)
                    order_ids = {item['_order_id'] for item in top if isinstance(item['_order_id'], str)}
                    arrivals = {item['_order_id'] for item in top if item['basis'] == 'evacuation_arrived'}
                    if invalid_order or len(order_ids) > 1:
                        chosen = unknown
                    elif chosen['_order_id'] is None and len(order_ids) == 1 and order_ids == arrivals:
                        chosen['_order_id'] = next(iter(order_ids))
                        # Association is not available until both the
                        # matching arrival and its public order are delivered.
                        available = min(item['received_at'] for item in top
                                        if item['basis'] == 'evacuation_arrived'
                                        and item['_order_id'] == chosen['_order_id'])
                        chosen['received_at'] = max(chosen['received_at'], available)
                    invalidators = [h for h in hazards[group_id] if h[0] >= latest and
                                    (h[4] in ('movement_blocked', 'movement_resumed') or h[3] != chosen['_order_id'])
                                    and not (h[0] == latest and h[4] == 'movement_resumed'
                                             and h[3] == chosen['_order_id'])]
                    if invalidators:
                        chosen = unknown
            else:
                chosen = unknown
        else:
            chosen = unknown
        chosen.pop('_order_id', None)
        district['knowledge_v3'] = chosen
    return result
