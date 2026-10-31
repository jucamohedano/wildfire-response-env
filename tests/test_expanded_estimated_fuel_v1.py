"""Synthetic tests for separate estimated-fuel fire physics."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_estimated_synthetic():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
    assert p.returncode==0,p.stdout+p.stderr


def run():
    import adapter_v1 as api
    import wildfire_expanded_incident_v1.estimated_fuel_v1 as m
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_expanded_incident_v1.estimated_weather_v1 import EstimatedWeatherFireV1
    n=7;grid=dict(width=n,height=n,crs='EPSG:25830',transform=[500,0,0,0,-500,3500])
    doc=dict(version=api._contract_module.VERSION,grid=grid,domain_mask=[[True]*n for _ in range(n)],
        surface=[['land']*n for _ in range(n)],fuel=[[4]*n for _ in range(n)],elevation=[[100.]*n for _ in range(n)],
        access=[['unknown']*n for _ in range(n)],source_pins={k:'a'*64 for k in ('surface','fuel','elevation','access')})
    doc['surface'][3][4]='unknown';doc['fuel'][3][4]=None
    doc['surface'][1][1]='unknown';doc['fuel'][1][1]=None
    doc['surface'][5][5]='sea';doc['fuel'][5][5]=None;doc['elevation'][5][5]=None
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
    base_id=dict(strict='s',water_v2='w',overlay_v3='3',overlay_v4='4',dera_rivers='r',crossing_proxy=.9,catalog='c')
    base=MixedInputsV2(contract,(),tuple(sorted(base_id.items())),_VERIFIED)
    m.prepare_exercise_v4=lambda *a,**k:base
    def source():return dict(surface='land',fuel_status='source',source_fuel_class=4,worldcover_samples={'10':2500})
    cells=[[source() for x in range(n)] for y in range(n)]
    strict=dict(cells=copy.deepcopy(cells));strict['cells'][3][4]['source_fuel_class']=None
    water=dict(grid=grid,cells=copy.deepcopy(cells));water['cells'][3][4].update(surface='unknown',fuel_status='unknown',source_fuel_class=None)
    scenarios={}
    for name,klass,factor in [('low',2,.8),('nominal',4,.9),('high',4,1.)]:
        mapped=m.fire_v1.FUEL_MAPPING[klass]
        scenarios[name]=dict(assumed_fuel_class=klass,assumed_terrain=mapped['terrain'],
            exercise_base_initial_fuel=mapped['initial_fuel'],fuel_factor_assumption=factor,
            modeled_initial_fuel=mapped['initial_fuel']*factor)
    record=dict(column=4,row=3,surface='mixed_exercise_assumption',source_fuel_class=None,terrain_candidate=100.,
        worldcover_samples={'10':2500},surface_evidence=dict(both_land_faces_and_coastal_water_polygon_screen=True,
        osm_water_area_conflicts=0,watercourse_evidence_present=True),fuel_hypothesis=dict(confidence='low_uncalibrated',
        status='ambiguous_sensitivity_hypothesis',scenarios=scenarios))
    hypo=dict(version='mixed-estimated-fuel-hypotheses-v1',status='private_sensitivity_evidence_not_runtime_input',
        grid=grid,cells=[record],source_pins=dict(strict='s',water_v2='w',exercise_v3='3',exercise_v4='4',dera_rivers_response_sha256='r'))
    hypo['sha256']=api._hash(hypo);m.HYPOTHESIS_SHA256=hypo['sha256']
    def prepare(s='nominal',h=hypo,opt=True):
        return m.prepare_estimated_inputs(strict,water,None,None,None,None,h,fuel_scenario=s,opt_in_estimated_fuel=opt)
    low,nom,high=[prepare(s) for s in ('low','nominal','high')]
    assert low.base.contract.fuel[3][4] is None and low.base.contract.surface[3][4]=='unknown'
    assert low.estimated_mask[3][4] and not low.source_mask[3][4] and low.source_mask[3][3]
    with pytest.raises(ValueError):prepare(opt=False)
    altered=copy.deepcopy(hypo);altered['cells'][0]['surface_evidence']['osm_water_area_conflicts']=1
    altered['sha256']=api._hash({k:v for k,v in altered.items() if k!='sha256'})
    with pytest.raises(ValueError,match='independent pin'):prepare(h=altered)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def fire(i=nom):return m.EstimatedMixedFireV1(i,seed=7,containment_config=config,pressure_scale=1,wind=[3,0],opt_in_estimated_fuel=True)
    a,b,c=map(fire,(low,nom,high))
    assert [f._fire.cells[3][4]['initial_fuel'] for f in (a,b,c)]==pytest.approx([.6,.9,1.])
    assert a._fire.cells[3][4]['terrain']=='field' and b._fire.cells[3][4]['terrain']=='scrub'
    assert b._fire._mixed_openness[(4,3)]==.9
    assert not fire().change({'ignite':[[2,2]]}) # adjacent untrusted cell censors before step
    sea=fire();assert not sea.change({'ignite':[[5,5]]})
    assert sea._fire.cells[5][5]['fuel']==0 and sea._fire.heights[5][5] is None
    post=fire();assert post.change({'ignite':[[3,2]]})
    post._fire.cells[2][3].update(heat=1.,age=119)
    class Zero:
        def random(self):return 0.
    post._fire.rng=Zero();post._fire.spread_interval=lambda dx,dy:1
    assert not post.step() and post.reasons==('unknown_fire_state',)
    assert post._fire.tick==1
    with pytest.raises(api.CensoredError):post.step()
    assert post._fire.cells[1][1]['heat']==0 and post._fire.cells[5][5]['heat']==0
    diagonal=fire();assert diagonal.change({'ignite':[[4,5]]})
    diagonal._fire.cells[5][4].update(heat=1.,age=10)
    diagonal._fire.rng=Zero();diagonal._fire.spread_interval=lambda dx,dy:1
    assert not diagonal.step()  # another spread reaches the outer grid boundary
    assert diagonal.reasons==('grid_boundary',)
    assert diagonal._fire.cells[4][5]['heat']==0  # diagonal corner touches sea
    assert diagonal._fire.cells[5][5]['heat']==0
    # A draw between reduced and unreduced probabilities verifies the explicit
    # water proxy is used, while a zero draw verifies it is not a firebreak.
    from simulator.terrain import PROFILES
    def spread(draw,ignore=False):
        f=fire(high);assert f.change({'ignite':[[3,3]]})
        f._fire.cells[3][3].update(heat=1.,age=119)
        if ignore:f._fire._mixed_openness[(4,3)]=1.
        class Fixed:
            def random(self):return draw
        f._fire.rng=Fixed();assert f.step();return f._fire.cells[3][4]['heat']
    chance=.5*PROFILES['scrub']['spread']
    assert spread(chance*.95)==0 and spread(chance*.95,True)>0 and spread(0.)>0
    f=fire();assert f.change({'ignite':[[4,3]]})
    for _ in range(5):assert f.step()
    saved=json.loads(json.dumps(f.snapshot()));g=fire();g._poisoned=True;g.restore(saved)
    for _ in range(10):
        assert f.step()==g.step();assert api._encoded(f.snapshot())==api._encoded(g.snapshot())
    with pytest.raises(ValueError):fire(low).restore(saved)
    case=dict(id='synthetic',seed=7,initial_fire=[[4,3]],warmup_minutes=2,wind=[1,0],events=[],
        weather_schedule=[dict(minute=0,wind=[1,0],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0)])
    case['sha256']=api._hash(case)
    drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',source_catalog_sha256='c',cases=[case])
    drafts['sha256']=api._hash(drafts)
    def host():return EstimatedWeatherFireV1(nom,drafts,expected_drafts_sha256=drafts['sha256'],case_id='synthetic',
        containment_config=config,pressure_scale=1,opt_in_estimated_weather=True)
    h=host();h.step();s=json.loads(json.dumps(h.checkpoint()));j=host();j._poisoned=True;j.restore(s)
    for _ in range(5):
        assert h.step()==j.step();assert api._encoded(h.checkpoint())==api._encoded(j.checkpoint())


if __name__=='__main__':run()
