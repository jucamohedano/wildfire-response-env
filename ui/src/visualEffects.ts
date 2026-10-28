import type {Observation,Position} from './types';

export const MAX_EFFECT_CELLS=40;
export const PARTICLES_PER_CELL=6;

export function effectCells(observation:Pick<Observation,'minute'|'sensor_memory'|'geography'>):Position[]{
  const latest=new Map<string,(typeof observation.sensor_memory)[number]>();
  for(const sample of observation.sensor_memory){
    if(!Number.isInteger(sample.x)||!Number.isInteger(sample.y)||sample.x<0||sample.y<0||sample.x>=observation.geography.width||sample.y>=observation.geography.height||!Number.isFinite(sample.observed_at)||sample.observed_at>observation.minute)continue;
    const key=`${sample.x},${sample.y}`,previous=latest.get(key);
    if(!previous||previous.observed_at<=sample.observed_at)latest.set(key,sample);
  }
  return [...latest.values()].filter(sample=>sample.burning&&observation.minute-sample.observed_at<=4)
    .sort((first,second)=>first.y-second.y||first.x-second.x).map(sample=>[sample.x,sample.y]);
}

export function smokeDirection(wind:Position|null):Position|null{
  if(!wind||!wind.every(Number.isFinite))return null;
  const length=Math.hypot(...wind);
  return length>0&&Number.isFinite(length)?[wind[0]/length,-wind[1]/length]:null;
}

export function particleSeed(column:number,row:number,index:number):number{
  const value=Math.sin(column*127.1+row*311.7+index*74.7)*43758.5453;
  return value-Math.floor(value);
}

export function effectsAnimate(requested:boolean,visible:boolean,reducedMotion:boolean):boolean{
  return requested&&visible&&!reducedMotion;
}
