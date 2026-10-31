"""Private one-CPU source diagnostics with authenticated atomic generations.

Use a 32-byte private key and independently retain its SHA-256. The key is an
integrity capability, not protection from a principal able to read it. Retain
an external head digest as well if rollback detection is required. Progress,
checkpoints and source data stay in private storage; no scores are produced.
"""
import argparse
import collections
import copy
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import time

VERSION='private-12case-component-evaluation-v3'


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def digest(value):return hashlib.sha256(encoded(value)).hexdigest()
def file_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verify_directory(root):
    root=Path(root)
    if not root.is_absolute() or root.is_symlink():raise ValueError('absolute private directory required')
    st=root.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o700:
        raise ValueError('private directory must be owned and mode 0700')
    return root


def read_key(path,expected):
    path=Path(path);_verify_directory(path.parent)
    if not path.is_absolute() or path.is_symlink() or len(expected)!=64:raise ValueError('private key and trusted SHA-256 required')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_nlink!=1:
            raise ValueError('invalid private key permissions')
        key=os.read(fd,33)
    finally:os.close(fd)
    if len(key)!=32 or not hmac.compare_digest(hashlib.sha256(key).hexdigest(),expected):raise ValueError('trusted key digest differs')
    return key


class ExclusiveLock:
    def __init__(self,root):self.root=_verify_directory(root);self.fd=None
    def __enter__(self):
        path=self.root/'batch.lock';fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_nlink!=1:
                raise ValueError('invalid lock file')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:os.close(fd);raise
        self.fd=fd;return self
    def __exit__(self,*args):
        fcntl.flock(self.fd,fcntl.LOCK_UN);os.close(self.fd);self.fd=None


class AuthenticatedStore:
    """Immutable authenticated generations, atomically published by signed head."""
    def __init__(self,root,key):
        from wildfire_expanded_incident_v1.private_output import read_private_json,write_atomic_private_json
        self.root=_verify_directory(root);self.key=key;self.read=read_private_json;self.write=write_atomic_private_json
        self.head=None;self.head_inode=None
    def sign(self,value):return hmac.new(self.key,encoded(value),hashlib.sha256).hexdigest()
    def seal(self,value):return {'payload':value,'hmac_sha256':self.sign(value)}
    def unseal(self,value):
        if (type(value) is not dict or set(value)!={'payload','hmac_sha256'} or type(value['hmac_sha256']) is not str
                or not hmac.compare_digest(value['hmac_sha256'],self.sign(value['payload']))):
            raise ValueError('authenticated record differs')
        return value['payload']
    def load(self):
        path=self.root/'head.json';head=self.unseal(self.read(path,max_bytes=10000))
        name=head.get('generation_file')
        if type(name) is not str or Path(name).name!=name or not name.startswith('generation-'):
            raise ValueError('invalid generation reference')
        document=self.read(self.root/name,max_bytes=10_000_000)
        if digest(document)!=head.get('generation_sha256'):raise ValueError('generation digest differs')
        generation=self.unseal(document)
        if generation.get('number')!=head.get('number'):raise ValueError('generation number differs')
        st=path.stat();self.head_inode=(st.st_dev,st.st_ino);self.head=head
        return copy.deepcopy(generation['state'])
    def commit(self,state,check):
        """Do not advance head unless every source check succeeds."""
        check();number=0 if self.head is None else self.head['number']+1
        generation={'version':VERSION,'number':number,'previous_generation_sha256':None if self.head is None else self.head['generation_sha256'],
                    'state':copy.deepcopy(state)}
        sealed=self.seal(generation);pin=digest(sealed);name='generation-%08d-%s.json'%(number,pin)
        path=self.root/name
        if path.exists():
            if self.read(path,max_bytes=10_000_000)!=sealed:raise ValueError('generation collision')
        else:self.write(path,sealed)
        head={'version':VERSION,'number':number,'generation_file':name,'generation_sha256':pin}
        check()
        self.head_inode=self.write(self.root/'head.json',self.seal(head),self.head_inode)
        self.head=head
        return pin
    def save_checkpoint(self,index,checkpoint):
        if (type(checkpoint) is not dict or type(checkpoint.get('sha256')) is not str
                or digest({k:v for k,v in checkpoint.items() if k!='sha256'})!=checkpoint['sha256']):
            raise ValueError('checkpoint digest differs')
        pin=checkpoint['sha256'];name='case-%02d-%s.json'%(index,pin);path=self.root/name
        if path.exists():
            if self.read(path,max_bytes=150_000_000)!=checkpoint:raise ValueError('checkpoint collision')
        else:self.write(path,checkpoint)
        return name,pin
    def load_checkpoint(self,name,pin):
        if type(name) is not str or Path(name).name!=name or not name.startswith('case-'):raise ValueError('invalid checkpoint reference')
        cp=self.read(self.root/name,max_bytes=150_000_000)
        if cp.get('sha256')!=pin or digest({k:v for k,v in cp.items() if k!='sha256'})!=pin:raise ValueError('trusted checkpoint digest differs')
        return cp


def promote_chunk(store,state,index,checkpoint,updates,check):
    """Stage candidate separately; original trusted state is never mutated."""
    check();candidate=copy.deepcopy(state);record=candidate['cases'][index]
    record.update(copy.deepcopy(updates))
    if checkpoint is not None:
        name,pin=store.save_checkpoint(index+1,checkpoint)
        record['checkpoint_path']=name;record['checkpoint_sha256']=pin
    store.commit(candidate,check)
    return candidate


def import_v2_progress(store,path,expected,identity,cases,check):
    """Copy a separately trusted v2 snapshot; never write to the old run."""
    if type(expected) is not str or len(expected)!=64:raise ValueError('trusted v2 progress digest required')
    path_for_progress=path
    previous=store.read(path_for_progress,max_bytes=10_000_000)
    if previous.get('sha256')!=expected or digest({k:v for k,v in previous.items() if k!='sha256'})!=expected:
        raise ValueError('trusted v2 progress differs')
    old=previous['identity']
    for field in ('profile_sha256','policy_sha256','staging_identity_sha256','parent_sha256','catalog_sha256','drafts_sha256'):
        if old.get(field)!=identity.get(field):raise ValueError('v2 source identity differs')
    if old.get('policy_source_sha256')!=identity['source_sha256']['policy_code']:
        raise ValueError('v2 policy source differs')
    records=copy.deepcopy(previous['cases'])
    if len(records)!=12 or [r['case_id'] for r in records]!=[c['id'] for c in cases]:raise ValueError('v2 case selection differs')
    for i,record in enumerate(records):
        if record.get('index')!=i+1 or record.get('status') not in ('unstarted','active','censored','diagnostic_end','terminal_error'):
            raise ValueError('invalid v2 case state')
        path=record.get('checkpoint_path');pin=record.get('checkpoint_sha256')
        if path is None:
            if pin is not None or record['status'] in ('active','diagnostic_end'):raise ValueError('v2 checkpoint missing')
        else:
            cp=store.read(path,max_bytes=150_000_000)
            if cp.get('sha256')!=pin or digest({k:v for k,v in cp.items() if k!='sha256'})!=pin:
                raise ValueError('v2 checkpoint differs from trusted progress')
            record['checkpoint_path'],record['checkpoint_sha256']=store.save_checkpoint(i+1,cp)
    if store.read(path_for_progress,max_bytes=10_000_000)!=previous:raise ValueError('v2 progress changed during copy')
    check()
    return {'identity':identity,'cases':records,'cursor':previous['cursor'],'total_steps':previous['total_steps'],
            'imported_v2_progress_sha256':expected,'limitations':['uncalibrated synthetic assumptions','unscored fire/weather only']}


def initialize_store(store,policy,audit,identity,cases,check,*,import_path=None,import_digest=None):
    """Idempotently stage private inputs and publish the first authenticated head.

    A crash before publication can leave immutable, content-addressed orphans;
    retry reuses only identical private files and never overwrites collisions.
    Call while holding the invocation lock.
    """
    if (store.root/'head.json').exists():raise ValueError('existing run requires --resume')
    references={}
    for label,value in (('policy',policy),('audit',audit)):
        pin=digest(value);name=label+'-'+pin+'.json';path=store.root/name
        if path.exists() or path.is_symlink():
            if store.read(path,max_bytes=150_000_000)!=value:raise ValueError('initial metadata collision')
        else:store.write(path,value)
        references[label]={'path':name,'sha256':pin}
    if import_path is not None:
        state=import_v2_progress(store,import_path,import_digest,identity,cases,check)
    else:
        state={'identity':identity,'cases':[{'index':i+1,'case_id':c['id'],'status':'unstarted','minute':0,'warmup_steps':0,
           'checkpoint_sha256':None,'checkpoint_path':None,'reason':None,'restore_verified':False} for i,c in enumerate(cases)],
           'cursor':0,'total_steps':0,'limitations':['uncalibrated synthetic assumptions','unscored fire/weather only']}
    state['initial_metadata']=references
    store.commit(state,check)
    return state


def _source_snapshot(paths):return {name:file_sha(path) for name,path in paths.items()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--auth-key',type=Path,required=True)
    parser.add_argument('--key-sha256',required=True);parser.add_argument('--wall-seconds',type=int,default=600)
    parser.add_argument('--resume',action='store_true');parser.add_argument('--expected-head-sha256')
    parser.add_argument('--import-v2-progress',type=Path);parser.add_argument('--v2-progress-sha256')
    a=parser.parse_args()
    if not 1<=a.wall_seconds<=1800:raise ValueError('wall budget must be 1..1800 seconds')
    if (a.import_v2_progress is None)!=(a.v2_progress_sha256 is None) or (a.resume and a.import_v2_progress is not None):
        raise ValueError('v2 import needs a trusted digest and a fresh output')
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});root=_verify_directory(a.output);key=read_key(a.auth_key,a.key_sha256)
    with ExclusiveLock(root):
        _run(a,root,key)


def _run(a,root,key):
    import adapter_v1 as api
    from eval import domain_built_water_estimated_policy_v2 as policy_module
    from eval import source_built_water_estimated_adapter_v1 as adapter_module
    from eval import fresh_built_water_estimated_weather_v1 as owner_module
    from eval.extended_scenario_drafts_v1 import build_drafts
    P=Path('/tmp/wildfire-map-resolution-private')
    files={'profile':P/'domain-built-water-estimated-profile-v1.json','built':P/'domain-built-water-profile-v2.json',
      'parent_profile':P/'domain-inland-low-water-profile-v1.json','strict':P/'resolution-v1.json','water':P/'resolution-v2.json',
      'parent':Path('/tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json'),
      'catalog':Path('/tmp/wildfire-eval-v1-final2-20261030/catalog.json'),
      'policy_code':Path(policy_module.__file__),'adapter_code':Path(adapter_module.__file__),
      'owner_code':Path(owner_module.__file__),'runner':Path(__file__)}
    pins=_source_snapshot(files)
    if (pins['policy_code']!='cea3c8ed8bb0beeda1a470cac8c7816df850b2f5e29f2ea90a8019b4c887ef29'
            or pins['adapter_code']!='b0fec53c8b505905b6f2f86d0f1d817410d041052e04b81221d7889cb0d3d841'
            or pins['owner_code']!='88f7de45c8beb88f4eff481e6b714be2d8079813fe774a3a80c60f092e1fae26'):
        raise ValueError('independently pinned policy, adapter or owner differs')
    def check():
        if _source_snapshot(files)!=pins:raise RuntimeError('source changed; last authenticated generation retained')
        owner_module._ensure_code()
    def read(name):
        with files[name].open('r') as stream:return json.load(stream)
    profile,built,parent_profile,strict,water,parent,catalog=(read(k) for k in ('profile','built','parent_profile','strict','water','parent','catalog'))
    selections,sea,policy=policy_module.build_policy(profile,parent_profile,water)
    transfer={'vegetation>vegetation':1.,'built>built':1.,'vegetation>built':.2,'built>vegetation':.2}
    staged=adapter_module.adapt(profile,built,parent_profile,strict,water,bbox=(0,0,84,59),selections=selections,sea_options=sea,
      staging_transfer_anchor=transfer,max_cells=5000,opt_in=True)
    drafts=build_drafts(catalog,parent['grid'],staged['grid']);cases=drafts['cases']
    if len(cases)!=12 or len({c['id'] for c in cases})!=12:raise ValueError('expected twelve unique source drafts')
    identity={'version':VERSION,'source_sha256':pins,'profile_sha256':profile['sha256'],'policy_sha256':policy['sha256'],
      'staging_identity_sha256':digest(staged['identity']),'parent_sha256':parent['sha256'],
      'catalog_sha256':catalog['sha256'],'drafts_sha256':drafts['sha256']}
    check();store=AuthenticatedStore(root,key)
    if a.resume:
        state=store.load()
        if a.expected_head_sha256 and store.head['generation_sha256']!=a.expected_head_sha256:raise ValueError('trusted head differs')
        if state['identity']!=identity or [c['id'] for c in cases]!=[r['case_id'] for r in state['cases']]:raise ValueError('resume source identity differs')
    else:
        state=initialize_store(store,policy,staged['private_audit'],identity,cases,check,
          import_path=a.import_v2_progress,import_digest=a.v2_progress_sha256)
    start=time.monotonic();deadline=start+a.wall_seconds
    while time.monotonic()<deadline:
        eligible=[i for i,r in enumerate(state['cases']) if r['status'] in ('unstarted','active')]
        if not eligible:break
        index=next((i for i in range(state['cursor'],12) if i in eligible),eligible[0]);record=state['cases'][index];case=cases[index]
        candidate=copy.deepcopy(state);candidate['cursor']=(index+1)%12
        try:
            config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
            owner=owner_module.FreshBuiltWaterEstimatedWeatherV1(staged,profile,parent,catalog,
              expected_staging_identity=digest(staged['identity']),expected_parent_sha256=parent['sha256'],
              expected_catalog_sha256=catalog['sha256'],case_id=case['id'],ignition_components={tuple(p):'vegetation' for p in case['initial_fire']},
              containment_config=config,pressure_scale=1,day_horizon=1440,opt_in=True)
            restored=False
            if record['status']=='active':
                cp=store.load_checkpoint(record['checkpoint_path'],record['checkpoint_sha256'])
                owner.restore(cp,expected_checkpoint_sha256=record['checkpoint_sha256']);restored=True
            situation=owner.situation();steps=0
            if situation['status']=='active' and time.monotonic()<deadline:
                outcome=owner_module.run_bounded(owner,wall_seconds=max(.001,min(30.,deadline-time.monotonic())),step_limit=60)
                steps=outcome['steps'];situation=owner.situation()
            checkpoint=owner.checkpoint() if situation['status'] in ('active','diagnostic_end') else None
            check();candidate['total_steps']+=steps
            updates={'status':situation['status'],'minute':situation['minute'],'warmup_steps':situation['warmup_steps'],
                     'reason':owner._reason,'restore_verified':record['restore_verified'] or restored}
            state=promote_chunk(store,candidate,index,checkpoint,updates,check)
        except Exception as exc:
            # If source validation fails, stop without committing anything derived
            # from that source. A later trusted resume uses the old generation.
            check()
            updates={'status':'terminal_error','reason':type(exc).__name__+': '+str(exc)}
            state=promote_chunk(store,candidate,index,None,updates,check)
        print(json.dumps({'case':index+1,'status':state['cases'][index]['status'],
          'counts':dict(collections.Counter(r['status'] for r in state['cases'])),
          'elapsed':round(time.monotonic()-start,3)}),flush=True)
    print(json.dumps({'stop':'wall_budget' if any(r['status'] in ('active','unstarted') for r in state['cases']) else 'all_terminal',
       'counts':dict(collections.Counter(r['status'] for r in state['cases'])),
       'head_sha256':store.head['generation_sha256'],'elapsed':round(time.monotonic()-start,3)}),flush=True)


if __name__=='__main__':main()
