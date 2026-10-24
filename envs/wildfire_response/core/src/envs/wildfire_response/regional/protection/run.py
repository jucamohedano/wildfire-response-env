"""Sequential CPU validation of the opt-in protection mode; not model training."""
import argparse
import gzip
import json
from pathlib import Path
import time

from ..grounded.anchors import load_anchor_pack
from ..ops.baselines import passive
from ..ops.generator import digest
from .environment import ProtectionDevelopmentEnv, ProtectionBaseline, verify_protection_trajectory
from .tasks import case_ids, task_manifest
from .context import assess_source_anchor


def run(pack_path, output, *, smoke=False):
    started = time.monotonic()
    pack = load_anchor_pack(pack_path)
    identifiers = case_ids(pack)
    if smoke:
        identifiers = [identifier for identifier in identifiers
                       if identifier.startswith(('protect-dev-000-', 'protect-dev-002-'))]
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh protection rollout directory')
    output.mkdir(parents=True)
    (output / 'manifest.json').write_text(json.dumps(task_manifest(pack), indent=2) + '\n', encoding='utf-8')
    (output / 'private').mkdir()
    (output / 'private' / 'source-readiness.json').write_text(json.dumps(dict(
        role='evaluator_only', actor_input_eligible=False, historical_replay_ready=False,
        context_status='not_provided', anchors=[assess_source_anchor(anchor) for anchor in pack['anchors']]),
        indent=2) + '\n', encoding='utf-8')
    results = []
    for identifier in identifiers:
        for name in ('passive', 'evacuate', 'shelter'):
            policy = passive if name == 'passive' else ProtectionBaseline(prefer_shelter=name == 'shelter')
            environment = ProtectionDevelopmentEnv(pack)
            observation, _ = environment.reset(identifier)
            invalid = 0
            while True:
                observation, _, terminated, truncated, info = environment.step(policy(observation))
                invalid += info['error'] is not None
                if terminated or truncated:
                    break
            verification = environment.verify_episode()
            trace = environment.trajectory()
            public = output / identifier / name
            public.mkdir(parents=True)
            with gzip.open(public / 'trajectory.json.gz', 'wt', encoding='utf-8') as stream:
                json.dump(trace, stream, ensure_ascii=False, allow_nan=False)
            private = output / 'private' / identifier / name
            private.mkdir(parents=True)
            (private / 'evaluator.json').write_text(json.dumps(verification, indent=2) + '\n', encoding='utf-8')
            orders = observation['protection_orders']
            result = dict(case_id=identifier, policy=name, verified=verification['passed'],
                reward=environment.reward, objective_flags=verification['objective_result']['by_kind'],
                invalid_actions=invalid, terminated=terminated, truncated=truncated,
                protection_orders=len(orders), delivery_failures=sum(order['status']=='failed' for order in orders),
                stale_deliveries=sum(order['status']=='stale_ignored' for order in orders),
                in_person_orders=sum(order['channel']=='in_person' for order in orders),
                executed_orders=sum(order['executed_at'] is not None for order in orders),
                exposed_people=sum(district['exposed'] for district in observation['districts']),
                public_trace_sha256=digest(trace))
            results.append(result)
            print(json.dumps(result), flush=True)
    summary = dict(scope='Synthetic development protection exercises, not held-out operational validation',
                   anchor_pack_sha256=pack['sha256'], episodes=len(results),
                   all_verified=all(row['verified'] for row in results),
                   elapsed_seconds=round(time.monotonic()-started,3), results=results)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', required=True)
    parser.add_argument('--output')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--replay')
    options = parser.parse_args()
    if options.replay:
        with gzip.open(options.replay, 'rt', encoding='utf-8') as stream:
            print(json.dumps(verify_protection_trajectory(json.load(stream), load_anchor_pack(options.pack)), indent=2))
    elif options.output:
        print(json.dumps(run(options.pack, options.output, smoke=options.smoke), indent=2))
    else:
        parser.error('Choose --output or --replay')


if __name__ == '__main__':
    main()
