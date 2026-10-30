"""Agent-facing control wrapper; simulation, commands, and reward remain in v2."""
import copy
import hashlib
import json

from wildfire_core.incident_day import IncidentDayTools

from . import INTERFACE_VERSION


def _fingerprint(value):
    """A stable fingerprint for public JSON data, never a model-visible digest."""
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(raw).digest()


class ControlCenterV3:
    """Wrap one already-reset incident-day environment without changing its engine.

    Three identical invalid draft checks in an unchanged public situation are
    allowed; further identical checks receive a guard response. The guard never
    advances time, commits orders, or blocks reads, queries or committed actions.
    A new public situation, a changed draft, or a route query allows checking
    again. A newly constructed wrapper starts a new check budget.
    """

    version = INTERFACE_VERSION

    def __init__(self, environment, *, identical_check_limit=3):
        if type(identical_check_limit) is not int or identical_check_limit < 1:
            raise ValueError('identical_check_limit must be a positive integer')
        self.environment = environment
        self._tools = IncidentDayTools(environment)
        self.identical_check_limit = identical_check_limit
        self._last_failed_check = None
        self._repetitions = 0

    def execute(self, name, arguments):
        if name == 'advance':
            if not isinstance(arguments, dict) or set(arguments) - {'max_minutes'}:
                return {'error': 'advance accepts only optional max_minutes', 'committed': False}
            name = 'advance_time'
            arguments = {'max_minutes': arguments.get('max_minutes', 60)}
        if name == 'check_action' and isinstance(arguments, dict) and set(arguments) == {'action'}:
            situation_key = _fingerprint(self.environment.get_situation())
            action_key = _fingerprint(arguments['action'])
            signature = (situation_key, action_key) if situation_key and action_key else None
            if signature is not None and signature == self._last_failed_check and self._repetitions >= self.identical_check_limit:
                return {'valid': False, 'error': 'This unchanged draft has already failed the check.',
                        'code': 'repeated_invalid_draft',
                        'next_step': 'Change the draft, obtain fresh options, or take another valid action.'}
            result = self._tools.execute(name, arguments)
            if isinstance(result, dict) and result.get('valid') is False and signature is not None:
                if signature == self._last_failed_check:
                    self._repetitions += 1
                else:
                    self._last_failed_check, self._repetitions = signature, 1
            else:
                self._last_failed_check, self._repetitions = None, 0
            return copy.deepcopy(result)
        result = self._tools.execute(name, arguments)
        if name in ('route_options', 'evacuation_options', 'commit_orders', 'request_status', 'advance_time'):
            self._last_failed_check, self._repetitions = None, 0
        return copy.deepcopy(result)
