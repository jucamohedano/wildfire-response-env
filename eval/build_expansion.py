"""Compile expanded task metadata and optionally smoke-test training cases on one CPU."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import time

from wildfire_core.expansion import ExpandedEnv, build_catalog, coverage, public_manifest
from envs.wildfire_response.regional.benchmark_v1.baselines import PublicDispatcher
from envs.wildfire_response.regional.expansion.catalog import FAMILIES
from envs.wildfire_response.regional.expansion.environment import implementation_digest
from envs.wildfire_response.regional.prepared.data import load_inputs
from envs.wildfire_response.regional.topographic.data import load_domain


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def compile_dataset(output, prepared, domain, *, smoke=False, seconds=600):
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError('Choose a CPU budget between 1 and 3600 seconds')
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh output directory; never overwrite a frozen dataset')
    started = time.monotonic()
    catalog = build_catalog(prepared, domain)
    write_json(output / 'private/catalog.json', catalog)
    write_json(output / 'public/manifest.json', public_manifest(catalog))
    write_json(output / 'coverage.json', coverage(catalog))
    root = Path(__file__).resolve().parents[1]
    source = root / 'envs/wildfire_response/core/src/envs/wildfire_response/regional/expansion'
    provenance = dict(version='wildfire-synthetic-expansion-provenance-v1',
        catalog_sha256=catalog['sha256'], prepared_inputs_sha256=prepared['sha256'],
        topographic_inputs_sha256=domain['sha256'], parent_catalog_sha256=catalog['parent_catalog_sha256'],
        engine_and_expansion_sha256=implementation_digest(),
        source_revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        compiler_files_sha256={str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in [*sorted(source.glob('*.py')), Path(__file__).resolve()]},
        source_registry='data/malaga_2021/provenance.json',
        generation='Seeded synthetic configurations and calls, not historical trajectories',
        new_real_incidents=0, source_landscapes=1, workers=1,
        split_policy='Matching ignition/claimed-location layouts stay together; exact frozen v1 configurations excluded',
        distribution='Local private candidate; no source-rights expansion or automatic Hub upload',
        reward='Unchanged benchmark v1: 80% population protection / 20% unburned fraction',
        limitations=['Previous v1 outcomes informed environment development; not blind to prior research',
                     'No new geography, daylight, aircraft, population roster or longer horizons',
                     'Marginal sampling, not exhaustive Cartesian or pairwise coverage',
                     'No held-out outcome evaluation, policy learning or operational calibration'])
    write_json(output / 'provenance.json', provenance)
    summary = dict(version='wildfire-expansion-smoke-v1', catalog_sha256=catalog['sha256'],
        status='compiled', split='train', rank_eligible=False, workers=1, cases=[],
        note='First variant of one training configuration per family; not a benchmark leaderboard')
    try:
        if smoke:
            summary['status'] = 'running'
            for family in FAMILIES:
                group = next(group for group in catalog['groups']
                             if group['family'] == family and group['split'] == 'train')
                identifier = group['cases'][0]['id']
                for policy in ('passive', 'dispatcher'):
                    environment = ExpandedEnv(prepared, domain,
                        expected_prepared_sha256=prepared['sha256'], expected_topographic_sha256=domain['sha256'])
                    observation, _ = environment.reset(identifier)
                    actor = PublicDispatcher(policy)
                    invalid = 0
                    while True:
                        if time.monotonic() - started >= seconds:
                            summary['status'] = 'budget_stopped'
                            return summary
                        observation, _, terminated, truncated, info = environment.step(actor(observation))
                        invalid += bool(info['error'])
                        if terminated or truncated:
                            break
                    verification = environment.verify_episode()
                    trajectory = environment.trajectory()
                    encoded = gzip.compress(json.dumps(trajectory, sort_keys=True, allow_nan=False).encode(), mtime=0)
                    path = output / 'public/trajectories' / identifier / (policy + '.json.gz')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(encoded)
                    write_json(output / 'private' / identifier / (policy + '-audit.json'), environment.private_audit())
                    row = dict(task_id=identifier, family=family, policy=policy, decisions=observation['decision'],
                        minute=observation['minute'], invalid_actions=invalid, replay_verified=verification['passed'],
                        training_eligible=verification['outcomes']['suitable_for_training'],
                        trajectory=str(path.relative_to(output)), trajectory_sha256=hashlib.sha256(encoded).hexdigest(),
                        **verification['outcomes'])
                    summary['cases'].append(row)
                    write_json(output / 'summary.json', summary)
                    print(json.dumps({key: row[key] for key in ('family', 'policy', 'graded_score', 'replay_verified')}), flush=True)
            summary['status'] = 'complete'
        return summary
    except Exception as error:
        summary.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        summary.update(elapsed_seconds=round(time.monotonic() - started, 3),
                       peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2))
        write_json(output / 'summary.json', summary)


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ('prepared-inputs', 'prepared-sha256', 'topographic-inputs', 'topographic-sha256', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--seconds', type=int, default=600)
    args = parser.parse_args()
    prepared = load_inputs(args.prepared_inputs, expected_sha256=args.prepared_sha256)
    domain = load_domain(args.topographic_inputs, prepared, args.topographic_sha256)
    compile_dataset(args.output, prepared, domain, smoke=args.smoke, seconds=args.seconds)


if __name__ == '__main__':
    main()
