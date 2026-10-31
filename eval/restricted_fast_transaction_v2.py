"""Atomic authenticated snapshots of private restricted-run progress."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import tempfile

MAGIC = b'wildfire-fast-transaction-v2\n'
MAX_PROGRESS = 400_000_000


def _private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        os.close(fd)
        raise ValueError('invalid private file')
    return os.fdopen(fd, 'rb')


def _key(directory):
    with _private(directory/'continuation.key') as stream:
        key = stream.read(33)
    if len(key) != 32:
        raise ValueError('invalid continuation key')
    return key


def _directory_sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(directory, pins, version, source):
    header = json.dumps(dict(version=version, pins=pins), sort_keys=True, separators=(',', ':')).encode()+b'\n'
    if len(header) > 16384:
        raise ValueError('transaction header too large')
    mac = hmac.new(_key(directory), digestmod='sha256');mac.update(header)
    fd, temporary = tempfile.mkstemp(prefix='.fast-transaction-', dir=directory)
    try:
        with os.fdopen(fd, 'w+b') as output:
            output.write(MAGIC+b'\0'*32+header)
            total = 0
            while block := source.read(1024*1024):
                total += len(block)
                if total > MAX_PROGRESS:
                    raise ValueError('progress too large')
                mac.update(block);output.write(block)
            output.seek(len(MAGIC));output.write(mac.digest())
            output.flush();os.fsync(output.fileno())
        os.replace(temporary, directory/'checkpoint.auth')
        _directory_sync(directory)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def commit(directory, pins, version):
    """Publish a complete signed snapshot in one atomic replacement."""
    directory = Path(directory)
    with _private(directory/'progress.json') as source:
        _publish(directory, pins, version, source)


def initialize(directory, pins, version, progress):
    """Commit the authenticated handover report before creating progress.json."""
    import io
    directory = Path(directory)
    _publish(directory, pins, version, io.BytesIO(progress))
    restore(directory, pins, version)


def restore(directory, pins, version):
    """Authenticate before atomically restoring the last committed progress."""
    directory = Path(directory)
    key = _key(directory)
    with _private(directory/'checkpoint.auth') as source:
        if source.read(len(MAGIC)) != MAGIC:
            raise ValueError('invalid transaction version')
        expected = source.read(32)
        header = source.readline(16385)
        if len(expected) != 32 or len(header) > 16384 or not header.endswith(b'\n'):
            raise ValueError('invalid transaction header')
        if json.loads(header) != dict(version=version, pins=pins):
            raise ValueError('transaction identity differs')
        mac = hmac.new(key, digestmod='sha256');mac.update(header)
        fd, temporary = tempfile.mkstemp(prefix='.fast-progress-', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as output:
                total = 0
                while block := source.read(1024*1024):
                    total += len(block)
                    if total > MAX_PROGRESS:
                        raise ValueError('progress too large')
                    mac.update(block);output.write(block)
                if not hmac.compare_digest(expected, mac.digest()):
                    raise ValueError('transaction authentication failed')
                output.flush();os.fsync(output.fileno())
            os.replace(temporary, directory/'progress.json')
            _directory_sync(directory)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)


def saved(path, report, continuations, *, original, guard, pins, version):
    """Bridge the legacy atomic writer to the authenticated commit point."""
    original(path, report, continuations)
    guard()
    commit(Path(path).parent, pins, version)
