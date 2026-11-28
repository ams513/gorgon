// ---------- Logic tab (studio only, never shipped in a cartridge): when/then triggers, cutscenes, rules, behaviours ----------
// Spliced into the studio page script at %%LOGICUI%% so it shares its helpers ($, E, opts, L, W, levels, changed, ...).
var LGV={door:['Open / close a door',[['id','Door','door'],['state','Do','state']]],sound:['Play a sound',[['sound','Sound','sound'],['caption','Caption for subtitles','text']]],
 dialogue:['Start a conversation',[['dialog','Conversation','dialog'],['npc','Spoken by','npc']]],say:['Show a line of text',[['speaker','Who','text'],['text','Line','text'],['secs','Seconds on screen (0 = auto)','num']]],
 flag:['Set / clear a flag',[['flag','Flag name','flag'],['value','Set it to','bool']]],give:['Give an item',[['item','Item','item'],['n','How many','num']]],take:['Take an item',[['item','Item','item'],['n','How many','num']]],
 gold:['Change gold',[['gold','Gold (+ or -)','num']]],xp:['Give XP',[['amount','XP','num']]],heal:['Heal fully',[]],startQuest:['Start a quest',[['quest','Quest','quest']]],completeQuest:['Finish a quest',[['quest','Quest','quest']]],
 cutscene:['Play a cutscene',[['cutscene','Cutscene','cut']]],teleport:['Move the player',[['x','X','num'],['z','Z','num']],1],level:['Go to a level',[['to','Level','level']]],win:['Win the game',[]],
 achievement:['Unlock an achievement',[['achievement','Achievement','ach']]],shake:['Shake the screen',[['amount','Strength 0-2','num']]],wait:['Wait',[['secs','Seconds','num']]],
 camera:['Camera move',[['x','Camera X','num'],['z','Camera Z','num'],['h','Height','num'],['lx','Look at X','num'],['lz','Look at Z','num'],['lh','Look height','num'],['dur','Move time (s)','num']],2],
 var:['Change a variable',[['var','Variable','var'],['op','How','vop'],['num','Number','num'],['max','Random: up to','num']]],
 move:['Walk someone',[['id','Who','mover'],['x','To X','num'],['z','To Z','num'],['dur','Time (s)','num']],1],fade:['Fade the screen',[['a','Fade to (0 clear, 1 black)','num'],['dur','Seconds','num']]]};
var LGW={enter:'the player enters an area',leave:'the player leaves an area',start:'the level starts',talk:'the player talks to someone',defeat:'an enemy is defeated',collect:'the player picks up an item',flag:'a flag is set',quest:'a quest is finished',timer:'some seconds have passed',every:'every few seconds (repeats)',key:'the player presses a key',var:'a variable reaches a value'};
var LGC=[['','(always)'],['has','player has item'],['lacks','player lacks item'],['done','quest is finished'],['active','quest is active'],['flag','flag is set'],['minLevel','player level at least'],['var','a variable compares'],['chance','random chance (%)']];
var LG_CMP=[['eq','is equal to'],['ne','is not'],['lt','is less than'],['le','is at most'],['gt','is more than'],['ge','is at least']];
var LG_VOP=[['add','add'],['sub','subtract'],['set','set to'],['random','random number from']];
var LG_KEYS=(function(){var o=[];'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').forEach(function(c){o.push(['Key'+c,c]);});for(var i=0;i<10;i++)o.push(['Digit'+i,String(i)]);return o.concat([['Space','Space'],['Enter','Enter'],['ArrowUp','Up arrow'],['ArrowDown','Down arrow'],['ArrowLeft','Left arrow'],['ArrowRight','Right arrow']]);})();
function lgVarList(){return(W&&Array.isArray(W.vars)?W.vars:[]).filter(function(v){return v&&v.id;}).map(function(v){return[v.id,(v.name||v.id)+' ('+v.id+')'];});}
var LG_CS=null,LG_OPEN={};
function lgLevel(){return L();}
function lgDoors(){var o=[];(L().objects||[]).forEach(function(d,i){if(d.t==='door')o.push(d);});var n=1,used={};o.forEach(function(d){if(d.id)used[d.id]=1;});var fixed=false;o.forEach(function(d){if(!d.id){while(used['door'+n])n++;d.id='door'+n;used[d.id]=1;fixed=true;}});if(fixed)changed(true);return o;}
function lgMovers(){var o=[['player','The player']];(L().objects||[]).forEach(function(d){if((d.t==='npc'||d.t==='enemy')&&d.id)o.push([d.id,d.name||d.id]);});return o;}
function lgFlags(){var s={};function sc(a){if(!a)return;if(a.flag)s[a.flag]=1;(a.then||[]).forEach(sc);(a.steps||[]).forEach(sc);}levels().forEach(function(l){(l.triggers||[]).forEach(function(t){sc(t.when);sc(t.if);sc(t);});Object.keys(l.cutscenes||{}).forEach(function(k){sc(l.cutscenes[k]);});Object.keys(l.dialogs||{}).forEach(function(k){var d=l.dialogs[k];Object.keys(d.nodes||{}).forEach(function(n){var nd=d.nodes[n];sc(nd.do);(nd.choices||[]).forEach(sc);sc(nd.if);});});});return Object.keys(s);}
function lgAchs(){var o=[];levels().forEach(function(l){(l.achievements||[]).forEach(function(a){if(a.id)o.push([a.id,a.title||a.id]);});});GS.Logic.builtin.forEach(function(a){o.push([a.id,a.title]);});return o;}
function lgCuts(){var o=[];levels().forEach(function(l){Object.keys(l.cutscenes||{}).forEach(function(k){o.push([k,(l.cutscenes[k].name||k)]);});});return o;}
function lgSounds(){var o=Object.keys(GS.SFX);Object.keys((W&&W.sfx)||{}).forEach(function(k){if(o.indexOf(k)<0)o.push(k);});return o;}
function lgCtl(kind,path,val,extra){var a=' data-lp="'+E(path)+'" data-ty="'+kind+'"',v=val==null?'':val;
 if(kind==='num')return'<input type="number" step="0.1"'+a+' value="'+E(v)+'" style="width:72px">';
 if(kind==='text')return'<input'+a+' value="'+E(v)+'" style="width:100%">';
 if(kind==='bool')return'<select'+a+'>'+opts([['1','on / yes'],['0','off / no']],v===false?'0':'1')+'</select>';
 if(kind==='state')return'<select'+a+'>'+opts([['open','Open it'],['close','Close it'],['toggle','Toggle it']],v||'open')+'</select>';
 if(kind==='vop')return'<select'+a+'>'+opts(LG_VOP,v||'add')+'</select>';
 if(kind==='cmp')return'<select'+a+'>'+opts(LG_CMP,v||'ge')+'</select>';
 if(kind==='key')return'<select'+a+'>'+opts(LG_KEYS,v||'KeyR')+'</select>';
 if(kind==='var'){var vl=lgVarList();if(!vl.length)return'<span class="mute">add a variable under Game rules below</span>';return'<select'+a+'>'+opts(vl,v,'(choose)')+'</select>';}
 if(kind==='flag')return'<input'+a+' list="lgFlags" value="'+E(v)+'" placeholder="a name, e.g. met_the_smith" style="width:100%">';
 var list=kind==='door'?lgDoors().map(function(d){return[d.id,'Door '+d.id+' ('+(+d.x).toFixed(0)+','+(+d.z).toFixed(0)+')'];}):kind==='sound'?lgSounds():kind==='dialog'?Object.keys(allDialogs()):kind==='npc'?npcs().map(function(n){return[n.id,n.name];}):
  kind==='item'?itemIds().map(function(k){return[k,itemName(k)];}):kind==='quest'?quests().map(function(x){return[x.q.id,x.q.title||x.q.id];}):kind==='cut'?lgCuts():kind==='level'?levels().map(function(l,i){return[String(i),'Level '+(i+1)+': '+(l.name||'')];}):
  kind==='ach'?lgAchs():kind==='mover'?lgMovers():kind==='enemy'?(L().objects||[]).filter(function(o){return o.t==='enemy'&&o.id;}).map(function(o){return[o.id,o.name||o.id];}):[];
 return'<select'+a+'>'+opts(list,v===''?'':String(v),'(choose)')+'</select>';}
function lgField(label,html){return'<label class="lgf"><span class="mute">'+E(label)+'</span>'+html+'</label>';}
function lgTarget(p){var a=p.split('|'),lv=L(),t;
 if(a[0]==='tg'){t=lv.triggers[+a[1]];return{o:t.when,k:a[2]};}
 if(a[0]==='th'){t=lv.triggers[+a[1]];return{o:t.then[+a[2]],k:a[3]};}
 if(a[0]==='tc'){t=lv.triggers[+a[1]];return{o:t,k:'if',cond:1};}
 if(a[0]==='tn'){t=lv.triggers[+a[1]];return{o:t,k:a[2]};}
 if(a[0]==='tc2'){t=lv.triggers[+a[1]];t.if=t.if||{};return{o:t.if,k:a[2]};}
 if(a[0]==='vd'){return{o:W.vars[+a[1]],k:a[2]};}
 if(a[0]==='cs'){return{o:lgCutOf(LG_CS).steps[+a[1]],k:a[2]};}
 if(a[0]==='cn'){return{o:lgCutOf(LG_CS),k:a[1]};}
 if(a[0]==='ac'){return{o:W.achievements[+a[1]],k:a[2]};}
 if(a[0]==='aw'){var A=W.achievements[+a[1]];A.when=A.when||{type:'kills'};return{o:A.when,k:a[2]};}
 if(a[0]==='wd'){return{o:W,k:a[1]};}
 return null;}
function lgCutOf(id){var r=null;levels().forEach(function(l){if(l.cutscenes&&l.cutscenes[id])r=l.cutscenes[id];});return r;}
function lgSet(el){var p=el.dataset.lp,a=p.split('|'),t=lgTarget(p);if(!t)return;var ty=el.dataset.ty,v=el.value,lv=L();
 if(ty==='verb'){
  if(a[0]==='tg'){var old=t.o,w={type:v};if(v==='enter'||v==='leave'){w.x=old.x||0;w.z=old.z||0;w.r=old.r||3;}if(v==='timer')w.secs=5;if(v==='every')w.secs=5;if(v==='key')w.key='KeyR';if(v==='var'){var vl=lgVarList();w.var=vl.length?vl[0][0]:'';w.cmp='ge';w.num=10;}lv.triggers[+a[1]].when=w;if(v==='every'||v==='key')lv.triggers[+a[1]].once=false;}
  else if(a[0]==='th'){lv.triggers[+a[1]].then[+a[2]]=lgDefaults(v);}
  else if(a[0]==='cs'){var st=lgDefaults(v);st.t=t.o.t||0;lgCutOf(LG_CS).steps[+a[1]]=st;}
  else if(a[0]==='aw'){var A=W.achievements[+a[1]];if(v)A.when={type:v,n:1};else delete A.when;}
  changed();renderLogic();return;}
 if(ty==='vshow'){if(v==='0')delete t.o.show;else t.o.show=v==='max'?'max':true;changed();renderLogic();return;}
 if(ty==='vid'){var nv=idOf(v),old=t.o.id;if(!nv||(W.vars||[]).some(function(x){return x!==t.o&&x.id===nv;})){status('Each variable needs its own id.',true);renderLogic();return;}t.o.id=nv;lgRenameVar(old,nv);changed();renderLogic();return;}
 if(ty==='num')v=v===''||!isFinite(+v)?undefined:+v;else if(ty==='bool')v=v==='1';else if(ty==='flag')v=v?idOf(v):'';else if(ty==='level')v=v===''?undefined:+v;
 if((t.k==='once'||t.k==='skip')&&typeof v==='boolean'){if(v)delete t.o[t.k];else t.o[t.k]=false;changed();return;}
 if(t.k==='showTimer'){if(v)t.o.showTimer=true;else delete t.o.showTimer;changed();return;}
 if(v===''||v===undefined)delete t.o[t.k];else t.o[t.k]=v;changed();}
function lgCondHtml(t,i){var c=t.if||{},k=Object.keys(c)[0]||'',val=k?c[k]:'',h='<div class="row"><span class="lgb lgif">IF</span><select data-lp="tc|'+i+'" data-ty="cond">'+opts(LGC,k)+'</select>';
 if(c.var!=null){h=h.replace('<option value="var">','<option value="var" selected>');return h+lgCtl('var','tc2|'+i+'|var',c.var,0)+lgCtl('cmp','tc2|'+i+'|cmp',c.cmp,0)+lgCtl('num','tc2|'+i+'|num',c.num==null?0:c.num,0)+'</div>';}
 if(k==='chance')return h+lgCtl('num','tc2|'+i+'|chance',c.chance==null?50:c.chance,0)+'<span class="mute">% of the time</span></div>';
 if(k){var kind=k==='has'||k==='lacks'?'item':k==='done'||k==='active'?'quest':k==='flag'?'flag':'num';h+='<span data-cv="'+i+'">'+lgCondVal(kind,i,k,val)+'</span>';}return h+'</div>';}
function lgCondVal(kind,i,k,val){var o='';if(kind==='num')return'<input type="number" min="1" data-cvk="'+i+':'+k+'" value="'+E(val||2)+'" style="width:72px">';
 if(kind==='flag')return'<input list="lgFlags" data-cvk="'+i+':'+k+'" value="'+E(val)+'" placeholder="flag name">';
 var list=kind==='item'?itemIds().map(function(x){return[x,itemName(x)];}):quests().map(function(x){return[x.q.id,x.q.title||x.q.id];});return'<select data-cvk="'+i+':'+k+'">'+opts(list,val,'(choose)')+'</select>';}
function lgBlockHtml(scope,i,j,a,cut){var spec=LGV[a.do]||['?',[]],h='<div class="lgblk'+(cut?' cut':'')+'"><div class="row">'+(cut?'<span class="mute">at</span>'+lgCtl('num','cs|'+j+'|t',a.t==null?0:a.t,0)+'<span class="mute">s</span>':'<span class="lgb lgthen">THEN</span>')+
 '<select data-lp="'+(cut?'cs|'+j:'th|'+i+'|'+j)+'|do" data-ty="verb">'+opts((cut?GS.Logic.CUT:GS.Logic.VERBS).map(function(v){return[v,LGV[v][0]];}),a.do)+'</select>'+
 '<button class="x" data-lgdel="'+scope+':'+i+':'+j+'" title="Remove this block">x</button><button data-lgup="'+scope+':'+i+':'+j+'" title="Move up">&uarr;</button></div><div class="lgfs">';
 spec[1].forEach(function(f){var path=cut?'cs|'+j+'|'+f[0]:'th|'+i+'|'+j+'|'+f[0];h+=lgField(f[1],lgCtl(f[2],path,a[f[0]],0));});
 if(spec[2]===1)h+='<button data-lgpick="'+scope+':'+i+':'+j+'">Pick on map</button>';
 if(spec[2]===2)h+='<button data-lgview="'+j+'" title="Copy the editor camera into this step">Use editor view</button>';
 return h+'</div></div>';}
function lgTrigHtml(t,i){var w=t.when||{type:'enter'},h='<div class="card lgcard"><button class="x" data-lgtdel="'+i+'">Delete</button><div class="row"><span class="lgb lgwhen">WHEN</span><select data-lp="tg|'+i+'|type" data-ty="verb">'+opts(Object.keys(LGW).map(function(k){return[k,LGW[k]];}),w.type)+'</select></div><div class="lgfs">';
 if(w.type==='enter'||w.type==='leave')h+=lgField('X',lgCtl('num','tg|'+i+'|x',w.x,0))+lgField('Z',lgCtl('num','tg|'+i+'|z',w.z,0))+lgField('Radius',lgCtl('num','tg|'+i+'|r',w.r==null?3:w.r,0))+'<button data-lgtpick="'+i+'">Pick on map</button>';
 else if(w.type==='talk')h+=lgField('Who',lgCtl('npc','tg|'+i+'|npc',w.npc,0));
 else if(w.type==='defeat')h+=lgField('Enemy with id',lgCtl('enemy','tg|'+i+'|id',w.id,0))+lgField('or any of kind','<select data-lp="tg|'+i+'|kind" data-ty="text">'+opts([['','(that enemy only)'],['any','any enemy'],['grunt','Slime'],['archer','Archer'],['brute','Brute'],['wisp','Wisp'],['boss','Boss']],w.kind||'')+'</select>');
 else if(w.type==='collect')h+=lgField('Item',lgCtl('item','tg|'+i+'|item',w.item,0));
 else if(w.type==='flag')h+=lgField('Flag',lgCtl('flag','tg|'+i+'|flag',w.flag,0));
 else if(w.type==='quest')h+=lgField('Quest',lgCtl('quest','tg|'+i+'|quest',w.quest,0));
 else if(w.type==='timer')h+=lgField('Seconds after the level starts',lgCtl('num','tg|'+i+'|secs',w.secs==null?5:w.secs,0));
 else if(w.type==='every')h+=lgField('Every how many seconds',lgCtl('num','tg|'+i+'|secs',w.secs==null?5:w.secs,0))+'<span class="mute" style="flex-basis:100%">Repeats while the level is played (paused in menus, conversations and cutscenes).</span>';
 else if(w.type==='key')h+=lgField('Key',lgCtl('key','tg|'+i+'|key',w.key,0))+'<span class="mute" style="flex-basis:100%">On a phone or a gamepad only the keys the game already uses (move, Space, E, J ...) can be pressed.</span>';
 else if(w.type==='var')h+=lgField('Variable',lgCtl('var','tg|'+i+'|var',w.var,0))+lgField('Becomes',lgCtl('cmp','tg|'+i+'|cmp',w.cmp,0))+lgField('Number',lgCtl('num','tg|'+i+'|num',w.num==null?0:w.num,0));
 h+='<label class="lgf"><span class="mute">Only once</span><select data-lp="tn|'+i+'|once" data-ty="bool">'+opts([['1','yes'],['0','every time']],t.once===false?'0':'1')+'</select></label></div>'+lgCondHtml(t,i);
 (t.then||[]).forEach(function(a,j){h+=lgBlockHtml('th',i,j,a,false);});
 return h+'<div class="row"><select data-lgadd="'+i+'">'+opts(GS.Logic.VERBS.map(function(v){return[v,LGV[v][0]];}),'','+ add a THEN block')+'</select></div></div>';}
function lgDefaults(v){var d={do:v};if(v==='sound')d.sound='coin';else if(v==='say'){d.speaker='';d.text='...';}else if(v==='flag')d.flag='my_flag';else if(v==='give'||v==='take')d.n=1;else if(v==='gold')d.gold=10;else if(v==='xp')d.amount=20;else if(v==='teleport'){d.x=0;d.z=0;}
 else if(v==='wait')d.secs=1;else if(v==='shake')d.amount=0.6;else if(v==='camera'){d.x=0;d.z=10;d.h=4;d.dur=2;}else if(v==='move'){d.id='player';d.x=0;d.z=0;d.dur=2;}else if(v==='fade'){d.a=1;d.dur=1;}else if(v==='door')d.state='open';else if(v==='var'){var vl=lgVarList();d.var=vl.length?vl[0][0]:'';d.op='add';d.num=1;}return d;}
function lgPick(cb){if(MODE!=='edit')setMode('edit');if(L().mode!=='3d'){status('Spots can be picked on 3D levels - on 2D levels type the tile column and row.');return;}ENG.setTool('pickpoint');status('Click the ground to choose the spot...');PENDING_PICK=function(x,z){cb(x,z);changed();renderLogic();status('Spot set: '+x+', '+z);};}
function lgCutHtml(){var lv=L(),all=lgCuts(),h='<h4>Cutscenes</h4><p class="mute">A timeline: at each second do one thing - move the camera, say a line, walk someone, play a sound, fade. The player is frozen and Esc skips it. Start one from a THEN block ("Play a cutscene") or from a conversation.</p><div class="row"><select id="lgCs" style="flex:1">'+opts(all,LG_CS,'(choose a cutscene)')+'</select><button id="lgCsNew">+ new</button></div>';
 var c=LG_CS&&lgCutOf(LG_CS);if(!c)return h;h+='<div class="card lgcard"><div class="two"><label>Name<input data-lp="cn|name" data-ty="text" value="'+E(c.name||'')+'"></label><label>Id<input disabled value="'+E(LG_CS)+'"></label></div><div class="row"><label style="flex:none">Esc skips it <select data-lp="cn|skip" data-ty="bool">'+opts([['1','yes'],['0','no']],c.skip===false?'0':'1')+'</select></label><button id="lgCsPlay">Test it in Play</button><button id="lgCsDel" style="color:var(--bad)">Delete</button></div>';
 (c.steps||[]).forEach(function(a,j){h+=lgBlockHtml('cs',0,j,a,true);});
 h+='<div class="row"><select id="lgCsAdd">'+opts(GS.Logic.CUT.map(function(v){return[v,LGV[v][0]];}),'','+ add a step')+'</select></div></div>';return h;}
function lgVarsHtml(){var V=W.vars||[],h='<h4>Variables (numbers the game keeps)</h4><style>#tLogic .lgcard .three>label{min-width:0}#tLogic .lgcard .three input,#tLogic .lgcard .three select{width:100%;min-width:0}</style><p class="mute">A variable is a named number, like <b>coins</b> or <b>lives</b>. Change it with the THEN block <i>Change a variable</i>, test it with an IF line or a WHEN <i>a variable reaches a value</i>, and choose <i>On screen</i> to show it in the corner while playing. It is saved with the player&#39;s game.</p>';
 V.forEach(function(v,i){h+='<div class="card lgcard"><button class="x" data-lgvdel="'+i+'">x</button><div class="three"><label>Id<input data-lp="vd|'+i+'|id" data-ty="vid" value="'+E(v.id||'')+'"></label><label>Name shown<input data-lp="vd|'+i+'|name" data-ty="text" value="'+E(v.name||'')+'"></label><label>Starts at<input type="number" data-lp="vd|'+i+'|start" data-ty="num" value="'+E(v.start==null?0:v.start)+'"></label></div><div class="three"><label>Lowest<input type="number" data-lp="vd|'+i+'|min" data-ty="num" placeholder="none" value="'+E(v.min==null?'':v.min)+'"></label><label>Highest<input type="number" data-lp="vd|'+i+'|max" data-ty="num" placeholder="none" value="'+E(v.max==null?'':v.max)+'"></label><label>On screen<select data-lp="vd|'+i+'|show" data-ty="vshow">'+opts([['0','hidden'],['1','number'],['max','number / highest']],v.show==='max'?'max':v.show?'1':'0')+'</select></label></div></div>';});
 return h+'<div class="row"><button id="lgVarAdd"'+(V.length>=30?' disabled':'')+'>+ variable</button></div>';}
function lgRulesHtml(){var h='<h4>Game rules</h4><div class="row"><label style="flex:none">High-score order <select data-lp="wd|scoring" data-ty="text">'+opts([['','Highest score first'],['time','Fastest time first (time trial)']],W.scoring==='time'?'time':'')+'</select></label><label style="flex:none">Show a timer <select data-lp="wd|showTimer" data-ty="bool">'+opts([['0','no'],['1','yes']],W.showTimer?'1':'0')+'</select></label></div><p class="mute">Players get a local high-score table, a pause-menu list of achievements (10 built in), remappable keys, a colour-blind palette, subtitles and slow mode - all automatically.</p>'+lgVarsHtml()+'<h4>Achievements you add</h4>';
 (W.achievements||[]).forEach(function(a,i){var w=a.when||{type:''};h+='<div class="card lgcard"><button class="x" data-lgadel="'+i+'">x</button><div class="two"><label>Id<input data-lp="ac|'+i+'|id" data-ty="id" value="'+E(a.id||'')+'"></label><label>Title<input data-lp="ac|'+i+'|title" data-ty="text" value="'+E(a.title||'')+'"></label></div><label>Description<input data-lp="ac|'+i+'|desc" data-ty="text" value="'+E(a.desc||'')+'"></label><div class="row"><span class="mute">Unlocks when</span><select data-lp="aw|'+i+'|type" data-ty="verb">'+opts([['','(only by a trigger)'],['kills','enemies defeated'],['gold','gold held'],['level','player level'],['quests','quests finished'],['flag','flag set'],['item','item held'],['win','game won'],['levels','levels visited'],['time','won within N seconds']],w.type||'')+'</select>';
  if(/kills|gold|level|quests|levels|time/.test(w.type||''))h+=lgCtl('num','aw|'+i+'|n',w.n==null?1:w.n,0);if(w.type==='flag')h+=lgCtl('flag','aw|'+i+'|flag',w.flag,0);if(w.type==='item')h+=lgCtl('item','aw|'+i+'|item',w.item,0);h+='</div></div>';});
 return h+'<div class="row"><button id="lgAchAdd">+ achievement</button></div>';}
function renderLogic(){var s=$('tLogic');if(!s)return;if(!W){s.innerHTML='';return;}var lv=L(),trs=lv.triggers||[],keep=s.scrollTop;
 var h='<datalist id="lgFlags">'+lgFlags().map(function(f){return'<option value="'+E(f)+'">';}).join('')+'</datalist><style>.lgcard{border-left:3px solid var(--acc2)}.lgb{padding:2px 8px;border-radius:6px;font-weight:700;font-size:11px;color:#111;flex:none}.lgwhen{background:#5fd3b5}.lgthen{background:#ffd166}.lgif{background:#b8b8ff}.lgblk{margin:6px 0;padding:6px;border-radius:8px;background:var(--panel)}.lgblk.cut{border-left:3px solid #6aa7ff}.lgfs{display:flex;flex-wrap:wrap;gap:6px;align-items:flex-end}.lgf{display:flex;flex-direction:column;gap:2px;min-width:90px;flex:1 1 110px}</style>'+
  '<p class="mute">Visual logic: <b>WHEN</b> something happens on this level (' +E(lv.name||W.name)+'), <b>THEN</b> do blocks in order. Triggers need no scripting and run in the exported game.</p><div class="row"><button id="lgTAdd">+ new trigger (when / then)</button></div>';
 if(!trs.length)h+='<div class="empty">No triggers on this level yet. Example: WHEN the player enters an area, THEN open a door, play a sound, start a conversation.</div>';
 trs.forEach(function(t,i){h+=lgTrigHtml(t,i);});h+='<hr style="border-color:var(--line)">'+lgCutHtml()+'<hr style="border-color:var(--line)">'+lgRulesHtml();s.innerHTML=h;s.scrollTop=keep;
 s.querySelectorAll('[data-lp]').forEach(function(el){el.onchange=function(){if(el.dataset.ty==='cond'){lgSetCond(el);return;}if(el.dataset.ty==='id')el.value=idOf(el.value);lgSet(el);};});
 s.querySelectorAll('[data-cvk]').forEach(function(el){el.onchange=function(){var a=el.dataset.cvk.split(':'),c={};c[a[1]]=a[1]==='minLevel'||a[1]==='n'?+el.value:el.value;if(a[1]==='flag')c.flag=idOf(el.value);lv.triggers[+a[0]].if=c;changed();};});
 s.querySelectorAll('[data-lgtdel]').forEach(function(b){b.onclick=function(){lv.triggers.splice(+b.dataset.lgtdel,1);changed();renderLogic();};});
 s.querySelectorAll('[data-lgdel]').forEach(function(b){b.onclick=function(){var a=b.dataset.lgdel.split(':');if(a[0]==='cs')lgCutOf(LG_CS).steps.splice(+a[2],1);else lv.triggers[+a[1]].then.splice(+a[2],1);changed();renderLogic();};});
 s.querySelectorAll('[data-lgup]').forEach(function(b){b.onclick=function(){var a=b.dataset.lgup.split(':'),arr=a[0]==='cs'?lgCutOf(LG_CS).steps:lv.triggers[+a[1]].then,j=+a[2];if(j>0){var x=arr[j];arr[j]=arr[j-1];arr[j-1]=x;changed();renderLogic();}};});
 s.querySelectorAll('[data-lgadd]').forEach(function(el){el.onchange=function(){if(!el.value)return;var t=lv.triggers[+el.dataset.lgadd];t.then=t.then||[];t.then.push(lgDefaults(el.value));changed();renderLogic();};});
 s.querySelectorAll('[data-lgpick]').forEach(function(b){b.onclick=function(){var a=b.dataset.lgpick.split(':'),o=a[0]==='cs'?lgCutOf(LG_CS).steps[+a[2]]:lv.triggers[+a[1]].then[+a[2]];lgPick(function(x,z){o.x=x;o.z=z;});};});
 s.querySelectorAll('[data-lgtpick]').forEach(function(b){b.onclick=function(){var w=lv.triggers[+b.dataset.lgtpick].when;lgPick(function(x,z){w.x=x;w.z=z;});};});
 s.querySelectorAll('[data-lgview]').forEach(function(b){b.onclick=function(){var v=ENG&&ENG.getView&&ENG.getView(),st=lgCutOf(LG_CS).steps[+b.dataset.lgview];if(!v||!v.t){status('Open a 3D level in Build mode first.');return;}
  st.x=Math.round((v.t[0]+Math.sin(v.y)*v.d)*10)/10;st.z=Math.round((v.t[2]+Math.cos(v.y)*v.d)*10)/10;st.h=Math.round(v.d*0.88*10)/10;st.lx=Math.round(v.t[0]*10)/10;st.lz=Math.round(v.t[2]*10)/10;st.lh=0;changed();renderLogic();status('Camera set from the editor view.');};});
 $('lgTAdd').onclick=function(){lv.triggers=lv.triggers||[];var n=1,ids=lv.triggers.map(function(t){return t.id;});while(ids.indexOf('t'+n)>=0)n++;lv.triggers.push({id:'t'+n,when:{type:'enter',x:0,z:0,r:3},then:[{do:'say',speaker:'',text:'Something happens!'}]});changed();renderLogic();};
 if($('lgCs'))$('lgCs').onchange=function(){LG_CS=this.value||null;renderLogic();};
 if($('lgCsNew'))$('lgCsNew').onclick=function(){lv.cutscenes=lv.cutscenes||{};var n=1;while(lgCutOf('cut'+n))n++;LG_CS='cut'+n;lv.cutscenes[LG_CS]={name:'Cutscene '+n,steps:[{t:0,do:'camera',x:10,z:10,h:5,dur:2},{t:1,do:'say',speaker:'',text:'Look over there...',secs:2.5}]};changed();renderLogic();};
 if($('lgCsAdd'))$('lgCsAdd').onchange=function(){if(!this.value)return;var c=lgCutOf(LG_CS),last=0;(c.steps||[]).forEach(function(a){last=Math.max(last,(+a.t||0)+(+a.dur||0));});var st=lgDefaults(this.value);st.t=Math.round(last*10)/10;c.steps.push(st);changed();renderLogic();};
 if($('lgCsDel'))$('lgCsDel').onclick=function(){levels().forEach(function(l){if(l.cutscenes)delete l.cutscenes[LG_CS];});LG_CS=null;changed();renderLogic();};
 if($('lgCsPlay'))$('lgCsPlay').onclick=function(){var id=LG_CS;setMode('play');setTimeout(function(){var g=ENG&&ENG.game&&ENG.game();if(g){g.titleOn=false;g.cutscene(id);}},300);};
 if($('lgAchAdd'))$('lgAchAdd').onclick=function(){W.achievements=W.achievements||[];var n=1,ids=W.achievements.map(function(a){return a.id;});while(ids.indexOf('ach'+n)>=0)n++;W.achievements.push({id:'ach'+n,title:'New achievement',desc:'',when:{type:'kills',n:5}});changed();renderLogic();};
 if($('lgVarAdd'))$('lgVarAdd').onclick=function(){W.vars=W.vars||[];var ids=W.vars.map(function(v){return v.id;}),first=ids.indexOf('coins')<0,n=1;while(ids.indexOf('var'+n)>=0)n++;W.vars.push(first?{id:'coins',name:'Coins',start:0,show:true}:{id:'var'+n,name:'Variable '+n,start:0});changed();renderLogic();};
 s.querySelectorAll('[data-lgvdel]').forEach(function(b){b.onclick=function(){W.vars.splice(+b.dataset.lgvdel,1);if(!W.vars.length)delete W.vars;changed();renderLogic();};});
 s.querySelectorAll('[data-lgadel]').forEach(function(b){b.onclick=function(){W.achievements.splice(+b.dataset.lgadel,1);changed();renderLogic();};});}
function lgRenameVar(a,b){if(!a||a===b)return;function fix(o){if(o&&typeof o==='object'&&o.var===a)o.var=b;}levels().forEach(function(l){(l.triggers||[]).forEach(function(t){fix(t.when);fix(t.if);(t.then||[]).forEach(fix);});Object.keys(l.cutscenes||{}).forEach(function(k){(l.cutscenes[k].steps||[]).forEach(fix);});Object.keys(l.dialogs||{}).forEach(function(k){var d=l.dialogs[k];Object.keys(d.nodes||{}).forEach(function(n){(d.nodes[n].choices||[]).forEach(function(c){fix(c);fix(c.if);});});(d.branches||[]).forEach(function(br){fix(br.if);});});});}
function lgSetCond(el){var i=+el.dataset.lp.split('|')[1],t=L().triggers[i],v=el.value;if(!v)delete t.if;else if(v==='var'){var vl=lgVarList();t.if={var:vl.length?vl[0][0]:'',cmp:'ge',num:1};}else if(v==='chance')t.if={chance:50};else{var c={};c[v]=v==='minLevel'?2:'';t.if=c;}changed();renderLogic();}
// ----- inspector extras: behaviour, patrol path, shop, door id -----
function lgInsp(o){var lv=L(),h='';if(o.t==='door')return'<label>Id (so a trigger can open it)<input data-lk="id" value="'+E(o.id||'')+'"></label><label>Who can open it<select data-lk="sealed">'+opts([['0','The key opens it'],['1','Only a trigger (sealed)']],o.sealed?'1':'0')+'</select></label>';
 if(o.t!=='npc'&&o.t!=='enemy')return'';var is3=lv.mode==='3d',bl=o.t==='npc'?[['','Stands still'],['patrol','Patrols a path'],['follow','Follows the player'],['flee','Runs away from the player'],['companion','Companion (follows and fights)']]:[['','Wanders'],['patrol','Patrols a path'],['guard','Guards its post'],['coward','Runs when hurt']];
 if(is3){h+='<label>Behaviour<select data-lk="behavior">'+opts(bl,o.behavior||'')+'</select></label>';
  if(o.behavior==='patrol'){h+='<div class="mute">Path: '+(o.path||[]).map(function(p,i){return(i+1)+':('+(+p.x).toFixed(0)+','+(+p.z).toFixed(0)+')';}).join(' ')+'</div><div class="row"><button data-lkpath="add">+ add point on map</button><button data-lkpath="clear">Clear path</button></div>';}
  if(o.t==='npc'&&o.behavior)h+='<div class="two"><label>Speed<input type="number" step="0.1" data-lk="speed" value="'+E(o.speed==null?'':o.speed)+'" placeholder="2.2"></label><label>'+(o.behavior==='flee'?'Flee radius':'Stay this close')+'<input type="number" step="0.5" data-lk="radius" value="'+E(o.radius==null?'':o.radius)+'"></label></div>';}
 if(o.t==='npc'){var sh=o.shop;h+='<label>Shop<select data-lk="shopOn">'+opts([['0','No shop'],['1','Buys and sells']],sh?'1':'0')+'</select></label>';
  if(sh){h+='<div class="two"><label>Pays this share of value<input type="number" step="0.05" data-lk="buyRate" value="'+E(sh.buyRate==null?0.5:sh.buyRate)+'"></label><label>Buys from player<select data-lk="shopSell">'+opts([['1','yes'],['0','no']],sh.sell===false?'0':'1')+'</select></label></div>';
   (sh.stock||[]).forEach(function(e,i){h+='<div class="row"><select data-lks="'+i+':item" style="flex:1">'+opts(itemIds().map(function(k){return[k,itemName(k)];}),e.item)+'</select><input type="number" data-lks="'+i+':price" value="'+E(e.price==null?'':e.price)+'" placeholder="price" style="width:70px"><input type="number" data-lks="'+i+':n" value="'+E(e.n==null?'':e.n)+'" placeholder="stock" style="width:62px"><button class="x" data-lksdel="'+i+'">x</button></div>';});
   h+='<div class="row"><button data-lksadd>+ item for sale</button></div><div class="mute">Leave price empty for the automatic price; stock empty = unlimited. Talk to them (E) to trade.</div>';}}
 return h;}
function lgInspBind(box,o){var lv=L(),refresh=function(){if(ENG)ENG.rebuild();changed();inspector();};
 box.querySelectorAll('[data-lk]').forEach(function(el){el.onchange=function(){var k=el.dataset.lk,v=el.value;
  if(k==='shopOn'){if(v==='1')o.shop=o.shop||{stock:[],sell:true,buyRate:0.5};else delete o.shop;refresh();return;}
  if(k==='shopSell'){o.shop.sell=v==='1';refresh();return;}if(k==='buyRate'){o.shop.buyRate=Math.max(0.05,Math.min(1,+v||0.5));refresh();return;}
  if(k==='sealed'){if(v==='1')o.sealed=true;else delete o.sealed;refresh();return;}if(k==='id'){v=v?idOf(v):'';}if(el.type==='number')v=v===''||!isFinite(+v)?undefined:+v;if(v===''||v===undefined)delete o[k];else o[k]=v;if(k==='behavior'&&v==='patrol'&&!o.path)o.path=[{x:+o.x+4,z:+o.z},{x:+o.x+4,z:+o.z+4}];refresh();};});
 box.querySelectorAll('[data-lks]').forEach(function(el){el.onchange=function(){var a=el.dataset.lks.split(':'),e=o.shop.stock[+a[0]];if(a[1]==='item')e.item=el.value;else{var v=el.value===''?undefined:+el.value;if(v===undefined)delete e[a[1]];else e[a[1]]=v;}refresh();};});
 box.querySelectorAll('[data-lksdel]').forEach(function(b){b.onclick=function(){o.shop.stock.splice(+b.dataset.lksdel,1);refresh();};});
 box.querySelectorAll('[data-lksadd]').forEach(function(b){b.onclick=function(){o.shop.stock=o.shop.stock||[];o.shop.stock.push({item:itemIds()[0]});refresh();};});
 box.querySelectorAll('[data-lkpath]').forEach(function(b){b.onclick=function(){if(b.dataset.lkpath==='clear'){delete o.path;refresh();return;}if(MODE!=='edit')setMode('edit');ENG.setTool('pickpoint');status('Click the ground to add a path point...');PENDING_PICK=function(x,z){o.path=o.path||[];o.path.push({x:x,z:z});changed();inspector();status('Path point '+o.path.length+' added.');};};});}
