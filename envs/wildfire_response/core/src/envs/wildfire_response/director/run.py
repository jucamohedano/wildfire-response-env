"""Capped free-model evaluation, with a matched deterministic baseline and replay."""
import argparse
import json
from pathlib import Path
import subprocess
import time

from ..core.environment import SCHEMA_VERSION, WildfireResponseEnv
from ..core.reward import REWARD_VERSION
from ..core.scenarios import get_task
from ..eval.run import dump, replay, run_episode, source_digest
from .policy import DEFAULT_MODEL, DirectorPolicy
from .choices import CHOICE_VERSION


def run(output, scenario_id='train-000', decisions=8, model=DEFAULT_MODEL, transport=None, tool_mode='forced',
        profile='standard'):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh output directory')
    env = WildfireResponseEnv(max_decisions=decisions)
    policy = DirectorPolicy(model=model, max_calls=decisions, transport=transport, tool_mode=tool_mode, profile=profile)
    observation, info = env.reset(scenario_id)
    task = get_task(scenario_id)
    trajectory = dict(version=SCHEMA_VERSION, reward_version=REWARD_VERSION, scenario_id=scenario_id,
                      split=task['split'], seed=task['seed'], max_decisions=decisions, policy='director',
                      model=model, tool_mode=tool_mode, profile=profile, choice_version=CHOICE_VERSION,
                      initial_observation=observation, initial_info=info, transitions=[],
                      engine_revision=subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip(),
                      source_digest=source_digest())
    output.mkdir(parents=True)
    started = time.perf_counter()
    try:
        while True:
            action = policy(observation, feedback=info)
            observation, value, terminated, truncated, info = env.step(action)
            trajectory['transitions'].append(dict(action=action, observation=observation, reward=value,
                                                  terminated=terminated, truncated=truncated, info=info))
            print(json.dumps(dict(decision=observation['decision'], tick=observation['tick'],
                                  reward=value, error=info.get('error'))), flush=True)
            if terminated or truncated:
                break
    except Exception as error:
        dump(output / 'partial-trajectory.json.gz', trajectory)
        dump(output / 'requests.json', policy.records)
        dump(output / 'status.json', dict(status='incomplete_transport_or_runtime_failure', requests=policy.calls,
                                         error_type=type(error).__name__))
        raise
    trajectory['elapsed_seconds'] = round(time.perf_counter()-started, 3)
    trajectory['total_reward'] = sum(item['reward'] for item in trajectory['transitions'])
    dump(output / 'director.json.gz', trajectory)
    dump(output / 'requests.json', policy.records)
    baseline, _ = run_episode(task, 'deterministic', max_decisions=decisions)
    dump(output / 'deterministic.json.gz', baseline)
    summary = dict(status='complete', scenario_id=scenario_id, max_decisions=decisions,
                   choice_version=CHOICE_VERSION, tool_mode=tool_mode, profile=profile,
                   director_reward=trajectory['total_reward'], deterministic_reward=baseline['total_reward'],
                   director_replayed_reward=replay(trajectory), requests=policy.calls,
                   invalid_actions=env.evaluator_state()['counters']['invalid_actions'],
                   outcome_components=env.evaluator_state()['components'],
                   terminated=terminated, truncated=truncated, elapsed_seconds=trajectory['elapsed_seconds'],
                   note='Synthetic bounded smoke test, not a held-out benchmark or model training')
    dump(output / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--scenario-id', default='train-000')
    parser.add_argument('--max-decisions', type=int, choices=range(1, 33), default=8)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--tool-mode', choices=['forced', 'auto'], default='forced')
    parser.add_argument('--profile', choices=['standard', 'risk-aware'], default='standard')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.output, args.scenario_id, args.max_decisions, args.model,
                             tool_mode=args.tool_mode, profile=args.profile)))
    except (RuntimeError, ValueError) as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
