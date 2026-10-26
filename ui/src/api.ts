async function responseFor(path:string,body?:unknown):Promise<Response> {
  const response=await fetch(`/api/${path}`,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if (!response.ok) {
    const payload=await response.json();
    throw new Error(typeof payload.detail==='string'?payload.detail:JSON.stringify(payload.detail??payload));
  }
  return response;
}

export async function request<Result>(path:string,body?:unknown):Promise<Result> {
  return (await responseFor(path,body)).json() as Promise<Result>;
}

export async function requestText(path:string):Promise<string> {
  return (await responseFor(path)).text();
}
