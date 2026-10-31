"""Synthetic source-like event slices; no claim of a complete source day."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_two_source_like_event_slices():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT)]))
    out=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    assert out.returncode==0,out.stdout+out.stderr


def main():
    import copy,json
    import adapter_v1 as api
    from wildfire_source_road_v1.timeline import EventTimelineV1
    def make_case(case_id,events,*,incoming=(),aircraft=False):
        records=[dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)]
        for event in events:
            if event['kind']=='wind':
                records.append(dict(minute=event['minute'],wind=event['wind'],temperature_c=20,
                    relative_humidity_pct=50,rain_mm_per_hour=0,received_at=event['minute']+2))
        c=dict(id=case_id,seed=7,synthetic=True,historical_replay=False,centers=['center-0','center-1'],
            sector_grid=[6,6],wind=[0.,0.],weather_schedule=records,events=copy.deepcopy(events),
            incoming_reports=list(incoming),resources=[dict(id='plane',kind='aircraft')] if aircraft else [],
            groups=[dict(id='g',count=5,position=[10.,20.],home=[10.,20.],status='unwarned',exposed=False)],
            destinations=[dict(id='d',capacity=10,position=[30.,40.],status='open')],profile={})
        c['sha256']=api._hash(c);return c
    grid=dict(width=32,height=32,crs='EPSG:25830',transform=[500,0,290000,0,-500,4064500])
    def host(c):return EventTimelineV1(c,expected_scenario_sha256=c['sha256'],grid=grid,
                                       expected_grid_sha256=api._hash(grid),opt_in=True)
    first=make_case('source-like-access',[
        dict(kind='access_restriction',minute=2,resource_kind='engine'),
        dict(kind='destination_closed',minute=3,destination_id='d'),
        dict(kind='wind',minute=4,wind=[1.,0.])],incoming=[dict(minute=1,source='caller',role='resident',channel='radio',
            location=[5,5],text='Synthetic observed smoke.',delay=3,category='true')])
    first['groups'][0].update(position=[30.,40.],status='arrived',destination_id='d')
    first['profile']['closure_affects_occupants']=True
    first['sha256']=api._hash({k:v for k,v in first.items() if k!='sha256'})
    a=host(first);b=host(first)
    assert a.situation()['restrictions']==[] and a.situation()['wind']==[0.,0.]
    a.advance(2)
    assert a.private_excluded_cells('engine')['cells'] and a.private_excluded_cells('crew')['cells']==[]
    assert a.situation()['restrictions']==[]  # restriction report has not been delivered
    saved=json.loads(json.dumps(a.checkpoint()));trusted_saved=api._hash(saved)
    b.restore(saved,expected_checkpoint_sha256=trusted_saved)
    # Rehashing an altered private state cannot replace the independent pin.
    forged=copy.deepcopy(saved);forged['restrictions']=[]
    forged['sha256']=api._hash({k:v for k,v in forged.items() if k!='sha256'})
    assert api._hash(forged)!=trusted_saved
    try:b.restore(forged,expected_checkpoint_sha256=trusted_saved)
    except ValueError:pass
    else:raise AssertionError('rehash removed an applied restriction')
    b.restore(saved,expected_checkpoint_sha256=trusted_saved)
    assert b.private_excluded_cells('engine')['cells']
    try:b.restore(saved)
    except TypeError:pass
    else:raise AssertionError('restore did not require an independent pin')
    assert a.advance()==b.advance()
    assert a._ops.groups['g']['position']==[30.,40.] and a._ops.groups['g']['status']=='blocked'  # no snapping or teleportation
    assert a._ops.destinations['d']['status']=='closed' and a.situation()['destinations'][0]['status']=='open'
    assert not any(r['kind']=='incoming_report' for r in a.reports(limit=200))
    assert a.advance()==b.advance()
    incoming=next(r for r in a.reports(limit=200) if r['kind']=='incoming_report')
    assert incoming['observed_at']==1 and incoming['received_at']==4
    assert incoming['location']==[5,5] and incoming['payload']['coordinate_frame']=='original_scenario_grid'
    assert 'category' not in json.dumps(incoming)
    assert a.situation()['wind']==[0.,0.]  # scheduled update not delivered until minute 6
    assert a.advance(5)==b.advance(5)
    assert a.situation()['wind']==[1.,0.] and a.situation()['restrictions']==['engine']
    assert a.situation()['destinations'][0]['status']=='closed'
    assert any(r['kind']=='relocation_required' and r['payload']['group_id']=='g' for r in a.reports(limit=200))
    assert api._encoded(a.checkpoint())==api._encoded(b.checkpoint())
    replay=host(first);transcript=a.transcript()
    assert replay.replay(transcript,expected_sha256=api._hash(transcript),
        expected_identity_sha256=transcript['identity_sha256'])==a.situation()
    assert api._encoded(replay.checkpoint())==api._encoded(a.checkpoint())
    assert 'events' not in json.dumps(a.situation()) and 'weather_schedule' not in json.dumps(a.situation())
    # Second source-like event sequence: center service is unavailable while
    # its request is serviced, then an unimplemented aircraft event stops before
    # its minute rather than being silently ignored.
    second=make_case('source-like-center',[dict(kind='center_unavailable',minute=2,center_id='center-1',until=50),
        dict(kind='aircraft_unavailable',minute=20,until=30)])
    c=host(second);c.advance(2);c.request_status('center-1')
    while c.situation()['status']=='active':c.advance()
    assert c.situation()['status']=='unsupported' and c.situation()['minute']==19
    assert c.situation()['reason']=='unsupported_scheduled_event:aircraft_unavailable'
    assert any(r['kind']=='status_response' and r['payload']['available'] is False for r in c.reports(limit=200))
    cp=json.loads(json.dumps(c.checkpoint()));d=host(second);d.restore(cp,expected_checkpoint_sha256=api._hash(cp))
    assert api._encoded(cp)==api._encoded(d.checkpoint())
    # Always exercise the unsupported-aircraft checkpoint boundary, even on
    # machines without the private source catalog.
    blocked=host(make_case('synthetic-aircraft',[],aircraft=True))
    assert blocked.situation()['status']=='unsupported'
    trusted=json.loads(json.dumps(blocked.checkpoint()));pin=api._hash(trusted)
    forged=copy.deepcopy(trusted);forged['status']='active';forged['reason']=None
    forged['sha256']=api._hash({k:v for k,v in forged.items() if k!='sha256'})
    try:blocked.restore(forged,expected_checkpoint_sha256=pin)
    except ValueError:pass
    else:raise AssertionError('forged aircraft reactivation accepted')
    blocked.restore(trusted,expected_checkpoint_sha256=pin)
    assert blocked.situation()['status']=='unsupported'

    # The actual source draft includes aircraft at handover. The component
    # explicitly stops rather than quietly dropping them or claiming a full case.
    anchored_path=Path('/tmp/expanded-post-anchors-v4/scenario-drafts.json')
    if anchored_path.exists():
        cases=json.loads(anchored_path.read_bytes())['cases']
        for index in (1,8):
            real=host(cases[index])
            assert real.situation()['status']=='unsupported' and real.situation()['minute']==0
            assert real.situation()['reason']=='aircraft_roster_not_implemented'
            trusted_aircraft=json.loads(json.dumps(real.checkpoint()))
            trusted_aircraft_digest=api._hash(trusted_aircraft)
            forged=copy.deepcopy(trusted_aircraft);forged['status']='active';forged['reason']=None
            forged['sha256']=api._hash({k:v for k,v in forged.items() if k!='sha256'})
            try:real.restore(forged,expected_checkpoint_sha256=trusted_aircraft_digest)
            except ValueError:pass
            else:raise AssertionError('rehash reactivated an unsupported aircraft scenario')
            real.restore(trusted_aircraft,expected_checkpoint_sha256=trusted_aircraft_digest)
            try:real.private_excluded_cells('engine')
            except RuntimeError:pass
            else:raise AssertionError('stopped timeline authorized routing')
            try:real.advance()
            except RuntimeError:pass
            else:raise AssertionError('aircraft silently ignored')


if __name__=='__main__':main()
