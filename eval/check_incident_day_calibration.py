"""Run bounded synthetic mechanics and paired development-case audits on CPU."""
import argparse
import gzip
import html
import json
import math
import multiprocessing
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_response/core/src'))
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.baselines import DayDispatcher
from envs.wildfire_response.incident_day.calibration import run_probes
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.scenarios import CASE_IDS

VERSION = 'incident-day-calibration-report-v2'


def _episode(case_id, policy, private, connection):
    try:
        inputs, network = fixture_inputs()
        env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
        observation, _ = env.reset(case_id)
        dispatcher = DayDispatcher(policy)
        while not (env.terminated or env.truncated):
            observation, _, _, _, _ = env.step(dispatcher.action(env, observation))
        verification = env.verify_episode()
        if not all(verification.get(field) is True for field in ('passed', 'exact_replay', 'independent_reward_agreement')):
            raise ValueError('Replay or independent reward check failed')
        trajectory = env.trajectory()
        with gzip.open(private / (case_id + '-' + policy + '.json.gz'), 'wt') as stream:
            json.dump(dict(trajectory=trajectory, audit=env._audit), stream, allow_nan=False)
        outcome = verification['outcomes']
        orders = [(transition['start_minute'], transition['action']['protection_orders'])
                  for transition in trajectory['transitions'] if transition['action'].get('protection_orders')]
        connection.send(dict(case=case_id, policy=policy, status='completed' if env.terminated else 'censored',
                             minute=env.minute, seed=trajectory['seed'], inputs_sha256=trajectory['inputs_sha256'],
                             network_sha256=trajectory['network_sha256'], scenario_sha256=trajectory['scenario_sha256'],
                             implementation_sha256=trajectory['implementation_sha256'], exact_replay=True,
                             independent_reward_agreement=True, new_exposed_population=outcome['new_exposed_population'],
                             additional_burned_hectares=outcome['additional_burned_hectares'],
                             unresolved_evacuations=outcome['unresolved_evacuations'], protection_orders=orders))
    except BaseException:
        connection.send(dict(case=case_id, policy=policy, status='error', error=traceback.format_exc()))
    finally:
        connection.close()


def run(output, cases, wall_budget):
    if not cases or any(case not in CASE_IDS for case in cases):
        raise ValueError('Use incident-day development case IDs')
    if not math.isfinite(wall_budget) or wall_budget <= 0 or wall_budget > 600:
        raise ValueError('Wall budget must be greater than 0 and at most 600 seconds')
    output = Path(output).absolute()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    private = output / 'private'
    private.mkdir(mode=0o700)
    started = time.monotonic()
    report = dict(version=VERSION, data_scope='synthetic development cases; not held-out or historical',
                  qualification='Mechanism checks do not validate real-world physics; real source pack remains blocked.',
                  implementation_sha256=implementation_digest(), wall_budget_seconds=wall_budget,
                  probes=run_probes(), cases=[])
    for case in cases:
        for policy in ('passive', 'dispatcher'):
            remaining = wall_budget - (time.monotonic() - started)
            if remaining <= 0:
                report['cases'].append(dict(case=case, policy=policy, status='censored', reason='wall_budget'))
                continue
            receiver, sender = multiprocessing.Pipe(duplex=False)
            process = multiprocessing.Process(target=_episode, args=(case, policy, private, sender))
            process.start()
            sender.close()
            if receiver.poll(remaining):
                try:
                    result = receiver.recv()
                except EOFError:
                    result = dict(case=case, policy=policy, status='error', error='worker exited without result')
                process.join(timeout=1)
            else:
                result = dict(case=case, policy=policy, status='censored', reason='wall_budget')
            if process.is_alive():
                process.terminate()
                process.join()
            receiver.close()
            report['cases'].append(result)
    report['elapsed_seconds'] = time.monotonic() - started
    report['implementation_unchanged'] = (report['implementation_sha256'] == implementation_digest()
        and all(case.get('implementation_sha256', report['implementation_sha256']) == report['implementation_sha256']
                for case in report['cases']))
    if not report['implementation_unchanged']:
        report['warning'] = 'Implementation changed during the run; comparisons require a new run.'
    if not report['implementation_unchanged'] or any(case['status'] == 'error' for case in report['cases']):
        report['status'] = 'failed'
    elif any(case['status'] != 'completed' for case in report['cases']):
        report['status'] = 'incomplete'
    else:
        report['status'] = 'complete'
    (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    rows = ''.join('<tr>' + ''.join('<td>' + html.escape(str(row.get(key, '—'))) + '</td>'
                   for key in ('case', 'policy', 'status', 'minute', 'new_exposed_population',
                               'exact_replay', 'independent_reward_agreement')) + '</tr>' for row in report['cases'])
    probes = report['probes']
    highlights = [
        ('First ground work, setup 1 vs 5 minutes', (probes['ground_setup_short']['work_minutes'][0],
                                                    probes['ground_setup_long']['work_minutes'][0])),
        ('First ground work, blocked route', probes['ground_blocked']['work_minutes'][0]),
        ('Ground work minutes, 40 vs 20 litres', (probes['ground_setup_short']['work_minutes'],
                                                   probes['ground_low_stock']['work_minutes'])),
        ('Insufficient facility water final state', probes['ground_low_supply']['trace'][-1]['state']),
        ('Aircraft drops, open vs closed window vs wind', (probes['aircraft_open']['drops'],
            probes['aircraft_window_closes']['drops'], probes['aircraft_wind_increases']['drops'])),
        ('Home exposure, early vs late order', (probes['protection_early']['trace'][4]['exposed'],
                                                 probes['protection_late']['trace'][4]['exposed']))]
    probe_rows = ''.join('<tr><td>' + html.escape(label) + '</td><td>' + html.escape(str(value)) + '</td></tr>'
                         for label, value in highlights)
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Incident-day calibration</title>'
            '<h1>Incident-day synthetic calibration probes</h1><p>Overall status: ' + html.escape(report['status']) +
            '</p><p>' + html.escape(report['qualification']) +
            '</p><p>Development cases only; not held out. Detailed probes are in report.json; private audits '
            'are in private/.</p><table><thead><tr><th>Case</th><th>Policy</th><th>Status</th>'
            '<th>Minute</th><th>New exposure</th><th>Exact replay</th><th>Independent reward</th>'
            '</tr></thead><tbody>' + rows + '</tbody></table><h2>Mechanism comparisons</h2>'
            '<table><thead><tr><th>Probe</th><th>Observed value</th></tr></thead><tbody>' + probe_rows +
            '</tbody></table></html>')
    (output / 'report.html').write_text(page)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--cases', nargs='+', default=['incident-day-dev-001', 'incident-day-dev-004'])
    parser.add_argument('--wall-budget', type=float, default=120)
    args = parser.parse_args()
    report = run(args.output, args.cases, args.wall_budget)
    print(json.dumps(dict(version=report['version'], status=report['status'], cases=report['cases'],
                          elapsed_seconds=report['elapsed_seconds'])))
    return int(report['status'] != 'complete')


if __name__ == '__main__':
    sys.exit(main())
