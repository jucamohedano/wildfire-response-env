"""Trusted, process-scoped catalog dispatch for the frozen incident-day engines.

The caller must keep this context active for reset, replay, and checkpoint recovery,
use only its owning thread, and isolate the process from untrusted code. This is
not a sandbox or source attestation. Pin this module and the catalog separately
from the frozen engine and v3 identities. Scenario fields consumed by the engine
(including handover notices) may be public; catalog metadata is never injected.

For recovery use ``bridge.restore_control(control, checkpoint)`` rather than
calling the control's restore directly: core checks checkpoint integrity, but
not whether its embedded scenario belongs to the externally pinned catalog.
"""
import copy
from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import re
import threading

from envs.wildfire_response.incident_day import environment as _environment
from envs.wildfire_response.incident_day.data import _check_hash
from envs.wildfire_response.incident_day.scenarios import ALL_CASE_IDS, build_scenario as _original_builder
from envs.wildfire_response.regional.ops.generator import digest

VERSION = 'wildfire-incident-eval-bridge-v1'
CATALOG_VERSION = 'wildfire-incident-eval-catalog-v1'
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
# The built-in day suites use these pinned seeds, including their descendants.
_LEGACY_SEEDS = frozenset(range(2844000, 2844006)) | frozenset(range(3866400, 3866406))
_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_lock = threading.RLock()
_active = None


def _source_check():
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != _SOURCE_SHA256:
        raise RuntimeError('Bridge source changed; restart the process')


def _json_value(value):
    """Reject non-JSON types, non-string keys and non-finite numbers recursively."""
    if value is None or type(value) in (bool, int, str):
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
    raise ValueError('Catalog must contain finite JSON values with string keys')


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _nonempty(value):
    return type(value) is str and bool(value.strip())


def _wind(value):
    return (type(value) is list and len(value) == 2 and all(
        type(x) in (int, float) and math.isfinite(x) and abs(x) <= 3 for x in value))


def _indexed(items):
    if type(items) is not list or not items or any(
            type(item) is not dict or not _nonempty(item.get('id')) for item in items):
        raise ValueError('Invalid scenario collection')
    ids = [item['id'] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate scenario collection identifier')
    return set(ids)


def _validate_case(case):
    if type(case) is not dict:
        raise ValueError('Invalid compiled scenario')
    if (not _nonempty(case.get('version')) or type(case.get('id')) is not str
            or not _ID.fullmatch(case['id']) or case['id'] in ALL_CASE_IDS
            or type(case.get('seed')) is not int or not 0 <= case['seed'] < 2**63
            or case['seed'] in _LEGACY_SEEDS
            or not _hash(case.get('sha256'))
            or digest({k: v for k, v in case.items() if k != 'sha256'}) != case['sha256']):
        raise ValueError('Invalid scenario identity, seed or hash')
    if (type(case.get('horizon_minutes')) is not int or case['horizon_minutes'] != 1440
            or type(case.get('warmup_minutes')) is not int or not 0 <= case['warmup_minutes'] <= 1440
            or not _wind(case.get('wind')) or not _nonempty(case.get('family'))
            or type(case.get('profile')) is not dict or not _nonempty(case['profile'].get('version'))):
        raise ValueError('Invalid scenario configuration')
    try:
        start = datetime.fromisoformat(case['start_utc'])
        if start.utcoffset() is None or not _nonempty(case.get('timezone')):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('Invalid scenario start time') from None
    fire = case.get('initial_fire')
    if (type(fire) is not list or not fire or any(type(p) is not list or len(p) != 2
            or any(type(v) is not int or v < 0 for v in p) for p in fire)
            or len({tuple(p) for p in fire}) != len(fire)):
        raise ValueError('Invalid initial fire')
    resources = _indexed(case.get('resources'))
    destinations = _indexed(case.get('destinations'))
    _indexed(case.get('groups'))
    centers = case.get('centers')
    if (type(centers) is not list or not centers or any(not _nonempty(c) for c in centers)
            or len(set(centers)) != len(centers) or 'engine-0' not in resources):
        raise ValueError('Invalid centers or missing engine-0')
    if any(unit.get('kind') not in ('engine', 'crew', 'aircraft')
           or unit.get('home_base') not in centers for unit in case['resources']):
        raise ValueError('Invalid resource kind or center')
    if any(group.get('destination_id') is not None and group['destination_id'] not in destinations
           for group in case['groups']):
        raise ValueError('Unknown initial destination')
    events = case.get('events')
    kinds = {'wind', 'access_restriction', 'aircraft_unavailable', 'destination_closed', 'center_unavailable'}
    if type(events) is not list:
        raise ValueError('Invalid scenario events')
    previous = -1
    for event in events:
        if (type(event) is not dict or event.get('kind') not in kinds
                or type(event.get('minute')) is not int or not 1 <= event['minute'] <= 1440
                or event['minute'] < previous):
            raise ValueError('Invalid or unsorted scenario event')
        previous = event['minute']
        kind = event['kind']
        if ((kind == 'wind' and not _wind(event.get('wind')))
                or (kind == 'access_restriction' and event.get('resource_kind') not in ('engine', 'crew', 'aircraft'))
                or (kind == 'destination_closed' and event.get('destination_id') not in destinations)
                or (kind == 'center_unavailable' and event.get('center_id') not in centers)
                or (kind in ('aircraft_unavailable', 'center_unavailable') and
                    (type(event.get('until')) is not int or not event['minute'] < event['until'] <= 1440))):
            raise ValueError('Invalid scenario event arguments')


def _catalog_copy(catalog):
    _json_value(catalog)
    if (type(catalog) is not dict or set(catalog) !=
            {'version', 'inputs_sha256', 'network_sha256', 'cases', 'sha256'}
            or catalog['version'] != CATALOG_VERSION
            or not all(_hash(catalog[k]) for k in ('inputs_sha256', 'network_sha256', 'sha256'))
            or type(catalog['cases']) is not list or not catalog['cases']
            or digest({k: v for k, v in catalog.items() if k != 'sha256'}) != catalog['sha256']):
        raise ValueError('Invalid catalog identity or hash')
    value = copy.deepcopy(catalog)
    for case in value['cases']:
        _validate_case(case)
        context = case.get('source_context')
        if context is not None and (type(context) is not dict
                or ('pack_sha256' in context and context['pack_sha256'] != value['inputs_sha256'])
                or ('network_sha256' in context and context['network_sha256'] != value['network_sha256'])):
            raise ValueError('Scenario source context differs from catalog pins')
    ids = [case['id'] for case in value['cases']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate catalog case ID')
    seeds = [case['seed'] for case in value['cases']]
    if len(seeds) != len(set(seeds)):
        raise ValueError('Duplicate catalog case seed')
    return value


class IncidentEvalBridgeV1:
    """Context manager for one immutable catalog in one process and thread.

    The caller holds the trusted expected catalog pin independently; compare it
    to ``pins`` before running. Nested contexts with the same catalog and owner
    are allowed. Other threads, different catalogs and inherited fork contexts
    fail closed. Contexts must exit in LIFO order.
    """
    def __init__(self, catalog):
        self._catalog = _catalog_copy(catalog)
        self._entered = False
        self._state = None

    @property
    def pins(self):
        _source_check()
        return {'bridge_version': VERSION, 'bridge_source_sha256': _SOURCE_SHA256,
                'catalog_version': CATALOG_VERSION, 'catalog_sha256': self._catalog['sha256'],
                'inputs_sha256': self._catalog['inputs_sha256'],
                'network_sha256': self._catalog['network_sha256']}

    def __enter__(self):
        global _active
        with _lock:
            _source_check()
            if self._entered:
                raise RuntimeError('Bridge context already entered')
            owner = (os.getpid(), threading.get_ident())
            if _active is not None:
                if (_active['owner'] != owner or _active['catalog'] != self._catalog
                        or _environment.build_scenario is not _active['dispatcher']):
                    raise RuntimeError('A different or concurrent bridge is active')
                state = _active
            else:
                if _environment.build_scenario is not _original_builder:
                    raise RuntimeError('Scenario builder is already modified')
                state = {'owner': owner, 'catalog': copy.deepcopy(self._catalog), 'stack': []}
                cases = {case['id']: case for case in state['catalog']['cases']}

                def dispatch(inputs, network, identifier, seed=None):
                    _source_check()
                    if ((os.getpid(), threading.get_ident()) != state['owner']
                            or _active is not state or _environment.build_scenario is not dispatch):
                        raise RuntimeError('Bridge is not active on its owning thread and process')
                    if identifier not in cases:
                        return _original_builder(inputs, network, identifier, seed)
                    case = cases[identifier]
                    if (seed is not None and (type(seed) is not int or seed != case['seed'])):
                        raise ValueError('Scenario seed differs from catalog pin')
                    if (_check_hash(inputs, 'bridge input') != state['catalog']['inputs_sha256']
                            or _check_hash(network.document, 'bridge network') != state['catalog']['network_sha256']
                            or network.sha256 != state['catalog']['network_sha256']
                            or inputs.get('network_sha256') != network.sha256):
                        raise ValueError('Scenario inputs or network differ from catalog pins')
                    width, height = inputs['grid']['width'], inputs['grid']['height']
                    if any(not (1 <= x < width - 1 and 1 <= y < height - 1)
                           for x, y in case['initial_fire']):
                        raise ValueError('Scenario ignition is outside the interior grid')
                    return copy.deepcopy(case)

                state['dispatcher'] = dispatch
                _active = state
                _environment.build_scenario = dispatch
            state['stack'].append(self)
            self._state, self._entered = state, True
            return self

    def _check_active(self):
        _source_check()
        if (not self._entered or _active is not self._state
                or self._state['owner'] != (os.getpid(), threading.get_ident())
                or _environment.build_scenario is not self._state['dispatcher']):
            raise RuntimeError('Bridge is not active on its owning thread and process')

    def validate_checkpoint(self, checkpoint):
        """Check the embedded compiled scenario against this trusted catalog.

        This is an additional authorization check, not a replacement for the
        control's own checkpoint integrity and interface/engine validation.
        """
        self._check_active()
        try:
            engine = checkpoint['engine_checkpoint']
            core = engine['core'] if engine.get('version') == 'wildfire-incident-day-population-v3' else engine
            scenario = core['state']['_scenario']
            case = next(case for case in self._state['catalog']['cases'] if case['id'] == scenario['id'])
            if (scenario != case or core['inputs_sha256'] != self._catalog['inputs_sha256']
                    or core['network_sha256'] != self._catalog['network_sha256']):
                raise ValueError
        except (KeyError, TypeError, StopIteration, ValueError):
            raise ValueError('Checkpoint does not match the pinned catalog') from None

    def restore_control(self, control, checkpoint):
        self.validate_checkpoint(checkpoint)
        return control.restore_private_checkpoint(checkpoint)

    def __exit__(self, exc_type, exc, traceback):
        global _active
        with _lock:
            if (not self._entered or _active is not self._state
                    or self._state['owner'] != (os.getpid(), threading.get_ident())):
                raise RuntimeError('Bridge is not active on its owning thread and process')
            if not self._state['stack'] or self._state['stack'][-1] is not self:
                raise RuntimeError('Bridge contexts must exit in LIFO order')
            valid = _environment.build_scenario is self._state['dispatcher']
            try:
                _source_check()
            except (OSError, RuntimeError):
                valid = False
            self._state['stack'].pop()
            self._entered = False
            if not self._state['stack'] or not valid:
                _environment.build_scenario = _original_builder
                _active = None
            self._state = None
            if not valid:
                raise RuntimeError('Bridge source or scenario dispatcher changed')
        return False
