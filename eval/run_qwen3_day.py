"""Local FP16 Qwen3-8B tool-policy evaluation; no provider API or private prompts."""
import argparse, json, time, re, os, hashlib, random
from pathlib import Path
from eval.qwen3_history import trim_once
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from eval.hub_runtime import make_environment
from wildfire_core.incident_day import IncidentDayTools
from envs.wildfire_response.incident_day.agent import tools
from envs.wildfire_response.incident_day.environment import implementation_digest

MODEL='Qwen/Qwen3-8B'
REV='b968826d9c46dd6066d109eabc6255188de91218'
SYSTEM='''You operate a wildfire control centre for one simulated day. Protect people first, then limit additional fire damage. Use only delivered knowledge. Tools are your sole way to inspect and act. Return exactly one tool call in <tool_call>{"name":"...","arguments":{...}}</tool_call>. Do not output reasoning or prose. Read/check tools consume no simulation time. Orders and status requests cost one minute. advance_time waits at most60minutes and wakes early for important events. Do not poll unchanged information repeatedly: advance time to receive delayed reports and offers. To dispatch: inspect route_options, request the selected resource/sector/approach through commit_orders; wait for its offer, then authorize the received offer ID. Evacuation needs a destination and an offered evacuation approach. Resources are finite and travel/setup take time. Invalid actions cannot be repaired silently. You may wait when appropriate. Complete the day; do not merely announce a plan.'''

def dump(path,value):
 path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')

def run(args):
 out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
 torch.set_num_threads(4);torch.manual_seed(20261029);random.seed(20261029)
 tokenizer=AutoTokenizer.from_pretrained(MODEL,revision=REV)
 model=AutoModelForCausalLM.from_pretrained(MODEL,revision=REV,torch_dtype=torch.float16,device_map='cuda:0',attn_implementation='sdpa').eval()
 manifest=json.loads((Path(args.data)/'manifest.json').read_text());runtime=manifest['runtimes']['es-ma-2021']
 started=time.monotonic();summary={'model':MODEL,'revision':REV,'dtype':'float16','thinking':False,'temperature':0,'cases':[],'status':'running'}
 summary.update(run_id=out.name,manifest_sha256=hashlib.sha256((Path(args.data)/'manifest.json').read_bytes()).hexdigest(),engine_implementation_sha256=implementation_digest(),lifecycle_sha256=manifest['lifecycle_sha256'],inputs_sha256=runtime['inputs_sha256'],network_sha256=runtime['network_sha256'],context_tokens=args.context,max_new_tokens=768,case_wall_seconds=args.case_seconds,global_wall_seconds=args.seconds,hardware=torch.cuda.get_device_name(0),torch_version=torch.__version__)
 dump(out/'summary.json',summary)
 for case in args.cases:
  if time.monotonic()-started>args.seconds:break
  env,entry=make_environment(args.data,'es-ma-2021',allow_unapproved_data=True)
  obs,_=env.reset(case);bridge=IncidentDayTools(env);messages=[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(obs,separators=(',',':'))}]
  case_start=time.monotonic();records=[];errors=0;pruned=0;stop_reason=None
  directory=out/case;directory.mkdir()
  for turn in range(args.max_calls):
   if env.terminated or env.truncated or time.monotonic()-started>args.seconds or time.monotonic()-case_start>args.case_seconds:break
   try:
    while True:
     encoded=tokenizer.apply_chat_template(messages,tools=tools(),tokenize=True,add_generation_prompt=True,enable_thinking=False,return_tensors='pt',return_dict=True)
     if encoded['input_ids'].shape[-1]<=args.context:break
     messages=trim_once(messages);pruned+=1
   except ValueError as error:
    stop_reason='context_capacity: '+str(error);break
   encoded={k:v.to('cuda') for k,v in encoded.items()};n=encoded['input_ids'].shape[-1];t=time.monotonic()
   try:
    with torch.inference_mode():generated=model.generate(**encoded,max_new_tokens=768,do_sample=False,pad_token_id=tokenizer.eos_token_id)
   except torch.OutOfMemoryError:
    torch.cuda.empty_cache();stop_reason='gpu_memory_capacity';break
   response=tokenizer.decode(generated[0,n:],skip_special_tokens=True);elapsed=time.monotonic()-t
   rec={'turn':turn,'minute':env.minute,'input_tokens':n,'output_tokens':int(generated.shape[-1]-n),'seconds':elapsed,'response':response}
   try:
    match=re.search(r'<tool_call>\s*(.*?)\s*</tool_call>',response,re.S)
    call=json.loads(match.group(1) if match else response)
    if set(call)!={'name','arguments'} or not isinstance(call['name'],str) or not isinstance(call['arguments'],dict):raise ValueError('Expected named tool and argument object')
    result=bridge.execute(call['name'],call['arguments']);rec.update(tool=call['name'],arguments=call['arguments'],result=result)
    if isinstance(result,dict) and (result.get('error') or (isinstance(result.get('info'),dict) and result['info'].get('error'))):errors+=1
   except (ValueError,TypeError,KeyError) as e:
    errors+=1;rec['error']=str(e)
    observation,reward,terminated,truncated,info=env.step({'malformed_model_response':True})
    result={'error':str(e),'observation':observation,'reward':reward,'terminated':terminated,'truncated':truncated,'info':info}
   if env.minute != rec['minute']:
    env.save_checkpoint(directory/'checkpoint.json.gz')
   records.append(rec)
   with (directory/'requests.jsonl').open('a') as f:f.write(json.dumps(rec,allow_nan=False)+'\n')
   if rec.get('tool'):
    messages.append({'role':'assistant','content':'','tool_calls':[{'type':'function','function':{'name':call['name'],'arguments':call['arguments']}}]})
    messages.append({'role':'tool','name':call['name'],'content':json.dumps(result,separators=(',',':'))})
   else:
    messages.append({'role':'assistant','content':response})
    messages.append({'role':'user','content':'Invalid tool response: '+json.dumps(result,separators=(',',':'))})
   dump(directory/'progress.json',{'turn':turn+1,'minute':env.minute,'errors':errors,'elapsed_seconds':time.monotonic()-case_start})
  completed=env.terminated and not env.truncated and env.minute==1440
  verification=env.verify_episode() if env.terminated or env.truncated else None
  dump(directory/'trajectory.json',env.trajectory())
  if verification:dump(directory/'verification.json',verification)
  if stop_reason is None and not completed:
   stop_reason='episode_truncated' if env.truncated else 'case_wall_budget' if time.monotonic()-case_start>args.case_seconds else 'global_wall_budget' if time.monotonic()-started>args.seconds else 'model_call_budget'
  row={'reason':stop_reason,'case':case,'minute':env.minute,'completed':completed,'truncated':env.truncated,'calls':len(records),'errors':errors,'pruned_history_pairs':pruned,'seconds':time.monotonic()-case_start,'verification':verification,'outcomes':env.outcomes() if completed else None,'gpu_peak_bytes':torch.cuda.max_memory_allocated()}
  summary['cases'].append(row);dump(out/'summary.json',summary)
 summary['status']='finished';summary['elapsed_seconds']=time.monotonic()-started;dump(out/'summary.json',summary)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--output',required=True);p.add_argument('--seconds',type=int,default=7200);p.add_argument('--case-seconds',type=int,default=900);p.add_argument('--context',type=int,default=32768);p.add_argument('--max-calls',type=int,default=1200);p.add_argument('--cases',nargs='+',default=[f'incident-day-realmap-v2-{i:03}' for i in range(6)]);run(p.parse_args())
