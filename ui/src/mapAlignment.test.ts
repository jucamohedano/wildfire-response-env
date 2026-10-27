import {describe,it,expect} from 'vitest';
import {transform} from 'ol/proj.js';
import {cellCenter} from './model';
import {mapMetadataMatches,mapProjection,aerialTileUrl,publicActivityExtent} from './mapModel';
import type {MapMetadata,Observation} from './types';

const grid={width:37,height:36,cell_km:.5,crs:'EPSG:25830',transform:[500,0,297000,0,-500,4054500]};
const metadata:MapMetadata={extent:[297000,4036500,315500,4054500],crs:'EPSG:25830',image_url:'/api/map.png',source_label:'IGN'};

describe('original source geography stays aligned',()=>{
  it('matches all four original grid edges and rejects displaced, rotated or wrong-CRS terrain',()=>{
    expect(mapMetadataMatches(metadata,grid)).toBe(true);
    for(let index=0;index<4;index++){
      const extent=[...metadata.extent] as MapMetadata['extent'];extent[index]+=25;
      expect(mapMetadataMatches({...metadata,extent},grid)).toBe(false);
    }
    expect(mapMetadataMatches({...metadata,crs:'EPSG:3857'},grid)).toBe(false);
    expect(mapMetadataMatches(metadata,{...grid,transform:[500,1,297000,0,-500,4054500]})).toBe(false);
    expect(mapMetadataMatches({...metadata,extent:[NaN,4036500,315500,4054500]},grid)).toBe(false);
  });
  it('keeps original pinned source cell centres and geographic coordinates, including continuous movement',()=>{
    const location=transform(cellCenter(grid,12,18),mapProjection,'EPSG:4326');
    expect(location[0]).toBeCloseTo(-5.1977253534,7);
    expect(location[1]).toBeCloseTo(36.5324099419,7);
    expect(transform(cellCenter(grid,0,0),mapProjection,'EPSG:4326')[0]).toBeCloseTo(-5.267069141,7);
    expect(cellCenter(grid,14,18.5)).toEqual([304250,4045000]);
    expect(grid.transform).toEqual([500,0,297000,0,-500,4054500]);
  });
  it('retains the official imagery source rather than generated cartography',()=>{
    expect(aerialTileUrl).toBe('https://tms-pnoa-ma.idee.es/1.0.0/pnoa-ma/{z}/{x}/{-y}.jpeg');
  });
  it('zooms around public activity without moving points or exposing future report/sensor locations',()=>{
    const observation={minute:0,geography:grid,districts:[{home:[14,18],position:null,refuge:[14,22]}],reports:[],sensor_memory:[],resources:[]} as unknown as Observation;
    const initial=publicActivityExtent(observation);
    expect(initial).toEqual([303500,4042500,305000,4046000]);
    const later={...observation,reports:[{location:[30,2],received_at:4,observed_at:2}],sensor_memory:[{x:30,y:2,burning:true,observed_at:4}]} as Observation;
    expect(publicActivityExtent(later)).toEqual(initial);
    expect(publicActivityExtent({...later,minute:4})[2]).toBeGreaterThan(initial[2]);
    expect(cellCenter(grid,14,18)).toEqual([304250,4045250]);
  });
});
