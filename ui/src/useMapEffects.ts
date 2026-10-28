import {useEffect,useRef} from 'react';
import type OLMap from 'ol/Map.js';
import Layer from 'ol/layer/Layer.js';
import {cellCenter} from './model';
import {effectCells,effectsAnimate,MAX_EFFECT_CELLS,PARTICLES_PER_CELL,particleSeed,smokeDirection} from './visualEffects';
import type {Observation} from './types';

export default function useMapEffects(map:OLMap|null,observation:Observation,enabled:boolean,animating:boolean){
  const clock=useRef({observation,elapsed:0});
  const metrics=useRef({draws:0,total:0,maximum:0});
  useEffect(()=>{
    if(!map||!enabled)return;
    const canvas=document.createElement('canvas'),context=canvas.getContext('2d');
    if(!context)return;
    canvas.className='incident-effects';canvas.setAttribute('aria-hidden','true');
    canvas.style.pointerEvents='none';canvas.style.position='absolute';
    const cells=effectCells(observation),wind=smokeDirection(observation.wind);
    const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
    if(clock.current.observation!==observation)clock.current={observation,elapsed:0};
    let timer:number|undefined,elapsed=clock.current.elapsed,previous=performance.now(),inViewport=true;
    const shouldAnimate=()=>effectsAnimate(animating,document.visibilityState==='visible'&&inViewport,reduced.matches)&&cells.length>0&&inView();
    function inView(){
      if((map!.getView().getResolution()??Infinity)>300)return false;
      const extent=map!.getView().calculateExtent(map!.getSize());
      return cells.some(([column,row])=>{const [east,north]=cellCenter(observation.geography,column,row);return east>=extent[0]-1000&&east<=extent[2]+1000&&north>=extent[1]-1000&&north<=extent[3]+1000;});
    }
    const layer=new Layer({render:frame=>{
      const started=performance.now(),ratio=Math.min(1.5,frame.pixelRatio),[width,height]=frame.size;
      if(canvas.width!==Math.round(width*ratio))canvas.width=Math.round(width*ratio);
      if(canvas.height!==Math.round(height*ratio))canvas.height=Math.round(height*ratio);
      canvas.style.width=`${width}px`;canvas.style.height=`${height}px`;
      context.setTransform(ratio,0,0,ratio,0,0);context.clearRect(0,0,width,height);
      let visible=0;
      if(frame.viewState.resolution<=300){
        const affine=frame.coordinateToPixelTransform;
        const pixel=(east:number,north:number)=>[affine[0]*east+affine[2]*north+affine[4],affine[1]*east+affine[3]*north+affine[5]];
        const size=Math.max(52,Math.min(140,500/frame.viewState.resolution)),phase=observation.minute*.37+elapsed/1000;
        for(const [column,row] of cells){
          const [east,north]=cellCenter(observation.geography,column,row),[centreX,centreY]=pixel(east,north);
          if(centreX<-size*2||centreY<-size*2||centreX>width+size*2||centreY>height+size*2)continue;
          if(visible>=MAX_EFFECT_CELLS)break;
          visible++;
          const glow=context.createRadialGradient(centreX,centreY,0,centreX,centreY,size*.55);
          glow.addColorStop(0,'rgba(255,105,18,.60)');glow.addColorStop(.5,'rgba(242,69,15,.28)');glow.addColorStop(1,'rgba(242,69,15,0)');
          context.fillStyle=glow;context.fillRect(centreX-size,centreY-size,size*2,size*2);
          for(let index=0;index<PARTICLES_PER_CELL;index++){
            const seed=particleSeed(column,row,index),angle=seed*Math.PI*2;
            const flicker=.82+.18*Math.sin(phase*4+index+seed*8);
            const fireX=centreX+Math.cos(angle)*size*.45,fireY=centreY+Math.sin(angle)*size*.45;
            const radius=Math.max(2,Math.min(10,size*.13))*flicker;
            context.save();context.translate(fireX,fireY);context.rotate(Math.sin(phase+seed)*.18);
            context.fillStyle=index%2?'#ff9b25':'#f76a22';context.beginPath();context.moveTo(0,radius);
            context.bezierCurveTo(-radius,radius,-radius,-radius*.3,0,-radius*2.2);
            context.bezierCurveTo(-radius*.2,-radius*.2,radius*1.1,-radius*.7,radius*.8,radius*.3);
            context.quadraticCurveTo(radius*.7,radius,0,radius);context.fill();
            context.fillStyle='#ffe494';context.beginPath();context.ellipse(0,radius*.25,radius*.3,radius*.65,0,0,Math.PI*2);context.fill();context.restore();
            if(wind){
              const age=(phase*.12+seed)%1,drift=age*size*2.5;
              const [windX,windY]=pixel(east+wind[0]*500,north+wind[1]*500);
              const directionLength=Math.hypot(windX-centreX,windY-centreY)||1;
              const smokeX=fireX+(windX-centreX)/directionLength*drift;
              const smokeY=fireY+(windY-centreY)/directionLength*drift;
              const smokeRadius=size*(.18+age*.5);
              const smoke=context.createRadialGradient(smokeX,smokeY,0,smokeX,smokeY,smokeRadius);
              smoke.addColorStop(0,`rgba(155,163,160,${.42*(1-age)})`);smoke.addColorStop(1,'rgba(155,163,160,0)');
              context.fillStyle=smoke;context.fillRect(smokeX-smokeRadius,smokeY-smokeRadius,smokeRadius*2,smokeRadius*2);
            }
          }
        }
      }
      const duration=performance.now()-started,stats=metrics.current;
      stats.draws++;stats.total+=duration;stats.maximum=Math.max(stats.maximum,duration);
      canvas.dataset.cells=String(visible);canvas.dataset.draws=String(stats.draws);
      canvas.dataset.drawMs=duration.toFixed(2);canvas.dataset.meanDrawMs=(stats.total/stats.draws).toFixed(3);canvas.dataset.maxDrawMs=stats.maximum.toFixed(2);
      canvas.dataset.running=String(shouldAnimate());
      return canvas;
    }});
    map.getLayers().insertAt(3,layer);
    function schedule(){
      window.clearTimeout(timer);previous=performance.now();
      if(shouldAnimate()){
        timer=window.setTimeout(()=>{const now=performance.now();elapsed+=now-previous;map!.render();schedule();},1000/30);
      }
      map!.render();
    }
    const visibility=new IntersectionObserver(entries=>{inViewport=entries.some(entry=>entry.isIntersecting);schedule();});
    const target=map.getTargetElement();if(target)visibility.observe(target);
    map.on('moveend',schedule);document.addEventListener('visibilitychange',schedule);reduced.addEventListener('change',schedule);schedule();
    return()=>{clock.current.elapsed=elapsed;window.clearTimeout(timer);visibility.disconnect();map.un('moveend',schedule);document.removeEventListener('visibilitychange',schedule);reduced.removeEventListener('change',schedule);map.removeLayer(layer);layer.dispose();};
  },[map,observation,enabled,animating]);
}
