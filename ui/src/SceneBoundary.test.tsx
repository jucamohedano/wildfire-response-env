import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import SceneBoundary from './SceneBoundary';

describe('optional 3D failure stays isolated',()=>{
  it('keeps normal children unchanged and offers a 2D fallback after a renderer failure',()=>{
    const boundary=new SceneBoundary({children:<div>Existing dashboard</div>,onClose:()=>{}});
    expect(renderToStaticMarkup(boundary.render())).toContain('Existing dashboard');
    boundary.state=SceneBoundary.getDerivedStateFromError();
    const failure=renderToStaticMarkup(boundary.render());
    expect(failure).toContain('role="alert"');expect(failure).toContain('Keep 2D map');expect(failure).toContain('episode and 2D map are unchanged');
  });
});
