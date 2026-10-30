"""Independent source, topology and coverage checks for access observations."""
import copy
import importlib.util
from pathlib import Path

import pytest
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('extended_access_v1', ROOT / 'eval/extended_access_v1.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


@pytest.fixture
def snapshot():
    inverse = Transformer.from_crs(25830, 4326, always_xy=True)
    # Two crossing ways have distinct IDs at the crossing; they are not joined.
    pts = [(300100, 4040100), (300900, 4040100),
           (300500, 4039800), (300500, 4040400), (301100, 4040100)]
    nodes = []
    for i, (x, y) in enumerate(pts, 1):
        lon, lat = inverse.transform(x, y)
        nodes.append({'type': 'node', 'id': i, 'lon': lon, 'lat': lat, 'tags': {}})
    ways = [{'type': 'way', 'id': 11, 'nodes': [1, 2, 5], 'tags': {'highway': 'track', 'access': 'private'}},
            {'type': 'way', 'id': 12, 'nodes': [3, 4], 'tags': {'highway': 'footway'}}]
    return {'elements': nodes + ways}


def test_observations_do_not_infer_access_or_crossing_junction(snapshot):
    result = m.compile_observations(snapshot)
    assert len(result['segments']) == 3
    assert result['segments'][0]['nodes'] == [1, 2]
    assert result['segments'][1]['nodes'] == [2, 5]
    assert result['segments'][2]['nodes'] == [3, 4]
    assert result['segments'][0]['highway'] == 'track'
    assert result['segments'][2]['highway'] == 'footway'
    assert result['surface'] == 'unknown_for_every_cell'
    assert m.validate(result, snapshot)['segments'] == 3


@pytest.mark.parametrize('mutation,match', [
    ('drop', 'missing source segment'), ('fake', 'unbacked source segment'),
    ('node', 'topology'), ('move', 'geometry'), ('coverage', 'coverage'),
    ('surface', 'surface claim'), ('duplicate', 'duplicate segment'),
    ('reverse', 'direction'),
])
def test_independent_validator_rejects_mutations(snapshot, mutation, match):
    result = m.compile_observations(snapshot)
    if mutation == 'drop':
        result['segments'].pop()
    elif mutation == 'fake':
        item = copy.deepcopy(result['segments'][0]); item['way'] = 99
        result['segments'].append(item)
    elif mutation == 'node':
        result['segments'][0]['nodes'][1] = 3
    elif mutation == 'move':
        result['segments'][0]['coordinates'][0][0] += 10
    elif mutation == 'coverage':
        result['observed_cells'].pop()
    elif mutation == 'surface':
        result['surface'] = 'land'
    elif mutation == 'duplicate':
        result['segments'].append(copy.deepcopy(result['segments'][0]))
    elif mutation == 'reverse':
        result['segments'][0]['coordinates'].reverse()
    with pytest.raises(ValueError, match=match):
        m.validate(result, snapshot)


def test_missing_source_nodes_and_snapshot_hash(snapshot, tmp_path):
    snapshot['elements'][5]['nodes'].append(123456)
    with pytest.raises(ValueError, match='incomplete'):
        m.compile_observations(snapshot)
    bad = tmp_path / 'source.gz'
    bad.write_bytes(b'not the pinned source')
    with pytest.raises(ValueError, match='hash mismatch'):
        m.load_source(bad)


def test_clip_boundaries_and_grid_contact():
    assert m.clip((0, 0), (2, 0), (1, 0, 2, 1)) == [[1, 0], [2, 0]]
    assert m.clip((0, -1), (2, -1), (1, 0, 2, 1)) is None
    # A segment exactly on a grid line touches cells on both sides.
    a, b = (290500, 4064400), (290500, 4064300)
    touched = {rc for rc in m.cell_range(a, b) if m.clip(a, b, m.cell_bounds(*rc)) is not None}
    assert touched == {(0, 0), (0, 1)}


def test_clipped_segment_keeps_original_node_ids(snapshot):
    inverse = Transformer.from_crs(25830, 4326, always_xy=True)
    # Southern map border lies inside the source bbox.
    for ident, y in ((20, 4034800), (21, 4035200)):
        lon, lat = inverse.transform(300000, y)
        snapshot['elements'].append({'type': 'node', 'id': ident, 'lon': lon, 'lat': lat, 'tags': {}})
    snapshot['elements'].append({'type': 'way', 'id': 50, 'nodes': [20, 21], 'tags': {'highway': 'residential'}})
    result = m.compile_observations(snapshot)
    item = next(v for v in result['segments'] if v['way'] == 50)
    assert item['nodes'] == [20, 21]
    assert item['coordinates'][0][1] == pytest.approx(m.BOUNDS[1])
    m.validate(result, snapshot)


def test_corrected_extent_and_selection_footprint():
    assert m.BOUNDS == (290000, 4035000, 332000, 4064500)
    assert (m.WIDTH, m.HEIGHT, m.CELL) == (84, 59, 500)
    footprint = m.approximate_footprint()
    assert footprint['approx_cells_outside_source_bbox'] > 3000
    assert 0.31 < footprint['approx_source_bbox_area_fraction'] < 0.33


def test_acquisition_footprint_is_larger_but_still_incomplete():
    acquisition = m.approximate_footprint(m.ACQUISITION_BBOX)
    assert acquisition['approx_cells_outside_source_bbox'] == 1688
    assert 0.64 < acquisition['approx_source_bbox_area_fraction'] < 0.66
    assert acquisition['approx_cells_intersecting_source_bbox'] > m.approximate_footprint()['approx_cells_intersecting_source_bbox']


def test_sample_audit_checks_hash_nodes_and_open_coast(tmp_path, snapshot):
    import json
    sample = copy.deepcopy(snapshot)
    sample['elements'].append({'type': 'way', 'id': 90, 'nodes': [1, 2],
                               'tags': {'natural': 'coastline'}})
    raw = json.dumps(sample).encode()
    path = tmp_path / 'tile.json'
    path.write_bytes(raw)
    manifest = {'version': 'extended-access-osm-samples-v1',
                'status': 'partial_samples_not_complete_coverage',
                'tiles': [{'name': 'sample', 'path': str(path),
                           'bytes': len(raw), 'sha256': m.hash_bytes(raw)}]}
    report = m.audit_tiles(manifest)['tiles'][0]
    assert report['road_ways'] == 2
    assert report['coastline_ways_intersecting_map'] == 1
    assert report['coastline_endpoint_degree_one'] == 2
    path.write_bytes(raw + b' ')
    with pytest.raises(ValueError, match='hash or size'):
        m.audit_tiles(manifest)
    sample['elements'][-1]['nodes'].append(999)
    raw = json.dumps(sample).encode()
    path.write_bytes(raw)
    manifest['tiles'][0].update(bytes=len(raw), sha256=m.hash_bytes(raw))
    with pytest.raises(ValueError, match='incomplete source way'):
        m.audit_tiles(manifest)


def test_lattice_audit_detects_gaps_and_conflicting_node_ids(tmp_path, snapshot, monkeypatch):
    import json
    monkeypatch.setattr(m, 'LATTICE', (-5200000, 36480000, -5170000, 36510000))
    roots = m.lattice_roots()
    tile = roots[0]
    x0, y0, x1, y1 = tile['bounds_microdegrees']
    mid = (x0+x1)//2
    tile['status'] = 'split'
    tile['children'] = [tile['id'] + '-0', tile['id'] + '-1']
    children = []
    for i, bounds in enumerate(([x0,y0,mid,y1], [mid,y0,x1,y1])):
        data = copy.deepcopy(snapshot)
        if i == 1:
            data['elements'][0]['lat'] += 0.0001
        raw = json.dumps(data).encode()
        path = tmp_path / f'tile-{i}.json'
        path.write_bytes(raw)
        children.append({'id': tile['children'][i], 'bounds_microdegrees': bounds,
                         'status': 'success', 'path': str(path), 'sha256': m.hash_bytes(raw),
                         'bytes': len(raw), 'url': m.bbox_url(bounds)})
    manifest = {'version': 'extended-access-lattice-v1',
                'envelope_microdegrees': list(m.LATTICE), 'step_microdegrees': m.STEP,
                'status': 'incomplete_requests', 'tiles': [tile] + children}
    report = m.audit_lattice(manifest)
    assert report['leaf_statuses'] == {'success': 2}
    assert report['conflicting_node_ids'] == 1
    assert report['duplicate_node_occurrences'] == 5
    assert report['estimated_cells_outside_successful_boxes'] > 0
    broken = copy.deepcopy(manifest)
    broken['tiles'].pop()
    with pytest.raises(ValueError, match='missing or inconsistent'):
        m.audit_lattice(broken)
    children[0]['sha256'] = 'bad'
    with pytest.raises(ValueError, match='hash mismatch'):
        m.audit_lattice(manifest)


def test_lattice_acquisition_splits_and_reuses_cache(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(m, 'LATTICE', (-5200000, 36480000, -5170000, 36510000))
    monkeypatch.setattr(m, 'MAX_BYTES', 2_000_000)
    calls = []
    def fake_curl(command, **kwargs):
        calls.append(command[-1])
        output = Path(command[command.index('--output')+1])
        header = Path(command[command.index('--dump-header')+1])
        header.write_bytes(b'HTTP/2 200\r\n')
        if len(calls) == 1:
            output.write_bytes(b'too many nodes')
            return SimpleNamespace(returncode=0, stdout='400', stderr='')
        output.write_bytes(b'{"elements":[]}')
        return SimpleNamespace(returncode=0, stdout='200', stderr='')
    monkeypatch.setattr(m.subprocess, 'run', fake_curl)
    report = m.acquire_lattice(tmp_path)
    assert report['tile_statuses'] == {'split': 1, 'success': 2}
    assert report['response_header_bytes'] == 3 * len(b'HTTP/2 200\r\n')
    assert len(calls) == 3
    import stat
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in tmp_path.iterdir() if path.is_file())
    assert m.acquire_lattice(tmp_path)['status'] == 'complete_requests'
    assert len(calls) == 3


def test_lattice_samples_validate_url_and_identity(tmp_path, snapshot, monkeypatch):
    import json
    monkeypatch.setattr(m, 'LATTICE', (-5200000, 36480000, -5170000, 36510000))
    roots = m.lattice_roots()
    manifest = {'version': 'extended-access-lattice-v1', 'envelope_microdegrees': list(m.LATTICE),
                'step_microdegrees': m.STEP, 'status': 'incomplete_requests', 'tiles': roots}
    raw = json.dumps(snapshot).encode()
    path = tmp_path / 'sample.json'
    path.write_bytes(raw)
    bbox = [-5.2, 36.48, -5.17, 36.51]
    samples = {'version': 'extended-access-osm-samples-v1', 'status': 'partial_samples_not_complete_coverage',
               'tiles': [{'name': 'one', 'bbox_wgs84': bbox, 'path': str(path), 'bytes': len(raw),
                          'sha256': m.hash_bytes(raw), 'url': 'https://api.openstreetmap.org/api/0.6/map.json?bbox=-5.2,36.48,-5.17,36.51'}]}
    assert m.audit_lattice(manifest, samples)['additional_sample_tiles'] == 1
    samples['tiles'][0]['url'] = 'https://example.com/api/0.6/map.json?bbox=-5.2,36.48,-5.17,36.51'
    with pytest.raises(ValueError, match='URL or bbox'):
        m.audit_lattice(manifest, samples)


def test_lattice_byte_limit_counts_body_and_headers_with_reserve(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(m, 'LATTICE', (-5200000, 36480000, -5170000, 36510000))
    monkeypatch.setattr(m, 'MAX_BYTES', 1_000_100)
    def fake_curl(command, **kwargs):
        assert command[command.index('--max-filesize') + 1] == '100'
        Path(command[command.index('--output')+1]).write_bytes(b'x' * 100)
        Path(command[command.index('--dump-header')+1]).write_bytes(b'heads')
        return SimpleNamespace(returncode=63, stdout='200', stderr='')
    monkeypatch.setattr(m.subprocess, 'run', fake_curl)
    report = m.acquire_lattice(tmp_path)
    assert report['status'] == 'byte_limit'
    assert report['response_body_bytes'] == 100
    assert report['response_header_bytes'] == 5
    assert report['tile_statuses'] == {'pending': 1}
    assert not list(tmp_path.glob('*.partial'))


def test_resume_has_separate_response_and_time_budget(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(m, 'LATTICE', (-5200000, 36480000, -5170000, 36510000))
    monkeypatch.setattr(m, 'MAX_BYTES', 1_000_100)
    calls = []
    def fake_curl(command, **kwargs):
        calls.append(command)
        output = Path(command[command.index('--output') + 1])
        header = Path(command[command.index('--dump-header') + 1])
        header.write_bytes(b'heads')
        if len(calls) == 1:
            output.write_bytes(b'x'*100)
            return SimpleNamespace(returncode=63, stdout='200', stderr='')
        assert command[command.index('--max-filesize') + 1] == '100'
        output.write_bytes(b'{"elements":[]}')
        return SimpleNamespace(returncode=0, stdout='200', stderr='')
    monkeypatch.setattr(m.subprocess, 'run', fake_curl)
    assert m.acquire_lattice(tmp_path)['status'] == 'byte_limit'
    result = m.acquire_lattice(tmp_path, additional_bytes=1_000_100, additional_seconds=30)
    assert result['status'] == 'complete_requests'
    assert result['resumed_run']['response_body_bytes'] == len(b'{"elements":[]}')
    assert result['resumed_run']['response_header_bytes'] == 5
    with pytest.raises(ValueError, match='authorized bounds'):
        m.acquire_lattice(tmp_path, additional_bytes=100_000_001, additional_seconds=30)


def test_lattice_audit_flags_unavailable_restriction_members(tmp_path, snapshot, monkeypatch):
    import json
    monkeypatch.setattr(m, 'LATTICE', (-5200000, 36480000, -5170000, 36510000))
    tile = m.lattice_roots()[0]
    source = copy.deepcopy(snapshot)
    source['elements'].append({'type': 'relation', 'id': 123, 'version': 1,
        'tags': {'type': 'restriction', 'restriction': 'no_left_turn'},
        'members': [{'type': 'way', 'ref': 11, 'role': 'from'},
                    {'type': 'node', 'ref': 2, 'role': 'via'},
                    {'type': 'way', 'ref': 999, 'role': 'to'}]})
    raw = json.dumps(source).encode(); path = tmp_path / 'tile.json'; path.write_bytes(raw)
    tile.update(status='success', path=str(path), sha256=m.hash_bytes(raw), bytes=len(raw),
                url=m.bbox_url(tile['bounds_microdegrees']))
    manifest = {'version': 'extended-access-lattice-v1', 'envelope_microdegrees': list(m.LATTICE),
                'step_microdegrees': m.STEP, 'status': 'incomplete_requests', 'tiles': [tile]}
    report = m.audit_lattice(manifest)
    assert report['restriction_relation_ids'] == 1
    assert report['restriction_relations_with_missing_members'] == 1


def test_coastline_topology_traces_outside_endpoints_without_polygonizing():
    inv = Transformer.from_crs(25830,4326,always_xy=True)
    coords = [(289000,4040000), (300000,4040000), (333000,4040000)]
    nodes = {i:(1,*inv.transform(x,y)) for i,(x,y) in enumerate(coords,1)}
    ways = {7:(1,(1,2,3),None,'coastline')}
    result = m.coastline_topology(ways,nodes,set(),set())
    component = result['intersecting_components'][0]
    assert [e['position'] for e in component['endpoints']] == ['outside','outside']
    assert component['branch_or_isolated_nodes'] == 0
    assert component['direction_inconsistent_nodes'] == 0
    assert len(component['boundary_intersections']) == 2
    assert 'no proof' in result['interpretation']


def test_directed_road_candidate_keeps_source_ids_and_blocks_restrictions(snapshot):
    nodes, _ = m.index_source(snapshot)
    ways = {10:{'id':10,'version':1,'nodes':[1,2,5], 'tags':{'highway':'residential','oneway':'yes','name':'test'}},
            20:{'id':20,'version':1,'nodes':[3,4], 'tags':{'highway':'residential','oneway':'-1'}},
            30:{'id':30,'version':1,'nodes':[1,5], 'tags':{'highway':'residential'}},
            40:{'id':40,'version':1,'nodes':[1,3], 'tags':{'highway':'residential','access':'private'}}}
    restrictions={9:{'id':9,'version':1,'tags':{'type':'restriction','restriction':'no_left_turn'},
                     'members':[{'type':'way','ref':30,'role':'from'},
                                {'type':'node','ref':5,'role':'via'},
                                {'type':'way','ref':999,'role':'to'}]}}
    graph=m.compile_road_candidate(nodes,ways,restrictions,'fixture')
    assert {(a['way'],a['source'],a['target']) for a in graph['arcs']} == {(10,1,2),(20,4,3)}
    assert graph['restriction_relations'] == [restrictions[9]]
    assert graph['restriction_way_evidence'] == [ways[30]]
    assert graph['restriction_node_evidence'] == [nodes[5]]
    assert graph['ways'][0]['tags']['name'] == 'test'
    report=m.validate_road_candidate(graph,nodes,ways,restrictions,'fixture')
    assert report['directed_arcs'] == 2
    assert report['weak_components'] == 2
    assert report['largest_weak_component_nodes'] == 2
    bad=copy.deepcopy(graph); bad['arcs'][0]['target']=4
    with pytest.raises(ValueError, match='topology'):
        m.validate_road_candidate(bad,nodes,ways,restrictions,'fixture')
    bad=copy.deepcopy(graph); bad['ways'][0]['tags']['name']='altered'
    with pytest.raises(ValueError, match='tags'):
        m.validate_road_candidate(bad,nodes,ways,restrictions,'fixture')
    bad=copy.deepcopy(graph); bad['restriction_relations']=[]
    with pytest.raises(ValueError, match='restriction'):
        m.validate_road_candidate(bad,nodes,ways,restrictions,'fixture')


@pytest.mark.parametrize('junction', [1123524259, 915674901])
def test_outside_junction_cannot_join_inside_roads(junction):
    inv = Transformer.from_crs(25830,4326,always_xy=True)
    positions = {1:(291000,4040000), junction:(289000,4040500), 2:(291000,4041000)}
    nodes = {}
    for ident,(x,y) in positions.items():
        lon,lat=inv.transform(x,y)
        nodes[ident]={'id':ident,'lon':lon,'lat':lat,'tags':{}}
    ways = {10:{'id':10,'nodes':[1,junction],'tags':{'highway':'residential'}},
            11:{'id':11,'nodes':[junction,2],'tags':{'highway':'residential'}}}
    graph=m.compile_road_candidate(nodes,ways,{},'test')
    assert graph['arcs'] == []
    assert graph['nodes'] == []
    assert len(graph['boundary_evidence']) == 2
    assert junction in {n['id'] for n in graph['boundary_nodes']}
    assert m.validate_road_candidate(graph,nodes,ways,{},'test')['non_routable_boundary_segments'] == 2
    bad=copy.deepcopy(graph)
    bad['arcs'].append({'way':10,'segment':0,'direction':'f','source':1,'target':junction})
    with pytest.raises(ValueError,match='topology'):
        m.validate_road_candidate(bad,nodes,ways,{},'test')


def test_private_output_permissions(tmp_path):
    import stat
    dest=tmp_path/'private.json'
    dest.write_bytes(b'old'); dest.chmod(0o664)
    m.write_private_bytes(dest,b'new')
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    nested=tmp_path/'one'/'two'/'private'
    m.write_private_bytes(nested,b'private')
    assert stat.S_IMODE((tmp_path/'one').stat().st_mode) == 0o700
    assert stat.S_IMODE(nested.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(nested.stat().st_mode) == 0o600
    directory=tmp_path/'dedicated'
    directory.mkdir(mode=0o775)
    saved=m.lattice_roots
    try:
        m.lattice_roots=lambda: []
        m.acquire_lattice(directory)
    finally:
        m.lattice_roots=saved
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory/'manifest.json').stat().st_mode) == 0o600


def test_coastline_face_diagnostic_respects_left_right_and_rejects_crossings():
    diagnostic=m.coastline_face_diagnostic([(-1,5),(11,5)],(0,0,10,10))
    assert diagnostic['boundary_crossings'] == 2
    assert diagnostic['oriented_segments_tested'] == 1
    sides={f['side']:f['representative_m'][1] for f in diagnostic['faces']}
    assert sides['left'] > 5 and sides['right'] < 5
    reverse=m.coastline_face_diagnostic([(11,5),(-1,5)],(0,0,10,10))
    sides={f['side']:f['representative_m'][1] for f in reverse['faces']}
    assert sides['left'] < 5 and sides['right'] > 5
    with pytest.raises(ValueError,match='self-intersecting'):
        m.coastline_face_diagnostic([(-1,2),(8,8),(2,8),(11,2)],(0,0,10,10))
    with pytest.raises(ValueError,match='endpoint'):
        m.coastline_face_diagnostic([(5,5),(11,5)],(0,0,10,10))


def test_coastal_surface_assessment_is_fail_closed():
    inv=Transformer.from_crs(25830,4326,always_xy=True)
    nodes={}
    for ident,(x,y) in {1:(289000,4040000),2:(300000,4040000),3:(333000,4040000)}.items():
        lon,lat=inv.transform(x,y)
        nodes[ident]={'id':ident,'lon':lon,'lat':lat}
    ways={8:{'id':8,'nodes':[1,2,3], 'tags':{'natural':'coastline'}}}
    result=m.compile_surface_assessment(nodes,ways,'fixture')
    assert result['status'] == 'all_unknown_source_completeness_unverified'
    assert m.validate_surface_assessment(result,nodes,ways,'fixture')['classified_cells'] == 0
    bad=copy.deepcopy(result); bad['surface'][0][0]='sea'
    with pytest.raises(ValueError,match='fail-closed'):
        m.validate_surface_assessment(bad,nodes,ways,'fixture')


def test_surface_assessment_fails_closed_on_disconnected_coastline():
    nodes={1:{'id':1,'lon':-5.2,'lat':36.5},2:{'id':2,'lon':-5.1,'lat':36.5},
           3:{'id':3,'lon':-5.0,'lat':36.5},4:{'id':4,'lon':-4.9,'lat':36.5}}
    ways={1:{'id':1,'nodes':[1,2],'tags':{'natural':'coastline'}},
          2:{'id':2,'nodes':[3,4],'tags':{'natural':'coastline'}}}
    result=m.compile_surface_assessment(nodes,ways,'fixture')
    assert result['diagnostic']['status'] == 'failed'
    assert all(cell=='unknown' for row in result['surface'] for cell in row)
    assert m.validate_surface_assessment(result,nodes,ways,'fixture')['classified_cells'] == 0


@pytest.mark.skipif(not Path('/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz').is_file(),
                    reason='private compiled candidate not available')
def test_real_reported_outside_junctions_never_enter_routing_graph():
    import gzip
    import json
    p=Path('/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz')
    with gzip.open(p,'rt') as f:
        graph=json.load(f)
    problematic={1123524259,915674901}
    assert not (problematic & {n['id'] for n in graph['nodes']})
    assert not any(e['source'] in problematic or e['target'] in problematic for e in graph['arcs'])
    assert problematic <= {n['id'] for n in graph['boundary_nodes']}


def test_candidate_json_rejects_duplicate_keys_and_nonfinite_values():
    with pytest.raises(ValueError,match='duplicate JSON key'):
        m.strict_json(b'{"surface":"sea","surface":"unknown"}')
    with pytest.raises(ValueError,match='invalid JSON number'):
        m.strict_json(b'{"x":NaN}')


def test_private_output_rejects_symlink_without_touching_target(tmp_path):
    secret=tmp_path/'secret'
    secret.write_bytes(b'keep this content')
    link=tmp_path/'output'
    link.symlink_to(secret)
    with pytest.raises(ValueError,match='regular file.*symlink'):
        m.write_private_bytes(link,b'overwrite')
    assert secret.read_bytes() == b'keep this content'
    assert link.is_symlink()
    assert not list(tmp_path.glob('.output.*.tmp'))


def test_private_output_atomically_replaces_hardlink_without_truncation(tmp_path):
    import os
    original=tmp_path/'original'
    original.write_bytes(b'untouched')
    output=tmp_path/'output'
    os.link(original,output)
    m.write_private_bytes(output,b'new file')
    assert original.read_bytes() == b'untouched'
    assert output.read_bytes() == b'new file'


def test_coastline_tangent_boundary_contact_is_not_a_crossing():
    tangent=[(-1,2),(5,2),(10,5),(5,8),(-1,8)]
    with pytest.raises(ValueError,match='tangent or ambiguous'):
        m.coastline_face_diagnostic(tangent,(0,0,10,10))


def test_private_output_rejects_symlinked_parent(tmp_path):
    actual=tmp_path/'actual'; actual.mkdir()
    parent_link=tmp_path/'link'; parent_link.symlink_to(actual,target_is_directory=True)
    with pytest.raises(OSError):
        m.write_private_bytes(parent_link/'output',b'private')
    assert not (actual/'output').exists()


def test_tangent_assessment_stays_unknown(monkeypatch):
    points=[(-1,2),(5,2),(10,5),(5,8),(-1,8)]
    def tangent_diagnostic(nodes,ways):
        return m.coastline_face_diagnostic(points,(0,0,10,10))
    monkeypatch.setattr(m,'source_coastline_diagnostic',tangent_diagnostic)
    result=m.compile_surface_assessment({}, {}, 'fixture')
    assert result['diagnostic']['status']=='failed'
    assert 'tangent or ambiguous' in result['diagnostic']['reason']
    assert all(cell=='unknown' for row in result['surface'] for cell in row)


def test_private_output_rejects_nonregular_target(tmp_path):
    target=tmp_path/'directory'; target.mkdir()
    with pytest.raises(ValueError,match='regular file'):
        m.write_private_bytes(target,b'data')
    assert target.is_dir()


def test_private_output_does_not_remove_preexisting_temp_collision(tmp_path, monkeypatch):
    target=tmp_path/'output'; collision=tmp_path/'.output.fixed.tmp'
    collision.write_bytes(b'unchanged')
    monkeypatch.setattr(m.secrets,'token_hex',lambda _: 'fixed')
    with pytest.raises(FileExistsError):
        m.write_private_bytes(target,b'data')
    assert collision.read_bytes() == b'unchanged'
    assert not target.exists()


def test_acquisition_ignores_predictable_partial_symlink(tmp_path, monkeypatch):
    from types import SimpleNamespace
    victim=tmp_path/'victim'; victim.write_bytes(b'keep')
    legacy=tmp_path/'tile.partial'; legacy.symlink_to(victim)
    def fake_curl(command, **kwargs):
        body=command[command.index('--output')+1]
        header=command[command.index('--dump-header')+1]
        assert body.startswith('/proc/self/fd/') and header.startswith('/proc/self/fd/')
        assert len(kwargs['pass_fds']) == 2
        Path(body).write_bytes(b'{"elements":[]}')
        Path(header).write_bytes(b'HTTP/2 200\r\n')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    result,raw,_,valid,target,_=m.fetch_lattice_tile(tmp_path,{'id':'tile','attempts':[]},
                                                     'https://api.openstreetmap.org/api/0.6/map.json?bbox=0,0,1,1',1000,10)
    assert valid and raw==b'{"elements":[]}' and Path(target).read_bytes()==raw
    assert victim.read_bytes()==b'keep' and legacy.is_symlink()


@pytest.mark.parametrize('kind',['symlink','hardlink'])
def test_acquisition_rejects_existing_cache_link_before_curl(tmp_path, monkeypatch,kind):
    import os
    victim=tmp_path/'victim'; victim.write_bytes(b'untouched')
    output=tmp_path/'tile.fixed.json'
    if kind=='symlink':
        output.symlink_to(victim)
    else:
        os.link(victim,output)
    def forbidden(*args,**kwargs):
        raise AssertionError('curl must not be called for an existing cache entry')
    monkeypatch.setattr(m.subprocess,'run',forbidden)
    with pytest.raises(FileExistsError,match='cache entry'):
        m.fetch_lattice_tile(tmp_path,{'id':'tile','attempts':[]},'unused',1000,10,attempt_id='fixed')
    assert victim.read_bytes()==b'untouched'
    assert output.is_symlink() if kind=='symlink' else output.read_bytes()==b'untouched'


@pytest.mark.parametrize('kind',['symlink','hardlink'])
def test_download_temp_rejects_preexisting_link_collision(tmp_path,monkeypatch,kind):
    import os
    victim=tmp_path/'victim'; victim.write_bytes(b'keep')
    collision=tmp_path/'.tile.body.fixed.tmp'
    if kind=='symlink':
        collision.symlink_to(victim)
    else:
        os.link(victim,collision)
    monkeypatch.setattr(m.secrets,'token_hex',lambda _:'fixed')
    with pytest.raises(FileExistsError):
        m.PrivateDownloadTemp(tmp_path,'tile.body')
    assert victim.read_bytes()==b'keep'
    assert collision.is_symlink() if kind=='symlink' else collision.read_bytes()==b'keep'


def test_acquisition_rejects_temp_path_replacement(tmp_path,monkeypatch):
    import os
    from types import SimpleNamespace
    victim=tmp_path/'victim'; victim.write_bytes(b'keep')
    replaced=[]
    def fake_curl(command, **kwargs):
        body=command[command.index('--output')+1]
        header=command[command.index('--dump-header')+1]
        temp=Path(os.readlink(body)); temp.unlink(); temp.symlink_to(victim)
        replaced.append(temp)
        Path(body).write_bytes(b'{"elements":[]}')
        Path(header).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    with pytest.raises(ValueError,match='identity or link count'):
        m.fetch_lattice_tile(tmp_path,{'id':'tile','attempts':[]},'unused',1000,10)
    assert victim.read_bytes()==b'keep'
    assert replaced[0].is_symlink()
    assert not (tmp_path/'tile.json').exists()


def test_acquisition_rejects_temp_hardlink_created_after_open(tmp_path,monkeypatch):
    import os
    from types import SimpleNamespace
    def fake_curl(command, **kwargs):
        body=command[command.index('--output')+1]
        header=command[command.index('--dump-header')+1]
        os.link(os.readlink(body),tmp_path/'new-hardlink')
        Path(body).write_bytes(b'{"elements":[]}')
        Path(header).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    with pytest.raises(ValueError,match='identity or link count'):
        m.fetch_lattice_tile(tmp_path,{'id':'tile','attempts':[]},'unused',1000,10)
    assert not (tmp_path/'tile.json').exists()


def test_real_curl_uses_inherited_private_descriptors_without_network(tmp_path):
    import shutil
    if shutil.which('curl') is None:
        pytest.skip('curl unavailable')
    source=tmp_path/'source'
    source.write_bytes(b'{"elements":[]}')
    result,raw,_,valid,target,header=m.fetch_lattice_tile(
        tmp_path,{'id':'tile','attempts':[]},source.as_uri(),1000,10)
    assert result.returncode == 0
    assert raw == b'{"elements":[]}'
    assert result.stdout == '000' and not valid
    assert not Path(target).exists()
    assert Path(header).is_file()


def test_acquisition_rejects_existing_header_symlink_before_curl(tmp_path,monkeypatch):
    victim=tmp_path/'victim'; victim.write_bytes(b'untouched')
    collision=tmp_path/'tile.fixed.headers'; collision.symlink_to(victim)
    def forbidden(*args,**kwargs):
        raise AssertionError('curl must not run on header collision')
    monkeypatch.setattr(m.subprocess,'run',forbidden)
    with pytest.raises(FileExistsError,match='cache entry'):
        m.fetch_lattice_tile(tmp_path,{'id':'tile','attempts':[]},'unused',1000,10,attempt_id='fixed')
    assert collision.is_symlink() and victim.read_bytes()==b'untouched'


def test_acquisition_rejects_path_traversal_in_cache_id(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('curl must not run')
    monkeypatch.setattr(m.subprocess,'run',forbidden)
    with pytest.raises(ValueError,match='single filename'):
        m.fetch_lattice_tile(tmp_path,{'id':'../outside','attempts':[]},'unused',1000,10)


def test_acquisition_rejects_symlink_directory_without_chmod(tmp_path):
    import stat
    target=tmp_path/'actual';target.mkdir(mode=0o755); target.chmod(0o755)
    link=tmp_path/'link';link.symlink_to(target,target_is_directory=True)
    with pytest.raises(OSError):
        m.acquire_lattice(link)
    assert stat.S_IMODE(target.stat().st_mode)==0o755


@pytest.mark.parametrize('kind',['symlink','hardlink'])
def test_acquisition_rejects_cache_collision_created_during_curl(tmp_path,monkeypatch,kind):
    import os
    from types import SimpleNamespace
    victim=tmp_path/'victim'; victim.write_bytes(b'keep')
    target=tmp_path/'tile.fixed.json'
    def fake_curl(command, **kwargs):
        if kind=='symlink':
            target.symlink_to(victim)
        else:
            os.link(victim,target)
        Path(command[command.index('--output')+1]).write_bytes(b'{"elements":[]}')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    with pytest.raises(FileExistsError):
        m.fetch_lattice_tile(tmp_path,{'id':'tile','attempts':[]},'unused',1000,10,attempt_id='fixed')
    assert victim.read_bytes()==b'keep'
    assert target.is_symlink() if kind=='symlink' else target.read_bytes()==b'keep'


@pytest.mark.parametrize('crash_at', ['before_header','after_header','after_body',
                                     'before_manifest_commit','after_manifest_commit'])
def test_acquisition_crash_boundaries_recover_without_collision(tmp_path,monkeypatch,crash_at):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(m,'LATTICE',(-5200000,36480000,-5170000,36510000))
    calls=[]
    def fake_curl(command, **kwargs):
        calls.append(command)
        Path(command[command.index('--output')+1]).write_bytes(b'{"elements":[]}')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    original_publish=m.PrivateDownloadTemp.publish
    original_save=m.save_manifest
    fired=[]
    def publish(self,destination):
        is_header=destination.endswith('.headers')
        if not fired and crash_at=='before_header' and is_header:
            fired.append(True); raise RuntimeError('injected crash')
        original_publish(self,destination)
        if not fired and ((crash_at=='after_header' and is_header) or
                          (crash_at=='after_body' and destination.endswith('.json'))):
            fired.append(True); raise RuntimeError('injected crash')
    def save(path,manifest):
        commit=(not manifest.get('inflight') and manifest['tiles'][0]['status']=='success')
        if not fired and commit and crash_at=='before_manifest_commit':
            fired.append(True); raise RuntimeError('injected crash')
        original_save(path,manifest)
        if not fired and commit and crash_at=='after_manifest_commit':
            fired.append(True); raise RuntimeError('injected crash')
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',publish)
    monkeypatch.setattr(m,'save_manifest',save)
    with pytest.raises(RuntimeError,match='injected crash'):
        m.acquire_lattice(tmp_path)
    assert fired and len(calls)==1
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',original_publish)
    monkeypatch.setattr(m,'save_manifest',original_save)
    result=m.acquire_lattice(tmp_path)
    assert result['status']=='complete_requests'
    data=json.loads((tmp_path/'manifest.json').read_text())
    assert 'inflight' not in data
    attempts=data['tiles'][0]['attempts']
    expected_calls=2 if crash_at in ('before_header','after_header') else 1
    assert len(calls)==expected_calls
    assert data['bytes_transferred']==expected_calls*len(b'{"elements":[]}')
    assert data['headers_bytes_transferred']==expected_calls*len(b'headers')
    assert len({a['attempt_id'] for a in attempts}) == len(attempts)
    assert len(attempts)==expected_calls
    assert m.hash_bytes(Path(data['tiles'][0]['path']).read_bytes())==data['tiles'][0]['sha256']
    # Repeating recovery never charges the same attempt twice.
    m.acquire_lattice(tmp_path)
    again=json.loads((tmp_path/'manifest.json').read_text())
    assert again['bytes_transferred']==data['bytes_transferred']
    assert len(calls)==expected_calls


def test_interrupted_request_without_receipt_charges_reserved_budget(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(m,'LATTICE',(-5200000,36480000,-5170000,36510000))
    original_save=m.save_manifest
    calls=[]
    def fake_curl(command, **kwargs):
        calls.append(command)
        Path(command[command.index('--output')+1]).write_bytes(b'{"elements":[]}')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    def fail_receipt(path,manifest):
        if manifest.get('inflight',{}).get('receipt') is not None:
            raise RuntimeError('crash before receipt commit')
        original_save(path,manifest)
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    monkeypatch.setattr(m,'save_manifest',fail_receipt)
    with pytest.raises(RuntimeError,match='before receipt'):
        m.acquire_lattice(tmp_path)
    monkeypatch.setattr(m,'save_manifest',original_save)
    result=m.acquire_lattice(tmp_path)
    assert result['status']=='byte_limit' and len(calls)==1
    data=json.loads((tmp_path/'manifest.json').read_text())
    assert data['bytes_transferred']==m.MAX_BYTES-m.PROTOCOL_RESERVE
    assert data['headers_bytes_transferred']==m.PROTOCOL_RESERVE
    assert data['tiles'][0]['attempts'][0]['accounting']=='conservative_upper_bound_after_interruption'
    # An explicitly authorized new allowance permits a fresh attempt.
    result=m.acquire_lattice(tmp_path,additional_bytes=1_000_100,additional_seconds=30)
    assert result['status']=='complete_requests' and len(calls)==2


@pytest.mark.parametrize('crash_at',['before_recovery_save','after_recovery_save'])
def test_recovery_itself_is_idempotent_across_manifest_crashes(tmp_path,monkeypatch,crash_at):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(m,'LATTICE',(-5200000,36480000,-5170000,36510000))
    calls=[]
    def fake_curl(command,**kwargs):
        calls.append(command)
        Path(command[command.index('--output')+1]).write_bytes(b'{"elements":[]}')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    original_publish=m.PrivateDownloadTemp.publish
    def crash_after_body(self,destination):
        original_publish(self,destination)
        if destination.endswith('.json'):
            raise RuntimeError('crash after body publish')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',crash_after_body)
    with pytest.raises(RuntimeError,match='after body'):
        m.acquire_lattice(tmp_path)
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',original_publish)
    original_save=m.save_manifest
    def crash_recovery(path,manifest):
        recovered=(not manifest.get('inflight') and manifest['tiles'][0]['status']=='success')
        if recovered and crash_at=='before_recovery_save':
            raise RuntimeError('recovery crash')
        original_save(path,manifest)
        if recovered and crash_at=='after_recovery_save':
            raise RuntimeError('recovery crash')
    monkeypatch.setattr(m,'save_manifest',crash_recovery)
    with pytest.raises(RuntimeError,match='recovery crash'):
        m.acquire_lattice(tmp_path)
    monkeypatch.setattr(m,'save_manifest',original_save)
    assert m.acquire_lattice(tmp_path)['status']=='complete_requests'
    data=json.loads((tmp_path/'manifest.json').read_text())
    assert len(calls)==1
    assert len(data['tiles'][0]['attempts'])==1
    assert data['bytes_transferred']==len(b'{"elements":[]}')
    assert data['headers_bytes_transferred']==len(b'headers')


def test_recovery_rejects_corrupt_published_body_without_retry(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(m,'LATTICE',(-5200000,36480000,-5170000,36510000))
    calls=[]
    def fake_curl(command,**kwargs):
        calls.append(command)
        Path(command[command.index('--output')+1]).write_bytes(b'{"elements":[]}')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    original=m.PrivateDownloadTemp.publish
    def crash(self,destination):
        original(self,destination)
        if destination.endswith('.json'):
            raise RuntimeError('crash')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',crash)
    with pytest.raises(RuntimeError,match='crash'):
        m.acquire_lattice(tmp_path)
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',original)
    pending=json.loads((tmp_path/'manifest.json').read_text())['inflight']
    body=tmp_path/f"{pending['tile_id']}.{pending['attempt_id']}.json"
    body.write_bytes(b'{"elements":[1]}')
    with pytest.raises(ValueError,match='published cache content mismatch'):
        m.acquire_lattice(tmp_path)
    assert len(calls)==1


def test_recovery_rejects_published_header_symlink_without_following(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(m,'LATTICE',(-5200000,36480000,-5170000,36510000))
    calls=[]
    def fake_curl(command,**kwargs):
        calls.append(command)
        Path(command[command.index('--output')+1]).write_bytes(b'{"elements":[]}')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    original=m.PrivateDownloadTemp.publish
    def crash(self,destination):
        original(self,destination)
        if destination.endswith('.headers'):
            raise RuntimeError('crash')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',crash)
    with pytest.raises(RuntimeError,match='crash'):
        m.acquire_lattice(tmp_path)
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',original)
    pending=json.loads((tmp_path/'manifest.json').read_text())['inflight']
    header=tmp_path/f"{pending['tile_id']}.{pending['attempt_id']}.headers"
    victim=tmp_path/'victim';victim.write_bytes(b'keep')
    header.unlink();header.symlink_to(victim)
    with pytest.raises(OSError):
        m.acquire_lattice(tmp_path)
    assert victim.read_bytes()==b'keep' and len(calls)==1


@pytest.mark.parametrize('legacy_kind',['header','body','both'])
def test_legacy_orphan_requires_fresh_allowance_and_is_not_overwritten(tmp_path,monkeypatch,legacy_kind):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(m,'LATTICE',(-5200000,36480000,-5170000,36510000))
    tile=m.lattice_roots()[0]
    manifest={'version':'extended-access-lattice-v1','envelope_microdegrees':list(m.LATTICE),
              'step_microdegrees':m.STEP,'byte_limit':m.MAX_BYTES,'time_limit_seconds':m.MAX_SECONDS,
              'bytes_transferred':0,'headers_bytes_transferred':0,'elapsed_seconds':0,
              'status':'in_progress','tiles':[tile]}
    m.save_manifest(tmp_path/'manifest.json',manifest)
    orphans=[]
    if legacy_kind in ('header','both'):
        orphans.append(tmp_path/f"{tile['id']}.attempt-001.headers")
    if legacy_kind in ('body','both'):
        orphans.append(tmp_path/f"{tile['id']}.json")
    for orphan in orphans:
        orphan.write_bytes(b'untrusted old data')
    calls=[]
    def fake_curl(command,**kwargs):
        calls.append(command)
        Path(command[command.index('--output')+1]).write_bytes(b'{"elements":[]}')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    result=m.acquire_lattice(tmp_path)
    assert result['status']=='byte_limit' and not calls
    saved=json.loads((tmp_path/'manifest.json').read_text())
    assert saved['bytes_transferred']==m.MAX_BYTES
    assert saved['tiles'][0]['attempts'][0]['accounting']=='conservative_combined_upper_bound_for_legacy_orphan'
    result=m.acquire_lattice(tmp_path,additional_bytes=1_000_100,additional_seconds=30)
    assert result['status']=='complete_requests' and len(calls)==1
    assert all(orphan.read_bytes()==b'untrusted old data' for orphan in orphans)


@pytest.mark.parametrize('crash_at',['headers','json'])
def test_abrupt_process_exit_recovers_published_boundary(tmp_path,monkeypatch,crash_at):
    import subprocess
    import sys
    from types import SimpleNamespace
    monkeypatch.setattr(m,'LATTICE',(-5200000,36480000,-5170000,36510000))
    script=f'''
import importlib.util, os
from pathlib import Path
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('child', {str(ROOT/'eval/extended_access_v1.py')!r})
x=importlib.util.module_from_spec(spec); spec.loader.exec_module(x)
x.LATTICE=(-5200000,36480000,-5170000,36510000)
def fake(command, **kwargs):
    Path(command[command.index('--output')+1]).write_bytes(b'{{"elements":[]}}')
    Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
    return SimpleNamespace(returncode=0,stdout='200',stderr='')
x.subprocess.run=fake
original=x.PrivateDownloadTemp.publish
def crash(self,destination):
    original(self,destination)
    if destination.endswith({('.'+crash_at)!r}): os._exit(77)
x.PrivateDownloadTemp.publish=crash
x.acquire_lattice(Path({str(tmp_path)!r}))
'''
    child=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,check=False)
    assert child.returncode==77,child.stderr
    calls=[]
    def fake_curl(command,**kwargs):
        calls.append(command)
        Path(command[command.index('--output')+1]).write_bytes(b'{"elements":[]}')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    assert m.acquire_lattice(tmp_path)['status']=='complete_requests'
    assert len(calls)==(1 if crash_at=='headers' else 0)
    if crash_at=='headers':
        assert list(tmp_path.glob('.*.tmp'))  # Abrupt exit left a private orphan; no overwrite needed.


@pytest.mark.parametrize('legacy_receipt',[False,True])
@pytest.mark.parametrize('crash_at',['headers','json'])
def test_process_exit_between_link_and_unlink_is_reconciled(tmp_path,monkeypatch,crash_at,legacy_receipt):
    import json
    import subprocess
    import sys
    from types import SimpleNamespace
    monkeypatch.setattr(m,'LATTICE',(-5200000,36480000,-5170000,36510000))
    script=f'''
import importlib.util, os
from pathlib import Path
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('child',{str(ROOT/'eval/extended_access_v1.py')!r})
x=importlib.util.module_from_spec(spec);spec.loader.exec_module(x)
x.LATTICE=(-5200000,36480000,-5170000,36510000)
def fake(command,**kwargs):
    Path(command[command.index('--output')+1]).write_bytes(b'{{"elements":[]}}')
    Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
    return SimpleNamespace(returncode=0,stdout='200',stderr='')
x.subprocess.run=fake
link=os.link
def crash(source,destination,*args,**kwargs):
    link(source,destination,*args,**kwargs)
    if destination.endswith({('.'+crash_at)!r}): os._exit(77)
os.link=crash
x.acquire_lattice(Path({str(tmp_path)!r}))
'''
    child=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,check=False)
    assert child.returncode==77,child.stderr
    manifest=json.loads((tmp_path/'manifest.json').read_text())
    journal=manifest['inflight']
    kind='header' if crash_at=='headers' else 'body'
    temp=tmp_path/journal['receipt'][kind+'_temp']['name']
    published=tmp_path/f"{journal['tile_id']}.{journal['attempt_id']}.{crash_at}"
    assert temp.stat().st_ino==published.stat().st_ino
    assert temp.stat().st_nlink==published.stat().st_nlink==2
    if legacy_receipt:
        del journal['receipt'][kind+'_temp']
        m.save_manifest(tmp_path/'manifest.json',manifest)
    calls=[]
    def fake_curl(command,**kwargs):
        calls.append(command)
        Path(command[command.index('--output')+1]).write_bytes(b'{"elements":[]}')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='200',stderr='')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    assert m.acquire_lattice(tmp_path)['status']=='complete_requests'
    assert len(calls)==(1 if crash_at=='headers' else 0)
    assert published.stat().st_nlink==1
    assert not temp.exists()
    saved=json.loads((tmp_path/'manifest.json').read_text())
    assert saved['bytes_transferred']==(2 if crash_at=='headers' else 1)*len(b'{"elements":[]}')
    assert saved['headers_bytes_transferred']==(2 if crash_at=='headers' else 1)*len(b'headers')


def test_recovered_third_http_failure_respects_retry_limit(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(m,'LATTICE',(-5200000,36480000,-5170000,36510000))
    calls=[]
    def fake_curl(command,**kwargs):
        calls.append(command)
        Path(command[command.index('--output')+1]).write_bytes(b'unavailable')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0,stdout='503',stderr='')
    original=m.PrivateDownloadTemp.publish
    def crash_after_third_header(self,destination):
        original(self,destination)
        if len(calls)==3 and destination.endswith('.headers'):
            raise RuntimeError('crash after third failure')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',crash_after_third_header)
    with pytest.raises(RuntimeError,match='third failure'):
        m.acquire_lattice(tmp_path)
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',original)
    result=m.acquire_lattice(tmp_path)
    saved=json.loads((tmp_path/'manifest.json').read_text())
    assert result['status']=='incomplete_requests'
    assert saved['tiles'][0]['status']=='failed_request'
    assert len(saved['tiles'][0]['attempts'])==3
    assert saved['bytes_transferred']==3*len(b'unavailable')
    assert len(calls)==3
    m.acquire_lattice(tmp_path)
    assert len(calls)==3


def test_recovery_refuses_unexpected_third_hardlink(tmp_path):
    import os
    temp=tmp_path/'.tile.header.fixed.tmp';temp.write_bytes(b'headers')
    destination=tmp_path/'tile.attempt.headers'; extra=tmp_path/'external-link'
    os.link(temp,destination);os.link(temp,extra)
    info=temp.stat();journal={'name':temp.name,'device':info.st_dev,'inode':info.st_ino}
    with pytest.raises(ValueError,match='link count'):
        m.read_recoverable_published(destination,journal,'header','tile',7,m.hash_bytes(b'headers'))
    assert temp.read_bytes()==destination.read_bytes()==extra.read_bytes()==b'headers'
    assert temp.stat().st_nlink==3


def test_recovery_refuses_symlink_as_journaled_temporary_link(tmp_path):
    import os
    destination=tmp_path/'tile.attempt.headers';destination.write_bytes(b'headers')
    extra=tmp_path/'external-link';os.link(destination,extra)
    temp=tmp_path/'.tile.header.fixed.tmp';temp.symlink_to(destination)
    info=destination.stat();journal={'name':temp.name,'device':info.st_dev,'inode':info.st_ino}
    with pytest.raises(ValueError,match='temporary link'):
        m.read_recoverable_published(destination,journal,'header','tile',7,m.hash_bytes(b'headers'))
    assert temp.is_symlink() and destination.read_bytes()==extra.read_bytes()==b'headers'


def test_recovery_verifies_content_before_removing_journaled_link(tmp_path):
    import os
    temp=tmp_path/'.tile.body.fixed.tmp';temp.write_bytes(b'altered')
    destination=tmp_path/'tile.attempt.json';os.link(temp,destination)
    info=temp.stat();journal={'name':temp.name,'device':info.st_dev,'inode':info.st_ino}
    with pytest.raises(ValueError,match='content mismatch'):
        m.read_recoverable_published(destination,journal,'body','tile',8,m.hash_bytes(b'expected'))
    assert temp.exists() and destination.exists() and temp.stat().st_nlink==2


def test_recovery_rejects_fifo_without_blocking(tmp_path):
    import os
    path=tmp_path/'tile.attempt.headers';os.mkfifo(path)
    with pytest.raises(ValueError,match='file type or link count'):
        m.read_recoverable_published(path,None,'header','tile',0,m.hash_bytes(b''))


def test_recovered_third_budget_stop_does_not_become_retry_failure(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(m,'LATTICE',(-5200000,36480000,-5170000,36510000))
    calls=[]
    def fake_curl(command,**kwargs):
        calls.append(command)
        Path(command[command.index('--output')+1]).write_bytes(b'unavailable' if len(calls)<3 else b'')
        Path(command[command.index('--dump-header')+1]).write_bytes(b'headers')
        return SimpleNamespace(returncode=0 if len(calls)<3 else 63,
                               stdout='503' if len(calls)<3 else '200',stderr='')
    original=m.PrivateDownloadTemp.publish
    def crash_after_third_header(self,destination):
        original(self,destination)
        if len(calls)==3 and destination.endswith('.headers'):
            raise RuntimeError('crash after budget stop')
    monkeypatch.setattr(m.subprocess,'run',fake_curl)
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',crash_after_third_header)
    with pytest.raises(RuntimeError,match='budget stop'):
        m.acquire_lattice(tmp_path)
    monkeypatch.setattr(m.PrivateDownloadTemp,'publish',original)
    result=m.acquire_lattice(tmp_path)
    saved=json.loads((tmp_path/'manifest.json').read_text())
    assert result['status']=='byte_limit'
    assert saved['tiles'][0]['status']=='pending'
    assert len(saved['tiles'][0]['attempts'])==3 and len(calls)==3


def test_recovery_reconciles_older_receipt_with_unique_verified_temp_link(tmp_path):
    import os
    temp=tmp_path/'.tile.header.fixed.tmp';temp.write_bytes(b'headers')
    destination=tmp_path/'tile.attempt.headers';os.link(temp,destination)
    assert m.read_recoverable_published(destination,None,'header','tile',7,m.hash_bytes(b'headers'))==b'headers'
    assert not temp.exists() and destination.stat().st_nlink==1


def test_recovery_rejects_older_receipt_without_matching_temp_link(tmp_path):
    import os
    destination=tmp_path/'tile.attempt.headers';destination.write_bytes(b'headers')
    extra=tmp_path/'unexpected';os.link(destination,extra)
    with pytest.raises(ValueError,match='uniquely reconcile'):
        m.read_recoverable_published(destination,None,'header','tile',7,m.hash_bytes(b'headers'))
    assert destination.exists() and extra.exists() and destination.stat().st_nlink==2
