import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import MapView from './MapView';
import type {MapMetadata,Observation} from './types';

const geography={width:37,height:36,cell_km:.5,crs:'EPSG:25830',transform:[500,0,297000,0,-500,4054500]};
const metadata:MapMetadata={extent:[297000,4036500,315500,4054500],crs:'EPSG:25830',image_url:'/api/map.png',source_label:'IGN'};
const observation={geography,minute:0,wind:null,sensor_memory:[],mobility:undefined} as unknown as Observation;

describe('v2 map view selector',()=>{
  it('offers 3D world with both original 2D styles and removes the redundant Coast control',()=>{
    const html=renderToStaticMarkup(<MapView metadata={metadata} observation={observation} world={null} selectedReport="" onSelect={()=>{}} focusKey="test"/>);
    expect(html).toContain('aria-label="Map view"');
    for(const label of ['Aerial imagery','Street map','3D world'])expect(html).toContain(label);
    expect(html).not.toContain('>Coast</button>');
    expect(html).not.toContain('>3D close-up</button>');
    expect(html).toContain('Fit domain');expect(html).toContain('Spain');
  });
});
