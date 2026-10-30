"""Read-only, additive diagnostics for incident-day actions.

Call ``check_action_v3(env, action)`` on a reset IncidentDayEnv. The result
always contains the core check's ``valid`` and ``error`` plus ``code`` (None
for valid actions). Invalid results may also contain ``field``, zero-based
``order_index``, and ``next_tool``. Codes describe the first detected issue in
core validation order; ``invalid_action`` is the fallback for an unclassified
core rejection. Suggested tools are names only: this function never calls them.

The classifier reads the public situation and the binding metadata of options
already issued by route_options/evacuation_options. It does not compute routes,
advance the episode, or inspect physical state or private evaluation records.
The core validator remains authoritative; this module does not alter commit or
batch semantics. An unexpected validator exception is reported as an invalid
malformed action with a fixed public error, rather than exposing its contents.
"""

import json
from . import _source_sha256

_LOADED_SOURCE_SHA256 = _source_sha256(__file__)


_FIELDS = ('requests', 'authorizations', 'releases', 'status_requests', 'protection_orders')
_ALLOWED = frozenset((*_FIELDS, 'advance_minutes'))
_MESSAGES = {
    'invalid_action_format': 'Provide a bounded JSON action object.',
    'unknown_action_field': 'The action contains an unsupported field.',
    'invalid_advance_minutes': 'Wait must be from 1 to 60 minutes and cannot include orders.',
    'invalid_order_list': 'The indicated order field must be a list containing at most six entries.',
    'invalid_request': 'A resource request needs resource_id, sector_id and approach_id.',
    'duplicate_resource_order': 'Submit at most one mission-changing order per resource.',
    'unknown_resource_or_sector': 'Choose a resource and sector from the current public situation.',
    'invalid_resource_approach': 'Choose an approach proposed for this resource and sector.',
    'request_pending': 'A request for this resource is already pending.',
    'invalid_offer_id': 'Provide a valid public offer ID.',
    'unknown_offer': 'The offer is not in the current public situation.',
    'offer_not_live': 'This offer is no longer available for authorization.',
    'unknown_resource': 'Choose a resource from the current public situation.',
    'invalid_status_request': 'Choose a known centre and a supported status subject.',
    'invalid_protection_order': 'Protection orders need group, action, destination and approach fields.',
    'unknown_group': 'Choose a population group from the current public situation.',
    'invalid_protection_action': 'Choose shelter or evacuate.',
    'duplicate_protection_order': 'Submit at most one protection order per population group.',
    'unknown_destination': 'Choose a destination from the current public situation.',
    'invalid_shelter_destination': 'Sheltering does not use a destination or an evacuation approach.',
    'invalid_evacuation_approach': 'Choose an approach proposed for this group and destination.',
    'invalid_action': 'The action did not pass validation.',
}


def _problem(code, field=None, index=None, next_tool=None):
    result = {'code': code}
    if field is not None:
        result['field'] = field
    if index is not None:
        result['order_index'] = index
    if next_tool is not None:
        result['next_tool'] = next_tool
    return result


def _identifier(value):
    return isinstance(value, str) and bool(value)


def _classify(env, action):
    if not isinstance(action, dict):
        return _problem('invalid_action_format')
    try:
        if len(json.dumps(action, allow_nan=False).encode()) > 65536:
            return _problem('invalid_action_format')
    except (TypeError, ValueError, OverflowError, RecursionError):
        return _problem('invalid_action_format')
    if any(key not in _ALLOWED for key in action):
        return _problem('unknown_action_field')
    if 'advance_minutes' in action:
        wait = action['advance_minutes']
        if len(action) != 1 or type(wait) is not int or not 1 <= wait <= 60:
            return _problem('invalid_advance_minutes', 'advance_minutes')
        return None
    for field in _FIELDS:
        value = action.get(field, [])
        if not isinstance(value, list) or len(value) > 6:
            return _problem('invalid_order_list', field)

    situation = env.get_situation()
    resources = {item['id'] for item in situation['resources']}
    groups = {item['id'] for item in situation['districts']}
    destinations = {item['id'] for item in situation['destinations']}
    centers = set(situation['centers'])
    offers = {item['id']: item for item in situation['offers']}
    pending = set(situation['pending_resource_requests'])
    # These are bindings for options that the environment has already returned
    # to the caller, not a fresh route query or an inspection of route safety.
    approaches = env._approaches
    assigned = set()
    for index, request in enumerate(action.get('requests', [])):
        field = 'requests'
        if not isinstance(request, dict) or set(request) != {'resource_id', 'sector_id', 'approach_id'}:
            return _problem('invalid_request', field, index)
        resource, sector, approach = (request[k] for k in ('resource_id', 'sector_id', 'approach_id'))
        if not all(_identifier(x) for x in (resource, sector, approach)):
            return _problem('invalid_request', field, index)
        if resource in assigned:
            return _problem('duplicate_resource_order', field, index)
        if resource not in resources or sector != 'incident':
            return _problem('unknown_resource_or_sector', field, index, 'get_situation')
        option = approaches.get(approach)
        if not isinstance(option, dict) or option.get('resource_id') != resource or option.get('sector_id') != sector:
            return _problem('invalid_resource_approach', field, index, 'route_options')
        assigned.add(resource)
        if resource in pending:
            return _problem('request_pending', field, index, 'get_situation')
    for index, identifier in enumerate(action.get('authorizations', [])):
        field = 'authorizations'
        if not _identifier(identifier):
            return _problem('invalid_offer_id', field, index)
        offer = offers.get(identifier)
        if offer is None:
            return _problem('unknown_offer', field, index, 'get_situation')
        if offer['status'] != 'offered' or offer['expires_at'] <= situation['minute']:
            return _problem('offer_not_live', field, index, 'get_situation')
        resource = offer['resource_id']
        if resource in assigned:
            return _problem('duplicate_resource_order', field, index)
        assigned.add(resource)
    for index, identifier in enumerate(action.get('releases', [])):
        field = 'releases'
        if not _identifier(identifier) or identifier not in resources:
            return _problem('unknown_resource', field, index, 'get_situation')
        if identifier in assigned:
            return _problem('duplicate_resource_order', field, index)
        assigned.add(identifier)
    for index, request in enumerate(action.get('status_requests', [])):
        if (not isinstance(request, dict) or set(request) != {'center_id', 'subject'}
                or not _identifier(request.get('center_id')) or request['center_id'] not in centers
                or request.get('subject') not in ('resources', 'situation')):
            return _problem('invalid_status_request', 'status_requests', index, 'get_situation')
    protected = set()
    for index, order in enumerate(action.get('protection_orders', [])):
        field = 'protection_orders'
        if not isinstance(order, dict) or set(order) != {'group_id', 'action', 'destination_id', 'approach_id'}:
            return _problem('invalid_protection_order', field, index)
        group, kind, destination, approach = (order[k] for k in ('group_id', 'action', 'destination_id', 'approach_id'))
        if not _identifier(group) or group not in groups:
            return _problem('unknown_group', field, index, 'get_situation')
        if kind not in ('shelter', 'evacuate'):
            return _problem('invalid_protection_action', field, index)
        if group in protected:
            return _problem('duplicate_protection_order', field, index)
        protected.add(group)
        if kind == 'shelter':
            if destination is not None or approach is not None:
                return _problem('invalid_shelter_destination', field, index)
        else:
            if not _identifier(destination) or destination not in destinations:
                return _problem('unknown_destination', field, index, 'get_situation')
            option = approaches.get(approach) if _identifier(approach) else None
            if not isinstance(option, dict) or option.get('group_id') != group or option.get('destination_id') != destination:
                return _problem('invalid_evacuation_approach', field, index, 'evacuation_options')
    return None


def explain_result_v3(env, action, result):
    """Explain an authoritative core check result without making another check."""
    if result['valid']:
        return {'valid': True, 'error': result['error'], 'code': None}
    diagnostic = _classify(env, action)
    details = diagnostic or _problem('invalid_action')
    result = {'valid': False, 'error': _MESSAGES[details['code']], **details}
    if details['code'] in ('invalid_resource_approach', 'invalid_evacuation_approach'):
        result['field_path'] = f"{details['field']}[{details['order_index']}].approach_id"
    return result


def check_action_v3(env, action):
    """Return core validity and deterministic, public-only diagnostic metadata."""
    try:
        result = env.check_action(action)
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
        result = {'valid': False, 'error': 'Malformed action'}
    return explain_result_v3(env, action, result)
