import {Component} from 'react';
import type {ReactNode} from 'react';

export default class WorldSceneBoundary extends Component<{children:ReactNode;onRetry:()=>void},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){
    if(this.state.failed)return <div className="terrain-scene-error" role="alert">The 3D map could not load. The episode is unchanged.<button onClick={this.props.onRetry}>Retry 3D</button></div>;
    return this.props.children;
  }
}
