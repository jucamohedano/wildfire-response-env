import {describe,it,expect} from 'vitest';
import {effectCells,effectsAnimate,MAX_EFFECT_CELLS,PARTICLES_PER_CELL,particleSeed,smokeDirection} from './visualEffects';

describe('cosmetic effects preserve the public evidence boundary',()=>{
  const geography={width:8,height:8,crs:'EPSG:25830',cell_km:.5,transform:[500,0,0,0,-500,4000] as [number,number,number,number,number,number]};
  const sample=(x:number,y:number,observed_at:number,burning=true)=>({x,y,observed_at,burning});
  it('excludes future, stale, extinguished and out-of-domain samples, without mutation',()=>{
    const observation={minute:10,geography,sensor_memory:[sample(1,2,6),sample(2,2,5),sample(3,2,11),sample(4,2,9,false),sample(8,2,9),sample(1.5,2,9)]};
    const original=JSON.stringify(observation);
    expect(effectCells(observation)).toEqual([[1,2]]);
    expect(JSON.stringify(observation)).toBe(original);
    expect(effectCells({...observation,minute:4})).toEqual([]);
  });
  it('deduplicates each cell using the latest delivered sensor evidence',()=>{
    expect(effectCells({minute:10,geography,sensor_memory:[sample(2,2,9),sample(1,1,8),sample(2,2,10,false),sample(1,1,7)]})).toEqual([[1,1]]);
  });
  it('converts engine east/south wind into native east/north, never inferring absent wind',()=>{
    expect(smokeDirection([0,1])).toEqual([0,-1]);
    expect(smokeDirection([1,0])).toEqual([1,-0]);
    expect(smokeDirection([-3,4])).toEqual([-.6,-.8]);
    for(const wind of [null,[0,0],[NaN,1],[Infinity,0]] as ([number,number]|null)[])expect(smokeDirection(wind)).toBeNull();
  });
  it('uses reproducible decorative seeds and a bounded rendering budget',()=>{
    const seeds=Array.from({length:30},(_,index)=>particleSeed(2,3,index));
    expect(seeds).toEqual(Array.from({length:30},(_,index)=>particleSeed(2,3,index)));
    expect(seeds.every(seed=>seed>=0&&seed<1)).toBe(true);
    expect(MAX_EFFECT_CELLS*PARTICLES_PER_CELL).toBeLessThanOrEqual(240);
  });
  it('animates only during visible playback without reduced-motion preference',()=>{
    expect(effectsAnimate(true,true,false)).toBe(true);
    expect(effectsAnimate(false,true,false)).toBe(false);
    expect(effectsAnimate(true,false,false)).toBe(false);
    expect(effectsAnimate(true,true,true)).toBe(false);
  });
});
