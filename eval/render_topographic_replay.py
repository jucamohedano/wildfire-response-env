"""Record a simple verified episode and render a local, evaluator-only HTML replay."""
import argparse
import copy
import json
from pathlib import Path

from wildfire_core.prepared import load_inputs
from wildfire_core.topographic import TopographicEnv, ProtectionBaseline, load_domain
from envs.wildfire_response.regional.ops.generator import digest


TEMPLATE = Path(__file__).with_name('templates') / 'topographic_replay.html'
VERSION = 'wildfire-topographic-visual-replay-v1'


def safe_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).replace(
        '<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def frame(environment, observation, action, reward):
    fire = [dict(column=column, row=row, heat=cell['heat'], burned=cell['burned'])
            for row, cells in enumerate(environment._core.sim.cells)
            for column, cell in enumerate(cells) if cell['heat'] > 0 or cell['burned'] > 0]
    return dict(minute=observation['minute'], decision=observation['decision'],
        reward=reward, total_reward=environment.reward,
        terminated=environment._core.terminated, truncated=environment._core.truncated,
        boundary_censored=observation['topographic_status']['boundary_censored'],
        world_fire=fire, sensor_memory=copy.deepcopy(observation['sensor_memory']),
        districts=copy.deepcopy(observation['districts']), resources=copy.deepcopy(observation['resources']),
        reports=copy.deepcopy(observation['reports']), acknowledgements=copy.deepcopy(observation['acknowledgements']),
        protection_orders=copy.deepcopy(observation['protection_orders']),
        pending_deliveries=copy.deepcopy(observation['pending_deliveries']),
        weather=copy.deepcopy(observation['weather_context']), wind=observation['wind'][:],
        daylight=observation['daylight'], aircraft_operable=observation['aircraft_operable'],
        action=copy.deepcopy(action))


def record(prepared, domain, policy_name):
    environment = TopographicEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
        expected_topographic_sha256=domain['sha256'], slope_enabled=True, allow_synthetic_conditions=True)
    observation, _ = environment.reset('topo-dev-000')
    frames = [frame(environment, observation, {}, 0)]
    baseline = ProtectionBaseline(prefer_shelter=False)
    while True:
        action = baseline(observation) if policy_name == 'response' else {}
        observation, reward, terminated, truncated, info = environment.step(action)
        if info['error'] is not None:
            raise ValueError('The recorded baseline submitted an invalid action: ' + info['error'])
        frames.append(frame(environment, observation, action, reward))
        if terminated or truncated:
            break
    verification = environment.verify_episode()
    return dict(policy=policy_name, frames=frames, reward=environment.reward,
        verified=verification['passed'], public_trace_sha256=digest(environment.trajectory()),
        objective_flags=verification['objective_result']['by_kind'])


def render(prepared_source, topographic_source, prepared_sha256, topographic_sha256, output):
    prepared = load_inputs(prepared_source, expected_sha256=prepared_sha256)
    domain = load_domain(topographic_source, prepared, topographic_sha256)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('Choose a fresh private replay directory')
    crop = domain['crop']
    left, top, width, height = (crop[key] for key in ('left', 'top', 'width', 'height'))
    data = dict(version=VERSION, role='private_evaluator_visualization_not_actor_observation',
        task_id='topo-dev-000', seed=771100, slope_enabled=True,
        prepared_inputs_sha256=prepared_sha256, topographic_inputs_sha256=topographic_sha256,
        grid=domain['grid'], heights=domain['elevations_metres'],
        fuel_classes=[row[left:left + width] for row in prepared['fuel_classes'][top:top + height]],
        runs=[record(prepared, domain, name) for name in ('response', 'passive')])
    payload = safe_json(data)
    template = TEMPLATE.read_text()
    if template.count('__REPLAY_DATA__') != 1:
        raise ValueError('Expected one safe JSON data slot')
    output.mkdir(parents=True)
    (output / 'index.html').write_text(template.replace('__REPLAY_DATA__', payload))
    (output / 'replay.json').write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    summary = dict(role=data['role'], task_id=data['task_id'], seed=data['seed'],
        runs=[dict(policy=run['policy'], frames=len(run['frames']), reward=run['reward'],
                   minutes=run['frames'][-1]['minute'], verified=run['verified'],
                   public_trace_sha256=run['public_trace_sha256']) for run in data['runs']])
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-inputs', required=True)
    parser.add_argument('--prepared-sha256', required=True)
    parser.add_argument('--topographic-inputs', required=True)
    parser.add_argument('--topographic-sha256', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(render(args.prepared_inputs, args.topographic_inputs, args.prepared_sha256,
                            args.topographic_sha256, args.output), indent=2))


if __name__ == '__main__':
    main()
