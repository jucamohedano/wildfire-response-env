"""Run and replay ops development fixtures on one CPU worker."""
import argparse
import json
import resource
import sys
import time
from pathlib import Path

from ...eval.run import dump, load, source_digest
from .baselines import ControlBaseline, DispatchBaseline, RandomValidBaseline, blanket_warning, passive
from .environment import OPS_INTERFACE_VERSION, OpsControlCenterEnv
from .objectives import REWARD_VERSION, verify_ops_trajectory
from .tasks import OPS_TASK_MANIFEST_SHA256, OPS_TASK_VERSION, get_ops_task, list_ops_tasks


POLICIES = ('passive', 'random_valid', 'dispatch', 'control', 'blanket_warning')


def run_episode(task_id='control-000', policy_name='control', seed=None, *,
                max_decisions=60, ticks_per_action=2):
    if policy_name not in POLICIES:
        raise ValueError(f'Unknown ops policy: {policy_name}')
    started = time.perf_counter()
    env = OpsControlCenterEnv(max_decisions=max_decisions, ticks_per_action=ticks_per_action)
    observation, _ = env.reset(task_id, seed)
    effective_seed = env.trajectory()['seed']
    policy = dict(passive=lambda: passive, dispatch=DispatchBaseline,
                  control=ControlBaseline, blanket_warning=lambda: blanket_warning,
                  random_valid=lambda: RandomValidBaseline(effective_seed))[policy_name]()
    while True:
        action = policy(observation)
        observation, _, terminated, truncated, _ = env.submit_action(action)
        if terminated or truncated:
            break
    trajectory = env.trajectory()
    trajectory['policy'] = policy_name
    trajectory['source_sha256'] = source_digest()
    verification = env.verify_episode()
    transitions = trajectory['transitions']
    task = get_ops_task(task_id)
    summary = dict(task_id=task_id, task_set=trajectory['task_set'], split=task['split'], seed=effective_seed, policy=policy_name,
        group_id=task['group_id'], family=task.get('family', task['scenario_id']),
        interface_version=OPS_INTERFACE_VERSION, task_version=OPS_TASK_VERSION,
        reward_version=REWARD_VERSION, objective_score=verification['objective_score'],
        completed_objectives=verification['objective_result']['completed_count'],
        objective_count=verification['objective_result']['total_count'],
        objective_components=verification['objective_result']['by_kind'],
        all_objectives_met=verification['objective_result']['all_objectives_met'],
        control_evidence=verification['evaluator_evidence'],
        decisions=len(transitions), model_minutes=observation['minute'],
        invalid_actions=sum(not step['action_valid'] for step in transitions),
        corrections_sent=sum(len(step['action'].get('readback_corrections', []))
                             for step in transitions if step['action_valid']),
        failed_deliveries=sum(record['status'] == 'failed' for record in observation['message_deliveries']),
        terminated=terminated, truncated=truncated, verified=verification['passed'],
        diagnostics=verification['core_accounting']['components'],
        elapsed_seconds=round(time.perf_counter() - started, 6),
        process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == 'darwin' else 1024))
    return trajectory, summary, verification


def run_suite(output, task_ids, policies=POLICIES, *, max_decisions=60, ticks_per_action=2):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh ops rollout directory')
    if not task_ids or not policies:
        raise ValueError('Select at least one ops task and policy')
    for task_id in task_ids:
        get_ops_task(task_id)
    if any(policy not in POLICIES for policy in policies):
        raise ValueError('Unknown ops policy')
    output.mkdir(parents=True)
    summaries = []
    started = time.perf_counter()
    for task_id in task_ids:
        for policy in policies:
            trajectory, summary, verification = run_episode(
                task_id, policy, max_decisions=max_decisions, ticks_per_action=ticks_per_action)
            directory = output / task_id / policy
            directory.mkdir(parents=True)
            dump(directory / 'trajectory.json.gz', trajectory)
            dump(directory / 'summary.json', summary)
            dump(directory / 'verification.json', verification)
            summaries.append(summary)
    evaluator = output / 'evaluator'
    evaluator.mkdir()
    dump(evaluator / 'task-manifest.json', dict(version=OPS_TASK_VERSION,
        sha256=OPS_TASK_MANIFEST_SHA256, tasks=[get_ops_task(task_id) for task_id in task_ids]))
    result = dict(scope='Development fixtures only; not a held-out evaluation', workers=1,
                  episodes=len(summaries), elapsed_seconds=round(time.perf_counter() - started, 6),
                  interface_version=OPS_INTERFACE_VERSION, task_version=OPS_TASK_VERSION,
                  reward_version=REWARD_VERSION, summaries=summaries)
    dump(output / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', default='.runtime/ops-development-smoke')
    parser.add_argument('--task-id', default='all')
    parser.add_argument('--task-set', choices=['dispatch', 'control', 'all'], default='control')
    parser.add_argument('--policy', choices=[*POLICIES, 'all'], default='all')
    parser.add_argument('--max-decisions', type=int, default=60)
    parser.add_argument('--ticks-per-action', type=int, default=2)
    parser.add_argument('--replay')
    args = parser.parse_args()
    if args.replay:
        print(json.dumps(verify_ops_trajectory(load(args.replay))))
    else:
        task_set = None if args.task_set == 'all' else args.task_set
        task_ids = [task['id'] for task in list_ops_tasks(task_set)] if args.task_id == 'all' else [args.task_id]
        policies = POLICIES if args.policy == 'all' else [args.policy]
        result = run_suite(args.output, task_ids, policies, max_decisions=args.max_decisions,
                           ticks_per_action=args.ticks_per_action)
        print(json.dumps(dict(episodes=result['episodes'], elapsed_seconds=result['elapsed_seconds'],
                              output=args.output, verified=all(row['verified'] for row in result['summaries']))))


if __name__ == '__main__':
    main()
