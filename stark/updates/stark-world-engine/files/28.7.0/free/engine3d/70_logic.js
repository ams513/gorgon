// ---------- Logic: when/then triggers, cutscenes, shops, achievements, high scores, accessibility ----------
// Spliced into the engine (inside the GS closure, after Game). Everything is data in the world (triggers / cutscenes /
// achievements / npc.shop / npc.behavior) and works in the editor's Play mode and in the exported one-file cartridge.
var LG_CUR=null;
var LG_VERBS=['door','sound','dialogue','say','flag','give','take','gold','xp','heal','startQuest','completeQuest','cutscene','teleport','level','win','achievement','shake','wait'];
var LG_CUTV=['camera','say','move','sound','fade','shake','flag','give','take','gold','xp','heal','door','teleport','achievement'];
var A11Y_ACTIONS=[['up','Move up','KeyW'],['down','Move down','KeyS'],['left','Move left','KeyA'],['right','Move right','KeyD'],['jump','Jump','Space'],['attack','Attack','KeyJ'],['interact','Talk / open','KeyE'],['dash','Dash','ShiftLeft'],['whirl','Whirl','KeyK'],['bolt','Star Bolt','KeyF'],['potion','Potion','KeyH'],['quests','Quest log','KeyL'],['bag','Bag','KeyI'],['camL','Camera left','KeyZ'],['camR','Camera right','KeyC']];
var A11Y={keys:{},cb:'off',subs:'on',slow:1};
var CB_MODES=[['off','Off'],['protan','Red-weak (protanopia)'],['deutan','Green-weak (deuteranopia)'],['tritan','Blue-weak (tritanopia)']];
var A11Y_FWD=null;
function a11yLoad(){try{var j=JSON.parse(localStorage.getItem('gs_access')||'null');if(j&&typeof j==='object'){
 if(j.keys&&typeof j.keys==='object'){A11Y_ACTIONS.forEach(function(a){var c=j.keys[a[0]];if(typeof c==='string'&&/^[A-Za-z0-9]{1,20}$/.test(c))A11Y.keys[a[0]]=c;});}
 if(CB_MODES.some(function(m){return m[0]===j.cb;}))A11Y.cb=j.cb;if(j.subs==='on'||j.subs==='large'||j.subs==='off')A11Y.subs=j.subs;if(j.slow===1||j.slow===0.75||j.slow===0.5)A11Y.slow=j.slow;}}catch(e){}a11yFwd();}
function a11ySave(){try{localStorage.setItem('gs_access',JSON.stringify(A11Y));}catch(e){}}
// physical key -> the default key the engine already understands (so every rebind works with no engine change)
function a11yFwd(){var f={},d={};A11Y_ACTIONS.forEach(function(a){d[a[0]]=a[2];});
 A11Y_ACTIONS.forEach(function(a){var b=A11Y.keys[a[0]];if(b&&b!==a[2])f[a[2]]='None';});
 A11Y_ACTIONS.forEach(function(a){var b=A11Y.keys[a[0]];if(b&&b!==a[2])f[b]=a[2];});A11Y_FWD=f;}
function KM(c){return(LG_CUR&&A11Y_FWD&&A11Y_FWD[c])||c;}
function TSC(){return LG_CUR?A11Y.slow:1;}
function a11yBind(action,code){var cur=A11Y.keys,def={};A11Y_ACTIONS.forEach(function(a){def[a[0]]=a[2];});
 if(!def[action]||!/^[A-Za-z0-9]{1,20}$/.test(code)||/^(Escape|Tab|Enter|F\d+|Digit\d)$/.test(code))return false;
 A11Y_ACTIONS.forEach(function(a){var k=cur[a[0]]||a[2];if(a[0]!==action&&k===code){cur[a[0]]=cur[action]||def[action];}});
 if(code===def[action])delete cur[action];else cur[action]=code;
 A11Y_ACTIONS.forEach(function(a){if(cur[a[0]]===a[2])delete cur[a[0]];});a11yFwd();a11ySave();return true;}
function a11yKeyOf(action){var a=A11Y_ACTIONS.filter(function(x){return x[0]===action;})[0];return A11Y.keys[action]||(a&&a[2]);}
function keyLabel(c){return String(c).replace(/^Key/,'').replace(/^Digit/,'').replace('ArrowUp','Up').replace('ArrowDown','Down').replace('ArrowLeft','Left').replace('ArrowRight','Right').replace(/^Shift(Left|Right)$/,'Shift').replace('Space','Space');}
// colour-blind palette: daltonisation as an SVG colour matrix on the game canvas and HUD (no shader change)
var CB_SIM={protan:[[0.152286,1.052583,-0.204868],[0.114503,0.786281,0.099216],[-0.003882,-0.048116,1.051998]],deutan:[[0.367322,0.860646,-0.227968],[0.280085,0.672501,0.047413],[-0.011820,0.042940,0.968881]],tritan:[[1.255528,-0.076749,-0.178779],[-0.078411,0.930809,0.147602],[0.004733,0.691367,0.303900]]};
function cbMatrix(mode){var S=CB_SIM[mode];if(!S)return null;var sh=mode==='tritan'?[[1,0,0.7],[0,1,0.7],[0,0,0]]:[[0,0,0],[0.7,1,0],[0.7,0,1]],M=[[0,0,0],[0,0,0],[0,0,0]];
 for(var i=0;i<3;i++)for(var j=0;j<3;j++){var e=0;for(var k=0;k<3;k++)e+=sh[i][k]*((k===j?1:0)-S[k][j]);M[i][j]=(i===j?1:0)+e;}return M;}
function a11yApply(){if(typeof document==='undefined'||!document.documentElement)return;var de=document.documentElement;
 if(!document.getElementById('gs-a11y-css')){var st=document.createElement('style');st.id='gs-a11y-css';var h='';
  ['protan','deutan','tritan'].forEach(function(m){h+='html[data-gscb="'+m+'"] canvas,html[data-gscb="'+m+'"] .gsui{filter:url(#gscb-'+m+')}';});st.textContent=h;document.head.appendChild(st);
  var d=document.createElement('div');d.style.cssText='position:absolute;width:0;height:0;overflow:hidden';d.setAttribute('aria-hidden','true');
  var svg='<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0">';['protan','deutan','tritan'].forEach(function(m){var M=cbMatrix(m);
   svg+='<filter id="gscb-'+m+'" color-interpolation-filters="sRGB"><feColorMatrix type="matrix" values="'+M.map(function(r){return r.map(function(v){return v.toFixed(4);}).join(' ')+' 0 0';}).join(' ')+' 0 0 0 1 0"/></filter>';});
  d.innerHTML=svg+'</svg>';document.body.appendChild(d);}
 if(LG_CUR&&A11Y.cb!=='off')de.setAttribute('data-gscb',A11Y.cb);else de.removeAttribute('data-gscb');}
a11yLoad();
// ---------- per-game state (saved with the game) ----------
function lgs(G){if(!G._lg){G._lg={fired:{},kills:0,shop:{},visited:{},runT:0,bossKilled:false,scored:false};G._lg.visited[G.li]=1;G.ach=lgAchLoad(G);G._lgQ=[];G._lgIn={};G._lgT=0;G._lgPoll=0;G._lgAchT=0;}return G._lg;}
function lgAchKey(G){return'gs_ach|'+(G.world.name||'game')+'|'+(G.key?G.key('x').split('|')[2]:'');}
function lgAchLoad(G){try{var j=JSON.parse(localStorage.getItem(lgAchKey(G))||'null');return j&&typeof j==='object'?j:{};}catch(e){return{};}}
function lgAchSave(G){try{localStorage.setItem(lgAchKey(G),JSON.stringify(G.ach));}catch(e){}}
Game.prototype.cutscenes=function(){var o={};this.levels.forEach(function(L){var d=L.cutscenes||{};for(var k in d)o[k]=d[k];});return o;};
Game.prototype.levelTriggers=function(){var L=this.levels[this.li]||{};return Array.isArray(L.triggers)?L.triggers:[];};
// ---------- achievements ----------
var ACH_BUILTIN=[{id:'first_blood',title:'First Blood',desc:'Defeat your first enemy.',when:{type:'kills',n:1}},{id:'hunter',title:'Monster Hunter',desc:'Defeat 25 enemies.',when:{type:'kills',n:25}},
 {id:'boss_slayer',title:'Giant Slayer',desc:'Defeat a boss.',when:{type:'boss'}},{id:'pocket_change',title:'Pocket Change',desc:'Hold 100 gold at once.',when:{type:'gold',n:100}},
 {id:'treasure',title:'Dragon Hoard',desc:'Hold 1000 gold at once.',when:{type:'gold',n:1000}},{id:'level5',title:'Seasoned',desc:'Reach level 5.',when:{type:'level',n:5}},
 {id:'quest1',title:'Helping Hand',desc:'Finish a quest.',when:{type:'quests',n:1}},{id:'quest5',title:'Folk Hero',desc:'Finish 5 quests.',when:{type:'quests',n:5}},
 {id:'wanderer',title:'Wanderer',desc:'Visit 2 different levels.',when:{type:'levels',n:2}},{id:'finish',title:'World Complete',desc:'Beat the game.',when:{type:'win'}}];
Game.prototype.achievements=function(){var o=[],seen={};this.levels.forEach(function(L){(L.achievements||[]).forEach(function(a){if(a&&a.id&&!seen[a.id]){seen[a.id]=1;o.push(a);}});});
 ACH_BUILTIN.forEach(function(a){if(!seen[a.id]){seen[a.id]=1;o.push(a);}});return o;};
Game.prototype.unlock=function(id){lgs(this);if(this.ach[id])return false;var a=this.achievements().filter(function(x){return x.id===id;})[0];if(!a)return false;
 this.ach[id]=Date.now();lgAchSave(this);this.toast('Achievement: '+a.title,'a');playSfx(SFX.levelup);return true;};
function lgAchMet(G,w){var s=G._lg,n=+w.n||1;switch(w.type){case'kills':return s.kills>=n;case'boss':return!!s.bossKilled;case'gold':return G.st.gold>=n;case'level':return G.st.lvl>=n;
 case'quests':return Object.keys(G.q).filter(function(k){return G.q[k].state==='done';}).length>=n;case'flag':return!!G.flags[w.flag];case'item':return G.has(w.item,n);
 case'win':return!!G.won;case'levels':return Object.keys(s.visited).length>=n;case'time':return!!G.won&&s.runT<=n;}return false;}
// ---------- high scores (local) ----------
function lgScoreKey(G){return'gs_scores|'+(G.world.name||'game')+'|'+(G.key?G.key('x').split('|')[2]:'');}
Game.prototype.scores=function(){try{var j=JSON.parse(localStorage.getItem(lgScoreKey(this))||'[]');return Array.isArray(j)?j.filter(function(e){return e&&isFinite(+e.s);}).slice(0,10):[];}catch(e){return[];}};
Game.prototype.byTime=function(){return this.world.scoring==='time';};
Game.prototype.addScore=function(name){var s=lgs(this),e={n:String(name||'Player').slice(0,16),s:Math.round(this.st.score),t:Math.round(s.runT*10)/10,l:this.st.lvl,d:Date.now()},list=this.scores(),tm=this.byTime();
 list.push(e);list.sort(function(a,b){return tm?(a.t-b.t)||(b.s-a.s):(b.s-a.s)||(a.t-b.t);});list=list.slice(0,10);try{localStorage.setItem(lgScoreKey(this),JSON.stringify(list));}catch(x){}
 this._lastScore=e;return list.indexOf(e)+1;};
function lgFmtT(t){t=Math.max(0,+t||0);var m=Math.floor(t/60),s=t-m*60;return m+':'+(s<10?'0':'')+s.toFixed(1);}
Game.prototype.scoreTable=function(){var tm=this.byTime(),L=this.scores(),h='<table style="width:100%;border-collapse:collapse;font-size:13px"><tr class="mute"><td>#</td><td>Name</td><td>'+(tm?'Time':'Score')+'</td><td>'+(tm?'Score':'Time')+'</td></tr>';
 if(!L.length)h+='<tr><td colspan="4" class="mute">No scores yet - finish the game to set one.</td></tr>';
 L.forEach(function(e,i){h+='<tr'+(e===this._lastScore||(this._lastScore&&e.d===this._lastScore.d)?' style="color:#ffd166"':'')+'><td>'+(i+1)+'</td><td>'+esc(e.n)+'</td><td>'+(tm?lgFmtT(e.t):e.s)+'</td><td>'+(tm?e.s:lgFmtT(e.t))+'</td></tr>';},this);return h+'</table>';};
// ---------- subtitles / captions / timer / bars (DOM) ----------
var LG_CSS='.gsui .gs-sub{position:absolute;left:50%;bottom:92px;transform:translateX(-50%);max-width:min(760px,92%);padding:8px 16px;text-align:center;font-size:17px;line-height:1.4;background:rgba(0,0,0,.72);border-radius:10px;pointer-events:none;z-index:6}'+
'.gsui .gs-sub.lg{font-size:26px}.gsui .gs-sub b{color:#ffd166;margin-right:6px}.gsui .gs-sub i{color:#9fd8ff}'+
'.gsui .gs-bar{position:absolute;left:0;right:0;height:0;background:#000;transition:height .5s;pointer-events:none;z-index:5}.gsui .gs-bar.on{height:11vh}.gsui .gs-bar.t{top:0}.gsui .gs-bar.b{bottom:0}'+
'.gsui .gs-fade{position:absolute;inset:0;background:#000;opacity:0;pointer-events:none;z-index:5}.gsui .gs-tm{position:absolute;left:50%;top:10px;transform:translateX(-50%);padding:4px 14px;font:700 20px monospace;pointer-events:none}'+
'.gsui .toast.a{border-color:#ffd166;color:#ffe9a8;background:rgba(60,40,0,.82)}.gsui .gs-skip{position:absolute;right:16px;bottom:14px;font-size:12px;color:#ccd;pointer-events:none;z-index:6}'+
'.gsui table td{padding:2px 6px}.gsui .gs-key{min-width:88px}';
function lgEls(G){if(G._lgEl||!G.root)return G._lgEl||{};if(!document.getElementById('gs-ui-css2')){var st=document.createElement('style');st.id='gs-ui-css2';st.textContent=LG_CSS;document.head.appendChild(st);}
 var r=G.root,mk=function(c,t){var d=document.createElement('div');d.className=c;if(t)d.hidden=true;r.appendChild(d);return d;};
 G._lgEl={top:mk('gs-bar t'),bot:mk('gs-bar b'),fade:mk('gs-fade'),sub:mk('gs-sub',1),tm:mk('gs-tm',1),skip:mk('gs-skip',1)};return G._lgEl;}
Game.prototype.sub=function(speaker,text,secs,force){var e=lgEls(this);if(!e.sub)return;if(A11Y.subs==='off'&&!force){return;}
 e.sub.hidden=false;e.sub.className='gs-sub'+(A11Y.subs==='large'?' lg':'');e.sub.innerHTML=(speaker?'<b>'+esc(speaker)+'</b>':'')+esc(text||'');var s=+secs||Math.min(9,2.2+String(text||'').length*0.045);this._subT=s;};
Game.prototype.caption=function(text){if(A11Y.subs==='off')return;var e=lgEls(this);if(!e.sub)return;if(!e.sub.hidden&&this._subT>0&&!this._capOnly)return;e.sub.hidden=false;e.sub.className='gs-sub'+(A11Y.subs==='large'?' lg':'');e.sub.innerHTML='<i>'+esc(text)+'</i>';this._subT=1.6;this._capOnly=true;};
// ---------- actions ----------
function lgUnit(G){return(G.engine&&G.engine.unit)||1;}
function lgDialogue(G,a){var D=G.dialogs()[a.dialog];if(!D)return false;var fake={id:a.npc||'',name:a.npc?G.npcName(a.npc):'',dialog:a.dialog},start=D.start||Object.keys(D.nodes||{})[0];
 (D.branches||[]).some(function(b){if(G.cond(b.if)&&D.nodes&&D.nodes[b.node]){start=b.node;return true;}return false;});G.showNode(fake,D,start);return true;}
// run one block; returns 'wait' (secs in G) / 'dlg' / 'cut' when the chain must pause
function lgDo(G,a,inCut){var en=G.engine;switch(a.do){
 case'door':if(en&&en.openDoor)en.openDoor(a);break;
 case'sound':var p=sfxFor(G.world,a.sound)||SFX[a.sound];if(p)playSfx(p);G.caption(a.caption||String(a.sound||'sound').replace(/_/g,' '));break;
 case'say':G.sub(a.speaker,a.text,a.secs);break;
 case'flag':G.flags[a.flag]=a.value!==false;break;
 case'give':if(a.item)G.give(a.item,+a.n||1);break;
 case'take':if(a.item)G.take(a.item,+a.n||1);G.refresh();break;
 case'gold':G.st.gold=Math.max(0,G.st.gold+(+a.gold||0));G.toast((a.gold>0?'+':'')+(+a.gold||0)+' gold');G.refresh();break;
 case'xp':if(+a.amount>0)G.addXp(+a.amount);break;
 case'heal':G.st.hp=G.stats().maxHp;G.refresh();break;
 case'startQuest':if(a.quest)G.startQuest(a.quest);break;
 case'completeQuest':var q=G.quest(a.quest);if(q&&G.q[a.quest]&&G.q[a.quest].state==='active')G.completeQuest(q);break;
 case'cutscene':if(!inCut&&G.cutscene(a.cutscene))return'cut';break;
 case'dialogue':if(!inCut&&lgDialogue(G,a))return'dlg';break;
 case'teleport':if(en&&en.setPos){var u=lgUnit(G);en.setPos({x:(+a.x||0)*u,z:(+a.z||0)*u});}break;
 case'level':if(G.opt.switchLevel&&G.levels[+a.to])G.opt.switchLevel(+a.to);break;
 case'win':G.win();break;
 case'achievement':if(a.achievement)G.unlock(a.achievement);break;
 case'shake':if(en&&en.shake)en.shake(+a.amount||0.5);break;
 case'wait':G._lgWait=Math.max(0,+a.secs||0);return'wait';}return null;}
Game.prototype.runBlocks=function(acts){lgs(this);if(!Array.isArray(acts)||!acts.length)return;if(this._lgQ.length>=16)return;this._lgQ.push({a:acts,i:0,w:0,b:null});};
function lgRunQ(G,dt){var Q=G._lgQ;for(var qi=0;qi<Q.length;qi++){var c=Q[qi];
 if(c.b==='dlg'){if(G.dlgOpen)continue;c.b=null;}else if(c.b==='cut'){if(G.cut)continue;c.b=null;}
 if(c.w>0){c.w-=dt;if(c.w>0)continue;}
 var guard=0;while(c.i<c.a.length&&guard++<40){var a=c.a[c.i++];G._lgWait=0;var r=lgDo(G,a);if(r==='wait'){c.w=G._lgWait;break;}if(r==='dlg'||r==='cut'){c.b=r;break;}}
 if(c.i>=c.a.length&&!c.b&&!(c.w>0)){Q.splice(qi,1);qi--;}}}
// ---------- triggers ----------
function lgFire(G,t,i){var s=lgs(G),k=G.li+':'+(t.id||i);if(t.once!==false&&s.fired[k])return false;if(t.if&&!G.cond(t.if))return false;if(t.once!==false)s.fired[k]=1;G.runBlocks(t.then||[]);return true;}
function lgEach(G,type,fn){G.levelTriggers().forEach(function(t,i){if(t&&t.when&&t.when.type===type)fn(t,i);});}
function lgOnEvent(G,type,d){if(!G.engine||!G._lg||G.titleOn)return;var s=G._lg;
 if(type==='defeat'){s.kills++;if(d&&d.kind==='boss')s.bossKilled=true;}
 if(type!=='talk'&&type!=='defeat'&&type!=='collect')return;
 lgEach(G,type,function(t,i){var w=t.when,ok=true;
  if(type==='talk')ok=!w.npc||w.npc===d.npc;
  else if(type==='defeat'){if(w.id)ok=w.id===d.id;else if(w.kind&&w.kind!=='any')ok=w.kind===d.kind;}
  else if(type==='collect')ok=!w.item||w.item===d.item;
  if(ok)lgFire(G,t,i);});}
function lgTick(G,dt){var s=lgs(G);if(!G.engine||G.titleOn)return;var en=G.engine;
 if(G._lgEng!==en){G._lgEng=en;G._lgT=0;G._lgIn={};G._lgPoll=0;s.visited[G.li]=1;lgEach(G,'start',function(t,i){lgFire(G,t,i);});}
 G._lgT+=dt;if(!G.modal&&!G.won&&!G.cut)s.runT+=dt;
 if(G.world.showTimer||G.world.scoring==='time'){var e=lgEls(G);if(e.tm){e.tm.hidden=false;e.tm.textContent=lgFmtT(s.runT);}}
 var pos=en.getPos?en.getPos():null,u=lgUnit(G);
 if(pos&&!G.cut){G.levelTriggers().forEach(function(t,i){var w=t&&t.when;if(!w||(w.type!=='enter'&&w.type!=='leave'))return;var k=G.li+':'+(t.id||i),inz=Math.hypot(pos.x/u-(+w.x),pos.z/u-(+w.z))<=(+w.r||3),was=!!G._lgIn[k];G._lgIn[k]=inz;
  if(w.type==='enter'&&inz&&!was)lgFire(G,t,i);else if(w.type==='leave'&&!inz&&was)lgFire(G,t,i);});}
 lgEach(G,'timer',function(t,i){if(G._lgT>=(+t.when.secs||0))lgFire(G,t,i);});
 G._lgPoll-=dt;if(G._lgPoll<=0){G._lgPoll=0.25;
  lgEach(G,'flag',function(t,i){if(G.flags[t.when.flag])lgFire(G,t,i);});
  lgEach(G,'quest',function(t,i){var r=G.q[t.when.quest];if(r&&r.state==='done')lgFire(G,t,i);});}
 lgRunQ(G,dt);lgCut(G,dt);lgBack(G,dt);
 if(G._subT>0){G._subT-=dt;if(G._subT<=0&&G._lgEl){G._lgEl.sub.hidden=true;G._capOnly=false;}}
 G._lgAchT-=dt;if(G._lgAchT<=0){G._lgAchT=0.5;G.achievements().forEach(function(a){if(!G.ach[a.id]&&a.when&&lgAchMet(G,a.when))G.unlock(a.id);});}}
// ---------- cutscenes ----------
function lgEase(k){k=Math.max(0,Math.min(1,k));return k*k*(3-2*k);}
function lgStepDur(a){return a.do==='say'?(+a.secs||Math.min(9,2.2+String(a.text||'').length*0.045)):(+a.dur||0);}
Game.prototype.cutscene=function(id){var C=this.cutscenes()[id];if(!C||this.cut||!this.engine)return false;lgs(this);var steps=(C.steps||[]).map(function(a){return a;}).sort(function(a,b){return(+a.t||0)-(+b.t||0);}),len=0;
 steps.forEach(function(a){len=Math.max(len,(+a.t||0)+lgStepDur(a));});var e=lgEls(this);
 this.cut={id:id,t:0,len:len+0.4,steps:steps,i:0,cam:null,mv:[],fade:null,skip:C.skip!==false};if(e.top){e.top.classList.add('on');e.bot.classList.add('on');}
 if(this.cut.skip&&e.skip){e.skip.hidden=false;e.skip.textContent='Esc / Space: skip';}this.closeDlg&&this.dlgOpen&&this.closeDlg();return true;};
function lgCamFrom(G){var en=G.engine;return en&&en.camNow?en.camNow():null;}
function lgStartStep(G,c,a){var en=G.engine,u=lgUnit(G),e=lgEls(G);
 if(a.do==='camera'){if(!en||!en.camSet)return;var from=c.cam?c.cam.cur:lgCamFrom(G),g=en.groundAt||function(){return 0;},px=en.getPos?en.getPos():{x:0,z:0},lx=a.lx!=null?+a.lx:px.x/u,lz=a.lz!=null?+a.lz:px.z/u;
  var to={e:[+a.x,g(+a.x,+a.z)+(a.h!=null?+a.h:3),+a.z],c:[lx,g(lx,lz)+(a.lh!=null?+a.lh:1.3),lz]};c.cam={from:from||to,to:to,t0:c.t,dur:Math.max(0.01,+a.dur||0.01),cur:from||to};}
 else if(a.do==='move'){var p=en&&en.entPos?en.entPos(a.id):null;if(p)c.mv.push({id:a.id,fx:p.x/u,fz:p.z/u,tx:+a.x||0,tz:+a.z||0,t0:c.t,dur:Math.max(0.05,+a.dur||1)});}
 else if(a.do==='fade'){c.fade={from:+(e.fade&&e.fade.style.opacity)||0,to:+a.a||0,t0:c.t,dur:Math.max(0.01,+a.dur||0.5)};}
 else lgDo(G,a,true);}
function lgCut(G,dt){var c=G.cut;if(!c)return;c.t+=dt;var en=G.engine,u=lgUnit(G),e=lgEls(G);
 while(c.i<c.steps.length&&(+c.steps[c.i].t||0)<=c.t){lgStartStep(G,c,c.steps[c.i]);c.i++;}
 if(c.cam&&en&&en.camSet){var k=lgEase((c.t-c.cam.t0)/c.cam.dur),f=c.cam.from,t=c.cam.to;c.cam.cur={e:[0,1,2].map(function(j){return f.e[j]+(t.e[j]-f.e[j])*k;}),c:[0,1,2].map(function(j){return f.c[j]+(t.c[j]-f.c[j])*k;})};en.camSet(c.cam.cur.e,c.cam.cur.c);}
 c.mv.forEach(function(m){var k=lgEase((c.t-m.t0)/m.dur);if(en&&en.entSet)en.entSet(m.id,(m.fx+(m.tx-m.fx)*k)*u,(m.fz+(m.tz-m.fz)*k)*u,Math.atan2(m.tx-m.fx,m.tz-m.fz),k<1);});
 if(c.fade&&e.fade){var f2=c.fade,kk=lgEase((c.t-f2.t0)/f2.dur);e.fade.style.opacity=String(f2.from+(f2.to-f2.from)*kk);}
 if(c.t>=c.len)lgCutEnd(G,false);}
function lgCutEnd(G,skipped){var c=G.cut;if(!c)return;var en=G.engine,u=lgUnit(G),e=lgEls(G);
 if(skipped){while(c.i<c.steps.length){var a=c.steps[c.i++];if(a.do==='camera'||a.do==='fade'||a.do==='say'||a.do==='sound')continue;if(a.do==='move'){if(en&&en.entSet)en.entSet(a.id,(+a.x||0)*u,(+a.z||0)*u,0,false);continue;}lgDo(G,a,true);}
  c.mv.forEach(function(m){if(en&&en.entSet)en.entSet(m.id,m.tx*u,m.tz*u,Math.atan2(m.tx-m.fx,m.tz-m.fz),false);});}
 G.flags['cut_'+c.id]=true;if(c.cam&&!skipped&&c.cam.cur)G._camBack={a:c.cam.cur,t:0};else if(en&&en.camSet)en.camSet(null);
 if(e.top){e.top.classList.remove('on');e.bot.classList.remove('on');}if(e.fade)e.fade.style.opacity='0';if(e.skip)e.skip.hidden=true;if(e.sub&&skipped){e.sub.hidden=true;}
 G.cut=null;G.busyT=0.3;}
function lgBack(G,dt){var b=G._camBack;if(!b)return;var en=G.engine;if(!en||!en.camSet||!en.camNow){G._camBack=null;return;}b.t+=dt;var k=lgEase(b.t/0.7),live=en.camNow();
 if(!live||k>=1){G._camBack=null;en.camSet(null);return;}en.camSet([0,1,2].map(function(j){return b.a.e[j]+(live.e[j]-b.a.e[j])*k;}),[0,1,2].map(function(j){return b.a.c[j]+(live.c[j]-b.a.c[j])*k;}));}
// ---------- shops ----------
function lgValue(G,id){var it=G.item(id);if(it.kind==='quest'||it.kind==='key')return it.value!=null?+it.value:0;if(it.value!=null)return Math.max(0,Math.round(+it.value));if(it.gold)return Math.round(+it.gold);
 if(it.kind==='consumable')return Math.round(4+(+it.heal||1)*3);return Math.round(12+(+it.atk||0)*18+(+it.def||0)*18+(+it.hp||0)*8+(it.speed?20:0));}
Game.prototype.shopPrice=function(sh,e){return e.price!=null?Math.max(0,Math.round(+e.price)):lgValue(this,e.item);};
Game.prototype.sellPrice=function(sh,id){var v=lgValue(this,id);if(v<=0)return 0;return Math.max(1,Math.floor(v*(sh.buyRate!=null?+sh.buyRate:0.5)));};
Game.prototype.shopLeft=function(npc,e){var s=lgs(this),k=npc.id||npc.name||'shop',sold=((s.shop[k]||{})[e.item])||0;return e.n!=null?Math.max(0,(+e.n)-sold):Infinity;};
Game.prototype.buy=function(npc,idx){var sh=npc.shop||{},e=(sh.stock||[])[idx];if(!e)return false;var pr=this.shopPrice(sh,e);if(this.shopLeft(npc,e)<=0){this.toast('Sold out');return false;}
 if(this.st.gold<pr){this.toast('Not enough gold ('+pr+' needed)');playSfx(SFX.hit);return false;}this.st.gold-=pr;var s=lgs(this),k=npc.id||npc.name||'shop';s.shop[k]=s.shop[k]||{};s.shop[k][e.item]=(s.shop[k][e.item]||0)+1;
 this.give(e.item,1);playSfx(SFX.coin);this.refresh();return true;};
Game.prototype.sell=function(npc,id){var sh=npc.shop||{};if(sh.sell===false||!this.has(id))return false;var pr=this.sellPrice(sh,id);if(pr<=0){this.toast('They are not interested in that');return false;}
 this.take(id,1);this.st.gold+=pr;this.toast('Sold '+this.item(id).name+' for '+pr+' gold');playSfx(SFX.coin);this.refresh();return true;};
Game.prototype.openShop=function(npc){if(!npc||!npc.shop)return;var self=this,sh=npc.shop,its=this.items(),h='<h2>'+esc(npc.name||'Shop')+'</h2><div class="mute">You have <b style="color:#ffd166">'+this.st.gold+' gold</b></div><h3 style="margin:10px 0 4px">Buy</h3>';
 (sh.stock||[]).forEach(function(e,i){var it=self.item(e.item),pr=self.shopPrice(sh,e),left=self.shopLeft(npc,e);h+='<div class="it"><span class="sw" style="background:'+esc(Array.isArray(it.col)?'#888':it.col||'#888')+'"></span><div style="flex:1"><b>'+esc(it.name)+'</b><div class="mute">'+esc(it.desc||it.kind)+(left!==Infinity?' &middot; '+left+' left':'')+'</div></div><button class="btn" data-buy="'+i+'"'+(left<=0?' disabled':'')+'>'+(left<=0?'Sold out':pr+' gold')+'</button></div>';});
 if(!(sh.stock||[]).length)h+='<p class="mute">Nothing for sale.</p>';
 if(sh.sell!==false){h+='<h3 style="margin:10px 0 4px">Sell</h3>';var any=false;Object.keys(this.st.inv).forEach(function(id){var pr=self.sellPrice(sh,id);if(pr<=0)return;any=true;var it=its[id]||self.item(id);
  h+='<div class="it"><span class="sw" style="background:'+esc(Array.isArray(it.col)?'#888':it.col||'#888')+'"></span><div style="flex:1"><b>'+esc(it.name)+'</b> x'+self.st.inv[id]+'</div><button class="btn" data-sell="'+esc(id)+'">+'+pr+' gold</button></div>';});
  if(!any)h+='<p class="mute">You have nothing they want.</p>';}
 var m=this.openModal('shop',h+'<div class="row"><button class="btn" data-x>Leave</button></div>');m.querySelector('[data-x]').onclick=function(){self.closeModal();};
 [].forEach.call(m.querySelectorAll('[data-buy]'),function(b){b.onclick=function(){self.buy(npc,+b.getAttribute('data-buy'));self.openShop(npc);};});
 [].forEach.call(m.querySelectorAll('[data-sell]'),function(b){b.onclick=function(){self.sell(npc,b.getAttribute('data-sell'));self.openShop(npc);};});};
// ---------- menus: accessibility + controls, achievements, high scores ----------
Game.prototype.openAccess=function(back){var self=this,h='<h2>Accessibility &amp; controls</h2><h3 style="margin:6px 0 4px">Keys</h3><div class="mute">Click a key, then press the new one. Esc cancels.</div>';
 A11Y_ACTIONS.forEach(function(a){h+='<div class="it"><div style="flex:1">'+esc(a[1])+'</div><button class="btn gs-key" data-bind="'+a[0]+'">'+esc(keyLabel(a11yKeyOf(a[0])))+'</button></div>';});
 h+='<div class="row"><button class="btn" data-reset>Reset keys</button></div><h3 style="margin:10px 0 4px">Colour-blind palette</h3><div class="row">'+CB_MODES.map(function(m){return'<button class="btn" data-cb="'+m[0]+'" style="'+(A11Y.cb===m[0]?'border-color:#5fd3b5':'')+'">'+m[1]+'</button>';}).join('')+'</div>'+
  '<h3 style="margin:10px 0 4px">Subtitles</h3><div class="row">'+[['on','On'],['large','Large'],['off','Off']].map(function(m){return'<button class="btn" data-sub="'+m[0]+'" style="'+(A11Y.subs===m[0]?'border-color:#5fd3b5':'')+'">'+m[1]+'</button>';}).join('')+'</div><div class="mute">Subtitles show cutscene and event lines and name the sounds ([door opens]). Conversations always show their text.</div>'+
  '<h3 style="margin:10px 0 4px">Game speed (slow mode)</h3><div class="row">'+[[1,'Normal'],[0.75,'75%'],[0.5,'50%']].map(function(m){return'<button class="btn" data-slow="'+m[0]+'" style="'+(A11Y.slow===m[0]?'border-color:#5fd3b5':'')+'">'+m[1]+'</button>';}).join('')+'</div>';
 var m=this.openModal('access',h+'<div class="row"><button class="btn" data-x>Back</button></div>');m.querySelector('[data-x]').onclick=function(){if(back)back();else self.closeModal();};
 m.querySelector('[data-reset]').onclick=function(){A11Y.keys={};a11yFwd();a11ySave();self.openAccess(back);};
 [].forEach.call(m.querySelectorAll('[data-cb]'),function(b){b.onclick=function(){A11Y.cb=b.getAttribute('data-cb');a11ySave();a11yApply();self.openAccess(back);};});
 [].forEach.call(m.querySelectorAll('[data-sub]'),function(b){b.onclick=function(){A11Y.subs=b.getAttribute('data-sub');a11ySave();self.openAccess(back);};});
 [].forEach.call(m.querySelectorAll('[data-slow]'),function(b){b.onclick=function(){A11Y.slow=+b.getAttribute('data-slow');a11ySave();self.openAccess(back);};});
 [].forEach.call(m.querySelectorAll('[data-bind]'),function(b){b.onclick=function(){b.textContent='press a key...';var done=function(e){e.preventDefault();e.stopPropagation();window.removeEventListener('keydown',done,true);if(e.code!=='Escape'&&!a11yBind(b.getAttribute('data-bind'),e.code))self.toast('That key cannot be used');self.openAccess(back);};window.addEventListener('keydown',done,true);};});};
Game.prototype.openAch=function(back){var self=this,h='<h2>Achievements</h2>',list=this.achievements(),n=list.filter(function(a){return self.ach[a.id];}).length;lgs(this);h+='<div class="mute">'+n+' of '+list.length+' unlocked</div>';
 list.forEach(function(a){var on=!!self.ach[a.id];h+='<div class="it" style="opacity:'+(on?1:0.55)+'"><span class="sw" style="background:'+(on?'#ffd166':'#556')+'"></span><div style="flex:1"><b>'+esc(a.title)+'</b><div class="mute">'+esc(a.desc||'')+'</div></div><span class="mute">'+(on?new Date(self.ach[a.id]).toLocaleDateString():'locked')+'</span></div>';});
 var m=this.openModal('ach',h+'<div class="row"><button class="btn" data-x>Back</button></div>');m.querySelector('[data-x]').onclick=function(){if(back)back();else self.closeModal();};};
Game.prototype.openScores=function(back){var self=this,m=this.openModal('scores','<h2>High scores</h2><div class="mute">'+(this.byTime()?'Fastest finish first':'Highest score first')+' - saved on this computer</div>'+this.scoreTable()+'<div class="row"><button class="btn" data-x>Back</button><button class="btn" data-clr>Clear</button></div>');
 m.querySelector('[data-x]').onclick=function(){if(back)back();else self.closeModal();};m.querySelector('[data-clr]').onclick=function(){try{localStorage.removeItem(lgScoreKey(self));}catch(e){}self._lastScore=null;self.openScores(back);};};
// ---------- hook the existing Game ----------
(function(){var P=Game.prototype,_upd=P.update,_ev=P.event,_busy=P.busy,_keys=P.keys,_snap=P.snapshot,_load=P.load,_menu=P.openMenu,_title=P.titleScreen,_win=P.win,_cred=P.credits,_talk=P.talk,_show=P.showNode,_act=P.act,_att=P.attach,_dest=P.destroy;
 P.update=function(dt){_upd.call(this,dt);try{lgTick(this,dt);}catch(e){if(window.console)console.error('[logic]',e);}};
 P.event=function(type,d){_ev.apply(this,arguments);if(this._lg)lgOnEvent(this,type,d||{});};
 P.busy=function(){return _busy.call(this)||!!this.cut;};
 P.keys=function(c){if(this.cut){if(this.cut.skip&&(c==='Escape'||c==='Space'||c==='Enter'))lgCutEnd(this,true);return true;}return _keys.call(this,c);};
 P.snapshot=function(){var j=JSON.parse(_snap.call(this));j.lg=lgs(this);return JSON.stringify(j);};
 P.load=function(slot){var lgj=null;try{var j=JSON.parse(localStorage.getItem(this.key(slot))||'null');lgj=j&&j.lg&&typeof j.lg==='object'?j.lg:null;}catch(e){}
  var ok=_load.call(this,slot);if(ok){lgs(this);var s=this._lg;s.fired=(lgj&&lgj.fired)||{};s.kills=+(lgj&&lgj.kills)||0;s.shop=(lgj&&lgj.shop)||{};s.visited=(lgj&&lgj.visited)||{};s.runT=+(lgj&&lgj.runT)||0;s.bossKilled=!!(lgj&&lgj.bossKilled);s.scored=!!this.won&&!!(lgj&&lgj.scored);this._lgQ=[];this._lgIn={};this.cut=null;}return ok;};
 P.attach=function(parent){_att.call(this,parent);LG_CUR=this;lgs(this);lgEls(this);a11yApply();};
 P.destroy=function(){if(LG_CUR===this){LG_CUR=null;a11yApply();}_dest.call(this);};
 P.talk=function(npc){if(npc&&npc.shop&&!npc.dialog){this.event('talk',{npc:npc.id});playSfx(SFX.talk);this.openShop(npc);return;}_talk.call(this,npc);};
 P.showNode=function(npc,D,id){this._curNpc=npc;var node=(D.nodes||{})[id];
  if(node&&npc&&npc.shop&&!(node.choices&&node.choices.length)&&!node.next){var n2={};for(var k in node)n2[k]=node[k];n2.choices=[{text:'Show me your wares.',shop:true,end:true},{text:'Farewell.',end:true}];var nodes={};for(var k2 in D.nodes)nodes[k2]=D.nodes[k2];nodes[id]=n2;var D2={};for(var k3 in D)D2[k3]=D[k3];D2.nodes=nodes;D=D2;}
  return _show.call(this,npc,D,id);};
 P.act=function(c){_act.apply(this,arguments);if(!c)return;if(c.shop&&this._curNpc&&this._curNpc.shop)this.openShop(this._curNpc);if(c.door&&this.engine&&this.engine.openDoor)this.engine.openDoor({id:c.door});if(c.achievement)this.unlock(c.achievement);if(c.cutscene)this.cutscene(c.cutscene);};
 P.win=function(){var was=this.won;_win.call(this);if(!was&&this.won){var s=lgs(this);if(!s.scored){s.scored=true;this.addScore(localStorage.getItem('gs_player')||'Player');}this.unlock('finish');}};
 P.credits=function(){_cred.call(this);var m=this.modalEl,self=this;if(!m)return;var d=document.createElement('div');d.innerHTML='<h3 style="margin:10px 0 4px;text-align:center">High scores</h3><div class="row" style="justify-content:center"><span class="mute" style="align-self:center">Your name</span><input data-nm maxlength="16" value="'+esc((this._lastScore&&this._lastScore.n)||'Player')+'" style="width:130px"></div>'+this.scoreTable();
  var row=m.querySelector('.row');if(row&&m.insertBefore)m.insertBefore(d,row);else m.appendChild(d);var inp=d.querySelector('[data-nm]');
  if(inp)inp.onchange=function(){var nm=String(inp.value||'Player').slice(0,16);try{localStorage.setItem('gs_player',nm);}catch(e){}if(self._lastScore){var L=self.scores();L.forEach(function(e){if(e.d===self._lastScore.d)e.n=nm;});try{localStorage.setItem(lgScoreKey(self),JSON.stringify(L));}catch(e2){}self._lastScore.n=nm;}self.credits();};};
 P.openMenu=function(){_menu.call(this);var m=this.modalEl,self=this;if(!m)return;var d=document.createElement('div');d.innerHTML='<h3 style="margin:10px 0 4px">More</h3><div class="row"><button class="btn" data-a11y>Accessibility &amp; controls</button><button class="btn" data-ach>Achievements</button><button class="btn" data-hs>High scores</button></div>';
  var back=function(){self.openMenu();};m.appendChild(d);d.querySelector('[data-a11y]').onclick=function(){self.openAccess(back);};d.querySelector('[data-ach]').onclick=function(){self.openAch(back);};d.querySelector('[data-hs]').onclick=function(){self.openScores(back);};};
 P.titleScreen=function(onStart){_title.call(this,onStart);var m=this.titleEl,self=this;if(!m)return;var row=m.querySelector('.row');if(!row)return;var b=document.createElement('button');b.className='btn';b.textContent='Accessibility & controls';b.style.cssText='font-size:15px;padding:12px 18px';
  b.onclick=function(){self.openAccess(function(){self.closeModal();});};row.appendChild(b);var h=document.createElement('button');h.className='btn';h.textContent='High scores';h.style.cssText='font-size:15px;padding:12px 18px';h.onclick=function(){self.openScores(function(){self.closeModal();});};row.appendChild(h);};})();
var LGX={VERBS:LG_VERBS,CUT:LG_CUTV,ACTIONS:A11Y_ACTIONS,A11Y:A11Y,cbMatrix:cbMatrix,CB_MODES:CB_MODES,KM:KM,bind:a11yBind,fwd:function(){return A11Y_FWD;},value:lgValue,builtin:ACH_BUILTIN,setCur:function(g){LG_CUR=g;},fmt:lgFmtT,reload:a11yLoad,apply:a11yApply,tsc:TSC};
