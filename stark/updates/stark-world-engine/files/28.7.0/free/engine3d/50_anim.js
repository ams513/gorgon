// ========== skeletal animation: clip sampling, name-based retargeting, crossfades, joint matrices ==========
function chanSample(ch,t,out){var T=ch.t,n=T.length,V=ch.v,k=ch.path==='rotation'?4:(ch.path==='weights'?0:3);if(!k)return null;var cs=ch.interp==='CUBICSPLINE'?3:1;
 if(n===1||t<=T[0]){for(var i=0;i<k;i++)out[i]=V[(cs===3?1:0)*k+i];return out;}if(t>=T[n-1]){var b=(n-1)*k*cs+(cs===3?k:0);for(var j=0;j<k;j++)out[j]=V[b+j];return out;}
 var lo=0,hi=n-1;while(hi-lo>1){var mid=(lo+hi)>>1;if(T[mid]<=t)lo=mid;else hi=mid;}var u=(t-T[lo])/((T[hi]-T[lo])||1),a=lo*k*cs+(cs===3?k:0),c=hi*k*cs+(cs===3?k:0);
 if(ch.interp==='STEP'){for(var s=0;s<k;s++)out[s]=V[a+s];return out;}
 if(k===4){var q=qslerp([V[a],V[a+1],V[a+2],V[a+3]],[V[c],V[c+1],V[c+2],V[c+3]],u);out[0]=q[0];out[1]=q[1];out[2]=q[2];out[3]=q[3];return out;}
 for(var e=0;e<k;e++)out[e]=V[a+e]+(V[c+e]-V[a+e])*u;return out;}
// bind a clip (from any glTF) to a target model by node name. Only root-ish translations are taken, scaled to the target.
R2.bindClip=function(clip,srcModel,dstModel){var map=[],rootNames={pelvis:1,hips:1,Hips:1,root:1,Root:1,mixamorigHips:1};var scale=1;
 var sp=srcModel&&srcModel.byName&&(srcModel.byName.pelvis!=null?srcModel.byName.pelvis:srcModel.byName.Hips),dp=dstModel.byName.pelvis!=null?dstModel.byName.pelvis:dstModel.byName.Hips;
 if(srcModel&&sp!=null&&dp!=null&&srcModel!==dstModel){var hs=srcModel.rest[sp][13],hd=dstModel.rest[dp][13];if(hs>0.05&&hd>0.05)scale=hd/hs;}
 clip.ch.forEach(function(ch){var ti=srcModel===dstModel?ch.node:dstModel.byName[ch.name];if(ti==null)return;if(ch.path==='scale')return;
  if(ch.path==='translation'&&srcModel!==dstModel&&!rootNames[ch.name])return;map.push({ch:ch,node:ti,tr:ch.path==='translation'});});
 return{name:clip.name,dur:clip.dur||1,map:map,scale:scale};};
// one animated character: local TRS per node, a current + previous clip with a crossfade
R2.Actor=function(G,clips){var M=G.M,N=M.nodes.length,A={G:G,M:M,clips:clips||{},cur:null,prev:null,t:0,pt:0,fade:0,fadeDur:0.2,speed:1,once:false,done:false,onEnd:null};
 A.lt=new Float32Array(N*3);A.lr=new Float32Array(N*4);A.ls=new Float32Array(N*3);var tmp=[0,0,0,0];
 function reset(){for(var i=0;i<N;i++){var n=M.nodes[i];A.lt.set(n.t,i*3);A.lr.set(n.r,i*4);A.ls.set(n.s,i*3);}}reset();
 A.has=function(n){return!!A.clips[n];};
 A.play=function(name,opt){opt=opt||{};var c=A.clips[name];if(!c)return false;if(A.cur===c&&!opt.restart){A.speed=opt.speed||A.speed;return true;}
  A.prev=A.cur;A.pt=A.t;A.cur=c;A.t=opt.at||0;A.fade=A.prev?(opt.fade==null?0.18:opt.fade):0;A.fadeDur=A.fade||1;A.speed=opt.speed||1;A.once=!!opt.once;A.done=false;A.onEnd=opt.onEnd||null;return true;};
 function apply(c,t,w){if(!c)return;c.map.forEach(function(b){var i=b.node;if(b.tr){var v=chanSample(b.ch,t,tmp);if(!v)return;var sc=c.scale;
   if(w>=1){A.lt[i*3]=v[0]*sc;A.lt[i*3+1]=v[1]*sc;A.lt[i*3+2]=v[2]*sc;}else for(var k=0;k<3;k++)A.lt[i*3+k]+=(v[k]*sc-A.lt[i*3+k])*w;}
  else{var q=chanSample(b.ch,t,tmp);if(!q)return;if(w>=1){A.lr[i*4]=q[0];A.lr[i*4+1]=q[1];A.lr[i*4+2]=q[2];A.lr[i*4+3]=q[3];}
   else{var r=qslerp([A.lr[i*4],A.lr[i*4+1],A.lr[i*4+2],A.lr[i*4+3]],[q[0],q[1],q[2],q[3]],w);A.lr.set(r,i*4);}}});}
 A.update=function(dt){if(!A.cur)return;A.t+=dt*A.speed;var d=A.cur.dur;if(A.t>=d){if(A.once){A.t=d;if(!A.done){A.done=true;if(A.onEnd){var cb=A.onEnd;A.onEnd=null;cb();}}}else A.t%=d;}
  if(A.prev){A.pt+=dt;A.fade-=dt;if(A.fade<=0)A.prev=null;}
  if(A.prev){apply(A.prev,A.pt%A.prev.dur,1);apply(A.cur,A.t,1-Math.max(0,A.fade)/A.fadeDur);}else apply(A.cur,A.t,1);};
 // world matrices of every node (model space) and the joint palette of every skin
 A.pose=function(){var Gm=new Array(N);function walk(i,pm){var l=m4trs([A.lt[i*3],A.lt[i*3+1],A.lt[i*3+2]],[A.lr[i*4],A.lr[i*4+1],A.lr[i*4+2],A.lr[i*4+3]],[A.ls[i*3],A.ls[i*3+1],A.ls[i*3+2]]);Gm[i]=pm?mul(pm,l):l;M.nodes[i].children.forEach(function(c){walk(c,Gm[i]);});}
  M.roots.forEach(function(r){walk(r,null);});for(var i=0;i<N;i++)if(!Gm[i])Gm[i]=m4();A.globals=Gm;
  A.joints=M.skins.map(function(s,si){var ib=s.ibmA||(s.ibmA=s.joints.map(function(_,j){return s.ibm?Array.prototype.slice.call(s.ibm,j*16,j*16+16):m4();}));
   var J=A['jt'+si]||(A['jt'+si]=new Float32Array(s.joints.length*16));s.joints.forEach(function(ni,j){J.set(mul(Gm[ni],ib[j]),j*16);});return J;});
  return Gm;};
 A.nj=M.skins.length?M.skins[0].joints.length:0;
 A.bone=function(name){var i=M.byName[name];return i==null||!A.globals?null:A.globals[i];};
 return A;};
// shared humanoid clip library (Universal Animation Library subset) bound per target model
R2.HumanClips={src:null,promise:null};
R2.loadHumanClips=function(path){if(!R2.HumanClips.promise)R2.HumanClips.promise=R2.loadGLTF(path).then(function(M){R2.HumanClips.src=M;return M;});return R2.HumanClips.promise;};
R2.clipsFor=function(G,src){var out={};var lib=src||R2.HumanClips.src;if(lib)lib.anims.forEach(function(a){out[a.name]=R2.bindClip(a,lib,G.M);});G.M.anims.forEach(function(a){out[a.name]=R2.bindClip(a,G.M,G.M);});return out;};
