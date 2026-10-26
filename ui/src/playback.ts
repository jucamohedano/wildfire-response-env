import type {Envelope} from './types';

export async function finishEpisode(
  initial:Envelope,maxDecisions:number,
  advance:(state:Envelope)=>Promise<Envelope>,
  record:(state:Envelope)=>void,
  stopped:()=>boolean,
):Promise<Envelope> {
  let state=initial;
  while(!state.terminated&&!state.truncated&&state.observation.decision<maxDecisions&&!stopped()){
    const next=await advance(state);
    if(next.episode_id!==state.episode_id||next.observation.decision!==state.observation.decision+1)
      throw new Error('Unexpected episode or decision returned. Refresh the live state before continuing.');
    state=next;record(state);
    if(state.info.error)break;
  }
  return state;
}
