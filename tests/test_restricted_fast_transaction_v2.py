"""Hard-kill recovery, bytecode avoidance and runner pin regression tests."""
import hashlib
import os
from pathlib import Path
import py_compile
import signal
import shutil
import subprocess
import sys
import time

import pytest

from eval import restricted_fast_transaction_v2 as tx

ROOT=Path(__file__).resolve().parents[1]


def test_kill_after_initial_authenticated_publish_before_progress_creation(tmp_path):
    directory=tmp_path/'run';directory.mkdir(mode=0o700)
    key=directory/'continuation.key';key.write_bytes(b'K'*32);key.chmod(0o600)
    marker=directory/'marker'
    code=r'''
from pathlib import Path
import sys,time
from eval import restricted_fast_transaction_v2 as tx
directory=Path(sys.argv[1])
def pause(*args):
    (directory/'marker').write_text('ready')
    while True:time.sleep(1)
tx.restore=pause
tx.initialize(directory,{'runner':'a'*64},'test-version',b'initial-handover')
'''
    env=dict(os.environ,PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
    process=subprocess.Popen([sys.executable,'-c',code,str(directory)],cwd=ROOT,env=env)
    try:
        limit=time.monotonic()+20
        while not marker.exists() and time.monotonic()<limit:
            if process.poll() is not None:raise AssertionError('child exited before gap')
            time.sleep(.02)
        assert marker.exists() and (directory/'checkpoint.auth').exists()
        assert not (directory/'progress.json').exists()
        process.send_signal(signal.SIGKILL);process.wait(timeout=10)
        tx.restore(directory,{'runner':'a'*64},'test-version')
        assert (directory/'progress.json').read_bytes()==b'initial-handover'
    finally:
        if process.poll() is None:process.kill();process.wait()


def test_killed_launcher_resumes_initial_authenticated_handover(tmp_path):
    root=tmp_path/'clone';root.mkdir()
    for name in ('envs','eval'):
        shutil.copytree(ROOT/name,root/name,symlinks=True,ignore=shutil.ignore_patterns('__pycache__'))
    private=tmp_path/'private';private.mkdir(mode=0o700)
    code=r'''
from argparse import Namespace
from pathlib import Path
import hashlib,json,sys,time,types
root=Path(sys.argv[1]);private=Path(sys.argv[2]);initial=sys.argv[3]=='initial'
raw=(root/'eval/restricted_fast_source_bootstrap_v1.py').read_bytes()
tracker=types.ModuleType('wildfire_verified_source');exec(compile(raw,str(root/'eval/restricted_fast_source_bootstrap_v1.py'),'exec'),tracker.__dict__)
sys.modules[tracker.__name__]=tracker;tracker.install(root)
sys.path[:0]=[str(p) for p in (root,root/'envs',root/'envs/wildfire_extended_inputs_v1',root/'envs/wildfire_response/core/src',root/'envs/wildfire_containment_v1/src',root/'envs/wildfire_containment_score_v1/src')]
from eval import run_source_restricted_fast_transaction_v2 as launcher
from eval import run_source_restricted_private_v1 as base
from eval import restricted_fast_transaction_v2 as tx
import adapter_v1 as api
import wildfire_source_restricted_v1 as restricted
class Selector:
    def case_ids(self):return ['case-'+str(i) for i in range(12)]
class Host:
    def __init__(self,inputs,selector,parent,*,case_id,**kwargs):
        if case_id!='case-0':raise ValueError('blocked fixture')
        self.minute=0;self.status='active'
    def situation(self):return {'minute':self.minute,'status':self.status}
    def advance(self,n):self.minute+=n;return self.situation()
    def checkpoint(self):return {'minute':self.minute,'status':self.status}
    def restore(self,value,*,expected_checkpoint_sha256):
        assert api._hash(value)==expected_checkpoint_sha256
        self.minute=value['minute'];self.status=value['status']
restricted.RestrictedSourceIncidentV1=Host;base.prepare_inputs=lambda:(None,Selector(),None)
pins={k:launcher._sha(v) for k,v in launcher._files('minute').items()}
manifest=launcher._preverified_manifest(launcher._files('minute'),pins)
pins['source-manifest']=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
if initial:
    def pause(*args):
        (private/'marker').write_text('ready')
        while True:time.sleep(1)
    tx.restore=pause
launcher._worker(Namespace(mode='minute',run_dir=private,resume=not initial,wall_seconds=20,max_decisions=1),pins)
'''
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PROJ_NETWORK='OFF',
        WILDFIRE_FAST_DIGEST_PARENT_V1=str(os.getpid()),WILDFIRE_FAST_DIGEST_TOKEN_V1='test-child')
    process=subprocess.Popen([sys.executable,'-I','-c',code,str(root),str(private),'initial'],
        cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    try:
        marker=private/'marker';limit=time.monotonic()+30
        while not marker.exists() and time.monotonic()<limit:
            if process.poll() is not None:raise AssertionError(process.stderr.read())
            time.sleep(.02)
        assert marker.exists() and (private/'checkpoint.auth').exists()
        assert not (private/'progress.json').exists()
        process.kill();process.wait(timeout=10)
        resumed=subprocess.run([sys.executable,'-I','-c',code,str(root),str(private),'resume'],
            cwd=root,env=env,capture_output=True,text=True,timeout=120)
        assert resumed.returncode==0,resumed.stderr
        import json
        assert json.loads((private/'progress.json').read_bytes())['cases'][0]['minute']==1
    finally:
        if process.poll() is None:process.kill();process.wait()


def test_kill_between_progress_and_authenticated_commit_recovers_previous(tmp_path):
    directory=tmp_path/'run';directory.mkdir(mode=0o700)
    key=directory/'continuation.key';key.write_bytes(b'K'*32);key.chmod(0o600)
    progress=directory/'progress.json';progress.write_bytes(b'previous');progress.chmod(0o600)
    pins={'runner':'a'*64};tx.commit(directory,pins,'test-version')
    marker=directory/'marker'
    code=r'''
from pathlib import Path
import sys,time
from eval import restricted_fast_transaction_v2 as tx
directory=Path(sys.argv[1]);marker=directory/'marker'
def original(path,report,continuations):
    path.write_bytes(b'uncommitted');path.chmod(0o600)
def pause(*args):
    marker.write_text('ready')
    while True:time.sleep(1)
tx.commit=pause
tx.saved(directory/'progress.json',{}, {}, original=original,guard=lambda:None,pins={'runner':'a'*64},version='test-version')
'''
    env=dict(os.environ,PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
    process=subprocess.Popen([sys.executable,'-c',code,str(directory)],cwd=ROOT,env=env)
    try:
        limit=time.monotonic()+20
        while not marker.exists() and time.monotonic()<limit:
            if process.poll() is not None:raise AssertionError('child exited before gap')
            time.sleep(.02)
        assert marker.exists() and progress.read_bytes()==b'uncommitted'
        process.send_signal(signal.SIGKILL);process.wait(timeout=10)
        tx.restore(directory,pins,'test-version')
        assert progress.read_bytes()==b'previous'
        bundle=directory/'checkpoint.auth';data=bundle.read_bytes()
        bundle.write_bytes(data[:-1]+bytes([data[-1]^1]))
        with pytest.raises(ValueError,match='authentication'):
            tx.restore(directory,pins,'test-version')
        assert progress.read_bytes()==b'previous'
        bundle.write_bytes(data)
        with pytest.raises(ValueError,match='identity'):
            tx.restore(directory,{'runner':'b'*64},'test-version')
        assert progress.read_bytes()==b'previous'
    finally:
        if process.poll() is None:process.kill();process.wait()


def test_same_size_timestamp_preserving_stale_bytecode_is_not_executed(tmp_path):
    module=tmp_path/'example.py';module.write_text("value='old'\n")
    old=module.stat();py_compile.compile(str(module),doraise=True)
    module.write_text("value='new'\n");os.utime(module,ns=(old.st_atime_ns,old.st_mtime_ns))
    control=subprocess.run([sys.executable,'-I','-c',
        'import sys;sys.path.insert(0,'+repr(str(tmp_path))+');import example;print(example.value)'],
        capture_output=True,text=True,timeout=30)
    assert control.returncode==0 and control.stdout.strip()=='old'
    bootstrap=(ROOT/'eval/restricted_fast_source_bootstrap_v1.py').read_bytes()
    expected=hashlib.sha256(module.read_bytes()).hexdigest()
    code=("import sys,types\n"
          "m=types.ModuleType('verified')\n"
          "exec(compile("+repr(bootstrap)+",'bootstrap','exec'),m.__dict__)\n"
          "m.install("+repr(str(tmp_path))+","+repr({str(module):expected})+")\n"
          "sys.path.insert(0,"+repr(str(tmp_path))+")\n"
          "import example\nassert example.value=='new'\nm.verify()\n")
    result=subprocess.run([sys.executable,'-I','-c',code],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    wrong=code.replace(expected,'0'*64)
    rejected=subprocess.run([sys.executable,'-I','-c',wrong],capture_output=True,text=True,timeout=30)
    assert rejected.returncode!=0 and 'preverified manifest' in rejected.stderr


def test_runner_pin_is_required_and_mismatch_is_rejected(tmp_path):
    from eval import run_source_restricted_fast_transaction_v2 as launcher
    directory=tmp_path/'private';directory.mkdir(mode=0o700)
    files=launcher._files('minute')
    args=['--opt-in-fast-digest','--mode','minute','--run-dir',str(directory)]
    for name,path in files.items():
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        args.extend(['--'+name+'-sha256','0'*64 if name=='runner' else digest])
    with pytest.raises(SystemExit):launcher.main(args)
    assert not list(directory.iterdir())


def test_source_verified_worker_resume_and_runner_mutation(tmp_path):
    root=tmp_path/'clone';root.mkdir()
    for name in ('envs','eval'):
        shutil.copytree(ROOT/name,root/name,symlinks=True,ignore=shutil.ignore_patterns('__pycache__'))
    runner=root/'eval/qualify_source_restricted_day_v1.py'
    original=runner.read_bytes();timestamp=runner.stat()
    old=b"VERSION = 'wildfire-source-restricted-day-orchestration-v1'"
    stale=b"VERSION = 'wildfire-source-restricted-day-orchestration-v9'"
    assert old in original and len(old)==len(stale)
    runner.write_bytes(original.replace(old,stale));os.utime(runner,ns=(timestamp.st_atime_ns,timestamp.st_mtime_ns))
    py_compile.compile(str(runner),doraise=True)
    runner.write_bytes(original);os.utime(runner,ns=(timestamp.st_atime_ns,timestamp.st_mtime_ns))
    run_dir=tmp_path/'private';run_dir.mkdir(mode=0o700)
    code=r'''
from argparse import Namespace
from pathlib import Path
import hashlib,json,os,sys,types
root=Path(sys.argv[1]);private=Path(sys.argv[2]);raw=(root/'eval/restricted_fast_source_bootstrap_v1.py').read_bytes()
tracker=types.ModuleType('wildfire_verified_source');exec(compile(raw,str(root/'eval/restricted_fast_source_bootstrap_v1.py'),'exec'),tracker.__dict__)
sys.modules[tracker.__name__]=tracker;tracker.install(root)
sys.path[:0]=[str(p) for p in (root,root/'envs',root/'envs/wildfire_extended_inputs_v1',root/'envs/wildfire_response/core/src',root/'envs/wildfire_containment_v1/src',root/'envs/wildfire_containment_score_v1/src')]
from eval import run_source_restricted_fast_transaction_v2 as launcher
from eval import run_source_restricted_private_v1 as base
from eval import restricted_fast_transaction_v2 as tx
import adapter_v1 as api
import wildfire_source_restricted_v1 as restricted
class Selector:
    def case_ids(self):return ['case-'+str(i) for i in range(12)]
class Host:
    def __init__(self,inputs,selector,parent,*,case_id,**kwargs):
        if case_id!='case-0':raise ValueError('blocked fixture')
        self.minute=0;self.status='active'
    def situation(self):return {'minute':self.minute,'status':self.status}
    def advance(self,n):self.minute+=n;return self.situation()
    def checkpoint(self):return {'minute':self.minute,'status':self.status}
    def restore(self,value,*,expected_checkpoint_sha256):
        assert api._hash(value)==expected_checkpoint_sha256
        self.minute=value['minute'];self.status=value['status']
restricted.RestrictedSourceIncidentV1=Host
base.prepare_inputs=lambda:(None,Selector(),None)
pins={k:launcher._sha(v) for k,v in launcher._files('minute').items()}
def manifest_pin(mode,values):
    manifest=launcher._preverified_manifest(launcher._files(mode),values)
    return hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
pins['source-manifest']=manifest_pin('minute',pins)
args=Namespace(mode='minute',run_dir=private,resume=False,wall_seconds=20,max_decisions=1)
launcher._worker(args,pins)
initial=json.loads((private/'progress.json').read_bytes());assert initial['cases'][0]['minute']==1
(private/'progress.json').write_text('partial write')
args.resume=True;launcher._worker(args,pins)
resumed=json.loads((private/'progress.json').read_bytes());assert resumed['cases'][0]['minute']==2
assert (private/'checkpoint.auth').exists()
hourly=private.parent/'hourly';hourly.mkdir(mode=0o700)
hourpins={k:launcher._sha(v) for k,v in launcher._files('hourly').items()}
hourpins['source-manifest']=manifest_pin('hourly',hourpins)
hourargs=Namespace(mode='hourly',run_dir=hourly,resume=False,wall_seconds=20,max_decisions=1)
launcher._worker(hourargs,hourpins)
assert json.loads((hourly/'progress.json').read_bytes())['cases'][0]['minute']==60
(hourly/'progress.json').write_text('partial write')
hourargs.resume=True;launcher._worker(hourargs,hourpins)
assert json.loads((hourly/'progress.json').read_bytes())['cases'][0]['minute']==120
# A loaded, otherwise unpinned helper changes the manifest identity and cannot
# authenticate the original checkpoint, even with identical metadata.
source=root/'eval/incident_weather_v1.py';before=source.stat();data=source.read_bytes()
source.write_bytes(bytes([data[0]^1])+data[1:]);os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns))
changed=dict(pins);changed['source-manifest']=manifest_pin('minute',changed)
assert changed['source-manifest']!=pins['source-manifest']
unchanged=(private/'progress.json').read_bytes()
try:launcher._worker(args,changed)
except RuntimeError:pass
else:raise AssertionError('resume with changed loaded source accepted')
assert (private/'progress.json').read_bytes()==unchanged
try:tx.restore(private,changed,launcher.VERSION+':minute')
except ValueError:pass
else:raise AssertionError('changed loaded-source manifest accepted')
assert (private/'progress.json').read_bytes()==unchanged
source.write_bytes(data);os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns))
with launcher._bindings(args,pins) as (base,runner,transaction,guard,version):
    assert runner.VERSION=='wildfire-source-restricted-day-orchestration-v1'
    path=root/'eval/qualify_source_restricted_day_v1.py';before=path.stat();data=path.read_bytes()
    path.write_bytes(bytes([data[0]^1])+data[1:]);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    try:guard()
    except RuntimeError:pass
    else:raise AssertionError('mutated runner accepted')
    path.write_bytes(data);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
from envs.wildfire_response.incident_day import environment
from wildfire_containment_v1 import incident_day as containment
import adapter_fire_v1 as adapter
from eval import qualify_source_restricted_day_v1 as runner
original_digest=environment.implementation_digest;original_save=runner._save
try:
    with launcher._bindings(args,pins):raise LookupError('forced exception')
except LookupError:pass
assert adapter.implementation_digest is original_digest
assert containment.implementation_digest is original_digest and runner._save is original_save
'''
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PROJ_NETWORK='OFF',
        WILDFIRE_FAST_DIGEST_PARENT_V1=str(os.getpid()),WILDFIRE_FAST_DIGEST_TOKEN_V1='test-child')
    result=subprocess.run([sys.executable,'-I','-c',code,str(root),str(run_dir)],cwd=root,env=env,
                          capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stderr
