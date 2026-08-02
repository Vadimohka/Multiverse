const API='/api/v1';
export type ApiError={detail?:string};
let refreshInFlight:Promise<void>|null=null;

async function refreshAccessToken(){
 if(!refreshInFlight) refreshInFlight=(async()=>{
   const refreshToken=localStorage.getItem('refresh_token');
   if(!refreshToken) throw new Error('Сессия истекла. Войдите снова.');
   const response=await fetch(`${API}/auth/refresh`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refreshToken})});
   if(!response.ok){localStorage.removeItem('access_token');localStorage.removeItem('refresh_token');throw new Error('Сессия истекла. Войдите снова.');}
   const pair=await response.json(); localStorage.setItem('access_token',pair.access_token); localStorage.setItem('refresh_token',pair.refresh_token);
 })().finally(()=>{refreshInFlight=null});
 return refreshInFlight;
}

async function request(path:string, options:RequestInit={}, retry=true){
 const token=localStorage.getItem('access_token');
 const headers=new Headers(options.headers); if(!headers.has('Content-Type')&&options.body)headers.set('Content-Type','application/json'); if(token)headers.set('Authorization',`Bearer ${token}`);
 const response=await fetch(`${API}${path}`,{...options,headers});
 if(response.status===401&&retry&&path!=='/auth/login'&&path!=='/auth/refresh'){await refreshAccessToken();return request(path,options,false);}
 return response;
}
export async function api<T>(path:string, options:RequestInit={}):Promise<T>{
 const res=await request(path,options); if(!res.ok){const data=await res.json().catch(()=>({}));throw new Error(data.detail||`HTTP ${res.status}`)}; const ct=res.headers.get('content-type')||''; return (ct.includes('json')?await res.json():await res.blob()) as T;
}
export async function apiForm<T>(path:string, body:FormData, options:RequestInit={}):Promise<T>{
 const res=await request(path,{...options,method:options.method||'POST',body});if(!res.ok){const data=await res.json().catch(()=>({}));throw new Error(data.detail||`HTTP ${res.status}`)}return await res.json() as T;
}
export async function login(email:string,password:string){const pair=await api<{access_token:string;refresh_token:string}>('/auth/login',{method:'POST',body:JSON.stringify({email,password})});localStorage.setItem('access_token',pair.access_token);localStorage.setItem('refresh_token',pair.refresh_token);return pair;}
