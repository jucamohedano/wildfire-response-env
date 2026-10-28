import {terrainSupported,validateMesh} from './terrainSceneModel';
import type {TerrainMeshData} from './terrainSceneModel';

export function mountainShadeOpacity(elevation:number):number{
  if(!Number.isFinite(elevation))throw new Error('Terrain elevation must be finite.');
  const fraction=Math.max(0,Math.min(1,(elevation-250)/1050));
  return .36*fraction*fraction*(3-2*fraction);
}

function validateShadingMesh(mesh:TerrainMeshData):void{
  validateMesh(mesh,mesh.extent);
  for(const axis of [mesh.eastings,mesh.northings]){
    const spacing=(axis[axis.length-1]-axis[0])/(axis.length-1);
    if(axis.some((value,index)=>Math.abs(value-(axis[0]+index*spacing))>.01))throw new Error('Terrain shading requires regularly spaced source samples.');
  }
}

export function mountainShadePixels(mesh:TerrainMeshData):{pixels:Uint8ClampedArray<ArrayBuffer>;width:number;height:number}{
  validateShadingMesh(mesh);
  const width=mesh.columns-1,height=mesh.rows-1,pixels=new Uint8ClampedArray(width*height*4);
  for(let row=0;row<height;row++)for(let column=0;column<width;column++){
    if(!terrainSupported(mesh,row,column)||!terrainSupported(mesh,row,column+1)||!terrainSupported(mesh,row+1,column)||!terrainSupported(mesh,row+1,column+1))continue;
    const elevation=(mesh.heights_metres[row][column+1]+mesh.heights_metres[row+1][column])/2;
    const offset=(row*width+column)*4;
    pixels[offset]=12;pixels[offset+1]=12;pixels[offset+2]=12;
    pixels[offset+3]=Math.round(mountainShadeOpacity(elevation)*255);
  }
  return {pixels,width,height};
}

export function sunlitReliefPixels(mesh:TerrainMeshData):{pixels:Uint8ClampedArray<ArrayBuffer>;width:number;height:number}{
  validateShadingMesh(mesh);
  const width=mesh.columns-1,height=mesh.rows-1,pixels=new Uint8ClampedArray(width*height*4);
  const sun=1/Math.sqrt(3);
  for(let row=0;row<height;row++)for(let column=0;column<width;column++){
    if(!terrainSupported(mesh,row,column)||!terrainSupported(mesh,row,column+1)||!terrainSupported(mesh,row+1,column)||!terrainSupported(mesh,row+1,column+1))continue;
    const northWest=mesh.heights_metres[row][column],northEast=mesh.heights_metres[row][column+1];
    const southWest=mesh.heights_metres[row+1][column],southEast=mesh.heights_metres[row+1][column+1];
    const eastSlope=(northEast+southEast-northWest-southWest)/(2*(mesh.eastings[column+1]-mesh.eastings[column]));
    const northSlope=(northWest+northEast-southWest-southEast)/(2*(mesh.northings[row]-mesh.northings[row+1]));
    const light=(1+eastSlope-northSlope)*sun/Math.hypot(1,eastSlope,northSlope)-sun;
    const offset=(row*width+column)*4;
    if(light>=0){pixels[offset]=250;pixels[offset+1]=219;pixels[offset+2]=163;pixels[offset+3]=Math.round(Math.min(.30,light*.80)*255);}
    else{pixels[offset]=12;pixels[offset+1]=12;pixels[offset+2]=12;pixels[offset+3]=Math.round(Math.min(.22,-light*.50)*255);}
  }
  return {pixels,width,height};
}
