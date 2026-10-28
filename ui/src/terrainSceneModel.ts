import type {Observation,Position} from './types';
import {cellCenter} from './model';
import {particleSeed,smokeDirection} from './visualEffects';

export const FIRE_PARTICLES_3D=28;
export const SMOKE_PUFFS_3D=10;

export interface TerrainMeshData {
  version:string;crs:string;extent:[number,number,number,number];columns:number;rows:number;
  eastings:number[];northings:number[];heights_metres:number[][];support_mask?:string[];
  simulation_extent?:[number,number,number,number];source_sha256:string;source_label:string;role:string;
}
export interface MapTexture {canvas:HTMLCanvasElement;extent:[number,number,number,number];label:string}

export function validateMesh(value:TerrainMeshData,extent:number[]):void{
  const expanded=value.version==='wildfire-display-mesh-v2';
  if((!expanded&&value.version!=='wildfire-display-mesh-v1')||value.role!=='display_only'||value.crs!=='EPSG:25830'||value.extent.length!==4||extent.length!==4
    ||!value.extent.every(Number.isFinite)||!extent.every(Number.isFinite)
    ||(expanded?(extent[0]<value.extent[0]-.01||extent[1]<value.extent[1]-.01||extent[2]>value.extent[2]+.01||extent[3]>value.extent[3]+.01)
      :!extent.every((coordinate,index)=>Math.abs(coordinate-value.extent[index])<.01))
    ||!Number.isInteger(value.columns)||!Number.isInteger(value.rows)||value.columns<2||value.rows<2||value.columns*value.rows>(expanded?450000:160000)
    ||value.eastings.length!==value.columns||value.northings.length!==value.rows||value.heights_metres.length!==value.rows)throw new Error('3D terrain does not match the validated map domain.');
  const [west,south,east,north]=value.extent;
  if(value.eastings[0]!==west||value.eastings[value.columns-1]!==east||value.northings[0]!==north||value.northings[value.rows-1]!==south)throw new Error('3D terrain axes do not cover the pinned extent.');
  if(expanded&&(!value.support_mask||value.support_mask.length!==value.rows||value.support_mask.some(row=>typeof row!=='string'||row.length!==value.columns||/[^01]/.test(row))))throw new Error('Expanded terrain requires its complete support mask.');
  if(value.eastings.some((coordinate,index)=>!Number.isFinite(coordinate)||(index>0&&coordinate<=value.eastings[index-1]))||value.northings.some((coordinate,index)=>!Number.isFinite(coordinate)||(index>0&&coordinate>=value.northings[index-1]))
    ||value.heights_metres.some((row,rowIndex)=>row.length!==value.columns||row.some((height,column)=>!Number.isFinite(height)||height>5000||(terrainSupported(value,rowIndex,column)?height<=0:height!==0))))throw new Error('3D terrain contains invalid elevations or mask values.');
}

export function terrainSupported(mesh:TerrainMeshData,row:number,column:number):boolean{
  return mesh.support_mask?mesh.support_mask[row]?.[column]==='1':true;
}

function containingSegment(axis:number[],value:number,ascending:boolean):number{
  let low=0,high=axis.length-1;
  while(high-low>1){const middle=Math.floor((low+high)/2);if(ascending?axis[middle]<value:axis[middle]>value)low=middle;else high=middle;}
  return Math.min(low,axis.length-2);
}

export function terrainHeight(mesh:TerrainMeshData,point:Position):number|null{
  const [east,north]=point;
  if(!Number.isFinite(east)||!Number.isFinite(north)||east<mesh.extent[0]||east>mesh.extent[2]||north<mesh.extent[1]||north>mesh.extent[3])return null;
  const column=containingSegment(mesh.eastings,east,true),row=containingSegment(mesh.northings,north,false);
  const horizontal=(east-mesh.eastings[column])/(mesh.eastings[column+1]-mesh.eastings[column]);
  const vertical=(mesh.northings[row]-north)/(mesh.northings[row]-mesh.northings[row+1]);
  const northwest=mesh.heights_metres[row][column],northeast=mesh.heights_metres[row][column+1];
  const southwest=mesh.heights_metres[row+1][column],southeast=mesh.heights_metres[row+1][column+1];
  if(!terrainSupported(mesh,row,column+1)||!terrainSupported(mesh,row+1,column)
    ||!terrainSupported(mesh,horizontal+vertical<=1?row:row+1,horizontal+vertical<=1?column:column+1))return null;
  return horizontal+vertical<=1?northwest+(northeast-northwest)*horizontal+(southwest-northwest)*vertical
    :southeast+(southwest-southeast)*(1-horizontal)+(northeast-southeast)*(1-vertical);
}

export function sceneCoordinates(mesh:TerrainMeshData,point:Position,lift=0):[number,number,number]|null{
  const height=terrainHeight(mesh,point);if(height===null)return null;
  return [point[0]-(mesh.extent[0]+mesh.extent[2])/2,height+lift,(mesh.extent[1]+mesh.extent[3])/2-point[1]];
}

export function textureUV(point:Position,extent:number[]):[number,number]{
  return [(point[0]-extent[0])/(extent[2]-extent[0]),(point[1]-extent[1])/(extent[3]-extent[1])];
}

export function sceneFocus(observation:Observation,mesh:TerrainMeshData):Position{
  const report=observation.reports.find(item=>item.location&&item.received_at<=observation.minute&&item.observed_at<=item.received_at);
  const sample=observation.sensor_memory.find(item=>item.burning&&item.observed_at<=observation.minute&&observation.minute-item.observed_at<=4);
  const candidate=sample?cellCenter(observation.geography,sample.x,sample.y):report?.location?cellCenter(observation.geography,...report.location):null;
  return candidate&&terrainHeight(mesh,candidate)!==null?candidate:[(mesh.extent[0]+mesh.extent[2])/2,(mesh.extent[1]+mesh.extent[3])/2];
}

export function smokePuff(column:number,row:number,index:number,phase:number,wind:Position|null):{offset:[number,number,number];size:number;opacity:number}|null{
  const direction=smokeDirection(wind);if(!direction)return null;
  const seed=particleSeed(column,row,index),age=((index+phase*.35)/SMOKE_PUFFS_3D)%1;
  const spread=40+age*140,drift=age*age*1100;
  return {offset:[direction[0]*drift+Math.sin(seed*23+age*4)*spread,35+age*1150,-direction[1]*drift+Math.cos(seed*19+age*3)*spread],
    size:180+age*720,opacity:.68*Math.sin(Math.PI*(.12+.88*age))*(1-age*.65)};
}

export function sceneCameraOffset(topDown:boolean):[number,number,number]{
  return topDown?[0,4200,1]:[1850,2350,2650];
}

export function screenSizedScale(depth:number,viewportHeight:number,fovDegrees:number,widthPixels:number):[number,number]|null{
  if(!Number.isFinite(depth)||depth<=0)return null;
  const metresPerPixel=2*depth*Math.tan(fovDegrees*Math.PI/360)/Math.max(1,viewportHeight);
  return [widthPixels*metresPerPixel,widthPixels*112/640*metresPerPixel];
}

export function unconfirmedReportLocations(observation:Pick<Observation,'reports'|'sensor_memory'|'minute'>):Position[]{
  const locations=new Map<string,Position>();
  for(const report of observation.reports){
    if(!report.location||report.received_at>observation.minute||report.observed_at>report.received_at)continue;
    if(observation.sensor_memory.some(sample=>sample.x===report.location![0]&&sample.y===report.location![1]&&sample.observed_at>=report.observed_at&&sample.observed_at<=observation.minute&&observation.minute-sample.observed_at<=4))continue;
    locations.set(report.location.join(','),report.location);
  }
  return [...locations.values()];
}

export function clipToTerrain(first:Position,second:Position,extent:number[]):Position[]|null{
  const delta=[second[0]-first[0],second[1]-first[1]],directions=[-delta[0],delta[0],-delta[1],delta[1]];
  const distances=[first[0]-extent[0],extent[2]-first[0],first[1]-extent[1],extent[3]-first[1]];
  let start=0,end=1;
  for(let side=0;side<4;side++){
    if(directions[side]===0){if(distances[side]<0)return null;continue;}
    const ratio=distances[side]/directions[side];
    if(directions[side]<0)start=Math.max(start,ratio);else end=Math.min(end,ratio);
    if(start>end)return null;
  }
  return [[first[0]+delta[0]*start,first[1]+delta[1]*start],[first[0]+delta[0]*end,first[1]+delta[1]*end]];
}
