import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {decisionActivity,incomingActivity,simulationClock} from './activity';
import ActivityFeed from './ActivityFeed';
import type {Action,Frame,Observation} from './types';

const observation=(decision:number):Observation=>({task_id:'case',minute:decision*2,decision,
  reports:[],incident_events:[],acknowledgements:[],readback_events:[],sensor_memory:[],wind:null,resources:[],
} as unknown as Observation);
const frame=(decision:number,action:Action|null):Frame=>({observation:observation(decision),action,reward:0,total_reward:0,terminated:false,truncated:false});

describe('causal dashboard activity',()=>{
  it('shows issuance time separately from the recorded post-action frame and hides future decisions',()=>{
    const frames=[frame(0,null),frame(1,{protection_orders:[{recipient:'settlement-west',action:'shelter',channel:'broadcast'}]}),frame(2,{authorizations:['offer-later']})];
    expect(decisionActivity(frames,observation(0)).rows).toHaveLength(0);
    const first=decisionActivity(frames,observation(1)).rows;
    expect(first).toHaveLength(1);expect(first[0].minute).toBe(0);expect(first[0].availableAt).toBe(2);
    expect(first[0].title).toContain('order sent');expect(first[0].title).not.toContain('executed');
    expect(decisionActivity(frames,observation(2)).rows).toHaveLength(2);
  });
  it('counts waits rather than burying actions in sixty no-op rows, and preserves rejected actions',()=>{
    const frames=[frame(0,null),frame(1,{}),frame(2,{requests:[]}),frame(3,{_invalid_ops_action:true})];
    const result=decisionActivity(frames,observation(3));
    expect(result.waits).toBe(2);expect(result.rows).toHaveLength(1);expect(result.rows[0].type).toBe('rejected');
    const html=renderToStaticMarkup(<ActivityFeed observation={observation(3)} frames={frames} onReport={()=>{}} onDecision={()=>{}}/>);
    expect(html).toContain('2 recorded wait decisions');expect(html).toContain('Action rejected');
  });
  it('only displays reports and acknowledgements received at or before the selected decision',()=>{
    const current=observation(2);
    current.reports=[{id:'old',received_at:2,observed_at:0,channel:'synthetic_phone',text:'Smoke',source:'a',source_role:'resident',location:null},
      {id:'later',received_at:6,observed_at:4,channel:'synthetic_phone',text:'SECRET',source:'a',source_role:'resident',location:null}];
    current.acknowledgements=[{minute:4,kind:'protection_delivered'},{minute:6,kind:'SECRET_ACK'}];
    const rows=incomingActivity(current);
    expect(rows).toHaveLength(2);expect(JSON.stringify(rows)).not.toContain('SECRET');
    expect(rows.find(row=>row.type==='report')?.detail).toContain('Observed at 0 minutes');
  });
  it('does not disclose wind or sensor snapshots from later replay frames',()=>{
    const frames=[frame(0,null),frame(1,{})];
    frames[1].observation.wind=[1,0];frames[1].observation.sensor_memory=[{x:2,y:3,burning:true,observed_at:1}];
    expect(incomingActivity(observation(0),frames)).toHaveLength(0);
    const later=incomingActivity(observation(1),frames);
    expect(later).toHaveLength(2);expect(later.some(row=>row.type==='sensor')).toBe(true);
    expect(simulationClock(124)).toBe('02:04');
  });
});
