"""Synthetic source-converter and diagnostic-host regression tests."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case', ['converter', 'host', 'initial_censor'])
def test_source_diagnostic(case):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([
        str(ROOT / 'envs'), str(ROOT / 'envs/wildfire_extended_inputs_v1'),
        str(ROOT / 'envs/wildfire_response/core/src'),
        str(ROOT / 'envs/wildfire_containment_v1/src'), str(ROOT)]))
    result = subprocess.run([sys.executable, __file__, case], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def run(case):
    import copy
    import gzip
    import hashlib
    import json
    import tempfile
    import adapter_v1 as api
    from wildfire_expanded_incident_v1.source_converter_v1 import convert
    from wildfire_expanded_incident_v1.host import SourceGroundedDiagnosticV1

    n = 7
    grid = dict(width=n, height=n, crs='EPSG:25830', transform=[500, 0, 0, 0, -500, 3500],
                bounds=[0, 0, 3500, 3500], cell_metres=500)
    cells = [[dict(surface='land', fuel_status='source', source_fuel_class=4,
                   estimated_fuel=None, fuel_provenance={'kind': 'source'}) for x in range(n)] for y in range(n)]
    cells[0][0] = dict(surface='land', fuel_status='estimated', source_fuel_class=None,
                       estimated_fuel={'class': 4, 'method': 'test'}, fuel_provenance={'kind': 'estimate'})
    if case == 'initial_censor':
        cells[3][4] = dict(surface='unknown', fuel_status='source', source_fuel_class=4,
                           estimated_fuel=None, fuel_provenance={'kind': 'source'})
    resolution = dict(version='extended-map-resolution-evidence-v1', status='private_evidence_not_simulator_input',
                      grid=grid, cells=cells, terrain_candidate=[[100.] * n for _ in range(n)])
    resolution['sha256'] = api._hash(resolution)
    parent = dict(version='incident-day-inputs-v2', grid=copy.deepcopy(grid), network_sha256='b'*64)
    parent['sha256'] = api._hash(parent)
    cases = []
    for i in range(12):
        item = dict(id=f'incident-eval-v1-{i:03}', seed=100+i, initial_fire=[[3, 3]], sector_grid=[3, 3],
                    weather_schedule=[dict(minute=0, wind=[1, 0]), dict(minute=3, wind=[0, 1])],
                    events=[dict(minute=3, kind='wind')], wind=[1, 0], warmup_minutes=2,
                    centers=['center-0'])
        item['sha256'] = api._hash(item)
        cases.append(item)
    catalog = dict(version='wildfire-incident-eval-catalog-v1', inputs_sha256=parent['sha256'],
                   network_sha256=parent['network_sha256'], cases=cases)
    catalog['sha256'] = api._hash(catalog)
    def raises(exc, fn):
        try:
            fn()
        except exc:
            return
        raise AssertionError('expected exception ' + str(exc))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'road.gz'
        road = dict(version='extended-access-directed-road-candidate-v2', status='source_candidate_not_operational_access',
                    crs='EPSG:25830', bounds=grid['bounds'], nodes=[], arcs=[])
        with gzip.open(path, 'wt') as f:
            json.dump(road, f)
        road_hash = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
        def converted(**kwargs):
            return convert(resolution, parent, catalog, road_path=path, expected_resolution_sha256=resolution['sha256'],
                expected_parent_sha256=parent['sha256'], expected_catalog_sha256=catalog['sha256'],
                expected_road_sha256=road_hash, opt_in_source_grounded=kwargs.get('opt_in', True))
        if case == 'converter':
            raises(ValueError, lambda: converted(opt_in=False))
            doc, evidence, drafts = converted()
            assert doc['fuel'][0][0] is None
            assert evidence['fuel_provenance'][0][0]['estimate']['class'] == 4
            assert all(x == 'unknown' for row in doc['access'] for x in row)
            assert drafts['cases'][0]['initial_fire'] == [[3, 3]]
            raises(ValueError, lambda: convert(resolution, parent, catalog, road_path=path,
                expected_resolution_sha256=resolution['sha256'], expected_parent_sha256=parent['sha256'],
                expected_catalog_sha256=catalog['sha256'], expected_road_sha256='0'*64,
                opt_in_source_grounded=True))
            resolution['cells'][0][0]['surface'] = 'unknown'
            raises(ValueError, converted)
        else:
            doc, evidence, drafts = converted()
            config = dict(work_gain=2, max_strength=5, decay=.5, wind_factor=1, report_delay=2, wear_factor=.1)
            def host():
                return SourceGroundedDiagnosticV1(doc, expected_inputs_sha256=api._hash(doc),
                    expected_source_pins=doc['source_pins'], evidence=evidence,
                    expected_evidence_sha256=api._hash(evidence), drafts=drafts,
                    expected_drafts_sha256=drafts['sha256'], case_id=drafts['cases'][0]['id'],
                    containment_config=config, pressure_scale=1, opt_in_source_grounded=True)
            a = host()
            if case == 'initial_censor':
                assert a.situation()['status'] == 'censored'
                assert a.situation()['warmup_steps'] == 0
                raises(api.CensoredError, a.advance)
            else:
                assert a.situation()['warmup_steps'] == 2
                a.advance()
                saved = json.loads(json.dumps(a.checkpoint()))
                b = host()
                b.restore(saved)
                assert api._encoded(a.checkpoint()) == api._encoded(b.checkpoint())
                assert a.advance(5) == b.advance(5)
                assert a.situation()['status'] == 'blocked' and a.situation()['minute'] == 2
                raises(NotImplementedError, a.outcomes)
                raises(NotImplementedError, lambda: a.route_options('crew', [1, 1]))


if __name__ == '__main__':
    run(sys.argv[1])
