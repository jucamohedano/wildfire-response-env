export const referenceMapPalette=Object.freeze({
  version:'reference-map-palette-v2',role:'illustrative-display-only',
  basis:'RGB colour styling, not vegetation, fuel, burn-state or acquisition-date evidence.',
  colors:Object.freeze({pine:'#294b31',olive:'#687440',sand:'#d5c18f',cream:'#ede0bb'}),
  earthMidtoneStrength:1.55,
  sharpening:Object.freeze({radiusPixels:1,strength:.45,maximumChannelDelta:8,opaqueOnly:true}),
});

function smoothstep(minimum:number,maximum:number,value:number):number{
  const fraction=Math.max(0,Math.min(1,(value-minimum)/(maximum-minimum)));
  return fraction*fraction*(3-2*fraction);
}

function validateMapPixels(pixels:Uint8ClampedArray,width:number,height:number):void{
  if(!(pixels instanceof Uint8ClampedArray))throw new TypeError('Expected an RGBA Uint8ClampedArray.');
  if(!Number.isSafeInteger(width)||!Number.isSafeInteger(height)||width<=0||height<=0
    ||!Number.isSafeInteger(width*height*4)||pixels.length!==width*height*4){
    throw new RangeError('Positive integer dimensions must match the RGBA buffer length.');
  }
}

export function gradeMapPixels(pixels:Uint8ClampedArray,width:number,height:number):Uint8ClampedArray<ArrayBuffer>{
  validateMapPixels(pixels,width,height);
  const result=new Uint8ClampedArray(pixels),stride=width*4;
  const {strength,maximumChannelDelta}=referenceMapPalette.sharpening;
  for(let row=0;row<height;row++){
    for(let column=0;column<width;column++){
      const offset=row*stride+column*4,alpha=pixels[offset+3];
      if(alpha===0)continue;
      const red=pixels[offset],green=pixels[offset+1],blue=pixels[offset+2];
      const maximum=Math.max(red,green,blue),minimum=Math.min(red,green,blue),chroma=maximum-minimum;
      const chromaticWeight=smoothstep(18,42,chroma);
      const waterWeight=smoothstep(-12,0,blue-red)*smoothstep(-12,0,blue-green*.78);
      const highlightProtection=smoothstep(222,230,maximum)*(1-smoothstep(36,44,chroma));
      const styleWeight=chromaticWeight*(1-waterWeight)*(1-highlightProtection);
      if(styleWeight===0)continue;
      const luminance=(54*red+183*green+19*blue)/256;
      const warmWeight=styleWeight*smoothstep(4,28,Math.min(red,green)-blue);
      const midtoneWeight=1-smoothstep(110,180,luminance);
      const earthStrength=1.1+(referenceMapPalette.earthMidtoneStrength-1.1)*smoothstep(-12,12,red-green);
      const greenShift=warmWeight*midtoneWeight*(10+chroma*.36)*earthStrength;
      const highlightWarmth=warmWeight*smoothstep(145,220,luminance)*12;
      const shadowLift=Math.max(0,14*(1-luminance/180))*styleWeight;
      let detail=0;
      if(alpha===255&&row>0&&row<height-1&&column>0&&column<width-1){
        const left=offset-4,right=offset+4,above=offset-stride,below=offset+stride;
        if(pixels[left+3]===255&&pixels[right+3]===255&&pixels[above+3]===255&&pixels[below+3]===255){
          const neighbours=((pixels[left]+pixels[right]+pixels[above]+pixels[below])*54
            +(pixels[left+1]+pixels[right+1]+pixels[above+1]+pixels[below+1])*183
            +(pixels[left+2]+pixels[right+2]+pixels[above+2]+pixels[below+2])*19)/1024;
          detail=Math.max(-maximumChannelDelta,Math.min(maximumChannelDelta,(luminance-neighbours)*strength))*styleWeight;
        }
      }
      result[offset]=red-greenShift+highlightWarmth+shadowLift+detail;
      result[offset+1]=green+greenShift*.34+highlightWarmth*.5+shadowLift+detail;
      result[offset+2]=blue-greenShift*.30-highlightWarmth*.65+shadowLift+detail;
    }
  }
  return result;
}

export function gradeIllustratedMapPixels(pixels:Uint8ClampedArray,width:number,height:number):Uint8ClampedArray<ArrayBuffer>{
  validateMapPixels(pixels,width,height);
  const result=new Uint8ClampedArray(pixels);
  const curve=Uint8ClampedArray.from({length:256},(_,value)=>255*Math.pow(value/255,.86));
  for(let offset=0;offset<result.length;offset+=4){
    if(result[offset+3]===0)continue;
    const luminance=Math.round((54*pixels[offset]+183*pixels[offset+1]+19*pixels[offset+2])/256);
    if(luminance===0)continue;
    const gain=curve[luminance]/luminance;
    result[offset]=pixels[offset]*gain;result[offset+1]=pixels[offset+1]*gain;result[offset+2]=pixels[offset+2]*gain;
  }
  return result;
}
