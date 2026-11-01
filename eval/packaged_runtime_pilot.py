"""Run a bounded, public-policy vertical slice against a verified runtime package.

Example: python -m eval.packaged_runtime_pilot --data-dir /path/to/package
The evidence gate is intentionally stricter than successful episode verification.
"""
import argparse
import gzip
import hashlib
import os
import resource
import sys
import tempfile
import json
import math
import signal
import time
from pathlib import Path

from eval.hub_runtime import make_environment
from wildfire_core.catalog_lifecycle import select_day_case_id
from envs.wildfire_response.incident_day.agent import IncidentDayTools
from envs.wildfire_response.incident_day.baselines import DayDispatcher
from envs.wildfire_response.incident_day.environment import implementation_digest

LIMIT_SECONDS = 1800
MAX_CHECKPOINT_BYTES = 128 * 1024 * 1024
MAX_TRAJECTORY_BYTES = 128 * 1024 * 1024
VERSION = "packaged-runtime-pilot-v2"


class PilotPolicy:
    """Exercise a protection order and recovery, then use the public dispatcher."""
    def __init__(self):
        self.dispatcher = DayDispatcher()
        self.protected = set()
        self.released = set()

    def checkpoint(self):
        return dict(version=VERSION, protected=sorted(self.protected), released=sorted(self.released),
                    asked=sorted((list(item) for item in self.dispatcher.asked), key=repr))

    def restore(self, state):
        if state.get("version") != VERSION:
            raise ValueError("Incompatible pilot policy checkpoint")
        self.protected = set(state["protected"])
        self.released = set(state["released"])
        self.dispatcher.asked = {tuple(item) for item in state["asked"]}

    def action(self, env, obs):
        for group in obs['districts']:
            if group['id'] not in self.protected and group['status'] in ('unwarned', 'at_home'):
                draft = {'protection_orders': [dict(group_id=group['id'], action='shelter',
                                                    destination_id=None, approach_id=None)]}
                if env.check_action(draft).get('valid'):
                    self.protected.add(group['id'])
                    return draft
        # Let water units work first, then return them through the real routing and refill machinery.
        if obs['minute'] >= 20:
            for unit in obs['resources']:
                if (unit['id'] not in self.released and unit['kind'] in ('engine', 'aircraft')
                        and unit['state'] in ('on_scene', 'working')
                        and unit.get('water_litres', 0) < unit.get('capacity_litres', 0)):
                    draft = {'releases': [unit['id']]}
                    if env.check_action(draft).get('valid'):
                        self.released.add(unit['id'])
                        return draft
        return self.dispatcher.action(env, obs)


def collect_evidence(audit):
    """Count concrete transitions, with arrivals linked to a dispatch in this run."""
    if not audit or any(row.get('minute') != i for i, row in enumerate(audit)):
        raise ValueError('Incomplete or non-contiguous minute audit')
    roster = set(audit[0]['resources'])
    groups = set(audit[0]['population'])
    dispatches, arrivals, work, refills, population = [], [], [], [], []
    dispatched = set()
    for before, after in zip(audit, audit[1:]):
        if set(after['resources']) != roster or set(after['population']) != groups:
            raise ValueError('Audit roster changed')
        minute = after['minute']
        for identifier in sorted(roster):
            old, new = before['resources'][identifier], after['resources'][identifier]
            if old['state'] != 'mobilizing' and new['state'] == 'mobilizing':
                dispatched.add(identifier)
                dispatches.append(dict(minute=minute, resource_id=identifier))
            if identifier in dispatched and old['state'] == 'travel' and new['state'] == 'on_scene':
                arrivals.append(dict(minute=minute, resource_id=identifier))
            if old['state'] == 'refilling' and any(
                    isinstance(old.get(k), (int, float)) and isinstance(new.get(k), (int, float))
                    and new[k] > old[k] for k in ('water_litres', 'fuel_minutes')):
                refills.append(dict(minute=minute, resource_id=identifier))
        for item in after['applied_work']:
            if item.get('resource_id') not in roster or item.get('state') != 'working':
                raise ValueError('Invalid applied work')
            work.append(dict(minute=minute, resource_id=item['resource_id']))
        for identifier in sorted(groups):
            old, new = before['population'][identifier], after['population'][identifier]
            if old['status'] != new['status'] and new['status'] in ('sheltering', 'evacuating', 'arrived'):
                population.append(dict(minute=minute, group_id=identifier, status=new['status']))
    return dict(dispatch=dispatches, arrival=arrivals, suppression=work,
                population=population, refill=refills)


class PublicTools:
    """Expose the dispatcher read interface exclusively through named tools."""
    def __init__(self, environment, trajectory=None):
        self.tools = IncidentDayTools(environment)
        self.trajectory = trajectory if trajectory is not None else []
        self.last_minute = None

    def call(self, name, **arguments):
        result = self.tools.execute(name, arguments)
        if isinstance(result, dict) and result.get('committed') is False and 'error' in result:
            raise ValueError(f"{name} failed: {result['error']}")
        if name == 'get_situation':
            self.last_minute = result['minute']
        elif name in ('commit_orders', 'advance_time', 'request_status'):
            obs = result['observation']
            self.trajectory.append(dict(start_minute=self.last_minute, minute=obs['minute'],
                tool=name, arguments=arguments, observation=_public_snapshot(obs),
                terminated=result['terminated'], truncated=result['truncated']))
            self.last_minute = obs['minute']
        return result

    def route_options(self, resource_id, sector_id='incident'):
        return self.call('route_options', resource_id=resource_id, sector_id=sector_id)

    def evacuation_options(self, group_id, destination_id):
        return self.call('evacuation_options', group_id=group_id, destination_id=destination_id)

    def check_action(self, action):
        return self.call('check_action', action=action)


def _public_snapshot(observation):
    """Use an explicit public-field allowlist, never evaluator or audit records."""
    resources = [dict(id=unit['id'], kind=unit['kind'], state=unit['state'],
                      observed_at=unit.get('observed_at'), water_litres=unit.get('water_litres'))
                 for unit in observation.get('resources', [])]
    population = [dict(id=group['id'], status=group['status'], count=group.get('count'),
                       destination_id=group.get('destination_id'))
                  for group in observation.get('districts', [])]
    reports = [dict(kind=report['kind'], source=report['source'], observed_at=report.get('observed_at'),
                    received_at=report.get('received_at')) for report in observation.get('reports', [])]
    return dict(minute=observation['minute'], resources=resources, population=population, reports=reports)


def _peak_rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def _atomic_bytes(path, data):
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _write_success(private, result, trajectory, peak):
    """Persist only allowlisted actor-visible data and the aggregate evaluator result."""
    lines = b''.join(_canonical(row) + b'\n' for row in trajectory)
    if len(lines) > MAX_TRAJECTORY_BYTES:
        raise ValueError('Public trajectory exceeds private output byte limit')
    trace_path, result_path = private / 'public_trajectory.jsonl', private / 'result.json'
    checkpoint = private / 'checkpoint.json.gz'
    result.update(peak_rss_bytes=max(peak, _peak_rss_bytes()),
                  trajectory_path=str(trace_path), result_path=str(result_path))
    sizes = dict(public_trajectory=len(lines), result=0,
                 checkpoint=checkpoint.stat().st_size if checkpoint.exists() else 0)
    # Include the result file itself in the byte count, accounting for the digits it adds.
    for _ in range(20):
        sizes['total'] = sizes['public_trajectory'] + sizes['checkpoint'] + sizes['result']
        result['artifact_bytes'] = dict(sizes)
        raw = (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
        if len(raw) == sizes['result']:
            break
        sizes['result'] = len(raw)
    else:
        raise ValueError('Artifact size accounting did not converge')
    _atomic_bytes(trace_path, lines)
    _atomic_bytes(result_path, raw)


def _policy_pin():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _save(path, data):
    data = dict(data)
    data['sha256'] = hashlib.sha256(_canonical(data)).hexdigest()
    raw = _canonical(data)
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise ValueError('Checkpoint exceeds private output byte limit')
    compressed = gzip.compress(raw, mtime=0)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(compressed)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _load(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CHECKPOINT_BYTES:
        raise ValueError('Invalid private checkpoint')
    with gzip.open(path, 'rb') as stream:
        raw = stream.read(MAX_CHECKPOINT_BYTES + 1)
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise ValueError('Checkpoint exceeds private output byte limit')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('sha256') != hashlib.sha256(
            _canonical({key: value for key, value in data.items() if key != 'sha256'})).hexdigest():
        raise ValueError('Checkpoint integrity differs')
    return data


def run(data_dir, *, runtime='fixture', case_id=None, allow_unapproved_data=False,
        wall_seconds=LIMIT_SECONDS, max_commands=5000, output_dir=None, resume=False):
    if (type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds)
            or not 0 < wall_seconds <= LIMIT_SECONDS or type(max_commands) is not int or max_commands < 1):
        raise ValueError('Invalid limits: wall_seconds must be in (0, 1800] and max_commands positive')
    if output_dir is None:
        if resume:
            raise ValueError('Resume requires an output directory')
        output = Path(tempfile.mkdtemp(prefix='wildfire-pilot-'))
    else:
        output = Path(output_dir).absolute()
        if resume:
            if output.is_symlink() or not output.is_dir() or (output / 'private').is_symlink():
                raise ValueError('Invalid private output directory')
        else:
            output.mkdir(mode=0o700, parents=True, exist_ok=False)
    private = output / 'private'
    if not resume:
        private.mkdir(mode=0o700)
    checkpoint = private / 'checkpoint.json.gz'
    started = time.monotonic()
    result = dict(status='incomplete', reason=None, score=None, verification='not_run',
                  commands=0, simulated_minutes=None, evidence=None, gate_passed=False,
                  output_dir=str(output), checkpoint=None)
    expired = False
    def expire(signum, frame):
        nonlocal expired
        expired = True
    old_handler = signal.signal(signal.SIGALRM, expire)
    old_timer = signal.setitimer(signal.ITIMER_REAL, wall_seconds)
    env = policy = None
    previous_elapsed = 0.0
    previous_peak = 0
    trajectory = []
    try:
        env, entry = make_environment(data_dir, runtime, allow_unapproved_data=allow_unapproved_data)
        chosen = select_day_case_id(case_id, entry['inputs_sha256'], entry['network_sha256'])
        allowed = entry['scenario_ids'] + entry.get('additional_scenario_ids', [])
        if chosen not in allowed:
            raise ValueError('Case is not advertised by this runtime package')
        result.update(case_id=chosen, runtime=runtime)
        pin = implementation_digest()
        policy = PilotPolicy()
        if resume:
            saved = _load(checkpoint)
            if (saved.get('version') != VERSION or saved.get('policy_sha256') != _policy_pin()
                    or saved.get('runtime') != runtime or saved.get('case_id') != chosen
                    or saved.get('output_dir') != str(output)
                    or saved.get('inputs_sha256') != entry['inputs_sha256']
                    or saved.get('network_sha256') != entry['network_sha256']
                    or saved.get('implementation_sha256') != pin):
                raise ValueError('Checkpoint identity differs')
            previous_elapsed = saved['elapsed_seconds']
            previous_peak = saved['peak_rss_bytes']
            trajectory = saved['public_trajectory']
            result['commands'] = saved['commands']
            policy.restore(saved['policy'])
            env.restore(saved['environment'])
            if env.trajectory()['task_id'] != chosen:
                raise ValueError('Checkpoint scenario differs')
        else:
            env.reset(chosen)
        public = PublicTools(env, trajectory)
        while not (env.terminated or env.truncated) and result['commands'] < max_commands:
            if expired or time.monotonic() - started >= wall_seconds:
                break
            obs = public.call('get_situation')
            action = policy.action(public, obs)
            if 'advance_minutes' in action:
                public.call('advance_time', max_minutes=action['advance_minutes'])
            else:
                public.call('commit_orders', action=action)
            result['commands'] += 1
        result['simulated_minutes'] = env.minute
        evidence = collect_evidence(env._audit)
        result['evidence'] = {key: len(value) for key, value in evidence.items()}
        if expired or time.monotonic() - started >= wall_seconds:
            result['reason'] = 'wall_timeout'
        elif not env.terminated or env.truncated or env.minute != 1440:
            result['reason'] = 'command_limit' if not (env.terminated or env.truncated) else 'unfinished_episode'
        else:
            verified = env.verify_episode()
            outcome = verified.get('outcomes', {})
            valid = (all(verified.get(k) is True for k in ('passed', 'exact_replay', 'independent_reward_agreement'))
                     and outcome.get('completed_minutes') == 1440 and outcome.get('score_eligible') is True
                     and implementation_digest() == pin)
            result['verification'] = 'passed' if valid else 'failed'
            checks = {key: bool(value) for key, value in evidence.items()}
            result['evidence_checks'] = checks
            total = previous_elapsed + time.monotonic() - started
            if expired or time.monotonic() - started >= wall_seconds:
                result['reason'] = 'wall_timeout'
            else:
                result['gate_passed'] = valid and all(checks.values()) and total <= LIMIT_SECONDS
                result['status'] = 'complete' if result['gate_passed'] else 'failed'
                result['reason'] = None if result['gate_passed'] else ('verification_failed' if not valid
                    else 'evidence_missing' if not all(checks.values()) else '30_minute_gate')
                if result['gate_passed']:
                    result['score'] = outcome['graded_score']
        if result['status'] == 'incomplete' and result['reason'] in ('wall_timeout', 'command_limit'):
            # Signals only set a flag: state is saved between complete tool calls, never mid-step.
            signal.setitimer(signal.ITIMER_REAL, 0)
            saved = dict(version=VERSION, policy_sha256=_policy_pin(), runtime=runtime, case_id=chosen,
                         output_dir=str(output), inputs_sha256=entry['inputs_sha256'],
                         network_sha256=entry['network_sha256'], implementation_sha256=pin,
                         elapsed_seconds=previous_elapsed + time.monotonic() - started,
                         commands=result['commands'], policy=policy.checkpoint(), environment=env.checkpoint(),
                         public_trajectory=trajectory, peak_rss_bytes=max(previous_peak, _peak_rss_bytes()))
            _save(checkpoint, saved)
            result['checkpoint'] = str(checkpoint)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *old_timer)
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    result['total_elapsed_seconds'] = round(previous_elapsed + time.monotonic() - started, 3)
    if result['status'] == 'complete':
        _write_success(private, result, trajectory, previous_peak)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--runtime', default='fixture')
    parser.add_argument('--case-id')
    parser.add_argument('--allow-unapproved-data', action='store_true')
    parser.add_argument('--wall-seconds', type=float, default=LIMIT_SECONDS)
    parser.add_argument('--max-commands', type=int, default=5000)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = run(args.data_dir, runtime=args.runtime, case_id=args.case_id,
                     allow_unapproved_data=args.allow_unapproved_data, wall_seconds=args.wall_seconds,
                     max_commands=args.max_commands, output_dir=args.output_dir, resume=args.resume)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if result['gate_passed'] else 1
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        parser.exit(2, f'Pilot failed: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
