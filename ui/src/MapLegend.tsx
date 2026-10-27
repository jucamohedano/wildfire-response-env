import {fireCells} from './mapModel';
import MapGlyph from './MapGlyph';
import type {GlyphKind} from './mapGlyphs';
import type {Observation,World} from './types';

export default function MapLegend({observation,world}:{observation:Pick<Observation,'minute'|'sensor_memory'>;world:World|null}){
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
    {kind:'arrival',title:'Mission target',description:'An assigned destination, not the current GPS position of a vehicle. Arrival requires a delivered status update.'},
    {kind:'population',title:'People',description:'Synthetic exercise population at its recorded scenario position, not a verified historical town. Number = people.'},
    {kind:'refuge',title:'Refuge',description:'The exercise group’s assigned evacuation destination.'},
    {kind:'unit',title:'Crew on scene',description:'A response unit is reported on scene at its assigned location. Multiple units can share a marker.'},
    {kind:'exposed',title:'Exposed people',description:'Reported cumulative fire exposure, not a mortality estimate. Arrival at a refuge does not erase earlier exposure.'},
  ];
  const status=world?`${recent} burning ${recent===1?'cell':'cells'} · ${older} burned ${older===1?'cell':'cells'} without active heat. Evaluator view.`:recent?`${recent} recently observed burning ${recent===1?'cell':'cells'} · ${older} older ${older===1?'sighting':'sightings'}.`:older?`Only older fire sightings are available (${older}). Request an updated observation.`:'No burning cells in the available sensor reports. Unobserved areas may still contain fire.';
  return <section className="map-key tactical-key" aria-label="Map legend">
    <p className="map-key-status" role="status">{status}</p>
    <ul>{items.map(item=><li className="legend-item" key={item.kind}><button type="button" aria-describedby={`key-${item.kind}`} onClick={event=>event.currentTarget.focus()}><MapGlyph kind={item.kind}/><span>{item.title}</span></button><span className="legend-tooltip" role="tooltip" id={`key-${item.kind}`}>{item.description}</span></li>)}</ul>
    <details className="map-symbol-details"><summary>Map key and display assumptions</summary><p>The simulator remains 500 m grid-based. Symbols are deliberately enlarged for a game-like display; they do not infer sub-cell spread or an exact perimeter. Results near the study-area edge may be incomplete. Hover or focus a symbol for its explanation; tap one on touch screens.</p></details>
  </section>;
}
