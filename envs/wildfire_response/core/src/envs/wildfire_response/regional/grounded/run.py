"""CPU rollout and replay for the opt-in Spanish development bridge."""
import argparse
import gzip
import json
from pathlib import Path
import time

from ..ops.baselines import ControlBaseline, passive
from ..ops.generator import digest
from .anchors import load_anchor_pack
from .environment import SpanishDevelopmentEnv, ClarifyingControlBaseline, verify_spanish_trajectory
from .tasks import case_ids, task_manifest


def run(pack_path, output, *, smoke=False, policies=('passive', 'control', 'clarifying')):
    started = time.monotonic()
    pack = load_anchor_pack(pack_path)
    identifiers = case_ids(pack)
    if smoke:
        identifiers = [identifier for identifier in identifiers
                       if identifier.startswith(('es-dev-000-', 'es-dev-002-'))]
    if any(policy not in ('passive', 'control', 'clarifying') for policy in policies):
        raise ValueError('Unknown public-observation baseline')
    if not policies or len(set(policies)) != len(policies):
        raise ValueError('Choose each baseline once')
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh rollout directory')
    output.mkdir(parents=True)
    (output / 'manifest.json').write_text(json.dumps(task_manifest(pack), indent=2) + '\n', encoding='utf-8')
    results = []
    for identifier in identifiers:
        for name in policies:
            policy = passive if name == 'passive' else ControlBaseline() if name == 'control' else ClarifyingControlBaseline()
            environment = SpanishDevelopmentEnv(pack)
            observation, _ = environment.reset(identifier)
            invalid = 0
            while True:
                observation, _, terminated, truncated, info = environment.submit_action(policy(observation))
                invalid += info['error'] is not None
                if terminated or truncated:
                    break
            verification = environment.verify_episode()
            trajectory = environment.trajectory()
            public = output / identifier / name
            public.mkdir(parents=True)
            with gzip.open(public / 'trajectory.json.gz', 'wt', encoding='utf-8') as stream:
                json.dump(trajectory, stream, ensure_ascii=False, allow_nan=False)
            private = output / 'private' / identifier / name
            private.mkdir(parents=True)
            (private / 'evaluator.json').write_text(json.dumps(verification, indent=2) + '\n', encoding='utf-8')
            result = dict(case_id=identifier, policy=name, reward=environment.reward,
                verified=verification['passed'], invalid_actions=invalid, decisions=observation['decision'],
                terminated=terminated, truncated=truncated,
                clarification_answers=sum(record['status'] == 'answered' for record in observation['clarification_requests']),
                objective_flags=verification['objective_result']['by_kind'],
                public_trace_sha256=digest(trajectory))
            results.append(result)
            print(json.dumps(result), flush=True)
    summary = dict(scope='Source-informed synthetic development exercises, not held-out evaluation',
                   anchor_pack_sha256=pack['sha256'], episodes=len(results),
                   all_verified=all(row['verified'] for row in results),
                   elapsed_seconds=round(time.monotonic() - started, 3), results=results)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', required=True)
    parser.add_argument('--output')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--policies', nargs='+', choices=('passive', 'control', 'clarifying'),
                        default=['passive', 'control', 'clarifying'])
    parser.add_argument('--replay')
    options = parser.parse_args()
    if options.replay:
        with gzip.open(options.replay, 'rt', encoding='utf-8') as stream:
            print(json.dumps(verify_spanish_trajectory(json.load(stream), load_anchor_pack(options.pack)), indent=2))
    elif options.output:
        print(json.dumps(run(options.pack, options.output, smoke=options.smoke, policies=options.policies), indent=2))
    else:
        parser.error('Choose --output or --replay')


if __name__ == '__main__':
    main()
