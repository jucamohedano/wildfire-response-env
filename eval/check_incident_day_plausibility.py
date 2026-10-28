"""Audit development exercises using cached inputs, with a bounded single worker."""

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
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.plausibility import audit_case, counterfactual_case, route_checks, static_case
from envs.wildfire_response.incident_day.scenarios import ALL_CASE_IDS, CASE_IDS, CALIBRATED_CASE_IDS, EGRESS_CASE_IDS, COVERAGE_CASE_IDS, REAL_MAP_CASE_IDS


VERSION = 'incident-day-plausibility-report-v2'
BOUNDS_PATH = Path(__file__).resolve().parents[1] / 'data/incident_day_v2/plausibility/bounds-v1.json'


def environment(inputs, network, allow_unapproved=False):
    return IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                          expected_network_sha256=network['sha256'], allow_unapproved_data=allow_unapproved)


def static_audit(inputs, network, bounds, land_mask, case_ids=CALIBRATED_CASE_IDS, allow_unapproved=False):
    results = []
    for case in case_ids:
        env = environment(inputs, network, allow_unapproved)
        try:
            env.reset(case)
        except ValueError as error:
            results.append(dict(case=case, family='scenario_compilation_failed',
                                findings=[dict(code='scenario_compilation_failed', category='blocking',
                                               detail=str(error), minute=None, resource=None)]))
            continue
        result = static_case(env._scenario, env.inputs, env.network, env._burning(), bounds, land_mask)
        result['counterfactual'] = counterfactual_case(env)
        center = env._sector()
        routes = []
        for unit in env._scenario['resources']:
            if unit['kind'] == 'aircraft' or unit['state'] != 'available':
                continue
            plan = env.router.options(unit['position'], center, mode=unit['kind'], burning=env._burning(),
                                      wind=env._scenario['wind'], limit=1)
            route = dict(resource=unit['id'], status=plan['status'])
            if plan['options']:
                journey = plan['options'][0]['journey']
                route.update(duration_minutes=journey['duration_minutes'], distance_m=sum(leg['distance_m'] for leg in journey['legs']),
                             destination=journey['destination'], leg_speeds_kph=[leg['speed_kph'] for leg in journey['legs']])
            routes.append(route)
        result['initial_route_options'] = routes
        results.append(result)
    return results


def verify_status(env):
    try:
        verification = env.verify_episode()
    except Exception:
        return dict(passed=False, exact_replay=None, independent_reward_agreement=None,
                    error=traceback.format_exc()), 'verification_failed'
    checks = {field: verification.get(field) for field in ('passed', 'exact_replay', 'independent_reward_agreement')}
    if not all(value is True for value in checks.values()):
        return checks, 'verification_failed'
    if env.terminated and not env.truncated and env.minute == env._scenario['horizon_minutes']:
        return checks, 'completed'
    return checks, 'censored'


def exercise_gate(contradictions, blocked, complete, unchanged, static_cases):
    if contradictions or blocked:
        return 'failed'
    if (complete and unchanged and static_cases and all(
            case.get('counterfactual', {}).get('status') == 'demonstrated' for case in static_cases)):
        return 'passed'
    return 'not established'


def episode(case, inputs, network, bounds, land_mask, private, connection, allow_unapproved=False):
    try:
        env = environment(inputs, network, allow_unapproved)
        observation, unused = env.reset(case)
        dispatcher = DayDispatcher('dispatcher')
        while not (env.terminated or env.truncated):
            observation, unused_reward, unused_terminated, unused_truncated, unused_info = env.step(dispatcher.action(env, observation))
        verification, status = verify_status(env)
        trajectory = env.trajectory()
        reports = env.bus.reports
        orders = list(env.ops.orders.values())
        analysis = audit_case(env._scenario, env.inputs, env.network, env._audit, bounds, reports, orders,
                              land_mask, queries=env._query_log)
        analysis['route_checks'] = route_checks(env._approaches, env.network)
        analysis['findings'].extend(analysis['route_checks']['findings'])
        analysis.update(case=case, status=status, verification=verification,
                        outcome=env.outcomes(), scenario_sha256=trajectory['scenario_sha256'],
                        implementation_sha256=trajectory['implementation_sha256'],
                        outcome_area_interpretation='Burned hectares are a coarse grid-cell footprint proxy, not measured fire area or perimeter.')
        with gzip.open(private / f'{case}.json.gz', 'wt') as stream:
            json.dump(dict(audit=env._audit, reports=reports, orders=orders, trajectory=trajectory,
                           approaches=env._approaches, verification=verification), stream, allow_nan=False)
        connection.send(analysis)
    except BaseException:
        connection.send(dict(case=case, status='error', error=traceback.format_exc()))
    finally:
        connection.close()


def write_report(output, report):
    (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    def display(value):
        if value is None:
            return 'unknown'
        if isinstance(value, bool):
            return 'yes' if value else 'no'
        if isinstance(value, dict):
            return '; '.join(str(key).replace('_', ' ') + ': ' + display(item) for key, item in value.items())
        if isinstance(value, list):
            if len(value) > 12:
                return str(len(value)) + ' entries; see JSON for the complete list'
            return ', '.join(display(item) for item in value) if value else 'none'
        return str(value)

    def escape(value):
        return html.escape(display(value))

    def table(headers, rows):
        heading = ''.join('<th>' + escape(header) + '</th>' for header in headers)
        body = ''.join('<tr>' + ''.join('<td>' + escape(value) + '</td>' for value in row) + '</tr>' for row in rows)
        return '<table><thead><tr>' + heading + '</tr></thead><tbody>' + body + '</tbody></table>'

    sections = []
    for case in report['static_cases']:
        dynamic = next((item for item in report['dynamic_cases'] if item['case'] == case['case']), None)
        section = '<section><h2>' + escape(case['case'] + ' — ' + case['family']) + '</h2>'
        counterfactual = case.get('counterfactual', {})
        section += '<p>Counterfactual mechanics: <strong>' + escape(counterfactual.get('status', 'not available')) + '</strong>.</p>'
        if counterfactual.get('details'):
            section += '<details><summary>Counterfactual evidence</summary>' + table(
                ('Check', 'Evidence'), [(key.replace('_', ' '), value) for key, value in counterfactual['details'].items()]) + '</details>'
        findings = case.get('findings', []) + (dynamic.get('findings', []) if dynamic else [])
        if findings:
            section += '<h3>Findings and evidence gaps</h3>' + table(('Class', 'Minute', 'Resource', 'Finding'),
                [(item['category'], item.get('minute'), item.get('resource'), item['detail']) for item in findings])
        if dynamic is None:
            sections.append(section + '<p>Dynamic run not requested.</p></section>')
            continue
        section += '<p>Dynamic status: <strong>' + escape(dynamic['status']) + '</strong>. Verification: ' + escape(dynamic.get('verification')) + '.</p>'
        if dynamic.get('error'):
            section += '<p>Worker error: ' + escape(dynamic['error']) + '</p>'
        if dynamic.get('event_evidence'):
            section += '<h3>Events and deliveries</h3>' + table(('Minute', 'Event', 'Observed evidence', 'Report times'),
                [(item['event']['minute'], item['event']['kind'], item['status'],
                  [f"{record['kind']} observed {record['observed_at']}, received {record['received_at']}"
                   for record in item.get('reports', [])]) for item in dynamic['event_evidence']])
        if dynamic.get('resource_timeline'):
            section += '<h3>Resource timelines</h3><p>Times are minutes from handover. Each row expands to show state changes, position, water, work and fuel. '
            section += 'Applied work counts and first work refer to effects recorded in the minute audit.</p>'
            for identifier, entries in dynamic['resource_timeline'].items():
                section += '<details><summary>' + escape(identifier) + ': first applied work ' + escape(dynamic['first_applied_work'].get(identifier))
                section += ', applied effects ' + escape(dynamic['applied_work_counts'].get(identifier, 0)) + '</summary>'
                section += table(('Minute', 'State', 'Position', 'Water L', 'Work remaining', 'Total work', 'Fuel min', 'Due'),
                    [(item['minute'], item['state'], item['position'], item['water_litres'], item['work_remaining'],
                      item['total_work_remaining'], item['fuel_minutes'], item['due_at']) for item in entries]) + '</details>'
        if dynamic.get('protection_orders'):
            section += '<h3>Protection orders</h3>' + table(('Order', 'Group', 'Action', 'State', 'Issued', 'Delivered', 'Acknowledged', 'Started', 'Completed'),
                [(item.get('id'), item.get('group_id'), item.get('action'), item.get('state'), item.get('issued_at'),
                  item.get('delivered_at'), item.get('acknowledged_at'), item.get('started_at'), item.get('completed_at'))
                 for item in dynamic['protection_orders']])
        elif 'protection_orders' in dynamic:
            section += '<p>No protection orders were recorded.</p>'
        if dynamic.get('population_final'):
            section += '<h3>Population at end of observed run</h3>' + table(('Group', 'Count', 'Status', 'Exposed', 'Protection pending'),
                [(identifier, group['count'], group['status'], group['exposed'], group['protection_pending'])
                 for identifier, group in dynamic['population_final'].items()])
        section += '<p>Communications delivered: ' + escape(dynamic.get('communication_count')) + '. Full records, offered routes and minute audit are in the private output; report.json contains all checks.</p></section>'
        sections.append(section)
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Incident-day exercise plausibility</title>'
            '<style>body{font:15px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#222}'
            'section{border-top:1px solid #bbb;padding:1rem 0}table{border-collapse:collapse;width:100%;margin:.6rem 0}'
            'th,td{border-bottom:1px solid #ddd;text-align:left;padding:.4rem;vertical-align:top}summary{cursor:pointer;padding:.3rem}</style>'
            '<h1>Incident-day exercise plausibility</h1><p>Assumed exercise bounds; synthetic output is not physical validation. '
            'Burned hectares are a coarse grid-cell footprint proxy, not measured fire area or perimeter.</p>'
            '<p>Exercise consistency: ' + html.escape(report['gates']['exercise_consistent']) +
            '. Human review: ' + html.escape(report['gates']['human_review']) +
            '. Physical validation: ' + html.escape(report['gates']['physically_validated']) + '.</p>' + ''.join(sections) + '</html>')
    (output / 'report.html').write_text(page)


def run(output, cases, wall_budget, inputs=None, network=None, land_mask=None, allow_unapproved=False):
    if any(case not in ALL_CASE_IDS for case in cases) or len(set(cases)) != len(cases):
        raise ValueError('Select distinct incident-day case IDs')
    if not math.isfinite(wall_budget) or not 0 < wall_budget <= 3600:
        raise ValueError('Wall budget must be positive and at most 3600 seconds')
    if (inputs is None) != (network is None):
        raise ValueError('Inputs and network must be supplied together')
    if inputs is None:
        inputs, network = fixture_inputs()
    if land_mask is not None:
        height, width = inputs['grid']['height'], inputs['grid']['width']
        if len(land_mask) != height or any(len(row) != width or any(value not in (0, 1, None) for value in row) for row in land_mask):
            raise ValueError('Land mask must be a 0/1/null matrix matching the input grid')
    output = Path(output).absolute()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    private = output / 'private'
    private.mkdir(mode=0o700)
    bounds = json.loads(BOUNDS_PATH.read_text())
    started = time.monotonic()
    digest_before = implementation_digest()
    selected = set(cases)
    catalog_suites = (COVERAGE_CASE_IDS, REAL_MAP_CASE_IDS, EGRESS_CASE_IDS, CALIBRATED_CASE_IDS, CASE_IDS)
    suites = next((list(suite) for suite in catalog_suites if selected <= set(suite)), None)
    if suites is None:
        suites = list(dict.fromkeys(case for suite in catalog_suites
                                   if selected.intersection(suite) for case in suite))
    static = static_audit(inputs, network, bounds, land_mask, suites, allow_unapproved)
    report = dict(version=VERSION, bounds=bounds, input_sha256=inputs['sha256'], network_sha256=network['sha256'],
                  implementation_sha256=digest_before, wall_budget_seconds=wall_budget, static_cases=static, dynamic_cases=[],
                  land_mask_supplied=land_mask is not None, allow_unapproved_data=allow_unapproved,
                  input_approval_status=inputs.get('approval_status'), policy='dispatcher', workers=1)
    for case in cases:
        remaining = wall_budget - (time.monotonic() - started)
        if remaining <= 0:
            report['dynamic_cases'].append(dict(case=case, status='censored', reason='wall_budget'))
            continue
        receiver, sender = multiprocessing.Pipe(duplex=False)
        process = multiprocessing.Process(target=episode, args=(case, inputs, network, bounds, land_mask, private, sender, allow_unapproved))
        process.start()
        sender.close()
        if receiver.poll(remaining):
            try:
                result = receiver.recv()
            except EOFError:
                result = dict(case=case, status='error', error='worker exited without result')
        else:
            result = dict(case=case, status='censored', reason='wall_budget')
        if process.is_alive():
            process.terminate()
        process.join()
        receiver.close()
        report['dynamic_cases'].append(result)
    report['elapsed_seconds'] = time.monotonic() - started
    report['implementation_unchanged'] = (digest_before == implementation_digest() and all(
        item.get('implementation_sha256', digest_before) == digest_before for item in report['dynamic_cases']))
    contradictions = sum(item['category'] == 'contradiction' for case in static + report['dynamic_cases'] for item in case.get('findings', []))
    blocked = sum(item['category'] == 'blocking' for case in static + report['dynamic_cases'] for item in case.get('findings', []))
    complete = len(report['dynamic_cases']) == len(suites) and all(item['status'] == 'completed' for item in report['dynamic_cases'])
    intended_kind = {'wind_access_loss': 'wind', 'crew_only_access': 'access_restriction',
                     'aircraft_window': 'aircraft_unavailable', 'destination_threat': 'destination_closed',
                     'regional_shortage': 'center_unavailable'}
    coverage = {}
    for static_case_result in static:
        case = static_case_result['case']
        dynamic = next((item for item in report['dynamic_cases'] if item['case'] == case), None)
        if not dynamic or dynamic['status'] != 'completed' or static_case_result['family'] == 'scenario_compilation_failed':
            coverage[case] = 'not_observed'
        elif static_case_result['family'] == 'quiet_handover':
            coverage[case] = 'work_observed_review_required' if sum(dynamic['applied_work_counts'].values()) else 'no_work_observed'
        else:
            matches = [item for item in dynamic['event_evidence'] if item['event']['kind'] == intended_kind[static_case_result['family']]]
            coverage[case] = 'constraint_evidence_review_required' if any(item['status'] == 'constraint_evidence_observed' for item in matches) else 'no_constraint_evidence_observed'
    exercise = exercise_gate(contradictions, blocked, complete, report['implementation_unchanged'], static)
    report['gates'] = dict(exercise_consistent=exercise, physically_validated='no', contradiction_count=contradictions,
                           blocking_count=blocked, human_review='pending',
                           intended_constraint_coverage=coverage,
                           note='Automated exercise consistency requires six verified completed days, no contradictions or blockers, stable implementation and demonstrated counterfactual mechanics. Human review is pending; neither counterfactuals nor temporal correlations prove causation or physical validity.')
    report['status'] = 'failed' if not report['implementation_unchanged'] or any(item['status'] in ('error', 'verification_failed') for item in report['dynamic_cases']) else 'complete' if complete else 'incomplete'
    write_report(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--wall-budget', type=float, default=600)
    parser.add_argument('--cases', nargs='*', choices=ALL_CASE_IDS, default=list(COVERAGE_CASE_IDS))
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--network', type=Path)
    parser.add_argument('--land-mask', type=Path, help='Optional cached grid-aligned 0/1/null land-mask JSON matrix')
    parser.add_argument('--allow-unapproved-data', action='store_true', help='Use explicitly unapproved exercise inputs; never grants approval')
    args = parser.parse_args()
    try:
        report = run(args.output, args.cases, args.wall_budget,
                     json.loads(args.inputs.read_text()) if args.inputs else None,
                     json.loads(args.network.read_text()) if args.network else None,
                     json.loads(args.land_mask.read_text()) if args.land_mask else None, args.allow_unapproved_data)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f'Plausibility audit failed: {error}\n')
    print(json.dumps(dict(status=report['status'], gates=report['gates'], elapsed_seconds=report['elapsed_seconds'])))
    return int(report['status'] != 'complete' or report['gates']['exercise_consistent'] == 'failed')


if __name__ == '__main__':
    sys.exit(main())
