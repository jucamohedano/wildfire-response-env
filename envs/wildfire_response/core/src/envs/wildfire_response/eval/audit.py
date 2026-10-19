"""Full-budget counterfactual reward checks and baseline coverage readiness gates."""
import argparse
import copy
import json
from pathlib import Path
import resource
import time

from ..baselines import passive
from ..core.environment import WildfireResponseEnv
from ..core.reward import WEIGHTS, cost
from ..core.scenarios import FAMILIES, get_task
from .run import dump, load, replay, source_digest


def warning_fixture(urgent):
    task = get_task('train-001')
    env = WildfireResponseEnv()
    observation, _ = env.reset(scenario=task)
    home = next(district['home'] for district in observation['districts'] if district['id'] == 'farm')
    task.update(id='audit-urgent' if urgent else 'audit-false-alarm', minimum_ticks=64,
                events=[dict(tick=6, kind='ignition', location=home)] if urgent else [],
                reports=[dict(id='r000', observed_at=0, received_at=0, source_id='audit-citizen',
                    source_role='resident', channel='chat', location=home,
                    text='Smoke reported at farm; verify.')], private_annotations=[])
    return task


def rollout(task, policy, mode):
    env = WildfireResponseEnv(max_decisions=32)
    observation, _ = env.reset(scenario=task)
    initial_cost = env.evaluator_state()['cost']
    rewards, decisions, actions = [], 0, []
    while True:
        action = policy(observation)
        actions.append(copy.deepcopy(action))
        observation, value, terminated, truncated, info = env.step(action)
        rewards.append(value)
        decisions += 1
        if terminated or truncated:
            break
    private = env.evaluator_state()
    total = sum(rewards)
    reconstructed = initial_cost - cost(private['components'])
    if abs(total-reconstructed) > 1e-9:
        raise ValueError('Reward does not match weighted outcome components')
    return dict(policy=mode, scenario_id=task['id'], total_reward=total,
                components=private['components'], decisions=decisions,
                terminated=terminated, truncated=truncated, final_tick=observation['tick'],
                initial_cost=initial_cost, reconstructed_reward=reconstructed,
                actions=actions, rewards=rewards)


def warning_policy(delay=0, everyone=False, repeat=False):
    def policy(observation):
        action = passive(observation)
        if observation['decision'] == delay or (repeat and observation['decision'] >= delay):
            action['messages'] = [dict(recipient=district['id'], purpose='evacuate',
                channel='broadcast', report_ids=['r000']) for district in observation['districts']
                if everyone or district['id'] == 'farm']
        return action
    return policy


def notice_policy(repeat):
    def policy(observation):
        action = passive(observation)
        if repeat or observation['decision'] == 0:
            action['messages'] = [dict(recipient='farm', purpose='inform', channel='broadcast', report_ids=['r000'])]
        return action
    return policy


def reward_audit(output):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh audit output directory')
    output.mkdir(parents=True)
    started = time.perf_counter()
    urgent, false_alarm = warning_fixture(True), warning_fixture(False)
    specifications = [
        ('urgent_early', urgent, warning_policy()),
        ('urgent_delayed', urgent, warning_policy(delay=2)),
        ('urgent_passive', urgent, passive),
        ('false_alarm_restraint', false_alarm, passive),
        ('false_alarm_blanket_evacuation', false_alarm, warning_policy(everyone=True)),
        ('single_notice', false_alarm, notice_policy(False)),
        ('repeated_notices', false_alarm, notice_policy(True)),
        ('invalid_abort', urgent, lambda observation: {}),
    ]
    rows = {}
    for name, task, policy in specifications:
        result = rollout(task, policy, name)
        dump(output / f'{name}.json', result)
        rows[name] = {key: value for key, value in result.items() if key not in ('actions', 'rewards')}
        print(json.dumps(dict(check_case=name, reward=result['total_reward'],
                              terminated=result['terminated'], truncated=result['truncated'])), flush=True)
    checks = dict(
        early_warning_beats_delay=rows['urgent_early']['total_reward'] > rows['urgent_delayed']['total_reward'],
        warning_reduces_exposure=rows['urgent_early']['components']['exposed_population'] < rows['urgent_delayed']['components']['exposed_population'],
        restraint_beats_blanket_evacuation=rows['false_alarm_restraint']['total_reward'] > rows['false_alarm_blanket_evacuation']['total_reward'],
        repeated_notices_lose_reward=rows['single_notice']['total_reward'] > rows['repeated_notices']['total_reward'],
        duplicate_notices_do_not_change_physical_outcomes=all(
            rows['single_notice']['components'][key] == rows['repeated_notices']['components'][key]
            for key in ('exposed_population', 'burned_terrain', 'unnecessary_evacuation')),
        invalid_abort_does_not_beat_passive=rows['invalid_abort']['total_reward'] < rows['urgent_passive']['total_reward'],
        no_unexpected_invalid_actions=all(row['components']['invalid_actions'] == 0
                                        for name, row in rows.items() if name != 'invalid_abort'))
    result = dict(version='wildfire-readiness-audit-v1', checks=checks, passed=all(checks.values()),
                  cases=rows, weights=WEIGHTS, max_decisions=32, source_sha256=source_digest(),
                  elapsed_seconds=round(time.perf_counter()-started, 3),
                  peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  note='Synthetic counterfactual fixtures, not training or held-out benchmark scenarios')
    dump(output / 'summary.json', result)
    return result


def coverage_audit(directory, rewards):
    root = Path(directory)
    summary = load(root / 'summary.json')
    rows = [json.loads(line) for line in (root / 'results.jsonl').read_text().splitlines()]
    policies = ('passive', 'random', 'deterministic')
    checks = dict(reward_checks_pass=rewards['passed'], complete_matrix=len(rows) == 18,
                  same_reward_weights=summary['weights'] == rewards['weights'],
                  training_only=all(row['split'] == 'train' for row in rows),
                  no_invalid_baseline_actions=all(row['components']['invalid_actions'] == 0 for row in rows))
    checks['six_families_per_policy'] = all(
        {row['family'] for row in rows if row['policy'] == policy} == set(FAMILIES) for policy in policies)
    verified = []
    for row in rows:
        trajectory = load(root / 'trajectories' / f'{row["scenario"]}-{row["policy"]}.json.gz')
        if trajectory['max_decisions'] != 32:
            raise ValueError('Expected the full 32-decision budget')
        replayed = replay(trajectory)
        if abs(replayed-row['total_reward']) > 1e-9:
            raise ValueError('Summary reward differs from trajectory')
        initial = WildfireResponseEnv()
        initial.reset(trajectory['scenario_id'], trajectory['seed'])
        reconstructed = initial.evaluator_state()['cost'] - cost(row['components'])
        if abs(reconstructed-replayed) > 1e-9:
            raise ValueError('Outcome components do not reconstruct reward')
        verified.append(dict(scenario=row['scenario'], policy=row['policy'], reward=replayed))
        print(f'Replayed {row["scenario"]} {row["policy"]}', flush=True)
    checks['all_trajectories_replayed'] = len(verified) == 18
    result = dict(version='wildfire-readiness-audit-v1', checks=checks,
                  ready_for_bounded_training_search=all(checks.values()),
                  coverage_summary=summary, replayed=verified,
                  naturally_terminated=sum(row['terminated'] for row in rows),
                  budget_truncated=sum(not row['terminated'] for row in rows),
                  limitations=['Only six training configurations; no generalization claim.',
                      'Budget truncation is not fire resolution; compare lifecycle and outcome components.',
                      'No validation or test cases were used for selecting policies.',
                      'Reward checks cover named exploits, not every possible exploit.'])
    dump(root / 'readiness.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--coverage')
    parser.add_argument('--reward-results')
    args = parser.parse_args()
    if args.coverage:
        if not args.reward_results:
            parser.error('--coverage requires --reward-results')
        result = coverage_audit(args.coverage, load(args.reward_results))
        dump(args.output, result)
    else:
        result = reward_audit(args.output)
    print(json.dumps({key: result[key] for key in ('checks',) if key in result}))


if __name__ == '__main__':
    main()
