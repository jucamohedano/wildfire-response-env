"""Check aircraft activity in independently verified private development audits.

Input is the private directory produced by check_incident_day_plausibility.py;
only aggregate results are written. This reads existing evidence, not a replay.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import gzip
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
from envs.wildfire_response.incident_day.clock import solar_daylight
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.scenarios import REAL_MAP_V2_CASE_IDS, build_scenario
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork

INPUT_PIN = '50031b3029dcee0a1367ccdd8125d081d19d3b500b2dd373e30ef127d47a2c4d'
NETWORK_PIN = '12782cb4ccb4bbfd2d59eaaa7380ad6b1a807320cf5a15b6d01e44d7ec89fc16'


def pinned(path, expected):
    value = json.loads(Path(path).read_text())
    if value.get('sha256') != expected or digest({k: v for k, v in value.items() if k != 'sha256'}) != expected:
        raise ValueError('Input content does not match its canonical pin')
    return value


def inspect_case(data, scenario, inputs, implementation):
    errors = Counter()
    counts = Counter()
    trajectory = data['trajectory']
    verification = data['verification']
    if not all(verification.get(k) is True for k in ('passed', 'exact_replay', 'independent_reward_agreement')):
        errors['unverified'] += 1
    if (trajectory.get('scenario_sha256') != scenario['sha256'] or
            trajectory.get('implementation_sha256') != implementation or
            trajectory.get('inputs_sha256') != INPUT_PIN or trajectory.get('network_sha256') != NETWORK_PIN or
            trajectory.get('task_id') != scenario['id']):
        errors['stale_or_wrong_execution'] += 1
    audit = data['audit']
    horizon = scenario['horizon_minutes']
    if len(audit) != horizon + 1 or any(row.get('minute') != i for i, row in enumerate(audit)):
        return counts, errors + Counter({'incomplete_audit': 1})
    units = {u['id']: u for u in trajectory['initial_observation']['resources'] if u['kind'] == 'aircraft'}
    if not units:
        errors['no_aircraft_roster'] += 1
        return counts, errors
    offers = {r['payload']['offer']['id']: r['payload']['offer'] for r in data['reports']
              if r['kind'] == 'resource_offer' and 'offer' in r.get('payload', {})}
    authorizations = {}
    for transition in trajectory['transitions']:
        if transition.get('valid') is True:
            for offer in transition['action'].get('authorizations', []):
                if offer in offers:
                    authorizations.setdefault(offers[offer]['resource_id'], []).append(transition['start_minute'])
    accepted = {}
    for report in data['reports']:
        if report['kind'] == 'dispatch_accepted':
            accepted.setdefault(report['payload']['resource_id'], []).append(report['observed_at'])
    start = datetime.fromisoformat(scenario['start_utc'])
    x, y = scenario.get('sector_grid', [inputs['grid']['width']//2, inputs['grid']['height']//2])
    centers = inputs['grid'].get('centre_lon_lat')
    lon, lat = centers[y][x] if centers else (-5.15, 36.55)
    closures = [(e['minute'], e['until']) for e in scenario['events'] if e['kind'] == 'aircraft_unavailable']

    def window_open(minute, row):
        return (solar_daylight(start + timedelta(minutes=minute), lon, lat)
                and math.hypot(*row['wind']) <= scenario['profile']['aircraft_max_wind']
                and not any(a <= minute < b for a, b in closures))
    for identifier, unit in units.items():
        water_cap, fuel_cap = unit['capacity_litres'], unit['fuel_capacity_minutes']
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (water_cap, fuel_cap)):
            errors['invalid_capacity'] += 1
            continue
        stage = 0
        mobilized = None
        last_mobilized = -1
        authorized = False
        flew = False
        for minute, row in enumerate(audit):
            current = row['resources'].get(identifier)
            if current is None:
                errors['missing_resource'] += 1
                break
            previous = audit[minute - 1]['resources'][identifier] if minute else None
            for field, cap in (('water_litres', water_cap), ('fuel_minutes', fuel_cap)):
                value = current.get(field)
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= cap:
                    errors['invalid_stock'] += 1
            state = current['state']
            for field in ('work_remaining', 'total_work_remaining'):
                value = current.get(field)
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    errors['invalid_work_stock'] += 1
            if previous and current['water_litres'] > previous['water_litres'] and previous['state'] != 'refilling':
                errors['unexplained_reload'] += 1
            if previous and current['fuel_minutes'] > previous['fuel_minutes'] and previous['state'] != 'refilling':
                errors['unexplained_refuel'] += 1
            if state == 'mobilizing' and (not previous or previous['state'] != 'mobilizing'):
                counts['mobilizations'] += 1
                mobilized, stage = minute, 1
                flew = False
                authorized = (minute in accepted.get(identifier, []) and any(
                    last_mobilized < t < minute for t in authorizations.get(identifier, [])))
                last_mobilized = minute
                if not authorized:
                    errors['dispatch_without_authorization'] += 1
            if stage == 1 and state == 'travel' and minute > mobilized:
                if not window_open(minute, row):
                    errors['takeoff_outside_window'] += 1
                stage = 2
            if stage == 2 and state == 'travel' and current['position'] != unit['base_position']:
                flew = True
            if stage == 2 and state == 'on_scene':
                if not flew or current['position'] == unit['base_position']:
                    errors['arrival_without_flight'] += 1
                stage = 3
            works = [w for w in row['applied_work'] if w['resource_id'] == identifier]
            for work in works:
                counts['drops'] += 1
                if (stage != 3 or previous is None or previous['state'] not in ('on_scene', 'working') or
                    previous.get('due_at') is None or minute < previous['due_at'] or work.get('state') != 'working'):
                    errors['work_before_arrival_or_setup'] += 1
                if previous is None or previous['water_litres'] <= current['water_litres']:
                    errors['work_without_water_consumption'] += 1
                if previous and (previous['work_remaining'] <= 0 or minute >= unit['duty_end_at']):
                    errors['work_outside_duty'] += 1
                if not window_open(minute, row):
                    errors['work_outside_window'] += 1
                if not authorized:
                    errors['work_without_authorization'] += 1
                stage = 4
            if stage == 4 and state == 'returning':
                stage = 5
            if stage == 5 and state == 'refilling' and current['position'] == unit['base_position']:
                stage = 6
            if stage == 6 and previous and previous['state'] == 'refilling' and current['water_litres'] > previous['water_litres'] and current['fuel_minutes'] > previous['fuel_minutes']:
                counts['completed_cycles'] += 1
                stage = 0
    counts['cases_with_drops'] = int(counts['drops'] > 0)
    counts['cases_with_completed_cycles'] = int(counts['completed_cycles'] > 0)
    return counts, errors


def run(private, inputs, network, implementation):
    totals, errors = Counter(), Counter()
    for case in REAL_MAP_V2_CASE_IDS:
        path = Path(private) / (case + '.json.gz')
        if not path.exists():
            errors['missing_case'] += 1
            continue
        try:
            with gzip.open(path, 'rt') as stream:
                data = json.load(stream)
            scenario = build_scenario(inputs, network, case)
            counts, findings = inspect_case(data, scenario, inputs, implementation)
            totals.update(counts)
            errors.update(findings)
            totals['cases_inspected'] += 1
        except (ValueError, KeyError, TypeError, IndexError, OSError):
            errors['invalid_case_data'] += 1
    status = 'failed' if errors else 'passed' if totals['completed_cycles'] else 'not_established'
    return dict(status=status, development_cases=True, totals=dict(totals), findings=dict(errors),
                limitations=['Uses recorded replay-verification results, not a new replay.',
                             'Minute audit omits clearance; authorization and accepted dispatch are corroborating evidence, not a direct clearance record.',
                             'Exercise solar and wind gates are modeled assumptions, not real flight authorization.',
                             'A case without drops is not necessarily unsafe or a failure.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path, help='Plausibility run directory or its private subdirectory')
    parser.add_argument('--inputs', type=Path, default=ROOT / '.runtime/estimated-incident-day-inputs-final.json')
    parser.add_argument('--network', type=Path, default=ROOT / 'data/coastal_mobility_v1/network.json')
    parser.add_argument('--output', type=Path, default=Path('/tmp/active-aircraft-review.json'))
    args = parser.parse_args()
    try:
        inputs, network_doc = pinned(args.inputs, INPUT_PIN), pinned(args.network, NETWORK_PIN)
        network = RoadNetwork(network_doc, NETWORK_PIN)
        private = args.run / 'private' if (args.run / 'private').is_dir() else args.run
        report = run(private, inputs, network, implementation_digest())
    except (ValueError, OSError) as error:
        parser.exit(2, f'Aircraft coverage check failed: {error}\n')
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps(report))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
