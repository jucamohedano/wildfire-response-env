import type {Movement,Observation,Position,Resource} from './types';
import townLabels from '../../data/coastal_mobility_v1/town_labels.json';

export const coastalExtent:[number,number,number,number]=[-5.43,36.34,-4.80,36.69];
export const coastalPlaces:{name:string;lonLat:Position;position:Position}[]=townLabels.towns.map(town=>({name:town.name,lonLat:[town.lonlat[0],town.lonlat[1]],position:[town.position[0],town.position[1]]}));

export function reportedPosition(unit:Resource,minute:number):Position|null{
  if(!unit.map_position||unit.observed_at===null||unit.observed_at===undefined||unit.received_at===null||unit.received_at===undefined||unit.observed_at>unit.received_at||unit.received_at>minute||!unit.map_position.every(Number.isFinite))return null;
  return [...unit.map_position];
}

export function positionOnRoute(route:Movement,minute:number):Position{
  let elapsed=Math.max(0,Math.min(minute,route.observed_at)-route.departed_at);
  for(const leg of route.legs){
    if(elapsed<leg.duration_minutes){
      let remaining=leg.distance_m*elapsed/leg.duration_minutes;
      for(let index=1;index<leg.coordinates.length;index++){
        const start=leg.coordinates[index-1],end=leg.coordinates[index],length=Math.hypot(end[0]-start[0],end[1]-start[1]);
        if(remaining<=length&&length>0)return [start[0]+(end[0]-start[0])*remaining/length,start[1]+(end[1]-start[1])*remaining/length];
        remaining-=length;
      }
      return [...leg.coordinates[leg.coordinates.length-1]];
    }
    elapsed-=leg.duration_minutes;
  }
  return [...route.position];
}

export function deliveredRoutes(observation:Observation):Resource[]{
  return observation.resources.filter(unit=>!!unit.movement&&!!reportedPosition(unit,observation.minute)&&unit.movement.received_at<=observation.minute&&unit.movement.observed_at<=unit.movement.received_at);
}

export function routeDisplayLines(route:Movement):{mode:string;coordinates:Position[]}[]{
  const result:{mode:string;coordinates:Position[]}[]=[];
  for(const leg of route.legs){
    const previous=result[result.length-1],end=previous?.coordinates[previous.coordinates.length-1],start=leg.coordinates[0];
    if(previous&&previous.mode===leg.mode&&end&&start&&end[0]===start[0]&&end[1]===start[1])previous.coordinates.push(...leg.coordinates.slice(1));
    else result.push({mode:leg.mode,coordinates:leg.coordinates.slice()});
  }
  return result;
}
