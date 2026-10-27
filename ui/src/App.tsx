import {useEffect,useRef,useState} from 'react';
import type {FormEvent,ReactNode} from 'react';
import MapView from './MapView';
import IncidentInbox,{ResourceTiming} from './IncidentInbox';
import OperationsOverview from './OperationsOverview';
import ReplayControls from './ReplayControls';
import {request,requestText} from './api';
import {currentWorld,groupName,parseAction,pretty,resourceKindName,stageAction} from './model';
import {finishEpisode} from './playback';
import type {Action,Check,Config,Envelope,Frame,History,Imported,MapMetadata,Observation,World} from './types';

const initialDraft='{}';
const frameOf=(state:Envelope):Frame=>({observation:state.observation,action:state.last_action??null,reward:state.reward,total_reward:state.total_reward,terminated:state.terminated,truncated:state.truncated,action_error:state.info.error??null});

function Field({label,children}:{label:string;children:ReactNode}){return <label className="field"><span>{label}</span>{children}</label>;}
function Empty({children}:{children:ReactNode}){return <p className="muted empty">{children}</p>;}

export default function App(){
  const [config,setConfig]=useState<Config|null>(null),[metadata,setMetadata]=useState<MapMetadata|null>(null),[live,setLive]=useState<Envelope|null>(null);
  const [frames,setFrames]=useState<Frame[]>([]),[imported,setImported]=useState<Imported|null>(null),[viewMode,setViewMode]=useState<'live'|'history'|'imported'>('live'),[frameIndex,setFrameIndex]=useState(0);
  const [tab,setTab]=useState('Overview'),[scenario,setScenario]=useState(''),[policy,setPolicy]=useState('manual'),[playing,setPlaying]=useState(false),[speed,setSpeed]=useState(700);
  const [generating,setGenerating]=useState(false),[replaying,setReplaying]=useState(false);
  const [draft,setDraft]=useState(initialDraft),[check,setCheck]=useState<Check|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[verification,setVerification]=useState<unknown>(null);
  const [reportDetails,setReportDetails]=useState(false);
  const [selectedReport,setSelectedReport]=useState(''),[selectedResource,setSelectedResource]=useState(''),[selectedDistrict,setSelectedDistrict]=useState(''),[world,setWorld]=useState<World|null>(null),[showTruth,setShowTruth]=useState(false);
  const episode=useRef(''),fileInput=useRef<HTMLInputElement>(null),stopGeneration=useRef(false);
  const inspectorPanel=useRef<HTMLDivElement>(null);
  useEffect(()=>{const section=inspectorPanel.current?.querySelector(`[data-section="${tab}"]`);if(section&&tab!=='Overview')section.scrollIntoView({block:'nearest'});},[tab]);
  const displayedFrames=viewMode==='imported'?imported?.frames??[]:frames;
  const current=viewMode==='live'&&live?frameOf(live):displayedFrames[frameIndex];
  const observation=current?.observation;
  const readOnly=viewMode!=='live',done=!!live&&(live.terminated||live.truncated),canAct=!!live&&!readOnly&&!busy&&!playing&&!done;
  const hasDraft=draft.trim()!=='{}'&&draft.trim()!=='';

  function apply(next:Envelope,reset=false){
    const changed=episode.current!==next.episode_id;episode.current=next.episode_id;setLive(next);setWorld(null);setVerification(null);setCheck(null);
    setFrames(previous=>changed||reset?[frameOf(next)]:[...previous.filter(frame=>frame.observation.decision<next.observation.decision),frameOf(next)]);
    if(reset||changed){setViewMode('live');setImported(null);setDraft(initialDraft);setSelectedReport('');setSelectedResource('');setSelectedDistrict('');setReportDetails(false);}
    if(next.info.error){setError(`Action rejected: ${next.info.error}. Simulation time advanced; existing missions remain in force.`);setPlaying(false);}
    if(next.terminated||next.truncated)setPlaying(false);
  }
  async function operate(operation:()=>Promise<void>){
    setBusy(true);setError('');setNotice('');
    try{await operation();}catch(error){setError(error instanceof Error?error.message:String(error));setPlaying(false);setReplaying(false);}finally{setBusy(false);}
  }
  useEffect(()=>{
    void operate(async()=>{const [configuration,map,state,history]=await Promise.all([request<Config>('config'),request<MapMetadata>('map'),request<Envelope>('state'),request<History>('history')]);setConfig(configuration);setMetadata(map);apply(state,true);setFrames(history.frames);setScenario(state.observation.task_id);});
  },[]);
  async function advance(){
    if(!live||readOnly||busy)return;
    if(hasDraft){setPlaying(false);setError('Apply or clear the draft before advancing playback.');return;}
    await operate(async()=>{const next=await request<Envelope>('advance',{episode_id:live.episode_id,expected_decision:live.observation.decision,policy:policy==='manual'?'passive':policy});apply(next);});
  }
  useEffect(()=>{
    if(!playing||busy||done||readOnly)return;
    const timer=window.setTimeout(()=>void advance(),speed);
    return()=>window.clearTimeout(timer);
  },[playing,busy,live,speed,readOnly,done]);
  useEffect(()=>{
    if(!replaying||!readOnly||busy)return;
    if(frameIndex>=displayedFrames.length-1){setReplaying(false);return;}
    const timer=window.setTimeout(()=>setFrameIndex(index=>index+1),speed);
    return()=>window.clearTimeout(timer);
  },[replaying,readOnly,busy,frameIndex,displayedFrames.length,speed]);
  function selectFrame(index:number){setPlaying(false);setReplaying(false);setFrameIndex(index);if(viewMode==='live')setViewMode('history');setShowTruth(false);}
  function replay(){
    if(replaying){setReplaying(false);return;}
    if(viewMode==='live'||frameIndex>=displayedFrames.length-1)selectFrame(0);
    setReplaying(true);
  }
  async function generate(){
    if(!live||!config||busy||readOnly||done||hasDraft||playing)return;
    setGenerating(true);setPlaying(false);setReplaying(false);setShowTruth(false);stopGeneration.current=false;
    await operate(async()=>{
      const final=await finishEpisode(live,config.max_decisions,
        state=>request<Envelope>('advance',{episode_id:state.episode_id,expected_decision:state.observation.decision,policy:policy==='manual'?'passive':policy}),
        state=>apply(state),()=>stopGeneration.current);
      if(final.info.error)return;
      if(final.terminated||final.truncated){setViewMode('history');setFrameIndex(0);setNotice('Full trajectory ready. Drag Start → Finish or play the read-only replay.');}
      else setNotice(`Generation stopped at ${final.observation.minute} model minutes. Recorded decisions are available on the slider.`);
    });
    setGenerating(false);
  }
  useEffect(()=>{
    let active=true;
    if(!showTruth||!live||readOnly){setWorld(null);return;}
    request<World>('evaluator').then(value=>{if(active&&value.episode_id===live.episode_id&&value.decision===live.observation.decision)setWorld(value);}).catch(error=>{if(active){setShowTruth(false);setError(String(error));}});
    return()=>{active=false;};
  },[showTruth,live,readOnly]);
  function stage(field:string,value:unknown){try{setDraft(stageAction(draft,field,value));setCheck(null);setNotice('Order staged. Check and apply the draft to send it.');}catch(error){setError(String(error));}}
  function form(event:FormEvent<HTMLFormElement>,field:string,build:(data:FormData)=>unknown){event.preventDefault();try{stage(field,build(new FormData(event.currentTarget)));}catch(error){setError(String(error));}}
  async function checkDraft(){if(!canAct||!live)return;await operate(async()=>{const action=parseAction(draft);const result=await request<{action_check:Check}>('check',{episode_id:live.episode_id,expected_decision:live.observation.decision,action});setCheck(result.action_check);});}
  async function commit(){if(!canAct||!live)return;await operate(async()=>{const action=parseAction(draft);const next=await request<Envelope>('action',{episode_id:live.episode_id,expected_decision:live.observation.decision,action});apply(next);if(!next.info.error){setDraft(initialDraft);setNotice('Draft committed. Time advanced by two model minutes.');}});}
  async function reset(){setPlaying(false);setReplaying(false);await operate(async()=>{const state=await request<Envelope>('reset',{task_id:scenario});apply(state,true);setNotice('New episode started.');});}
  async function returnLive(){setPlaying(false);setReplaying(false);await operate(async()=>{const [state,history]=await Promise.all([request<Envelope>('state'),request<History>('history')]);apply(state);setFrames(history.frames);setViewMode('live');setImported(null);setFrameIndex(history.frames.length-1);setScenario(state.observation.task_id);});}
  async function exportTrace(){await operate(async()=>{const text=await requestText('trajectory');const url=URL.createObjectURL(new Blob([text],{type:'application/json'}));const anchor=document.createElement('a');anchor.href=url;anchor.download=`${live?.observation.task_id??'wildfire'}-trajectory.json`;anchor.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000);setNotice(done?'Public trajectory exported. Private evaluator evidence is excluded.':'In-progress public trace exported; only completed traces can be imported or verified.');});}
  async function openTrace(file:File){
    setPlaying(false);setReplaying(false);
    await operate(async()=>{
      if(file.size>19_000_000)throw new Error('Trace file exceeds 19 MB.');
      const stream=file.name.endsWith('.gz')?file.stream().pipeThrough(new DecompressionStream('gzip')):file.stream();
      const reader=stream.getReader(),decoder=new TextDecoder();let text='',bytes=0;
      while(true){const chunk=await reader.read();if(chunk.done)break;bytes+=chunk.value.byteLength;if(bytes>19_000_000){await reader.cancel();throw new Error('Expanded trace exceeds 19 MB.');}text+=decoder.decode(chunk.value,{stream:true});}
      text+=decoder.decode();
      const result=await request<Imported>('import',{trajectory_json:text});setImported(result);setViewMode('imported');setFrameIndex(0);setShowTruth(false);setNotice('Verified trace opened in read-only replay. The live episode was not changed.');
    });
  }
  const controlDisabled=!canAct;
  if(!observation||!config||!metadata)return <main className="loading"><h1>Wildfire Ops</h1><p>{error||'Loading the local simulator and terrain…'}</p><p className="muted">Start the local backend on port 8767. No API key or model is needed.</p><button onClick={()=>window.location.reload()}>Retry</button></main>;
  const report=observation.reports.find(report=>report.id===selectedReport)??observation.reports[0];
  const events=[...observation.acknowledgements,...observation.readback_events].sort((first,second)=>second.minute-first.minute);
  const lastWeather=observation.weather_context.records.at(-1),district=observation.districts.find(group=>group.id===selectedDistrict)??observation.districts[0];
  const selectedCase=config.cases.find(item=>item.id===scenario),draftCount=(()=>{try{return Object.values(parseAction(draft)).reduce<number>((count,value)=>count+(Array.isArray(value)?value.length:1),0);}catch{return 0;}})();
  const visibleWorld=currentWorld(world,live?.episode_id,observation.decision,readOnly,showTruth);
  const lastRecorded=displayedFrames.at(-1);
  const horizon=observation.clock?.horizon_minutes??config.max_decisions*config.ticks_per_action;
  const clock=(minute:number)=>`${String(Math.floor(minute/60)).padStart(2,'0')}:${String(minute%60).padStart(2,'0')}`;
  return <div className="app">
    <header className="app-header"><div className="identity"><h1>Wildfire Ops</h1><span className="tag">Synthetic simulation · Not live</span></div><div className="scenario-control"><label>Scenario <select aria-label="Scenario" value={readOnly?observation.task_id:scenario} disabled={busy||playing||readOnly} onChange={event=>setScenario(event.target.value)}><optgroup label="Benchmark · delivered evidence">{config.cases.filter(item=>item.mode==='benchmark').map(item=><option key={item.id}>{item.id}</option>)}</optgroup><optgroup label="Reactive incidents · calls & crew updates">{config.cases.filter(item=>item.mode==='reactive').map(item=><option key={item.id}>{item.id}</option>)}</optgroup><optgroup label="Development">{config.cases.filter(item=>item.mode==='development').map(item=><option key={item.id}>{item.id}</option>)}</optgroup><optgroup label="Training stress smoke">{config.cases.filter(item=>item.mode==='stress').map(item=><option key={item.id}>{item.id}</option>)}</optgroup></select></label><button disabled={busy||readOnly} onClick={()=>void reset()}>{scenario===live?.observation.task_id?'Reset episode':'Load selected'}</button></div></header>
    <div className="status-strip"><span><b>{clock(observation.minute)} / {clock(horizon)}</b> Simulation time · decision {observation.decision}/{config.max_decisions}</span><span>{observation.daylight===null?'Daylight unknown':observation.daylight?'Daylight':'Night response'} · aircraft {observation.aircraft_operable===null?'readiness unknown':observation.aircraft_operable?'operable':'grounded'}</span><span>Slope {observation.landscape.slope_enabled?'experimental · on':'off'}</span><span className={readOnly?'badge amber':'badge'}>{readOnly?'Read-only replay':current.terminated?'Episode complete':current.truncated?'Episode truncated':'Live episode'}</span>{config.evaluation_available&&<label className="truth-switch"><input type="checkbox" checked={showTruth&&!readOnly} disabled={readOnly} onChange={event=>setShowTruth(event.target.checked)}/> Evaluator truth</label>}</div>
    {error&&<div className="banner error" role="alert">{error} <button disabled={busy} onClick={()=>void returnLive()}>Refresh state</button></div>}{notice&&<div className="banner" role="status">{notice}</div>}
    {readOnly&&<div className="banner replay">{viewMode==='imported'?'Imported verified trajectory':'Earlier recorded decision'} · actions are disabled. <button disabled={busy} onClick={()=>void returnLive()}>Return to live episode</button></div>}
    {visibleWorld&&<div className="banner warning">Evaluator-only fire truth is visible. It is not part of the actor observation or public export.</div>}
    {!readOnly&&scenario!==live?.observation.task_id&&<div className="banner">Selected scenario is not loaded yet. Press Load selected; current controls still operate on {live?.observation.task_id}.</div>}
    <div className="workspace">
      <div className="map-workspace">
      <MapView metadata={metadata} observation={observation} world={visibleWorld} selectedReport={selectedReport} animating={playing||replaying} replay={
      <ReplayControls minute={observation.minute} recordedMinutes={lastRecorded?.observation.minute??0} frameCount={displayedFrames.length} frameIndex={viewMode==='live'?displayedFrames.length-1:frameIndex} busy={busy} replaying={replaying} onPlay={replay} onPause={()=>setReplaying(false)} onReset={()=>selectFrame(0)} onSeek={selectFrame}/>
      } focusKey={viewMode==='imported'?`imported:${imported?.task_id}`:live?.episode_id??''} onSelect={selection=>{if(selection.type==='report'){setSelectedReport(selection.id);setReportDetails(true);setTab('Reports');}else if(selection.type==='resource'){setSelectedResource(selection.id);setTab('Resources');}else{setSelectedDistrict(selection.id);setTab('Orders');}}}/>
      </div>
      <aside className="inspector unified-inspector" aria-label="Operations dashboard"><header className="dashboard-heading"><strong>Operations control</strong><span>Updates → decisions → confirmed effects</span></header>
      <div className="panel-content" ref={inspectorPanel}>
        <OperationsOverview observation={observation} frames={displayedFrames} onDecision={selectFrame} onPanel={setTab} onReport={id=>{setSelectedReport(id);setReportDetails(true);setTab('Reports');}} onResource={id=>{setSelectedResource(id);setTab('Resources');}}/>
        <details data-section="Reports" className="report-details operations-section" open={reportDetails} onToggle={event=>setReportDetails(event.currentTarget.open)}><summary>All reports, triage & weather</summary>
        {observation.incident_events&&<IncidentInbox events={observation.incident_events} minute={observation.minute}/>}
        <div className="section-heading"><h2>Received reports</h2><span>{observation.reports.length} received</span></div>
        <p className="muted">Claims are not confirmation. Source role does not establish correctness.</p>
        <div className="report-list">{observation.reports.map(item=><button key={item.id} className={`report-row ${report?.id===item.id?'selected':''}`} onClick={()=>setSelectedReport(item.id)}><span>{pretty(item.channel.replace('synthetic_',''))} · {item.received_at} minutes</span><strong>{item.id}</strong><span>{item.location?`Cell ${item.location.join(', ')}`:'Location unknown'}</span></button>)}</div>
        {report?<article className="selected-report"><h3>{report.id}</h3><p>{report.text}</p><dl><dt>Observed / received</dt><dd>{report.observed_at} minutes / {report.received_at} minutes</dd><dt>Source</dt><dd>{report.source} · {pretty(report.source_role)}</dd><dt>Claimed location</dt><dd>{report.location?.join(', ')??'Unknown'}</dd></dl><div className="button-wrap"><button disabled={controlDisabled||!!report.location||observation.clarification_requests.some(item=>item.report_id===report.id)} onClick={()=>stage('clarifications',report.id)}>Clarify location</button>{['fire_observed','no_fire_observed','defer'].map(assessment=><button key={assessment} disabled={controlDisabled} onClick={()=>stage('triage',{report_ids:[report.id],assessment})}>{pretty(assessment)}</button>)}</div><p className="hint">Conclusive triage requires fresh supporting sensor evidence. Use Resources to request reconnaissance.</p></article>:<Empty>No reports received yet. Future reports are not revealed.</Empty>}
        <section className="weather"><h3>Weather context</h3><p>Simulated wind: <b>[{observation.wind?.join(', ')??'Unknown'}]</b> · relative model units</p><p className="muted">{lastWeather?`${lastWeather.temperature_c??'—'} °C · station wind ${lastWeather.wind_speed_m_s??'—'} m/s · received ${lastWeather.received_at_utc}`:'No current station reading available.'}</p><p className="hint">Estepona station observations are context only, not fire-front weather physics.</p></section>
        </details>
      <details data-section="Resources" className="operations-section" open={tab==='Resources'} onToggle={event=>{if(event.currentTarget.open)setTab('Resources');else if(tab==='Resources')setTab('Overview');}}><summary>Resource requests and dispatch</summary>
        <h2>Resources & base coordination</h2><p className="muted">Track authorization, mobilization, travel and arrival. Amber map rings mark destinations; unit positions and road routes are not simulated.</p>
        {observation.bases.map(base=><section key={base.id}><h3>{base.name}</h3>{observation.resources.filter(unit=>unit.home_base===base.id).map(unit=><div key={unit.id} className={`resource-row ${selectedResource===unit.id?'selected':''}`}><div><strong>{resourceKindName(unit.kind)}</strong><span className="state">{pretty(unit.state)}{unit.kind==='aircraft'?(observation.aircraft_operable===null?' · readiness unknown':!observation.aircraft_operable?' · grounded':''):''}</span></div><small>{unit.capacity_litres?`${unit.water_litres?.toLocaleString()??'Unknown'} / ${unit.capacity_litres.toLocaleString()} L · `:''}{unit.target?`Target ${unit.target.join(', ')}`:'Abstract off-map base'}</small><ResourceTiming resource={unit} minute={observation.minute}/><button disabled={controlDisabled||unit.state===null||unit.state==='available'||unit.state==='unavailable'} onClick={()=>stage('releases',unit.id)}>Stage release</button></div>)}</section>)}
        <form onSubmit={event=>form(event,'requests',data=>({resource_id:String(data.get('resource')),sector_id:String(data.get('sector')),mission:String(data.get('mission'))}))}><h3>Request allocation</h3><Field label="Resource"><select key={selectedResource+live?.episode_id} name="resource" aria-label="Allocation resource" defaultValue={selectedResource||observation.resources[0]?.id} disabled={controlDisabled}>{observation.resources.map(unit=><option key={unit.id} value={unit.id}>{unit.id} · {pretty(unit.state)}</option>)}</select></Field><Field label="Public target"><select key={selectedReport+live?.episode_id} name="sector" aria-label="Allocation target" defaultValue={report?.location?`report:${report.id}`:observation.sectors[0]?.id} disabled={controlDisabled}>{observation.sectors.map(sector=><option key={sector.id} value={sector.id}>{sector.id} · {sector.center.join(', ')}</option>)}</select></Field><Field label="Mission"><select name="mission" disabled={controlDisabled}><option value="recon">Reconnaissance</option><option value="suppress">Suppression</option></select></Field><button disabled={controlDisabled}>Stage allocation request</button></form>
        <h3>Offers & readbacks</h3>{observation.offers.filter(offer=>offer.status==='offered'&&offer.expires_at>observation.minute).map(offer=>{
          const readback=observation.readbacks.filter(readback=>readback.offer_id===offer.id).at(-1),mismatch=readback&&['resource_id','sector_id','mission'].some(field=>readback[field as keyof typeof readback]!==offer[field as keyof typeof offer]);
          return <article className="offer" key={offer.id}><strong>{offer.id}</strong><p>{offer.resource_id} · {offer.sector_id} · {offer.mission}</p><small>Expires at {offer.expires_at} minutes</small>{readback&&<><p className={mismatch?'danger':''}>{readback.text}</p><span className="state">{pretty(readback.status)}</span><div className="button-wrap">{mismatch?<button disabled={controlDisabled||readback.status!=='awaiting_confirmation'} onClick={()=>stage('readback_corrections',{readback_id:readback.id,resource_id:offer.resource_id,sector_id:offer.sector_id,mission:offer.mission})}>Stage corrected readback</button>:<><button disabled={controlDisabled||readback.status!=='awaiting_confirmation'} onClick={()=>stage('readback_confirmations',readback.id)}>Confirm only</button><button disabled={controlDisabled||readback.status!=='awaiting_confirmation'} onClick={()=>{try{let next=stageAction(draft,'readback_confirmations',readback.id);next=stageAction(next,'authorizations',offer.id);setDraft(next);setCheck(null);}catch(error){setError(String(error));}}}>Confirm + authorize</button></>}<button disabled={controlDisabled||readback.status!=='confirmed'} onClick={()=>stage('authorizations',offer.id)}>Authorize</button></div></>}</article>;
        })}{!observation.offers.some(offer=>offer.status==='offered'&&offer.expires_at>observation.minute)&&<Empty>No live offers. Request, receive a readback, then confirm and authorize.</Empty>}
      </details>
      <details data-section="Orders" className="operations-section" open={tab==='Orders'} onToggle={event=>{if(event.currentTarget.open)setTab('Orders');else if(tab==='Orders')setTab('Overview');}}><summary>Population protection and messages</summary>
        <h2>Population protection</h2>{observation.districts.map(group=><button className={`district-row ${district.id===group.id?'selected':''}`} key={group.id} onClick={()=>setSelectedDistrict(group.id)}><strong>{groupName(group.id)} · {group.population}</strong><span>{pretty(group.status)} · exposed {group.exposed??'Unknown'} · smoke dose {group.smoke_dose_units??'Unknown'}</span></button>)}
        <form key={district.id+live?.episode_id} onSubmit={event=>form(event,'protection_orders',data=>({recipient:district.id,action:String(data.get('action')),channel:String(data.get('channel')),resource_id:data.get('channel')==='in_person'?(String(data.get('crew'))||null):null,supersedes:observation.latest_protection_orders[district.id]??null}))}><h3>Order for {groupName(district.id)}</h3><Field label="Instruction"><select name="action" disabled={controlDisabled}><option value="evacuate">Evacuate</option><option value="shelter">Shelter</option><option value="release">Release shelter</option></select></Field><Field label="Channel"><select name="channel" disabled={controlDisabled}><option value="broadcast">Broadcast</option><option value="in_person">In person</option></select></Field><Field label="Crew (in-person only)"><select name="crew" disabled={controlDisabled}><option value="">None · broadcast</option>{observation.resources.filter(unit=>unit.kind==='crew').map(unit=><option key={unit.id} value={unit.id}>{resourceKindName(unit.kind)} · {pretty(unit.state)}</option>)}</select></Field><button disabled={controlDisabled}>Stage protection order</button><p className="hint">Supersedes the latest issued order. In-person warning needs an eligible on-scene crew; Check validates it.</p></form>
        {observation.protection_orders.map(order=><article className="order" key={order.id}><div className="section-heading"><strong>{order.id} · {groupName(order.recipient)}</strong><span className="state">{pretty(order.status)}</span></div><p>{pretty(order.action)} · {pretty(order.channel)} · sequence {order.sequence}</p><ol className="lifecycle">{[['Sent',order.issued_at],['Delivered',order.delivered_at],['Acknowledged',order.acknowledged_at],['Executed',order.executed_at]].map(([label,time])=><li key={label} className={time===null?'pending':'reached'}><span>{label}</span><small>{time===null?'Pending':`${time} minutes`}</small></li>)}</ol>{order.effect&&<p className="hint">Effect: {pretty(order.effect)}</p>}</article>)}
        <form onSubmit={event=>form(event,'messages',data=>({recipient:String(data.get('recipient')),purpose:String(data.get('purpose'))}))}><h3>Other communication</h3><Field label="Recipient"><select name="recipient" disabled={controlDisabled}>{[...observation.bases,...observation.districts].map(recipient=><option key={recipient.id} value={recipient.id}>{recipient.id}</option>)}</select></Field><Field label="Purpose"><select name="purpose" disabled={controlDisabled}><option value="status">Request status</option><option value="inform">Inform only</option></select></Field><button disabled={controlDisabled}>Stage message</button><p className="hint">Inform does not move people. Use a protection order for evacuation.</p></form>
        <details><summary>Pending deliveries ({observation.pending_deliveries.length})</summary><pre>{JSON.stringify(observation.pending_deliveries,null,2)}</pre></details>
      </details>
      <details data-section="History" className="operations-section" open={tab==='History'} onToggle={event=>{if(event.currentTarget.open)setTab('History');else if(tab==='History')setTab('Overview');}}><summary>Recorded actions and acknowledgements</summary>
        <h2>Recorded decisions</h2><p className="muted">Only received evidence and committed actions. Selecting a past frame is read-only.</p><div className="decision-list">{displayedFrames.map((frame,index)=><button key={index} className={readOnly&&index===frameIndex?'selected':''} onClick={()=>selectFrame(index)}>Decision {frame.observation.decision} · {frame.observation.minute} minutes <span>+{frame.reward.toFixed(2)}</span></button>)}</div><h3>Selected committed action</h3><pre>{current.action===null?'Initial state or action available in the exported trace.':JSON.stringify(current.action,null,2)}</pre><h3>Events & acknowledgements</h3>{events.map((event,index)=><div className="event" key={index}><time>{event.minute} minutes</time><div><strong>{pretty(event.kind)}</strong><small>{event.resource_id??event.order_id??event.recipient??event.source??''}</small></div></div>)}{!events.length&&<Empty>No events received yet.</Empty>}
      </details>
      </div>
      <div className="draft"><div className="section-heading"><strong>Action draft</strong><span>{draftCount} staged</span></div><details><summary>Advanced JSON · all action fields</summary><p className="hint">Requests, authorizations, releases, messages, readback confirmations/corrections, triage, clarifications and protection_orders can share one committed action.</p><textarea aria-label="Action JSON" spellCheck={false} rows={9} value={draft} disabled={controlDisabled} onChange={event=>{setDraft(event.target.value);setCheck(null);}}/></details><div className="draft-buttons"><button disabled={controlDisabled} onClick={()=>void checkDraft()}>Check draft</button><button className="primary" disabled={controlDisabled} onClick={()=>void commit()}>Apply draft · +2 minutes</button><button disabled={controlDisabled} onClick={()=>{setDraft(initialDraft);setCheck(null);}}>Clear</button></div>{check&&<p className={check.valid?'check-good':'danger'}>{check.valid?'Valid at this decision':check.error} · checks left {String(check.remaining_checks??'—')}</p>}</div>
      </aside>
    </div>
    <section className="playback" aria-label="Simulation controls"><div className="run-controls">{generating?<button onClick={()=>{stopGeneration.current=true;}}>Stop generation</button>:<button disabled={busy||readOnly||done||playing||hasDraft} onClick={()=>void generate()}>Run to end</button>}<button className="primary" disabled={!playing&&(busy||readOnly||done||hasDraft)} onClick={()=>setPlaying(value=>!value)}>{playing?'Pause live':'Run live'}</button><button disabled={busy||playing||readOnly||done||hasDraft} onClick={()=>void advance()}>Step · +2 minutes</button><label>Policy <select aria-label="Playback policy" disabled={playing||busy||readOnly} value={policy} onChange={event=>setPolicy(event.target.value)}><option value="manual">Manual / wait</option><option value="evacuate">{selectedCase?.mode==='benchmark'?'Cooperative reference':'Scripted evacuation'}</option><option value="shelter">{selectedCase?.mode==='benchmark'?'Public dispatcher':'Scripted shelter'}</option><option value="passive">Passive</option></select></label><label>Playback <select aria-label="Playback speed" value={speed} onChange={event=>setSpeed(Number(event.target.value))}><option value={1400}>Normal</option><option value={700}>Fast</option><option value={200}>Fastest</option></select></label></div><div className="file-controls"><button disabled={busy||readOnly} onClick={()=>void exportTrace()}>Export trace</button><button disabled={busy||playing||replaying} onClick={()=>fileInput.current?.click()}>Open trace</button><input className="file-input" ref={fileInput} type="file" accept=".json,.gz,application/json,application/gzip" onChange={event=>{const file=event.target.files?.[0];event.target.value='';if(file)void openTrace(file);}}/><button disabled={busy||readOnly||!done} onClick={()=>void operate(async()=>setVerification(await request('verify',{episode_id:live!.episode_id,expected_decision:live!.observation.decision})))}>Verify outcome</button></div></section>
    {(current.terminated||current.truncated)&&<div className="outcome"><strong>Recorded objective score: {current.total_reward.toFixed(2)}</strong>{observation.topographic_status.boundary_censored?' · Boundary-censored: excluded from learning labels.':' · Synthetic exercise outcomes, not real-world effectiveness.'}{verification!==null&&<pre>{JSON.stringify(verification,null,2)}</pre>}</div>}
    <footer>Source-grounded synthetic simulation · not a live forecast. Every commit advances two assumed model minutes. Displayed roads do not determine travel. Live messaging and model inference are disabled. {selectedCase?.mode==='stress'?'Training smoke catalog; held-out scenarios are not exposed.':''}</footer>
  </div>;
}
