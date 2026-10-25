"""One-worker slope on/off comparisons, exact repetitions, exported replay and censor probes."""
import argparse
import gzip
import json
from pathlib import Path
import resource
import sys
import time

from ..ops.baselines import passive
from ..ops.generator import digest
from ..prepared.data import load_inputs
from ..protection.environment import ProtectionBaseline
from .data import crop_inputs, load_domain
from .environment import BOUNDARY_CASE_ID, CASE_IDS, TopographicEnv, task_manifest, verify_topographic_trajectory


def episode(prepared, domain, task_id, policy_name, enabled):
    environment = TopographicEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
        expected_topographic_sha256=domain['sha256'], slope_enabled=enabled, allow_synthetic_conditions=True)
    observation, _ = environment.reset(task_id)
    policy = passive if policy_name == 'passive' else ProtectionBaseline(prefer_shelter=policy_name == 'shelter')
    invalid = 0
    while True:
        observation, _, terminated, truncated, info = environment.step(policy(observation))
        invalid += info['error'] is not None
        if terminated or truncated:
            break
    verified = environment.verify_episode()
    trace = environment.trajectory()
    cells = environment._core.sim.cells
    result = dict(task_id=task_id, policy=policy_name, slope_enabled=enabled, reward=environment.reward,
        objectives=verified['objective_result']['by_kind'], components=verified['core_accounting']['components'],
        boundary_censored=verified['boundary_censored'], suitable_for_training=verified['suitable_for_training'],
        terminated=terminated, truncated=truncated, decisions=observation['decision'], minutes=observation['minute'],
        exposed_people=sum(group['exposed'] for group in observation['districts']), invalid_actions=invalid,
        physical_cells_sha256=digest(cells), actions_sha256=digest([item['action'] for item in trace['transitions']]),
        public_trace_sha256=digest(trace), verified=verified['passed'])
    return result, trace


def run(prepared_source, domain_source, prepared_sha256, domain_sha256, output, *, repeat=False):
    prepared = load_inputs(prepared_source, expected_sha256=prepared_sha256)
    domain = load_domain(domain_source, prepared, domain_sha256)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    rows = []
    for task_id in (*CASE_IDS, BOUNDARY_CASE_ID):
        names = ('passive',) if task_id == BOUNDARY_CASE_ID else ('passive', 'evacuate', 'shelter')
        for name in names:
            for enabled in (False, True):
                result, trace = episode(prepared, domain, task_id, name, enabled)
                if repeat:
                    repeated, _ = episode(prepared, domain, task_id, name, enabled)
                    if repeated != result:
                        raise ValueError('Same-seed topographic rollout did not reproduce exactly')
                    result['repeated_exactly'] = True
                destination = output / task_id / name / ('slope_on' if enabled else 'slope_off')
                destination.mkdir(parents=True)
                path = destination / 'trajectory.json.gz'
                with gzip.open(path, 'wt', encoding='utf-8') as stream:
                    json.dump(trace, stream, ensure_ascii=False, allow_nan=False)
                with gzip.open(path, 'rt', encoding='utf-8') as stream:
                    decoded = json.load(stream)
                replay = verify_topographic_trajectory(decoded, prepared, domain)
                if digest(decoded) != result['public_trace_sha256'] or replay['total_score'] != result['reward']:
                    raise ValueError('Exported topographic JSON trace failed exact reward replay')
                result['exported_replay_verified'] = True
                rows.append(result)
                print(json.dumps(result), flush=True)
    comparisons = []
    for task_id in CASE_IDS:
        for name in ('passive', 'evacuate', 'shelter'):
            off, on = [next(row for row in rows if row['task_id'] == task_id and row['policy'] == name
                           and row['slope_enabled'] is enabled) for enabled in (False, True)]
            comparisons.append(dict(task_id=task_id, policy=name, same_seed_and_domain=True,
                same_committed_actions=off['actions_sha256'] == on['actions_sha256'],
                changed_physical_cells=off['physical_cells_sha256'] != on['physical_cells_sha256'],
                off_reward=off['reward'], on_reward=on['reward'],
                interpretation='Same reactive policy can issue different actions when observations differ'))
    probes = [row for row in rows if row['task_id'] == BOUNDARY_CASE_ID]
    if not all(row['boundary_censored'] and not row['suitable_for_training'] and row['reward'] == 0 for row in probes):
        raise ValueError('Boundary contact must censor, not earn a training success')
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary = dict(version='wildfire-topographic-comparison-v1', prepared_inputs_sha256=prepared_sha256,
        topographic_inputs_sha256=domain_sha256, manifest=task_manifest(crop_inputs(prepared, domain), domain),
        scope='Single source landscape, synthetic slope coupling; not real-world validation or held-out training evaluation',
        primary_rollouts=len(rows), repeated_rollouts=len(rows) if repeat else 0, workers=1,
        elapsed_seconds=round(time.monotonic() - started, 3),
        peak_process_rss_bytes=peak if sys.platform == 'darwin' else peak * 1024,
        all_verified=all(row['verified'] and row['exported_replay_verified'] for row in rows),
        comparisons=comparisons, results=rows)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-inputs', required=True)
    parser.add_argument('--prepared-sha256', required=True)
    parser.add_argument('--topographic-inputs', required=True)
    parser.add_argument('--topographic-sha256', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeat', action='store_true')
    args = parser.parse_args()
    run(args.prepared_inputs, args.topographic_inputs, args.prepared_sha256, args.topographic_sha256,
        args.output, repeat=args.repeat)


if __name__ == '__main__':
    main()
