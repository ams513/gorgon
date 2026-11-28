// ========== Stark World Engine Render 2: core math + GL helpers (inside the GS module; uses rng/norm/mul/... from above) ==========
var R2={};
function m4(){return[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1];}
function m4inv(m){var a00=m[0],a01=m[1],a02=m[2],a03=m[3],a10=m[4],a11=m[5],a12=m[6],a13=m[7],a20=m[8],a21=m[9],a22=m[10],a23=m[11],a30=m[12],a31=m[13],a32=m[14],a33=m[15];
 var b00=a00*a11-a01*a10,b01=a00*a12-a02*a10,b02=a00*a13-a03*a10,b03=a01*a12-a02*a11,b04=a01*a13-a03*a11,b05=a02*a13-a03*a12,b06=a20*a31-a21*a30,b07=a20*a32-a22*a30,b08=a20*a33-a23*a30,b09=a21*a32-a22*a31,b10=a21*a33-a23*a31,b11=a22*a33-a23*a32;
 var d=b00*b11-b01*b10+b02*b09+b03*b08-b04*b07+b05*b06;if(!d)return m4();d=1/d;
 return[(a11*b11-a12*b10+a13*b09)*d,(a02*b10-a01*b11-a03*b09)*d,(a31*b05-a32*b04+a33*b03)*d,(a22*b04-a21*b05-a23*b03)*d,(a12*b08-a10*b11-a13*b07)*d,(a00*b11-a02*b08+a03*b07)*d,(a32*b02-a30*b05-a33*b01)*d,(a20*b05-a22*b02+a23*b01)*d,
  (a10*b10-a11*b08+a13*b06)*d,(a01*b08-a00*b10-a03*b06)*d,(a30*b04-a31*b02+a33*b00)*d,(a21*b02-a20*b04-a23*b00)*d,(a11*b07-a10*b09-a12*b06)*d,(a00*b09-a01*b07+a02*b06)*d,(a31*b01-a30*b03-a32*b00)*d,(a20*b03-a21*b01+a22*b00)*d];}
function m4trs(t,q,s){var x=q[0],y=q[1],z=q[2],w=q[3],x2=x+x,y2=y+y,z2=z+z,xx=x*x2,xy=x*y2,xz=x*z2,yy=y*y2,yz=y*z2,zz=z*z2,wx=w*x2,wy=w*y2,wz=w*z2,sx=s[0],sy=s[1],sz=s[2];
 return[(1-(yy+zz))*sx,(xy+wz)*sx,(xz-wy)*sx,0,(xy-wz)*sy,(1-(xx+zz))*sy,(yz+wx)*sy,0,(xz+wy)*sz,(yz-wx)*sz,(1-(xx+yy))*sz,0,t[0],t[1],t[2],1];}
function m4yaw(x,y,z,yaw,s,sy){var c=Math.cos(yaw),n=Math.sin(yaw);sy=sy==null?s:sy;return[c*s,0,-n*s,0,0,sy,0,0,n*s,0,c*s,0,x,y,z,1];}
function m4pt(m,p){return[m[0]*p[0]+m[4]*p[1]+m[8]*p[2]+m[12],m[1]*p[0]+m[5]*p[1]+m[9]*p[2]+m[13],m[2]*p[0]+m[6]*p[1]+m[10]*p[2]+m[14]];}
function m4pt4(m,p){return[m[0]*p[0]+m[4]*p[1]+m[8]*p[2]+m[12]*p[3],m[1]*p[0]+m[5]*p[1]+m[9]*p[2]+m[13]*p[3],m[2]*p[0]+m[6]*p[1]+m[10]*p[2]+m[14]*p[3],m[3]*p[0]+m[7]*p[1]+m[11]*p[2]+m[15]*p[3]];}
function qslerp(a,b,t){var d=a[0]*b[0]+a[1]*b[1]+a[2]*b[2]+a[3]*b[3],s=1;if(d<0){d=-d;s=-1;}var k0,k1;if(d>0.9995){k0=1-t;k1=t*s;}else{var th=Math.acos(d),sn=Math.sin(th);k0=Math.sin((1-t)*th)/sn;k1=Math.sin(t*th)/sn*s;}
 var o=[a[0]*k0+b[0]*k1,a[1]*k0+b[1]*k1,a[2]*k0+b[2]*k1,a[3]*k0+b[3]*k1],l=Math.hypot(o[0],o[1],o[2],o[3])||1;return[o[0]/l,o[1]/l,o[2]/l,o[3]/l];}
function qaxis(ax,a){var s=Math.sin(a/2);return[ax[0]*s,ax[1]*s,ax[2]*s,Math.cos(a/2)];}
function qmul(a,b){return[a[3]*b[0]+a[0]*b[3]+a[1]*b[2]-a[2]*b[1],a[3]*b[1]-a[0]*b[2]+a[1]*b[3]+a[2]*b[0],a[3]*b[2]+a[0]*b[1]-a[1]*b[0]+a[2]*b[3],a[3]*b[3]-a[0]*b[0]-a[1]*b[1]-a[2]*b[2]];}
function srgb2lin(c){return c.map(function(v){return v<=0.04045?v/12.92:Math.pow((v+0.055)/1.055,2.4);});}
function halton(i,b){var f=1,r=0;while(i>0){f/=b;r+=f*(i%b);i=Math.floor(i/b);}return r;}
function hash2(x,z){var h=Math.sin(x*127.1+z*311.7)*43758.5453;return h-Math.floor(h);}
function vnoise2(x,z){var ix=Math.floor(x),iz=Math.floor(z),fx=x-ix,fz=z-iz;fx=fx*fx*(3-2*fx);fz=fz*fz*(3-2*fz);
 return lerp(lerp(hash2(ix,iz),hash2(ix+1,iz),fx),lerp(hash2(ix,iz+1),hash2(ix+1,iz+1),fx),fz);}
function fbm2(x,z,o){var s=0,a=0.5,f=1;for(var i=0;i<(o||4);i++){s+=a*vnoise2(x*f,z*f);f*=2.03;a*=0.5;}return s;}
// ---------- GL helpers ----------
R2.compile=function(gl,vs,fs,defs,names){var pre='#version 300 es\n'+(defs||[]).map(function(d){return'#define '+d+'\n';}).join('');
 function sh(t,src){var s=gl.createShader(t);gl.shaderSource(s,pre+src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)){var log=gl.getShaderInfoLog(s);gl.deleteShader(s);throw new Error('shader: '+log);}return s;}
 var p=gl.createProgram(),a=sh(gl.VERTEX_SHADER,vs),b=sh(gl.FRAGMENT_SHADER,fs);gl.attachShader(p,a);gl.attachShader(p,b);gl.linkProgram(p);gl.deleteShader(a);gl.deleteShader(b);
 if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw new Error('link: '+gl.getProgramInfoLog(p));
 var U={p:p},n=gl.getProgramParameter(p,gl.ACTIVE_UNIFORMS);for(var i=0;i<n;i++){var info=gl.getActiveUniform(p,i),nm=info.name.replace(/\[0\]$/,'');U[nm]=gl.getUniformLocation(p,info.name);}
 (names||[]).forEach(function(k){if(!(k in U))U[k]=null;});return U;};
R2.tex2d=function(gl,w,h,ifmt,fmt,type,opt){opt=opt||{};var t=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,t);gl.texImage2D(gl.TEXTURE_2D,0,ifmt,w,h,0,fmt,type,opt.data||null);
 var f=opt.filter||gl.LINEAR;gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,f);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,f===gl.LINEAR_MIPMAP_LINEAR?gl.LINEAR:f);
 var wr=opt.wrap||gl.CLAMP_TO_EDGE;gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,wr);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,wr);t.w=w;t.h=h;return t;};
R2.fbo=function(gl,color,depth){var f=gl.createFramebuffer();gl.bindFramebuffer(gl.FRAMEBUFFER,f);if(color)gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,color,0);
 if(depth)gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.DEPTH_ATTACHMENT,gl.TEXTURE_2D,depth,0);f.ok=gl.checkFramebufferStatus(gl.FRAMEBUFFER)===gl.FRAMEBUFFER_COMPLETE;gl.bindFramebuffer(gl.FRAMEBUFFER,null);f.color=color;f.depth=depth;return f;};
// quality presets: everything the renderer scales for weaker GPUs lives here. Low (older laptops, integrated graphics,
// software rendering): no SSAO, no shadows (so no soft-shadow filtering), no TAA, no image-based lighting bake (ibl:0 = a
// flat sky/ground ambient), no eye adaptation / sun shafts / grain, lazy textures (lazyTex: loaded after the first frames,
// one per frame, at most texMax px, no normal/ARM maps) and a sharpened half-resolution image.
// ae: eye adaptation, shafts: god-ray samples (0 = off), grain: film grain, pcss: contact-hardening soft shadows,
// sharp: sharpening after FXAA / TAA, mtn: distant mountain layers. Ultra = High + 1.5x supersampling, PCSS, more of all.
R2.QUALITY={
 low:{cullDist:55,scale:0.5,cascades:1,shadow:1,pcf:1,ssao:0,bloomMips:0,aa:'fxaa',grass:0.06,scatter:0.25,refract:0,ssr:0,ibl:0,env:0,envSamples:0,lights:6,dprCap:1,foliageShadow:0,taa:0,ring:0,lazyTex:1,texMax:256,ae:0,shafts:0,grain:0,pcss:0,sharp:0.55,mtn:1},
 medium:{scatter:0.75,ring:0.8,scale:0.85,cascades:2,shadow:2048,pcf:8,ssao:8,bloomMips:5,aa:'fxaa',grass:0.65,refract:1,ssr:0,ibl:1,env:128,envSamples:48,lights:12,dprCap:1,foliageShadow:1,taa:0,lazyTex:0,texMax:0,ae:1,shafts:24,grain:0,pcss:0,sharp:0.3,mtn:2},
 high:{scatter:1,ring:1,scale:1,cascades:3,shadow:4096,pcf:16,ssao:12,bloomMips:6,aa:'taa',grass:1.35,refract:1,ssr:1,ibl:1,env:256,envSamples:64,lights:16,dprCap:1.5,foliageShadow:1,taa:1,lazyTex:0,texMax:0,ae:1,shafts:40,grain:1,pcss:0,sharp:0.35,mtn:2},
 ultra:{scatter:1.15,ring:1.2,scale:1.5,cascades:3,shadow:4096,pcf:16,ssao:16,bloomMips:7,aa:'taa',grass:1.8,refract:1,ssr:1,ibl:1,env:256,envSamples:96,lights:16,dprCap:2,foliageShadow:1,taa:1,lazyTex:0,texMax:0,ae:1,shafts:64,grain:1,pcss:1,sharp:0.25,mtn:2}};
R2.QORDER=['low','medium','high','ultra'];
R2.gpuInfo=function(gl){var s='';try{var e=gl.getExtension('WEBGL_debug_renderer_info');s=e?gl.getParameter(e.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);}catch(err){}
 s=String(s||'');return{name:s,software:/swiftshader|llvmpipe|software|basic render|microsoft basic/i.test(s)};};
// hardware check (first start): the GPU name picks the starting preset for 'auto'. Software rendering (SwiftShader,
// llvmpipe, Microsoft Basic Render) and old integrated chips -> low; GeForce RTX / Radeon RX 6000+ / Arc A7xx / Apple M Pro-Max -> high;
// everything else -> medium. The pick is remembered per GPU (localStorage gs_hw); Graphics > low/medium/high overrides it.
R2.hwClass=function(name){var s=String(name||'');
 if(/swiftshader|llvmpipe|softpipe|software|basic render|microsoft basic/i.test(s))return{preset:'low',why:'software rendering'};
 if(/\bRTX\b|Radeon(\(TM\))? RX ?[6-9]\d{3}|Radeon RX 7\d{2}M|Arc(\(TM\))? A7\d\d|Apple M\d+ (Pro|Max|Ultra)/i.test(s))return{preset:'high',why:'fast graphics card'};
 if(/Intel(\(R\))? (HD Graphics( [2-5]\d{2,3})?\b(?! ?6)|GMA)|Mali-[4T][0-9]|Adreno(\(TM\))? [3-5]\d\d|PowerVR/i.test(s))return{preset:'low',why:'older graphics chip'};
 return{preset:'medium',why:'graphics card'};};
R2.hwPick=function(info){var name=info&&info.name||'',c=R2.hwClass(name),st=null;try{st=JSON.parse(localStorage.getItem('gs_hw')||'null');}catch(e){st=null;}
 if(st&&st.v===1&&st.name===name&&R2.QUALITY[st.preset])return{preset:st.preset,why:st.why,name:name,first:false};
 try{localStorage.setItem('gs_hw',JSON.stringify({v:1,name:name,preset:c.preset,why:c.why}));}catch(e){}
 return{preset:c.preset,why:c.why,name:name,first:true};};
// Auto: measured frame time (seconds, averaged over ~2.5 s of play) steps the preset along low < medium < high < ultra.
// Under 20 fps -> low; under ~29 fps -> one step down; Ultra drops to High under 54 fps. Upgrades need sustained headroom
// (medium -> high above 77 fps, high -> ultra above 133 fps, i.e. only with an unlocked / high-refresh display) and are
// blocked for the session once Auto had to step down (noUp), so it never oscillates.
R2.autoStep=function(q,avg,frames,noUp){var O=R2.QORDER,i=O.indexOf(q);if(i<0)return q;
 if(avg>0.05&&q!=='low')return'low';if(avg>0.034&&i>0)return O[i-1];if(q==='ultra'&&avg>0.0185)return'high';
 if(!noUp&&q==='medium'&&avg<0.013&&frames>200)return'high';if(!noUp&&q==='high'&&avg<0.0075&&frames>400)return'ultra';return q;};
