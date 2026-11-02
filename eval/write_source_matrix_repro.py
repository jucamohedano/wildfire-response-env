"""Write a private reproducibility manifest for a source-matrix report snapshot.

This compiles scenario definitions (including their bounded handover checks), but
never runs an incident day. Use a fresh output path outside the data package.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess

from eval.hub_runtime import _json, _read, make_environment, verify_package
from eval.validate_source_runtime_matrix import (
    VERSION as MATRIX_VERSION, POLICY_NAMES, checksum, expected_cells, validator_digest,
)
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day import outcomes as reward_module
from envs.wildfire_response.incident_day.outcomes import VERSION as REWARD_VERSION
from envs.wildfire_response.incident_day.scenarios import build_scenario
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.catalog_lifecycle import registry

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = 'es-ma-2021'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_identity():
    def run(*arguments):
        return subprocess.check_output(['git', '-C', str(ROOT), *arguments], text=True).strip()
    return {'revision': run('rev-parse', 'HEAD'),
            'working_tree_dirty': bool(run('status', '--porcelain', '--untracked-files=all'))}


def task_rows(data_dir, package):
    # Verify again the exact bytes that the table reader consumes.
    import pyarrow as pa
    import pyarrow.parquet as pq
    entry = next(item for item in package['files'] if item['path'] == 'tasks/development.parquet')
    raw = _read(Path(data_dir), entry['path'], entry['bytes'])
    if len(raw) != entry['bytes'] or sha256(raw) != entry['sha256']:
        raise ValueError('Development task table changed after package verification')
    return pq.read_table(pa.BufferReader(raw), columns=['task_id', 'runtime_id', 'split', 'scenario_sha256']).to_pylist()


def identity(cell):
    return tuple(cell[key] for key in ('runtime_id', 'case_id', 'policy'))


def build_manifest(report, report_raw, data_dir):
    package = verify_package(data_dir)
    git = git_identity()
    reward_sha256 = sha256(Path(reward_module.__file__).read_bytes())
    implementation = implementation_digest()
    lifecycle = registry()['sha256']
    validator = validator_digest()
    if report.get('version') != MATRIX_VERSION:
        raise ValueError('Report is not a supported source matrix')
    for field, expected in (('package_sha256', checksum(package)),
                            ('implementation_sha256', implementation),
                            ('lifecycle_sha256', lifecycle), ('validator_sha256', validator)):
        if report.get(field) != expected:
            raise ValueError(f'Report {field} differs from package or current code')
    if report.get('policies') != list(POLICY_NAMES):
        raise ValueError('Report policy set differs from the validator')
    expected = expected_cells(package, RUNTIME)
    expected_ids = [identity(cell) for cell in expected]
    supplied = report.get('expected_cells')
    cells = report.get('cells')
    if not isinstance(supplied, list) or [identity(cell) for cell in supplied] != expected_ids:
        raise ValueError('Report expected cells differ from package catalog')
    if not isinstance(cells, list):
        raise ValueError('Report cells missing')
    actual_ids = [identity(cell) for cell in cells]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected_ids):
        raise ValueError('Report cells do not match the expected matrix')
    entry = package['runtimes'][RUNTIME]
    scenario_ids = entry['additional_scenario_ids']
    rows = [row for row in task_rows(data_dir, package)
            if row['runtime_id'] == RUNTIME and row['task_id'] in scenario_ids]
    by_task = {row['task_id']: row for row in rows}
    if len(rows) != len(scenario_ids) or set(by_task) != set(scenario_ids):
        raise ValueError('Package task table does not uniquely cover source scenarios')
    env, loaded_entry = make_environment(data_dir, RUNTIME, allow_unapproved_data=True)
    if loaded_entry != entry:
        raise ValueError('Runtime entry changed during loading')
    cases = []
    for case_id in scenario_ids:
        scenario = build_scenario(env.inputs, env.network, case_id)
        scenario_hash = digest({key: val for key, val in scenario.items() if key != 'sha256'})
        if (scenario.get('id') != case_id or scenario.get('sha256') != scenario_hash
                or type(scenario.get('seed')) is not int
                or by_task[case_id]['split'] != 'development'
                or by_task[case_id]['scenario_sha256'] != scenario_hash):
            raise ValueError(f'Scenario differs from packaged task: {case_id}')
        cases.append({'id': case_id, 'seed': scenario['seed'], 'scenario_sha256': scenario_hash})
    hashes = {case['id']: case['scenario_sha256'] for case in cases}
    verified_cells = 0
    for cell in cells:
        pins = cell.get('pins')
        status = cell.get('status')
        if status not in ('not_run', 'incomplete', 'complete', 'blocked', 'failed'):
            raise ValueError('Unknown report cell status')
        if pins is None:
            if status not in ('not_run', 'incomplete'):
                raise ValueError('Finished cell has no scenario pins')
        elif (not isinstance(pins, dict) or pins.get('scenario_sha256') != hashes[cell['case_id']]
              or pins.get('inputs_sha256') != entry['inputs_sha256']
              or pins.get('network_sha256') != entry['network_sha256']):
            raise ValueError(f"Report cell pins differ: {cell['case_id']} / {cell['policy']}")
        else:
            verified_cells += 1
        outcome = cell.get('outcome')
        if outcome is None and status not in ('not_run', 'incomplete'):
            raise ValueError('Finished cell has no reward outcome')
        if outcome is not None and (not isinstance(outcome, dict) or outcome.get('reward_version') != REWARD_VERSION):
            raise ValueError('Report outcome reward version differs from current code')
    # Detect changes while compiling; never combine identities from different generations.
    if (verify_package(data_dir) != package or implementation_digest() != implementation
            or registry()['sha256'] != lifecycle or validator_digest() != validator
            or sha256(Path(reward_module.__file__).read_bytes()) != reward_sha256
            or git_identity() != git):
        raise ValueError('Package or code changed during manifest generation')
    raw_manifest = _read(Path(data_dir), 'manifest.json', 4 * 1024 * 1024)
    if _json(raw_manifest) != package:
        raise ValueError('Package manifest changed during generation')
    return dict(version='wildfire-source-matrix-repro-v1', private=True,
        report_sha256=sha256(report_raw), report_status=report.get('status'),
        report_cells_with_verified_pins=verified_cells, report_cells_total=len(cells),
        git=git, package_manifest_sha256=sha256(raw_manifest),
        package_identity_sha256=checksum(package), implementation_sha256=implementation,
        validator_sha256=validator, lifecycle_sha256=lifecycle, runtime_id=RUNTIME,
        inputs_sha256=entry['inputs_sha256'], network_sha256=entry['network_sha256'],
        reward_version=REWARD_VERSION, reward_sha256=reward_sha256,
        policies=list(POLICY_NAMES), scenarios=cases,
        note='Private synthetic development evidence; pending cells without pins have not been verified.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.data_dir.resolve(strict=True)
    output = args.output.absolute()
    if root == output.resolve() or root in output.resolve().parents:
        raise ValueError('Output must be outside the verified package')
    raw = args.report.read_bytes()
    report = _json(raw)
    manifest = build_manifest(report, raw, args.data_dir)
    encoded = (json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encoded)
    print(f"Wrote private manifest for {len(manifest['scenarios'])} scenarios; report status: {manifest['report_status']}")


if __name__ == '__main__':
    main()
