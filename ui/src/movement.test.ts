import {describe,it,expect} from 'vitest';
import {coastalExtent,positionOnRoute,reportedPosition,routeDisplayLines} from './movement';
import type {Movement,Resource} from './types';

describe('reported movement follows recorded roads without future extrapolation',()=>{
  const route={departed_at:0,observed_at:4,received_at:5,position:[100,100],legs:[{coordinates:[[0,0],[100,0],[100,100]],distance_m:200,duration_minutes:4}]} as Movement;
  it('follows the bend in a route rather than drawing a straight shortcut',()=>{
    expect(positionOnRoute(route,1)).toEqual([50,0]);
    expect(positionOnRoute(route,3)).toEqual([100,50]);
    expect(positionOnRoute(route,100)).toEqual([100,100]);
    expect(positionOnRoute({...route,observed_at:3,position:[100,50]},4)).toEqual([100,50]);
  });
  it('withholds future or unstamped position reports',()=>{
    const unit={map_position:[100,50],observed_at:3,received_at:4} as Resource;
    expect(reportedPosition(unit,3)).toBeNull();
    expect(reportedPosition(unit,4)).toEqual([100,50]);
    expect(reportedPosition({...unit,received_at:2},4)).toBeNull();
    expect(reportedPosition({...unit,observed_at:undefined},4)).toBeNull();
  });
  it('frames the coastal towns separately from the unchanged simulation grid',()=>{
    expect(coastalExtent[0]).toBeLessThan(-5.27);
    expect(coastalExtent[2]).toBeGreaterThan(-4.89);
    expect(coastalExtent[1]).toBeLessThan(36.38);
  });
  it('batches connected drawing segments without shortcuts or joining disconnected roads',()=>{
    const journey={legs:[{mode:'road',coordinates:[[0,0],[10,0]]},{mode:'road',coordinates:[[10,0],[10,10]]},{mode:'walk',coordinates:[[10,10],[20,20]]},{mode:'road',coordinates:[[50,50],[60,60]]}]} as Movement;
    const before=JSON.stringify(journey);
    expect(routeDisplayLines(journey)).toEqual([{mode:'road',coordinates:[[0,0],[10,0],[10,10]]},{mode:'walk',coordinates:[[10,10],[20,20]]},{mode:'road',coordinates:[[50,50],[60,60]]}]);
    expect(JSON.stringify(journey)).toBe(before);
  });
});
