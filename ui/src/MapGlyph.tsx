import {glyphColors,glyphPaths} from './mapGlyphs';
import type {GlyphKind} from './mapGlyphs';

export default function MapGlyph({kind}:{kind:GlyphKind}){
  return <svg className="map-key-symbol" viewBox="0 0 40 40" aria-hidden="true"><rect x="2" y="2" width="36" height="36" rx="10" fill="#10291f" stroke={glyphColors[kind]} strokeWidth="1.5"/><g transform="translate(4 4)"><path d={glyphPaths[kind]} fill={kind==='fire'?'#f8792b':'none'} stroke={glyphColors[kind]} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>{kind==='fire'&&<path d="M16 16C21 22 20 27 16 27C11 27 12 21 16 16Z" fill="#ffdc7f"/>}</g></svg>;
}
