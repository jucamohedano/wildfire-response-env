"""Synthetic tests for private mixed-surface fuel hypotheses."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'eval'))
import mixed_estimated_fuel_v1 as fuel
from extended_map_common import file_sha256

MAPPING={2:{'terrain':'field','initial_fuel':0.75},4:{'terrain':'scrub','initial_fuel':1.0},
         8:{'terrain':'woodland','initial_fuel':0.9},1:{'terrain':'field','initial_fuel':0.75}}


def votes(*classes):
    return [{'source_class':value} for value in classes]


def test_class_variation_is_weak_hypothesis_not_source_truth():
    result=fuel.decide(votes(*([4]*5+[2]*3)),MAPPING)
    assert result['status']=='ambiguous_sensitivity_hypothesis'
    assert result['confidence']=='low_uncalibrated' and result['vote_share']==0.625
    assert result['vote_counts']=={'2':3,'4':5}
    assert result['scenarios']['low']['assumed_fuel_class']==2
    assert result['scenarios']['nominal']['assumed_fuel_class']==4
    assert result['scenarios']['high']['assumed_fuel_class']==4
    assert [result['scenarios'][x]['modeled_initial_fuel'] for x in ('low','nominal','high')]==[0.6,0.9,1.0]


def test_unanimous_vote_is_still_uncalibrated_and_ties_remain_unknown():
    result=fuel.decide(votes(*([4]*5)),MAPPING)
    assert result['status']=='estimated_hypothesis' and result['confidence']=='low_uncalibrated'
    assert [result['scenarios'][x]['modeled_initial_fuel'] for x in ('low','nominal','high')]==[0.8,0.9,1.0]
    assert fuel.decide(votes(2,4),MAPPING)['status']=='unknown'
    assert fuel.decide(votes(2,2,4,4),MAPPING)['status']=='unknown'
    assert fuel.decide(votes(1,1,2,2,4,4,8),MAPPING)['status']=='unknown'


def test_neighbor_radius_is_bounded_and_never_includes_target():
    points=list(fuel.neighbor_positions(4,4,9,9))
    assert len(points)==12
    assert all(0 < d <= 1000 for _,_,d in points)
    assert (4,4) not in {(r,c) for r,c,_ in points}


def test_target_input_is_private_pinned_and_rejects_duplicates(tmp_path,monkeypatch):
    monkeypatch.setattr(fuel.source,'PRIVATE_ROOT',tmp_path/'private')
    path=tmp_path/'private'/'targets.json';path.parent.mkdir()
    path.write_text(json.dumps({'version':'private-spatial-targets-v1','cells':[[1,1],[3,3]]}))
    assert fuel.read_targets(path,file_sha256(path),5,5)==[(1,1),(3,3)]
    with pytest.raises(ValueError,match='hash mismatch'):
        fuel.read_targets(path,'0'*64,5,5)
    path.write_text(json.dumps({'version':'private-spatial-targets-v1','cells':[[1,1],[1,1]]}))
    with pytest.raises(ValueError,match='duplicate'):
        fuel.read_targets(path,file_sha256(path),5,5)
    with pytest.raises(ValueError,match='outside all git worktrees'):
        fuel.read_targets(tmp_path/'outside', '0'*64, 5, 5)


def test_support_uses_only_source_classes_and_v4_surface():
    v2={'grid':{'height':1,'width':4},'cells':[[
        {'surface':'land','fuel_status':'source','source_fuel_class':4},
        {'surface':'unknown','fuel_status':'source','source_fuel_class':2},
        {'surface':'land','fuel_status':'estimated','source_fuel_class':None},
        {'surface':'land','fuel_status':'source','source_fuel_class':8}]]}
    v4={'cells':[{'row':0,'column':1}],'additional_dera_censors':[{'row':0,'column':3}]}
    original=copy.deepcopy(v2)
    supported=fuel.source_support(v2,v4)
    assert supported=={(0,0):{'class':4,'surface_basis':'v2_mapped_land'},
                       (0,1):{'class':2,'surface_basis':'v4_mixed_exercise'}}
    assert v2==original
    v4['additional_dera_censors'].append({'row':0,'column':1})
    with pytest.raises(ValueError,match='invalid v4'):
        fuel.source_support(v2,v4)


def test_spatial_crossings_and_mapped_water_area_exclusion():
    python=Path('/home/juancm/miniconda3/envs/detectree/bin/python')
    if not python.is_file(): pytest.skip('GIS interpreter unavailable')
    script=r'''
import sys
from shapely.geometry import LineString,box
sys.path.insert(0,'eval')
import mixed_estimated_fuel_v1 as m
grid={'width':5,'height':5,'transform':[500,0,0,0,-500,2500]}
support={(2,1):{'class':2,'surface_basis':'v2_mapped_land'},
         (2,3):{'class':4,'surface_basis':'v4_mixed_exercise'}}
# The segment to one neighbor touches mapped water; the other crosses a mapped line.
area=box(950,1200,1000,1300)
line=LineString([(1550,1000),(1550,1500)])
features=[(1,'water_area','polygon',area),(2,'watercourse','line',line)]
indexes=m.make_spatial_indexes(features,[],[])
items,blocked=m.neighbors_for(2,2,grid,support,indexes)
assert blocked==1 and len(items)==1 and items[0]['source_class']==4
assert items[0]['crosses_osm_watercourse']
assert m.decide(items,{4:{'terrain':'scrub','initial_fuel':1.0}})['status']=='unknown'
'''
    result=subprocess.run([str(python),'-c',script],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_cli_fails_explicitly_for_missing_sources(tmp_path):
    cmd=[sys.executable,str(ROOT/'eval/mixed_estimated_fuel_v1.py'),'compile']
    keys=('parent','fuel-archive','boundary-archive','expanded','parent-sample','coast','water','worldcover',
          'osm-manifest','osm-samples','dera-rivers','catalog','strict','water-v2','exercise-v3','exercise-v4','targets')
    for key in keys:cmd += ['--'+key,str(tmp_path/key)]
    for key in ('strict-sha256','water-v2-sha256','exercise-v3-sha256','exercise-v4-sha256','targets-sha256'):
        cmd += ['--'+key,'0'*64]
    cmd+=['--output','/tmp/wildfire-map-resolution-private/missing-test-output.json']
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode!=0 and 'required validation sources absent' in result.stderr
    assert 'fuel-archive' in result.stderr and 'targets' in result.stderr


def test_mixed_gate_never_promotes_source_or_crosses_area_evidence(monkeypatch):
    hist={'10':2400,'20':100}
    original={'surface':'land','source_fuel_class':None,'estimated_fuel':None}
    current={'surface':'unknown','source_fuel_class':None,'fuel_status':'unknown',
             'worldcover_samples':hist,
             'osm_water_conflicts':[{'kind':'watercourse','way_id':1,'geometry':'line','epoch':'2026'}]}
    strict={'sha256':'strict','cells':[[original]]}
    v2={'sha256':'v2','parent_document_sha256':'strict','grid':{'height':1,'width':1},
        'cells':[[current]],'terrain_candidate':[[100.0]]}
    v4={'parent_v2_sha256':'v2'}
    neighbors=[{'source_class':4,'crosses_osm_watercourse':False,'crosses_dera_watercourse':False}]*5 + [
        {'source_class':2,'crosses_osm_watercourse':True,'crosses_dera_watercourse':True}]*3
    monkeypatch.setattr(fuel,'source_support',lambda *_:{})
    monkeypatch.setattr(fuel,'make_spatial_indexes',lambda *_:(None,None,None))
    monkeypatch.setattr(fuel,'neighbors_for',lambda *args,**kwargs:(neighbors,0))
    monkeypatch.setattr(fuel,'source_holdout',lambda *args,**kwargs:{})
    previous=copy.deepcopy((strict,v2,v4))
    def build():
        return fuel.build_hypotheses(strict,v2,v4,[[True]],{},[[None]],[],[],[],[(0,0)],MAPPING,{})
    doc=build();cell=doc['cells'][0]
    assert cell['source_fuel_class'] is None and cell['full_archive_center_sample'] is None
    assert cell['surface']=='mixed_exercise_assumption'
    assert cell['fuel_hypothesis']['status']=='ambiguous_sensitivity_hypothesis'
    assert cell['no_mapped_watercourse_crossing_votes']=={'4':5}
    assert cell['no_crossing_has_three_source_neighbors'] is True
    assert (strict,v2,v4)==previous
    current['osm_water_conflicts'].append({'kind':'water_area','way_id':2})
    with pytest.raises(ValueError,match='evidence gate'):
        build()


def test_leave_one_out_and_quadrant_block_keep_distinct_coverage(monkeypatch):
    grid={'height':6,'width':6}
    support={(1,1):{'class':4},(4,4):{'class':2}}
    observed=[]
    def neighbours(row,col,grid,support,indexes,*,withheld_quadrant=None):
        observed.append(withheld_quadrant)
        return [dict(source_class=4,crosses_osm_watercourse=False,crosses_dera_watercourse=False) for _ in range(3)],0
    monkeypatch.setattr(fuel,'neighbors_for',neighbours)
    leave=fuel.source_holdout(grid,support,None,MAPPING,quadrant_blocked=False)
    block=fuel.source_holdout(grid,support,None,MAPPING,quadrant_blocked=True)
    assert leave['counts']['eligible']==2 and leave['counts']['correct']==1
    assert leave['counts']['no_cross_vote_eligible']==2 and leave['counts']['no_cross_vote_correct']==1
    assert block['counts']['eligible']==2 and block['counts']['correct']==1
    assert observed==[None,None,(0,0),(1,1)]


def test_pinned_json_uses_checked_bytes_and_rejects_later_mutation(tmp_path,monkeypatch):
    path=tmp_path/'source.json'
    before=b'{"observation":"original"}'
    after=b'{"observation":"modified"}'
    path.write_bytes(before)
    expected=file_sha256(path)
    read=Path.read_bytes
    calls=[]
    def read_then_mutate(self):
        raw=read(self)
        if self==path:
            calls.append(1)
            self.write_bytes(after)
        return raw
    monkeypatch.setattr(Path,'read_bytes',read_then_mutate)
    assert fuel.read_pinned_json(path,expected,'synthetic DERA')=={'observation':'original'}
    assert len(calls)==1
    with pytest.raises(ValueError,match='synthetic DERA hash mismatch'):
        fuel.read_pinned_json(path,expected,'synthetic DERA')


def test_pinned_dera_index_uses_the_same_verified_geometry_bytes(tmp_path):
    import hashlib
    python=Path('/home/juancm/miniconda3/envs/detectree/bin/python')
    if not python.is_file(): pytest.skip('GIS interpreter unavailable')
    for layer in ('water','rivers'):
        path=tmp_path/(layer+'.json')
        coordinates=[[[0,0],[2,0],[2,2],[0,2],[0,0]]] if layer=='water' else [[0,0],[2,2]]
        kind='Polygon' if layer=='water' else 'LineString'
        data={'type':'FeatureCollection','numberMatched':1,'numberReturned':1,
              'crs':{'type':'name','properties':{'name':'urn:ogc:def:crs:EPSG::25830'}},
              'features':[{'type':'Feature','geometry':{'type':kind,'coordinates':coordinates},'properties':{'id_dera':123,'nombre':'synthetic'}}]}
        raw=json.dumps(data).encode();path.write_bytes(raw)
        expected=hashlib.sha256(raw).hexdigest()
        script=r'''
import sys
from pathlib import Path
from shapely.geometry import Point
sys.path.insert(0,'eval')
import mixed_estimated_fuel_v1 as m
p=Path(sys.argv[1]); pin=sys.argv[2]; layer=sys.argv[3]
read=Path.read_bytes; calls=[]
def read_then_mutate(self):
    raw=read(self)
    if self==p and not calls:
        calls.append(1)
        changed=raw.replace(b'[2, 2]',b'[3, 2]',1)
        assert changed!=raw  # valid geometry shifted within the same coarse cell
        self.write_bytes(changed)
    return raw
Path.read_bytes=read_then_mutate
features,geometries=m.read_pinned_collection(p,pin,1,'DERA '+layer,include_features=True)
assert len(calls)==1 and len(geometries)==1
water=geometries if layer=='water' else []
rivers=geometries if layer=='rivers' else []
area,_,river=m.make_spatial_indexes([],water,rivers)
tree=area if layer=='water' else river
assert len(tree.query(Point(1,1),predicate='intersects'))==1
if layer=='rivers':
    cells=m.river_cells_from_verified_features(features,geometries,1,1,0,500)
    assert cells[(0,0)][0]['id_dera']==123 and cells[(0,0)][0]['name']=='synthetic'
try:
    m.read_pinned_collection(p,pin,1,'DERA '+layer)
except ValueError as error:
    assert 'hash mismatch' in str(error)
else:
    raise AssertionError('changed source was accepted')
'''
        result=subprocess.run([str(python),'-c',script,str(path),expected,layer],cwd=ROOT,capture_output=True,text=True)
        assert result.returncode==0,result.stderr


def test_hardlinked_output_and_input_alias_are_rejected(tmp_path,monkeypatch):
    import os
    private=tmp_path/'private';private.mkdir(mode=0o700)
    monkeypatch.setattr(fuel.source,'PRIVATE_ROOT',private)
    worktree=tmp_path/'checkout';worktree.mkdir()
    (worktree/'.git').mkdir(); (worktree/'.git'/'HEAD').write_text('ref: refs/heads/test\n')
    external=worktree/'source.json';external.write_text('existing source')
    output=private/'result.json';os.link(external,output)
    before=external.read_bytes()
    with pytest.raises(ValueError,match='hardlinked output'):
        fuel.check_private_output(output,[external])
    with pytest.raises(ValueError,match='hardlinked output'):
        fuel.write_private_atomic(output,{'private':'never exposed'},[external])
    assert external.read_bytes()==output.read_bytes()==before
    assert external.stat().st_ino==output.stat().st_ino


def test_atomic_output_replaces_inode_without_touching_old_links(tmp_path,monkeypatch):
    import os
    private=tmp_path/'private';private.mkdir(mode=0o700)
    monkeypatch.setattr(fuel.source,'PRIVATE_ROOT',private)
    output=private/'result.json';output.write_text('old harmless contents')
    old_inode=output.stat().st_ino
    exposed=tmp_path/'linked-old-output.json'
    replace=os.replace
    def link_old_then_replace(src,dst,**kwargs):
        # Simulate a link added after the output check and before replacement.
        os.link(output,exposed)
        return replace(src,dst,**kwargs)
    monkeypatch.setattr(fuel.os,'replace',link_old_then_replace)
    fuel.write_private_atomic(output,{'private':'new content'},[])
    assert exposed.read_text()=='old harmless contents'
    assert exposed.stat().st_ino==old_inode
    assert output.stat().st_ino!=old_inode and output.stat().st_nlink==1
    assert json.loads(output.read_text())=={'private':'new content'}
    assert output.stat().st_mode & 0o777==0o600
    assert not list(private.glob('.mixed-estimated-*.tmp'))


def test_output_path_itself_cannot_be_a_pinned_input(tmp_path,monkeypatch):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    monkeypatch.setattr(fuel.source,'PRIVATE_ROOT',private)
    source=private/'source.json';source.write_text('source must survive')
    with pytest.raises(ValueError,match='overwrite an input'):
        fuel.write_private_atomic(source,{'private':'forbidden'},[source])
    assert source.read_text()=='source must survive'


def test_atomic_failure_keeps_original_and_removes_temp(tmp_path,monkeypatch):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    monkeypatch.setattr(fuel.source,'PRIVATE_ROOT',private)
    destination=private/'result.json';destination.write_text('prior candidate')
    with pytest.raises(ValueError):
        fuel.write_private_atomic(destination,{'invalid':float('nan')},[])
    assert destination.read_text()=='prior candidate'
    assert not list(private.glob('.mixed-estimated-*.tmp'))


def test_pinned_json_rejects_duplicate_keys_even_when_hash_matches(tmp_path):
    path=tmp_path/'duplicate.json';path.write_bytes(b'{"a":1,"a":2}')
    with pytest.raises(ValueError,match='duplicate JSON key'):
        fuel.read_pinned_json(path,file_sha256(path),'synthetic source')


def test_reconstruction_uses_verified_dera_snapshots_for_all_followup_data(monkeypatch):
    from types import SimpleNamespace
    args=SimpleNamespace(parent='parent',boundary_archive='boundary',fuel_archive='fuel',
        expanded='expanded',parent_sample='old',coast='coast',water='water',worldcover='world',
        osm_manifest='manifest',osm_samples='samples',strict='strict',strict_sha256='s',
        water_v2='v2',water_v2_sha256='v',catalog='catalog',exercise_v3='v3',exercise_v3_sha256='t',
        dera_rivers='rivers',exercise_v4='v4',exercise_v4_sha256='u')
    docs={'strict':{'name':'strict'},'v2':{'name':'v2'},'v3':{'name':'v3'},
          'v4':{'name':'v4'},'catalog':{'name':'catalog'}}
    monkeypatch.setattr(fuel,'read_json',lambda path:docs[path])
    monkeypatch.setattr(fuel.source.base,'load_parent',lambda *_:{})
    monkeypatch.setattr(fuel.source.base,'inspect_boundaries',lambda *_:{})
    monkeypatch.setattr(fuel.source.base,'sample_fuel',lambda *a,**k:[])
    monkeypatch.setattr(fuel.source.base,'compile_map',lambda *a:{})
    monkeypatch.setattr(fuel.source.terrain,'build',lambda *a:{})
    monkeypatch.setattr(fuel.source,'surface_inputs',lambda *a,**k:([],[],{},[]))
    monkeypatch.setattr(fuel.source,'compile_document',lambda *a:docs['strict'])
    monkeypatch.setattr(fuel.source,'checked_document',lambda *a:None)
    monkeypatch.setattr(fuel,'load_pinned_osm_water',lambda *a,**k:([],{},{}))
    monkeypatch.setattr(fuel.source,'water_conflicts',lambda *a:{})
    monkeypatch.setattr(fuel.source,'compile_water_v2',lambda *a:docs['v2'])
    monkeypatch.setattr(fuel.source,'compile_exercise_v3',lambda *a:docs['v3'])
    monkeypatch.setattr(fuel.source,'compile_exercise_v4',lambda *a:docs['v4'])
    def forbidden(*args,**kwargs):
        raise AssertionError('unverified DERA path reopened')
    monkeypatch.setattr(fuel.source,'collection',forbidden)
    monkeypatch.setattr(fuel.source,'dera_river_conflicts',forbidden)
    calls=[]
    def verified(path,pin,count,label,*,include_features=False):
        calls.append((path,pin,count,label,include_features))
        return (['verified features'],['verified river geometry']) if include_features else ['verified water geometry']
    monkeypatch.setattr(fuel,'read_pinned_collection',verified)
    def river_cells(features,geometries,*args):
        assert features==['verified features'] and geometries==['verified river geometry']
        return {'from':'verified snapshot'}
    monkeypatch.setattr(fuel,'river_cells_from_verified_features',river_cells)
    result=fuel.reconstruct(args)
    assert calls==[('water',fuel.source.PINS['water'],94,'DERA water',False),
                   ('rivers',fuel.source.DERA_RIVER_PIN,410,'DERA rivers',True)]
    assert result[4]=={'from':'verified snapshot'}
    assert result[-2:]==(['verified water geometry'],['verified river geometry'])


def test_osm_tile_mutation_between_passes_is_rejected(tmp_path):
    import hashlib
    python=Path('/home/juancm/miniconda3/envs/detectree/bin/python')
    if not python.is_file(): pytest.skip('GIS interpreter unavailable')
    tile=tmp_path/'tile.json';manifest=tmp_path/'manifest.json';samples=tmp_path/'samples.json'
    tile_data={'elements':[{'type':'node','id':1,'version':1,'lon':0,'lat':40},
                           {'type':'node','id':2,'version':1,'lon':0.001,'lat':40.001},
                           {'type':'way','id':3,'version':1,'nodes':[1,2],'tags':{'waterway':'stream'}}]}
    tile_raw=json.dumps(tile_data).encode();tile.write_bytes(tile_raw)
    manifest_data={'status':'complete_requests','started_at':'2026-01-01',
                   'tiles':[{'status':'success','path':str(tile),'sha256':hashlib.sha256(tile_raw).hexdigest()}]}
    manifest_raw=json.dumps(manifest_data).encode();manifest.write_bytes(manifest_raw)
    samples_raw=b'{"tiles":[]}';samples.write_bytes(samples_raw)
    script=r'''
import sys
from pathlib import Path
sys.path.insert(0,'eval')
import mixed_estimated_fuel_v1 as m
manifest,samples,tile=map(Path,sys.argv[1:4])
m.source.PINS['osm_manifest']=sys.argv[4];m.source.PINS['osm_samples']=sys.argv[5]
m.source.base.BOUNDS=[100000,0,1000000,11000000]
features,stats,_=m.load_pinned_osm_water(manifest,samples)
assert len(features)==1 and stats['watercourse_line_features']==1
read=Path.read_bytes; calls=[]
def read_then_mutate(self):
    raw=read(self)
    if self==tile and not calls:
        calls.append(1)
        self.write_bytes(raw.replace(b'40.001',b'40.002'))
    return raw
Path.read_bytes=read_then_mutate
try:
    m.load_pinned_osm_water(manifest,samples)
except ValueError as error:
    assert 'OSM water tile hash mismatch' in str(error)
else:
    raise AssertionError('mutated OSM tile accepted on second read')
'''
    result=subprocess.run([str(python),'-c',script,str(manifest),str(samples),str(tile),
                           hashlib.sha256(manifest_raw).hexdigest(),hashlib.sha256(samples_raw).hexdigest()],
                          cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def synthetic_tile_manifests(tmp_path,monkeypatch,tile,*,use_sample=False):
    manifest=tmp_path/'manifest.json';samples=tmp_path/'samples.json'
    entry={'status':'success','path':str(tile),'sha256':file_sha256(tile)}
    manifest.write_text(json.dumps({'status':'complete_requests','started_at':'2026-01-01',
                                    'tiles':[] if use_sample else [entry]}))
    samples.write_text(json.dumps({'tiles':[entry] if use_sample else []}))
    monkeypatch.setitem(fuel.source.PINS,'osm_manifest',file_sha256(manifest))
    monkeypatch.setitem(fuel.source.PINS,'osm_samples',file_sha256(samples))
    return manifest,samples


@pytest.mark.parametrize('use_sample,link_kind',[(False,'symlink'),(True,'symlink'),(False,'hardlink')])
def test_manifest_tile_alias_to_output_is_protected(tmp_path,monkeypatch,use_sample,link_kind):
    import os
    private=tmp_path/'private';private.mkdir(mode=0o700)
    monkeypatch.setattr(fuel.source,'PRIVATE_ROOT',private)
    output=private/'result.json';output.write_text('{"elements":[]}')
    tile=tmp_path/'tile.json'
    if link_kind=='symlink': tile.symlink_to(output)
    else: os.link(output,tile)
    before=output.read_bytes()
    if link_kind=='symlink': assert output.stat().st_nlink==1
    manifest,samples=synthetic_tile_manifests(tmp_path,monkeypatch,tile,use_sample=use_sample)
    snapshot=fuel.snapshot_osm_tile_inputs(manifest,samples)
    if link_kind=='symlink': assert snapshot[0][1]==output.resolve()
    else: assert os.path.samefile(snapshot[0][0],output)
    protected=[manifest,samples]+fuel.protected_osm_tile_paths(snapshot)
    with pytest.raises(ValueError,match='overwrite an input|hardlinked output'):
        fuel.write_private_atomic(output,{'private':'must not overwrite tile'},protected)
    assert output.read_bytes()==tile.read_bytes()==before


def test_tile_symlink_retarget_before_replace_aborts_without_overwrite(tmp_path,monkeypatch):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    monkeypatch.setattr(fuel.source,'PRIVATE_ROOT',private)
    output=private/'result.json';output.write_text('prior output')
    source_file=tmp_path/'source.json';source_file.write_text('{"elements":[]}')
    tile=tmp_path/'tile.json';tile.symlink_to(source_file)
    manifest,samples=synthetic_tile_manifests(tmp_path,monkeypatch,tile)
    snapshot=fuel.snapshot_osm_tile_inputs(manifest,samples)
    protected=[manifest,samples]+fuel.protected_osm_tile_paths(snapshot)
    def retarget_and_check():
        tile.unlink();tile.symlink_to(output)
        fuel.verify_osm_tile_identities(snapshot)
    with pytest.raises(ValueError,match='OSM tile identity changed'):
        fuel.write_private_atomic(output,{'private':'must not overwrite tile'},protected,
                                  pre_replace_check=retarget_and_check)
    assert output.read_text()=='prior output' and source_file.read_text()=='{"elements":[]}'
    assert tile.read_text()=='prior output'
    assert not list(private.glob('.mixed-estimated-*.tmp'))


def test_cli_rejects_manifest_tile_symlink_to_output_before_reconstruction(tmp_path,monkeypatch):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    monkeypatch.setattr(fuel.source,'PRIVATE_ROOT',private)
    output=private/'result.json';output.write_text('{"elements":[]}')
    tile=tmp_path/'tile.json';tile.symlink_to(output)
    manifest,samples=synthetic_tile_manifests(tmp_path,monkeypatch,tile)
    keys=('parent','fuel-archive','boundary-archive','expanded','parent-sample','coast','water','worldcover',
          'osm-manifest','osm-samples','dera-rivers','catalog','strict','water-v2','exercise-v3','exercise-v4','targets')
    arguments=['mixed_estimated_fuel_v1.py','compile']
    for key in keys:
        path=manifest if key=='osm-manifest' else samples if key=='osm-samples' else tmp_path/key
        if key not in ('osm-manifest','osm-samples'): path.write_text('synthetic input')
        arguments.extend(['--'+key,str(path)])
    for key in ('strict-sha256','water-v2-sha256','exercise-v3-sha256','exercise-v4-sha256','targets-sha256'):
        arguments.extend(['--'+key,'0'*64])
    arguments.extend(['--output',str(output)])
    monkeypatch.setattr(sys,'argv',arguments)
    before=output.read_bytes()
    def forbidden(*args,**kwargs): raise AssertionError('reconstruction must not start')
    monkeypatch.setattr(fuel,'reconstruct',forbidden)
    with pytest.raises(ValueError,match='overwrite an input'):
        fuel.main()
    assert output.read_bytes()==tile.read_bytes()==before and output.stat().st_nlink==1


def test_dangling_tile_alias_cannot_create_a_new_output(tmp_path,monkeypatch):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    monkeypatch.setattr(fuel.source,'PRIVATE_ROOT',private)
    output=private/'new-result.json'
    source_file=tmp_path/'source.json';source_file.write_text('{"elements":[]}')
    tile=tmp_path/'tile.json';tile.symlink_to(source_file)
    manifest,samples=synthetic_tile_manifests(tmp_path,monkeypatch,tile)
    snapshot=fuel.snapshot_osm_tile_inputs(manifest,samples)
    protected=[manifest,samples]+fuel.protected_osm_tile_paths(snapshot)
    def retarget_and_check():
        tile.unlink();tile.symlink_to(output)
        fuel.verify_osm_tile_identities(snapshot)
    with pytest.raises(ValueError,match='OSM tile identity changed'):
        fuel.write_private_atomic(output,{'private':'must not replace tile'},protected,
                                  pre_replace_check=retarget_and_check)
    assert not output.exists() and source_file.read_text()=='{"elements":[]}'
    assert not list(private.glob('.mixed-estimated-*.tmp'))
