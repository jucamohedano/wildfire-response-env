import {describe,it,expect} from 'vitest';
import {mosaicPanels,registeredCrop,frameMatchesCapture,imageHasContent} from './mapMosaicV2';

describe('registered source-imagery mosaic',()=>{
  it('stitches four native coordinate panels north-first with shared edges and no coordinate shifts',()=>{
    const extent:[number,number,number,number]=[297000,4036500,315500,4054500],before=[...extent];
    const panels=mosaicPanels(extent);
    expect(panels.map(panel=>[panel.left,panel.top])).toEqual([[0,0],[1536,0],[0,1536],[1536,1536]]);
    expect(panels[0].extent[2]).toBe(panels[1].extent[0]);
    expect(panels[0].extent[1]).toBe(panels[2].extent[3]);
    expect(panels[3].extent).toEqual([306250,4036500,315500,4045500]);
    expect(extent).toEqual(before);
  });
  it('removes viewport padding by projected extent rather than stretching adjacent captures',()=>{
    expect(registeredCrop([0,0,200,200],[0,100,100,200],1000,1000)).toEqual([0,0,500,500]);
    expect(registeredCrop([0,0,200,200],[100,0,200,100],1000,1000)).toEqual([500,500,500,500]);
    expect(registeredCrop([0,-25,100,125],[0,0,100,100],1200,1800)).toEqual([0,300,1200,1200]);
  });
  it('rejects unsupported extents and unbounded image sizes instead of inventing coverage',()=>{
    expect(()=>mosaicPanels([0,0,NaN,10])).toThrow();
    expect(()=>mosaicPanels([0,0,100,100],10000)).toThrow();
    expect(()=>registeredCrop([0,0,100,100],[-1,0,100,100],1000,1000)).toThrow();
  });
  it('rejects stale-sized render frames during a downgrade and retains registration from the accepted frame',()=>{
    expect(frameMatchesCapture({size:[2048,2048],extent:[0,0,100,100]},1536,[0,0,100,100])).toBe(false);
    expect(frameMatchesCapture({size:[1536,1536],extent:[0,-25,100,125]},1536,[0,0,100,100])).toBe(true);
    expect(frameMatchesCapture({size:[1536,1536],extent:[0,0,75,100]},1536,[0,0,100,100])).toBe(false);
  });
  it('rejects missing or fully transparent imagery instead of accepting an empty mosaic',()=>{
    expect(imageHasContent(new Uint8ClampedArray())).toBe(false);
    expect(imageHasContent(new Uint8ClampedArray([200,200,200,0,100,100,100,0]))).toBe(false);
    expect(imageHasContent(new Uint8ClampedArray([20,50,80,255]))).toBe(true);
  });
});
