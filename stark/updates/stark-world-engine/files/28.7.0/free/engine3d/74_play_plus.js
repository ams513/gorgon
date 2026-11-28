// ---------- Play+ (28.7): touch controls, gamepads, variables + on-screen counters, key / repeating / variable
// events, read-aloud, and the Tune player. Spliced into the engine after 70_logic.js (inside the GS closure), so it runs
// in the studio's Play mode and in every exported one-file cartridge. Offline: no network, no accounts, no analytics.
var PX={touch:'auto',tts:'off',rate:1};
function pxLoad(){try{var j=JSON.parse(localStorage.getItem('gs_play2')||'null');if(j&&typeof j==='object'){
 if(j.touch==='auto'||j.touch==='on'||j.touch==='off')PX.touch=j.touch;if(j.tts==='on'||j.tts==='off')PX.tts=j.tts;if(j.rate===0.8||j.rate===1||j.rate===1.25)PX.rate=j.rate;}}catch(e){}}
function pxSave(){try{localStorage.setItem('gs_play2',JSON.stringify(PX));}catch(e){}}
pxLoad();
// ----- every Input registers itself, so touch and gamepad can press the same keys the keyboard does -----
var PX_IN=[],PX_TOUCH=false,PX_HELD={};
(function(){var _I=Input;Input=function(t){_I.call(this,t);PX_IN.push(this);var self=this,_d=this.dispose;
 this.dispose=function(){var i=PX_IN.indexOf(self);if(i>=0)PX_IN.splice(i,1);return _d.apply(self,arguments);};};Input.prototype=_I.prototype;})();
function pxInput(){return PX_IN[PX_IN.length-1]||null;}
function pxKey(code,down){var I=pxInput();if(!I)return false;if(down){if(!I.keys[code])I.pressed[code]=true;I.keys[code]=true;
 if(I.onKey)I.onKey(code,{code:code,target:null,preventDefault:function(){}});}else I.keys[code]=false;return true;}
function pxHold(code,on){on=!!on;if(!!PX_HELD[code]===on)return;PX_HELD[code]=on;pxKey(code,on);}
function pxRelease(){for(var k in PX_HELD)if(PX_HELD[k])pxHold(k,false);}
try{if(typeof window!=='undefined'&&window.addEventListener)window.addEventListener('touchstart',function(){PX_TOUCH=true;},{passive:true});
 if(typeof window!=='undefined'&&window.matchMedia&&window.matchMedia('(pointer: coarse)').matches&&!window.matchMedia('(any-pointer: fine)').matches)PX_TOUCH=true;}catch(e){}
function pxTouchOn(){return PX.touch==='on'||(PX.touch==='auto'&&PX_TOUCH);}
// ----- on-screen touch controls: a joystick on the left, action buttons on the right -----
var PX_CSS='.gsui .pxt{position:absolute;inset:0;pointer-events:none;z-index:8;touch-action:none;-webkit-user-select:none;user-select:none}'+
'.gsui .pxs{position:absolute;left:18px;bottom:18px;width:136px;height:136px;border-radius:50%;background:rgba(255,255,255,.10);border:2px solid rgba(255,255,255,.28);pointer-events:auto;touch-action:none}'+
'.gsui .pxs i{position:absolute;left:38px;top:38px;width:56px;height:56px;border-radius:50%;background:rgba(255,255,255,.55);box-shadow:0 2px 10px rgba(0,0,0,.4)}'+
'.gsui .pxb{position:absolute;right:16px;bottom:16px;width:186px;height:150px;pointer-events:none}'+
'.gsui .pxt button{pointer-events:auto;touch-action:none;border:2px solid rgba(255,255,255,.35);background:rgba(10,14,22,.55);color:#fff;font:700 13px system-ui,sans-serif;border-radius:50%;padding:0}'+
'.gsui .pxt button:active,.gsui .pxt button.dn{background:rgba(95,211,181,.55)}'+
'.gsui .pxb .j{position:absolute;right:0;bottom:0;width:78px;height:78px;font-size:15px}.gsui .pxb .a{position:absolute;right:86px;bottom:8px;width:66px;height:66px}.gsui .pxb .u{position:absolute;right:16px;bottom:88px;width:58px;height:58px}'+
'.gsui .pxm{position:absolute;left:14px;bottom:166px;max-width:min(420px,56vw);display:flex;flex-wrap:wrap;gap:6px;pointer-events:none}.gsui .pxm button{border-radius:10px;padding:8px 10px;min-width:48px;font-size:12px}'+
'.gsui .pxsk{position:absolute;right:16px;bottom:16px;border-radius:12px!important;padding:12px 18px!important}'+
'.gsui.pxon .mm{bottom:174px;width:110px;height:110px}@media (max-width:899px),(max-height:700px){.gsui.pxon .mm{display:none}}'+
'body.pxtouch #hud{display:none}@media (max-width:520px){.gsui .hl{min-width:0;max-width:calc(56% - 14px);padding:8px 10px;font-size:13px}.gsui .tr{max-width:calc(44% - 14px);padding:8px 10px;font-size:12px}}'+
'.gsui .pxv{margin-top:6px;font-size:13px}.gsui .pxv b{color:#ffd166}.gsui .pxf{outline:3px solid #5fd3b5!important;outline-offset:2px}';
var PX_ABIL=[['ShiftLeft','Dash','Dash'],['KeyK','Whirl','Whirl',1],['KeyF','Star Bolt','Bolt']];
function pxEls(G){if(G._pxEl||!G.root)return G._pxEl||null;if(!document.getElementById('gs-px-css')){var st=document.createElement('style');st.id='gs-px-css';st.textContent=PX_CSS;document.head.appendChild(st);}
 var d=document.createElement('div');d.className='pxt';d.hidden=true;
 d.innerHTML='<div class="pxs" aria-label="Move"><i></i></div><div class="pxb"><button class="u" data-k="KeyE" aria-label="Talk / open">Use</button><button class="a" data-k="KeyJ" aria-label="Attack">Hit</button><button class="j" data-k="Space" aria-label="Jump">Jump</button></div>'+
  '<div class="pxm">'+PX_ABIL.map(function(a){return'<button data-k="'+a[0]+'" data-ab="'+a[1]+'"'+(a[3]?' data-d3="1"':'')+' hidden>'+a[2]+'</button>';}).join('')+
  '<button data-k="KeyH" aria-label="Drink a potion">Potion</button><button data-k="KeyI">Bag</button><button data-k="KeyL">Quests</button><button data-k="Escape">Menu</button></div><button class="pxsk" data-k="Space" hidden>Skip</button>';
 G.root.appendChild(d);var el={root:d,stick:d.querySelector('.pxs'),knob:d.querySelector('.pxs i'),pad:d.querySelector('.pxb'),more:d.querySelector('.pxm'),skip:d.querySelector('.pxsk'),ab:[].slice.call(d.querySelectorAll('[data-ab]'))};
 [].forEach.call(d.querySelectorAll('button[data-k]'),function(b){var code=b.getAttribute('data-k'),on=false;
  function dn(e){if(e&&e.preventDefault)e.preventDefault();if(on)return;on=true;b.classList.add('dn');pxKey(code,true);}
  function up(){if(!on)return;on=false;b.classList.remove('dn');pxKey(code,false);}
  b.addEventListener('pointerdown',dn);b.addEventListener('pointerup',up);b.addEventListener('pointercancel',up);b.addEventListener('pointerleave',up);
  b.addEventListener('contextmenu',function(e){e.preventDefault();});});
 var sid=null,cx=0,cy=0,R=50,st2=el.stick;
 function mv(e){var dx=e.clientX-cx,dy=e.clientY-cy,l=Math.hypot(dx,dy),k=l>R?R/l:1;el.knob.style.transform='translate('+(dx*k).toFixed(1)+'px,'+(dy*k).toFixed(1)+'px)';pxDir(dx/R,dy/R);}
 st2.addEventListener('pointerdown',function(e){sid=e.pointerId;var r=st2.getBoundingClientRect();cx=r.left+r.width/2;cy=r.top+r.height/2;try{st2.setPointerCapture(sid);}catch(x){}mv(e);if(e.preventDefault)e.preventDefault();});
 st2.addEventListener('pointermove',function(e){if(e.pointerId===sid)mv(e);});
 function end(e){if(e.pointerId!==sid)return;sid=null;el.knob.style.transform='';pxDir(0,0);}
 st2.addEventListener('pointerup',end);st2.addEventListener('pointercancel',end);
 G._pxEl=el;return el;}
function pxDir(x,y){var t=0.35;pxHold('KeyW',y<-t);pxHold('KeyS',y>t);pxHold('KeyA',x<-t);pxHold('KeyD',x>t);}
function pxTouchView(G,dt){var S=pxS(G),want=pxTouchOn()&&!!G.engine&&!G.titleOn,el=want?pxEls(G):G._pxEl;if(!el)return;
 var mode=!want?'off':G.cut?(G.cut.skip?'skip':'off'):(G.dlgOpen||G.modal)?'off':'play';
 if(mode!==S.tv){S.tv=mode;el.root.hidden=mode==='off';el.stick.hidden=el.pad.hidden=el.more.hidden=mode!=='play';el.skip.hidden=mode!=='skip';
  if(G.root.classList)G.root.classList[mode==='play'?'add':'remove']('pxon');if(document.body&&document.body.classList)document.body.classList[mode==='play'?'add':'remove']('pxtouch');if(mode!=='play'){pxDir(0,0);}}
 S.abT=(S.abT||0)-dt;if(mode==='play'&&S.abT<=0){S.abT=0.5;var d3=G.engine&&G.engine.kind==='3d';el.ab.forEach(function(b){b.hidden=!G.hasAbility(b.getAttribute('data-ab'))||(b.getAttribute('data-d3')==='1'&&!d3);});}}
// ----- gamepads (standard mapping): play, menus and conversations -----
var PAD_PLAY={0:'Space',1:'KeyE',2:'KeyJ',3:'KeyH',4:'KeyZ',5:'KeyC',6:'ShiftLeft',7:'KeyK',8:'KeyI',9:'Escape',10:'KeyL',11:'KeyF'};
function pxPadGet(){try{var ps=navigator.getGamepads?navigator.getGamepads():[];for(var i=0;i<(ps?ps.length:0);i++)if(ps[i]&&ps[i].connected!==false)return ps[i];}catch(e){}return null;}
function pxFocusables(G){var c=G.titleOn?G.titleEl:G.modalEl||(G.dlgOpen&&G.el?G.el.dlg:null);if(!c||!c.querySelectorAll)return null;
 var l=[].slice.call(c.querySelectorAll('button:not([disabled])')).filter(function(b){return!b.hidden;});return l.length?l:null;}
function pxFocusMove(G,S,d){var L=pxFocusables(G);if(!L)return;var i=L.indexOf(S.fe);i=i<0?(d>0?0:L.length-1):(i+d+L.length)%L.length;if(S.fe&&S.fe.classList)S.fe.classList.remove('pxf');S.fe=L[i];if(S.fe.classList)S.fe.classList.add('pxf');try{S.fe.focus();}catch(e){}}
function pxPad(G,dt){var p=pxPadGet(),S=pxS(G),ph=function(c,on){on=!!on;if(!!S.ph[c]!==on){S.ph[c]=on;pxHold(c,on);}};S.ph=S.ph||{};
 if(!p){if(S.padOn){S.padOn=false;for(var k in S.ph)ph(k,false);}return;}
 if(!S.padOn){S.padOn=true;S.pb={};G.toast('Gamepad ready: A jump, X attack, B talk, Start menu','l');}
 var bt=function(i){var x=p.buttons&&p.buttons[i];return!!(x&&(x.pressed||x.value>0.5));},ax=p.axes||[],now={},i;for(i=0;i<16;i++)now[i]=bt(i);
 var up=now[12]||ax[1]<-0.5,dn=now[13]||ax[1]>0.5,lf=now[14]||ax[0]<-0.5,rt=now[15]||ax[0]>0.5,edge=function(i){return now[i]&&!S.pb[i];};
 var nav=G.titleOn||!!G.modal||(G.dlgOpen&&(G.dlgCh||[]).length>0);
 if(nav){for(var k2 in S.ph)ph(k2,false);var dir=(dn||rt)?1:(up||lf)?-1:0;if(dir&&dir!==S.nd){pxFocusMove(G,S,dir);}S.nd=dir;
  if(edge(0)){var L=pxFocusables(G);if(L&&L.indexOf(S.fe)>=0)S.fe.click();else if(G.dlgOpen&&L)L[0].click();else if(G.titleOn&&G._titleGo)G._titleGo(false);else pxFocusMove(G,S,1);}
  if((edge(1)||edge(9))&&!G.titleOn){pxKey('Escape',true);pxKey('Escape',false);}
  S.pb=now;return;}
 S.nd=0;if(S.fe&&S.fe.classList){S.fe.classList.remove('pxf');S.fe=null;}
 if(G.cut){if(edge(0)||edge(9)){pxKey('Space',true);pxKey('Space',false);}S.pb=now;return;}
 ph('KeyW',up);ph('KeyS',dn);ph('KeyA',lf);ph('KeyD',rt);
 ph('KeyZ',now[4]||ax[2]<-0.5);ph('KeyC',now[5]||ax[2]>0.5);
 for(var b in PAD_PLAY){if(b==='4'||b==='5')continue;ph(PAD_PLAY[b],now[b]);}
 S.pb=now;}
// ----- variables (numbers) + on-screen counters -----
Game.prototype.vdefs=function(){return Array.isArray(this.world.vars)?this.world.vars:[];};
function pxVars(G){if(!G.vars||typeof G.vars!=='object'){G.vars={};G.vdefs().forEach(function(d){if(d&&d.id)G.vars[d.id]=isFinite(+d.start)?+d.start:0;});}return G.vars;}
function pxDef(G,id){return G.vdefs().filter(function(x){return x&&x.id===id;})[0]||null;}
function pxClamp(G,id,v){var d=pxDef(G,id);v=Math.round((+v||0)*1000)/1000;if(d){if(d.min!=null&&v<+d.min)v=+d.min;if(d.max!=null&&v>+d.max)v=+d.max;}return Math.max(-1e6,Math.min(1e6,v));}
function pxSetVar(G,a){var V=pxVars(G),id=a&&a.var;if(!id)return;var cur=+V[id]||0,n=+a.num||0,v;
 switch(a.op){case'add':v=cur+n;break;case'sub':v=cur-n;break;case'random':var hi=a.max==null?n:+a.max,lo=Math.min(n,hi);hi=Math.max(n,hi);v=lo+Math.floor(Math.random()*(Math.floor(hi)-Math.floor(lo)+1));break;default:v=n;}
 V[id]=pxClamp(G,id,v);pxHud(G);}
function pxCmp(v,op,n){v=+v||0;n=+n||0;switch(op){case'ne':return v!==n;case'lt':return v<n;case'le':return v<=n;case'gt':return v>n;case'ge':return v>=n;default:return v===n;}}
function pxFmt(v){v=+v||0;return Math.abs(v-Math.round(v))<1e-9?String(Math.round(v)):v.toFixed(2).replace(/0+$/,'');}
function pxHud(G){var hl=G.el&&G.el.hl;if(!hl||!hl.querySelector)return;var d=G.vdefs().filter(function(x){return x&&x.id&&x.show;}),box=hl.querySelector('.pxv');
 if(box&&box.className!=='pxv')box=null;if(!d.length){if(box&&box.parentNode)box.parentNode.removeChild(box);return;}
 if(!box){box=document.createElement('div');box.className='pxv';hl.appendChild(box);}var V=pxVars(G);
 box.innerHTML=d.map(function(x){return'<div><span class="mute">'+esc(x.name||x.id)+'</span> <b>'+esc(pxFmt(V[x.id]))+(x.max!=null&&x.show==='max'?' / '+esc(pxFmt(x.max)):'')+'</b></div>';}).join('');}
// ----- read text aloud: only voices built into this computer (localService) are used, so nothing goes online -----
function pxVoices(){try{return(typeof speechSynthesis!=='undefined'&&speechSynthesis.getVoices?speechSynthesis.getVoices():[]).filter(function(v){return v&&v.localService;});}catch(e){return[];}}
function pxSpeak(text){if(PX.tts!=='on'||!LG_CUR||!text||typeof speechSynthesis==='undefined'||typeof SpeechSynthesisUtterance==='undefined')return false;var vs=pxVoices();if(!vs.length)return false;
 try{speechSynthesis.cancel();var u=new SpeechSynthesisUtterance(String(text).slice(0,500)),lang=String((document.documentElement&&document.documentElement.lang)||navigator.language||'en').slice(0,2).toLowerCase();
  u.voice=vs.filter(function(v){return String(v.lang||'').slice(0,2).toLowerCase()===lang;})[0]||vs[0];u.rate=PX.rate;speechSynthesis.speak(u);return true;}catch(e){return false;}}
function pxHush(){try{if(typeof speechSynthesis!=='undefined')speechSynthesis.cancel();}catch(e){}}
// ----- Tune player: a looping step tune (melody + drums) stored in world.tune, played with WebAudio -----
var TUNE_SC={penta:[0,2,4,7,9],major:[0,2,4,5,7,9,11],minor:[0,2,3,5,7,8,10]},TUNE_ROWS=12,TUNE_WAVES={square:'square',triangle:'triangle',sine:'sine',saw:'sawtooth'};
function tuneHas(t){return!!(t&&typeof t==='object'&&((Array.isArray(t.mel)&&t.mel.length)||(Array.isArray(t.drum)&&t.drum.length)));}
function tuneFreq(sc,row,oct){var s=TUNE_SC[sc]||TUNE_SC.penta,o=Math.floor(row/s.length),d=s[row%s.length];return 261.63*Math.pow(2,(o*12+d)/12+(+oct||0));}
function Tune(t){this.t=t||{};this.on=false;this.step=0;this.next=0;this.timer=null;this.map={};var n=this.len(),m=this.map;
 (this.t.mel||[]).forEach(function(x){if(Array.isArray(x)&&x[0]>=0&&x[0]<n){(m[x[0]]=m[x[0]]||[]).push(['m',x[1]|0]);}});
 (this.t.drum||[]).forEach(function(x){if(Array.isArray(x)&&x[0]>=0&&x[0]<n){(m[x[0]]=m[x[0]]||[]).push(['d',x[1]|0]);}});}
Tune.prototype.len=function(){return(+this.t.len===32)?32:16;};
Tune.prototype.spb=function(){return 60/Math.max(60,Math.min(200,+this.t.bpm||110))/4;};
Tune.prototype.start=function(){if(this.on||!tuneHas(this.t))return false;try{if(!AC)AC=new(window.AudioContext||window.webkitAudioContext)();}catch(e){return false;}
 if(AC.state==='suspended'&&AC.resume){try{AC.resume();}catch(e){}}var self=this;this.out=AC.createGain();this.out.gain.value=(this.t.vol==null?0.6:Math.max(0,Math.min(1,+this.t.vol)))*0.3;this.out.connect(AC.destination);
 this.next=AC.currentTime+0.06;this.step=0;this.on=true;this.timer=setInterval(function(){self.tick();},40);return true;};
Tune.prototype.tick=function(){if(!this.on||!AC)return;var spb=this.spb(),n=this.len();if(this.next<AC.currentTime-0.5)this.next=AC.currentTime+0.02;
 while(this.next<AC.currentTime+0.15){if(!MUTE)this.notes(this.step,this.next,spb);this.next+=spb;this.step=(this.step+1)%n;}};
Tune.prototype.notes=function(step,t0,spb){var L=this.map[step];if(!L)return;for(var i=0;i<L.length;i++){if(L[i][0]==='m')this.tone(L[i][1],t0,spb);else this.drum(L[i][1],t0);}};
Tune.prototype.tone=function(row,t0,spb){try{var o=AC.createOscillator(),g=AC.createGain(),d=spb*1.6;o.type=TUNE_WAVES[this.t.wave]||'square';o.frequency.value=tuneFreq(this.t.scale,row,this.t.oct);
 g.gain.setValueAtTime(0.0001,t0);g.gain.exponentialRampToValueAtTime(o.type==='square'||o.type==='sawtooth'?0.35:0.7,t0+0.012);g.gain.exponentialRampToValueAtTime(0.0001,t0+d);o.connect(g);g.connect(this.out);o.start(t0);o.stop(t0+d+0.02);}catch(e){}};
Tune.prototype.noise=function(){if(!Tune._nb){var n=Math.floor(AC.sampleRate*0.25),b=AC.createBuffer(1,n,AC.sampleRate),d=b.getChannelData(0),r=rng(99);for(var i=0;i<n;i++)d[i]=r()*2-1;Tune._nb=b;}return Tune._nb;};
Tune.prototype.drum=function(k,t0){try{var g=AC.createGain();g.connect(this.out);
 if(k===0){var o=AC.createOscillator();o.type='sine';o.frequency.setValueAtTime(150,t0);o.frequency.exponentialRampToValueAtTime(42,t0+0.14);g.gain.setValueAtTime(1.1,t0);g.gain.exponentialRampToValueAtTime(0.0001,t0+0.2);o.connect(g);o.start(t0);o.stop(t0+0.22);return;}
 var s=AC.createBufferSource(),f=AC.createBiquadFilter();s.buffer=this.noise();f.type='highpass';f.frequency.value=k===2?7000:1200;s.connect(f);f.connect(g);var len=k===2?0.045:0.14;
 g.gain.setValueAtTime(k===2?0.35:0.7,t0);g.gain.exponentialRampToValueAtTime(0.0001,t0+len);s.start(t0);s.stop(t0+len+0.02);}catch(e){}};
Tune.prototype.stop=function(){if(this.timer)clearInterval(this.timer);this.timer=null;this.on=false;try{if(this.out)this.out.disconnect();}catch(e){}this.out=null;};
// ----- per-game state + events -----
function pxS(G){if(!G._px)G._px={ev:{},vin:{},vp:0,pb:{},tv:null};return G._px;}
function pxRepeat(G,t,i){var c={};for(var k in t)c[k]=t[k];c.once=false;return lgFire(G,c,i);}
function pxTick(G,dt){if(!G.engine||G.titleOn||!G._lg)return;var S=pxS(G);if(S.eng!==G.engine){S.eng=G.engine;S.ev={};S.vin={};S.vp=0;}
 if(!G.modal&&!G.cut&&!G.won&&!G.dlgOpen)lgEach(G,'every',function(t,i){var k=G.li+':'+(t.id||i),s=Math.max(0.5,+t.when.secs||5);S.ev[k]=(S.ev[k]||0)+dt;if(S.ev[k]>=s){S.ev[k]-=s;pxRepeat(G,t,i);}});
 S.vp-=dt;if(S.vp<=0){S.vp=0.1;var V=pxVars(G);lgEach(G,'var',function(t,i){var w=t.when,k=G.li+':'+(t.id||i),on=pxCmp(V[w.var],w.cmp,w.num),was=!!S.vin[k];S.vin[k]=on;if(on&&!was)lgFire(G,t,i);});}}
var _pxDo=lgDo;lgDo=function(G,a,inCut){if(a&&a.do==='var'){pxSetVar(G,a);return null;}return _pxDo(G,a,inCut);};
if(LG_VERBS.indexOf('var')<0)LG_VERBS.push('var');if(LG_CUTV.indexOf('var')<0)LG_CUTV.push('var');
(function(){var P=Game.prototype,_upd=P.update,_keys=P.keys,_cond=P.cond,_snap=P.snapshot,_load=P.load,_ref=P.refresh,_act=P.act,_att=P.attach,_dest=P.destroy,_sub=P.sub,_show=P.showNode,_close=P.closeDlg,_acc=P.openAccess;
 P.update=function(dt){_upd.call(this,dt);try{pxTick(this,dt);pxPad(this,dt);pxTouchView(this,dt);if(!this._tune&&!this.titleOn&&this.engine&&tuneHas(this.world.tune)&&!window.STARK_AUDIO_PACK){this._tune=new Tune(this.world.tune);if(!this._tune.start())this._tuneFail=(this._tuneFail||0)+1;if(this._tuneFail&&this._tuneFail<5&&!this._tune.on)this._tune=null;}}catch(e){if(window.console)console.error('[play+]',e);}};
 P.keys=function(c){var r=_keys.call(this,c),G=this;if(!r&&G.engine&&!G.titleOn&&!G.cut&&!G.modal&&!G.dlgOpen&&G._lg)lgEach(G,'key',function(t,i){if(t.when.key===c)lgFire(G,t,i);});return r;};
 P.cond=function(c){if(!_cond.call(this,c))return false;if(c&&c.var&&!pxCmp(pxVars(this)[c.var],c.cmp,c.num))return false;if(c&&c.chance!=null&&!(Math.random()*100<+c.chance))return false;return true;};
 P.snapshot=function(){var j=JSON.parse(_snap.call(this));j.vars=pxVars(this);return JSON.stringify(j);};
 P.load=function(slot){var vj=null;try{var j=JSON.parse(localStorage.getItem(this.key(slot))||'null');vj=j&&j.vars&&typeof j.vars==='object'?j.vars:null;}catch(e){}
  var ok=_load.call(this,slot);if(ok){this.vars=null;var V=pxVars(this);if(vj)for(var k in V)if(isFinite(+vj[k]))V[k]=+vj[k];pxHud(this);}return ok;};
 P.refresh=function(){_ref.apply(this,arguments);pxHud(this);};
 P.act=function(c){_act.apply(this,arguments);if(c&&c.var)pxSetVar(this,c);};
 P.attach=function(parent){_att.call(this,parent);pxVars(this);pxHud(this);};
 P.destroy=function(){if(this._tune){this._tune.stop();this._tune=null;}pxRelease();pxHush();if(document.body&&document.body.classList)document.body.classList.remove('pxtouch');_dest.call(this);};
 P.sub=function(speaker,text,secs,force){_sub.apply(this,arguments);pxSpeak((speaker?speaker+'. ':'')+(text||''));};
 P.showNode=function(npc,D,id){var r=_show.apply(this,arguments),node=D&&D.nodes&&D.nodes[id];if(node&&node.text)pxSpeak(((node.speaker||(npc&&npc.name))?(node.speaker||npc.name)+'. ':'')+node.text);return r;};
 P.closeDlg=function(){pxHush();return _close.apply(this,arguments);};
 P.openAccess=function(back){_acc.call(this,back);var m=this.modalEl,self=this;if(!m)return;var pad=pxPadGet(),vs=pxVoices(),sel=function(cur,v){return cur===v?'border-color:#5fd3b5':'';};
  var d=document.createElement('div');d.innerHTML='<h3 style="margin:10px 0 4px">On-screen touch controls</h3><div class="row">'+[['auto','Auto'],['on','On'],['off','Off']].map(function(x){return'<button class="btn" data-pxt="'+x[0]+'" style="'+sel(PX.touch,x[0])+'">'+x[1]+'</button>';}).join('')+'</div><div class="mute">Auto shows a joystick and buttons as soon as the screen is touched (phones, tablets, touch laptops).</div>'+
   '<h3 style="margin:10px 0 4px">Gamepad</h3><div class="mute">'+(pad?'Connected: '+esc(String(pad.id||'gamepad').slice(0,60))+'.':'Plug in a gamepad and press a button.')+' A jump, X attack, B talk / back, Y potion, LT dash, RT whirl, right-stick click bolt, LB / RB or right stick turn the camera, Back bag, left-stick click quests, Start menu. In menus: D-pad to choose, A to press.</div>'+
   '<h3 style="margin:10px 0 4px">Read text aloud</h3><div class="row">'+[['off','Off'],['on','On']].map(function(x){return'<button class="btn" data-pxs="'+x[0]+'" style="'+sel(PX.tts,x[0])+'">'+x[1]+'</button>';}).join('')+[[0.8,'Slow'],[1,'Normal'],[1.25,'Fast']].map(function(x){return'<button class="btn" data-pxr="'+x[0]+'" style="'+sel(PX.rate,x[0])+'">'+x[1]+'</button>';}).join('')+'</div>'+
   '<div class="mute">'+(vs.length?'Conversations and subtitles are read by a voice built into this computer ('+vs.length+' found). Nothing is sent online.':'No offline voice was found on this computer, so nothing will be read. (Online voices are never used.)')+'</div>';
  var rows=m.querySelectorAll?m.querySelectorAll('.row'):[],last=rows.length?rows[rows.length-1]:null;if(last&&last.parentNode===m&&m.insertBefore)m.insertBefore(d,last);else m.appendChild(d);
  var again=function(){self.openAccess(back);};
  [].forEach.call(d.querySelectorAll('[data-pxt]'),function(b){b.onclick=function(){PX.touch=b.getAttribute('data-pxt');pxSave();pxS(self).tv=null;again();};});
  [].forEach.call(d.querySelectorAll('[data-pxs]'),function(b){b.onclick=function(){PX.tts=b.getAttribute('data-pxs');pxSave();if(PX.tts==='on')pxSpeak('Reading aloud is on.');else pxHush();again();};});
  [].forEach.call(d.querySelectorAll('[data-pxr]'),function(b){b.onclick=function(){PX.rate=+b.getAttribute('data-pxr');pxSave();pxSpeak('This is the reading speed.');again();};});};})();
var PLX={opts:PX,save:pxSave,reload:pxLoad,vars:pxVars,setVar:pxSetVar,cmp:pxCmp,fmt:pxFmt,key:pxKey,hold:pxHold,release:pxRelease,dir:pxDir,touchOn:pxTouchOn,markTouch:function(v){PX_TOUCH=v!==false;},
 inputs:function(){return PX_IN;},newInput:function(){return new Input(null);},voices:pxVoices,speak:pxSpeak,pad:PAD_PLAY,padTick:pxPad,touchView:pxTouchView,Tune:Tune,tuneHas:tuneHas,tuneFreq:tuneFreq,scales:TUNE_SC,rows:TUNE_ROWS};
