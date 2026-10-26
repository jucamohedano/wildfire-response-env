import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import MapLegend from './MapLegend';

describe('plain-language map legend',()=>{
  it('explains the actual circle, cell and boundary symbols without claiming an empty record means no fire',()=>{
    const html=renderToStaticMarkup(<MapLegend observation={{minute:52,sensor_memory:[]}} world={null}/>);
    for(const text of ['Map key','Fire seen recently','Older fire sighting','Unverified report','People','Evacuation destination','People exposed to fire','Responder on scene','Simulation boundary','Model edge buffer','Unobserved areas may still contain fire','not a firebreak'])expect(html).toContain(text);
    expect(html).toContain('<circle');expect(html).toContain('stroke-dasharray="4 3"');
    expect(html).not.toContain('Evaluator only');expect(html).not.toContain('Burned ground');
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
    expect(html).toContain('evaluator view');expect(html).toContain('Simulated burning area');
    expect(html).toContain('1 burning cell · 1 burned cell without active heat.');
    expect(html).not.toContain('Fire seen recently');
  });
});
