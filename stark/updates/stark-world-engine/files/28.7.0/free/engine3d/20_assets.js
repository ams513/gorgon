// ========== assets: sources (studio server or cartridge-embedded), textures, glTF 2.0, Radiance HDR ==========
R2.source={base:'assets/',lib:'library/',embedded:null,cache:{}};
R2.setSource=function(o){for(var k in o)R2.source[k]=o[k];};
R2.resolve=function(from,uri){if(/^(data:|lib:|https?:)/.test(uri))return uri;var lib=/^lib:/.test(from),p=from.replace(/^lib:/,'').split('/');p.pop();
 decodeURIComponent(uri).split('/').forEach(function(s){if(s==='..')p.pop();else if(s&&s!=='.')p.push(s);});return(lib?'lib:':'')+p.join('/');};
function b64bytes(s){var bin=atob(s),u=new Uint8Array(bin.length);for(var i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);return u;}
R2.fetchBytes=function(path){var S=R2.source;if(S.cache[path])return S.cache[path];var pr;
 if(/^data:/.test(path)){pr=Promise.resolve(b64bytes(path.split(',')[1]).buffer);}
 else if(S.embedded){var e=S.embedded[path];if(!e)pr=Promise.reject(new Error('not in this cartridge: '+path));
  else if(e.charAt(0)==='z'&&typeof DecompressionStream!=='undefined'){pr=new Response(new Blob([b64bytes(e.slice(1))]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();}
  else pr=Promise.resolve(b64bytes(e.slice(1)).buffer);}
 else{var url=/^lib:/.test(path)?S.lib+path.slice(4):S.base+path;pr=fetch(url.split('/').map(function(x,i){return i?encodeURIComponent(x):x;}).join('/')).then(function(r){if(!r.ok)throw new Error(path+': '+r.status);return r.arrayBuffer();});}
 S.cache[path]=pr;return pr;};
R2.fetchJSON=function(path){return R2.fetchBytes(path).then(function(b){return JSON.parse(new TextDecoder().decode(b));});};
// ---------- textures ----------
R2.Textures=function(gl){var T={gl:gl,map:{},aniso:gl.getExtension('EXT_texture_filter_anisotropic')||gl.getExtension('WEBKIT_EXT_texture_filter_anisotropic')};
 T.maxAniso=T.aniso?Math.min(8,gl.getParameter(T.aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT)):0;
 T.white=R2.tex2d(gl,1,1,gl.RGBA8,gl.RGBA,gl.UNSIGNED_BYTE,{data:new Uint8Array([255,255,255,255])});
 T.flatN=R2.tex2d(gl,1,1,gl.RGBA8,gl.RGBA,gl.UNSIGNED_BYTE,{data:new Uint8Array([128,128,255,255])});
 T.upload=function(img,srgb,wrapS,wrapT){var t=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,t);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,false);gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL,gl.NONE);
  gl.texImage2D(gl.TEXTURE_2D,0,srgb?gl.SRGB8_ALPHA8:gl.RGBA8,gl.RGBA,gl.UNSIGNED_BYTE,img);gl.generateMipmap(gl.TEXTURE_2D);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,wrapS||gl.REPEAT);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,wrapT||gl.REPEAT);
  if(T.maxAniso)gl.texParameterf(gl.TEXTURE_2D,T.aniso.TEXTURE_MAX_ANISOTROPY_EXT,T.maxAniso);t.w=img.width;t.h=img.height;return t;};
 T.decode=function(bytes,mime){var blob=new Blob([bytes],{type:mime||'image/png'});
  if(typeof createImageBitmap==='function')return createImageBitmap(blob,{premultiplyAlpha:'none',colorSpaceConversion:'none'}).catch(function(){return T.decodeImg(blob);});return T.decodeImg(blob);};
 T.decodeImg=function(blob){return new Promise(function(ok,bad){var u=URL.createObjectURL(blob),im=new Image();im.onload=function(){URL.revokeObjectURL(u);ok(im);};im.onerror=function(){URL.revokeObjectURL(u);bad(new Error('bad image'));};im.src=u;});};
 // lazy loading (Low preset): every texture waits for a free slot (the renderer frees one per frame once the first
 // frames are on screen) and is scaled down to T.max px before it reaches the GPU
 T.lite=false;T.max=0;T.waiters=[];
 T.gate=function(){if(!T.lite)return Promise.resolve();return new Promise(function(ok){T.waiters.push(ok);});};
 T.pump=function(){var w=T.waiters.shift();if(w)w();};
 T.flush=function(){var w=T.waiters;T.waiters=[];w.forEach(function(f){f();});};
 T.shrink=function(img,max){if(!max||(img.width<=max&&img.height<=max)||typeof createImageBitmap!=='function')return Promise.resolve(img);var k=max/Math.max(img.width,img.height);
  return createImageBitmap(img,{resizeWidth:Math.max(1,Math.round(img.width*k)),resizeHeight:Math.max(1,Math.round(img.height*k)),resizeQuality:'medium',premultiplyAlpha:'none',colorSpaceConversion:'none'})
   .then(function(b){if(img.close)img.close();return b;}).catch(function(){return img;});};
 T.get=function(path,srgb,mime,wrapS,wrapT,max){max=max||0;var key=path+'|'+(srgb?1:0)+'|'+(wrapS||0)+'|'+(wrapT||0)+(max?'|'+max:'');if(T.map[key])return T.map[key];
  var ext=(path.split('.').pop()||'').toLowerCase(),mm=mime||(ext==='jpg'||ext==='jpeg'?'image/jpeg':ext==='webp'?'image/webp':'image/png');
  T.map[key]=(max?T.gate():Promise.resolve()).then(function(){return R2.fetchBytes(path);}).then(function(b){return T.decode(b,mm);}).then(function(img){return T.shrink(img,max);})
   .then(function(img){var t=T.upload(img,srgb,wrapS,wrapT);if(img.close)img.close();return t;});return T.map[key];};
 T.fromBytes=function(bytes,mime,srgb,wrapS,wrapT,max){return(max?T.gate():Promise.resolve()).then(function(){return T.decode(bytes,mime);}).then(function(img){return T.shrink(img,max);})
  .then(function(img){var t=T.upload(img,srgb,wrapS,wrapT);if(img.close)img.close();return t;});};
 // 4-layer texture arrays for the terrain (same size per layer; smaller images are scaled with a canvas)
 T.array=function(paths,srgb,size){return Promise.all(paths.map(function(p){return R2.fetchBytes(p).then(function(b){return T.decode(b,'image/jpeg');});})).then(function(imgs){
  var t=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D_ARRAY,t);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,false);gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL,gl.NONE);
  var S=size||imgs[0].width;gl.texImage3D(gl.TEXTURE_2D_ARRAY,0,srgb?gl.SRGB8_ALPHA8:gl.RGBA8,S,S,imgs.length,0,gl.RGBA,gl.UNSIGNED_BYTE,null);
  imgs.forEach(function(im,i){var src=im;if(im.width!==S||im.height!==S){var c=document.createElement('canvas');c.width=c.height=S;c.getContext('2d').drawImage(im,0,0,S,S);src=c;}
   gl.texSubImage3D(gl.TEXTURE_2D_ARRAY,0,0,0,i,S,S,1,gl.RGBA,gl.UNSIGNED_BYTE,src);if(im.close)im.close();});
  gl.generateMipmap(gl.TEXTURE_2D_ARRAY);gl.texParameteri(gl.TEXTURE_2D_ARRAY,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);gl.texParameteri(gl.TEXTURE_2D_ARRAY,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D_ARRAY,gl.TEXTURE_WRAP_S,gl.REPEAT);gl.texParameteri(gl.TEXTURE_2D_ARRAY,gl.TEXTURE_WRAP_T,gl.REPEAT);if(T.maxAniso)gl.texParameterf(gl.TEXTURE_2D_ARRAY,T.aniso.TEXTURE_MAX_ANISOTROPY_EXT,T.maxAniso);return t;});};
 return T;};
// ---------- glTF 2.0 / GLB ----------
var GLTF_N={SCALAR:1,VEC2:2,VEC3:3,VEC4:4,MAT4:16},GLTF_CT={5120:Int8Array,5121:Uint8Array,5122:Int16Array,5123:Uint16Array,5125:Uint32Array,5126:Float32Array};
R2.parseGLB=function(buf){var dv=new DataView(buf);if(dv.getUint32(0,true)!==0x46546C67)return null;var off=12,json=null,bin=null;
 while(off<buf.byteLength){var n=dv.getUint32(off,true),t=dv.getUint32(off+4,true);if(t===0x4E4F534A)json=JSON.parse(new TextDecoder().decode(new Uint8Array(buf,off+8,n)));else if(t===0x004E4942)bin=buf.slice(off+8,off+8+n);off+=8+n;}
 return{json:json,bin:bin};};
R2.loadGLTF=function(path){return R2.fetchBytes(path).then(function(buf){var g=R2.parseGLB(buf),json,bin=null;
  if(g){json=g.json;bin=g.bin;}else json=JSON.parse(new TextDecoder().decode(buf));
  var ext=(json.extensionsRequired||[]).filter(function(e){return['KHR_materials_emissive_strength','KHR_texture_transform','KHR_materials_unlit','KHR_mesh_quantization'].indexOf(e)<0;});
  if(ext.length)throw new Error('this glTF needs '+ext.join(', ')+' (export it again without compression)');
  return Promise.all((json.buffers||[]).map(function(b,i){if(b.uri==null)return bin;return R2.fetchBytes(R2.resolve(path,b.uri));})).then(function(bufs){return R2.buildModel(json,bufs,path);});});};
R2.accessor=function(json,bufs,i){var a=json.accessors[i],n=GLTF_N[a.type],C=GLTF_CT[a.componentType],out;
 if(a.bufferView==null)out=new C(a.count*n);else{var bv=json.bufferViews[a.bufferView],buf=bufs[bv.buffer],off=(bv.byteOffset||0)+(a.byteOffset||0),es=C.BYTES_PER_ELEMENT,st=bv.byteStride||es*n;
  if(st===es*n&&off%es===0)out=new C(buf,off,a.count*n).slice();else{out=new C(a.count*n);var src=new DataView(buf),get={1:'getUint8',2:'getUint16',4:'getFloat32'}[es];if(C===Int8Array)get='getInt8';if(C===Int16Array)get='getInt16';if(C===Uint32Array)get='getUint32';
   for(var k=0;k<a.count;k++)for(var c=0;c<n;c++)out[k*n+c]=src[get](off+k*st+c*es,true);}}
 if(a.sparse){var sp=a.sparse,iv=json.bufferViews[sp.indices.bufferView],vv=json.bufferViews[sp.values.bufferView],IC=GLTF_CT[sp.indices.componentType];
  var idx=new IC(bufs[iv.buffer],(iv.byteOffset||0)+(sp.indices.byteOffset||0),sp.count),val=new C(bufs[vv.buffer],(vv.byteOffset||0)+(sp.values.byteOffset||0),sp.count*n);
  for(var s=0;s<sp.count;s++)for(var c2=0;c2<n;c2++)out[idx[s]*n+c2]=val[s*n+c2];}
 out.n=n;out.norm=!!a.normalized;out.ct=a.componentType;out.amin=a.min;out.amax=a.max;return out;};
R2.buildModel=function(json,bufs,path){var M={path:path,nodes:[],meshes:[],materials:[],skins:[],anims:[],images:[],roots:[],json:json};
 (json.images||[]).forEach(function(im){if(im.uri!=null)M.images.push({path:R2.resolve(path,im.uri),mime:im.mimeType});
  else{var bv=json.bufferViews[im.bufferView];M.images.push({bytes:new Uint8Array(bufs[bv.buffer],bv.byteOffset||0,bv.byteLength),mime:im.mimeType});}});
 function texRef(t){if(!t)return null;var tx=(json.textures||[])[t.index];if(!tx)return null;var src=tx.source;if(src==null&&tx.extensions){var e=tx.extensions.EXT_texture_webp||tx.extensions.KHR_texture_basisu;if(e)src=e.source;}
  var sm=(json.samplers||[])[tx.sampler]||{};return{img:src,wrapS:sm.wrapS,wrapT:sm.wrapT,uv:t.texCoord||0,scale:t.scale,strength:t.strength};}
 (json.materials||[]).forEach(function(m){var pb=m.pbrMetallicRoughness||{},ex=m.extensions||{},es=ex.KHR_materials_emissive_strength;var nm=String(m.name||'');
  M.materials.push({name:nm,base:pb.baseColorFactor||[1,1,1,1],baseTex:texRef(pb.baseColorTexture),metal:pb.metallicFactor==null?1:pb.metallicFactor,rough:pb.roughnessFactor==null?1:pb.roughnessFactor,
   mrTex:texRef(pb.metallicRoughnessTexture),normTex:texRef(m.normalTexture),occTex:texRef(m.occlusionTexture),emis:(m.emissiveFactor||[0,0,0]).map(function(v){return v*(es?es.emissiveStrength:1);}),emisTex:texRef(m.emissiveTexture),
   alpha:m.alphaMode||'OPAQUE',cutoff:m.alphaCutoff==null?0.5:m.alphaCutoff,ds:!!m.doubleSided,unlit:!!ex.KHR_materials_unlit,
   foliage:/leaf|leave|grass|flower|fern|plant|clover|bush|vine|petal|needle|foliage/i.test(nm)||(m.alphaMode==='MASK'&&!/bark|trunk|wood/i.test(nm)),glass:/glass/i.test(nm),
   extras:m.extras||{}});});
 (json.meshes||[]).forEach(function(me){M.meshes.push({name:me.name||'',prims:(me.primitives||[]).map(function(p){var at={};for(var k in p.attributes)at[k]=R2.accessor(json,bufs,p.attributes[k]);
  return{attrs:at,idx:p.indices!=null?R2.accessor(json,bufs,p.indices):null,mode:p.mode==null?4:p.mode,mat:p.material==null?-1:p.material};})});});
 (json.nodes||[]).forEach(function(n,i){var mt=n.matrix,t=n.translation||[0,0,0],r=n.rotation||[0,0,0,1],s=n.scale||[1,1,1];
  if(mt){t=[mt[12],mt[13],mt[14]];var sx=Math.hypot(mt[0],mt[1],mt[2]),sy=Math.hypot(mt[4],mt[5],mt[6]),sz=Math.hypot(mt[8],mt[9],mt[10]);s=[sx,sy,sz];
   var m00=mt[0]/sx,m11=mt[5]/sy,m22=mt[10]/sz,tr=m00+m11+m22,q;if(tr>0){var S=Math.sqrt(tr+1)*2;q=[(mt[6]/sy-mt[9]/sz)/S,(mt[8]/sz-mt[2]/sx)/S,(mt[1]/sx-mt[4]/sy)/S,0.25*S];}
   else if(m00>m11&&m00>m22){var S1=Math.sqrt(1+m00-m11-m22)*2;q=[0.25*S1,(mt[4]/sy+mt[1]/sx)/S1,(mt[8]/sz+mt[2]/sx)/S1,(mt[6]/sy-mt[9]/sz)/S1];}
   else if(m11>m22){var S2=Math.sqrt(1+m11-m00-m22)*2;q=[(mt[4]/sy+mt[1]/sx)/S2,0.25*S2,(mt[9]/sz+mt[6]/sy)/S2,(mt[8]/sz-mt[2]/sx)/S2];}
   else{var S3=Math.sqrt(1+m22-m00-m11)*2;q=[(mt[8]/sz+mt[2]/sx)/S3,(mt[9]/sz+mt[6]/sy)/S3,0.25*S3,(mt[1]/sx-mt[4]/sy)/S3];}r=q;}
  M.nodes.push({name:n.name||('node'+i),mesh:n.mesh==null?-1:n.mesh,skin:n.skin==null?-1:n.skin,children:n.children||[],parent:-1,t:t.slice(),r:r.slice(),s:s.slice()});});
 M.nodes.forEach(function(n,i){n.children.forEach(function(c){if(M.nodes[c])M.nodes[c].parent=i;});});
 var sc=(json.scenes||[])[json.scene||0];M.roots=sc?sc.nodes.slice():M.nodes.map(function(n,i){return n.parent<0?i:-1;}).filter(function(i){return i>=0;});
 (json.skins||[]).forEach(function(s){M.skins.push({joints:s.joints.slice(),ibm:s.inverseBindMatrices!=null?R2.accessor(json,bufs,s.inverseBindMatrices):null});});
 (json.animations||[]).forEach(function(a,ai){var ch=[],dur=0;(a.channels||[]).forEach(function(c){var smp=a.samplers[c.sampler];if(!c.target||c.target.node==null)return;var tm=R2.accessor(json,bufs,smp.input),vl=R2.accessor(json,bufs,smp.output);
  if(vl.norm&&!(vl instanceof Float32Array)){var f=new Float32Array(vl.length),dv=vl instanceof Int16Array?32767:vl instanceof Int8Array?127:vl instanceof Uint16Array?65535:255;for(var i=0;i<vl.length;i++)f[i]=Math.max(-1,vl[i]/dv);vl=f;}
  dur=Math.max(dur,tm[tm.length-1]||0);ch.push({node:c.target.node,name:(M.nodes[c.target.node]||{}).name,path:c.target.path,t:tm,v:vl,interp:smp.interpolation||'LINEAR'});});
  M.anims.push({name:a.name||('anim'+ai),dur:dur,ch:ch});});
 // rest-pose globals + bounds
 M.global=function(local){var G=new Array(M.nodes.length);function walk(i,pm){var n=M.nodes[i],l=local?local[i]:m4trs(n.t,n.r,n.s);G[i]=pm?mul(pm,l):l;n.children.forEach(function(c){walk(c,G[i]);});}M.roots.forEach(function(r){walk(r,null);});
  for(var i=0;i<G.length;i++)if(!G[i])G[i]=m4trs(M.nodes[i].t,M.nodes[i].r,M.nodes[i].s);return G;};
 M.rest=M.global();var lo=[1e9,1e9,1e9],hi=[-1e9,-1e9,-1e9];
 M.nodes.forEach(function(n,i){if(n.mesh<0)return;var gm=n.skin>=0?m4():M.rest[i];M.meshes[n.mesh].prims.forEach(function(p){var P=p.attrs.POSITION;if(!P)return;var a=P.amin,b=P.amax;
  if(!a||!b){a=[1e9,1e9,1e9];b=[-1e9,-1e9,-1e9];for(var k=0;k<P.length;k+=3)for(var c=0;c<3;c++){a[c]=Math.min(a[c],P[k+c]);b[c]=Math.max(b[c],P[k+c]);}}
  for(var x=0;x<2;x++)for(var y=0;y<2;y++)for(var z=0;z<2;z++){var q=m4pt(gm,[x?b[0]:a[0],y?b[1]:a[1],z?b[2]:a[2]]);for(var c3=0;c3<3;c3++){lo[c3]=Math.min(lo[c3],q[c3]);hi[c3]=Math.max(hi[c3],q[c3]);}}});});
 M.bounds=[lo,hi];M.byName={};M.nodes.forEach(function(n,i){M.byName[n.name]=i;});return M;};
// ---------- Radiance .hdr (RGBE, RLE) -> Float32 RGB + sun estimate ----------
R2.parseHDR=function(buf){var u=new Uint8Array(buf),p=0;function line(){var s='';while(p<u.length&&u[p]!==10)s+=String.fromCharCode(u[p++]);p++;return s;}
 var l=line();if(!/^#\?(RADIANCE|RGBE)/.test(l))throw new Error('not a Radiance .hdr file');while((l=line())!==''&&p<u.length){}
 var m=/-Y (\d+) \+X (\d+)/.exec(line());if(!m)throw new Error('unsupported .hdr orientation');var H=+m[1],W=+m[2],out=new Float32Array(W*H*3),sc=new Uint8Array(W*4);
 for(var y=0;y<H;y++){if(u[p]===2&&u[p+1]===2&&!(u[p+2]&128)){p+=4;for(var c=0;c<4;c++){var x=0;while(x<W){var n=u[p++];if(n>128){n-=128;var v=u[p++];while(n--)sc[(x++)*4+c]=v;}else while(n--)sc[(x++)*4+c]=u[p++];}}}
  else{for(var x2=0;x2<W;x2++){sc[x2*4]=u[p++];sc[x2*4+1]=u[p++];sc[x2*4+2]=u[p++];sc[x2*4+3]=u[p++];}}
  for(var x3=0;x3<W;x3++){var e=sc[x3*4+3],f=e?Math.pow(2,e-136):0,o=(y*W+x3)*3;out[o]=sc[x3*4]*f;out[o+1]=sc[x3*4+1]*f;out[o+2]=sc[x3*4+2]*f;}}
 // sun: brightest 4x4 block; irradiance of every pixel within ~3 deg of it; average luminance of the rest
 var best=-1,bx=0,by=0,B=4;for(var yy=0;yy<H/2;yy+=B)for(var xx=0;xx<W;xx+=B){var s=0;for(var j=0;j<B;j++)for(var i=0;i<B;i++){var q=((yy+j)*W+xx+i)*3;s+=out[q]*0.2126+out[q+1]*0.7152+out[q+2]*0.0722;}if(s>best){best=s;bx=xx+B/2;by=yy+B/2;}}
 function dir(x,y){var ph=(x/W-0.5)*2*Math.PI,th=(0.5-y/H)*Math.PI;return[Math.cos(th)*Math.sin(ph),Math.sin(th),-Math.cos(th)*Math.cos(ph)];}
 var sd=dir(bx,by),sun=[0,0,0],tot=0,cnt=0,lum=0,lumN=0,cut=Math.cos(3*Math.PI/180),up=[0,0,0],upW=0,dn=[0,0,0],dnW=0;
 for(var y4=0;y4<H;y4+=2)for(var x4=0;x4<W;x4+=2){var d=dir(x4+0.5,y4+0.5),q4=(y4*W+x4)*3,L=out[q4]*0.2126+out[q4+1]*0.7152+out[q4+2]*0.0722,dw=4*(2*Math.PI/W)*(Math.PI/H)*Math.cos((0.5-(y4+0.5)/H)*Math.PI);
  if(d[0]*sd[0]+d[1]*sd[1]+d[2]*sd[2]>cut){sun[0]+=out[q4]*dw;sun[1]+=out[q4+1]*dw;sun[2]+=out[q4+2]*dw;cnt++;}else{lum+=L*dw;lumN+=dw;
  if(d[1]>=0){up[0]+=out[q4]*dw;up[1]+=out[q4+1]*dw;up[2]+=out[q4+2]*dw;upW+=dw;}else{dn[0]+=out[q4]*dw;dn[1]+=out[q4+1]*dw;dn[2]+=out[q4+2]*dw;dnW+=dw;}}}
 return{w:W,h:H,data:out,sunDir:sd,sunIrr:sun,avg:lumN?lum/lumN:1,up:up.map(function(v){return upW?v/upW:0;}),dn:dn.map(function(v){return dnW?v/dnW:0;})};};
