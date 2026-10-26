import {fireCells} from './mapModel';
import {mapColors} from './mapSymbols';
import type {Observation,World} from './types';

type SymbolKind='fire'|'heat'|'stale'|'burned'|'claim'|'population'|'refuge'|'unit'|'exposed'|'domain'|'guard';

function Symbol({kind}:{kind:SymbolKind}){
  const circle=['claim','population','refuge','unit','exposed'].includes(kind);
  const color=kind==='refuge'?mapColors.population:mapColors[kind==='heat'?'fire':kind==='stale'?'staleLine':kind];
  return <svg className="map-key-symbol" viewBox="0 0 40 32" aria-hidden="true" focusable="false">
    {kind==='heat'?<g><path d="M9 6h8v20H9z" fill="#ff6519"/><path d="M17 6h7v20h-7z" fill="#ffae19"/><path d="M24 6h7v20h-7z" fill="#ffeb19"/><rect x="9" y="6" width="22" height="20" fill="none" stroke={mapColors.front} strokeWidth="2"/></g>:circle?<circle cx="20" cy="16" r={kind==='claim'?10:kind==='refuge'?4:7} fill={kind==='claim'?mapColors.claimFill:color} stroke={kind==='claim'?color:'#fff'} strokeWidth="2" strokeDasharray={kind==='claim'?'4 3':undefined}/>:kind==='guard'?<path d="M4 16 H36" fill="none" stroke={mapColors.guard} strokeWidth="2" strokeDasharray="5 5"/>:<rect x="9" y="6" width="22" height="20" rx="1" fill={kind==='domain'?'none':kind==='stale'?mapColors.staleFill:color} stroke={kind==='fire'?mapColors.front:kind==='stale'?mapColors.staleLine:color} strokeWidth="2" strokeDasharray={kind==='stale'?'4 3':undefined}/>}
  </svg>;
}

export default function MapLegend({observation,world}:{observation:Pick<Observation,'minute'|'sensor_memory'>;world:World|null}){
  const cells=fireCells(observation,world),recent=cells.filter(cell=>cell.kind==='fire').length,older=cells.filter(cell=>cell.kind===(world?'burned':'stale')).length;
  const items:{kind:SymbolKind;title:string;description:string}[]=[
    ...(world?[
      {kind:'heat' as const,title:'Simulated burning area',description:'Evaluator only. Red = lower heat; yellow = higher heat.'},
      {kind:'burned' as const,title:'Burned ground',description:'Evaluator only. Burned cells with no active heat.'},
    ]:[
      {kind:'fire' as const,title:'Fire seen recently',description:'Orange cells: reported burning within the last 4 simulation minutes.'},
      {kind:'stale' as const,title:'Older fire sighting',description:'Dashed grey cells: more than 4 minutes old. Conditions may have changed.'},
    ]),
    {kind:'claim',title:'Unverified report',description:'Dashed amber circle: a reported location without recent sensor confirmation.'},
    {kind:'population',title:'People',description:'Blue dot: group location. The number beside it is the number of people.'},
    {kind:'refuge',title:'Evacuation destination',description:'Small blue dot labelled Refuge: the group’s designated destination.'},
    {kind:'exposed',title:'People exposed to fire',description:'Red dot: fire exposure is reported for this group.'},
    {kind:'unit',title:'Responder on scene',description:'Green dot: a response unit has arrived at its assigned location.'},
    {kind:'domain',title:'Simulation boundary',description:'Solid border: only the area inside it is simulated.'},
    {kind:'guard',title:'Model edge buffer',description:'Dashed inner line: nearby results may be incomplete. This is not a firebreak.'},
  ];
  return <section className="map-key" aria-label="Map legend">
    <div className="map-key-heading"><h2>Map key</h2><span>{observation.minute} simulation min{world?' · evaluator view':''}</span></div>
    <p className="map-key-status" role="status">{world?`${recent} burning ${recent===1?'cell':'cells'} · ${older} burned ${older===1?'cell':'cells'} without active heat.`:recent?`${recent} recently observed burning ${recent===1?'cell':'cells'} · ${older} older ${older===1?'sighting':'sightings'}.`:older?`Only older fire sightings are available (${older} ${older===1?'cell':'cells'}). Request an updated observation.`:'No burning cells in the available sensor reports. Unobserved areas may still contain fire.'}</p>
    <ul>{items.map(item=><li key={item.kind}><Symbol kind={item.kind}/><div><strong>{item.title}</strong><p>{item.description}</p></div></li>)}</ul>
    <p className="map-key-note">Fire shapes follow 500 m grid cells, not an exact fire perimeter.</p>
  </section>;
}
