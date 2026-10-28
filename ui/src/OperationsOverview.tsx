import {baseName,groupName,pretty,resourceKindName} from './model';
import {ResourceTiming} from './IncidentInbox';
import ActivityFeed from './ActivityFeed';
import MapGlyph from './MapGlyph';
import type {District,Frame,Observation} from './types';

export function populationSummary(groups:District[]){
  const totals={total:0,sheltering:0,moving:0,safe:0,unknown:0,awaiting:0,exposed:0,exposureUnknown:0};
  for(const group of groups){
    totals.total+=group.population;
    if(group.exposed===null)totals.exposureUnknown+=group.population;
    else totals.exposed+=group.exposed;
    if(group.status===null)totals.unknown+=group.population;
    else if(group.status==='safe')totals.safe+=group.population;
    else if(group.status==='evacuating'||group.status==='blocked')totals.moving+=group.population;
    else if(group.shelter_active===true)totals.sheltering+=group.population;
    else totals.awaiting+=group.population;
  }
  return totals;
}

export default function OperationsOverview({observation,frames=[],onDecision=()=>{},onReport,onResource,onPanel}:{observation:Observation;frames?:Frame[];onDecision?:(frame:number)=>void;onReport:(id:string)=>void;onResource:(id:string)=>void;onPanel:(name:string)=>void}){
  const deliveredReports=observation.reports.filter(report=>report.received_at<=observation.minute&&report.observed_at<=report.received_at);
  const reports=deliveredReports.slice().sort((first,second)=>second.received_at-first.received_at).slice(0,2);
  const priorities=['en_route','mobilizing','on_scene','reserved'];
  const resources=observation.resources.slice().sort((first,second)=>{
    const rank=(state:string|null)=>priorities.includes(state??'')?priorities.indexOf(state!):4;
    return rank(first.state)-rank(second.state);
  }).slice(0,2);
  const population=populationSummary(observation.districts);
  const baseLabels=new Map(observation.bases.map(base=>[base.id,base.name]));
  const latestEvents=new Map((observation.incident_events??[]).filter(event=>event.observed_at<=event.received_at&&event.received_at<=observation.minute)
    .slice().sort((first,second)=>first.received_at-second.received_at).map(event=>[event.incident_id,event]));
  const openEvents=[...latestEvents.values()].filter(event=>event.status==='open');
  const urgentEvents=openEvents.filter(event=>event.priority==='urgent');
  const counts=[['sheltering',population.sheltering],['moving / blocked',population.moving],['at refuge',population.safe],['status unknown',population.unknown],['not reported protected',population.awaiting]] as const;
  return <section className="operations-overview" aria-label="Operations overview">
    {openEvents.length>0&&<button className={`open-incident-summary${urgentEvents.length?' urgent':''}`} onClick={()=>onReport('')}>
      {openEvents.length} open {openEvents.length===1?'request':'requests'}{urgentEvents.length?` · ${urgentEvents.length} urgent`:''}<span>Review updates ›</span>
    </button>}
    <ActivityFeed observation={observation} frames={frames} onReport={onReport} onDecision={onDecision}/>
    <div className="section-heading overview-heading"><h2>Resources</h2><button className="text-button" onClick={()=>onPanel('Resources')}>View all ({observation.resources.length}) ›</button></div>
    {resources.map(unit=>{
      const offer=[...(observation.offers??[])].reverse().find(item=>item.resource_id===unit.id&&item.status==='offered'&&item.expires_at>observation.minute);
      const readback=offer?[...(observation.readbacks??[])].reverse().find(item=>item.offer_id===offer.id&&item.received_at<=observation.minute):undefined;
      const mismatch=!!offer&&!!readback&&readback.status==='awaiting_confirmation'&&['resource_id','sector_id','mission'].some(field=>readback[field as keyof typeof readback]!==offer[field as keyof typeof offer]);
      const pending=offer?(mismatch?'Readback mismatch · correction needed':!readback?'Offer received · awaiting readback':readback.status==='correction_pending'?'Correction sent · awaiting readback':readback.status==='awaiting_confirmation'?'Readback awaiting confirmation':readback.status==='confirmed'?'Readback confirmed · awaiting authorization':`Readback ${pretty(readback.status)}`):'';
      return <article className="unit-card overview-unit" key={unit.id}><button className="unit-card-heading" onClick={()=>onResource(unit.id)}>
        <MapGlyph kind={unit.kind==='crew'?'unit':unit.kind==='aircraft'?'plane':'truck'}/><span><strong>{resourceKindName(unit.kind)}</strong><small>{baseLabels.get(unit.home_base)??baseName(unit.home_base)}</small></span>
        <span className={`resource-status ${unit.state==='on_scene'?'on-scene':''}`}>{pretty(unit.state??'status unknown')}</span><span aria-hidden="true">›</span></button>
        {pending&&<p className={`readback-notice${mismatch?' warning':''}`}>{pending}</p>}<ResourceTiming resource={unit} minute={observation.minute} compact/></article>;
    })}
    {!resources.length&&<p className="muted">No resources reported.</p>}
    <div className="section-heading overview-heading"><h2>Population</h2><button className="text-button" onClick={()=>onPanel('Orders')}>Protect ›</button></div>
    <div className="population-card"><strong>{population.total.toLocaleString()} people</strong><p>{counts.filter(([,count])=>count>0).map(([label,count])=>`${count} ${label}`).join(' · ')||'No population groups'}</p>{population.exposed>0&&<p className="danger">{population.exposed} reported exposed · shelter/refuge does not erase earlier exposure</p>}{population.exposureUnknown>0&&<p className="hint">Exposure unknown for {population.exposureUnknown} people</p>}<small>{observation.districts.map(group=>groupName(group.id)).join(' · ')} · synthetic placements</small></div>
    <h2 className="overview-heading">Actions</h2>
    <div className="quick-actions"><button onClick={()=>onReport(reports[0]?.id??'')}><span aria-hidden="true">⌕</span> Investigate report <span aria-hidden="true">›</span></button><button onClick={()=>onPanel('Resources')}><span aria-hidden="true">△</span> Request support <span aria-hidden="true">›</span></button><button onClick={()=>onPanel('Orders')}><span aria-hidden="true">◇</span> Protect population <span aria-hidden="true">›</span></button></div>
    <p className="hint">Shortcuts open existing controls. No order is sent until you apply the draft; checking is optional.</p>
  </section>;
}
