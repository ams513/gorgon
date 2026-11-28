// ========== Renderer2: HDR forward PBR, CSM soft shadows (PCSS on Ultra), IBL bake, SSAO, bloom, sun shafts, eye adaptation,
// ========== ACES / AgX tone curves, split toning, FXAA+CAS / TAA, aerial perspective, mountain skyline, water, FX ==========
var CUBE_FACES=[[0,0,-1,0,-1,0,1,0,0],[0,0,1,0,-1,0,-1,0,0],[1,0,0,0,0,1,0,1,0],[1,0,0,0,0,-1,0,-1,0],[1,0,0,0,-1,0,0,0,1],[-1,0,0,0,-1,0,0,0,-1]];
function fxCube(){var F=[[0,0,1],[0,0,-1],[1,0,0],[-1,0,0],[0,1,0],[0,-1,0]],out=[];F.forEach(function(n){var u=n[1]!==0?[1,0,0]:[0,1,0],v=cross(n,u);function c(a,b){return[(n[0]+u[0]*a+v[0]*b)*0.5,(n[1]+u[1]*a+v[1]*b)*0.5,(n[2]+u[2]*a+v[2]*b)*0.5];}
 [c(-1,-1),c(1,-1),c(1,1),c(-1,-1),c(1,1),c(-1,1)].forEach(function(p){out.push(p[0],p[1],p[2],n[0],n[1],n[2]);});});return new Float32Array(out);}
function fxSphere(seg,rings){var o=[];for(var j=0;j<rings;j++)for(var i=0;i<seg;i++){var p=function(a,b){var th=b/rings*Math.PI,ph=a/seg*Math.PI*2;return[Math.sin(th)*Math.cos(ph)*0.5,Math.cos(th)*0.5,Math.sin(th)*Math.sin(ph)*0.5];};
 var q=[p(i,j),p(i+1,j),p(i+1,j+1),p(i,j),p(i+1,j+1),p(i,j+1)];q.forEach(function(v){var l=Math.hypot(v[0],v[1],v[2])||1;o.push(v[0],v[1],v[2],v[0]/l,v[1]/l,v[2]/l);});}return new Float32Array(o);}
function fxCyl(seg){var o=[];for(var i=0;i<seg;i++){var a0=i/seg*Math.PI*2,a1=(i+1)/seg*Math.PI*2,c0=Math.cos(a0)*0.5,s0=Math.sin(a0)*0.5,c1=Math.cos(a1)*0.5,s1=Math.sin(a1)*0.5;
 [[c0,-0.5,s0],[c1,-0.5,s1],[c1,0.5,s1],[c0,-0.5,s0],[c1,0.5,s1],[c0,0.5,s0]].forEach(function(v){var l=Math.hypot(v[0],v[2])||1;o.push(v[0],v[1],v[2],v[0]/l,0,v[2]/l);});
 o.push(0,0.5,0,0,1,0,c1,0.5,s1,0,1,0,c0,0.5,s0,0,1,0);o.push(0,-0.5,0,0,-1,0,c0,-0.5,s0,0,-1,0,c1,-0.5,s1,0,-1,0);}return new Float32Array(o);}
function fxCone(seg){var o=[];for(var i=0;i<seg;i++){var a0=i/seg*Math.PI*2,a1=(i+1)/seg*Math.PI*2,p0=[Math.cos(a0)*0.5,-0.5,Math.sin(a0)*0.5],p1=[Math.cos(a1)*0.5,-0.5,Math.sin(a1)*0.5],t=[0,0.5,0];
 var n=norm(cross(sub(p1,p0),sub(t,p0)));n=[-n[0],-n[1],-n[2]];if(n[1]<0)n=[-n[0],-n[1],-n[2]];[p0,t,p1].forEach(function(v){o.push(v[0],v[1],v[2],n[0],n[1],n[2]);});o.push(0,-0.5,0,0,-1,0,p0[0],p0[1],p0[2],0,-1,0,p1[0],p1[1],p1[2],0,-1,0);}return new Float32Array(o);}
function fxGem(){var T=[0,0.5,0],B=[0,-0.5,0],e=[];for(var i=0;i<6;i++){var a=i/6*Math.PI*2;e.push([Math.cos(a)*0.5,0.05,Math.sin(a)*0.5]);}var o=[];
 for(var k=0;k<6;k++){var a1=e[k],b1=e[(k+1)%6];[[a1,T,b1],[b1,B,a1]].forEach(function(tr){var n=norm(cross(sub(tr[1],tr[0]),sub(tr[2],tr[0])));tr.forEach(function(v){o.push(v[0],v[1],v[2],n[0],n[1],n[2]);});});}return new Float32Array(o);}
var FX_MESHES=['cube','cyl','cone','pyr','rock','sphere','gem'];
function Renderer2(canvas){
 var gl=canvas.getContext('webgl2',{antialias:false,alpha:false,depth:false,stencil:false,preserveDrawingBuffer:false,powerPreference:'high-performance'});if(!gl)return null;
 var R={gl:gl,q:'medium',Q:R2.QUALITY.medium,scale:1,fbW:0,fbH:0,frame:0,b:{},stats:{draws:0,tris:0}};
 R.hdr=!!gl.getExtension('EXT_color_buffer_float');gl.getExtension('OES_texture_float_linear');R.gpu=R2.gpuInfo(gl);R.tex=R2.Textures(gl);
 R.rawS=gl.createSampler();gl.samplerParameteri(R.rawS,gl.TEXTURE_COMPARE_MODE,gl.NONE);gl.samplerParameteri(R.rawS,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.samplerParameteri(R.rawS,gl.TEXTURE_MAG_FILTER,gl.NEAREST);
 R.black=R2.tex2d(gl,1,1,gl.RGBA8,gl.RGBA,gl.UNSIGNED_BYTE,{data:new Uint8Array([0,0,0,255])});
 var progs={};function prog(key,vs,fs,defs){if(!progs[key])progs[key]=R2.compile(gl,vs,fs,defs);return progs[key];}
 function lite(d){return R.q==='low'?d.concat(['LITE']):R.Q.pcss?d.concat(['PCSS']):d;}   // Low: LITE shaders; Ultra: PCSS shadows
 function meshProg(defs){defs=lite(defs);var k='mesh:'+defs.join(',');return prog(k,VS_MESH,GL_COMMON+FS_MESH,defs);}
 function shadowProg(defs){var k='shadow:'+defs.join(',');return prog(k,VS_MESH,FS_SHADOW,defs);}
 // ---------- static geometry ----------
 R.fsq=gl.createVertexArray();gl.bindVertexArray(R.fsq);var qb=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,qb);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,2,gl.FLOAT,false,8,0);gl.bindVertexArray(null);
 function fsq(){gl.bindVertexArray(R.fsq);gl.drawArrays(gl.TRIANGLES,0,6);}
 // FX primitives (instanced, the classic API: add/beginStatic/endStatic/beginDynamic/upload)
 var geos={cube:fxCube(),cyl:fxCyl(12),cone:fxCone(10),pyr:fxCone(4),rock:fxSphere(7,5),sphere:fxSphere(16,10),gem:fxGem()},CAP=4096;
 FX_MESHES.forEach(function(k){var m={verts:geos[k].length/6,cap:CAP,data:new Float32Array(CAP*13),n:0,sn:0};m.vao=gl.createVertexArray();gl.bindVertexArray(m.vao);
  var vb=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,vb);gl.bufferData(gl.ARRAY_BUFFER,geos[k],gl.STATIC_DRAW);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,3,gl.FLOAT,false,24,0);gl.enableVertexAttribArray(1);gl.vertexAttribPointer(1,3,gl.FLOAT,false,24,12);
  m.ib=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,m.ib);gl.bufferData(gl.ARRAY_BUFFER,m.data.byteLength,gl.DYNAMIC_DRAW);
  [[2,3,0],[3,3,12],[4,3,24],[5,4,36]].forEach(function(a){gl.enableVertexAttribArray(a[0]);gl.vertexAttribPointer(a[0],a[1],gl.FLOAT,false,52,a[2]);gl.vertexAttribDivisor(a[0],1);});R.b[k]=m;});
 gl.bindVertexArray(null);
 R.add=function(k,x,y,z,sx,sy,sz,c,emit,sway,rot,spec){var m=R.b[k]||R.b.cube;if(m.n>=m.cap){if(m.cap>=262144)return;var nd=new Float32Array(m.cap*2*13);nd.set(m.data);m.data=nd;m.cap*=2;m.realloc=true;}
  var d=m.data,o=m.n*13;d[o]=x;d[o+1]=y;d[o+2]=z;d[o+3]=sx;d[o+4]=sy;d[o+5]=sz;d[o+6]=c[0];d[o+7]=c[1];d[o+8]=c[2];d[o+9]=emit||0;d[o+10]=sway||0;d[o+11]=rot||0;d[o+12]=spec==null?0.15:spec;m.n++;};
 R.beginStatic=function(){FX_MESHES.forEach(function(k){R.b[k].n=0;});};
 function realloc(m){gl.bindBuffer(gl.ARRAY_BUFFER,m.ib);if(m.realloc){gl.bufferData(gl.ARRAY_BUFFER,m.data.byteLength,gl.DYNAMIC_DRAW);m.realloc=false;return true;}return false;}
 R.endStatic=function(){FX_MESHES.forEach(function(k){var m=R.b[k];m.sn=m.n;realloc(m);gl.bindBuffer(gl.ARRAY_BUFFER,m.ib);gl.bufferSubData(gl.ARRAY_BUFFER,0,m.data,0,m.n*13);});};
 R.beginDynamic=function(){FX_MESHES.forEach(function(k){R.b[k].n=R.b[k].sn;});R.dyn=[];R.skin=[];R.skinRows=[];R.parts=0;};
 R.upload=function(){FX_MESHES.forEach(function(k){var m=R.b[k];if(realloc(m)){gl.bufferSubData(gl.ARRAY_BUFFER,0,m.data,0,m.n*13);return;}if(m.n>m.sn){gl.bindBuffer(gl.ARRAY_BUFFER,m.ib);gl.bufferSubData(gl.ARRAY_BUFFER,m.sn*52,m.data,m.sn*13,(m.n-m.sn)*13);}});
  if(R.parts){gl.bindBuffer(gl.ARRAY_BUFFER,R.pb);gl.bufferData(gl.ARRAY_BUFFER,R.pdata.subarray(0,R.parts*8),gl.DYNAMIC_DRAW);}};
 R.count=function(){var c=0;FX_MESHES.forEach(function(k){c+=R.b[k].n;});for(var k2 in R.batches)c+=R.batches[k2].n;return c+(R.dyn?R.dyn.length:0)+(R.skin?R.skin.length:0);};
 // soft particles (additive billboards)
 R.pdata=new Float32Array(8*8192);R.parts=0;R.pvao=gl.createVertexArray();gl.bindVertexArray(R.pvao);var pq=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,pq);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,1,1,-1,-1,1,1,-1,1]),gl.STATIC_DRAW);
 gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,2,gl.FLOAT,false,8,0);R.pb=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,R.pb);gl.bufferData(gl.ARRAY_BUFFER,R.pdata.byteLength,gl.DYNAMIC_DRAW);
 gl.enableVertexAttribArray(2);gl.vertexAttribPointer(2,4,gl.FLOAT,false,32,0);gl.vertexAttribDivisor(2,1);gl.enableVertexAttribArray(3);gl.vertexAttribPointer(3,4,gl.FLOAT,false,32,16);gl.vertexAttribDivisor(3,1);gl.bindVertexArray(null);
 R.part=function(x,y,z,s,c,a){if(R.parts>=8192)return;var d=R.pdata,o=R.parts*8;d[o]=x;d[o+1]=y;d[o+2]=z;d[o+3]=s;d[o+4]=c[0];d[o+5]=c[1];d[o+6]=c[2];d[o+7]=a==null?1:a;R.parts++;};
 // ---------- glTF models on the GPU ----------
 R.models={};
 R.texReg=[];
 R.loadMatTex=function(job,max){job.want=max;var tr=job.tr,im=job.im,s=job.s;
  var p=im.path?R.tex.get(im.path,s[3],im.mime,tr.wrapS,tr.wrapT,max):R.tex.fromBytes(im.bytes,im.mime,s[3],tr.wrapS,tr.wrapT,max);
  return p.then(function(t){if(job.want!==max)return;job.max=max;job.g.tex[s[1]]=t;job.g.maps|=s[2];}).catch(function(e){if(window.console)console.warn('texture',im.path||'embedded',e.message);});};
 R.upgradeTextures=function(){R.texReg.forEach(function(j){if(j.want!==0)R.loadMatTex(j,0);});
  if(R._groundReq&&(!R.groundSet||R.groundSet.lite)&&!R._groundFull){R._groundFull=true;R.setGround(R._groundReq.paths,R._groundReq.full);}};
 R.upload_model=function(M){var G={M:M,prims:[],ready:null,skinned:false},texJobs=[];
  var binds=null;function attr(a,loc,vao){if(!a)return;var b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,a,gl.STATIC_DRAW);gl.enableVertexAttribArray(loc);
   var t={5126:gl.FLOAT,5121:gl.UNSIGNED_BYTE,5123:gl.UNSIGNED_SHORT,5120:gl.BYTE,5122:gl.SHORT,5125:gl.UNSIGNED_INT}[a.ct];gl.vertexAttribPointer(loc,a.n,t,loc===4?false:a.norm,0,0);binds.push([loc,b,a.n,t,loc===4?false:a.norm]);}
  var mats=M.materials.map(function(m){var g={src:m,maps:0,base:m.base.slice(),emis:m.emis.slice(),metal:m.metal,rough:m.rough,normS:(m.normTex&&m.normTex.scale!=null)?m.normTex.scale:1,occS:(m.occTex&&m.occTex.strength!=null)?m.occTex.strength:1,
    cut:m.cutoff,alpha:m.alpha,ds:m.ds,unlit:m.unlit,foliage:m.foliage,trans:m.foliage?0.6:0,tex:{}};
   [['baseTex','uBase',1,true],['mrTex','uMR',2,false],['normTex','uNorm',4,false],['occTex','uOcc',8,false],['emisTex','uEmis',16,true]].forEach(function(s){var tr=m[s[0]];if(!tr||tr.img==null)return;var im=M.images[tr.img];if(!im)return;
    var job={g:g,s:s,tr:tr,im:im,want:-1};R.texReg.push(job);if(R.tex.lite&&(s[2]&14))return;   // Low: no MR / normal / occlusion maps
    texJobs.push(R.loadMatTex(job,R.tex.lite?R.tex.max:0));});return g;});
  var def={src:{name:'default'},maps:0,base:[0.8,0.8,0.8,1],emis:[0,0,0],metal:0,rough:0.8,normS:1,occS:1,cut:0.5,alpha:'OPAQUE',ds:false,unlit:false,foliage:false,trans:0,tex:{}};
  M.meshes.forEach(function(me,mi){me.gp=me.prims.map(function(p){if(p.mode!==4||!p.attrs.POSITION)return null;var vao=gl.createVertexArray();gl.bindVertexArray(vao);binds=[];
   attr(p.attrs.POSITION,0);attr(p.attrs.NORMAL,1);attr(p.attrs.TEXCOORD_0,2);attr(p.attrs.COLOR_0,3);var sk=!!(p.attrs.JOINTS_0&&p.attrs.WEIGHTS_0);if(sk){attr(p.attrs.JOINTS_0,4);attr(p.attrs.WEIGHTS_0,5);G.skinned=true;}
   if(!p.attrs.NORMAL)gl.vertexAttrib3f(1,0,1,0);if(!p.attrs.TEXCOORD_0)gl.vertexAttrib2f(2,0,0);if(!p.attrs.COLOR_0)gl.vertexAttrib4f(3,1,1,1,1);else if(p.attrs.COLOR_0.n===3){}
   var ib=null,it=0,cnt=p.attrs.POSITION.length/3;if(p.idx){ib=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,ib);var ix=p.idx;if(ix instanceof Uint8Array)ix=new Uint16Array(ix);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,ix,gl.STATIC_DRAW);it=ix instanceof Uint32Array?gl.UNSIGNED_INT:gl.UNSIGNED_SHORT;cnt=ix.length;}
   gl.bindVertexArray(null);var P=p.attrs.POSITION,lo=P.amin||[0,0,0],hi=P.amax||[1,1,1];
   var gp={vao:vao,idx:!!ib,ib:ib,binds:binds,it:it,count:cnt,mat:p.mat>=0?mats[p.mat]:def,skin:sk,mesh:mi,lo:lo,hi:hi,colors:!!p.attrs.COLOR_0,instKey:null};G.prims.push(gp);return gp;});});
  G.drawNodes=[];M.nodes.forEach(function(n,i){if(n.mesh>=0)G.drawNodes.push(i);});
  G.ready=R.tex.lite?Promise.resolve(G):Promise.all(texJobs).then(function(){return G;});return G;};   // Low: draw now, textures stream in
 R.model=function(path){if(!R.models[path])R.models[path]=R2.loadGLTF(path).then(function(M){var G=R.upload_model(M);G.path=path;return G;});return R.models[path];};
 // ---------- static instanced batches (scenery) ----------
 R.batches={};
 R.clearStatic=function(){for(var k in R.batches){var b=R.batches[k];if(b.buf)gl.deleteBuffer(b.buf);if(b.vao)gl.deleteVertexArray(b.vao);}R.batches={};};
 R.addStatic=function(G,mat,tint,opt){opt=opt||{};G.drawNodes.forEach(function(ni){var n=G.M.nodes[ni],gm=mul(mat,G.M.rest[ni]);G.M.meshes[n.mesh].gp.forEach(function(gp,pi){if(!gp||gp.skin)return;
  var key=G.path+'#'+ni+'#'+pi+(opt.fade?'#f':'')+(opt.tintAll?'#t':''),b=R.batches[key];if(!b){b=R.batches[key]={gp:gp,G:G,data:new Float32Array(20*16),n:0,dirty:true,fade:opt.fade||0,shadow:opt.shadow!==false,windH:opt.windH||0,wind:opt.wind!=null?opt.wind:(gp.mat.foliage?1:0),tintAll:!!opt.tintAll};}
  if(b.n*20>=b.data.length){var nd=new Float32Array(b.data.length*2);nd.set(b.data);b.data=nd;}var o=b.n*20;for(var i=0;i<16;i++)b.data[o+i]=gm[i];var t=tint||[1,1,1,1];b.data[o+16]=t[0];b.data[o+17]=t[1];b.data[o+18]=t[2];b.data[o+19]=t[3]==null?1:t[3];b.n++;b.dirty=true;});});};
 // Low preset: draw only the instances near and in front of the camera (software renderers are vertex-bound)
 function cullBatch(b,F){var D=R.Q.cullDist*(b.gp.count>6000?0.5:b.gp.count>1500?0.75:1),e=F.eye,f=F.fwd||[0,0,-1];var key=Math.round(e[0]/2)+','+Math.round(e[2]/2)+','+Math.round(f[0]*8)+','+Math.round(f[2]*8);
  if(b.ck===key)return;b.ck=key;if(!b.vd||b.vd.length<b.data.length)b.vd=new Float32Array(b.data.length);var n=0,d=b.data,vd=b.vd,D2=D*D;
  for(var i=0;i<b.n;i++){var o=i*20,dx=d[o+12]-e[0],dz=d[o+14]-e[2],q=dx*dx+dz*dz;if(q>D2)continue;if(q>144&&dx*f[0]+dz*f[2]<-10)continue;for(var j=0;j<20;j++)vd[n*20+j]=d[o+j];n++;}
  b.vn=n;gl.bindBuffer(gl.ARRAY_BUFFER,b.buf);gl.bufferSubData(gl.ARRAY_BUFFER,0,vd.subarray(0,n*20));}
 function batchVAO(b){if(!b.dirty)return;b.dirty=false;b.ck=null;var gp=b.gp;if(!b.buf){b.buf=gl.createBuffer();}gl.bindBuffer(gl.ARRAY_BUFFER,b.buf);gl.bufferData(gl.ARRAY_BUFFER,b.data.subarray(0,b.n*20),gl.STATIC_DRAW);
  if(!b.vao){b.vao=gl.createVertexArray();gl.bindVertexArray(b.vao);(gp.binds||[]).forEach(function(x){gl.bindBuffer(gl.ARRAY_BUFFER,x[1]);gl.enableVertexAttribArray(x[0]);gl.vertexAttribPointer(x[0],x[2],x[3],x[4],0,0);});
   if(gp.ib)gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,gp.ib);gl.bindBuffer(gl.ARRAY_BUFFER,b.buf);for(var c=0;c<5;c++){gl.enableVertexAttribArray(8+c);gl.vertexAttribPointer(8+c,4,gl.FLOAT,false,80,c*16);gl.vertexAttribDivisor(8+c,1);}gl.bindVertexArray(null);}}
 // per-frame dynamic draws
 R.dyn=[];R.skin=[];
 R.drawModel=function(G,mat,tint,opt){if(!G||!G.M)return;opt=opt||{};G.drawNodes.forEach(function(ni){var n=G.M.nodes[ni];if(opt.hide&&opt.hide[n.name])return;var gm=mul(mat,(opt.pose&&opt.pose[ni])||G.M.rest[ni]);
  G.M.meshes[n.mesh].gp.forEach(function(gp){if(gp&&!gp.skin)R.dyn.push({gp:gp,m:gm,tint:tint||[1,1,1,1],shadow:opt.shadow!==false,emis:opt.emis,tintAll:!!opt.tintAll});});});};
 R.drawSkinned=function(G,mat,joints,nj,tint,opt){opt=opt||{};if(R.skinRows.length>=R.jtH)return;var row=R.skinRows.length,used=false;
  G.drawNodes.forEach(function(ni){var n=G.M.nodes[ni];if(opt.hide&&opt.hide[n.name])return;if(opt.show&&!opt.show[n.name])return;
   G.M.meshes[n.mesh].gp.forEach(function(gp){if(!gp)return;if(gp.skin&&n.skin>=0){used=true;R.skin.push({gp:gp,m:mat,row:row,tint:tint||[1,1,1,1]});}
    else if(opt.pose)R.dyn.push({gp:gp,m:mul(mat,opt.pose[ni]),tint:tint||[1,1,1,1],shadow:true});});});
  if(used)R.skinRows.push({joints:joints[0],nj:nj});};
 // joint texture: one row per skinned draw, 4 texels per joint
 R.jtW=4*80;R.jtH=128;R.skinRows=[];R.jt=R2.tex2d(gl,R.jtW,R.jtH,gl.RGBA32F,gl.RGBA,gl.FLOAT,{filter:gl.NEAREST});R.jdata=new Float32Array(R.jtW*R.jtH*4);
 // ---------- terrain ----------
 R.setTerrain=function(T){if(R.ter){gl.deleteVertexArray(R.ter.vao);}var t={n:T.idx.length,vao:gl.createVertexArray()};gl.bindVertexArray(t.vao);
  function a(arr,loc,n){var b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,arr,gl.STATIC_DRAW);gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,n,gl.FLOAT,false,0,0);}
  a(T.pos,0,3);a(T.nor,1,3);a(T.splat,3,4);a(T.extra,6,4);var eb=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,eb);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,T.idx,gl.STATIC_DRAW);gl.bindVertexArray(null);R.ter=t;};
 R.groundSet=null;R.setGround=function(paths,size){var lite=!!R.tex.lite;R._groundReq={paths:paths,full:size};var sz=lite?Math.min(size,R.tex.max||256):size;
  var jobs=lite?[R.tex.gate().then(function(){return R.tex.array(paths.albedo,true,sz);})]:[R.tex.array(paths.albedo,true,size),R.tex.array(paths.normal,false,size),R.tex.array(paths.arm,false,size)];
  Promise.all(jobs).then(function(t){if(lite&&R.groundSet&&!R.groundSet.lite)return;R.groundSet=lite?{a:t[0],n:t[0],r:t[0],lite:true}:{a:t[0],n:t[1],r:t[2]};}).catch(function(e){if(window.console)console.warn('ground textures',e.message);});};
 // ---------- render targets ----------
 var T={};function delT(){for(var k in T){var o=T[k];if(!o)continue;if(o.isF){gl.deleteFramebuffer(o);}else gl.deleteTexture(o);}T={};}
 function ctex(w,h,hdr,filter){return R2.tex2d(gl,w,h,hdr&&R.hdr?gl.RGBA16F:gl.RGBA8,gl.RGBA,hdr&&R.hdr?gl.HALF_FLOAT:gl.UNSIGNED_BYTE,{filter:filter});}
 function dtex(w,h){return R2.tex2d(gl,w,h,gl.DEPTH_COMPONENT24,gl.DEPTH_COMPONENT,gl.UNSIGNED_INT,{filter:gl.NEAREST});}
 function F(name,color,depth){var f=R2.fbo(gl,color,depth);f.isF=true;T[name]=f;return f;}
 R.resize=function(w,h){if(R.fbW===w&&R.fbH===h&&R.fbQ===R.q)return;R.fbW=w;R.fbH=h;R.fbQ=R.q;delT();
  T.sceneC=ctex(w,h,true);T.sceneD=dtex(w,h);F('scene',T.sceneC,T.sceneD);if(!T.scene.ok&&R.hdr){R.hdr=false;R.fbW=0;return R.resize(w,h);}
  T.copyC=ctex(w,h,true);T.copyD=dtex(w,h);F('copy',T.copyC,T.copyD);
  var hw=Math.max(1,w>>1),hh=Math.max(1,h>>1);T.ao1=R2.tex2d(gl,hw,hh,gl.RGBA8,gl.RGBA,gl.UNSIGNED_BYTE);T.ao2=R2.tex2d(gl,hw,hh,gl.RGBA8,gl.RGBA,gl.UNSIGNED_BYTE);F('aoF1',T.ao1);F('aoF2',T.ao2);
  R.bloom=[];var bw=hw,bh=hh;for(var i=0;i<R.Q.bloomMips;i++){var bt=ctex(Math.max(1,bw),Math.max(1,bh),true);T['bl'+i]=bt;R.bloom.push({t:bt,f:F('blF'+i,bt),w:Math.max(1,bw),h:Math.max(1,bh)});bw>>=1;bh>>=1;}
  if(R.hdr){T.lum=R2.tex2d(gl,64,64,gl.RG16F,gl.RG,gl.HALF_FLOAT,{filter:gl.LINEAR_MIPMAP_LINEAR});F('lumF',T.lum);T.ae0=ctex(1,1,true,gl.NEAREST);T.ae1=ctex(1,1,true,gl.NEAREST);F('ae0F',T.ae0);F('ae1F',T.ae1);R.aeI=0;R.aeReset=true;}
  var sw4=Math.max(1,w>>2),sh4=Math.max(1,h>>2);T.shA=ctex(sw4,sh4,true);T.shB=ctex(sw4,sh4,true);F('shAF',T.shA);F('shBF',T.shB);R.shW=sw4;R.shH=sh4;
  T.ldrA=ctex(w,h,false);F('ldrAF',T.ldrA);T.ldrB=ctex(w,h,false);F('ldrBF',T.ldrB);T.hist=ctex(w,h,false);F('histF',T.hist);R.taaReset=true;R._T=T;};
 // shadow atlas (2x2 tiles of cascades)
 R.shadowSize=0;R.setShadow=function(sz){if(R.shadowSize===sz)return;if(R.stex){gl.deleteTexture(R.stex);gl.deleteFramebuffer(R.sfb);}R.shadowSize=sz;var s=Math.max(1,sz);
  var t=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,t);gl.texStorage2D(gl.TEXTURE_2D,1,gl.DEPTH_COMPONENT24,s,s);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_COMPARE_MODE,gl.COMPARE_REF_TO_TEXTURE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_COMPARE_FUNC,gl.LEQUAL);
  var f=gl.createFramebuffer();gl.bindFramebuffer(gl.FRAMEBUFFER,f);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.DEPTH_ATTACHMENT,gl.TEXTURE_2D,t,0);gl.bindFramebuffer(gl.FRAMEBUFFER,null);R.stex=t;R.sfb=f;};
 // ---------- environment (IBL) ----------
 function cubeTex(res,mips){var t=gl.createTexture();gl.bindTexture(gl.TEXTURE_CUBE_MAP,t);var lv=mips?Math.floor(Math.log2(res))+1:1;gl.texStorage2D(gl.TEXTURE_CUBE_MAP,lv,R.hdr?gl.RGBA16F:gl.RGBA8,res,res);
  gl.texParameteri(gl.TEXTURE_CUBE_MAP,gl.TEXTURE_MIN_FILTER,mips?gl.LINEAR_MIPMAP_LINEAR:gl.LINEAR);gl.texParameteri(gl.TEXTURE_CUBE_MAP,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  [gl.TEXTURE_WRAP_S,gl.TEXTURE_WRAP_T,gl.TEXTURE_WRAP_R].forEach(function(w){gl.texParameteri(gl.TEXTURE_CUBE_MAP,w,gl.CLAMP_TO_EDGE);});t.res=res;t.lv=lv;return t;}
 R.cubeF=gl.createFramebuffer();R.env=null;R.envKey='';R.hdris={};
 R.loadHDRI=function(path){if(!R.hdris[path])R.hdris[path]=R.tex.gate().then(function(){return R2.fetchBytes(path);}).then(function(b){var H=R2.parseHDR(b);var t=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,t);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);
  gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB16F,H.w,H.h,0,gl.RGB,gl.FLOAT,H.data);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.REPEAT);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);H.tex=t;H.data=null;return H;});return R.hdris[path];};
 function skyU(P,S){var on=S.mode==='hdri'&&S.hdri;gl.uniform1i(P.uSkyMode,on?1:0);if(on){gl.activeTexture(gl.TEXTURE7);gl.bindTexture(gl.TEXTURE_2D,S.hdri.tex);gl.uniform1i(P.uHdri,7);
   gl.activeTexture(gl.TEXTURE10);gl.bindTexture(gl.TEXTURE_2D,(S.hdri2||S.hdri).tex);gl.uniform1i(P.uHdri2,10);}
  gl.uniform1f(P.uHdriMix,S.mix||0);gl.uniform1f(P.uHdriRot,S.rot||0);gl.uniform1f(P.uHdriRot2,S.rot2||0);gl.uniform1f(P.uHdriK,S.k||1);gl.uniform1f(P.uHdriK2,S.k2||S.k||1);
  gl.uniform3fv(P.uSkyTint,S.tint||[1,1,1]);gl.uniform1f(P.uBgClamp,S.bgClamp||65000);gl.uniform3fv(P.uDiscDir,S.discDir||[0,1,0]);gl.uniform3fv(P.uDiscCol,S.discCol||[0,0,0]);gl.uniform1f(P.uStars,S.stars||0);
  gl.uniform3fv(P.uZen,S.zen||[0.2,0.35,0.7]);gl.uniform3fv(P.uHor,S.hor||[0.7,0.75,0.8]);gl.uniform3fv(P.uSkySun,S.sunCol||[1,1,1]);gl.uniform3fv(P.uSkySunDir,S.sunDir||[0,1,0]);
  gl.uniform1f(P.uNight,S.night||0);gl.uniform1f(P.uAur,S.aur||0);gl.uniform1f(P.uSkyTime,S.time||0);gl.uniform1f(P.uClampK,S.clampK||65000);}
 R.bakeEnv=function(S){var res=R.Q.env;if(!R.env||R.env.res!==res){if(R.env){gl.deleteTexture(R.env.base);gl.deleteTexture(R.env.spec);gl.deleteTexture(R.env.irr);}R.env={res:res,base:cubeTex(res,true),spec:cubeTex(res,true),irr:cubeTex(32,false)};}
  var E=R.env;gl.disable(gl.DEPTH_TEST);gl.disable(gl.BLEND);gl.bindFramebuffer(gl.FRAMEBUFFER,R.cubeF);
  var P=prog('cube',VS_FSQ,FS_CUBE);gl.useProgram(P.p);var S2={};for(var k in S)S2[k]=S[k];S2.clampK=S.mode==='hdri'?40:30;skyU(P,S2);
  for(var f=0;f<6;f++){gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_CUBE_MAP_POSITIVE_X+f,E.base,0);gl.viewport(0,0,res,res);gl.uniformMatrix3fv(P.uFace,false,CUBE_FACES[f]);fsq();}
  gl.bindTexture(gl.TEXTURE_CUBE_MAP,E.base);gl.generateMipmap(gl.TEXTURE_CUBE_MAP);
  var PF=prog('pref',VS_FSQ,FS_PREF);gl.useProgram(PF.p);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_CUBE_MAP,E.base);gl.uniform1i(PF.uSrc,0);gl.uniform1f(PF.uSrcRes,res);
  var mips=Math.min(6,E.spec.lv);E.mips=mips-1;for(var m=0;m<mips;m++){var r=Math.max(1,res>>m);gl.uniform1f(PF.uRough,m/(mips-1));gl.uniform1i(PF.uN,m===0?1:R.Q.envSamples);
   for(var f2=0;f2<6;f2++){gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_CUBE_MAP_POSITIVE_X+f2,E.spec,m);gl.viewport(0,0,r,r);gl.uniformMatrix3fv(PF.uFace,false,CUBE_FACES[f2]);fsq();}}
  gl.bindTexture(gl.TEXTURE_CUBE_MAP,E.spec);gl.texParameteri(gl.TEXTURE_CUBE_MAP,gl.TEXTURE_MAX_LEVEL,mips-1);
  var PI2=prog('irr',VS_FSQ,FS_IRR);gl.useProgram(PI2.p);gl.bindTexture(gl.TEXTURE_CUBE_MAP,E.base);gl.uniform1i(PI2.uSrc,0);gl.uniform1f(PI2.uMip,Math.max(0,Math.log2(res)-4));
  for(var f3=0;f3<6;f3++){gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_CUBE_MAP_POSITIVE_X+f3,E.irr,0);gl.viewport(0,0,32,32);gl.uniformMatrix3fv(PI2.uFace,false,CUBE_FACES[f3]);fsq();}
  gl.bindFramebuffer(gl.FRAMEBUFFER,null);E.baked=true;};
 // Low (ibl:0): no image-based lighting bake. A 4x4-per-face cube holds a flat sky/ground ambient so every shader keeps
 // its inputs; the colours come from the procedural sky or from the HDRI's average above / below the horizon.
 R.ambient=function(S){var sky,gnd;S=S||{};
  if(S.mode==='hdri'&&S.hdri&&S.hdri.up){var k=S.k||1,t=S.tint||[1,1,1],up=scl3(S.hdri.up,k),dn=scl3(S.hdri.dn,k);
   if(S.hdri2&&S.hdri2.up&&S.mix>0){var k2=S.k2||k;up=mix3(up,scl3(S.hdri2.up,k2),S.mix);dn=mix3(dn,scl3(S.hdri2.dn,k2),S.mix);}
   sky=[up[0]*t[0],up[1]*t[1],up[2]*t[2]];gnd=[dn[0]*t[0],dn[1]*t[1],dn[2]*t[2]];}
  else{var z=S.zen||[0.2,0.35,0.7],h=S.hor||[0.7,0.75,0.8];sky=mix3(h,z,0.5);gnd=scl3(h,0.45);}
  return{sky:sky,gnd:gnd};};
 R.flatEnv=function(S){var A=R.ambient(S),N=4,E=R.env;
  if(!E||!E.flat){if(E){gl.deleteTexture(E.base);gl.deleteTexture(E.spec);gl.deleteTexture(E.irr);}var t=gl.createTexture();gl.bindTexture(gl.TEXTURE_CUBE_MAP,t);gl.texStorage2D(gl.TEXTURE_CUBE_MAP,1,gl.RGBA16F,N,N);
   gl.texParameteri(gl.TEXTURE_CUBE_MAP,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_CUBE_MAP,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
   [gl.TEXTURE_WRAP_S,gl.TEXTURE_WRAP_T,gl.TEXTURE_WRAP_R].forEach(function(w){gl.texParameteri(gl.TEXTURE_CUBE_MAP,w,gl.CLAMP_TO_EDGE);});E=R.env={res:-1,flat:true,base:t,spec:t,irr:t,mips:0};}
  gl.bindTexture(gl.TEXTURE_CUBE_MAP,E.irr);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);var d=new Float32Array(N*N*4);
  for(var f=0;f<6;f++){var M=CUBE_FACES[f];for(var j=0;j<N;j++)for(var i=0;i<N;i++){var x=(i+0.5)/N*2-1,y=(j+0.5)/N*2-1,dx=M[0]*x+M[3]*y+M[6],dy=M[1]*x+M[4]*y+M[7],dz=M[2]*x+M[5]*y+M[8];
    var w=Math.min(1,Math.max(0,dy/Math.max(1e-6,Math.hypot(dx,dy,dz))*0.5+0.5)),o=(j*N+i)*4;for(var c=0;c<3;c++)d[o+c]=A.gnd[c]+(A.sky[c]-A.gnd[c])*w;d[o+3]=1;}
   gl.texSubImage2D(gl.TEXTURE_CUBE_MAP_POSITIVE_X+f,0,0,0,N,N,gl.RGBA,gl.FLOAT,d);}
  E.baked=true;R.envFlatN=(R.envFlatN||0)+1;};
 // water normal/foam texture
 (function(){var t=R2.tex2d(gl,256,256,gl.RGBA8,gl.RGBA,gl.UNSIGNED_BYTE,{wrap:gl.REPEAT,filter:gl.LINEAR_MIPMAP_LINEAR});var f=R2.fbo(gl,t);gl.bindFramebuffer(gl.FRAMEBUFFER,f);gl.viewport(0,0,256,256);
  var P=prog('wn',VS_FSQ,FS_WNORM);gl.useProgram(P.p);fsq();gl.bindFramebuffer(gl.FRAMEBUFFER,null);gl.deleteFramebuffer(f);gl.bindTexture(gl.TEXTURE_2D,t);gl.generateMipmap(gl.TEXTURE_2D);R.wnTex=t;})();
 R.wvao=gl.createVertexArray();gl.bindVertexArray(R.wvao);var wb=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,wb);var wq=[],WN=48;for(var j=0;j<WN;j++)for(var i=0;i<WN;i++){var x0=i/WN*2-1,z0=j/WN*2-1,x1=(i+1)/WN*2-1,z1=(j+1)/WN*2-1;wq.push(x0,z0,x1,z0,x1,z1,x0,z0,x1,z1,x0,z1);}
 gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(wq),gl.STATIC_DRAW);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,2,gl.FLOAT,false,8,0);gl.bindVertexArray(null);R.wverts=wq.length/2;
 // ---------- distant mountain skyline (two rings around the camera: far layer first, then the nearer one) ----------
 R.mtn=null;R.setMountains=function(seed){if(R.mtn&&R.mtn.seed===seed)return;if(R.mtn)gl.deleteVertexArray(R.mtn.vao);var out=[],per=[];
  [[330,105,1],[215,58,0]].forEach(function(Ly){var r=Ly[0],Hm=Ly[1],layer=Ly[2],N=360,top=[],k0=seed*0.37+layer*11.3,n0=out.length;
   for(var i=0;i<=N;i++){var a=i/N*Math.PI*2,u=Math.cos(a)*3.1+k0,v=Math.sin(a)*3.1-k0,f=fbm2(u,v,5),rd=1-Math.abs(2*fbm2(u*1.9+5.2,v*1.9-1.7,4)-1);
    var spire=Math.pow(vnoise2(u*6.3+k0,v*6.3),9)*0.75;top.push(Hm*(0.18+0.55*f*f+0.35*rd*rd+spire)*(0.75+0.25*Math.sin(a*3+k0)));}
   for(var j=0;j<N;j++){var a0=j/N*Math.PI*2,a1=(j+1)/N*Math.PI*2,d0=[Math.cos(a0),Math.sin(a0)],d1=[Math.cos(a1),Math.sin(a1)],seg=r*(a1-a0);
    function col(d,h,slope){var t=[-d[1],d[0]],n=norm([-d[0]+t[0]*slope*1.4,0.55,-d[1]+t[1]*slope*1.4]);return{x:d[0]*r,z:d[1]*r,h:h,n:n};}
    var sl0=(top[(j+1)%N]-top[(j-1+N)%N])/(2*seg),sl1=(top[(j+2)%N]-top[j])/(2*seg),A=col(d0,top[j],sl0),B=col(d1,top[j+1],sl1);
    function v(C,y,hn){out.push(C.x,y,C.z,C.n[0],C.n[1],C.n[2],hn,layer);}
    var yb=-48;[[A,yb,0],[A,A.h*0.45,0.45*A.h/Hm],[B,yb,0],[A,A.h*0.45,0.45*A.h/Hm],[B,B.h*0.45,0.45*B.h/Hm],[B,yb,0],
     [A,A.h*0.45,0.45*A.h/Hm],[A,A.h,A.h/Hm],[B,B.h*0.45,0.45*B.h/Hm],[A,A.h,A.h/Hm],[B,B.h,B.h/Hm],[B,B.h*0.45,0.45*B.h/Hm]].forEach(function(q){v(q[0],q[1],q[2]);});}
   per.push((out.length-n0)/8);});
  var vao=gl.createVertexArray();gl.bindVertexArray(vao);var b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(out),gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,3,gl.FLOAT,false,32,0);gl.enableVertexAttribArray(1);gl.vertexAttribPointer(1,3,gl.FLOAT,false,32,12);gl.enableVertexAttribArray(2);gl.vertexAttribPointer(2,2,gl.FLOAT,false,32,24);gl.bindVertexArray(null);
  R.mtn={seed:seed,vao:vao,far:per[0],all:per[0]+per[1]};};
 function drawMountains(F,VP){var M=F.mtn;if(!M||!R.Q.mtn)return;R.setMountains(M.seed||1);var P=prog('mtn'+R.q,VS_MTN,GL_COMMON+FS_MTN,lite([]));gl.useProgram(P.p);gl.uniformMatrix4fv(P.uVP,false,VP);gl.uniform3fv(P.uEyeV,F.eye);
  gl.uniform1f(P.uBaseY,M.base==null?-2:M.base);setCommon(P,F);gl.uniform3fv(P.uRock,M.rock);gl.uniform3fv(P.uRock2,M.rock2);gl.uniform1f(P.uSnow,M.snow||0);gl.uniform3fv(P.uHaze,M.haze);
  gl.bindVertexArray(R.mtn.vao);gl.drawArrays(gl.TRIANGLES,0,R.Q.mtn>1?R.mtn.all:R.mtn.far);gl.bindVertexArray(null);R.stats.draws++;}
 // ---------- cascades ----------
 function cascades(F){var Q=R.Q,n=Q.cascades,splits=n===1?[45]:n===2?[14,60]:[9,28,85],out=[],prevFar=F.near;
  var fov=F.fov,asp=F.asp,fw=F.fwd,rt=F.right,up=F.up,th=Math.tan(fov/2),ld=F.sunDir,lup=Math.abs(ld[1])>0.99?[0,0,1]:[0,1,0],tile=R.shadowSize/2;
  for(var c=0;c<n;c++){var nf=c===0?F.near:splits[c-1]*0.92,ff=splits[c],pts=[];[nf,ff].forEach(function(d){var hh=th*d,hw=hh*asp;[[-1,-1],[1,-1],[1,1],[-1,1]].forEach(function(s){pts.push([F.eye[0]+fw[0]*d+rt[0]*hw*s[0]+up[0]*hh*s[1],F.eye[1]+fw[1]*d+rt[1]*hw*s[0]+up[1]*hh*s[1],F.eye[2]+fw[2]*d+rt[2]*hw*s[0]+up[2]*hh*s[1]]);});});
   var cx=0,cy=0,cz=0;pts.forEach(function(p){cx+=p[0];cy+=p[1];cz+=p[2];});cx/=8;cy/=8;cz/=8;var rad=0;pts.forEach(function(p){rad=Math.max(rad,Math.hypot(p[0]-cx,p[1]-cy,p[2]-cz));});rad=Math.ceil(rad*16)/16;
   var V=lookAt([cx+ld[0]*100,cy+ld[1]*100,cz+ld[2]*100],[cx,cy,cz],lup),tx=2*rad/tile;var o=m4pt(V,[0,0,0]);var sx=(o[0]%tx),sy=(o[1]%tx);V[12]-=sx;V[13]-=sy;
   var P=ortho(-rad,rad,-rad,rad,1,100+rad*2+60),VP=mul(P,V),tx2=(c%2)*0.5,ty2=Math.floor(c/2)*0.5,B=[0.25,0,0,0,0,0.25,0,0,0,0,0.5,0,0.25+tx2,0.25+ty2,0.5,1];
   out.push({vp:VP,m:mul(B,VP),far:ff,tile:[tx2,ty2],rad:rad,range:100+rad*2+60-1});}return out;}
 // ---------- drawing helpers ----------
 function setCommon(P,F){gl.uniform3fv(P.uSunDir,F.sunDir);gl.uniform3fv(P.uSunCol,F.sunCol);gl.uniform3fv(P.uEye,F.eye);gl.uniform1f(P.uTime,F.t);
  gl.activeTexture(gl.TEXTURE4);gl.bindTexture(gl.TEXTURE_CUBE_MAP,R.env.spec);gl.uniform1i(P.uEnvSpec,4);gl.activeTexture(gl.TEXTURE5);gl.bindTexture(gl.TEXTURE_CUBE_MAP,R.env.irr);gl.uniform1i(P.uEnvIrr,5);
  gl.uniform1f(P.uEnvMips,R.env.mips==null?5:R.env.mips);gl.uniform1f(P.uEnvK,F.envK==null?1:F.envK);gl.uniform3fv(P.uFogCol,F.fogCol);gl.uniform3fv(P.uFogSun,F.fogSun);gl.uniform1f(P.uFogDen,F.fogDen);gl.uniform1f(P.uFogH,F.fogH);gl.uniform1f(P.uFogY,F.fogY);
  gl.activeTexture(gl.TEXTURE6);gl.bindTexture(gl.TEXTURE_2D,R.stex);gl.uniform1i(P.uShadow,6);gl.uniform1i(P.uShadowN,F.casc?F.casc.length:0);
  if(F.casc){var ms=new Float32Array(48);F.casc.forEach(function(c,i){ms.set(c.m,i*16);});gl.uniformMatrix4fv(P.uShadowM,false,ms);gl.uniform4f(P.uCasc,F.casc[0].far,(F.casc[1]||F.casc[0]).far,(F.casc[2]||F.casc[0]).far,0);}
  gl.uniform1f(P.uShadowTx,1/Math.max(1,R.shadowSize));gl.uniform1i(P.uPcf,R.Q.pcf);gl.uniform1f(P.uAerial,F.aerial||0);
  if(R.Q.pcss&&F.casc&&P.uShadowRaw){gl.activeTexture(gl.TEXTURE11);gl.bindTexture(gl.TEXTURE_2D,R.stex);gl.bindSampler(11,R.rawS);gl.uniform1i(P.uShadowRaw,11);var c0=F.casc[0],c1=F.casc[1]||c0,c2=F.casc[2]||c0;
   gl.uniform4f(P.uCascR,c0.rad,c1.rad,c2.rad,0);gl.uniform4f(P.uCascZ,c0.range,c1.range,c2.range,0);gl.uniform1f(P.uSunAng,F.sunAng||0.03);}
  var L=F.lights||[],n=Math.min(R.Q.lights,L.length),lp=new Float32Array(64),lc=new Float32Array(48);for(var i=0;i<n;i++){lp.set([L[i][0],L[i][1],L[i][2],L[i][3]],i*4);lc.set(L[i][4],i*3);}
  gl.uniform1i(P.uNL,n);gl.uniform4fv(P.uLP,lp);gl.uniform3fv(P.uLC,lc);}
 function bindMat(P,m,shadow){var t=m.tex;gl.uniform4fv(P.uBaseF,m.base);gl.uniform1f(P.uCut,m.cut);gl.uniform1i(P.uMaps,shadow?(m.maps&1):m.maps);
  gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,t.uBase||R.tex.white);gl.uniform1i(P.uBase,0);if(shadow)return;
  gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,t.uMR||R.tex.white);gl.uniform1i(P.uMR,1);gl.activeTexture(gl.TEXTURE2);gl.bindTexture(gl.TEXTURE_2D,t.uNorm||R.tex.flatN);gl.uniform1i(P.uNorm,2);
  gl.activeTexture(gl.TEXTURE3);gl.bindTexture(gl.TEXTURE_2D,t.uOcc||R.tex.white);gl.uniform1i(P.uOcc,3);gl.activeTexture(gl.TEXTURE8);gl.bindTexture(gl.TEXTURE_2D,t.uEmis||R.tex.white);gl.uniform1i(P.uEmis,8);
  gl.uniform3fv(P.uEmisF,m.emis);gl.uniform2f(P.uMRF,m.metal,m.rough);gl.uniform1f(P.uNormS,m.normS);gl.uniform1f(P.uOccS,m.occS);gl.uniform1f(P.uTrans,m.trans);gl.uniform1f(P.uUnlit,m.unlit?1:0);
  var tm=m.foliage||m.tintable||R._tintAll?1:0;gl.uniform3f(P.uTintMask,tm,tm,tm);}
 function cullOn(ds){if(ds)gl.disable(gl.CULL_FACE);else{gl.enable(gl.CULL_FACE);gl.cullFace(gl.BACK);}}
 // draw everything that is a mesh, for the main pass (shadow=false) or a shadow cascade (shadow=true)
 function drawMeshes(F,VP,shadow,cascIdx){var st=R.stats;
  var groups={};function grp(defs){var k=defs.join(',');return groups[k]||(groups[k]={defs:defs,items:[]});}
  for(var k in R.batches){var b=R.batches[k];if(!b.n||!b.gp.mat||R.dbgSkip&&R.dbgSkip.batch&&k.indexOf(R.dbgSkip.batch)>=0)continue;if(shadow&&(!b.shadow||(b.gp.mat.foliage&&!R.Q.foliageShadow&&cascIdx>0)))continue;var m=b.gp.mat,a=m.alpha==='MASK'||m.alpha==='BLEND';
   var d=['INST'],lo=R.q==='low';if(a)d.push('ALPHA');if(b.wind&&!lo)d.push('WIND');if(b.fade&&!lo)d.push('FADE');/* Low: no sway / fade variants (fewer shaders to build) */grp(d).items.push({b:b});}
  R.dyn.forEach(function(x){if(shadow&&!x.shadow)return;if(R.dbgSkip&&R.dbgSkip.dyn)return;var m=x.gp.mat,d=[];if(m.alpha!=='OPAQUE')d.push('ALPHA');grp(d).items.push({x:x});});
  R.skin.forEach(function(x){if(R.dbgSkip&&R.dbgSkip.skin)return;if(R.Q.cullDist&&x.m){var sx=x.m[12]-F.eye[0],sz=x.m[14]-F.eye[2];if(sx*sx+sz*sz>R.Q.cullDist*R.Q.cullDist*0.36)return;}var m=x.gp.mat,d=['SKIN'];if(m.alpha!=='OPAQUE')d.push('ALPHA');grp(d).items.push({s:x});});
  for(var gk in groups){var g=groups[gk],P=shadow?shadowProg(g.defs):meshProg(g.defs);gl.useProgram(P.p);gl.uniformMatrix4fv(P.uVP,false,VP);gl.uniform1f(P.uTime,F.t);gl.uniform3fv(P.uEyeV,F.eye);
   gl.uniform4fv(P.uWind,F.wind||[0.6,0.3,0.5,0.1]);if(!shadow)setCommon(P,F);if(g.defs.indexOf('SKIN')>=0){gl.activeTexture(gl.TEXTURE9);gl.bindTexture(gl.TEXTURE_2D,R.jt);gl.uniform1i(P.uJT,9);}
   g.items.forEach(function(it){R._tintAll=it.b?it.b.tintAll:(it.x?it.x.tintAll:(it.s?it.s.tintAll:false));if(it.b){var b=it.b,gp=b.gp;batchVAO(b);bindMat(P,gp.mat,shadow);cullOn(gp.mat.ds||shadow);var hgt=Math.max(0.3,(gp.hi[1]||1));gl.uniform1f(P.uWindH,b.windH||1/hgt);gl.uniform1f(P.uFade,b.fade||1e9);
     var bn=b.n;if(R.Q.cullDist&&b.buf){cullBatch(b,F);bn=b.vn;}if(!bn)return;gl.bindVertexArray(b.vao||gp.vao);if(gp.idx)gl.drawElementsInstanced(gl.TRIANGLES,gp.count,gp.it,0,bn);else gl.drawArraysInstanced(gl.TRIANGLES,0,gp.count,bn);st.draws++;st.tris+=gp.count/3*bn;}
    else{var x=it.x||it.s,gp2=x.gp;bindMat(P,gp2.mat,shadow);cullOn(gp2.mat.ds||shadow);gl.uniformMatrix4fv(P.uModel,false,x.m);gl.uniform4fv(P.uTintU,x.tint);
     if(it.s){gl.uniform1i(P.uJRow,x.row);}gl.bindVertexArray(gp2.vao);
     if(gp2.idx)gl.drawElements(gl.TRIANGLES,gp2.count,gp2.it,0);else gl.drawArrays(gl.TRIANGLES,0,gp2.count);st.draws++;st.tris+=gp2.count/3;}});}
  gl.bindVertexArray(null);gl.disable(gl.CULL_FACE);}
 function uploadJoints(){var rows=Math.min(R.skinRows.length,R.jtH);if(!rows)return;var d=R.jdata;R.skinRows.forEach(function(x,i){if(i>=R.jtH)return;var n=Math.min(x.nj,80);d.set(x.joints.subarray(0,n*16),i*R.jtW*4);});
  gl.bindTexture(gl.TEXTURE_2D,R.jt);gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,R.jtW,rows,gl.RGBA,gl.FLOAT,d.subarray(0,rows*R.jtW*4));}
 function drawTerrain(F,VP,shadow){if(!R.ter)return;if(shadow){var P0=prog('tershadow',VS_TER,'precision mediump float;out vec4 o;void main(){o=vec4(1.0);}');gl.useProgram(P0.p);gl.uniformMatrix4fv(P0.uVP,false,VP);gl.uniform3fv(P0.uEyeV,F.eye);gl.bindVertexArray(R.ter.vao);gl.drawElements(gl.TRIANGLES,R.ter.n,gl.UNSIGNED_INT,0);return;}
  if(!R.groundSet||(R.groundSet.lite&&R.q!=='low'))return drawTerrainFlat(F,VP);var P=prog('ter'+R.q,VS_TER,GL_COMMON+FS_TER,lite([]));gl.useProgram(P.p);gl.uniformMatrix4fv(P.uVP,false,VP);gl.uniform3fv(P.uEyeV,F.eye);setCommon(P,F);
  gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D_ARRAY,R.groundSet.a);gl.uniform1i(P.uTA,0);gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D_ARRAY,R.groundSet.n);gl.uniform1i(P.uTN,1);gl.activeTexture(gl.TEXTURE2);gl.bindTexture(gl.TEXTURE_2D_ARRAY,R.groundSet.r);gl.uniform1i(P.uTR,2);
  gl.uniform3fv(P.uMeadow,F.meadow[0]);gl.uniform3fv(P.uMeadow2,F.meadow[1]);gl.uniform1f(P.uTile,F.tile||3.2);gl.uniform3fv(P.uPathCol,F.pathCol||[1,0.92,0.8]);
  gl.bindVertexArray(R.ter.vao);gl.drawElements(gl.TRIANGLES,R.ter.n,gl.UNSIGNED_INT,0);R.stats.draws++;R.stats.tris+=R.ter.n/3;}
 function drawTerrainFlat(F,VP){var P=prog('terflat',VS_TER,GL_COMMON+'in vec3 vW;in vec3 vN;in vec4 vSp;in vec4 vX;in float vDist;uniform vec3 uMeadow;out vec4 o;void main(){vec3 N=normalize(vN);vec3 V=normalize(uEye-vW);vec3 a=mix(uMeadow,vec3(0.35,0.3,0.25),clamp(vSp.x+vSp.y,0.0,1.0));vec3 c=shadePBR(vW,N,V,a,0.0,0.9,vX.y,shadowAt(vW,N,vDist),0.0);o=vec4(fogApply(c,vW,V),1.0);}');
  gl.useProgram(P.p);gl.uniformMatrix4fv(P.uVP,false,VP);gl.uniform3fv(P.uEyeV,F.eye);setCommon(P,F);gl.uniform3fv(P.uMeadow,F.meadow[0]);gl.bindVertexArray(R.ter.vao);gl.drawElements(gl.TRIANGLES,R.ter.n,gl.UNSIGNED_INT,0);}
 function drawFX(F,VP,shadow){var P=shadow?prog('fxs',VS_FX,'precision mediump float;in float vE;out vec4 o;void main(){if(vE>0.45)discard;o=vec4(1.0);}'):prog('fx'+R.q,VS_FX,GL_COMMON+FS_FX,lite([]));gl.useProgram(P.p);gl.uniformMatrix4fv(P.uVP,false,VP);gl.uniform3fv(P.uEyeV,F.eye);if(!shadow)setCommon(P,F);
  FX_MESHES.forEach(function(k){var m=R.b[k];if(!m.n||R.dbgSkip&&R.dbgSkip.fx)return;gl.bindVertexArray(m.vao);gl.drawArraysInstanced(gl.TRIANGLES,0,m.verts,m.n);R.stats.draws++;});gl.bindVertexArray(null);}
 // ---------- the frame ----------
 R.render=function(F){var W=canvas.width,H=canvas.height,w=Math.max(2,W),h=Math.max(2,H);R.resize(w,h);R.frame++;R.stats={draws:0,tris:0};var Q=R.Q;
  if(!R.env||!R.env.baked){if(Q.ibl)R.bakeEnv(F.sky);else R.flatEnv(F.sky);}
  // camera matrices (TAA jitter on high)
  var near=F.near||0.1,far=F.far||400,proj=persp(F.fov,F.asp,near,far),view=lookAt(F.eye,F.target,[0,1,0]),VPu=mul(proj,view),VP=VPu;
  if(Q.taa&&!R.noJitter){var jx=(halton(R.frame%8+1,2)-0.5)*2/w,jy=(halton(R.frame%8+1,3)-0.5)*2/h,pj=proj.slice();pj[8]+=jx;pj[9]+=jy;VP=mul(pj,view);}
  var VPf=new Float32Array(VP);R._dbg={VP:VP,VPu:VPu,proj:proj,jit:Q.taa&&!R.noJitter};F.casc=R.shadowSize>1?cascades({eye:F.eye,fwd:F.fwd,right:F.right,up:F.up,fov:F.fov,asp:F.asp,near:near,sunDir:F.shadowDir||F.sunDir}):null;
  uploadJoints();
  // shadows
  if(F.casc){gl.bindFramebuffer(gl.FRAMEBUFFER,R.sfb);gl.viewport(0,0,R.shadowSize,R.shadowSize);gl.clear(gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LESS);gl.depthMask(true);gl.enable(gl.POLYGON_OFFSET_FILL);gl.polygonOffset(1.6,3.0);
   var ts=R.shadowSize/2;F.casc.forEach(function(c,i){gl.viewport(c.tile[0]*R.shadowSize,c.tile[1]*R.shadowSize,ts,ts);var vp=new Float32Array(c.vp);drawMeshes(F,vp,true,i);if(i>0||!F.noTerrainShadow)drawTerrain(F,vp,true);drawFX(F,vp,true);});gl.disable(gl.POLYGON_OFFSET_FILL);}
  // main HDR pass
  gl.bindFramebuffer(gl.FRAMEBUFFER,T.scene);gl.viewport(0,0,w,h);gl.clearColor(0,0,0,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LEQUAL);gl.depthMask(true);gl.disable(gl.BLEND);
  drawTerrain(F,VPf,false);drawMeshes(F,VPf,false);drawFX(F,VPf,false);drawMountains(F,VPf);
  var invVP=new Float32Array(m4inv(VP));var PS=prog('sky',VS_FSQ,FS_SKY);gl.useProgram(PS.p);skyU(PS,F.sky);gl.uniformMatrix4fv(PS.uInvVP,false,invVP);gl.uniform3fv(PS.uEye,F.eye);gl.uniform3fv(PS.uFogCol,F.fogCol);gl.uniform1f(PS.uSkyFog,F.skyFog==null?0.6:F.skyFog);gl.uniform3fv(PS.uHzSun,F.fogSun||[0,0,0]);gl.uniform3fv(PS.uHzDir,F.sunDir);gl.depthMask(false);fsq();gl.depthMask(true);
  // copy opaque colour + depth (water refraction, SSAO, TAA)
  gl.bindFramebuffer(gl.READ_FRAMEBUFFER,T.scene);gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER,T.copy);gl.blitFramebuffer(0,0,w,h,0,0,w,h,gl.DEPTH_BUFFER_BIT,gl.NEAREST);if(F.water!=null&&Q.refract)gl.blitFramebuffer(0,0,w,h,0,0,w,h,gl.COLOR_BUFFER_BIT,gl.NEAREST);
  gl.bindFramebuffer(gl.FRAMEBUFFER,T.scene);
  if(F.water!=null){var PW=prog('wat',VS_WAT,GL_COMMON+FS_WAT);gl.useProgram(PW.p);gl.uniformMatrix4fv(PW.uVP,false,VPf);gl.uniform1f(PW.uSize,F.size+40);gl.uniform1f(PW.uLvl,F.water);gl.uniform3fv(PW.uEyeV,F.eye);setCommon(PW,F);
   gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,T.copyC);gl.uniform1i(PW.uScene,0);gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,T.copyD);gl.uniform1i(PW.uDepth,1);gl.activeTexture(gl.TEXTURE2);gl.bindTexture(gl.TEXTURE_2D,R.wnTex);gl.uniform1i(PW.uWN,2);
   gl.uniform2f(PW.uRes,w,h);gl.uniformMatrix4fv(PW.uInvVP,false,invVP);gl.uniform3fv(PW.uWCol,F.wcol);gl.uniform3fv(PW.uWDeep,F.wdeep||scl3(F.wcol,0.25));gl.uniform1i(PW.uRefr,F.ice?1:Q.refract);gl.uniform1i(PW.uSSR,Q.ssr);gl.uniform1f(PW.uIce,F.ice?1:0);gl.uniform1f(PW.uNear,near);gl.uniform1f(PW.uFar,far);
   if(!Q.refract){gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);}gl.depthMask(false);gl.bindVertexArray(R.wvao);gl.drawArrays(gl.TRIANGLES,0,R.wverts);gl.depthMask(true);gl.disable(gl.BLEND);}
  if(R.parts){var PP=prog('part',VS_PART,FS_PART);gl.useProgram(PP.p);gl.uniformMatrix4fv(PP.uVP,false,VPf);gl.uniform3fv(PP.uCamR,F.right);gl.uniform3fv(PP.uCamU,F.up);gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE);gl.depthMask(false);
   gl.bindVertexArray(R.pvao);gl.drawArraysInstanced(gl.TRIANGLES,0,6,R.parts);gl.depthMask(true);gl.disable(gl.BLEND);}
  gl.disable(gl.DEPTH_TEST);gl.bindVertexArray(R.fsq);
  // SSAO (half res) from the opaque depth
  var hw=Math.max(1,w>>1),hh=Math.max(1,h>>1);
  if(Q.ssao){var PA=prog('ssao',VS_FSQ,FS_SSAO);gl.bindFramebuffer(gl.FRAMEBUFFER,T.aoF1);gl.viewport(0,0,hw,hh);gl.useProgram(PA.p);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,T.copyD);gl.uniform1i(PA.uDepth,0);
   gl.uniformMatrix4fv(PA.uProj,false,new Float32Array(proj));gl.uniformMatrix4fv(PA.uInvProj,false,new Float32Array(m4inv(proj)));gl.uniform2f(PA.uRes,w,h);gl.uniform1i(PA.uN,Q.ssao);gl.uniform1f(PA.uRad,0.9);fsq();
   var PB=prog('blur',VS_FSQ,FS_BLUR);gl.useProgram(PB.p);gl.uniform1i(PB.uT,0);gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,T.copyD);gl.uniform1i(PB.uDepth,1);gl.uniform1f(PB.uNear,near);gl.uniform1f(PB.uFar,far);gl.activeTexture(gl.TEXTURE0);
   gl.bindFramebuffer(gl.FRAMEBUFFER,T.aoF2);gl.bindTexture(gl.TEXTURE_2D,T.ao1);gl.uniform2f(PB.uDir,1/hw,0);fsq();gl.bindFramebuffer(gl.FRAMEBUFFER,T.aoF1);gl.bindTexture(gl.TEXTURE_2D,T.ao2);gl.uniform2f(PB.uDir,0,1/hh);fsq();}
  // bloom: prefilter -> down chain -> up chain (additive)
  var BL=R.bloom;if(BL.length){var PBr=prog('bright',VS_FSQ,R2FS_BRIGHT),PD=prog('down',VS_FSQ,FS_DOWN),PU=prog('up',VS_FSQ,FS_UP);
  gl.bindFramebuffer(gl.FRAMEBUFFER,BL[0].f);gl.viewport(0,0,BL[0].w,BL[0].h);gl.useProgram(PBr.p);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,T.sceneC);gl.uniform1i(PBr.uT,0);gl.uniform2f(PBr.uPx,1/w,1/h);gl.uniform1f(PBr.uThr,F.bloomThr||1.4);fsq();
  gl.useProgram(PD.p);gl.uniform1i(PD.uT,0);for(var i=1;i<BL.length;i++){gl.bindFramebuffer(gl.FRAMEBUFFER,BL[i].f);gl.viewport(0,0,BL[i].w,BL[i].h);gl.bindTexture(gl.TEXTURE_2D,BL[i-1].t);gl.uniform2f(PD.uPx,1/BL[i-1].w,1/BL[i-1].h);fsq();}
  gl.useProgram(PU.p);gl.uniform1i(PU.uT,0);gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE);for(var u=BL.length-1;u>0;u--){gl.bindFramebuffer(gl.FRAMEBUFFER,BL[u-1].f);gl.viewport(0,0,BL[u-1].w,BL[u-1].h);gl.bindTexture(gl.TEXTURE_2D,BL[u].t);gl.uniform2f(PU.uPx,1/BL[u].w,1/BL[u].h);gl.uniform1f(PU.uK,1.0);fsq();}gl.disable(gl.BLEND);}
  // sun shafts: sky brighter than a threshold near the sun's screen position, radially blurred (quarter resolution)
  var shK=0;if(Q.shafts&&F.shafts>0){var sp=m4pt4(VPu,[F.eye[0]+F.sunDir[0]*1000,F.eye[1]+F.sunDir[1]*1000,F.eye[2]+F.sunDir[2]*1000,1]),fd=dot(F.fwd||[0,0,-1],F.sunDir);
   if(sp[3]>0&&fd>0){var su=[sp[0]/sp[3]*0.5+0.5,sp[1]/sp[3]*0.5+0.5],off=Math.max(Math.max(-su[0],su[0]-1),Math.max(-su[1],su[1]-1));shK=F.shafts*clamp((fd-0.05)/0.4,0,1)*clamp(1-off/0.6,0,1);
    if(shK>0.001){var PM=prog('shmask',VS_FSQ,FS_SHMASK),PR=prog('shblur',VS_FSQ,FS_SHBLUR);gl.bindFramebuffer(gl.FRAMEBUFFER,T.shAF);gl.viewport(0,0,R.shW,R.shH);gl.useProgram(PM.p);
     gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,T.sceneC);gl.uniform1i(PM.uT,0);gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,T.copyD);gl.uniform1i(PM.uDepth,1);
     gl.uniform2f(PM.uSun,su[0],su[1]);gl.uniform1f(PM.uAsp,w/h);gl.uniform1f(PM.uThr,F.shaftThr||0.6);fsq();
     gl.bindFramebuffer(gl.FRAMEBUFFER,T.shBF);gl.useProgram(PR.p);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,T.shA);gl.uniform1i(PR.uT,0);gl.uniform2f(PR.uSun,su[0],su[1]);
     gl.uniform1i(PR.uN,Q.shafts);gl.uniform1f(PR.uDecay,Math.pow(0.04,1/Q.shafts));gl.uniform1f(PR.uLen,0.85);fsq();}}}
  R.shaftK=shK;
  // eye adaptation: centre-weighted log luminance -> mips -> 1x1, smoothed over time (about 1.2 s to settle)
  var aeOn=Q.ae&&R.hdr&&T.lum&&F.ae!==0;if(aeOn){var now=performance.now(),adt=R.aeT?Math.min(0.25,(now-R.aeT)/1000):0;R.aeT=now;var PL=prog('lum',VS_FSQ,FS_LUM),PA2=prog('adapt',VS_FSQ,FS_ADAPT);
   gl.bindFramebuffer(gl.FRAMEBUFFER,T.lumF);gl.viewport(0,0,64,64);gl.useProgram(PL.p);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,T.sceneC);gl.uniform1i(PL.uT,0);fsq();gl.bindTexture(gl.TEXTURE_2D,T.lum);gl.generateMipmap(gl.TEXTURE_2D);
   var src=R.aeI?T.ae1:T.ae0,dst=R.aeI?T.ae0F:T.ae1F;R.aeI=1-R.aeI;gl.bindFramebuffer(gl.FRAMEBUFFER,dst);gl.viewport(0,0,1,1);gl.useProgram(PA2.p);gl.uniform1i(PA2.uLum,0);gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,src);gl.uniform1i(PA2.uPrev,1);
   gl.uniform1f(PA2.uLvl,6);gl.uniform1f(PA2.uK,R.aeReset||F.cut?1:1-Math.exp(-adt*2.2));fsq();R.aeReset=false;R.aeTex=R.aeI?T.ae1:T.ae0;}
  // composite (exposure, AO, bloom, shafts, eye adaptation, ACES/AgX, grade, split tone, grain) -> LDR
  var C=prog('comp',VS_FSQ,R2FS_COMP),G=F.grade||{};gl.bindFramebuffer(gl.FRAMEBUFFER,T.ldrAF);gl.viewport(0,0,w,h);gl.useProgram(C.p);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,T.sceneC);gl.uniform1i(C.uScene,0);
  gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,BL.length?BL[0].t:R.tex.white);gl.uniform1i(C.uBloom,1);gl.activeTexture(gl.TEXTURE2);gl.bindTexture(gl.TEXTURE_2D,Q.ssao?T.ao1:R.tex.white);gl.uniform1i(C.uAO,2);gl.uniform1f(C.uAOK,Q.ssao?0.85:0);
  gl.uniform1f(C.uBloomK,BL.length?(F.bloomK==null?0.06:F.bloomK):0);gl.uniform1f(C.uExpo,F.expo);gl.uniform1f(C.uSat,G.sat||1);gl.uniform1f(C.uCon,G.con||1);gl.uniform3fv(C.uTint,G.tint||[1,1,1]);gl.uniform3fv(C.uLift,G.lift||[0,0,0]);gl.uniform3fv(C.uGain,G.gain||[1,1,1]);
  gl.uniform1f(C.uVig,(G.vig==null?0.55:G.vig)*0.6);gl.uniform1f(C.uTime,F.t%10);gl.uniform1f(C.uHurt,F.hurt||0);gl.uniform1f(C.uFlash,F.flash||0);
  gl.activeTexture(gl.TEXTURE3);gl.bindTexture(gl.TEXTURE_2D,aeOn?R.aeTex:R.tex.white);gl.uniform1i(C.uAE,3);var AE=F.aeP||[0.5,0.7,2.4,0.16];gl.uniform4f(C.uAEP,aeOn?AE[0]:0,AE[1],AE[2],AE[3]);
  gl.activeTexture(gl.TEXTURE4);gl.bindTexture(gl.TEXTURE_2D,shK>0.001?T.shB:R.black);gl.uniform1i(C.uShaft,4);var shc=F.shaftCol||[1,1,1];gl.uniform3f(C.uShaftC,shc[0]*shK,shc[1]*shK,shc[2]*shK);
  gl.uniform1i(C.uTone,F.tone==='agx'?1:0);gl.uniform3fv(C.uShTint,G.shadows||[1,1,1]);gl.uniform3fv(C.uHiTint,G.highlights||[1,1,1]);gl.uniform1f(C.uGrain,Q.grain?(G.grain==null?0.014:G.grain):0);fsq();
  // anti-aliasing -> canvas
  gl.activeTexture(gl.TEXTURE0);if(Q.taa&&!R.noTaaResolve){var PT=prog('taa',VS_FSQ,FS_TAA);gl.bindFramebuffer(gl.FRAMEBUFFER,T.ldrBF);gl.useProgram(PT.p);gl.bindTexture(gl.TEXTURE_2D,T.ldrA);gl.uniform1i(PT.uCur,0);gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,T.hist);gl.uniform1i(PT.uHist,1);
   gl.activeTexture(gl.TEXTURE2);gl.bindTexture(gl.TEXTURE_2D,T.copyD);gl.uniform1i(PT.uDepth,2);gl.uniformMatrix4fv(PT.uInvVP,false,invVP);gl.uniformMatrix4fv(PT.uPrevVP,false,new Float32Array(R.prevVP||VPu));gl.uniform2f(PT.uPx,1/w,1/h);gl.uniform1f(PT.uReset,R.taaReset||F.cut?1:0);fsq();R.taaReset=false;
   gl.bindFramebuffer(gl.READ_FRAMEBUFFER,T.ldrBF);gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER,T.histF);gl.blitFramebuffer(0,0,w,h,0,0,w,h,gl.COLOR_BUFFER_BIT,gl.NEAREST);
   gl.bindFramebuffer(gl.FRAMEBUFFER,null);gl.viewport(0,0,W,H);var PO=prog('out',VS_FSQ,FS_OUT);gl.useProgram(PO.p);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,T.ldrB);gl.uniform1i(PO.uT,0);gl.uniform2f(PO.uPx,1/w,1/h);gl.uniform1f(PO.uSharp,Q.sharp==null?0.35:Q.sharp);fsq();}
  else{var PF2=prog('fxaa',VS_FSQ,R2FS_FXAA);gl.bindFramebuffer(gl.FRAMEBUFFER,null);gl.viewport(0,0,W,H);gl.useProgram(PF2.p);gl.bindTexture(gl.TEXTURE_2D,T.ldrA);gl.uniform1i(PF2.uT,0);gl.uniform2f(PF2.uPx,1/w,1/h);gl.uniform1f(PF2.uSharp,Q.sharp||0);fsq();}
  gl.bindVertexArray(null);R.prevVP=VPu;if(R.frame>3)R.tex.pump();};   // lazy textures: one per frame after the first frames
 R.setQuality=function(q){if(!R2.QUALITY[q])q='medium';R.q=q;R.Q=R2.QUALITY[q];R.aeReset=true;R.setShadow(R.Q.shadow);R.scale=R.Q.scale;R.fbW=0;if(R.env&&(R.Q.ibl?R.env.res!==R.Q.env:!R.env.flat))R.env.baked=false;
  R.tex.lite=!!R.Q.lazyTex;R.tex.max=R.Q.texMax||0;if(!R.tex.lite){R.tex.flush();R.upgradeTextures();}};
 R.invalidateEnv=function(){if(R.env)R.env.baked=false;};
 R.destroy=function(){var e=gl.getExtension('WEBGL_lose_context');if(e)e.loseContext();};
 if(R.gpu.software){R2.QUALITY.low.grass=0;R2.QUALITY.low.scatter=0.15;R2.QUALITY.low.scale=0.38;R2.QUALITY.low.cullDist=42;}R.setQuality(R.gpu.software?'low':'medium');return R;}
