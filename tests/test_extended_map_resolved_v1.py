"""Unknowns and estimates must survive reconstruction without becoming source facts."""
import copy
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'eval'))
import extended_map_resolved_v1 as resolved
from extended_map_common import digest


def test_surface_requires_agreement_and_rejects_shoreline_water_and_nodata():
    classify=resolved.classify_cell
    assert classify(True,False,False,{10,20}) == 'land'
    assert classify(False,True,False,{0,80}) == 'sea'
    assert classify(True,False,False,{0,10}) == 'unknown'
    assert classify(True,False,False,{10,80}) == 'unknown'
    assert classify(True,False,False,{10,90}) == 'unknown'
    assert classify(True,False,True,{10}) == 'unknown'
    assert classify(False,False,False,{10}) == 'unknown'
    assert classify(False,True,False,{0,10}) == 'unknown'
    assert classify(False,True,False,set()) == 'unknown'


def test_estimate_uses_known_land_neighbors_with_support_threshold():
    surface=[['land']*5 for _ in range(5)]
    source=[[None]*5 for _ in range(5)]
    source[1][2]=4; source[2][1]=4; source[2][3]=4; source[3][2]=5
    e=resolved.estimate(2,2,source,surface)
    assert e['class']==4 and e['support_count']==4 and e['agreement']==.75
    assert e['confidence']=='low_uncalibrated' and e['method']=='neighbour_majority_1000m_v1'
    assert resolved.estimate(2,2,source,surface,exclude_quadrant=(0,1)) is None
    source[3][2]=5; source[2][3]=5
    assert resolved.estimate(2,2,source,surface) is None
    surface[2][3]='unknown'; surface[3][2]='sea'
    assert resolved.estimate(2,2,source,surface) is None
    assert resolved.vegetation_supported({'10':2250,'50':250})
    assert not resolved.vegetation_supported({'10':2249,'50':251})


def test_estimates_are_separate_unknowns_censored_and_tampering_rejected():
    source=[[None]*5 for _ in range(5)]
    source[1][2]=4; source[2][1]=4; source[2][3]=4
    source[0][0]=7
    surfaces=[['land']*5 for _ in range(5)]
    surfaces[0][0]='sea'  # contradictory fuel demotes surface, preserving evidence
    surfaces[4][4]='unknown'
    cover=[[{'20':2500} for _ in range(5)] for _ in range(5)]
    map_doc={'source_fuel_class':source,'grid':{},'sha256':'map'}
    terrain={'sha256':'terrain','elevation_candidate':[[None]],'zero_samples_per_cell':[[1]]}
    doc=resolved.compile_document(map_doc,terrain,surfaces,cover,{})
    assert doc['cells'][2][2]['fuel_status']=='estimated'
    assert doc['cells'][2][2]['source_fuel_class'] is None
    assert doc['cells'][2][2]['fuel_provenance'] == {'kind':'spatial_estimate', 'confidence':'low_uncalibrated'}
    assert doc['cells'][1][2]['fuel_provenance']['kind'] == 'effis_source_center_sample'
    assert doc['cells'][2][2]['estimated_fuel']['class']==4
    assert doc['cells'][0][0]['surface']=='unknown' and doc['cells'][0][0]['source_fuel_class']==7
    assert doc['cells'][4][4]['surface']=='unknown' and doc['cells'][4][4]['fuel_status']=='unknown'
    assert doc['status']=='private_evidence_not_simulator_input'
    resolved.validate_document(doc,map_doc,terrain,surfaces,cover,{})
    bad=copy.deepcopy(doc); bad['cells'][2][2]['fuel_status']='source'
    bad['sha256']=digest({k:v for k,v in bad.items() if k!='sha256'})
    with pytest.raises(ValueError,match='reconstruction'):
        resolved.validate_document(bad,map_doc,terrain,surfaces,cover,{})


def test_source_hash_and_collection_integrity(tmp_path):
    bad=tmp_path/'bad'; bad.write_text('{}')
    with pytest.raises(ValueError,match='hash mismatch'):
        resolved.checked(bad,'coast')
    with pytest.raises(ValueError,match='incomplete'):
        resolved.collection(bad,1580)


def test_candidate_output_is_private_and_outside_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(resolved, "PRIVATE_ROOT", tmp_path / "private")
    resolved.require_private_path(tmp_path / "private" / "candidate.json")
    resolved.reject_output_alias(tmp_path/'candidate.json',[tmp_path/'source.json'])
    with pytest.raises(ValueError,match='overwrite an input'):
        resolved.reject_output_alias(tmp_path/'source.json',[tmp_path/'source.json'])
    with pytest.raises(ValueError,match='outside all git worktrees'):
        resolved.require_private_path(ROOT / '.runtime' / 'candidate.json')
    with pytest.raises(ValueError,match='outside all git worktrees'):
        resolved.require_private_path(ROOT / 'data' / 'candidate.json')
    other=tmp_path/'other-worktree'; other.mkdir(); (other/'.git').write_text('gitdir: /tmp/elsewhere')
    with pytest.raises(ValueError,match='outside all git worktrees'):
        resolved.require_private_path(other/'candidate.json')
    nested=tmp_path/'private'/'nested'; nested.mkdir(parents=True); (nested/'.git').write_text('gitdir: /tmp/elsewhere')
    with pytest.raises(ValueError,match='outside all git worktrees'):
        resolved.require_private_path(nested/'candidate.json')


def test_private_candidate_matches_public_aggregate():
    from collections import Counter
    from extended_map_common import read_json
    path=Path('/tmp/wildfire-map-resolution-private/resolution-v1.json')
    if not path.is_file():
        pytest.skip('private candidate unavailable')
    doc=read_json(path)
    evidence=read_json(ROOT/'data/extended_map_resolved_v1/evidence.json')
    assert path.stat().st_mode & 0o777 == 0o600
    assert doc['sha256']==digest({k:v for k,v in doc.items() if k!='sha256'})
    assert doc['sha256']==evidence['private_candidate_sha256']
    assert doc['summary']==evidence['summary']
    cells=[cell for row in doc['cells'] for cell in row]
    counts=Counter((cell['surface'],cell['fuel_status']) for cell in cells)
    assert counts[('land','estimated')]==evidence['surface_fuel_cross_counts']['land_estimated']
    assert counts[('land','unknown')]==evidence['surface_fuel_cross_counts']['land_unknown']
    assert all(cell['estimated_fuel'] is None or
               (cell['surface']=='land' and cell['source_fuel_class'] is None and
                cell['fuel_provenance']['confidence']=='low_uncalibrated') for cell in cells)


def test_sensitivity_is_separate_bounded_and_keeps_mixed_unknown():
    def cell(surface,status,hist):
        return {'surface':surface,'fuel_status':status,'worldcover_samples':hist}
    strict={'cells':[[cell('land','unknown',{'10':2400,'60':100}),
                      cell('land','unknown',{'20':2400,'50':100}),
                      cell('land','unknown',{'10':2300,'20':200}),
                      cell('land','unknown',{'30':2500}),
                      cell('unknown','unknown',{'10':2500}),
                      cell('land','estimated',{'10':2500})]]}
    strict['sha256']=digest(strict)
    candidate=resolved.compile_sensitivity(strict)
    assert candidate['strict_candidate_sha256']==strict['sha256']
    assert candidate['summary']['synthetic_parameterized']==2
    assert candidate['summary']['unresolved_built_up']==1
    assert candidate['summary']['unresolved_mixed_or_unmapped']==1
    assert [(x['initial_fuel_low'],x['initial_fuel_high']) for x in candidate['cells']] == [(0.45,1.0),(0.375,1.0)]
    assert candidate['parameter_sum_sensitivity']=={'low':0.825,'high':2.0}
    assert all(x['kind']=='synthetic_sensitivity_only' and x['confidence']=='not_calibrated' for x in candidate['cells'])
    resolved.validate_sensitivity(candidate,strict)
    forged=copy.deepcopy(candidate); forged['cells'][0]['initial_fuel_low']=0
    forged['sha256']=digest({k:v for k,v in forged.items() if k!='sha256'})
    with pytest.raises(ValueError,match='reconstruction'):
        resolved.validate_sensitivity(forged,strict)


def test_sensitivity_aggregate_matches_private_candidate():
    from extended_map_common import read_json
    strict_path=Path('/tmp/wildfire-map-resolution-private/resolution-v1.json')
    sensitivity_path=Path('/tmp/wildfire-map-resolution-private/sensitivity-v1.json')
    if not strict_path.is_file() or not sensitivity_path.is_file():
        pytest.skip('private sensitivity candidate unavailable')
    strict=read_json(strict_path); sensitivity=read_json(sensitivity_path)
    evidence=read_json(ROOT/'data/extended_map_resolved_v1/evidence.json')['synthetic_sensitivity']
    resolved.validate_sensitivity(sensitivity,strict)
    assert sensitivity_path.stat().st_mode & 0o777 == 0o600
    assert sensitivity['sha256']==evidence['private_document_sha256']
    assert sensitivity['summary']==evidence['summary']
    assert sensitivity['parameter_sum_sensitivity']==evidence['parameter_sum_sensitivity']


def test_water_conflicts_demote_and_recalculate_estimates():
    source=[[None]*5 for _ in range(5)]
    source[1][2]=4; source[2][1]=4; source[2][3]=4
    surfaces=[['land']*5 for _ in range(5)]
    cover=[[{'20':2500} for _ in range(5)] for _ in range(5)]
    map_doc={'source_fuel_class':source,'grid':{},'sha256':'map'}
    terrain={'sha256':'terrain','elevation_candidate':[[None]],'zero_samples_per_cell':[[1]]}
    strict=resolved.compile_document(map_doc,terrain,surfaces,cover,{})
    conflicts={(1,2):[{'way_id':123,'kind':'water_area','geometry':'polygon','epoch':'2026'}]}
    v2=resolved.compile_water_v2(strict,map_doc,terrain,surfaces,cover,{},conflicts,{'water_area_polygon_features':1})
    assert strict['cells'][1][2]['surface']=='land'
    assert strict['cells'][2][2]['fuel_status']=='estimated'
    assert v2['cells'][1][2]['surface']=='unknown'
    assert v2['cells'][1][2]['source_fuel_class']==4
    assert v2['cells'][1][2]['osm_water_conflicts'][0]['way_id']==123
    assert v2['cells'][2][2]['fuel_status']=='unknown'
    assert v2['parent_document_sha256']==strict['sha256']
    assert resolved.osm_water_kind({'natural':'water'})=='water_area'
    assert resolved.osm_water_kind({'waterway':'stream'})=='watercourse'


def test_missing_source_fails_release_validation(tmp_path):
    with pytest.raises(FileNotFoundError,match='required validation sources absent') as err:
        resolved.require_source_files([tmp_path/'missing-fuel',tmp_path/'missing-water'])
    assert 'missing-fuel' in str(err.value) and 'missing-water' in str(err.value)


def test_every_aggregate_field_is_checked_against_artifacts():
    from extended_map_common import read_json
    private=Path('/tmp/wildfire-map-resolution-private')
    paths=[private/x for x in ('resolution-v1.json','sensitivity-v1.json','dera-archive-audit.json','resolution-v2.json','sensitivity-v2.json','inland-mixed-exercise-v3.json','small-water-exercise-v4.json')]
    missing=[str(p) for p in paths if not p.is_file()]
    if missing:
        pytest.skip('private artifacts unavailable: '+', '.join(missing))
    strict,sensitivity,archive,v2,sensitivity_v2,exercise_v3,exercise_v4=map(read_json,paths)
    evidence=read_json(ROOT/'data/extended_map_resolved_v1/evidence.json')
    resolved.validate_aggregate(evidence,strict,sensitivity,archive,v2,sensitivity_v2,exercise_v3,exercise_v4)
    def leaves(value, prefix=()):
        if isinstance(value,dict):
            for key,entry in value.items(): yield from leaves(entry,prefix+(key,))
        else: yield prefix
    for keys in leaves(evidence):
        bad=copy.deepcopy(evidence); node=bad
        for key in keys[:-1]: node=node[key]
        node[keys[-1]]='tampered'
        with pytest.raises(ValueError,match='aggregate differs'):
            resolved.validate_aggregate(bad,strict,sensitivity,archive,v2,sensitivity_v2,exercise_v3,exercise_v4)


def test_water_v2_private_area_conflicts_remain_unknown():
    from extended_map_common import read_json
    path=Path('/tmp/wildfire-map-resolution-private/resolution-v2.json')
    if not path.is_file(): pytest.skip('private water candidate unavailable')
    cells=[cell for row in read_json(path)['cells'] for cell in row
           if any(x['kind'] in ('water_area','water_area_relation')
                  for x in cell.get('osm_water_conflicts',[]))]
    assert cells and all(cell['surface']=='unknown' for cell in cells)


def test_release_gate_reports_missing_osm_tile(tmp_path, monkeypatch):
    import json
    manifest=tmp_path/'manifest.json'; samples=tmp_path/'samples.json'
    missing=tmp_path/'absent-tile.json'
    manifest.write_text(json.dumps({'tiles':[{'status':'success','path':str(missing)}]}))
    samples.write_text(json.dumps({'tiles':[]}))
    monkeypatch.setattr(resolved,'checked',lambda *args:None)
    with pytest.raises(FileNotFoundError,match='required validation sources absent') as err:
        resolved.require_osm_tile_files(manifest,samples)
    assert str(missing) in str(err.value)


def test_mixed_hypotheses_reject_coastal_or_water_polygon_evidence():
    hist={'10':2490,'80':10}
    line={'kind':'watercourse','geometry':'line','way_id':1,'epoch':'2026'}
    entry={'surface':'unknown','worldcover_samples':hist,'source_fuel_class':4}
    strict={'cells':[[entry,copy.deepcopy(entry)],[copy.deepcopy(entry),copy.deepcopy(entry)]],
            'terrain_candidate':[[100,100],[100,100]]}
    strict['sha256']=digest(strict)
    v2={'parent_document_sha256':strict['sha256'],
        'cells':[[dict(entry,osm_water_conflicts=[line]),copy.deepcopy(entry)],
                 [copy.deepcopy(entry),copy.deepcopy(entry)]]}
    v2['sha256']=digest(v2)
    audit={'strict_sha256':strict['sha256'],'v2_sha256':v2['sha256'],
           'reviewed_cells':[{'column':0,'row':0,'worldcover_samples':hist,
               'fuel_source_class':4,'terrain_candidate_mean':100,
               'dera_land_face_covers':True,'osm_land_face_covers':True,
               'coast_distance_m':1000,'osm_coast_distance_m':1000,
               'water_polygon_intersections':0,'osm_area_conflicts':0}],
           'cases':{'synthetic':{'v2_unknown':[[0,0],[1,1]],'v2_unknown_count':2}}}
    audit['sha256']=digest(audit)
    def build(a):
        return resolved.compile_interior_mixed(strict,v2,a,strict_pin=strict['sha256'],v2_pin=v2['sha256'],audit_pin=a['sha256'])
    result=build(audit)
    assert result['cells'][0]['sample_nonwater_fraction']==0.996
    assert result['cells'][0]['surface']=='mixed'
    assert result['hypothetical_surface_preflight']['synthetic']['hypothetical_unknown_after_mixed']==1
    for field,value in [('coast_distance_m',250),('osm_coast_distance_m',250),('water_polygon_intersections',1),('osm_area_conflicts',1)]:
        bad=copy.deepcopy(audit); bad['reviewed_cells'][0][field]=value
        bad['sha256']=digest({k:v for k,v in bad.items() if k!='sha256'})
        with pytest.raises(ValueError,match='evidence incomplete'):
            build(bad)
    bad=copy.deepcopy(audit); bad['reviewed_cells'][0]['row']=-1
    bad['sha256']=digest({k:v for k,v in bad.items() if k!='sha256'})
    with pytest.raises(ValueError,match='invalid mixed cell coordinate'):
        build(bad)


def test_aggregate_command_explicitly_reports_absent_sources(tmp_path):
    import subprocess
    cmd=[sys.executable,str(ROOT/'eval/extended_map_resolved_v1.py'),'validate-aggregate']
    for name in ('parent','fuel-archive','boundary-archive','expanded','parent-sample','coast','water','worldcover','osm-manifest','osm-samples'):
        cmd += ['--'+name,str(tmp_path/name)]
    private='/tmp/wildfire-map-resolution-private/'
    for name in ('output','sensitivity-output','water-v2-output','water-v2-sensitivity-output','exercise-v3-output','exercise-v4-output'):
        cmd += ['--'+name,private+'missing-'+name+'.json']
    for name in ('relief-archive','limits-archive','archive-audit','aggregate','exercise-v3-catalog','dera-rivers'):
        cmd += ['--'+name,str(tmp_path/name)]
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode!=0
    assert 'required validation sources absent' in result.stderr
    assert 'fuel-archive' in result.stderr and 'missing-water-v2-output.json' in result.stderr
    assert 'dera-rivers' in result.stderr and 'missing-exercise-v4-output.json' in result.stderr


def test_v3_eligibility_requires_inland_source_fuel_and_no_water_area():
    line={'kind':'watercourse','geometry':'line','way_id':1,'epoch':'2026'}
    cell={'surface':'unknown','osm_water_conflicts':[line],'fuel_status':'source',
          'source_fuel_class':4,'worldcover_samples':{'10':2475,'80':25}}
    assert resolved.select_exercise_mixed(cell,True)==(True,'eligible')
    assert resolved.select_exercise_mixed(cell,False)==(False,'not_corroborated_interior')
    for kind in ('water_area','water_area_relation'):
        conflict=copy.deepcopy(cell);conflict['osm_water_conflicts'].append({'kind':kind})
        assert resolved.select_exercise_mixed(conflict,True)[1]=='osm_water_area_or_open_area_boundary'
    for status in ('unknown','estimated'):
        missing=copy.deepcopy(cell);missing['fuel_status']=status
        assert resolved.select_exercise_mixed(missing,True)[1]=='no_source_fuel'
    for hist in ({'10':2474,'80':26}, {'10':2499,'0':1}, {'10':2000,'50':500}):
        bad=copy.deepcopy(cell);bad['worldcover_samples']=hist
        assert resolved.select_exercise_mixed(bad,True)[1]=='worldcover_mixed_or_major_water'
    sea=copy.deepcopy(cell);sea['surface']='sea'
    assert resolved.select_exercise_mixed(sea,True)[0] is False
    assert resolved.EXERCISE_FACTORS=={'low':0.8,'nominal':0.9,'high':1.0}


def test_v3_preflight_counts_unknown_and_keeps_original_initial_cells(monkeypatch):
    grid={'width':7,'height':7,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    parent={'grid':grid,'sha256':'a'*64,'network_sha256':'b'*64}
    base_cell={'surface':'land','fuel_status':'source','source_fuel_class':4}
    v2={'grid':grid,'cells':[[copy.deepcopy(base_cell) for _ in range(7)] for _ in range(7)],
        'terrain_candidate':[[100]*7 for _ in range(7)]}
    v2['cells'][3][4]={'surface':'unknown','fuel_status':'source','source_fuel_class':4}
    v2['cells'][2][4]={'surface':'unknown','fuel_status':'unknown','source_fuel_class':None}
    cases=[]
    for i in range(12):
        case={'id':f'incident-eval-v1-{i:03}','initial_fire':[[3,3]]}
        case['sha256']=digest(case);cases.append(case)
    catalog={'version':'wildfire-incident-eval-catalog-v1','inputs_sha256':parent['sha256'],
             'network_sha256':parent['network_sha256'],'cases':cases}
    catalog['sha256']=digest(catalog);monkeypatch.setattr(resolved,'EXERCISE_CATALOG_PIN',catalog['sha256'])
    original=copy.deepcopy(catalog)
    result=resolved.exercise_neighborhoods(catalog,parent,v2,{(3,4)})
    assert len(result)==12 and catalog==original
    assert result[0]['mixed_exercise_cells']==1
    assert result[0]['unknown_surface_cells']==1
    assert not result[0]['fire_layer_stencil_complete_assuming_estimates']
    result2=resolved.exercise_neighborhoods(catalog,parent,v2,{(3,4),(2,4)})
    assert result2[0]['fuel_unknown_cells']==1  # hypothetical mask never fills missing fuel
    assert not result2[0]['fire_layer_stencil_complete_assuming_estimates']


def test_v3_private_overlay_preserves_v2_and_reports_all_initial_cases():
    from extended_map_common import read_json
    root=Path('/tmp/wildfire-map-resolution-private')
    paths=[root/'resolution-v2.json',root/'inland-mixed-exercise-v3.json']
    if not all(p.is_file() for p in paths): pytest.skip('private v2 or v3 unavailable')
    v2,v3=map(read_json,paths)
    assert v3['version']=='extended-map-inland-mixed-exercise-v3'
    assert v3['parent_v2_sha256']==v2['sha256']
    assert v3['sha256']==digest({k:v for k,v in v3.items() if k!='sha256'})
    assert paths[1].stat().st_mode & 0o777 == 0o600
    seen=set()
    for cell in v3['cells']:
        c,r=cell['column'],cell['row']; prior=v2['cells'][r][c]
        assert (r,c) not in seen;seen.add((r,c))
        assert prior['surface']=='unknown' and prior['fuel_status']=='source'
        assert cell['surface']=='mixed_exercise_assumption'
        assert cell['source_fuel_class']==prior['source_fuel_class']
        assert cell['fuel_factor_assumptions']=={'low':0.8,'nominal':0.9,'high':1.0}
        assert not any(e['kind'] in ('water_area','water_area_relation') for e in prior.get('osm_water_conflicts',[]))
    assert v3['summary']['mixed_exercise_cells']==len(seen)


def test_v4_geometry_and_dera_conflicts_with_synthetic_features():
    import subprocess
    python=Path('/home/juancm/miniconda3/envs/detectree/bin/python')
    if not python.is_file(): pytest.skip('GIS interpreter unavailable')
    script=r'''
import sys
from shapely.geometry import box,LineString,Polygon
sys.path.insert(0,'eval')
import extended_map_resolved_v1 as m
from extended_map_common import digest
cell=box(0,0,500,500)
def assessment(polys,refs=None,tags=None):
    refs=refs or [{'kind':'water_area','geometry':'polygon','way_id':k} for k in polys]
    meta={k:{'version':1,'tags':tags or {'natural':'water'}} for k in polys}
    return m.assess_small_water_polygons(cell,refs,polys,meta)
ok,reason,info=assessment({1:box(100,100,110,110),2:box(200,200,210,210)})
assert ok and info['mapped_union_area_m2']==200
assert not assessment({1:box(100,100,225,200)})[0]  # exactly 5%
assert not assessment({1:box(0,0,10,10)})[0]  # touches cell edge
assert not assessment({1:box(100,100,120,120),2:box(120,100,130,120)})[0]  # touching
assert not assessment({1:box(100,100,110,110)},tags={'natural':'wetland'})[0]
assert not assessment({1:box(100,100,110,110)},refs=[{'kind':'water_area_relation','geometry':'polygon','relation_id':1}])[0]
hole=Polygon([(10,10),(40,10),(40,40),(10,40)],holes=[[(20,20),(30,20),(30,30),(20,30)]])
assert not assessment({1:hole})[0]
# Test full-cell DERA line intersection without any real source data.
lines=[LineString([(290100,4064250),(290200,4064250)])]+[LineString([(0,i),(1,i)]) for i in range(409)]
obj={'features':[{'properties':{'id_dera':i,'nombre':'synthetic'}} for i in range(410)]}
m.file_sha256=lambda _:m.DERA_RIVER_PIN
m.read_json=lambda _:obj
m.collection=lambda *_:lines
hits=m.dera_river_conflicts('unused',2,1,290000,4064500)
assert list(hits)==[(0,0)] and hits[(0,0)][0]['source_epoch']=='unverified'
# A DERA-challenged land cell with no source fuel must be censored, not a firebreak.
grid={'width':3,'height':1,'crs':'EPSG:25830','bounds':[290000,4064000,291500,4064500],
      'transform':[500,0,290000,0,-500,4064500]}
def row(surface,status,fuel,conflicts):
    return {'surface':surface,'fuel_status':status,'source_fuel_class':fuel,
            'fuel_provenance':{'kind':'test'},'worldcover_samples':{'10':2500},
            'osm_water_conflicts':conflicts}
v2={'version':'extended-map-resolution-evidence-v2','grid':grid,
    'cells':[[row('land','unknown',None,[]),
              row('unknown','source',4,[{'kind':'water_area','geometry':'polygon','way_id':1},
                                         {'kind':'watercourse','geometry':'line','way_id':2}]),
              row('land','source',4,[])]], 'terrain_candidate':[[10,10,10]]}
v2['sha256']=digest(v2)
v3={'version':'extended-map-inland-mixed-exercise-v3','parent_v2_sha256':v2['sha256'],
    'grid':grid,'cells':[]};v3['sha256']=digest(v3)
poly=box(290600,4064100,290610,4064110)
features=[(1,'water_area','polygon',poly)]
metadata={1:{'version':1,'tags':{'natural':'water'}}}
rivers={(0,c):[{'id_dera':c,'source_epoch':'unverified'}] for c in range(3)}
m.exercise_neighborhoods=lambda *args:[]
result=m.compile_exercise_v4(v2,v3,[[True]*3],features,metadata,rivers,{'sha256':'catalog'}, {})
assert [(x['column'],x['exercise_basis']) for x in result['cells']]==[(1,'small_osm_water_area_2026'),(2,'additional_dera_watercourse')]
assert result['additional_dera_censors'][0]['column']==0
assert result['cells'][0]['fuel_factor_assumptions']=={'low':0.8,'nominal':0.9,'high':1.0}
assert result['cells'][0]['mapped_2026_water_area']['mapped_cell_fraction']==0.0004
'''
    result=subprocess.run([str(python),'-c',script],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_public_aggregate_omits_private_evaluator_audit():
    from extended_map_common import read_json
    evidence=read_json(ROOT/'data/extended_map_resolved_v1/evidence.json')
    for key in ('exercise_v3','exercise_v4'):
        section=evidence[key]
        assert 'private_document_sha256' not in section
        assert 'source_catalog_sha256' not in section
        def visit(value):
            if isinstance(value,dict):
                for k,v in value.items():
                    assert not k.startswith('cases') and k not in ('case_id','initial_fire_cells','stencil_cells')
                    visit(v)
            elif isinstance(value,list):
                for item in value: visit(item)
        visit(section)
