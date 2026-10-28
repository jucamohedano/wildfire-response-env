import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import MapLegend from './MapLegend';
import {markerStyles} from './tacticalMarkersV2';
import {glyphPaths} from './mapGlyphs';

describe('plain-language map legend',()=>{
  it('explains operational symbols without artificial map boundaries or claiming an empty record means no fire',()=>{
    const html=renderToStaticMarkup(<MapLegend observation={{minute:52,sensor_memory:[]}} world={null}/>);
    for(const text of ['Map key','Fire sighting','Old sighting','Unconfirmed call','People','Refuge','Exposed people','Crew on scene','Mission target','Unobserved areas may still contain fire','500 m grid-based','sub-cell spread','study-area edge may be incomplete'])expect(html).toContain(text);
    expect(html).not.toContain('Simulation boundary');expect(html).not.toContain('Model edge buffer');
    expect(html).toContain('role="tooltip"');expect(html).toContain('4 simulation minutes');expect(html).not.toContain('Reported within 4m');
    expect(html).not.toContain('Evaluator view');expect(html).not.toContain('Burned ground');
  });
  it('counts observed cells separately from stale sightings and excludes future evidence',()=>{
    const html=renderToStaticMarkup(<MapLegend observation={{minute:10,sensor_memory:[
      {x:1,y:1,burning:true,observed_at:10},{x:2,y:1,burning:true,observed_at:8},
      {x:3,y:1,burning:true,observed_at:4},{x:4,y:1,burning:true,observed_at:11},
    ]}} world={null}/>);
    expect(html).toContain('2 recently observed burning cells · 1 older sighting.');
  });
  it('clearly labels evaluator symbols and does not present private counts as observed evidence',()=>{
    const html=renderToStaticMarkup(<MapLegend observation={{minute:10,sensor_memory:[]}} world={{episode_id:'a',decision:5,world_fire:[{column:1,row:1,heat:.5,burned:.2},{column:2,row:1,heat:0,burned:.8}]}}/>);
    expect(html).toContain('Evaluator view');expect(html).toContain('Simulated heat');
    expect(html).toContain('1 burning cell · 1 burned cell without active heat.');
    expect(html).not.toContain('Fire seen recently');
  });
  it('calls the assumed assembly point a temporary response base, not a command post or verified station',()=>{
    const html=renderToStaticMarkup(<MapLegend observation={{minute:0,sensor_memory:[],mobility:{version:'test',network_sha256:'test',telemetry_delay_minutes:1,basis:'synthetic'}}} world={null}/>);
    expect(html).toContain('Temporary response base');
    expect(html).toContain('Not a verified fire station, operations camp or incident command post');
    expect(html).not.toContain('Exercise staging');
  });
  it('uses the actual v2 settlement and dashed-call silhouettes in the 3D legend',()=>{
    const html=renderToStaticMarkup(<MapLegend observation={{minute:0,sensor_memory:[]}} world={null} tacticalWorld/>);
    expect(html).toContain(markerStyles.settlement.badgePath);
    expect(html).toContain('stroke-dasharray="3 2"');
    expect(html).not.toContain(glyphPaths.claim);
    expect(html).toContain('d="M16 3L30 28H2Z"');
    expect(html).not.toContain('d="M16"');
  });
});
