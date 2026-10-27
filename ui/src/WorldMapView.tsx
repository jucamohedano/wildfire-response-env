import {lazy,Suspense,useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import MapLegend from './MapLegend';
import WorldSceneBoundary from './WorldSceneBoundary';
import {captureTerrainTexture} from './captureTerrainTexture';
import {mapMetadataMatches} from './mapModel';
import {validateMesh} from './terrainSceneModel';
import type {MapTexture,TerrainMeshData} from './terrainSceneModel';
import type {MapMetadata,Observation,World} from './types';

const TerrainScene=lazy(()=>import('./TerrainScene'));
const MESH_SHA256='b1ab88dc315e95adc0654f7133e2dcc618dd7878af8b18964dea9fbbe52f377f';
type Selection={type:'report'|'district'|'resource';id:string};
type Scene={mesh:TerrainMeshData;texture:MapTexture};

async function loadExpandedMesh(extent:number[],signal:AbortSignal):Promise<TerrainMeshData>{
  const response=await fetch('/assets/map-mesh-expanded-v2.json',{signal});
  if(!response.ok)throw new Error('Expanded IGN terrain is unavailable.');
  const buffer=await response.arrayBuffer();
  if(buffer.byteLength>5000000||!globalThis.crypto?.subtle)throw new Error('Expanded terrain cannot be verified in this browser.');
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),value=>value.toString(16).padStart(2,'0')).join('');
  if(hash!==MESH_SHA256)throw new Error('Expanded terrain failed its source integrity check.');
  const mesh=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(buffer)) as TerrainMeshData;
  validateMesh(mesh,extent);return mesh;
}

export default function WorldMapView({metadata,observation,world,onSelect,replay,animating=false}:
  {metadata:MapMetadata;observation:Observation;world:World|null;selectedReport:string;onSelect:(selection:Selection)=>void;focusKey:string;replay?:ReactNode;animating?:boolean;animationDuration?:number}){
  const [scene,setScene]=useState<Scene|null>(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
  const cache=useRef<{key:string;scene:Scene}|null>(null);
  const aligned=mapMetadataMatches(metadata,observation.geography);
  const key=JSON.stringify([metadata.extent,metadata.crs]);
  useEffect(()=>{
    const controller=new AbortController();setError('');
    if(!aligned){setScene(null);setError('This episode does not match the source map. Geographic overlays are withheld.');return()=>controller.abort();}
    if(cache.current?.key===key){setScene(cache.current.scene);return()=>controller.abort();}
    setScene(null);
    void (async()=>{
      const mesh=await loadExpandedMesh(metadata.extent,controller.signal);
      const texture=await captureTerrainTexture(mesh.extent,'aerial',controller.signal,mesh);
      if(controller.signal.aborted)return;
      const next={mesh,texture};cache.current={key,scene:next};setScene(next);
    })().catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'3D preparation failed.');});
    return()=>controller.abort();
  },[aligned,key,attempt]);
  const retry=()=>{cache.current=null;setScene(null);setAttempt(value=>value+1);};
  return <section className="map-panel" aria-label="3D terrain map">
    <div className="map-surface scene-open">
      {error?<div className="terrain-scene-error" role="alert">{error}<button onClick={retry}>Retry 3D</button></div>
        :!scene?<div className="scene-loading" role="status">Preparing expanded terrain and registered imagery…</div>
        :<WorldSceneBoundary key={attempt} onRetry={retry}><Suspense fallback={<div className="scene-loading" role="status">Loading the 3D renderer…</div>}>
          <TerrainScene mesh={scene.mesh} texture={scene.texture} observation={observation} world={world} animating={animating} onClose={retry} onSelect={onSelect}/>
        </Suspense></WorldSceneBoundary>}
    </div>
    {replay&&<div className="map-replay-footer">{replay}</div>}
    <MapLegend observation={observation} world={world} tacticalWorld/>
    <details className="map-data-details"><summary>Map data and technical details</summary>
      <p>IGN terrain and imagery with © OpenStreetMap contributors. The 32 × 32 kilometre display covers approximately 3.08 times the original area. Source imagery is not live and dates may differ.</p>
      <p>Outside the original 500 metre-cell simulation domain, the landscape is visual context only. Nonpositive or uncertain elevations are masked; flat imagery underneath is not a measured height. The expanded WCS samples differ from the simulator's pinned terrain and do not change its physics.</p>
      <p>OSM roads and town-name points are illustrative cartography and do not establish road access; the cached road overlay does not cover the full northern edge. Exercise population and refuge markers are synthetic, not verified historical locations.</p>
    </details>
  </section>;
}
