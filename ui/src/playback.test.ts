import {describe,it,expect,vi} from 'vitest';
import {finishEpisode} from './playback';
import type {Envelope} from './types';

const stateAt=(decision:number,extra:Partial<Envelope>={}):Envelope=>({
  episode_id:'episode-a',observation:{decision,minute:decision*2} as Envelope['observation'],
  reward:0,total_reward:0,terminated:false,truncated:false,info:{},...extra,
});

describe('bounded complete-run generation',()=>{
  it('records every real decision using the previous episode token and stops at natural completion',async()=>{
    const recorded:Envelope[]=[];
    const advance=vi.fn(async(state:Envelope)=>stateAt(state.observation.decision+1,{terminated:state.observation.decision===2}));
    const final=await finishEpisode(stateAt(0),60,advance,state=>recorded.push(state),()=>false);
    expect(final.terminated).toBe(true);
    expect(recorded.map(state=>state.observation.minute)).toEqual([2,4,6]);
    expect(advance.mock.calls.map(([state])=>state.observation.decision)).toEqual([0,1,2]);
  });
  it('resumes from the current decision and respects the configured horizon',async()=>{
    const advance=vi.fn(async(state:Envelope)=>stateAt(state.observation.decision+1));
    const record=vi.fn();
    const final=await finishEpisode(stateAt(58),60,advance,record,()=>false);
    expect(final.observation.decision).toBe(60);
    expect(record).toHaveBeenCalledTimes(2);
  });
  it('stops on cancellation without retrying or dropping the already committed frame',async()=>{
    let stop=false;
    const advance=vi.fn(async()=>{stop=true;return stateAt(1);});
    const record=vi.fn();
    const final=await finishEpisode(stateAt(0),60,advance,record,()=>stop);
    expect(final.observation.decision).toBe(1);
    expect(record).toHaveBeenCalledTimes(1);
    expect(advance).toHaveBeenCalledTimes(1);
  });
  it('never advances completed/truncated episodes and stops after a newly invalid action',async()=>{
    for(const extra of [{terminated:true},{truncated:true}]){
      const advance=vi.fn();
      await finishEpisode(stateAt(2,extra),60,advance,vi.fn(),()=>false);
      expect(advance).not.toHaveBeenCalled();
    }
    const advance=vi.fn(async()=>stateAt(1,{info:{error:'Invalid action'}}));
    const record=vi.fn();
    await finishEpisode(stateAt(0),60,advance,record,()=>false);
    expect(record).toHaveBeenCalledTimes(1);
    expect(advance).toHaveBeenCalledTimes(1);
  });
  it('can resume with a valid baseline decision after an earlier rejected action',async()=>{
    const advance=vi.fn(async()=>stateAt(3,{terminated:true}));
    const final=await finishEpisode(stateAt(2,{info:{error:'Earlier invalid action'}}),60,advance,vi.fn(),()=>false);
    expect(final.terminated).toBe(true);
    expect(advance).toHaveBeenCalledTimes(1);
  });
  it('rejects stale/mixed frames and does not hide transport failures behind retries',async()=>{
    for(const invalid of [stateAt(0),stateAt(2),stateAt(1,{episode_id:'episode-b'})]){
      const record=vi.fn();
      await expect(finishEpisode(stateAt(0),60,async()=>invalid,record,()=>false)).rejects.toThrow('Unexpected episode or decision');
      expect(record).not.toHaveBeenCalled();
    }
    const advance=vi.fn().mockRejectedValue(new Error('Server unavailable'));
    await expect(finishEpisode(stateAt(0),60,advance,vi.fn(),()=>false)).rejects.toThrow('Server unavailable');
    expect(advance).toHaveBeenCalledTimes(1);
  });
});
