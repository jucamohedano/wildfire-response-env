import type {IncidentEvent,Resource} from './types';
import {pretty} from './model';

export function ResourceTiming({resource,minute}:{resource:Resource;minute?:number}){
  if(resource.state===null)return <small className="arrival-time">Awaiting base status</small>;
  if(resource.state==='on_scene')return <><ArrivalStages state={resource.state}/><small className="arrival-time">On scene</small></>;
  if(resource.arrival_at!==undefined&&resource.arrival_at!==null){
    if(minute!==undefined&&minute>resource.arrival_at)return <><ArrivalStages state={resource.state}/><small className="arrival-time overdue">ETA {resource.arrival_at}m passed · awaiting status update</small></>;
    const remaining=minute===undefined?resource.arrival_in_minutes:Math.max(0,resource.arrival_at-minute);
    return <><ArrivalStages state={resource.state}/><small className="arrival-time" title={resource.travel_time_basis}>Estimated arrival at {resource.arrival_at}m · {remaining} min remaining</small></>;
  }
  return <><ArrivalStages state={resource.state}/><small className="arrival-time">{resource.due_at===null?'No arrival scheduled':`Next phase expected at ${resource.due_at}m`}</small></>;
}

function ArrivalStages({state}:{state:string|null}){
  const stages=['Reserved','Mobilizing','En route','On scene'];
  const active=state==='reserved'?0:state==='mobilizing'?1:state==='en_route'?2:state==='on_scene'?3:-1;
  if(active<0)return null;
  return <ol className="arrival-stages" aria-label={`Arrival progress: ${stages[active]}`}>
    {stages.map((stage,index)=><li key={stage} className={index<active?'complete':index===active?'current':'pending'} aria-current={index===active?'step':undefined}><span>{stage}</span></li>)}
  </ol>;
}

export default function IncidentInbox({events,minute}:{events:IncidentEvent[];minute:number}){
  const delivered=events.filter(event=>event.observed_at<=event.received_at&&event.received_at<=minute);
  const latest=new Map(delivered.map(event=>[event.incident_id,event]));
  const active=[...latest.values()].filter(event=>event.status==='open');
  const card=(event:IncidentEvent)=><article key={event.id} className={`incident-card ${event.priority==='urgent'&&event.status==='open'?'urgent':''}`}>
    <div><strong>{pretty(event.kind)}</strong><span>{event.status==='open'?'Open request':'Update / resolved'}</span></div>
    <p>{event.text}</p><small>{event.source} · {pretty(event.channel.replace('synthetic_',''))} · cell {event.location.join(', ')}<br/>Observed {event.observed_at}m → received {event.received_at}m{event.received_at>minute-2?' · New':''}</small>
  </article>;
  return <section className="incident-inbox" aria-label="Incoming incident updates">
    <div className="section-heading"><h2>Calls & crew updates</h2><span>{active.length} open</span></div>
    <p className="hint">Requests stay here until a resolution arrives. Updates describe local conditions; a resolved request does not mean the fire is out.</p>
    {active.length?active.map(card):<p className="muted">No outstanding requests received.</p>}
    <details><summary>Received update history · {delivered.length}</summary>{[...delivered].reverse().map(card)}</details>
  </section>;
}
