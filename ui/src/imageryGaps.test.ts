import {describe,expect,it} from 'vitest';
import {fallbackColorField,imageryGapMask,sampleFallbackColor} from './imageryGaps';
import type {TerrainMeshData} from './terrainSceneModel';

describe('source imagery gap detection',()=>{
  it('marks large missing-color areas only where elevation is unsupported',()=>{
    const mesh={extent:[0,0,100,100],columns:3,rows:2,support_mask:['110','110']} as TerrainMeshData;
    const pixels=new Uint8ClampedArray(64*64*4);
    for(let offset=0;offset<pixels.length;offset+=4)pixels.set([32,26,38,255],offset);
    const mask=imageryGapMask(pixels,64,64,[0,0,100,100],mesh);
    expect(mask[10*64+10]).toBe(0);expect(mask[10*64+55]).toBe(3);
  });
  it('does not erase small dark details or green vegetation',()=>{
    const mesh={extent:[0,0,64,64],columns:2,rows:2,support_mask:['00','00']} as TerrainMeshData;
    const pixels=new Uint8ClampedArray(64*64*4);
    for(let offset=0;offset<pixels.length;offset+=4)pixels.set([36,66,38,255],offset);
    for(let row=10;row<15;row++)for(let column=10;column<15;column++)pixels.set([0,0,0,255],(row*64+column)*4);
    expect(imageryGapMask(pixels,64,64,[0,0,64,64],mesh).every(value=>value===0)).toBe(true);
  });
  it('balances fallback color using valid adjacent image pixels',()=>{
    const width=32,height=32,primary=new Uint8ClampedArray(width*height*4),fallback=new Uint8ClampedArray(width*height*4),mask=new Uint8Array(width*height);
    for(let offset=0;offset<primary.length;offset+=4){primary.set([100,110,120,255],offset);fallback.set([80,80,80,255],offset);}
    for(let row=8;row<24;row++)for(let column=8;column<24;column++)mask[row*width+column]=3;
    const field=fallbackColorField(primary,fallback,mask,width,height),adjustment=new Float32Array(3);
    sampleFallbackColor(field,16,16,adjustment);expect([...adjustment]).toEqual([20,30,40]);
  });
});
