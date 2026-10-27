import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {glyphPaths} from './mapGlyphs';
import {MARKER_CANVAS_SIZE,markerKinds,markerStyles,markerTextureCanvas,tacticalLabelStyle} from './tacticalMarkersV2';
import type {TacticalMarkerKind} from './tacticalMarkersV2';

describe('v2 tactical marker design metadata',()=>{
  it('supports exactly the exercise marker kinds at bounded screen sizes',()=>{
    expect(markerKinds).toEqual(['settlement','refuge','crew','truck','plane','claim','base']);
    expect(Object.keys(markerStyles)).toEqual([...markerKinds]);
    for(const kind of markerKinds){
      expect(markerStyles[kind].screenPixels).toBeGreaterThanOrEqual(36);
      expect(markerStyles[kind].screenPixels).toBeLessThanOrEqual(46);
      expect(markerStyles[kind].badgePath).toMatch(/^M/);
      expect(markerStyles[kind].outline).toMatch(/^#[\da-f]{6}$/);
    }
  });

  it('matches the reference palette and uses established paths without a question glyph',()=>{
    expect(markerStyles.settlement).toMatchObject({shape:'circle',fill:'#176a9a',glyphColor:'#ffffff',glyphPath:glyphPaths.population});
    expect(markerStyles.refuge).toMatchObject({shape:'hexagon',fill:'#17683f',glyphPath:glyphPaths.population});
    expect(markerStyles.crew).toMatchObject({shape:'triangle',fill:'#2b7b40',badgePath:'M16 3L30 28H2Z',glyphPath:null});
    expect(markerStyles.crew.badgePath).not.toContain('M16 12');
    expect(markerStyles.truck).toMatchObject({outline:'#ffc34c',glyphPath:glyphPaths.truck});
    expect(markerStyles.plane).toMatchObject({glyphColor:'#daf5ff',glyphPath:glyphPaths.plane,fillGlyph:true});
    expect(markerStyles.claim).toMatchObject({fill:null,outline:'#ffc34c',glyphPath:null,dash:[3,2]});
    expect(markerStyles.base.glyphPath).toBe(glyphPaths.base);
    for(const kind of markerKinds)expect(markerStyles[kind].glyphPath).not.toBe(glyphPaths.claim);
  });

  it('keeps palette metadata immutable and population/staging semantics synthetic',()=>{
    expect(Object.isFrozen(markerKinds)).toBe(true);
    expect(Object.isFrozen(markerStyles)).toBe(true);
    for(const kind of markerKinds){
      expect(Object.isFrozen(markerStyles[kind])).toBe(true);
      expect(Object.isFrozen(markerStyles[kind].dash)).toBe(true);
    }
    expect(markerStyles.settlement.description).toMatch(/Synthetic exercise/);
    expect(markerStyles.refuge.description).toMatch(/Exercise/);
    expect(markerStyles.base.description).toMatch(/Exercise/);
  });

  it('gives selected and urgent labels first placement without permitting overlaps',()=>{
    expect(tacticalLabelStyle.background).toMatch(/^#[\da-f]{6}$/);
    expect(tacticalLabelStyle.text).toBe('#ffffff');
    expect(tacticalLabelStyle.priorities.selected).toBeGreaterThan(tacticalLabelStyle.priorities.urgent);
    expect(tacticalLabelStyle.priorities.urgent).toBeGreaterThan(tacticalLabelStyle.priorities.ordinary);
    expect(tacticalLabelStyle.allowOrdinaryOverlap).toBe(false);
    expect(tacticalLabelStyle.allowPriorityOverlap).toBe(false);
    expect(tacticalLabelStyle.minimumGapPixels).toBeGreaterThanOrEqual(5);
    expect(tacticalLabelStyle.maxWidthPixels).toBeLessThanOrEqual(190);
    expect(tacticalLabelStyle.leaderWidthPixels).toBe(1);
    expect(Object.isFrozen(tacticalLabelStyle)).toBe(true);
    expect(Object.isFrozen(tacticalLabelStyle.priorities)).toBe(true);
  });
});

describe('v2 canvas marker rendering without a DOM library',()=>{
  const paths:string[]=[];
  const context={save:vi.fn(),restore:vi.fn(),scale:vi.fn(),translate:vi.fn(),
    setLineDash:vi.fn(),fill:vi.fn(),stroke:vi.fn(),fillText:vi.fn(),
    lineCap:'',lineJoin:'',lineWidth:0,strokeStyle:'',fillStyle:''};
  const getContext=vi.fn(()=>context);
  const createElement=vi.fn(()=>({width:0,height:0,getContext}));

  beforeEach(()=>{
    vi.clearAllMocks();paths.length=0;
    getContext.mockImplementation(()=>context);
    createElement.mockImplementation(()=>({width:0,height:0,getContext}));
    vi.stubGlobal('document',{createElement});
    vi.stubGlobal('Path2D',class {constructor(path:string){paths.push(path);}});
  });

  afterEach(()=>vi.unstubAllGlobals());

  it.each(markerKinds)('draws %s from approved SVG paths with balanced state',kind=>{
    const originalGlyphs=JSON.stringify(glyphPaths),originalStyles=JSON.stringify(markerStyles);
    const canvas=markerTextureCanvas(kind),style=markerStyles[kind];
    expect(createElement).toHaveBeenCalledExactlyOnceWith('canvas');
    expect(canvas.width).toBe(MARKER_CANVAS_SIZE);
    expect(canvas.height).toBe(MARKER_CANVAS_SIZE);
    expect(getContext).toHaveBeenCalledExactlyOnceWith('2d');
    expect(context.scale).toHaveBeenNthCalledWith(1,4,4);
    expect(context.setLineDash).toHaveBeenNthCalledWith(1,[...style.dash]);
    expect(paths).toEqual(style.glyphPath?[style.badgePath,style.glyphPath]:[style.badgePath]);
    expect(context.save).toHaveBeenCalledOnce();
    expect(context.restore).toHaveBeenCalledOnce();
    expect(context.stroke).toHaveBeenCalledTimes(style.glyphPath?3:2);
    expect(context.fillText).not.toHaveBeenCalled();
    expect(JSON.stringify(glyphPaths)).toBe(originalGlyphs);
    expect(JSON.stringify(markerStyles)).toBe(originalStyles);
  });

  it('leaves an unconfirmed call hollow and dashed, with no extra question label',()=>{
    markerTextureCanvas('claim');
    expect(context.fill).not.toHaveBeenCalled();
    expect(context.setLineDash).toHaveBeenCalledExactlyOnceWith([3,2]);
    expect(paths).toEqual([markerStyles.claim.badgePath]);
    expect(context.fillText).not.toHaveBeenCalled();
  });

  it('fills the aircraft silhouette and returns independent canvases for texture ownership',()=>{
    const first=markerTextureCanvas('plane'),second=markerTextureCanvas('plane');
    expect(first).not.toBe(second);
    expect(context.fill).toHaveBeenCalledTimes(4);
    expect(paths).toEqual([markerStyles.plane.badgePath,glyphPaths.plane,markerStyles.plane.badgePath,glyphPaths.plane]);
  });

  it('rejects unsupported kinds before touching the document',()=>{
    for(const kind of ['fire','exposed','unknown','toString'])expect(()=>markerTextureCanvas(kind as TacticalMarkerKind)).toThrow(TypeError);
    expect(createElement).not.toHaveBeenCalled();
  });

  it('reports a missing drawing context instead of returning an empty icon',()=>{
    getContext.mockReturnValueOnce(null as unknown as typeof context);
    expect(()=>markerTextureCanvas('settlement')).toThrow('Canvas 2D drawing is unavailable.');
  });
});
