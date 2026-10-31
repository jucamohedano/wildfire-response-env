"""Synthetic tests for validated inland mixed-surface assumptions."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case', ['factors_replay', 'mixed_spread', 'water_effect',
                                 'land_parity', 'ineligible', 'unknown_guard', 'coastal_audit_tamper'])
def test_mixed(case):
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
    import adapter_fire_v1 as original
    from wildfire_expanded_incident_v1.mixed_v2 import prepare_mixed_inputs, InteriorMixedFireV2, _compiler
    from wildfire_expanded_incident_v1 import source_gate_v1

    n = 7
    grid = dict(width=n, height=n, crs='EPSG:25830', transform=[500,0,0,0,-500,3500],
                bounds=[0,0,3500,3500], cell_metres=500)
    hist = {'10': 2490, '80': 10}
    def cell():
        return dict(surface='land', fuel_status='source', source_fuel_class=4,
                    estimated_fuel=None, fuel_provenance={'kind':'source'}, worldcover_samples={'10':2500},
                    osm_water_conflicts=[])
    cells = [[cell() for _ in range(n)] for _ in range(n)]
    cells[3][4].update(surface='unknown', worldcover_samples=hist)
    if case == 'land_parity':
        cells[3][4] = cell()
    strict = dict(grid=grid, cells=copy.deepcopy(cells), terrain_candidate=[[100.] * n for _ in range(n)])
    strict['sha256'] = api._hash(strict)
    water = dict(version='extended-map-resolution-evidence-v2', status='private_evidence_not_simulator_input',
                 parent_document_sha256=strict['sha256'], grid=grid, cells=copy.deepcopy(cells),
                 terrain_candidate=[[100.] * n for _ in range(n)])
    if case != 'land_parity':
        water['cells'][3][4]['osm_water_conflicts'] = [{'kind':'watercourse', 'id':'synthetic'}]
    if case == 'unknown_guard':
        water['cells'][2][3]['surface'] = 'unknown'
    water['sha256'] = api._hash(water)
    source_gate_v1.STRICT_SHA256 = strict['sha256']
    source_gate_v1.WATER_V2_SHA256 = water['sha256']
    evidence = dict(column=4,row=3,worldcover_samples=hist,fuel_source_class=4,terrain_candidate_mean=100.,
        dera_land_face_covers=True,osm_land_face_covers=True,coast_distance_m=1000.,osm_coast_distance_m=1000.,
        water_polygon_intersections=0,osm_area_conflicts=0)
    audit = dict(strict_sha256=strict['sha256'],v2_sha256=water['sha256'],
                 reviewed_cells=[] if case == 'land_parity' else [evidence],cases={})
    audit['sha256'] = api._hash(audit)
    trusted_manifest = dict(version='wildfire-private-mixed-audit-trust-v1',
        strict_sha256=strict['sha256'],water_v2_sha256=water['sha256'],audit_sha256=audit['sha256'],
        validation='independently-source-reconstructed-private-audit-v1')
    source_gate_v1.MIXED_AUDIT_MANIFEST_SHA256 = api._hash(trusted_manifest)
    compiler = _compiler()
    hypo = compiler.compile_interior_mixed(strict,water,audit,strict_pin=strict['sha256'],
                                            v2_pin=water['sha256'],audit_pin=audit['sha256'])
    def raises(exc, fn):
        try:
            fn()
        except exc:
            return
        raise AssertionError('expected ' + str(exc))
    with tempfile.TemporaryDirectory() as tmp:
        road = Path(tmp)/'road.gz'
        with gzip.open(road,'wt') as f:
            json.dump(dict(version='extended-access-directed-road-candidate-v2',
                status='source_candidate_not_operational_access',crs='EPSG:25830',bounds=grid['bounds'],
                nodes=[],arcs=[]),f)
        road_hash = hashlib.file_digest(road.open('rb'),'sha256').hexdigest()
        def inputs(scenario='nominal', opt=True):
            return prepare_mixed_inputs(strict, water, audit, hypo, strict_pin=strict['sha256'],
                water_v2_pin=water['sha256'],audit_pin=audit['sha256'],hypothesis_pin=hypo['sha256'],
                trusted_audit_manifest=trusted_manifest,road_path=road,road_pin=road_hash,
                scenario=scenario,opt_in_mixed_experiment=opt)
        config = dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
        def make(value):
            return InteriorMixedFireV2(value,seed=7,containment_config=config,pressure_scale=1,
                                       wind=[1,0],opt_in_mixed_experiment=True)
        if case == 'factors_replay':
            low, nominal, high = (make(inputs(k)) for k in ('low','nominal','high'))
            assert low._inputs.surface[3][4] == 'mixed'
            assert low._fire.cells[3][4]['initial_fuel'] == pytest.approx(.9)
            assert nominal._fire.cells[3][4]['initial_fuel'] == pytest.approx(.996)
            assert high._fire.cells[3][4]['initial_fuel'] == 1.
            assert all(low._inputs.access[y][x]=='unknown' for y in range(n) for x in range(n))
            old = original.ExpandedContainmentFireV1(nominal._inputs,seed=7,containment_config=config,
                                                     pressure_scale=1,wind=[1,0])
            assert not old.change({'ignite':[[3,3]]})  # v1 still censors mixed neighbour
            assert nominal.change({'ignite':[[4,3]]})
            for _ in range(5): assert nominal.step()
            saved = json.loads(json.dumps(nominal.snapshot()))
            resumed = make(inputs())
            resumed.restore(saved)
            for _ in range(5):
                assert nominal.step() == resumed.step()
                assert api._encoded(nominal.snapshot()) == api._encoded(resumed.snapshot())
        elif case == 'ineligible':
            raises(ValueError, lambda: inputs(opt=False))
            raises(ValueError, lambda: inputs('unknown'))
            corrupt = copy.deepcopy(hypo); corrupt['cells'][0]['modeled_initial_fuel_factor']['low_assumption'] = 1.
            raises(ValueError, lambda: prepare_mixed_inputs(strict,water,audit,corrupt,strict_pin=strict['sha256'],
                water_v2_pin=water['sha256'],audit_pin=audit['sha256'],hypothesis_pin=hypo['sha256'],
                trusted_audit_manifest=trusted_manifest,road_path=road,road_pin=road_hash,
                scenario='low',opt_in_mixed_experiment=True))
            bad = copy.deepcopy(audit); bad['reviewed_cells'][0]['coast_distance_m'] = 100.
            bad['sha256'] = api._hash({k:v for k,v in bad.items() if k!='sha256'})
            raises(ValueError, lambda: compiler.compile_interior_mixed(strict,water,bad,strict_pin=strict['sha256'],
                v2_pin=water['sha256'],audit_pin=bad['sha256']))
        elif case == 'coastal_audit_tamper':
            # The independently trusted synthetic audit says the location is
            # coastal. A forged audit and matching hypothesis reclassify it.
            trusted = copy.deepcopy(audit)
            trusted['reviewed_cells'][0]['coast_distance_m'] = 100.
            trusted['sha256'] = api._hash({k:v for k,v in trusted.items() if k!='sha256'})
            manifest = dict(trusted_manifest, audit_sha256=trusted['sha256'])
            source_gate_v1.MIXED_AUDIT_MANIFEST_SHA256 = api._hash(manifest)
            forged = copy.deepcopy(trusted)
            forged['reviewed_cells'][0]['coast_distance_m'] = 1000.
            forged['sha256'] = api._hash({k:v for k,v in forged.items() if k!='sha256'})
            forged_hypo = compiler.compile_interior_mixed(strict,water,forged,
                strict_pin=strict['sha256'],v2_pin=water['sha256'],audit_pin=forged['sha256'])
            assert len(forged_hypo['cells']) == 1  # reconstruction alone accepted the false claim
            with pytest.raises(ValueError, match='independent audit gate'):
                prepare_mixed_inputs(strict,water,forged,forged_hypo,strict_pin=strict['sha256'],
                    water_v2_pin=water['sha256'],audit_pin=forged['sha256'],hypothesis_pin=forged_hypo['sha256'],
                    trusted_audit_manifest=manifest,road_path=road,road_pin=road_hash,
                    scenario='nominal',opt_in_mixed_experiment=True)
            with pytest.raises(ValueError, match='independent audit gate'):
                prepare_mixed_inputs(strict,water,forged,forged_hypo,strict_pin=strict['sha256'],
                    water_v2_pin=water['sha256'],audit_pin=forged['sha256'],hypothesis_pin=forged_hypo['sha256'],
                    trusted_audit_manifest=dict(manifest,audit_sha256=forged['sha256']),
                    road_path=road,road_pin=road_hash,scenario='nominal',opt_in_mixed_experiment=True)
        elif case == 'mixed_spread':
            value = inputs()
            def fresh():
                return InteriorMixedFireV2(value,seed=7,containment_config=config,pressure_scale=1,
                                           wind=[3,0],opt_in_mixed_experiment=True)
            a = fresh()
            assert a.change({'ignite':[[3,3]]})
            b = fresh()
            b.restore(json.loads(json.dumps(a.snapshot())))
            for _ in range(400):
                assert a.step() == b.step()
                assert api._encoded(a.snapshot()) == api._encoded(b.snapshot())
                if a._fire.cells[3][4]['heat'] > 0:
                    break
                assert not a.censored
            else:
                raise AssertionError('expected a real spread into the validated mixed cell')
        elif case == 'water_effect':
            # High fuel still applies a positive water-crossing reduction. A
            # controlled draw between the reduced and unreduced probabilities
            # detects both an ignored water cell and an artificial firebreak.
            from simulator.terrain import PROFILES
            value = inputs('high')
            def fresh(draw, ignore_water=False):
                f = InteriorMixedFireV2(value,seed=7,containment_config=config,pressure_scale=1,
                                        wind=[3,0],opt_in_mixed_experiment=True)
                assert f.change({'ignite':[[3,3]]})
                f._fire.cells[3][3].update(heat=1., age=119)
                if ignore_water:
                    f._fire._mixed_openness[(4,3)] = 1.0  # negative control only
                class Fixed:
                    def random(self): return draw
                f._fire.rng = Fixed()
                assert f.step()
                return f._fire.cells[3][4]['heat']
            chance = .5 * PROFILES['scrub']['spread']
            assert fresh(chance * .998) == 0  # between reduced and unmodified chances
            assert fresh(chance * .998, ignore_water=True) > 0
            assert fresh(0.) > 0  # mixed cell is not an impermeable barrier
        elif case == 'land_parity':
            value = inputs()
            for seed in (0, 7):
                v1 = original.ExpandedContainmentFireV1(value.contract,seed=seed,containment_config=config,
                                                        pressure_scale=1,wind=[3,0])
                v2 = InteriorMixedFireV2(value,seed=seed,containment_config=config,pressure_scale=1,
                                         wind=[3,0],opt_in_mixed_experiment=True)
                assert v1.change({'ignite':[[3,3]]}) and v2.change({'ignite':[[3,3]]})
                for _ in range(130):
                    assert v1.step() == v2.step()
                    assert v1._fire.cells == v2._fire.cells
                    assert v1._fire.rng.getstate() == v2._fire.rng.getstate()
                    assert v1._fire._containment.checkpoint() == v2._fire._containment.checkpoint()
                    if v1.censored:
                        break
        elif case == 'unknown_guard':
            f = make(inputs())
            assert not f.change({'ignite':[[3,3]]})
            assert f.reasons == ('unknown_fire_state',)
            raises(api.CensoredError, f.outcomes)


if __name__ == '__main__':
    run(sys.argv[1])
