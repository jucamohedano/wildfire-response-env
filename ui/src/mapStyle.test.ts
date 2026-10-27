import {describe,it,expect} from 'vitest';
import Feature from 'ol/Feature.js';
import Point from 'ol/geom/Point.js';
import {Style} from 'ol/style.js';
import {featureStyle} from './mapStyle';

describe('map presentation is not simulated geography',()=>{
  it('styles operational labels without moving their point or creating a second label',()=>{
    for(const kind of ['population','unit','claim','arrival-target']){
      const point=new Point([303250,4045250]),feature=new Feature({geometry:point,kind,label:'West settlement · 200'});
      const style=featureStyle(feature,false,20) as Style;
      expect(feature.getGeometry()?.getCoordinates()).toEqual([303250,4045250]);
      expect(style.getText()?.getText()).toBe('West settlement · 200');
      expect(style.getText()?.getBackgroundFill()).not.toBeNull();
      expect(style.getText()?.getDeclutterMode()).toBe('declutter');
    }
  });
  it('does not manufacture a heat intensity from a public fire sighting',()=>{
    const publicStyle=featureStyle(new Feature({kind:'fire',heat:null}),false,20);
    expect(publicStyle).toBeInstanceOf(Style);
    const evaluatorStyle=featureStyle(new Feature({kind:'fire',heat:.8}),false,20);
    expect(Array.isArray(evaluatorStyle)).toBe(true);
    expect(featureStyle(new Feature({kind:'unit'}),false,400)).toBeUndefined();
  });
});
