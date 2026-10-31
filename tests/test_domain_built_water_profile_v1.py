"""Source-backed mixed built/water hypotheses remain explicit and domain-wide."""
import copy
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'eval'))
import domain_built_water_profile_v1 as m
from extended_map_common import digest


def source():
    return {'surface':'unknown','fuel_status':'source','source_fuel_class':4,
      'fuel_provenance':{'kind':'effis_source_center_sample'},
      'worldcover_samples':{'10':2000,'50':400,'60':100},
      'osm_water_conflicts':[{'kind':'watercourse','geometry':'line'}]}


def test_rule_keeps_unknown_water_and_missing_sources_censored():
    original={'surface':'land'};current=source()
    assert m.screen(original,current,True,100,False,[]) is None
    assert m.screen(original,current,False,100,False,[])=='inland_face_or_coast_screen_failed'
    assert m.screen(original,current,True,None,False,[])=='terrain_missing'
    assert m.screen(original,current,True,100,True,[])=='mapped_water_area_conflict'
    assert m.screen(original,{**current,'source_fuel_class':None},True,100,False,[])=='source_fuel_missing'
    assert m.screen(original,{**current,'osm_water_conflicts':[]},True,100,False,[])=='no_mapped_watercourse'
    assert m.screen(original,{**current,'worldcover_samples':{'10':2500}},True,100,False,[])=='unsupported_landcover_samples'
    assert m.screen({'surface':'unknown'},current,True,100,False,[])=='surface_not_supported'


def test_domain_profile_preserves_parent_and_records_synthetic_uncertainty(monkeypatch):
    grid={'width':3,'height':1,'crs':'EPSG:25830','transform':[500,0,0,0,-500,500]}
    cells=[[source(),source(),source()]];strict={'cells':[[{'surface':'land'},{'surface':'land'},{'surface':'unknown'}]]}
    water={'grid':grid,'cells':cells,'terrain_candidate':[[100,100,100]]}
    records=[{'column':x,'row':0,'status':'unknown','reason':'worldcover_screen_failed'} for x in range(3)]
    parent={'version':m.low.VERSION,'grid':grid,'cells':records,'policy':{},'source_pins':{}};parent['sha256']=digest(parent)
    monkeypatch.setattr(m,'PARENT_PIN',parent['sha256']);original=copy.deepcopy(parent)
    result=m.extend_profile(parent,strict,water,[[True]*3],{},[],[],[], 'source')
    assert parent==original and result['counts']['new_built_watercourse_source_sensitivity']==2
    assert result['counts']['unknown']==1 and result['cells'][2]==parent['cells'][2]
    for r in result['cells'][:2]:
        assert r['sample_proportions']=={'vegetation':.8,'built_up':.16,'bare':.04}
        assert r['evidence']['source_fuel_class']==4 and r['sensitivity']['crossing_default'] is None
        assert r['sensitivity']['strict_censor_alternative'] is True
