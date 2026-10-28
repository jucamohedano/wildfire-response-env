"""Run three CPU-only routed development episodes and verify their public replays."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import resource
import time

from wildfire_core.mobility import CASE_IDS, CoastalMobilityEnv, MobilityBaseline


def run(options):
    output = Path(options.output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    episodes = []
    for task_id in CASE_IDS:
        env = CoastalMobilityEnv(options.prepared_inputs, options.topographic_inputs,
            options.mobility_network, expected_network_sha256=options.mobility_sha256,
            expected_prepared_sha256=options.prepared_sha256,
            expected_topographic_sha256=options.topographic_sha256,
            slope_enabled=True, allow_synthetic_conditions=True)
        observation, _ = env.reset(task_id)
        policy = MobilityBaseline(prefer_aircraft=True)
        errors, positions, route_modes = [], {}, set()
        while True:
            action = {} if task_id.endswith('002') else policy(observation)
            observation, reward, terminated, truncated, info = env.step(action)
            if info.get('error'):
                errors.append(dict(decision=observation['decision'], error=info['error']))
            for unit in observation['resources']:
                positions.setdefault(unit['id'], set()).add(tuple(unit['map_position']))
                if unit.get('movement'):
                    route_modes.update(leg['mode'] for leg in unit['movement']['legs'])
                if not unit['observed_at'] <= unit['received_at'] <= observation['minute']:
                    raise AssertionError('Noncausal public movement packet')
            if terminated or truncated:
                break
        verified = env.verify_episode()
        trajectory = json.dumps(env.trajectory(), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        path = output / (task_id + '.json.gz')
        with path.open('wb') as stream:
            with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0, filename='') as archive:
                archive.write(trajectory)
        episodes.append(dict(task_id=task_id, decisions=observation['decision'],
            objective_score=env.reward, verified=verified['passed'],
            boundary_censored=env._core.boundary_censored, terminated=terminated, truncated=truncated,
            action_errors=errors, moving_resources=sorted(identifier for identifier, fixes in positions.items() if len(fixes)>1),
            observed_route_modes=sorted(route_modes), public_trajectory=path.name,
            trajectory_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            trajectory_bytes=len(trajectory), compressed_bytes=path.stat().st_size))
        print(json.dumps(episodes[-1]), flush=True)
    summary = dict(version='coastal-mobility-smoke-v1', workers=1, episodes=episodes,
        elapsed_seconds=round(time.perf_counter()-started, 3),
        peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, 2),
        road_network_sha256=options.mobility_sha256,
        limitations='Development smoke only. Synthetic speeds, fleet, daytime and operations; not historical or held-out evaluation.')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared-inputs', 'prepared-sha256', 'topographic-inputs',
                 'topographic-sha256', 'mobility-network', 'mobility-sha256', 'output'):
        parser.add_argument('--'+name, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2))


if __name__ == '__main__':
    main()
