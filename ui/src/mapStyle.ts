import Feature from 'ol/Feature.js';
import {Style,Fill,Stroke,Circle as CircleStyle,RegularShape,Text} from 'ol/style.js';
import {mapColors} from './mapSymbols';
import {glyphColors,glyphPaths} from './mapGlyphs';
import type {GlyphKind} from './mapGlyphs';

function label(text:string,offsetY=-25){
  return new Text({text,offsetY,font:'600 12px system-ui',fill:new Fill({color:'#f3f7f2'}),
    backgroundFill:new Fill({color:'rgba(13,34,29,.94)'}),backgroundStroke:new Stroke({color:'rgba(192,214,193,.5)',width:1}),padding:[4,7,4,7],declutterMode:'declutter'});
}

export function featureStyle(feature:Feature,narrow=false,resolution=0,tactical=true):Style|Style[]|undefined {
  const kind=feature.get('kind') as string;
  if(kind==='route')return resolution<=1500?new Style({stroke:new Stroke({color:feature.get('mode')==='flight'?'rgba(144,207,244,.85)':'rgba(255,200,81,.9)',width:2.5,lineDash:feature.get('mode')==='walk'?[2,5]:[8,5]}),zIndex:2}):undefined;
  if(kind==='town')return resolution<=1800?new Style({text:label(feature.get('label'),0),zIndex:1}):undefined;
  if(kind==='base'&&resolution>300)return resolution<=1800?new Style({image:new CircleStyle({radius:5,fill:new Fill({color:'#e6caa0'}),stroke:new Stroke({color:'#183129',width:2})}),text:label(feature.get('label')),zIndex:5}):undefined;
  if(resolution>300&&kind!=='locator')return undefined;
  if(kind==='locator')return resolution>300?new Style({image:new CircleStyle({radius:6,fill:new Fill({color:'#e88048'}),stroke:new Stroke({color:'#fff',width:2}),declutterMode:'none'}),text:label('Incident area'),zIndex:10}):undefined;
  if(kind==='grid')return new Style({stroke:new Stroke({color:'rgba(38,51,36,.5)',width:.8})});
  if(kind==='burned')return new Style({fill:new Fill({color:`rgba(48,34,31,${.28+.52*(feature.get('burned') as number)})`}),zIndex:1});
  const glyph:GlyphKind|undefined=kind==='base'?'base':kind==='arrival-target'?'arrival':kind==='unit'||kind==='vehicle'?(feature.get('resourceKind')==='engine'?'truck':feature.get('resourceKind')==='aircraft'?'plane':'unit'):['fire','stale','claim','population','refuge','exposed'].includes(kind)?kind as GlyphKind:undefined;
  if(glyph&&(kind!=='fire'||feature.get('heat')===null)){
    const renderer:NonNullable<ConstructorParameters<typeof Style>[0]>['renderer']=(coordinates,state)=>{
      const point=coordinates as number[],context=state.context,ratio=state.pixelRatio;
      context.save();context.translate(point[0],point[1]);context.scale(ratio,ratio);
      if(glyph==='fire'){context.fillStyle='rgba(249,106,37,.18)';context.beginPath();context.arc(0,0,26,0,Math.PI*2);context.fill();}
      const symbolScale=tactical?(narrow?.7:.85):.55;context.scale(symbolScale,symbolScale);context.translate(-20,-20);
      context.fillStyle='#10291f';context.strokeStyle=glyphColors[glyph];context.lineWidth=1.6;
      context.beginPath();context.roundRect(2,2,36,36,9);context.fill();context.stroke();context.translate(4,4);
      if(glyph==='plane'){context.translate(16,16);context.rotate((feature.get('heading') as number|undefined)??0);context.translate(-16,-16);}
      const path=new Path2D(glyphPaths[glyph]);context.strokeStyle=glyphColors[glyph];context.lineWidth=2;context.lineCap='round';context.lineJoin='round';
      if(glyph==='fire'){context.fillStyle='#f8792b';context.fill(path);}context.stroke(path);
      if(glyph==='fire'){context.fillStyle='#ffdc7f';context.fill(new Path2D('M16 16C21 22 20 27 16 27C11 27 12 21 16 16Z'));}
      context.restore();
    };
    const badge=new Style({renderer,hitDetectionRenderer:renderer,zIndex:kind==='fire'?3:6});
    const text=feature.get('label') as string|undefined;
    return text?[badge,new Style({text:label(narrow&&(kind==='population'||kind==='exposed')?text.split(' · ')[0]:text,(feature.get('labelOffset') as number|undefined)??(kind==='refuge'?30:kind==='arrival-target'?37:kind==='claim'?-43:-31)),zIndex:kind==='population'||kind==='exposed'?10:7})]:badge;
  }
  if(kind==='fire'){
    const heat=feature.get('heat') as number|null,cellPixels=500/Math.max(1,resolution);
    if(heat===null)return new Style({image:new CircleStyle({radius:Math.max(5,Math.min(22,cellPixels*.3)),fill:new Fill({color:'rgba(255,119,29,.36)'}),stroke:new Stroke({color:mapColors.fire,width:2,lineDash:[3,3]}),declutterMode:'none'}),zIndex:2});
    const intensity=Math.min(1,Math.max(0,heat)),cellRadius=cellPixels*.46;
    const coreRadius=Math.min(cellRadius*.65,Math.max(3,cellPixels*(.12+.2*intensity)));
    const haloRadius=Math.min(cellRadius,Math.max(coreRadius+2,coreRadius*2.2));
    return [new Style({image:new CircleStyle({radius:haloRadius,fill:new Fill({color:`rgba(255,105,22,${.12+.12*intensity})`}),declutterMode:'none'}),zIndex:2}),new Style({image:new CircleStyle({radius:coreRadius,fill:new Fill({color:`rgba(255,${Math.round(65+170*intensity)},25,.9)`}),stroke:new Stroke({color:'rgba(112,35,16,.75)',width:1}),declutterMode:'none'}),zIndex:3})];
  }
  if(kind==='stale')return new Style({image:new CircleStyle({radius:Math.max(5,Math.min(11,250/Math.max(1,resolution))),fill:new Fill({color:mapColors.staleFill}),stroke:new Stroke({color:mapColors.staleLine,width:1.5,lineDash:[4,3]}),declutterMode:'none'}),zIndex:1});
  const text=feature.get('label') as string;
  const color=kind==='claim'?mapColors.claim:kind==='arrival-target'?mapColors.arrival:kind==='unit'?mapColors.unit:kind==='exposed'?mapColors.exposed:mapColors.population;
  const target=kind==='claim'||kind==='arrival-target';
  const image=kind==='unit'?new RegularShape({points:3,radius:10,fill:new Fill({color}),stroke:new Stroke({color:'#d9f6d0',width:2}),declutterMode:'none'}):new CircleStyle({radius:target?10:kind==='refuge'?5:8,fill:new Fill({color:target?'rgba(248,191,91,.2)':color}),stroke:new Stroke({color:target?color:'#f3f7f2',width:2,lineDash:target?[4,3]:undefined}),declutterMode:'none'});
  const offsetY=(feature.get('labelOffset') as number|undefined)??(kind==='refuge'?19:kind==='claim'?-48:kind==='arrival-target'?32:-25);
  const priority=kind==='population'||kind==='exposed'?10:kind==='unit'?9:kind==='arrival-target'?8:kind==='claim'?6:4;
  return new Style({image,text:label(narrow&&(kind==='population'||kind==='exposed')?text.split(' · ')[0]:text,offsetY),zIndex:priority});
}
