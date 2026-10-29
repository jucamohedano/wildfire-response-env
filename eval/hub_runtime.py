"""Verify a downloaded runtime package and run a bounded development episode."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys
import time

from wildfire_core.catalog_lifecycle import active_day_case_ids, registry
from wildfire_core.incident_day import IncidentDayEnv
from envs.wildfire_response.incident_day.baselines import DayDispatcher
from envs.wildfire_response.incident_day.environment import implementation_digest

VERSION = 'wildfire-response-spain-runtime-v1'
RELEASE_VERSION = '0.2.0'
MAX_FILES = 10000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_HASH = re.compile(r'[0-9a-f]{64}\Z')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw, object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON number')))


def _path(value):
    if (type(value) is not str or not value or '\\' in value or '\x00' in value
            or value.startswith('/') or re.match(r'^[A-Za-z]:', value)
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('Invalid package path')
    return PurePosixPath(value)


def _read(root, path, limit):
    path = _path(path)
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink in package path')
    fd = os.open(current, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Package entry must be a regular file')
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            data = stream.read(limit + 1)
    finally:
        if fd != -1:
            os.close(fd)
    if len(data) > limit:
        raise ValueError('Package file exceeds byte limit')
    return data


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _transport_metadata(name):
    return name == '.gitattributes' or name.startswith('.cache/huggingface/')


def verify_package(data_dir):
    """Validate inventory, lifecycle and runtime pins without approving source data."""
    root = Path(data_dir)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Package root must be a directory, not a symlink')
    manifest = _json(_read(root, 'manifest.json', MAX_MANIFEST_BYTES))
    required = {'version', 'release_version', 'dataset_repo', 'source_revision',
                'engine_implementation_sha256', 'lifecycle_sha256', 'files', 'runtimes'}
    if type(manifest) is not dict or not required <= set(manifest):
        raise ValueError('Invalid manifest shape')
    if manifest['version'] != VERSION:
        raise ValueError('Unsupported manifest schema version')
    if (type(manifest['release_version']) is not str
            or re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', manifest['release_version']) is None):
        raise ValueError('Invalid release version')
    for key in ('dataset_repo', 'source_revision'):
        if type(manifest[key]) is not str or not manifest[key].strip():
            raise ValueError(f'Invalid {key}')
    if manifest['engine_implementation_sha256'] != implementation_digest():
        raise ValueError('Engine implementation pin mismatch')
    if manifest['lifecycle_sha256'] != registry()['sha256']:
        raise ValueError('Lifecycle pin mismatch')
    files = manifest['files']
    if type(files) is not list or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('Invalid inventory count')
    inventory, total = {}, 0
    for entry in files:
        if (type(entry) is not dict or set(entry) != {'path', 'sha256', 'bytes'}
                or not _hash(entry['sha256']) or type(entry['bytes']) is not int
                or not 0 <= entry['bytes'] <= MAX_FILE_BYTES):
            raise ValueError('Invalid inventory entry')
        name = str(_path(entry['path']))
        if (name in inventory or name == 'manifest.json' or _transport_metadata(name)
                or PurePosixPath(name).name.lower() == 'readme.md'):
            raise ValueError('Duplicate or reserved inventory path')
        total += entry['bytes']
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Package exceeds total byte limit')
        raw = _read(root, name, entry['bytes'])
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError(f'Inventory hash or size mismatch: {name}')
        inventory[name] = entry
    if 'tasks/development.parquet' not in inventory:
        raise ValueError('Development tasks missing from inventory')
    # All payload must be accounted for, including unlisted symlinks and special files.
    found = set()
    walked = 0
    for base, dirs, names in os.walk(root, followlinks=False):
        walked += len(dirs) + len(names)
        if walked > MAX_FILES * 2:
            raise ValueError('Package contains too many entries')
        for name in dirs + names:
            item = Path(base) / name
            if item.is_symlink():
                raise ValueError('Symlink in package')
            if not (item.is_dir() or item.is_file()):
                raise ValueError('Special file in package')
        for name in names:
            relative = (Path(base) / name).relative_to(root).as_posix()
            if (relative != 'manifest.json' and name.lower() != 'readme.md'
                    and not _transport_metadata(relative)):
                found.add(relative)
    if found != set(inventory):
        raise ValueError('Package inventory does not match files')
    runtimes = manifest['runtimes']
    if type(runtimes) is not dict or not runtimes or 'fixture' not in runtimes:
        raise ValueError('Invalid runtimes')
    for name, entry in runtimes.items():
        if type(name) is not str or not name or type(entry) is not dict:
            raise ValueError('Invalid runtime entry')
        keys = {'inputs', 'network', 'inputs_sha256', 'network_sha256', 'allow_unapproved_data', 'scenario_ids'}
        if not keys <= set(entry) or type(entry['allow_unapproved_data']) is not bool:
            raise ValueError('Invalid runtime entry')
        for key in ('inputs', 'network'):
            if str(_path(entry[key])) not in inventory:
                raise ValueError('Runtime path missing from inventory')
        if not _hash(entry['inputs_sha256']) or not _hash(entry['network_sha256']):
            raise ValueError('Invalid runtime pin')
        active = active_day_case_ids(entry['inputs_sha256'], entry['network_sha256'])
        ids = entry['scenario_ids']
        if type(ids) is not list or any(type(item) is not str for item in ids) or len(ids) != len(active) or set(ids) != set(active):
            raise ValueError('Runtime scenario IDs differ from active set')
    return manifest


def make_environment(data_dir, runtime='fixture', *, allow_unapproved_data=False):
    if type(allow_unapproved_data) is not bool:
        raise ValueError('allow_unapproved_data must be a boolean')
    manifest = verify_package(data_dir)
    if runtime not in manifest['runtimes']:
        raise ValueError('Unknown runtime')
    entry = manifest['runtimes'][runtime]
    if entry['allow_unapproved_data'] and not allow_unapproved_data:
        raise ValueError('Runtime requires explicit allow_unapproved_data')
    root = Path(data_dir)
    # Recheck the bytes used for loading, then let the core validate canonical pins.
    inventory = {item['path']: item for item in manifest['files']}
    documents = []
    for key in ('inputs', 'network'):
        item = inventory[entry[key]]
        raw = _read(root, entry[key], item['bytes'])
        if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Runtime file changed after verification')
        documents.append(_json(raw))
    env = IncidentDayEnv(*documents, expected_inputs_sha256=entry['inputs_sha256'],
                         expected_network_sha256=entry['network_sha256'],
                         allow_unapproved_data=allow_unapproved_data)
    return env, entry


class _Deadline(Exception):
    pass


def run_episode(env, entry, *, policy='passive', case_id=None, wall_seconds=300, max_commands=5000):
    if policy not in ('passive', 'dispatcher') or type(max_commands) is not int or max_commands < 1:
        raise ValueError('Invalid episode limits or policy')
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('wall_seconds must be finite and positive')
    ids = entry['scenario_ids']
    if case_id is None:
        case_id = ids[0]
    if case_id not in ids:
        raise ValueError('Case is not active for runtime')
    start = time.monotonic()
    engine_pin = implementation_digest()
    result = dict(status='incomplete', case_id=case_id, policy=policy, commands=0, score=None,
                  verification='not_run')
    def expire(signum, frame):
        raise _Deadline()
    old_handler = signal.signal(signal.SIGALRM, expire)
    old_timer = signal.setitimer(signal.ITIMER_REAL, wall_seconds)
    try:
        observation, _ = env.reset(case_id)
        dispatcher = DayDispatcher(policy=policy)
        while not env.terminated and not env.truncated and result['commands'] < max_commands:
            action = dispatcher.action(env, observation)
            observation, _, _, _, _ = env.step(action)
            result['commands'] += 1
        if env.terminated and not env.truncated and env.minute == 1440:
            verification = env.verify_episode()
            outcome = verification.get('outcomes', {})
            if (all(verification.get(k) is True for k in ('passed', 'exact_replay', 'independent_reward_agreement'))
                    and outcome.get('completed_minutes') == 1440 and outcome.get('score_eligible') is True
                    and implementation_digest() == engine_pin):
                result.update(status='complete', verification='passed', score=outcome['graded_score'])
            else:
                result.update(status='failed', verification='failed')
        else:
            result['reason'] = 'command_limit' if not (env.terminated or env.truncated) else 'unfinished_episode'
    except _Deadline:
        result.update(status='incomplete', score=None, verification='not_passed', reason='wall_timeout')
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *old_timer)
    result['simulated_minutes'] = getattr(env, 'minute', None)
    result['elapsed_seconds'] = round(time.monotonic() - start, 3)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--runtime', default='fixture')
    parser.add_argument('--policy', choices=('passive', 'dispatcher'), default='passive')
    parser.add_argument('--allow-unapproved-data', action='store_true')
    parser.add_argument('--wall-seconds', type=float, default=300)
    parser.add_argument('--max-commands', type=int, default=5000)
    parser.add_argument('--case-id')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.verify_only:
            manifest = verify_package(args.data_dir)
            result = dict(status='verified', files=len(manifest['files']), runtimes=sorted(manifest['runtimes']))
        else:
            env, entry = make_environment(args.data_dir, args.runtime, allow_unapproved_data=args.allow_unapproved_data)
            result = run_episode(env, entry, policy=args.policy, case_id=args.case_id,
                                 wall_seconds=args.wall_seconds, max_commands=args.max_commands)
        output = json.dumps(result, sort_keys=True, allow_nan=False)
        if args.output:
            args.output.write_text(output + '\n', encoding='utf-8')
        print(output)
        return 0 if result['status'] in ('complete', 'verified') else 1
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        parser.exit(2, f'Runtime failed: {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
