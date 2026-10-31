"""Real reviewed road pair with synthetic surface fixtures; no trust override."""
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=Path('/tmp/expanded-post-anchors-v4')
SOURCE=Path('/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz')


@pytest.mark.skipif(not SOURCE.exists() or not (PRIVATE/'network.json').exists() or
                    not (PRIVATE/'scenario-drafts.json').exists(),reason='private reviewed pair unavailable')
def test_real_source_road_only():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    out=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    assert out.returncode==0,out.stdout+out.stderr


def main():
    import copy,json
    import adapter_v1 as api
    from envs.wildfire_response.regional.ops.generator import digest
    from eval.expanded_road_runtime_v1 import load_candidate
    from eval.expanded_guarded_routes_v1 import check_anchored_draft, REVIEWED_PAIR_MANIFEST
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_source_road_v1 import SourceRoadOnlyV1
    source=load_candidate();graph=json.loads((PRIVATE/'network.json').read_bytes())
    drafts=json.loads((PRIVATE/'scenario-drafts.json').read_bytes())
    anchors=check_anchored_draft(drafts,drafts['sha256'],graph['sha256'],graph)
    a,b=anchors['center-0'][1],anchors['center-1'][1]
    grid=dict(width=84,height=59,crs='EPSG:25830',transform=[500,0,290000,0,-500,4064500])
    doc=dict(version='wildfire-extended-inputs-v1',grid=grid,domain_mask=[[True]*84 for _ in range(59)],
        surface=[['land']*84 for _ in range(59)],fuel=[[4]*84 for _ in range(59)],
        elevation=[[100.]*84 for _ in range(59)],access=[['unknown']*84 for _ in range(59)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    def inputs(document):
        contract=api.load_inputs(document,api._hash(document),expected_source_pins=document['source_pins'],opt_in=True)
        # Synthetic fire support only; graph/source trust is unmodified production.
        ident=dict(version='wildfire-small-water-mixed-exercise-input-v4',catalog='e'*64,
                   input_sha256=contract.document_sha256,road=REVIEWED_PAIR_MANIFEST['source_compressed_sha256'])
        return MixedInputsV2(contract,(),tuple(sorted(ident.items())),_VERIFIED)
    allowed=[e['id'] for e in graph['edges'] if 'engine' in e['allowed_modes'] and e.get('exercise_access_basis')
             in ('assumed_public_road','explicit_access_tag_public_road','explicit_access_tag_service')]
    def build(document=doc, whitelist=allowed):
        return SourceRoadOnlyV1(inputs(document),graph,source,expected_graph_sha256=graph['sha256'],
            expected_source_sha256=digest(source),allowed_edge_ids=whitelist,
            expected_whitelist_sha256=api._hash(sorted(whitelist)),opt_in=True)
    router=build()
    assert all(v=='unknown' for row in router._inputs.contract.access for v in row)
    result=router.options(a,b,mode='engine',burning=[],wind=[0.,0.])
    assert result['status']=='reachable' and result['options'],result
    journey=result['options'][0]['journey']
    assert all(leg['mode']=='road' and leg['coordinates']==router._network.edges[leg['edge_id']]['coordinates']
               for leg in journey['legs'])
    assert 'synthetic' in router._identity['access'] and not hasattr(router,'safe_position')
    mid=[(journey['legs'][0]['coordinates'][0][i]+journey['legs'][0]['coordinates'][1][i])/2 for i in (0,1)]
    assert router.options(mid,b,mode='engine',burning=[],wind=[0,0])['status']=='unmapped'
    c=router._guard._router._cell(a)
    reverse=router.options(b,a,mode='crew',burning=[],wind=[0,0])
    assert reverse['status']=='reachable' and reverse['options']
    journey['departed_at']=0
    assert router.movement_safe(journey,1,current_position=a,mode='engine',burning=[],wind=[0,0])
    assert not router.movement_safe(journey,1,current_position=a,mode='engine',burning=[list(c)],wind=[0,0])
    assert not router.movement_safe(journey,1,current_position=b,mode='engine',burning=[],wind=[0,0])
    # Apply the private, correctly grid-labeled restriction produced by a
    # source-like event before its delayed public report is delivered.
    from wildfire_source_road_v1.timeline import EventTimelineV1
    scenario=dict(id='source-like-road-restriction',seed=7,synthetic=True,historical_replay=False,
        centers=['center-0'],sector_grid=list(c),wind=[0.,0.],
        weather_schedule=[dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,
                               rain_mm_per_hour=0,received_at=0)],
        events=[dict(kind='access_restriction',minute=1,resource_kind='engine')],
        resources=[],groups=[],destinations=[],profile={})
    scenario['sha256']=api._hash(scenario)
    timeline=EventTimelineV1(scenario,expected_scenario_sha256=scenario['sha256'],
                             grid=grid,expected_grid_sha256=api._hash(grid),opt_in=True)
    timeline.advance()
    assert timeline.situation()['restrictions']==[]
    restrictions=timeline.private_excluded_cells('engine')
    assert restrictions['grid_identity']==api._hash(grid)
    assert not router.options(a,b,mode='engine',burning=[],wind=[0,0],excluded_cells=restrictions['cells'])['options']
    forged=copy.deepcopy(journey);forged['legs'][0]['coordinates'][0][0]+=1
    assert not router.movement_safe(forged,1,current_position=a,mode='engine',burning=[],wind=[0,0])
    forged=copy.deepcopy(journey);forged['legs'][0]['mode']='walk'
    assert not router.movement_safe(forged,1,current_position=a,mode='engine',burning=[],wind=[0,0])
    forged=copy.deepcopy(journey);forged['legs'][0]['duration_minutes']*=.1
    assert not router.movement_safe(forged,1,current_position=a,mode='engine',burning=[],wind=[0,0])
    unknown=copy.deepcopy(doc);unknown['surface'][c[1]][c[0]]='unknown'
    restricted=build(unknown)
    assert not restricted.options(a,b,mode='engine',burning=[],wind=[0,0])['options']
    # Even independently supplied road whitelists cannot admit an unpermitted edge.
    rejected=next(e['id'] for e in graph['edges'] if 'engine' not in e['allowed_modes'])
    try:build(whitelist=[rejected])
    except ValueError:pass
    else:raise AssertionError('unpermitted edge admitted')


if __name__=='__main__':main()
