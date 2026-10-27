import {useEffect,useRef,useState} from 'react';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {cellCenter,groupName,resourceKindName} from './model';
import {effectCells,particleSeed} from './visualEffects';
import {coastalPlaces,positionOnRoute,routeDisplayLines} from './movement';
import {clipToTerrain,sceneCoordinates,terrainSupported,textureUV,validateMesh,unconfirmedReportLocations} from './terrainSceneModel';
import {markerTextureCanvas,markerStyles,tacticalLabelStyle} from './tacticalMarkersV2';
import type {TacticalMarkerKind} from './tacticalMarkersV2';
import {constrainTacticalFrame,declutterTacticalLabels,fireObservationLabel,orthographicPixelsPerMetre,publicTacticalFrame,
  reportedTacticalPosition,staleTacticalCells,tacticalFireLabels,tacticalSmokePuff,terrainHeightRange,
  MAX_TACTICAL_FIRE_CELLS,TACTICAL_FIRE_PARTICLES,TACTICAL_FIRE_VERTEX_SHADER,
  TACTICAL_PITCH_DEGREES,TACTICAL_SMOKE_PUFFS,tacticalDistrictPosition,tacticalPacketDelivered,tacticalZoomLimits,deliveredTacticalRoutes,activeTacticalTarget,tacticalScale,tacticalNorthRotation} from './tacticalTerrainV2';
import type {LabelBounds,TacticalLabelCandidate} from './tacticalTerrainV2';
import type {MapTexture,TerrainMeshData} from './terrainSceneModel';
import type {Observation,Position,Resource,World} from './types';
import {mapAppearance,validateDisplayRoads} from './mapAppearanceV2';
import type {DisplayRoadMap,MapPalette} from './mapAppearanceV2';

type MapSelection={type:'report'|'district'|'resource';id:string};
interface SceneHandle {update:(observation:Observation,world:World|null)=>void;setPlaying:(playing:boolean)=>void;focus:()=>void;fitArea:()=>void;setAppearance:(palette:MapPalette,showRoads:boolean)=>void;setRoads:(roads:DisplayRoadMap)=>void;dispose:()=>void}
interface SceneTag {id:string;sprite:THREE.Sprite;anchor:THREE.Object3D;leader:THREE.Line;width:number;height:number;priority:number}

function labelCanvas(text:string,color:string):{canvas:HTMLCanvasElement;width:number;height:number}{
  const canvas=document.createElement('canvas'),context=canvas.getContext('2d');
  if(!context)throw new Error('Canvas text rendering is unavailable.');
  context.font=tacticalLabelStyle.font;
  const width=Math.min(tacticalLabelStyle.maxWidthPixels,Math.max(118,Math.ceil(context.measureText(text).width)+22)),height=30;
  canvas.width=width*3;canvas.height=height*3;context.scale(3,3);
  context.fillStyle=tacticalLabelStyle.background;context.strokeStyle=color;context.lineWidth=1;
  context.beginPath();context.roundRect(.5,.5,width-1,height-1,4);context.fill();context.stroke();
  context.fillStyle=tacticalLabelStyle.text;context.font=tacticalLabelStyle.font;
  context.textAlign='center';context.textBaseline='middle';context.fillText(text,width/2,height/2,width-14);
  return {canvas,width,height};
}

function cloudTexture():THREE.CanvasTexture{
  const canvas=document.createElement('canvas');canvas.width=192;canvas.height=192;
  const context=canvas.getContext('2d');if(!context)throw new Error('Smoke texture rendering is unavailable.');
  const body=context.createRadialGradient(96,96,8,96,96,86);
  body.addColorStop(0,'rgba(70,76,71,.86)');body.addColorStop(.48,'rgba(112,119,112,.68)');body.addColorStop(1,'rgba(174,182,172,0)');
  context.fillStyle=body;context.fillRect(0,0,192,192);
  for(let lobe=0;lobe<8;lobe++){
    const angle=lobe*2.4,centreX=96+Math.cos(angle)*39,centreY=96+Math.sin(angle)*39;
    const gradient=context.createRadialGradient(centreX,centreY,0,centreX,centreY,49);
    gradient.addColorStop(0,'rgba(157,165,154,.27)');gradient.addColorStop(.5,'rgba(150,158,149,.17)');gradient.addColorStop(1,'rgba(184,192,182,0)');
    context.fillStyle=gradient;context.fillRect(0,0,192,192);
  }
  const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;return texture;
}

function sceneView(container:HTMLDivElement,mesh:TerrainMeshData,snapshot:MapTexture,initial:Observation,initialWorld:World|null,
  onGraphicsStatus:(message:string)=>void,showEffects:boolean,onSelect:(selection:MapSelection)=>void):SceneHandle{
  validateMesh(mesh,initial.geography.transform.length===6?[initial.geography.transform[2],
    initial.geography.transform[5]-initial.geography.height*500,
    initial.geography.transform[2]+initial.geography.width*500,initial.geography.transform[5]]:[]);
  if(!snapshot.extent.every(Number.isFinite)||snapshot.extent[0]>mesh.extent[0]+.01
    ||snapshot.extent[1]>mesh.extent[1]+.01||snapshot.extent[2]<mesh.extent[2]-.01
    ||snapshot.extent[3]<mesh.extent[3]-.01)throw new Error('The source image does not cover the registered terrain domain.');
  const appearance=mapAppearance(snapshot.canvas,snapshot.extent,mesh);
  let palette:MapPalette='illustrated',showRoadCartography=true,displayRoads:DisplayRoadMap|null=null;
  appearance.draw(palette,null);
  const renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:'high-performance'});
  renderer.setPixelRatio(Math.min(1.5,window.devicePixelRatio));renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.15;
  renderer.domElement.setAttribute('aria-label','Tactical terrain. Left drag pans; right drag orbits; scroll zooms.');
  renderer.domElement.tabIndex=0;renderer.domElement.dataset.texturePixels=String(snapshot.canvas.width);
  renderer.domElement.dataset.projection='orthographic';renderer.domElement.dataset.designVersion='tactical-v2';
  container.append(renderer.domElement);
  const scene=new THREE.Scene();scene.background=new THREE.Color('#10271f');
  const [minimumHeight,maximumHeight]=terrainHeightRange(mesh),referenceHeight=(minimumHeight+maximumHeight)/2;
  const middleEast=(mesh.extent[0]+mesh.extent[2])/2,middleNorth=(mesh.extent[1]+mesh.extent[3])/2;
  const distance=Math.max(mesh.extent[2]-mesh.extent[0],mesh.extent[3]-mesh.extent[1],12000)*1.5;
  let baseHeight=8000,cameraChanging=false;
  const camera=new THREE.OrthographicCamera(-1,1,1,-1,1,100000);
  camera.position.set(0,referenceHeight+distance*Math.sin(Math.PI/3),distance*Math.cos(Math.PI/3));
  const controls=new OrbitControls(camera,renderer.domElement);controls.target.set(0,referenceHeight,0);
  controls.enableDamping=false;controls.screenSpacePanning=false;
  controls.minPolarAngle=Math.PI/9;controls.maxPolarAngle=Math.PI*2/9;
  controls.minAzimuthAngle=-Math.PI/3;controls.maxAzimuthAngle=Math.PI/3;
  controls.mouseButtons.LEFT=THREE.MOUSE.PAN;controls.mouseButtons.RIGHT=THREE.MOUSE.ROTATE;
  controls.touches.ONE=THREE.TOUCH.PAN;controls.touches.TWO=THREE.TOUCH.DOLLY_ROTATE;
  controls.update();
  const sun=new THREE.DirectionalLight('#fff6de',2.25);sun.position.set(-7000,8500,-6500);
  scene.add(sun,new THREE.HemisphereLight('#e2f2ef','#233a29',1));
  const geometry=new THREE.BufferGeometry(),positions=new Float32Array(mesh.columns*mesh.rows*3);
  const uvs=new Float32Array(mesh.columns*mesh.rows*2),indices:number[]=[];
  for(let row=0;row<mesh.rows;row++)for(let column=0;column<mesh.columns;column++){
    const index=row*mesh.columns+column,point:Position=[mesh.eastings[column],mesh.northings[row]];
    positions.set([point[0]-middleEast,mesh.heights_metres[row][column],middleNorth-point[1]],index*3);uvs.set(textureUV(point,snapshot.extent),index*2);
    if(row<mesh.rows-1&&column<mesh.columns-1){
      const right=index+1,bottom=index+mesh.columns;
      if(terrainSupported(mesh,row,column)&&terrainSupported(mesh,row+1,column)&&terrainSupported(mesh,row,column+1))indices.push(index,bottom,right);
      if(terrainSupported(mesh,row,column+1)&&terrainSupported(mesh,row+1,column)&&terrainSupported(mesh,row+1,column+1))indices.push(right,bottom,bottom+1);
    }
  }
  geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));geometry.setAttribute('uv',new THREE.BufferAttribute(uvs,2));
  geometry.setIndex(indices);geometry.computeVertexNormals();
  const mapTexture=new THREE.CanvasTexture(appearance.canvas);mapTexture.colorSpace=THREE.SRGBColorSpace;
  mapTexture.anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());
  const contextGeometry=new THREE.PlaneGeometry(mesh.extent[2]-mesh.extent[0],mesh.extent[3]-mesh.extent[1]);
  const contextMaterial=new THREE.MeshBasicMaterial({map:mapTexture});
  const flatContext=new THREE.Mesh(contextGeometry,contextMaterial);flatContext.rotation.x=-Math.PI/2;flatContext.position.y=-20;
  if(mesh.support_mask)scene.add(flatContext);
  const ground=new THREE.Mesh(geometry,new THREE.MeshLambertMaterial({map:mapTexture}));scene.add(ground);
  const redrawAppearance=()=>{appearance.draw(palette,showRoadCartography?displayRoads:null);mapTexture.needsUpdate=true;
    renderer.domElement.dataset.palette=palette;renderer.domElement.dataset.roadLines=String(showRoadCartography?displayRoads?.lines.length??0:0);render();};
  let observation=initial,playing=false,visible=document.visibilityState==='visible',intersecting=false,contextLost=false;
  let elapsed=0,last=performance.now(),statisticsAt=0,frame:number|undefined,disposed=false;
  let objects=new THREE.Group(),effects=new THREE.Group();scene.add(objects,effects);
  let journeys:{actor:THREE.Group;unit:Resource;previousMinute:number}[]=[];
  let clouds:{sprite:THREE.Sprite;column:number;row:number;index:number;anchor:THREE.Vector3}[]=[];
  let tags:SceneTag[]=[],badges:{sprite:THREE.Sprite;pixels:number}[]=[];
  const persistent=new Set<THREE.Material>(),icons=new Map<TacticalMarkerKind,THREE.SpriteMaterial>();
  const labels=new Map<string,{material:THREE.SpriteMaterial;width:number;height:number}>();
  const lineMaterials=new Map<string,THREE.LineBasicMaterial>(),ringMaterials=new Map<string,THREE.MeshBasicMaterial>();
  const activeLabels=new Set<string>(),smokeTexture=cloudTexture();
  const smokeMaterials=Array.from({length:TACTICAL_SMOKE_PUFFS},()=>{
    const material=new THREE.SpriteMaterial({map:smokeTexture,transparent:true,depthWrite:false,opacity:.6});
    persistent.add(material);return material;
  });
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
  const lineMaterial=(color:string)=>{
    let material=lineMaterials.get(color);
    if(!material){material=new THREE.LineBasicMaterial({color,transparent:true,opacity:.82,depthTest:false});lineMaterials.set(color,material);persistent.add(material);}
    return material;
  };
  const ringMaterial=(color:string,opacity=1)=>{
    const key=color+opacity;let material=ringMaterials.get(key);
    if(!material){material=new THREE.MeshBasicMaterial({color,side:THREE.DoubleSide,transparent:opacity<1,opacity,depthWrite:false});ringMaterials.set(key,material);persistent.add(material);}
    return material;
  };
  const disposables=(group:THREE.Object3D)=>group.traverse(object=>{
    const drawable=object as THREE.Mesh;if(!(object as THREE.Sprite).isSprite)drawable.geometry?.dispose();
    const materials=drawable.material?(Array.isArray(drawable.material)?drawable.material:[drawable.material]):[];
    for(const material of materials)if(!persistent.has(material))material.dispose();
  });
  const label=(id:string,text:string,anchor:THREE.Object3D,color:string,priority:number)=>{
    const key=text+color;activeLabels.add(key);let entry=labels.get(key);
    if(!entry){
      const rendered=labelCanvas(text,color),texture=new THREE.CanvasTexture(rendered.canvas);texture.colorSpace=THREE.SRGBColorSpace;
      const material=new THREE.SpriteMaterial({map:texture,depthTest:false,depthWrite:false,transparent:true});
      entry={material,width:rendered.width,height:rendered.height};labels.set(key,entry);persistent.add(material);
    }
    const sprite=new THREE.Sprite(entry.material);sprite.renderOrder=50;objects.add(sprite);
    const leader=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(),new THREE.Vector3()]),lineMaterial(color));
    leader.renderOrder=49;leader.frustumCulled=false;objects.add(leader);
    tags.push({id,sprite,anchor,leader,width:entry.width,height:entry.height,priority});
  };
  const anchorAt=(metric:Position,lift=24)=>{
    const position=sceneCoordinates(mesh,metric,lift);if(!position)return null;
    const anchor=new THREE.Group();anchor.position.set(...position);objects.add(anchor);return anchor;
  };
  const beacon=(kind:TacticalMarkerKind,metric:Position,id:string,text:string,priority:number,lift=24,selection?:MapSelection)=>{
    const anchor=anchorAt(metric,lift);if(!anchor)return null;
    let material=icons.get(kind);
    if(!material){
      const texture=new THREE.CanvasTexture(markerTextureCanvas(kind));texture.colorSpace=THREE.SRGBColorSpace;
      material=new THREE.SpriteMaterial({map:texture,depthTest:false,depthWrite:false,transparent:true});icons.set(kind,material);persistent.add(material);
    }
    const sprite=new THREE.Sprite(material);sprite.renderOrder=45;sprite.userData.selection=selection;anchor.add(sprite);
    badges.push({sprite,pixels:markerStyles[kind].screenPixels});
    label(id,text,anchor,markerStyles[kind].outline,priority);return anchor;
  };
  function constrainCamera(){
    const width=Math.max(1,container.clientWidth),height=Math.max(1,container.clientHeight);
    const pitch=90-controls.getPolarAngle()*180/Math.PI,azimuth=controls.getAzimuthalAngle();
    const bounded=constrainTacticalFrame([controls.target.x+middleEast,middleNorth-controls.target.z],
      baseHeight/camera.zoom,width/height,mesh.extent,maximumHeight-minimumHeight,pitch,azimuth);
    const offset=new THREE.Vector3(bounded.centre[0]-middleEast-controls.target.x,referenceHeight-controls.target.y,
      middleNorth-bounded.centre[1]-controls.target.z);
    controls.target.add(offset);camera.position.add(offset);camera.zoom=baseHeight/bounded.height;
    controls.minZoom=baseHeight/bounded.maximumHeight;controls.maxZoom=baseHeight/bounded.minimumHeight;
    camera.left=-baseHeight*width/height/2;camera.right=-camera.left;camera.top=baseHeight/2;camera.bottom=-camera.top;
    camera.updateProjectionMatrix();
  }
  function focus(){
    const area=publicTacticalFrame(observation,mesh,Math.max(1,container.clientWidth)/Math.max(1,container.clientHeight));
    showFrame(area);
  }
  function fitArea(){
    const area=constrainTacticalFrame([(mesh.extent[0]+mesh.extent[2])/2,mesh.extent[1]+(mesh.extent[3]-mesh.extent[1])*.38],
      mesh.extent[3]-mesh.extent[1],Math.max(1,container.clientWidth)/Math.max(1,container.clientHeight),mesh.extent,maximumHeight-minimumHeight);
    showFrame(area);
  }
  function showFrame(area:ReturnType<typeof publicTacticalFrame>){
    baseHeight=area.height;camera.zoom=1;Object.assign(controls,tacticalZoomLimits(area));
    controls.target.set(area.centre[0]-middleEast,referenceHeight,middleNorth-area.centre[1]);
    const pitch=TACTICAL_PITCH_DEGREES*Math.PI/180;
    camera.position.copy(controls.target).add(new THREE.Vector3(0,distance*Math.sin(pitch),distance*Math.cos(pitch)));
    cameraChanging=true;controls.update();constrainCamera();cameraChanging=false;render();
  }
  let fireMaterial:THREE.ShaderMaterial|undefined;
  function particles(cells:Position[]){
    if(!fireMaterial){
      fireMaterial=new THREE.ShaderMaterial({transparent:true,depthWrite:false,blending:THREE.NormalBlending,
        uniforms:{phase:{value:0},pixelsPerMetre:{value:1}},vertexShader:TACTICAL_FIRE_VERTEX_SHADER,
        fragmentShader:`uniform float phase;varying float age;varying float variation;
          void main(){vec2 point=gl_PointCoord;float rise=1.-point.y;float width=mix(.43,.035,rise);
          float sway=sin(rise*8.-phase*4.+variation*17.)*.065*rise;
          float edge=1.-smoothstep(width*.5,width,abs(point.x-.5-sway));
          float end=smoothstep(0.,.13,rise)*(1.-smoothstep(.8,1.,rise));
          float core=1.-smoothstep(.02,.25,abs(point.x-.5));
          vec3 color=mix(vec3(1.,.19,.012),vec3(1.,.91,.32),core*(1.-rise));
          float alpha=edge*end*(1.-age*.48);if(alpha<.015)discard;gl_FragColor=vec4(color,alpha);}`});
      persistent.add(fireMaterial);
    }
    const count=cells.length*TACTICAL_FIRE_PARTICLES,anchors=new Float32Array(count*3),seeds=new Float32Array(count);
    let index=0;
    for(const [column,row] of cells){
      const centre=cellCenter(observation.geography,column,row);
      for(let particle=0;particle<TACTICAL_FIRE_PARTICLES;particle++){
        const seed=particleSeed(column,row,particle),angle=seed*Math.PI*2,radius=45+particleSeed(column,row,particle+100)*135;
        const position=sceneCoordinates(mesh,[centre[0]+Math.cos(angle)*radius,centre[1]+Math.sin(angle)*radius],16);
        if(!position)continue;anchors.set(position,index*3);seeds[index]=seed;index++;
      }
    }
    const particleGeometry=new THREE.BufferGeometry();particleGeometry.setAttribute('position',new THREE.BufferAttribute(anchors,3));
    particleGeometry.setAttribute('seed',new THREE.BufferAttribute(seeds,1));particleGeometry.setDrawRange(0,index);
    const points=new THREE.Points(particleGeometry,fireMaterial);points.frustumCulled=false;points.renderOrder=18;effects.add(points);
  }
  function update(next:Observation,world:World|null){
    const previous=new Map(observation.resources.map(unit=>[unit.id,unit]));observation=next;
    elapsed=0;journeys=[];clouds=[];tags=[];badges=[];activeLabels.clear();
    disposables(objects);disposables(effects);scene.remove(objects,effects);objects=new THREE.Group();effects=new THREE.Group();scene.add(objects,effects);
    for(const town of coastalPlaces){
      const anchor=anchorAt(town.position,20);if(anchor)label(`town:${town.name}`,town.name,anchor,'#d9e5d4',tacticalLabelStyle.priorities.ordinary-40);
    }
    for(const location of unconfirmedReportLocations(observation)){
      const report=observation.reports.find(item=>item.location?.[0]===location[0]&&item.location[1]===location[1]&&item.received_at<=observation.minute);
      beacon('claim',cellCenter(observation.geography,...location),`claim:${location.join(',')}`,'Unconfirmed smoke report',
        tacticalLabelStyle.priorities.ordinary+10,24,report?{type:'report',id:report.id}:undefined);
    }
    const refuges=new Set<string>();
    for(const district of observation.districts){
      beacon('settlement',cellCenter(observation.geography,...tacticalDistrictPosition(district,observation.minute)),`group:${district.id}`,
        `${groupName(district.id)} · ${district.population}`,tacticalLabelStyle.priorities.ordinary+200,24,{type:'district',id:district.id});
      const metric=cellCenter(observation.geography,...district.refuge),key=metric.join(',');
      if(!refuges.has(key)){refuges.add(key);beacon('refuge',metric,`refuge:${key}`,'Exercise refuge',tacticalLabelStyle.priorities.ordinary+100);}
    }
    for(const unit of observation.resources){
      if(!tacticalPacketDelivered(unit,observation.minute))continue;
      const metric=reportedTacticalPosition(unit,observation.minute,observation.geography);
      const kind:TacticalMarkerKind=unit.kind==='aircraft'?'plane':unit.kind.includes('crew')?'crew':'truck';
      const status=unit.state?.replaceAll('_',' ')??'reported';
      const actor=metric?beacon(kind,metric,`unit:${unit.id}`,`${resourceKindName(unit.kind)} · ${status}`,
        tacticalLabelStyle.priorities.ordinary+50,unit.kind==='aircraft'?420:25,{type:'resource',id:unit.id}):null;
      const earlier=previous.get(unit.id);
      if(actor&&unit.movement&&earlier?.movement?.route_id===unit.movement.route_id&&earlier.observed_at!=null
        &&earlier.observed_at<unit.movement.observed_at)journeys.push({actor,unit,previousMinute:earlier.observed_at});
      const assignment=activeTacticalTarget(unit,observation.minute);
      if(assignment){
        const target=anchorAt(cellCenter(observation.geography,...assignment),16);
        if(target){
          const ring=new THREE.Mesh(new THREE.RingGeometry(38,53,32),ringMaterial('#ffc34c'));ring.rotation.x=-Math.PI/2;target.add(ring);
          label(`target:${unit.id}`,`Assigned target · ${resourceKindName(unit.kind)}`,target,'#ffc34c',tacticalLabelStyle.priorities.ordinary+20);
        }
      }
    }
    for(const unit of deliveredTacticalRoutes(observation))for(const line of routeDisplayLines(unit.movement!)){
      const vertices:THREE.Vector3[]=[];
      for(let index=1;index<line.coordinates.length;index++){
        const clipped=clipToTerrain(line.coordinates[index-1],line.coordinates[index],mesh.extent);if(!clipped)continue;
        const count=Math.max(1,Math.ceil(Math.hypot(clipped[1][0]-clipped[0][0],clipped[1][1]-clipped[0][1])/50));
        const points:THREE.Vector3[]=[];
        for(let step=0;step<=count;step++){
          const metric:Position=[clipped[0][0]+(clipped[1][0]-clipped[0][0])*step/count,
            clipped[0][1]+(clipped[1][1]-clipped[0][1])*step/count];
          const point=sceneCoordinates(mesh,metric,line.mode==='flight'?420:12);if(point)points.push(new THREE.Vector3(...point));
        }
        for(let vertex=1;vertex<points.length;vertex++)vertices.push(points[vertex-1],points[vertex]);
      }
      if(vertices.length)objects.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(vertices),lineMaterial(line.mode==='flight'?'#a1deff':'#ffcc65')));
    }
    for(const cell of (world?[]:staleTacticalCells(observation).slice(0,80))){
      const anchor=anchorAt(cellCenter(observation.geography,...cell),8);if(!anchor)continue;
      const ring=new THREE.Mesh(new THREE.RingGeometry(45,55,24),ringMaterial('#c2b9a6',.6));ring.rotation.x=-Math.PI/2;anchor.add(ring);
    }
    if(world){
      for(const heat of [false,true]){
        const cells=world.world_fire.filter(cell=>Number.isInteger(cell.column)&&Number.isInteger(cell.row)&&cell.column>=0&&cell.row>=0
          &&cell.column<observation.geography.width&&cell.row<observation.geography.height&&(heat?cell.heat>0:cell.heat<=0&&cell.burned>0));
        if(!cells.length)continue;
        const geometry=new THREE.CircleGeometry(180,20),material=new THREE.MeshBasicMaterial({color:heat?'#ff6a19':'#4e3b35',transparent:true,opacity:heat ? .8 : .65,depthWrite:false,side:THREE.DoubleSide});
        const markers=new THREE.InstancedMesh(geometry,material,cells.length),matrix=new THREE.Object3D();let count=0;
        for(const cell of cells){const position=sceneCoordinates(mesh,cellCenter(observation.geography,cell.column,cell.row),35);if(!position)continue;
          matrix.position.set(...position);matrix.rotation.x=-Math.PI/2;matrix.updateMatrix();markers.setMatrixAt(count++,matrix.matrix);}
        markers.count=count;markers.renderOrder=17;objects.add(markers);
      }
    }
    const cells=showEffects?(world?world.world_fire.filter(cell=>cell.heat>0).map(cell=>[cell.column,cell.row] as Position):effectCells(observation)).slice(0,MAX_TACTICAL_FIRE_CELLS):[];particles(cells);
    for(const item of world?[]:tacticalFireLabels(observation)){
      const anchor=anchorAt(cellCenter(observation.geography,...item.cell),28);
      if(anchor)label(`fire:${item.cell.join(',')}`,fireObservationLabel(item.age),anchor,'#ffad4c',tacticalLabelStyle.priorities.urgent);
    }
    for(const [column,row] of cells){
      const position=sceneCoordinates(mesh,cellCenter(observation.geography,column,row),20);
      if(!position||!tacticalSmokePuff(column,row,0,0,observation.wind))continue;
      for(let index=0;index<TACTICAL_SMOKE_PUFFS;index++){
        const sprite=new THREE.Sprite(smokeMaterials[index]);sprite.renderOrder=20;effects.add(sprite);
        clouds.push({sprite,column,row,index,anchor:new THREE.Vector3(...position)});
      }
    }
    for(const [key,entry] of labels)if(!activeLabels.has(key)){
      entry.material.map?.dispose();entry.material.dispose();persistent.delete(entry.material);labels.delete(key);
    }
    renderer.domElement.dataset.fireCells=String(cells.length);render();schedule();
  }
  function render(){
    if(disposed||contextLost||!visible||!intersecting)return;
    const progress=playing&&!reduced.matches?Math.min(1,elapsed/700):1;
    for(const journey of journeys){
      const route=journey.unit.movement!,minute=journey.previousMinute+(route.observed_at-journey.previousMinute)*progress;
      const position=sceneCoordinates(mesh,positionOnRoute(route,minute),journey.unit.kind==='aircraft'?420:25);
      if(position)journey.actor.position.set(...position);
    }
    const phase=observation.minute*.15+elapsed/1000;
    const pixelsPerMetre=orthographicPixelsPerMetre(baseHeight,camera.zoom,Math.max(1,container.clientHeight));
    const compass=container.parentElement?.querySelector<HTMLElement>('[data-tactical-north]');
    const scaleBar=container.parentElement?.querySelector<HTMLElement>('[data-tactical-scale]');
    const scaleLabel=container.parentElement?.querySelector<HTMLElement>('[data-tactical-scale-label]');
    const rotation=`rotate(${tacticalNorthRotation(controls.getAzimuthalAngle(),90-controls.getPolarAngle()*180/Math.PI).toFixed(1)}deg)`,scale=tacticalScale(pixelsPerMetre);
    if(compass&&compass.style.transform!==rotation)compass.style.transform=rotation;
    if(scaleBar&&scaleBar.style.width!==`${scale.pixels.toFixed(1)}px`)scaleBar.style.width=`${scale.pixels.toFixed(1)}px`;
    if(scaleLabel&&scaleLabel.textContent!==scale.label)scaleLabel.textContent=scale.label;
    if(fireMaterial){fireMaterial.uniforms.phase.value=phase;fireMaterial.uniforms.pixelsPerMetre.value=pixelsPerMetre*renderer.getPixelRatio();}
    for(const cloud of clouds){
      const puff=tacticalSmokePuff(cloud.column,cloud.row,cloud.index,phase,observation.wind);if(!puff)continue;
      cloud.sprite.position.copy(cloud.anchor).add(new THREE.Vector3(...puff.offset));cloud.sprite.scale.set(puff.size,puff.size,1);
      smokeMaterials[cloud.index].opacity=puff.opacity;
    }
    camera.updateMatrixWorld();scene.updateMatrixWorld(true);
    const candidates:TacticalLabelCandidate[]=[],obstacles:LabelBounds[]=[];
    const viewportWidth=Math.max(1,container.clientWidth),viewportHeight=Math.max(1,container.clientHeight);
    for(const badge of badges){
      const size=badge.pixels/pixelsPerMetre;badge.sprite.scale.set(size,size,1);
      const projected=badge.sprite.getWorldPosition(new THREE.Vector3()).project(camera);
      const horizontal=(projected.x+1)*viewportWidth/2,vertical=(1-projected.y)*viewportHeight/2;
      if(projected.z>-1&&projected.z<1)obstacles.push({left:horizontal-badge.pixels/2,right:horizontal+badge.pixels/2,
        top:vertical-badge.pixels/2,bottom:vertical+badge.pixels/2});
    }
    for(const tag of tags){
      const projected=tag.anchor.getWorldPosition(new THREE.Vector3()).project(camera);
      if(projected.z>-1&&projected.z<1)candidates.push({id:tag.id,horizontal:(projected.x+1)*viewportWidth/2,
        vertical:(1-projected.y)*viewportHeight/2,width:tag.width,height:tag.height,priority:tag.priority});
    }
    const placements=new Map(declutterTacticalLabels(candidates,viewportWidth,viewportHeight,obstacles,
      tacticalLabelStyle.minimumGapPixels).map(item=>[item.id,item]));
    const cameraRight=new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld,0),cameraUp=new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld,1);
    for(const tag of tags){
      const placement=placements.get(tag.id);tag.sprite.visible=tag.leader.visible=!!placement;if(!placement)continue;
      const anchor=tag.anchor.getWorldPosition(new THREE.Vector3());
      tag.sprite.position.copy(anchor).addScaledVector(cameraRight,placement.offset[0]/pixelsPerMetre)
        .addScaledVector(cameraUp,-placement.offset[1]/pixelsPerMetre);
      tag.sprite.scale.set(tag.width/pixelsPerMetre,tag.height/pixelsPerMetre,1);
      const coordinates=tag.leader.geometry.getAttribute('position') as THREE.BufferAttribute;
      coordinates.setXYZ(0,anchor.x,anchor.y,anchor.z);coordinates.setXYZ(1,tag.sprite.position.x,tag.sprite.position.y,tag.sprite.position.z);coordinates.needsUpdate=true;
    }
    const started=performance.now();renderer.render(scene,camera);const completed=performance.now();
    if(!playing||completed-statisticsAt>1000){
      statisticsAt=completed;renderer.domElement.dataset.triangles=String(renderer.info.render.triangles);
      renderer.domElement.dataset.drawCalls=String(renderer.info.render.calls);renderer.domElement.dataset.drawMs=(completed-started).toFixed(2);
      renderer.domElement.dataset.points=String(renderer.info.render.points);renderer.domElement.dataset.smokeVisible=String(clouds.length>0);
      renderer.domElement.dataset.programs=String(renderer.info.programs?.length??0);renderer.domElement.dataset.visibleLabels=String(placements.size);
      renderer.domElement.dataset.cameraPitch=(90-controls.getPolarAngle()*180/Math.PI).toFixed(1);
    }
  }
  function schedule(){
    window.clearTimeout(frame);last=performance.now();
    const running=playing&&visible&&intersecting&&!contextLost&&!reduced.matches
      &&(Number(renderer.domElement.dataset.fireCells)>0||journeys.length>0&&elapsed<700);
    if(renderer.domElement.dataset.animating!==String(running))renderer.domElement.dataset.animating=String(running);
    if(running)frame=window.setTimeout(()=>{const now=performance.now();elapsed+=now-last;render();schedule();},1000/30);
  }
  function resize(){
    renderer.setSize(Math.max(1,container.clientWidth),Math.max(1,container.clientHeight),false);
    cameraChanging=true;constrainCamera();controls.update();cameraChanging=false;render();
  }
  const cameraChanged=()=>{if(cameraChanging)return;cameraChanging=true;constrainCamera();controls.update();cameraChanging=false;render();};
  const resizeObserver=new ResizeObserver(resize);resizeObserver.observe(container);
  const visibility=()=>{visible=document.visibilityState==='visible';render();schedule();};
  const motionChanged=()=>{render();schedule();};
  const viewport=new IntersectionObserver(entries=>{intersecting=entries.some(entry=>entry.isIntersecting);render();schedule();});viewport.observe(container);
  const loseContext=(event:Event)=>{
    event.preventDefault();contextLost=true;window.clearTimeout(frame);renderer.domElement.dataset.animating='false';
    onGraphicsStatus('3D graphics were interrupted. Waiting for recovery; the 2D map is unchanged.');
  };
  const restoreContext=()=>{
    contextLost=false;mapTexture.needsUpdate=true;smokeTexture.needsUpdate=true;
    for(const material of persistent){const texture=(material as THREE.MeshStandardMaterial).map;if(texture)texture.needsUpdate=true;}
    onGraphicsStatus('');resize();render();schedule();
  };
  let pointerStart:Position|null=null;
  const pointerDown=(event:PointerEvent)=>{pointerStart=[event.clientX,event.clientY];};
  const pointerUp=(event:PointerEvent)=>{
    if(!pointerStart||Math.hypot(event.clientX-pointerStart[0],event.clientY-pointerStart[1])>5){pointerStart=null;return;}
    pointerStart=null;const bounds=renderer.domElement.getBoundingClientRect();
    const pointer=new THREE.Vector2((event.clientX-bounds.left)/bounds.width*2-1,1-(event.clientY-bounds.top)/bounds.height*2);
    const raycaster=new THREE.Raycaster();raycaster.setFromCamera(pointer,camera);
    const hit=raycaster.intersectObjects(badges.map(badge=>badge.sprite)).find(entry=>entry.object.userData.selection);
    if(hit)onSelect(hit.object.userData.selection as MapSelection);
  };
  renderer.domElement.addEventListener('pointerdown',pointerDown);renderer.domElement.addEventListener('pointerup',pointerUp);
  renderer.domElement.addEventListener('webglcontextlost',loseContext);renderer.domElement.addEventListener('webglcontextrestored',restoreContext);
  document.addEventListener('visibilitychange',visibility);reduced.addEventListener('change',motionChanged);controls.addEventListener('change',cameraChanged);
  update(initial,initialWorld);resize();fitArea();
  return {update,focus,fitArea,setPlaying(value){playing=value;schedule();render();},
    setAppearance(mode,showRoads){palette=mode;showRoadCartography=showRoads;redrawAppearance();},
    setRoads(roads){validateDisplayRoads(roads,mesh.extent);displayRoads=roads;redrawAppearance();},dispose(){
    disposed=true;window.clearTimeout(frame);resizeObserver.disconnect();viewport.disconnect();
    document.removeEventListener('visibilitychange',visibility);reduced.removeEventListener('change',motionChanged);
    renderer.domElement.removeEventListener('pointerdown',pointerDown);renderer.domElement.removeEventListener('pointerup',pointerUp);
    renderer.domElement.removeEventListener('webglcontextlost',loseContext);renderer.domElement.removeEventListener('webglcontextrestored',restoreContext);
    controls.removeEventListener('change',cameraChanged);controls.dispose();disposables(objects);disposables(effects);
    for(const material of persistent){const texture=(material as THREE.MeshStandardMaterial).map;if(texture&&texture!==smokeTexture)texture.dispose();material.dispose();}
    smokeTexture.dispose();geometry.dispose();contextGeometry.dispose();contextMaterial.dispose();mapTexture.dispose();(ground.material as THREE.Material).dispose();
    renderer.dispose();renderer.forceContextLoss();renderer.domElement.remove();
  }};
}

export default function TerrainScene({mesh,texture,observation,world=null,animating,onClose,onSelect,showEffects=true}:
  {mesh:TerrainMeshData;texture:MapTexture;observation:Observation;world?:World|null;animating:boolean;onClose:()=>void;onSelect?:(selection:MapSelection)=>void;showEffects?:boolean}){
  const container=useRef<HTMLDivElement>(null),handle=useRef<SceneHandle|null>(null),current=useRef(observation),currentWorld=useRef(world),selection=useRef(onSelect);
  current.current=observation;currentWorld.current=world;selection.current=onSelect;
  const [error,setError]=useState(''),[graphicsStatus,setGraphicsStatus]=useState('');
  const [palette,setPalette]=useState<MapPalette>('illustrated'),[showRoads,setShowRoads]=useState(true),[roadStatus,setRoadStatus]=useState('loading');
  useEffect(()=>{
    if(!container.current)return;
    try{handle.current=sceneView(container.current,mesh,texture,current.current,currentWorld.current,setGraphicsStatus,showEffects,item=>selection.current?.(item));}
    catch(exception){setError(exception instanceof Error?exception.message:'This browser cannot render the tactical view.');}
    return()=>{handle.current?.dispose();handle.current=null;};
  },[mesh,texture,showEffects]);
  useEffect(()=>{handle.current?.update(observation,world);},[observation,world]);
  useEffect(()=>{handle.current?.setPlaying(animating);},[animating]);
  useEffect(()=>{handle.current?.setAppearance(palette,showRoads);},[palette,showRoads,mesh,texture,showEffects]);
  useEffect(()=>{
    const controller=new AbortController();setRoadStatus('loading');
    void fetch(mesh.support_mask?'/assets/map-roads-expanded-v2.json':'/assets/map-roads-v2.json',{signal:controller.signal}).then(async response=>{
      if(!response.ok)throw new Error('Road cartography unavailable');
      const text=await response.text();if(text.length>6000000)throw new Error('Road cartography exceeds the display limit');
      return JSON.parse(text) as DisplayRoadMap;
    }).then(roads=>{if(controller.signal.aborted)return;handle.current?.setRoads(roads);setRoadStatus('ready');})
      .catch(()=>{if(!controller.signal.aborted)setRoadStatus('unavailable');});
    return()=>controller.abort();
  },[mesh,texture,showEffects]);
  return <div className="terrain-scene" role="region" aria-label="Tactical 3D terrain view">
    <div className="terrain-scene-canvas" ref={container}/>
    <div className="terrain-scene-toolbar"><strong>North-up tactical relief · 1× heights</strong>
      <button title="Frame public groups, sightings and assigned targets" onClick={()=>handle.current?.focus()}>Fit activity</button>
      <button title="Zoom out to the available terrain; pan to inspect its full extent" onClick={()=>handle.current?.fitArea()}>Wider area</button>
      <select aria-label="Map colors" value={palette} onChange={event=>setPalette(event.target.value as MapPalette)} title="Illustrated relief uses display-only lighting and color from source terrain and imagery, not measured vegetation or fuel changes"><option value="illustrated">Illustrated relief</option><option value="reference">Reference colors</option><option value="original">Original colors</option></select>
      <label title="Emphasize real OSM road geometry. Dashed tracks and paths are context, not certified vehicle routes."><input type="checkbox" checked={showRoads} onChange={event=>setShowRoads(event.target.checked)}/> Roads</label>
      {world&&<strong>Evaluator truth</strong>}</div>
    <div className="tactical-world-north" role="img" aria-label="North on the map grid; may differ slightly from true north" title="North on the map grid. It may differ slightly from true north; the arrow rotates with the map."><b data-tactical-north aria-hidden="true">↑</b><span>North</span></div>
    <div className="tactical-world-scale" title="Horizontal projected distance, not road length or surface travel distance"><span data-tactical-scale-label>Scale</span><i data-tactical-scale/></div>
    {error&&<div className="terrain-scene-error" role="alert">3D unavailable: {error}<button onClick={onClose}>Retry 3D</button></div>}
    {graphicsStatus&&<div className="terrain-scene-error" role="alert">{graphicsStatus}<button onClick={onClose}>Retry 3D</button></div>}
    <div className="terrain-scene-note" title={`Left drag to pan, right drag to orbit, scroll to zoom. ${texture.label}. Source coordinates and unexaggerated supported heights; flat imagery where elevation is unavailable is not a measured height. Styled colors and lighting are illustrative, not vegetation or fuel measurements. Roads are incomplete source cartography, not vehicle-access certification. Flames, smoke and aircraft display altitude are illustrative.`}>{palette!=='original'?'Illustrative styling · ':''}500 metre simulation cells · {showRoads?(roadStatus==='ready'?'OSM roads emphasized':roadStatus==='loading'?'Roads loading':'Road overlay unavailable'):'Road emphasis off'}<br/>
      {texture.label.startsWith('OpenStreetMap')?'Map © OpenStreetMap':'Imagery © IGN'}{texture.label.includes('gaps remain')?' · imagery gaps remain':texture.label.includes('gaps filled')?' · imagery gaps filled':''}{mesh.support_mask?' · some heights unknown':''} · not live</div>
  </div>;
}
