"""Synthetic exercise of the saved batch's budget and trusted resume path."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys
import types
import pytest

ROOT=Path(__file__).resolve().parents[1]
RUNNER=ROOT/'eval/run_component_12case_v2.py'


def test_bounded_batch_resumes_from_trusted_snapshot(tmp_path, monkeypatch):
    # Execute the runner body with synthetic owners and inputs; no private data.
    root=tmp_path/'private';root.mkdir();maps=tmp_path/'maps';maps.mkdir()
    for name,value in {
      'domain-built-water-estimated-profile-v1.json':{'sha256':'profile'},
      'domain-built-water-profile-v2.json':{},'domain-inland-low-water-profile-v1.json':{},
      'resolution-v1.json':{},'resolution-v2.json':{},'parent.json':{'sha256':'parent','grid':{}},
      'catalog.json':{'sha256':'catalog'}}.items():
        (maps/name).write_text(json.dumps(value))
    source=RUNNER.read_text();body=source[source.index('R=Path('):]
    body=body.replace('/tmp/wildfire-component-12case-v2-20261031',str(root))
    body=body.replace('/tmp/wildfire-map-resolution-private',str(maps))
    body=body.replace('/tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json',str(maps/'parent.json'))
    body=body.replace('/tmp/wildfire-eval-v1-final2-20261030/catalog.json',str(maps/'catalog.json'))
    body=body.replace('/tmp/wildfire-issue60/eval/domain_built_water_estimated_policy_v2.py',str(ROOT/'eval/domain_built_water_estimated_policy_v2.py'))
    def hash_value(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    api=types.SimpleNamespace(_hash=hash_value)
    cases=[{'id':str(i),'initial_fire':[[0,0]]} for i in range(12)]
    class Owner:
        def __init__(self,*a,case_id,**kw):self.case_id=case_id;self.minute=0;self._reason=None
        def situation(self):return {'status':'diagnostic_end' if self.minute>=2 else 'active','minute':self.minute,'warmup_steps':0}
        def checkpoint(self):
            cp={'case':self.case_id,'minute':self.minute};cp['sha256']=hash_value(cp);return cp
        def restore(self,cp,*,expected_checkpoint_sha256):
            assert cp['case']==self.case_id and cp['sha256']==expected_checkpoint_sha256
            assert hash_value({k:v for k,v in cp.items() if k!='sha256'})==expected_checkpoint_sha256
            self.minute=cp['minute']
    def bounded(owner,**kw):owner.minute+=1;return {'steps':1}
    def read_private(path,**kw):return json.loads(Path(path).read_text())
    def write_private(path,value):
        assert not Path(path).exists();Path(path).write_text(json.dumps(value))
    def atomic(path,value,expected_identity=None):
        path=Path(path)
        if expected_identity is None:assert not path.exists()
        else:assert (path.stat().st_dev,path.stat().st_ino)==expected_identity
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value));temp.replace(path)
        return path.stat().st_dev,path.stat().st_ino
    class Clock:
        def __init__(self):self.now=0
        def monotonic(self):self.now+=.04;return self.now
        def process_time(self):return self.now
    def invoke(arguments):
        monkeypatch.setattr(sys,'argv',['runner',*arguments]);clock=Clock()
        namespace=dict(__file__=str(RUNNER),Path=Path,argparse=argparse,collections=collections,hashlib=hashlib,
          json=json,os=__import__('os'),time=clock,api=api,digest=hash_value,
          build_drafts=lambda *a:{'cases':cases,'sha256':'drafts'},
          build_policy=lambda *a:({}, {}, {'sha256':'policy'}),
          adapt=lambda *a,**kw:{'grid':{},'identity':{},'private_audit':{}},
          FreshBuiltWaterEstimatedWeatherV1=Owner,run_bounded=bounded,read_private_json=read_private,
          write_private_json=write_private,write_atomic_private_json=atomic)
        exec(compile(body,str(RUNNER),'exec'),namespace)
    invoke(['--wall-seconds','1'])
    partial=read_private(root/'progress.json');pin=partial['sha256']
    assert partial['last_stop']=='wall_budget' and any(c['status']=='active' for c in partial['cases'])
    assert any(c['status']=='unstarted' for c in partial['cases'])
    with pytest.raises(ValueError,match='trusted resume digest'):
        invoke(['--wall-seconds','30','--resume-sha256','0'*64])
    invoke(['--wall-seconds','30','--resume-sha256',pin])
    finished=read_private(root/'progress.json')
    assert finished['last_stop']=='all_terminal'
    assert len(finished['cases'])==12 and all(c['status']=='diagnostic_end' for c in finished['cases'])
    assert all(c['restore_verified'] and c['minute']==2 for c in finished['cases'])
