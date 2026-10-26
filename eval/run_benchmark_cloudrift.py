"""Bounded DeepSeek development evaluation on the delivered-evidence benchmark."""
import argparse
import getpass
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from urllib.request import urlopen

from envs.wildfire_response.director.policy import ProviderHTTPError
from envs.wildfire_response.regional.benchmark_v1.baselines import PublicDispatcher
from envs.wildfire_response.regional.benchmark_v1.model import BenchmarkCloudRiftPolicy
from envs.wildfire_response.regional.ops.cloudrift import BASE_URL, MODEL, CloudRiftTransport
from eval.run_benchmark import experiment_spec
from eval.run_ops_model import BoundedTransport, dump
from wildfire_core.benchmark_v1 import BenchmarkEnv


FAMILIES = ('uncertain_smoke', 'false_alarm', 'overlapping_fires')


def save_trace(directory, environment):
    directory.mkdir(parents=True, exist_ok=True)
    encoded = gzip.compress(json.dumps(environment.trajectory(), sort_keys=True, allow_nan=False).encode(), mtime=0)
    temporary = directory/'trajectory.json.gz.tmp'
    temporary.write_bytes(encoded)
    temporary.replace(directory/'trajectory.json.gz')
    dump(directory/'private'/'audit.json', environment.private_audit())
    return hashlib.sha256(encoded).hexdigest()


def render(summary, output):
    sections = []
    for episode in summary['episodes']:
        outcomes = episode.get('outcomes', {})
        metrics = {key: outcomes.get(key) for key in (
            'safe_success', 'protected_population_fraction', 'additional_burned_hectares', 'graded_score')}
        events = []
        for event in episode.get('timeline', []):
            events.append('<li><b>Minute '+str(event['minute'])+'</b> · '+html.escape(event['tool'])
                +('<strong> · Invalid action</strong>' if event['invalid'] else '')
                +'<details><summary>Action and feedback</summary><pre>'
                +html.escape(json.dumps(event, indent=2))+'</pre></details></li>')
        sections.append('<section><h2>'+html.escape(episode['family'])+'</h2><p>'
            +html.escape(f"{episode['status']} · {episode.get('decisions', 0)}/60 decisions · {episode.get('minute', 0)}/120 minutes")
            +'</p><p>'+html.escape(str(metrics) if episode.get('scored') else 'Unfinished or censored: no outcome score.')
            +'</p><details><summary>Matched CPU references (complete episodes)</summary><pre>'
            +html.escape(json.dumps(episode['baselines'], indent=2))+'</pre></details><ol>'+''.join(events)
            +'</ol><p><a href="'+html.escape(episode['task_id'])+'/model/trajectory.json.gz">Recorded trajectory</a> · '
            +'<a href="'+html.escape(episode['task_id'])+'/model/requests.json">Tool calls and rejected-response diagnostics</a></p></section>')
    (output/'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>DeepSeek benchmark trial</title>'
        '<style>body{font:16px/1.6 system-ui;background:#f6f4ee;color:#24362d;max-width:1050px;margin:auto;padding:32px}'
        'section{background:white;border:1px solid #d9d9ce;border-radius:12px;padding:24px;margin:24px 0}'
        'li{border-left:2px solid #708577;padding:12px 20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}'
        'strong{color:#a94432}summary{cursor:pointer}a{color:#356653}</style><h1>DeepSeek · new benchmark trial</h1><p>'
        +html.escape(f"{summary['model']} · {summary['status']} · {summary['requests']} provider attempts")
        +'</p><p>Development smoke test, not a held-out leaderboard. No training or scripted completion of model episodes. '
        'Exposure means synthetic exposure, not deaths. Incomplete runs have no outcome score.</p><pre>'
        +html.escape(json.dumps(summary.get('provider_budget', {}), indent=2))+'</pre>'
        +''.join(sections)+'<p><a href="summary.json">Full summary and provenance</a></p></html>')


def run(output, factory, transport, *, families=FAMILIES, max_requests=160, max_tokens=2048,
        seconds=1800, provider_trial=False):
    if (not families or len(set(families)) != len(families) or not set(families) <= set(FAMILIES)
            or type(max_requests) is not int or not 1 <= max_requests <= 240
            or type(max_tokens) is not int or not 256 <= max_tokens <= 4096
            or type(seconds) is not int or not 1 <= seconds <= 3600):
        raise ValueError('Choose bounded development cases and request/time/token limits')
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh output directory')
    probe = factory()
    cases = [next(case for case in probe.catalog['development'] if case['family'] == family) for family in families]
    experiment = experiment_spec(probe, 'development', ('passive', 'dispatcher'),
                                 [case['id'] for case in cases], False)
    root = Path(__file__).resolve().parents[1]
    sources = ('eval/run_benchmark_cloudrift.py', 'eval/run_ops_model.py',
        'envs/wildfire_response/core/src/envs/wildfire_response/regional/ops/cloudrift.py',
        'envs/wildfire_response/core/src/envs/wildfire_response/regional/ops/compact_model.py',
        'envs/wildfire_response/core/src/envs/wildfire_response/regional/director/policy.py',
        'envs/wildfire_response/core/src/envs/wildfire_response/regional/director/diagnostics.py')
    summary = dict(version='wildfire-benchmark-cloudrift-evaluation-v1', model=MODEL,
        mode='provider_trial' if provider_trial else 'injected_transport_test',
        contract_version=BenchmarkCloudRiftPolicy.contract_version, status='running',
        experiment=experiment, planned_families=list(families), episodes=[], requests=0,
        source_revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        source_sha256={name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources},
        max_requests=max_requests, max_tokens=max_tokens, seconds_limit=seconds,
        rank_eligible=False, training_eligible=False,
        sampling=dict(temperature=1., top_p=1., n=1, stream=False,
                      presence_penalty=0., frequency_penalty=0.), endpoint=BASE_URL)
    output.mkdir(parents=True, mode=0o700)
    dump(output/'plan.json', summary)
    bounded = BoundedTransport(transport, max_requests=max_requests, seconds=seconds,
                               interval=0, allow_nonzero_cost=True)
    started = time.monotonic()
    try:
        for case in cases:
            if bounded.calls >= max_requests or time.monotonic() >= bounded.deadline:
                raise RuntimeError('Global provider request/time budget exhausted')
            entry = dict(task_id=case['id'], seed=case['seed'], family=case['family'], status='running', scored=False,
                         baselines={}, timeline=[])
            summary['episodes'].append(entry)
            for name in ('passive', 'dispatcher'):
                reference = factory()
                observation, _ = reference.reset(case['id'])
                policy = PublicDispatcher(name)
                while True:
                    observation, _, terminated, truncated, _ = reference.step(policy(observation))
                    if terminated or truncated:
                        break
                verified = reference.verify_episode()
                if not verified['passed']:
                    raise RuntimeError('Reference replay verification failed')
                entry['baselines'][name] = verified['outcomes']
                save_trace(output/case['id']/name, reference)
            environment = factory()
            observation, feedback = environment.reset(case['id'])
            actor = BenchmarkCloudRiftPolicy(model=MODEL, transport=bounded, allow_cloudrift=True,
                interval=0, tool_mode='auto', max_calls=max_requests, max_tokens=max_tokens, decision_limit=60)
            directory = output/case['id']/'model'
            print(json.dumps(dict(event='starting', family=case['family'], task_id=case['id'])), flush=True)
            try:
                while True:
                    action = actor(observation, feedback=feedback, check_action=environment.check_orders)
                    observation, _, terminated, truncated, feedback = environment.step(action)
                    entry['timeline'].append(dict(minute=observation['minute'], decision=observation['decision'],
                        tool=actor.records[-1].get('tool', 'invalid_model_action'), action=action,
                        invalid=bool(feedback.get('error')), error=feedback.get('error')))
                    entry.update(minute=observation['minute'], decisions=observation['decision'])
                    save_trace(directory, environment)
                    dump(directory/'requests.json', actor.records)
                    summary.update(requests=bounded.calls, provider_budget=transport.budget_status()
                                   if hasattr(transport, 'budget_status') else {})
                    dump(output/'summary.json', summary)
                    print(json.dumps(dict(event='decision', family=case['family'], minute=observation['minute'],
                        tool=entry['timeline'][-1]['tool'], invalid=entry['timeline'][-1]['invalid'],
                        requests=bounded.calls)), flush=True)
                    if terminated or truncated:
                        break
                verification = environment.verify_episode()
                dump(directory/'private'/'verification.json', verification)
                if not verification['passed']:
                    raise RuntimeError('Model trajectory replay verification failed')
                entry.update(status='complete', scored=verification['outcomes']['score_eligible'],
                    outcomes=verification['outcomes'], terminated=terminated, truncated=truncated)
            except BaseException:
                entry.update(status='incomplete', scored=False)
                raise
            finally:
                entry.update(trajectory_sha256=save_trace(directory, environment),
                    minute=observation['minute'], decisions=observation['decision'],
                    invalid_actions=sum(record['invalid'] for record in entry['timeline']),
                    protocol_errors=sum(bool(record.get('protocol_error')) for record in actor.records),
                    served_models=sorted({record['served_model'] for record in actor.records if record.get('served_model')}))
                dump(directory/'requests.json', actor.records)
                dump(directory/'status.json', entry)
        summary['status'] = 'complete'
    except (Exception, KeyboardInterrupt) as error:
        summary.update(status='stopped', stop_reason=dict(error_type=type(error).__name__,
                       http_status=getattr(error, 'status_code', None)))
        known_stops = (
            'Conservative CloudRift estimated cost cap reached before next request',
            'CloudRift estimated usage exceeded cap; no further calls allowed',
            'CloudRift request failed; no retry or provider fallback attempted',
            'Global provider request/time budget exhausted',
            'Global provider deadline exhausted',
            'Regional director request budget exhausted; episode is incomplete',
        )
        if str(error) in known_stops:
            summary['stop_reason']['reason'] = str(error)
        if isinstance(error, ProviderHTTPError) and getattr(error, 'safe_message', None):
            summary['stop_reason']['provider_message'] = error.safe_message
    finally:
        summary.update(requests=bounded.calls, elapsed_seconds=round(time.monotonic()-started, 3),
                       provider_budget=transport.budget_status() if hasattr(transport, 'budget_status') else {})
        dump(output/'summary.json', summary)
        render(summary, output)
    return summary


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ('output', 'prepared-inputs', 'prepared-sha256', 'topographic-inputs', 'topographic-sha256'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--allow-cloudrift', action='store_true', required=True)
    parser.add_argument('--key-stdin', action='store_true')
    parser.add_argument('--usd-cap', type=float, required=True)
    parser.add_argument('--max-requests', type=int, default=160)
    parser.add_argument('--max-tokens', type=int, default=2048)
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--request-timeout', type=int, default=180)
    args = parser.parse_args()
    if not 0 < args.usd_cap <= 5 or not 30 <= args.request_timeout <= 180:
        parser.error('Use a positive cap of at most5USD and a30–180second request timeout')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    with urlopen(BASE_URL+'/models', timeout=25) as response:
        metadata = next((model for model in json.load(response)['data'] if model.get('id') == MODEL), None)
    if metadata is None:
        parser.error('Requested model unavailable; no substitution')
    key = getpass.getpass('CloudRift inference key (hidden): ') if args.key_stdin else os.environ.get('CLOUDRIFT_INFERENCE_KEY')
    transport = CloudRiftTransport(api_key=key, model_metadata=metadata, usd_cap=args.usd_cap,
                                   timeout=args.request_timeout)
    del key
    def factory():
        return BenchmarkEnv(args.prepared_inputs, args.topographic_inputs,
            expected_prepared_sha256=args.prepared_sha256, expected_topographic_sha256=args.topographic_sha256,
            slope_enabled=True, allow_synthetic_conditions=True)
    result = run(args.output, factory, transport, max_requests=args.max_requests,
                 max_tokens=args.max_tokens, seconds=args.seconds, provider_trial=True)
    print(json.dumps({key: result[key] for key in ('status', 'requests', 'provider_budget')}), flush=True)


if __name__ == '__main__':
    main()
