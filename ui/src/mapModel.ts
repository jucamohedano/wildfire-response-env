import type {Observation,Position,World} from './types';

export const mapViewOptions={minZoom:2,maxZoom:18,enableRotation:false};
export const spainExtent: [number,number,number,number]=[-18.3,27.4,4.7,44.4];
export const ignContextUrl='https://tms-mapa-raster.ign.es/1.0.0/mapa-raster/{z}/{x}/{-y}.jpeg';

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
