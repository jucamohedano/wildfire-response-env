"""Trusted-only, single-episode recovery for deterministic local v3 sessions.

All session work must go through this driver, including policy reads and evaluator
updates. A private owner-only directory and a fresh unique run identity are
required. Sources and dependencies must be immutable from before import through
execution. The journal contains hidden state and is never an actor-facing API.

Selection, local control execution and evaluation must be deterministic and have
no external effects. A crash may physically repeat any of them. This does not
recover external agent conversation history, delivery acknowledgements, model
calls, billing, learner state, training jobs, or any other remote effect. The caller must coordinate those with
its own durable idempotency protocol; do not use a paid/external policy here.
SHA-256 detects accidental damage, not malicious edits or replacement with an
older valid journal. Anti-rollback across process restarts needs an independent
trusted monotonic anchor, which this layer does not provide.
"""
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from eval.incident_day_movement_checks import MovementObserver
from eval.incident_day_policies import make_policy
from eval.wait_recovery_v1 import _checkpoint_identity, _unique_keys

VERSION = 'session-recovery-v1'
_FILES = (Path(__file__), Path(__file__).with_name('incident_day_policies.py'),
          Path(__file__).with_name('incident_day_movement_checks.py'),
          Path(__file__).with_name('wait_recovery_v1.py'))

def _sources():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in _FILES}

_SOURCE = _sources()
_SELECTION_TOOLS = frozenset(('get_situation', 'get_reports', 'route_options',
                              'evacuation_options', 'check_action', 'get_briefing',
                              'get_report_page'))


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _copy(value):
    # Normalize tuples to JSON lists so memory and on-disk comparisons agree.
    return json.loads(_json(value))


def _private_file(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
        raise ValueError('Invalid private file')


class SessionRecoveryError(RuntimeError):
    """Private error; never expose exception objects or tracebacks to actors."""


class _Public:
    """Policy-only facade: all calls go through the v3 control and call ledger."""
    __slots__ = ('__session', '__selection')

    def __init__(self, session, *, selection=False):
        self.__session, self.__selection = session, selection

    def execute(self, name, arguments):
        s = self.__session
        if self.__selection and (not isinstance(name, str) or name not in _SELECTION_TOOLS):
            s._selection_violation = True
            raise ValueError('Tool not allowed during selection')
        env = s.control.environment
        before, decision = env.minute, env.decision
        result = s.control.execute(name, copy.deepcopy(arguments))
        retained = {k: copy.deepcopy(result[k]) for k in
                    ('info', 'reward', 'terminated', 'truncated', 'error', 'committed', 'valid')
                    if isinstance(result, dict) and k in result}
        s.calls.append(dict(name=name, arguments=copy.deepcopy(arguments), before_minute=before,
                            after_minute=env.minute, before_decision=decision,
                            after_decision=env.decision, result=retained))
        return result

    def get_situation(self):
        return self.execute('get_situation', {})

    def get_reports(self, **arguments):
        return self.execute('get_reports', arguments)

    def route_options(self, resource_id, sector_id='incident'):
        return self.execute('route_options', dict(resource_id=resource_id, sector_id=sector_id))

    def evacuation_options(self, group_id, destination_id):
        return self.execute('evacuation_options', dict(group_id=group_id, destination_id=destination_id))

    def check_action(self, action):
        return self.execute('check_action', {'action': action})


class SessionRecoveryV1:
    """Own a reset v3 control and a deterministic local checkpointable policy.

    initialize() durably saves the initial complete session. step(n) selects and
    saves an action before executing it, then atomically commits all post-state and
    the result; n starts at 1. recover() on an identically reset session resumes
    the selected action or restores the latest result. Repeating the latest step
    redelivers its result. Only the latest result is retained. No direct mutation
    of control, policy, calls, observer, or evaluation is permitted between calls.

    evaluation is private JSON state. The optional deterministic local evaluator
    mutates it after each result and must be identified by evaluator_identity,
    including its code/configuration version. It may not perform I/O. Evaluator
    exceptions and indeterminate control results fail closed and remain pending.
    The owner must supply the same evaluator and identity on restart. For a custom
    policy, policy_factory must be a deterministic, local, no-argument factory
    returning an object with action(public, observation), checkpoint(), and
    restore(state). Its checkpoint must be JSON and cover every decision-relevant
    bit of policy state. policy_identity must explicitly pin its implementation
    and configuration; the owner must keep them immutable. Built-in policies
    use policy_name and do not need a separate identity. These contracts cannot
    be enforced for arbitrary user code. Neither learner state nor training jobs
    are supported.
    """
    def __init__(self, control, private_dir, *, run_identity, policy_name,
                 evaluation=None, evaluator=None, evaluator_identity='none',
                 policy_factory=None, policy_identity=None):
        if not isinstance(run_identity, str) or not run_identity:
            raise ValueError('A unique run identity is required')
        if not isinstance(evaluator_identity, str) or not evaluator_identity:
            raise ValueError('An evaluator identity is required')
        if evaluator is None and evaluator_identity != 'none':
            raise ValueError('Evaluator missing')
        if evaluator is not None and (not callable(evaluator) or evaluator_identity == 'none'):
            raise ValueError('Versioned evaluator required')
        if not isinstance(policy_name, str) or not policy_name:
            raise ValueError('A policy name is required')
        if policy_factory is None:
            if policy_identity is not None:
                raise ValueError('Built-in policy identity is fixed')
            self._policy_factory = lambda: make_policy(policy_name)
            policy_identity = 'builtin:' + policy_name
        else:
            if not callable(policy_factory) or not isinstance(policy_identity, str) or not policy_identity:
                raise ValueError('Custom policy factory and identity required')
            self._policy_factory = policy_factory
        self.control, self.directory, self.identity = control, Path(private_dir), run_identity
        self.policy = self._new_policy()
        self.policy_name, self.policy_identity = policy_name, policy_identity
        self.evaluator, self.evaluator_identity = evaluator, evaluator_identity
        self.calls, self.evaluation = [], _copy({} if evaluation is None else evaluation)
        self.observation = _copy(control.environment.get_situation())
        self.observer = MovementObserver(control.environment)
        self._ready = None
        self._failure = None
        self._allowed = set()
        self._poisoned = False
        self._selection_violation = False
        self._initial = _digest(self._snapshot())

    def _new_policy(self):
        policy = self._policy_factory()
        if any(not callable(getattr(policy, name, None)) for name in ('action', 'checkpoint', 'restore')):
            raise ValueError('Invalid policy')
        return policy

    def _snapshot(self):
        return _copy(dict(control=self.control.private_checkpoint(), policy=self.policy.checkpoint(),
                          observation=self.observation, calls=self.calls,
                          observer=self.observer.snapshot(), evaluation=self.evaluation))

    def _remember_failure(self):
        # Non-JSON state cannot be compared safely with later mutations.
        # Never roll this instance back; a fresh reset can recover the journal.
        try:
            self._failure = _digest(self._snapshot())
        except Exception:
            self._failure = None
            self._poisoned = True

    def _restore(self, state):
        # Construct/validate the policy before changing the active control. The
        # control validates its own checkpoint on a detached engine before swap.
        policy = self._new_policy()
        policy.restore(copy.deepcopy(state['policy']))
        old_environment = self.control.environment
        old_control = self.control.private_checkpoint()
        old_policy, old_observer = self.policy, self.observer
        old_observation, old_calls, old_evaluation = self.observation, self.calls, self.evaluation
        try:
            self.control.restore_private_checkpoint(copy.deepcopy(state['control']))
            observer = MovementObserver(self.control.environment)
            observer.restore(copy.deepcopy(state['observer']))
            self.policy, self.observer = policy, observer
            self.observation = _copy(state['observation'])
            self.calls, self.evaluation = _copy(state['calls']), _copy(state['evaluation'])
            if self._snapshot() != state:
                raise ValueError('Snapshot differs after restore')
        except Exception:
            # Preserve the actual previous environment and its observer hooks.
            try:
                self.control.restore_private_checkpoint(old_control)
                self.control.environment = old_environment
                from wildfire_core.incident_day import IncidentDayTools
                self.control._tools = IncidentDayTools(old_environment)
                self.policy, self.observer = old_policy, old_observer
                self.observation, self.calls, self.evaluation = old_observation, old_calls, old_evaluation
            except Exception:
                self._poisoned = True
            raise

    @contextlib.contextmanager
    def _locked(self):
        directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError('Invalid private directory')
            lock = os.open('session-recovery-v1.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                           0o600, dir_fd=directory)
            try:
                _private_file(lock)
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    with self.control._lock:
                        if (self._poisoned or _sources() != _SOURCE
                                or self.control.environment.artifact_dir is not None):
                            raise ValueError('Invalid session')
                        os.fsync(directory)
                        yield directory
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
            finally:
                os.close(lock)
        finally:
            os.close(directory)

    def _write(self, directory, record):
        self._allowed.add(_digest(record))
        payload = _json({'record': record, 'sha256': _digest(record)})
        name = '.session-recovery-' + secrets.token_hex(16)
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, 'session-recovery-v1.json', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass

    def _read(self, directory):
        try:
            fd = os.open('session-recovery-v1.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            return None
        try:
            _private_file(fd)
            with os.fdopen(fd, 'rb') as f:
                fd = -1
                envelope = json.load(f, object_pairs_hook=_unique_keys,
                                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        finally:
            if fd != -1:
                os.close(fd)
        if not isinstance(envelope, dict) or set(envelope) != {'record', 'sha256'}:
            raise ValueError('Invalid journal')
        r = envelope['record']
        fields = {'version', 'sources', 'identity', 'policy', 'policy_identity', 'evaluator', 'initial',
                  'episode', 'sequence', 'phase', 'state', 'action', 'result'}
        if (not isinstance(r, dict) or set(r) != fields or _digest(r) != envelope['sha256']
                or r['version'] != VERSION or r['sources'] != _SOURCE or r['identity'] != self.identity
                or r['policy'] != self.policy_name or r['policy_identity'] != self.policy_identity
                or r['evaluator'] != self.evaluator_identity
                or r['initial'] != self._initial or type(r['sequence']) is not int or r['sequence'] < 0
                or r['phase'] not in ('selected', 'complete') or not isinstance(r['state'], dict)
                or set(r['state']) != {'control', 'policy', 'observation', 'calls', 'observer', 'evaluation'}
                or r['episode'] != _checkpoint_identity(self.control.private_checkpoint())
                or r['episode'] != _checkpoint_identity(r['state']['control'])
                or (r['phase'] == 'selected' and (r['sequence'] < 1 or not isinstance(r['action'], dict)
                                                   or r['result'] is not None))
                or (r['sequence'] == 0 and (r['phase'] != 'complete' or r['action'] is not None or r['result'] is not None))
                or (r['phase'] == 'complete' and r['sequence'] > 0
                    and (not isinstance(r['action'], dict) or not isinstance(r['result'], dict)))):
            raise ValueError('Journal identity or integrity differs')
        return r

    def initialize(self):
        try:
            with self._locked() as directory:
                if self._read(directory) is not None or self._ready is not None or _digest(self._snapshot()) != self._initial:
                    raise ValueError('Already initialized or changed')
                state = self._snapshot()
                r = dict(version=VERSION, sources=_SOURCE, identity=self.identity, policy=self.policy_name,
                         policy_identity=self.policy_identity, evaluator=self.evaluator_identity, initial=self._initial,
                         episode=_checkpoint_identity(state['control']), sequence=0, phase='complete',
                         state=state, action=None, result=None)
                self._write(directory, r)
                self._ready = 0
                return None
        except Exception:
            pass
        raise SessionRecoveryError('Session recovery failed')

    def _finish(self, directory, record):
        action = record['action']
        public = _Public(self)
        if set(action) == {'advance_minutes'}:
            result = public.execute('advance', {'max_minutes': action['advance_minutes']})
        elif set(action) == {'status_requests'} and len(action['status_requests']) == 1:
            result = public.execute('request_status', action['status_requests'][0])
        else:
            result = public.execute('commit_orders', {'action': action})
        if (not isinstance(result, dict) or not isinstance(result.get('observation'), dict)
                or result.get('execution') == 'indeterminate'):
            raise ValueError('Action did not finish')
        self.observation = _copy(result['observation'])
        if self.evaluator is not None:
            self.evaluator(self.evaluation, copy.deepcopy(action), copy.deepcopy(result))
        completed = {**record, 'phase': 'complete', 'state': self._snapshot(), 'result': _copy(result)}
        self._write(directory, completed)
        self._ready, self._failure = record['sequence'], None
        return copy.deepcopy(completed['result'])

    def _resume(self, directory, record):
        current = self._snapshot()
        if self._ready is None:
            if _digest(current) != self._initial:
                raise ValueError('Restart is not fresh')
        elif self._failure is not None:
            if _digest(record) not in self._allowed or _digest(current) != self._failure:
                raise ValueError('State changed after failure')
        elif self._ready != record['sequence'] or current != record['state']:
            raise ValueError('Session advanced or changed')
        if current != record['state']:
            self._restore(record['state'])
        if record['phase'] == 'selected':
            self._ready = record['sequence']
            self._allowed = {_digest(record)}
            try:
                return self._finish(directory, record)
            except Exception:
                self._remember_failure()
                self._allowed.add(_digest(record))
                raise
        self._ready, self._failure = record['sequence'], None
        return copy.deepcopy(record['result'])

    def recover(self):
        try:
            with self._locked() as directory:
                record = self._read(directory)
                if record is None:
                    raise ValueError('Missing journal')
                return self._resume(directory, record)
        except Exception:
            pass
        raise SessionRecoveryError('Session recovery failed')

    def step(self, sequence):
        try:
            if type(sequence) is not int or sequence < 1:
                raise ValueError('Invalid sequence')
            with self._locked() as directory:
                record = self._read(directory)
                if record is None or self._ready is None:
                    raise ValueError('Initialize or recover first')
                if sequence == record['sequence']:
                    return self._resume(directory, record)
                if (record['phase'] != 'complete' or sequence != record['sequence'] + 1
                        or self._ready != record['sequence'] or self._failure is not None
                        or self._snapshot() != record['state']):
                    raise ValueError('Session position differs')
                self._allowed = {_digest(record)}
                try:
                    self._selection_violation = False
                    action = _copy(self.policy.action(_Public(self, selection=True), copy.deepcopy(self.observation)))
                    if self._selection_violation or not isinstance(action, dict):
                        raise ValueError('Invalid selected action')
                    selected = {**record, 'sequence': sequence, 'phase': 'selected',
                                'state': self._snapshot(), 'action': action, 'result': None}
                    self._write(directory, selected)
                    self._allowed = {_digest(selected)}
                    return self._finish(directory, selected)
                except Exception:
                    self._remember_failure()
                    raise
        except Exception:
            pass
        raise SessionRecoveryError('Session recovery failed')
