import {describe,it,expect} from 'vitest';
import {baseName,cellAt,cellCenter,currentWorld,parseAction,stageAction} from './model';

describe('structured action drafts',()=>{
  it('preserves combined fields without mutating or duplicating exact staged orders',()=>{
    const initial='{"clarifications":["claim-1"]}';
    const staged=stageAction(initial,'authorizations','offer-1');
    expect(parseAction(staged)).toEqual({clarifications:['claim-1'],authorizations:['offer-1']});
    expect(stageAction(staged,'authorizations','offer-1')).toBe(staged);
    expect(initial).toBe('{"clarifications":["claim-1"]}');
  });
  it('does not silently replace malformed draft fields or nonfinite numbers',()=>{
    for(const text of ['null','[]','true','{"value":1e400}']) expect(()=>parseAction(text)).toThrow();
    expect(()=>stageAction('{"requests":2}','requests',{})).toThrow();
  });
});
describe('evaluator separation',()=>{
  it('never overlays current private truth on a replay or another decision/episode',()=>{
    const world={episode_id:'episode-a',decision:4,world_fire:[]};
    expect(currentWorld(world,'episode-a',4,false,true)).toBe(world);
    expect(currentWorld(world,'episode-a',4,false,false)).toBeNull();
    expect(currentWorld(world,'episode-a',4,true,true)).toBeNull();
    expect(currentWorld(world,'episode-b',4,false,true)).toBeNull();
    expect(currentWorld(world,'episode-a',3,false,true)).toBeNull();
    expect(currentWorld(null,'episode-a',4,false,true)).toBeNull();
  });
});
describe('metric simulator grid',()=>{
  const grid={width:37,height:36,cell_km:.5,crs:'EPSG:25830',transform:[500,0,297000,0,-500,4054500]};
  it('maps continuous public positions to the same cell centres',()=>{
    expect(cellCenter(grid,0,0)).toEqual([297250,4054250]);
    expect(cellAt(grid,...cellCenter(grid,12,18))).toEqual([12,18]);
    expect(cellCenter(grid,14,18.5)).toEqual([304250,4045000]);
  });
  it('does not assign unknown outer terrain an invented cell',()=>{
    expect(cellAt(grid,296999,4054250)).toBeNull();
    expect(cellAt(grid,315500,4054250)).toBeNull();
    expect(cellAt(grid,297250,4036500)).toBeNull();
  });
});
describe('map labels',()=>{
  it('renders resource base IDs as short readable place names',()=>{
    expect(baseName('cabezudos')).toBe('Cabezudos');
    expect(baseName('las-arenas')).toBe('Las Arenas');
  });
});
