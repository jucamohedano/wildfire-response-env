import {useState} from 'react';
import {decisionActivity,incomingActivity,simulationClock} from './activity';
import type {Activity} from './activity';
import type {Frame,Observation} from './types';

function Entry({row,onReport,onDecision}:{row:Activity;onReport:(id:string)=>void;onDecision:(frame:number)=>void}){
  return <li className={`activity-entry ${row.type}`}><time title={`${row.minute} simulation minutes`}>{simulationClock(row.minute)}</time><div><button className="activity-title" onClick={()=>{if(row.reportId)onReport(row.reportId);else if(row.frame!==undefined)onDecision(row.frame);}} disabled={!row.reportId&&row.frame===undefined}>{row.title}</button><details><summary>{row.type==='decision'||row.type==='rejected'?`Recorded by ${simulationClock(row.availableAt)} · details`:'Read update'}</summary><p>{row.detail}</p></details></div></li>;
}

export default function ActivityFeed({observation,frames,onReport,onDecision}:{observation:Observation;frames:Frame[];onReport:(id:string)=>void;onDecision:(frame:number)=>void}){
  const [expanded,setExpanded]=useState(false);
  const incoming=incomingActivity(observation,frames),decisions=decisionActivity(frames,observation);
  const limit=expanded?Math.max(incoming.length,decisions.rows.length):6;
  return <section className="activity-board" aria-label="Incoming updates and controller decisions">
    <section aria-label="Incoming updates"><div className="section-heading"><h2>Incoming updates</h2><span>{incoming.length} received</span></div><ol className="activity-list">{incoming.slice(0,limit).map(row=><Entry key={row.id} row={row} onReport={onReport} onDecision={onDecision}/>)}</ol>{!incoming.length&&<p className="muted">No updates received yet.</p>}</section>
    <section aria-label="Controller decisions"><div className="section-heading"><h2>Controller decisions</h2><span>{decisions.rows.length} submissions</span></div><ol className="activity-list decision-list-feed">{decisions.rows.slice(0,limit).map(row=><Entry key={row.id} row={row} onReport={onReport} onDecision={onDecision}/>)}</ol>{!decisions.rows.length&&<p className="muted">No orders submitted in the recorded decisions so far.</p>}<p className="wait-summary">{decisions.waits} recorded wait {decisions.waits===1?'decision':'decisions'} · not hidden activity</p></section>
    {(incoming.length>6||decisions.rows.length>6)&&<button className="text-button" onClick={()=>setExpanded(value=>!value)}>{expanded?'Show recent activity':'Show all received activity'}</button>}
  </section>;
}
