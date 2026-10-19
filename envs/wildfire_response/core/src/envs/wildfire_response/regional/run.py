"""Reproducible regional v2 CPU rollout and component-accounted replay."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from ..eval.run import dump, load, source_digest
from .baselines import CooperativeBaseline, RandomValidBaseline, blanket_warning, passive
from .data import PACK
from .environment import RegionalResponseEnv, VERSION, REWARD_VERSION
from .verifier import verify


def replay(trajectory):
    if trajectory['version'] != VERSION:
        raise ValueError('Regional observation/action schema changed; use the recorded revision for old replays')
    if trajectory['reward_version'] != REWARD_VERSION:
        raise ValueError('Regional reward version changed; use the recorded revision to replay older experiments')
    if trajectory['pack_sha256'] != hashlib.sha256(PACK.read_bytes()).hexdigest():
        raise ValueError('Regional evidence/assumption pack changed')
    env = RegionalResponseEnv(max_decisions=trajectory['max_decisions'], ticks_per_action=trajectory['ticks_per_action'])
    observation, _ = env.reset(trajectory['scenario_id'], trajectory['seed'])
    if observation != trajectory['initial_observation']:
        raise ValueError('Regional initial observation differs')
    total = 0
    for recorded in trajectory['transitions']:
        result = env.step(recorded['action'])
        actual = dict(observation=result[0], reward=result[1], terminated=result[2], truncated=result[3], info=result[4])
        if actual != {key: recorded[key] for key in actual}:
            raise ValueError('Regional trajectory differs')
        total += result[1]
    if abs(total-trajectory['total_reward']) > 1e-9:
        raise ValueError('Regional total reward differs')
    return total


def run(output, scenario_id='regional-000', policy_name='cooperative', max_decisions=60):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh rollout directory')
    env = RegionalResponseEnv(max_decisions=max_decisions)
    observation, info = env.reset(scenario_id)
    policies = dict(cooperative=CooperativeBaseline, passive=lambda: passive,
                    random=lambda: RandomValidBaseline(env.seed), blanket_warning=lambda: blanket_warning)
    if policy_name not in policies:
        raise ValueError('Unknown regional baseline')
    policy = policies[policy_name]()
    trajectory = dict(version=VERSION, reward_version=REWARD_VERSION, scenario_id=scenario_id, seed=env.seed,
                      policy=policy_name, max_decisions=max_decisions, ticks_per_action=env.ticks_per_action,
                      initial_observation=observation, initial_info=info, transitions=[],
                      source_sha256=source_digest(),
                      pack_sha256=hashlib.sha256(PACK.read_bytes()).hexdigest())
    output.mkdir(parents=True)
    started = time.perf_counter()
    while True:
        action = policy(observation)
        observation, value, terminated, truncated, info = env.step(action)
        trajectory['transitions'].append(dict(action=action, observation=observation, reward=value,
            terminated=terminated, truncated=truncated, info=info))
        if terminated or truncated:
            break
    private = env.evaluator_state()
    trajectory['total_reward'] = sum(item['reward'] for item in trajectory['transitions'])
    verification = verify(trajectory, private)
    summary = dict(scenario=scenario_id, policy=policy_name, total_reward=trajectory['total_reward'],
                   components=private['components'], terminated=terminated, truncated=truncated,
                   decisions=env.decisions, model_minutes=env.sim.tick,
                   elapsed_seconds=round(time.perf_counter()-started, 3),
                   source_backed_facilities=3, assumed_inventory=True)
    if abs(trajectory['total_reward'] + private['cost']) > 1e-9:
        raise ValueError('Reward components do not account for the episode')
    dump(output/'trajectory.json.gz', trajectory)
    dump(output/'evaluator.json.gz', private)
    dump(output/'summary.json', summary)
    dump(output/'verification.json', verification)
    return summary


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', default='.runtime/regional-v2-demo')
    parser.add_argument('--scenario-id', default='regional-000')
    parser.add_argument('--policy', choices=['cooperative', 'passive', 'random', 'blanket_warning'], default='cooperative')
    parser.add_argument('--max-decisions', type=int, default=60)
    parser.add_argument('--replay')
    args = parser.parse_args()
    if args.replay:
        print(json.dumps(dict(replayed_reward=replay(load(args.replay)))))
    else:
        print(json.dumps(run(args.output, args.scenario_id, args.policy, args.max_decisions)))


if __name__ == '__main__':
    main()
