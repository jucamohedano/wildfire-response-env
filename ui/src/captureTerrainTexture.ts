import OLMap from 'ol/Map.js';
import View from 'ol/View.js';
import TileLayer from 'ol/layer/Tile.js';
import OSM from 'ol/source/OSM.js';
import XYZ from 'ol/source/XYZ.js';
import {aerialTileUrl,mapProjection} from './mapModel';
import type {MapTexture} from './terrainSceneModel';
import type {TerrainMeshData} from './terrainSceneModel';
import {mosaicPanels,registeredCrop,frameMatchesCapture,imageHasContent} from './mapMosaicV2';
import type {MapExtent} from './mapMosaicV2';
import {fallbackColorField,imageryGapMask,isImageryVoid,sampleFallbackColor} from './imageryGaps';

const TEXTURE_SIZE=2048;

function captureMapPanel(extent:number[],style:'street'|'aerial',signal:AbortSignal,pixels=TEXTURE_SIZE,allowStreetFallback=true,maxSourceZoom=19):Promise<MapTexture>{
  return new Promise((resolve,reject)=>{
    const target=document.createElement('div');target.setAttribute('aria-hidden','true');
    Object.assign(target.style,{position:'fixed',left:'-20000px',top:'0',width:`${pixels}px`,height:`${pixels}px`,pointerEvents:'none'});
    document.body.append(target);
    const streets=new OSM({crossOrigin:'anonymous'}),aerial=new XYZ({url:aerialTileUrl,crossOrigin:'anonymous',maxZoom:maxSourceZoom});
    const streetLayer=new TileLayer({source:streets,visible:style==='street'}),aerialLayer=new TileLayer({source:aerial,visible:style==='aerial'});
    let aerialFailed=false,streetsFailed=false,finished=false,size=pixels;
    const view=new View({projection:mapProjection,enableRotation:false});
    const map=new OLMap({target,view,controls:[],interactions:[],pixelRatio:1,layers:[streetLayer,aerialLayer]});
    const cleanup=()=>{window.clearTimeout(timer);window.clearTimeout(downgrade);signal.removeEventListener('abort',abort);map.setTarget(undefined);map.dispose();target.remove();};
    const fail=(message:string)=>{if(finished)return;finished=true;cleanup();reject(new Error(message));};
    const abort=()=>fail('3D preparation cancelled.');
    const timer=window.setTimeout(()=>fail('Map imagery is still loading. Retry the 3D view.'),allowStreetFallback?30000:18000);
    const downgrade=window.setTimeout(()=>{if(finished||size<=1536)return;size=1536;target.style.width=`${size}px`;target.style.height=`${size}px`;map.updateSize();view.fit(extent,{size:[size,size],padding:[0,0,0,0]});map.render();},10000);
    streets.on('tileloaderror',()=>{streetsFailed=true;});
    aerial.on('tileloaderror',()=>{if(!allowStreetFallback){fail('Aerial mosaic panel unavailable.');return;}aerialFailed=true;aerialLayer.setVisible(false);streetLayer.setVisible(true);});
    map.on('rendercomplete',event=>{
      if(finished)return;
      const frame=event.frameState,renderedExtent=frame?.extent;
      if(!frame||!renderedExtent||!frameMatchesCapture({size:frame.size,extent:renderedExtent},size,extent))return;
      if(streetsFailed&&(style==='street'||aerialFailed)){fail('Source map imagery is unavailable; no invented map texture will be used.');return;}
      try{
        const canvas=document.createElement('canvas');canvas.width=size;canvas.height=size;
        const context=canvas.getContext('2d');if(!context)throw new Error('Canvas rendering is unavailable.');
        const viewport=map.getViewport();if(!viewport)throw new Error('The map capture viewport is unavailable.');
        let drawables=0;
        for(const source of viewport.querySelectorAll<HTMLCanvasElement>('canvas')){
          if(!source.width||!source.height)continue;
          const matrix=new DOMMatrix(source.style.transform||undefined);
          context.globalAlpha=Number(source.parentElement?.style.opacity||1);
          context.setTransform(matrix);context.drawImage(source,0,0);
          drawables++;
        }
        if(!drawables||!imageHasContent(context.getImageData(0,0,size,size).data))throw new Error('Source map capture is empty.');
        const result:MapTexture={canvas,extent:[...renderedExtent] as MapExtent,label:style==='aerial'&&!aerialFailed?'IGN PNOA / Sentinel-2 imagery':'OpenStreetMap street-map texture'};
        finished=true;cleanup();resolve(result);
      }catch(error){fail(error instanceof Error?error.message:'The source map could not be registered as a 3D texture.');}
    });
    signal.addEventListener('abort',abort,{once:true});
    if(signal.aborted){abort();return;}
    view.fit(extent,{size:[pixels,pixels],padding:[0,0,0,0]});map.render();
  });
}

async function gapFallback(texture:MapTexture,mesh:TerrainMeshData|undefined,signal:AbortSignal):Promise<MapTexture>{
  if(!mesh?.support_mask)return texture;
  const context=texture.canvas.getContext('2d');if(!context)throw new Error('Map imagery is unavailable.');
  const width=texture.canvas.width,height=texture.canvas.height,primary=context.getImageData(0,0,width,height);
  const mask=imageryGapMask(primary.data,width,height,texture.extent,mesh);
  if(!mask.some(value=>value===3))return texture;
  try{
    const overview=await captureMapPanel(texture.extent,'aerial',signal,1536,true,13);
    const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
    const fallbackContext=canvas.getContext('2d');if(!fallbackContext)throw new Error('Fallback imagery is unavailable.');
    const crop=registeredCrop(overview.extent,texture.extent,overview.canvas.width,overview.canvas.height);
    fallbackContext.drawImage(overview.canvas,...crop,0,0,width,height);
    const fallback=fallbackContext.getImageData(0,0,width,height).data;
    const correction=overview.label.startsWith('OpenStreetMap')?null:fallbackColorField(primary.data,fallback,mask,width,height);
    let needsStreet=false;
    for(let index=0;index<mask.length;index++)if(mask[index]===3){
      const offset=index*4;
      if(isImageryVoid(fallback[offset],fallback[offset+1],fallback[offset+2],fallback[offset+3])){needsStreet=true;break;}
    }
    let street:Uint8ClampedArray|null=null;
    if(needsStreet){
      const contextMap=await captureMapPanel(texture.extent,'street',signal,1536);
      const streetCrop=registeredCrop(contextMap.extent,texture.extent,contextMap.canvas.width,contextMap.canvas.height);
      fallbackContext.clearRect(0,0,width,height);fallbackContext.drawImage(contextMap.canvas,...streetCrop,0,0,width,height);
      street=fallbackContext.getImageData(0,0,width,height).data;
    }
    let unfilled=0;const adjustment=new Float32Array(3);
    for(let index=0;index<mask.length;index++)if(mask[index]===3){
      const offset=index*4,missing=isImageryVoid(fallback[offset],fallback[offset+1],fallback[offset+2],fallback[offset+3]);
      const source=missing?street:fallback;
      if(!source||isImageryVoid(source[offset],source[offset+1],source[offset+2],source[offset+3])){unfilled++;continue;}
      if(!missing&&correction)sampleFallbackColor(correction,index%width,Math.floor(index/width),adjustment);else adjustment.fill(0);
      primary.data[offset]=source[offset]+adjustment[0];primary.data[offset+1]=source[offset+1]+adjustment[1];
      primary.data[offset+2]=source[offset+2]+adjustment[2];primary.data[offset+3]=source[offset+3];
    }
    context.putImageData(primary,0,0);
    const sourceLabel=overview.label.startsWith('OpenStreetMap')?'OSM':`color-balanced lower-resolution IGN${needsStreet?' / OSM':''}`;
    return {...texture,label:`${texture.label} · imagery gaps filled from ${sourceLabel}${unfilled?' (some gaps remain)':''}`};
  }catch(error){
    if(signal.aborted)throw error;
    return {...texture,label:`${texture.label} · some source imagery gaps remain`};
  }
}

export async function captureTerrainTexture(extent:number[],style:'street'|'aerial',signal:AbortSignal,mesh?:TerrainMeshData):Promise<MapTexture>{
  if(style==='street')return captureMapPanel(extent,style,signal);
  const controller=new AbortController(),abort=()=>controller.abort();signal.addEventListener('abort',abort,{once:true});
  if(signal.aborted)controller.abort();
  try{
    const panels=mosaicPanels(extent as MapExtent),canvas=document.createElement('canvas');canvas.width=3072;canvas.height=3072;
    const context=canvas.getContext('2d');if(!context)throw new Error('Mosaic rendering is unavailable.');
    for(let batch=0;batch<panels.length;batch+=2){
      const group=panels.slice(batch,batch+2),images=await Promise.all(group.map(panel=>captureMapPanel(panel.extent,'aerial',controller.signal,panel.size,false)));
      for(let index=0;index<group.length;index++){
        const panel=group[index],image=images[index],crop=registeredCrop(image.extent,panel.extent,image.canvas.width,image.canvas.height);
        context.drawImage(image.canvas,...crop,panel.left,panel.top,panel.size,panel.size);
        if(!imageHasContent(context.getImageData(panel.left,panel.top,panel.size,panel.size).data))throw new Error('The cropped mosaic panel is empty.');
      }
    }
    return await gapFallback({canvas,extent:[...extent] as MapExtent,label:'IGN PNOA / Sentinel-2 · registered 4-panel mosaic'},mesh,signal);
  }catch(error){
    controller.abort();if(signal.aborted)throw error;
    return gapFallback(await captureMapPanel(extent,'aerial',signal),mesh,signal);
  }finally{signal.removeEventListener('abort',abort);}
}
