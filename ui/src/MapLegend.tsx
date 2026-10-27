import {fireCells} from './mapModel';
import MapGlyph from './MapGlyph';
import type {GlyphKind} from './mapGlyphs';
import type {Observation,World} from './types';
import {markerStyles} from './tacticalMarkersV2';
import type {TacticalMarkerKind} from './tacticalMarkersV2';

function LegendGlyph({kind,tacticalWorld}:{kind:GlyphKind;tacticalWorld:boolean}){
  const mapping:Partial<Record<GlyphKind,TacticalMarkerKind>>={population:'settlement',refuge:'refuge',unit:'crew',truck:'truck',plane:'plane',claim:'claim',base:'base'};
  if(tacticalWorld&&kind==='stale')return <svg className="map-key-symbol" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="11" fill="none" stroke="#c2b9a6" strokeWidth="2"/></svg>;
  const target=tacticalWorld?mapping[kind]:undefined;
  if(!target)return <MapGlyph kind={kind}/>;
  const style=markerStyles[target];
  return <svg className="map-key-symbol" viewBox="0 0 32 32" aria-hidden="true"><path d={style.badgePath} fill={style.fill??'none'} stroke={style.outline} strokeWidth="1.35" strokeDasharray={style.dash.join(' ')}/>{style.glyphPath&&<g transform="translate(5 5) scale(.6875)"><path d={style.glyphPath} fill={style.fillGlyph?style.glyphColor:'none'} stroke={style.glyphColor} strokeWidth="2.45" strokeLinecap="round" strokeLinejoin="round"/></g>}</svg>;
}

export default function MapLegend({observation,world,tacticalWorld=false}:{observation:Pick<Observation,'minute'|'sensor_memory'|'mobility'>;world:World|null;tacticalWorld?:boolean}){
  const cells=fireCells(observation,world),recent=cells.filter(cell=>cell.kind==='fire').length,older=cells.filter(cell=>cell.kind===(world?'burned':'stale')).length;
  const items:{kind:GlyphKind;title:string;description:string}[]=[
    ...(world?[
      {kind:'fire' as const,title:'Simulated heat',description:'Evaluator-only simulated fire intensity. Not public sensor evidence.'},
      {kind:'stale' as const,title:'Burned ground',description:'Evaluator-only burned fraction per 500 metre simulation cell.'},
    ]:[
      {kind:'fire' as const,title:'Fire sighting',description:'A 500 metre cell was observed burning within the last 4 simulation minutes. This is not a measured fire perimeter or a live temperature.'},
      {kind:'stale' as const,title:'Old sighting',description:'This fire observation is more than 4 simulation minutes old. Conditions may have changed.'},
    ]),
    {kind:'claim',title:'Unconfirmed call',description:'A caller or message reported this location. The claim has not been confirmed by current sensor evidence.'},
    ...(!observation.mobility?[{kind:'arrival' as const,title:'Mission target',description:'An assigned destination, not the current GPS position of a vehicle. Arrival requires a delivered status update.'}]:[]),
    {kind:'population',title:'People',description:'Synthetic exercise population at its recorded scenario position, not a verified historical town. Number = people.'},
    {kind:'refuge',title:'Refuge',description:'The exercise group’s assigned evacuation destination.'},
    {kind:'unit',title:observation.mobility?'Fire crew':'Crew on scene',description:observation.mobility?'Last delivered crew position on its road or assumed walking journey. Not real GPS.':'A response unit is reported on scene at its assigned location. Multiple units can share a marker.'},
    {kind:'exposed',title:'Exposed people',description:'Reported cumulative fire exposure, not a mortality estimate. Arrival at a refuge does not erase earlier exposure.'},
    ...(observation.mobility?[
      {kind:'truck' as const,title:'Fire truck',description:'Last delivered position on the declared road journey. Speeds, staging and truck accessibility are exercise assumptions.'},
      {kind:'plane' as const,title:'Aircraft',description:'Last delivered aircraft position. Flight paths are synthetic, not approved aviation routes.'},
      {kind:'base' as const,title:'Temporary response base',description:'A simulated responder assembly/departure point. Not a verified fire station, operations camp or incident command post.'},
    ]:[]),
  ];
  const status=world?`${recent} burning ${recent===1?'cell':'cells'} · ${older} burned ${older===1?'cell':'cells'} without active heat. Evaluator view.`:recent?`${recent} recently observed burning ${recent===1?'cell':'cells'} · ${older} older ${older===1?'sighting':'sightings'}.`:older?`Only older fire sightings are available (${older}). Request an updated observation.`:'No burning cells in the available sensor reports. Unobserved areas may still contain fire.';
  return <section className="map-key tactical-key" aria-label="Map legend">
    <p className="map-key-status" role="status">{status}</p>
    <ul>{items.map(item=><li className="legend-item" key={item.kind}><button type="button" aria-describedby={`key-${item.kind}`} onClick={event=>event.currentTarget.focus()}><LegendGlyph kind={item.kind} tacticalWorld={tacticalWorld}/><span>{item.title}</span></button><span className="legend-tooltip" role="tooltip" id={`key-${item.kind}`}>{item.description}</span></li>)}</ul>
    <p className="routing-attribution">Town labels{observation.mobility?' and road routes':''} © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>{observation.mobility?' · ODbL · planned journeys, not live traffic':''}</p>
    <details className="map-symbol-details"><summary>Map key and display assumptions</summary><p>The simulator remains 500 m grid-based. Symbols and decorative flames are deliberately enlarged; they do not infer sub-cell spread or an exact perimeter. Effects use only fresh delivered fire sightings. Smoke illustrates the direction of known model wind, not measured smoke or a dispersion forecast. No smoke is drawn for unknown or calm wind. Motion follows playback and respects reduced-motion settings. Terrain shading exaggerates lighting, never height or slope physics; its edges fade to avoid a rectangular map patch. Results near the study-area edge may be incomplete. Hover or focus a symbol for its explanation; tap one on touch screens.</p></details>
  </section>;
}
