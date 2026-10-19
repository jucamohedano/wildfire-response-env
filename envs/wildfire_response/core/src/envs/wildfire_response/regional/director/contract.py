"""Model-facing projection and named action schemas built from public observations."""
import copy
import json

CONTRACT_VERSION = 'regional-director-tools-v1'
ACTION_FIELDS = ('requests', 'authorizations', 'releases', 'messages', 'explanation')
RECORD_FIELDS = dict(
    geography=('width', 'height', 'cell_km', 'northwest_lon_lat', 'longitude_km_per_degree', 'transform'),
    assumptions=('inventory', 'travel', 'effectiveness'),
    rules=('time', 'objective', 'wind', 'inform', 'evacuate', 'dispatch', 'authority', 'offer_timing', 'aircraft', 'grounding'),
    bases=('id', 'name', 'type', 'coordinates'),
    resources=('id', 'kind', 'home_base', 'people', 'capacity_litres', 'water_litres', 'state', 'sector_id', 'mission', 'due_at', 'target'),
    offers=('id', 'status', 'resource_id', 'sector_id', 'mission', 'target', 'lead_time_minutes', 'expires_at'),
    reports=('id', 'observed_at', 'received_at', 'available_at', 'location', 'text', 'source', 'source_role', 'channel'),
    sectors=('id', 'center', 'observed_at'),
    districts=('id', 'name', 'population', 'home', 'position', 'refuge', 'status', 'exposed', 'basis'),
    sensor_memory=('x', 'y', 'burning', 'observed_at', 'source'),
    acknowledgements=('minute', 'source', 'recipient', 'kind', 'resource_id', 'request_id', 'offer_id', 'sector_id', 'via', 'purpose', 'effect', 'reason', 'litres'),
    pending_deliveries=('kind', 'due_at'))


def public_context(observation):
    context = {key: copy.deepcopy(observation[key]) for key in (
        'schema_version', 'minute', 'decision', 'terrain_basis', 'wind', 'daylight', 'aircraft_operable')}
    for field, keys in RECORD_FIELDS.items():
        value = observation[field]
        if isinstance(value, list):
            context[field] = [{key: copy.deepcopy(record[key]) for key in keys if key in record} for record in value]
        else:
            context[field] = {key: copy.deepcopy(value[key]) for key in keys if key in value}
    return context


def object_schema(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def identifiers(values):
    return dict(type='string', enum=values) if values else dict(type='string')


def action_schema(observation):
    resources = [unit['id'] for unit in observation['resources']]
    sectors = [sector['id'] for sector in observation['sectors']]
    offers = [offer['id'] for offer in observation['offers'] if offer['status'] == 'offered'
              and offer['expires_at'] > observation['minute']]
    releasable = [unit['id'] for unit in observation['resources'] if unit['state'] in (
        'reserved', 'mobilizing', 'en_route', 'on_scene')]
    recipients = [item['id'] for field in ('bases', 'districts') for item in observation[field]]
    return object_schema(dict(
        requests=dict(type='array', maxItems=12, items=object_schema(dict(
            resource_id=identifiers(resources), sector_id=identifiers(sectors),
            mission=dict(type='string', enum=['recon', 'suppress'])))),
        authorizations=dict(type='array', maxItems=12 if offers else 0, items=identifiers(offers)),
        releases=dict(type='array', maxItems=12 if releasable else 0, items=identifiers(releasable)),
        messages=dict(type='array', maxItems=12, items=object_schema(dict(
            recipient=identifiers(recipients), purpose=dict(type='string', enum=['status', 'inform', 'evacuate'])))),
        explanation=dict(type='string', maxLength=1000)))


def parse_action(arguments):
    if not isinstance(arguments, str) or len(arguments.encode()) > 65536:
        raise ValueError('Action arguments must be a JSON string of at most 64 KiB')

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError('Nonfinite JSON value')

    action = json.loads(arguments, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(action, dict) or set(action) != set(ACTION_FIELDS):
        raise ValueError('Supply exactly the five regional action fields')
    if not isinstance(action['explanation'], str) or len(action['explanation']) > 1000:
        raise ValueError('Explanation must contain at most 1000 characters')
    for field in ACTION_FIELDS[:-1]:
        if not isinstance(action[field], list) or len(action[field]) > 12:
            raise ValueError('Action lists are limited to twelve entries')
    for field, keys in (('requests', ('resource_id', 'sector_id', 'mission')), ('messages', ('recipient', 'purpose'))):
        for item in action[field]:
            if (not isinstance(item, dict) or set(item) != set(keys)
                    or any(not isinstance(value, str) for value in item.values())):
                raise ValueError('Invalid structured action item')
    if any(not isinstance(value, str) for field in ('authorizations', 'releases') for value in action[field]):
        raise ValueError('Authorization and release IDs must be strings')
    return action
