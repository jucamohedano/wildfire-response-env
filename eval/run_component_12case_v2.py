"""One-CPU, private, resumable, round-robin unscored source diagnostics."""
import argparse,collections,hashlib,json,os,time
from pathlib import Path
os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
import adapter_v1 as api
from eval.extended_map_common import digest
from eval.extended_scenario_drafts_v1 import build_drafts
from eval.source_built_water_estimated_adapter_v1 import adapt
from eval.fresh_built_water_estimated_weather_v1 import FreshBuiltWaterEstimatedWeatherV1,run_bounded
from eval.domain_built_water_estimated_policy_v2 import build_policy
from wildfire_expanded_incident_v1.private_output import read_private_json,write_private_json,write_atomic_private_json

R=Path('/tmp/wildfire-component-12case-v2-20261031');P=Path('/tmp/wildfire-map-resolution-private')
parser=argparse.ArgumentParser();parser.add_argument('--wall-seconds',type=int,default=600);parser.add_argument('--resume-sha256');args=parser.parse_args()
if not 1<=args.wall_seconds<=1800:raise ValueError('bounded wall budget required')
def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def record_hash(record):return api._hash({k:v for k,v in record.items() if k!='sha256'})
profile=read(P/'domain-built-water-estimated-profile-v1.json');built=read(P/'domain-built-water-profile-v2.json');parent_profile=read(P/'domain-inland-low-water-profile-v1.json');strict=read(P/'resolution-v1.json');water=read(P/'resolution-v2.json')
parent=read('/tmp/wildfire-qwen3-data/runtime/es-ma-2021/inputs.json');catalog=read('/tmp/wildfire-eval-v1-final2-20261030/catalog.json')
selections,sea,policy=build_policy(profile,parent_profile,water)
transfer={'vegetation>vegetation':1.,'built>built':1.,'vegetation>built':.2,'built>vegetation':.2}
staged=adapt(profile,built,parent_profile,strict,water,bbox=(0,0,84,59),selections=selections,sea_options=sea,staging_transfer_anchor=transfer,max_cells=5000,opt_in=True)
drafts=build_drafts(catalog,parent['grid'],staged['grid']);cases=drafts['cases']
if len(cases)!=12 or len({c['id'] for c in cases})!=12:raise ValueError('expected exactly twelve distinct source drafts')
identity={'version':'private-12case-component-evaluation-v2','profile_sha256':profile['sha256'],'policy_sha256':policy['sha256'],
 'policy_source_sha256':sha('/tmp/wildfire-issue60/eval/domain_built_water_estimated_policy_v2.py'),
 'runner_sha256':sha(__file__),'staging_identity_sha256':digest(staged['identity']),
 'parent_sha256':parent['sha256'],'catalog_sha256':catalog['sha256'],'drafts_sha256':drafts['sha256']}
state_path=R/'progress.json';state_inode=None
if args.resume_sha256 is None:
 if state_path.exists():raise ValueError('existing run requires explicit trusted --resume-sha256')
 write_private_json(R/'policy.json',policy);write_private_json(R/'audit.json',staged['private_audit'])
 state={'version':identity['version'],'identity':identity,'cases':[{'index':i+1,'case_id':c['id'],'status':'unstarted','minute':0,
  'warmup_steps':0,'checkpoint_sha256':None,'checkpoint_path':None,'reason':None,'restore_verified':False} for i,c in enumerate(cases)],
  'cursor':0,'total_steps':0,'limitations':['synthetic uncalibrated assumptions','unscored fire/weather only']}
else:
 state=read_private_json(state_path,max_bytes=10_000_000)
 if state.get('sha256')!=args.resume_sha256 or record_hash(state)!=args.resume_sha256:raise ValueError('trusted resume digest differs')
 if state['identity']!=identity or [c['id'] for c in cases]!=[z['case_id'] for z in state['cases']]:raise ValueError('resume source identity differs')
 stat=state_path.stat();state_inode=(stat.st_dev,stat.st_ino)

def persist():
 global state_inode
 state['sha256']=record_hash(state);state_inode=write_atomic_private_json(state_path,state,state_inode)

def save_checkpoint(index,checkpoint):
 pin=checkpoint['sha256'];path=R/('case-%02d-%s.json'%(index,pin))
 if path.exists():
  existing=read_private_json(path,max_bytes=150_000_000)
  if existing!=checkpoint:raise ValueError('immutable checkpoint differs')
 else:write_atomic_private_json(path,checkpoint)
 return str(path),pin

def counts():return dict(collections.Counter(c['status'] for c in state['cases']))

if args.resume_sha256 is None:persist()
start=time.monotonic();deadline=start+args.wall_seconds;cpu_start=time.process_time();chunks=0
while time.monotonic()<deadline:
 eligible=[i for i,c in enumerate(state['cases']) if c['status'] in ('unstarted','active')]
 if not eligible:break
 index=next((i for i in range(state['cursor'],12) if i in eligible),eligible[0]);record=state['cases'][index];case=cases[index]
 state['cursor']=(index+1)%12;previous=record['checkpoint_path']
 try:
  ignition={tuple(point):'vegetation' for point in case['initial_fire']}
  config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
  owner=FreshBuiltWaterEstimatedWeatherV1(staged,profile,parent,catalog,expected_staging_identity=digest(staged['identity']),
    expected_parent_sha256=parent['sha256'],expected_catalog_sha256=catalog['sha256'],case_id=case['id'],
    ignition_components=ignition,containment_config=config,pressure_scale=1,day_horizon=1440,opt_in=True)
  if record['status']=='active':
   saved=read_private_json(record['checkpoint_path'],max_bytes=150_000_000)
   owner.restore(saved,expected_checkpoint_sha256=record['checkpoint_sha256']);record['restore_verified']=True
  state_now=owner.situation()
  if state_now['status']=='active' and time.monotonic()<deadline:
   result=run_bounded(owner,wall_seconds=max(.001,min(30.,deadline-time.monotonic())),step_limit=60)
   state['total_steps']+=result['steps'];state_now=owner.situation();chunks+=1
  record.update(status=state_now['status'],minute=state_now['minute'],warmup_steps=state_now['warmup_steps'],reason=owner._reason)
  if state_now['status'] in ('active','diagnostic_end'):
   cp=owner.checkpoint();record['checkpoint_path'],record['checkpoint_sha256']=save_checkpoint(index+1,cp)
  # The terminal censor deliberately retains the previous supported checkpoint.
  if sha('/tmp/wildfire-issue60/eval/domain_built_water_estimated_policy_v2.py')!=identity['policy_source_sha256'] or sha(__file__)!=identity['runner_sha256']:
   raise RuntimeError('policy or batch source changed')
 except Exception as exc:
  record['status']='terminal_error';record['reason']=type(exc).__name__+': '+str(exc)
 state['elapsed_this_invocation']=round(time.monotonic()-start,3);state['cpu_this_invocation']=round(time.process_time()-cpu_start,3)
 persist()
 if previous and previous!=record['checkpoint_path']:
  try:Path(previous).unlink()
  except FileNotFoundError:pass
 print(json.dumps({'case':index+1,'status':record['status'],'minute':record['minute'],'warmup':record['warmup_steps'],
  'counts':counts(),'elapsed':state['elapsed_this_invocation']}),flush=True)
state['last_stop']='all_terminal' if all(c['status'] not in ('unstarted','active') for c in state['cases']) else 'wall_budget'
state['elapsed_this_invocation']=round(time.monotonic()-start,3);state['cpu_this_invocation']=round(time.process_time()-cpu_start,3);persist()
print(json.dumps({'stop':state['last_stop'],'counts':counts(),'elapsed':state['elapsed_this_invocation'],
 'resume_sha256':state['sha256'],'chunks':chunks}),flush=True)
