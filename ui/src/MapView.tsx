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
import XYZ from 'ol/source/XYZ.js';
import ImageStatic from 'ol/source/ImageStatic.js';
import VectorLayer from 'ol/layer/Vector.js';
import TileLayer from 'ol/layer/Tile.js';
import ImageLayer from 'ol/layer/Image.js';
import {Style,Fill,Stroke,Circle as CircleStyle,Text} from 'ol/style.js';
import {transform,transformExtent} from 'ol/proj.js';
import {register} from 'ol/proj/proj4.js';
import {defaults as interactions} from 'ol/interaction/defaults.js';
import {defaults as controls} from 'ol/control/defaults.js';
import ScaleLine from 'ol/control/ScaleLine.js';
import proj4 from 'proj4';
import 'ol/ol.css';
import {cellAt,cellCenter,groupName} from './model';
import {fireCells,fireBoundary,mapViewOptions,spainExtent,ignContextUrl} from './mapModel';
import type {MapMetadata,Observation,Position,World} from './types';

proj4.defs('EPSG:25830','+proj=utm +zone=30 +ellps=GRS80 +units=m +no_defs');
register(proj4);
const project=(point:Position)=>transform(point,'EPSG:25830','EPSG:3857');
type Selection={type:'report'|'district'|'resource';id:string};

function featureStyle(feature:Feature,narrow=false,resolution=0):Style|undefined {
  const kind=feature.get('kind') as string;
  if(resolution>300&&!['domain','locator'].includes(kind))return undefined;
  if(kind==='locator')return resolution>300?new Style({image:new CircleStyle({radius:6,fill:new Fill({color:'#a72a1c'}),stroke:new Stroke({color:'#fff',width:2})}),text:new Text({text:'Simulation domain',offsetY:-18,font:'600 12px system-ui',fill:new Fill({color:'#172d22'}),stroke:new Stroke({color:'#fff',width:3})}),zIndex:10}):undefined;
  const color=kind==='fire'?'#e8772e':kind==='stale'?'#947257':kind==='claim'?'#b87b17':kind==='unit'?'#255e46':kind==='exposed'?'#bb3d36':'#245c83';
  if (kind==='guard'||kind==='grid'||kind==='domain') return new Style({stroke:new Stroke({color:kind==='domain'?'#213f2a':kind==='guard'?'#3d5142':'rgba(28,51,36,.35)',width:kind==='grid'?.8:1.8,lineDash:kind==='guard'?[5,5]:undefined})});
  if(kind==='front')return new Style({stroke:new Stroke({color:'#8a2118',width:2.5}),zIndex:3});
  if(kind==='burned')return new Style({fill:new Fill({color:`rgba(48,34,31,${.28+.52*(feature.get('burned') as number)})`}),zIndex:1});
  if(kind==='fire'){
    const heat=feature.get('heat') as number|null;
    return new Style({fill:new Fill({color:heat===null?'rgba(246,125,25,.8)':`rgba(255,${Math.round(65+170*Math.min(1,Math.max(0,heat)))},25,.88)`}),zIndex:2});
  }
  if(kind==='stale')return new Style({fill:new Fill({color:'rgba(104,83,71,.24)'}),stroke:new Stroke({color:'#675347',width:1.5,lineDash:[4,3]}),zIndex:1});
  const label=feature.get('label') as string;
  return new Style({image:new CircleStyle({radius:kind==='claim'?11:kind==='refuge'?4:7,fill:new Fill({color:kind==='claim'?'rgba(248,219,154,.2)':color}),stroke:new Stroke({color:kind==='claim'?color:'#fff',width:kind==='claim'?1.8:1.7,lineDash:kind==='claim'?[4,3]:undefined})}),text:new Text({text:narrow&&(kind==='population'||kind==='exposed')?label.split(' · ')[0]:label,offsetY:kind==='claim'?-21:-18,font:'600 12px system-ui',fill:new Fill({color:'#193428'}),stroke:new Stroke({color:'rgba(255,255,255,.95)',width:3})}),zIndex:5});
}

export default function MapView({metadata,observation,world,selectedReport,onSelect}:{metadata:MapMetadata;observation:Observation;world:World|null;selectedReport:string;onSelect:(selection:Selection)=>void}) {
  const container=useRef<HTMLDivElement>(null),map=useRef<OLMap|null>(null),overlay=useRef<VectorSource<Feature<Geometry>>|null>(null),labels=useRef<TileLayer<XYZ>|null>(null),context=useRef<TileLayer<XYZ>|null>(null);
  const current=useRef(observation),select=useRef(onSelect);
  current.current=observation;select.current=onSelect;
  const [grid,setGrid]=useState(false),[labelStatus,setLabelStatus]=useState('loading'),[contextStatus,setContextStatus]=useState('loading'),[showLabels,setShowLabels]=useState(true),[hover,setHover]=useState('Select a report or point at a cell to inspect its mean elevation.');
  const displayedFire=fireCells(observation,world);
  useEffect(()=>{
    if(!container.current)return;
    const source=new VectorSource<Feature<Geometry>>();overlay.current=source;
    const labelSource=new XYZ({url:'https://tms-ign-base.idee.es/1.0.0/IGNBaseSimplificado/{z}/{x}/{-y}.png',crossOrigin:'anonymous',maxZoom:18,attributions:'Labels © IGN · CC BY 4.0 · current cartography, not routing'});
    const labelLayer=new TileLayer({source:labelSource,minZoom:8,extent:transformExtent(metadata.extent,metadata.crs,'EPSG:3857')});labels.current=labelLayer;
    const contextSource=new XYZ({url:ignContextUrl,crossOrigin:'anonymous',maxZoom:17,attributions:'Map © IGN · CC BY 4.0 · current cartography, display only'});
    const contextLayer=new TileLayer({source:contextSource,className:'national-context'});context.current=contextLayer;
    contextSource.on('tileloadend',()=>setContextStatus(previous=>previous==='unavailable'?previous:'ready'));
    contextSource.on('tileloaderror',()=>{setContextStatus('unavailable');contextLayer.setVisible(false);});
    labelSource.on('tileloadend',()=>setLabelStatus(previous=>previous==='unavailable'?previous:'ready'));
    labelSource.on('tileloaderror',()=>{setLabelStatus('unavailable');labelLayer.setVisible(false);});
    const view=new View({...mapViewOptions,center:project([(metadata.extent[0]+metadata.extent[2])/2,(metadata.extent[1]+metadata.extent[3])/2]),zoom:12});
    const instance=new OLMap({target:container.current,view,interactions:interactions({altShiftDragRotate:false,pinchRotate:false}),controls:controls({rotate:false}).extend([new ScaleLine()]),layers:[contextLayer,new ImageLayer({className:'terrain-relief',source:new ImageStatic({url:metadata.image_url,projection:metadata.crs,imageExtent:metadata.extent,attributions:metadata.source_label.startsWith('IGN')?'Terrain © IGN · MDT05 (sampled at 25 m) · display only':'Coarse 500 m domain-height display'})}),labelLayer,new VectorLayer({source,style:(entry,resolution)=>featureStyle(entry as Feature,(map.current?.getSize()?.[0]??1000)<450,resolution)})]});
    map.current=instance;
    view.fit(transformExtent(metadata.extent,metadata.crs,'EPSG:3857'),{padding:[35,30,35,30]});
    instance.on('pointermove',event=>{
      if(event.dragging)return;
      const native=transform(event.coordinate,'EPSG:3857','EPSG:25830'),observation=current.current;
      const cell=cellAt(observation.geography,native[0],native[1]);
      if(!cell){setHover('Outside the supported simulation domain. No elevation or movement is inferred.');return;}
      const [column,row]=cell,height=observation.landscape.elevations_metres[row][column],fuel=observation.landscape.fuel_classes[row][column];
      setHover(`Cell ${column}, ${row} · mean height ${height.toFixed(1)} m · fuel ${fuel||'unknown (synthetic fallback)'}`);
    });
    instance.on('singleclick',event=>{instance.forEachFeatureAtPixel(event.pixel,entry=>{const selection=entry.get('selection') as Selection|undefined;if(selection){select.current(selection);return true;}return undefined;},{hitTolerance:5});});
    const resize=new ResizeObserver(()=>{instance.updateSize();source.changed();});resize.observe(container.current);
    return()=>{resize.disconnect();instance.setTarget(undefined);map.current=null;};
  },[metadata]);
  useEffect(()=>{
    const layer=labels.current,base=context.current;
    if(!layer||!base)return;
    layer.setVisible(showLabels);base.setVisible(showLabels);
    if(showLabels){setLabelStatus('loading');setContextStatus('loading');layer.getSource()?.refresh();base.getSource()?.refresh();}
  },[showLabels]);
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
  useEffect(()=>{const report=observation.reports.find(report=>report.id===selectedReport);if(report?.location)map.current?.getView().setCenter(project(cellCenter(observation.geography,...report.location)));},[selectedReport]);
  return <section className="map-panel" aria-label="2D terrain map">
    <div className="map-options"><strong>Terrain map <span className="tag">2D · north up</span></strong><div><label><input type="checkbox" checked={grid} onChange={event=>setGrid(event.target.checked)}/> Grid</label><label><input type="checkbox" checked={showLabels} onChange={event=>setShowLabels(event.target.checked)}/> IGN map</label><button onClick={()=>map.current?.getView().fit(transformExtent(spainExtent,'EPSG:4326','EPSG:3857'),{padding:[25,25,25,25]})}>Spain</button><button onClick={()=>map.current?.getView().fit(transformExtent(metadata.extent,metadata.crs,'EPSG:3857'),{padding:[35,30,35,30]})}>Fit domain</button></div></div>
    <div ref={container} className="map" role="region" aria-label="Flat north-up wildfire map" tabIndex={0}/>
    <div className="map-readout">{hover}</div>
    <div className="map-legend"><span><i className="legend-fire"/> {world?'Private heat: red → yellow':'Fresh observed fire'}</span>{world?<span><i className="legend-burned"/> Burned cells</span>:<span><i className="legend-stale"/> Observation older than 4m</span>}<span><i className="legend-claim"/> Unconfirmed location</span><span><i className="legend-population"/> Population / refuge</span></div>
    <div className="fire-summary">{observation.minute} model min · {displayedFire.filter(cell=>cell.kind==='fire').length} {world?'burning':'fresh burning observations'} · {displayedFire.filter(cell=>cell.kind===(world?'burned':'stale')).length} {world?'burned, no active heat':'stale burning observations'}. Outline follows cell edges, not a precise fire perimeter.</div>
    <div className="map-source" title={metadata.source_label}>{metadata.source_label.startsWith('IGN')?'IGN MDT05 · 25 m terrain samples':'Coarse 500 m height display'} · 500 m cells. Solid: domain · dashed: censor guard. Outside: map context only, not simulated. Roads are not routing.{showLabels&&(labelStatus==='unavailable'||contextStatus==='unavailable')?' An IGN layer is unavailable; turn map off/on to retry. Offline terrain and simulation remain available.':''}</div>
  </section>;
}
