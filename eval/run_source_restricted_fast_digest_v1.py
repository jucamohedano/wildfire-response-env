"""Opt-in private, unscored source driver in a fresh subprocess only.

This experimental launcher does not change the pinned host, source files, or
existing run identities. Only its own authenticated v2 runs may be resumed.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'wildfire-restricted-private-fast-digest-v2'
_PARENT_ENV = 'WILDFIRE_FAST_DIGEST_PARENT_V1'
_TOKEN_ENV = 'WILDFIRE_FAST_DIGEST_TOKEN_V1'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _source_path(module, relative):
    expected = ROOT / relative
    if not getattr(module, '__file__', None) or Path(module.__file__).resolve() != expected.resolve():
        raise RuntimeError('unexpected runtime module origin')
    return expected


def _function(function, module, name, path):
    if (not callable(function) or getattr(function, '__module__', None) != module
            or getattr(function, '__qualname__', None) != name
            or not getattr(function, '__code__', None)
            or Path(function.__code__.co_filename).resolve() != path.resolve()):
        raise RuntimeError('unexpected runtime function origin')


def _require_child():
    if (os.environ.get(_PARENT_ENV) != str(os.getppid())
            or not os.environ.get(_TOKEN_ENV) or os.getpid() == os.getppid()):
        raise RuntimeError('experimental bindings require the private subprocess')


@contextmanager
def _bindings(*, launcher_sha256, prototype_sha256, driver_sha256, adapter_sha256, containment_sha256):
    """Install only in a disposable worker; restore even on interruption."""
    _require_child()
    from envs.wildfire_response.incident_day import environment
    from wildfire_containment_v1 import incident_day as containment
    import adapter_fire_v1 as adapter
    from eval import incident_core_digest_fast_v1 as prototype
    from eval import run_source_restricted_private_v1 as driver

    paths = {
        'launcher': Path(__file__).resolve(),
        'prototype': _source_path(prototype, 'eval/incident_core_digest_fast_v1.py'),
        'driver': _source_path(driver, 'eval/run_source_restricted_private_v1.py'),
        'environment': _source_path(environment, 'envs/wildfire_response/core/src/envs/wildfire_response/incident_day/environment.py'),
        'adapter': _source_path(adapter, 'envs/wildfire_extended_inputs_v1/adapter_fire_v1.py'),
        'containment': _source_path(containment, 'envs/wildfire_containment_v1/src/wildfire_containment_v1/incident_day.py'),
    }
    if paths['launcher'] != (ROOT / 'eval/run_source_restricted_fast_digest_v1.py').resolve():
        raise RuntimeError('unexpected launcher origin')
    expected = dict(launcher=launcher_sha256, prototype=prototype_sha256, driver=driver_sha256,
                    adapter=adapter_sha256, containment=containment_sha256)
    if any(type(pin) is not str or len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin)
           for pin in expected.values()):
        raise ValueError('invalid independent source pin')
    if any(_sha(paths[name]) != pin for name, pin in expected.items()):
        raise RuntimeError('experimental source pin differs')
    original = environment.implementation_digest
    fast = prototype.implementation_digest
    paths_function = prototype._relative_paths
    _function(original, environment.__name__, 'implementation_digest', paths['environment'])
    _function(fast, prototype.__name__, 'implementation_digest', paths['prototype'])
    _function(paths_function, prototype.__name__, '_relative_paths', paths['prototype'])
    _function(adapter._source, adapter.__name__, '_source', paths['adapter'])
    _function(containment._check_runtime, containment.__name__, '_check_runtime', paths['containment'])
    _function(containment._source_digest, containment.__name__, '_source_digest', paths['containment'])
    _function(driver.run, driver.__name__, 'run', paths['driver'])
    if driver.VERSION != 'wildfire-restricted-private-source-driver-v1':
        raise RuntimeError('legacy driver version differs')
    if adapter.implementation_digest is not original or containment.implementation_digest is not original:
        raise RuntimeError('legacy digest bindings differ')
    # Also detect in-place code replacement and edits to the binding targets.
    functions = (original, fast, paths_function, adapter._source,
                 containment._check_runtime, containment._source_digest, driver.run)
    codes = tuple(f.__code__ for f in functions)
    pinned = {name: _sha(path) for name, path in paths.items() if name != 'environment'}
    if original() != containment.CORE or fast() != containment.CORE:
        raise RuntimeError('legacy and prototype digest parity differs')

    def checked_digest():
        if (adapter.implementation_digest is not checked_digest
                or containment.implementation_digest is not checked_digest
                or environment.implementation_digest is not original
                or prototype.implementation_digest is not fast or prototype._relative_paths is not paths_function
                or tuple(f.__code__ for f in functions) != codes
                or adapter._source is not functions[3] or containment._check_runtime is not functions[4]
                or containment._source_digest is not functions[5] or driver.run is not functions[6]
                or any(_sha(paths[name]) != pin for name, pin in pinned.items())):
            raise RuntimeError('experimental binding or source changed')
        return fast()

    try:
        adapter.implementation_digest = checked_digest
        containment.implementation_digest = checked_digest
        yield driver, expected
    finally:
        adapter.implementation_digest = original
        containment.implementation_digest = original


def _private_dir(path, *, resume):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
            or (not resume and any(path.iterdir()))):
        raise ValueError('a private user-owned directory is required; new runs must be empty')


def _private_read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ValueError('invalid private file')
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError('private file too large')
    return value


@contextmanager
def _run_lock(path, *, resume):
    flags = os.O_RDWR | os.O_NOFOLLOW | (0 if resume else os.O_CREAT | os.O_EXCL)
    fd = os.open(path / 'run.lock', flags, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ValueError('invalid run lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _seal_body(run_dir, pins):
    fd = os.open(run_dir / 'progress.json', os.O_RDONLY | os.O_NOFOLLOW)
    digest = hashlib.sha256()
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ValueError('invalid private progress file')
        total = 0
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > 400_000_000:
                raise ValueError('private progress file too large')
            digest.update(block)
    return json.dumps(dict(version=VERSION, pins=pins, progress_sha256=digest.hexdigest()),
                      sort_keys=True, separators=(',', ':')).encode()


def _authenticate(run_dir, pins):
    key = _private_read(run_dir / 'continuation.key', 32)
    if len(key) != 32:
        raise ValueError('invalid continuation key')
    seal = _private_read(run_dir / 'resume.seal', 4096)
    expected = hmac.digest(key, _seal_body(run_dir, pins), 'sha256')
    if len(seal) != 32 or not hmac.compare_digest(seal, expected):
        raise ValueError('experimental run authentication or identity differs')


def _write_seal(run_dir, pins):
    key = _private_read(run_dir / 'continuation.key', 32)
    if len(key) != 32:
        raise ValueError('invalid continuation key')
    seal = hmac.digest(key, _seal_body(run_dir, pins), 'sha256')
    fd, path = tempfile.mkstemp(prefix='.fast-seal-', dir=run_dir)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(seal)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, run_dir / 'resume.seal')
        directory_fd = os.open(run_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _worker(args):
    _require_child()
    # Keep the experimental worker below normal-priority live runs and on one
    # CPU, without changing any other process's affinity or priority.
    os.nice(10)
    if hasattr(os, 'sched_getaffinity') and hasattr(os, 'sched_setaffinity'):
        os.sched_setaffinity(0, {max(os.sched_getaffinity(0))})
    _private_dir(args.run_dir, resume=args.resume)
    with _bindings(launcher_sha256=args.launcher_sha256, prototype_sha256=args.prototype_sha256,
                   driver_sha256=args.driver_sha256, adapter_sha256=args.adapter_sha256,
                   containment_sha256=args.containment_sha256) as (driver, pins):
        pins.update(host=args.host_sha256, scorer=args.scorer_sha256)
        if _sha(driver.HOST) != pins['host'] or _sha(driver.SCORER) != pins['scorer']:
            raise RuntimeError('host or scorer source pin differs')
        with _run_lock(args.run_dir, resume=args.resume):
            if args.resume:
                _authenticate(args.run_dir, pins)
            # Bind all seven pins into both the run ID and continuation version.
            fingerprint = hashlib.sha256(json.dumps(pins, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            old_version = driver.VERSION
            try:
                driver.VERSION = VERSION + ':' + fingerprint
                run_args = argparse.Namespace(checkpoint=args.run_dir / 'progress.json',
                    key_file=args.run_dir / 'continuation.key', host_sha256=args.host_sha256,
                    scorer_sha256=args.scorer_sha256, approval=None, approval_sha256=None,
                    wall_seconds=args.wall_seconds, max_decisions=args.max_decisions, resume=args.resume)
                report = driver.run(run_args)
                from wildfire_containment_v1 import incident_day as containment
                if (containment.implementation_digest() != containment.CORE
                        or _sha(driver.HOST) != pins['host'] or _sha(driver.SCORER) != pins['scorer']):
                    raise RuntimeError('runtime source changed before sealing')
                _write_seal(args.run_dir, pins)
                counts = {key: sum(row['status'] == key for row in report['cases'])
                          for key in ('restricted_scored', 'censored', 'blocked', 'incomplete')}
                print(json.dumps(dict(version=VERSION, run_identity=fingerprint, mode='unscored_experimental',
                                      scope='restricted_no_ground_no_evac', counts=counts)))
            finally:
                driver.VERSION = old_version


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in-fast-digest', action='store_true')
    parser.add_argument('--run-dir', type=Path, required=True)
    for name in ('launcher', 'prototype', 'driver', 'adapter', 'containment', 'host', 'scorer'):
        parser.add_argument('--'+name+'-sha256', required=True)
    parser.add_argument('--wall-seconds', type=float, default=300)
    parser.add_argument('--max-decisions', type=int, default=1)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if (not args.opt_in_fast_digest or not math.isfinite(args.wall_seconds)
            or not 0 < args.wall_seconds <= 1800 or not 1 <= args.max_decisions <= 1440
            or not args.run_dir.is_absolute()):
        parser.error('explicit opt-in, absolute private directory and bounded limits required')
    for name in ('launcher', 'prototype', 'driver', 'adapter', 'containment', 'host', 'scorer'):
        pin = getattr(args, name+'_sha256')
        if len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin):
            parser.error('invalid SHA-256')
    if args._worker:
        try:
            _worker(args)
        except Exception:
            print('Experimental private worker failed; inspect its private run directory.', file=sys.stderr)
            return 1
        return 0
    _private_dir(args.run_dir, resume=args.resume)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PROJ_NETWORK='OFF',
               **{_PARENT_ENV: str(os.getpid()), _TOKEN_ENV: secrets.token_hex(32)})
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        env[key] = '1'
    paths = [ROOT, ROOT/'envs', ROOT/'envs/wildfire_extended_inputs_v1',
             ROOT/'envs/wildfire_response/core/src', ROOT/'envs/wildfire_containment_v1/src',
             ROOT/'envs/wildfire_containment_score_v1/src']
    env['PYTHONPATH'] = os.pathsep.join(map(str, paths))
    return subprocess.run([sys.executable, str(Path(__file__).resolve()),
                           *(sys.argv[1:] if argv is None else argv), '--_worker'],
                          cwd=ROOT, env=env, check=False).returncode


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
