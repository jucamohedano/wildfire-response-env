import {describe,it,expect} from 'vitest';
import {mountainShadeOpacity,mountainShadePixels} from './terrainPaletteV2';
import type {TerrainMeshData} from './terrainSceneModel';

describe('source-aligned mountain color gradient',()=>{
  it('transitions continuously toward a darker shade at higher elevations',()=>{
    expect(mountainShadeOpacity(100)).toBe(0);
    expect(mountainShadeOpacity(600)).toBeGreaterThan(0);
    expect(mountainShadeOpacity(1100)).toBeGreaterThan(mountainShadeOpacity(600));
    expect(mountainShadeOpacity(1600)).toBe(.36);
    for(let metres=250;metres<1300;metres+=10){
      expect(mountainShadeOpacity(metres+10)).toBeGreaterThanOrEqual(mountainShadeOpacity(metres));
      expect(mountainShadeOpacity(metres+10)-mountainShadeOpacity(metres)).toBeLessThan(.006);
    }
    expect(()=>mountainShadeOpacity(NaN)).toThrow();
  });

  it('uses the rendered triangle height at cell centers and keeps the source mesh unchanged',()=>{
    const mesh:TerrainMeshData={version:'wildfire-display-mesh-v1',role:'display_only',crs:'EPSG:25830',
      extent:[100,100,300,300],columns:3,rows:2,eastings:[100,200,300],northings:[300,100],
      heights_metres:[[200,200,1400],[200,200,1400]],source_sha256:'a'.repeat(64),source_label:'fixture'};
    const before=structuredClone(mesh),shade=mountainShadePixels(mesh);
    expect([shade.width,shade.height]).toEqual([2,1]);
    expect(shade.pixels[3]).toBe(0);
    expect(shade.pixels[7]).toBe(Math.round(mountainShadeOpacity(800)*255));
    expect(shade.pixels[7]).toBeGreaterThan(shade.pixels[3]);
    expect(mesh).toEqual(before);
    expect(()=>mountainShadePixels({...mesh,heights_metres:[[200,NaN,1400],[200,200,1400]]})).toThrow();
    expect(()=>mountainShadePixels({...mesh,eastings:[100,180,300]})).toThrow(/regularly spaced/);
  });
});
