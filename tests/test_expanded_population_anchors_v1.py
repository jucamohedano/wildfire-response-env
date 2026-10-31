"""Same-source-node population and destination anchor candidates."""
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest
from eval.expanded_population_anchors_v1 import (AnchorResolver, CATALOG, GRAPH, OLD, OUT,
    SNAPSHOT, SOURCE_PIN, GRAPH_PIN, build_candidate, load_pinned_inputs)
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from eval.expanded_road_runtime_v1 import CANDIDATE
from envs.wildfire_response.regional.ops.generator import digest


class Guard:
    def __init__(self,ambiguous=False): self.ambiguous=ambiguous
    def assess_origin(self,position):
        return {'status':'ambiguous' if self.ambiguous else 'unambiguous',
                'local_source_components':2 if self.ambiguous else 1}


def fixture():
    exact=[290100.02,4064000.03]; other=[290300.,4064000.]
    old={'nodes':[{'id':'osm:n1','position':[290100.,4064000.]}]}
    snapshot={'elements':[{'type':'node','id':1,'lon':-5.,'lat':36.}]}
    source={'nodes':[{'id':1,'position_m':exact,'lon':-5.,'lat':36.}]}
    graph={'nodes':[{'id':'1','position':exact},{'id':'2','position':other}],
        'edges':[{'source':'1','target':'2','allowed_modes':['engine'],
                  'exercise_access_basis':'assumed_public_road'},
                 {'source':'2','target':'1','allowed_modes':['engine'],
                  'exercise_access_basis':'assumed_public_road'}]}
    return old,snapshot,source,graph


def test_exact_same_id_rounding_and_permitted_in_out():
    old,snapshot,source,graph=fixture();resolver=AnchorResolver(old,snapshot,source,graph,Guard())
    out=resolver.resolve([290100.,4064000.])
    assert out['status']=='source_supported_exercise_anchor'
    assert out['original_node_id']=='osm:n1' and out['graph_node_id']=='1'
    assert out['displacement_m']==pytest.approx(math.sqrt(.02**2+.03**2))
    assert out['engine_incoming_edges']==out['engine_outgoing_edges']==1
    assert out['cell_access']=='unknown' and out['legal_and_vehicle_suitability']=='unverified'
    out['source_position'][0]=0
    assert resolver.resolve([290100.,4064000.])['source_position'][0]!=0


@pytest.mark.parametrize(('change','reason'),[
    ('old_duplicate','original_source_node_not_unique'),
    ('missing_source','same_source_node_missing'),
    ('changed_lat','same_source_node_coordinates_changed'),
    ('rounding','original_rounding_not_established'),
    ('second_nearby','reviewed_graph_node_not_unique_within_tolerance'),
    ('no_nearby','reviewed_graph_node_not_unique_within_tolerance'),
    ('different_graph_node','nearest_graph_node_is_not_original_source_node'),
    ('ambiguous','source_topology_ambiguous'),
    ('no_engine_in','engine_mode_arrival_or_departure_not_supported'),
    ('no_engine_out','engine_mode_arrival_or_departure_not_supported'),
])
def test_unresolved_never_snaps_or_promotes_unknown(change,reason):
    old,snapshot,source,graph=fixture();guard=Guard()
    if change=='old_duplicate':old['nodes'].append(dict(id='osm:n99',position=[290100.,4064000.]))
    if change=='missing_source':source['nodes']=[]
    if change=='changed_lat':source['nodes'][0]['lat']=37.
    if change=='rounding':source['nodes'][0]['position_m']=[290100.08,4064000.03]
    if change=='second_nearby':graph['nodes'].append({'id':'3','position':[290100.04,4064000.04]})
    if change=='no_nearby':graph['nodes'][0]['position']=[290200.,4064000.]
    if change=='different_graph_node':graph['nodes'][0]['id']='3'
    if change=='ambiguous':guard=Guard(True)
    if change=='no_engine_in':graph['edges'][1]['allowed_modes']=['crew']
    if change=='no_engine_out':graph['edges'][0]['allowed_modes']=['crew']
    result=AnchorResolver(old,snapshot,source,graph,guard).resolve([290100.,4064000.])
    assert result['status']=='unresolved' and result['reason']==reason
    assert 'source_position' not in result and 'graph_node_id' not in result
    assert result['cell_access']=='unknown'


def test_duplicate_identities_and_invalid_position_rejected():
    old,snapshot,source,graph=fixture();snapshot['elements'].append(copy.deepcopy(snapshot['elements'][0]))
    with pytest.raises(ValueError,match='duplicate'):
        AnchorResolver(old,snapshot,source,graph,Guard())
    old,snapshot,source,graph=fixture();resolver=AnchorResolver(old,snapshot,source,graph,Guard())
    with pytest.raises(ValueError,match='invalid original position'):
        resolver.resolve([float('nan'),0])


def test_candidate_keeps_parent_case_hashes_and_separate_home(monkeypatch):
    import eval.expanded_population_anchors_v1 as module
    old,snapshot,source,graph=fixture();resolver=AnchorResolver(old,snapshot,source,graph,Guard())
    case={'id':'private-case','sha256':'a'*64,
          'groups':[{'id':'g','status':'arrived','destination_id':'d',
                     'position':[290100.,4064000.],'home':[290200.,4064000.]}],
          'destinations':[{'id':'d','position':[290100.,4064000.]}]}
    catalog={'cases':[case]}; before=copy.deepcopy(catalog)
    monkeypatch.setattr(module,'check_catalog',lambda _:None)
    candidate=build_candidate(catalog,resolver,object())
    record=candidate['cases'][0]
    assert catalog==before and record['original_case_sha256']=='a'*64
    assert record['groups'][0]['position']['status']=='source_supported_exercise_anchor'
    assert record['groups'][0]['home']['status']=='unresolved'
    assert record['topology'][0]['engine_graph_topology']=='same_source_node'
    assert candidate['status']=='private-unqualified-not-runnable'
    assert candidate['sha256']==digest({k:v for k,v in candidate.items() if k!='sha256'})


def test_output_directory_is_write_once(tmp_path,monkeypatch):
    import eval.expanded_population_anchors_v1 as module
    monkeypatch.setattr(module,'PRIVATE_PARENT',tmp_path)
    directory=tmp_path/'expanded-population-anchors-v1-existing';directory.mkdir(mode=0o700)
    out=directory/'candidate.json';out.write_bytes(b'keep')
    with pytest.raises(FileExistsError,match='refusing to overwrite'):
        module.reserve_private_directory(directory)
    with pytest.raises(ValueError,match='fresh'):
        module.private_candidate_destination(directory)
    assert out.read_bytes()==b'keep'


@pytest.mark.skipif(not all(p.exists() for p in (CATALOG,GRAPH,OLD,SNAPSHOT,CANDIDATE,OUT/'candidate.json')),
                    reason='private pinned artifacts unavailable')
def test_full_pinned_candidate_matches_all_original_cases_and_source():
    candidate=json.loads((OUT/'candidate.json').read_bytes())
    assert candidate['sha256']==digest({k:v for k,v in candidate.items() if k!='sha256'})
    catalog,old,snapshot,source,graph,_=load_pinned_inputs()
    cells=[[0]*84 for _ in range(59)];heights=[[None]*84 for _ in range(59)]
    grid={'width':84,'height':59,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    guard=GuardedOriginRouter(graph,GRAPH_PIN,source,SOURCE_PIN,{'grid':grid},heights,walk_mask=cells,
                              allowed_edge_ids=[e['id'] for e in graph['edges']])
    resolver=AnchorResolver(old,snapshot,source,graph,guard)
    rebuilt=build_candidate(catalog,resolver,guard._router.network)
    assert rebuilt==candidate
    assert len(candidate['cases'])==12
    assert sum(len(c['groups']) for c in candidate['cases'])==24
    assert sum(len(c['destinations']) for c in candidate['cases'])==24
    all_anchors=[];arrived=0
    for original,case in zip(catalog['cases'],candidate['cases']):
        assert case['original_case_id']==original['id']
        assert case['original_case_sha256']==original['sha256']==digest({k:v for k,v in original.items() if k!='sha256'})
        for group in case['groups']:
            all_anchors.extend([group['position'],group['home']])
            if group['original_status']=='arrived':
                arrived+=1
                assert group['position']['graph_node_id']!=group['home']['graph_node_id']
        all_anchors.extend(d['position'] for d in case['destinations'])
    assert arrived==1
    assert all(a['status']=='source_supported_exercise_anchor' for a in all_anchors)
    assert max(a['displacement_m'] for a in all_anchors)<math.sqrt(2)*.05+1e-8
    assert all(a['cell_access']=='unknown' and a['legal_and_vehicle_suitability']=='unverified' for a in all_anchors)


def test_cli_rejects_current_and_other_git_worktrees_before_audit(tmp_path):
    root=Path(__file__).resolve().parents[1]
    primary=tmp_path/'primary';primary.mkdir()
    subprocess.run(['git','init','-q',str(primary)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(primary),'-c','user.name=Test','-c','user.email=test@example.invalid',
                    'commit','--allow-empty','-qm','fixture'],check=True,capture_output=True)
    other=tmp_path/'linked-worktree'
    subprocess.run(['git','-C',str(primary),'worktree','add','--detach',str(other)],check=True,capture_output=True)
    candidates=[root/'results'/'expanded-population-anchors-v1-test-private',
                other/'expanded-population-anchors-v1-test-private']
    for directory in candidates:
        proc=subprocess.run([sys.executable,'eval/expanded_population_anchors_v1.py','--output-dir',str(directory)],
            cwd=root,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True)
        assert proc.returncode!=0 and 'dedicated private directory' in proc.stderr
        assert not directory.exists()


def test_existing_private_guard_rejects_git_ancestor(tmp_path,monkeypatch):
    import eval.expanded_population_anchors_v1 as module
    repo=tmp_path/'worktree';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True,capture_output=True)
    monkeypatch.setattr(module,'PRIVATE_PARENT',repo)
    directory=repo/'expanded-population-anchors-v1-probe'
    with pytest.raises(ValueError,match='git worktree'):
        module.reserve_private_directory(directory)
    assert not directory.exists()


@pytest.mark.parametrize('served_name',['assets','public','static','dist'])
def test_served_assets_directories_rejected_even_if_owner_private(tmp_path,served_name):
    root=Path(__file__).resolve().parents[1]
    served=tmp_path/'site'/served_name;served.mkdir(parents=True,mode=0o700)
    directory=served/'expanded-population-anchors-v1-probe'
    proc=subprocess.run([sys.executable,'eval/expanded_population_anchors_v1.py','--output-dir',str(directory)],
        cwd=root,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True)
    assert proc.returncode!=0 and 'dedicated private directory' in proc.stderr
    assert not directory.exists()


def test_output_directory_symlink_and_ancestor_symlink_rejected(tmp_path,monkeypatch):
    import eval.expanded_population_anchors_v1 as module
    monkeypatch.setattr(module,'PRIVATE_PARENT',tmp_path)
    served=tmp_path/'assets';served.mkdir(mode=0o700)
    link=tmp_path/'expanded-population-anchors-v1-link';link.symlink_to(served,target_is_directory=True)
    with pytest.raises(FileExistsError): module.reserve_private_directory(link)
    assert list(served.iterdir())==[]
    ancestor=tmp_path/'alias';ancestor.symlink_to(served,target_is_directory=True)
    monkeypatch.setattr(module,'PRIVATE_PARENT',ancestor)
    nested=ancestor/'expanded-population-anchors-v1-child'
    with pytest.raises(ValueError,match='symlinks'):
        module.reserve_private_directory(nested)
    assert not (served/'expanded-population-anchors-v1-child').exists()


@pytest.mark.parametrize('link_type',['symlink','hardlink'])
def test_existing_target_link_is_never_followed_or_modified(tmp_path,monkeypatch,link_type):
    import eval.expanded_population_anchors_v1 as module
    monkeypatch.setattr(module,'PRIVATE_PARENT',tmp_path)
    directory=module.reserve_private_directory(tmp_path/'expanded-population-anchors-v1-safe')
    external=tmp_path/'served-original';external.write_bytes(b'original-public-data')
    target=directory/'candidate.json'
    if link_type=='symlink': target.symlink_to(external)
    else: os.link(external,target)
    with pytest.raises(ValueError,match='fresh'):
        module.private_candidate_destination(directory)
    assert external.read_bytes()==b'original-public-data'


def test_private_fresh_output_passes_existing_guard(tmp_path,monkeypatch):
    import eval.expanded_population_anchors_v1 as module
    monkeypatch.setattr(module,'PRIVATE_PARENT',tmp_path)
    directory=module.reserve_private_directory(tmp_path/'expanded-population-anchors-v1-safe')
    assert directory.stat().st_mode & 0o777 == 0o700
    target=module.private_candidate_destination(directory)
    module.write_private_bytes(target,b'{"synthetic":true}')
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError,match='fresh'):
        module.private_candidate_destination(directory)


def test_output_directory_hardlink_is_never_modified(tmp_path,monkeypatch):
    import eval.expanded_population_anchors_v1 as module
    monkeypatch.setattr(module,'PRIVATE_PARENT',tmp_path)
    existing=tmp_path/'served-original';existing.write_bytes(b'keep-public')
    destination=tmp_path/'expanded-population-anchors-v1-hardlink'
    os.link(existing,destination)
    with pytest.raises(FileExistsError): module.reserve_private_directory(destination)
    assert existing.read_bytes()==b'keep-public'


def test_recheck_rejects_directory_swapped_for_served_symlink(tmp_path,monkeypatch):
    import eval.expanded_population_anchors_v1 as module
    monkeypatch.setattr(module,'PRIVATE_PARENT',tmp_path)
    directory=module.reserve_private_directory(tmp_path/'expanded-population-anchors-v1-swap')
    directory.rename(tmp_path/'reserved-original')
    served=tmp_path/'assets';served.mkdir(mode=0o700)
    directory.symlink_to(served,target_is_directory=True)
    with pytest.raises(ValueError,match='symlinks'):
        module.private_candidate_destination(directory)
    assert list(served.iterdir())==[]


def test_recheck_rejects_git_marker_created_after_reservation(tmp_path,monkeypatch):
    import eval.expanded_population_anchors_v1 as module
    monkeypatch.setattr(module,'PRIVATE_PARENT',tmp_path)
    directory=module.reserve_private_directory(tmp_path/'expanded-population-anchors-v1-git')
    (directory/'.git').write_text('gitdir: /tmp/elsewhere')
    with pytest.raises(ValueError,match='git worktree'):
        module.private_candidate_destination(directory)
    assert not (directory/'candidate.json').exists()
