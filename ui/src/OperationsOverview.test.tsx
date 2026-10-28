import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import OperationsOverview,{populationSummary} from './OperationsOverview';
import type {District,Observation,Resource} from './types';

const district=(fields:Partial<District>)=>({id:'population-west',population:200,status:null,shelter_active:null,exposed:null,...fields}) as District;
const observation={minute:10,acknowledgements:[],readback_events:[],bases:[{id:'cabezudos',name:'Exercise ground desk B'}],reports:[{id:'claim',received_at:8,observed_at:6,channel:'synthetic_phone',text:'Smoke visible.'},{id:'future',received_at:12,observed_at:10,channel:'synthetic_phone',text:'FUTURE SECRET'}],districts:[district({}),district({id:'population-east',population:150,status:'sheltering',shelter_active:true})],resources:[{id:'crew',kind:'crew',home_base:'cabezudos',state:null,due_at:null} as Resource]} as unknown as Observation;
const render=(value:Observation)=>renderToStaticMarkup(<OperationsOverview observation={value} onReport={()=>{}} onResource={()=>{}} onPanel={()=>{}}/>);

describe('control-center overview',()=>{
  it('keeps unknown population status distinct from sheltering or safety',()=>{
    const counts=populationSummary(observation.districts);
    expect(counts).toEqual({total:350,sheltering:150,moving:0,safe:0,unknown:200,awaiting:0,exposed:0,exposureUnknown:350});
    const html=render(observation);
    expect(html).toContain('350 people');expect(html).toContain('200 status unknown');
    expect(html).toContain('150 sheltering');expect(html).not.toContain('reported safe');
    expect(html).toContain('synthetic placements');
  });
  it('shows only delivered, causally valid reports and keeps the source object unchanged',()=>{
    const before=JSON.stringify(observation);
    const html=render(observation);
    expect(html).toContain('Smoke visible.');expect(html).not.toContain('FUTURE SECRET');
    expect(html).toContain('1 received');expect(html).not.toContain('2 received');
    expect(html).toContain('00:08');expect(JSON.stringify(observation)).toBe(before);
    expect(html).toContain('Investigate report');expect(html).toContain('Request support');expect(html).toContain('Protect population');
    expect(html).toContain('No order is sent');
  });
  it('shows blocked movement and passed ETAs honestly rather than inventing arrival',()=>{
    const counts=populationSummary([district({status:'blocked'}),district({population:150,status:'safe'})]);
    expect(counts.moving).toBe(200);expect(counts.safe).toBe(150);
    const html=render({...observation,resources:[{id:'engine',kind:'engine',home_base:'valverde',state:'en_route',arrival_at:8,due_at:8} as Resource]});
    expect(html).toContain('awaiting status update');expect(html).not.toContain('Arrival progress: On scene');
  });
  it('does not erase reported exposure when a group later reaches a refuge',()=>{
    const html=render({...observation,districts:[district({status:'safe',exposed:20})]});
    expect(html).toContain('200 at refuge');expect(html).toContain('20 reported exposed');
    expect(html).not.toContain('reported safe');
  });
});
