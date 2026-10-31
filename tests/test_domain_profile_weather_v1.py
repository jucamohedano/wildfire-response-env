"""Synthetic domain-profile conversion, censoring and private replay."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_domain_profile_backend():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'eval'),str(ROOT)]))
    run=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
    assert run.returncode==0,run.stdout+run.stderr


def run():
    import copy
    import pytest
    import adapter_v1 as api
    from eval.domain_profile_weather_v1 import convert_profile,DomainProfileWeatherFireV1,CHOICE,check_v4_provenance
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_expanded_incident_v1.private_output import private_destination
    n=7;pins={k:'a'*64 for k in ('surface','fuel','elevation','access')}
    grid={'width':n,'height':n,'crs':'EPSG:25830','transform':[500,0,0,0,-500,3500]}
    doc={'version':api._contract_module.VERSION,'grid':grid,'domain_mask':[[True]*n for _ in range(n)],
         'surface':[['land']*n for _ in range(n)],'fuel':[[4]*n for _ in range(n)],
         'elevation':[[100.]*n for _ in range(n)],'access':[['unknown']*n for _ in range(n)],'source_pins':pins}
    doc['surface'][1][1]='sea';doc['fuel'][1][1]=doc['elevation'][1][1]=None
    doc['surface'][1][5]='unknown';doc['fuel'][1][5]=None
    doc['fuel'][3][4]=None;doc['surface'][4][4]='unknown';doc['fuel'][4][4]=None
    doc['surface'][5][2]='unknown'
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=pins,opt_in=True)
    identity=dict(strict='s',water_v2='w',overlay_v3='3',overlay_v4='4',dera_rivers='r',catalog='c',crossing_proxy=.9)
    base=MixedInputsV2(contract,(),tuple(sorted(identity.items())),_VERIFIED)
    cells=[]
    for y in range(n):
        for x in range(n):
            status='mapped_land_source'; src=doc['fuel'][y][x]
            if (x,y)==(1,1):status='mapped_sea'
            if (x,y)==(5,1):status='unknown'
            r=dict(column=x,row=y,status=status,source_fuel_class=src)
            if (x,y)==(4,3):r.update(status='new_land_fuel_ambiguous_sensitivity_hypothesis',surface_assumption='land',
                fuel_hypothesis={'status':'ambiguous_sensitivity_hypothesis','nominal_class_assumption':4,
                                 'scenarios':{'nominal':{'assumed_fuel_class':4}}})
            if (x,y)==(4,4):r.update(status='new_mixed_fuel_tied_sensitivity_only',surface_assumption='mixed',
                fuel_hypothesis={'status':'tied_sensitivity_only','scenario_classes':[2,4]})
            if (x,y)==(2,5):r['status']='new_mixed_source'
            cells.append(r)
    profile=dict(version='wildfire-domain-fuel-surface-sensitivity-profile-v3',
        status='private_synthetic_evidence_not_runtime_input',grid=grid,cells=cells,
        source_pins=dict(strict='s',water_v2='w',exercise_v3='3',exercise_v4='4',dera_rivers='r'))
    profile['sha256']=api._hash(profile)
    def convert(p=profile,opt=True):return convert_profile(p,base,expected_profile_sha256=profile['sha256'],opt_in=opt)
    with pytest.raises(ValueError):convert(opt=False)
    changed=copy.deepcopy(profile);changed['cells'][0]['status']='mapped_sea'
    with pytest.raises(ValueError):convert(changed)
    v4={'sha256':'v4','dera_rivers_response_sha256':'river','dera_rivers_acquired':'2026',
        'dera_rivers_source_epoch':'unverified','worldcover_epoch':'2021','osm_water_epoch':'2026',
        'source_catalog_sha256':'catalog','cells':[]}
    keys=('dera_rivers_response_sha256','dera_rivers_acquired','dera_rivers_source_epoch',
          'worldcover_epoch','osm_water_epoch','source_catalog_sha256')
    prov={'document_sha256':'v4',**{k:v4[k] for k in keys}}
    check_v4_provenance({'inherited_v4_provenance':prov,'cells':[]},v4)
    with pytest.raises(ValueError):check_v4_provenance({'inherited_v4_provenance':{},'cells':[]},v4)
    inp=convert();assert dict(inp.identity)['choice']==CHOICE
    assert (4,3,4,.9,1.) in inp.estimates and (4,4,2,.9,.9) in inp.estimates
    assert inp.base.contract.surface[5][2]=='mixed' and inp.base.contract.surface[1][1]=='sea'
    assert inp.base.contract.surface[1][5]=='unknown' and not inp.estimated_mask[1][5]
    case=dict(id='synthetic',seed=7,initial_fire=[[3,3]],warmup_minutes=2,wind=[1,0],events=[],
      weather_schedule=[dict(minute=0,wind=[1,0],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0)])
    case['sha256']=api._hash(case);drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',source_catalog_sha256='c',cases=[case]);drafts['sha256']=api._hash(drafts)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def host():return DomainProfileWeatherFireV1(inp,drafts,expected_drafts_sha256=drafts['sha256'],case_id='synthetic',containment_config=config,pressure_scale=1,opt_in_estimated_weather=True)
    a=host();assert set(a.situation())=={'version','status','minute','warmup_steps','mode'}
    assert 'source_fuel_class' not in str(a.situation()) and 'profile_sha256' not in str(a.situation())
    a.step();saved=a.checkpoint();b=host();b.restore(saved)
    for _ in range(5):assert a.step()==b.step() and api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
    assert a._fire._fire.cells[1][1]['fuel']==0
    assert not host()._fire.change({'ignite':[[5,1]]})
    with pytest.raises(ValueError):private_destination(ROOT/'public-domain-profile-test.json')

if __name__=='__main__':run()
