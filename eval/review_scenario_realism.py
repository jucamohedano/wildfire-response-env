"""Policy-free aggregate static screen for incident-day scenario generators."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.scenarios import (ALL_CASE_IDS, CASE_IDS, CALIBRATED_CASE_IDS,
    EGRESS_CASE_IDS, COVERAGE_VARIANT_IDS, REAL_MAP_CASE_IDS, REAL_MAP_V2_CASE_IDS)
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN
from envs.wildfire_response.incident_day.plausibility import static_case

VERSION = 'incident-day-static-realism-screen-v2'


def screen(scenario):
    """Structural contradictions only; shortages and adverse scenarios remain valid."""
    codes = set()
    h = scenario['horizon_minutes']
    if type(h) is not int or h <= 0 or any(type(e.get('minute')) is not int or not 0 <= e['minute'] < h for e in scenario['events']):
        codes.add('event_outside_horizon')
    for event in scenario['events']:
        if 'until' in event and (type(event['until']) is not int or event['until'] <= event['minute']):
            codes.add('invalid_event_duration')
    units = scenario['resources']
    if len({u['id'] for u in units}) != len(units):
        codes.add('duplicate_resource')
    for u in units:
        if (u['kind'] not in ('engine', 'crew', 'aircraft') or u['home_base'] not in scenario['centers']
                or any(type(u.get(k)) not in (int, float) or not math.isfinite(u[k]) or u[k] < 0
                       for k in ('available_at', 'water_litres', 'capacity_litres', 'work_remaining', 'total_work_remaining'))
                or u['water_litres'] > u['capacity_litres']
                or type(u.get('duty_end_at')) is not int or u['duty_end_at'] < u['available_at']
                or u['work_remaining'] > u['total_work_remaining']):
            codes.add('invalid_resource_lifecycle')
    profile = scenario['profile']
    for k in ('ground_setup_minutes', 'aircraft_setup_minutes', 'ground_refill_minutes', 'air_refill_minutes', 'rest_minutes', 'flight_speed_kph'):
        if type(profile.get(k)) not in (int, float) or not math.isfinite(profile[k]) or profile[k] <= 0:
            codes.add('invalid_duration_or_speed')
    groups = scenario['groups']
    destinations = scenario['destinations']
    if (len({g['id'] for g in groups}) != len(groups) or len({d['id'] for d in destinations}) != len(destinations)
            or any(type(g.get('count')) is not int or g['count'] <= 0 for g in groups)
            or any(type(d.get('capacity')) is not int or d['capacity'] <= 0 for d in destinations)):
        codes.add('invalid_population_or_capacity')
    destination_ids = {d['id'] for d in destinations}
    if any(g.get('destination_id') is not None and g['destination_id'] not in destination_ids for g in groups):
        codes.add('unknown_initial_destination')
    for destination in destinations:
        occupants = sum(g['count'] for g in groups if g.get('destination_id') == destination['id'] and g.get('status') == 'arrived')
        if occupants > destination['capacity']:
            codes.add('initial_destination_over_capacity')
    for event in scenario['events']:
        if event['kind'] == 'destination_closed' and event['destination_id'] not in destination_ids:
            codes.add('unknown_closed_destination')
        if event['kind'] == 'center_unavailable' and event['center_id'] not in scenario['centers']:
            codes.add('unknown_event_center')
    return sorted(codes)


def case_groups():
    groups = [('fixture', CASE_IDS), ('shift', CALIBRATED_CASE_IDS), ('egress', EGRESS_CASE_IDS),
              ('coverage_variants', COVERAGE_VARIANT_IDS), ('realmap_v1', REAL_MAP_CASE_IDS),
              ('realmap_v2', REAL_MAP_V2_CASE_IDS)]
    identifiers = [identifier for _, values in groups for identifier in values]
    if len(identifiers) != len(set(identifiers)) or set(identifiers) != set(ALL_CASE_IDS):
        raise ValueError('Incident-day catalog partition differs from ALL_CASE_IDS')
    return groups


def run(real_inputs, network):
    fixture, roads = fixture_inputs()
    real = json.loads(Path(real_inputs).read_text())
    net = json.loads(Path(network).read_text())
    contexts = [(label, real if label.startswith('realmap_') else fixture,
                 net if label.startswith('realmap_') else roads, ids) for label, ids in case_groups()]
    bounds = json.loads(Path('data/incident_day_v2/plausibility/bounds-v1.json').read_text())
    groups = {}
    for label, inputs, document, ids in contexts:
        env = IncidentDayEnv(inputs, document, expected_inputs_sha256=inputs['sha256'],
            expected_network_sha256=document['sha256'], allow_unapproved_data=label.startswith('realmap_'))
        if label.startswith('realmap_') and (inputs['sha256'] != PACK_PIN or document['sha256'] != NETWORK_PIN):
            raise ValueError('Real-map pack/network pins differ')
        contradictions, unsupported, candidates, compile_failures = Counter(), Counter(), Counter(), 0
        for identifier in ids:
            try:
                env.reset(identifier)
            except ValueError:
                compile_failures += 1
                continue
            for code in screen(env._scenario):
                contradictions[code] += 1
            if any(event.get('until', 0) > env._scenario['horizon_minutes'] for event in env._scenario['events']):
                candidates['event_continues_after_horizon'] += 1
            result = static_case(env._scenario, env.inputs, env.network, env._burning(), bounds)
            for finding in {item['code']: item for item in result['findings']}.values():
                if finding['code'] == 'initial_assignment_out_of_reach':
                    candidates[finding['code']] += 1
                    continue
                if finding['category'] == 'contradiction':
                    contradictions[finding['code']] += 1
                else:
                    unsupported[finding['code']] += 1
        groups[label] = dict(cases=len(ids), compiled=len(ids)-compile_failures, compilation_failures=compile_failures,
                             contradictions=dict(contradictions), review_candidates=dict(candidates), unsupported=dict(unsupported))
    return dict(version=VERSION, bounds_version=bounds['version'], case_groups=groups,
                policy_rollouts=0, held_out_outcomes_inspected=False,
                limitations=['Initial static checks do not verify future event effects, travel permissions, field accuracy or physical calibration.',
                             'No authoritative land mask supplied; source-known fuel does not establish land or road access.',
                             'On-scene initialization is a synthetic handover and does not establish an observed prior mission.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-inputs', required=True)
    parser.add_argument('--network', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.real_inputs, args.network), sort_keys=True))


if __name__ == '__main__':
    main()
