import {glyphPaths} from './mapGlyphs';
import type {GlyphKind} from './mapGlyphs';

export const markerKinds=Object.freeze(['settlement','refuge','crew','truck','plane','claim','base'] as const);
export type TacticalMarkerKind=typeof markerKinds[number];

export const MARKER_CANVAS_SIZE=128;

type BadgeShape='circle'|'hexagon'|'triangle'|'rounded-square';

export interface TacticalMarkerStyle {
  readonly shape:BadgeShape;
  readonly fill:string|null;
  readonly outline:string;
  readonly glyph:GlyphKind|null;
  readonly glyphColor:string;
  readonly fillGlyph:boolean;
  readonly dash:readonly number[];
  readonly screenPixels:number;
  readonly description:string;
  readonly badgePath:string;
  readonly glyphPath:string|null;
}

const badgePaths:Readonly<Record<BadgeShape,string>>=Object.freeze({
  circle:'M29 16A13 13 0 1 1 3 16A13 13 0 1 1 29 16Z',
  hexagon:'M16 2L28 9V23L16 30L4 23V9Z',
  triangle:glyphPaths.exposed.slice(0,glyphPaths.exposed.indexOf('Z')+1),
  'rounded-square':'M7 3H25Q29 3 29 7V25Q29 29 25 29H7Q3 29 3 25V7Q3 3 7 3Z',
});

function createStyle(definition:Omit<TacticalMarkerStyle,'badgePath'|'glyphPath'>):TacticalMarkerStyle{
  return Object.freeze({...definition,dash:Object.freeze([...definition.dash]),
    badgePath:badgePaths[definition.shape],glyphPath:definition.glyph?glyphPaths[definition.glyph]:null});
}

export const markerStyles:Readonly<Record<TacticalMarkerKind,TacticalMarkerStyle>>=Object.freeze({
  settlement:createStyle({shape:'circle',fill:'#176a9a',outline:'#bfe8ff',glyph:'population',
    glyphColor:'#ffffff',fillGlyph:false,dash:[],screenPixels:42,description:'Synthetic exercise population group'}),
  refuge:createStyle({shape:'hexagon',fill:'#17683f',outline:'#8bf199',glyph:'population',
    glyphColor:'#effff0',fillGlyph:false,dash:[],screenPixels:40,description:'Exercise refuge'}),
  crew:createStyle({shape:'triangle',fill:'#2b7b40',outline:'#a3f09a',glyph:null,
    glyphColor:'#effff0',fillGlyph:false,dash:[],screenPixels:38,description:'Reported exercise crew'}),
  truck:createStyle({shape:'hexagon',fill:'#3e2c0c',outline:'#ffc34c',glyph:'truck',
    glyphColor:'#ffcd63',fillGlyph:false,dash:[],screenPixels:42,description:'Reported exercise fire engine'}),
  plane:createStyle({shape:'circle',fill:'#163d4c',outline:'#beeaff',glyph:'plane',
    glyphColor:'#daf5ff',fillGlyph:true,dash:[],screenPixels:46,description:'Reported exercise aircraft'}),
  claim:createStyle({shape:'circle',fill:null,outline:'#ffc34c',glyph:null,
    glyphColor:'#ffc34c',fillGlyph:false,dash:[3,2],screenPixels:40,description:'Unconfirmed received report location'}),
  base:createStyle({shape:'rounded-square',fill:'#183e32',outline:'#c2d9bc',glyph:'base',
    glyphColor:'#eff9e9',fillGlyph:false,dash:[],screenPixels:40,description:'Exercise response staging'}),
});

export const tacticalLabelStyle=Object.freeze({
  background:'#0b211d',text:'#ffffff',border:'#698175',leader:'#d2e8d9',
  font:'600 12px system-ui',paddingX:7,paddingY:4,maxWidthPixels:190,
  leaderWidthPixels:1,minimumGapPixels:6,maxOffsetPixels:42,
  priorities:Object.freeze({selected:1000,urgent:900,ordinary:100}),
  allowOrdinaryOverlap:false,allowPriorityOverlap:false,
});

export function markerTextureCanvas(kind:TacticalMarkerKind):HTMLCanvasElement{
  if(!Object.hasOwn(markerStyles,kind))throw new TypeError(`Unsupported tactical marker kind: ${String(kind)}`);
  const style=markerStyles[kind],canvas=document.createElement('canvas');
  canvas.width=MARKER_CANVAS_SIZE;canvas.height=MARKER_CANVAS_SIZE;
  const context=canvas.getContext('2d');
  if(!context)throw new Error('Canvas 2D drawing is unavailable.');
  context.save();context.scale(MARKER_CANVAS_SIZE/32,MARKER_CANVAS_SIZE/32);
  context.lineCap='round';context.lineJoin='round';context.setLineDash([...style.dash]);
  const badge=new Path2D(style.badgePath);
  if(style.fill){context.fillStyle=style.fill;context.fill(badge);}
  context.strokeStyle='#071b15';context.lineWidth=2.5;context.stroke(badge);
  context.strokeStyle=style.outline;context.lineWidth=1.35;context.stroke(badge);
  if(style.glyphPath){
    context.setLineDash([]);context.translate(5,5);context.scale(22/32,22/32);
    const glyph=new Path2D(style.glyphPath);
    context.strokeStyle=style.glyphColor;context.fillStyle=style.glyphColor;context.lineWidth=2.45;
    if(style.fillGlyph)context.fill(glyph);
    context.stroke(glyph);
  }
  context.restore();
  return canvas;
}
