"""Run an explicitly unapproved synthetic-fuel sensitivity on real geography."""
import argparse
import json
import math
from pathlib import Path
import re
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
from envs.wildfire_response.incident_day.data import _check_hash, _unique, load_inputs, prepare_inputs
from envs.wildfire_response.incident_day.environment import IncidentDayEnv, implementation_digest
from envs.wildfire_response.incident_day.scenarios import CASE_IDS

PREPARED_PIN = 'ab9d649730b6033c5d3f5ad2d01b096b85b767f21f18bd4341c44d791646d108'
DOMAIN_PIN = 'e1fdba3d88b4b75db7048d84ab24cdd40c387d6240b9bec2528f0f71a68666fd'
NETWORK_PIN = '12782cb4ccb4bbfd2d59eaaa7380ad6b1a807320cf5a15b6d01e44d7ec89fc16'
CORE_PIN = '03e4fc4d2957e37a168f53183399347b27e0adc107d857dee18c44df772e48d5'
CLASSES = (1, 4, 8)


def read_document(path, expected):
    with Path(path).open('rb') as stream:
        raw = stream.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError('Input document exceeds 10 MB')
    document = json.loads(raw, object_pairs_hook=_unique)
    if _check_hash(document, str(path)) != expected:
        raise ValueError(f'Input document pin mismatch: {path}')
    return document


def compile_assumptions(prepared, domain, network, expected_unknown=80):
    packs = []
    for fuel_class in CLASSES:
        pack = prepare_inputs(prepared, domain, network,
                              fuel_resolution={'kind': 'synthetic', 'class': fuel_class})
        load_inputs(pack, pack['sha256'], allow_unapproved=True)
        if pack['approval_status'] != 'unapproved_source_pack':
            raise ValueError('Sensitivity pack must remain unapproved')
        unknown = [(row, column) for row, values in enumerate(pack['fuel_known_mask'])
                   for column, known in enumerate(values) if not known]
        if len(unknown) != expected_unknown:
            raise ValueError(f'Expected {expected_unknown} unknown cells, found {len(unknown)}')
        if any(pack['fuel_classes'][row][column] != fuel_class for row, column in unknown):
            raise ValueError('Synthetic fallback differs from declared class')
        packs.append(pack)
    return packs


def write_report(output, report):
    (output / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n')


def run(runtime, output, wall_budget=120, *, expected_core_sha256=CORE_PIN, verify_episode=False):
    if (type(wall_budget) not in (int, float) or not math.isfinite(wall_budget)
            or not 0 < wall_budget <= 120):
        raise ValueError('Wall budget must be greater than zero and at most 120 seconds')
    if type(expected_core_sha256) is not str or not re.fullmatch(r'[0-9a-f]{64}', expected_core_sha256):
        raise ValueError('Expected core SHA-256 must be 64 lowercase hexadecimal characters')
    if type(verify_episode) is not bool:
        raise ValueError('Replay verification flag must be boolean')
    started = time.monotonic()
    output = Path(output)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    report = {'version': 'incident-day-fuel-sensitivity-v1', 'status': 'incomplete',
              'label': 'UNAPPROVED sensitivity only; not benchmark, training, or historical replay',
              'source_pack_approval': 'blocked', 'workers': 1, 'policy': 'passive',
              'scenario': CASE_IDS[0], 'wall_budget_seconds': wall_budget,
              'core_sha256': expected_core_sha256, 'prepared_sha256': PREPARED_PIN,
              'domain_sha256': DOMAIN_PIN, 'network_sha256': NETWORK_PIN,
              'assumption_classes': list(CLASSES), 'verification_requested': verify_episode, 'results': []}
    write_report(output, report)

    def timeout_handler(signum, frame):
        raise TimeoutError(f'{wall_budget}-second sensitivity budget exhausted')

    previous_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, wall_budget)
    try:
        if implementation_digest() != expected_core_sha256:
            raise ValueError('Frozen incident-day implementation digest differs from expected pin')
        runtime = Path(runtime)
        prepared = read_document(runtime / 'malaga-prepared-simulation-v1/malaga-prepared-inputs-v1/inputs.json', PREPARED_PIN)
        domain = read_document(runtime / 'topographic-experiment-v1/topographic-inputs-v1/inputs.json', DOMAIN_PIN)
        network = read_document(ROOT / 'data/coastal_mobility_v1/network.json', NETWORK_PIN)
        packs = compile_assumptions(prepared, domain, network)
        for fuel_class, pack in zip(CLASSES, packs):
            result = {'synthetic_fuel_class': fuel_class, 'input_sha256': pack['sha256'],
                      'approval_status': pack['approval_status'], 'unknown_source_cells': 80,
                      'status': 'running', 'graded': False}
            report['results'].append(result)
            write_report(output, report)
            environment = IncidentDayEnv(pack, network, expected_inputs_sha256=pack['sha256'],
                                         expected_network_sha256=NETWORK_PIN, allow_unapproved_data=True)
            environment.reset(CASE_IDS[0])
            while not environment.terminated and not environment.truncated:
                environment.step({'advance_minutes': 60})
            outcome = environment.outcomes()
            result.update(completed_minutes=environment.minute, boundary_censored=environment.censored,
                          inherited_burned_hectares=outcome['inherited_burned_hectares'],
                          additional_burned_hectares=outcome['additional_burned_hectares'],
                          new_exposed_population=outcome['new_exposed_population'])
            if verify_episode:
                result['verification'] = 'running'
                write_report(output, report)
                verification = environment.verify_episode()
                if (verification.get('passed') is not True or verification.get('exact_replay') is not True
                        or verification.get('independent_reward_agreement') is not True):
                    raise ValueError('Episode replay verification failed')
                result['verification'] = 'passed'
            else:
                result['verification'] = 'not_requested'
            result['status'] = ('boundary_censored' if environment.censored else
                                ('complete_ungraded' if environment.terminated else 'truncated_ungraded'))
            write_report(output, report)
        if implementation_digest() != expected_core_sha256:
            raise ValueError('Frozen incident-day implementation changed during run')
        report['status'] = 'complete_unapproved_sensitivity'
    except TimeoutError as error:
        if report['results'] and report['results'][-1]['status'] == 'running':
            report['results'][-1]['status'] = 'interrupted_ungraded'
            if report['results'][-1].get('verification') == 'running':
                report['results'][-1]['verification'] = 'interrupted'
        report.update(status='incomplete', error=f'{type(error).__name__}: {error}')
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        if report['results'] and report['results'][-1]['status'] == 'running':
            report['results'][-1]['status'] = 'failed_ungraded'
            if report['results'][-1].get('verification') == 'running':
                report['results'][-1]['verification'] = 'failed'
        report.update(status='incomplete', error=f'{type(error).__name__}: {error}')
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        write_report(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='fresh output directory')
    parser.add_argument('--wall-budget', type=float, default=120)
    parser.add_argument('--expected-core-sha256', default=CORE_PIN, help='caller-held frozen implementation pin')
    parser.add_argument('--verify-episode', action='store_true', help='independently replay each finished or censored case')
    options = parser.parse_args()
    report = run(options.runtime, options.output, options.wall_budget,
                 expected_core_sha256=options.expected_core_sha256, verify_episode=options.verify_episode)
    print(json.dumps({'status': report['status'], 'error': report.get('error'), 'output': str(options.output)}))
    return 0 if report['status'] == 'complete_unapproved_sensitivity' else 1


if __name__ == '__main__':
    sys.exit(main())
