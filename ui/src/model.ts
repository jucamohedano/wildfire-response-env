import type {Action, Geography, Position, World} from './types';

export function parseAction(text:string):Action {
  const value:unknown = JSON.parse(text);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('An action must be a JSON object.');
  function finite(item:unknown):void {
    if (typeof item === 'number' && !Number.isFinite(item)) throw new Error('Action numbers must be finite.');
    if (item && typeof item === 'object') Object.values(item).forEach(finite);
  }
  finite(value);
  if (new TextEncoder().encode(JSON.stringify(value)).length > 65536) throw new Error('Action exceeds 64 KiB.');
  return value as Action;
}

export function stageAction(text:string, field:string, value:unknown):string {
  const action = parseAction(text);
  const previous = action[field] ?? [];
  if (!Array.isArray(previous)) throw new Error(`${field} must be an array. Fix the advanced draft first.`);
  const items = previous.some(item=>JSON.stringify(item)===JSON.stringify(value)) ? previous : [...previous,value];
  return JSON.stringify({...action,[field]:items},null,2);
}

export function cellCenter(grid:Geography, column:number, row:number):Position {
  const [scaleX,rotateX,west,rotateY,scaleY,north]=grid.transform;
  return [west+(column+.5)*scaleX+(row+.5)*rotateX,north+(column+.5)*rotateY+(row+.5)*scaleY];
}

export function cellAt(grid:Geography, east:number, north:number):Position|null {
  const [scaleX,rotateX,west,rotateY,scaleY,top]=grid.transform;
  if (rotateX!==0 || rotateY!==0 || !Number.isFinite(east) || !Number.isFinite(north)) return null;
  const column=Math.floor((east-west)/scaleX),row=Math.floor((north-top)/scaleY);
  return column>=0 && row>=0 && column<grid.width && row<grid.height ? [column,row] : null;
}

export function pretty(value:string|undefined|null):string {return (value??'—').replaceAll('_',' ');}
export function groupName(value:string):string {return value.endsWith('west')?'West group':value.endsWith('east')?'East group':pretty(value);}

export function currentWorld(world:World|null,episodeId:string|undefined,decision:number,readOnly:boolean,enabled:boolean):World|null {
  return enabled&&!readOnly&&world?.episode_id===episodeId&&world?.decision===decision?world:null;
}
