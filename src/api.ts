export async function api<T=any>(path:string, method='GET', body?:unknown, key?:string):Promise<T>{
 const token=sessionStorage.getItem('forge-access-token');
 const response=await fetch(`/v1${path}`,{method,headers:{...(body!==undefined?{'Content-Type':'application/json'}:{}),...(key?{'Idempotency-Key':key}:{}),...(token?{Authorization:`Bearer ${token}`}:{})},body:body===undefined?undefined:JSON.stringify(body)});
 if(!response.ok){const error=await response.json().catch(()=>({message:`HTTP ${response.status}`}));throw new Error(`${error.code||response.status}: ${error.message}${error.errors?' · '+error.errors.map((e:any)=>e.message).join('; '):''}`)}
 return response.json();
}
