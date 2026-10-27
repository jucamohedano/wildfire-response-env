export type MapExtent=[number,number,number,number];
export interface MosaicPanel {extent:MapExtent;left:number;top:number;size:number}

export function frameMatchesCapture(frame:{size:number[];extent:number[]},size:number,requested:number[]):boolean{
  return frame.size.length===2&&frame.size[0]===size&&frame.size[1]===size&&frame.extent.length===4
    &&frame.extent.every(Number.isFinite)&&frame.extent[2]>frame.extent[0]&&frame.extent[3]>frame.extent[1]
    &&frame.extent[0]<=requested[0]+.01&&frame.extent[1]<=requested[1]+.01
    &&frame.extent[2]>=requested[2]-.01&&frame.extent[3]>=requested[3]-.01;
}

export function imageHasContent(pixels:Uint8ClampedArray):boolean{
  for(let offset=3;offset<pixels.length;offset+=4)if(pixels[offset]>0)return true;
  return false;
}

export function mosaicPanels(extent:MapExtent,size=1536):MosaicPanel[]{
  if(!extent.every(Number.isFinite)||extent[2]<=extent[0]||extent[3]<=extent[1]
    ||!Number.isInteger(size)||size<256||size>1536)throw new Error('Invalid bounded map mosaic.');
  const middleEast=(extent[0]+extent[2])/2,middleNorth=(extent[1]+extent[3])/2;
  return [
    {extent:[extent[0],middleNorth,middleEast,extent[3]],left:0,top:0,size},
    {extent:[middleEast,middleNorth,extent[2],extent[3]],left:size,top:0,size},
    {extent:[extent[0],extent[1],middleEast,middleNorth],left:0,top:size,size},
    {extent:[middleEast,extent[1],extent[2],middleNorth],left:size,top:size,size},
  ];
}

export function registeredCrop(source:MapExtent,target:MapExtent,width:number,height:number):[number,number,number,number]{
  if(![...source,...target,width,height].every(Number.isFinite)||width<=0||height<=0
    ||source[2]<=source[0]||source[3]<=source[1]||target[2]<=target[0]||target[3]<=target[1]
    ||target[0]<source[0]-.01||target[1]<source[1]-.01||target[2]>source[2]+.01||target[3]>source[3]+.01)throw new Error('Source image does not cover the mosaic panel.');
  return [(target[0]-source[0])/(source[2]-source[0])*width,
    (source[3]-target[3])/(source[3]-source[1])*height,
    (target[2]-target[0])/(source[2]-source[0])*width,
    (target[3]-target[1])/(source[3]-source[1])*height];
}
