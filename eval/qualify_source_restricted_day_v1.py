"""Bounded orchestration for restricted no-ground, no-evacuation exercises.

This is development infrastructure, not a source host or an authorization to
score one. The factory and verifier must independently enforce all source,
configuration, mask, replay and reward pins. IDs and run_id must be public,
non-sensitive labels. The checkpoint is trusted local state: protect it from
modification and use a new run_id whenever any input, policy or verifier changes.

The factory(case_id) returns a fresh scored host exposing situation() and the
shared-clock public commands below. policy_factory(case_id) returns a policy
whose act(public_host, observation) executes one bounded decision. The verifier
(case_id, host) independently replays and scores a completed host, returning the
restricted verifier's status/scope/reward_version/verified/score_eligible/score mapping.

One worker runs at a time. Budgets are cooperative between calls: an injected
factory, policy, host or verifier must itself bound blocking work. With an optional trusted state adapter, authenticated private host and policy
state is saved after each completed decision. Without one, unfinished cases
restart from handover. Exception text is never serialized.
"""
import base64
import json
import math
import os
import stat
from pathlib import Path
import tempfile
import time

from eval.qualify_extended_day_v1 import _ids

VERSION = 'wildfire-source-restricted-day-orchestration-v1'
SCOPE = 'restricted_no_ground_no_evac'
REWARD_VERSION = 'wildfire-restricted-no-ground-no-evac-outcomes-v1'
HORIZON = 1440
_COMMANDS = frozenset(('situation', 'reports', 'dispatch_hold', 'clear_aircraft',
                       'dispatch_aircraft', 'protect', 'release', 'request_status', 'advance'))


class BlockedCase(Exception):
    """A trusted integration cannot currently construct or run a case."""


class _StateError(Exception):
    pass


class _PublicHost:
    """Convenience boundary for trusted policies, not an in-process sandbox."""
    __slots__ = ('__host',)

    def __init__(self, host):
        self.__host = host

    def __getattr__(self, name):
        if name not in _COMMANDS:
            raise AttributeError(name)
        return getattr(self.__host, name)


def _observation(host):
    obs = host.situation()
    if (type(obs) is not dict or type(obs.get('minute')) is not int
            or not 0 <= obs['minute'] <= HORIZON
            or obs.get('status') not in ('active', 'censored', 'end_unscored')):
        raise ValueError('invalid public situation')
    return obs


def _empty(case_id):
    return {'id': case_id, 'status': 'incomplete', 'reason': 'not_started',
            'minute': None, 'score': None}


def _check_path(path, *, existing):
    """Require a user-owned private directory and a private regular file."""
    directory = path.parent.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid()
            or directory.st_mode & 0o077):
        raise ValueError('checkpoint directory must be private and user-owned')
    if existing:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ValueError('checkpoint must be a private user-owned regular file')


def _save(path, report, continuations):
    # Same-directory replacement prevents a partial checkpoint after interruption.
    fd, name = tempfile.mkstemp(prefix='.source-day-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            document = dict(report)
            if continuations:
                document['continuations'] = continuations
            json.dump(document, stream, allow_nan=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _load(path, ids, run_id):
    _check_path(path, existing=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding='utf-8') as stream:
        report = json.load(stream)
    continuations = report.pop('continuations', {}) if type(report) is dict else {}
    if (type(continuations) is not dict or any(type(k) is not str or type(v) is not str
            or len(v) > 32_000_000 for k, v in continuations.items())):
        raise ValueError('invalid private continuation')
    if (type(report) is not dict or set(report) != {'version', 'run_id', 'scope', 'cases'}
            or report['version'] != VERSION or report['run_id'] != run_id or report['scope'] != SCOPE
            or type(report['cases']) is not list or len(report['cases']) != 12):
        raise ValueError('checkpoint identity or format mismatch')
    for case_id, row in zip(ids, report['cases']):
        if (type(row) is not dict or set(row) != {'id', 'status', 'reason', 'minute', 'score'}
                or row['id'] != case_id or row['status'] not in ('incomplete', 'blocked', 'censored', 'restricted_scored')
                or row['reason'] not in ('not_started', 'running', 'budget', 'decision_limit',
                                         'interrupted', 'integration_blocked', 'integration_error',
                                         'verification_failed', 'boundary', 'verified')
                or (row['minute'] is not None and (type(row['minute']) is not int or not 0 <= row['minute'] <= HORIZON))
                or (row['status'] == 'restricted_scored' and (row['reason'] != 'verified' or row['minute'] != HORIZON
                    or type(row['score']) not in (int, float) or not math.isfinite(row['score']) or not 0 <= row['score'] <= 1))
                or (row['status'] != 'restricted_scored' and row['score'] is not None)):
            raise ValueError('invalid checkpoint case')
    if any(key not in ids or report['cases'][ids.index(key)]['status'] != 'incomplete'
           for key in continuations):
        raise ValueError('invalid private continuation')
    return report, continuations


def _capture(adapter, host, policy, run_id, case_id, obs):
    """Bind opaque adapter state to the run, case and public clock before sealing."""
    state = adapter.capture(host, policy)
    if type(state) is not bytes or len(state) > 16_000_000:
        raise ValueError('invalid private state')
    body = json.dumps({'version': VERSION, 'run_id': run_id, 'case_id': case_id,
                       'minute': obs['minute'], 'status': obs['status'],
                       'state': base64.b64encode(state).decode('ascii')},
                      separators=(',', ':')).encode()
    sealed = adapter.seal(body)
    if type(sealed) is not bytes or len(sealed) > 24_000_000:
        raise ValueError('invalid sealed state')
    return base64.b64encode(sealed).decode('ascii')


def _restore(adapter, envelope, run_id, case_id):
    try:
        sealed = base64.b64decode(envelope, validate=True)
        body = adapter.open(sealed)  # Must authenticate before returning plaintext.
        if type(body) is not bytes or len(body) > 24_000_000:
            raise ValueError('invalid authenticated state')
        data = json.loads(body)
        if (type(data) is not dict or set(data) != {'version', 'run_id', 'case_id', 'minute', 'status', 'state'}
                or data['version'] != VERSION or data['run_id'] != run_id or data['case_id'] != case_id
                or type(data['minute']) is not int or not 0 <= data['minute'] <= HORIZON
                or data['status'] not in ('active', 'censored', 'end_unscored')):
            raise ValueError('state binding differs')
        state = base64.b64decode(data['state'], validate=True)
        if len(state) > 16_000_000:
            raise ValueError('invalid private state')
        host, policy = adapter.restore(case_id, state)
        obs = _observation(host)
        if obs['minute'] != data['minute'] or obs['status'] != data['status']:
            raise ValueError('restored host differs')
        return host, policy, obs
    except Exception:
        raise ValueError('private continuation authentication or restore failed') from None


def qualify(case_ids, *, checkpoint, run_id, make_environment, make_policy,
            verify_outcome, wall_seconds=1800, max_decisions=3000, resume=False,
            now=time.monotonic, state_adapter=None):
    """Evaluate twelve cases; return a scope-labeled, sanitized private checkpoint report.

    Existing checkpoints are never overwritten unless resume=True and identity
    matches. Completed, censored, and blocked rows are retained on resume;
    incomplete rows resume from authenticated state when an adapter is supplied;
    otherwise they restart, so policies must be reproducible from handover.
    A restricted_scored row requires an uncensored terminal restricted day and the trusted independent
    verifier's affirmative full-day result. No aggregate score is inferred.
    """
    ids = _ids(case_ids)
    if type(run_id) is not str or not run_id:
        raise ValueError('public run_id required')
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('positive finite wall budget required')
    if type(max_decisions) is not int or max_decisions <= 0 or type(resume) is not bool:
        raise ValueError('invalid decision limit or resume flag')
    path = Path(checkpoint)
    _check_path(path, existing=resume)
    if state_adapter is not None and any(not callable(getattr(state_adapter, method, None))
                                         for method in ('capture', 'seal', 'open', 'restore')):
        raise ValueError('invalid state adapter')
    if resume:
        report, continuations = _load(path, ids, run_id)
        if continuations and state_adapter is None:
            raise ValueError('state adapter required for private continuation')
    else:
        report = {'version': VERSION, 'run_id': run_id, 'scope': SCOPE, 'cases': [_empty(i) for i in ids]}
        continuations = {}
        # Exclusive creation prevents accidentally replacing prior evidence.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
    start = now()
    current = None
    try:
        for index, row in enumerate(report['cases']):
            if row['status'] != 'incomplete':
                continue
            if now() - start >= wall_seconds:
                break
            current = index
            # Authentication must succeed before changing any persisted result.
            restored = (_restore(state_adapter, continuations[row['id']], run_id, row['id'])
                        if row['id'] in continuations else None)
            row.update(status='incomplete', reason='running', score=None)
            _save(path, report, continuations)
            try:
                if restored is None:
                    host = make_environment(row['id'])
                    obs = _observation(host)
                    row['minute'] = obs['minute']
                    if obs['minute'] != 0:
                        raise ValueError('factory did not return handover')
                    policy = make_policy(row['id']) if obs['status'] == 'active' else None
                else:
                    host, policy, obs = restored
                    row['minute'] = obs['minute']
                public = _PublicHost(host)
                if state_adapter is not None and restored is None:
                    try:
                        continuations[row['id']] = _capture(state_adapter, host, policy, run_id, row['id'], obs)
                        _save(path, report, continuations)
                    except Exception:
                        raise _StateError from None
                decisions = 0
                while obs['status'] == 'active':
                    if now() - start >= wall_seconds:
                        row['reason'] = 'budget'
                        break
                    if decisions >= max_decisions:
                        row['reason'] = 'decision_limit'
                        break
                    previous = obs['minute']
                    policy.act(public, obs)
                    decisions += 1
                    obs = _observation(host)
                    if obs['minute'] < previous:
                        raise ValueError('clock went backwards')
                    row['minute'] = obs['minute']
                    if state_adapter is not None:
                        try:
                            continuations[row['id']] = _capture(state_adapter, host, policy, run_id, row['id'], obs)
                            _save(path, report, continuations)
                        except Exception:
                            raise _StateError from None
                else:
                    if obs['status'] == 'censored':
                        row.update(status='censored', reason='boundary')
                    elif obs['minute'] != HORIZON:
                        row.update(status='blocked', reason='integration_error')
                    elif now() - start >= wall_seconds:
                        row['reason'] = 'budget'
                    else:
                        gate = verify_outcome(row['id'], host)
                        if now() - start >= wall_seconds:
                            row['reason'] = 'budget'
                        elif (type(gate) is dict and gate.get('status') == 'restricted_full_day'
                              and gate.get('scope') == SCOPE and gate.get('reward_version') == REWARD_VERSION
                              and gate.get('verified') is True and gate.get('score_eligible') is True
                              and type(gate.get('score')) in (int, float)
                              and math.isfinite(gate['score']) and 0 <= gate['score'] <= 1):
                            row.update(status='restricted_scored', reason='verified', score=gate['score'])
                        else:
                            row.update(status='blocked', reason='verification_failed')
            except _StateError:
                # Leave the last durable continuation untouched on adapter failure.
                raise ValueError('private continuation capture failed') from None
            except BlockedCase:
                row.update(status='blocked', reason='integration_blocked', score=None)
            except Exception:
                row.update(status='blocked', reason='integration_error', score=None)
            if row['status'] != 'incomplete':
                continuations.pop(row['id'], None)
            _save(path, report, continuations)
            current = None
            if row['reason'] == 'budget':
                break
    except KeyboardInterrupt:
        # Discard any in-flight changes and return to the last durable decision.
        report, continuations = _load(path, ids, run_id)
        if current is not None:
            report['cases'][current].update(status='incomplete', reason='interrupted', score=None)
        _save(path, report, continuations)
    return report
