import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import IncidentInbox,{ResourceTiming} from './IncidentInbox';
import type {IncidentEvent,Resource} from './types';

const event:IncidentEvent={id:'event-1',incident_id:'smoke:west',kind:'resident_smoke_call',source:'resident:west',source_role:'resident',channel:'synthetic_phone',location:[4,5],observed_at:1,received_at:3,text:'Please advise.',status:'open',priority:'urgent',related_event_id:null};

describe('incident updates and arrival timing',()=>{
  it('keeps unresolved requests visible and excludes future updates while replaying',()=>{
    const resolution={...event,id:'event-2',received_at:6,observed_at:4,status:'resolved' as const,text:'Following instructions.'};
    const early=renderToStaticMarkup(<IncidentInbox events={[event,resolution]} minute={4}/>);
    expect(early).toContain('1 open');expect(early).toContain('Observed 1 minutes → received 3 minutes');
    expect(early).not.toContain('Following instructions.');
    const later=renderToStaticMarkup(<IncidentInbox events={[event,resolution]} minute={6}/>);
    expect(later).toContain('0 open');expect(later).toContain('Received update history · 2');
  });
  it('distinguishes arrival from a phase deadline',()=>{
    const resource={id:'engine',state:'mobilizing',due_at:6,arrival_at:18,arrival_in_minutes:14} as Resource;
    expect(renderToStaticMarkup(<ResourceTiming resource={resource}/>)).toContain('Estimated arrival at 18 minutes · 14 minutes remaining');
    expect(renderToStaticMarkup(<ResourceTiming resource={{...resource,arrival_at:null,state:'refilling'}}/>)).toContain('Next phase expected at 6 minutes');
    expect(renderToStaticMarkup(<ResourceTiming resource={{...resource,state:'on_scene'}}/>)).toContain('On scene');
    expect(renderToStaticMarkup(<ResourceTiming resource={{...resource,state:null}}/>)).toContain('Awaiting base status');
    expect(renderToStaticMarkup(<ResourceTiming resource={resource} minute={8}/>)).toContain('10 minutes remaining');
    expect(renderToStaticMarkup(<ResourceTiming resource={resource} minute={20}/>)).toContain('awaiting status');
  });
});
