"""A narrow, explicit public-method bridge; no simulator or model access."""

import copy
import hashlib
import json
import math

VERSION = 'wildfire-containment-agent-tools-v1'
LOG_VERSION = 'wildfire-containment-agent-call-log-v1'
MAX_CALLS = 256
MAX_INPUT_BYTES = 65536
MAX_LOG_BYTES = 8 * 1024 * 1024
_RESERVE_BYTES = 2 * 1024 * 1024
_INPUT_ERROR = 'Tool call must contain bounded finite JSON values'
_CAPACITY_ERROR = 'Tool call log capacity exhausted'


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ValueError(_INPUT_ERROR)


def _object(properties, required):
    return dict(type='object', properties=properties, required=required, additionalProperties=False)


def tools():
    """Return fresh provider-independent function definitions and JSON schemas."""
    identifier = dict(type='string', minLength=1, maxLength=128)
    request = _object(dict(resource_id=identifier, sector_id=identifier, approach_id=identifier),
                      ['resource_id', 'sector_id', 'approach_id'])
    nullable_identifier = dict(type=['string', 'null'], maxLength=128)
    protection = _object(dict(group_id=identifier, action=dict(type='string', enum=['shelter', 'evacuate']),
                              destination_id=nullable_identifier, approach_id=nullable_identifier),
                         ['group_id', 'action', 'destination_id', 'approach_id'])
    protection['oneOf'] = [
        {'properties': {'action': {'const': 'shelter'}, 'destination_id': {'type': 'null'},
                        'approach_id': {'type': 'null'}}},
        {'properties': {'action': {'const': 'evacuate'}, 'destination_id': identifier,
                        'approach_id': identifier}},
    ]
    status = _object(dict(center_id=identifier, subject=dict(type='string', enum=['resources', 'situation'])),
                     ['center_id', 'subject'])
    action = _object({name: dict(type='array', items=item, maxItems=6)
                      for name, item in dict(requests=request, authorizations=identifier,
                                             releases=identifier, protection_orders=protection,
                                             status_requests=status).items()}, [])
    action['properties']['advance_minutes'] = dict(type='integer', minimum=1, maximum=60)
    action['allOf'] = [{'if': {'required': ['advance_minutes']}, 'then': {'maxProperties': 1}}]
    definitions = [
        ('get_situation', 'Read the latest delivered public situation without advancing time.', _object({}, [])),
        ('route_options', 'Get public approaches using last-known hazards.',
         _object(dict(resource_id=identifier, sector_id=identifier), ['resource_id'])),
        ('evacuation_options', 'Get public approaches for a reported group and destination.',
         _object(dict(group_id=identifier, destination_id=identifier), ['group_id', 'destination_id'])),
        ('request_resource', 'Request one resource using its public route; commits one minute, even when invalid.',
         _object(dict(resource_id=identifier, sector_id=identifier, approach_id=identifier),
                 ['resource_id', 'sector_id', 'approach_id'])),
        ('propose_hold', 'Get public line proposals for a live delivered ground-resource offer.',
         _object(dict(offer_id=identifier), ['offer_id'])),
        ('authorize_hold', 'Authorize a live offer using one of its public proposals; commits one minute.',
         _object(dict(offer_id=identifier, proposal_id=identifier), ['offer_id', 'proposal_id'])),
        ('check_action', 'Check a draft action without committing or advancing time.',
         _object(dict(action=copy.deepcopy(action)), ['action'])),
        ('submit_action', 'Commit incident-day orders or a wait; invalid orders also consume one minute.',
         _object(dict(action=copy.deepcopy(action)), ['action'])),
        ('request_status', 'Request a centre resource or situation report; commits one minute.',
         _object(dict(center_id=identifier, subject=dict(type='string', enum=['resources', 'situation'])),
                 ['center_id'])),
        ('advance_time', 'Wait up to 60 minutes; actionable deliveries may interrupt the wait.',
         _object(dict(max_minutes=dict(type='integer', minimum=1, maximum=60)), ['max_minutes'])),
        ('get_reports', 'Read at most 20 already-delivered reports by public ID or cursor.',
         _object(dict(ids=dict(type='array', items=identifier, maxItems=20),
                      after_cursor=dict(type='integer', minimum=0),
                      limit=dict(type='integer', minimum=1, maximum=20)), [])),
    ]
    return [dict(type='function', function=dict(name=name, description=description, parameters=schema))
            for name, description, schema in definitions]


class ContainmentAgentTools:
    """Dispatch a fixed allowlist to an episode controlled by a trusted host.

    This is an interface boundary, not a Python sandbox. Exceptions from the
    episode propagate unchanged; callers must handle uncertain failures as the
    underlying episode requires.
    """

    def __init__(self, environment):
        self._environment = environment
        self._calls = []
        self._valid_log = True
        self._closed = False

    def host_log(self):
        """Return a detached public call log; trusted host only, not an agent tool."""
        if not self._valid_log:
            raise RuntimeError('Host call log is invalid; discard this adapter')
        return json.loads(_canonical(dict(version=LOG_VERSION, calls=self._calls, closed=self._closed)))

    def host_log_sha256(self):
        return hashlib.sha256(_canonical(self.host_log()).encode('utf-8')).hexdigest()

    def replay_host_log(self, log, *, expected_sha256):
        """Replay into a new host adapter whose episode was reset by a trusted host.

        The digest must be independently trusted; discard this adapter after any
        error. The host is responsible for recreating the episode's trusted pins.
        """
        if self._calls or self._closed or not self._valid_log:
            raise ValueError('Replay requires a fresh tool adapter')
        try:
            _json_value(log)
            encoded = _canonical(log).encode('utf-8')
            if (len(encoded) > MAX_LOG_BYTES or hashlib.sha256(encoded).hexdigest() != expected_sha256
                    or type(log) is not dict or set(log) != {'version', 'calls', 'closed'}
                    or log['version'] != LOG_VERSION or type(log['calls']) is not list
                    or type(log['closed']) is not bool or len(log['calls']) > MAX_CALLS + 1):
                raise ValueError('Invalid host call log or digest')
        except (TypeError, ValueError, RecursionError, OverflowError) as error:
            raise ValueError('Invalid host call log or digest') from error
        for entry in log['calls']:
            if type(entry) is not dict:
                self._valid_log = False
                raise ValueError('Invalid host call entry')
            try:
                if entry.get('input_redacted') is True:
                    self.execute(None, object())
                else:
                    self.execute(entry['name'], entry['arguments'])
            except (ValueError, TypeError, KeyError, RuntimeError):
                pass
            if not self._valid_log or not self._calls or _canonical(self._calls[-1]) != _canonical(entry):
                self._valid_log = False
                raise ValueError('Host call replay mismatch')
        if _canonical(self.host_log()) != _canonical(log):
            self._valid_log = False
            raise ValueError('Host call replay mismatch')

    def _append(self, entry):
        try:
            normalized = json.loads(_canonical(entry))
            prospective = dict(version=LOG_VERSION, calls=[*self._calls, normalized], closed=self._closed)
            if len(_canonical(prospective).encode('utf-8')) > MAX_LOG_BYTES:
                raise ValueError('Host call log limit exceeded')
            self._calls.append(normalized)
        except (ValueError, TypeError, RecursionError, OverflowError):
            self._valid_log = False
            raise RuntimeError('Host call log is invalid; discard this adapter') from None

    def execute(self, name, arguments):
        if not self._valid_log or self._closed:
            raise RuntimeError('Host call log is closed or invalid; discard this adapter')
        recorded_error = None
        try:
            try:
                input_value = dict(name=name, arguments=arguments)
                _json_value(input_value)
                if len(_canonical(input_value).encode('utf-8')) > MAX_INPUT_BYTES:
                    raise ValueError(_INPUT_ERROR)
                entry = json.loads(_canonical(input_value))
            except (TypeError, ValueError, RecursionError, OverflowError):
                entry = {'input_redacted': True}
            if (len(self._calls) >= MAX_CALLS or
                    len(_canonical(self.host_log()).encode('utf-8')) > MAX_LOG_BYTES - _RESERVE_BYTES):
                self._closed = True
                self._append(dict(**entry, error={'type': 'RuntimeError', 'message': _CAPACITY_ERROR}))
                recorded_error = RuntimeError(_CAPACITY_ERROR)
                raise recorded_error
            try:
                if 'input_redacted' in entry:
                    raise ValueError(_INPUT_ERROR)
                result = self._dispatch(name, arguments)
            except (ValueError, TypeError, KeyError, RuntimeError) as error:
                self._append(dict(**entry, error={'type': type(error).__name__, 'message': str(error)}))
                recorded_error = error
                raise
            self._append(dict(**entry, result=result))
            return result
        except BaseException as error:
            # Only the exact error already recorded may leave the log valid.
            # Interruptions after append, including before return/re-raise,
            # differ from the recorded outcome and invalidate the log.
            if error is not recorded_error:
                self._valid_log = False
            raise

    def _dispatch(self, name, arguments):
        definitions = {item['function']['name']: item['function']['parameters'] for item in tools()}
        if not isinstance(name, str) or name not in definitions or not isinstance(arguments, dict):
            raise ValueError('A named tool and argument object are required')
        schema = definitions[name]
        if set(arguments) - set(schema['properties']) or set(schema['required']) - set(arguments):
            raise ValueError('Tool arguments do not match the named contract')
        env = self._environment
        if name == 'request_resource':
            result = env.step({'requests': [dict(resource_id=arguments['resource_id'],
                                                  sector_id=arguments['sector_id'],
                                                  approach_id=arguments['approach_id'])]})
        else:
            methods = dict(get_situation=env.get_situation, route_options=env.route_options,
                           evacuation_options=env.evacuation_options, submit_action=env.step,
                           propose_hold=env.propose_hold, authorize_hold=env.authorize_hold,
                           check_action=env.check_action, advance_time=env.advance_time,
                           get_reports=env.get_reports, request_status=env.request_status)
            result = methods[name](**arguments)
        if name in ('request_resource', 'submit_action', 'authorize_hold', 'advance_time', 'request_status'):
            observation, reward, terminated, truncated, info = result
            return dict(observation=observation, reward=reward, terminated=terminated,
                        truncated=truncated, info=info)
        return result
