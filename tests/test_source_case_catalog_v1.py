"""Pin actual twelve-case lineage without running source scenarios."""
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
       Path('/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz')]


@pytest.mark.skipif(not all(p.exists() for p in PATHS),reason='private source lineage artifacts unavailable')
def test_source_case_lineage_without_running_cases():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
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
    graph,anchored,original,parent,fire=(json.loads(p.read_bytes()) for p in PATHS[:5])
    source=load_candidate();g=fire['grid'];w,h=g['width'],g['height']
    doc=dict(version='wildfire-extended-inputs-v1',grid=copy.deepcopy(g),
        domain_mask=[[True]*w for _ in range(h)],surface=[['land']*w for _ in range(h)],
        fuel=[[4]*w for _ in range(h)],elevation=[[100.]*w for _ in range(h)],
        access=[['unknown']*w for _ in range(h)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
    # Synthetic stand-in only for v4 fire inputs. Catalogs, snapshots, graph,
    # pair verifier and reconstruction run against the real pinned artifacts.
    ident=dict(version='wildfire-small-water-mixed-exercise-input-v4',input_sha256=contract.document_sha256,
        catalog=original['sha256'],parent=parent['sha256'],road=REVIEWED_PAIR_MANIFEST['source_compressed_sha256'])
    mixed=MixedInputsV2(contract,(),tuple(sorted(ident.items())),_VERIFIED)
    def build(*,anc=anchored,fi=fire):
        return SourceCaseCatalogV1(mixed,original,parent,anc,fi,graph,source,
            expected_original_sha256=original['sha256'],expected_parent_sha256=parent['sha256'],
            expected_anchored_sha256=anc['sha256'],expected_fire_drafts_sha256=fi['sha256'],
            expected_graph_sha256=graph['sha256'],expected_source_sha256=digest(source),opt_in=True)
    catalog=build();ids=catalog.case_ids()
    assert len(ids)==12
    for i in (1,8):
        item=catalog.private_case(ids[i])
        assert item['original']['sha256']==original['cases'][i]['sha256']
        assert item['original']['groups']==original['cases'][i]['groups']
        assert item['original']['destinations']==original['cases'][i]['destinations']
        assert item['expanded_fire']['parent_scenario_sha256']==item['original']['sha256']
        assert item['expanded_fire']['seed']==item['original']['seed']+100000
        assert 'post_anchored' not in item
        assert all(r['kind'] in ('crew','engine') for r in item['anchored_ground_resources'])
        assert any(r['kind']=='aircraft' for r in item['original']['resources'])
    # Even a rehashed anchored case cannot silently move a cohort.
    altered=copy.deepcopy(anchored);altered['cases'][1]['groups'][0]['position'][0]+=100
    c=altered['cases'][1];c['sha256']=api._hash({k:v for k,v in c.items() if k!='sha256'})
    altered['sha256']=api._hash({k:v for k,v in altered.items() if k!='sha256'})
    try:build(anc=altered)
    except ValueError:pass
    else:raise AssertionError('rehash accepted altered cohort')
    changed=copy.deepcopy(fire);changed['cases'][8]['initial_fire'][0][0]+=1
    c=changed['cases'][8];c['sha256']=api._hash({k:v for k,v in c.items() if k!='sha256'})
    changed['sha256']=api._hash({k:v for k,v in changed.items() if k!='sha256'})
    try:build(fi=changed)
    except ValueError:pass
    else:raise AssertionError('rehash accepted altered ignition mapping')


if __name__=='__main__':main()
