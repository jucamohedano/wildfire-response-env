"""Inspect existing verified rollouts for cross-component causal invariants, without rerunning them."""
import argparse
import gzip
import json
from pathlib import Path

from eval.build_malaga_provenance import artifact, encoded
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.regional.prepared.data import utc_time


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inspect_trace(trace):
    observations = [trace['initial_observation'], *(step['observation'] for step in trace['transitions'])]
    coverage = set()
    for index, observation in enumerate(observations):
        minute = observation['minute']
        require(minute == index * trace['ticks_per_action'], 'Decision clock diverged')
        require(not {'world_fire', 'future_events', 'historical_reference'} & observation.keys(), 'Private state in actor view')
        now = utc_time(observation['weather_context']['now_utc'])
        for report in observation['reports']:
            require(report['observed_at'] <= report['received_at'] <= minute, 'Future report evidence')
            if report['observed_at'] < report['received_at']:
                coverage.add('delayed_reports')
        for weather in observation['weather_context']['records']:
            require(utc_time(weather['observed_at_utc']) <= utc_time(weather['received_at_utc']) <= now,
                    'Future weather evidence')
        for resource in observation['resources']:
            require(0 <= resource['water_litres'] <= resource.get('capacity_litres', 0), 'Unbounded resource water')
            if resource.get('capacity_litres', 0) > resource['water_litres']:
                coverage.add('finite_water_consumption')
        for order in observation['protection_orders']:
            previous = order['issued_at']
            for field in ('delivered_at', 'acknowledged_at', 'executed_at'):
                value = order[field]
                if value is not None:
                    require(previous is not None and previous <= value <= minute, 'Protection lifecycle out of order')
                previous = value
            require(order['effect'] is None or order['executed_at'] is not None, 'Effect before execution')
            require(order['status'] != 'failed' or order['executed_at'] is None, 'Failed delivery executed')
        coverage.update(ack['kind'] for ack in observation['acknowledgements'])
        coverage.update(event['kind'] for event in observation['readback_events'])
        if observation['wind'] != observations[0]['wind']:
            coverage.add('wind_change')
    require(trace['transitions'] and (trace['transitions'][-1]['terminated'] or trace['transitions'][-1]['truncated']),
            'Only completed episodes qualify')
    return coverage


def analyse(root, rollout_directory):
    root, rollout_directory = Path(root).resolve(), Path(rollout_directory).resolve()
    summary = json.loads((rollout_directory / 'summary.json').read_text())
    require(summary['all_verified'], 'Runner did not verify every rollout')
    expected = {(f'topo-dev-{index:03}', policy, enabled) for index in range(6)
                for policy in ('passive', 'evacuate', 'shelter') for enabled in (False, True)}
    expected.update(('topo-boundary-probe', 'passive', enabled) for enabled in (False, True))
    actual = [(row['task_id'], row['policy'], row['slope_enabled']) for row in summary['results']]
    require(len(actual) == len(expected) and set(actual) == expected, 'Incomplete or duplicated rollout matrix')
    coverage, records, observations_checked = set(), [], 0
    for row in summary['results']:
        mode = 'slope_on' if row['slope_enabled'] else 'slope_off'
        path = rollout_directory / row['task_id'] / row['policy'] / mode / 'trajectory.json.gz'
        trace = json.loads(gzip.decompress(path.read_bytes()))
        require(digest(trace) == row['public_trace_sha256'], 'Export differs from verified trace')
        require(row['verified'] and row['repeated_exactly'] and row['exported_replay_verified'], 'Replay or repeat failed')
        require(sum(step['objective_reward'] for step in trace['transitions']) == row['reward'], 'Recorded rewards diverged')
        coverage.update(inspect_trace(trace))
        observations_checked += 1 + len(trace['transitions'])
        records.append(artifact(root, path.relative_to(root)))
    required = {'delayed_reports', 'finite_water_consumption', 'wind_change', 'readback_correction_sent',
                'readback_confirmed', 'resource_dispatched', 'resource_arrived', 'resource_ready',
                'clarification_answered', 'protection_delivery_failed', 'protection_acknowledged', 'protection_executed'}
    require(required <= coverage, 'Missing exercised components: ' + ', '.join(sorted(required - coverage)))
    probes = [row for row in summary['results'] if row['task_id'] == 'topo-boundary-probe']
    require(all(row['boundary_censored'] and not row['suitable_for_training'] and row['reward'] == 0 for row in probes),
            'Boundary escape earned credit or learning eligibility')
    return dict(version='wildfire-topographic-component-acceptance-v1', status='pass',
        scope='Synthetic development component integration, not held-out policy evaluation or real-world certification',
        checked_on='2026-10-25', rollouts=len(actual), repeat_runs=summary['repeated_rollouts'],
        tested_components=sorted(required), observed_event_kinds=sorted(coverage),
        observations_checked=observations_checked,
        trajectory_files=records, runner_summary=artifact(root, (rollout_directory / 'summary.json').relative_to(root)),
        implementation=artifact(root, 'eval/check_topographic_components.py'),
        limitations=['Invalid-action and reset isolation use separate targeted unit regressions, not these valid-policy rollouts.',
            'This checks selected public-view privacy fields, not a general information-flow proof.',
            'Station weather is delayed context; front weather, fire physics, travel, fleet and communications remain assumed.',
            'No independent terrain accuracy, fire spread calibration, operational safety or training readiness is certified.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--rollouts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    report = analyse(options.root, options.rollouts)
    with options.output.open('xb') as stream:
        stream.write(encoded(report))
    print(json.dumps({key: value for key, value in report.items() if key != 'trajectory_files'}, indent=2))


if __name__ == '__main__':
    main()
