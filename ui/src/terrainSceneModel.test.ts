import {describe,it,expect} from 'vitest';
import {clipToTerrain,sceneCoordinates,terrainHeight,textureUV,validateMesh,smokePuff,SMOKE_PUFFS_3D,FIRE_PARTICLES_3D,sceneCameraOffset,screenSizedScale,unconfirmedReportLocations} from './terrainSceneModel';
import type {TerrainMeshData} from './terrainSceneModel';

const mesh:TerrainMeshData={version:'wildfire-display-mesh-v1',role:'display_only',crs:'EPSG:25830',extent:[100,100,300,300],columns:3,rows:3,eastings:[100,200,300],northings:[300,200,100],heights_metres:[[100,200,300],[200,300,400],[300,400,500]],source_sha256:'a'.repeat(64),source_label:'test'};

describe('3D geography and visual sampling',()=>{
  it('preserves true metre coordinates, north orientation and source elevation without exaggeration',()=>{
    validateMesh(mesh,mesh.extent);
    expect(sceneCoordinates(mesh,[100,300])).toEqual([-100,100,-100]);
    expect(sceneCoordinates(mesh,[300,100])).toEqual([100,500,100]);
    expect(terrainHeight(mesh,[150,250])).toBe(200);
    expect(sceneCoordinates(mesh,[150,250],20)).toEqual([-50,220,-50]);
  });
  it('never invents a flat world beyond validated heights or fills missing values',()=>{
    expect(terrainHeight(mesh,[99,200])).toBeNull();
    expect(sceneCoordinates(mesh,[400,200])).toBeNull();
    expect(()=>validateMesh({...mesh,heights_metres:[[100,200,300],[100,NaN,300],[300,400,500]]},mesh.extent)).toThrow();
    expect(()=>validateMesh({...mesh,eastings:[100,100,300]},mesh.extent)).toThrow();
    expect(()=>validateMesh(mesh,[0,0,1,1])).toThrow();
  });
  it('registers map texture pixels by native extent, not by a guessed geographic stretch',()=>{
    expect(textureUV([100,300],mesh.extent)).toEqual([0,1]);
    expect(textureUV([300,100],mesh.extent)).toEqual([1,0]);
    expect(textureUV([200,200],mesh.extent)).toEqual([.5,.5]);
  });
  it('clips an outside flight leg without joining unrelated off-domain sections',()=>{
    expect(clipToTerrain([0,200],[400,200],mesh.extent)).toEqual([[100,200],[300,200]]);
    expect(clipToTerrain([0,50],[400,50],mesh.extent)).toBeNull();
    expect(clipToTerrain([150,250],[250,150],mesh.extent)).toEqual([[150,250],[250,150]]);
  });
  it('anchors actors to the exact rendered triangle rather than a different bilinear surface',()=>{
    const saddle={...mesh,columns:2,rows:2,eastings:[100,300],northings:[300,100],heights_metres:[[100,100],[100,500]]};
    expect(terrainHeight(saddle,[200,200])).toBe(100);
    expect(terrainHeight(saddle,[250,150])).toBe(300);
  });
  it('keeps decorative smoke bounded, deterministic and aligned with grid wind in scene coordinates',()=>{
    expect(smokePuff(1,2,0,3,null)).toBeNull();
    expect(smokePuff(1,2,0,3,[0,0])).toBeNull();
    expect(smokePuff(1,2,0,3,[NaN,1])).toBeNull();
    const column=Array.from({length:SMOKE_PUFFS_3D},(_,index)=>smokePuff(1,2,index,3,[1,1])!);
    expect(column).toEqual(Array.from({length:SMOKE_PUFFS_3D},(_,index)=>smokePuff(1,2,index,3,[1,1])!));
    expect(column.at(-1)!.offset[0]).toBeGreaterThan(0);
    expect(column.at(-1)!.offset[2]).toBeGreaterThan(0);
    for(const cloud of column){expect(cloud.size).toBeLessThanOrEqual(900);expect(cloud.opacity).toBeGreaterThan(0);expect(cloud.opacity).toBeLessThanOrEqual(.68);expect(cloud.offset[1]).toBeLessThanOrEqual(1185);}
    expect(FIRE_PARTICLES_3D*40).toBeLessThanOrEqual(1120);
    expect(SMOKE_PUFFS_3D*40).toBeLessThanOrEqual(400);
  });
  it('changes viewing angle without changing any terrain or world coordinates',()=>{
    const before=structuredClone(mesh);
    expect(sceneCameraOffset(false)).toEqual([1850,2350,2650]);
    expect(sceneCameraOffset(true)).toEqual([0,4200,1]);
    expect(mesh).toEqual(before);
  });
  it('sizes labels by camera-space depth so off-axis bounds stay equal to decluttering rectangles',()=>{
    const [width,height]=screenSizedScale(1000,600,60,160)!;
    const pixelsPerMetre=600/(2*1000*Math.tan(Math.PI/6));
    expect(width*pixelsPerMetre).toBeCloseTo(160);
    expect(height*pixelsPerMetre).toBeCloseTo(28);
    expect(screenSizedScale(-1,600,60,160)).toBeNull();
    expect(screenSizedScale(NaN,600,60,160)).toBeNull();
  });
  it('keeps newer uncleared reports even when an older report at the same cell was cleared',()=>{
    const reports=[{location:[1,2],observed_at:0,received_at:0},{location:[1,2],observed_at:3,received_at:3},{location:[3,4],observed_at:8,received_at:8},{location:[5,6],observed_at:8,received_at:2}] as Parameters<typeof unconfirmedReportLocations>[0]['reports'];
    const sensor_memory=[{x:1,y:2,observed_at:2,burning:false}];
    expect(unconfirmedReportLocations({reports,sensor_memory,minute:4})).toEqual([[1,2]]);
    expect(unconfirmedReportLocations({reports:[...reports].reverse(),sensor_memory,minute:4})).toEqual([[1,2]]);
    expect(unconfirmedReportLocations({reports,sensor_memory:[...sensor_memory,{x:1,y:2,observed_at:4,burning:false}],minute:4})).toEqual([]);
  });
});
