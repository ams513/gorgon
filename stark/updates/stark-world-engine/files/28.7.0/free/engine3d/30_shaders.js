// ========== shaders: PBR (GGX + IBL), cascaded soft shadows, fog with sun in-scatter, sky, IBL baking, water, FX ==========
var GL_COMMON=`precision highp float;precision highp int;precision highp sampler2DShadow;precision highp sampler2DArray;
#define PI 3.14159265
uniform vec3 uSunDir;uniform vec3 uSunCol;uniform vec3 uEye;uniform float uTime;
uniform samplerCube uEnvSpec;uniform samplerCube uEnvIrr;uniform float uEnvMips;uniform float uEnvK;
uniform vec3 uFogCol;uniform vec3 uFogSun;uniform float uFogDen;uniform float uFogH;uniform float uFogY;
uniform highp sampler2DShadow uShadow;uniform mat4 uShadowM[3];uniform vec4 uCasc;uniform int uShadowN;uniform float uShadowTx;uniform int uPcf;
uniform int uNL;uniform vec4 uLP[16];uniform vec3 uLC[16];uniform float uAerial;
#ifdef PCSS
uniform highp sampler2D uShadowRaw;uniform vec4 uCascR;uniform vec4 uCascZ;uniform float uSunAng;
#endif
float ign(vec2 p){return fract(52.9829189*fract(dot(p,vec2(0.06711056,0.00583715))));}
vec3 F_Schlick(vec3 f0,float vh){float k=pow(1.0-vh,5.0);return f0+(1.0-f0)*k;}
float D_GGX(float nh,float a){float a2=a*a;float d=nh*nh*(a2-1.0)+1.0;return a2/(PI*d*d+1e-7);}
float V_Smith(float nv,float nl,float a){float a2=a*a;float gv=nl*sqrt(nv*nv*(1.0-a2)+a2);float gl=nv*sqrt(nl*nl*(1.0-a2)+a2);return 0.5/max(gv+gl,1e-5);}
vec3 envBRDF(vec3 f0,float r,float nv){vec4 c0=vec4(-1.0,-0.0275,-0.572,0.022);vec4 c1=vec4(1.0,0.0425,1.04,-0.04);vec4 q=r*c0+c1;float a004=min(q.x*q.x,exp2(-9.28*nv))*q.x+q.y;vec2 AB=vec2(-1.04,1.04)*a004+q.zw;return f0*AB.x+AB.y;}
const vec2 PD[16]=vec2[16](vec2(-0.94201624,-0.39906216),vec2(0.94558609,-0.76890725),vec2(-0.09418410,-0.92938870),vec2(0.34495938,0.29387760),vec2(-0.91588581,0.45771432),vec2(-0.81544232,-0.87912464),vec2(-0.38277543,0.27676845),vec2(0.97484398,0.75648379),
 vec2(0.44323325,-0.97511554),vec2(0.53742981,-0.47373420),vec2(-0.26496911,-0.41893023),vec2(0.79197514,0.19090188),vec2(-0.24188840,0.99706507),vec2(-0.81409955,0.91437590),vec2(0.19984126,0.78641367),vec2(0.14383161,-0.14100790));
#ifdef LITE
float shadowAt(vec3 P,vec3 Ng,float dist){return 1.0;}   // Low: no shadow map, so no soft-shadow filtering either
#else
float shadowAt(vec3 P,vec3 Ng,float dist){if(uShadowN<=0)return 1.0;int c=dist<uCasc.x?0:(dist<uCasc.y?1:2);if(c>=uShadowN)return 1.0;
 float kc=c==0?1.0:(c==1?2.2:5.0);vec4 s=uShadowM[c]*vec4(P+Ng*(0.035*kc)+uSunDir*(0.02*kc),1.0);vec3 q=s.xyz;
 vec2 tile=vec2(float(c%2),float(c/2))*0.5;vec2 lo=tile+uShadowTx*2.0,hi=tile+0.5-uShadowTx*2.0;if(q.x<lo.x||q.y<lo.y||q.x>hi.x||q.y>hi.y||q.z>=1.0)return 1.0;
 float a=ign(gl_FragCoord.xy)*6.2831853;mat2 rot=mat2(cos(a),sin(a),-sin(a),cos(a));float sum=0.0;float rad=uShadowTx*(c==0?2.2:1.6);
#ifdef PCSS
 // Ultra: percentage-closer soft shadows. A blocker search sizes the filter, so contact shadows stay crisp and
 // shadows from tall things soften with distance (sun angular size uSunAng, cascade radius/depth range per cascade).
 {float bs=0.0,bn=0.0;float sr=uShadowTx*(c==0?12.0:7.0);float zr=q.z-0.0004*kc;
  for(int i=0;i<8;i++){vec2 o=rot*PD[i*2+1]*sr;float d=texture(uShadowRaw,clamp(q.xy+o,lo,hi)).r;if(d<zr){bs+=d;bn+=1.0;}}
  if(bn>0.5){float cr=c==0?uCascR.x:(c==1?uCascR.y:uCascR.z);float cz=c==0?uCascZ.x:(c==1?uCascZ.y:uCascZ.z);
   float pen=(zr-bs/bn)*cz*uSunAng;rad=clamp(pen/(4.0*cr),uShadowTx*1.2,uShadowTx*(c==0?14.0:8.0));}}
#endif
 for(int i=0;i<16;i++){if(i>=uPcf)break;vec2 o=rot*PD[i]*rad;sum+=texture(uShadow,vec3(clamp(q.xy+o,lo,hi),q.z-0.0004*kc));}
 float sh=sum/float(uPcf);float edge=c==uShadowN-1?smoothstep(uCasc[c]*0.8,uCasc[c],dist):0.0;return mix(sh,1.0,edge);}
#endif
vec3 fogApply(vec3 c,vec3 P,vec3 V){float d=length(P-uEye);float h0=max(uEye.y-uFogY,0.0),h1=max(P.y-uFogY,0.0);
 float fh=uFogH>0.0?exp(-uFogH*min(h0,h1))*(abs(h1-h0)>0.01?(1.0-exp(-uFogH*abs(h1-h0)))/(uFogH*abs(h1-h0)):1.0):1.0;
 vec3 f=1.0-exp(-d*uFogDen*fh*mix(vec3(1.0),vec3(0.72,1.0,1.42),uAerial));float sunK=pow(max(dot(-V,uSunDir),0.0),8.0);vec3 fc=uFogCol+uFogSun*sunK;return mix(c,fc,clamp(f,0.0,1.0));}   // aerial perspective: blue scatters first
vec3 pointLights(vec3 P,vec3 N,vec3 V,vec3 diff,vec3 f0,float a){vec3 c=vec3(0);for(int i=0;i<16;i++){if(i>=uNL)break;vec3 d=uLP[i].xyz-P;float dl=length(d);float r=uLP[i].w;
 float att=clamp(1.0-pow(dl/r,4.0),0.0,1.0);att=att*att/(dl*dl+1.0);if(att<=0.0)continue;vec3 L=d/dl;float nl=max(dot(N,L),0.0);vec3 H=normalize(L+V);
 vec3 F=F_Schlick(f0,max(dot(V,H),0.0));c+=(diff/PI*(1.0-F)+D_GGX(max(dot(N,H),0.0),a)*V_Smith(max(dot(N,V),1e-4),nl,a)*F)*uLC[i]*nl*att*4.0;}return c;}
vec3 shadePBR(vec3 P,vec3 N,vec3 V,vec3 albedo,float metal,float rough,float ao,float sh,float trans){
#ifdef LITE
 {vec3 dl=albedo*(1.0-metal);float nl0=max(dot(N,uSunDir),0.0);vec3 h0=normalize(uSunDir+V);float sp=pow(max(dot(N,h0),0.0),mix(96.0,8.0,rough))*(1.0-rough)*0.3;
  vec3 c0=(dl/PI+sp*mix(vec3(0.04),albedo,metal))*uSunCol*nl0*sh;if(trans>0.0)c0+=dl*uSunCol*trans*max(dot(-N,uSunDir),0.0)*sh*0.25/PI;
  return c0+dl*texture(uEnvIrr,N).rgb*ao*uEnvK;}
#endif
 vec3 f0=mix(vec3(0.04),albedo,metal);vec3 diff=albedo*(1.0-metal);float nv=max(dot(N,V),1e-4);float a=max(rough*rough,0.002);
 vec3 L=uSunDir;vec3 H=normalize(L+V);float nl=max(dot(N,L),0.0);vec3 F=F_Schlick(f0,max(dot(V,H),0.0));
 vec3 c=(diff*(1.0-F)/PI+D_GGX(max(dot(N,H),0.0),a)*V_Smith(nv,nl,a)*F)*uSunCol*nl*sh;
 if(trans>0.0){float bl=pow(max(dot(-V,L),0.0),4.0)*0.6+0.25;c+=diff*uSunCol*trans*max(dot(-N,L),0.0)*sh*bl/PI;}
 vec3 irr=texture(uEnvIrr,N).rgb;
 vec3 R=reflect(-V,N);vec3 pre=textureLod(uEnvSpec,R,rough*uEnvMips).rgb;vec3 brdf=envBRDF(f0,rough,nv);
 float so=clamp(pow(nv+ao,exp2(-16.0*rough-1.0))-1.0+ao,0.0,1.0);float hor=clamp(1.0+dot(R,N),0.0,1.0);
 c+=(diff*irr*(0.6+0.4*sh)+pre*brdf*so*hor*hor)*ao*uEnvK;c+=pointLights(P,N,V,diff,f0,a);return c;}
vec3 perturbN(vec3 N,vec3 p,vec2 uv,vec3 t){vec3 dp1=dFdx(p),dp2=dFdy(p);vec2 du1=dFdx(uv),du2=dFdy(uv);vec3 dp2p=cross(dp2,N),dp1p=cross(N,dp1);
 vec3 T=dp2p*du1.x+dp1p*du2.x;vec3 B=dp2p*du1.y+dp1p*du2.y;float im=inversesqrt(max(max(dot(T,T),dot(B,B)),1e-20));return normalize(mat3(T*im,B*im,N)*t);}
`;
// ---- standard meshes (static instanced, dynamic, skinned) ----
var VS_MESH=`precision highp float;precision highp int;
layout(location=0) in vec3 aPos;layout(location=1) in vec3 aNor;layout(location=2) in vec2 aUV;layout(location=3) in vec4 aCol;layout(location=4) in vec4 aJ;layout(location=5) in vec4 aW;
layout(location=8) in vec4 iM0;layout(location=9) in vec4 iM1;layout(location=10) in vec4 iM2;layout(location=11) in vec4 iM3;layout(location=12) in vec4 iTint;
uniform mat4 uVP;uniform mat4 uModel;uniform vec4 uTintU;uniform float uTime;uniform vec4 uWind;uniform float uWindH;uniform vec3 uEyeV;uniform float uFade;
#ifdef SKIN
uniform highp sampler2D uJT;uniform int uJRow;
mat4 jm(int j){int x=j*4;return mat4(texelFetch(uJT,ivec2(x,uJRow),0),texelFetch(uJT,ivec2(x+1,uJRow),0),texelFetch(uJT,ivec2(x+2,uJRow),0),texelFetch(uJT,ivec2(x+3,uJRow),0));}
#endif
out vec3 vW;out vec3 vN;out vec2 vUV;out vec4 vCol;out vec3 vTint;out float vDist;out float vHgt;
void main(){vec4 p=vec4(aPos,1.0);vec3 n=aNor;vHgt=1.0;
#ifdef SKIN
 mat4 sk=aW.x*jm(int(aJ.x))+aW.y*jm(int(aJ.y))+aW.z*jm(int(aJ.z))+aW.w*jm(int(aJ.w));p=sk*p;n=mat3(sk)*n;
#endif
#ifdef INST
 mat4 M=mat4(iM0,iM1,iM2,iM3);vec4 tint=iTint;
#else
 mat4 M=uModel;vec4 tint=uTintU;
#endif
 vec4 w=M*p;vec3 wn=normalize(mat3(M)*n);
#ifdef WIND
 float h=clamp(aPos.y*uWindH,0.0,1.5);float k=h*h*uWind.z*(0.6+0.4*tint.a);float ph=uTime*1.7+M[3].x*0.37+M[3].z*0.29;
 vec2 sw=uWind.xy*(sin(ph)*0.55+sin(ph*2.3+aPos.x*1.7)*0.18+uWind.w)+vec2(sin(uTime*3.3+w.x*2.1+w.z),cos(uTime*2.9+w.z*1.9))*0.06*uWind.z;
 w.xz+=sw*k;w.y-=dot(sw,sw)*k*0.25;vHgt=h;
#endif
#ifdef FADE
 float fd=length(w.xyz-uEyeV);float lodk=fract(sin(dot(M[3].xz,vec2(12.9898,78.233)))*43758.5453)<0.5?0.62:1.0;float fs=1.0-smoothstep(uFade*0.75*lodk,uFade*lodk,fd);w.xyz=mix(M[3].xyz,w.xyz,fs);   // density LOD: far away half the blades shrink out early
#endif
 vW=w.xyz;vN=wn;vUV=aUV;vCol=aCol;vTint=tint.rgb;vDist=length(w.xyz-uEyeV);gl_Position=uVP*w;}`;
var FS_MESH=`
in vec3 vW;in vec3 vN;in vec2 vUV;in vec4 vCol;in vec3 vTint;in float vDist;in float vHgt;
uniform sampler2D uBase;uniform sampler2D uMR;uniform sampler2D uNorm;uniform sampler2D uOcc;uniform sampler2D uEmis;
uniform vec4 uBaseF;uniform vec3 uEmisF;uniform vec2 uMRF;uniform float uNormS;uniform float uOccS;uniform float uCut;uniform int uMaps;uniform float uTrans;uniform float uUnlit;uniform vec3 uTintMask;
out vec4 o;
void main(){vec4 base=uBaseF*vCol;base.rgb*=mix(vec3(1.0),vTint,uTintMask);if((uMaps&1)!=0)base*=texture(uBase,vUV);
#ifdef ALPHA
 if(base.a<uCut)discard;
#endif
 vec3 Ng=normalize(vN);vec3 N=gl_FrontFacing?Ng:-Ng;
#ifndef LITE
 if((uMaps&4)!=0){vec3 t=texture(uNorm,vUV).xyz*2.0-1.0;t.xy*=uNormS;t.y=-t.y;N=perturbN(N,vW,vUV,t);}
#endif

 float metal=uMRF.x,rough=uMRF.y,ao=1.0;
#ifndef LITE
 if((uMaps&2)!=0){vec4 mr=texture(uMR,vUV);rough*=mr.g;metal*=mr.b;}if((uMaps&8)!=0)ao=mix(1.0,texture(uOcc,vUV).r,uOccS);
#endif
 ao*=mix(0.45,1.0,smoothstep(0.0,0.75,vHgt));   // swaying foliage: darker at the roots, so grass sits in the ground
 rough=clamp(rough,0.045,1.0);vec3 em=uEmisF;if((uMaps&16)!=0)em*=texture(uEmis,vUV).rgb;
 vec3 V=normalize(uEye-vW);vec3 c;
 if(uUnlit>0.5)c=base.rgb;else{float sh=shadowAt(vW,gl_FrontFacing?Ng:-Ng,vDist);c=shadePBR(vW,N,V,base.rgb,metal,rough,ao,sh,uTrans);}
 c+=em;c=fogApply(c,vW,V);o=vec4(c,1.0);}`;
// ---- shadow depth pass ----
var FS_SHADOW=`precision mediump float;in vec2 vUV;in vec4 vCol;uniform sampler2D uBase;uniform vec4 uBaseF;uniform float uCut;uniform int uMaps;out vec4 o;
void main(){
#ifdef ALPHA
 float a=uBaseF.a*vCol.a;if((uMaps&1)!=0)a*=texture(uBase,vUV).a;if(a<uCut)discard;
#endif
 o=vec4(1.0);}`;
// ---- terrain: splat of 5 layers (meadow tint, dirt, rock, sand, snow) with height blending ----
var VS_TER=`precision highp float;layout(location=0) in vec3 aPos;layout(location=1) in vec3 aNor;layout(location=3) in vec4 aCol;layout(location=6) in vec4 aX;
uniform mat4 uVP;uniform vec3 uEyeV;out vec3 vW;out vec3 vN;out vec4 vSp;out vec4 vX;out float vDist;
void main(){vW=aPos;vN=aNor;vSp=aCol;vX=aX;vDist=length(aPos-uEyeV);gl_Position=uVP*vec4(aPos,1.0);}`;
var FS_TER=`
in vec3 vW;in vec3 vN;in vec4 vSp;in vec4 vX;in float vDist;
uniform sampler2DArray uTA;uniform sampler2DArray uTN;uniform sampler2DArray uTR;uniform vec3 uMeadow;uniform vec3 uMeadow2;uniform float uTile;uniform float uSnowMood;uniform vec3 uPathCol;
out vec4 o;
float hn(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float vn2(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);return mix(mix(hn(i),hn(i+vec2(1,0)),f.x),mix(hn(i+vec2(0,1)),hn(i+vec2(1,1)),f.x),f.y);}
void main(){vec2 uv=vW.xz/uTile;vec2 uv2=vec2(uv.x*0.7071-uv.y*0.7071,uv.x*0.7071+uv.y*0.7071)*0.37+0.31;
 float mac=vn2(vW.xz*0.045)*0.6+vn2(vW.xz*0.17)*0.4;float far=smoothstep(18.0,60.0,vDist);
 vec4 A[4];vec4 Nn[4];vec4 R[4];
#ifdef LITE
 for(int i=0;i<4;i++){A[i]=texture(uTA,vec3(uv,float(i)));Nn[i]=vec4(0.5,0.5,1.0,1.0);R[i]=vec4(1.0,0.85,0.0,1.0);}
#else
 for(int i=0;i<4;i++){A[i]=mix(texture(uTA,vec3(uv,float(i))),texture(uTA,vec3(uv2,float(i))),0.35+0.3*far);Nn[i]=texture(uTN,vec3(uv,float(i)));R[i]=texture(uTR,vec3(uv,float(i)));}
#endif
 float hd=dot(A[0].rgb,vec3(0.33));vec3 meadow=mix(uMeadow,uMeadow2,smoothstep(0.25,0.85,mac))*(0.62+0.85*hd);
 float wm=max(0.0,1.0-vSp.x-vSp.y-vSp.z-vSp.w);float w[5];w[0]=wm;w[1]=vSp.x;w[2]=vSp.y;w[3]=vSp.z;w[4]=vSp.w;
 float h[5];h[0]=hd*0.6+0.3;h[1]=hd;h[2]=dot(A[1].rgb,vec3(0.33))+0.2;h[3]=dot(A[2].rgb,vec3(0.33));h[4]=dot(A[3].rgb,vec3(0.33))+0.1;
 float mx=0.0;for(int i=0;i<5;i++)mx=max(mx,w[i]+h[i]*0.5);float s=0.0;for(int i=0;i<5;i++){w[i]=max(w[i]+h[i]*0.5-mx+0.18,0.0);s+=w[i];}for(int i=0;i<5;i++)w[i]/=max(s,1e-4);
 vec3 alb=meadow*w[0]+A[0].rgb*mix(vec3(1.0),uPathCol,vX.x)*w[1]+A[1].rgb*w[2]+A[2].rgb*w[3]+A[3].rgb*w[4];
 vec3 tn=(Nn[0].xyz*2.0-1.0)*(w[0]*0.55+w[1])+(Nn[1].xyz*2.0-1.0)*w[2]+(Nn[2].xyz*2.0-1.0)*w[3]+(Nn[3].xyz*2.0-1.0)*w[4];tn.z=max(tn.z,0.2);tn.xy*=1.0-far*0.6;
 vec4 arm=R[0]*(w[0]+w[1])+R[1]*w[2]+R[2]*w[3]+R[3]*w[4];float rough=mix(arm.g,0.95,w[0]*0.6);float ao=mix(1.0,arm.r,0.8)*mix(0.82,1.0,mac)*vX.y;
 alb*=mix(0.86,1.1,mac);vec3 Ng=normalize(vN);vec3 T=normalize(vec3(1.0,0.0,0.0)-Ng*Ng.x);vec3 B=normalize(cross(Ng,T));vec3 N=normalize(T*tn.x-B*tn.y+Ng*tn.z);
 vec3 V=normalize(uEye-vW);float sh=shadowAt(vW,Ng,vDist);vec3 c=shadePBR(vW,N,V,alb,0.0,rough,ao,sh,0.0);c=fogApply(c,vW,V);o=vec4(c,1.0);}`;
// ---- sky background (HDRI equirect or procedural), env cube baking, prefilter, irradiance ----
var VS_FSQ=`layout(location=0) in vec2 aP;out vec2 vUv;void main(){vUv=aP*0.5+0.5;gl_Position=vec4(aP,0.9999,1.0);}`;
var GL_SKY=`
#ifndef PI
#define PI 3.14159265
#endif
uniform int uSkyMode;uniform sampler2D uHdri;uniform sampler2D uHdri2;uniform float uHdriMix;uniform float uHdriRot;uniform float uHdriRot2;uniform float uHdriK;uniform float uHdriK2;uniform vec3 uSkyTint;uniform float uBgClamp;
uniform vec3 uDiscDir;uniform vec3 uDiscCol;uniform float uStars;
uniform vec3 uZen;uniform vec3 uHor;uniform vec3 uSkySun;uniform vec3 uSkySunDir;uniform float uNight;uniform float uAur;uniform float uSkyTime;uniform float uClampK;
vec3 procSky(vec3 d){float y=d.y;float yp=max(y,0.0);vec3 c=mix(uHor,uZen,pow(yp,0.45));float sd=max(dot(d,uSkySunDir),0.0);
 c+=uSkySun*(pow(sd,8.0)*0.35+pow(sd,64.0)*0.6)*(1.0-uNight*0.7);
 vec2 cp=d.xz/(yp+0.08)*0.6+vec2(uSkyTime*0.006,uSkyTime*0.002);float cl=sin(cp.x*1.9+sin(cp.y*1.3))*sin(cp.y*1.7+sin(cp.x*0.8)*1.5)*0.5+0.5;cl=smoothstep(0.55,0.95,cl)*smoothstep(0.0,0.25,yp)*0.5;
 c=mix(c,uHor*1.1+uSkySun*0.08*(1.0-uNight),cl);if(y<0.0)c=mix(uHor,uHor*0.4,clamp(-y*3.0,0.0,1.0));return c;}
vec3 hdriAt(sampler2D t,vec3 d,float rot){float ph=atan(d.x,-d.z)+rot;vec2 uv=vec2(ph/6.2831853+0.5,0.5-asin(clamp(d.y,-1.0,1.0))/PI);return textureLod(t,uv,0.0).rgb;}
vec3 aurora(vec3 d){float yp=max(d.y,0.0);float a=sin(d.x*5.0+uSkyTime*0.25+sin(d.z*3.0+uSkyTime*0.17)*2.5)*0.5+0.5;float a2=sin(d.z*7.0-uSkyTime*0.2+d.x*2.0)*0.5+0.5;
 float band=smoothstep(0.1,0.28,yp)*smoothstep(0.85,0.4,yp);return mix(vec3(0.1,1.0,0.55),vec3(0.55,0.25,1.0),a2)*pow(a,4.0)*band*uAur*1.6;}
vec3 skyDir(vec3 d){vec3 c;
 if(uSkyMode==1){c=min(hdriAt(uHdri,d,uHdriRot)*uHdriK,vec3(uBgClamp));if(uHdriMix>0.001)c=mix(c,min(hdriAt(uHdri2,d,uHdriRot2)*uHdriK2,vec3(uBgClamp)),uHdriMix);c*=uSkyTint;}else c=procSky(d);
 if(uDiscCol.r+uDiscCol.g+uDiscCol.b>0.0){float sd=dot(d,uDiscDir);c+=uDiscCol*(smoothstep(0.99965,0.99985,sd)*40.0+pow(max(sd,0.0),180.0)*0.6);}
 if(uStars>0.01){vec3 q=floor(d*220.0);float h=fract(sin(dot(q,vec3(12.9898,78.233,45.164)))*43758.5453);c+=vec3(step(0.9972,h))*uStars*smoothstep(0.0,0.2,d.y)*(0.6+0.4*sin(uSkyTime*3.0+h*90.0))*0.9;}
 if(uAur>0.0)c+=aurora(d);return min(c,vec3(uClampK));}
`;
var FS_SKY=`precision highp float;in vec2 vUv;uniform mat4 uInvVP;uniform vec3 uEye;uniform vec3 uFogCol;uniform float uSkyFog;uniform vec3 uHzSun;uniform vec3 uHzDir;`+GL_SKY+`out vec4 o;
void main(){vec4 a=uInvVP*vec4(vUv*2.0-1.0,1.0,1.0);vec3 d=normalize(a.xyz/a.w-uEye);vec3 c=skyDir(d);
 // horizon: the sky meets the fog colour exactly at (and below) the horizon, so the world edge never shows as a hard line;
 // the fog carries the same sun in-scatter as fogApply. A Henyey-Greenstein (Mie) halo glows around the sun.
 float cs=dot(d,uHzDir);vec3 fc=uFogCol+uHzSun*pow(max(cs,0.0),8.0);float hz=d.y<0.0?1.0:mix(1.0-smoothstep(0.0,0.22,d.y),1.0,1.0-smoothstep(0.0,0.045,d.y));
 c=mix(c,fc,clamp(hz*mix(uSkyFog,1.0,1.0-smoothstep(0.0,0.045,d.y)),0.0,1.0));float g=0.78;c+=uHzSun*(1.0-g*g)/(4.0*PI*pow(1.0+g*g-2.0*g*cs,1.5))*0.35*smoothstep(-0.05,0.1,d.y);o=vec4(c,1.0);}`;
var FS_CUBE=`precision highp float;in vec2 vUv;uniform mat3 uFace;`+GL_SKY+`out vec4 o;void main(){vec2 p=vUv*2.0-1.0;vec3 d=normalize(uFace*vec3(p.x,p.y,1.0));o=vec4(skyDir(d),1.0);}`;
var FS_PREF=`precision highp float;
#define PI 3.14159265
in vec2 vUv;uniform mat3 uFace;uniform samplerCube uSrc;uniform float uRough;uniform int uN;uniform float uSrcRes;out vec4 o;
float rad(uint b){b=(b<<16u)|(b>>16u);b=((b&0x55555555u)<<1u)|((b&0xAAAAAAAAu)>>1u);b=((b&0x33333333u)<<2u)|((b&0xCCCCCCCCu)>>2u);b=((b&0x0F0F0F0Fu)<<4u)|((b&0xF0F0F0F0u)>>4u);b=((b&0x00FF00FFu)<<8u)|((b&0xFF00FF00u)>>8u);return float(b)*2.3283064365386963e-10;}
void main(){vec2 p=vUv*2.0-1.0;vec3 N=normalize(uFace*vec3(p.x,p.y,1.0));vec3 up=abs(N.y)<0.999?vec3(0,1,0):vec3(1,0,0);vec3 T=normalize(cross(up,N));vec3 B=cross(N,T);
 float a=max(uRough*uRough,0.001);vec3 acc=vec3(0);float w=0.0;
 for(int i=0;i<128;i++){if(i>=uN)break;vec2 xi=vec2(float(i)/float(uN),rad(uint(i)));float ph=6.2831853*xi.x;float ct=sqrt((1.0-xi.y)/(1.0+(a*a-1.0)*xi.y));float st=sqrt(1.0-ct*ct);
  vec3 H=normalize(T*(st*cos(ph))+B*(st*sin(ph))+N*ct);vec3 L=2.0*dot(N,H)*H-N;float nl=dot(N,L);if(nl>0.0){float D=a*a/(PI*pow(ct*ct*(a*a-1.0)+1.0,2.0)+1e-6);float pdf=D/4.0+1e-5;
  float sa=1.0/(float(uN)*pdf);float sx=4.0*PI/(6.0*uSrcRes*uSrcRes);float mip=uRough<0.01?0.0:max(0.5*log2(sa/sx)+1.0,0.0);acc+=textureLod(uSrc,L,mip).rgb*nl;w+=nl;}}
 o=vec4(acc/max(w,1e-4),1.0);}`;
var FS_IRR=`precision highp float;in vec2 vUv;uniform mat3 uFace;uniform samplerCube uSrc;uniform float uMip;out vec4 o;
void main(){vec2 p=vUv*2.0-1.0;vec3 N=normalize(uFace*vec3(p.x,p.y,1.0));vec3 up=abs(N.y)<0.999?vec3(0,1,0):vec3(1,0,0);vec3 T=normalize(cross(up,N));vec3 B=cross(N,T);vec3 acc=vec3(0);float n=0.0;
 for(int i=0;i<12;i++)for(int j=0;j<6;j++){float ph=(float(i)+0.5)/12.0*6.2831853;float th=(float(j)+0.5)/6.0*1.5707963;vec3 d=T*(sin(th)*cos(ph))+B*(sin(th)*sin(ph))+N*cos(th);acc+=textureLod(uSrc,d,uMip).rgb*cos(th)*sin(th);n+=cos(th)*sin(th);}
 o=vec4(acc/n,1.0);}`;
// ---- water: refraction of the opaque scene, screen-space reflection fallback to the sky, shore foam, sun glint ----
var VS_WAT=`precision highp float;layout(location=0) in vec2 aP;uniform mat4 uVP;uniform float uSize;uniform float uLvl;uniform vec3 uEyeV;out vec3 vW;out float vDist;
void main(){vW=vec3(aP.x*uSize,uLvl,aP.y*uSize);vDist=length(vW-uEyeV);gl_Position=uVP*vec4(vW,1.0);}`;
var FS_WAT=`
in vec3 vW;in float vDist;uniform sampler2D uScene;uniform sampler2D uDepth;uniform sampler2D uWN;uniform vec2 uRes;uniform mat4 uVP;uniform mat4 uInvVP;uniform vec3 uWCol;uniform vec3 uWDeep;uniform int uRefr;uniform int uSSR;uniform float uNear;uniform float uFar;uniform float uIce;
out vec4 o;
float lin(float d){float z=d*2.0-1.0;return 2.0*uNear*uFar/(uFar+uNear-z*(uFar-uNear));}
void main(){vec2 sp=gl_FragCoord.xy/uRes;float t=uTime;vec2 p=vW.xz;
 vec3 n1=texture(uWN,p*0.045+vec2(t*0.012,t*0.007)).xyz*2.0-1.0;vec3 n2=texture(uWN,p*0.11-vec2(t*0.018,-t*0.011)).xyz*2.0-1.0;vec3 n3=texture(uWN,p*0.27+vec2(-t*0.03,t*0.025)).xyz*2.0-1.0;
 vec3 tn=normalize(n1*0.55+n2*0.35+n3*0.2+vec3(0,0,1.6));float calm=mix(0.25,1.0,smoothstep(70.0,8.0,vDist));vec3 N=normalize(vec3(tn.x*calm,tn.z,tn.y*calm));
 vec3 V=normalize(uEye-vW);float nv=max(dot(N,V),0.0);float F=0.02+0.98*pow(1.0-nv,5.0);
 float dScene=texture(uDepth,sp).r;float thick=max(lin(dScene)-lin(gl_FragCoord.z),0.0);vec2 rofs=N.xz*0.035*clamp(thick*0.5,0.0,1.0);
 vec3 refr;if(uRefr==1){vec2 su=clamp(sp+rofs,0.001,0.999);float d2=texture(uDepth,su).r;if(d2<gl_FragCoord.z)su=sp;refr=texture(uScene,su).rgb;float th2=max(lin(texture(uDepth,su).r)-lin(gl_FragCoord.z),0.0);
  vec3 ab=exp(-th2*(vec3(1.0)-uWCol)*1.6);refr=mix(uWDeep*(texture(uEnvIrr,vec3(0,1,0)).rgb*0.6+uSunCol*0.04),refr*ab,exp(-th2*0.35));}
 else refr=uWDeep*(texture(uEnvIrr,vec3(0,1,0)).rgb*0.7+uSunCol*0.05);
 vec3 R=reflect(-V,N);R.y=abs(R.y);vec3 refl=textureLod(uEnvSpec,R,0.5).rgb;
 if(uSSR==1){vec3 rp=vW;float stepL=0.9;for(int i=0;i<24;i++){rp+=R*stepL;stepL*=1.12;vec4 c=uVP*vec4(rp,1.0);vec3 q=c.xyz/c.w*0.5+0.5;if(q.x<0.0||q.x>1.0||q.y<0.0||q.y>1.0||c.w<0.0)break;
  float ds=texture(uDepth,q.xy).r;if(ds<q.z&&lin(q.z)-lin(ds)<3.0){float fe=smoothstep(0.0,0.12,min(min(q.x,1.0-q.x),min(q.y,1.0-q.y)));refl=mix(refl,texture(uScene,q.xy).rgb,fe);break;}}}
 vec3 H=normalize(uSunDir+V);float spec=D_GGX(max(dot(N,H),0.0),0.012)*V_Smith(nv,max(dot(N,uSunDir),0.0),0.012)*max(dot(N,uSunDir),0.0)*F_Schlick(vec3(0.02),max(dot(V,H),0.0)).x;
 vec3 c=mix(refr,refl,F)+uSunCol*spec*min(1.0,thick*3.0+0.2);
 float fn=texture(uWN,p*0.35+vec2(t*0.03,0.0)).a;float foam=smoothstep(0.28,0.02,thick)*smoothstep(0.5,0.8,fn+0.2*sin(t*1.6+p.x*0.7+p.y*0.5));
 if(uIce>0.5){float n1i=texture(uWN,p*0.05).a,n2i=texture(uWN,p*0.21+0.37).a;float crack=smoothstep(0.035,0.0,abs(n1i-0.5))*0.6+smoothstep(0.02,0.0,abs(n2i-0.5))*0.4;
  vec3 iceAlb=mix(vec3(0.62,0.78,0.9),vec3(0.86,0.93,0.98),n2i);vec3 lit=iceAlb*(texture(uEnvIrr,vec3(0,1,0)).rgb*1.1+uSunCol*max(uSunDir.y,0.0)*0.45);
  c=mix(lit*(1.0-crack*0.35),refl,clamp(F*0.8+0.12,0.0,0.6))+uSunCol*spec*0.25;foam=0.0;}
 else c=mix(c,vec3(0.9,0.95,1.0)*(texture(uEnvIrr,vec3(0,1,0)).rgb+uSunCol*0.3),foam*0.8);
 float a=uRefr==1?1.0:clamp(0.55+F*0.4+foam,0.0,1.0);a*=smoothstep(0.0,0.06,thick);c=fogApply(c,vW,V);o=vec4(c,a);}`;
// ---- FX primitives (gems, orbs, beams; the old instanced API) + soft particles ----
var VS_FX=`precision highp float;layout(location=0) in vec3 aPos;layout(location=1) in vec3 aNor;layout(location=2) in vec3 iOff;layout(location=3) in vec3 iScl;layout(location=4) in vec3 iCol;layout(location=5) in vec4 iMisc;
uniform mat4 uVP;uniform vec3 uEyeV;out vec3 vW;out vec3 vN;out vec3 vC;out float vE;out float vSp;out float vDist;
void main(){vec3 p=aPos*iScl;float c=cos(iMisc.z),s=sin(iMisc.z);p=vec3(c*p.x+s*p.z,p.y,-s*p.x+c*p.z);vec3 n=aNor/max(iScl,vec3(0.001));n=vec3(c*n.x+s*n.z,n.y,-s*n.x+c*n.z);
 vW=p+iOff;vN=n;vC=iCol;vE=iMisc.x;vSp=iMisc.w;vDist=length(vW-uEyeV);gl_Position=uVP*vec4(vW,1.0);}`;
var FS_FX=`
in vec3 vW;in vec3 vN;in vec3 vC;in float vE;in float vSp;in float vDist;out vec4 o;
float fxn(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);float n=dot(i,vec3(1.0,57.0,113.0));
 vec4 h=fract(sin(vec4(n,n+1.0,n+57.0,n+58.0))*43758.5453),k=fract(sin(vec4(n+113.0,n+114.0,n+170.0,n+171.0))*43758.5453);
 vec2 a=mix(h.xz,h.yw,f.x),b=mix(k.xz,k.yw,f.x);return mix(mix(a.x,a.y,f.y),mix(b.x,b.y,f.y),f.z);}
void main(){vec3 N=normalize(vN);vec3 V=normalize(uEye-vW);vec3 alb=pow(max(vC,vec3(0.0)),vec3(2.2));float ef=clamp(vE,0.0,1.0);
 // material break-up: low-frequency albedo/roughness variation so primitives never read as flat plastic
 float mv=fxn(vW*2.3)*0.6+fxn(vW*7.1)*0.4;alb*=0.86+0.28*mv;
 vec3 c=shadePBR(vW,N,V,alb,0.0,clamp(mix(0.7,0.15,clamp(vSp,0.0,1.0))+(mv-0.5)*0.2,0.05,1.0),1.0,shadowAt(vW,N,vDist),0.0);
 float rim=pow(1.0-max(dot(N,V),0.0),2.0);float sw=fxn(vW*3.0+vec3(0.0,uTime*0.9,uTime*0.4));   // emissive: a slow living swirl + hot rim
 c=mix(c,alb*(2.0+vE*5.0)*(0.7+0.6*sw)+alb*rim*(3.0+2.0*sw),ef);c=fogApply(c,vW,V);o=vec4(c,1.0);}`;
var VS_PART=`precision highp float;layout(location=0) in vec2 aQ;layout(location=2) in vec4 iP;layout(location=3) in vec4 iC;uniform mat4 uVP;uniform vec3 uCamR;uniform vec3 uCamU;out vec2 vQ;out vec4 vC;
void main(){vec3 w=iP.xyz+(uCamR*aQ.x+uCamU*aQ.y)*iP.w;vQ=aQ;vC=iC;gl_Position=uVP*vec4(w,1.0);}`;
var FS_PART=`precision highp float;in vec2 vQ;in vec4 vC;out vec4 o;void main(){float r=dot(vQ,vQ);if(r>1.0)discard;float a=(1.0-r);a*=a;o=vec4(pow(vC.rgb,vec3(2.2))*vC.a*a*3.0,0.0);}`;
// ---- post: SSAO, blur, bloom, composite (ACES + grade), FXAA, TAA ----
var FS_SSAO=`precision highp float;in vec2 vUv;uniform sampler2D uDepth;uniform mat4 uProj;uniform mat4 uInvProj;uniform vec2 uRes;uniform int uN;uniform float uRad;out vec4 o;
vec3 vpos(vec2 uv){float d=texture(uDepth,uv).r;vec4 p=uInvProj*vec4(uv*2.0-1.0,d*2.0-1.0,1.0);return p.xyz/p.w;}
float ign(vec2 p){return fract(52.9829189*fract(dot(p,vec2(0.06711056,0.00583715))));}
void main(){float d=texture(uDepth,vUv).r;if(d>=0.99999){o=vec4(1.0);return;}vec3 P=vpos(vUv);vec2 px=1.0/uRes;
 vec3 pr=vpos(vUv+vec2(px.x,0.0)),pl=vpos(vUv-vec2(px.x,0.0)),pu=vpos(vUv+vec2(0.0,px.y)),pd=vpos(vUv-vec2(0.0,px.y));
 vec3 dx=abs(pr.z-P.z)<abs(P.z-pl.z)?pr-P:P-pl;vec3 dy=abs(pu.z-P.z)<abs(P.z-pd.z)?pu-P:P-pd;vec3 N=normalize(cross(dx,dy));
 float a=ign(gl_FragCoord.xy)*6.2831853;float occ=0.0;float rad=uRad*clamp(-P.z*0.06+0.4,0.4,1.6);
 for(int i=0;i<16;i++){if(i>=uN)break;float fi=(float(i)+0.5)/float(uN);float ph=a+fi*38.83;float r=sqrt(fi);vec3 dir=vec3(cos(ph)*r,sin(ph)*r,sqrt(1.0-fi));
  vec3 up=abs(N.z)<0.99?vec3(0,0,1):vec3(1,0,0);vec3 T=normalize(cross(up,N));vec3 B=cross(N,T);vec3 S=P+(T*dir.x+B*dir.y+N*dir.z)*rad*(0.3+0.7*fi);
  vec4 c=uProj*vec4(S,1.0);vec2 su=c.xy/c.w*0.5+0.5;float sz=vpos(su).z;float rc=smoothstep(0.0,1.0,rad/abs(P.z-sz));occ+=(sz>=S.z+0.02?1.0:0.0)*rc;}
 o=vec4(vec3(clamp(1.0-occ/float(uN)*1.15,0.0,1.0)),1.0);}`;
var FS_BLUR=`precision highp float;in vec2 vUv;uniform sampler2D uT;uniform sampler2D uDepth;uniform vec2 uDir;uniform float uNear;uniform float uFar;out vec4 o;
float lin(float d){float z=d*2.0-1.0;return 2.0*uNear*uFar/(uFar+uNear-z*(uFar-uNear));}
void main(){float c0=lin(texture(uDepth,vUv).r);float s=0.0,w=0.0;for(int i=-3;i<=3;i++){vec2 u=vUv+uDir*float(i);float z=lin(texture(uDepth,u).r);float k=exp(-float(i*i)/8.0)*exp(-abs(z-c0)*2.0/max(c0*0.05,0.05));s+=texture(uT,u).r*k;w+=k;}o=vec4(vec3(s/w),1.0);}`;
var R2FS_BRIGHT=`precision highp float;in vec2 vUv;uniform sampler2D uT;uniform vec2 uPx;uniform float uThr;out vec4 o;
void main(){vec3 c=texture(uT,vUv+uPx*vec2(-0.5,-0.5)).rgb+texture(uT,vUv+uPx*vec2(0.5,-0.5)).rgb+texture(uT,vUv+uPx*vec2(-0.5,0.5)).rgb+texture(uT,vUv+uPx*vec2(0.5,0.5)).rgb;c*=0.25;
 float l=max(c.r,max(c.g,c.b));float k=clamp(l-uThr*0.5,0.0,uThr);k=k*k/(4.0*uThr+1e-4);float m=max(k,l-uThr)/max(l,1e-4);o=vec4(min(c*m,vec3(64.0)),1.0);}`;
var FS_DOWN=`precision highp float;in vec2 vUv;uniform sampler2D uT;uniform vec2 uPx;out vec4 o;
void main(){vec3 a=texture(uT,vUv+uPx*vec2(-2,2)).rgb,b=texture(uT,vUv+uPx*vec2(0,2)).rgb,c=texture(uT,vUv+uPx*vec2(2,2)).rgb,d=texture(uT,vUv+uPx*vec2(-2,0)).rgb,e=texture(uT,vUv).rgb,f=texture(uT,vUv+uPx*vec2(2,0)).rgb,
 g=texture(uT,vUv+uPx*vec2(-2,-2)).rgb,h=texture(uT,vUv+uPx*vec2(0,-2)).rgb,i=texture(uT,vUv+uPx*vec2(2,-2)).rgb,j=texture(uT,vUv+uPx*vec2(-1,1)).rgb,k=texture(uT,vUv+uPx*vec2(1,1)).rgb,l=texture(uT,vUv+uPx*vec2(-1,-1)).rgb,m=texture(uT,vUv+uPx*vec2(1,-1)).rgb;
 o=vec4(e*0.125+(a+c+g+i)*0.03125+(b+d+f+h)*0.0625+(j+k+l+m)*0.125,1.0);}`;
var FS_UP=`precision highp float;in vec2 vUv;uniform sampler2D uT;uniform vec2 uPx;uniform float uK;out vec4 o;
void main(){vec3 s=texture(uT,vUv+uPx*vec2(-1,-1)).rgb+texture(uT,vUv+uPx*vec2(1,-1)).rgb+texture(uT,vUv+uPx*vec2(-1,1)).rgb+texture(uT,vUv+uPx*vec2(1,1)).rgb;s*=1.0;
 s+=(texture(uT,vUv+uPx*vec2(0,-1)).rgb+texture(uT,vUv+uPx*vec2(0,1)).rgb+texture(uT,vUv+uPx*vec2(-1,0)).rgb+texture(uT,vUv+uPx*vec2(1,0)).rgb)*2.0+texture(uT,vUv).rgb*4.0;o=vec4(s/16.0*uK,1.0);}`;
var R2FS_COMP=`precision highp float;in vec2 vUv;uniform sampler2D uScene;uniform sampler2D uBloom;uniform sampler2D uAO;uniform float uAOK;uniform float uBloomK;uniform float uExpo;uniform float uSat;uniform float uCon;uniform vec3 uTint;
uniform vec3 uLift;uniform vec3 uGain;uniform float uVig;uniform float uTime;uniform float uHurt;uniform float uFlash;
uniform sampler2D uAE;uniform vec4 uAEP;uniform sampler2D uShaft;uniform vec3 uShaftC;uniform int uTone;uniform vec3 uShTint;uniform vec3 uHiTint;uniform float uGrain;out vec4 o;
vec3 aces(vec3 x){const mat3 m1=mat3(0.59719,0.07600,0.02840,0.35458,0.90834,0.13383,0.04823,0.01566,0.83777);const mat3 m2=mat3(1.60475,-0.10208,-0.00327,-0.53108,1.10813,-0.07276,-0.07367,-0.00605,1.07602);
 vec3 v=m1*x;vec3 a=v*(v+0.0245786)-0.000090537;vec3 b=v*(0.983729*v+0.4329510)+0.238081;return clamp(m2*(a/b),0.0,1.0);}
// AgX (Troy Sobotka's curve, polynomial fit after Benjamin Wrensch): gentler highlight roll-off, hues hold in bright skies
vec3 agxC(vec3 x){vec3 x2=x*x,x4=x2*x2;return 15.5*x4*x2-40.14*x4*x+31.96*x4-6.868*x2*x+0.4298*x2+0.1191*x-0.00232;}
vec3 agx(vec3 c){const mat3 m=mat3(0.842479062253094,0.0423282422610123,0.0423756549057051,0.0784335999999992,0.878468636469772,0.0784336,0.0792237451477643,0.0791661274605434,0.879142973793104);
 const mat3 mi=mat3(1.19687900512017,-0.0528968517574562,-0.0529716355144438,-0.0980208811401368,1.15190312990417,-0.0980434501171241,-0.0990297440797205,-0.0989611768448433,1.15107367264116);
 c=m*max(c,vec3(1e-10));c=clamp(log2(c),-12.47393,4.026069);c=(c+12.47393)/16.500999;c=agxC(c);
 float l=dot(c,vec3(0.2126,0.7152,0.0722));c=l+1.12*(c-l);c=pow(max(c,0.0),vec3(1.06));
 c=mi*c;return clamp(pow(max(c,0.0),vec3(2.2)),0.0,1.0);}
vec3 toSrgb(vec3 c){return mix(c*12.92,1.055*pow(c,vec3(1.0/2.4))-0.055,step(0.0031308,c));}
void main(){vec3 c=texture(uScene,vUv).rgb;float ao=texture(uAO,vUv).r;c*=mix(1.0,ao,uAOK);c+=texture(uBloom,vUv).rgb*uBloomK;c+=texture(uShaft,vUv).rgb*uShaftC;
 float ae=1.0;if(uAEP.x>0.0){float la=texture(uAE,vec2(0.5)).r;ae=clamp(pow(uAEP.w/max(la*uExpo,1e-4),uAEP.x),uAEP.y,uAEP.z);}
 c*=uExpo*ae;c+=vec3(uFlash);
 c=uTone==1?agx(c):aces(c);float l=dot(c,vec3(0.2126,0.7152,0.0722));c=mix(vec3(l),c,uSat);c=clamp((c-0.18)*uCon+0.18,0.0,1.0);c=c*uTint;
 c*=mix(uShTint,uHiTint,smoothstep(0.05,0.7,l));c=uLift+(uGain-uLift)*c;
 vec2 q=vUv-0.5;c*=1.0-dot(q,q)*uVig;c=mix(c,vec3(0.6,0.03,0.05),uHurt*smoothstep(0.15,0.65,length(q)));c=toSrgb(clamp(c,0.0,1.0));
 float g=fract(sin(dot(vUv*vec2(1734.1,971.3)+uTime,vec2(12.9898,78.233)))*43758.5453);float g2=fract(sin(dot(vUv*vec2(913.7,1561.9)-uTime*1.7,vec2(39.346,11.135)))*24634.6345);
 c+=(g-0.5)/255.0+(g+g2-1.0)*uGrain*(1.0-0.7*dot(c,vec3(0.299,0.587,0.114)));
 o=vec4(c,dot(c,vec3(0.299,0.587,0.114)));}`;
// composite notes: eye adaptation moves a share (uAEP.x) of the way from the mood's exposure toward middle grey, clamped
// to [uAEP.y, uAEP.z]; split toning tints shadows / highlights per mood; dither always, film grain (High/Ultra) in shadows.
var R2FS_FXAA=`precision highp float;in vec2 vUv;uniform sampler2D uT;uniform vec2 uPx;uniform float uSharp;out vec4 o;
vec3 fxaa(){vec3 rgbM=texture(uT,vUv).rgb;float lM=texture(uT,vUv).a;float lNW=texture(uT,vUv+vec2(-1,-1)*uPx).a,lNE=texture(uT,vUv+vec2(1,-1)*uPx).a,lSW=texture(uT,vUv+vec2(-1,1)*uPx).a,lSE=texture(uT,vUv+vec2(1,1)*uPx).a;
 float mn=min(lM,min(min(lNW,lNE),min(lSW,lSE))),mx=max(lM,max(max(lNW,lNE),max(lSW,lSE)));if(mx-mn<max(0.0312,mx*0.125))return rgbM;
 vec2 dir=vec2(-((lNW+lNE)-(lSW+lSE)),((lNW+lSW)-(lNE+lSE)));float rd=1.0/(min(abs(dir.x),abs(dir.y))+max((lNW+lNE+lSW+lSE)*0.03125,0.0078));dir=clamp(dir*rd,-8.0,8.0)*uPx;
 vec3 a=0.5*(texture(uT,vUv+dir*(1.0/3.0-0.5)).rgb+texture(uT,vUv+dir*(2.0/3.0-0.5)).rgb);vec3 b=a*0.5+0.25*(texture(uT,vUv-dir*0.5).rgb+texture(uT,vUv+dir*0.5).rgb);
 float lb=dot(b,vec3(0.299,0.587,0.114));return (lb<mn||lb>mx)?a:b;}
void main(){vec3 c=fxaa();
 if(uSharp>0.0){vec3 n=texture(uT,vUv+vec2(uPx.x,0)).rgb,s=texture(uT,vUv-vec2(uPx.x,0)).rgb,e=texture(uT,vUv+vec2(0,uPx.y)).rgb,w=texture(uT,vUv-vec2(0,uPx.y)).rgb;
  vec3 mn=min(c,min(min(n,s),min(e,w))),mx=max(c,max(max(n,s),max(e,w)));vec3 amp=sqrt(clamp(min(mn,1.0-mx)/max(mx,1e-4),0.0,1.0));
  vec3 wt=-amp*uSharp*0.2;c=clamp((c+(n+s+e+w)*wt)/(1.0+4.0*wt),0.0,1.0);}
 o=vec4(c,1.0);}`;
// FXAA, then contrast-adaptive sharpening (CAS-lite) that recovers detail lost to the lower render scale of Low/Medium.
var FS_TAA=`precision highp float;in vec2 vUv;uniform sampler2D uCur;uniform sampler2D uHist;uniform sampler2D uDepth;uniform mat4 uInvVP;uniform mat4 uPrevVP;uniform vec2 uPx;uniform float uReset;out vec4 o;
vec3 ycc(vec3 c){return vec3(dot(c,vec3(0.25,0.5,0.25)),dot(c,vec3(0.5,0.0,-0.5)),dot(c,vec3(-0.25,0.5,-0.25)));}vec3 rgb(vec3 y){return vec3(y.x+y.y-y.z,y.x+y.z,y.x-y.y-y.z);}
void main(){vec3 c=texture(uCur,vUv).rgb;vec3 mn=vec3(1e9),mx=vec3(-1e9),m1=vec3(0),m2=vec3(0);for(int j=-1;j<=1;j++)for(int i=-1;i<=1;i++){vec3 s=ycc(texture(uCur,vUv+vec2(i,j)*uPx).rgb);m1+=s;m2+=s*s;}
 m1/=9.0;vec3 sd=sqrt(max(m2/9.0-m1*m1,0.0));mn=m1-sd*1.25;mx=m1+sd*1.25;
 float d=texture(uDepth,vUv).r;vec4 wp=uInvVP*vec4(vUv*2.0-1.0,d*2.0-1.0,1.0);wp/=wp.w;vec4 pc=uPrevVP*wp;vec2 pu=pc.xy/pc.w*0.5+0.5;
 if(uReset>0.5||pu.x<0.0||pu.y<0.0||pu.x>1.0||pu.y>1.0){o=vec4(c,dot(c,vec3(0.299,0.587,0.114)));return;}
 vec3 h=ycc(texture(uHist,pu).rgb);h=clamp(h,mn,mx);vec3 r=mix(rgb(h),c,0.1);o=vec4(r,dot(r,vec3(0.299,0.587,0.114)));}`;
var FS_OUT=`precision highp float;in vec2 vUv;uniform sampler2D uT;uniform vec2 uPx;uniform float uSharp;out vec4 o;
void main(){vec3 c=texture(uT,vUv).rgb;if(uSharp>0.0){vec3 n=texture(uT,vUv+vec2(uPx.x,0)).rgb+texture(uT,vUv-vec2(uPx.x,0)).rgb+texture(uT,vUv+vec2(0,uPx.y)).rgb+texture(uT,vUv-vec2(0,uPx.y)).rgb;c=clamp(c+(c*4.0-n)*uSharp*0.25,0.0,1.0);}o=vec4(c,1.0);}`;
var FS_WNORM=`precision highp float;in vec2 vUv;out vec4 o;float h(vec2 p){p=mod(p,vec2(8.0));return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
float H(vec2 p){float s=0.0,a=0.5;vec2 q=p;for(int i=0;i<4;i++){s+=a*n(q);q*=2.0;a*=0.45;}return s;}
void main(){vec2 p=vUv*8.0;float e=8.0/256.0;float hx=H(p+vec2(e,0))-H(p-vec2(e,0)),hz=H(p+vec2(0,e))-H(p-vec2(0,e));vec3 N=normalize(vec3(-hx/e*0.35,-hz/e*0.35,1.0));o=vec4(N*0.5+0.5,H(p+vec2(3.7,1.3)));}`;
// ---- eye adaptation: centre-weighted log-luminance (mipmapped to 1x1), then a time-smoothed 1x1 target ----
var FS_LUM=`precision highp float;in vec2 vUv;uniform sampler2D uT;out vec4 o;
void main(){vec3 c=texture(uT,vUv).rgb;float l=dot(c,vec3(0.2126,0.7152,0.0722));float w=1.0-0.65*smoothstep(0.1,0.75,length(vUv-0.5)*1.6);o=vec4(log(max(l,1e-4))*w,w,0.0,1.0);}`;
var FS_ADAPT=`precision highp float;in vec2 vUv;uniform sampler2D uLum;uniform sampler2D uPrev;uniform float uLvl;uniform float uK;out vec4 o;
void main(){vec2 m=textureLod(uLum,vec2(0.5),uLvl).rg;float t=clamp(exp(m.x/max(m.y,1e-4)),1e-3,64.0);float p=texture(uPrev,vec2(0.5)).r;o=vec4(uK>=1.0||!(p>0.0)?t:p+(t-p)*uK,0.0,0.0,1.0);}`;
// ---- sun shafts (screen-space god rays): bright sky near the sun, radially blurred toward the sun's screen position ----
var FS_SHMASK=`precision highp float;in vec2 vUv;uniform sampler2D uT;uniform sampler2D uDepth;uniform vec2 uSun;uniform float uAsp;uniform float uThr;out vec4 o;
void main(){float d=texture(uDepth,vUv).r;vec3 c=texture(uT,vUv).rgb;float l=dot(c,vec3(0.2126,0.7152,0.0722));vec2 q=(vUv-uSun)*vec2(uAsp,1.0);
 float k=(d>=0.99999?1.0:0.0)*smoothstep(0.75,0.0,length(q));o=vec4(min(c*clamp((l-uThr)/max(l,1e-4),0.0,1.0),vec3(16.0))*k,1.0);}`;
var FS_SHBLUR=`precision highp float;in vec2 vUv;uniform sampler2D uT;uniform vec2 uSun;uniform int uN;uniform float uDecay;uniform float uLen;out vec4 o;
float ign(vec2 p){return fract(52.9829189*fract(dot(p,vec2(0.06711056,0.00583715))));}
void main(){vec2 dv=(vUv-uSun)*uLen/float(uN);vec2 uv=vUv-dv*ign(gl_FragCoord.xy);vec3 acc=vec3(0);float w=1.0,ws=0.0;
 for(int i=0;i<64;i++){if(i>=uN)break;acc+=texture(uT,uv).rgb*w;ws+=w;w*=uDecay;uv-=dv;}o=vec4(acc/max(ws,1e-4),1.0);}`;
// ---- distant mountain skyline: two impostor rings that follow the camera, lit by the sun, hazed by aerial perspective ----
var VS_MTN=`precision highp float;layout(location=0) in vec3 aPos;layout(location=1) in vec3 aNor;layout(location=2) in vec2 aX;uniform mat4 uVP;uniform vec3 uEyeV;uniform float uBaseY;
out vec3 vW;out vec3 vN;out vec2 vX;void main(){vW=vec3(aPos.x+uEyeV.x,aPos.y+uBaseY,aPos.z+uEyeV.z);vN=aNor;vX=aX;gl_Position=uVP*vec4(vW,1.0);}`;
var FS_MTN=`
in vec3 vW;in vec3 vN;in vec2 vX;uniform vec3 uRock;uniform vec3 uRock2;uniform float uSnow;uniform vec3 uHaze;out vec4 o;
float h3(vec3 p){return fract(sin(dot(p,vec3(127.1,311.7,74.7)))*43758.5453);}
void main(){vec3 N=normalize(vN);vec3 V=normalize(uEye-vW);float hn=vX.x,layer=vX.y;
 float br=h3(floor(vW*0.35))*0.5+h3(floor(vW*0.11))*0.5;vec3 alb=mix(uRock,uRock2,br*0.6+layer*0.3);
 float sn=uSnow*smoothstep(0.55,0.8,hn+br*0.12)*smoothstep(0.25,0.6,N.y);alb=mix(alb,vec3(0.86,0.9,0.95),sn);
 float nl=max(dot(N,uSunDir),0.0);vec3 c=alb*(uSunCol*nl*0.85/PI+texture(uEnvIrr,N).rgb*uEnvK*(0.55+0.45*N.y));
 float f=clamp(uHaze.x+layer*uHaze.y+(1.0-smoothstep(0.0,0.45,hn))*uHaze.z,0.0,1.0);vec3 fv=pow(vec3(f),mix(vec3(1.0),vec3(1.25,1.0,0.75),uAerial));
 float sunK=pow(max(dot(-V,uSunDir),0.0),8.0);c=mix(c,uFogCol+uFogSun*sunK,fv);o=vec4(c,1.0);}`;
