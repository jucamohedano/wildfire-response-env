import {groupName,pretty,resourceKindName} from './model';
import type {Action,Frame,Observation} from './types';

export interface Activity {id:string;minute:number;availableAt:number;title:string;detail:string;type:'report'|'update'|'sensor'|'decision'|'rejected';reportId?:string;frame?:number}

const unitLabel=(id:string,observation:Observation)=>{
  const unit=observation.resources.find(resource=>resource.id===id);
  return unit?resourceKindName(unit.kind):pretty(id);
};

function actionDescriptions(action:Action,observation:Observation):string[]{
  const descriptions:string[]=[];
  for(const [field,values] of Object.entries(action)){
    if(!Array.isArray(values)||!values.length)continue;
    for(const value of values){
      if(field==='requests'&&value&&typeof value==='object'){
        const request=value as {resource_id:string;mission:string;sector_id:string};
        descriptions.push(`${request.mission==='recon'?'Reconnaissance':'Suppression'} requested · ${unitLabel(String(request.resource_id??'Unknown resource'),observation)} · ${request.sector_id??'Unknown target'}`);
      }else if(field==='protection_orders'&&value&&typeof value==='object'){
        const order=value as {action:string;recipient:string;channel:string};
        descriptions.push(`${pretty(typeof order.action==='string'?order.action:undefined)} order sent · ${typeof order.recipient==='string'?groupName(order.recipient):'Unknown recipient'} · ${pretty(typeof order.channel==='string'?order.channel:undefined)}`);
      }else if(field==='authorizations')descriptions.push(`Dispatch authorized · ${value}`);
      else if(field==='readback_confirmations')descriptions.push(`Readback confirmed · ${value}`);
      else if(field==='readback_corrections')descriptions.push('Readback correction sent');
      else if(field==='clarifications')descriptions.push(`Location clarification requested · ${value}`);
      else if(field==='triage'&&value&&typeof value==='object'){
        const assessment=value as {assessment:string;report_ids:string[]};
        descriptions.push(`Report assessment · ${pretty(typeof assessment.assessment==='string'?assessment.assessment:undefined)} · ${Array.isArray(assessment.report_ids)?assessment.report_ids.join(', '):'Unknown reports'}`);
      }else if(field==='releases')descriptions.push(`Resource release requested · ${unitLabel(String(value),observation)}`);
      else if(field==='messages'&&value&&typeof value==='object'){
        const message=value as {purpose:string;recipient:string};
        descriptions.push(`${pretty(typeof message.purpose==='string'?message.purpose:undefined)} message sent · ${message.recipient}`);
      }else descriptions.push(`${pretty(field)} · ${typeof value==='string'?value:JSON.stringify(value)}`);
    }
  }
  return descriptions;
}

export function decisionActivity(frames:Frame[],selected:Observation){
  const rows:Activity[]=[];
  let waits=0;
  for(let index=1;index<frames.length;index++){
    const frame=frames[index],previous=frames[index-1].observation;
    if(frame.observation.decision>selected.decision||frame.observation.minute>selected.minute)continue;
    if(frame.action===null)continue;
    const rejected=!!frame.action_error||Object.keys(frame.action).some(key=>key.startsWith('_invalid'));
    const descriptions=actionDescriptions(frame.action,previous);
    if(!rejected&&!descriptions.length){waits++;continue;}
    rows.push({id:`decision-${frame.observation.decision}`,minute:previous.minute,availableAt:frame.observation.minute,
      title:rejected?'Action rejected':descriptions[0],detail:rejected?'Time advanced; previous missions remained in force.':descriptions.slice(1).join('\n')||'Submitted, not proof of delivery or execution.',
      type:rejected?'rejected':'decision',frame:index});
  }
  return {rows:rows.reverse(),waits};
}

export function incomingActivity(observation:Observation,frames:Frame[]=[]):Activity[]{
  const rows:Activity[]=[];
  for(const report of observation.reports){
    if(report.observed_at>report.received_at||report.received_at>observation.minute)continue;
    rows.push({id:'report-'+report.id,minute:report.received_at,availableAt:report.received_at,
      title:`${pretty(report.channel.replace('synthetic_',''))} report · ${report.id}`,
      detail:`${report.text}\nObserved at ${report.observed_at} minutes; received at ${report.received_at} minutes. A claim is not confirmation.`,type:'report',reportId:report.id});
  }
  for(const event of observation.incident_events??[]){
    if(event.observed_at>event.received_at||event.received_at>observation.minute)continue;
    rows.push({id:'incident-'+event.id,minute:event.received_at,availableAt:event.received_at,
      title:pretty(event.kind),detail:`${event.text}\n${event.source} · observed at ${event.observed_at} minutes · ${event.status}`,type:'update'});
  }
  for(const [source,events] of [['ack',observation.acknowledgements],['readback',observation.readback_events]] as const){
    for(const [index,event] of events.entries()){
      if(event.minute>observation.minute)continue;
      rows.push({id:`${source}-${index}`,minute:event.minute,availableAt:event.minute,title:pretty(event.kind),
        detail:event.resource_id?unitLabel(event.resource_id,observation):event.order_id??event.recipient??event.source??'Received operational acknowledgement',type:'update'});
    }
  }
  let previousWind='',previousSensors='',sensorRefreshes=0;
  let sensorRow:Activity|undefined;
  for(const frame of frames){
    const current=frame.observation;
    if(current.decision>observation.decision||current.minute>observation.minute)continue;
    const wind=JSON.stringify(current.wind);
    if(current.wind&&wind!==previousWind)rows.push({id:`wind-${current.decision}`,minute:current.minute,availableAt:current.minute,title:'Simulated wind update',detail:`[${current.wind.join(', ')}] in model units. Available by this decision; not a measured fire-front forecast.`,type:'update'});
    previousWind=wind;
    const visibleSensors=(current.sensor_memory??[]).filter(cell=>cell.observed_at<=current.minute);
    const sensors=JSON.stringify(visibleSensors.map(cell=>[cell.x,cell.y,cell.burning]));
    if(visibleSensors.length){
      if(sensors!==previousSensors){sensorRefreshes=0;sensorRow=undefined;}
      sensorRefreshes++;
      const detail=`${visibleSensors.filter(cell=>cell.burning).length} recorded burning cells · ${sensorRefreshes} snapshots of this footprint. Latest available by this decision; older observations can be stale. No fire perimeter or heat is inferred.`;
      if(sensorRow)Object.assign(sensorRow,{minute:current.minute,availableAt:current.minute,detail});
      else{sensorRow={id:`sensor-${current.decision}`,minute:current.minute,availableAt:current.minute,title:'Sensor evidence available',detail,type:'sensor'};rows.push(sensorRow);}
    }
    previousSensors=sensors;
  }
  return rows.sort((first,second)=>second.minute-first.minute||first.id.localeCompare(second.id));
}

export const simulationClock=(minute:number)=>`${String(Math.floor(minute/60)).padStart(2,'0')}:${String(minute%60).padStart(2,'0')}`;
