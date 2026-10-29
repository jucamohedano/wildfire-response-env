"""Private, evidence-limited runtime checks for incident-day operations.

Call journals contain name, arguments, before_minute, after_minute and result;
optional before_decision/after_decision expose whether a read committed.  No
claim here validates a map, aircraft clearance, or an accepted command's effect.
"""
import json
import math
from pathlib import Path

from envs.wildfire_response.incident_day.plausibility import audit_case, finding, route_checks

ROOT = Path(__file__).resolve().parents[1]
BOUNDS = json.loads((ROOT / 'data/incident_day_v2/plausibility/action-bounds-v1.json').read_text())
CASE_BOUNDS = json.loads((ROOT / 'data/incident_day_v2/plausibility/bounds-v1.json').read_text())
READS = ('get_situation', 'get_reports', 'route_options', 'evacuation_options', 'check_action')
TOOLS = READS + ('commit_orders', 'request_status', 'advance_time')
KINDS = ('requests', 'authorizations', 'releases', 'shelter', 'evacuate', 'status_requests', 'waits')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _safe(value):
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def audit_runtime(env, calls=()):
    """Return JSON-safe findings, operation coverage, and observed durations."""
    findings, durations = [], []
    actions, audit = env._actions, env._audit
    reports, orders = env.bus.reports, list(env.ops.orders.values())
    scenario, profile = env._scenario, env._scenario['profile']
    counts = {name: 0 for name in TOOLS + KINDS}
    journal_complete = {name: True for name in TOOLS}

    def add(code, detail, minute=None, resource=None, category='contradiction'):
        findings.append(finding(code, detail, minute, resource, category))

    # Existing audit owns movement, setup/rest/refill transitions and other
    # established checks. Keep independent checks below available on corruption.
    try:
        findings.extend(audit_case(scenario, env.inputs, env.network, audit, CASE_BOUNDS,
                                   reports=reports, orders=orders,
                                   queries=getattr(env, '_query_log', ()))['findings'])
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        add('case_audit_unavailable', f'Existing audit could not inspect corrupted or incomplete evidence: {exc}', category='unsupported')
    try:
        findings.extend(route_checks(env._approaches, env.network)['findings'])
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        add('route_audit_unavailable', f'Offered route evidence could not be inspected: {exc}', category='unsupported')

    for call in calls:
        name = call.get('name')
        if name not in counts or name not in TOOLS:
            add('unknown_tool', f'Unknown named tool {name!r}.', call.get('before_minute'))
            continue
        counts[name] += 1
        before, after = call.get('before_minute'), call.get('after_minute')
        args, result = call.get('arguments', {}), call.get('result', {})
        if not isinstance(args, dict) or not isinstance(result, (dict, list)) or type(before) is not int or type(after) is not int:
            journal_complete[name] = False
            add('tool_journal_incomplete', f'{name} lacks arguments, result, or integer timestamps.', category='unsupported')
            continue
        elapsed = after - before
        durations.append(dict(operation=name, start_minute=before, end_minute=after, minutes=elapsed,
                              basis='named tool clock; synthetic exercise contract'))
        rejected = isinstance(result, dict) and 'error' in result and result.get('committed') is False
        if name in READS:
            if elapsed != 0:
                add('read_advanced_time', f'{name} advanced {elapsed} minutes; read/draft contract is zero.', before)
            if 'before_decision' in call and 'after_decision' in call:
                if call['after_decision'] != call['before_decision']:
                    add('read_committed', f'{name} changed the decision counter.', before)
            else:
                journal_complete[name] = False
        elif rejected:
            if elapsed != 0:
                add('rejected_tool_advanced_time', f'{name} failed before commit but advanced {elapsed} minutes.', before)
        else:
            wait = args.get('max_minutes') if name == 'advance_time' else None
            if name == 'commit_orders' and isinstance(args.get('action'), dict):
                wait = args['action'].get('advance_minutes')
            if wait is None:
                if elapsed != BOUNDS['commit_minutes']:
                    add('commit_duration', f'{name} took {elapsed} minutes; exercise contract is one.', before)
            elif type(wait) is int and 1 <= wait <= 60:
                wake = result.get('info', {}).get('wake_reason') if isinstance(result, dict) and isinstance(result.get('info'), dict) else None
                if elapsed < 1 or elapsed > wait:
                    add('wait_duration', f'Wait requested {wait} minutes but advanced {elapsed}.', before)
                elif elapsed < wait:
                    terminal = isinstance(result, dict) and result.get('terminated') is True
                    truncated = isinstance(result, dict) and result.get('truncated') is True
                    justified = (
                        (wake == 'actionable_delivery' and any(r.get('received_at') == after and r.get('actionable') for r in reports))
                        or (wake == 'allocation_deadline' and any(o.get('expires_at') in (after + 1, after + 2) for o in env._offers.values()))
                        or (wake == 'hourly_checkpoint' and after % 60 == 0)
                        or (wake == 'day_complete' and after == 1440 and terminal)
                        or (wake in ('input_uncertainty_censored', 'boundary_censored', 'invalid_abandonment') and truncated))
                    if not justified:
                        add('wait_unexplained_early_wake', f'Wait stopped after {elapsed} of {wait} minutes without supporting evidence for {wake!r}.', before)
            else:
                # Invalid waits are committed invalid actions and still cost one minute.
                if elapsed != 1:
                    add('invalid_wait_duration', f'Invalid wait consumed {elapsed} minutes; invalid commits cost one.', before)

    # Transition ledger catches direct environment calls as well as named tools.
    previous_end = 0
    for transition in actions:
        start, end = transition.get('start_minute'), transition.get('minute')
        action = transition.get('action', {})
        if type(start) is not int or type(end) is not int or not isinstance(action, dict):
            add('transition_incomplete', 'Transition lacks integer times or an action.', category='unsupported')
            continue
        if start != previous_end:
            add('transition_gap', f'Transition starts at {start} after prior end {previous_end}.', start)
        previous_end = end
        valid = transition.get('valid') is True
        wait = action.get('advance_minutes') if valid else None
        if wait is not None:
            counts['waits'] += 1
            if type(wait) is not int or not 1 <= wait <= 60 or not 1 <= end - start <= wait:
                add('transition_wait_duration', f'Wait {wait!r} advanced {end-start} minutes.', start)
        elif end - start != 1:
            add('transition_commit_duration', f'Commit advanced {end-start} minutes; expected one.', start)
        if valid:
            for kind in ('requests', 'authorizations', 'releases', 'status_requests'):
                counts[kind] += len(action.get(kind, []))
            for order in action.get('protection_orders', []):
                if order.get('action') in ('shelter', 'evacuate'):
                    counts[order['action']] += 1
    if actions and previous_end != env.minute:
        add('transition_clock_mismatch', 'Final committed transition does not match environment clock.', env.minute)
    if audit and (len(audit) != env.minute + 1 or any(record.get('minute') != i for i, record in enumerate(audit))):
        add('audit_clock_mismatch', 'Minute audit does not cover every minute through the current clock.', env.minute)

    initial_units = {u['id']: u for u in scenario['resources']}
    initial_groups = {g['id']: g for g in scenario['groups']}
    for index, record in enumerate(audit):
        minute = record.get('minute')
        for identifier, unit in record.get('resources', {}).items():
            spec = initial_units.get(identifier, {})
            for field in ('water_litres', 'work_remaining', 'total_work_remaining', 'fuel_minutes'):
                value = unit.get(field)
                if value is None and field in ('fuel_minutes', 'total_work_remaining'):
                    continue
                if not _number(value) or value < 0:
                    add('resource_invalid_stock', f'{field} must be finite and nonnegative.', minute, identifier)
                    continue
                limit = spec.get({'water_litres': 'capacity_litres', 'fuel_minutes': 'fuel_capacity_minutes'}.get(field, ''))
                if _number(limit) and value > limit:
                    add('resource_capacity_exceeded', f'{field} exceeds scenario capacity {limit}.', minute, identifier)
        for identifier, group in record.get('population', {}).items():
            count, exposed = group.get('count'), group.get('exposed')
            expected = initial_groups.get(identifier, {}).get('count')
            if type(count) is not int or count < 0 or count != expected:
                add('population_count_mismatch', 'Population count differs from the scenario cohort.', minute, identifier)
            if type(exposed) is not int or exposed not in (0, count):
                add('population_exposure_invalid', 'Exposed count must be zero or the cohort count.', minute, identifier)
            if index and exposed == 0 and audit[index-1].get('population', {}).get(identifier, {}).get('exposed', 0) > 0:
                add('population_exposure_reversed', 'Cumulative exercise exposure cannot decrease.', minute, identifier)

    # Record observed phase spans without assuming that a phase already in
    # progress at handover started at minute zero or that an open phase ended.
    for uid, spec in initial_units.items():
        if not audit or uid not in audit[0].get('resources', {}):
            continue
        start, state = 0, audit[0]['resources'][uid].get('state')
        for record in audit[1:] + [None]:
            current = record.get('resources', {}).get(uid, {}).get('state') if record else None
            if current == state:
                continue
            end = record['minute'] if record else audit[-1]['minute']
            if state in ('mobilizing', 'travel', 'returning', 'blocked', 'on_scene', 'working', 'refilling', 'resting'):
                minimum = {'mobilizing': profile.get('mobilization_minutes', 1),
                           'on_scene': profile.get('aircraft_setup_minutes' if spec.get('kind') == 'aircraft' else 'ground_setup_minutes'),
                           'refilling': profile.get('air_refill_minutes', 15) if spec.get('kind') == 'aircraft' else profile.get('ground_refill_minutes'),
                           'resting': spec.get('rest_minutes', profile.get('rest_minutes'))}.get(state)
                durations.append(dict(operation='resource_' + state, resource=uid, start_minute=start, end_minute=end,
                    minutes=end-start, minimum_minutes=minimum, inherited=start == 0, censored=record is None,
                    basis='observed state span; profile minimum applies only to uninterrupted completed phases; blocked and interrupted phases have no upper bound'))
                completions = {'mobilizing': ('travel',), 'on_scene': ('working',),
                               'refilling': ('available', 'resting'), 'resting': ('available',)}
                if (record is not None and start > 0 and _number(minimum)
                        and current in completions.get(state, ()) and end-start > minimum):
                    add('phase_completion_late',
                        f'Uninterrupted {state} completed after {end-start} minutes; declared tick duration is {minimum}.', end, uid)
            if record is not None:
                start, state = end, current

    # Applied work and stock are recorded together at each tick.
    for previous, record in zip(audit, audit[1:]):
        for work in record.get('applied_work', []):
            uid = work.get('resource_id')
            before = previous.get('resources', {}).get(uid, {}).get('water_litres')
            after = record.get('resources', {}).get(uid, {}).get('water_litres')
            kind = initial_units.get(uid, {}).get('kind')
            if _number(before) and _number(after) and kind in ('engine', 'aircraft'):
                expected = 0 if kind == 'aircraft' else max(0, before-profile.get('engine_litres_per_minute', 20))
                if after != expected:
                    add('work_water_conservation', f'Applied {kind} work leaves {after} litres; exercise stock rule expects {expected}.', record.get('minute'), uid)

    for identifier, unit in env.ops.resources.items():
        for field in ('water_litres', 'capacity_litres', 'work_remaining', 'total_work_remaining', 'fuel_minutes', 'fuel_capacity_minutes'):
            value = unit.get(field)
            if value is not None and (not _number(value) or value < 0):
                add('resource_invalid_stock', f'Current {field} must be finite and nonnegative.', env.minute, identifier)
    for identifier, destination in env.ops.destinations.items():
        capacity = destination.get('capacity')
        occupants = sum(g['count'] for gid, g in env.ops.groups.items()
                        if env.ops.reservations.get(gid) == identifier or g.get('destination_id') == identifier)
        if not _number(capacity) or capacity < 0 or occupants > capacity:
            add('destination_capacity', f'Destination occupancy/reservations {occupants} exceed valid capacity {capacity}.', env.minute, identifier)
    for gid, destination in env.ops.reservations.items():
        if gid not in env.ops.groups or destination not in env.ops.destinations:
            add('invalid_reservation', 'Reservation references an unknown cohort or destination.', env.minute, gid)
    for gid, group in env.ops.groups.items():
        if group.get('destination_id') is not None and group['destination_id'] not in env.ops.destinations:
            add('unknown_group_destination', 'Cohort references an unknown destination.', env.minute, gid)
        if group.get('status') == 'arrived' and group.get('destination_id') in env.ops.destinations:
            if group.get('position') != env.ops.destinations[group['destination_id']]['position']:
                add('arrival_position_mismatch', 'Arrived cohort is not at its recorded destination.', env.minute, gid)

    # Facility stock is debited at reservation, not at completion. Reconstruct
    # completed refills from per-minute increases and add outstanding reservations.
    initial_facilities = profile.get('facilities')
    facilities = env.ops.profile.get('facilities')
    if isinstance(initial_facilities, dict) and isinstance(facilities, dict):
        for fid, initial in initial_facilities.items():
            stock = facilities.get(fid, {}).get('water_litres')
            start_stock = initial.get('water_litres')
            if not _number(stock) or stock < 0:
                add('facility_invalid_stock', 'Facility water must be finite and nonnegative.', env.minute, fid)
                continue
            spent = 0
            for prev, curr in zip(audit, audit[1:]):
                for uid, unit in curr.get('resources', {}).items():
                    old = prev.get('resources', {}).get(uid, {})
                    runtime = env.ops.resources.get(uid, {})
                    a, b = old.get('water_litres'), unit.get('water_litres')
                    if runtime.get('facility_id') == fid and old.get('state') == 'refilling' and _number(a) and _number(b) and b > a:
                        spent += b - a
            reserved = sum(r.get('water_litres', 0) for u in env.ops.resources.values()
                           if isinstance((r := u.get('refill_reservation')), dict) and r.get('facility_id') == fid
                           and _number(r.get('water_litres')))
            inherited = any(u.get('facility_id') == fid and audit and audit[0]['resources'].get(uid, {}).get('state') == 'refilling' for uid, u in env.ops.resources.items())
            if not inherited and _number(start_stock) and abs(start_stock - stock - spent - reserved) > 1e-6:
                add('facility_stock_conservation', f'Initial stock {start_stock}, current {stock}, completed refill {spent}, reserved {reserved} do not balance.', env.minute, fid)

    for order in orders:
        identifier = order.get('group_id')
        if identifier not in initial_groups or order.get('action') not in ('shelter', 'evacuate'):
            add('invalid_protection_order', 'Order references an unknown cohort or action.', order.get('issued_at'), identifier)
        if order.get('action') == 'evacuate' and order.get('destination_id') not in env.ops.destinations:
            add('invalid_order_destination', 'Evacuation order references an unknown destination.', order.get('issued_at'), identifier)
        if order.get('action') == 'shelter' and (order.get('destination_id') is not None or order.get('journey') is not None):
            add('invalid_shelter_destination', 'Shelter order contains a destination or journey.', order.get('issued_at'), identifier)
        for left, right, key, default in (('issued_at', 'delivered_at', 'order_delivery_minutes', 1),
                                          ('delivered_at', 'acknowledged_at', 'order_ack_minutes', 1),
                                          ('acknowledged_at', 'started_at', 'order_execution_minutes', 1),
                                          ('acknowledged_at', 'completed_at', 'order_execution_minutes', 1)):
            if left in order and right in order and (right != 'completed_at' or order.get('action') == 'shelter'):
                a, b, minimum = order[left], order[right], profile.get(key, default)
                if _number(a) and _number(b) and _number(minimum):
                    durations.append(dict(operation='protection_' + right, resource=identifier, start_minute=a, end_minute=b,
                                          minutes=b-a, minimum_minutes=minimum, basis='scenario profile; blocked/cancelled phases may take longer'))
                    if b-a < minimum:
                        add('protection_phase_too_short', f'{left} to {right} took {b-a} minutes; profile minimum {minimum}.', b, identifier)

    # A response is causally tied to its status request only where the bus
    # retains its correlation identifier; pending or timed-out requests are valid.
    for report in reports:
        if report.get('kind') != 'status_response':
            continue
        req = env.bus.requests.get(report.get('correlation_id'))
        payload = report.get('payload', {})
        if req:
            requested, serviced, received = req.get('requested_at'), payload.get('serviced_at'), report.get('received_at')
            if all(_number(v) for v in (requested, serviced, received)):
                durations.append(dict(operation='status_response', start_minute=requested, end_minute=received,
                    minutes=received-requested, minimum_minutes=0, maximum_minutes=60,
                    basis='exercise status request timeout; pending and censored requests may lack a response'))
                if not requested <= serviced <= received or received-requested >= 60:
                    add('status_response_sequence', 'Status response is outside its request/service/timeout sequence.', received, req.get('center_id'))
    for order in orders:
        matching = [t['start_minute'] for t in actions if t.get('valid') for o in t.get('action', {}).get('protection_orders', [])
                    if o.get('group_id') == order.get('group_id') and o.get('action') == order.get('action')
                    and o.get('destination_id') == order.get('destination_id') and t['start_minute'] <= order.get('issued_at', -1)]
        if matching and order['issued_at'] - max(matching) < BOUNDS['authorization_release_protection_command_delay_minutes']:
            add('protection_before_command_delay', 'Protection issued before the exercise command delay.', order['issued_at'], order.get('group_id'))

    # Delivered offers provide actual request/authorization evidence; absence of
    # delivery is not evidence of an impossible command or failed dispatch.
    requests = [(t['start_minute'], r) for t in actions if t.get('valid') for r in t.get('action', {}).get('requests', [])]
    authorizations = [(t['start_minute'], o) for t in actions if t.get('valid') for o in t.get('action', {}).get('authorizations', [])]
    for report in reports:
        if report.get('kind') == 'resource_offer':
            offer = report.get('payload', {}).get('offer', {})
            matched = [t for t, r in requests if all(r.get(k) == offer.get(k) for k in ('resource_id', 'sector_id', 'approach_id')) and t <= report.get('observed_at', -1)]
            if matched and report['observed_at'] - max(matched) < BOUNDS['request_command_delay_minutes']:
                add('offer_before_request_delay', 'Offer observed before the exercise request command delay.', report['observed_at'], offer.get('resource_id'))
    for minute, oid in authorizations:
        matching = [r for r in reports if r.get('kind') == 'resource_offer' and r.get('payload', {}).get('offer', {}).get('id') == oid]
        if matching and min(r['received_at'] for r in matching) > minute:
            add('authorization_before_offer', 'Authorization precedes delivery of its recorded offer.', minute, oid)

    # Match delivered execution reports only to identifiable committed commands.
    # An absent report can be pending, suppressed, censored, or simply undelivered.
    resource_authorizations = []
    for minute, oid in authorizations:
        offer = env._offers.get(oid)
        if offer:
            resource_authorizations.append((minute, offer.get('resource_id')))
    releases = [(t['start_minute'], uid) for t in actions if t.get('valid')
                for uid in t.get('action', {}).get('releases', [])]
    for report in reports:
        kind, observed = report.get('kind'), report.get('observed_at')
        payload = report.get('payload', {})
        uid = payload.get('resource_id')
        candidates = resource_authorizations if kind in ('dispatch_accepted', 'dispatch_declined') else releases if kind in ('release_accepted', 'release_declined') else []
        matching = [minute for minute, resource in candidates if resource == uid]
        if matching and _number(observed) and observed < min(matching) + BOUNDS['authorization_release_protection_command_delay_minutes']:
            add('command_effect_before_delay', f'{kind} was observed before its matching command could execute.', observed, uid)

    effect_evidence = {
        'requests': ('resource_offer', 'request_unavailable'),
        'authorizations': ('dispatch_accepted', 'dispatch_declined'),
        'releases': ('release_accepted', 'release_declined'),
        'shelter': ('protection_accepted', 'protection_declined'),
        'evacuate': ('protection_accepted', 'protection_declined'),
        'status_requests': ('status_response',),
        'waits': (),
    }
    coverage = {}
    for name in TOOLS:
        coverage[name] = dict(status=('not_exercised' if not counts[name] else 'complete' if journal_complete[name] else 'unsupported'),
                              observed=counts[name], scope='named-tool timing' + (' and decision counter' if name in READS else ''),
                              limitation='A complete entry covers recorded call checks, not downstream effects.')
    for name in KINDS:
        coverage[name] = dict(status='complete' if counts[name] else 'not_exercised', observed=counts[name],
                              scope='committed action and available runtime evidence',
                              delivered_reports=sum(r.get('kind') in effect_evidence[name] for r in reports),
                              limitation='Accepted action alone does not prove its downstream effect; unobserved or censored phases remain unresolved.')
    protection_count = counts['shelter'] + counts['evacuate']
    coverage['protection_orders'] = dict(status='complete' if protection_count else 'not_exercised',
        observed=protection_count, scope='committed protection orders; see shelter and evacuate',
        limitation='Acceptance does not establish delivery, compliance, arrival, or exposure outcome.')
    coverage['actual_routes_and_clearance'] = dict(status='unsupported', observed=0,
        limitation='Per-minute audit does not prove actual paths, map validity, or clearance.')
    coverage['facility_conservation'] = dict(status='complete' if isinstance(initial_facilities, dict) and isinstance(facilities, dict) and not any(audit and audit[0]['resources'].get(uid, {}).get('state') == 'refilling' for uid in env.ops.resources) else 'unsupported',
        observed=len(facilities) if isinstance(facilities, dict) else 0,
        limitation='Uses scenario facility assignment and audited refill increases; inherited refill reservations are not reconstructable.')
    return _safe(dict(version=BOUNDS['version'], findings=findings, coverage=coverage, durations=durations))
