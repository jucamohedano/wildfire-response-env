import {useEffect,useRef,useState} from 'react';
import OLMap from 'ol/Map.js';
import View from 'ol/View.js';
import Feature from 'ol/Feature.js';
import Point from 'ol/geom/Point.js';
import Polygon from 'ol/geom/Polygon.js';
import LineString from 'ol/geom/LineString.js';
import MultiLineString from 'ol/geom/MultiLineString.js';
import type Geometry from 'ol/geom/Geometry.js';
import VectorSource from 'ol/source/Vector.js';
import OSM from 'ol/source/OSM.js';
import XYZ from 'ol/source/XYZ.js';
import ImageStatic from 'ol/source/ImageStatic.js';
import VectorLayer from 'ol/layer/Vector.js';
import TileLayer from 'ol/layer/Tile.js';
import ImageLayer from 'ol/layer/Image.js';
import {Style,Fill,Stroke,Circle as CircleStyle,Text} from 'ol/style.js';
import {transform,transformExtent} from 'ol/proj.js';
import {defaults as interactions} from 'ol/interaction/defaults.js';
import {defaults as controls} from 'ol/control/defaults.js';
import ScaleLine from 'ol/control/ScaleLine.js';
import 'ol/ol.css';
import {cellAt,cellCenter,groupName} from './model';
import {fireCells,fireBoundary,mapViewOptions,spainExtent,mapProjection,limitCountryZoom,aerialTileUrl,terrainOpacity} from './mapModel';
import MapLegend from './MapLegend';
import {mapColors} from './mapSymbols';
import type {MapMetadata,Observation,Position,World} from './types';

const project=(point:Position)=>transform(point,'EPSG:25830',mapProjection);
type Selection={type:'report'|'district'|'resource';id:string};

function featureStyle(feature:Feature,narrow=false,resolution=0):Style|Style[]|undefined {
  const kind=feature.get('kind') as string;
  if(resolution>300&&!['domain','locator'].includes(kind))return undefined;
  if(kind==='locator')return resolution>300?new Style({image:new CircleStyle({radius:6,fill:new Fill({color:'#a72a1c'}),stroke:new Stroke({color:'#fff',width:2})}),text:new Text({text:'Simulation domain',offsetY:-18,font:'600 12px system-ui',fill:new Fill({color:'#172d22'}),stroke:new Stroke({color:'#fff',width:3})}),zIndex:10}):undefined;
  const color=kind==='claim'?mapColors.claim:kind==='unit'?mapColors.unit:kind==='exposed'?mapColors.exposed:mapColors.population;
  if(kind==='guard'||kind==='domain')return [new Style({stroke:new Stroke({color:'rgba(255,255,255,.85)',width:4,lineDash:kind==='guard'?[5,5]:undefined})}),new Style({stroke:new Stroke({color:kind==='domain'?mapColors.domain:mapColors.guard,width:1.8,lineDash:kind==='guard'?[5,5]:undefined})})];
  if(kind==='grid')return new Style({stroke:new Stroke({color:'rgba(38,51,36,.5)',width:.8})});
  if(kind==='front')return new Style({stroke:new Stroke({color:mapColors.front,width:2.5}),zIndex:3});
  if(kind==='burned')return new Style({fill:new Fill({color:`rgba(48,34,31,${.28+.52*(feature.get('burned') as number)})`}),zIndex:1});
  if(kind==='fire'){
    const heat=feature.get('heat') as number|null;
    return new Style({fill:new Fill({color:heat===null?mapColors.fire:`rgba(255,${Math.round(65+170*Math.min(1,Math.max(0,heat)))},25,.88)`}),zIndex:2});
  }
  if(kind==='stale')return new Style({fill:new Fill({color:mapColors.staleFill}),stroke:new Stroke({color:mapColors.staleLine,width:1.5,lineDash:[4,3]}),zIndex:1});
  const label=feature.get('label') as string;
  return new Style({image:new CircleStyle({radius:kind==='claim'?11:kind==='refuge'?4:7,fill:new Fill({color:kind==='claim'?'rgba(248,219,154,.2)':color}),stroke:new Stroke({color:kind==='claim'?color:'#fff',width:kind==='claim'?1.8:1.7,lineDash:kind==='claim'?[4,3]:undefined})}),text:new Text({text:narrow&&(kind==='population'||kind==='exposed')?label.split(' · ')[0]:label,offsetY:kind==='claim'?-21:-18,font:'600 12px system-ui',fill:new Fill({color:'#193428'}),stroke:new Stroke({color:'rgba(255,255,255,.95)',width:3})}),zIndex:5});
}

export default function MapView({metadata,observation,world,selectedReport,onSelect,focusKey}:{metadata:MapMetadata;observation:Observation;world:World|null;selectedReport:string;onSelect:(selection:Selection)=>void;focusKey:string}) {
  const container=useRef<HTMLDivElement>(null),map=useRef<OLMap|null>(null),overlay=useRef<VectorSource<Feature<Geometry>>|null>(null),context=useRef<TileLayer<OSM>|null>(null),terrain=useRef<ImageLayer<ImageStatic>|null>(null);
  const cancelIntro=useRef(()=>{});
  const aerial=useRef<TileLayer<XYZ>|null>(null);
  const previousFocus=useRef(focusKey);
  const current=useRef(observation),select=useRef(onSelect);
  current.current=observation;select.current=onSelect;
  const [grid,setGrid]=useState(false),[contextStatus,setContextStatus]=useState('loading'),[showMap,setShowMap]=useState(true),[showTerrain,setShowTerrain]=useState(false),[hover,setHover]=useState('Select a report or point at a cell to inspect its mean elevation.');
  const [baseStyle,setBaseStyle]=useState<'street'|'aerial'>('aerial'),[aerialStatus,setAerialStatus]=useState('loading');
  useEffect(()=>{
    if(!container.current)return;
    const source=new VectorSource<Feature<Geometry>>();overlay.current=source;
    const contextSource=new OSM({crossOrigin:'anonymous',maxZoom:19});
    const contextLayer=new TileLayer({source:contextSource,className:'national-context'});context.current=contextLayer;
    contextSource.on('tileloadend',()=>setContextStatus(previous=>previous==='unavailable'?previous:'ready'));
    contextSource.on('tileloaderror',()=>{setContextStatus('unavailable');contextLayer.setVisible(false);});
    const aerialSource=new XYZ({url:aerialTileUrl,crossOrigin:'anonymous',maxZoom:19,attributions:'Imagery © IGN · PNOA / Sentinel-2 · not live'});
    const aerialLayer=new TileLayer({source:aerialSource});aerial.current=aerialLayer;
    aerialSource.on('tileloadend',()=>setAerialStatus(previous=>previous==='unavailable'?previous:'ready'));
    aerialSource.on('tileloaderror',()=>{setAerialStatus('unavailable');aerialLayer.setVisible(false);});
    const view=new View({...mapViewOptions,center:project([(metadata.extent[0]+metadata.extent[2])/2,(metadata.extent[1]+metadata.extent[3])/2]),zoom:12});
    const terrainLayer=new ImageLayer({className:'terrain-relief',opacity:.38,source:new ImageStatic({url:metadata.image_url,projection:metadata.crs,imageExtent:metadata.extent,attributions:metadata.source_label.startsWith('IGN')?'Terrain © IGN · MDT05 (sampled at 25 m) · display only':'Coarse 500 m domain-height display'})});terrain.current=terrainLayer;
    const instance=new OLMap({target:container.current,view,interactions:interactions({altShiftDragRotate:false,pinchRotate:false}),controls:controls({rotate:false,attributionOptions:{collapsible:false}}).extend([new ScaleLine()]),layers:[contextLayer,aerialLayer,terrainLayer,new VectorLayer({source,style:(entry,resolution)=>featureStyle(entry as Feature,(map.current?.getSize()?.[0]??1000)<450,resolution)})]});
    map.current=instance;
    view.fit(transformExtent(spainExtent,'EPSG:4326',mapProjection,16),{padding:[25,25,25,25]});
    limitCountryZoom(view,instance.getSize()??[800,600]);
    const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const introTimer=window.setTimeout(()=>view.fit(transformExtent(metadata.extent,metadata.crs,mapProjection),{padding:[35,30,35,30],duration:reducedMotion?0:900}),1800);
    const cancel=()=>{window.clearTimeout(introTimer);view.cancelAnimations();};cancelIntro.current=cancel;
    const element=container.current;
    element.addEventListener('pointerdown',cancel,{passive:true});element.addEventListener('wheel',cancel,{passive:true});
    instance.on('pointermove',event=>{
      if(event.dragging)return;
      const native=transform(event.coordinate,mapProjection,'EPSG:25830'),observation=current.current;
      const cell=cellAt(observation.geography,native[0],native[1]);
      if(!cell){setHover('Outside the supported simulation domain. No elevation or movement is inferred.');return;}
      const [column,row]=cell,height=observation.landscape.elevations_metres[row][column],fuel=observation.landscape.fuel_classes[row][column];
      setHover(`Cell ${column}, ${row} · mean height ${height.toFixed(1)} m · fuel ${fuel||'unknown (synthetic fallback)'}`);
    });
    instance.on('singleclick',event=>{instance.forEachFeatureAtPixel(event.pixel,entry=>{const selection=entry.get('selection') as Selection|undefined;if(selection){select.current(selection);return true;}return undefined;},{hitTolerance:5});});
    const resize=new ResizeObserver(()=>{instance.updateSize();limitCountryZoom(view,instance.getSize()??[800,600]);source.changed();});resize.observe(container.current);
    return()=>{cancel();element.removeEventListener('pointerdown',cancel);element.removeEventListener('wheel',cancel);resize.disconnect();instance.setTarget(undefined);map.current=null;};
  },[metadata]);
  useEffect(()=>{
    const base=context.current;if(!base)return;
    base.setVisible(showMap);
    if(showMap){setContextStatus('loading');base.getSource()?.refresh();}
    aerial.current?.setVisible(showMap&&baseStyle==='aerial');
    if(showMap&&baseStyle==='aerial'){setAerialStatus('loading');aerial.current?.getSource()?.refresh();}
  },[showMap,baseStyle]);
  useEffect(()=>{terrain.current?.setOpacity(terrainOpacity(showMap,baseStyle));terrain.current?.setVisible(showTerrain||!showMap);},[showTerrain,showMap,baseStyle]);
  useEffect(()=>{
    if(previousFocus.current===focusKey)return;
    previousFocus.current=focusKey;cancelIntro.current();
    map.current?.getView().fit(transformExtent(metadata.extent,metadata.crs,mapProjection),{padding:[35,30,35,30]});
  },[focusKey,metadata]);
  useEffect(()=>{
    const source=overlay.current;if(!source)return;source.clear();
    const geography=observation.geography;
    const nativeCorner=(column:number,row:number):Position=>[geography.transform[2]+column*500,geography.transform[5]-row*500];
    const rectangle=(column:number,row:number,width=1,height=1)=>new Polygon([[nativeCorner(column,row),nativeCorner(column+width,row),nativeCorner(column+width,row+height),nativeCorner(column,row+height),nativeCorner(column,row)].map(project)]);
    function marker(kind:string,position:Position,label:string,selection?:Selection){const feature=new Feature({geometry:new Point(project(cellCenter(geography,...position))),kind,label,selection});source!.addFeature(feature);}
    source.addFeature(new Feature({geometry:rectangle(0,0,geography.width,geography.height),kind:'domain'}));
    source.addFeature(new Feature({geometry:new Point(project([(metadata.extent[0]+metadata.extent[2])/2,(metadata.extent[1]+metadata.extent[3])/2])),kind:'locator'}));
    source.addFeature(new Feature({geometry:rectangle(2,2,geography.width-4,geography.height-4),kind:'guard'}));
    if(grid){
      for(let column=0;column<=geography.width;column++)source.addFeature(new Feature({geometry:new LineString([project(nativeCorner(column,0)),project(nativeCorner(column,geography.height))]),kind:'grid'}));
      for(let row=0;row<=geography.height;row++)source.addFeature(new Feature({geometry:new LineString([project(nativeCorner(0,row)),project(nativeCorner(geography.width,row))]),kind:'grid'}));
    }
    const cells=fireCells(observation,world);
    for(const cell of cells)source.addFeature(new Feature({geometry:rectangle(cell.column,cell.row),...cell}));
    const boundary=fireBoundary(cells);
    if(boundary.length)source.addFeature(new Feature({geometry:new MultiLineString(boundary.map(edge=>edge.map(([column,row])=>project(nativeCorner(column,row))))),kind:'front'}));
    const grouped=new Set<string>();
    for(const report of observation.reports){
      if(!report.location)continue;
      const key=report.location.join(','),fresh=observation.sensor_memory.some(sample=>sample.x===report.location![0]&&sample.y===report.location![1]&&sample.observed_at>=report.observed_at&&observation.minute-sample.observed_at<=4);
      if(grouped.has(key)||fresh)continue;grouped.add(key);
      marker('claim',report.location,report.id===selectedReport?'Selected report':'?',{type:'report',id:report.id});
    }
    for(const district of observation.districts){marker('refuge',district.refuge,'Refuge');marker(district.exposed?'exposed':'population',district.position,`${groupName(district.id).replace(' group','')} · ${district.population}`,{type:'district',id:district.id});}
    for(const unit of observation.resources)if(['on_scene','warning_service'].includes(unit.state)&&unit.target)marker('unit',unit.target,`${unit.kind} · ${unit.home_base}`,{type:'resource',id:unit.id});
  },[observation,world,grid,selectedReport]);
  useEffect(()=>{const report=observation.reports.find(report=>report.id===selectedReport);if(report?.location){cancelIntro.current();map.current?.getView().setCenter(project(cellCenter(observation.geography,...report.location)));}},[selectedReport]);
  return <section className="map-panel" aria-label="2D terrain map">
    <div className="map-options"><strong>Incident map <span className="tag">2D · grid north up</span></strong><div><label><input type="checkbox" checked={grid} onChange={event=>setGrid(event.target.checked)}/> Grid</label><label><input type="checkbox" checked={showMap} onChange={event=>setShowMap(event.target.checked)}/> Basemap</label><select aria-label="Map style" value={baseStyle} disabled={!showMap} onChange={event=>setBaseStyle(event.target.value as 'street'|'aerial')}><option value="aerial">Aerial imagery</option><option value="street">Street map</option></select><label><input type="checkbox" checked={showTerrain} onChange={event=>setShowTerrain(event.target.checked)}/> Terrain</label><button title="Mainland Spain and Balearic Islands" onClick={()=>{cancelIntro.current();map.current?.getView().fit(transformExtent(spainExtent,'EPSG:4326',mapProjection,16),{padding:[25,25,25,25]});}}>Spain</button><button onClick={()=>{cancelIntro.current();map.current?.getView().fit(transformExtent(metadata.extent,metadata.crs,mapProjection),{padding:[35,30,35,30]});}}>Fit domain</button></div></div>
    <div ref={container} className="map" role="region" aria-label="Flat grid-aligned wildfire map" tabIndex={0}/>
    {showMap&&baseStyle==='aerial'&&<p className="imagery-note">{aerialStatus==='unavailable'?'Aerial imagery unavailable; showing the street-map fallback. Toggle Basemap to retry.':'PNOA aerial / Sentinel-2 background · not live. Fire overlays are simulated on 500 m cells.'}</p>}
    {showMap&&contextStatus==='unavailable'&&(baseStyle==='street'||aerialStatus==='unavailable')&&<p className="map-key-warning" role="status">Street map unavailable. Toggle Basemap to retry; turn it off for the local terrain view.</p>}
    <MapLegend observation={observation} world={world}/>
    <details className="map-data-details"><summary>Map data and technical details</summary><p>{hover}</p><p>{metadata.source_label}</p><p>500 m simulation cells. Outside the boundary is map context only. Displayed roads do not determine simulated travel.</p></details>
  </section>;
}
