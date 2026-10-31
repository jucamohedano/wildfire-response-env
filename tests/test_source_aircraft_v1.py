"""Aircraft lifecycle with real pinned synthetic roster and synthetic fire support."""
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
PATHS=[Path('/tmp/expanded-post-anchors-v4/network.json'),Path('/tmp/expanded-post-anchors-v4/scenario-drafts.json'),
       Path('/tmp/wildfire-eval-v1-final2-20261030/catalog.json'),
       Path('/tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json'),
       Path('/tmp/wildfire-expanded-source-diagnostic-v1-20261030/drafts.json'),
       Path('/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz')]


@pytest.mark.skipif(not all(p.exists() for p in PATHS),reason='private pinned source lineage unavailable')
def test_source_aircraft_lifecycle():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    result=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=240)
    assert result.returncode==0,result.stdout+result.stderr


def main():
    import copy,json
    import adapter_v1 as api
    from envs.wildfire_response.regional.ops.generator import digest
    from eval.expanded_road_runtime_v1 import load_candidate
    from eval.expanded_guarded_routes_v1 import REVIEWED_PAIR_MANIFEST
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    from wildfire_source_road_v1.catalog import SourceCaseCatalogV1
    from wildfire_source_air_v1 import SourceAircraftV1
    graph,anchored,original,parent,fire=(json.loads(p.read_bytes()) for p in PATHS[:5])
    source=load_candidate();g=fire['grid'];w,h=g['width'],g['height']
    doc=dict(version='wildfire-extended-inputs-v1',grid=copy.deepcopy(g),
        domain_mask=[[True]*w for _ in range(h)],surface=[['land']*w for _ in range(h)],
        fuel=[[4]*w for _ in range(h)],elevation=[[100.]*w for _ in range(h)],
        access=[['unknown']*w for _ in range(h)],
        source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
    ident=dict(version='wildfire-small-water-mixed-exercise-input-v4',input_sha256=contract.document_sha256,
        catalog=original['sha256'],parent=parent['sha256'],road=REVIEWED_PAIR_MANIFEST['source_compressed_sha256'])
    # Test-only synthetic all-land fire support. The aircraft roster and its
    # scenario/profile are selected from the real, pinned synthetic catalog.
    mixed=MixedInputsV2(contract,(),tuple(sorted(ident.items())),_VERIFIED)
    selector=SourceCaseCatalogV1(mixed,original,parent,anchored,fire,graph,source,
        expected_original_sha256=original['sha256'],expected_parent_sha256=parent['sha256'],
        expected_anchored_sha256=anchored['sha256'],expected_fire_drafts_sha256=fire['sha256'],
        expected_graph_sha256=graph['sha256'],expected_source_sha256=digest(source),opt_in=True)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    case_id=selector.case_ids()[5]
    def factory():return SourceAircraftV1(mixed,selector,parent,case_id=case_id,
        containment_config=config,pressure_scale=1,opt_in=True)
    a=factory();assert a.situation()['status']=='active' and a._weather._warmup_done==60
    assert 'aircraft-only' in a.situation()['scope'] and len(a.situation()['resources'])==2
    truth=a._fire.burning();known=a.situation()['known_fire']
    assert len(known)==max(1,len(truth)//2) and set(map(tuple,known))<=set(map(tuple,truth))
    assert next(r for r in a.reports(limit=200) if r['kind']=='handover')['payload']['cells']==known
    initial=json.loads(json.dumps(a.checkpoint()));target=a.situation()['known_fire'][0]
    option=a.route_options('plane-0',target)['options'][0]
    # Clearance is required. The dispatch request is submitted, while denial is delayed.
    assert a.dispatch('plane-0',target,option['id'])=={'status':'submitted'}
    assert a._ops.resources['plane-0']['state']=='available'
    assert not any(r['kind']=='dispatch_result' for r in a.reports(limit=200))
    a.advance(6)
    assert any(r['kind']=='dispatch_result' and not r['payload']['accepted'] for r in a.reports(limit=200))
    assert a.clear_aircraft('plane-0')=={'status':'submitted'}
    option=a.route_options('plane-0',target)['options'][0]
    a.dispatch('plane-0',target,option['id'])
    assert a._ops.resources['plane-0']['state']=='mobilizing', (a._ops.resources['plane-0'], a._ops.air_window_open, a._minute)
    a.advance(2)
    assert a._ops.resources['plane-0']['state']=='travel'
    mid=json.loads(json.dumps(a.checkpoint()));b=factory();b.restore(mid,expected_checkpoint_sha256=api._hash(mid))
    states=set()
    for _ in range(80):
        assert a.advance()==b.advance()
        unit=a._ops.resources['plane-0'];states.add(unit['state'])
        if a._drop_audit:break
    assert a._drop_audit and a._drop_audit[0]['before']>a._drop_audit[0]['after']
    unit=a._ops.resources['plane-0']
    assert unit['water_litres']==0 and unit['work_remaining']==239 and unit['fuel_minutes']<180
    assert not any(r['kind']=='drop_completed' for r in a.reports(limit=200))
    for _ in range(80):
        assert a.advance()==b.advance()
        unit=a._ops.resources['plane-0'];states.add(unit['state'])
        if unit['state']=='available':break
    assert unit['state']=='available' and unit['water_litres']==3100 and unit['fuel_minutes']==180
    assert 'returning' in states and ('air_refill_queue' in states or 'refilling' in states)
    assert a._ops.profile['facilities']['landload-base']['water_litres']==100000-3100
    assert any(r['kind']=='drop_completed' for r in a.reports(limit=200))
    assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
    # Runtime source bindings are checked, including an in-memory solar-clock
    # replacement even when the on-disk file itself is unchanged.
    from unittest.mock import patch
    from wildfire_source_air_v1 import host as air_module
    trusted=json.loads(json.dumps(a.checkpoint()))
    with patch.object(air_module.clock_module,'solar_daylight',lambda *args:True):
        try:a.situation()
        except RuntimeError:pass
        else:raise AssertionError('replaced solar clock accepted')
        try:a.restore(trusted,expected_checkpoint_sha256=api._hash(trusted))
        except RuntimeError:pass
        else:raise AssertionError('checkpoint accepted replaced solar clock')
    a.restore(trusted,expected_checkpoint_sha256=api._hash(trusted))
    public=json.dumps(a.situation())+json.dumps(a.reports(limit=200))
    assert 'drop_audit' not in public and 'moisture_proxy' not in public and 'weather_schedule' not in public
    tape=a.transcript();end_hash=api._hash(a.checkpoint())
    b.restore(initial,expected_checkpoint_sha256=api._hash(initial))
    assert b.replay(tape,expected_sha256=api._hash(tape),expected_identity_sha256=tape['identity_sha256'],
                    expected_final_checkpoint_sha256=end_hash)==a.situation()
    assert api._hash(b.checkpoint())==end_hash
    # A recomputed embedded hash cannot replace the separately trusted digest.
    altered=copy.deepcopy(mid);altered['operations']['resources']['plane-0']['fuel_minutes']=180
    altered['sha256']=api._hash({k:v for k,v in altered.items() if k!='sha256'})
    try:b.restore(altered,expected_checkpoint_sha256=api._hash(mid))
    except ValueError:pass
    else:raise AssertionError('rehashed aircraft checkpoint accepted')
    b.restore(mid,expected_checkpoint_sha256=api._hash(mid))
    # Weather/daylight and resource availability are the real Ops decisions.
    night_id=selector.case_ids()[0]
    night=SourceAircraftV1(mixed,selector,parent,case_id=night_id,containment_config=config,pressure_scale=1,opt_in=True)
    night_target=night.situation()['known_fire'][0]
    night.clear_aircraft('plane-0')
    night_opt=night.route_options('plane-0',night_target)['options'][0]
    night.dispatch('plane-0',night_target,night_opt['id'])
    assert night._ops.resources['plane-0']['state']=='available'
    night.advance(6)
    assert any(r['kind']=='dispatch_result' and not r['payload']['accepted'] for r in night.reports(limit=200))

    # A release consumes exactly one return-flight minute and moves in that
    # same tick. This reproduces the review's 177 -> 176 in-flight case.
    releasing=factory();releasing.clear_aircraft('plane-0')
    target=releasing.situation()['known_fire'][0];option=releasing.route_options('plane-0',target)['options'][0]
    releasing.dispatch('plane-0',target,option['id']);releasing.advance(4)
    prior=copy.deepcopy(releasing._ops.resources['plane-0'])
    assert prior['state']=='travel' and prior['fuel_minutes']==177
    releasing.release('plane-0');after=releasing._ops.resources['plane-0']
    assert after['fuel_minutes']==176 and after['position']!=prior['position']
    assert after['journey']['departed_at']==releasing._minute-1

    # A one-minute recovery with exactly one minute of remaining fuel reaches
    # the base rather than exhausting it while stationary. The finite budget is
    # a controlled test-only injection, not a source roster claim.
    short=factory();short.clear_aircraft('plane-0')
    target=short.situation()['known_fire'][0];option=short.route_options('plane-0',target)['options'][0]
    short.dispatch('plane-0',target,option['id']);short.advance(2)
    unit=short._ops.resources['plane-0'];assert unit['state']=='travel'
    assert short._return_plan(unit,short._minute)['duration_minutes']==1
    unit['fuel_minutes']=1
    saved_short=json.loads(json.dumps(short.checkpoint()))
    resumed_short=factory();resumed_short.restore(saved_short,expected_checkpoint_sha256=api._hash(saved_short))
    assert short.release('plane-0')==resumed_short.release('plane-0')
    unit=short._ops.resources['plane-0']
    assert unit['position']==unit['base_position'] and unit['fuel_minutes']==0
    assert unit['state']=='refilling' and unit['state']!='needs_assistance'
    assert api._encoded(short.checkpoint())==api._encoded(resumed_short.checkpoint())
    short.advance();assert short._ops.resources['plane-0']['fuel_minutes']==0

    # A synthetic low-fuel fault verifies the pinned Operations preflight does
    # not authorize an outbound sortie without enough recovery fuel.
    low=factory();low._ops.resources['plane-0']['fuel_minutes']=1
    low.clear_aircraft('plane-0')
    target=low.situation()['known_fire'][0];option=low.route_options('plane-0',target)['options'][0]
    low.dispatch('plane-0',target,option['id'])
    assert low._ops.resources['plane-0']['state']=='available' and not low._drop_audit
    low.advance(6)
    assert any(r['kind']=='dispatch_result' and not r['payload']['accepted'] for r in low.reports(limit=200))

    # A real post-step boundary censor stops all observations and restores the
    # joint aircraft/communications/fire state from an independent pin.
    import random
    from eval import incident_weather_v1 as weather
    class ZeroRandom(random.Random):
        def random(self): return 0.
    boundary=factory();boundary.clear_aircraft('plane-0')
    target=boundary.situation()['known_fire'][0];option=boundary.route_options('plane-0',target)['options'][0]
    boundary.dispatch('plane-0',target,option['id'])
    assert boundary._fire.change({'ignite':[[1,1]]})
    row=weather.conditions(boundary._weather._profile,boundary._minute+1);row.update(minute=0,received_at=0)
    profile=weather.WeatherProfile.from_records([row])
    prior=weather.initial_state(profile,moisture_proxy=boundary._weather._weather_state['moisture_proxy'])
    f=boundary._fire._fire;f.wind=tuple(row['wind'])
    f.rules['spread_factor']=weather.spread_factor(weather.advance(profile,prior,1),base_factor=.5)
    f.cells[1][1].update(heat=1.,age=f.spread_interval(0,-1)-1);f.rng=ZeroRandom(7)
    assert boundary.advance()['status']=='censored'
    assert set(boundary.situation())=={'version','status','minute'}
    assert boundary._ops.last_minute==boundary._minute
    try:boundary.reports()
    except api.CensoredError:pass
    else:raise AssertionError('censored aircraft reports leaked')
    snapshot=json.loads(json.dumps(boundary.checkpoint()))
    b.restore(snapshot,expected_checkpoint_sha256=api._hash(snapshot))
    assert api._encoded(snapshot)==api._encoded(b.checkpoint())

    # The real synthetic case's forecast changes at minute 180 to a wind
    # above the pinned aircraft limit; Operations aborts an in-flight sortie.
    windy=factory();windy.advance(60);windy.advance(60);windy.advance(50)
    assert windy._minute==170 and windy.situation()['status']=='active'
    windy.clear_aircraft('plane-0')
    target=windy.situation()['known_fire'][0];option=windy.route_options('plane-0',target)['options'][0]
    windy.dispatch('plane-0',target,option['id'])
    while windy._minute<180:windy.advance()
    assert windy._ops.air_window_open is False
    assert windy._ops.resources['plane-0']['state']=='returning'
    assert not windy._ops.resources['plane-0']['clearance'] and not windy._drop_audit
    windy.advance(6)
    assert any(r['kind']=='aircraft_aborted' for r in windy.reports(limit=200))
    # The actual minute-300 synthetic aircraft outage is applied, not ignored.
    while windy._minute<300:
        windy.advance(min(60,300-windy._minute))
        assert windy.situation()['status']=='active'
    assert windy._air_until==480
    windy.advance(6)
    assert any(r['kind']=='aircraft_window' and r['payload']['until']==480 for r in windy.reports(limit=200))

    # Unsupported ground restriction is not dropped when its source event comes due.
    restricted=SourceAircraftV1(mixed,selector,parent,case_id=selector.case_ids()[4],
        containment_config=config,pressure_scale=1,opt_in=True)
    restricted.advance(60);restricted.advance(29)
    assert restricted._minute==89 and restricted.situation()['status']=='active'
    assert restricted.advance()['status']=='unsupported'
    assert restricted._minute==89 and restricted.situation()['reason']=='unsupported_scheduled_event:access_restriction'


if __name__=='__main__':main()
