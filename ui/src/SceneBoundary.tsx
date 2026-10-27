import {Component} from 'react';
import type {ReactNode} from 'react';

export default class SceneBoundary extends Component<{children:ReactNode;onClose:()=>void},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){
    if(this.state.failed)return <div className="terrain-scene-error" role="alert">The optional 3D renderer could not load. Your episode and 2D map are unchanged.<button onClick={this.props.onClose}>Keep 2D map</button></div>;
    return this.props.children;
  }
}
