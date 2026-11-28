'use strict';
const V='stark-sw-e00b500b325d';
const PRE=["./", "index.html", "offline.html", "trust.html"];
const OFF='offline.html';
const NO=/(^|\/)(checkout|thank_you)\.html$|\/updates\/|(^|\/)sw\.js$/;
const STATIC=/\.(?:css|js|svg|png|jpe?g|webp|avif|gif|ico|woff2?)$/i;
const NOTE='<p role="status" style="margin:0;padding:10px 16px;text-align:center;background:#2a1f05;color:#ffe7a3;font:600 14px/1.4 system-ui,sans-serif">You are offline. This is a saved copy: prices may have changed. Check again online before you buy.</p>';
self.addEventListener('install',e=>{e.waitUntil(caches.open(V).then(c=>c.addAll(PRE)).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k.indexOf('stark-sw-')===0&&k!==V).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
function keep(req,res){if(res&&res.ok&&res.type==='basic'){const c=res.clone();caches.open(V).then(k=>k.put(req,c))}return res}
function saved(res){return res.text().then(t=>new Response(t.replace(/<body([^>]*)>/i,'<body$1>'+NOTE),{status:200,headers:{'Content-Type':'text/html; charset=utf-8'}}))}
self.addEventListener('fetch',e=>{const r=e.request;if(r.method!=='GET')return;let u;try{u=new URL(r.url)}catch(x){return}
if(u.origin!==self.location.origin||u.search||NO.test(u.pathname))return;
const page=r.mode==='navigate'||(r.headers.get('accept')||'').indexOf('text/html')>=0;
if(page){e.respondWith(fetch(r.url,{cache:'no-cache',credentials:'same-origin'}).then(res=>keep(r,res)).catch(()=>caches.match(r,{ignoreSearch:true}).then(m=>m?saved(m):caches.match(new URL(OFF,self.registration.scope).href))));return}
if(STATIC.test(u.pathname))e.respondWith(caches.match(r).then(m=>m||fetch(r).then(res=>keep(r,res))))});
