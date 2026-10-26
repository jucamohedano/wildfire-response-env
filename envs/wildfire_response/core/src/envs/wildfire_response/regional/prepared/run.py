"""CPU smoke comparisons and exact replay for six prepared-data development cases."""
import argparse
import gzip
import json
from pathlib import Path
import resource
import sys
import time

from ..ops.baselines import passive
from ..ops.generator import digest
from ..protection.environment import ProtectionBaseline
from .data import load_inputs
from .environment import PreparedDataEnv
from .tasks import CASE_IDS, task_manifest


def episode(inputs, identifier, policy_name):
    policy = passive if policy_name == 'passive' else ProtectionBaseline(prefer_shelter=policy_name == 'shelter')
    environment = PreparedDataEnv(inputs, expected_sha256=inputs['sha256'], allow_synthetic_conditions=True)
    observation, _ = environment.reset(identifier)
    errors = 0
    while True:
        observation, _, terminated, truncated, info = environment.step(policy(observation))
        errors += info['error'] is not None
        if terminated or truncated:
            break
    verification = environment.verify_episode()
    trace = environment.trajectory()
    return dict(case_id=identifier, policy=policy_name, reward=environment.reward,
        objective_flags=verification['objective_result']['by_kind'],
        components=verification['core_accounting']['components'],
        invalid_actions=errors, terminated=terminated, truncated=truncated, decisions=observation['decision'],
        minutes=observation['minute'], exposed_people=sum(group['exposed'] for group in observation['districts']),
        public_trace_sha256=digest(trace), verified=verification['passed']), trace


def run(source, expected_sha256, output, *, repeat=False, one=False, allow_synthetic_conditions=False):
    if allow_synthetic_conditions is not True:
        raise ValueError('Explicit synthetic-conditions opt-in is required')
    inputs = load_inputs(source, expected_sha256=expected_sha256)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    results = []
    for identifier in CASE_IDS[:1] if one else CASE_IDS:
        for name in ('passive', 'evacuate', 'shelter'):
            result, trace = episode(inputs, identifier, name)
            if repeat:
                again, _ = episode(inputs, identifier, name)
                if again != result:
                    raise ValueError('Repeated rollout differs')
                result['repeated_exactly'] = True
            destination = output / identifier / name
            destination.mkdir(parents=True)
            with gzip.open(destination / 'trajectory.json.gz', 'wt', encoding='utf-8') as stream:
                json.dump(trace, stream, ensure_ascii=False, allow_nan=False)
            results.append(result)
            print(json.dumps(result), flush=True)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary = dict(inputs_sha256=inputs['sha256'], manifest=task_manifest(inputs),
        scope='One incident landscape, six synthetic development cases; not held-out or physical validation',
        primary_rollouts=len(results), repeated=repeat, all_verified=all(row['verified'] for row in results),
        elapsed_seconds=round(time.monotonic() - started, 3),
        peak_process_rss_bytes=peak if sys.platform == 'darwin' else peak * 1024,
        workers=1, memory_scope='Process high-water RSS including interpreter and verification', results=results)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeat', action='store_true')
    parser.add_argument('--one', action='store_true')
    parser.add_argument('--allow-synthetic-conditions', action='store_true')
    options = parser.parse_args()
    run(options.inputs, options.expected_sha256, options.output, repeat=options.repeat, one=options.one,
        allow_synthetic_conditions=options.allow_synthetic_conditions)


if __name__ == '__main__':
    main()
