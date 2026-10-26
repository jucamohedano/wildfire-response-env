import {afterEach,it,expect,vi} from 'vitest';
import {request,requestText} from './api';

afterEach(()=>vi.unstubAllGlobals());

it('exports original JSON numeric tokens without parsing and reserializing',async()=>{
  const original='{"position":[14.0,18.0],"reward":1.0,"seed":7}';
  const fetchMock=vi.fn().mockResolvedValue(new Response(original,{status:200}));
  vi.stubGlobal('fetch',fetchMock);
  expect(await requestText('trajectory')).toBe(original);
  expect(fetchMock).toHaveBeenCalledWith('/api/trajectory',undefined);
});

it('keeps ordinary typed UI reads and explicit transport errors',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response('{"decision":4}',{status:200})).mockResolvedValueOnce(new Response('{"detail":"Stale episode"}',{status:409})));
  expect(await request<{decision:number}>('state')).toEqual({decision:4});
  await expect(request('action',{action:{}})).rejects.toThrow('Stale episode');
});
