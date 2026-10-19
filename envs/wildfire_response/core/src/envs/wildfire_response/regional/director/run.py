"""Explicitly gated regional model trials and CPU-only scripted protocol smoke tests."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from ...director.policy import DEFAULT_MODEL, ProviderHTTPError
from ...eval.run import dump, source_digest
from ..benchmark import verify_lock
from ..data import PACK
from ..environment import RegionalResponseEnv, REWARD_VERSION, VERSION
from ..run import replay, run as run_baseline
from ..scenarios import get_scenario
from ..verifier import verify
from .contract import CONTRACT_VERSION
from .policy import RegionalDirectorPolicy, ScriptedTransport


def run(output, scenario_id='regional-train-001', decisions=8, max_requests=16, model=DEFAULT_MODEL,
        max_tokens=4096, transport=None, allow_provider=False, interval=3.1, tool_mode='forced'):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh trial directory')
    if type(decisions) is not int or not 1 <= decisions <= 60:
        raise ValueError('Use a decision budget from 1 to 60')
    benchmark = verify_lock()
    task = get_scenario(scenario_id)
    if task.get('split') != 'train':
        raise ValueError('Director development uses training tasks only; held-out model trials require a separate protocol')
    policy = RegionalDirectorPolicy(model=model, max_calls=max_requests, max_tokens=max_tokens,
        transport=transport, allow_provider=allow_provider, interval=interval, tool_mode=tool_mode,
        decision_limit=decisions)
    mode = 'scripted_protocol_test' if isinstance(transport, ScriptedTransport) else (
        'injected_transport_test' if transport is not None else 'provider_trial')
    env = RegionalResponseEnv(max_decisions=decisions)
    observation, info = env.reset(scenario_id)
    trajectory = dict(version=VERSION, reward_version=REWARD_VERSION, scenario_id=scenario_id,
        seed=env.seed, split='train', policy='regional_director', mode=mode, model=model,
        contract_version=CONTRACT_VERSION, tool_mode=tool_mode, benchmark=benchmark,
        max_decisions=decisions, ticks_per_action=env.ticks_per_action,
        initial_observation=observation, initial_info=info, transitions=[], source_sha256=source_digest(),
        pack_sha256=hashlib.sha256(PACK.read_bytes()).hexdigest())
    output.mkdir(parents=True)
    started = time.perf_counter()
    try:
        while True:
            action = policy(observation, feedback=info, check_action=env.check_action)
            observation, reward, terminated, truncated, info = env.step(action)
            trajectory['transitions'].append(dict(action=action, observation=observation, reward=reward,
                terminated=terminated, truncated=truncated, info=info))
            if terminated or truncated:
                break
    except Exception as error:
        dump(output/'partial-trajectory.json.gz', trajectory)
        dump(output/'requests.json', policy.records)
        status = dict(status='incomplete', mode=mode, requests=policy.calls, decisions=env.decisions,
                      error_type=type(error).__name__, scored=False)
        if isinstance(error, ProviderHTTPError):
            status['http_status'] = error.status_code
        dump(output/'status.json', status)
        raise
    trajectory['total_reward'] = sum(item['reward'] for item in trajectory['transitions'])
    private = env.evaluator_state()
    verification = verify(trajectory, private)
    replayed = replay(trajectory)
    (output/'private').mkdir()
    dump(output/'trajectory.json.gz', trajectory)
    dump(output/'private'/'evaluator.json.gz', private)
    dump(output/'requests.json', policy.records)
    dump(output/'verification.json', verification)
    baseline = run_baseline(output/'cooperative', scenario_id, 'cooperative', decisions)
    summary = dict(status='complete', mode=mode, benchmark=benchmark, benchmark_budget_matched=decisions==60,
        scenario_id=scenario_id, requested_model=model,
        served_models=sorted({item['served_model'] for item in policy.records if isinstance(item.get('served_model'), str)}),
        max_decisions=decisions, max_requests=max_requests, max_tokens=max_tokens,
        requests=policy.calls, action_checks=sum(item.get('tool')=='check_action' for item in policy.records),
        protocol_errors=sum(item['protocol_error'] is not None for item in policy.records),
        total_reward=trajectory['total_reward'], replayed_reward=replayed, components=private['components'],
        terminated=terminated, truncated=truncated, independent_verification=verification['passed'],
        cooperative_reward=baseline['total_reward'], elapsed_seconds=round(time.perf_counter()-started, 3),
        note='Scripted/injected transports measure protocol correctness, never model performance; no training or publication')
    dump(output/'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--scenario-id', default='regional-train-001')
    parser.add_argument('--max-decisions', type=int, default=8)
    parser.add_argument('--max-requests', type=int, default=16)
    parser.add_argument('--max-tokens', type=int, default=4096)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--tool-mode', choices=['forced', 'auto'], default='forced')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--scripted', action='store_true', help='CPU-only frozen baseline exercising the model tool protocol')
    mode.add_argument('--allow-provider', action='store_true', help='Explicitly enable bounded free-provider requests')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.output, args.scenario_id, args.max_decisions, args.max_requests,
            args.model, args.max_tokens, transport=ScriptedTransport() if args.scripted else None,
            allow_provider=args.allow_provider, interval=0 if args.scripted else 3.1, tool_mode=args.tool_mode)))
    except (RuntimeError, ValueError) as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
