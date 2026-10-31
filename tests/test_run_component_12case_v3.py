"""Crash, mutation and concurrency tests for private authenticated generations."""
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import pytest
from eval.run_component_12case_v3 import AuthenticatedStore,ExclusiveLock,digest,promote_chunk,read_key


def checkpoint(n):
    cp={'minute':n};cp['sha256']=digest(cp);return cp


def store_at(root):return AuthenticatedStore(root,b'x'*32)


def setup_store(root):
    s=store_at(root);state={'cases':[{'status':'active','checkpoint_path':None,'checkpoint_sha256':None}]}
    state=promote_chunk(s,state,0,checkpoint(1),{'minute':1},lambda:None)
    return s,state


def crash_during_commit(root,when):
    s=store_at(root);state=s.load();calls=0
    def check():
        nonlocal calls
        calls+=1
        if calls==when:os.kill(os.getpid(),signal.SIGKILL)
    promote_chunk(s,state,0,checkpoint(2),{'minute':2},check)
    os.kill(os.getpid(),signal.SIGKILL)


def test_hard_kill_retains_trusted_head_and_postcommit_state(tmp_path):
    root=tmp_path/'private';root.mkdir(mode=0o700);s,old=setup_store(root);old_head=s.head.copy()
    # 3rd source check occurs after the candidate generation is durable but
    # before the atomic head replacement.
    p=multiprocessing.Process(target=crash_during_commit,args=(root,3));p.start();p.join(10)
    assert p.exitcode==-signal.SIGKILL
    recovered=store_at(root);assert recovered.load()==old and recovered.head==old_head
    # 4th check is never reached: process dies immediately after a full commit.
    p=multiprocessing.Process(target=crash_during_commit,args=(root,4));p.start();p.join(10)
    assert p.exitcode==-signal.SIGKILL
    recovered=store_at(root);new=recovered.load();assert new['cases'][0]['minute']==2
    assert recovered.load_checkpoint(new['cases'][0]['checkpoint_path'],new['cases'][0]['checkpoint_sha256'])==checkpoint(2)
    assert (root/old['cases'][0]['checkpoint_path']).exists()


def test_source_mutation_never_promotes_candidate_or_deletes_prior(tmp_path):
    root=tmp_path/'private';root.mkdir(mode=0o700);s,old=setup_store(root);head=s.head.copy()
    source=tmp_path/'source';source.write_text('original');pin=hashlib.sha256(source.read_bytes()).hexdigest();calls=0
    def check():
        nonlocal calls
        calls+=1
        if calls==2:source.write_text('changed')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=pin:raise RuntimeError('source changed')
    with pytest.raises(RuntimeError,match='source changed'):
        promote_chunk(s,old,0,checkpoint(2),{'minute':2},check)
    other=store_at(root);assert other.load()==old and other.head==head
    assert other.load_checkpoint(old['cases'][0]['checkpoint_path'],old['cases'][0]['checkpoint_sha256'])==checkpoint(1)


def hold_lock(root,ready):
    with ExclusiveLock(root):
        ready.send(True);ready.recv()


def test_exclusive_lock_rejects_concurrent_invocation_and_releases_on_death(tmp_path):
    root=tmp_path/'private';root.mkdir(mode=0o700);parent,child=multiprocessing.Pipe()
    p=multiprocessing.Process(target=hold_lock,args=(root,child));p.start();assert parent.poll(10) and parent.recv()
    with pytest.raises(BlockingIOError):
        with ExclusiveLock(root):pass
    p.kill();p.join(10)
    with ExclusiveLock(root):pass


def test_authentication_and_independent_key_digest(tmp_path):
    root=tmp_path/'private';root.mkdir(mode=0o700);key=root/'key';key.write_bytes(b'x'*32);key.chmod(0o600)
    pin=hashlib.sha256(key.read_bytes()).hexdigest();assert read_key(key,pin)==b'x'*32
    with pytest.raises(ValueError,match='key digest'):read_key(key,'0'*64)
    s,state=setup_store(root);head=root/'head.json';doc=json.loads(head.read_text());doc['payload']['number']+=1;head.write_text(json.dumps(doc));head.chmod(0o600)
    with pytest.raises(ValueError,match='authenticated'):store_at(root).load()


def test_import_v2_snapshot_requires_trusted_digest_and_retains_old_files(tmp_path):
    from eval.run_component_12case_v3 import import_v2_progress
    from wildfire_expanded_incident_v1.private_output import write_atomic_private_json
    oldroot=tmp_path/'old';oldroot.mkdir(mode=0o700);newroot=tmp_path/'new';newroot.mkdir(mode=0o700)
    cases=[{'id':str(i)} for i in range(12)]
    identity={'profile_sha256':'p','policy_sha256':'q','staging_identity_sha256':'s','parent_sha256':'a',
              'catalog_sha256':'c','drafts_sha256':'d','source_sha256':{'policy_code':'code'}}
    old_identity={k:v for k,v in identity.items() if k!='source_sha256'};old_identity['policy_source_sha256']='code'
    cp=checkpoint(8);cp_path=oldroot/'cp.json';write_atomic_private_json(cp_path,cp)
    records=[{'index':i+1,'case_id':str(i),'status':'active' if i==0 else 'unstarted','minute':8 if i==0 else 0,
       'warmup_steps':0,'checkpoint_path':str(cp_path) if i==0 else None,'checkpoint_sha256':cp['sha256'] if i==0 else None,
       'reason':None,'restore_verified':False} for i in range(12)]
    previous={'identity':old_identity,'cases':records,'cursor':1,'total_steps':8};previous['sha256']=digest(previous)
    progress=oldroot/'progress.json';write_atomic_private_json(progress,previous)
    store=store_at(newroot)
    with pytest.raises(ValueError,match='trusted v2 progress'):
        import_v2_progress(store,progress,'0'*64,identity,cases,lambda:None)
    copied=import_v2_progress(store,progress,previous['sha256'],identity,cases,lambda:None)
    assert copied['imported_v2_progress_sha256']==previous['sha256'] and copied['cases'][0]['minute']==8
    assert store.load_checkpoint(copied['cases'][0]['checkpoint_path'],cp['sha256'])==cp
    assert cp_path.exists() and json.loads(progress.read_text())==previous
    assert copied['cases'][1]['status']=='unstarted'


def test_rehashed_checkpoint_cannot_replace_authenticated_reference(tmp_path):
    root=tmp_path/'private';root.mkdir(mode=0o700);s,state=setup_store(root)
    ref=state['cases'][0];path=root/ref['checkpoint_path'];forged=checkpoint(999)
    path.write_text(json.dumps(forged));path.chmod(0o600)
    recovered=store_at(root);loaded=recovered.load()
    with pytest.raises(ValueError,match='trusted checkpoint digest'):
        recovered.load_checkpoint(loaded['cases'][0]['checkpoint_path'],loaded['cases'][0]['checkpoint_sha256'])


def killed_during_first_import(root,progress,pin,identity,cases):
    from eval.run_component_12case_v3 import initialize_store
    store=store_at(root);calls=0
    def check():
        nonlocal calls
        calls+=1
        # Import and first generation are durable; the head is not published.
        if calls==3:os.kill(os.getpid(),signal.SIGKILL)
    with ExclusiveLock(root):
        initialize_store(store,{'policy':'fixed'},{'evidence':'private'},identity,cases,check,
                         import_path=progress,import_digest=pin)


def test_killed_full_import_can_retry_same_private_directory(tmp_path):
    from eval.run_component_12case_v3 import initialize_store
    from wildfire_expanded_incident_v1.private_output import write_atomic_private_json
    oldroot=tmp_path/'old';oldroot.mkdir(mode=0o700);root=tmp_path/'new';root.mkdir(mode=0o700)
    cases=[{'id':str(i)} for i in range(12)]
    identity={'profile_sha256':'p','policy_sha256':'q','staging_identity_sha256':'s','parent_sha256':'a',
              'catalog_sha256':'c','drafts_sha256':'d','source_sha256':{'policy_code':'code'}}
    old_identity={k:v for k,v in identity.items() if k!='source_sha256'};old_identity['policy_source_sha256']='code'
    cp=checkpoint(8);cp_path=oldroot/'cp.json';write_atomic_private_json(cp_path,cp)
    records=[{'index':i+1,'case_id':str(i),'status':'active' if i==0 else 'unstarted','minute':8 if i==0 else 0,
       'warmup_steps':0,'checkpoint_path':str(cp_path) if i==0 else None,'checkpoint_sha256':cp['sha256'] if i==0 else None,
       'reason':None,'restore_verified':False} for i in range(12)]
    previous={'identity':old_identity,'cases':records,'cursor':1,'total_steps':8};previous['sha256']=digest(previous)
    progress=oldroot/'progress.json';write_atomic_private_json(progress,previous)
    p=multiprocessing.Process(target=killed_during_first_import,args=(root,progress,previous['sha256'],identity,cases))
    p.start();p.join(10);assert p.exitcode==-signal.SIGKILL
    assert not (root/'head.json').exists()
    assert len(list(root.glob('policy-*.json')))==len(list(root.glob('audit-*.json')))==1
    assert list(root.glob('case-*.json')) and list(root.glob('generation-*.json'))
    store=store_at(root)
    with ExclusiveLock(root):
        state=initialize_store(store,{'policy':'fixed'},{'evidence':'private'},identity,cases,lambda:None,
                               import_path=progress,import_digest=previous['sha256'])
    assert store_at(root).load()==state
    assert state['imported_v2_progress_sha256']==previous['sha256'] and state['cases'][0]['minute']==8
    assert store.load_checkpoint(state['cases'][0]['checkpoint_path'],cp['sha256'])==cp
    assert cp_path.exists() and json.loads(progress.read_text())==previous
    for entry in state['initial_metadata'].values():
        assert digest(json.loads((root/entry['path']).read_text()))==entry['sha256']


def test_initialization_fails_closed_on_metadata_collision(tmp_path):
    from eval.run_component_12case_v3 import initialize_store
    root=tmp_path/'private';root.mkdir(mode=0o700);store=store_at(root)
    value={'policy':'fixed'};target=root/('policy-'+digest(value)+'.json')
    target.write_text(json.dumps({'policy':'other'}));target.chmod(0o600)
    with pytest.raises(ValueError,match='metadata collision'):
        initialize_store(store,value,{}, {}, [],lambda:None)
    assert not (root/'head.json').exists() and json.loads(target.read_text())=={'policy':'other'}
