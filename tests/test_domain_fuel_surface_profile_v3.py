"""Domain policy: unknowns and tied votes remain explicit."""
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'eval'))
import domain_fuel_surface_profile_v3 as profile


def samples(*codes):return [{'source_class':code} for code in codes]


def test_votes_require_source_support_and_expose_ties():
    mapping={i:{'initial_fuel':i/10,'terrain':'field'} for i in (1,2,3,4)}
    assert profile.vote_policy(samples(1,1),mapping)['reason']=='fewer_than_three_source_neighbors'
    assert profile.vote_policy(samples(1,2,3),mapping)['reason']=='tied_single_neighbor_classes'
    tied=profile.vote_policy(samples(1,1,2,2,3),mapping)
    assert tied['status']=='tied_sensitivity_only' and tied['scenario_classes']==[1,2] and tied['nominal_class'] is None
    assert profile.vote_policy(samples(1,2,3,4),mapping)['reason']=='more_than_three_source_classes'
    strong=profile.vote_policy(samples(1,1,1,2),mapping)
    assert strong['nominal_class_assumption']==1 and strong['confidence']=='low_uncalibrated'


def test_water_area_geometry_screen():
    from shapely.geometry import box
    square=box(0,0,500,500);g=box(10,10,30,30); basin={'tags':{'landuse':'basin'}}
    assert profile.small_polygons(square,[(1,'water_area','polygon',g)],{1:basin},[])['mapped_fraction']==pytest.approx(.0016)
    assert profile.small_polygons(square,[(1,'water_area','polygon',g)],{1:basin},[g]) is None
    assert profile.small_polygons(square,[(1,'water_area_relation','polygon',g)],{1:basin},[]) is None
    assert profile.small_polygons(square,[(1,'water_area','polygon',box(0,1,10,10))],{1:basin},[]) is None
    assert profile.small_polygons(square,[(1,'water_area','polygon',box(1,1,200,200))],{1:basin},[]) is None


def test_grid_partition_censors_unknown_surface_and_missing_terrain():
    grid={'width':3,'height':3,'crs':'EPSG:25830','transform':[500,0,0,0,-500,1500]}
    def source():
        return {'surface':'land','fuel_status':'source','source_fuel_class':1,
                'worldcover_samples':{'10':2500}}
    rows=[[source() for _ in range(3)] for _ in range(3)]
    rows[1][1].update(fuel_status='unknown',source_fuel_class=None)
    rows[0][0].update(surface='unknown',fuel_status='unknown',source_fuel_class=None)
    rows[0][2].update(surface='sea',fuel_status='unknown',source_fuel_class=None)
    strict_rows=[[dict(cell) for cell in row] for row in rows]
    strict={'sha256':'s','cells':strict_rows}; elevations=[[100]*3 for _ in range(3)];elevations[2][0]=None
    v2={'sha256':'w','parent_document_sha256':'s','grid':grid,'cells':rows,'terrain_candidate':elevations}
    v4={'sha256':'v4','parent_v2_sha256':'w','cells':[],'additional_dera_censors':[],
        'dera_rivers_response_sha256':'river','dera_rivers_acquired':'2026-10-30',
        'dera_rivers_source_epoch':'unverified','worldcover_epoch':'2021','osm_water_epoch':'2026',
        'source_catalog_sha256':'catalog'}
    mapping={1:{'initial_fuel':1.,'terrain':'field'}}
    result=profile.build_profile(strict,v2,v4,[[True]*3 for _ in range(3)],{},[[None]*3 for _ in range(3)],[],{},[],[],mapping,{})
    assert sum(result['counts'].values())==9
    assert result['counts']['mapped_sea']==1
    assert result['counts']['new_land_fuel_estimated_hypothesis']==1
    assert result['unknown_reasons']=={'surface_not_corroborated_inland':1,'terrain_missing':1}
    center=next(c for c in result['cells'] if (c['column'],c['row'])==(1,1))
    assert center['source_fuel_class'] is None and center['fuel_hypothesis']['confidence']=='low_uncalibrated'


def test_inherited_mixed_preserves_v4_evidence():
    grid={'width':1,'height':1,'crs':'EPSG:25830','transform':[500,0,0,0,-500,500]}
    cell={'surface':'unknown','fuel_status':'source','source_fuel_class':2,'worldcover_samples':{'10':2500}}
    strict={'sha256':'s','cells':[[dict(cell)]]}
    v2={'sha256':'w','parent_document_sha256':'s','grid':grid,'cells':[[cell]],'terrain_candidate':[[100]]}
    evidence={'row':0,'column':0,'exercise_basis':'small_osm_water_area_2026',
              'worldcover_epoch':'2021','osm_water_epoch':'2026',
              'mapped_2026_water_area':{'mapped_cell_fraction':.01},
              'fuel_factor_assumptions':{'nominal':.9},'limitations':'Synthetic only'}
    v4={'sha256':'v4','parent_v2_sha256':'w','cells':[evidence],'additional_dera_censors':[],
        'dera_rivers_response_sha256':'river','dera_rivers_acquired':'2026-10-30',
        'dera_rivers_source_epoch':'unverified','worldcover_epoch':'2021','osm_water_epoch':'2026',
        'source_catalog_sha256':'catalog'}
    result=profile.build_profile(strict,v2,v4,[[True]],{},[[None]],[],{},[],[],{2:{'terrain':'field','initial_fuel':.75}}, {})
    rec=result['cells'][0]
    assert rec['status']=='existing_v4_mixed_source' and rec['inherited_v4_evidence']==evidence
    assert rec['inherited_v4_document_sha256']=='v4'
    assert result['inherited_v4_provenance']=={k:v4[k] for k in ('dera_rivers_response_sha256',
      'dera_rivers_acquired','dera_rivers_source_epoch','worldcover_epoch','osm_water_epoch',
      'source_catalog_sha256')} | {'document_sha256':'v4'}
    evidence['worldcover_epoch']='changed'
    assert rec['inherited_v4_evidence']['worldcover_epoch']=='2021'
