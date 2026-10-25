"""One-CPU-worker stress smoke batch with frozen splits and isolated episode artifacts."""
import argparse
import gzip
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

from eval.build_malaga_provenance import artifact, encoded
from eval.stress_storage import RunStore
from wildfire_core.prepared import load_inputs
from wildfire_core.stress import StressEnv, build_catalog
from wildfire_core.topographic import ProtectionBaseline, load_domain
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.regional.stress import implementation_digest


VERSION = 'wildfire-topographic-stress-evaluation-v1'
SMOKE_GROUP_INDICES = tuple(range(6)) + tuple(range(18, 24))
POLICIES = ('passive', 'evacuate', 'shelter', 'blanket')


def public_catalog(catalog):
    return dict(version=catalog['version'], catalog_sha256=catalog['sha256'],
        prepared_inputs_sha256=catalog['prepared_inputs_sha256'],
        topographic_inputs_sha256=catalog['topographic_inputs_sha256'], source_groups=1,
        split_groups=catalog['split_groups'], variants_per_group=catalog['variants_per_group'],
        split_basis=catalog['split_basis'],
        groups=[{key: group[key] for key in ('id', 'split', 'configuration_sha256', 'cases')}
                for group in catalog['groups']])


def select_cases(catalog, split, *, full, consume_held_out):
    if split not in ('train', 'validation', 'test'):
        raise ValueError('Unknown split')
    if split != 'train' and not consume_held_out:
        raise ValueError('Held-out policy evaluation requires --consume-held-out; do not tune on it')
    if not full and split != 'train':
        raise ValueError('The registered smoke batch is training-only; choose --full for a held-out split')
    groups = ([group for group in catalog['groups'] if group['split'] == split] if full else
              [catalog['groups'][index] for index in SMOKE_GROUP_INDICES])
    return [(group, case) for group in groups for case in (group['cases'] if full else group['cases'][:1])]


def episode(prepared, domain, catalog, task_id, policy_name, enabled):
    environment = StressEnv(prepared, domain, catalog, expected_prepared_sha256=prepared['sha256'],
        expected_topographic_sha256=domain['sha256'], expected_catalog_sha256=catalog['sha256'],
        slope_enabled=enabled, allow_synthetic_conditions=True)
    observation, _ = environment.reset(task_id)
    baseline = ProtectionBaseline(prefer_shelter=policy_name == 'shelter')
    invalid = 0
    while True:
        action = {} if policy_name == 'passive' else baseline(observation)
        if policy_name == 'blanket' and observation['minute'] == 0:
            action['protection_orders'] = [dict(recipient=group['id'], action='evacuate', channel='broadcast',
                                               resource_id=None, supersedes=None) for group in observation['districts']]
        observation, _, terminated, truncated, info = environment.step(action)
        invalid += info['error'] is not None
        if terminated or truncated:
            break
    trace = environment.trajectory()
    signature = dict(public_trace_sha256=digest(trace), reward=environment.reward,
        physical_cells_sha256=digest(environment._core.sim.cells),
        exposed_people=sum(group['exposed'] for group in observation['districts']),
        invalid_actions=invalid, decisions=observation['decision'],
        boundary_censored=info['boundary_censored'], terminated=terminated, truncated=truncated)
    return environment, trace, signature


def run(prepared_source, domain_source, prepared_sha256, domain_sha256, catalog_sha256, output, *,
        slope_enabled, split='train', full=False, consume_held_out=False, repeat=False,
        policies=('passive', 'evacuate')):
    if not policies or len(set(policies)) != len(policies) or any(policy not in POLICIES for policy in policies):
        raise ValueError('Choose unique supported baseline policies')
    prepared = load_inputs(prepared_source, expected_sha256=prepared_sha256)
    domain = load_domain(domain_source, prepared, domain_sha256)
    catalog = build_catalog(prepared, domain)
    if catalog['sha256'] != catalog_sha256:
        raise ValueError('Catalog changed; review a new version before running policies')
    selected = select_cases(catalog, split, full=full, consume_held_out=consume_held_out)
    store = RunStore(output)
    (store.root / 'private/catalog.json').write_bytes(encoded(catalog))
    (store.root / 'public/catalog_manifest.json').write_bytes(encoded(public_catalog(catalog)))
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], text=True, capture_output=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(['git', 'status', '--porcelain'], text=True, capture_output=True, check=True).stdout)
    manifest = dict(version=VERSION, status='running', engine_revision=revision, working_tree_dirty=dirty,
        implementation_sha256=implementation_digest(), prepared_inputs_sha256=prepared_sha256,
        topographic_inputs_sha256=domain_sha256, catalog_sha256=catalog_sha256,
        slope_enabled=slope_enabled, split=split, workers=1, repeat=repeat, policies=list(policies),
        selection='full_split' if full else 'registered_training_smoke_12_groups_variant0',
        selected_case_ids=[case['id'] for _, case in selected], episodes=[],
        held_out_policy_outcomes_consumed=split != 'train',
        scope='Synthetic configuration-level stress evaluation on one landscape, not real incident generalisation',
        sources=[artifact(Path.cwd(), relative) for relative in (
            'envs/wildfire_response/core/src/envs/wildfire_response/regional/stress.py',
            'eval/run_topographic_stress.py', 'eval/stress_storage.py')])
    manifest_path = store.root / 'manifest.json'
    manifest_path.write_bytes(encoded(manifest))
    started = time.monotonic()
    try:
        for group, case in selected:
            for policy_name in policies:
                environment, trace, signature = episode(prepared, domain, catalog, case['id'], policy_name, slope_enabled)
                verified = environment.verify_episode()
                if repeat:
                    repeated, _, repeated_signature = episode(prepared, domain, catalog, case['id'], policy_name, slope_enabled)
                    if signature != repeated_signature:
                        raise ValueError('Same seeded stress policy did not reproduce exactly')
                    del repeated
                record = store.write_episode(case['id'], policy_name, trace, verified,
                                             split=group['split'], group_id=group['id'])
                exported = json.loads(gzip.decompress((store.root / record['trajectory_path']).read_bytes()))
                if exported != trace or record['reward'] != verified['objective_score']:
                    raise ValueError('Serialized trajectory differs from the verified public replay')
                record.update(signature, repeated_exactly=repeat, reward_verified_by_replay=True,
                              canonical_export_equals_verified_trace=True)
                manifest['episodes'].append(record)
                manifest_path.write_bytes(encoded(manifest))
                print(json.dumps(dict(task_id=case['id'], policy=policy_name, reward=record['reward'],
                    exposed_people=record['exposed_people'], boundary_censored=record['boundary_censored'],
                    invalid_actions=record['invalid_actions'])), flush=True)
        manifest['status'] = 'complete'
    except Exception as error:
        manifest.update(status='failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        manifest.update(elapsed_seconds=round(time.monotonic() - started, 3),
            peak_process_rss_bytes=peak if sys.platform == 'darwin' else peak * 1024,
            primary_rollouts=len(manifest['episodes']),
            repeated_rollouts=len(manifest['episodes']) if repeat else 0)
        manifest_path.write_bytes(encoded(manifest))
    rows = manifest['episodes']
    summary = dict(version=VERSION, status='complete', catalog_sha256=catalog_sha256,
        split=split, primary_rollouts=len(rows), repeated_rollouts=len(rows) if repeat else 0,
        boundary_censored=sum(row['boundary_censored'] for row in rows),
        invalid_actions=sum(row['invalid_actions'] for row in rows),
        elapsed_seconds=manifest['elapsed_seconds'], peak_process_rss_bytes=manifest['peak_process_rss_bytes'],
        policies={name: dict(episodes=len(selected), mean_reward=sum(row['reward'] for row in rows if row['policy'] == name) / len(selected),
            exposed_people=sum(row['exposed_people'] for row in rows if row['policy'] == name),
            censored=sum(row['boundary_censored'] for row in rows if row['policy'] == name)) for name in policies},
        all_rewards_replay_verified=all(row['reward_verified_by_replay'] for row in rows),
        held_out_policy_outcomes_consumed=split != 'train',
        physical_calibration_established=False, training_release_ready=False)
    (store.root / 'summary.json').write_bytes(encoded(summary))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-inputs', required=True)
    parser.add_argument('--prepared-sha256', required=True)
    parser.add_argument('--topographic-inputs', required=True)
    parser.add_argument('--topographic-sha256', required=True)
    parser.add_argument('--catalog-sha256', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--slope-enabled', choices=('true', 'false'), required=True)
    parser.add_argument('--split', choices=('train', 'validation', 'test'), default='train')
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--consume-held-out', action='store_true')
    parser.add_argument('--repeat', action='store_true')
    parser.add_argument('--policies', choices=POLICIES, nargs='+', default=['passive', 'evacuate'])
    options = parser.parse_args()
    summary = run(options.prepared_inputs, options.topographic_inputs,
        options.prepared_sha256, options.topographic_sha256, options.catalog_sha256, options.output,
        slope_enabled=options.slope_enabled == 'true', split=options.split, full=options.full,
        consume_held_out=options.consume_held_out, repeat=options.repeat, policies=options.policies)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
