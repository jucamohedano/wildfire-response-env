"""Provider-independent named tools for a control-centre policy."""
import copy


def _object(properties, required):
    return dict(type='object', properties=properties, required=required, additionalProperties=False)


def tools():
    identifier = dict(type='string', minLength=1, maxLength=128)
    nullable_identifier = dict(type=['string', 'null'], maxLength=128)
    resource_request = _object(dict(resource_id=identifier, sector_id=identifier, approach_id=identifier),
                               ['resource_id', 'sector_id', 'approach_id'])
    protection = _object(dict(group_id=identifier, action=dict(type='string', enum=['shelter', 'evacuate']),
                              destination_id=nullable_identifier, approach_id=nullable_identifier),
                         ['group_id', 'action', 'destination_id', 'approach_id'])
    status = _object(dict(center_id=identifier, subject=dict(type='string', enum=['resources', 'situation'])),
                     ['center_id', 'subject'])
    action = _object({name: dict(type='array', items=item, maxItems=6)
                      for name, item in dict(requests=resource_request, authorizations=identifier,
                          releases=identifier, protection_orders=protection, status_requests=status).items()}, [])
    definitions = [
        ('get_situation', 'Read delivered operational knowledge; no time passes.', _object({}, [])),
        ('get_reports', 'Read at most20 already-delivered reports, including older evidence.',
         _object(dict(ids=dict(type='array', items=identifier, maxItems=20),
                      after_cursor=dict(type='integer', minimum=0), limit=dict(type='integer', minimum=1, maximum=20)), [])),
        ('route_options', 'Propose safe staging approaches using last-known hazards, not global truth.',
         _object(dict(resource_id=identifier, sector_id=identifier), ['resource_id'])),
        ('evacuation_options', 'Propose a routed approach to a reported evacuation destination.',
         _object(dict(group_id=identifier, destination_id=identifier), ['group_id', 'destination_id'])),
        ('check_action', 'Validate a draft against public knowledge without committing or advancing time.',
         _object(dict(action=action), ['action'])),
        ('commit_orders', 'Commit bounded structured orders. Time advances one minute even for invalid orders.',
         _object(dict(action=action), ['action'])),
        ('request_status', 'Ask a centre for its timestamped resource or situation knowledge. Costs one minute.',
         _object(dict(center_id=identifier, subject=dict(type='string', enum=['resources', 'situation'])),
                 ['center_id', 'subject'])),
        ('advance_time', 'Wait up to60minutes; stop earlier for actionable delivery or an operational deadline.',
         _object(dict(max_minutes=dict(type='integer', minimum=1, maximum=60)), ['max_minutes'])),
    ]
    return [dict(type='function', function=dict(name=name, description=description, parameters=schema))
            for name, description, schema in definitions]


class IncidentDayTools:
    def __init__(self, environment):
        self.environment = environment
        self.calls = []

    def execute(self, name, arguments):
        schemas = {definition['function']['name']: definition['function']['parameters'] for definition in tools()}
        functions = dict(get_situation=self.environment.get_situation, get_reports=self.environment.get_reports,
            route_options=self.environment.route_options, evacuation_options=self.environment.evacuation_options,
            check_action=self.environment.check_action, commit_orders=lambda action: self.environment.step(action),
            request_status=self.environment.request_status, advance_time=self.environment.advance_time)
        record = dict(name=name, arguments=copy.deepcopy(arguments), minute=self.environment.minute)
        try:
            if not isinstance(name, str) or name not in schemas or not isinstance(arguments, dict):
                raise ValueError('A named tool and argument object are required')
            schema = schemas[name]
            if set(arguments) - set(schema['properties']) or set(schema['required']) - set(arguments):
                raise ValueError('Tool arguments do not match the named contract')
            result = functions[name](**arguments)
        except (ValueError, TypeError, KeyError, RuntimeError) as error:
            record['error'] = str(error)
            self.calls.append(record)
            return dict(error=str(error), committed=False)
        self.calls.append(record)
        if name in ('commit_orders', 'request_status', 'advance_time'):
            observation, reward, terminated, truncated, info = result
            return dict(observation=observation, reward=reward, terminated=terminated, truncated=truncated, info=info)
        return copy.deepcopy(result)
