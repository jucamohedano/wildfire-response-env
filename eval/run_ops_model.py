"""Bounded current-contract development smoke; never a held-out training run."""
import argparse
import atexit
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import time
from urllib.request import urlopen

from envs.wildfire_response.director.policy import OpenRouterTransport, ProviderHTTPError
from envs.wildfire_response.regional.ops.model import OpsModelPolicy, CONTRACT_VERSION
from wildfire_core.topographic import TopographicEnv, ProtectionBaseline


CASES = ('topo-dev-001', 'topo-dev-000', 'topo-dev-003', 'topo-dev-004')


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n')
    temporary.replace(path)


def read_key(path=None):
    if path is None:
        value = os.environ.get('OR_API_KEY')
        if not value:
            raise ValueError('OR_API_KEY is not available')
        return value
    values = []
    for line in Path(path).expanduser().read_text().splitlines():
        match = re.fullmatch(r'\s*(?:export\s+)?OR_API_KEY\s*=\s*(.*)', line)
        if match:
            words = shlex.split(match.group(1), comments=True)
            if len(words) != 1:
                raise ValueError('Invalid OR_API_KEY assignment; secret not printed')
            values.append(words[0])
    if len(values) != 1 or not values[0]:
        raise ValueError('Expected one OR_API_KEY assignment; secret not printed')
    return values[0]


class BoundedTransport:
    def __init__(self, transport, *, max_requests=160, seconds=1200, interval=3.2):
        self.transport = transport
        self.max_requests = max_requests
        self.deadline = time.monotonic()+seconds
        self.interval = interval
        self.calls = 0
        self.last_call = None
        self.costs = []

    def __call__(self, payload):
        if self.calls >= self.max_requests or time.monotonic() >= self.deadline:
            raise RuntimeError('Global provider request/time budget exhausted')
        if self.last_call is not None:
            time.sleep(max(0, self.interval-(time.monotonic()-self.last_call)))
        if time.monotonic() >= self.deadline:
            raise RuntimeError('Global provider deadline exhausted')
        self.calls += 1
        self.last_call = time.monotonic()
        response = self.transport(payload)
        if response.get('error'):
            code = response['error'].get('code') if isinstance(response['error'], dict) else None
            raise ProviderHTTPError(code if type(code) is int else 502)
        if any(choice.get('finish_reason') == 'error' for choice in response.get('choices', [])):
            raise ProviderHTTPError(502)
        usage = response.get('usage', {})
        if usage.get('cost') is not None:
            cost = float(usage['cost'])
            if not math.isfinite(cost) or cost != 0:
                raise RuntimeError('Unexpected nonzero/invalid provider cost; stopping')
            self.costs.append(cost)
        return response


def stop_preview(pid_file):
    if pid_file is None:
        return 'not_requested'
    path = Path(pid_file)
    if not path.exists():
        return 'already_stopped'
    pid = int(path.read_text().strip())
    proc = Path('/proc') / str(pid)
    if not proc.exists():
        return 'already_stopped'
    arguments = (proc/'cmdline').read_bytes().split(b'\0')
    if b'eval.ui_server' not in arguments or (proc/'cwd').resolve() != Path(__file__).resolve().parents[1]:
        return 'refused_unverified_process'
    os.kill(pid, signal.SIGTERM)
    for attempt in range(50):
        if not proc.exists():
            return 'stopped'
        time.sleep(.1)
    return 'termination_pending'


def run(output, factory, transport, *, model, max_requests=160, max_tokens=2048,
        seconds=1200, interval=3.2, preview_pid=None, provider_trial=False):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh output directory')
    output.mkdir(parents=True)
    bounded = BoundedTransport(transport, max_requests=max_requests, seconds=seconds, interval=interval)
    summary = dict(mode='provider_trial' if provider_trial else 'injected_transport_test',
        status='running', model=model, contract_version=CONTRACT_VERSION, cases_planned=list(CASES),
        scope='Four development fixtures on one source region; not held-out evaluation or training',
        max_provider_requests=max_requests, max_tokens=max_tokens, episodes=[])
    root = Path(__file__).resolve().parents[1]
    summary['source_revision'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    source_files = ['eval/run_ops_model.py',
        'envs/wildfire_response/core/src/envs/wildfire_response/regional/ops/model.py',
        'envs/wildfire_response/core/src/envs/wildfire_response/regional/director/policy.py']
    summary['source_sha256'] = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in source_files}
    started = time.monotonic()
    try:
        for task_id in CASES:
            if bounded.calls >= max_requests or time.monotonic() >= bounded.deadline:
                summary.update(status='stopped', stop_reason=dict(error_type='GlobalBudgetExhausted', http_status=None))
                break
            case = output/task_id
            baseline_results = {}
            for name in ('passive', 'scripted_evacuation'):
                env = factory()
                observation, info = env.reset(task_id)
                baseline = ProtectionBaseline(prefer_shelter=False)
                while True:
                    action = {} if name == 'passive' else baseline(observation)
                    observation, reward, terminated, truncated, info = env.step(action)
                    if terminated or truncated:
                        break
                verified = env.verify_episode()
                baseline_results[name] = dict(score=env.reward, by_kind=verified['objective_result']['by_kind'],
                    boundary_censored=verified['objective_result'].get('boundary_censored', False))
                dump(case/name/'trajectory.json', env.trajectory())
            env = factory()
            observation, info = env.reset(task_id)
            actor = OpsModelPolicy(model=model, transport=bounded, interval=0, tool_mode='auto',
                max_calls=max_requests, max_tokens=max_tokens, decision_limit=60)
            entry = dict(task_id=task_id, status='running', scored=False, baselines=baseline_results,
                reward_version=env.trajectory()['reward_version'], reward_weights=observation['reward_weights'])
            summary['episodes'].append(entry)
            print(json.dumps(dict(event='starting', task_id=task_id, requests_used=bounded.calls)), flush=True)
            try:
                while True:
                    action = actor(observation, feedback=info, check_action=env.check_orders)
                    observation, reward, terminated, truncated, info = env.step(action)
                    dump(case/'partial-trajectory.json', env.trajectory())
                    dump(case/'requests.json', actor.records)
                    print(json.dumps(dict(event='decision', task_id=task_id, minute=observation['minute'],
                        requests_used=bounded.calls, action_error=info['error'])), flush=True)
                    if terminated or truncated:
                        break
                verified = env.verify_episode()
                dump(case/'private'/'verification.json', verified)
                dump(case/'trajectory.json', env.trajectory())
                entry.update(status='complete', scored=True, score=env.reward,
                    reward_version=info['reward_version'], reward_weights=info['reward_weights'],
                    by_kind=verified['objective_result']['by_kind'],
                    all_objectives_met=verified['objective_result']['all_objectives_met'],
                    boundary_censored=verified['objective_result'].get('boundary_censored', False),
                    model_minutes=observation['minute'], decisions=observation['decision'],
                    terminated=terminated, truncated=truncated,
                    invalid_actions=env._core.counters['invalid_actions'],
                    requests=actor.calls, protocol_errors=sum(record['protocol_error'] is not None for record in actor.records),
                    served_models=sorted({record['served_model'] for record in actor.records if record.get('served_model')}),
                    demonstration_eligible=False)
            except Exception as error:
                dump(case/'partial-trajectory.json', env.trajectory())
                dump(case/'requests.json', actor.records)
                entry.update(status='incomplete', error_type=type(error).__name__, requests=actor.calls,
                    decisions=observation['decision'], scored=False)
                if isinstance(error, ProviderHTTPError):
                    entry['http_status'] = error.status_code
                    if error.safe_message:
                        entry['provider_message'] = error.safe_message
                summary['status'] = 'stopped'
                summary['stop_reason'] = dict(error_type=type(error).__name__, http_status=getattr(error, 'status_code', None))
                print(json.dumps(dict(event='stopping', **summary['stop_reason'])), flush=True)
                break
            finally:
                dump(case/'status.json', entry)
                dump(output/'summary.json', summary)
            print(json.dumps(dict(event='completed', **entry)), flush=True)
        else:
            summary['status'] = 'complete'
    except BaseException as error:
        summary.update(status='stopped', stop_reason=dict(error_type=type(error).__name__, http_status=None))
        raise
    finally:
        summary.update(requests=bounded.calls, reported_cost=sum(bounded.costs) if bounded.costs else None,
            cost_reported_responses=len(bounded.costs), seconds=round(time.monotonic()-started, 3),
            preview_cleanup=stop_preview(preview_pid))
        dump(output/'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--prepared-inputs', required=True)
    parser.add_argument('--prepared-sha256', required=True)
    parser.add_argument('--topographic-inputs', required=True)
    parser.add_argument('--topographic-sha256', required=True)
    parser.add_argument('--model', default='thinkingmachines/inkling:free')
    parser.add_argument('--key-file')
    parser.add_argument('--max-requests', type=int, default=160)
    parser.add_argument('--max-tokens', type=int, default=2048)
    parser.add_argument('--seconds', type=int, default=1200)
    parser.add_argument('--stop-preview-pid-file')
    parser.add_argument('--allow-provider', action='store_true', required=True)
    args = parser.parse_args()
    atexit.register(stop_preview, args.stop_preview_pid_file)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    if not 1 <= args.max_requests <= 160 or not 256 <= args.max_tokens <= 4096 or not 1 <= args.seconds <= 1800:
        parser.error('Invalid bounded evaluation budget')
    with urlopen('https://openrouter.ai/api/v1/models', timeout=25) as response:
        models = json.load(response)['data']
    selected = next((item for item in models if item['id'] == args.model), None)
    if (selected is None or not args.model.endswith(':free') or 'tools' not in selected.get('supported_parameters', [])
            or any(float(selected['pricing'].get(field, -1)) != 0 for field in ('prompt', 'completion'))):
        parser.error('Choose a currently listed zero-price tool-capable :free model')
    transport = OpenRouterTransport(api_key=read_key(args.key_file), timeout=90)
    def factory():
        return TopographicEnv(args.prepared_inputs, args.topographic_inputs,
            expected_prepared_sha256=args.prepared_sha256, expected_topographic_sha256=args.topographic_sha256,
            slope_enabled=True, allow_synthetic_conditions=True)
    result = run(args.output, factory, transport, model=args.model, max_requests=args.max_requests,
        max_tokens=args.max_tokens, seconds=args.seconds, preview_pid=args.stop_preview_pid_file, provider_trial=True)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
