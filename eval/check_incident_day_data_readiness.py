"""Audit pinned incident-day data readiness without approving or running a day."""

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_response/core/src'))
from envs.wildfire_response.incident_day.data import _check_hash, _unique, load_inputs, prepare_inputs
from envs.wildfire_response.incident_day.environment import IncidentDayEnv, implementation_digest
from envs.wildfire_response.incident_day.routing import HazardRouter
from wildfire_core.incident_day import EGRESS_CASE_IDS
from wildfire_core.mobility_routing import RoadNetwork


VERSION = 'incident-day-data-readiness-v1'
MAX_DOCUMENT_BYTES = 10_000_000
CANDIDATE_CROP = dict(top=16, left=0, height=20, width=26)


def read_pinned(path, expected):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Source document must not be a symlink')
    with path.open('rb') as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError('Source document exceeds byte limit')
    document = json.loads(raw, object_pairs_hook=_unique)
    if _check_hash(document, str(path)) != expected:
        raise ValueError('Source document differs from caller pin')
    return document


def read_run_report(path, expected):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Run report must not be a symlink')
    with path.open('rb') as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Run report differs from caller-held raw-file pin')
    return json.loads(raw, object_pairs_hook=_unique)


def crop_summary(pack, estimates, crop):
    grid = pack['grid']
    top, left, height, width = (crop[key] for key in ('top', 'left', 'height', 'width'))
    if (any(type(value) is not int for value in (top, left, height, width))
            or min(top, left) < 0 or min(height, width) < 1
            or top + height > grid['height'] or left + width > grid['width']):
        raise ValueError('Candidate crop must be a bounded rectangle inside the audited grid')
    cells = [cell for cell in estimates['cells'] if top <= cell['row'] < top + height
             and left <= cell['column'] < left + width]
    unknown = sum(not pack['fuel_known_mask'][row][column]
                  for row in range(top, top + height) for column in range(left, left + width))
    complete = all(pack['study_mask'][row][column] == 1
                   and type(pack['heights'][row][column]) in (int, float)
                   and math.isfinite(pack['heights'][row][column])
                   for row in range(top, top + height) for column in range(left, left + width))
    return dict(**crop, cells=height * width, source_known_cells=height * width - unknown,
                estimated_cells=len(cells), unresolved_land_water_cells=sum(
                    cell['land_kind'] == 'unresolved' for cell in cells),
                category_counts=dict(sorted(collections.Counter(cell['land_category'] for cell in cells).items())),
                complete_study_and_heights=complete,
                current_large_fire_dimensions_supported=height >= 24 and width >= 24,
                note='Rectangular candidate only; no pack or scenario has been compiled or approved.')


def graph_summary(pack, document, crop):
    grid = pack['grid']
    transform = grid['transform']
    def cell(position):
        return (math.floor((position[1] - transform[5]) / transform[4]),
                math.floor((position[0] - transform[2]) / transform[0]))
    def within(position, bounds):
        row, column = cell(position)
        return (bounds['top'] <= row < bounds['top'] + bounds['height']
                and bounds['left'] <= column < bounds['left'] + bounds['width'])
    full = dict(top=0, left=0, height=grid['height'], width=grid['width'])
    result = {}
    for label, bounds in (('full_grid', full), ('candidate', crop)):
        nodes = {node['id'] for node in document['nodes'] if within(node['position'], bounds)}
        result[label] = dict(nodes_in_rectangle=len(nodes), directed_arcs_with_endpoints_in_rectangle=sum(
            edge['source'] in nodes and edge['target'] in nodes for edge in document['edges']))
    result['limitations'] = ('Endpoint counts do not verify edge geometry within the rectangle, '
                             'connectedness, engine clearance or historical road access.')
    return result


def verify_guards(pack, pin, estimates):
    try:
        load_inputs(pack, pin)
    except ValueError as error:
        if 'unapproved' not in str(error):
            raise
    else:
        raise ValueError('Default loader unexpectedly accepts the source pack')
    unknown = {(cell['column'], cell['row']) for cell in estimates['cells']
               if cell['land_kind'] == 'unresolved'}
    if unknown:
        example = min(unknown)
        neighbour = (example[0] + 1, example[1])
        probe = SimpleNamespace(_uncertain_land=unknown, _burning=lambda: {neighbour})
        if not IncidentDayEnv._land_guard(probe):
            raise ValueError('Unresolved-land guard did not stop adjacent fire')
        probe._burning = lambda: set()
        if IncidentDayEnv._land_guard(probe):
            raise ValueError('Unresolved-land guard incorrectly stops an empty fire')
    return unknown


def static_routes(pack, network_document):
    network = RoadNetwork(network_document, pack['network_sha256'])
    expected_unresolved = {(cell['column'], cell['row'])
                           for cell in pack['provenance']['fuel_resolution']['document']['cells']
                           if cell['land_kind'] == 'unresolved'}
    counts = collections.Counter()
    case_results = []
    for case in EGRESS_CASE_IDS:
        environment = IncidentDayEnv(pack, network_document, expected_inputs_sha256=pack['sha256'],
                                     expected_network_sha256=network.sha256, allow_unapproved_data=True)
        if environment._uncertain_land != expected_unresolved:
            raise ValueError('Runtime unresolved-land guard differs from estimated inputs')
        try:
            environment.reset(case)
        except ValueError:
            counts['reset_failed'] += 1
            case_results.append(dict(case=case, reset='failed', route_assessment='not_run'))
            continue
        counts['reset_succeeded'] += 1
        scenario = environment._scenario
        handover = collections.Counter(item['kind'] for item in scenario['handover_staging'])
        counts.update({'handover_' + key: value for key, value in handover.items()})
        routers = {'current': environment.router,
                   'source_known_walking': HazardRouter(network, pack, pack['heights'],
                                                        walk_mask=pack['fuel_known_mask'])}
        center = [pack['grid']['width'] // 2, pack['grid']['height'] // 2]
        burning = environment._burning()
        mode_counts = collections.Counter()
        first_wave = [unit for unit in scenario['resources']
                      if unit['id'] in ('engine-0', 'engine-1', 'crew-0', 'crew-1')]
        for label, router in routers.items():
            for unit in first_wave:
                mode = unit['kind']
                result = router.options(unit['base_position'], center, mode=mode,
                                        burning=burning, wind=scenario['wind'], limit=32)
                key = label + '_' + mode
                mode_counts[key + '_bases_checked'] += 1
                mode_counts[key + '_approach_' + result['status']] += 1
                for option in result['options']:
                    point = option['approach_grid']
                    if not any(math.dist(fire, center) <= 3 and math.dist(fire, point) <= 3
                               for fire in burning):
                        continue
                    target = router._cell(unit['base_position'])
                    returned = router.options(option['approach_point'], target, mode=mode,
                        burning=burning, wind=scenario['wind'], target_metric=unit['base_position'], limit=1)
                    if returned['options']:
                        mode_counts[key + '_bases_with_useful_approach_and_return'] += 1
                        break
        counts.update(mode_counts)
        case_results.append(dict(case=case, reset='succeeded',
                                 handover_staging=dict(sorted(handover.items())),
                                 initial_routes=dict(sorted(mode_counts.items()))))
    return dict(scope='Initial egress-case handover; declared first-wave bases regardless of availability; no episode run.',
                limits=('Approaches are bounded router results. Source-known walking is conservative; '
                        'mapped roads retain their existing access assumptions. No dynamic, shift-time, '
                        'turn-restriction or operational validation is established.'),
                totals=dict(sorted(counts.items())), cases=case_results)


def summarize_run(report, *, report_sha256, pack_sha256, network_sha256, implementation_sha256):
    if (report.get('version') != 'incident-day-plausibility-report-v2'
            or report.get('input_sha256') != pack_sha256
            or report.get('network_sha256') != network_sha256
            or report.get('implementation_sha256') != implementation_sha256):
        raise ValueError('Run report identity differs from caller pins')
    if report.get('implementation_unchanged') is not True:
        raise ValueError('Run report does not establish a stable implementation')
    cases, static, gates = report['dynamic_cases'], report['static_cases'], report['gates']
    if (not isinstance(cases, list) or not isinstance(static, list) or not isinstance(gates, dict)
            or len({item['case'] for item in cases}) != len(cases)
            or {item['case'] for item in cases} != {item['case'] for item in static}):
        raise ValueError('Run report case sets are invalid')
    route_counts = collections.Counter(item['status'] for case in static
                                       for item in case['initial_route_options'])
    counterfactuals = collections.Counter(case['counterfactual']['status'] for case in static)
    inherited_assignments = sum(len(case['counterfactual'].get('details', {}).get(
        'useful_safe_inherited_assignments', [])) for case in static)
    inherited_recoveries = sum(len(case['counterfactual'].get('details', {}).get(
        'legal_inherited_recoveries', [])) for case in static)
    case_summaries = []
    for case in cases:
        verification = case['verification']
        work = case['applied_work_counts']
        ground_work = sum(count for identifier, count in work.items()
                          if identifier.startswith(('engine-', 'crew-')))
        case_summaries.append(dict(case=case['case'], status=case['status'], complete=case['complete'],
            minutes_observed=case['minutes_observed'],
            exact_replay=verification.get('exact_replay'),
            independent_reward_agreement=verification.get('independent_reward_agreement'),
            boundary_censored=case['outcome']['boundary_censored'],
            unresolved_evacuations=case['outcome']['unresolved_evacuations'],
            applied_ground_work=ground_work,
            intended_constraint_coverage=gates['intended_constraint_coverage'].get(case['case'])))
    return dict(report_sha256=report_sha256, input_sha256=report['input_sha256'],
        network_sha256=report['network_sha256'], implementation_sha256=report['implementation_sha256'],
        status=report['status'], policy=report['policy'], workers=report['workers'],
        allow_unapproved_data=report['allow_unapproved_data'], input_approval_status=report['input_approval_status'],
        land_mask_supplied=report['land_mask_supplied'], implementation_unchanged=True,
        gates={name: gates[name] for name in ('exercise_consistent', 'physically_validated',
               'contradiction_count', 'blocking_count', 'human_review')},
        static_initial_route_statuses=dict(sorted(route_counts.items())),
        static_counterfactual_statuses=dict(sorted(counterfactuals.items())),
        static_useful_safe_inherited_assignments=inherited_assignments,
        static_legal_inherited_recoveries=inherited_recoveries,
        completed_without_reported_boundary_censoring=sum(
            case['complete'] is True and case['minutes_observed'] == 1440
            and case['boundary_censored'] is False for case in case_summaries),
        cases=case_summaries,
        limitations='Reported episode evidence, not source approval, physical calibration or operational validation.')


def audit(pack, prepared, domain, network, estimates, pins, *, routes=True, crop=CANDIDATE_CROP,
          measured_run=None):
    initial_implementation = implementation_digest()
    documents = dict(pack=pack, prepared=prepared, domain=domain, network=network, estimates=estimates)
    for name, document in documents.items():
        if _check_hash(document, name) != pins[name]:
            raise ValueError('Document differs from caller pin: ' + name)
    verified_pack = load_inputs(pack, pins['pack'], allow_unapproved=True)
    if verified_pack['approval_status'] != 'unapproved_source_pack':
        raise ValueError('Expected the unapproved real-data exercise pack')
    unresolved_land = verify_guards(pack, pins['pack'], estimates)
    resolution = verified_pack['provenance']['fuel_resolution']
    if (not isinstance(resolution, dict) or resolution.get('kind') != 'estimated'
            or resolution.get('document') != estimates):
        raise ValueError('Pack does not embed the pinned estimated-fuel document')
    rebuilt = prepare_inputs(prepared, domain, network, fuel_resolution=resolution)
    if rebuilt != verified_pack:
        raise ValueError('Pack is not reproducible from the caller-pinned sources')
    cells = estimates['cells']
    unknown = sum(not cell for row in pack['fuel_known_mask'] for cell in row)
    if len(cells) != unknown:
        raise ValueError('Estimate count differs from unknown source cells')
    crop_result = crop_summary(pack, estimates, crop)
    height, width = pack['grid']['height'], pack['grid']['width']
    land_counts = collections.Counter(cell['land_kind'] for cell in cells)
    if routes:
        route_result = static_routes(pack, network)
    else:
        route_result = dict(scope='not run', totals={}, cases=[])
    if implementation_digest() != initial_implementation:
        raise ValueError('Implementation changed during the audit')
    return dict(version=VERSION, source_pins=dict(sorted(pins.items())),
        implementation_sha256=initial_implementation,
        scope='Offline pinned input validation and initial-handover route probes; no episode or safety validation.',
        integrity=dict(identity_verified=True, regenerated_from_pinned_sources=True,
            default_loader_rejects=True, source_mask_unchanged=True, grid_height=height, grid_width=width,
            known_fuel_cells=height * width - unknown, unknown_fuel_cells=unknown,
            estimated_cells=len(cells), land_kind_counts=dict(sorted(land_counts.items())),
            complete_study_and_heights=True, unresolved_land_guard_cells=len(unresolved_land),
            unknown_land_guard='verified_for_unresolved_and_empty_fire'),
        approval_matrix=dict(identity='verified', estimated_experiment='opt_in_only_with_unknown_land_guard',
            historical_source='blocked', physical_operational_validation='not_established'),
        candidate_crop=crop_result, graph=graph_summary(pack, network, crop), routes=route_result,
        measured_run=measured_run,
        remaining_actions=['Keep unresolved land/water guard and explicit unapproved-data opt-in.',
            'Validate a compatible >=24 by 24 domain and real mapped road access before claiming readiness.',
            'Run scoped episodes and independent replay separately; assess boundary censoring and access.',
            'Review source provenance, experimental scope and physical/operational evidence independently.'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('pack', 'prepared', 'domain', 'network', 'estimates'):
        parser.add_argument('--' + name, required=True, type=Path)
        parser.add_argument('--' + name + '-sha256', required=True)
    parser.add_argument('--run-report', type=Path)
    parser.add_argument('--run-report-sha256')
    parser.add_argument('--run-implementation-sha256')
    args = parser.parse_args(argv)
    if any((args.run_report, args.run_report_sha256, args.run_implementation_sha256)) and not all(
            (args.run_report, args.run_report_sha256, args.run_implementation_sha256)):
        parser.error('Run report, raw-file SHA256 and run implementation SHA256 must be supplied together')
    try:
        names = ('pack', 'prepared', 'domain', 'network', 'estimates')
        pins = {name: getattr(args, name + '_sha256') for name in names}
        documents = {name: read_pinned(getattr(args, name), pins[name]) for name in names}
        measured = None
        if args.run_report:
            measured = summarize_run(read_run_report(args.run_report, args.run_report_sha256),
                report_sha256=args.run_report_sha256, pack_sha256=pins['pack'],
                network_sha256=pins['network'], implementation_sha256=args.run_implementation_sha256)
        report = audit(**documents, pins=pins, measured_run=measured)
        if measured is not None:
            measured['implementation_matches_current_audit'] = (
                measured['implementation_sha256'] == report['implementation_sha256'])
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        parser.exit(2, 'Readiness audit failed: ' + str(error) + '\n')
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
