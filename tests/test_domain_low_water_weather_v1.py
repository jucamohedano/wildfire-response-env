"""Synthetic low-water input choices, source checks, censoring and replay."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_low_water_inputs():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'eval'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
    assert p.returncode==0,p.stdout+p.stderr


def run():
    import copy
    import pytest
    import adapter_v1 as api
    from eval import domain_low_water_weather_v1 as m
    from eval.domain_profile_weather_v2 import convert_profile
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    n=7;pins={k:'a'*64 for k in ('surface','fuel','elevation','access')}
    grid={'width':n,'height':n,'crs':'EPSG:25830','transform':[500,0,0,0,-500,3500]}
    doc={'version':api._contract_module.VERSION,'grid':grid,'domain_mask':[[True]*n for _ in range(n)],
         'surface':[['land']*n for _ in range(n)],'fuel':[[4]*n for _ in range(n)],
         'elevation':[[100.]*n for _ in range(n)],'access':[['unknown']*n for _ in range(n)],'source_pins':pins}
    doc['surface'][1][1]='sea';doc['fuel'][1][1]=doc['elevation'][1][1]=None
    for x,y in ((4,3),(5,1)):
        doc['surface'][y][x]='unknown';doc['fuel'][y][x]=None
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=pins,opt_in=True)
    ident=dict(strict='s',water_v2='w',overlay_v3='3',overlay_v4='4',dera_rivers='r',catalog='c',crossing_proxy=.9)
    base=MixedInputsV2(contract,(),tuple(sorted(ident.items())),_VERIFIED)
    cells=[];strict=[];water=[]
    for y in range(n):
        sr=[];wr=[]
        for x in range(n):
            surface=doc['surface'][y][x];fuel=doc['fuel'][y][x]
            status='mapped_sea' if surface=='sea' else 'unknown' if surface=='unknown' else 'mapped_land_source'
            cells.append(dict(column=x,row=y,status=status,reason='unresolved' if status=='unknown' else None,
                              v2_surface=surface,source_fuel_class=fuel,terrain_candidate_available=doc['elevation'][y][x] is not None))
            obj=dict(surface=surface,fuel_status='source' if fuel is not None else 'unknown',source_fuel_class=fuel,
                     worldcover_samples={'10':2499,'80':1} if (x,y)==(4,3) else {'10':2500})
            sr.append(copy.deepcopy(obj));wr.append(copy.deepcopy(obj))
        strict.append(sr);water.append(wr)
    parent=dict(version='wildfire-domain-fuel-surface-sensitivity-profile-v3',status='private_synthetic_evidence_not_runtime_input',
                grid=grid,cells=cells,source_pins=dict(strict='s',water_v2='w',exercise_v3='3',exercise_v4='4',dera_rivers='r'))
    parent['sha256']=api._hash(parent);m.PARENT_PIN=parent['sha256']
    parent_in=convert_profile(parent,base,expected_profile_sha256=parent['sha256'],opt_in=True)
    new=copy.deepcopy(parent);new['version']=m.compiler.VERSION;new['parent_profile_sha256']=parent['sha256']
    r=next(r for r in new['cells'] if (r['column'],r['row'])==(4,3))
    alternatives=[]
    for k in (2,4):
        mapping=m.estimated.fire_v1.FUEL_MAPPING[k]
        alternatives.append(dict(assumed_fuel_class=k,assumed_terrain=mapping['terrain'],
          exercise_base_initial_fuel=mapping['initial_fuel'],fuel_factor_assumptions=dict(m.compiler.prior.source.EXERCISE_FACTORS)))
    r.update(status='new_low_water_mixed_fuel_ambiguous_sensitivity_hypothesis',reason=None,
       surface_assumption='mixed_synthetic_sensitivity',source_neighbor_support=[{'source_class':2},{'source_class':4},{'source_class':4}],
       fuel_hypothesis={'status':'ambiguous_sensitivity_hypothesis','source_votes':{'2':1,'4':2}},
       synthetic_fuel_class_alternatives=alternatives,
       low_water_evidence={'both_independent_mapped_land_faces_cover_cell':True,'outside_both_250m_coast_buffers':True,
          'dera_water_polygon_intersection':False,'osm_water_area_intersection':False,'worldcover_samples':{'10':2499,'80':1}},
       cross_water_sensitivity={'crossing_openness_scenarios':m.compiler.CROSSING_SCENARIOS,'default':None,'strict_censor_alternative':True})
    new['sha256']=api._hash({k:v for k,v in new.items() if k!='sha256'});m.PROFILE_PIN=new['sha256']
    def convert(klass=4,cross=.9,opt=True,profile=new):
        return m.convert_verified(profile,parent,parent_in,{'cells':strict},{'cells':water},candidate_class=klass,crossing_proxy=cross,opt_in=opt)
    with pytest.raises(ValueError):convert(opt=False)
    with pytest.raises(ValueError):convert(klass=10)
    with pytest.raises(ValueError):convert(cross=.7)
    altered=copy.deepcopy(new);altered['cells'][0]['status']='unknown'
    with pytest.raises(ValueError):convert(profile=altered)
    a=convert(2,.8);b=convert(4,1.)
    assert (4,3,2,.9,.8) in a.estimates and (4,3,4,.9,1.) in b.estimates
    assert not a.estimated_mask[1][5] and a.base.contract.surface[1][5]=='unknown'
    assert a.base.contract.surface[1][1]=='sea'
    case=dict(id='synthetic',seed=7,initial_fire=[[3,3]],warmup_minutes=2,wind=[1,0],events=[],
      weather_schedule=[dict(minute=0,wind=[1,0],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0)])
    case['sha256']=api._hash(case);drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',source_catalog_sha256='c',cases=[case]);drafts['sha256']=api._hash(drafts)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def host(i):return m.LowWaterWeatherFireV1(i,drafts,expected_drafts_sha256=drafts['sha256'],case_id='synthetic',containment_config=config,pressure_scale=1,opt_in_estimated_weather=True)
    x=host(a);x.step();saved=x.checkpoint();y=host(a);y.restore(saved)
    for _ in range(5):assert x.step()==y.step() and api._encoded(x.checkpoint())==api._encoded(y.checkpoint())
    with pytest.raises(ValueError):host(b).restore(saved)
    assert not host(a)._fire.change({'ignite':[[5,1]]})
    assert not host(a)._fire.change({'ignite':[[1,1]]})
    assert host(a)._fire._fire.cells[1][1]['fuel']==0

if __name__=='__main__':run()
