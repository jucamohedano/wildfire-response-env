import type {District,Geography,Observation,Position,Resource} from './types';
import type {TerrainMeshData} from './terrainSceneModel';
import {terrainHeight,terrainSupported,unconfirmedReportLocations} from './terrainSceneModel';
import {cellCenter} from './model';
import {reportedPosition,deliveredRoutes} from './movement';
import {effectCells,particleSeed,smokeDirection} from './visualEffects';

export const TACTICAL_PITCH_DEGREES=60;
export const TACTICAL_FIRE_PARTICLES=36;
export const TACTICAL_SMOKE_PUFFS=12;
export const MAX_TACTICAL_FIRE_CELLS=40;

export interface TacticalFrame {
  centre:Position;height:number;minimumHeight:number;maximumHeight:number;
  halfEast:number;halfNorth:number;
}
export interface LabelBounds {left:number;top:number;right:number;bottom:number}
export interface TacticalLabelCandidate {
  id:string;horizontal:number;vertical:number;width:number;height:number;priority:number;
}
export interface TacticalLabelPlacement {id:string;offset:Position;bounds:LabelBounds}

export function terrainHeightRange(mesh:TerrainMeshData):[number,number]{
  let minimum=Infinity,maximum=-Infinity;
  for(let row=0;row<mesh.rows;row++)for(let column=0;column<mesh.columns;column++)if(terrainSupported(mesh,row,column)){
    const height=mesh.heights_metres[row][column];minimum=Math.min(minimum,height);maximum=Math.max(maximum,height);
  }
  return [minimum,maximum];
}

export function constrainTacticalFrame(centre:Position,height:number,aspect:number,extent:number[],
  heightRange:number,pitchDegrees=TACTICAL_PITCH_DEGREES,azimuth=0):TacticalFrame{
  if(![...centre,height,aspect,...extent,heightRange,pitchDegrees,azimuth].every(Number.isFinite)
    ||height<=0||aspect<=0||extent.length!==4||extent[2]<=extent[0]||extent[3]<=extent[1]
    ||heightRange<0||pitchDegrees<45||pitchDegrees>90)throw new Error('Invalid tactical camera bounds.');
  const pitch=pitchDegrees*Math.PI/180,sine=Math.sin(pitch),cosine=Math.cos(pitch);
  const alongEast=Math.abs(Math.cos(azimuth)),alongNorth=Math.abs(Math.sin(azimuth));
  const reliefPadding=heightRange*cosine/(2*sine);
  const eastPadding=alongNorth*reliefPadding,northPadding=alongEast*reliefPadding;
  const eastFactor=alongEast*aspect+alongNorth/sine,northFactor=alongNorth*aspect+alongEast/sine;
  const maximumHeight=Math.min((extent[2]-extent[0]-2*eastPadding)/eastFactor,
    (extent[3]-extent[1]-2*northPadding)/northFactor);
  if(maximumHeight<=0)throw new Error('The supported terrain cannot cover this camera angle.');
  const minimumHeight=Math.min(1700,maximumHeight),boundedHeight=Math.max(minimumHeight,Math.min(maximumHeight,height));
  const halfEast=boundedHeight*eastFactor/2+eastPadding,halfNorth=boundedHeight*northFactor/2+northPadding;
  return {centre:[Math.max(extent[0]+halfEast,Math.min(extent[2]-halfEast,centre[0])),
    Math.max(extent[1]+halfNorth,Math.min(extent[3]-halfNorth,centre[1]))],
  height:boundedHeight,minimumHeight,maximumHeight,halfEast,halfNorth};
}

export function reportedTacticalPosition(unit:Resource,minute:number,geography:Geography):Position|null{
  if(!tacticalPacketDelivered(unit,minute))return null;
  const measured=reportedPosition(unit,minute);if(measured)return measured;
  if(unit.movement||unit.state!=='on_scene'||!unit.target)return null;
  if(unit.observed_at!=null&&unit.observed_at>minute||unit.received_at!=null&&unit.received_at>minute)return null;
  return cellCenter(geography,...unit.target);
}

export function tacticalPacketDelivered(packet:{observed_at?:number|null;received_at?:number|null},minute:number):boolean{
  if(packet.observed_at==null&&packet.received_at==null)return true;
  return typeof packet.observed_at==='number'&&typeof packet.received_at==='number'
    &&Number.isFinite(packet.observed_at)&&Number.isFinite(packet.received_at)
    &&packet.observed_at>=0&&packet.observed_at<=packet.received_at&&packet.received_at<=minute;
}

export function tacticalDistrictPosition(district:District,minute:number):Position{
  return district.position&&tacticalPacketDelivered(district,minute)?district.position:district.home;
}

export function deliveredTacticalRoutes(observation:Observation):Resource[]{
  return deliveredRoutes(observation).filter(unit=>tacticalPacketDelivered(unit,observation.minute)
    &&tacticalPacketDelivered(unit.movement!,observation.minute));
}

export function activeTacticalTarget(unit:Resource,minute:number):Position|null{
  return tacticalPacketDelivered(unit,minute)&&['reserved','mobilizing','en_route'].includes(unit.state??'')
    &&unit.movement?.direction!=='return'?(unit.target??null):null;
}

export function publicTacticalPositions(observation:Observation,mesh:TerrainMeshData):Position[]{
  const points=new Map<string,Position>();
  const add=(position:Position|null)=>{if(position&&terrainHeight(mesh,position)!==null)points.set(position.join(','),[...position]);};
  for(const district of observation.districts){
    add(cellCenter(observation.geography,...tacticalDistrictPosition(district,observation.minute)));
    add(cellCenter(observation.geography,...district.refuge));
  }
  for(const cell of effectCells(observation))add(cellCenter(observation.geography,...cell));
  for(const cell of unconfirmedReportLocations(observation))add(cellCenter(observation.geography,...cell));
  for(const unit of observation.resources){
    if(!tacticalPacketDelivered(unit,observation.minute))continue;
    add(reportedTacticalPosition(unit,observation.minute,observation.geography));
    const target=activeTacticalTarget(unit,observation.minute);
    if(target)add(cellCenter(observation.geography,...target));
  }
  return [...points.values()].sort((first,second)=>first[0]-second[0]||first[1]-second[1]);
}

export function publicTacticalFrame(observation:Observation,mesh:TerrainMeshData,aspect:number):TacticalFrame{
  const points=publicTacticalPositions(observation,mesh),[minimum,maximum]=terrainHeightRange(mesh);
  const middleHeight=(minimum+maximum)/2,pitch=TACTICAL_PITCH_DEGREES*Math.PI/180;
  if(!points.length)return constrainTacticalFrame([(mesh.extent[0]+mesh.extent[2])/2,
    (mesh.extent[1]+mesh.extent[3])/2],mesh.extent[3]-mesh.extent[1],aspect,mesh.extent,maximum-minimum);
  const eastings=points.map(point=>point[0]),vertical=points.map(point=>point[1]*Math.sin(pitch)+terrainHeight(mesh,point)!*Math.cos(pitch));
  const west=Math.min(...eastings),east=Math.max(...eastings),south=Math.min(...vertical),north=Math.max(...vertical);
  const centre:Position=[(west+east)/2,((south+north)/2-middleHeight*Math.cos(pitch))/Math.sin(pitch)];
  return constrainTacticalFrame(centre,Math.max((east-west+1600)/aspect,north-south+1600),
    aspect,mesh.extent,maximum-minimum);
}

export function orthographicPixelsPerMetre(viewHeight:number,zoom:number,viewportPixels:number):number{
  if(![viewHeight,zoom,viewportPixels].every(Number.isFinite)||viewHeight<=0||zoom<=0||viewportPixels<=0)return 0;
  return viewportPixels*zoom/viewHeight;
}

export function tacticalZoomLimits(frame:TacticalFrame):{minZoom:number;maxZoom:number}{
  return {minZoom:frame.height/frame.maximumHeight,maxZoom:frame.height/frame.minimumHeight};
}

export function tacticalNorthRotation(azimuth:number,pitchDegrees:number):number{
  return Math.atan2(Math.sin(azimuth),Math.sin(pitchDegrees*Math.PI/180)*Math.cos(azimuth))*180/Math.PI;
}

export function tacticalScale(pixelsPerMetre:number):{pixels:number;label:string}{
  if(!Number.isFinite(pixelsPerMetre)||pixelsPerMetre<=0)return {pixels:0,label:'Scale unavailable'};
  const maximum=110/pixelsPerMetre,power=10**Math.floor(Math.log10(maximum));
  const metres=[5,2,1].map(factor=>factor*power).find(value=>value<=maximum)??power;
  return {pixels:metres*pixelsPerMetre,label:metres>=1000?`${metres/1000} ${metres===1000?'kilometre':'kilometres'}`:`${metres} metres`};
}

export function declutterTacticalLabels(candidates:TacticalLabelCandidate[],width:number,height:number,
  obstacles:LabelBounds[]=[],gap=6):TacticalLabelPlacement[]{
  const occupied=[...obstacles],result:TacticalLabelPlacement[]=[];
  const intersects=(first:LabelBounds,second:LabelBounds)=>first.left<second.right+gap&&first.right>second.left-gap
    &&first.top<second.bottom+gap&&first.bottom>second.top-gap;
  for(const label of [...candidates].sort((first,second)=>second.priority-first.priority||first.id.localeCompare(second.id))){
    if(![label.horizontal,label.vertical,label.width,label.height].every(Number.isFinite)||label.width<=0||label.height<=0)continue;
    const offsets:Position[]=[[0,-42],[0,42],[label.width/2+30,-30],[-label.width/2-30,-30],
      [label.width/2+30,30],[-label.width/2-30,30],[0,-82],[0,82]];
    for(const offset of offsets){
      const horizontal=label.horizontal+offset[0],vertical=label.vertical+offset[1];
      const bounds={left:horizontal-label.width/2,right:horizontal+label.width/2,
        top:vertical-label.height/2,bottom:vertical+label.height/2};
      if(bounds.left<10||bounds.right>width-10||bounds.top<58||bounds.bottom>height-80||occupied.some(other=>intersects(bounds,other)))continue;
      occupied.push(bounds);result.push({id:label.id,offset,bounds});break;
    }
  }
  return result;
}

export function staleTacticalCells(observation:Observation):Position[]{
  const latest=new Map<string,Observation['sensor_memory'][number]>();
  for(const sample of observation.sensor_memory){
    if(!Number.isInteger(sample.x)||!Number.isInteger(sample.y)||sample.x<0||sample.y<0
      ||sample.x>=observation.geography.width||sample.y>=observation.geography.height
      ||!Number.isFinite(sample.observed_at)||sample.observed_at>observation.minute)continue;
    const key=`${sample.x},${sample.y}`,previous=latest.get(key);
    if(!previous||sample.observed_at>previous.observed_at||sample.observed_at===previous.observed_at&&!sample.burning)latest.set(key,sample);
  }
  return [...latest.values()].filter(sample=>sample.burning&&observation.minute-sample.observed_at>4)
    .sort((first,second)=>first.y-second.y||first.x-second.x).map(sample=>[sample.x,sample.y]);
}

export function tacticalFireLabels(observation:Observation):{cell:Position;age:number}[]{
  const cells=effectCells(observation).slice(0,MAX_TACTICAL_FIRE_CELLS),remaining=new Map(cells.map(cell=>[cell.join(','),cell]));
  const labels:{cell:Position;age:number}[]=[];
  for(const seed of cells){
    if(!remaining.delete(seed.join(',')))continue;
    const component:Position[]=[seed],pending=[seed];
    while(pending.length){
      const current=pending.pop()!;
      for(const next of [[current[0]-1,current[1]],[current[0]+1,current[1]],
        [current[0],current[1]-1],[current[0],current[1]+1]] as Position[]){
        if(remaining.delete(next.join(','))){pending.push(next);component.push(next);}
      }
    }
    const centre=[component.reduce((sum,cell)=>sum+cell[0],0)/component.length,
      component.reduce((sum,cell)=>sum+cell[1],0)/component.length];
    const cell=[...component].sort((first,second)=>Math.hypot(first[0]-centre[0],first[1]-centre[1])
      -Math.hypot(second[0]-centre[0],second[1]-centre[1])||first[1]-second[1]||first[0]-second[0])[0];
    const observed=Math.max(...observation.sensor_memory.filter(sample=>sample.x===cell[0]&&sample.y===cell[1]
      &&sample.observed_at<=observation.minute).map(sample=>sample.observed_at));
    labels.push({cell,age:observation.minute-observed});
  }
  return labels;
}

export function fireObservationLabel(age:number):string{
  return `Observed ${age} ${age===1?'minute':'minutes'} ago`;
}

export function tacticalSmokePuff(column:number,row:number,index:number,phase:number,wind:Position|null):
  {offset:[number,number,number];size:number;opacity:number}|null{
  const direction=smokeDirection(wind);if(!direction||!Number.isFinite(phase))return null;
  const seed=particleSeed(column,row,index),age=((index+.3+phase*.26)/TACTICAL_SMOKE_PUFFS%1+1)%1;
  const drift=80+Math.pow(age,1.4)*1450,spread=30+age*100;
  return {offset:[direction[0]*drift+Math.sin(seed*23+age*4)*spread,45+age*850,
    -direction[1]*drift+Math.cos(seed*19+age*3)*spread],size:230+age*670,
  opacity:.62*Math.sin(Math.PI*(.13+.78*age))*(1-age*.3)};
}

export const TACTICAL_FIRE_VERTEX_SHADER=`
attribute float seed;
uniform float phase;
uniform float pixelsPerMetre;
varying float age;
varying float variation;
void main(){
  age=fract(seed+phase*.38);variation=seed;
  vec3 point=position;
  point.x+=sin(phase*2.3+seed*31.)*age*24.;
  point.y+=age*180.;
  gl_Position=projectionMatrix*modelViewMatrix*vec4(point,1.);
  gl_PointSize=clamp(mix(320.,140.,age)*pixelsPerMetre,5.,150.);
}`;
