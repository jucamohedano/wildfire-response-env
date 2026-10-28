import {describe,it,expect} from 'vitest';
import View from 'ol/View.js';
import {transform,transformExtent} from 'ol/proj.js';
import {fireBoundary,fireCells,mapViewOptions,spainExtent,mapProjection,limitCountryZoom,aerialTileUrl,localTerrainVisible} from './mapModel';

describe('fire evolution display',()=>{
  const sample=(x:number,y:number,observed_at=8,burning=true)=>({x,y,burning,observed_at});
  it('uses only received burning evidence, ages it and never invents heat or burn scars',()=>{
    const observation={minute:10,sensor_memory:[sample(1,2),sample(2,2,5),sample(3,2,10,false),sample(4,2,11)]};
    const cells=fireCells(observation,null);
    expect(cells).toEqual([
      {column:1,row:2,kind:'fire',heat:null,burned:0,observedAt:8},
      {column:2,row:2,kind:'stale',heat:null,burned:0,observedAt:5},
    ]);
    expect(fireCells({...observation,minute:16},null).every(cell=>cell.kind==='stale')).toBe(true);
    expect(fireCells({minute:0,sensor_memory:[]},null)).toEqual([]);
  });
  it('uses heat and burned fraction only in the explicit evaluator view',()=>{
    const world={episode_id:'a',decision:2,world_fire:[
      {column:1,row:2,heat:.6,burned:.2},
      {column:2,row:2,heat:0,burned:.8},
      {column:3,row:2,heat:0,burned:0},
    ]};
    const observation={minute:4,sensor_memory:[sample(5,5,4)]};
    expect(fireCells(observation,world).map(cell=>[cell.kind,cell.heat,cell.burned])).toEqual([['fire',.6,.2],['burned',0,.8]]);
    expect(fireCells(observation,null).map(cell=>[cell.column,cell.row])).toEqual([[5,5]]);
  });
  it('removes shared internal edges while preserving holes, disconnected cells and stale boundaries',()=>{
    const cells=fireCells({minute:10,sensor_memory:[sample(1,1),sample(2,1),sample(6,6),sample(3,1,2)]},null);
    const edges=fireBoundary(cells);
    expect(edges).toHaveLength(10);
    expect(edges).not.toContainEqual([[2,1],[2,2]]);
    expect(edges).toContainEqual([[3,1],[3,2]]);
    expect(fireBoundary(cells.filter(cell=>cell.kind==='stale'))).toEqual([]);
    const ring=fireCells({minute:8,sensor_memory:Array.from({length:9},(_,index)=>sample(index%3,Math.floor(index/3))).filter(cell=>cell.x!==1||cell.y!==1)},null);
    expect(fireBoundary(ring)).toHaveLength(16);
  });
  it('rebuilds the boundary from each selected frame rather than retaining future growth',()=>{
    const earlier={minute:8,sensor_memory:[sample(1,1)]};
    const later={minute:10,sensor_memory:[sample(1,1,10),sample(2,1,10)]};
    expect(fireBoundary(fireCells(earlier,null))).toHaveLength(4);
    expect(fireBoundary(fireCells(later,null))).toHaveLength(6);
    expect(fireBoundary(fireCells(earlier,null))).toHaveLength(4);
  });
});

describe('national map navigation',()=>{
  it('fits a tighter mainland/Balearic overview and limits zoom-out to that scale',()=>{
    const extent=transformExtent(spainExtent,'EPSG:4326',mapProjection,16);
    for(const size of [[1100,650],[320,300]]){
      const view=new View(mapViewOptions);
      view.fit(extent,{size,padding:[25,25,25,25]});
      const visible=view.calculateExtent(size);
      expect(view.getZoom()).toBeLessThan(7);
      expect(visible[0]).toBeLessThanOrEqual(extent[0]);
      expect(visible[1]).toBeLessThanOrEqual(extent[1]);
      expect(visible[2]).toBeGreaterThanOrEqual(extent[2]);
      expect(visible[3]).toBeGreaterThanOrEqual(extent[3]);
      const countryZoom=view.getZoom()!;
      view.setMinZoom(countryZoom);
      view.setZoom(countryZoom-3);
      expect(view.getZoom()).toBeCloseTo(countryZoom);
      expect(view.getMaxZoom()).toBeGreaterThan(19);
      expect(view.getMaxZoom()).toBeLessThanOrEqual(20);
    }
  });
  it('keeps native terrain and fire-cell edges square, without rotating or shifting the map',()=>{
    const view=new View(mapViewOptions);
    expect(view.getProjection().getCode()).toBe('EPSG:25830');
    expect(view.getRotation()).toBe(0);
    const corners=[[297000,4054500],[297500,4054500],[297500,4054000]].map(point=>transform(point,'EPSG:25830',view.getProjection()));
    const horizontal=[corners[1][0]-corners[0][0],corners[1][1]-corners[0][1]];
    const vertical=[corners[2][0]-corners[1][0],corners[2][1]-corners[1][1]];
    expect(horizontal).toEqual([500,0]);expect(vertical).toEqual([0,-500]);
    expect(horizontal[0]*vertical[0]+horizontal[1]*vertical[1]).toBe(0);
  });
  it('recomputes the country zoom-out limit when the same map becomes narrower',()=>{
    const view=new View(mapViewOptions),extent=transformExtent(spainExtent,'EPSG:4326',mapProjection,16);
    for(const size of [[1100,650],[320,300],[1100,650]]){
      limitCountryZoom(view,size);
      view.fit(extent,{size,padding:[25,25,25,25]});
      const visible=view.calculateExtent(size);
      expect(visible[0]).toBeLessThanOrEqual(extent[0]);expect(visible[2]).toBeGreaterThanOrEqual(extent[2]);
      expect(visible[1]).toBeLessThanOrEqual(extent[1]);expect(visible[3]).toBeGreaterThanOrEqual(extent[3]);
      expect(view.getZoom()).toBeCloseTo(view.getMinZoom());
    }
  });
});
describe('aerial background display',()=>{
  it('uses the official PNOA/Sentinel TMS layer and does not assume it is live sensor data',()=>{
    expect(aerialTileUrl).toBe('https://tms-pnoa-ma.idee.es/1.0.0/pnoa-ma/{z}/{x}/{-y}.jpeg');
    expect(localTerrainVisible(true)).toBe(false);
    expect(localTerrainVisible(false)).toBe(true);
  });
});
