import {fireCells} from './mapModel';
import {mapColors} from './mapSymbols';
import type {Observation,World} from './types';

type SymbolKind='fire'|'heat'|'stale'|'burned'|'claim'|'population'|'refuge'|'unit'|'arrival'|'exposed';
const captions:Record<SymbolKind,string>={fire:'Reported within 4m',heat:'Evaluator only',stale:'More than 4m old',burned:'Evaluator only',claim:'Location not confirmed',population:'Exercise group · people',refuge:'Assigned destination',unit:'Confirmed arrival',arrival:'Target, not GPS',exposed:'Reported exposure'};

function Symbol({kind}:{kind:SymbolKind}){
  const circle=['claim','population','refuge','unit','exposed'].includes(kind);
  const color=kind==='refuge'?mapColors.population:kind==='arrival'?mapColors.arrival:mapColors[kind==='heat'?'fire':kind==='stale'?'staleLine':kind];
  return <svg className="map-key-symbol" viewBox="0 0 40 32" aria-hidden="true" focusable="false">
    {kind==='unit'&&<path d="M20 5 L33 26 L7 26 Z" fill={color} stroke="#d9f6d0" strokeWidth="2"/>}
    {kind==='unit'?null:kind==='heat'?<g><circle cx="20" cy="16" r="14" fill="rgba(255,105,22,.22)"/><circle cx="20" cy="16" r="9" fill="#ffae19"/><circle cx="20" cy="16" r="4" fill="#ffeb19" stroke="#8a2118" strokeWidth="1"/></g>:kind==='arrival'?<circle cx="20" cy="16" r="10" fill="rgba(248,191,91,.2)" stroke={color} strokeWidth="2.5" strokeDasharray="3 3"/>:kind==='fire'?<g><circle cx="20" cy="16" r="13" fill="rgba(246,125,25,.14)" stroke={mapColors.fire} strokeWidth="1.8" strokeDasharray="3 3"/><circle cx="20" cy="16" r="4" fill={mapColors.fire}/></g>:kind==='stale'?<circle cx="20" cy="16" r="11" fill={mapColors.staleFill} stroke={mapColors.staleLine} strokeWidth="1.5" strokeDasharray="4 3"/>:circle?<circle cx="20" cy="16" r={kind==='claim'?10:kind==='refuge'?4:7} fill={kind==='claim'?mapColors.claimFill:color} stroke={kind==='claim'?color:'#fff'} strokeWidth="2" strokeDasharray={kind==='claim'?'4 3':undefined}/>:<rect x="9" y="6" width="22" height="20" rx="1" fill={color} stroke={color} strokeWidth="2"/>}
  </svg>;
}

export default function MapLegend({observation,world}:{observation:Pick<Observation,'minute'|'sensor_memory'>;world:World|null}){
  const cells=fireCells(observation,world),recent=cells.filter(cell=>cell.kind==='fire').length,older=cells.filter(cell=>cell.kind===(world?'burned':'stale')).length;
  const items:{kind:SymbolKind;title:string;description:string}[]=[
    ...(world?[
      {kind:'heat' as const,title:'Simulated fire intensity',description:'Evaluator view. The soft glow uses simulator heat per 500 m cell; brighter means more heat.'},
      {kind:'burned' as const,title:'Burned ground',description:'Evaluator view. Burned fraction is recorded at 500 m cell resolution.'},
    ]:[
      {kind:'fire' as const,title:'Recent fire observation',description:'Orange marker: a 500 m cell was reported burning within the last 4 simulation minutes; its perimeter is unknown.'},
      {kind:'stale' as const,title:'Older fire sighting',description:'Dashed grey marker: the observation is more than 4 minutes old. Conditions may have changed.'},
    ]),
    {kind:'claim',title:'Unverified report',description:'Dashed amber circle: a reported location without recent sensor confirmation.'},
      {kind:'population',title:'People',description:'Blue dot: synthetic exercise group at its recorded position, not an identified historical town. Number = people.'},
    {kind:'refuge',title:'Evacuation destination',description:'Small blue dot labelled Refuge: the group’s designated destination.'},
    {kind:'exposed',title:'People exposed to fire',description:'Red dot: fire exposure is reported for this group.'},
      {kind:'unit',title:'Responder on scene',description:'Green triangle: a response unit has arrived at its assigned location.'},
    {kind:'arrival',title:'En-route target',description:'Dashed amber ring marks the assigned target and estimated arrival, not the vehicle’s current location.'},
  ];
  return <section className="map-key" aria-label="Map legend">
    <div className="map-key-heading"><h2>Map key</h2><span>{observation.minute} simulation min{world?' · evaluator view':''}</span></div>
    <p className="map-key-status" role="status">{world?`${recent} burning ${recent===1?'cell':'cells'} · ${older} burned ${older===1?'cell':'cells'} without active heat.`:recent?`${recent} recently observed burning ${recent===1?'cell':'cells'} · ${older} older ${older===1?'sighting':'sightings'}.`:older?`Only older fire sightings are available (${older} ${older===1?'cell':'cells'}). Request an updated observation.`:'No burning cells in the available sensor reports. Unobserved areas may still contain fire.'}</p>
    <ul>{items.map(item=><li key={item.kind}><Symbol kind={item.kind}/><div><strong>{item.title}</strong><p>{captions[item.kind]}</p></div></li>)}</ul>
    <details className="map-symbol-details"><summary>How to read these symbols</summary><dl>{items.map(item=><div key={item.kind}><dt>{item.title}</dt><dd>{item.description}</dd></div>)}</dl></details>
    <p className="map-key-note">The simulator remains 500 m grid-based. Fire glows smooth the display only; they do not infer sub-cell spread or an exact perimeter. Results near the study-area edge may be incomplete.</p>
  </section>;
}
