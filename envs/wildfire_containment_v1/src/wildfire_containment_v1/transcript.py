"""Public call recording for the opt-in containment incident-day adapter."""
import functools
import inspect
import json
import math

TRANSCRIPT_VERSION = 'wildfire-containment-public-transcript-v1'


def json_equal(left, right):
    """Compare JSON encodings, retaining bool/int/float distinctions."""
    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return canonical(left) == canonical(right)


PUBLIC_CALLS = frozenset(('step', 'advance_time', 'request_status', 'check_action',
                          'get_situation', 'get_reports', 'route_options',
                          'evacuation_options', 'propose_hold', 'authorize_hold', 'outcomes'))


def _check_json(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _check_json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _check_json(item)
        return
    raise ValueError('Public call arguments must be finite JSON values')


def json_result(value):
    """Normalize returned tuples to JSON arrays, and detach all references."""
    return json.loads(json.dumps(value, allow_nan=False))


def arguments(args, kwargs):
    value = {'args': list(args), 'kwargs': kwargs}
    _check_json(value)
    return json_result(value)


# These frozen methods only read delivered/public data; exceptions from all
# other public calls may have mutated cached approaches, commands or physics.
_READ_ONLY = frozenset(('get_situation', 'get_reports', 'check_action', 'outcomes'))


def transcribed(function):
    """Record only the outermost public call, including actor-visible errors."""
    signature = inspect.signature(function, follow_wrapped=False)

    @functools.wraps(function)
    def wrapped(*call_args, **kwargs):
        # A named wrapper parameter called "self" would itself reject an
        # actor-supplied self keyword before we can record the binding error.
        if call_args:
            self, args = call_args[0], call_args[1:]
        elif 'self' in kwargs:
            # Preserve direct, unbound class calls with the instance by keyword.
            kwargs = dict(kwargs)
            self, args = kwargs.pop('self'), ()
        else:
            raise TypeError('A bound adapter instance is required')
        if not self._transcript_depth:
            self._check_public_runtime()
        if not self._transcript_enabled or self._transcript_depth:
            return function(self, *args, **kwargs)
        # Reject unsupported values before a possibly time-consuming operation.
        args_json = arguments(args, kwargs)
        before = self.minute if self._scenario is not None else None
        safe_error_recorded = False
        self._safe_validation_error = None
        self._transcript_depth += 1
        try:
            try:
                try:
                    # A binding failure cannot have entered the method, so it
                    # is safe to record even for queries that can mutate later.
                    try:
                        signature.bind(self, *args, **kwargs)
                    except TypeError as error:
                        self._safe_validation_error = error
                        raise
                    result = function(self, *args, **kwargs)
                except BaseException as error:
                    # Interruptions are never safe, even on reads: they may
                    # arrive while the public record itself is being written.
                    safe = (isinstance(error, Exception) and
                            (error is self._safe_validation_error or function.__name__ in _READ_ONLY))
                    if not safe:
                        self._transcript_valid = False
                    self._transcript.append(dict(operation=function.__name__, arguments=args_json,
                        before_minute=before, after_minute=self.minute if self._scenario is not None else None,
                        error=dict(type=type(error).__name__, message=str(error))))
                    safe_error_recorded = safe
                    raise
                try:
                    value = json_result(result)
                except (TypeError, ValueError, OverflowError) as error:
                    raise RuntimeError('Public result is not JSON-safe; transcript cannot be replayed') from error
                self._transcript.append(dict(operation=function.__name__, arguments=args_json,
                    before_minute=before, after_minute=self.minute if self._scenario is not None else None,
                    result=value))
                return result
            except BaseException:
                if not safe_error_recorded:
                    self._transcript_valid = False
                raise
        finally:
            self._safe_validation_error = None
            self._transcript_depth -= 1
    return wrapped
