"""Failed hosts retain their last durable state and cannot silently restart."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_v4_checkpoint_failure_retains_durable_state():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'eval'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
    assert p.returncode==0,p.stdout+p.stderr


def run():
    import copy
    import pytest
    from eval.run_domain_profile_weather_v4 import checkpoint_records,initialize_entries,_resume_matches,VERSION,LEGACY_RUNNERS
    class Host:
        def __init__(self,tick,fail=False):self.tick=tick;self.fail=fail
        def situation(self):
            if self.fail:raise RuntimeError('injected failure')
            return {'status':'active','warmup_steps':0,'minute':self.tick}
        def checkpoint(self):return {'tick':self.tick}
    cases=[{'id':'a'},{'id':'b'}]
    entries=[dict(case=cases[0],host=Host(5),error=None),dict(case=cases[1],host=Host(7),error=None)]
    first,_=checkpoint_records(entries,{})
    durable={row['case']:copy.deepcopy(row) for row in first}
    entries[0]['host']=Host(9,fail=True);entries[1]['host']=Host(11)
    second,_=checkpoint_records(entries,durable)
    assert second[0]['status']=='terminal_error' and second[0]['error']['kind']=='RuntimeError'
    assert second[0]['checkpoint']=={'tick':5} and second[1]['checkpoint']=={'tick':11}
    assert durable['a']['checkpoint']=={'tick':5} and durable['b']['checkpoint']=={'tick':7}
    # A restore error aborts before writing, and retry restores the same snapshots.
    original=copy.deepcopy(durable)
    class Restorer:
        def __init__(self,fail=False):self.fail=fail;self.restored=None
        def restore(self,snapshot):
            if self.fail:raise RuntimeError('restore failure')
            self.restored=copy.deepcopy(snapshot)
    with pytest.raises(RuntimeError,match='original checkpoint preserved'):
        initialize_entries(cases,durable,lambda case:Restorer(case['id']=='b'))
    assert durable==original
    retried=initialize_entries(cases,durable,lambda case:Restorer())
    assert [item['host'].restored for item in retried]==[{'tick':5},{'tick':7}]
    saved={row['case']:row for row in second}
    with pytest.raises(ValueError,match='terminal-error'):
        initialize_entries(cases,saved,lambda case:Host(0))
    # A failure before any durable snapshot is explicit rather than silently restarting.
    empty=[dict(case={'id':'c'},host=None,error={'kind':'Error','detail':'init'})]
    records,_=checkpoint_records(empty,{})
    assert records[0]['status']=='terminal_error' and records[0]['checkpoint'] is None
    current={'version':VERSION,'runner_sha256':'new','input':'same'}
    assert LEGACY_RUNNERS=={}
    assert not _resume_matches({'version':'wildfire-domain-profile-weather-diagnostic-run-v3','identity':current},current)
    import signal, tempfile
    from wildfire_expanded_incident_v1.private_output import read_private_json
    for kind in ('situation','step'):
        with tempfile.TemporaryDirectory(prefix='wildfire-checkpoint-kill-') as directory:
            path=Path(directory)/'checkpoint.json'
            child=subprocess.run([sys.executable,__file__,'--kill-child',kind,str(path)],
                                 capture_output=True,text=True,timeout=20)
            assert child.returncode==-signal.SIGKILL,child.stdout+child.stderr
            record=read_private_json(path)
            assert record['cases'][0]['status']=='terminal_error'
            assert record['cases'][0]['error']['kind']=='RuntimeError'
            assert record['cases'][0]['checkpoint']=={'tick':5}
            assert record['cases'][1]['checkpoint']=={'tick':7}
            with pytest.raises(ValueError,match='terminal-error'):
                initialize_entries(cases,{r['case']:r for r in record['cases']},lambda case:Host(0))
    for version,pin in LEGACY_RUNNERS.items():
        saved={'version':version,'identity':{'version':version,'runner_sha256':pin,'input':'same'}}
        assert _resume_matches(saved,current)
        saved['identity']['runner_sha256']='changed'
        assert not _resume_matches(saved,current)

def kill_child(kind,path):
    import signal
    from eval.run_domain_profile_weather_v4 import advance_round,checkpoint_records
    from wildfire_expanded_incident_v1.private_output import write_atomic_private_json
    class Host:
        def __init__(self,tick,fail):self.tick=tick;self.fail=fail
        def situation(self):
            if self.fail=='situation':raise RuntimeError('failure before checkpoint interval')
            return {'status':'active','warmup_steps':0,'minute':self.tick}
        def checkpoint(self):return {'tick':self.tick}
        def step(self):
            if self.fail=='step':raise RuntimeError('failure before checkpoint interval')
            self.tick+=1
    durable={'a':{'case':'a','checkpoint':{'tick':5},'status':'active','error':None},
             'b':{'case':'b','checkpoint':{'tick':7},'status':'active','error':None}}
    entries=[{'case':{'id':'a'},'host':Host(5,kind),'error':None},
             {'case':{'id':'b'},'host':Host(7,None),'error':None}]
    identity=write_atomic_private_json(path,{'cases':list(durable.values())})
    def save_and_kill():
        records,_=checkpoint_records(entries,durable)
        write_atomic_private_json(path,{'cases':records},identity)
        os.kill(os.getpid(),signal.SIGKILL)
    advance_round(entries,{'signal':None},10,120,save_and_kill,clock=lambda:0)
    raise AssertionError('failure did not checkpoint immediately')


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--kill-child':kill_child(sys.argv[2],sys.argv[3])
    else:run()
