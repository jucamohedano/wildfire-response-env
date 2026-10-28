import {describe,it,expect} from 'vitest';
import {OrthographicCamera} from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import type {Observation,Resource} from './types';
import type {TerrainMeshData} from './terrainSceneModel';
import {constrainTacticalFrame,declutterTacticalLabels,fireObservationLabel,orthographicPixelsPerMetre,
  publicTacticalFrame,publicTacticalPositions,reportedTacticalPosition,staleTacticalCells,
  tacticalFireLabels,tacticalSmokePuff,TACTICAL_FIRE_VERTEX_SHADER,TACTICAL_PITCH_DEGREES,
  TACTICAL_SMOKE_PUFFS,TACTICAL_FIRE_PARTICLES,MAX_TACTICAL_FIRE_CELLS,tacticalDistrictPosition,tacticalPacketDelivered,tacticalZoomLimits,deliveredTacticalRoutes,activeTacticalTarget,tacticalScale,tacticalNorthRotation} from './tacticalTerrainV2';

const mesh:TerrainMeshData={version:'wildfire-display-mesh-v1',role:'display_only',crs:'EPSG:25830',
  extent:[297000,4036500,315500,4054500],columns:2,rows:2,eastings:[297000,315500],
  northings:[4054500,4036500],heights_metres:[[300,900],[100,200]],source_sha256:'a'.repeat(64),source_label:'pinned'};
function observation():Observation{
  return {minute:26,geography:{width:37,height:36,cell_km:.5,crs:'EPSG:25830',transform:[500,0,297000,0,-500,4054500]},
    districts:[{id:'west',position:[8,18],home:[8,18],refuge:[12,25],population:200}],resources:[],
    sensor_memory:[{x:15,y:12,observed_at:24,burning:true}],reports:[],wind:[1,1]} as unknown as Observation;
}

describe('v2 tactical camera and public evidence',()=>{
  it('defaults to a north-up high angle and fits all public groups, refuges and sightings',()=>{
    const current=observation(),before=structuredClone({current,mesh});
    current.resources=[{id:'truck',target:[20,20],state:'mobilizing'} as Resource];
    const points=publicTacticalPositions(current,mesh);
    expect(points).toContainEqual([301250,4045250]);
    expect(points).toContainEqual([303250,4041750]);
    expect(points).toContainEqual([304750,4048250]);
    expect(points).toContainEqual([307250,4044250]);
    const frame=publicTacticalFrame(current,mesh,1.5);
    expect(TACTICAL_PITCH_DEGREES).toBe(60);
    for(const point of points){expect(Math.abs(point[0]-frame.centre[0])).toBeLessThan(frame.halfEast);
      expect(Math.abs(point[1]-frame.centre[1])).toBeLessThan(frame.halfNorth);}
    expect(mesh).toEqual(before.mesh);
  });

  it('clamps pan and zoom footprints inside measured terrain, including optional rotated views',()=>{
    for(const aspect of [.65,1.5,2.2])for(const pitch of [50,60,70])for(const azimuth of [-.6,0,.6]){
      const frame=constrainTacticalFrame([290000,4060000],100000,aspect,mesh.extent,900,pitch,azimuth);
      expect(frame.centre[0]-frame.halfEast).toBeGreaterThanOrEqual(mesh.extent[0]-.000001);
      expect(frame.centre[0]+frame.halfEast).toBeLessThanOrEqual(mesh.extent[2]+.000001);
      expect(frame.centre[1]-frame.halfNorth).toBeGreaterThanOrEqual(mesh.extent[1]-.000001);
      expect(frame.centre[1]+frame.halfNorth).toBeLessThanOrEqual(mesh.extent[3]+.000001);
      expect(frame.height).toBeLessThanOrEqual(frame.maximumHeight);
    }
    expect(constrainTacticalFrame([305000,4045000],1,1.5,mesh.extent,900).height).toBe(1700);
    expect(()=>constrainTacticalFrame([305000,4045000],1000,0,mesh.extent,900)).toThrow();
  });

  it('ignores future reports, cleared/stale fire and outside-airport positions when composing the view',()=>{
    const current=observation();current.sensor_memory.push({x:15,y:12,observed_at:25,burning:false});
    current.sensor_memory.push({x:30,y:30,observed_at:40,burning:true});
    current.reports=[{location:[25,20],observed_at:28,received_at:29}] as Observation['reports'];
    current.resources=[{id:'aircraft',map_position:[366040,4059866],observed_at:25,received_at:26,state:'available'} as Resource];
    const points=publicTacticalPositions(current,mesh);
    expect(points).not.toContainEqual([304750,4048250]);
    expect(points).not.toContainEqual([312250,4039250]);
    expect(points).not.toContainEqual([366040,4059866]);
    expect(points).toHaveLength(2);
  });

  it('does not treat an assigned target as a current unit position',()=>{
    const current=observation(),unit={state:'mobilizing',target:[8,18]} as Resource;
    expect(reportedTacticalPosition(unit,26,current.geography)).toBeNull();
    expect(reportedTacticalPosition({...unit,state:'on_scene'},26,current.geography)).toEqual([301250,4045250]);
    expect(reportedTacticalPosition({...unit,state:'on_scene',received_at:30},26,current.geography)).toBeNull();
  });
  it('does not revive a completed fire assignment while a unit is returning or available',()=>{
    const unit={state:'en_route',target:[8,18]} as Resource;
    expect(activeTacticalTarget(unit,26)).toEqual([8,18]);
    for(const state of ['returning','available','refilling','on_scene'])expect(activeTacticalTarget({...unit,state},26)).toBeNull();
    expect(activeTacticalTarget({...unit,movement:{direction:'return'} as Resource['movement']},26)).toBeNull();
  });
  it('withholds future and contradictory dynamic coordinates while retaining known home geography',()=>{
    const current=observation(),home=current.districts[0].home;
    current.districts[0]={...current.districts[0],position:[30,30],observed_at:29,received_at:30};
    current.resources=[{id:'future',state:'on_scene',target:[32,31],observed_at:29,received_at:30} as Resource];
    expect(tacticalDistrictPosition(current.districts[0],26)).toEqual(home);
    expect(publicTacticalPositions(current,mesh)).not.toContainEqual([312250,4039250]);
    expect(publicTacticalPositions(current,mesh)).not.toContainEqual([313250,4038750]);
    const invalid={state:'on_scene',target:[8,18],observed_at:25,received_at:24} as Resource;
    expect(reportedTacticalPosition(invalid,26,current.geography)).toBeNull();
    expect(tacticalPacketDelivered({observed_at:25},26)).toBe(false);
    expect(tacticalPacketDelivered({observed_at:NaN,received_at:25},26)).toBe(false);
    expect(tacticalPacketDelivered({},26)).toBe(true);
  });
  it('uses the same causal packet guard for route geometry and unit symbols',()=>{
    const current=observation();
    const unit={id:'truck',map_position:[301250,4045250],observed_at:25,received_at:26,
      movement:{observed_at:25,received_at:26}} as Resource;
    current.resources=[unit];expect(deliveredTacticalRoutes(current)).toHaveLength(1);
    for(const invalid of [-1,NaN,27]){
      current.resources=[{...unit,observed_at:invalid}];expect(deliveredTacticalRoutes(current)).toEqual([]);
      current.resources=[{...unit,movement:{...unit.movement!,observed_at:invalid}}];expect(deliveredTacticalRoutes(current)).toEqual([]);
    }
  });

  it('retains source positions and a bounded empty-observation fallback',()=>{
    const current=observation();current.districts=[];current.sensor_memory=[];
    const frame=publicTacticalFrame(current,mesh,1.5);
    expect(Number.isFinite(frame.height)).toBe(true);
    expect(frame.centre[0]-frame.halfEast).toBeGreaterThanOrEqual(mesh.extent[0]);
    expect(publicTacticalPositions(current,mesh)).toEqual([]);
  });

  it('sizes orthographic sprites and flame points without a perspective depth divisor',()=>{
    expect(orthographicPixelsPerMetre(6000,1,600)).toBe(.1);
    expect(orthographicPixelsPerMetre(6000,2,600)).toBe(.2);
    expect(orthographicPixelsPerMetre(0,1,600)).toBe(0);
    expect(orthographicPixelsPerMetre(NaN,1,600)).toBe(0);
    expect(TACTICAL_FIRE_VERTEX_SHADER).toContain('*pixelsPerMetre');
    expect(TACTICAL_FIRE_VERTEX_SHADER).not.toContain('projected.z');
    expect(TACTICAL_FIRE_VERTEX_SHADER).not.toContain('gl_Position.w');
  });
  it('replaces stale resize zoom limits before resetting the tactical composition',()=>{
    const camera=new OrthographicCamera(-4000,4000,4000,-4000,1,100000);
    camera.position.set(0,24000,14000);
    const controls=new OrbitControls(camera);
    controls.minZoom=1.28;controls.maxZoom=5;camera.zoom=1;controls.update();
    expect(camera.zoom).toBe(1.28);
    const frame=publicTacticalFrame(observation(),mesh,2.9);
    camera.zoom=1;Object.assign(controls,tacticalZoomLimits(frame));controls.update();
    expect(camera.zoom).toBe(1);
    expect(controls.minZoom).toBeLessThanOrEqual(1);
    expect(controls.maxZoom).toBeGreaterThanOrEqual(1);
  });
  it('keeps the compass camera-aware and the metric scale bounded in real projected metres',()=>{
    expect(tacticalNorthRotation(0,60)).toBe(0);
    expect(tacticalNorthRotation(Math.PI/3,60)).toBeGreaterThan(0);
    expect(tacticalNorthRotation(-Math.PI/3,60)).toBeLessThan(0);
    expect(tacticalScale(.1)).toEqual({pixels:100,label:'1 kilometre'});
    expect(tacticalScale(.2)).toEqual({pixels:100,label:'500 metres'});
    expect(tacticalScale(NaN).pixels).toBe(0);
    for(const resolution of [.01,.05,.13,.5,1])expect(tacticalScale(resolution).pixels).toBeLessThanOrEqual(110);
  });

  it('gives co-located public fire and unit labels separate non-overlapping positions',()=>{
    const labels=[{id:'engine',horizontal:300,vertical:230,width:170,height:30,priority:100},
      {id:'fire',horizontal:300,vertical:230,width:170,height:30,priority:900}];
    const placed=declutterTacticalLabels(labels,800,600);
    expect(placed.map(item=>item.id)).toEqual(['fire','engine']);
    expect(placed[0].bounds.bottom).toBeLessThan(placed[1].bounds.top);
    expect(declutterTacticalLabels([...labels].reverse(),800,600)).toEqual(placed);
    for(const item of placed){expect(item.bounds.top).toBeGreaterThanOrEqual(58);expect(item.bounds.bottom).toBeLessThanOrEqual(520);}
  });

  it('hides labels rather than overlapping or moving their source anchors in a dense view',()=>{
    const labels=Array.from({length:30},(_,index)=>({id:String(index),horizontal:150,vertical:150,width:180,height:30,priority:100}));
    const before=structuredClone(labels),placed=declutterTacticalLabels(labels,300,300);
    expect(placed.length).toBeLessThan(labels.length);expect(labels).toEqual(before);
    for(let first=0;first<placed.length;first++)for(let second=first+1;second<placed.length;second++){
      const one=placed[first].bounds,two=placed[second].bounds;
      expect(one.right<=two.left||one.left>=two.right||one.bottom<=two.top||one.top>=two.bottom).toBe(true);
    }
  });

  it('labels only fresh public observations and uses a real observed cell rather than an invented fire centroid',()=>{
    const current=observation();current.sensor_memory.push({x:16,y:12,observed_at:25,burning:true});
    const before=structuredClone(current),labels=tacticalFireLabels(current);
    expect(labels).toHaveLength(1);expect([[15,12],[16,12]]).toContainEqual(labels[0].cell);
    expect(labels[0].age).toBeLessThanOrEqual(2);expect(current).toEqual(before);
    current.minute=38;expect(tacticalFireLabels(current)).toEqual([]);
  });

  it('does not convert cleared observations into stale burn scars',()=>{
    const current=observation();current.minute=38;
    expect(staleTacticalCells(current)).toEqual([[15,12]]);
    current.sensor_memory.push({x:15,y:12,observed_at:30,burning:false});
    expect(staleTacticalCells(current)).toEqual([]);
  });

  it('spells out elapsed time without confusing minutes and metres',()=>{
    expect(fireObservationLabel(0)).toBe('Observed 0 minutes ago');
    expect(fireObservationLabel(1)).toBe('Observed 1 minute ago');
    expect(fireObservationLabel(2)).toBe('Observed 2 minutes ago');
  });

  it('uses deterministic, visible wind-directed smoke without changing public evidence',()=>{
    expect(tacticalSmokePuff(1,2,0,3,null)).toBeNull();
    expect(tacticalSmokePuff(1,2,0,3,[NaN,1])).toBeNull();
    const puffs=Array.from({length:TACTICAL_SMOKE_PUFFS},(_,index)=>tacticalSmokePuff(1,2,index,3,[1,1])!);
    expect(puffs).toEqual(Array.from({length:TACTICAL_SMOKE_PUFFS},(_,index)=>tacticalSmokePuff(1,2,index,3,[1,1])!));
    expect(puffs.at(-1)!.offset[0]).toBeGreaterThan(0);expect(puffs.at(-1)!.offset[2]).toBeGreaterThan(0);
    for(const puff of puffs){expect(puff.size).toBeLessThanOrEqual(900);expect(puff.opacity).toBeGreaterThan(0);
      expect(puff.opacity).toBeLessThanOrEqual(.62);expect(puff.offset[1]).toBeLessThanOrEqual(895);}
    expect(TACTICAL_FIRE_PARTICLES*MAX_TACTICAL_FIRE_CELLS).toBe(1440);
  });
});
