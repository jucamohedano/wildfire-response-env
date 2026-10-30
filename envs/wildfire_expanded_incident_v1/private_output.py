"""Fail-closed private diagnostic output destinations."""
import os
from pathlib import Path
import stat
import subprocess


def _private_location(value):
    path = Path(value)
    if not path.is_absolute() or path.name in ('', '.', '..'):
        raise ValueError('output must be an absolute path in an explicit private directory')
    parent = path.parent
    try:
        resolved = parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError('private output directory must already exist') from exc
    if resolved != parent or not resolved.is_dir():
        raise ValueError('output directory and ancestors must not use symlinks')
    info = resolved.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('output directory must be owned by the caller and mode 0700')
    for directory in (resolved, *resolved.parents):
        marker = directory / '.git'
        # Some sandbox mounts expose an empty read-only .git placeholder.
        # Real worktrees have a gitdir file or a repository directory; do not
        # let an empty placeholder prevent private outputs elsewhere in /tmp.
        if (marker.is_symlink() or marker.is_file()
                or (marker.is_dir() and ((marker / 'HEAD').exists() or (marker / 'config').exists()))):
            raise ValueError('private output cannot be inside any git worktree')
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env['GIT_CONFIG_NOSYSTEM'] = '1'
    try:
        probe = subprocess.run(['git', '-C', str(resolved), 'rev-parse', '--absolute-git-dir'],
                               env=env, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('cannot verify private directory is outside git') from exc
    if probe.returncode == 0:
        raise ValueError('private output cannot be inside any git repository')
    if 'not a git repository' not in probe.stderr.lower():
        raise ValueError('cannot establish that private output is outside git')
    return path


def private_destination(value):
    path = _private_location(value)
    if path.exists() or path.is_symlink():
        raise ValueError('output must be a fresh private file')
    return path


def read_private_json(value, *, max_bytes=1_000_000):
    """Read a small private JSON file outside Git without following links."""
    import json
    import secrets
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('private input must be an absolute path')
    private_destination(path.parent / ('.input-check-' + secrets.token_hex(16)))
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError('private input cannot be opened safely') from exc
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size > max_bytes):
            raise ValueError('invalid private input')
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError('private input exceeds size limit')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate private JSON key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid private JSON number')))


def write_private_json(path, value):
    import json
    path = private_destination(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def write_atomic_private_json(path, value, expected_identity=None):
    """Atomically create or replace only this driver's own private checkpoint.

    Returns the target's (device, inode) for the next guarded replacement.
    A fresh target is installed without overwriting an existing file.
    """
    import json
    import tempfile
    path = _private_location(path)

    def check_target():
        try:
            info = path.lstat()
        except FileNotFoundError:
            if expected_identity is not None:
                raise ValueError('private checkpoint disappeared')
            return
        if (expected_identity is None or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1 or (info.st_dev, info.st_ino) != expected_identity):
            raise ValueError('private checkpoint identity changed')

    check_target()
    fd, name = tempfile.mkstemp(prefix='.checkpoint-', suffix='.tmp', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, separators=(',', ':'), allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        check_target()
        if expected_identity is None:
            # link() fails if a competing file appears; it cannot overwrite.
            os.link(temp, path, follow_symlinks=False)
            temp.unlink()
        else:
            os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        info = path.lstat()
        return info.st_dev, info.st_ino
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
