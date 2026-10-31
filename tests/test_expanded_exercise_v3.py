"""Synthetic v3 reconstruction and v4 fail-closed compatibility tests."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case',['validated','water_area','reject_v4','v4_gate','weather_v4'])
def test_exercise_v3(case):
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'),str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    result=subprocess.run([sys.executable,__file__,case],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr


def run(case):
    import copy,gzip,hashlib,json,tempfile
    import adapter_v1 as api
    from wildfire_expanded_incident_v1.mixed_v2 import _compiler, InteriorMixedFireV2
    from wildfire_expanded_incident_v1.exercise_v3 import prepare_exercise_v3
    from wildfire_expanded_incident_v1.exercise_v4 import prepare_exercise_v4
    from wildfire_expanded_incident_v1.mixed_host_v3 import SourceGroundedMixedDiagnosticV3, SourceGroundedMixedDiagnosticV4
    from wildfire_expanded_incident_v1.weather_fire_v1 import WeatherMixedFireDiagnosticV1
    from eval import incident_weather_v1 as weather
    from wildfire_expanded_incident_v1 import source_gate_v1
    from eval.extended_scenario_drafts_v1 import build_drafts
    n=7
    grid=dict(width=n,height=n,crs='EPSG:25830',transform=[500,0,0,0,-500,3500],bounds=[0,0,3500,3500],cell_metres=500)
    def cell():return dict(surface='land',fuel_status='source',source_fuel_class=4,estimated_fuel=None,
        fuel_provenance={'kind':'source'},worldcover_samples={'10':2500},osm_water_conflicts=[])
    cells=[[cell() for _ in range(n)] for _ in range(n)]
    strict=dict(version='extended-map-resolution-evidence-v1',grid=grid,cells=copy.deepcopy(cells),
                terrain_candidate=[[100.]*n for _ in range(n)])
    strict['sha256']=api._hash(strict)
    water=dict(version='extended-map-resolution-evidence-v2',status='private_evidence_not_simulator_input',
        parent_document_sha256=strict['sha256'],grid=grid,cells=copy.deepcopy(cells),terrain_candidate=[[100.]*n for _ in range(n)])
    water['cells'][3][4]['surface']='unknown'
    water['cells'][3][4]['osm_water_conflicts']=[{'kind':'watercourse','id':'synthetic'}]
    if case in ('water_area','v4_gate','weather_v4','weather_worker'):
        water['cells'][3][4]['osm_water_conflicts'].append({'kind':'water_area','geometry':'polygon','way_id':123})
    water['sha256']=api._hash(water)
    # Synthetic test-only trust anchors, never derived from a production caller.
    source_gate_v1.STRICT_SHA256=strict['sha256']
    source_gate_v1.WATER_V2_SHA256=water['sha256']
    parent=dict(version='incident-day-inputs-v2',grid=copy.deepcopy(grid),network_sha256='b'*64)
    parent['sha256']=api._hash(parent)
    cases=[]
    for i in range(12):
        c=dict(id=f'incident-eval-v1-{i:03}',seed=100+i,initial_fire=[[3,3]],sector_grid=[3,3],
               weather_schedule=[dict(minute=0,wind=[1,0],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0),
                                 dict(minute=3,wind=[.2,.3],temperature_c=15,relative_humidity_pct=80,rain_mm_per_hour=5)],
               events=([dict(minute=2,kind='access_restriction')] if case in ('weather_v4','weather_worker') else [])
                      +[dict(minute=3,kind='wind',wind=[.2,.3])],
               warmup_minutes=2 if case in ('weather_v4','weather_worker') else 1,centers=['center-0'],wind=[1,0])
        c['sha256']=api._hash(c);cases.append(c)
    catalog=dict(version='wildfire-incident-eval-catalog-v1',inputs_sha256=parent['sha256'],
                 network_sha256=parent['network_sha256'],cases=cases)
    catalog['sha256']=api._hash(catalog)
    compiler=_compiler()
    compiler.EXERCISE_CATALOG_PIN=catalog['sha256']  # isolated synthetic fixture only
    cover=[[{'10':2500} for _ in range(n)] for _ in range(n)]
    interior=[[True]*n for _ in range(n)]
    if case in ('v4_gate','weather_v4','weather_worker'):interior[1][1]=False
    compiler.surface_inputs=lambda *a,**k:(None,cover,None,interior)  # synthetic raw-source stand-in
    overlay=compiler.compile_exercise_v3(strict,water,interior,catalog,parent)
    def raises(exc,fn,contains=None):
        try:fn()
        except exc as error:
            if contains is not None:assert contains in str(error)
            return
        raise AssertionError('expected '+str(exc))
    with tempfile.TemporaryDirectory() as tmp:
        road=Path(tmp)/'road.gz'
        raw=json.dumps(dict(version='extended-access-directed-road-candidate-v2',
            status='source_candidate_not_operational_access',crs='EPSG:25830',bounds=grid['bounds'],nodes=[],arcs=[])).encode()
        with road.open('wb') as target:
            with gzip.GzipFile(filename='',mode='wb',fileobj=target,mtime=0) as f:f.write(raw)
        road_hash=hashlib.file_digest(road.open('rb'),'sha256').hexdigest()
        def prepare(doc=overlay,cross=.9,opt=True):
            return prepare_exercise_v3(strict,water,doc,parent,catalog,strict_pin=strict['sha256'],
                water_pin=water['sha256'],overlay_pin=doc['sha256'],parent_pin=parent['sha256'],
                catalog_pin=catalog['sha256'],road_path=road,road_pin=road_hash,coast_path='synthetic',
                water_path='synthetic',worldcover_path='synthetic',osm_manifest_path='synthetic',
                osm_samples_path='synthetic',scenario='nominal',crossing_proxy=cross,opt_in_mixed_experiment=opt)
        if case in ('v4_gate','weather_v4','weather_worker'):
            from shapely.geometry import box
            poly=box(292010,4062600,292030,4062620)
            features=[(123,'water_area','polygon',poly)]
            metadata={123:dict(tags={'natural':'water'},version=1)}
            rivers={(1,1):[dict(id_dera=1)]}
            compiler.load_osm_water=lambda *a,**k:(features,{},metadata)
            compiler.dera_river_conflicts=lambda *a,**k:rivers
            v4=compiler.compile_exercise_v4(water,overlay,interior,features,metadata,rivers,catalog,parent)
            # Rehash both the altered parent and its recomputed overlay. The
            # independent gate must reject this self-consistent replacement.
            changed=copy.deepcopy(water)
            changed['cells'][3][4]['osm_water_conflicts']=[{'kind':'watercourse','id':'synthetic'}]
            changed['sha256']=api._hash({k:v for k,v in changed.items() if k!='sha256'})
            changed_v3=compiler.compile_exercise_v3(strict,changed,interior,catalog,parent)
            changed_v4=compiler.compile_exercise_v4(changed,changed_v3,interior,features,metadata,rivers,catalog,parent)
            raises(ValueError,lambda:prepare_exercise_v3(strict,changed,changed_v3,parent,catalog,
                strict_pin=strict['sha256'],water_pin=changed['sha256'],overlay_pin=changed_v3['sha256'],
                parent_pin=parent['sha256'],catalog_pin=catalog['sha256'],road_path=road,road_pin=road_hash,
                coast_path='synthetic',water_path='synthetic',worldcover_path='synthetic',
                osm_manifest_path='synthetic',osm_samples_path='synthetic',scenario='nominal',
                crossing_proxy=.9,opt_in_mixed_experiment=True),'independent full-source validation gate')
            raises(ValueError,lambda:prepare_exercise_v4(strict,changed,changed_v3,changed_v4,parent,catalog,
                strict_pin=strict['sha256'],water_pin=changed['sha256'],v3_pin=changed_v3['sha256'],
                v4_pin=changed_v4['sha256'],parent_pin=parent['sha256'],catalog_pin=catalog['sha256'],
                road_path=road,road_pin=road_hash,coast_path='synthetic',water_path='synthetic',
                worldcover_path='synthetic',osm_manifest_path='synthetic',osm_samples_path='synthetic',
                dera_rivers_path='synthetic',scenario='nominal',crossing_proxy=.9,opt_in_mixed_experiment=True),
                'independent full-source validation gate')
            value=prepare_exercise_v4(strict,water,overlay,v4,parent,catalog,strict_pin=strict['sha256'],
                water_pin=water['sha256'],v3_pin=overlay['sha256'],v4_pin=v4['sha256'],
                parent_pin=parent['sha256'],catalog_pin=catalog['sha256'],road_path=road,road_pin=road_hash,
                coast_path='synthetic',water_path='synthetic',worldcover_path='synthetic',
                osm_manifest_path='synthetic',osm_samples_path='synthetic',dera_rivers_path='synthetic',
                scenario='nominal',crossing_proxy=.9,opt_in_mixed_experiment=True)
            assert value.contract.surface[3][4]=='mixed'
            assert value.contract.surface[1][1]=='unknown'  # extra DERA conflict stays censored
            drafts=build_drafts(catalog,parent['grid'],grid)
            config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
            if case=='weather_worker':
                from wildfire_expanded_incident_v1 import run_weather_v4 as runner
                runner.prepare_exercise_v4=lambda *a,**k:value
                runner.build_drafts=lambda *a,**k:drafts
                sys.argv.pop(1)
                runner.main()
                return
            if case=='weather_v4':
                def weather_host():
                    return WeatherMixedFireDiagnosticV1(value,drafts,expected_drafts_sha256=drafts['sha256'],
                        case_id=drafts['cases'][1]['id'],containment_config=config,pressure_scale=1,
                        opt_in_weather_fire=True)
                a=weather_host(); initial=a.situation()
                assert 'weather' not in initial and 'seed' not in initial
                profile=weather.WeatherProfile.from_records(drafts['cases'][1]['weather_schedule'])
                assert any(round(v,2)!=v for v in profile.records[0].wind)
                warm=weather.WeatherProfile.from_records([profile.records[0].as_dict()])
                expected=weather.initial_state(warm)
                for i in (1,2):
                    expected=weather.advance(warm,expected,i);a.step()
                    assert a._weather_state['moisture_proxy']==expected['moisture_proxy']
                    if i==1:
                        warm_saved=json.loads(json.dumps(a.checkpoint()))
                        warm_restored=weather_host();warm_restored.restore(warm_saved)
                    else:
                        assert warm_restored.step()==a.situation()
                        assert api._encoded(a.checkpoint())==api._encoded(warm_restored.checkpoint())
                assert a._weather_kind=='day' and a._weather_state['minute']==0
                for minute in (1,2):
                    row=weather.conditions(profile,minute);row.update(minute=0,received_at=0)
                    selected=weather.WeatherProfile.from_records([row])
                    prior=weather.initial_state(selected,moisture_proxy=expected['moisture_proxy'])
                    expected=weather.advance(selected,prior,1)
                    a.step();assert a._weather_state['moisture_proxy']==expected['moisture_proxy']
                saved=json.loads(json.dumps(a.checkpoint()))
                b=weather_host();b.restore(saved)
                for minute in range(3,10):
                    row=weather.conditions(profile,minute);row.update(minute=0,received_at=0)
                    selected=weather.WeatherProfile.from_records([row])
                    prior=weather.initial_state(selected,moisture_proxy=expected['moisture_proxy'])
                    expected=weather.advance(selected,prior,1)
                    assert a.step()==b.step()
                    assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
                    assert list(a._fire._fire.wind)==weather.conditions(profile,minute)['wind']
                    assert a._weather_state['moisture_proxy']==expected['moisture_proxy']
                assert a.situation()['minute']==9  # non-weather event did not halt fire-only mode
                raises(NotImplementedError,a.outcomes)
                tampered=copy.deepcopy(saved);tampered['weather']['minute']+=1
                raises(ValueError,lambda:b.restore(tampered))
                tampered=copy.deepcopy(saved);tampered['wind']=[0.,0.]
                tampered['sha256']=api._hash({k:v for k,v in tampered.items() if k!='sha256'})
                raises(ValueError,lambda:b.restore(tampered))
                b.restore(saved)
                # A synthetic unsupported ignition neighbourhood must censor
                # before either the weather or fire clock advances.
                bad=copy.deepcopy(drafts);badcase=bad['cases'][1];badcase['initial_fire']=[[2,2]]
                badcase['sha256']=api._hash({k:v for k,v in badcase.items() if k!='sha256'})
                bad['sha256']=api._hash({k:v for k,v in bad.items() if k!='sha256'})
                cold=WeatherMixedFireDiagnosticV1(value,bad,expected_drafts_sha256=bad['sha256'],
                    case_id=badcase['id'],containment_config=config,pressure_scale=1,opt_in_weather_fire=True)
                assert cold.situation()['status']=='censored' and cold.situation()['warmup_steps']==0
                cold_saved=json.loads(json.dumps(cold.checkpoint()))
                again=WeatherMixedFireDiagnosticV1(value,bad,expected_drafts_sha256=bad['sha256'],
                    case_id=badcase['id'],containment_config=config,pressure_scale=1,opt_in_weather_fire=True)
                again.restore(cold_saved)
                assert api._encoded(cold_saved)==api._encoded(again.checkpoint())
                # Force an actual one-step boundary ignition and verify that
                # weather and fire clocks both include the censored step.
                import random
                post=copy.deepcopy(drafts);postcase=post['cases'][1]
                postcase['initial_fire']=[[3,1]];postcase['warmup_minutes']=0
                postcase['sha256']=api._hash({k:v for k,v in postcase.items() if k!='sha256'})
                post['sha256']=api._hash({k:v for k,v in post.items() if k!='sha256'})
                def boundary_host():
                    return WeatherMixedFireDiagnosticV1(value,post,expected_drafts_sha256=post['sha256'],
                        case_id=postcase['id'],containment_config=config,pressure_scale=1,opt_in_weather_fire=True)
                boundary=boundary_host()
                row=weather.conditions(boundary._profile,1);row.update(minute=0,received_at=0)
                selected=weather.WeatherProfile.from_records([row]);prior=weather.initial_state(selected)
                next_state=weather.advance(selected,prior,1)
                boundary._fire._fire.wind=tuple(row['wind'])
                boundary._fire._fire.rules['spread_factor']=weather.spread_factor(next_state,base_factor=.5)
                boundary._fire._fire.cells[1][3].update(heat=1.,age=boundary._fire._fire.spread_interval(0,-1)-1)
                class ZeroRandom(random.Random):
                    def random(self):return 0.
                boundary._fire._fire.rng=ZeroRandom(5)
                assert boundary.step()['status']=='censored'
                assert boundary._minute==1 and boundary._weather_state['minute']==1
                assert boundary._fire.snapshot()['tick']==1
                after=json.loads(json.dumps(boundary.checkpoint()))
                restored=boundary_host();restored.restore(after)
                assert api._encoded(after)==api._encoded(restored.checkpoint())
                return
            a=SourceGroundedMixedDiagnosticV4(value,drafts,expected_drafts_sha256=drafts['sha256'],
                case_id=drafts['cases'][0]['id'],containment_config=config,pressure_scale=1,
                opt_in_mixed_experiment=True)
            assert a.situation()['status']=='active'
            a.advance();snapshot=json.loads(json.dumps(a.checkpoint()))
            b=SourceGroundedMixedDiagnosticV4(value,drafts,expected_drafts_sha256=drafts['sha256'],
                case_id=drafts['cases'][0]['id'],containment_config=config,pressure_scale=1,
                opt_in_mixed_experiment=True)
            b.restore(snapshot)
            assert a.advance(5)==b.advance(5)
            assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
        elif case=='reject_v4':
            v4=copy.deepcopy(overlay);v4['version']='extended-map-inland-mixed-exercise-v4'
            v4['sha256']=api._hash({k:v for k,v in v4.items() if k!='sha256'})
            raises(ValueError,lambda:prepare(v4));raises(ValueError,lambda:prepare(cross=1))
            raises(ValueError,lambda:prepare(opt=False))
        else:
            value=prepare()
            config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
            f=InteriorMixedFireV2(value,seed=1,containment_config=config,pressure_scale=1,
                                  wind=[1,0],opt_in_mixed_experiment=True)
            if case=='water_area':
                assert value.contract.surface[3][4]=='unknown'
                assert not f.change({'ignite':[[3,3]]})
            else:
                assert value.contract.surface[3][4]=='mixed'
                assert f._fire.cells[3][4]['initial_fuel']==pytest.approx(.9)
                assert f._fire._mixed_openness[(4,3)]==.9
                assert f.change({'ignite':[[3,3]]})
                saved=json.loads(json.dumps(f.snapshot()))
                g=InteriorMixedFireV2(value,seed=1,containment_config=config,pressure_scale=1,
                                      wind=[1,0],opt_in_mixed_experiment=True)
                g.restore(saved)
                assert api._encoded(saved)==api._encoded(g.snapshot())
                drafts=build_drafts(catalog,parent['grid'],grid)
                def host():
                    return SourceGroundedMixedDiagnosticV3(value,drafts,expected_drafts_sha256=drafts['sha256'],
                        case_id=drafts['cases'][0]['id'],containment_config=config,pressure_scale=1,
                        opt_in_mixed_experiment=True)
                a=host();assert a.situation()['warmup_steps']==1
                a.advance();snapshot=json.loads(json.dumps(a.checkpoint()))
                b=host();b.restore(snapshot)
                assert a.advance(5)==b.advance(5)
                assert a.situation()['status']=='blocked' and a.situation()['minute']==2
                assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())


if __name__=='__main__':run(sys.argv[1])
