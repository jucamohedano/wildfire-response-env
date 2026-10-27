import {mapColors} from './mapSymbols';

export type GlyphKind='fire'|'stale'|'claim'|'arrival'|'population'|'refuge'|'unit'|'truck'|'plane'|'base'|'exposed';

export const glyphPaths:Record<GlyphKind,string>={
  fire:'M16 3C19 10 12 10 17 16C21 13 22 10 22 9C29 17 27 28 16 29C5 29 3 19 9 13C9 18 12 18 12 16C10 9 15 8 16 3Z',
  stale:'M16 3L29 16L16 29L3 16Z M10 16H22',
  claim:'M5 4H27V22H17L10 29V22H5Z M13 11C13 6 23 7 20 13L16 16 M16 19V20',
  arrival:'M5 5H11 M5 5V11 M27 5H21 M27 5V11 M5 27H11 M5 27V21 M27 27H21 M27 27V21 M9 16H23 M16 9V23',
  population:'M4 15L16 4L28 15 M7 14V28H25V14 M13 28V19H19V28',
  refuge:'M5 28V4H26L22 11L26 18H6 M13 8H19 M16 5V11',
  unit:'M5 17A11 11 0 0 1 27 17V20H5Z M10 20V24L16 29L22 24V20 M16 6V16 M3 20H29',
  truck:'M3 9H20V24H3Z M20 15H25L29 20V24H20 M8 10V17 M4 14H12 M7 24A3 3 0 1 0 13 24 M22 24A3 3 0 1 0 28 24',
  plane:'M16 2L19 13L29 18V22L19 19L18 27L23 29H9L14 27L13 19L3 22V18L13 13Z',
  base:'M5 29V4H27L22 11L27 18H6 M11 9H18 M14 6V13',
  exposed:'M16 3L30 28H2Z M16 12V20 M16 23V25',
};

export const glyphColors:Record<GlyphKind,string>={fire:mapColors.fire,stale:'#c3b6a2',claim:'#ffd267',arrival:'#69d5ef',population:'#71b8eb',refuge:'#9fd8c0',unit:'#a0e891',truck:'#a0e891',plane:'#b5e1ff',base:'#e6caa0',exposed:'#ff8a7f'};
