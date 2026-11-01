"""Build an isolated local development bundle from a verified pinned release.

Never uploads, modifies the source package, or writes a dataset card. Old
validation evidence is excluded because it describes different scenario IDs.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil

import pyarrow as pa
import pyarrow.parquet as pq

from eval.hub_runtime import _read, _path, _transport_metadata, MAX_MANIFEST_BYTES, verify_package
from envs.wildfire_response.incident_day.environment import implementation_digest
from wildfire_core.catalog_lifecycle import active_day_case_ids, registry
from wildfire_core.incident_day import IncidentDayEnv

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'data/hub_runtime_v1/release.json'


def build(source, output):
    source, requested = Path(source).resolve(strict=True), Path(output).absolute()
    output = requested.parent.resolve(strict=True) / requested.name
    if output.exists() or output.is_symlink() or source == output or source in output.parents:
        raise ValueError('Output must be a fresh directory outside the source package')
    raw = _read(source, 'manifest.json', MAX_MANIFEST_BYTES)
    lock = json.loads(LOCK.read_text())
    if hashlib.sha256(raw).hexdigest() != lock['manifest_sha256']:
        raise ValueError('Source manifest does not match the immutable release lock')
    manifest = json.loads(raw)
    expected = {str(_path(item['path'])) for item in manifest['files']}
    found = set()
    for base, dirs, names in os.walk(source, followlinks=False):
        for name in dirs + names:
            item = Path(base) / name
            if item.is_symlink() or not (item.is_dir() or item.is_file()):
                raise ValueError('Source package has a symlink or special file')
        for name in names:
            relative = (Path(base) / name).relative_to(source).as_posix()
            if relative != 'manifest.json' and name.lower() != 'readme.md' and not _transport_metadata(relative):
                found.add(relative)
    if found != expected:
        raise ValueError('Source package inventory mismatch')
    # Verify every original payload before copying; don't bless a damaged input.
    payload = {}
    for entry in manifest['files']:
        name = str(_path(entry['path']))
        data = _read(source, name, entry['bytes'])
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Source package inventory mismatch: ' + name)
        if name != 'tasks/development.parquet' and name != 'provenance/lifecycle.json' and not name.startswith('validation/'):
            payload[name] = data
    lifecycle = registry()
    runtimes = copy.deepcopy(manifest['runtimes'])
    tasks = []
    for name, entry in sorted(runtimes.items()):
        inputs = json.loads(payload[entry['inputs']]); network = json.loads(payload[entry['network']])
        ids = active_day_case_ids(entry['inputs_sha256'], entry['network_sha256'])
        entry['scenario_ids'] = list(ids)
        env = IncidentDayEnv(inputs, network, expected_inputs_sha256=entry['inputs_sha256'],
                             expected_network_sha256=entry['network_sha256'],
                             allow_unapproved_data=entry['allow_unapproved_data'])
        additional = [case for catalog in lifecycle['catalogs']
                      if catalog.get('id') == 'incident_day_realmap_development_v1'
                      and (catalog.get('inputs_sha256'), catalog.get('network_sha256')) ==
                          (entry['inputs_sha256'], entry['network_sha256'])
                      for case in catalog['case_ids']]
        if additional:
            entry['additional_scenario_ids'] = additional
        for index, case_id in enumerate(tuple(ids) + tuple(additional)):
            env.reset(case_id)
            tasks.append(dict(task_id=case_id, runtime_id=name, split='development',
                related_exercise_group=f'{name}-development-{index:03}', scenario_sha256=env._scenario['sha256'],
                horizon_minutes=1440, inputs_ref=entry['inputs'], network_ref=entry['network'],
                synthetic=True, historical_replay=False, training_eligible=False))
    output.mkdir(mode=0o700, parents=False)
    try:
        for name, data in payload.items():
            target = output / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        lifecycle_path = output / 'provenance/lifecycle.json'
        lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
        lifecycle_path.write_text(json.dumps(lifecycle, sort_keys=True, separators=(',', ':')) + '\n')
        tasks_path = output / 'tasks/development.parquet'; tasks_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(tasks), tasks_path, compression='zstd')
        files = []
        for path in sorted(output.rglob('*')):
            if path.is_file():
                data = path.read_bytes(); files.append(dict(path=path.relative_to(output).as_posix(),
                    sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)))
        result = copy.deepcopy(manifest)
        result.update(release_version='0.4.0-dev', source_revision='working-tree:' + implementation_digest(),
                      engine_implementation_sha256=implementation_digest(), lifecycle_sha256=lifecycle['sha256'],
                      runtimes=runtimes, files=files, training_corpus_available=False,
                      publication_status='local_private_development', dataset_card_status='unchanged')
        result.pop('validation_report', None)
        result.setdefault('tables', {}).pop('validation_cells', None)
        result.setdefault('counts', {}).pop('validation_cells', None)
        result['counts']['tasks'] = len(tasks)
        (output / 'manifest.json').write_text(json.dumps(result, sort_keys=True, separators=(',', ':')) + '\n')
        verify_package(output)
    except BaseException:
        shutil.rmtree(output)
        raise
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True); p.add_argument('--output', required=True)
    args = p.parse_args(); result = build(args.source, args.output)
    print(json.dumps({'status': 'built', 'tasks': result['counts']['tasks'], 'runtimes': sorted(result['runtimes'])}))


if __name__ == '__main__': main()
