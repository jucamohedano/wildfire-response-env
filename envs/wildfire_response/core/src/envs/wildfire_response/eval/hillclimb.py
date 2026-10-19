"""One bounded, training-only parameter hill-climb, followed by frozen validation."""
import argparse
import json
from pathlib import Path
import time

from ..baselines import DeterministicBaseline, passive
from ..core.environment import SCHEMA_VERSION, WildfireResponseEnv
from ..core.reward import REWARD_VERSION
from ..core.scenarios import FAMILIES, generate_tasks
from .run import dump, source_digest


class VerificationPolicy:
    def __init__(self, verify_first):
        self.verify_first = verify_first
        self.baseline = DeterministicBaseline()

    def __call__(self, observation):
        fresh_fire = any(cell['burning'] and cell['observed_at'] == observation['tick']
                         for cell in observation['sensor_memory'])
        if not self.verify_first or fresh_fire:
            return self.baseline(observation)
        action = passive(observation)
        candidates = observation['candidates']['reconnaissance']
        if candidates:
            point = candidates[0]
            for order in action['fleet']['scout_orders']:
                order.update(command='patrol', waypoints=[point], reason='Investigate before warning')
            for order in action['fleet']['extinguisher_orders']:
                order.update(command='scout', target_x=point[0], target_y=point[1], reason='Verify received evidence')
        return action


def balanced_tasks(split):
    if split not in ('train', 'validation'):
        raise ValueError('This smoke search does not access the test split')
    tasks = generate_tasks()
    return [next(task for task in tasks if task['split'] == split and task['family'] == family) for family in FAMILIES]


def evaluate(tasks, verify_first, output):
    output.mkdir()
    rows = []
    for task in tasks:
        env = WildfireResponseEnv(max_decisions=32)
        observation, info = env.reset(scenario=task)
        policy = VerificationPolicy(verify_first)
        trajectory = dict(version=SCHEMA_VERSION, reward_version=REWARD_VERSION, scenario_id=task['id'],
                          split=task['split'], seed=task['seed'], max_decisions=32,
                          policy='verification-parameter', parameters=dict(verify_first=verify_first),
                          initial_observation=observation, initial_info=info, transitions=[])
        total, decisions = 0.0, 0
        while True:
            action = policy(observation)
            observation, value, terminated, truncated, info = env.step(action)
            trajectory['transitions'].append(dict(action=action, observation=observation, reward=value,
                                                  terminated=terminated, truncated=truncated, info=info))
            total += value
            decisions += 1
            if terminated or truncated:
                break
        private = env.evaluator_state()
        trajectory['total_reward'] = total
        dump(output / f'{task["id"]}.json.gz', trajectory)
        row = dict(scenario_id=task['id'], split=task['split'], family=task['family'], seed=task['seed'],
                   verify_first=verify_first, total_reward=total, components=private['components'],
                   decisions=decisions, terminated=terminated, truncated=truncated)
        rows.append(row)
        print(json.dumps(dict(scenario=task['id'], verify_first=verify_first, reward=total)), flush=True)
    dump(output / 'scores.json', rows)
    return dict(mean_reward=sum(row['total_reward'] for row in rows)/len(rows),
                invalid_actions=sum(row['components']['invalid_actions'] for row in rows), rows=rows)


def search(output, readiness_path):
    readiness = json.loads(Path(readiness_path).read_text())
    if not readiness.get('ready_for_bounded_training_search'):
        raise ValueError('Run and pass the baseline/reward readiness audit first')
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh search output directory')
    output.mkdir(parents=True)
    started = time.perf_counter()
    training = balanced_tasks('train')
    incumbent = evaluate(training, False, output / 'train-incumbent')
    neighbor = evaluate(training, True, output / 'train-neighbor')
    if incumbent['invalid_actions'] or neighbor['invalid_actions']:
        raise ValueError('Policy search stopped: invalid actions obscure the outcome comparison')
    selected = neighbor['mean_reward'] > incumbent['mean_reward'] + 1e-12
    dump(output / 'selected-policy.json', dict(verify_first=selected, selected_using='training_only',
         training_ids=[task['id'] for task in training], selection_frozen_before_validation=True))
    validation = balanced_tasks('validation')
    validation_incumbent = evaluate(validation, False, output / 'validation-incumbent')
    validation_selected = (evaluate(validation, True, output / 'validation-selected') if selected else validation_incumbent)
    result = dict(version='wildfire-parameter-smoke-search-v1', parameter='verify_first',
                  selected=selected, training_incumbent=incumbent, training_neighbor=neighbor,
                  validation_incumbent=validation_incumbent, validation_selected=validation_selected,
                  source_sha256=source_digest(), elapsed_seconds=round(time.perf_counter()-started, 3),
                  limitations=['Single Boolean neighbor over six training cases, not model-weight training.',
                    'Validation is reporting only, not used to select or update the policy.',
                    'No test cases or model API requests used.',
                    'Templated reports and one map limit generalization; do not assume verification-first is always safe.'])
    dump(output / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--readiness', required=True)
    args = parser.parse_args()
    result = search(args.output, args.readiness)
    print(json.dumps(dict(selected_verify_first=result['selected'], elapsed_seconds=result['elapsed_seconds'])))


if __name__ == '__main__':
    main()
