import {simulationClock} from './activity';

export interface ReplayControlsProps {
  minute:number;recordedMinutes:number;frameCount:number;frameIndex:number;busy:boolean;replaying:boolean;
  onPlay:()=>void;onPause:()=>void;onReset:()=>void;onSeek:(index:number)=>void;
}

export default function ReplayControls({minute,recordedMinutes,frameCount,frameIndex,busy,replaying,onPlay,onPause,onReset,onSeek}:ReplayControlsProps){
  const unavailable=busy||frameCount<2;
  return <section className="replay-controls" aria-label="Full trajectory replay">
    <button type="button" className="replay-icon" aria-label="Play replay" title="Play replay" disabled={unavailable||replaying} onClick={onPlay}>▶</button>
    <button type="button" className="replay-icon" aria-label="Pause replay" title="Pause replay" disabled={!replaying} onClick={onPause}>Ⅱ</button>
    <button type="button" className="replay-icon" aria-label="Reset replay" title="Reset replay · rewind only, not the live simulation" disabled={unavailable} onClick={onReset}>↺</button>
    <input id="timeline" aria-label="Trajectory position" aria-valuetext={`${minute} of ${recordedMinutes} simulation minutes`} type="range" min={0} max={Math.max(0,frameCount-1)} value={frameIndex} disabled={unavailable} onChange={event=>onSeek(Number(event.target.value))}/>
    <button type="button" className="replay-icon replay-end" aria-label="Finish replay" title="Jump to last recorded frame" disabled={unavailable} onClick={()=>onSeek(frameCount-1)}>⏭</button>
    <span className="replay-position" title={`${minute} of ${recordedMinutes} simulation minutes · ${frameCount} recorded frames. Replay does not advance the simulator.`}>{simulationClock(minute)} / {simulationClock(recordedMinutes)}</span>
  </section>;
}
