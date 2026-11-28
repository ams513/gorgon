// ---------- Studio+ (28.7, studio only, never shipped in a cartridge): Tune Maker, remix (open a shared game), guided
// tutorials with live checks, copy / paste / duplicate, snap to grid, itch.io zip helper, and the Play-mode Watch panel.
// Spliced into the studio page script after the Logic tab, so it shares its helpers ($, E, opts, L, W, levels, changed...).
// ----- Tune Maker -----
var TN_PREV=null,TN_TICK=null;
function tnDefault(){return{bpm:110,wave:'square',scale:'penta',len:16,vol:0.6,mel:[],drum:[]};}
function tnGet(){if(!W.tune)W.tune=tnDefault();return W.tune;}
function tnLabel(t,row){var sc=GS.Plus.scales[(t&&t.scale)||'penta']||GS.Plus.scales.penta,nm=['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'],o=Math.floor(row/sc.length);return nm[sc[row%sc.length]]+(4+o+((t&&+t.oct)||0));}
function tnBeep(t,row){GS.playSfx({wave:t.wave==='saw'?'saw':t.wave==='square'?'square':'sine',freq:GS.Plus.tuneFreq(t.scale,row,t.oct),slide:0,dur:0.18,decay:2,vol:0.22,noise:0});}
function tnStop(){if(TN_PREV){TN_PREV.stop();TN_PREV=null;}if(TN_TICK){clearInterval(TN_TICK);TN_TICK=null;}var b=$('tnPlay');if(b)b.textContent='▶ Listen';document.querySelectorAll('.tng td.now').forEach(function(x){x.classList.remove('now');});}
function tnStart(){tnStop();if(!W||!GS.Plus.tuneHas(W.tune)){status('Place some notes first.');return;}TN_PREV=new GS.Plus.Tune(JSON.parse(JSON.stringify(W.tune)));if(!TN_PREV.start()){TN_PREV=null;status('This browser cannot play sound right now.',true);return;}
 var b=$('tnPlay');if(b)b.textContent='■ Stop';var n=TN_PREV.len();TN_TICK=setInterval(function(){if(!TN_PREV)return;var c=(TN_PREV.step-2+n)%n;document.querySelectorAll('.tng td.now').forEach(function(x){x.classList.remove('now');});document.querySelectorAll('.tng td[data-c="'+c+'"]').forEach(function(x){x.classList.add('now');});},60);}
function tnChanged(re){var t=W.tune;if(t&&!GS.Plus.tuneHas(t)&&!(t.bpm!==110||t.wave!=='square'||t.scale!=='penta'))delete W.tune;changed();if(TN_PREV)tnStart();if(re)renderTune();}
var TN_EXAMPLE={bpm:112,wave:'square',scale:'penta',len:16,vol:0.6,mel:[[0,5],[2,7],[4,8],[6,7],[8,5],[10,4],[11,3],[12,5],[14,2]],drum:[[0,0],[4,1],[8,0],[10,0],[12,1],[2,2],[6,2],[10,2],[14,2]]};
function renderTune(){var s=$('tTune');if(!s)return;if(!W){s.innerHTML='';return;}var t=W.tune||tnDefault(),n=+t.len===32?32:16,has={},r,c;
 (t.mel||[]).forEach(function(x){has['m'+x[0]+'_'+x[1]]=1;});(t.drum||[]).forEach(function(x){has['d'+x[0]+'_'+x[1]]=1;});
 var h='<style>#tTune .three{grid-template-columns:repeat(auto-fit,minmax(92px,1fr))}#tTune .three>label{min-width:0}#tTune .three select,#tTune .three input{width:100%;min-width:0}.tnw{overflow-x:auto;max-width:100%;margin:8px 0}.tng{border-collapse:separate;border-spacing:2px}.tng th{font:600 10px monospace;color:var(--mute);padding-right:4px;text-align:right;white-space:nowrap}.tng td{width:15px;height:15px;min-width:15px;background:var(--panel2);border-radius:3px;cursor:pointer;padding:0}.tng td.b{background:#26372f}.tng td.on{background:var(--acc)}.tng tr.dr td.on{background:var(--acc2)}.tng td.now{outline:2px solid #fff}</style>'+
  '<p class="mute"><b>Tune Maker.</b> Click squares to place notes, then press Listen. The tune loops while your game is played: in Play mode and <b>inside the exported HTML5 game</b>. It is made on this computer and it is yours.</p>'+
  '<div class="row"><button id="tnPlay">▶ Listen</button><button id="tnEx">Example tune</button><button id="tnClr">Clear</button></div>'+
  '<div class="three"><label>Speed (beats per minute)<input type="number" id="tnBpm" min="60" max="200" value="'+E(t.bpm||110)+'"></label><label>Sound<select id="tnWave">'+opts([['square','Retro (square)'],['triangle','Soft (triangle)'],['sine','Flute (sine)'],['saw','Bright (saw)']],t.wave||'square')+'</select></label>'+
  '<label>Notes<select id="tnScale">'+opts([['penta','Cheerful (pentatonic)'],['major','Major'],['minor','Minor (mysterious)']],t.scale||'penta')+'</select></label></div>'+
  '<div class="three"><label>Length<select id="tnLen">'+opts([['16','16 steps'],['32','32 steps']],String(n))+'</select></label><label>Octave<select id="tnOct">'+opts([['-1','Low'],['0','Middle'],['1','High']],String(t.oct||0))+'</select></label><label>Volume<input type="range" id="tnVol" min="0" max="1" step="0.05" value="'+E(t.vol==null?0.6:t.vol)+'"></label></div>'+
  '<div class="tnw"><table class="tng" aria-label="Tune grid: rows are notes, columns are steps"><tbody>';
 for(r=GS.Plus.rows-1;r>=0;r--){h+='<tr><th>'+tnLabel(t,r)+'</th>';for(c=0;c<n;c++)h+='<td class="'+(has['m'+c+'_'+r]?'on ':'')+(c%4===0?'b':'')+'" data-tn="m:'+c+':'+r+'" data-c="'+c+'"></td>';h+='</tr>';}
 ['Kick','Snare','Hat'].forEach(function(nm,rr){h+='<tr class="dr"><th>'+nm+'</th>';for(var cc=0;cc<n;cc++)h+='<td class="'+(has['d'+cc+'_'+rr]?'on ':'')+(cc%4===0?'b':'')+'" data-tn="d:'+cc+':'+rr+'" data-c="'+cc+'"></td>';h+='</tr>';});
 var nn=(t.mel||[]).length+(t.drum||[]).length;
 h+='</tbody></table></div><p class="mute" id="tnInfo">'+nn+' note'+(nn===1?'':'s')+'. '+(W.music?'While a tune is set it replaces the soundtrack file in Play mode. ':'')+'Players can switch sound off in the game menu (Esc).</p>';
 s.innerHTML=h;
 s.querySelectorAll('[data-tn]').forEach(function(td){td.onclick=function(){var a=td.dataset.tn.split(':'),T=tnGet(),key=a[0]==='m'?'mel':'drum',cc=+a[1],rr=+a[2],i=-1;T[key]=T[key]||[];
  T[key].forEach(function(x,j){if(x[0]===cc&&x[1]===rr)i=j;});if(i>=0)T[key].splice(i,1);else{T[key].push([cc,rr]);if(a[0]==='m')tnBeep(T,rr);}td.classList.toggle('on',i<0);
  var m=(T.mel||[]).length+(T.drum||[]).length;$('tnInfo').firstChild.textContent=m+' note'+(m===1?'':'s')+'. ';tnChanged(false);};});
 $('tnPlay').onclick=function(){if(TN_PREV)tnStop();else tnStart();};
 $('tnEx').onclick=function(){W.tune=JSON.parse(JSON.stringify(TN_EXAMPLE));tnChanged(true);status('Example tune placed. Press Listen, then change any note.');};
 $('tnClr').onclick=function(){tnStop();delete W.tune;changed();renderTune();status('Tune cleared.');};
 $('tnBpm').onchange=function(){tnGet().bpm=Math.max(60,Math.min(200,Math.round(+this.value||110)));tnChanged(true);};
 $('tnWave').onchange=function(){tnGet().wave=this.value;tnChanged(false);};
 $('tnScale').onchange=function(){tnGet().scale=this.value;tnChanged(true);};
 $('tnOct').onchange=function(){var v=+this.value,T=tnGet();if(v)T.oct=v;else delete T.oct;tnChanged(true);};
 $('tnVol').onchange=function(){tnGet().vol=Math.round(Math.max(0,Math.min(1,+this.value))*100)/100;tnChanged(false);};
 $('tnLen').onchange=function(){var T=tnGet(),v=+this.value===32?32:16;T.len=v;['mel','drum'].forEach(function(k){T[k]=(T[k]||[]).filter(function(x){return x[0]<v;});});tnChanged(true);};}
// ----- remix: open a game someone shared (a cartridge .html or a world.json); it becomes a NEW game -----
function rmxPayload(txt){var i=txt.indexOf('var WORLD='),j=i>=0?txt.indexOf(';\nvar QS=',i):-1;return j>0?txt.slice(i+10,j):txt;}
function rmxOpen(file){var out=$('rmxOut');if(!file)return;if(file.size>48*1024*1024){out.textContent='That file is too big to be a game from this studio.';return;}out.textContent='Reading '+file.name+'...';
 var rd=new FileReader();rd.onload=function(){var txt=String(rd.result||'');(DIRTY?save(true):Promise.resolve()).then(function(){return api('/api/remix',{text:rmxPayload(txt)});})
  .then(function(r){out.innerHTML='Opened as <b>'+E(r.name)+'</b> ('+r.levels+' level'+(r.levels===1?'':'s')+').'+(r.dropped?' '+r.dropped+' imported model(s) or sky that only lived on the other computer were left out.':'');
   return refreshList(r.slug).then(function(){loadWorld(r.slug);status('Remix ready: '+r.name+'. The original file was not changed.');});})
  .catch(function(e){out.textContent=e.message;});};
 rd.onerror=function(){out.textContent='Could not read that file.';};rd.readAsText(file);}
// ----- guided tutorials with live checks (Learn tab + a coach card on the stage) -----
var TUTX={played:0,exported:0,talked:0},TUT_CUR=null,TUT_DONE={},TUT_TIMER=null;
try{TUT_DONE=JSON.parse(localStorage.getItem('gs_tut')||'{}')||{};}catch(e){TUT_DONE={};}
function tutCount(t,f){var lv=L();return((lv&&lv.objects)||[]).filter(function(o){return o.t===t&&(!f||f(o));}).length;}
function tutTrig(f){var n=0;levels().forEach(function(l){(l.triggers||[]).forEach(function(t){if(f(t))n++;});});return n;}
function tutGo(tab,tool){return function(){if(MODE!=='edit')setMode('edit');showTab(tab);if(tool)pickTool(tool);};}
var TUTS=[
 {id:'first',title:'Your first world',mins:5,steps:[
  ['Open a 3D world: press <b>New 3D world</b> at the top (or keep the 3D world you have open).',function(){return!!W&&!!L()&&L().mode==='3d';},function(){}],
  ['Place <b>3 trees</b>: Tools tab, pick <b>Tree</b>, click the ground three times.',function(){return tutCount('tree')>=3;},tutGo('tBuild','tree')],
  ['Place a <b>Crystal</b> for the player to find.',function(){return tutCount('crystal')>=1;},tutGo('tBuild','crystal')],
  ['Type a name in the <b>Name</b> box at the top and press <b>Save</b>.',function(){return!!W&&!/^(My Game|Untitled|My [23]D World)( \d+)?$/i.test(W.name||'')&&!DIRTY;},function(){$('wname').focus();}],
  ['Press <b>▶ Play</b> and walk with WASD to the crystal.',function(){return TUTX.played>0;},function(){setMode('play');}]]},
 {id:'talk',title:'Someone to talk to',mins:8,steps:[
  ['Place a <b>Person</b> (Tools tab, Story group).',function(){return tutCount('npc')>=1;},tutGo('tBuild','npc')],
  ['Use <b>Select / move</b>, click the person and give them a <b>name</b> of your own.',function(){return tutCount('npc',function(o){return o.name&&!/^Villager \d+$/.test(o.name);})>=1;},tutGo('tBuild','select')],
  ['Open the <b>Dialogue</b> tab and give the person a conversation.',function(){return tutCount('npc',function(o){return!!o.dialog;})>=1;},tutGo('tDlg')],
  ['Press <b>▶ Play</b>, walk up to them and press <b>E</b> to talk.',function(){return TUTX.talked>0;},function(){setMode('play');}]]},
 {id:'quest',title:'A quest with a reward',mins:10,steps:[
  ['Open the <b>Quests</b> tab and add a quest.',function(){return quests().length>=1;},tutGo('tQuest')],
  ['Give the quest at least one <b>step</b> (for example: collect 2 crystals).',function(){return quests().some(function(x){return(x.q.steps||[]).length>0;});},tutGo('tQuest')],
  ['Set a <b>reward</b>: some XP or gold.',function(){return quests().some(function(x){var r=x.q.reward||{};return(+r.xp||0)>0||(+r.gold||0)>0||(r.items||[]).length>0;});},tutGo('tQuest')],
  ['Press <b>▶ Play</b> and finish your quest.',function(){return TUTX.played>0;},function(){setMode('play');}]]},
 {id:'door',title:'A door that opens by magic',mins:10,steps:[
  ['Place a <b>Locked door</b>.',function(){return tutCount('door')>=1;},tutGo('tBuild','door')],
  ['Open the <b>Logic</b> tab and press <b>+ new trigger</b>.',function(){return tutTrig(function(){return true;})>=1;},tutGo('tLogic')],
  ['Add the THEN block <b>Open / close a door</b> and choose your door.',function(){return tutTrig(function(t){return(t.then||[]).some(function(a){return a.do==='door'&&a.id;});})>=1;},tutGo('tLogic')],
  ['Press <b>▶ Play</b> and walk into the trigger area (or meet its WHEN).',function(){return TUTX.played>0;},function(){setMode('play');}]]},
 {id:'vars',title:'Count coins with a variable',mins:12,steps:[
  ['Logic tab, <b>Game rules</b>: press <b>+ variable</b> (it makes <b>coins</b>, shown on screen).',function(){return(W.vars||[]).length>=1;},tutGo('tLogic')],
  ['Make a trigger with the THEN block <b>Change a variable</b>: add 1 (for example WHEN the player picks up an item, or WHEN the player presses a key).',function(){return tutTrig(function(t){return(t.then||[]).some(function(a){return a.do==='var'&&a.var;});})>=1;},tutGo('tLogic')],
  ['Make a second trigger: WHEN <b>a variable reaches a value</b> (coins is at least 5) THEN <b>Win the game</b>.',function(){return tutTrig(function(t){return t.when&&t.when.type==='var'&&(t.then||[]).some(function(a){return a.do==='win';});})>=1;},tutGo('tLogic')],
  ['Press <b>▶ Play</b> and watch the counter in the corner.',function(){return TUTX.played>0;},function(){setMode('play');}]]},
 {id:'share',title:'Music and sharing',mins:8,steps:[
  ['Open the <b>Tune</b> tab and place at least <b>4 notes</b> (or press Example tune).',function(){var t=W.tune||{};return((t.mel||[]).length+(t.drum||[]).length)>=4;},tutGo('tTune')],
  ['Press <b>Save</b>.',function(){return!DIRTY;},function(){save();}],
  ['Open the <b>Export</b> tab and press <b>HTML5 game</b> or <b>itch.io zip</b>.',function(){return TUTX.exported>0;},tutGo('tExport')],
  ['Done! Send the .html file to a friend: it plays offline in any modern browser.',function(){return TUTX.exported>0;},function(){}]]}];
function tutSave(){try{localStorage.setItem('gs_tut',JSON.stringify(TUT_DONE));}catch(e){}}
function tutStart(id){var t=TUTS.filter(function(x){return x.id===id;})[0];if(!t)return;TUT_CUR={t:t,ok:{}};TUTX.played=0;TUTX.exported=0;TUTX.talked=0;tutTick(true);if(!TUT_TIMER)TUT_TIMER=setInterval(function(){tutTick(false);},700);}
function tutStop(){TUT_CUR=null;if(TUT_TIMER){clearInterval(TUT_TIMER);TUT_TIMER=null;}var c=$('tutCard');if(c)c.remove();}
function tutTick(force){if(!TUT_CUR)return;var C=TUT_CUR,t=C.t,ch=false,first=-1;
 if(MODE==='play'){TUTX.played++;var g=ENG&&ENG.game&&ENG.game();if(g&&g.dlgOpen)TUTX.talked++;}
 t.steps.forEach(function(s,i){var ok=false;try{ok=!!s[1]();}catch(e){}if(ok&&!C.ok[i]&&(first<0)){C.ok[i]=1;ch=true;}if(!C.ok[i]&&first<0)first=i;});
 if(first<0&&!C.fin){C.fin=1;TUT_DONE[t.id]=Date.now();tutSave();GS.playSfx(GS.SFX.levelup);status('Tutorial finished: '+t.title+'!');renderLearn();ch=true;}
 if(!ch&&!force&&$('tutCard'))return;var c=$('tutCard');if(!c){c=document.createElement('div');c.id='tutCard';$('stage').appendChild(c);}
 c.innerHTML='<div class="row" style="margin:0 0 6px"><b style="flex:1;color:var(--acc2)">'+E(t.title)+'</b><button id="tutX" title="Stop this tutorial">Stop</button></div>'+t.steps.map(function(s,i){var on=!!C.ok[i],cur=i===first;return'<div class="tst'+(on?' ok':'')+(cur?' cur':'')+'"><span>'+(on?'✓':(i+1)+'.')+'</span><div>'+s[0]+(cur?' <button data-tshow="'+i+'">Show me</button>':'')+'</div></div>';}).join('')+(first<0?'<p><b>All done - nice work!</b></p>':'');
 $('tutX').onclick=tutStop;c.querySelectorAll('[data-tshow]').forEach(function(b){b.onclick=function(){var s=t.steps[+b.dataset.tshow];if(s&&s[2])s[2]();};});}
function renderLearn(){var box=$('tutList');if(!box)return;box.innerHTML=TUTS.map(function(t){var d=TUT_DONE[t.id];return'<div class="row" style="margin:4px 0"><button data-tut="'+t.id+'" style="flex:1;text-align:left">'+(d?'✓ ':'')+E(t.title)+' <span class="mute">(about '+t.mins+' min, '+t.steps.length+' steps)</span></button></div>';}).join('');
 box.querySelectorAll('[data-tut]').forEach(function(b){b.onclick=function(){click();tutStart(b.dataset.tut);};});}
// ----- copy / paste / duplicate -----
var CLIP=null;
function cpName(o){return o.t==='npc'?(o.name||'person'):o.t==='enemy'?(o.name||o.kind||'enemy'):o.t;}
function cpCopy(){var lv=L(),o=lv&&(lv.objects||[])[SEL];if(!o){status('Select something first (Select / move tool), then copy.');return false;}CLIP=JSON.parse(JSON.stringify(o));status('Copied '+cpName(o)+'. Ctrl+V pastes a copy next to it.');return true;}
function cpUniq(o){if(!o.id)return;var ids={};levels().forEach(function(l){(l.objects||[]).forEach(function(q){if(q&&q.id)ids[q.id]=1;});});var base=String(o.id).replace(/_\d+$/,'').slice(0,56),n=2;while(ids[base+'_'+n])n++;o.id=base+'_'+n;
 if(o.t==='npc'&&o.name)o.name=String(o.name).replace(/ \d+$/,'').slice(0,70)+' '+n;}
function cpPaste(){if(!CLIP||!W){status('Nothing copied yet: select something and press Ctrl+C.');return false;}if(MODE!=='edit')setMode('edit');var lv=L();lv.objects=lv.objects||[];
 if(lv.objects.length>=5000){status('This level is full (5,000 things).',true);return false;}var o=JSON.parse(JSON.stringify(CLIP)),d2=lv.mode==='2d',st=d2?1:2,lim=d2?999:(lv.size||48)-1;
 if(d2&&o.t!=='npc'){status('In 2D levels only people can be copied; paint tiles with the Tools.');return false;}
 o.x=Math.max(d2?0:-lim,Math.min(lim,Math.round((+o.x+st)*100)/100));if(!d2)o.z=Math.max(-lim,Math.min(lim,Math.round((+o.z+st)*100)/100));
 if(Array.isArray(o.path))o.path=o.path.map(function(p){return{x:Math.round((+p.x+st)*10)/10,z:Math.round((+p.z+(d2?0:st))*10)/10};});
 cpUniq(o);lv.objects.push(o);SEL=lv.objects.length-1;CLIP=JSON.parse(JSON.stringify(o));if(ENG){ENG.rebuild();if(ENG.select)ENG.select(SEL);}changed();inspector();status('Pasted '+cpName(o)+'.');return true;}
function cpDup(){return cpCopy()&&cpPaste();}
// ----- snap to grid (3D levels): placed and dragged things land on the grid -----
var SNAP=0;try{SNAP=+(localStorage.getItem('gs_snap')||0)||0;}catch(e){}
function snapSel(){if(!SNAP||!W||MODE!=='edit')return;var lv=L();if(!lv||lv.mode!=='3d')return;var o=(lv.objects||[])[SEL];if(!o)return;var x=Math.round(o.x/SNAP)*SNAP,z=Math.round(o.z/SNAP)*SNAP;
 if(x!==o.x||z!==o.z){o.x=x;o.z=z;if(ENG)ENG.rebuild();}}
// ----- Watch panel: what the game is thinking while you play (variables, flags, quests, fired triggers) -----
var WATCH=false,WATCH_T=null;try{WATCH=localStorage.getItem('gs_watch')==='1';}catch(e){}
function watchHtml(g){var h='<b>Watch</b> <span class="mute">(studio only)</span>',V=g.vars||{},vd=Array.isArray(W.vars)?W.vars:[];
 h+='<div class="wsec">Player</div><div>Level '+(g.li+1)+' &middot; HP '+Math.ceil(g.st.hp)+' &middot; gold '+g.st.gold+' &middot; XP '+g.st.xp+' &middot; lvl '+g.st.lvl+'</div>';
 h+='<div class="wsec">Variables</div>'+(vd.length?vd.map(function(d){return'<div>'+E(d.name||d.id)+' = <b>'+E(GS.Plus.fmt(V[d.id]))+'</b></div>';}).join(''):'<div class="mute">none</div>');
 var fl=Object.keys(g.flags||{}).filter(function(k){return g.flags[k];});h+='<div class="wsec">Flags set</div><div>'+(fl.length?fl.map(E).join(', '):'<span class="mute">none</span>')+'</div>';
 var q=Object.keys(g.q||{});h+='<div class="wsec">Quests</div>'+(q.length?q.map(function(k){return'<div>'+E(k)+': '+E(g.q[k].state)+'</div>';}).join(''):'<div class="mute">none started</div>');
 var fi=Object.keys((g._lg&&g._lg.fired)||{});h+='<div class="wsec">Triggers fired (once)</div><div>'+(fi.length?fi.map(function(k){return E(k.replace(/^\d+:/,''));}).join(', '):'<span class="mute">none yet</span>')+'</div>';return h;}
function watchTick(){var p=$('watchP');if(!WATCH||MODE!=='play'||!ENG||!ENG.game||!ENG.game()){if(p)p.hidden=true;return;}if(!p){p=document.createElement('div');p.id='watchP';$('stage').appendChild(p);}p.hidden=false;p.innerHTML=watchHtml(ENG.game());}
// ----- hooks into the studio page -----
(function(){var _changed=changed;changed=function(skipHist){snapSel();return _changed.apply(this,arguments);};
 var _rp=renderPanels;renderPanels=function(){_rp.apply(this,arguments);renderTune();};
 var _st=showTab;showTab=function(id){_st.apply(this,arguments);var t=$('tTune');if(t)t.hidden=id!=='tTune';if(id==='tTune')renderTune();else tnStop();if(id==='tClass')renderLearn();};
 var _sm=setMode;setMode=function(m){var r=_sm.apply(this,arguments);if(MODE==='play')tnStop();return r;};
 var _mu=music;music=function(on){if(on&&W&&GS.Plus.tuneHas(W.tune)){return _mu(false);}return _mu.apply(this,arguments);};
 var _rw=renderWorld;renderWorld=function(){_rw.apply(this,arguments);var s=$('tWorld');if(!s||!W)return;var d=document.createElement('div');d.className='card';
  d.innerHTML='<h4>Open a game someone shared (remix)</h4><p class="mute">Choose a game file (.html) exported from Stark World Engine, or a world.json. It opens as a new game named "... (remix)", so you can see how it was made and change it. The original file is not changed and nothing is uploaded.</p><input type="file" id="rmxFile" accept=".html,.htm,.json" style="width:100%"><div id="rmxOut" class="mute"></div>'+(W.remixOf?'<p class="mute">Remixed from <b>'+E(W.remixOf)+'</b>.</p>':'');
  s.insertBefore(d,s.firstChild);$('rmxFile').onchange=function(){rmxOpen(this.files&&this.files[0]);};};
 var _in=inspector;inspector=function(){_in.apply(this,arguments);var box=$('insp'),lv=L();if(!box||!lv||SEL<0||!(lv.objects||[])[SEL])return;var card=box.querySelector('.card');if(!card)return;var r=document.createElement('div');r.className='row';
  r.innerHTML='<button id="cpDup" title="Ctrl+D">Duplicate</button><button id="cpCopy" title="Ctrl+C">Copy</button><button id="cpPaste" title="Ctrl+V"'+(CLIP?'':' disabled')+'>Paste</button>';var h4=card.querySelector('h4');if(h4&&h4.nextSibling)card.insertBefore(r,h4.nextSibling);else card.appendChild(r);
  $('cpDup').onclick=cpDup;$('cpCopy').onclick=function(){cpCopy();inspector();};$('cpPaste').onclick=cpPaste;};
 window.addEventListener('keydown',function(e){var tag=(e.target&&e.target.tagName)||'';if(/INPUT|TEXTAREA|SELECT/.test(tag)||MODE!=='edit'||!(e.ctrlKey||e.metaKey)||e.shiftKey||e.altKey)return;
  if(e.code==='KeyC'&&SEL>=0){if(window.getSelection&&String(window.getSelection())!=='')return;e.preventDefault();cpCopy();}else if(e.code==='KeyV'&&CLIP){e.preventDefault();cpPaste();}else if(e.code==='KeyD'&&SEL>=0){e.preventDefault();cpDup();}});
 function init(){var dr=$('drone');if(dr&&dr.parentNode&&!$('snapSel')){var l=document.createElement('label');l.style.cssText='margin:0;display:inline-flex;gap:4px;align-items:center';l.title='Snap placed and moved things to a grid (3D levels)';
   l.innerHTML='Snap <select id="snapSel">'+opts([['0','off'],['1','1 m'],['2','2 m'],['4','4 m']],String(SNAP))+'</select>';dr.parentNode.appendChild(l);
   var w=document.createElement('label');w.style.cssText='margin:0;display:inline-flex;gap:4px;align-items:center';w.title='While you play in the studio, show variables, flags, quests and fired triggers (never in exported games)';
   w.innerHTML='<input type="checkbox" id="watchCk"'+(WATCH?' checked':'')+' style="width:auto"> Watch panel';dr.parentNode.appendChild(w);
   $('snapSel').onchange=function(){SNAP=+this.value||0;try{localStorage.setItem('gs_snap',String(SNAP));}catch(x){}status(SNAP?'Snap to a '+SNAP+' m grid.':'Snap off.');};
   $('watchCk').onchange=function(){WATCH=this.checked;try{localStorage.setItem('gs_watch',WATCH?'1':'0');}catch(x){}watchTick();};}
  var cl=$('tClass');if(cl&&!$('tutList')){var d=document.createElement('div');d.innerHTML='<h4>Guided tutorials</h4><p class="mute">Short step-by-step lessons for your first games. Each step ticks itself off when you have done it. Your progress stays on this computer.</p><div id="tutList"></div><hr style="border-color:var(--line)"><h4>Classroom lessons</h4>';cl.insertBefore(d,cl.firstChild);renderLearn();}
  ['exCart','exItch'].forEach(function(id){var b=$(id);if(b)b.addEventListener('click',function(){TUTX.exported++;});});
  if(!document.getElementById('sp-css')){var st=document.createElement('style');st.id='sp-css';st.textContent='#tutCard{position:absolute;left:14px;bottom:14px;z-index:31;width:min(380px,86%);max-height:calc(100% - 28px);overflow:auto;background:var(--panel);border:1px solid var(--acc2);border-radius:12px;padding:10px 12px;box-shadow:0 8px 30px rgba(0,0,0,.5)}'+
   '#tutCard .tst{display:flex;gap:8px;margin:5px 0;font-size:13px;opacity:.75}#tutCard .tst.cur{opacity:1}#tutCard .tst.ok{opacity:.55}#tutCard .tst.ok span{color:var(--acc)}#tutCard .tst span{flex:none;width:18px;font-weight:700}#tutCard button{padding:2px 8px;font-size:12px}'+
   '#watchP{position:absolute;right:10px;top:42%;z-index:9;width:min(260px,46%);max-height:52%;overflow:auto;padding:8px 10px;border-radius:10px;background:rgba(10,14,22,.82);border:1px solid var(--line);font-size:12px;pointer-events:auto}#watchP .wsec{color:var(--acc2);margin-top:6px;font-weight:700}';document.head.appendChild(st);}
  if(!WATCH_T)WATCH_T=setInterval(watchTick,300);}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else setTimeout(init,0);})();
