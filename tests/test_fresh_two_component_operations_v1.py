"""Synthetic component work, censor and replay of the versioned fresh tick."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def test_operations_tick():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'eval'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
    assert p.returncode==0,p.stdout+p.stderr

def run():
    import copy
    import json
    import pytest
    import adapter_v1 as api
    from eval import fresh_two_component_operations_v1 as owner
    from eval import fresh_two_component_weather_v2 as legacy
    from eval.extended_map_common import digest
    from wildfire_core.incident_day import fixture_inputs
    # Keep source loading out of synthetic tests; source staging and catalog are
    # separately tested against their independently pinned inputs.
    legacy._ensure_code=lambda:None
    parent=fixture_inputs()[0];grid=dict(width=84,height=59,crs='EPSG:25830',transform=[500,0,0,0,-500,29500])
    profile={'version':'synthetic','grid':grid};profile['sha256']=api._hash(profile);legacy.PROFILE_PIN=profile['sha256']
    initial_wind=[.123456789,-.987654321]
    case=dict(id='synthetic',seed=37,initial_fire=[[10,10]],warmup_minutes=2,wind=initial_wind,
        events=[{'minute':2,'kind':'wind','wind':[-1,0]}],
        weather_schedule=[dict(minute=0,wind=initial_wind,temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0),
                          dict(minute=2,wind=[-1,0],temperature_c=20,relative_humidity_pct=60,rain_mm_per_hour=1)])
    case['sha256']=api._hash(case);drafts={'cases':[case],'sha256':'d'*64}
    legacy.build_drafts=lambda *args:drafts
    catalog={'version':'synthetic','inputs_sha256':parent['sha256']};catalog['sha256']=api._hash(catalog)
    transfer={'vegetation>vegetation':1.,'vegetation>built':.2,'built>vegetation':.3,'built>built':1.}
    def make_staged(unknown=None):
        components=[[{'vegetation':None,'built':None,'inert':{'fraction':1.,'assumption':'synthetic_inert'}} for _ in range(84)] for _ in range(59)]
        components[10][10]={'vegetation':dict(terrain='field',fraction=1.,fuel=.8,initial_fuel=.8,heat=0.,age=0,wet=0,burned=0.),'built':None,'inert':None}
        if unknown is not None:
            x,y=unknown;components[y][x]={'vegetation':'unknown','built':'unknown','inert':'unknown'}
        heights=[[100.]*84 for _ in range(59)];crossings=[[1.]*84 for _ in range(59)]
        transfers=[[copy.deepcopy(transfer) for _ in range(84)] for _ in range(59)]
        audit={'bbox':[0,0,84,59],'secret':'private-evidence'}
        identity={'profile_sha256':profile['sha256'],'model_sha256':owner.STAGING_MODEL_PIN,'stage_sha256':owner.staging_adapter.STAGE_PIN,
            'adapter_sha256':owner.STAGING_ADAPTER_PIN,'bbox':[0,0,84,59],'provenance_sha256':digest(audit)}
        for k,v in (('components',components),('heights',heights),('crossings',crossings),('transfers',transfers)):identity[k+'_sha256']=digest(v)
        return dict(version=owner.staging_adapter.VERSION,role='private_v2_grid_staging_not_source_fire_owner',grid=grid,
                    components=components,heights=heights,crossings=crossings,transfers=transfers,private_audit=audit,identity=identity)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def build(staged,horizon=6):
        return owner.FreshTwoComponentOperationsV1(staged,profile,parent,catalog,expected_staging_identity=digest(staged['identity']),
          expected_parent_sha256=parent['sha256'],expected_catalog_sha256=catalog['sha256'],case_id='synthetic',
          ignition_components={(10,10):'vegetation'},containment_config=config,pressure_scale=1,day_horizon=horizon,opt_in=True)
    staged=make_staged((20,20))
    staged['components'][10][11]=copy.deepcopy(staged['components'][10][10])
    staged['identity']['components_sha256']=digest(staged['components'])
    # An operations-free tick must preserve the previous owner's exact
    # component physics, weather and random stream, including warmup.
    baseline=legacy.FreshTwoComponentWeatherV2(staged,profile,parent,catalog,expected_staging_identity=digest(staged['identity']),
      expected_parent_sha256=parent['sha256'],expected_catalog_sha256=catalog['sha256'],case_id='synthetic',
      ignition_components={(10,10):'vegetation'},containment_config=config,pressure_scale=1,day_horizon=6,opt_in=True)
    parity=build(staged)
    for _ in range(3):
        baseline.step();parity.step()
        assert baseline._model.checkpoint()==parity._model.checkpoint()
        assert baseline._weather_state==parity._weather_state
    a=build(staged);start=a.checkpoint()
    drop=[dict(resource_id='air',cell=[10,10],component='vegetation')]
    with pytest.raises(ValueError,match='warmup'):a.step(drops=drop)
    assert a.checkpoint()==start
    a.step();a.step();before=a.checkpoint()
    original_class=owner.TwoComponentFireV3
    try:
        owner.TwoComponentFireV3=object
        with pytest.raises(RuntimeError,match='clone binding'):a.step()
    finally:owner.TwoComponentFireV3=original_class
    assert a.checkpoint()==before
    original_restore=original_class.__dict__['restore']
    try:
        original_class.restore=classmethod(lambda cls,*args,**kwargs:None)
        with pytest.raises(RuntimeError,match='clone binding'):a.step()
    finally:original_class.restore=original_restore
    assert a.checkpoint()==before
    line=[dict(resource_id='crew',edge=[[10,10],[11,10]],kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)]
    for bad in ([dict(resource_id='air',cell=[20,20],component='vegetation')], [dict(resource_id='air',cell=[10,10],component='built')], drop+drop):
        with pytest.raises(ValueError):a.step(drops=bad)
        assert a.checkpoint()==before
    invalid=[dict(resource_id='crew',edge=[[19,20],[20,20]],kind='crew',ready=True,work_remaining=5,water_litres=0,water_cost=0)]
    with pytest.raises(ValueError):a.step(assignments=invalid)
    assert a.checkpoint()==before
    with pytest.raises(ValueError):
        a.step(assignments=line,drops=[dict(resource_id='crew',cell=[10,10],component='vegetation')])
    assert a.checkpoint()==before
    result=a.step(assignments=line,drops=drop)
    assert result['minute']==1 and a._model.fire._containment._minute==3
    assert a._model._components[10][10]['vegetation']['wet']>0
    assert len(a._last_costs)==1 and 'secret' not in str(result)
    saved=json.loads(json.dumps(a.checkpoint()));b=build(staged);b.restore(saved,expected_checkpoint_sha256=saved['sha256'])
    forged=copy.deepcopy(saved);forged['last_costs']=[]
    forged['sha256']=api._hash({k:v for k,v in forged.items() if k!='sha256'})
    wrong=build(staged)
    with pytest.raises(ValueError):wrong.restore(forged,expected_checkpoint_sha256=forged['sha256'])
    assert b.step()==a.step() and api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
    # A real model guard, rather than an injected failure: ignition reaches a
    # known cell adjoining unknown support, so the staged result is censored.
    case['warmup_minutes']=0;case['sha256']=api._hash({k:v for k,v in case.items() if k!='sha256'})
    edge=make_staged((12,10));edge['components'][10][11]=copy.deepcopy(edge['components'][10][10])
    edge['identity']['components_sha256']=digest(edge['components'])
    d=build(edge);conditions=owner.weather.conditions(d._profile,1)
    row=copy.deepcopy(conditions);row.update(minute=0,received_at=0)
    selected=owner.weather.WeatherProfile.from_records([row])
    previous=owner.weather.initial_state(selected,moisture_proxy=d._weather_state['moisture_proxy'])
    updated=owner.weather.advance(selected,previous,1)
    d._model.fire.wind=tuple(conditions['wind'])
    d._model.fire.rules['spread_factor']=owner.weather.spread_factor(updated,base_factor=.5)
    d._model.fire.rules['ignition_probability_per_eligible_cell']=100.
    d._model._components[10][10]['vegetation']['age']=d._model.fire.spread_interval(1,0)-1
    d._model.fire.rules['spread_factor']=.5
    d._model._sync_projection();last=d.checkpoint();rng=d._model.fire.rng.getstate()
    assert d.step()['status']=='censored' and d._minute==0 and d._model.fire.rng.getstate()==rng
    d.restore(last,expected_checkpoint_sha256=last['sha256']);assert d.checkpoint()==last

if __name__=='__main__':run()
