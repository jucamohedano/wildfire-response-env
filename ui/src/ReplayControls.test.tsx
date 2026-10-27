import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import ReplayControls from './ReplayControls';
import type {ReplayControlsProps} from './ReplayControls';

const props:ReplayControlsProps={minute:36,recordedMinutes:120,frameCount:61,frameIndex:18,busy:false,replaying:false,onPlay:()=>{},onPause:()=>{},onReset:()=>{},onSeek:()=>{}};

describe('compact replay footer',()=>{
  it('labels separate play, pause and rewind controls and writes full units in tooltips',()=>{
    const html=renderToStaticMarkup(<ReplayControls {...props}/>);
    for(const label of ['Play replay','Pause replay','Reset replay','Finish replay','Trajectory position'])expect(html).toContain(`aria-label="${label}"`);
    expect(html).toContain('title="Play replay"');expect(html).toContain('rewind only, not the live simulation');
    expect(html).toContain('36 of 120 simulation minutes');expect(html).toContain('00:36 / 02:00');
    expect(html).not.toContain('Run to end');expect(html).not.toContain('trajectory-heading');
  });
  it('disables starting an empty replay and leaves pause enabled while playing',()=>{
    const html=renderToStaticMarkup(<ReplayControls {...props} frameCount={1}/>);
    expect(html).toMatch(/aria-label="Play replay"[^>]*disabled/);
    const playing=renderToStaticMarkup(<ReplayControls {...props} replaying/>);
    expect(playing).toMatch(/aria-label="Play replay"[^>]*disabled/);
    expect(playing).not.toMatch(/aria-label="Pause replay"[^>]*disabled/);
  });
});
