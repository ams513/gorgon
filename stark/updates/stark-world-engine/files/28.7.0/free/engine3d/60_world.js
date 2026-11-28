// ========== world look + scene building: mood looks, splat terrain, paths, scatter, real models for every object ==========
function lin3(h){return srgb2lin(hexCol(h));}
// Mood looks: sky (Poly Haven HDRI or procedural), sun, fog, grading, ground and foliage choices. Colours are sRGB hex.
R2.LOOK=R2.LOOK||{};/*%%LOOKS%%*/
R2.look=function(L){var base=R2.LOOK[L.mood]||R2.LOOK['Alpine Sunset'],o={};for(var k in base)o[k]=base[k];var x=L.look||{};for(var k2 in x)o[k2]=x[k2];return o;};
R2.assetPath=function(id){if(/^lib:/.test(id))return id;var m=/^(nature|village|characters|creatures|props)\/(.+)$/.exec(id);return m?'models/'+m[1]+'/'+m[2]+(m[1]==='characters'?'.gltf':'.glb'):id;};
function pickW(table,r){var tot=0,k;for(k in table)tot+=table[k];var x=r()*tot;for(k in table){x-=table[k];if(x<=0)return k;}return k;}
// ---------- paths: explicit L.paths, else a minimum spanning tree over the places that matter ----------
R2.paths=function(L,spawn,size){var segs=[];(L.paths||[]).forEach(function(p){var pts=p.points||p;for(var i=1;i<pts.length;i++)segs.push([+pts[i-1][0],+pts[i-1][1],+pts[i][0],+pts[i][1],+(p.w||1.4)]);});
 if(segs.length)return segs;var key=[[spawn.x,spawn.z]];(L.objects||[]).forEach(function(o){if(o.t==='house'||o.t==='portal'||o.t==='checkpoint'||o.t==='npc'||o.t==='ruin'||o.t==='door')key.push([+o.x,+o.z]);});
 key=key.filter(function(p){return isFinite(p[0])&&isFinite(p[1])&&Math.abs(p[0])<size&&Math.abs(p[1])<size;});var inT=[0],rest=key.map(function(_,i){return i;}).slice(1);
 while(rest.length){var best=null,bd=1e9;inT.forEach(function(a){rest.forEach(function(b){var d=Math.hypot(key[a][0]-key[b][0],key[a][1]-key[b][1]);if(d<bd){bd=d;best=[a,b];}});});if(!best)break;
  if(bd<70)segs.push([key[best[0]][0],key[best[0]][1],key[best[1]][0],key[best[1]][1],bd<10?0.8:1.05]);inT.push(best[1]);rest.splice(rest.indexOf(best[1]),1);}return segs;};
function segDist(x,z,s){var dx=s[2]-s[0],dz=s[3]-s[1],l=dx*dx+dz*dz,t=l?clamp(((x-s[0])*dx+(z-s[1])*dz)/l,0,1):0;return Math.hypot(x-(s[0]+dx*t),z-(s[1]+dz*t));}
// ---------- terrain mesh with splat weights (dirt, rock, sand, snow) + extra (path mask, AO) ----------
R2.terrainMesh=function(o){var size=o.size,ext=size*1.3,st=o.step||Math.max(0.45,size/150),N=Math.round(2*ext/st),gh=o.gh,wl=o.water==null?-99:o.water,lk=o.look,segs=o.paths||[],plz=o.plazas||[];
 var V=(N+1)*(N+1),pos=new Float32Array(V*3),nor=new Float32Array(V*3),sp=new Float32Array(V*4),ex=new Float32Array(V*4),idx=new Uint32Array(N*N*6),seed=o.seed||1;
 var grid=4,cells={};segs.forEach(function(s,si){var x0=Math.min(s[0],s[2])-3,x1=Math.max(s[0],s[2])+3,z0=Math.min(s[1],s[3])-3,z1=Math.max(s[1],s[3])+3;for(var gx=Math.floor(x0/grid);gx<=Math.floor(x1/grid);gx++)for(var gz=Math.floor(z0/grid);gz<=Math.floor(z1/grid);gz++){var k=gx*4096+gz;(cells[k]||(cells[k]=[])).push(si);}});
 for(var j=0;j<=N;j++)for(var i=0;i<=N;i++){var v=j*(N+1)+i,x=-ext+i*st,z=-ext+j*st,h=gh(x,z),e=0.35,nx=gh(x-e,z)-gh(x+e,z),nz=gh(x,z-e)-gh(x,z+e),nl=Math.hypot(nx,2*e,nz);
  pos[v*3]=x;pos[v*3+1]=h;pos[v*3+2]=z;nor[v*3]=nx/nl;nor[v*3+1]=2*e/nl;nor[v*3+2]=nz/nl;var slope=1-nor[v*3+1],n1=fbm2(x*0.08+seed,z*0.08,3),n2=vnoise2(x*0.35,z*0.35);
  var rock=clamp((slope-0.12)*6+(n1-0.5)*0.6,0,1)+(lk.rockish||0)*clamp((n1-0.55)*3,0,1);var sand=wl>-90?clamp((wl+0.75-h)*1.6+(n2-0.5)*0.5,0,1):0;
  var pd=9;var cl=cells[Math.floor(x/grid)*4096+Math.floor(z/grid)];if(cl)cl.forEach(function(si){var s=segs[si],d=segDist(x,z,s)-s[4]*(0.75+0.5*n2);if(d<pd)pd=d;});
  plz.forEach(function(p){var d=Math.hypot(x-p[0],z-p[1])-p[2];if(d<pd)pd=d;});var path=clamp(1-pd/0.8,0,1);var dirt=Math.max(path,clamp((n1-0.68)*2.2,0,0.45)*(1-rock));
  var snow=lk.snow?clamp(1-rock*0.8-sand,0,1)*(1-path*0.65):0;if(lk.snow)dirt*=0.35;
  var edge=Math.max(Math.abs(x),Math.abs(z))/size;rock=Math.max(rock,clamp((edge-1.12)*3,0,1));
  var s4=[dirt,rock,sand,snow],sum=s4[0]+s4[1]+s4[2]+s4[3];if(sum>1){for(var q=0;q<4;q++)s4[q]/=sum;}sp.set(s4,v*4);
  var cav=(h-(gh(x+3,z)+gh(x-3,z)+gh(x,z+3)+gh(x,z-3))*0.25);ex[v*4]=path;ex[v*4+1]=clamp(0.82+cav*0.6,0.55,1.05);}
 var c=0;for(j=0;j<N;j++)for(i=0;i<N;i++){var a=j*(N+1)+i,b=a+1,d2=a+N+1,e2=d2+1;idx[c++]=a;idx[c++]=d2;idx[c++]=b;idx[c++]=b;idx[c++]=d2;idx[c++]=e2;}
 return{pos:pos,nor:nor,splat:sp,extra:ex,idx:idx,step:st,ext:ext};};
// ---------- house prefabs from the Medieval Village kit (4 x 4 m footprint, origin at the centre, front = +Z) ----------
var V_=function(n){return'village/'+n;};
R2.PREFAB={
 cottage:{walls:{front:['Wall_Plaster_Window_Wide_Round','Wall_Plaster_Door_Round'],back:['Wall_Plaster_Window_Wide_Round','Wall_Plaster_WoodGrid'],left:['Wall_Plaster_WoodGrid','Wall_Plaster_Window_Thin_Round'],right:['Wall_Plaster_Window_Thin_Round','Wall_Plaster_WoodGrid']},
  roof:'Roof_RoundTiles_4x4',gable:'Roof_Front_Brick4',chimney:'Prop_Chimney',door:'Door_1_Round',windows:{Wall_Plaster_Window_Wide_Round:'Window_Wide_Round1',Wall_Plaster_Window_Thin_Round:'Window_Thin_Round1'},
  shutters:{Wall_Plaster_Window_Wide_Round:'WindowShutters_Wide_Round_Open',Wall_Plaster_Window_Thin_Round:'WindowShutters_Thin_Round_Open'},vine:'Prop_Vine1'},
 stonehouse:{walls:{front:['Wall_UnevenBrick_Window_Wide_Round','Wall_UnevenBrick_Door_Round'],back:['Wall_UnevenBrick_Straight','Wall_UnevenBrick_Window_Wide_Round'],left:['Wall_UnevenBrick_Window_Thin_Round','Wall_UnevenBrick_Straight'],right:['Wall_UnevenBrick_Straight','Wall_UnevenBrick_Window_Thin_Round']},
  roof:'Roof_RoundTiles_4x4',gable:'Roof_Front_Brick4',chimney:'Prop_Chimney2',door:'Door_2_Round',windows:{Wall_UnevenBrick_Window_Wide_Round:'Window_Wide_Round1',Wall_UnevenBrick_Window_Thin_Round:'Window_Thin_Round1'},shutters:{},vine:'Prop_Vine2'}};
R2.HOUSE_CFG={wallOut:-1,wallOff:2.0,doorX:-0.55,roofY:3.12,gableRot:0,cornerKind:'Corner_Exterior_Wood'};
R2.housePieces=function(kind,r){var P=R2.PREFAB[kind]||R2.PREFAB.cottage,C=R2.HOUSE_CFG,out=[],sides={front:[0,1,0],back:[0,-1,Math.PI],left:[-1,0,-Math.PI/2],right:[1,0,Math.PI/2]};
 for(var sd in sides){var s=sides[sd],ang=s[2]+(C.wallOut<0?Math.PI:0);(P.walls[sd]||[]).forEach(function(wn,k){var along=(k===0?-1:1),cx,cz;
  if(s[0]===0){cx=along*(s[1]>0?1:-1);cz=s[1]*C.wallOff;}else{cx=s[0]*C.wallOff;cz=along*(s[0]>0?-1:1);}out.push({a:V_(wn),x:cx,y:0,z:cz,r:ang});
  if(P.windows&&P.windows[wn])out.push({a:V_(P.windows[wn]),x:cx,y:0,z:cz,r:ang});if(P.shutters&&P.shutters[wn]&&r()<0.7)out.push({a:V_(P.shutters[wn]),x:cx,y:0,z:cz,r:ang});
  if(/Door/.test(wn)&&P.door){var dx=C.doorX,c=Math.cos(ang),sn=Math.sin(ang);out.push({a:V_(P.door),x:cx+c*dx,y:0,z:cz-sn*dx,r:ang,door:1});}});}
 [[-1,-1],[1,-1],[1,1],[-1,1]].forEach(function(q){out.push({a:V_(C.cornerKind),x:q[0]*C.wallOff,y:0,z:q[1]*C.wallOff,r:0});});
 out.push({a:V_(P.roof),x:0,y:C.roofY,z:0,r:C.gableRot});out.push({a:V_(P.gable),x:0,y:C.roofY,z:C.wallOff,r:C.gableRot});out.push({a:V_(P.gable),x:0,y:C.roofY,z:-C.wallOff,r:C.gableRot+Math.PI});
 if(P.chimney)out.push({a:V_(P.chimney),x:1.1,y:C.roofY+0.6,z:-0.9,r:0});if(P.vine&&r()<0.6)out.push({a:V_(P.vine),x:-1.2,y:2.9,z:C.wallOff+0.12,r:0});return out;};
// ---------- scene: static models, scatter and per-object visuals ----------
R2.Scene=function(R,ctx){var S={R:R,ctx:ctx,ready:false,models:{},loading:0,loaded:0,failed:[]},gh=ctx.gh,look=ctx.look,r=rng((ctx.seed||1)*977+13);
 function M(id){var p=R2.assetPath(id);if(!S.models[p]){S.loading++;S.models[p]=R.model(p).then(function(G){return G.ready;}).then(function(G){S.loaded++;return G;}).catch(function(e){S.failed.push(p);if(window.console)console.warn('model',p,e.message);return null;});}return S.models[p];}
 S.model=M;var jobs=[];function place(id,mat,tint,opt){jobs.push(M(id).then(function(G){if(G)R.addStatic(G,mat,tint,opt);}));}
 S.place=place;
 S.build=function(){var size=ctx.size,water=ctx.water,objs=ctx.objects||[],taken=[];
  function free(x,z,rad){for(var i=0;i<taken.length;i++){var t=taken[i];if(Math.hypot(x-t[0],z-t[1])<rad+t[2])return false;}return true;}
  objs.forEach(function(o){var x=+o.x,z=+o.z;if(isFinite(x)&&isFinite(z))taken.push([x,z,o.t==='house'?4.2*(+o.s||1):o.t==='tree'?1.2:1.0]);});
  (ctx.paths||[]).forEach(function(s){for(var k=0;k<=8;k++){var t=k/8;taken.push([s[0]+(s[2]-s[0])*t,s[1]+(s[3]-s[1])*t,s[4]*0.9]);}});
  taken.push([ctx.spawn.x,ctx.spawn.z,2.5]);
  var leaf=lin3(look.leaf||'#ffffff'),gcol=lin3(look.grass||'#ffffff');
  // objects that are scenery
  objs.forEach(function(o,idx){var x=+o.x,z=+o.z,s=+o.s||1;if(!isFinite(x)||!isFinite(z))return;var b=gh(x,z),rr=rng(idx*13+(ctx.seed||1)),rot=rr()*6.283;
   if(o.t==='tree'){var tid=o.model||('nature/'+pickW(look.trees,rr));var ts=(look.treeScale||0.7)*s*(0.85+rr()*0.3)*(/TwistedTree/.test(tid)?0.55:1);place(tid,m4yaw(x,b-0.12,z,rot,ts),[leaf[0],leaf[1],leaf[2],rr()],{});}
   else if(o.t==='rock'){var rid='nature/Rock_Medium_'+(1+Math.floor(rr()*3));place(rid,m4yaw(x,b-0.25*s,z,rot,0.95*s),[1,1,1,1]);for(var p=0;p<3;p++){var pa=rr()*6.28,pr=1.4*s+rr()*0.8;place('nature/Pebble_Round_'+(1+p),m4yaw(x+Math.cos(pa)*pr,gh(x+Math.cos(pa)*pr,z+Math.sin(pa)*pr),z+Math.sin(pa)*pr,rr()*6,1.2+rr()*0.8),[1,1,1,1]);}}
   else if(o.t==='house'){var kind=o.style||(rr()<0.65?'cottage':'stonehouse'),ro=+o.rot||0,mat=mul(m4yaw(x,b-0.08,z,ro,1),[1.05*s,0,0,0,0,s,0,0,0,0,0.85*s,0,0,0,0,1]);
    R2.housePieces(kind,rr).forEach(function(pc){if(pc.door)return;place(pc.a,mul(mat,m4yaw(pc.x,pc.y,pc.z,pc.r,1)),[1,1,1,1]);});
    for(var c=0;c<3;c++){var ca=ro+(c-1)*0.9+Math.PI*0.5,cr=3.4*s;place(c===1?'village/Prop_Crate':'nature/Bush_Common'+(rr()<0.75?'_Flowers':''),m4yaw(x+Math.sin(ca)*cr,gh(x+Math.sin(ca)*cr,z+Math.cos(ca)*cr),z+Math.cos(ca)*cr,rr()*6,c===1?0.8:0.9+rr()*0.4),[leaf[0],leaf[1],leaf[2],1]);}}
   else if(o.t==='ruin'){S.ruin(x,b,z,s,rot,rr,idx);}
   else if(o.t==='asset'&&o.asset){place(String(o.asset),m4yaw(x,b,z,o.rot!=null?+o.rot:rot,s),[1,1,1,1],{tintAll:false});}
   else if(o.t==='lamp'){var lp=R2.PROP_DEFS.lamp_post(),lc=R2.PROP_DEFS.lamp_cage();proc('prop:lamp_post',lp[0],lp[1],m4yaw(x,b-0.02,z,rot,s),[1,1,1,1]);proc('prop:lamp_cage',lc[0],lc[1],m4yaw(x,b+2.36*s,z,rot,s),[1,1,1,1]);}});
  // forest edge ring + scatter
  var ringN=Math.round(size*1.6*(R.Q.ring||1));for(var i=0;i<ringN;i++){var a=i/ringN*Math.PI*2+r()*0.05,rd=size*(1.02+r()*0.22),ex=Math.cos(a)*rd,ez=Math.sin(a)*rd;var tid2='nature/'+pickW(look.trees,r);
   place(tid2,m4yaw(ex,gh(ex,ez)-0.15,ez,r()*6.28,(look.treeScale||0.7)*(0.9+r()*0.5)*(/TwistedTree/.test(tid2)?0.55:1)),[leaf[0],leaf[1],leaf[2],r()],{shadow:false});}
  var area=(2*size)*(2*size),Q=R.Q,gN=Math.round(area*0.42*Q.grass),grassIds=['Grass_Common_Short','Grass_Common_Tall','Grass_Wispy_Short','Grass_Wispy_Tall'];
  function ok(x,z,minH){var h=gh(x,z);if(water!=null&&h<water+(minH||0.25))return false;var nx=gh(x-0.5,z)-gh(x+0.5,z),nz=gh(x,z-0.5)-gh(x,z+0.5);return Math.hypot(nx,nz)<0.75;}
  for(var g=0;g<gN;g++){var gx=(r()*2-1)*size,gz=(r()*2-1)*size;if(!ok(gx,gz)||!free(gx,gz,0.2))continue;if(look.snow&&r()<0.8)continue;var gi=grassIds[(r()*4)|0];
   place('nature/'+gi,m4yaw(gx,gh(gx,gz)-0.03,gz,r()*6.28,0.55+r()*0.5),[gcol[0]*(0.85+r()*0.3),gcol[1]*(0.85+r()*0.3),gcol[2]*(0.85+r()*0.3),r()],{fade:34+Q.grass*30,shadow:false});}
  function cluster(id,n,spread,sc,tint,opt){var cx=(r()*2-1)*size*0.95,cz=(r()*2-1)*size*0.95;if(!ok(cx,cz))return;for(var k=0;k<n;k++){var px=cx+(r()*2-1)*spread,pz=cz+(r()*2-1)*spread;if(!ok(px,pz)||!free(px,pz,0.6))continue;
   place(id,m4yaw(px,gh(px,pz)-0.05,pz,r()*6.28,sc*(0.75+r()*0.5)),tint||[1,1,1,r()],opt);}}
  var fl=Math.round(size*0.7*(look.flowers||0)*(Q.scatter||1));for(var f=0;f<fl;f++)cluster('nature/'+(r()<0.5?'Flower_3_Group':'Flower_4_Group'),3+(r()*4|0),2.2,0.55,[1,1,1,1],{fade:60});
  var fe=Math.round(size*0.45*(look.ferns||0)*(Q.scatter||1));for(f=0;f<fe;f++)cluster('nature/'+(r()<0.6?'Fern_1':'Plant_7'),2+(r()*3|0),2.5,0.35,[leaf[0],leaf[1],leaf[2],1],{fade:70});
  var bu=Math.round(size*0.5*(look.bush||0)*(Q.scatter||1));for(f=0;f<bu;f++)cluster('nature/'+(r()<0.3?'Bush_Common':'Bush_Common_Flowers'),1+(r()*2|0),3,0.85,[leaf[0],leaf[1],leaf[2],1]);
  var mu=Math.round(size*0.5*(look.mushrooms||0)*(Q.scatter||1));for(f=0;f<mu;f++)cluster('nature/'+(r()<0.7?'Mushroom_Common':'Mushroom_Laetiporus'),2+(r()*4|0),1.5,1.0,[1,1,1,1],{fade:50});
  var cv=Math.round(size*0.6*(Q.scatter||1));for(f=0;f<cv;f++)cluster('nature/Clover_'+(1+(r()*2|0)),3+(r()*4|0),1.8,0.45,[gcol[0],gcol[1],gcol[2],1],{fade:45,shadow:false});
  var pb=Math.round(size*0.8*(Q.scatter||1));for(f=0;f<pb;f++){var px2=(r()*2-1)*size,pz2=(r()*2-1)*size;if(!ok(px2,pz2,0))continue;place('nature/Pebble_'+(r()<0.5?'Round_':'Square_')+(1+(r()*2|0)),m4yaw(px2,gh(px2,pz2),pz2,r()*6.28,1+r()*1.5),[1,1,1,1],{fade:50});}
  // stepping stones along paths
  (ctx.paths||[]).forEach(function(s){var L2=Math.hypot(s[2]-s[0],s[3]-s[1]);for(var d=0.8;d<L2;d+=1.6+r()*1.2){var t=d/L2,px=s[0]+(s[2]-s[0])*t+(r()-0.5)*0.8,pz=s[1]+(s[3]-s[1])*t+(r()-0.5)*0.8;if(water!=null&&gh(px,pz)<water+0.05)continue;
   if(r()<0.55)place('nature/RockPath_Round_Small_'+(1+(r()*3|0)),m4yaw(px,gh(px,pz)+0.01,pz,r()*6.28,0.7+r()*0.4),[1,1,1,1],{fade:55,shadow:false});}});
  return Promise.all(jobs).then(function(){S.ready=true;return S;});};
 // ruins: broken brick walls + rubble + vines (what is left of a watch post)
 // ruins: four broken shrine columns (they match the four pillar colliders), a fallen lintel, rubble and vines
 function proc(key,geo,mat,m,tint,opt){opt=opt||{};opt.tintAll=true;jobs.push(R2.procModel(R,key,geo,mat).then(function(G){if(G)R.addStatic(G,m,tint||[1,1,1,1],opt);}));}
 S.proc=proc;
 S.ruin=function(x,b,z,s,rot,rr,idx){var moss=[0.82,0.9,0.8,1];
  for(var c=0;c<4;c++){var px=x+(c%2?1.6:-1.6)*s,pz=z+(c<2?1.6:-1.6)*s,ph=(1.2+rr()*2.4)*s,pb=gh(px,pz);
   proc('proc:col'+(c+idx)%6,R2.geo.column(0.42,3.6,14,0.9,((c+idx)%6)*7+3,1.4),R2.MAT.brick,mul(m4yaw(px,pb-0.15,pz,rr()*6.28,s),[1,0,0,0,0,ph/(3.6*s),0,0,0,0,1,0,0,0,0,1]),moss);
   proc('proc:plinth',R2.geo.box(1.15,0.32,1.15,1.4),R2.MAT.brick,m4yaw(px,pb-0.12,pz,rot,s),[0.75,0.78,0.74,1]);
   if(c===1)place('village/Prop_Vine'+(rr()<0.5?'4':'5'),m4yaw(px,pb+Math.min(ph,2.6),pz+0.44*s,0,0.8),[1,1,1,1]);}
  var la=rot+0.4;proc('proc:lintel',R2.geo.box(4.0,0.5,0.8,1.4),R2.MAT.brick,mul(m4yaw(x+0.3*s,b+0.02,z-0.2*s,la,s),m4trs([0,0,0],qaxis([0,0,1],0.08),[1,1,1])),moss);
  for(var k=0;k<6;k++){var bx=x+(rr()*2-1)*2.8*s,bz=z+(rr()*2-1)*2.8*s;place('village/Prop_Brick'+(1+(k%3)),m4yaw(bx,gh(bx,bz)+0.05,bz,rr()*6.28,0.9),[0.9,0.92,0.88,1]);}
  place('nature/Rock_Medium_'+(1+(idx%3)),m4yaw(x+2.7*s,gh(x+2.7*s,z+1.2*s)-0.3,z+1.2*s,rr()*6,0.55*s),[1,1,1,1]);};
 return S;};
// ---------- procedural meshes with real PBR materials (columns, plinths, lamp posts, portal rings, chests) ----------
R2.procModel=function(R,key,geo,mat){if(R.models[key])return R.models[key];var P=new Float32Array(geo.p),Nn=new Float32Array(geo.n),U=new Float32Array(geo.uv),I=P.length/3>65535?new Uint32Array(geo.i):new Uint16Array(geo.i);
 P.n=3;P.ct=5126;Nn.n=3;Nn.ct=5126;U.n=2;U.ct=5126;I.ct=I instanceof Uint32Array?5125:5123;var lo=[1e9,1e9,1e9],hi=[-1e9,-1e9,-1e9];for(var k=0;k<P.length;k+=3)for(var c=0;c<3;c++){lo[c]=Math.min(lo[c],P[k+c]);hi[c]=Math.max(hi[c],P[k+c]);}P.amin=lo;P.amax=hi;
 var images=[],m={name:key,base:mat.base||[1,1,1,1],metal:mat.metal==null?0:mat.metal,rough:mat.rough==null?1:mat.rough,emis:mat.emis||[0,0,0],alpha:mat.alpha||'OPAQUE',cutoff:0.5,ds:!!mat.ds,unlit:!!mat.unlit,foliage:!!mat.foliage,glass:false,extras:{}};
 function img(p){images.push({path:p});return{img:images.length-1};}if(mat.albedo)m.baseTex=img(mat.albedo);if(mat.normal)m.normTex=img(mat.normal);if(mat.arm){m.mrTex=img(mat.arm);m.occTex={img:m.mrTex.img};}
 var M={path:key,nodes:[{name:key,mesh:0,skin:-1,children:[],parent:-1,t:[0,0,0],r:[0,0,0,1],s:[1,1,1]}],meshes:[{name:key,prims:[{attrs:{POSITION:P,NORMAL:Nn,TEXCOORD_0:U},idx:I,mode:4,mat:0}]}],materials:[m],images:images,skins:[],anims:[],roots:[0],rest:[m4()],byName:{},bounds:[lo,hi]};
 M.byName[key]=0;var G=R.upload_model(M);G.path=key;R.models[key]=G.ready;return G.ready;};
R2.geo={
 column:function(r,h,seg,jag,seedv,tile){var rr=rng(seedv||7),p=[],n=[],uv=[],ix=[],rings=Math.max(2,Math.round(h/0.35));tile=tile||1.6;var tops=[];for(var s=0;s<=seg;s++)tops.push(h-(s===seg?tops[0]:rr()*jag));
  for(var j=0;j<=rings;j++)for(var s2=0;s2<=seg;s2++){var a=s2/seg*Math.PI*2,y=Math.min(j/rings*h,tops[s2%seg]),bulge=1+0.04*Math.sin(j*1.7+s2),c=Math.cos(a),sn=Math.sin(a);
   p.push(c*r*bulge,y,sn*r*bulge);n.push(c,0,sn);uv.push(s2/seg*Math.PI*2*r/tile,y/tile);}
  for(j=0;j<rings;j++)for(var s3=0;s3<seg;s3++){var a0=j*(seg+1)+s3,b0=a0+1,c0=a0+seg+1,d0=c0+1;ix.push(a0,c0,b0,b0,c0,d0);}
  var cb=p.length/3;p.push(0,h-jag*0.6,0);n.push(0,1,0);uv.push(0.5,0.5);for(var s4=0;s4<=seg;s4++){var a2=s4/seg*Math.PI*2;p.push(Math.cos(a2)*r,tops[s4%seg],Math.sin(a2)*r);n.push(0,1,0);uv.push(0.5+Math.cos(a2)*r/tile,0.5+Math.sin(a2)*r/tile);}
  for(s4=0;s4<seg;s4++)ix.push(cb,cb+1+s4+1,cb+1+s4);return{p:p,n:n,uv:uv,i:ix};},
 box:function(sx,sy,sz,tile){tile=tile||1.6;var p=[],n=[],uv=[],ix=[],F=[[0,0,1],[0,0,-1],[1,0,0],[-1,0,0],[0,1,0],[0,-1,0]],hs=[sx/2,sy/2,sz/2];
  F.forEach(function(f){var u=f[1]!==0?[1,0,0]:[0,1,0],v=cross(f,u),b=p.length/3;[[-1,-1],[1,-1],[1,1],[-1,1]].forEach(function(q){var x=(f[0]+u[0]*q[0]+v[0]*q[1])*hs[0],y=(f[1]+u[1]*q[0]+v[1]*q[1])*hs[1]+hs[1],z=(f[2]+u[2]*q[0]+v[2]*q[1])*hs[2];
   p.push(x,y,z);n.push(f[0],f[1],f[2]);var a=Math.abs(f[0])>0.5?[z,y]:Math.abs(f[1])>0.5?[x,z]:[x,y];uv.push(a[0]/tile,a[1]/tile);});ix.push(b,b+1,b+2,b,b+2,b+3);});return{p:p,n:n,uv:uv,i:ix};},
 torus:function(R0,r0,seg,sides,tile){tile=tile||1.6;var p=[],n=[],uv=[],ix=[];for(var j=0;j<=seg;j++)for(var i=0;i<=sides;i++){var a=j/seg*Math.PI*2,b=i/sides*Math.PI*2,cx=Math.cos(a),sx=Math.sin(a),cb=Math.cos(b),sb=Math.sin(b);
  p.push((R0+r0*cb)*cx,(R0+r0*cb)*sx,r0*sb);n.push(cb*cx,cb*sx,sb);uv.push(j/seg*R0*6.28/tile,i/sides*r0*6.28/tile);}
  for(j=0;j<seg;j++)for(var i2=0;i2<sides;i2++){var q=j*(sides+1)+i2,w=q+sides+1;ix.push(q,w,q+1,q+1,w,w+1);}return{p:p,n:n,uv:uv,i:ix};},
 lathe:function(prof,seg,tile){tile=tile||1;var p=[],n=[],uv=[],ix=[];for(var j=0;j<prof.length;j++)for(var s=0;s<=seg;s++){var a=s/seg*Math.PI*2,c=Math.cos(a),sn=Math.sin(a),pr=prof[j],q=prof[Math.min(prof.length-1,j+1)],o=prof[Math.max(0,j-1)];
  var dr=q[0]-o[0],dy=q[1]-o[1],l=Math.hypot(dr,dy)||1;p.push(c*pr[0],pr[1],sn*pr[0]);n.push(c*dy/l,-dr/l,sn*dy/l);uv.push(s/seg*3,pr[1]/tile);}
  for(j=0;j<prof.length-1;j++)for(var s2=0;s2<seg;s2++){var a0=j*(seg+1)+s2,c0=a0+seg+1;ix.push(a0,c0,a0+1,a0+1,c0,c0+1);}return{p:p,n:n,uv:uv,i:ix};}};
R2.MAT={brick:{albedo:'ground/castle_brick_07/castle_brick_07_diff_1k.jpg',normal:'ground/castle_brick_07/castle_brick_07_nor_gl_1k.jpg',arm:'ground/castle_brick_07/castle_brick_07_arm_1k.jpg',rough:1,metal:1},
 rock:{albedo:'ground/rocky_terrain_02/rocky_terrain_02_diff_1k.jpg',normal:'ground/rocky_terrain_02/rocky_terrain_02_nor_gl_1k.jpg',arm:'ground/rocky_terrain_02/rocky_terrain_02_arm_1k.jpg',rough:1,metal:1},
 wood:{albedo:'textures/village/T_WoodTrim_BaseColor.jpg',normal:'textures/village/T_WoodTrim_Normal.jpg',rough:0.8,metal:0},
 iron:{base:[0.08,0.08,0.09,1],metal:0.85,rough:0.45},gold:{base:[1.0,0.78,0.34,1],metal:1,rough:0.3}};
R2.GROUND={albedo:['ground/forest_ground_04/forest_ground_04_diff_1k.jpg','ground/rocky_terrain_02/rocky_terrain_02_diff_1k.jpg','ground/coast_sand_01/coast_sand_01_diff_1k.jpg','ground/snow_02/snow_02_diff_1k.jpg'],
 normal:['ground/forest_ground_04/forest_ground_04_nor_gl_1k.jpg','ground/rocky_terrain_02/rocky_terrain_02_nor_gl_1k.jpg','ground/coast_sand_01/coast_sand_01_nor_gl_1k.jpg','ground/snow_02/snow_02_nor_gl_1k.jpg'],
 arm:['ground/forest_ground_04/forest_ground_04_arm_1k.jpg','ground/rocky_terrain_02/rocky_terrain_02_arm_1k.jpg','ground/coast_sand_01/coast_sand_01_arm_1k.jpg','ground/snow_02/snow_02_arm_1k.jpg']};
// ---------- crafted props: lamp post, shrine brazier, treasure chest (base + lid), portal ring ----------
R2.PROP_DEFS={
 lamp_post:function(){return[R2.geo.lathe([[0.2,0],[0.2,0.08],[0.13,0.14],[0.06,0.22],[0.045,0.4],[0.045,2.3],[0.07,2.36],[0.09,2.4]],14),R2.MAT.iron];},
 lamp_cage:function(){return[R2.geo.lathe([[0.0,-0.02],[0.16,0.0],[0.2,0.05],[0.17,0.36],[0.21,0.4],[0.14,0.5],[0.03,0.62],[0.0,0.66]],8),R2.MAT.iron];},
 shrine_base:function(){return[R2.geo.box(1.3,0.36,1.3,1.4),R2.MAT.brick];},
 shrine_col:function(){return[R2.geo.column(0.34,0.95,14,0.0,11,1.2),R2.MAT.brick];},
 brazier:function(){return[R2.geo.lathe([[0.0,0.0],[0.12,0.0],[0.16,0.04],[0.46,0.22],[0.5,0.3],[0.44,0.32],[0.14,0.12],[0.0,0.1]],18),R2.MAT.iron];},
 chest_base:function(){return[R2.geo.box(1.0,0.55,0.66,0.9),R2.MAT.wood];},
 chest_lid:function(){return[R2.geo.box(1.02,0.24,0.68,0.9),R2.MAT.wood];},
 chest_band:function(){return[R2.geo.box(1.04,0.08,0.7,1),R2.MAT.gold];},
 portal_ring:function(){return[R2.geo.torus(1.55,0.28,40,10,1.4),R2.MAT.brick];},
 portal_base:function(){return[R2.geo.box(3.6,0.3,1.5,1.4),R2.MAT.brick];}};
R2.prop=function(R,name){var key='prop:'+name;if(!R.propG)R.propG={};if(R.propG[key])return R.propG[key];if(!R.propG['p'+key]){R.propG['p'+key]=1;var d=R2.PROP_DEFS[name]();R2.procModel(R,key,d[0],d[1]).then(function(G){R.propG[key]=G;});}return null;};
