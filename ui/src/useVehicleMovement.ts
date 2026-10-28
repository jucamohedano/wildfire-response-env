import {useEffect,useRef} from 'react';
import type Feature from 'ol/Feature.js';
import Point from 'ol/geom/Point.js';
import type Geometry from 'ol/geom/Geometry.js';
import type VectorSource from 'ol/source/Vector.js';
import {deliveredRoutes,positionOnRoute} from './movement';
import type {Observation} from './types';

export default function useVehicleMovement(source:VectorSource<Feature<Geometry>>|null,observation:Observation,animating:boolean,duration:number,target:HTMLElement|null){
  const previous=useRef(observation);
  useEffect(()=>{
    const earlier=previous.current;previous.current=observation;
    const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
    if(!source||!animating||reduced.matches||document.visibilityState!=='visible'||earlier===observation)return;
    const prior=new Map(earlier.resources.map(unit=>[unit.id,unit]));
    const routes=deliveredRoutes(observation).filter(unit=>{
      const old=prior.get(unit.id);
      return old?.movement?.route_id===unit.movement!.route_id&&old.observed_at!==null&&old.observed_at!==undefined&&old.observed_at<unit.movement!.observed_at;
    });
    const started=performance.now();let timer:number|undefined;
    function update(){
      const fraction=Math.min(1,(performance.now()-started)/Math.max(1,duration));
      for(const unit of routes){
        const old=prior.get(unit.id)!,route=unit.movement!;
        const at=old.observed_at!+(route.observed_at-old.observed_at!)*fraction;
        const feature=source!.getFeatures().find(entry=>entry.get('vehicleId')===unit.id);
        (feature?.getGeometry() as Point|undefined)?.setCoordinates(positionOnRoute(route,at));
      }
      if(fraction<1&&document.visibilityState==='visible'&&!reduced.matches)timer=window.setTimeout(update,1000/30);
    }
    const finish=()=>{
      window.clearTimeout(timer);
      for(const unit of routes){const feature=source.getFeatures().find(entry=>entry.get('vehicleId')===unit.id);(feature?.getGeometry() as Point|undefined)?.setCoordinates(unit.map_position!);}
    };
    const visibility=()=>{if(document.visibilityState!=='visible')finish();};
    const viewport=new IntersectionObserver(entries=>{if(!entries.some(entry=>entry.isIntersecting))finish();});
    if(target)viewport.observe(target);
    document.addEventListener('visibilitychange',visibility);reduced.addEventListener('change',finish);update();
    return()=>{finish();viewport.disconnect();document.removeEventListener('visibilitychange',visibility);reduced.removeEventListener('change',finish);};
  },[source,observation,animating,duration,target]);
}
