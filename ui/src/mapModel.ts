import type {Geography,MapMetadata,Observation,Position,World} from './types';
import proj4 from 'proj4';
import {register} from 'ol/proj/proj4.js';
import {transformExtent} from 'ol/proj.js';
import type View from 'ol/View.js';
import {cellCenter} from './model';

proj4.defs('EPSG:25830','+proj=utm +zone=30 +ellps=GRS80 +units=m +no_defs');
register(proj4);
export const mapProjection='EPSG:25830';
export const mapViewOptions={projection:mapProjection,minZoom:2,maxZoom:20,enableRotation:false};
export const spainExtent: [number,number,number,number]=[-9.7,35.3,4.5,44.3];
export const aerialTileUrl='https://tms-pnoa-ma.idee.es/1.0.0/pnoa-ma/{z}/{x}/{-y}.jpeg';
export const localTerrainVisible=(showBasemap:boolean)=>!showBasemap;

export function publicActivityExtent(observation:Observation):[number,number,number,number]{
  const points:Position[]=[];
  for(const district of observation.districts)points.push(district.position??district.home,district.refuge);
  for(const report of observation.reports)if(report.location&&report.observed_at<=report.received_at&&report.received_at<=observation.minute)points.push(report.location);
  for(const sample of observation.sensor_memory)if(sample.burning&&sample.observed_at<=observation.minute)points.push([sample.x,sample.y]);
  for(const unit of observation.resources)if(unit.target)points.push(unit.target);
  const coordinates=points.map(point=>cellCenter(observation.geography,...point)).filter(point=>point.every(Number.isFinite));
  const grid=observation.geography;
  if(!coordinates.length)return [grid.transform[2],grid.transform[5]-grid.height*500,grid.transform[2]+grid.width*500,grid.transform[5]];
  return [Math.min(...coordinates.map(point=>point[0]))-750,Math.min(...coordinates.map(point=>point[1]))-750,
          Math.max(...coordinates.map(point=>point[0]))+750,Math.max(...coordinates.map(point=>point[1]))+750];
}

export function mapMetadataMatches(metadata:MapMetadata,grid:Geography):boolean {
  const [scaleX,rotateX,west,rotateY,scaleY,north]=grid.transform;
  if(metadata.crs!==grid.crs||grid.crs!==mapProjection||rotateX!==0||rotateY!==0||scaleX!==500||scaleY!==-500)return false;
  const expected=[west,north+grid.height*scaleY,west+grid.width*scaleX,north];
  return expected.every((value,index)=>Number.isFinite(value)&&Number.isFinite(metadata.extent[index])&&Math.abs(value-metadata.extent[index])<1e-6);
}

export function limitCountryZoom(view:View,size:number[]):void {
  const extent=transformExtent(spainExtent,'EPSG:4326',mapProjection,16);
  const resolution=view.getResolutionForExtent(extent,[Math.max(1,size[0]-50),Math.max(1,size[1]-50)]);
  const zoom=view.getZoomForResolution(resolution);
  if(zoom!==undefined)view.setMinZoom(zoom);
}

export interface FireCell {column:number;row:number;kind:'fire'|'stale'|'burned';heat:number|null;burned:number;observedAt:number|null}

export function fireCells(observation:Pick<Observation,'minute'|'sensor_memory'>,world:World|null):FireCell[] {
  if(world)return world.world_fire.filter(cell=>cell.heat>0||cell.burned>0).map(cell=>({
    column:cell.column,row:cell.row,kind:cell.heat>0?'fire':'burned',
    heat:cell.heat,burned:cell.burned,observedAt:null,
  }));
  return observation.sensor_memory.filter(cell=>cell.burning&&cell.observed_at<=observation.minute).map(cell=>({
    column:cell.x,row:cell.y,kind:observation.minute-cell.observed_at>4?'stale':'fire',
    heat:null,burned:0,observedAt:cell.observed_at,
  }));
}

export function fireBoundary(cells:FireCell[]):Position[][] {
  const active=cells.filter(cell=>cell.kind==='fire');
  const occupied=new Set(active.map(cell=>`${cell.column},${cell.row}`));
  const edges:Position[][]=[];
  for(const {column,row} of active){
    if(!occupied.has(`${column},${row-1}`))edges.push([[column,row],[column+1,row]]);
    if(!occupied.has(`${column+1},${row}`))edges.push([[column+1,row],[column+1,row+1]]);
    if(!occupied.has(`${column},${row+1}`))edges.push([[column+1,row+1],[column,row+1]]);
    if(!occupied.has(`${column-1},${row}`))edges.push([[column,row+1],[column,row]]);
  }
  return edges;
}
