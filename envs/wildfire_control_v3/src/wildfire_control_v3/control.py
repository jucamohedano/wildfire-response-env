"""Agent-facing control wrapper; simulation, commands, and reward remain in v2."""
import copy
import hashlib
import json
from pathlib import Path
from threading import RLock

from wildfire_core.incident_day import IncidentDayEnv, IncidentDayTools, tools

from . import (INTERFACE_VERSION, _LOADED_PACKAGE_CODE_MATCH, _LOADED_PACKAGE_SOURCE_SHA256,
               _capture_source, _source_sha256)
from . import action_feedback_v3 as _feedback_module, briefing_v3 as _briefing_module
from . import knowledge_v3 as _knowledge_module, verifier_v3 as _verifier_module
from .action_feedback_v3 import check_action_v3, explain_result_v3
from .briefing_v3 import build_briefing_v3
from .knowledge_v3 import reconcile_situation_v3
from .verifier_v3 import VERSION as VERIFIER_VERSION, verify_outcome


def tool_definitions_v3():
    """Offer a compact page and explicit ledger pages alongside existing tools."""
    definitions = copy.deepcopy(tools())
    for definition in definitions:
        if definition['function']['name'] == 'advance_time':
            definition['function'].update(name='advance', description='Wait until an actionable update or deadline, up to max_minutes (default 60).')
            definition['function']['parameters']['required'] = []
    definitions.extend([
        {'type': 'function', 'function': {'name': 'get_briefing',
         'description': 'Read a compact public briefing; use get_situation and get_report_page for complete delivered information.',
         'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}}},
        {'type': 'function', 'function': {'name': 'get_report_page',
         'description': 'Read a page of delivered reports with explicit continuation metadata.',
         'parameters': {'type': 'object', 'properties': {
             'after_cursor': {'type': 'integer', 'minimum': 0},
             'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}},
             'required': [], 'additionalProperties': False}}},
    ])
    return definitions


def _fingerprint(value):
    """A stable fingerprint for public JSON data, never a model-visible digest."""
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError):
        try:
            # Malformed Python callers can supply mixed key types; distinguish
            # changed drafts without exposing their contents in guard responses.
            raw = b'malformed:' + repr(value).encode('utf-8', 'backslashreplace')
        except Exception:
            return None
    return hashlib.sha256(raw).digest()


def interface_digest():
    """Pin the additive interface separately from the frozen engine digest."""
    package = Path(__file__).parent
    files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(package.glob('*.py'))}
    return _fingerprint(files).hex()


_LOADED_CONTROL_SOURCE_SHA256, _LOADED_CONTROL_CODE_MATCH = _capture_source(__name__, __file__, __loader__)


def _loaded_sources_match():
    try:
        package = Path(__file__).parent
        modules = (_feedback_module, _briefing_module, _knowledge_module, _verifier_module)
        return (_LOADED_PACKAGE_CODE_MATCH and _LOADED_CONTROL_CODE_MATCH
                and _source_sha256(package / '__init__.py') == _LOADED_PACKAGE_SOURCE_SHA256
                and _source_sha256(__file__) == _LOADED_CONTROL_SOURCE_SHA256
                and all(module._LOADED_CODE_MATCH
                        and Path(module.__file__).resolve().parent == package.resolve()
                        and _source_sha256(module.__file__) == module._LOADED_SOURCE_SHA256
                        for module in modules))
    except (OSError, AttributeError, TypeError):
        return False


# A long-running driver must restart after editing the interface package. The
# post-verification check below also detects edits made during a replay.
_LOADED_INTERFACE_SHA256 = interface_digest()


class ControlCenterV3:
    """Wrap one already-reset incident-day environment without changing its engine.

    Three identical invalid draft checks in an unchanged public situation are
    allowed; further identical checks receive a guard response. The guard never
    advances time, commits orders, or blocks reads, queries or committed actions.
    A new public situation, a changed draft, or newly returned route options
    allows checking again. A newly constructed wrapper starts a new check budget.
    """

    version = INTERFACE_VERSION

    def __init__(self, environment, *, identical_check_limit=3):
        if type(identical_check_limit) is not int or identical_check_limit < 1:
            raise ValueError('identical_check_limit must be a positive integer')
        self.environment = environment
        self._tools = IncidentDayTools(environment)
        self.identical_check_limit = identical_check_limit
        self._lock = RLock()
        self._last_failed_check = None
        self._repetitions = 0
        self._option_snapshots = {}

    def reset(self, scenario_id=None, seed=None):
        """Reset both the underlying episode and this interface's guard state."""
        with self._lock:
            observation, info = self.environment.reset(scenario_id, seed)
            self._tools = IncidentDayTools(self.environment)
            self._last_failed_check, self._repetitions = None, 0
            self._option_snapshots = {}
            info = copy.deepcopy(info)
            info['control_interface_version'] = self.version
            try:
                info['briefing'] = self._briefing()
            except Exception:
                info['briefing_error'] = 'Briefing unavailable; the full public observation is included.'
            return observation, info

    def private_checkpoint(self):
        """Return trusted-driver state, including the engine's private checkpoint.

        Never expose this method or its result as an agent tool or public artifact.
        """
        with self._lock:
            if not _loaded_sources_match() or interface_digest() != _LOADED_INTERFACE_SHA256:
                raise RuntimeError('Control interface source changed; restart before checkpointing')
            saved_guard = self._last_failed_check
            if saved_guard is not None and saved_guard[0] != _fingerprint(self.environment.get_situation()):
                saved_guard = None
            signature = None if saved_guard is None else [x.hex() for x in saved_guard]
            state = {'version': self.version, 'interface_sha256': interface_digest(),
                     'identical_check_limit': self.identical_check_limit,
                     'last_failed_check': signature, 'repetitions': self._repetitions if signature else 0,
                     'option_snapshots': [[key.hex(), value.hex()] for key, value in sorted(self._option_snapshots.items())],
                     'engine_checkpoint': self.environment.checkpoint()}
            return {**state, 'sha256': _fingerprint(state).hex()}

    def restore_private_checkpoint(self, checkpoint):
        """Restore a trusted checkpoint into this wrapper's dedicated environment."""
        with self._lock:
            if not isinstance(checkpoint, dict):
                raise ValueError('Invalid control checkpoint')
            value = copy.deepcopy(checkpoint)
            expected = {'version', 'interface_sha256', 'identical_check_limit', 'last_failed_check',
                        'repetitions', 'option_snapshots', 'engine_checkpoint', 'sha256'}
            state = {key: item for key, item in value.items() if key != 'sha256'}
            actual = _fingerprint(state)
            if (set(value) != expected or actual is None or value['sha256'] != actual.hex()
                    or value['version'] != self.version or value['interface_sha256'] != interface_digest()
                    or not _loaded_sources_match() or interface_digest() != _LOADED_INTERFACE_SHA256
                    or value['identical_check_limit'] != self.identical_check_limit
                    or type(value['repetitions']) is not int or not 0 <= value['repetitions'] <= self.identical_check_limit):
                raise ValueError('Control checkpoint identity or integrity differs')
            signature = value['last_failed_check']
            if signature is None:
                if value['repetitions'] != 0:
                    raise ValueError('Invalid control guard state')
            elif (not isinstance(signature, list) or len(signature) != 2 or value['repetitions'] == 0
                  or any(not isinstance(item, str) or len(item) != 64 or any(c not in '0123456789abcdef' for c in item)
                         for item in signature)):
                raise ValueError('Invalid control guard state')
            option_snapshots = value['option_snapshots']
            if (not isinstance(option_snapshots, list) or any(
                    not isinstance(pair, list) or len(pair) != 2 or any(
                        not isinstance(item, str) or len(item) != 64 or any(c not in '0123456789abcdef' for c in item)
                        for item in pair) for pair in option_snapshots)
                    or len({pair[0] for pair in option_snapshots}) != len(option_snapshots)):
                raise ValueError('Invalid control option snapshots')
            if self.environment.artifact_dir is not None:
                raise ValueError('Restore requires a dedicated environment without an active artifact directory')
            # Validate on a detached engine. The active wrapper is untouched if
            # core restore, pin checks, or guard consistency fail at any point.
            current = self.environment
            candidate = IncidentDayEnv(current.inputs, current.network.document,
                expected_inputs_sha256=current.inputs['sha256'],
                expected_network_sha256=current.network.sha256,
                allow_unapproved_data=current.inputs['approval_status'] != 'approved_synthetic_fixture')
            observation = candidate.restore(value['engine_checkpoint'])
            observed_key = _fingerprint(observation)
            if signature is not None and (observed_key is None or observed_key.hex() != signature[0]):
                raise ValueError('Control checkpoint public state does not match the guard')
            self.environment = candidate
            self._tools = IncidentDayTools(self.environment)
            self._last_failed_check = None if signature is None else tuple(bytes.fromhex(item) for item in signature)
            self._repetitions = value['repetitions']
            self._option_snapshots = {bytes.fromhex(key): bytes.fromhex(item) for key, item in option_snapshots}
            return observation

    def _briefing(self):
        situation = self.environment.get_situation()
        total = situation['report_count']
        reports, cursor = [], 0
        while cursor < total:
            page = self.environment.get_reports(after_cursor=cursor, limit=20)
            if not page:
                raise RuntimeError('Delivered report ledger is incomplete')
            reports.extend(page)
            cursor += len(page)
        if cursor != total:
            raise RuntimeError('Delivered report ledger changed during briefing')
        reconciled = reconcile_situation_v3(situation, reports)
        briefing = build_briefing_v3(reconciled)
        briefing['control_interface_version'] = self.version
        return briefing

    def finalize(self, *, expected_identity, expected_interface_sha256, infrastructure_timeout=False):
        """Trusted driver boundary: score only after pinned independent verification.

        This is intentionally not an actor-facing tool. The caller supplies
        predeclared trusted pins and the authoritative timeout state.
        """
        with self._lock:
            if type(infrastructure_timeout) is not bool:
                raise TypeError('infrastructure_timeout must be a boolean')
            if infrastructure_timeout:
                return verify_outcome(self.environment, expected_identity=expected_identity,
                                      infrastructure_timeout=True)
            if (not isinstance(expected_interface_sha256, str)
                    or expected_interface_sha256 != _LOADED_INTERFACE_SHA256
                    or not _loaded_sources_match()
                    or expected_interface_sha256 != interface_digest()):
                return {'verifier_version': VERIFIER_VERSION, 'status': 'identity_mismatch',
                        'verified': False, 'score_eligible': False, 'score': None}
            result = verify_outcome(self.environment, expected_identity=expected_identity,
                                    infrastructure_timeout=infrastructure_timeout)
            if not _loaded_sources_match() or expected_interface_sha256 != interface_digest():
                return {'verifier_version': VERIFIER_VERSION, 'status': 'identity_mismatch',
                        'verified': False, 'score_eligible': False, 'score': None}
            return result

    def execute(self, name, arguments):
        with self._lock:
            return self._execute(name, arguments)

    def _execute(self, name, arguments):
        if name == 'get_briefing':
            if arguments != {}:
                return {'error': 'get_briefing takes no arguments', 'committed': False}
            try:
                return self._briefing()
            except Exception:
                return {'error': 'The public briefing is unavailable; inspect the delivered situation and reports.',
                        'committed': False}
        if name == 'get_report_page':
            if not isinstance(arguments, dict) or set(arguments) - {'after_cursor', 'limit'}:
                return {'error': 'get_report_page accepts after_cursor and limit', 'committed': False}
            cursor, limit = arguments.get('after_cursor', 0), arguments.get('limit', 20)
            try:
                result = self._tools.execute('get_reports', {'after_cursor': cursor, 'limit': limit})
                if not isinstance(result, list):
                    return {'error': 'The report query failed; check the cursor and limit.', 'committed': False}
                total = self.environment.get_situation()['report_count']
            except Exception:
                return {'error': 'The report query failed; check the cursor and limit.', 'committed': False}
            next_cursor = cursor + len(result)
            return {'reports': result, 'total': total, 'next_cursor': next_cursor,
                    'has_more': next_cursor < total, 'control_interface_version': self.version}
        if name == 'advance':
            if not isinstance(arguments, dict) or set(arguments) - {'max_minutes'}:
                return {'error': 'advance accepts only optional max_minutes', 'committed': False}
            name = 'advance_time'
            arguments = {'max_minutes': arguments.get('max_minutes', 60)}
        if name == 'check_action' and isinstance(arguments, dict) and set(arguments) == {'action'}:
            try:
                situation_key = _fingerprint(self.environment.get_situation())
            except Exception:
                return {'valid': False, 'code': 'check_unavailable',
                        'error': 'The draft check is unavailable; inspect the public situation.'}
            # A fixed fallback still bounds pathological values whose repr fails.
            action_key = _fingerprint(arguments['action']) or hashlib.sha256(b'invalid-json').digest()
            signature = (situation_key, action_key) if situation_key else None
            if signature is not None and signature == self._last_failed_check and self._repetitions >= self.identical_check_limit:
                response = {'valid': False, 'error': 'This unchanged draft has already failed the check.',
                            'code': 'repeated_invalid_draft',
                            'next_step': 'Change the draft, obtain fresh options, or take another valid action.'}
                try:
                    response['previous_validation'] = explain_result_v3(
                        self.environment, arguments['action'], {'valid': False, 'error': None})
                except Exception:
                    # The guard must remain available without disclosing errors.
                    pass
                return response
            try:
                result = self._tools.execute(name, arguments)
                if isinstance(result, dict) and 'valid' in result:
                    result = explain_result_v3(self.environment, arguments['action'], result)
                else:
                    return {'valid': False, 'code': 'check_unavailable',
                            'error': 'The draft check is unavailable; inspect the public situation.'}
            except Exception:
                return {'valid': False, 'code': 'check_unavailable',
                        'error': 'The draft check is unavailable; inspect the public situation.'}
            if isinstance(result, dict) and result.get('valid') is False and signature is not None:
                if signature == self._last_failed_check:
                    self._repetitions += 1
                else:
                    self._last_failed_check, self._repetitions = signature, 1
            else:
                self._last_failed_check, self._repetitions = None, 0
            return copy.deepcopy(result)
        mutating = name in ('commit_orders', 'request_status', 'advance_time')
        if mutating:
            schemas = {item['function']['name']: item['function']['parameters'] for item in tools()}
            schema = schemas[name]
            if (not isinstance(arguments, dict) or set(arguments) - set(schema['properties'])
                    or set(schema['required']) - set(arguments)):
                return {'error': 'The tool arguments do not match the public contract.', 'committed': False}
        validation = None
        if name == 'commit_orders' and isinstance(arguments, dict) and set(arguments) == {'action'}:
            try:
                validation = check_action_v3(self.environment, arguments['action'])
            except Exception:
                return {'error': 'The order could not be checked; no order was submitted.', 'committed': False}
        try:
            result = self._tools.execute(name, arguments)
        except Exception:
            if mutating:
                self._last_failed_check, self._repetitions = None, 0
                return {'error': 'Execution status is unknown; inspect the current situation before retrying.',
                        'execution': 'indeterminate'}
            return {'error': 'The public query failed; inspect the tool inputs.', 'committed': False}
        if mutating and isinstance(result, dict) and result.get('committed') is False:
            # The v2 adapter catches exceptions even if the core already mutated.
            self._last_failed_check, self._repetitions = None, 0
            return {'error': 'Execution status is unknown; inspect the current situation before retrying.',
                    'execution': 'indeterminate'}
        if not mutating and isinstance(result, dict) and result.get('committed') is False:
            return {'error': 'The public query failed; inspect the tool inputs.', 'committed': False}
        if (validation and not validation['valid'] and isinstance(result, dict)
                and isinstance(result.get('info'), dict) and result['info'].get('error')):
            result['info']['validation'] = validation
            result['info']['error'] = validation['error']
        if (name in ('commit_orders', 'request_status', 'advance_time') and isinstance(result, dict)
                and isinstance(result.get('observation'), dict)):
            result['control_interface_version'] = self.version
            try:
                result['briefing'] = self._briefing()
            except Exception:
                # The order already ran; never disguise that fact as an action failure.
                result['briefing_error'] = 'Briefing unavailable; the full public observation is included.'
            if result.get('terminated') is True or result.get('truncated') is True:
                result['reward'] = None
                if isinstance(result.get('info'), dict):
                    result['info'].pop('outcomes', None)
                result['verification_required'] = True
        successful_query = (name in ('route_options', 'evacuation_options') and isinstance(result, dict)
                            and not result.get('error') and result.get('committed') is not False)
        changed_options = False
        if successful_query:
            query_key = _fingerprint([name, arguments])
            options_key = _fingerprint(result)
            if query_key is not None and options_key is not None:
                changed_options = self._option_snapshots.get(query_key) != options_key
                self._option_snapshots[query_key] = options_key
        progressed_action = (name in ('commit_orders', 'request_status', 'advance_time')
                             and isinstance(result, dict) and isinstance(result.get('observation'), dict))
        if changed_options or progressed_action:
            self._last_failed_check, self._repetitions = None, 0
        if name == 'get_situation' and isinstance(result, dict):
            result['control_interface_version'] = self.version
        return copy.deepcopy(result)
