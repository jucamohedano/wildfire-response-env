"""Run pinned synthetic evaluation candidates through the v3 public API.

Private catalog, prehistory, events, reports and audits never enter this report.
This is a development qualification run, not a held-out benchmark score or a
claim of operational calibration. Truncated, censored and interrupted cases do
not count as passing. Sources and data must remain immutable throughout a run.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import signal
import time

from eval.hub_runtime import make_environment, verify_package
from eval.incident_day_policies import POLICY_NAMES, make_policy
from eval.incident_weather_runtime_v1 import IncidentWeatherRuntimeV1
from eval.validate_incident_day_runtime import PublicFacade, commit
from wildfire_control_v3 import _capture_source
from wildfire_control_v3.population_engine_v3 import PopulationEngineV3

_MODULE_NAME = __spec__.name if __spec__ else __name__
_RUNNER_SHA, _RUNNER_MATCH = _capture_source(_MODULE_NAME, __file__, __loader__)


def _file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(data_dir, pack_dir, output, *, expected_lock_sha256, expected_catalog_sha256,
        policy='passive', limit=None, wall_seconds=1800):
    if policy not in POLICY_NAMES or type(wall_seconds) is not int or wall_seconds <= 0:
        raise ValueError('Invalid policy or budget')
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('Invalid case limit')
    pack, output = Path(pack_dir), Path(output)
    if not _RUNNER_MATCH or _file_sha(__file__) != _RUNNER_SHA:
        raise ValueError('Qualification source differs from loaded code')
    if output.exists():
        raise ValueError('Use a fresh output file')
    catalog = json.loads((pack/'catalog.json').read_text())
    lock = json.loads((pack/'lock.json').read_text())
    if (not re.fullmatch(r'[0-9a-f]{64}', expected_lock_sha256 or '') or
            not re.fullmatch(r'[0-9a-f]{64}', expected_catalog_sha256 or '') or
            _file_sha(pack/'lock.json') != expected_lock_sha256 or
            catalog.get('sha256') != expected_catalog_sha256):
        raise ValueError('Catalog or lock differs from independently supplied pins')
    if lock['version'] != 'wildfire-incident-eval-lock-v1':
        raise ValueError('Invalid evaluation lock')
    if _file_sha(Path(__file__).with_name('generate_incident_evaluation_v1.py')) != lock['generator_sha256']:
        raise ValueError('Generator changed after catalog lock')
    verify_package(data_dir)
    if _file_sha(Path(data_dir)/'manifest.json') != lock['dataset_manifest_sha256']:
        raise ValueError('Dataset differs from catalog lock')
    expected_ids = [f'incident-eval-v1-{index:03}' for index in range(12)]
    if [s['id'] for s in catalog['cases']] != expected_ids or lock['case_ids'] != expected_ids:
        raise ValueError('Case identities differ from catalog lock')
    runtime = IncidentWeatherRuntimeV1(catalog, expected_pins=lock['runtime_pins'])
    if runtime.pins != lock['runtime_pins']:
        raise ValueError('Runtime differs from catalog lock')
    base, _ = make_environment(data_dir, 'es-ma-2021', allow_unapproved_data=True)
    def check_sources():
        if (_file_sha(__file__) != _RUNNER_SHA or _file_sha(pack/'lock.json') != expected_lock_sha256
                or _file_sha(Path(__file__).with_name('generate_incident_evaluation_v1.py')) != lock['generator_sha256']
                or _file_sha(Path(__file__).with_name('incident_day_policies.py')) != lock['policy_sha256']
                or _file_sha(Path(__file__).with_name('validate_incident_day_runtime.py')) != lock['facade_sha256']
                or _file_sha(Path(data_dir)/'manifest.json') != lock['dataset_manifest_sha256']):
            raise ValueError('Qualification source, lock or dataset changed')
    check_sources()
    start = time.monotonic()
    result = dict(version='wildfire-eval-qualification-v1',status='incomplete',policy=policy,
        catalog_sha256=catalog['sha256'], generator_sha256=lock['generator_sha256'],
        dataset_manifest_sha256=lock['dataset_manifest_sha256'], runner_sha256=_RUNNER_SHA,
        runtime_pins=lock['runtime_pins'], cases=[],
        scope='Synthetic development qualification on one source-map context; not a held-out benchmark.',
        limitations=['Estimated inputs and uncalibrated weather/fire effects.',
                     'Private events, actor reports and evaluator audits excluded.',
                     'A truncated, censored or unfinished day is not a pass.'])
    stopped = False
    def stop(signum, frame):
        nonlocal stopped
        stopped = True
    def over_budget():
        return stopped or time.monotonic()-start >= wall_seconds
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    def publish():
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix(output.suffix+'.tmp')
        tmp.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
        tmp.replace(output)
    try:
        publish()
        with runtime:
            for scenario in catalog['cases'][:limit]:
                check_sources()
                if over_budget():
                    result['reason'] = 'budget_or_interrupt'; break
                env = PopulationEngineV3(base.inputs, base.network.document,
                    expected_inputs_sha256=base.inputs['sha256'], expected_network_sha256=base.network.sha256,
                    allow_unapproved_data=True)
                observation, _ = env.reset(scenario['id'], scenario['seed'])
                decision_policy, calls = make_policy(policy), []
                public = PublicFacade(env, calls)
                while not (env.terminated or env.truncated):
                    if over_budget():
                        result['reason'] = 'budget_or_interrupt'; break
                    action = decision_policy.action(public, observation)
                    outcome = commit(public, action)
                    if over_budget():
                        result['reason'] = 'budget_or_interrupt'; break
                    if 'observation' not in outcome:
                        raise ValueError('Public policy action failed')
                    observation = outcome['observation']
                    # Only already delivered evidence may be visible to actors.
                    if any(record['received_at'] > observation['minute']
                           or record['observed_at'] > record['received_at']
                           for record in observation['reports']):
                        raise ValueError('Observation chronology differs')
                if over_budget() or not (env.terminated or env.truncated):
                    break
                verification = env.verify_episode()
                if over_budget():
                    result['reason'] = 'budget_or_interrupt'; break
                valid = bool(env.terminated and not env.truncated and env.minute == 1440 and
                    verification.get('passed') is True and verification.get('exact_replay') is True and
                    verification.get('independent_reward_agreement') is True)
                reason = ('input_uncertainty' if env._input_censored else
                          'domain_boundary' if env.censored else
                          'invalid_abandonment' if env.abandoned else
                          'truncated' if env.truncated else None)
                result['cases'].append(dict(id=scenario['id'], status='passed' if valid else 'not_passed',
                    minute=env.minute, terminated=env.terminated, truncated=env.truncated,
                    reason=reason,
                    exact_replay=verification.get('exact_replay') is True,
                    independent_reward=verification.get('independent_reward_agreement') is True))
                publish()
            if limit is not None and limit < len(catalog['cases']):
                result['reason'] = 'selected_subset'
        # Never publish success until the context's final integrity checks have
        # succeeded and all locked inputs have been rechecked.
        check_sources()
        verify_package(data_dir)
        if over_budget():
            result['reason'] = 'budget_or_interrupt'
        elif (len(result['cases']) == len(expected_ids) and
              all(case['status'] == 'passed' for case in result['cases'])):
            result['status'] = 'passed'
        publish()
    except BaseException:
        result['status'] = 'incomplete'
        result['reason'] = 'validation_error'
        publish()
        raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--pack-dir', required=True)
    parser.add_argument('--expected-lock-sha256', required=True)
    parser.add_argument('--expected-catalog-sha256', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--policy', choices=POLICY_NAMES, default='passive')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--wall-seconds', type=int, default=1800)
    args = parser.parse_args()
    report = run(args.data_dir,args.pack_dir,args.output,expected_lock_sha256=args.expected_lock_sha256,
                 expected_catalog_sha256=args.expected_catalog_sha256,policy=args.policy,
                 limit=args.limit,wall_seconds=args.wall_seconds)
    print(json.dumps(dict(status=report['status'],reason=report.get('reason'),cases=len(report['cases']))))
    return 0 if report['status']=='passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
