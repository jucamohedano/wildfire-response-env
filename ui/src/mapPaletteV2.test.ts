import {describe,expect,it} from 'vitest';
import {gradeMapPixels,referenceMapPalette} from './mapPaletteV2';

function luminance(pixel:ArrayLike<number>):number{
  return pixel[0]*.2126+pixel[1]*.7152+pixel[2]*.0722;
}

function singlePixel(red:number,green:number,blue:number,alpha=255):Uint8ClampedArray{
  return gradeMapPixels(new Uint8ClampedArray([red,green,blue,alpha]),1,1);
}

describe('reference map palette is an appearance-only RGBA transformation',()=>{
  it('returns independent deterministic storage without mutating source pixels or alpha',()=>{
    const pixels=new Uint8ClampedArray([120,100,65,255,68,60,38,129,210,190,145,0]);
    const before=pixels.slice(),first=gradeMapPixels(pixels,3,1),second=gradeMapPixels(pixels,3,1);
    expect(pixels).toEqual(before);expect(first).toEqual(second);
    expect(first).toBeInstanceOf(Uint8ClampedArray);expect(first.buffer).not.toBe(pixels.buffer);
    const buffer:ArrayBuffer=first.buffer;expect(buffer).toBeInstanceOf(ArrayBuffer);
    expect(first.length).toBe(pixels.length);
    for(let offset=3;offset<pixels.length;offset+=4)expect(first[offset]).toBe(pixels[offset]);
    expect([...first.slice(8,12)]).toEqual([...pixels.slice(8,12)]);
    first[0]=0;expect(pixels).toEqual(before);expect(second[0]).not.toBe(0);
  });

  it('preserves blue and cyan source colors exactly, including dark water-like colors',()=>{
    for(const color of [[12,43,67],[23,75,118],[45,130,128],[70,155,175],[140,160,170],[0,0,255]]){
      expect([...singlePixel(...color as [number,number,number])]).toEqual([...color,255]);
    }
  });

  it('preserves neutral rock, near-white highlights and the full neutral ramp without tinting',()=>{
    for(const color of [[137,132,124],[85,90,88],[180,176,164],[245,238,220],[255,255,255],[0,0,0]]){
      expect([...singlePixel(...color as [number,number,number])]).toEqual([...color,255]);
    }
    const pixels=new Uint8ClampedArray(256*4);
    for(let value=0;value<256;value++)pixels.set([value,value,value,value],value*4);
    expect(gradeMapPixels(pixels,256,1)).toEqual(pixels);
  });

  it('avoids abrupt color jumps across warm and blue classification boundaries',()=>{
    for(const colors of [
      Array.from({length:25},(_,index)=>[128+index,140,100]),
      Array.from({length:17},(_,index)=>[75,100,70+index]),
    ]){
      const results=colors.map(color=>singlePixel(...color as [number,number,number]));
      for(let index=1;index<results.length;index++)for(let channel=0;channel<3;channel++)
        expect(Math.abs(results[index][channel]-results[index-1][channel])).toBeLessThanOrEqual(3);
    }
  });

  it('makes warm midtones visibly greener while retaining their luminance ordering and detail',()=>{
    const colors=[[68,60,38],[90,70,50],[120,100,65],[150,125,80]],results=colors.map(color=>singlePixel(...color as [number,number,number]));
    for(let index=0;index<colors.length;index++){
      const source=colors[index],graded=results[index];
      expect(graded[1]-graded[0]).toBeGreaterThan(source[1]-source[0]+8);
      expect(luminance(graded)).toBeGreaterThanOrEqual(luminance(source)-1);
      expect(luminance(graded)).toBeLessThan(luminance(source)+16);
      if(index>0)expect(luminance(graded)).toBeGreaterThan(luminance(results[index-1]));
    }
    expect(results[2][0]).toBeLessThan(colors[2][0]-15);
    expect(results[2][1]).toBeGreaterThan(colors[2][1]+8);
    expect(luminance(results[0])).toBeGreaterThan(luminance(colors[0])+3);
  });

  it('warms bright sand colors without turning them into midtone green',()=>{
    const source=[218,195,150],graded=singlePixel(...source as [number,number,number]);
    expect(graded[0]).toBeGreaterThan(source[0]);
    expect(graded[0]).toBeGreaterThan(graded[1]);
    expect(graded[1]).toBeGreaterThan(graded[2]);
    expect(luminance(graded)).toBeGreaterThan(luminance(source));
    expect(luminance(graded)).toBeLessThan(luminance(source)+10);
  });

  it('keeps sharpening local, bounded and centered on existing pixels',()=>{
    const centre=[160,130,75],pixels=new Uint8ClampedArray(3*3*4);
    for(let offset=0;offset<pixels.length;offset+=4)pixels.set([60,48,30,255],offset);
    pixels.set([...centre,255],16);
    const plain=singlePixel(...centre as [number,number,number]),graded=gradeMapPixels(pixels,3,3);
    for(let channel=0;channel<3;channel++){
      expect(graded[16+channel]).toBeGreaterThan(plain[channel]);
      expect(graded[16+channel]-plain[channel]).toBeLessThanOrEqual(referenceMapPalette.sharpening.maximumChannelDelta);
    }
    for(const offset of [0,4,8,12,20,24,28,32]){
      const expected=gradeMapPixels(pixels.slice(offset,offset+4),1,1);
      expect(graded.slice(offset,offset+4)).toEqual(expected);
    }
  });

  it('does not sharpen through transparency or recolor protected edge pixels',()=>{
    const pixels=new Uint8ClampedArray(3*3*4);
    for(let offset=0;offset<pixels.length;offset+=4)pixels.set([210,180,110,255],offset);
    pixels.set([120,100,65,255],16);pixels[3]=0;pixels[7]=0;
    expect(gradeMapPixels(pixels,3,3).slice(16,20)).toEqual(singlePixel(120,100,65));
    pixels[7]=255;pixels[19]=128;
    expect(gradeMapPixels(pixels,3,3).slice(16,20)).toEqual(singlePixel(120,100,65,128));
    for(const color of [[22,75,110],[150,146,137]]){
      pixels.set([...color,255],16);
      expect([...gradeMapPixels(pixels,3,3).slice(16,20)]).toEqual([...color,255]);
    }
  });

  it('keeps a flat patch uniform instead of inventing texture',()=>{
    const pixels=new Uint8ClampedArray(5*4*4);
    for(let offset=0;offset<pixels.length;offset+=4)pixels.set([120,100,65,255],offset);
    const graded=gradeMapPixels(pixels,5,4),expected=singlePixel(120,100,65);
    for(let offset=0;offset<graded.length;offset+=4)expect(graded.slice(offset,offset+4)).toEqual(expected);
  });

  it('grades a buffer view in isolation without touching surrounding storage',()=>{
    const storage=new Uint8ClampedArray([7,8,9,10,120,100,65,200,11,12,13,14]),before=storage.slice();
    expect(gradeMapPixels(storage.subarray(4,8),1,1)).toEqual(singlePixel(120,100,65,200));
    expect(storage).toEqual(before);
  });

  it('clamps extreme source colors and preserves all alpha values in a larger varied image',()=>{
    const width=97,height=83,pixels=new Uint8ClampedArray(width*height*4);
    for(let offset=0;offset<pixels.length;offset++)pixels[offset]=(offset*73+(offset>>>3)*29)%256;
    pixels.set([255,0,0,255],0);pixels.set([0,255,0,255],4);pixels.set([255,255,0,255],8);
    const before=pixels.slice(),graded=gradeMapPixels(pixels,width,height);
    expect(graded.length).toBe(pixels.length);expect(pixels).toEqual(before);
    expect([...graded.slice(0,12)]).toEqual([255,10,10,255,0,255,0,255,255,255,0,255]);
    for(let offset=0;offset<graded.length;offset++){
      expect(graded[offset]).toBeGreaterThanOrEqual(0);expect(graded[offset]).toBeLessThanOrEqual(255);
      if(offset%4===3)expect(graded[offset]).toBe(pixels[offset]);
    }
  });

  it('rejects invalid dimensions, buffer lengths and input representations',()=>{
    const pixels=new Uint8ClampedArray(4);
    for(const [width,height] of [[0,1],[1,0],[-1,1],[1.5,1],[NaN,1],[1,Infinity],[2,1],[Number.MAX_SAFE_INTEGER,2]]){
      expect(()=>gradeMapPixels(pixels,width,height)).toThrow(RangeError);
    }
    expect(()=>gradeMapPixels(new Uint8ClampedArray(5),1,1)).toThrow(RangeError);
    expect(()=>gradeMapPixels(new Uint8Array(4) as unknown as Uint8ClampedArray,1,1)).toThrow(TypeError);
  });

  it('declares illustrative styling and bounded sharpening without geographic or land-cover data',()=>{
    expect(referenceMapPalette.role).toBe('illustrative-display-only');
    expect(referenceMapPalette.basis).toMatch(/not vegetation, fuel, burn-state/);
    expect(referenceMapPalette.sharpening.radiusPixels).toBe(1);
    expect(referenceMapPalette.sharpening.opaqueOnly).toBe(true);
    expect(Object.isFrozen(referenceMapPalette)).toBe(true);
    expect(Object.isFrozen(referenceMapPalette.colors)).toBe(true);
    expect(Object.isFrozen(referenceMapPalette.sharpening)).toBe(true);
  });
});
