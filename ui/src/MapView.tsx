import {useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import OLMap from 'ol/Map.js';
import View from 'ol/View.js';
import Feature from 'ol/Feature.js';
import Point from 'ol/geom/Point.js';
import Polygon from 'ol/geom/Polygon.js';
import LineString from 'ol/geom/LineString.js';
import type Geometry from 'ol/geom/Geometry.js';
import VectorSource from 'ol/source/Vector.js';
import OSM from 'ol/source/OSM.js';
import XYZ from 'ol/source/XYZ.js';
import ImageStatic from 'ol/source/ImageStatic.js';
import VectorLayer from 'ol/layer/Vector.js';
import TileLayer from 'ol/layer/Tile.js';
import ImageLayer from 'ol/layer/Image.js';
import {transform,transformExtent} from 'ol/proj.js';
import {defaults as interactions} from 'ol/interaction/defaults.js';
import {defaults as controls} from 'ol/control/defaults.js';
import ScaleLine from 'ol/control/ScaleLine.js';
import 'ol/ol.css';
import {baseName,cellAt,cellCenter,groupName,resourceKindName} from './model';
import {fireCells,mapViewOptions,spainExtent,mapProjection,limitCountryZoom,aerialTileUrl,localTerrainVisible,mapMetadataMatches,publicActivityExtent} from './mapModel';
import MapLegend from './MapLegend';
import {featureStyle} from './mapStyle';
import useMapEffects from './useMapEffects';
import {smokeDirection} from './visualEffects';
import type {MapMetadata,Observation,Position,World} from './types';

const project=(point:Position)=>transform(point,'EPSG:25830',mapProjection);
type Selection={type:'report'|'district'|'resource';id:string};

export default function MapView({metadata,observation,world,selectedReport,onSelect,focusKey,replay,animating=false}:{metadata:MapMetadata;observation:Observation;world:World|null;selectedReport:string;onSelect:(selection:Selection)=>void;focusKey:string;replay?:ReactNode;animating?:boolean}) {
  const container=useRef<HTMLDivElement>(null),map=useRef<OLMap|null>(null),overlay=useRef<VectorSource<Feature<Geometry>>|null>(null),context=useRef<TileLayer<OSM>|null>(null),terrain=useRef<ImageLayer<ImageStatic>|null>(null);
  const cancelIntro=useRef(()=>{});
  const aerial=useRef<TileLayer<XYZ>|null>(null);
  const previousFocus=useRef(focusKey);
  const current=useRef(observation),select=useRef(onSelect);
  current.current=observation;select.current=onSelect;
  const [grid,setGrid]=useState(false),[contextStatus,setContextStatus]=useState('loading'),[showMap,setShowMap]=useState(true),[hover,setHover]=useState('Select a report or point at a cell to inspect its mean elevation.');
  const [contrast,setContrast]=useState(true);
  const [tactical,setTactical]=useState(true);
  const [instance,setInstance]=useState<OLMap|null>(null),[effects,setEffects]=useState(true),[relief,setRelief]=useState(true),[reliefFailed,setReliefFailed]=useState(false);
  const reliefLayer=useRef<ImageLayer<ImageStatic>|null>(null);
  const tacticalMode=useRef(tactical);tacticalMode.current=tactical;
  const aligned=mapMetadataMatches(metadata,observation.geography);
  const windDirection=smokeDirection(observation.wind);
  useMapEffects(instance,observation,aligned&&effects&&!world,animating);
  const [baseStyle,setBaseStyle]=useState<'street'|'aerial'>('aerial'),[aerialStatus,setAerialStatus]=useState('loading');
  const activityPadding=()=>[55,35,50,Math.min(190,(map.current?.getSize()?.[0]??800)*.3)];
  const fitActivity=()=>map.current?.getView().fit(publicActivityExtent(current.current),{padding:activityPadding(),maxZoom:16});
  useEffect(()=>{
    if(!container.current||!aligned)return;
    const source=new VectorSource<Feature<Geometry>>();overlay.current=source;
    const contextSource=new OSM({crossOrigin:'anonymous',maxZoom:19});
    const contextLayer=new TileLayer({source:contextSource,className:'national-context'});context.current=contextLayer;
    contextSource.on('tileloadend',()=>setContextStatus(previous=>previous==='unavailable'?previous:'ready'));
    contextSource.on('tileloaderror',()=>{setContextStatus('unavailable');contextLayer.setVisible(false);});
    const aerialSource=new XYZ({url:aerialTileUrl,crossOrigin:'anonymous',maxZoom:19,attributions:'Imagery © IGN · PNOA / Sentinel-2 · not live'});
    const aerialLayer=new TileLayer({source:aerialSource,className:'aerial-context'});aerial.current=aerialLayer;
    aerialSource.on('tileloadend',()=>setAerialStatus(previous=>previous==='unavailable'?previous:'ready'));
    aerialSource.on('tileloaderror',()=>{setAerialStatus('unavailable');aerialLayer.setVisible(false);});
    const view=new View({...mapViewOptions,center:project([(metadata.extent[0]+metadata.extent[2])/2,(metadata.extent[1]+metadata.extent[3])/2]),zoom:12});
    const terrainLayer=new ImageLayer({className:'terrain-relief',visible:false,source:new ImageStatic({url:metadata.image_url,projection:metadata.crs,imageExtent:metadata.extent,attributions:metadata.source_label.startsWith('IGN')?'Terrain © IGN · MDT05 (sampled at 25 m) · display only':'Coarse 500 m domain-height display'})});terrain.current=terrainLayer;
    const reliefSource=new ImageStatic({url:'/api/map-relief.png',projection:metadata.crs,imageExtent:metadata.extent});
    const shade=new ImageLayer({source:reliefSource,className:'real-terrain-shade',visible:true});reliefLayer.current=shade;
    setReliefFailed(false);reliefSource.on('imageloaderror',()=>{setReliefFailed(true);shade.setVisible(false);});
    const instance=new OLMap({target:container.current,view,interactions:interactions({altShiftDragRotate:false,pinchRotate:false}),controls:controls({rotate:false,attributionOptions:{collapsible:false}}).extend([new ScaleLine()]),layers:[contextLayer,aerialLayer,terrainLayer,shade,new VectorLayer({source,declutter:true,style:(entry,resolution)=>featureStyle(entry as Feature,(map.current?.getSize()?.[0]??1000)<450,resolution,tacticalMode.current)})]});
    map.current=instance;setInstance(instance);
    view.fit(transformExtent(spainExtent,'EPSG:4326',mapProjection,16),{padding:[25,25,25,25]});
    limitCountryZoom(view,instance.getSize()??[800,600]);
    const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const introTimer=window.setTimeout(()=>view.fit(publicActivityExtent(current.current),{padding:activityPadding(),duration:reducedMotion?0:900,maxZoom:16}),1800);
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
  },[metadata,aligned]);
  useEffect(()=>{
    const base=context.current;if(!base)return;
    base.setVisible(showMap);
    if(showMap){setContextStatus('loading');base.getSource()?.refresh();}
    aerial.current?.setVisible(showMap&&baseStyle==='aerial');
    if(showMap&&baseStyle==='aerial'){setAerialStatus('loading');aerial.current?.getSource()?.refresh();}
  },[showMap,baseStyle]);
  useEffect(()=>{terrain.current?.setVisible(localTerrainVisible(showMap));},[showMap]);
  useEffect(()=>{reliefLayer.current?.setVisible(relief&&!reliefFailed);},[relief,reliefFailed,metadata]);
  useEffect(()=>{overlay.current?.changed();},[tactical]);
  useEffect(()=>{
    if(previousFocus.current===focusKey)return;
    previousFocus.current=focusKey;cancelIntro.current();
    fitActivity();
  },[focusKey,metadata]);
  useEffect(()=>{
    const source=overlay.current;if(!source)return;source.clear();if(!aligned)return;
    const geography=observation.geography;
    const nativeCorner=(column:number,row:number):Position=>[geography.transform[2]+column*500,geography.transform[5]-row*500];
    const rectangle=(column:number,row:number,width=1,height=1)=>new Polygon([[nativeCorner(column,row),nativeCorner(column+width,row),nativeCorner(column+width,row+height),nativeCorner(column,row+height),nativeCorner(column,row)].map(project)]);
    function marker(kind:string,position:Position,label:string,selection?:Selection,labelOffset?:number){const feature=new Feature({geometry:new Point(project(cellCenter(geography,...position))),kind,label,selection,labelOffset});source!.addFeature(feature);}
    source.addFeature(new Feature({geometry:new Point(project([(metadata.extent[0]+metadata.extent[2])/2,(metadata.extent[1]+metadata.extent[3])/2])),kind:'locator'}));
    if(grid){
      for(let column=0;column<=geography.width;column++)source.addFeature(new Feature({geometry:new LineString([project(nativeCorner(column,0)),project(nativeCorner(column,geography.height))]),kind:'grid'}));
      for(let row=0;row<=geography.height;row++)source.addFeature(new Feature({geometry:new LineString([project(nativeCorner(0,row)),project(nativeCorner(geography.width,row))]),kind:'grid'}));
    }
    const cells=fireCells(observation,world);
    for(const cell of cells){
      const geometry=cell.kind==='burned'?rectangle(cell.column,cell.row):new Point(project(cellCenter(geography,cell.column,cell.row)));
      source.addFeature(new Feature({geometry,...cell}));
    }
    const grouped=new Set<string>();
    for(const report of observation.reports){
      if(!report.location)continue;
      const key=report.location.join(','),fresh=observation.sensor_memory.some(sample=>sample.x===report.location![0]&&sample.y===report.location![1]&&sample.observed_at>=report.observed_at&&observation.minute-sample.observed_at<=4);
      if(grouped.has(key)||fresh)continue;grouped.add(key);
      marker('claim',report.location,report.id===selectedReport?'Selected report':'',{type:'report',id:report.id});
    }
    for(const [index,district] of observation.districts.entries()){marker('refuge',district.refuge,'Refuge');marker(district.exposed?'exposed':'population',district.position??district.home,`${groupName(district.id)} · ${district.population}${district.position===null?' · home; position unknown':''}`,{type:'district',id:district.id},index%2?-55:-25);}
    const arrivals=new Map<string,typeof observation.resources>();
    const responders=new Map<string,typeof observation.resources>();
    for(const unit of observation.resources){
      if(unit.state==='on_scene'&&unit.target){
        const key=unit.target.join(',');responders.set(key,[...(responders.get(key)??[]),unit]);
      }
      if(['mobilizing','en_route'].includes(unit.state??'')&&unit.target){
        const key=unit.target.join(',');arrivals.set(key,[...(arrivals.get(key)??[]),unit]);
      }
    }
    for(const units of responders.values()){
      const position=units[0].target!;
      const label=units.length===1?`${resourceKindName(units[0].kind)} · ${baseName(units[0].home_base)}`:`${units.length} responders`;
      const feature=new Feature({geometry:new Point(project(cellCenter(geography,...position))),kind:'unit',label,resourceKind:units[0].kind,selection:{type:'resource',id:units[0].id}});source.addFeature(feature);
    }
    for(const units of arrivals.values()){
      const remaining=units.map(unit=>unit.arrival_at===null||unit.arrival_at===undefined?null:Math.max(0,unit.arrival_at-observation.minute)).filter((value):value is number=>value!==null);
      const eta=remaining.length?Math.min(...remaining):null;
      const label=eta===null?'En-route target':`${units.length>1?`${units.length} units`:resourceKindName(units[0].kind)} · ETA ${eta} minutes`;
      source.addFeature(new Feature({geometry:new Point(project(cellCenter(geography,...units[0].target!))),kind:'arrival-target',label,selection:{type:'resource',id:units[0].id}}));
    }
  },[observation,world,grid,selectedReport,aligned]);
  useEffect(()=>{const report=observation.reports.find(report=>report.id===selectedReport);if(report?.location){cancelIntro.current();map.current?.getView().setCenter(project(cellCenter(observation.geography,...report.location)));}},[selectedReport]);
  return <section className="map-panel" aria-label="2D terrain map">
    <div className="map-options"><strong>Incident map <span className="tag">2D · grid north up</span></strong><div><label><input type="checkbox" checked={grid} onChange={event=>setGrid(event.target.checked)}/> Grid</label><label><input type="checkbox" checked={showMap} onChange={event=>setShowMap(event.target.checked)}/> Basemap</label><select aria-label="Map style" value={baseStyle} disabled={!showMap} onChange={event=>setBaseStyle(event.target.value as 'street'|'aerial')}><option value="aerial">Aerial imagery</option><option value="street">Street map</option></select><button onClick={()=>{cancelIntro.current();fitActivity();}}>Fit activity</button><button title="Mainland Spain and Balearic Islands" onClick={()=>{cancelIntro.current();map.current?.getView().fit(transformExtent(spainExtent,'EPSG:4326',mapProjection,16),{padding:[25,25,25,25]});}}>Spain</button><button onClick={()=>{cancelIntro.current();map.current?.getView().fit(transformExtent(metadata.extent,metadata.crs,mapProjection),{padding:[35,30,35,30]});}}>Fit domain</button></div></div>
    {!aligned&&<p className="map-key-warning" role="alert">Map extent or projection does not match this episode. Geographic overlays are withheld rather than placed on the wrong map.</p>}
    <div className="map-surface">
      <div ref={container} className={`map ${tactical?'tactical-map':''} ${contrast?'enhanced-terrain':''}`} role="region" aria-label="Flat grid-aligned wildfire map" tabIndex={0}/>
      <span className="map-north" title="Native EPSG:25830 grid north; not true north">↑<small>Grid N</small></span>
      {windDirection&&<span className="map-wind" title="Delivered model wind direction, not measured fire-front wind or a speed scale.">Model wind <b aria-hidden="true" style={{transform:`rotate(${Math.atan2(-windDirection[1],windDirection[0])*180/Math.PI}deg)`}}>→</b></span>}
      <div className="map-display-controls">
        <label><input type="checkbox" checked={contrast} onChange={event=>setContrast(event.target.checked)}/> Enhanced contrast</label>
        <label title="Enlarged illustrative symbols only. Distances, coordinates, travel and weather are unchanged."><input type="checkbox" checked={tactical} onChange={event=>setTactical(event.target.checked)}/> Game symbols</label>
        <label title="Shadows from validated elevations, with display-only lighting exaggeration. No terrain heights change."><input type="checkbox" checked={relief} disabled={reliefFailed} onChange={event=>setRelief(event.target.checked)}/> Terrain shading</label>
        <label title="Decorative flames and smoke from fresh delivered fire sightings and known wind. Not a fire perimeter or smoke forecast. Motion follows playback."><input type="checkbox" checked={effects} onChange={event=>setEffects(event.target.checked)}/> Fire effects</label>
      </div>
      <span className="effects-caption">{effects&&!world?'Illustrative fire / smoke · ':''}500 metre simulation cells</span>
    </div>
    {replay&&<div className="map-replay-footer">{replay}</div>}
    {reliefFailed&&<p className="imagery-note" role="status">Terrain shading unavailable. Original basemap and simulation are unchanged.</p>}
    {showMap&&baseStyle==='aerial'&&aerialStatus==='unavailable'&&<p className="imagery-note" role="status">Aerial imagery unavailable; showing the street-map fallback. Toggle Basemap to retry.</p>}
    {showMap&&contextStatus==='unavailable'&&(baseStyle==='street'||aerialStatus==='unavailable')&&<p className="map-key-warning" role="status">Street map unavailable. Toggle Basemap to retry; turn it off for the local terrain view.</p>}
    <MapLegend observation={observation} world={world}/>
    <details className="map-data-details"><summary>Map data and technical details</summary><p>{hover}</p><p>{metadata.source_label}</p><p>Original georeferenced IGN imagery and OSM streets; contrast changes colors, never roads, coordinates or terrain heights. Exercise population groups retain the scenario positions, not verified historical town locations. Source dates vary; imagery is not a reconstructed 2021 map.</p><p>500 m simulation cells. Only the zoomed study area is simulated; imagery beyond it is context. Displayed roads do not determine simulated travel. Labels may be hidden to avoid overlap; map symbols stay at their original coordinates. Decorative fire effects are enlarged in screen pixels, like the symbols; they do not show the fire extent.</p></details>
  </section>;
}
