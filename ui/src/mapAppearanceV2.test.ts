import {describe,it,expect} from 'vitest';
import {roadPixel,validateDisplayRoads} from './mapAppearanceV2';
import type {DisplayRoadMap} from './mapAppearanceV2';

const extent:[number,number,number,number]=[100,100,300,300];
const roads:DisplayRoadMap={version:'test',crs:'EPSG:25830',extent,role:'display_only_not_routing',lines:[{id:'road',kind:'local',coordinates:[[100,300],[300,100]]}]};
describe('appearance-only road cartography',()=>{
  it('preserves native positions and north-up pixel registration',()=>{
    expect(roadPixel([100,300],extent,2000,2000)).toEqual([0,0]);
    expect(roadPixel([300,100],extent,2000,2000)).toEqual([2000,2000]);
    expect(roadPixel([200,200],extent,2000,2000)).toEqual([1000,1000]);
    const before=JSON.stringify(roads);validateDisplayRoads(roads,extent);expect(JSON.stringify(roads)).toBe(before);
  });
  it('withholds wrong-extent or unsupported road data rather than inventing routes',()=>{
    expect(()=>validateDisplayRoads({...roads,role:'routing'},extent)).toThrow();
    expect(()=>validateDisplayRoads({...roads,crs:'EPSG:4326'},extent)).toThrow();
    expect(()=>validateDisplayRoads({...roads,extent:[0,0,1,1]},extent)).toThrow();
    expect(()=>validateDisplayRoads({...roads,lines:[{...roads.lines[0],coordinates:[[0,300],[300,100]]}]},extent)).toThrow();
  });
});
