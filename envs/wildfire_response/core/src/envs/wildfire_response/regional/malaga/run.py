"""Sequential public-policy rollouts and exact repeat/replay checks, without models."""
import argparse
import gzip
import json
from pathlib import Path
import time

from ..ops.baselines import passive
from ..ops.generator import digest
from .environment import MalagaDevelopmentEnv, ProtectionBaseline, verify_malaga_trajectory
from .pack import load_pack
from .tasks import CASE_IDS, task_manifest


def episode(pack, identifier, policy_name):
    policy = passive if policy_name == 'passive' else ProtectionBaseline(prefer_shelter=policy_name == 'shelter')
    env = MalagaDevelopmentEnv(pack, allow_synthetic_conditions=True)
    observation, _ = env.reset(identifier)
    invalid = 0
    while True:
        observation, _, terminated, truncated, info = env.step(policy(observation))
        invalid += info['error'] is not None
        if terminated or truncated:
            break
    verification = env.verify_episode()
    trace = env.trajectory()
    result = dict(case_id=identifier, policy=policy_name, reward=env.reward,
                  objective_flags=verification['objective_result']['by_kind'],
                  invalid_actions=invalid, terminated=terminated, truncated=truncated,
                  minutes=observation['minute'], exposed_people=sum(group['exposed'] for group in observation['districts']),
                  public_trace_sha256=digest(trace), verified=verification['passed'])
    return result, trace, verification


def run(pack_path, output, *, allow_synthetic_conditions=False, repeat=False):
    if allow_synthetic_conditions is not True:
        raise ValueError('Explicit synthetic-conditions opt-in is required')
    pack = load_pack(pack_path)
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh rollout directory')
    output.mkdir(parents=True)
    (output / 'private').mkdir(mode=0o700)
    (output / 'manifest.json').write_text(json.dumps(task_manifest(pack), indent=2) + '\n')
    started = time.monotonic()
    results = []
    for identifier in CASE_IDS:
        for policy_name in ('passive', 'evacuate', 'shelter'):
            result, trace, verification = episode(pack, identifier, policy_name)
            if repeat:
                again, _, _ = episode(pack, identifier, policy_name)
                if result != again:
                    raise ValueError('Repeated rollout differed')
                result['repeated_exactly'] = True
            public = output / identifier / policy_name
            public.mkdir(parents=True)
            with gzip.open(public / 'trajectory.json.gz', 'wt', encoding='utf-8') as stream:
                json.dump(trace, stream, ensure_ascii=False, allow_nan=False)
            private = output / 'private' / identifier / policy_name
            private.mkdir(parents=True, mode=0o700)
            path = private / 'verification.json'
            path.write_text(json.dumps(verification, indent=2) + '\n')
            path.chmod(0o600)
            results.append(result)
            print(json.dumps(result), flush=True)
    summary = dict(scope='One source group, three synthetic-conditions development variants; not held-out or real-world validation',
                   episode_pack_sha256=pack['sha256'], episodes=len(results),
                   repeat_requested=repeat, all_verified=all(row['verified'] for row in results),
                   all_repeated_exactly=all(row.get('repeated_exactly', False) for row in results),
                   elapsed_seconds=round(time.monotonic() - started, 3), results=results)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', required=True)
    parser.add_argument('--output')
    parser.add_argument('--allow-synthetic-conditions', action='store_true')
    parser.add_argument('--repeat', action='store_true')
    parser.add_argument('--replay')
    options = parser.parse_args()
    if options.replay:
        with gzip.open(options.replay, 'rt', encoding='utf-8') as stream:
            print(json.dumps(verify_malaga_trajectory(json.load(stream), load_pack(options.pack)), indent=2))
    elif options.output:
        run(options.pack, options.output, allow_synthetic_conditions=options.allow_synthetic_conditions,
            repeat=options.repeat)
    else:
        parser.error('Choose --output or --replay')


if __name__ == '__main__':
    main()
