import type {IncidentEvent,Resource} from './types';
import {pretty} from './model';

export function ResourceTiming({resource,minute,compact=false}:{resource:Resource;minute?:number;compact?:boolean}){
  const arrival=resource.arrival_at;
  const awaitingArrival=resource.state!==null&&resource.state!=='on_scene'&&arrival!==null&&arrival!==undefined&&minute!==undefined&&minute>=arrival;
  const remaining=arrival===null||arrival===undefined?null:minute===undefined?resource.arrival_in_minutes:Math.max(0,arrival-minute);
  const timing=resource.state===null?'Awaiting base status':resource.state==='on_scene'?'On scene · reported':awaitingArrival
    ?`ETA ${arrival} minutes ${minute===arrival?'reached':'passed'} · awaiting status update`:arrival!==null&&arrival!==undefined
      ?`Estimated arrival at ${arrival} minutes${remaining===null||remaining===undefined?'':` · ${remaining} minutes remaining`}`
      :resource.due_at===null||resource.due_at===undefined?'No arrival scheduled':`Next phase expected at ${resource.due_at} minutes`;
  const movement=resource.movement;
  return <div className={movement?'movement-summary':'resource-timing'}>
    <ArrivalStages state={resource.state}/>
    <small className={`arrival-time${awaitingArrival?' overdue':''}`} title={resource.travel_time_basis}>{timing}</small>
    {movement&&!compact&&<><small>{pretty(resource.state??'unknown')} · {(movement.distance_m/1000).toFixed(1)} kilometres · {movement.direction==='return'?'returning to staging':'outbound mission'}</small>
      <small>Position observed at {movement.observed_at} minutes · received at {movement.received_at} minutes</small>
      <small>{movement.legs.some(leg=>leg.mode==='walk')?'Includes an assumed walking access leg.':resource.kind==='aircraft'?'Illustrative direct flight; not an approved flight plan.':'Road geometry is source-derived; speed and availability are assumed.'}</small></>}
    {movement&&compact&&<small className="timing-received">Position received at {movement.received_at} minutes · simulated</small>}
  </div>;
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
    <p>{event.text}</p><small>{event.source} · {pretty(event.channel.replace('synthetic_',''))} · cell {event.location.join(', ')}<br/>Observed {event.observed_at} minutes → received {event.received_at} minutes{event.received_at>minute-2?' · New':''}</small>
  </article>;
  return <section className="incident-inbox" aria-label="Incoming incident updates">
    <div className="section-heading"><h2>Calls & crew updates</h2><span>{active.length} open</span></div>
    <p className="hint">Requests stay here until a resolution arrives. Updates describe local conditions; a resolved request does not mean the fire is out.</p>
    {active.length?active.map(card):<p className="muted">No outstanding requests received.</p>}
    <details><summary>Received update history · {delivered.length}</summary>{[...delivered].reverse().map(card)}</details>
  </section>;
}
