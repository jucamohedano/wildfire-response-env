"""Store verified stress episodes with public replay and private evaluator artifacts.

Paths in manifest records are relative to the run root. Compact observation
landscape references are relative to their JSONL file. Each JSONL starts with a
reset record carrying episode metadata outside the observation, followed by step
records preserving all public transition fields.
"""
import gzip
import hashlib
import json
import math
from pathlib import Path
import re


VERSION = 'wildfire-stress-storage-v1'
TRANSITION_VERSION = 'wildfire-stress-transitions-v1'
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
RECORD_FIELDS = {'version', 'type', 'episode', 'step_index', 'training_eligible'}


def _label(value, name):
    if not isinstance(value, str) or not LABEL.fullmatch(value):
        raise ValueError(f'{name} must be a safe label of 1 to 128 ASCII characters')


def _json_bytes(value):
    def check(item):
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError('JSON object keys must be strings')
            for child in item.values():
                check(child)
        elif type(item) is list:
            for child in item:
                check(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError('Expected canonical JSON values')
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _validate(trajectory, verification):
    if not isinstance(verification, dict) or verification.get('passed') is not True:
        raise ValueError('Episode verification must have passed=True')
    if type(verification.get('suitable_for_training')) is not bool:
        raise ValueError('Verification must declare suitable_for_training')
    if not isinstance(trajectory, dict):
        raise ValueError('Expected a canonical public trajectory')
    for name in ('interface_version', 'reward_version', 'task_id'):
        if not isinstance(trajectory.get(name), str) or not trajectory[name]:
            raise ValueError(f'Trajectory must declare {name}')
    if type(trajectory.get('seed')) is not int:
        raise ValueError('Trajectory must declare an integer seed')
    transitions = trajectory.get('transitions')
    if not isinstance(transitions, list) or not transitions:
        raise ValueError('Trajectory must contain completed episode transitions')
    observations = [trajectory.get('initial_observation')]
    for index, transition in enumerate(transitions):
        if not isinstance(transition, dict) or not {'action', 'observation', 'objective_reward',
                                                   'terminated', 'truncated'} <= transition.keys():
            raise ValueError('Incomplete canonical transition')
        if RECORD_FIELDS.intersection(transition):
            raise ValueError('Transition conflicts with compact record metadata')
        if not isinstance(transition['action'], dict):
            raise ValueError('Transition action must be an object')
        if type(transition['objective_reward']) not in (int, float):
            raise ValueError('Transition objective_reward must be numeric')
        if any(type(transition[name]) is not bool for name in ('terminated', 'truncated')):
            raise ValueError('Transition terminal flags must be booleans')
        if index < len(transitions) - 1 and (transition['terminated'] or transition['truncated']):
            raise ValueError('Episode continues after a terminal transition')
        observations.append(transition['observation'])
    if not (transitions[-1]['terminated'] or transitions[-1]['truncated']):
        raise ValueError('Episode must terminate or truncate')
    landscape_bytes = None
    censored = verification.get('boundary_censored', False)
    if type(censored) is not bool:
        raise ValueError('boundary_censored must be a boolean')
    for observation in observations:
        if not isinstance(observation, dict) or not isinstance(observation.get('landscape'), dict):
            raise ValueError('Every observation must contain a landscape object')
        if 'landscape_ref' in observation:
            raise ValueError('Canonical observation already has landscape_ref')
        current = _json_bytes(observation['landscape'])
        if landscape_bytes is not None and current != landscape_bytes:
            raise ValueError('Landscape changes during the episode')
        landscape_bytes = current
        status = observation.get('topographic_status', {})
        if not isinstance(status, dict) or type(status.get('boundary_censored', False)) is not bool:
            raise ValueError('Invalid public boundary censoring status')
        censored = censored or status.get('boundary_censored', False)
    reward = sum(transition['objective_reward'] for transition in transitions)
    if not math.isfinite(reward):
        raise ValueError('Episode reward must be finite')
    return landscape_bytes, censored, reward


class RunStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        if self.root.exists() or self.root.is_symlink():
            raise ValueError('Choose a fresh stress run output directory')
        self.root.mkdir(parents=True)
        for relative in ('public/trajectories', 'public/transitions', 'shared/maps', 'private/evaluator'):
            (self.root / relative).mkdir(parents=True)
        (self.root / 'private').chmod(0o700)

    def write_episode(self, case_id, policy, trajectory, verification, *, split, group_id):
        for name, value in (('case_id', case_id), ('policy', policy), ('split', split), ('group_id', group_id)):
            _label(value, name)
        if split not in ('train', 'validation', 'test'):
            raise ValueError('split must be train, validation, or test')
        trajectory_bytes = _json_bytes(trajectory)
        verification_bytes = _json_bytes(verification)
        public = json.loads(trajectory_bytes)
        private = json.loads(verification_bytes)
        landscape_bytes, censored, reward = _validate(public, private)
        landscape_hash = hashlib.sha256(landscape_bytes).hexdigest()
        landscape_path = f'shared/maps/{landscape_hash}.json'
        eligible = split == 'train' and private['suitable_for_training'] and not censored
        metadata = {key: value for key, value in public.items()
                    if key not in ('initial_observation', 'transitions')}
        episode = dict(case_id=case_id, policy=policy, split=split, group_id=group_id,
                       training_eligible=eligible, trajectory_metadata=metadata)

        def compact(observation):
            result = {key: value for key, value in observation.items() if key != 'landscape'}
            result['landscape_ref'] = '../../../' + landscape_path
            return result

        records = [dict(version=TRANSITION_VERSION, type='reset', episode=episode,
                        training_eligible=eligible, observation=compact(public['initial_observation']))]
        for index, transition in enumerate(public['transitions'], 1):
            record = dict(transition)
            record.update(version=TRANSITION_VERSION, type='step', step_index=index,
                          training_eligible=eligible, observation=compact(transition['observation']))
            records.append(record)
        compact_bytes = b''.join(_json_bytes(record) + b'\n' for record in records)
        payloads = {
            'trajectory': (f'public/trajectories/{case_id}/{policy}.json.gz', trajectory_bytes),
            'transitions': (f'public/transitions/{case_id}/{policy}.jsonl.gz', compact_bytes),
            'verification': (f'private/evaluator/{case_id}/{policy}.json.gz', verification_bytes),
        }
        artifacts = {name: (relative, gzip.compress(payload, mtime=0))
                     for name, (relative, payload) in payloads.items()}
        for relative, _ in artifacts.values():
            path = self.root / relative
            if any(parent.is_symlink() for parent in path.parents if parent != self.root.parent):
                raise ValueError('Artifact paths must not contain symlinks')
            if path.exists() or path.is_symlink():
                raise ValueError('Episode artifact already exists: ' + relative)
        map_path = self.root / landscape_path
        if map_path.parent.is_symlink() or map_path.parent.parent.is_symlink() or map_path.is_symlink():
            raise ValueError('Landscape paths must not contain symlinks')
        if map_path.exists() and (not map_path.is_file() or map_path.read_bytes() != landscape_bytes):
            raise ValueError('Existing shared landscape does not match its hash')

        result = dict(version=VERSION, transition_version=TRANSITION_VERSION,
            case_id=case_id, policy=policy, split=split, group_id=group_id,
            task_id=public['task_id'], seed=public['seed'],
            interface_version=public['interface_version'], reward_version=public['reward_version'],
            trajectory_metadata=metadata, reward=reward, decisions=len(public['transitions']),
            terminated=public['transitions'][-1]['terminated'], truncated=public['transitions'][-1]['truncated'],
            suitable_for_training=private['suitable_for_training'], training_eligible=eligible,
            boundary_censored=censored, landscape_path=landscape_path,
            landscape_sha256=landscape_hash, landscape_bytes=len(landscape_bytes))
        if not map_path.exists():
            with map_path.open('xb') as target:
                target.write(landscape_bytes)
        for name, (relative, payload) in artifacts.items():
            path = self.root / relative
            path.parent.mkdir(exist_ok=True)
            with path.open('xb') as target:
                target.write(payload)
            result[name + '_path'] = relative
            result[name + '_sha256'] = hashlib.sha256(payload).hexdigest()
            result[name + '_bytes'] = len(payload)
        return result
