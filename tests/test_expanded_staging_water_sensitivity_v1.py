"""The sensitivity never authorizes routes or changes a mapped crossing."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.strtree import STRtree
from eval import expanded_staging_water_sensitivity_v1 as sensitivity
from envs.wildfire_response.regional.ops.generator import digest


class Cover:
    def __init__(self):self.bounds=[]
    def sample(self,corridor):
        self.bounds.append(corridor.bounds)
        return {10:1,50:1}


def fixture():
    a=[290750.,4062250.];b=[292250.,4062250.]
    edge={'coordinates':[a,b],'allowed_modes':['engine','crew']}
    way={'tags':{'highway':'residential'}};nodes=[{'tags':{}},{'tags':{}}]
    far=LineString([(300000,4050000),(300100,4050000)])
    near=LineString([(290800,4062280),(292000,4062280)])
    water={'osm':STRtree([near]),'dera':STRtree([far]),
           'bounds':box(290000,4035000,332000,4064500),
           'land':box(290000,4035000,332000,4064500),'shore':far}
    return edge,way,nodes,water,{(x,y) for x in range(84) for y in range(59)}


def test_water_proximity_only_changes_proximity_filter():
    edge,way,nodes,water,supported=fixture();cover=Cover()
    out={b:sensitivity.screen_at(b,edge,way,nodes,water,cover,supported,set()) for b in sensitivity.BUFFERS}
    assert out[0]['status']==out[25]['status']=='assumed_eligible'
    assert out[50]['reason']=='osm_mapped_water_or_buffer_conflict'
    assert out[0]['water_screen_buffer_m']==0 and out[25]['water_screen_buffer_m']==25
    assert out[0]['raster_and_land_corridor_m']==50
    assert len(cover.bounds)==2 and cover.bounds[0]==cover.bounds[1]


@pytest.mark.parametrize('feature',[
    LineString([(291000,4062200),(291000,4062300)]),
    LineString([(291000,4062250),(291000,4062300)]),
    Polygon([(291000,4062200),(291100,4062200),(291100,4062300),(291000,4062300)]),
])
@pytest.mark.parametrize('buffer_m',[0,25,50])
def test_actual_intersections_including_touches_are_always_rejected(feature,buffer_m):
    edge,way,nodes,water,supported=fixture();water['osm']=STRtree([feature])
    assert sensitivity.screen_at(buffer_m,edge,way,nodes,water,Cover(),supported,set())['status']=='unknown'


@pytest.mark.parametrize('buffer_m',[0,25,50])
def test_invalid_nearby_water_and_ambiguous_road_rejected(buffer_m):
    edge,way,nodes,water,supported=fixture()
    invalid=Polygon([(291000,4062240),(291100,4062280),(291000,4062280),(291100,4062240),(291000,4062240)])
    assert not invalid.is_valid
    water['osm']=STRtree([invalid])
    assert sensitivity.screen_at(buffer_m,edge,way,nodes,water,Cover(),supported,set())['reason']=='ambiguous_mapped_water_geometry'
    edge['coordinates']=[[290750.,4062250.],[292250.,4062250.],[290750.,4062250.]]
    assert sensitivity.screen_at(buffer_m,edge,way,nodes,water,Cover(),supported,set())['reason']=='ambiguous_road_geometry'


def test_missing_layer_and_unsupported_threshold_fail_closed():
    edge,way,nodes,water,supported=fixture();water['osm']=STRtree([])
    assert sensitivity.screen_at(0,edge,way,nodes,water,Cover(),supported,set())['reason']=='osm_water_layer_missing'
    with pytest.raises(ValueError):sensitivity.screen_at(10,edge,way,nodes,water,Cover(),supported,set())


def test_public_handover_is_reconstructed_exactly_and_tamper_rejected():
    candidate={'cases':[]};audit={'case_audits':[]};drafts={'cases':[]}
    for i in range(12):
        draft={'id':str(i),'sha256':'b'*64,'sector_grid':[2,3]}
        public={'id':str(i),'case_sha256':'b'*64,'status':'handover',
                'diagnostic_identity_sha256':'c'*64,'public_fire':[[1,2]],'wind':[0,1]}
        record={'case_id':str(i),'public_observation':{'sector':[2,3],
            'diagnostic_identity_sha256':'c'*64,'fire':[[1,2]],'wind':[0,1]}}
        candidate['cases'].append({'original_case_id':str(i),'fire_draft_id':str(i),
            'fire_draft_sha256':'b'*64,'handover_public_observation_sha256':digest(public)})
        audit['case_audits'].append(record);drafts['cases'].append(draft)
    assert len(sensitivity.public_records(candidate,audit,drafts)['cases'])==12
    altered=copy.deepcopy(audit);altered['case_audits'][0]['public_observation']['wind']=[1,0]
    with pytest.raises(ValueError,match='observation pin'):sensitivity.public_records(candidate,altered,drafts)


def test_setting_restores_original_screen_even_on_failure(monkeypatch):
    original=sensitivity.staging.screen_edge
    def fail(*args):raise ValueError('expected')
    monkeypatch.setattr(sensitivity.staging,'run_candidate',fail)
    with pytest.raises(ValueError,match='expected'):sensitivity.run_setting(25)
    assert sensitivity.staging.screen_edge is original


def test_private_comparison_counts_routes_and_attributes_shared_nonintersecting_feature():
    from types import SimpleNamespace
    edge,way,nodes,water,supported=fixture()
    edge.update(source_way=10,first_segment=0,last_segment=0,direction='f',source='1',target='2')
    source={'nodes':[{'id':1,'position_m':edge['coordinates'][0],'tags':{}},
                     {'id':2,'position_m':edge['coordinates'][1],'tags':{}}],
            'ways':[{'id':10,'nodes':[1,2],**way}]}
    network=SimpleNamespace(edges={'e':edge})
    base={'original_case_id':'development','original_case_sha256':'a'*64,'candidates':[]}
    new=copy.deepcopy(base);new['candidates']=[{'unit_id':'u','staging_node':'2',
                                              'outbound_edges':['e'],'return_edges':[]}]
    runs={50:([base],[{'search_incomplete':False}],{'e':{'reason':'osm_mapped_water_or_buffer_conflict'}}),
          25:([new],[{'search_incomplete':False}]),0:([new],[{'search_incomplete':False}])}
    summary,cases,baseline,conflicts=sensitivity.compare_runs(runs,network,source,water,Cover(),supported,set())
    assert baseline['all_conflicts_share_one_nonintersecting_feature'] and len(conflicts)==1
    assert conflicts[0]['mapped_intersections']==0 and conflicts[0]['hypothetical_screens']['25']=='assumed_eligible'
    for b in ('0','25'):
        assert summary[b]['added_routes']==1 and summary[b]['removed_routes']==0
        assert summary[b]['all_added_routes_share_single_nonintersecting_mapped_feature']
        assert summary[b]['distinct_associated_features']==1
        assert cases[0]['comparisons'][b]['route_evidence'][0]['changed_edges']


def test_cli_rejects_public_or_nonopted_output(tmp_path):
    root=Path(__file__).resolve().parents[1];script=root/'eval/expanded_staging_water_sensitivity_v1.py'
    for args in (['--output-dir',str(tmp_path/'private')],
                 ['--opt-in-development-sensitivity','--output-dir',str(root/'assets'/'private')]):
        proc=subprocess.run([sys.executable,str(script),*args],capture_output=True,text=True,cwd=root)
        assert proc.returncode and not (tmp_path/'private').exists()


@pytest.mark.skipif(not Path('/tmp/expanded-staging-water-sensitivity-v1-development-r2/audit.json').is_file(),
                    reason='private development sensitivity audit unavailable')
def test_private_pinned_comparison_audit():
    path=Path('/tmp/expanded-staging-water-sensitivity-v1-development-r2/audit.json')
    doc=json.loads(path.read_bytes())
    assert path.stat().st_mode & 0o777==0o600 and path.parent.stat().st_mode & 0o777==0o700
    assert doc['sha256']==digest({k:v for k,v in doc.items() if k!='sha256'})
    assert doc['pins']['baseline_candidate']==sensitivity.BASE_CANDIDATE_PIN
    assert doc['pins']['baseline_audit']==sensitivity.BASE_AUDIT_PIN
    assert doc['pins']['sensitivity_source']==hashlib.sha256(Path(sensitivity.__file__).read_bytes()).hexdigest()
    assert doc['buffers_m']==[0,25,50] and len(doc['cases'])==12
    assert doc['baseline_water_conflict_summary']['water_conflicted_directed_edges']==len(doc['baseline_water_conflicts'])
    for b in ('0','25'):
        records=[case['comparisons'][b] for case in doc['cases']]
        assert doc['summary'][b]['added_routes']==sum(v['added'] for v in records)
        assert doc['summary'][b]['removed_routes']==sum(v['removed'] for v in records)
