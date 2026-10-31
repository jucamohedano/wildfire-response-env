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
def test_restricted_source_lifecycle():
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
    from wildfire_source_restricted_v1 import RestrictedSourceIncidentV1
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
    def factory():return RestrictedSourceIncidentV1(mixed,selector,parent,case_id=case_id,
        containment_config=config,pressure_scale=1,opt_in=True)
    a=factory()
    assert a._minute==0
    assert a._ops.groups
    before=a.checkpoint()
    a._known_wind=None
    assert a.situation()['wind'] is None
    a.restore(before,expected_checkpoint_sha256=api._hash(before))
    try:a.protect(next(iter(a._ops.groups)),'evacuate','destination')
    except ValueError:pass
    else:raise AssertionError('evacuation allowed without access')
    assert api._encoded(before)==api._encoded(a.checkpoint())
    result=a.advance(2)
    assert a._minute==2 and a._weather._minute==2 and a._ops.last_minute==2
    assert 'population' not in result and 'score' not in result
    b=factory();b.restore(before,expected_checkpoint_sha256=api._hash(before))
    assert b.advance(2)==result
    assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
    group=next(iter(a._ops.groups))
    for identifier in (group,'not-public'):
        try:a.protect(identifier,'shelter')
        except ValueError as error:assert str(error)=='population orders require an approved public roster'
        else:raise AssertionError('raw host disclosed roster membership')
    assert a._submit_shelter(group,'shelter')=={'status':'submitted'}
    assert b._submit_shelter(group,'shelter')=={'status':'submitted'}
    assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
    tape=a.transcript();fresh=factory()
    assert fresh.replay(tape,expected_sha256=api._hash(tape),expected_identity_sha256=tape['identity_sha256'],expected_final_checkpoint_sha256=api._hash(a.checkpoint()))==a.situation()
    # An automatic abort does not charge a stationary flight minute. Test
    # both lost clearance and a flight-window closure with exactly the fuel
    # needed for a one-minute return, including checkpoint continuation.
    for cause in ('clearance','window'):
        short=factory();short.clear_aircraft('plane-0')
        target=short.situation()['known_fire'][0]
        option=short.route_options('plane-0',target)['options'][0]
        short.dispatch('plane-0',target,option['id']);short.advance(2)
        unit=short._ops.resources['plane-0']
        assert unit['state']=='travel' and short._return_plan(unit,short._minute)['duration_minutes']==1
        unit['fuel_minutes']=1
        if cause=='clearance':unit['clearance']=False
        else:short._air_until=short._minute+2
        before_position=copy.deepcopy(unit['position'])
        saved=json.loads(json.dumps(short.checkpoint()))
        if cause=='clearance':
            old=copy.deepcopy(saved);old['version']='wildfire-source-restricted-incident-v1'
            old['sha256']=api._hash({k:v for k,v in old.items() if k!='sha256'})
            rejected=factory()
            try:rejected.restore(old,expected_checkpoint_sha256=api._hash(old))
            except ValueError:pass
            else:raise AssertionError('old host version accepted')
        continued=factory();continued.restore(saved,expected_checkpoint_sha256=api._hash(saved))
        assert short.advance()==continued.advance()
        unit=short._ops.resources['plane-0']
        assert unit['state']=='returning' and unit['position']==before_position and unit['fuel_minutes']==1
        assert short.advance()==continued.advance()
        unit=short._ops.resources['plane-0']
        assert unit['position']==unit['base_position'] and unit['fuel_minutes']==0
        assert unit['state']!='needs_assistance'
        assert api._encoded(short.checkpoint())==api._encoded(continued.checkpoint())
    # When the outbound plane is still at base, an abort with full tanks
    # must not create a spurious refill reservation or occupy its refill slot.
    grounded=factory();grounded.clear_aircraft('plane-0')
    target=grounded.situation()['known_fire'][0]
    option=grounded.route_options('plane-0',target)['options'][0]
    grounded.dispatch('plane-0',target,option['id']);grounded.advance()
    unit=grounded._ops.resources['plane-0']
    assert unit['state']=='travel' and unit['position']==unit['base_position']
    assert unit['fuel_minutes']==unit['fuel_capacity_minutes']
    # A second returning aircraft legitimately needs the same refill slot.
    other_id=next(key for key in grounded._ops.resources if key!='plane-0')
    other=grounded._ops.resources[other_id]
    other['position']=[other['base_position'][0]+100,other['base_position'][1]]
    other['state']='returning';other['water_litres']=other['capacity_litres']-1
    other['journey']=grounded._return_plan(other,grounded._minute)
    other['journey']['departed_at']=grounded._minute
    other['journey']['arrives_at']=grounded._minute+other['journey']['duration_minutes']
    unit['clearance']=False;grounded.advance()
    unit=grounded._ops.resources['plane-0']
    assert unit['state']=='available' and unit['fuel_minutes']==unit['fuel_capacity_minutes']
    assert grounded._ops.air_refill_active==other_id and grounded._ops.resources[other_id]['state']=='refilling'
    assert 'plane-0' not in grounded._ops.air_refill_queue and 'refill_reservation' not in unit
    # Replay an unmodified pinned scenario whose later modeled wind closes the
    # flight window during travel. No private fault injection is needed.
    windy=factory();windy.advance(60);windy.advance(60);windy.advance(50)
    windy.clear_aircraft('plane-0')
    target=windy.situation()['known_fire'][0]
    option=windy.route_options('plane-0',target)['options'][0]
    windy.dispatch('plane-0',target,option['id'])
    while windy._minute<179:windy.advance()
    prior=copy.deepcopy(windy._ops.resources['plane-0'])
    assert prior['state']=='travel'
    windy.advance();unit=windy._ops.resources['plane-0']
    assert unit['state']=='returning' and unit['position']==prior['position']
    assert unit['fuel_minutes']==prior['fuel_minutes']
    tape=windy.transcript();replayed=factory()
    assert replayed.replay(tape,expected_sha256=api._hash(tape),expected_identity_sha256=tape['identity_sha256'],
        expected_final_checkpoint_sha256=api._hash(windy.checkpoint()))==windy.situation()

if __name__ == '__main__':
    main()


def test_only_delivered_explicit_fire_reports_authorize_targets():
    from types import SimpleNamespace
    from envs.wildfire_response.incident_day.events import EventBus
    from wildfire_source_restricted_v1.host import RestrictedSourceIncidentV1
    host=object.__new__(RestrictedSourceIncidentV1)
    host._handover_fire=[[1,1]]
    host._inputs=SimpleNamespace(contract=SimpleNamespace(width=10,height=10,transform=[500,0,0,0,-500,0]))
    host._bus=EventBus(1,['center'])
    host._bus.emit('resident','incoming_report',{'text':'reported','category':'fire'},0,location=[2,2],delay=2)
    host._bus.emit('resident2','incoming_report',{'text':'other','category':None},0,location=[3,3],delay=0)
    host._bus.tick(0,{})
    assert host._reported_fire_targets()==[[1,1]]
    host._bus.tick(2,{})
    assert host._reported_fire_targets()==[[1,1],[2,2]]


def test_weather_profile_requires_initial_delivery():
    from wildfire_expanded_incident_v1.weather_fire_v1 import _profile
    row=dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0,received_at=1)
    with pytest.raises(ValueError):_profile(dict(weather_schedule=[row],wind=[0.,0.],events=[]))
