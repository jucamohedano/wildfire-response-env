"""Fresh-seed synthetic weather, warmup, censor, privacy and replay."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_fresh_owner_v2_synthetic():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'eval'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
    assert p.returncode==0,p.stdout+p.stderr


def run():
    import copy
    import json
    import pytest
    import adapter_v1 as api
    from eval import fresh_two_component_weather_v2 as owner
    from eval.extended_map_common import digest
    from wildfire_core.incident_day import fixture_inputs
    # Keep source loading out of synthetic tests; source staging and catalog are
    # separately tested against their independently pinned inputs.
    owner._ensure_code=lambda:None
    parent=fixture_inputs()[0];grid=dict(width=84,height=59,crs='EPSG:25830',transform=[500,0,0,0,-500,29500])
    profile={'version':'synthetic','grid':grid};profile['sha256']=api._hash(profile);owner.PROFILE_PIN=profile['sha256']
    initial_wind=[.123456789,-.987654321]
    case=dict(id='synthetic',seed=37,initial_fire=[[10,10]],warmup_minutes=2,wind=initial_wind,
        events=[{'minute':2,'kind':'wind','wind':[-1,0]}],
        weather_schedule=[dict(minute=0,wind=initial_wind,temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0),
                          dict(minute=2,wind=[-1,0],temperature_c=20,relative_humidity_pct=60,rain_mm_per_hour=1)])
    case['sha256']=api._hash(case);drafts={'cases':[case],'sha256':'d'*64}
    owner.build_drafts=lambda *args:drafts
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
        return owner.FreshTwoComponentWeatherV2(staged,profile,parent,catalog,expected_staging_identity=digest(staged['identity']),
          expected_parent_sha256=parent['sha256'],expected_catalog_sha256=catalog['sha256'],case_id='synthetic',
          ignition_components={(10,10):'vegetation'},containment_config=config,pressure_scale=1,day_horizon=horizon,opt_in=True)
    staged=make_staged();a=build(staged);before=a._model.fire.rng.getstate()
    assert a.situation()['status']=='active' and a._model.fire.rng.getstate()==before
    assert list(a._model.fire.wind)==initial_wind
    prewarm=json.loads(json.dumps(a.checkpoint()));r0=build(staged)
    r0.restore(prewarm,expected_checkpoint_sha256=prewarm['sha256'])
    assert list(r0._model.fire.wind)==initial_wind and api._encoded(r0.checkpoint())==api._encoded(prewarm)
    assert 'private-evidence' not in str(a.situation()) and 'seed' not in a.situation()
    assert a.step()['warmup_steps']==1;assert a.step()['warmup_steps']==2
    preday=json.loads(json.dumps(a.checkpoint()));rday=build(staged)
    rday.restore(preday,expected_checkpoint_sha256=preday['sha256'])
    assert rday._weather_kind=='day' and rday._minute==0 and list(rday._model.fire.wind)==initial_wind
    assert a.step()==rday.step() and api._encoded(a.checkpoint())==api._encoded(rday.checkpoint())
    assert a.situation()['minute']==1;assert a.step()['minute']==2
    assert list(a._model.fire.wind)==[-1.0,0.0]
    saved=json.loads(json.dumps(a.checkpoint()));b=build(staged);b.restore(saved,expected_checkpoint_sha256=saved['sha256'])
    for _ in range(3):
        assert a.step()==b.step();assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
    forged=copy.deepcopy(saved);forged['minute']+=1;forged['sha256']=api._hash({k:v for k,v in forged.items() if k!='sha256'})
    c=build(staged)
    with pytest.raises(ValueError):c.restore(forged,expected_checkpoint_sha256=saved['sha256'])
    limited=build(staged,horizon=1)
    for _ in range(3):limited.step()
    assert limited.situation()['status']=='diagnostic_end'
    final=limited.checkpoint();finished=build(staged,horizon=1)
    finished.restore(final,expected_checkpoint_sha256=final['sha256'])
    assert finished.situation()['status']=='diagnostic_end'
    with pytest.raises(RuntimeError):limited.step()
    init_unknown=build(make_staged((10,10)))
    assert init_unknown.situation()['status']=='censored' and init_unknown._model.fire.tick==0
    with pytest.raises(RuntimeError):init_unknown.checkpoint()
    near_unknown=build(make_staged((11,10)))
    assert near_unknown.situation()['status']=='censored' and near_unknown._model.fire.tick==0
    bad=copy.deepcopy(staged);bad['grid']['transform'][2]+=500
    with pytest.raises(ValueError):build(bad)
    altered=copy.deepcopy(staged);altered['components'][10][10]['vegetation']['fuel']=.7
    with pytest.raises(ValueError):build(altered)
    # Zero warmup is also a pre-first-day checkpoint with the exact vector.
    case['warmup_minutes']=0;case['sha256']=api._hash({k:v for k,v in case.items() if k!='sha256'})
    day0=build(staged);day0_saved=json.loads(json.dumps(day0.checkpoint()));day0_copy=build(staged)
    day0_copy.restore(day0_saved,expected_checkpoint_sha256=day0_saved['sha256'])
    assert day0_copy._weather_kind=='day' and day0_copy._minute==0
    assert list(day0_copy._model.fire.wind)==initial_wind
    assert day0.step()==day0_copy.step() and api._encoded(day0.checkpoint())==api._encoded(day0_copy.checkpoint())
    # A wall deadline is checked after the status read and before any step.
    import time
    original_clock=time.monotonic;times=iter([0.,0.,2.,2.])
    time.monotonic=lambda:next(times,2.)
    try:
        bounded=build(staged);result=owner.run_bounded(bounded,wall_seconds=1,step_limit=2)
        assert result['steps']==0 and result['stop_reason']=='wall_limit' and bounded._model.fire.tick==0
    finally:time.monotonic=original_clock
    history=copy.deepcopy(staged);history['components'][10][10]['vegetation']['heat']=.25
    history['identity']['components_sha256']=digest(history['components'])
    with pytest.raises(ValueError,match='prior fire history'):build(history)

if __name__=='__main__':run()
