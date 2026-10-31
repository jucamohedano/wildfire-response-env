"""Domain-wide low-water profile gates and explicit alternatives."""
import copy
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'eval'))
import domain_inland_low_water_profile_v1 as extension
import domain_fuel_surface_profile_v3 as parent


def cell(surface='unknown',water=1):
    return {'surface':surface,'fuel_status':'unknown','source_fuel_class':None,
            'worldcover_samples':{'10':2500-water,'80':water}}


def test_surface_gate_rejects_unverified_land_and_conflicting_water():
    original=cell();current=cell()
    assert extension.screen_low_water(original,current,True,100,False,False) is None
    assert extension.screen_low_water(original,current,False,100,False,False)=='both_land_faces_or_coastal_water_screen_failed'
    assert extension.screen_low_water(original,current,True,None,False,False)=='terrain_missing'
    assert extension.screen_low_water(original,current,True,100,True,False)=='mapped_water_area_conflict'
    assert extension.screen_low_water(original,current,True,100,False,True)=='mapped_water_area_conflict'
    assert extension.screen_low_water(original,cell(water=26),True,100,False,False)=='worldcover_low_water_screen_failed'
    assert extension.screen_low_water(original,cell(water=0),True,100,False,False)=='worldcover_low_water_screen_failed'
    assert extension.screen_low_water(original,cell(surface='sea'),True,100,False,False)=='surface_not_unknown'
    assert extension.screen_low_water(cell(surface='land'),current,True,100,False,False)=='surface_not_unknown'


def test_domain_extension_keeps_parent_and_tied_classes_explicit(monkeypatch):
    grid={'width':3,'height':3,'crs':'EPSG:25830','transform':[500,0,0,0,-500,1500]}
    rows=[]
    for y in range(3):
        row=[]
        for x in range(3):
            k=2 if (x+y)%2==0 else 4
            row.append({'surface':'land','fuel_status':'source','source_fuel_class':k,'worldcover_samples':{'10':2500}})
        rows.append(row)
    rows[1][1]=cell();strict={'sha256':'strict','cells':copy.deepcopy(rows)}
    v2={'sha256':'water','parent_document_sha256':'strict','grid':grid,'cells':copy.deepcopy(rows),'terrain_candidate':[[100]*3 for _ in range(3)]}
    v4={'sha256':'v4','parent_v2_sha256':'water','cells':[],'additional_dera_censors':[],
        'dera_rivers_response_sha256':'river','dera_rivers_acquired':'2026-10-30',
        'dera_rivers_source_epoch':'unverified','worldcover_epoch':'2021','osm_water_epoch':'2026',
        'source_catalog_sha256':'catalog'}
    interior=[[True]*3 for _ in range(3)];archive=[[None]*3 for _ in range(3)]
    mapping={2:{'terrain':'field','initial_fuel':.75},4:{'terrain':'scrub','initial_fuel':1.}}
    base=parent.build_profile(strict,v2,v4,interior,{},archive,[],{},[],[],mapping,{})
    assert base['counts']['unknown']==1
    monkeypatch.setattr(extension,'BASE_PROFILE_PIN',base['sha256'])
    unchanged=copy.deepcopy(base)
    result=extension.extend_profile(base,strict,v2,v4,interior,{},archive,[],[],[],mapping,'code')
    assert base==unchanged and result['parent_profile_sha256']==base['sha256']
    r=result['cells'][4]
    assert r['status']=='new_low_water_mixed_fuel_tied_sensitivity_only'
    assert [x['assumed_fuel_class'] for x in r['synthetic_fuel_class_alternatives']]==[2,4]
    assert r['fuel_hypothesis']['nominal_class'] is None
    assert r['cross_water_sensitivity']['strict_censor_alternative'] is True
    assert r['cross_water_sensitivity']['default'] is None
    assert sum(result['counts'].values())==9 and result['counts'].get('unknown',0)==0
    assert all(a==b for i,(a,b) in enumerate(zip(base['cells'],result['cells'])) if i!=4)
    blocked=copy.deepcopy(interior);blocked[1][1]=False
    other=extension.extend_profile(base,strict,v2,v4,blocked,{},archive,[],[],[],mapping,'code')
    assert other['cells'][4]['status']=='unknown'
