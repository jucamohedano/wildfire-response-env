"""Private, control-only write-ahead recovery for v3 ``advance`` calls.

A journal belongs to one episode and a single trusted driver. It is not an actor
API or a matrix runner: policy, observer, and non-wait actions are not saved.
The owner must serialize those actions with this driver and resume the latest
journal before proceeding. Only the control checkpoint is saved: policy RNG and
state, observer state, and non-wait actions after the last journal write are not
recovered. The owner must durably coordinate those independently; this module
is not sufficient to resume a matrix run or external action/output delivery. A fresh private directory and unique run identity are
required per episode. Keep the journal and identity out of actor-facing output.

This protocol assumes a dedicated in-memory control with no external side effects
(and no artifact directory). A crash can cause physical re-execution, but only
from the saved pre-wait state. The saved post-state and public result are committed
atomically before delivery. Sequence numbers must be consecutive, starting at 1;
only the most recent result is retained. SHA-256 detects accidental corruption,
not malicious modification or rollback by someone who can write the journal.

This is a trusted-only API. Never expose or log its exception objects or
tracebacks outside the trusted boundary: traceback frames may retain private
state, and an exception may inherit an outer exception's context. Trusted code
should translate failures to a fixed, non-sensitive actor-facing response.
"""
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import secrets

VERSION = 'wait-recovery-v1'
_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class RecoveryError(RuntimeError):
    """A private recovery operation failed; details are intentionally withheld."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _private_file(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
        raise RecoveryError('Private journal storage is invalid')


class WaitRecoveryV1:
    """Journal one control's waits in an existing owner-only directory.

    ``run_identity`` is an immutable, nonempty trusted string uniquely naming
    the episode, not an actor credential. ``start`` creates sequence 1 only
    with ``initialize=True``. Thereafter use consecutive sequences; repeat the
    latest sequence with identical arguments to redeliver its result. ``recover``
    restores the most recent transaction, executing it if still pending. After
    restart call recover on a freshly reset dedicated control before any actions.
    Completed retries require the exact post-wait state and current journal
    position; later in-memory actions are never silently rolled back.
    Exception messages are generic. Exception objects and tracebacks are
    private and must never be exposed or logged outside the trusted boundary.
    """

    def __init__(self, control, private_dir, *, run_identity):
        if not isinstance(run_identity, str) or not run_identity:
            raise ValueError('A nonempty run identity is required')
        self.control = control
        self.directory = Path(private_dir)
        self.identity = run_identity
        self._ready_sequence = None
        self._pending_sequence = None
        self._pending_failure_state = None

    @contextlib.contextmanager
    def _locked(self):
        # Directory and file descriptors avoid symlink following at the leaves.
        directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise RecoveryError('Private journal storage is invalid')
            lock = os.open('wait-recovery-v1.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                           0o600, dir_fd=directory)
            try:
                _private_file(lock)
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != _SOURCE_SHA256:
                        raise RecoveryError('Recovery implementation changed')
                    if self.control.environment.artifact_dir is not None:
                        raise RecoveryError('Recovery requires a dedicated in-memory control')
                    with self.control._lock:
                        # A prior rename may have succeeded while its directory
                        # fsync failed. Do not rely on any visible journal until
                        # that rename is known durable.
                        os.fsync(directory)
                        yield directory
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
            finally:
                os.close(lock)
        finally:
            os.close(directory)

    def _read(self, directory):
        try:
            fd = os.open('wait-recovery-v1.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            return None
        try:
            _private_file(fd)
            with os.fdopen(fd, 'rb') as file:
                fd = -1
                value = json.load(file, object_pairs_hook=_unique_keys,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        finally:
            if fd != -1:
                os.close(fd)
        if not isinstance(value, dict) or set(value) != {'record', 'sha256'}:
            raise RecoveryError('Invalid recovery journal')
        record = value['record']
        if not isinstance(record, dict) or value['sha256'] != _digest(record):
            raise RecoveryError('Invalid recovery journal')
        fields = {'version', 'driver_sha256', 'run_identity', 'sequence', 'arguments', 'phase', 'checkpoint'}
        if record.get('phase') == 'complete':
            fields.add('result')
        if (set(record) != fields or record.get('version') != VERSION
                or record.get('driver_sha256') != _SOURCE_SHA256
                or record.get('run_identity') != self.identity
                or type(record.get('sequence')) is not int or record['sequence'] < 1
                or record.get('phase') not in ('pending', 'complete')
                or not isinstance(record.get('checkpoint'), dict)):
            raise RecoveryError('Recovery identity or journal differs')
        self._validate_arguments(record['arguments'])
        if _checkpoint_identity(self.control.private_checkpoint()) != _checkpoint_identity(record['checkpoint']):
            raise RecoveryError('Recovery identity differs')
        if record['phase'] == 'complete' and not isinstance(record['result'], dict):
            raise RecoveryError('Invalid recovery journal')
        return record

    @staticmethod
    def _validate_arguments(arguments):
        if (not isinstance(arguments, dict) or set(arguments) != {'max_minutes'}
                or type(arguments['max_minutes']) is not int
                or not 1 <= arguments['max_minutes'] <= 60):
            raise RecoveryError('Invalid wait command')

    def _write(self, directory, record):
        payload = _json({'record': record, 'sha256': _digest(record)})
        # Named temp files are private and in the same directory for atomic rename.
        name = '.wait-recovery-' + secrets.token_hex(16)
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, 'wait-recovery-v1.json', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass

    def _finish(self, directory, record):
        try:
            # Always discard any partially-mutated in-memory state before executing.
            self.control.restore_private_checkpoint(record['checkpoint'])
            result = self.control.execute('advance', copy.deepcopy(record['arguments']))
            if (not isinstance(result, dict) or not isinstance(result.get('observation'), dict)
                    or result.get('execution') == 'indeterminate'):
                raise RecoveryError('Wait execution could not be committed')
            complete = {**record, 'phase': 'complete',
                        'checkpoint': self.control.private_checkpoint(), 'result': result}
            self._write(directory, complete)
            self._pending_failure_state = None
            return copy.deepcopy(result)
        except Exception:
            # Permit rollback only while the control is exactly as it was when
            # the failed wait returned; later actions must never be discarded.
            try:
                self._pending_failure_state = _digest(self.control.private_checkpoint())
            except Exception:
                self._pending_failure_state = None
            raise

    def start(self, sequence, max_minutes=60, *, initialize=False):
        """Persist a wait before execution, or redeliver the latest wait."""
        try:
            args = {'max_minutes': max_minutes}
            self._validate_arguments(args)
            if type(sequence) is not int or sequence < 1 or type(initialize) is not bool:
                raise RecoveryError('Invalid wait command')
            with self._locked() as directory:
                old = self._read(directory)
                if old is None:
                    if sequence != 1 or not initialize:
                        raise RecoveryError('Missing recovery journal')
                elif initialize:
                    raise RecoveryError('Recovery journal already exists')
                elif sequence == old['sequence']:
                    if args != old['arguments']:
                        raise RecoveryError('Wait command differs')
                    if old['phase'] == 'complete':
                        if (self._ready_sequence != sequence
                                or self.control.private_checkpoint() != old['checkpoint']):
                            raise RecoveryError('Current state is not the completed wait state')
                        return copy.deepcopy(old['result'])
                    return self._resume(directory, old)
                elif (sequence != old['sequence'] + 1 or old['phase'] != 'complete'
                      or self._ready_sequence != old['sequence']):
                    raise RecoveryError('Wait sequence differs')
                record = {'version': VERSION, 'driver_sha256': _SOURCE_SHA256,
                          'run_identity': self.identity, 'sequence': sequence,
                          'arguments': args, 'phase': 'pending',
                          'checkpoint': self.control.private_checkpoint()}
                self._write(directory, record)
                self._pending_sequence = sequence
                result = self._finish(directory, record)
                self._pending_sequence = None
                self._ready_sequence = sequence
                return result
        except Exception:
            pass
        # Avoid chaining the caught failure. An outer exception context and
        # traceback frames can still contain private information.
        raise RecoveryError('Wait recovery failed')

    def _resume(self, directory, record):
        current = self.control.private_checkpoint()
        interrupted_here = (record['phase'] == 'pending'
                            and self._pending_sequence == record['sequence'])
        if current != record['checkpoint']:
            if interrupted_here:
                if (self._pending_failure_state is None
                        or _digest(current) != self._pending_failure_state):
                    raise RecoveryError('Current state changed after the interrupted wait')
            elif self._ready_sequence is not None or not self._is_fresh_reset(current):
                raise RecoveryError('Current state cannot be replaced by recovery')
        if record['phase'] == 'pending':
            self._pending_sequence = record['sequence']
            result = self._finish(directory, record)
            self._pending_sequence = None
            self._pending_failure_state = None
        else:
            if current != record['checkpoint']:
                self.control.restore_private_checkpoint(record['checkpoint'])
            result = copy.deepcopy(record['result'])
        self._ready_sequence = record['sequence']
        return result

    def _is_fresh_reset(self, checkpoint):
        # A new wrapper may contain later actor actions. Reconstruct the reset
        # state independently so recovery never silently overwrites those actions.
        from wildfire_control_v3.control import ControlCenterV3
        environment = self.control.environment
        scenario = environment._scenario
        fresh = type(environment)(environment.inputs, environment.network.document,
            expected_inputs_sha256=environment.inputs['sha256'],
            expected_network_sha256=environment.network.sha256,
            allow_unapproved_data=environment.inputs['approval_status'] != 'approved_synthetic_fixture')
        fresh.reset(scenario['id'], scenario['seed'])
        wrapper = ControlCenterV3(fresh, identical_check_limit=self.control.identical_check_limit)
        return wrapper.private_checkpoint() == checkpoint

    def recover(self):
        """Restore the latest wait and return its original actor-visible result."""
        try:
            with self._locked() as directory:
                record = self._read(directory)
                if record is None:
                    raise RecoveryError('Missing recovery journal')
                return self._resume(directory, record)
        except Exception:
            pass
        # Avoid chaining the caught failure. An outer exception context and
        # traceback frames can still contain private information.
        raise RecoveryError('Wait recovery failed')


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate key')
        result[key] = value
    return result


def _checkpoint_identity(checkpoint):
    """Pin the episode as well as the engine/interface identities on restore."""
    engine = checkpoint['engine_checkpoint']
    population = 'core' in engine
    core = engine['core'] if population else engine
    return _digest({
        'interface': checkpoint['version'],
        'interface_sha256': checkpoint['interface_sha256'],
        'identical_check_limit': checkpoint['identical_check_limit'],
        'population_version': engine['version'] if population else None,
        'population_sha256': engine['implementation_sha256'] if population else None,
        'core_version': core['version'],
        'core_sha256': core['implementation_sha256'],
        'inputs_sha256': core['inputs_sha256'],
        'network_sha256': core['network_sha256'],
        'scenario': core['state']['_scenario'],
    })
