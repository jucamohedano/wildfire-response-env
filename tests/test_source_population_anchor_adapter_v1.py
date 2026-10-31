"""Consume the independently pinned population candidate without route claims."""
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
PATHS=[Path('/tmp/expanded-post-anchors-v4/network.json'),Path('/tmp/expanded-post-anchors-v4/scenario-drafts.json'),
       Path('/tmp/wildfire-eval-v1-final2-20261030/catalog.json'),
       Path('/tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json'),
       Path('/tmp/wildfire-expanded-source-diagnostic-v1-20261030/drafts.json'),
       Path('/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz'),
       Path('/tmp/expanded-population-anchors-v1/candidate.json'),
       Path('/tmp/wildfire-expanded-source-diagnostic-v1-20261030/inputs-source-only.json'),
       Path('/tmp/wildfire-map-resolution-private/resolution-v2.json'),
       Path('/tmp/wildfire-map-resolution-private/small-water-exercise-v4.json')]


def test_boundary_node_makes_anchor_ambiguous():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PROJ_NETWORK='OFF',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    code = '''
from wildfire_source_population_anchor_v1.adapter import PopulationAnchorAdapterV1, _index, _near

origin = [0.0, 0.0]
graph = [{'id': '1', 'position': origin}]
source = [{'id': 1, 'position_m': origin}]
record = {'status': 'source_supported_exercise_anchor', 'original_position': origin,
          'cell_access': 'unknown', 'original_node_id': 'osm:n1', 'graph_node_id': '1'}
for which in ('graph', 'source'):
    gs = graph + ([{'id': '2', 'position': [0.15, 0.0]}] if which == 'graph' else [])
    ss = source + ([{'id': 2, 'position_m': [0.15, 0.0]}] if which == 'source' else [])
    gi = _index(gs, lambda n: n['position'])
    si = _index(ss, lambda n: n['position_m'])
    assert len(_near(gi, origin, lambda n: n['position'])) == (2 if which == 'graph' else 1)
    assert len(_near(si, origin, lambda n: n['position_m'])) == (2 if which == 'source' else 1)
    adapter = object.__new__(PopulationAnchorAdapterV1)
    try:
        adapter._validate_anchor(record, origin, {tuple(origin): ['osm:n1']}, None, None,
                                 gi, si, None, None, None, None, None, None, None, None, None)
    except ValueError as exc:
        assert 'ambiguous' in str(exc)
    else:
        raise AssertionError('boundary node was accepted as unique')

outside = [{'position': [0.150001, 0.0]}]
assert not _near(_index(outside, lambda n: n['position']), origin, lambda n: n['position'])
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.skipif(not all(p.exists() for p in PATHS),reason='private reviewed source and anchor artifacts unavailable')
def test_real_candidate_adapter():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PROJ_NETWORK='OFF',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    result=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,result.stdout+result.stderr


def main():
    import copy,json
    import adapter_v1 as api
    from envs.wildfire_response.regional.ops.generator import digest
    from eval.expanded_road_runtime_v1 import load_candidate
    from eval.expanded_guarded_routes_v1 import REVIEWED_PAIR_MANIFEST
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_source_road_v1.catalog import SourceCaseCatalogV1
    from wildfire_source_population_anchor_v1 import PopulationAnchorAdapterV1,CANDIDATE_PIN
    graph,anchored,original,parent,fire=(json.loads(p.read_bytes()) for p in PATHS[:5])
    candidate,source_inputs,water,v4=(json.loads(p.read_bytes()) for p in PATHS[6:])
    source=load_candidate();g=fire['grid'];w,h=g['width'],g['height']
    doc=dict(version='wildfire-extended-inputs-v1',grid=copy.deepcopy(g),domain_mask=[[True]*w for _ in range(h)],
        surface=[['land']*w for _ in range(h)],fuel=[[4]*w for _ in range(h)],elevation=[[100.]*w for _ in range(h)],
        access=[['unknown']*w for _ in range(h)],source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
    # Only the fire input is a synthetic test stand-in. Source graph, source
    # snapshot, candidate, water evidence and source catalog are not patched.
    ident=dict(version='wildfire-small-water-mixed-exercise-input-v4',input_sha256=contract.document_sha256,
        catalog=original['sha256'],parent=parent['sha256'],road=REVIEWED_PAIR_MANIFEST['source_compressed_sha256'])
    mixed=MixedInputsV2(contract,(),tuple(sorted(ident.items())),_VERIFIED)
    selector=SourceCaseCatalogV1(mixed,original,parent,anchored,fire,graph,source,
        expected_original_sha256=original['sha256'],expected_parent_sha256=parent['sha256'],
        expected_anchored_sha256=anchored['sha256'],expected_fire_drafts_sha256=fire['sha256'],
        expected_graph_sha256=graph['sha256'],expected_source_sha256=digest(source),opt_in=True)
    oldnetwork=json.loads((ROOT/'data/coastal_mobility_v1/network.json').read_bytes())
    oldraw=(ROOT/'data/coastal_mobility_v1/source_snapshot.json.gz').read_bytes()
    def build(c=candidate,*,pin=CANDIDATE_PIN,opt=True,water_arg=water):
        return PopulationAnchorAdapterV1(selector,c,graph,source,oldnetwork,oldraw,source_inputs,water_arg,v4,
                                          expected_candidate_sha256=pin,opt_in=opt)
    assert CANDIDATE_PIN=='5ea3fcb157146a61072dba4818c7b6d224a5242d50ed5efbd5a28fc822ce39d4'
    adapter=build();evidence=adapter.private_evidence()
    assert adapter.private_topology_summary()=={'directed_path_in_exercise_graph':47,'same_source_node':1}
    assert set(evidence)=={'321315386','6119295366','595570962','4341595138'}
    for v in evidence.values():
        assert v['displacement_m']<=.065577 and v['source_components']==1
        assert all(v['exercise_modes'][m][d]>0 for m in ('crew','engine') for d in ('in','out'))
        assert v['strict_surface']=='land' and v['water_v2_surface']=='unknown'
        assert v['v4_exercise_surface']=='mixed_assumption' and v['access']=='unknown'
        assert v['routeability']=='unqualified_unknown_surface_or_access'
    for i,case_id in enumerate(selector.case_ids()):
        original_case=selector.private_case(case_id)['original'];result=adapter.private_case(case_id)
        assert result['role']=='synthetic_same_osm_node_coordinate_correction'
        assert result['status']=='private-unqualified-not-runnable' and result['route_access']=='unknown_no_connector_or_whitelist'
        assert result['original_case_sha256']==original_case['sha256']
        candidate_case=candidate['cases'][i]
        for old,new,entry in zip(original_case['groups'],result['adapted_groups'],candidate_case['groups']):
            assert new['position']==entry['position']['source_position'] and new['home']==entry['home']['source_position']
            assert {k:v for k,v in new.items() if k not in ('position','home')}=={k:v for k,v in old.items() if k not in ('position','home')}
        for old,new,entry in zip(original_case['destinations'],result['adapted_destinations'],candidate_case['destinations']):
            assert new['position']==entry['position']['source_position']
            assert {k:v for k,v in new.items() if k!='position'}=={k:v for k,v in old.items() if k!='position'}
        assert selector.private_case(case_id)['original']==original_case
    for index in (1,8):
        case_id=selector.case_ids()[index];bundle=adapter.private_host_input(case_id)
        assert bundle['role']=='private_unqualified_synthetic_host_input'
        assert bundle['source_case']['original']['groups']==original['cases'][index]['groups']
        assert bundle['source_case']['original']['destinations']==original['cases'][index]['destinations']
        assert bundle['population_adaptation']==adapter.private_case(case_id)
        assert bundle['source_case']['expanded_fire']['parent_scenario_sha256']==original['cases'][index]['sha256']
    special=adapter.private_case(selector.case_ids()[6])
    g1=next(x for x in special['adapted_groups'] if x['id']=='population-1')
    d0=next(x for x in special['adapted_destinations'] if x['id']=='destination-0')
    assert g1['position']==d0['position'] and g1['home']!=g1['position'] and g1['status']=='arrived'
    # Independent pin cannot be replaced by rehashing a changed candidate.
    altered=copy.deepcopy(candidate);altered['cases'][0]['groups'][0]['position']['source_position'][0]+=1
    altered['sha256']=api._hash({k:v for k,v in altered.items() if k!='sha256'})
    def rejects(fn):
        try:fn()
        except ValueError:return
        raise AssertionError('untrusted input accepted')
    rejects(lambda:build(altered,pin=altered['sha256']))
    rejects(lambda:build(pin='0'*64))
    rejects(lambda:build(opt=False))
    changed=copy.deepcopy(water);changed['cells'][53][31]['surface']='land'
    changed['sha256']=api._hash({k:v for k,v in changed.items() if k!='sha256'})
    rejects(lambda:build(water_arg=changed))
    try:adapter.private_case('unknown')
    except ValueError:pass
    else:raise AssertionError('unknown case accepted')


if __name__=='__main__':main()
