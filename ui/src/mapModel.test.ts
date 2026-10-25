import {describe,it,expect} from 'vitest';
import View from 'ol/View.js';
import {transformExtent} from 'ol/proj.js';
import {fireBoundary,fireCells,mapViewOptions,spainExtent} from './mapModel';

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
  it('fits the mainland, Balearics, Canaries and autonomous cities even in a narrow map',()=>{
    const extent=transformExtent(spainExtent,'EPSG:4326','EPSG:3857');
    for(const size of [[1100,650],[320,300]]){
      const view=new View(mapViewOptions);
      view.fit(extent,{size,padding:[25,25,25,25]});
      const visible=view.calculateExtent(size);
      expect(view.getZoom()).toBeLessThan(7);
      expect(visible[0]).toBeLessThanOrEqual(extent[0]);
      expect(visible[1]).toBeLessThanOrEqual(extent[1]);
      expect(visible[2]).toBeGreaterThanOrEqual(extent[2]);
      expect(visible[3]).toBeGreaterThanOrEqual(extent[3]);
      expect(view.getMinZoom()).toBe(2);
      expect(view.getMaxZoom()).toBe(18);
    }
  });
});
