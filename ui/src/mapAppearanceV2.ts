import {gradeIllustratedMapPixels,gradeMapPixels} from './mapPaletteV2';
import type {MapExtent} from './mapMosaicV2';
import {registeredCrop} from './mapMosaicV2';
import {mountainShadePixels,sunlitReliefPixels} from './terrainPaletteV2';
import type {TerrainMeshData} from './terrainSceneModel';

export interface DisplayRoadLine {id:string;kind:'major'|'local'|'track'|'path';coordinates:[number,number][]}
export interface DisplayRoadMap {version:string;crs:string;extent:MapExtent;role:string;lines:DisplayRoadLine[]}
export type MapPalette='illustrated'|'reference'|'original';

export function validateDisplayRoads(value:DisplayRoadMap,extent:MapExtent):void{
  if(value.crs!=='EPSG:25830'||value.role!=='display_only_not_routing'||!Array.isArray(value.extent)
    ||value.extent.length!==4||!value.extent.every((coordinate,index)=>Number.isFinite(coordinate)&&Math.abs(coordinate-extent[index])<.01)
    ||!Array.isArray(value.lines)||value.lines.length>30000)throw new Error('Display road geography does not match the terrain.');
  let vertices=0;
  for(const line of value.lines){
    if(!['major','local','track','path'].includes(line.kind)||!Array.isArray(line.coordinates)||line.coordinates.length<2)throw new Error('Invalid display road line.');
    vertices+=line.coordinates.length;
    if(vertices>200000||line.coordinates.some(point=>!Array.isArray(point)||point.length!==2||!point.every(Number.isFinite)
      ||point[0]<extent[0]-.1||point[0]>extent[2]+.1||point[1]<extent[1]-.1||point[1]>extent[3]+.1))throw new Error('Display road vertices exceed supported terrain.');
  }
}

export function roadPixel(point:[number,number],extent:MapExtent,width:number,height:number):[number,number]{
  return [(point[0]-extent[0])/(extent[2]-extent[0])*width,(extent[3]-point[1])/(extent[3]-extent[1])*height];
}

export function drawDisplayRoads(context:CanvasRenderingContext2D,roads:DisplayRoadMap,extent:MapExtent,width:number,height:number):void{
  const metresPerPixel=((extent[2]-extent[0])/width+(extent[3]-extent[1])/height)/2;
  const styles={path:{color:'#c4c1a4',width:4,alpha:.54,dash:[12,16]},track:{color:'#d7c295',width:9,alpha:.82,dash:[28,12]},
    local:{color:'#f0d6a5',width:16,alpha:.97,dash:[]},major:{color:'#fff0cb',width:30,alpha:1,dash:[]}};
  context.save();context.lineCap='round';context.lineJoin='round';
  for(const kind of ['path','track','local','major'] as const){
    const style=styles[kind];context.beginPath();
    for(const line of roads.lines){
      if(line.kind!==kind)continue;
      const first=roadPixel(line.coordinates[0],extent,width,height);context.moveTo(...first);
      for(let index=1;index<line.coordinates.length;index++)context.lineTo(...roadPixel(line.coordinates[index],extent,width,height));
    }
    context.setLineDash(style.dash.map(length=>length/metresPerPixel));
    if(kind==='local'||kind==='major'){
      context.strokeStyle='#29372a';context.lineWidth=(style.width+12)/metresPerPixel;context.globalAlpha=.7;context.stroke();
    }
    context.strokeStyle=style.color;context.lineWidth=Math.max(.55,style.width/metresPerPixel);context.globalAlpha=style.alpha;context.stroke();
  }
  context.restore();
}

export function mapAppearance(source:HTMLCanvasElement,extent:MapExtent,mesh:TerrainMeshData):{canvas:HTMLCanvasElement;draw:(palette:MapPalette,roads:DisplayRoadMap|null)=>void}{
  const canvas=document.createElement('canvas');canvas.width=source.width;canvas.height=source.height;
  const context=canvas.getContext('2d');if(!context)throw new Error('Map appearance rendering is unavailable.');
  const styled:Partial<Record<Exclude<MapPalette,'original'>,HTMLCanvasElement>>={};
  return {canvas,draw(palette,roads){
    if(palette!=='original'&&!styled[palette]){
      const sourceContext=source.getContext('2d');if(!sourceContext)throw new Error('Source imagery is unavailable.');
      const pixels=sourceContext.getImageData(0,0,source.width,source.height);
      const styledCanvas=document.createElement('canvas');styledCanvas.width=source.width;styledCanvas.height=source.height;
      const styleContext=styledCanvas.getContext('2d');if(!styleContext)throw new Error('Map palette rendering is unavailable.');
      const graded=palette==='illustrated'?gradeIllustratedMapPixels(pixels.data,source.width,source.height):gradeMapPixels(pixels.data,source.width,source.height);
      styleContext.putImageData(new ImageData(graded,source.width,source.height),0,0);
      const destination=registeredCrop(extent,mesh.extent,source.width,source.height);
      styleContext.save();styleContext.globalCompositeOperation='source-atop';styleContext.imageSmoothingEnabled=true;
      styleContext.imageSmoothingQuality='high';
      const shades=palette==='illustrated'?[{image:mountainShadePixels(mesh),opacity:.6},{image:sunlitReliefPixels(mesh),opacity:1}]
        :[{image:mountainShadePixels(mesh),opacity:1}];
      for(const {image:shade,opacity} of shades){
        const overlay=document.createElement('canvas');overlay.width=shade.width;overlay.height=shade.height;
        const overlayContext=overlay.getContext('2d');if(!overlayContext)throw new Error('Terrain shading is unavailable.');
        overlayContext.putImageData(new ImageData(shade.pixels,shade.width,shade.height),0,0);
        styleContext.globalAlpha=opacity;
        styleContext.drawImage(overlay,...destination);
      }
      styleContext.restore();styled[palette]=styledCanvas;
    }
    context.clearRect(0,0,canvas.width,canvas.height);context.drawImage(palette==='original'?source:styled[palette]!,0,0);
    if(roads){validateDisplayRoads(roads,mesh.extent);drawDisplayRoads(context,roads,extent,canvas.width,canvas.height);}
  }};
}
