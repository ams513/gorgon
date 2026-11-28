"use strict";
// Stark Covenant practice screens (practice_suite): Today, Calendar (+ .ics file), Billing, Forms (designer), the
// chart's Appointments, Measures, Treatment plan, Billing and Forms tabs, note templates, co-signature, supervision
// and the access report.
// Loaded after sign-in by pmReady() in the page; uses the page's helpers (el, T, api, act, content ...).
// Every UI string comes from the catalog scope "pm" (fetched here) or "app"; nothing user-facing is hard-coded.
const PM=(()=>{
let INFO=null,STAFF=null;
const ATONE={scheduled:'info',arrived:'info',completed:'ok',no_show:'bad',cancelled:'',late_cancel:'warn'};
const BTONE={none_minimal:'ok',minimal:'ok',mild:'info',moderate:'warn',moderately_severe:'bad',severe:'bad',screen_negative:'ok',screen_positive:'warn',below_cutoff:'ok',near_cutoff:'warn',probable_ptsd:'bad',who5_low:'warn',who5_not_low:'ok'};
const GTONE={not_started:'',in_progress:'info',met:'ok',not_met:'warn',discontinued:''};
async function strings(){
 if(I18N.m['pm.title']!=null)return;
 const r=await fetch('/i18n/'+encodeURIComponent(I18N.locale)+'.json?scope=pm',{credentials:'same-origin'});
 if(!r.ok)throw new Error(T('app.pm.unavailable'));
 const b=await r.json();Object.assign(I18N.m,b.m);Object.assign(I18N.en,b.en||{});
 for(const k of b.clinical||[])_clin.add(k);for(const k of b.paired||[])_pair.add(k);for(const k of b.unreviewed||[])_unrev.add(k)}
async function info(){if(!INFO)INFO=await api('GET','/api/pm/info');return INFO}
async function staff(fresh){if(!STAFF||fresh)STAFF=(await api('GET','/api/pm/staff')).staff;return STAFF}
async function post(p,b){// like api(), but hands back the HTTP status (409 = overlap: offer "book anyway")
 const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':S.csrf||''},body:JSON.stringify(b||{}),credentials:'same-origin'});
 let j={};try{j=await r.json()}catch(e){}
 if(r.status===401&&S.user){S.user=null;S.csrf=null;showLogin(trErr(j.error||'')||T('app.login.session_locked'));throw new Error('locked')}
 return [r.status,j]}
// --- small helpers -----------------------------------------------------------------------------------------
const addDays=(s,n)=>{const d=new Date(s+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10)};
const isoDow=s=>new Date(s+'T00:00:00Z').getUTCDay()||7;
function weekStart(s){const f=Number((I18N.region||{}).first_day)||1;return addDays(s,-((isoDow(s)-f+7)%7))}
function wday(s){try{return new Intl.DateTimeFormat(I18N.intl,{weekday:'long',timeZone:'UTC'}).format(new Date(s+'T00:00:00Z'))}catch(e){return ''}}
function cur(){return (I18N.region||{}).currency||'USD'}
function cash(c,k){return money((c||0)/100,k||cur())}
function bal(b){const e=Object.entries(b||{}).filter(([k,v])=>v);return e.length?e.map(([k,v])=>cash(v,k)).join(' + '):cash(0)}
function sel(name,vals,label,v,extra){return el('select',Object.assign({name},extra||{}),vals.map(x=>el('option',{value:x,selected:String(x)===String(v)?true:null},label(x))))}
function card(title,icon,...kids){return el('div',{class:'card'},el('div',{class:'ch'},el('h3',null,icon?ic(icon):null,title)),...kids)}
function none(t){return el('div',{class:'empty'},el('p',null,t))}
function openBtn(pid,name){return can('demo.read')?el('button',{class:'ghost',type:'button',onclick:act(()=>openPatient(pid))},auto('span',name||pid)):auto('span',name||pid)}
function reasonForm(label,btn,fn,extra){const r=inp('reason','text','',{dir:'auto',required:true});
 const f=el('form',{class:'pmline',onsubmit:act(async()=>{await fn(r.value,f)})},field(label,r),extra||null,el('button',{class:'warnbtn',type:'submit'},btn));return f}
async function patients(){return (await api('GET','/api/patients?q=')).patients}
// --- appointments --------------------------------------------------------------------------------------------
function apptRow(a,withPatient,after){
 const end=(()=>{const[h,m]=a.time.split(':').map(Number),t=h*60+m+a.minutes;return String(Math.floor(t/60)).padStart(2,'0')+':'+String(t%60).padStart(2,'0')})();
 const box=el('li',{class:'pmappt'},el('div',{class:'it'},
  el('div',{class:'t'},el('span',{dir:'ltr'},a.time+'-'+end),' ',withPatient?openBtn(a.patient,a.patient_name):null),
  el('div',{class:'sub'},[TK('pm.akind',a.kind),a.clinician_name,T('pm.appt.minutes',{n:a.minutes}),a.series?T('pm.appt.series',{n:a.seq}):''].filter(Boolean).join(' · ')),
  a.notes?auto('div',a.notes,{class:'meta'}):null,a.reason?auto('div',T('pm.appt.reason',{reason:a.reason}),{class:'meta'}):null),
  chip(TK('pm.astatus',a.status),ATONE[a.status]));
 if(can('sched.write')){
  const go=st=>act(async()=>{await api('POST','/api/pm/appts/status',{id:a.id,date:a.date,status:st});say(T('pm.appt.updated'),'ok');await after()});
  const acts=el('div',{class:'pmacts'});
  for(const st of ['arrived','completed','no_show'])if(a.status!==st&&a.status!=='cancelled')acts.append(el('button',{class:'ghost',type:'button',onclick:go(st)},T('pm.appt.mark_'+st)));
  if(a.status==='scheduled'){
   const scope=a.series?el('label',{class:'inl'},el('input',{type:'checkbox',name:'following'}),' '+T('pm.appt.following')):null;
   const kind=sel('kind',['cancelled','late_cancel'],v=>TK('pm.astatus',v),'cancelled',{'aria-label':T('pm.appt.cancel_kind')});
   acts.append(el('details',null,el('summary',null,T('pm.appt.cancel')),reasonForm(T('pm.appt.cancel_reason'),T('pm.appt.cancel_btn'),async(reason,f)=>{
    await api('POST','/api/pm/appts/status',{id:a.id,date:a.date,status:kind.value,reason,scope:f.querySelector('[name=following]')&&f.querySelector('[name=following]').checked?'following':'one'});
    say(T('pm.appt.cancelled'),'ok');await after()},el('div',null,field(T('pm.appt.cancel_kind'),kind),scope))),
    el('details',null,el('summary',null,T('pm.appt.move')),moveForm(a,after)))}
  box.append(acts)}
 return box}
function moveForm(a,after){
 const msg=el('p',{role:'alert'});
 const f=el('form',{class:'pmline',onsubmit:act(async ev=>{const d=formData(f);const b={id:a.id,date:a.date,appt:{date:d.new_date,time:d.time,minutes:Number(d.minutes)}};
   if(f.dataset.force)b.appt.allow_overlap=true;const[st,j]=await post('/api/pm/appts/move',b);
   if(st===409){f.dataset.force='1';msg.className='bad';msg.textContent=trErr(j.error)+' '+T('pm.appt.again');return}
   if(!j.ok)throw new Error(trErr(j.error||('HTTP '+st)));say(T('pm.appt.moved'),'ok');await after()})},
  el('div',{class:'row'},field(T('app.field.date'),inp('new_date','date',a.date,{required:true})),field(T('pm.f.time'),inp('time','time',a.time,{required:true})),
   field(T('pm.f.minutes'),inp('minutes','number',a.minutes,{min:5,max:480,required:true}))),msg,el('button',{type:'submit'},T('pm.appt.move_btn')));
 return f}
async function bookForm(pid,after,day){
 const[ii,st]=await Promise.all([info(),staff()]);const pts=pid?null:await patients();
 const mine=st.find(x=>x.id===S.user.id);const msg=el('p',{role:'alert'});
 const rep=sel('every',[0,1,2,3,4],v=>v?T('pm.appt.every',{n:v}):T('pm.appt.once'),0);
 const f=el('form',{class:'card',onsubmit:act(async()=>{const d=formData(f);
   const b={patient:pid||d.patient,clinician:d.clinician,date:d.date,time:d.time,minutes:Number(d.minutes),kind:d.kind,notes:d.notes};
   if(Number(d.every))b.repeat={every_weeks:Number(d.every),count:Number(d.count||1)};
   if(f.dataset.force)b.allow_overlap=true;
   const[s,j]=await post('/api/pm/appts',b);
   if(s===409){f.dataset.force='1';msg.className='bad';msg.textContent=trErr(j.error)+' '+T('pm.appt.again');return}
   if(!j.ok)throw new Error(trErr(j.error||('HTTP '+s)));
   say(T('pm.appt.booked',{n:j.created.length}),'ok');await after()})},
  el('h3',{class:'pmh'},ic('plus'),T('pm.appt.book')),
  pid?null:field(T('pm.f.patient'),el('select',{name:'patient',required:true},pts.map(p=>el('option',{value:p.id},p.name+' ('+dday(p.dob)+')')))),
  el('div',{class:'row'},field(T('pm.f.clinician'),el('select',{name:'clinician',required:true},st.map(x=>el('option',{value:x.id,selected:mine&&x.id===mine.id?true:null},x.display)))),
   field(T('app.field.type'),sel('kind',ii.appt_kinds,v=>TK('pm.akind',v),'session'))),
  el('div',{class:'row'},field(T('app.field.date'),inp('date','date',day||ii.today,{required:true})),field(T('pm.f.time'),inp('time','time','09:00',{required:true})),
   field(T('pm.f.minutes'),inp('minutes','number','50',{min:5,max:480,required:true}))),
  el('div',{class:'row'},field(T('pm.f.repeat'),rep),field(T('pm.f.count'),inp('count','number','6',{min:1,max:ii.max_series}))),
  field(T('pm.f.notes'),inp('notes','text','',{dir:'auto',maxlength:300})),el('p',{class:'muted'},T('pm.appt.notes_hint')),msg,
  el('button',{type:'submit'},T('pm.appt.book_btn')));
 if(!st.length)return el('div',{class:'card'},el('p',{class:'muted'},T('pm.appt.no_staff')));
 if(!pid&&!pts.length)return el('div',{class:'card'},el('p',{class:'muted'},T('app.main.no_patients')));
 return f}
function statsLine(s){return el('p',{class:'muted'},T('pm.appt.stats',{completed:s.completed+s.arrived,no_show:s.no_show,late:s.late_cancel,cancelled:s.cancelled,
 rate:s.no_show_rate==null?'-':num(s.no_show_rate)+'%'}))}
async function cal(){
 await strings();const ii=await info();const st=await staff();
 S.pmWeek=S.pmWeek||weekStart(ii.today);const from=S.pmWeek,to=addDays(from,6);
 const r=await api('GET','/api/pm/appts?from='+from+'&to='+to+(S.pmClin?'&clinician='+encodeURIComponent(S.pmClin):''));
 const nav=k=>act(async()=>{S.pmWeek=k==='now'?weekStart(ii.today):addDays(S.pmWeek,k==='prev'?-7:7);await cal()});
 const who=sel('clin',[''].concat(st.map(x=>x.id)),v=>v?st.find(x=>x.id===v).display:T('pm.cal.everyone'),S.pmClin||'');
 who.addEventListener('change',act(async()=>{S.pmClin=who.value;await cal()}));
 const head=el('div',{class:'card'},el('h2',{class:'pmh'},ic('calendar'),T('pm.cal.title')),
  el('p',null,el('b',null,T('pm.cal.week',{from:dday(from),to:dday(to)}))),
  el('div',{class:'pmacts'},el('button',{class:'ghost',type:'button',onclick:nav('prev')},T('pm.cal.prev')),el('button',{class:'ghost',type:'button',onclick:nav('now')},T('pm.cal.this')),
   el('button',{class:'ghost',type:'button',onclick:nav('next')},T('pm.cal.next'))),
  field(T('pm.f.clinician'),who),statsLine(r.stats),el('p',{class:'muted'},T('pm.cal.local_time')),
  el('details',null,el('summary',null,T('pm.ics.title')),icsForm(st,from)));
 const days=[];for(let i=0;i<7;i++){const d=addDays(from,i),list=r.appts.filter(a=>a.date===d);
  days.push(el('div',{class:['card',d===ii.today?'pmtoday':'']},el('div',{class:'ch'},el('h3',null,wday(d)+' '+dday(d)),d===ii.today?chip(T('pm.cal.today'),'info'):null),
   list.length?el('ul',{class:'items'},list.map(a=>apptRow(a,true,cal))):none(T('pm.cal.free'))))}
 const parts=[head,el('div',{class:'grid'},days),await remCard(st),await waitCard(st)];if(can('sched.write'))parts.push(await bookForm(null,cal,from>ii.today?from:ii.today));
 content(...parts)}
function icsForm(st,from){// the appointments as a calendar file saved on this computer (nothing is sent anywhere)
 const f=el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(f);
   await download('GET','/api/pm/appts.ics?'+new URLSearchParams({from:d.ics_from,to:d.ics_to,clinician:d.ics_clin,names:d.names}).toString(),null,'appointments.ics')})},
  el('p',{class:'muted'},T('pm.ics.intro')),
  el('div',{class:'row'},field(T('pm.acc.from'),inp('ics_from','date',from,{required:true})),field(T('pm.acc.to'),inp('ics_to','date',addDays(from,27),{required:true})),
   field(T('pm.f.clinician'),sel('ics_clin',[''].concat(st.map(x=>x.id)),v=>v?st.find(x=>x.id===v).display:T('pm.cal.everyone'),S.pmClin||'')),
   field(T('pm.ics.names'),sel('names',['initials','full','none'],v=>TK('pm.icsnames',v),'initials'))),
  el('button',{type:'submit'},ic('down'),T('pm.ics.btn')));
 return f}
async function apptTab(box){
 const r=await api('GET','/api/pm/appts/patient?patient='+encodeURIComponent(S.pid));const redo=()=>apptTab(box);
 const past=r.appts.filter(a=>!r.upcoming.includes(a)&&!r.upcoming.some(u=>u.id===a.id)).reverse();
 box.replaceChildren(card(T('pm.appt.upcoming'),'calendar',r.upcoming.length?el('ul',{class:'items'},r.upcoming.map(a=>{const x=apptRow(a,false,redo);x.querySelector('.t').prepend(dday(a.date)+' ');return x})):none(T('pm.appt.none_upcoming'))),
  card(T('pm.appt.history'),null,statsLine(r.stats),past.length?el('ul',{class:'items'},past.map(a=>{const x=apptRow(a,false,redo);x.querySelector('.t').prepend(dday(a.date)+' ');return x})):none(T('pm.appt.none_past'))),
  can('sched.write')?await bookForm(S.pid,redo):null)}
// --- outcome measures ----------------------------------------------------------------------------------------
function trend(key,spec,list){// one series: score over time on the instrument's own 0..max scale, severity bands shaded
 const N='http://www.w3.org/2000/svg',W=640,H=220,L=36,R=16,TP=12,B=28,mx=spec.max;
 const mk=(t,a,...k)=>{const e=document.createElementNS(N,t);for(const[x,v]of Object.entries(a||{}))e.setAttribute(x,v);for(const c of k)e.append(c);return e};
 const ts=list.map(m=>Date.parse(m.date+'T00:00:00Z')),t0=Math.min(...ts),t1=Math.max(...ts);
 const X=t=>t1===t0?(L+W-R)/2:L+(W-L-R)*(t-t0)/(t1-t0),Y=v=>TP+(H-TP-B)*(1-v/mx);
 const svg=mk('svg',{viewBox:'0 0 '+W+' '+H,class:'pmchart',role:'img','aria-label':T('pm.meas.chart_aria',{name:spec.name,n:list.length})});
 spec.bands.forEach(([lo,hi,b],i)=>{svg.append(mk('rect',{x:L,y:Y(hi+(i<spec.bands.length-1?1:0)),width:W-L-R,height:Y(lo)-Y(hi+(i<spec.bands.length-1?1:0)),class:'band','fill-opacity':(0.04+0.05*i).toFixed(2)}));
  svg.append(mk('text',{x:L-6,y:Y(lo)-2,class:'ax','text-anchor':'end'},String(lo)))});
 svg.append(mk('text',{x:L-6,y:Y(mx)+10,class:'ax','text-anchor':'end'},String(mx)));
 svg.append(mk('line',{x1:L,y1:Y(0),x2:W-R,y2:Y(0),class:'base'}));
 svg.append(mk('polyline',{points:list.map((m,i)=>X(ts[i]).toFixed(1)+','+Y(m.total).toFixed(1)).join(' '),class:'ln'}));
 list.forEach((m,i)=>svg.append(mk('circle',{cx:X(ts[i]).toFixed(1),cy:Y(m.total).toFixed(1),r:5,class:'pt'},mk('title',null,dday(m.date)+': '+m.total+'/'+mx+' - '+T('pm.band.'+m.band)))));
 svg.append(mk('text',{x:L,y:H-8,class:'ax'},dday(list[0].date)));
 if(list.length>1)svg.append(mk('text',{x:W-R,y:H-8,class:'ax','text-anchor':'end'},dday(list[list.length-1].date)));
 return el('div',{class:'pmchartwrap'},svg)}
async function measTab(box){
 const ii=await info(),c=S.chart,ms=(c.measures||[]);const redo=async()=>{await reload()};
 const parts=[];
 for(const[k,spec]of Object.entries(ii.instruments)){const list=ms.filter(m=>m.instrument===k&&m.status==='active').sort((a,b)=>a.date<b.date?-1:a.date>b.date?1:a.at<b.at?-1:1);
  if(!list.length)continue;const last=list[list.length-1],first=list[0],gb=spec.bands_by_group&&spec.bands_by_group[last.group];
  const sp2=gb?Object.assign({},spec,{bands:gb}):spec,cl=last.clusters;
  parts.push(el('div',{class:['card','wide']},el('div',{class:'ch'},el('h3',{class:'pmh'},ic('chart'),T('pm.meas.trend',{name:spec.name})),chip(T('pm.band.'+last.band),BTONE[last.band])),
   el('p',null,T('pm.meas.latest',{date:dday(last.date),total:last.total,max:spec.max})+(spec.percent?' '+T('pm.care.who5_pct',{n:last.total*spec.percent}):'')+(list.length>1?' '+T('pm.meas.change',{n:(last.total-first.total>0?'+':'')+(last.total-first.total),date:dday(first.date)}):'')),
   last.flag?el('p',{class:'bad',role:'note'},T('pm.meas.flag')):null,gb?el('p',{class:'muted'},TK('pm.mgroup',last.group)):null,
   cl?el('p',null,T('pm.meas.clusters',{b:cl.B,c:cl.C,d:cl.D,e:cl.E})):null,cl?el('p',{class:last.criteria_met?'bad':'muted'},T(last.criteria_met?'pm.meas.criteria_met':'pm.meas.criteria_not_met')):null,
   trend(k,sp2,list.filter(m=>!gb||m.group===last.group))))}
 const rows=[...ms].sort((a,b)=>a.date<b.date?1:-1).map(m=>{const spec=ii.instruments[m.instrument]||{name:m.instrument,max:''};
  return el('tr',{class:m.status!=='active'?'muted':null},el('td',null,dday(m.date)),el('td',null,spec.name),el('td',{dir:'ltr'},m.total+'/'+spec.max),
   el('td',null,m.status==='active'?chip(T('pm.band.'+m.band),BTONE[m.band]):T('pm.meas.in_error',{reason:m.error_reason||''}),m.flag&&m.status==='active'?[' ',chip(T('pm.meas.flag_short'),'bad')]:null),
   el('td',null,m.by_name||''),el('td',null,can('measure.write')&&m.status==='active'?el('details',null,el('summary',null,T('pm.meas.mark_error')),
    reasonForm(T('app.field.reason'),T('pm.meas.mark_error'),async r=>{await api('POST','/api/pm/measures/error',{patient:S.pid,measure:m.id,reason:r});say(T('app.common.saved'),'ok');await redo()})):''))});
 parts.push(card(T('pm.meas.history'),'clip',rows.length?wrap(el('table',null,el('thead',null,el('tr',null,['app.field.date','pm.meas.col_measure','pm.meas.col_score','pm.meas.col_band','pm.meas.col_by'].map(h=>el('th',null,T(h))),el('th',null,el('span',{class:'sr'},T('app.vital.col_actions'))))),el('tbody',null,rows))):none(T('pm.meas.none')),
  el('p',{class:'muted'},T('pm.meas.not_diagnosis'))));
 if(S.st.care)parts.push(await mbcCard());
 if(can('measure.write'))parts.push(measForm(ii,redo));
 box.replaceChildren(...parts)}
function measForm(ii,redo){
 const which=sel('instrument',Object.keys(ii.instruments),k=>ii.instruments[k].name,'phq9');const items=el('div');
 const draw=()=>{const sp=ii.instruments[which.value];
  const q=(name,text,opts,req)=>el('fieldset',{class:'pmq'},el('legend',{lang:'en',dir:'ltr'},text),el('div',{class:'pmopts'},opts.map((o,i)=>el('label',{class:'inl',lang:'en',dir:'ltr'},el('input',{type:'radio',name,value:String(i),required:req?true:null}),' '+o+' ('+i+')'))));
  const sx=String((S.chart.demographics||{}).sex||'').toLowerCase(),sk=sp.skip_if_first_zero;
  items.replaceChildren(...[el('p',{lang:'en',dir:'ltr'},el('b',null,sp.title)),el('p',{lang:'en',dir:'ltr'},sp.stem),
   sp.groups.length?field(T('pm.meas.group'),sel('group',sp.groups,v=>TK('pm.mgroup',v),sx.startsWith('m')?'men':'women')):null,
   ...sp.items.map((t,i)=>q('q'+i,(i+1)+'. '+t,sp.item_options[i],!sk||!i)),sk?el('p',{class:'muted'},T('pm.meas.skip_note')):null,
   sp.difficulty?q('difficulty',sp.difficulty,sp.difficulty_options,false):null,
   el('p',{class:'muted',lang:'en',dir:'ltr'},sp.source),el('p',{class:'muted',lang:'en',dir:'ltr'},sp.permission)].filter(Boolean))};
 which.addEventListener('change',draw);draw();
 const out=el('div',{role:'status'});
 const f=el('form',{class:'card',onsubmit:act(async()=>{const sp=ii.instruments[which.value];const ans=[];
   for(let i=0;i<sp.items.length;i++){const x=f.querySelector('input[name=q'+i+']:checked');if(!x&&sp.skip_if_first_zero&&ans[0]===0){ans.push(null);continue}
    if(!x)throw new Error(T('pm.meas.answer_all'));ans.push(Number(x.value))}
   const dq=f.querySelector('input[name=difficulty]:checked'),gq=f.querySelector('[name=group]');
   const m=(await api('POST','/api/pm/measures',{patient:S.pid,instrument:which.value,date:f.querySelector('[name=date]').value,answers:ans,difficulty:dq?Number(dq.value):null,group:gq?gq.value:null})).measure;
   say(T('pm.meas.saved',{name:sp.name,total:m.total,max:sp.max,band:T('pm.band.'+m.band)}),'ok');await redo()})},
  el('h3',{class:'pmh'},ic('plus'),T('pm.meas.add')),el('div',{class:'row'},field(T('pm.meas.col_measure'),which),field(T('app.field.date'),inp('date','date',ii.today,{required:true,max:ii.today}))),
  el('p',{class:'muted'},T('pm.meas.english_note')),items,out,el('button',{type:'submit'},T('pm.meas.score_btn')));
 return f}
// --- treatment plans -----------------------------------------------------------------------------------------
function planCard(p,ii,box){
 const today=ii.today,over=p.status==='active'&&p.review&&p.review<today;
 const sig=p.signature?chip(p.signature_valid?T('pm.plan.signed'):T('app.chip.sig_invalid'),p.signature_valid?'ok':'bad'):chip(T('app.chip.draft'),'warn');
 const c=el('div',{class:['card','wide']},el('div',{class:'ch'},auto('h3',p.title),el('span',{class:'pmacts'},chip(TK('pm.pstatus',p.status),p.status==='active'?'info':''),sig,over?chip(T('pm.plan.review_due'),'warn'):null)),
  el('p',{class:'muted'},T('pm.plan.meta',{v:p.version,start:p.start?dday(p.start):'-',review:p.review?dday(p.review):'-'})),
  p.problems?el('div',null,el('b',null,T('pm.plan.problems')),auto('pre',p.problems,{class:'note'})):null,
  p.strengths?el('div',null,el('b',null,T('pm.plan.strengths')),auto('pre',p.strengths,{class:'note'})):null,
  p.goals.length?el('ol',{class:'pmgoals'},p.goals.map(g=>el('li',null,el('div',{class:'pmacts'},auto('span',g.text),chip(TK('pm.gstatus',g.status),GTONE[g.status])),
   g.objectives.length?el('ul',null,g.objectives.map(o=>el('li',null,auto('span',o.text),' ',o.target?el('span',{class:'muted'},T('pm.plan.by',{date:dday(o.target)})):null,' ',chip(TK('pm.gstatus',o.status),GTONE[o.status])))):null))):none(T('pm.plan.no_goals')),
  p.signature?el('p',{class:p.signature_valid?'signed':'bad'},T('pm.plan.signed_by',{name:p.signed_by_name||p.signed_by,when:fmt(p.signed_at)})):null);
 if(can('plan.write')){
  c.append(el('div',{class:'pmacts'},el('button',{class:'ghost',type:'button',onclick:()=>c.replaceWith(planForm(ii,p,box))},T('app.common.edit'))));
  if(!p.signature){const f=el('form',{class:'sign',onsubmit:act(async()=>{await api('POST','/api/pm/plans/sign',{patient:S.pid,plan_id:p.id,pin:f.querySelector('[name=pin]').value});say(T('pm.plan.signed_ok'),'ok');await reload()})},
   field(T('app.field.your_pin'),pinInput()),el('button',{type:'submit'},ic('lock'),T('pm.plan.sign')));c.append(f)}}
 if((p.history||[]).length)c.append(el('details',null,el('summary',null,T('pm.plan.versions',{n:p.history.length})),el('ul',null,p.history.slice().reverse().map(h=>el('li',null,
  T('pm.plan.version_line',{v:h.version||1,when:fmt(h.updated||h.created),name:h.updated_by_name||h.created_by_name||''})+(h.signature?' - '+T('pm.plan.signed'):''))))));
 return c}
function planForm(ii,p,box){
 p=p||{goals:[],status:'active'};
 const goals=el('div');
 const objRow=o=>{o=o||{};const r=el('div',{class:['row','pmobj']},field(T('pm.plan.objective'),inp('otext','text',o.text,{dir:'auto'})),field(T('pm.plan.target'),inp('otarget','date',o.target)),
   field(T('pm.f.status'),sel('ostatus',ii.goal_statuses,v=>TK('pm.gstatus',v),o.status||'not_started')),el('button',{class:'ghost',type:'button',onclick:()=>r.remove()},T('pm.plan.remove')));return r};
 const goalBox=g=>{g=Object.assign({objectives:[]},g);const objs=el('div');(g.objectives.length?g.objectives:[{}]).forEach(o=>objs.append(objRow(o)));
  const fs=el('fieldset',{class:'pmgoal'},el('legend',null,T('pm.plan.goal')),field(T('pm.plan.goal_text'),el('textarea',{name:'gtext',value:g.text||'',dir:'auto',style:'min-height:60px'})),
   field(T('pm.f.status'),sel('gstatus',ii.goal_statuses,v=>TK('pm.gstatus',v),g.status||'not_started')),objs,
   el('div',{class:'pmacts'},el('button',{class:'ghost',type:'button',onclick:()=>objs.append(objRow())},ic('plus'),T('pm.plan.add_objective')),
    el('button',{class:'ghost',type:'button',onclick:()=>fs.remove()},T('pm.plan.remove_goal'))));return fs};
 (p.goals.length?p.goals:[{}]).forEach(g=>goals.append(goalBox(g)));
 const f=el('form',{class:['card','wide'],onsubmit:act(async()=>{const q=n=>f.querySelector('[name='+n+']').value;
   const plan={title:q('title'),problems:q('problems'),strengths:q('strengths'),start:q('start'),review:q('review'),status:q('status'),
    goals:[...goals.querySelectorAll('.pmgoal')].map(g=>({text:g.querySelector('[name=gtext]').value,status:g.querySelector('[name=gstatus]').value,
     objectives:[...g.querySelectorAll('.pmobj')].map(o=>({text:o.querySelector('[name=otext]').value,target:o.querySelector('[name=otarget]').value,status:o.querySelector('[name=ostatus]').value}))}))};
   await api('POST','/api/pm/plans',{patient:S.pid,plan,plan_id:p.id||null});say(T(p.signature?'pm.plan.new_version':'app.common.saved'),'ok');await reload()})},
  el('h3',null,T(p.id?'pm.plan.edit':'pm.plan.new')),p.signature?el('p',{class:'bad'},T('pm.plan.signed_edit')):null,
  field(T('pm.plan.title_f'),inp('title','text',p.title,{required:true,dir:'auto',maxlength:120})),
  field(T('pm.plan.problems'),el('textarea',{name:'problems',value:p.problems||'',dir:'auto',style:'min-height:60px'})),
  field(T('pm.plan.strengths'),el('textarea',{name:'strengths',value:p.strengths||'',dir:'auto',style:'min-height:60px'})),
  el('div',{class:'row'},field(T('pm.plan.start'),inp('start','date',p.start)),field(T('pm.plan.review'),inp('review','date',p.review)),
   field(T('pm.f.status'),sel('status',ii.plan_statuses,v=>TK('pm.pstatus',v),p.status))),
  goals,el('button',{class:'ghost',type:'button',onclick:()=>goals.append(goalBox())},ic('plus'),T('pm.plan.add_goal')),
  el('div',{class:'pmacts'},el('button',{type:'submit'},T('app.common.save')),el('button',{class:'ghost',type:'button',onclick:act(()=>planTab(box))},T('app.common.cancel'))));
 return f}
async function planTab(box){
 const ii=await info(),ps=(S.chart.treatment_plans||[]).slice().reverse();
 const parts=ps.map(p=>planCard(p,ii,box));
 if(!ps.length)parts.push(el('div',{class:'card'},none(T('pm.plan.none'))));
 if(can('plan.write'))parts.unshift(el('div',{class:'pmacts'},el('button',{type:'button',onclick:()=>box.replaceChildren(planForm(ii,null,box))},ic('plus'),T('pm.plan.new'))));
 if(S.st.care)parts.unshift(await episodeCard());
 box.replaceChildren(...parts)}
// --- billing -------------------------------------------------------------------------------------------------
function invoiceForm(b,prof,inv,box){
 inv=inv||{diagnoses:[],lines:[],currency:prof.currency||cur()};const k=inv.currency||prof.currency||cur();
 const fees=b.fees||[],dl=el('datalist',{id:'pmfees'},fees.map(x=>el('option',{value:x.code},x.description)));
 const dx=el('div'),lines=el('div'),tot=el('p',{class:'muted',role:'status'});
 const recalc=()=>{let t=0;for(const r of lines.querySelectorAll('.pmlinerow')){const u=Number(r.querySelector('[name=units]').value||1),fe=Number(r.querySelector('[name=fee]').value||0);t+=u*Math.round(fe*100)}
  tot.textContent=T('pm.bill.total_preview',{total:cash(t,k)})};
 const dxRow=d=>{d=d||{};const r=el('div',{class:['row','pmdx']},field(T('pm.bill.icd'),inp('code','text',d.code,{dir:'ltr',placeholder:'F41.1',maxlength:8})),
   field(T('pm.bill.dx_desc'),inp('description','text',d.description,{dir:'auto'})),el('button',{class:'ghost',type:'button',onclick:()=>r.remove()},T('pm.plan.remove')));return r};
 const lineRow=l=>{l=l||{};const code=inp('code','text',l.code,{dir:'ltr',list:'pmfees',placeholder:'90837',maxlength:5,required:true}),desc=inp('description','text',l.description,{dir:'auto'}),fee=inp('fee','number',l.fee!=null?(l.fee/100).toFixed(2):'',{min:0,step:'0.01',required:true,dir:'ltr'});
  code.addEventListener('change',()=>{const x=fees.find(f=>f.code===code.value.trim().toUpperCase());if(x){if(!desc.value)desc.value=x.description;if(!fee.value)fee.value=(x.fee/100).toFixed(2);recalc()}});
  const r=el('div',{class:['row','pmlinerow']},field(T('pm.bill.dos'),inp('dos','date',l.dos||S.pmToday,{required:true})),field(T('pm.bill.code'),code),
   field(T('pm.bill.modifiers'),inp('modifiers','text',(l.modifiers||[]).join(' '),{dir:'ltr',maxlength:11})),field(T('pm.bill.units'),inp('units','number',l.units||1,{min:1,max:99})),
   field(T('pm.bill.fee'),fee),field(T('pm.bill.pos'),inp('pos','text',l.pos||'',{dir:'ltr',maxlength:2,placeholder:'11'})),
   field(T('pm.bill.dx_ptr'),inp('dx','text',(l.dx||[]).join(','),{dir:'ltr',placeholder:'1'})),field(T('pm.bill.line_desc'),desc),
   el('button',{class:'ghost',type:'button',onclick:()=>{r.remove();recalc()}},T('pm.plan.remove')));
  r.addEventListener('input',recalc);return r};
 (inv.diagnoses.length?inv.diagnoses:[{}]).forEach(d=>dx.append(dxRow(d)));(inv.lines.length?inv.lines:[{}]).forEach(l=>lines.append(lineRow(l)));
 const provs=STAFF||[];
 const f=el('form',{class:['card','wide'],onsubmit:act(async()=>{const g=(r,n)=>r.querySelector('[name='+n+']').value;
   const body={date:f.querySelector('[name=date]').value,provider:f.querySelector('[name=provider]').value,notes:f.querySelector('[name=notes]').value,currency:k,
    diagnoses:[...dx.querySelectorAll('.pmdx')].map(r=>({code:g(r,'code'),description:g(r,'description')})),
    lines:[...lines.querySelectorAll('.pmlinerow')].map(r=>({dos:g(r,'dos'),code:g(r,'code'),modifiers:g(r,'modifiers'),units:Number(g(r,'units')||1),fee:g(r,'fee'),pos:g(r,'pos'),dx:g(r,'dx'),description:g(r,'description')}))};
   await api('POST','/api/pm/invoices',{patient:S.pid,invoice:body,invoice_id:inv.id||null});say(T('pm.bill.draft_saved'),'ok');await billTab(box)})},
  el('h3',null,T(inv.id?'pm.bill.edit_invoice':'pm.bill.new_invoice')),dl,
  el('div',{class:'row'},field(T('app.field.date'),inp('date','date',inv.date||S.pmToday,{required:true})),
   field(T('pm.bill.provider'),el('select',{name:'provider'},el('option',{value:''},'-'),provs.map(x=>el('option',{value:x.id,selected:x.id===(inv.provider||S.user.id)?true:null},x.display))))),
  el('h3',null,T('pm.bill.diagnoses')),dx,el('button',{class:'ghost',type:'button',onclick:()=>dx.append(dxRow())},ic('plus'),T('pm.bill.add_dx')),
  el('h3',null,T('pm.bill.services')),lines,el('button',{class:'ghost',type:'button',onclick:()=>{lines.append(lineRow());recalc()}},ic('plus'),T('pm.bill.add_line')),
  field(T('pm.f.notes'),inp('notes','text',inv.notes||'',{dir:'auto'})),tot,el('p',{class:'muted'},T('pm.bill.codes_note')),
  el('div',{class:'pmacts'},el('button',{type:'submit'},T('pm.bill.save_draft')),el('button',{class:'ghost',type:'button',onclick:act(()=>billTab(box))},T('app.common.cancel'))));
 recalc();return f}
async function billTab(box){
 const ii=await info();S.pmToday=ii.today;await staff();
 const[b,pr]=await Promise.all([api('GET','/api/pm/billing?patient='+encodeURIComponent(S.pid)),api('GET','/api/pm/billing/profile')]);
 const prof=pr.profile,w=can('bill.write'),redo=()=>billTab(box);
 const invRows=b.invoices.slice().reverse().map(i=>el('tr',{class:i.status==='void'?'muted':null},el('td',{dir:'ltr'},i.number||T('app.chip.draft')),el('td',null,dday(i.date)),
  el('td',{dir:'ltr'},cash(i.total,i.currency)),el('td',{dir:'ltr'},i.status==='issued'?cash(i.paid,i.currency):'-'),
  el('td',null,chip(TK('pm.istatus',i.status),{draft:'warn',issued:'info',void:''}[i.status]),i.status==='void'&&i.void_reason?auto('div',i.void_reason,{class:'meta'}):null),
  el('td',null,el('div',{class:'pmacts'},
   w&&i.status==='draft'?el('button',{class:'ghost',type:'button',onclick:()=>box.replaceChildren(invoiceForm(b,prof,i,box))},T('app.common.edit')):null,
   w&&i.status==='draft'?el('button',{type:'button',onclick:act(async()=>{await api('POST','/api/pm/invoices/issue',{invoice_id:i.id});say(T('pm.bill.issued'),'ok');await redo()})},T('pm.bill.issue')):null,
   i.status==='issued'?el('button',{class:'ghost',type:'button',onclick:act(()=>download('GET','/api/pm/superbill?invoice='+i.id,null,'superbill-'+i.number+'.pdf'))},ic('down'),T('pm.bill.superbill')):null,
   i.status==='issued'?el('button',{class:'ghost',type:'button',onclick:act(()=>download('GET','/api/pm/cms1500?invoice='+i.id,null,'cms1500-'+i.number+'.pdf'))},ic('down'),T('pm.cms.btn')):null,
   w&&i.status!=='void'?el('details',null,el('summary',null,T('pm.bill.void')),reasonForm(T('app.field.reason'),T('pm.bill.void'),async r=>{await api('POST','/api/pm/void',{kind:'invoice',id:i.id,reason:r});say(T('pm.bill.voided'),'ok');await redo()})):null))));
 const payRows=b.payments.slice().reverse().map(y=>el('tr',{class:y.status==='void'?'muted':null},el('td',null,dday(y.date)),el('td',null,TK('pm.pkind',y.kind)),el('td',null,TK('pm.pmethod',y.method)),
  el('td',{dir:'ltr'},cash(y.amount,y.currency)),el('td',{dir:'ltr'},(b.invoices.find(i=>i.id===y.invoice)||{}).number||'-'),auto('td',y.reference||''),
  el('td',null,y.status==='void'?chip(TK('pm.istatus','void')):w?el('details',null,el('summary',null,T('pm.bill.void')),reasonForm(T('app.field.reason'),T('pm.bill.void'),async r=>{await api('POST','/api/pm/void',{kind:'payment',id:y.id,reason:r});say(T('pm.bill.voided'),'ok');await redo()})):'')));
 const k=prof.currency||cur();
 const pf=w?el('form',{class:'card',onsubmit:act(async()=>{const d=formData(pf);await api('POST','/api/pm/payments',{patient:S.pid,payment:Object.assign(d,{currency:k})});say(T('pm.bill.payment_saved'),'ok');await redo()})},
  el('h3',{class:'pmh'},ic('plus'),T('pm.bill.add_payment')),el('div',{class:'row'},field(T('app.field.date'),inp('date','date',ii.today,{required:true,max:ii.today})),
   field(T('pm.bill.amount',{cur:k}),inp('amount','number','',{min:'0.01',step:'0.01',required:true,dir:'ltr'})),field(T('pm.bill.kind'),sel('kind',ii.pay_kinds,v=>TK('pm.pkind',v),'payment')),
   field(T('pm.bill.method'),sel('method',ii.pay_methods,v=>TK('pm.pmethod',v),'card'))),
  el('div',{class:'row'},field(T('pm.bill.for_invoice'),el('select',{name:'invoice'},el('option',{value:''},T('pm.bill.no_invoice')),b.invoices.filter(i=>i.status==='issued').map(i=>el('option',{value:i.id},i.number+' - '+cash(i.total-i.paid,i.currency))))),
   field(T('pm.bill.reference'),inp('reference','text','',{dir:'auto',maxlength:80}))),el('button',{type:'submit'},T('app.common.save'))):null;
 box.replaceChildren(el('div',{class:'card'},el('div',{class:'ch'},el('h3',{class:'pmh'},ic('bill'),T('pm.bill.balance',{amount:bal(b.balance)})),
   w?el('button',{type:'button',onclick:()=>box.replaceChildren(invoiceForm(b,prof,null,box))},ic('plus'),T('pm.bill.new_invoice')):null),
   prof.practice_name?null:el('p',{class:'muted'},T('pm.bill.setup_hint'))),
  card(T('pm.bill.invoices'),null,invRows.length?wrap(el('table',null,el('thead',null,el('tr',null,['pm.bill.col_number','app.field.date','pm.bill.col_total','pm.bill.col_paid','pm.f.status'].map(h=>el('th',null,T(h))),el('th',null,el('span',{class:'sr'},T('app.vital.col_actions'))))),el('tbody',null,invRows))):none(T('pm.bill.no_invoices'))),
  card(T('pm.bill.payments'),null,payRows.length?wrap(el('table',null,el('thead',null,el('tr',null,['app.field.date','pm.bill.kind','pm.bill.method','pm.bill.col_amount','pm.bill.col_number','pm.bill.reference'].map(h=>el('th',null,T(h))),el('th',null,el('span',{class:'sr'},T('app.vital.col_actions'))))),el('tbody',null,payRows))):none(T('pm.bill.no_payments'))),pf,coverageCard(b,pr,redo),...(S.st.care?[await billCare()]:[]))}
async function billing(){
 await strings();const ii=await info();
 const[s,pr]=await Promise.all([api('GET','/api/pm/billing/summary'),api('GET','/api/pm/billing/profile')]);
 const tot=el('div',{class:'card'},el('h2',{class:'pmh'},ic('bill'),T('app.pm.billing')),el('p',null,el('b',null,T('pm.bill.outstanding',{amount:bal(s.totals)}))),
  el('p',{class:'muted'},T('pm.bill.drafts',{n:s.draft_invoices})),el('p',{class:'muted'},T('pm.bill.not_claims')));
 const bl=card(T('pm.bill.balances'),null,s.balances.length?wrap(el('table',null,el('thead',null,el('tr',null,el('th',null,T('pm.f.patient')),el('th',null,T('pm.bill.col_balance')),el('th',null,T('pm.bill.col_drafts')))),
  el('tbody',null,s.balances.map(r=>el('tr',null,el('td',null,openBtn(r.patient,r.patient_name)),el('td',{dir:'ltr'},bal(r.balance)),el('td',null,num(r.drafts))))))):none(T('pm.bill.all_settled')));
 const rp=card(T('pm.bill.recent'),null,s.recent.length?wrap(el('table',null,el('tbody',null,s.recent.map(y=>el('tr',{class:y.status==='void'?'muted':null},el('td',null,dday(y.date)),el('td',null,openBtn(y.patient,y.patient_name)),
  el('td',null,TK('pm.pkind',y.kind)),el('td',{dir:'ltr'},cash(y.amount,y.currency))))))):none(T('pm.bill.no_payments')));
 content(...[tot,el('div',{class:'grid'},bl,rp),...(S.st.care?await careBilling():[]),can('bill.write')?settings(pr,ii):null,can('bill.write')?slidingForm(pr):null].filter(Boolean))}
function settings(pr,ii){
 const p=pr.profile,fees=el('div');
 const feeRow=x=>{x=x||{};const r=el('div',{class:['row','pmfee']},field(T('pm.bill.code'),inp('code','text',x.code,{dir:'ltr',maxlength:5,placeholder:'90837'})),
   field(T('pm.bill.line_desc'),inp('description','text',x.description,{dir:'auto',maxlength:120})),field(T('pm.bill.fee'),inp('fee','number',x.fee!=null?(x.fee/100).toFixed(2):'',{min:0,step:'0.01',dir:'ltr'})),
   el('button',{class:'ghost',type:'button',onclick:()=>r.remove()},T('pm.plan.remove')));return r};
 (pr.fees.length?pr.fees:[{}]).forEach(x=>fees.append(feeRow(x)));
 const provs=el('div',null,pr.staff.map(s=>{const v=pr.providers[s.id]||{};return el('fieldset',{class:'pmprov','data-uid':s.id},el('legend',{dir:'auto'},s.display),
  el('div',{class:'row'},field(T('pm.bill.npi'),inp('npi','text',v.npi,{dir:'ltr',maxlength:10,inputmode:'numeric'})),field(T('pm.bill.credentials'),inp('credentials','text',v.credentials,{dir:'auto'})),
   field(T('pm.bill.license'),inp('license','text',v.license,{dir:'auto'})),field(T('pm.bill.taxonomy'),inp('taxonomy','text',v.taxonomy,{dir:'ltr',maxlength:10}))))}));
 const pf=['practice_name','address','phone','email','tax_id','npi','currency','invoice_prefix','footer'];
 const f=el('form',{class:['card','wide'],onsubmit:act(async()=>{const profile={};for(const k of pf)profile[k]=f.querySelector('[name=p_'+k+']').value;
   const providers={};for(const fs of provs.querySelectorAll('.pmprov')){const g=n=>fs.querySelector('[name='+n+']').value;providers[fs.dataset.uid]={npi:g('npi'),credentials:g('credentials'),license:g('license'),taxonomy:g('taxonomy')}}
   const fl=[...fees.querySelectorAll('.pmfee')].map(r=>({code:r.querySelector('[name=code]').value,description:r.querySelector('[name=description]').value,fee:r.querySelector('[name=fee]').value})).filter(x=>x.code.trim());
   await api('POST','/api/pm/billing/profile',{profile,providers,fees:fl});say(T('app.common.saved'),'ok');await billing()})},
  el('h3',null,T('pm.bill.settings')),
  el('div',{class:'row'},pf.slice(0,4).map(k=>field(T('pm.prof.'+k),inp('p_'+k,'text',p[k]||'',{dir:'auto'})))),
  el('div',{class:'row'},field(T('pm.prof.tax_id'),inp('p_tax_id','text',p.tax_id||'',{dir:'ltr'})),field(T('pm.prof.npi'),inp('p_npi','text',p.npi||'',{dir:'ltr',maxlength:10,inputmode:'numeric'})),
   field(T('pm.prof.currency'),inp('p_currency','text',p.currency||cur(),{dir:'ltr',maxlength:3})),field(T('pm.prof.invoice_prefix'),inp('p_invoice_prefix','text',p.invoice_prefix||'INV',{dir:'ltr',maxlength:8}))),
  field(T('pm.prof.footer'),el('textarea',{name:'p_footer',value:p.footer||'',dir:'auto',style:'min-height:60px'})),
  el('h3',null,T('pm.bill.providers')),provs,el('h3',null,T('pm.bill.fee_schedule')),el('p',{class:'muted'},T('pm.bill.codes_note')),fees,
  el('button',{class:'ghost',type:'button',onclick:()=>fees.append(feeRow())},ic('plus'),T('pm.bill.add_fee')),el('button',{type:'submit'},T('app.common.save')));
 return f}
// --- Today -------------------------------------------------------------------------------------------------
async function today(){
 await strings();const d=(await api('GET','/api/pm/today')).today;const cards=[];
 const row=(pid,name,sub,extra)=>el('li',null,el('div',{class:'it'},el('div',{class:'t'},openBtn(pid,name)),sub?auto('div',sub,{class:'sub'}):null),extra||null);
 if(d.appts)cards.push(el('div',{class:['card','wide']},el('div',{class:'ch'},el('h3',{class:'pmh'},ic('calendar'),T('pm.today.appts',{n:d.appts.length}))),
  d.appts.length?el('ul',{class:'items'},d.appts.map(a=>apptRow(a,true,today))):el('div',{class:'empty'},el('p',null,T('pm.today.no_appts')),
   can('sched.write')?el('button',{type:'button',class:'primary',id:'todayBook',onclick:act(async()=>{here([...document.querySelectorAll('nav.side [role=group] button')].find(b=>b.textContent===T('app.pm.calendar'))||null);await inner(cal)})},ic('calendar'),T('app.pm.calendar')):null)));
 if(d.drafts)cards.push(card(T('pm.today.drafts',{n:d.drafts.length}),'note',d.drafts.length?el('ul',{class:'items'},d.drafts.map(x=>row(x.patient,x.patient_name,x.title+' - '+fmt(x.created)))):none(T('pm.today.none'))));
 if(d.cosign)cards.push(card(T('pm.today.cosign',{n:d.cosign.length}),'lock',d.cosign.length?el('ul',{class:'items'},d.cosign.map(x=>row(x.patient,x.patient_name,x.title+' - '+fmt(x.signed_at)))):none(T('pm.today.none'))));
 if(d.reviews)cards.push(card(T('pm.today.reviews',{n:d.reviews.length}),'clip',d.reviews.length?el('ul',{class:'items'},d.reviews.map(x=>row(x.patient,x.patient_name,x.title+' - '+dday(x.review),x.overdue?chip(T('pm.plan.review_due'),'warn'):null))):none(T('pm.today.none'))));
 if(d.balances)cards.push(card(T('pm.today.balances'),'bill',d.balances.length?el('ul',{class:'items'},d.balances.map(x=>row(x.patient,x.patient_name,bal(x.balance)))):none(T('pm.bill.all_settled'))));
 content(el('div',{class:'card'},el('h2',{class:'pmh'},ic('clock'),T('pm.today.title')),el('p',{class:'muted'},wday(d.date)+' '+dday(d.date))),cards.length?el('div',{class:'grid'},cards):el('div',{class:'card'},none(T('pm.today.nothing'))))}
// --- practice dashboard and reminders ------------------------------------------------------------------------
function pct(r){return r==null?'-':num(r)+'%'}
async function dash(range){
 await strings();const ii=await info();range=range||{from:addDays(ii.today,-89),to:ii.today};
 const d=await api('GET','/api/pm/dashboard?'+new URLSearchParams(range).toString());const cards=[];
 const line=t=>el('p',null,t);
 if(d.money){const ks=Object.keys(d.money);cards.push(card(T('pm.dash.money'),'bill',ks.length?ks.map(k=>{const m=d.money[k];
  return el('div',null,line(T('pm.dash.billed',{amount:cash(m.billed,k)})),line(T('pm.dash.collected',{amount:cash(m.collected,k)})),
   line(T('pm.dash.written_off',{amount:cash(m.written_off,k)})),line(T('pm.dash.outstanding',{amount:cash(m.outstanding,k)})),
   m.per_visit!=null?line(T('pm.dash.per_visit',{amount:cash(m.per_visit,k)})):null)}):none(T('pm.bill.no_payments'))))}
 if(d.attendance){const a=d.attendance;cards.push(card(T('pm.dash.attendance'),'calendar',line(T('pm.dash.attended',{n:a.attended})),
  line(T('pm.dash.no_show',{n:a.no_show,rate:pct(a.no_show_rate)})),line(T('pm.dash.late',{n:a.late_cancel,rate:pct(a.late_cancel_rate)})),
  line(T('pm.dash.cancelled',{n:a.cancelled}))))}
 if(d.clients){const c=d.clients;cards.push(card(T('pm.dash.clients'),'user',line(T('pm.dash.seen',{n:c.seen})),line(T('pm.dash.new',{n:c.new})),line(T('pm.dash.returning',{n:c.returning}))))}
 if(d.outcomes)cards.push(card(T('pm.dash.outcomes'),'chart',...Object.values(d.outcomes).map(o=>line(o.clients?T('pm.dash.outcome_line',{name:o.name,n:o.clients,
  change:(o.mean_change>0?'+':'')+num(o.mean_change),up:o.improved,down:o.worse}):T('pm.dash.outcome_none',{name:o.name}))),el('p',{class:'muted'},T('pm.dash.outcome_note'))));
 const per=sel('days',[30,90,365],v=>T('pm.dash.days',{n:v}),'');
 const f=el('form',{class:'pmline',onsubmit:act(async()=>{await dash({from:f.querySelector('[name=from]').value,to:f.querySelector('[name=to]').value})})},
  el('div',{class:'row'},field(T('pm.acc.from'),inp('from','date',d.from,{required:true})),field(T('pm.acc.to'),inp('to','date',d.to,{required:true})),field(T('pm.dash.period'),per)),
  el('button',{type:'submit'},T('pm.dash.show')));
 per.prepend(el('option',{value:'',selected:true},'-'));
 per.addEventListener('change',act(async()=>{if(per.value)await dash({from:addDays(ii.today,1-Number(per.value)),to:ii.today})}));
 content(el('div',{class:'card'},el('h2',{class:'pmh'},ic('chart'),T('pm.dash.title')),el('p',{class:'muted'},T('pm.dash.intro')),f),el('div',{class:'grid'},cards))}
async function remCard(st){// Calendar: the day's reminder list. People call or text from their own phone.
 const ii=await info(),box=el('div',{class:'card'});
 const draw=async day=>{const r=await api('GET','/api/pm/reminders?'+new URLSearchParams({date:day,clinician:S.pmClin||''}).toString());
  const w=can('sched.write');
  const rows=r.appts.map(a=>{const last=(a.reminders||[]).slice(-1)[0],ta=el('textarea',{readonly:true,dir:'auto',style:'min-height:60px','aria-label':T('pm.rem.text')+' - '+a.patient_name},a.text);
   const out=sel('outcome',r.outcomes,v=>TK('pm.remout',v),'called',{'aria-label':T('pm.rem.outcome')+' - '+a.patient_name});
   return el('li',null,el('div',{class:'it'},el('div',{class:'t'},el('span',{dir:'ltr'},a.time),' ',openBtn(a.patient,a.patient_name)),
     el('div',{class:'sub'},a.phone?auto('span',a.phone,{dir:'ltr'}):T('pm.rem.no_phone'),' · ',a.clinician_name),
     last?el('div',{class:'meta'},T('pm.rem.last',{outcome:TK('pm.remout',last.outcome),when:fmt(last.at),name:last.by_name||''})):null,ta,
     el('div',{class:'pmacts'},el('button',{class:'ghost',type:'button',onclick:act(async()=>{await navigator.clipboard.writeText(a.text);say(T('pm.rem.copied'),'ok')})},T('pm.rem.copy')),
      w?out:null,w?el('button',{type:'button',onclick:act(async()=>{await api('POST','/api/pm/reminders',{id:a.id,date:a.date,outcome:out.value});say(T('pm.rem.saved'),'ok');await draw(day)})},T('pm.rem.save')):null)))});
  const dayIn=inp('rem_day','date',day,{required:true});dayIn.addEventListener('change',act(()=>draw(dayIn.value)));
  const tpl=el('textarea',{name:'rem_text',dir:'auto',style:'min-height:60px'},r.template);
  box.replaceChildren(el('h3',{class:'pmh'},ic('clock'),T('pm.rem.title')),el('p',{class:'muted'},T('pm.rem.intro')),field(T('pm.rem.day'),dayIn),
   rows.length?el('ul',{class:'items'},rows):none(T('pm.rem.none')),
   w?el('details',null,el('summary',null,T('pm.rem.template')),el('p',{class:'muted'},T('pm.rem.template_help')),field(T('pm.rem.template'),tpl),
    el('button',{type:'button',onclick:act(async()=>{await api('POST','/api/pm/reminders/text',{template:tpl.value});say(T('app.common.saved'),'ok');await draw(day)})},T('app.common.save'))):null)};
 await draw(addDays(ii.today,1));return box}
// --- letters, waitlist, groups ---------------------------------------------------------------------------------
async function letterTab(box){
 const ii=await info();const r=await api('GET','/api/pm/letters?patient='+encodeURIComponent(S.pid));const redo=()=>letterTab(box),w=can('note.sign');
 const form=x=>{x=x||{kind:'twimc'};const body=el('textarea',{name:'body',value:x.body||'',dir:'auto',style:'min-height:180px',required:true});
  const kind=sel('kind',r.kinds,v=>TK('pm.lkind',v),x.kind);
  kind.addEventListener('change',()=>{if(!body.value.trim()||body.dataset.auto){body.value=T('pm.lbody.'+kind.value);body.dataset.auto='1'}});
  if(!x.id){body.value=T('pm.lbody.'+x.kind);body.dataset.auto='1'}body.addEventListener('input',()=>{delete body.dataset.auto});
  const f=el('form',{class:['card','wide'],onsubmit:act(async()=>{const g=n=>f.querySelector('[name='+n+']').value;
    await api('POST','/api/pm/letters',{patient:S.pid,letter_id:x.id||null,letter:{kind:g('kind'),recipient:g('recipient'),subject:g('subject'),body:g('body'),date:g('date')}});say(T('app.common.saved'),'ok');await redo()})},
   el('h3',null,T(x.id?'pm.letter.edit':'pm.letter.new')),el('div',{class:'row'},field(T('pm.letter.kind'),kind),field(T('app.field.date'),inp('date','date',x.date||ii.today,{required:true,max:ii.today}))),
   field(T('pm.letter.recipient'),el('textarea',{name:'recipient',value:x.recipient||'',dir:'auto',style:'min-height:60px'})),field(T('pm.letter.subject'),inp('subject','text',x.subject||'',{dir:'auto',maxlength:200})),
   field(T('pm.letter.body'),body),el('p',{class:'muted'},T('pm.letter.hint')),
   el('div',{class:'pmacts'},el('button',{type:'submit'},T('app.note.save_draft')),el('button',{class:'ghost',type:'button',onclick:act(redo)},T('app.common.cancel'))));return f};
 const cardOf=x=>{const ok=x.signature_valid,err=x.status==='entered_in_error';
  const c=el('div',{class:['card','wide',err?'muted':'']},auto('h3',x.subject||TK('pm.lkind',x.kind)),el('div',{class:'pmacts'},chip(TK('pm.lkind',x.kind)),
    err?chip(T('pm.meas.in_error',{reason:x.error_reason||''})):x.signature?chip(T(ok?'pm.form.seal_ok':'app.chip.sig_invalid'),ok?'ok':'bad'):chip(T('app.chip.draft'),'warn')),
   el('p',{class:'muted'},dday(x.date)+' · '+(x.author_name||'')),x.recipient?auto('pre',x.recipient,{class:'note'}):null,auto('pre',x.body,{class:'note'}));
  if(x.signature)c.append(el('p',{class:ok?'signed':'bad'},T('pm.letter.signed_line',{name:x.signed_by_name||x.signed_by,when:fmt(x.signed_at),code:x.content_sha.slice(0,12)})),
   el('div',{class:'pmacts'},el('button',{class:'ghost',type:'button',onclick:act(()=>download('GET','/api/pm/letters/pdf?patient='+encodeURIComponent(S.pid)+'&letter='+x.id,null,'letter-'+x.id+'.pdf'))},ic('down'),T('pm.form.pdf'))));
  else if(w&&x.author===S.user.id&&!err){const sf=el('form',{class:'sign',onsubmit:act(async()=>{await api('POST','/api/pm/letters/sign',{patient:S.pid,letter_id:x.id,pin:sf.querySelector('[name=pin]').value});say(T('pm.letter.signed_ok'),'ok');await redo()})},
    field(T('app.field.your_pin'),pinInput()),el('button',{type:'submit'},ic('lock'),T('pm.letter.sign')));
   c.append(el('div',{class:'pmacts'},el('button',{class:'ghost',type:'button',onclick:()=>c.replaceWith(form(x))},T('app.common.edit'))),sf)}
  if(w&&!err)c.append(el('details',null,el('summary',null,T('pm.meas.mark_error')),reasonForm(T('app.field.reason'),T('pm.meas.mark_error'),async y=>{await api('POST','/api/pm/letters/error',{patient:S.pid,letter_id:x.id,reason:y});say(T('app.common.saved'),'ok');await redo()})));
  return c};
 const ls=r.letters.slice().reverse();
 box.replaceChildren(...[w?el('div',{class:'pmacts'},el('button',{type:'button',onclick:()=>box.replaceChildren(form(null))},ic('plus'),T('pm.letter.new'))):null,
  ...ls.map(cardOf),ls.length?null:el('div',{class:'card'},none(T('pm.letter.none')))].filter(Boolean))}
async function waitCard(st){
 const r=await api('GET','/api/pm/waitlist'),w=can('sched.write'),box=el('div',{class:'card'});
 const redo=async()=>box.replaceWith(await waitCard(st));
 const rows=r.entries.filter(e=>e.status==='waiting').map(e=>el('li',null,el('div',{class:'it'},el('div',{class:'t'},openBtn(e.patient,e.patient_name)),
   el('div',{class:'sub'},[e.clinician?(st.find(x=>x.id===e.clinician)||{}).display:T('pm.wait.anyone'),e.preferences].filter(Boolean).join(' · ')),
   e.note?auto('div',e.note,{class:'meta'}):null,el('div',{class:'meta'},T('pm.wait.added',{when:fmt(e.added)}))),
  w?el('div',{class:'pmacts'},['booked','removed'].map(s=>el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/pm/waitlist/status',{id:e.id,status:s});say(T('app.common.saved'),'ok');await redo()})},TK('pm.wstatus',s)))):null));
 let add=null;
 if(w){const pts=await patients();add=el('details',null,el('summary',null,T('pm.wait.add')),el('form',{class:'pmline',onsubmit:act(async ev=>{const d=formData(ev.target);await api('POST','/api/pm/waitlist',{patient:d.wpatient,clinician:d.wclin,preferences:d.prefs,note:d.wnote});say(T('app.common.saved'),'ok');await redo()})},
  el('div',{class:'row'},field(T('pm.f.patient'),el('select',{name:'wpatient',required:true},pts.map(p=>el('option',{value:p.id},p.name+' ('+dday(p.dob)+')')))),
   field(T('pm.f.clinician'),sel('wclin',[''].concat(st.map(x=>x.id)),v=>v?st.find(x=>x.id===v).display:T('pm.wait.anyone'),''))),
  field(T('pm.wait.prefs'),inp('prefs','text','',{dir:'auto',maxlength:300})),field(T('pm.f.notes'),inp('wnote','text','',{dir:'auto',maxlength:300})),el('button',{type:'submit'},T('app.common.save'))))}
 box.append(el('h3',{class:'pmh'},ic('clip'),T('pm.wait.title',{n:rows.length})),rows.length?el('ul',{class:'items'},rows):none(T('pm.wait.none')),...[add].filter(Boolean));
 return box}
async function groups(){
 await strings();const ii=await info();const[r,st]=await Promise.all([api('GET','/api/pm/groups'),staff()]);const pts=can('sched.write')?await patients():[];
 const w=can('sched.write'),ns=can('note.sign');
 const gform=g=>{g=g||{members:[]};const f=el('form',{class:['card','wide'],onsubmit:act(async()=>{const q=n=>f.querySelector('[name='+n+']').value;
    await api('POST','/api/pm/groups',{group_id:g.id||null,group:{name:q('gname'),clinician:q('gclin'),status:q('gstatus'),members:[...f.querySelectorAll('[name=gm]:checked')].map(i=>i.value)}});say(T('app.common.saved'),'ok');await groups()})},
   el('h3',null,T(g.id?'pm.group.edit':'pm.group.new')),el('div',{class:'row'},field(T('pm.group.name'),inp('gname','text',g.name||'',{required:true,dir:'auto',maxlength:120})),
    field(T('pm.f.clinician'),sel('gclin',st.map(x=>x.id),v=>st.find(x=>x.id===v).display,g.clinician||(st[0]||{}).id)),field(T('pm.f.status'),sel('gstatus',['active','closed'],v=>TK('pm.gstat',v),g.status||'active'))),
   el('fieldset',{class:'pmq'},el('legend',null,T('pm.group.members')),el('div',{class:'pmopts'},pts.map(p=>el('label',{class:'inl'},el('input',{type:'checkbox',name:'gm',value:p.id,checked:g.members.includes(p.id)?true:null}),' ',auto('span',p.name))))),
   el('div',{class:'pmacts'},el('button',{type:'submit'},T('app.common.save')),el('button',{class:'ghost',type:'button',onclick:act(groups)},T('app.common.cancel'))));return f};
 const gcard=g=>{const c=el('div',{class:['card','wide']},auto('h3',g.name),el('div',{class:'pmacts'},chip(TK('pm.gstat',g.status),g.status==='active'?'ok':''),chip(g.clinician_name)),
   el('p',{class:'muted'},T('pm.group.member_list',{names:g.members.map(p=>g.member_names[p]||p).join(', ')})));
  if(w&&g.status==='active'){const msg=el('p',{role:'alert'});const bf=el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(bf);const b={group_id:g.id,appt:{date:d.bdate,time:d.btime,minutes:Number(d.bmin)}};
     if(bf.dataset.force)b.appt.allow_overlap=true;const[s,j]=await post('/api/pm/groups/book',b);
     if(s===409){bf.dataset.force='1';msg.className='bad';msg.textContent=trErr(j.error)+' '+T('pm.appt.again');return}if(!j.ok)throw new Error(trErr(j.error||('HTTP '+s)));
     say(T('pm.appt.booked',{n:j.created.length}),'ok')})},
    el('div',{class:'row'},field(T('app.field.date'),inp('bdate','date',ii.today,{required:true})),field(T('pm.f.time'),inp('btime','time','17:00',{required:true})),field(T('pm.f.minutes'),inp('bmin','number','90',{min:5,max:480,required:true}))),msg,el('button',{type:'submit'},T('pm.group.book')));
   c.append(el('details',null,el('summary',null,T('pm.group.book')),bf),el('details',null,el('summary',null,T('app.common.edit')),gform(g)))}
  if(ns){const nf=el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(nf);const ind={};for(const t of nf.querySelectorAll('textarea[data-pid]'))if(t.value.trim())ind[t.dataset.pid]=t.value;
     const ms=[...nf.querySelectorAll('[name=nm]:checked')].map(i=>i.value);
     const j=await api('POST','/api/pm/groups/note',{group_id:g.id,date:d.ndate,title:d.ntitle,text:d.ntext,members:ms,individual:ind,pin:d.pin});say(T('pm.group.noted',{n:j.notes.length}),'ok');await groups()})},
    el('p',{class:'muted'},T('pm.group.note_help')),el('div',{class:'row'},field(T('app.field.date'),inp('ndate','date',ii.today,{required:true,max:ii.today})),field(T('app.field.title'),inp('ntitle','text',g.name,{dir:'auto'}))),
    field(T('pm.group.shared'),el('textarea',{name:'ntext',dir:'auto',style:'min-height:120px',required:true})),
    el('fieldset',{class:'pmq'},el('legend',null,T('pm.group.attended')),...g.members.map(p=>el('div',null,el('label',{class:'inl'},el('input',{type:'checkbox',name:'nm',value:p,checked:true}),' ',auto('span',g.member_names[p]||p)),
     el('textarea',{'data-pid':p,dir:'auto',style:'min-height:40px','aria-label':T('pm.group.own',{name:g.member_names[p]||p})})))),
    field(T('app.field.your_pin'),pinInput()),el('button',{type:'submit'},ic('lock'),T('pm.group.sign')));
   c.append(el('details',null,el('summary',null,T('pm.group.note')),nf))}
  return c};
 content(...[el('div',{class:'card'},el('h2',{class:'pmh'},ic('users'),T('app.pm.groups')),el('p',{class:'muted'},T('pm.group.intro')),w?el('button',{type:'button',onclick:()=>{const x=gform(null);document.getElementById('content').append(x);x.scrollIntoView()}},ic('plus'),T('pm.group.new')):null),
  ...r.groups.map(gcard),r.groups.length?null:el('div',{class:'card'},none(T('pm.group.none')))].filter(Boolean))}
// --- sliding scale, coverage, CMS-1500 ---------------------------------------------------------------------------
function slidingForm(pr){
 const rows=el('div');const row=t=>{t=t||{};const r=el('div',{class:['row','pmtier'],'data-id':t.id||''},field(T('pm.slide.name'),inp('tname','text',t.name||'',{dir:'auto',maxlength:60})),
   field(T('pm.slide.percent'),inp('tpct','number',t.percent!=null?t.percent:'',{min:0,max:100,dir:'ltr'})),el('button',{class:'ghost',type:'button',onclick:()=>r.remove()},T('pm.plan.remove')));return r};
 (pr.tiers.length?pr.tiers:[{}]).forEach(t=>rows.append(row(t)));
 const f=el('form',{class:'card',onsubmit:act(async()=>{const tiers=[...rows.querySelectorAll('.pmtier')].map(r=>({id:r.dataset.id,name:r.querySelector('[name=tname]').value,percent:r.querySelector('[name=tpct]').value||0}));
   await api('POST','/api/pm/billing/sliding',{tiers});say(T('app.common.saved'),'ok');await billing()})},
  el('h3',null,T('pm.slide.title')),el('p',{class:'muted'},T('pm.slide.help')),rows,el('button',{class:'ghost',type:'button',onclick:()=>rows.append(row())},ic('plus'),T('pm.slide.add')),el('button',{type:'submit'},T('app.common.save')));
 return f}
function coverageCard(b,pr,redo){
 const c=b.coverage||{},w=can('bill.write');
 const tier=w&&b.tiers.length?el('div',{class:'pmline'},field(T('pm.slide.tier'),sel('tier',[''].concat(b.tiers.map(t=>t.id)),v=>v?(x=>x.name+' (-'+x.percent+'%)')(b.tiers.find(t=>t.id===v)):T('pm.slide.full_fee'),b.tier||'',{})),
  el('button',{class:'ghost',type:'button',onclick:act(async ev=>{await api('POST','/api/pm/billing/tier',{patient:S.pid,tier:ev.target.closest('.pmline').querySelector('[name=tier]').value});say(T('app.common.saved'),'ok');await redo()})},T('app.common.save'))):null;
 const g=(n,l,v,x)=>field(T(l),inp(n,'text',v||'',Object.assign({dir:'auto'},x||{})));
 const f=w?el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(f);d.signature_on_file=f.querySelector('[name=signature_on_file]').checked;await api('POST','/api/pm/billing/coverage',{patient:S.pid,coverage:d});say(T('app.common.saved'),'ok');await redo()})},
  el('div',{class:'row'},g('payer_name','pm.cov.payer_name',c.payer_name),g('payer_id','pm.cov.payer_id',c.payer_id,{dir:'ltr'}),field(T('pm.cov.plan_type'),sel('plan_type',pr.plan_types,v=>TK('pm.ptype',v),c.plan_type||'group'))),
  g('payer_address','pm.cov.payer_address',c.payer_address),
  el('div',{class:'row'},g('member_id','pm.cov.member_id',c.member_id,{dir:'ltr'}),g('group','pm.cov.group',c.group,{dir:'ltr'}),field(T('pm.cov.relationship'),sel('relationship',pr.relationships,v=>TK('pm.rel',v),c.relationship||'self'))),
  el('div',{class:'row'},g('insured_name','pm.cov.insured_name',c.insured_name),field(T('pm.cov.insured_dob'),inp('insured_dob','date',c.insured_dob||''))),g('insured_address','pm.cov.insured_address',c.insured_address),
  el('label',{class:'inl'},el('input',{type:'checkbox',name:'signature_on_file',checked:c.signature_on_file?true:null}),' '+T('pm.cov.sof')),el('button',{type:'submit'},T('app.common.save'))):null;
 return card(T('pm.cov.title'),'bill',...[tier,el('p',{class:'muted'},T('pm.cms.not_validated')),w?el('details',null,el('summary',null,T('pm.cov.edit')),f):null].filter(Boolean))}
// --- intake and consent forms ------------------------------------------------------------------------------
async function forms(){// Practice > Forms: the clinic's templates (each save is a new version; signed forms keep theirs)
 await strings();const r=await api('GET','/api/pm/forms/templates'),box=el('div');
 const edit=t=>{box.replaceChildren(designForm(r,t));box.scrollIntoView({block:'start'})};
 const list=card(T('pm.form.templates'),'clip',r.templates.length?el('ul',{class:'items'},r.templates.map(t=>el('li',null,
  el('div',{class:'it'},auto('div',t.name,{class:'t'}),el('div',{class:'sub'},[TK('pm.fkind',t.kind),T('pm.form.version',{v:t.version}),T('pm.form.n_fields',{n:t.fields.length})].join(' · '))),
  el('span',{class:'pmacts'},chip(TK('pm.fstatus',t.status),t.status==='active'?'ok':''),can('form.design')?[el('button',{class:'ghost',type:'button',onclick:()=>edit(t)},T('app.common.edit')),
   el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/pm/forms/templates/status',{template_id:t.id,status:t.status==='active'?'retired':'active'});say(T('app.common.saved'),'ok');await forms()})},T(t.status==='active'?'pm.form.retire':'pm.form.restore'))]:null)))):none(T('pm.form.none')));
 content(el('div',{class:'card'},el('h2',{class:'pmh'},ic('clip'),T('app.pm.forms')),el('p',{class:'muted'},T('pm.form.intro_help')),
  can('form.design')?el('button',{type:'button',onclick:()=>edit(null)},ic('plus'),T('pm.form.new')):null),list,box)}
function designForm(r,t){
 t=t||{kind:'intake',fields:[]};const rows=el('div');
 const fieldRow=x=>{x=x||{type:'text'};const ty=sel('type',r.field_types,v=>TK('pm.ftype',v),x.type);
  const ow=field(T('pm.form.options'),el('textarea',{name:'options',value:(x.options||[]).join('\n'),dir:'auto',style:'min-height:60px'}));
  const sync=()=>{ow.hidden=!['choice','multi'].includes(ty.value)};ty.addEventListener('change',sync);
  const fs=el('fieldset',{class:['pmgoal','pmfield'],'data-key':x.key||''},el('legend',null,T('pm.form.field')),
   el('div',{class:'row'},field(T('pm.form.type'),ty),field(T('pm.form.label'),el('textarea',{name:'label',value:x.label||'',dir:'auto',style:'min-height:40px'}))),ow,
   el('label',{class:'inl'},el('input',{type:'checkbox',name:'required',checked:x.required?true:null}),' '+T('pm.form.required')),
   el('div',{class:'pmacts'},el('button',{class:'ghost',type:'button',onclick:()=>{const p=fs.previousElementSibling;if(p)p.before(fs)}},T('pm.form.up')),
    el('button',{class:'ghost',type:'button',onclick:()=>fs.remove()},T('pm.plan.remove'))));
  sync();return fs};
 (t.fields.length?t.fields:[{}]).forEach(x=>rows.append(fieldRow(x)));
 const f=el('form',{class:['card','wide'],onsubmit:act(async()=>{const q=n=>f.querySelector('[name='+n+']').value;
   const tpl={name:q('name'),kind:q('kind'),intro:q('intro'),statement:q('statement'),fields:[...rows.querySelectorAll('.pmfield')].map(fs=>({key:fs.dataset.key,
    type:fs.querySelector('[name=type]').value,label:fs.querySelector('[name=label]').value,required:fs.querySelector('[name=required]').checked,options:fs.querySelector('[name=options]').value}))};
   const x=(await api('POST','/api/pm/forms/templates',{template:tpl,template_id:t.id||null})).template;say(T('pm.form.saved_v',{v:x.version}),'ok');await forms()})},
  el('h3',null,T(t.id?'pm.form.edit':'pm.form.new')),
  el('div',{class:'row'},field(T('pm.form.name'),inp('name','text',t.name,{required:true,dir:'auto',maxlength:120})),field(T('pm.form.kind'),sel('kind',r.kinds,v=>TK('pm.fkind',v),t.kind))),
  field(T('pm.form.intro'),el('textarea',{name:'intro',value:t.intro||'',dir:'auto',style:'min-height:80px'})),
  el('h3',null,T('pm.form.fields')),rows,el('button',{class:'ghost',type:'button',onclick:()=>rows.append(fieldRow())},ic('plus'),T('pm.form.add_field')),
  field(T('pm.form.statement'),el('textarea',{name:'statement',value:t.statement||'',dir:'auto',style:'min-height:60px'})),
  t.id?el('p',{class:'muted'},T('pm.form.new_version_note')):null,
  el('div',{class:'pmacts'},el('button',{type:'submit'},T('app.common.save')),el('button',{class:'ghost',type:'button',onclick:act(forms)},T('app.common.cancel'))));
 return f}
function answerText(x,v){if(x.type==='multi')return (v||[]).join(', ');if(x.type==='yesno'&&v)return T('pm.form.'+v);if(x.type==='date'&&v)return dday(v);return v==null||v===''?'-':String(v)}
function fillForm(sp,fm,redo){// sp: a template, or (amending) the completed form with the fields it was signed with
 const A=(fm&&fm.answers)||{};
 const ctl=x=>{const v=A[x.key],n='a_'+x.key,req=x.required?true:null,lbl=x.label+(x.required?' *':'');
  if(x.type==='statement')return auto('p',x.label);
  if(['yesno','choice','multi'].includes(x.type)){const multi=x.type==='multi',vals=x.type==='yesno'?['yes','no']:x.options;
   return el('fieldset',{class:'pmq'},auto('legend',lbl),el('div',{class:'pmopts'},vals.map(o=>el('label',{class:'inl'},
    el('input',{type:multi?'checkbox':'radio',name:n,value:o,required:multi?null:req,checked:(multi?(v||[]).includes(o):v===o)?true:null}),' ',auto('span',x.type==='yesno'?T('pm.form.'+o):o)))))}
  const c=x.type==='textarea'?el('textarea',{name:n,value:v||'',dir:'auto',required:req,style:'min-height:60px'}):
   inp(n,{date:'date',number:'number'}[x.type]||'text',v||'',{dir:x.type==='text'?'auto':'ltr',required:req,step:x.type==='number'?'any':null});
  return field(lbl,c)};
 const f=el('form',{class:['card','wide'],onsubmit:act(async()=>{const answers={},g=n=>f.querySelector('[name="'+n+'"]');
   for(const x of sp.fields){const n='a_'+x.key;if(x.type==='statement')continue;
    if(x.type==='multi')answers[x.key]=[...f.querySelectorAll('[name="'+n+'"]:checked')].map(i=>i.value);
    else if(x.type==='yesno'||x.type==='choice'){const c=f.querySelector('[name="'+n+'"]:checked');answers[x.key]=c?c.value:''}else answers[x.key]=g(n).value}
   await api('POST','/api/pm/forms',{patient:S.pid,template:fm?null:sp.id,form:fm?fm.id:null,answers,signer_name:g('signer_name').value,
    signer_role:g('signer_role').value,signed_date:g('signed_date').value,pin:g('pin').value});say(T('pm.form.saved'),'ok');await redo()})},
  auto('h3',sp.name),fm?el('p',{class:'muted'},T('pm.form.amend_note')):null,sp.intro?auto('pre',sp.intro,{class:'note'}):null,...sp.fields.map(ctl),
  el('fieldset',{class:'pmgoal'},el('legend',null,T('pm.form.signature')),sp.statement?auto('p',sp.statement):null,
   el('div',{class:'row'},field(T('pm.form.signer'),inp('signer_name','text',fm?fm.signer_name:'',{required:true,dir:'auto',maxlength:120,autocomplete:'off'})),
    field(T('pm.form.signer_role'),sel('signer_role',['client','guardian','representative'],v=>TK('pm.srole',v),fm?fm.signer_role:'client')),
    field(T('pm.form.signed_date'),inp('signed_date','date',S.pmToday,{required:true,max:S.pmToday}))),
   field(T('pm.form.witness_pin'),pinInput())),
  el('div',{class:'pmacts'},el('button',{type:'submit'},ic('lock'),T('pm.form.sign_btn')),el('button',{class:'ghost',type:'button',onclick:act(redo)},T('app.common.cancel'))));
 return f}
function formCard(fm,redo){
 const ok=fm.signature_valid,err=fm.status!=='active',w=can('form.write')&&!err;
 const c=el('div',{class:['card','wide',err?'muted':'']},auto('h3',fm.name),el('div',{class:'pmacts'},chip(TK('pm.fkind',fm.kind)),
   err?chip(T('pm.meas.in_error',{reason:fm.error_reason||''})):chip(T(ok?'pm.form.seal_ok':'app.chip.sig_invalid'),ok?'ok':'bad')),
  el('p',{class:'muted'},T('pm.form.version',{v:fm.version})),
  el('dl',{class:'pmans'},fm.fields.filter(x=>x.type!=='statement').flatMap(x=>[auto('dt',x.label),auto('dd',answerText(x,(fm.answers||{})[x.key]))])),
  el('p',{class:ok?'signed':'bad'},T('pm.form.signed_line',{name:fm.signer_name,role:TK('pm.srole',fm.signer_role),date:dday(fm.signed_date),staff:fm.witness_name||fm.witness,when:fmt(fm.at)})),
  el('div',{class:'pmacts'},el('button',{class:'ghost',type:'button',onclick:act(()=>download('GET','/api/pm/forms/pdf?patient='+encodeURIComponent(S.pid)+'&form='+fm.id,null,'form-'+fm.id+'.pdf'))},ic('down'),T('pm.form.pdf')),
   w?el('button',{class:'ghost',type:'button',onclick:()=>c.replaceWith(fillForm(fm,fm,redo))},T('pm.form.amend')):null));
 if(w)c.append(el('details',null,el('summary',null,T('pm.meas.mark_error')),reasonForm(T('app.field.reason'),T('pm.meas.mark_error'),async x=>{await api('POST','/api/pm/forms/error',{patient:S.pid,form:fm.id,reason:x});say(T('app.common.saved'),'ok');await redo()})));
 if(fm.history.length)c.append(el('details',null,el('summary',null,T('pm.plan.versions',{n:fm.history.length})),el('ul',null,fm.history.slice().reverse().map(h=>el('li',null,
  T('pm.plan.version_line',{v:h.version,when:fmt(h.at),name:h.signer_name})+' - '+T(h.signature_valid?'pm.form.seal_ok':'app.chip.sig_invalid'))))));
 return c}
async function formTab(box){
 const ii=await info();S.pmToday=ii.today;const redo=()=>formTab(box);
 const[r,tp]=await Promise.all([api('GET','/api/pm/forms?patient='+encodeURIComponent(S.pid)),api('GET','/api/pm/forms/templates')]);
 const live=tp.templates.filter(t=>t.status==='active');
 const pick=live.length?el('form',{class:'card',onsubmit:act(async()=>{const id=pick.querySelector('[name=template]').value;box.replaceChildren(fillForm(live.find(t=>t.id===id),null,redo))})},
  el('h3',{class:'pmh'},ic('plus'),T('pm.form.fill')),field(T('pm.form.choose'),sel('template',live.map(t=>t.id),v=>live.find(t=>t.id===v).name,live[0].id)),
  el('button',{type:'submit'},T('pm.form.open'))):el('div',{class:'card'},el('p',{class:'muted'},T('pm.form.no_templates')));
 box.replaceChildren(pick,card(T('pm.form.completed'),'clip',r.forms.length?el('div',null,r.forms.slice().reverse().map(fm=>formCard(fm,redo))):none(T('pm.form.none_done'))))}
// --- note templates, co-signature, supervision, access report -----------------------------------------------
function noteForm(nf){
 const ta=nf.querySelector('textarea[name=text]'),ti=nf.querySelector('[name=title]');if(!ta||nf.querySelector('[name=template]'))return;
 const secs=el('div'),tpls=(S.st.note_templates||[]);let auto0=ti.value;
 const s=sel('template',['',...tpls],v=>v?T('pm.tpl.'+v):T('pm.tpl.free'),'');
 const compose=()=>{const parts=[...secs.querySelectorAll('textarea')].map(t=>t.dataset.label+':\n'+t.value.trim());ta.value=parts.join('\n\n')};
 s.addEventListener('change',()=>{secs.replaceChildren();const k=s.value;ta.closest('div').hidden=!!k;
  if(k){const info0=INFO&&INFO.templates[k];for(const sec of (info0||[])){const t=el('textarea',{dir:'auto',style:'min-height:60px','data-label':T('pm.sec.'+sec)});t.addEventListener('input',compose);secs.append(field(T('pm.sec.'+sec),t))}compose()}
  if(ti.value===auto0){ti.value=k?T('pm.tpl.'+k):T('app.note.default_title');auto0=ti.value}});
 const tf=ti.closest('div');nf.insertBefore(field(T('pm.tpl.label'),s),tf);tf.after(secs)}
function noteHook(n,box){
 const cs=n.cosign;if(!cs)return;
 if(cs.status==='done'){box.append(el('div',{class:cs.signature_valid?'signed':'bad'},T(cs.signature_valid?'pm.cosign.done':'pm.cosign.invalid',{name:cs.by_name||cs.by,when:fmt(cs.at)})));return}
 box.append(el('p',{class:'bad'},T('pm.cosign.waiting',{name:cs.required_name||cs.required})));
 if(cs.required===S.user.id&&can('note.cosign')){const f=el('form',{class:'sign',onsubmit:act(async()=>{await api('POST','/api/notes/cosign',{patient:S.pid,note:n.id,pin:f.querySelector('[name=pin]').value});say(T('pm.cosign.saved'),'ok');await reload()})},
  field(T('app.field.your_pin'),pinInput()),el('button',{type:'submit'},ic('lock'),T('pm.cosign.btn')));box.append(f)}}
async function usersHook(users,box){
 if(!S.st.plan.roles||!can('users.manage'))return;const st=await staff(true);if(!st.length)return;
 const sups=st.filter(x=>x.can_cosign);
 const rows=st.map(u=>{
  const s=sel('sup',['',...sups.filter(x=>x.id!==u.id).map(x=>x.id)],v=>v?sups.find(x=>x.id===v).display:T('pm.sup.none'),u.supervisor,{'aria-label':T('pm.sup.col_sup')+' - '+u.display});
  const save=el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/pm/supervisor',{user:u.id,supervisor:s.value});say(T('pm.sup.saved'),'ok');await usersHook(users,box)})},T('app.common.save'));
  return el('tr',null,el('td',{dir:'auto'},u.display),el('td',null,s),el('td',null,save))});
 const head=el('thead',null,el('tr',null,el('th',null,T('app.users.col_user')),el('th',null,T('pm.sup.col_sup')),el('th',null,el('span',{class:'sr'},T('app.vital.col_actions')))));
 box.replaceChildren(el('div',{class:'card'},el('h3',null,T('pm.sup.title')),el('p',{class:'muted'},T('pm.sup.intro')),wrap(el('table',null,head,el('tbody',null,rows)))))}
async function auditHook(box){
 if(!can('audit.read'))return;
 const[us,pts]=await Promise.all([api('GET','/api/users').then(r=>r.users).catch(()=>[]),can('demo.read')?patients():Promise.resolve([])]);
 const out=el('div');
 const pfx=['','chart.','note.','appt.','measure.','plan.','invoice.','payment.','superbill.','claim.','form.','letter.','group.','waitlist.','dashboard.','document.','mbc.','psynote.','safety.','release.','disclosure.','discharge.','episode.','auth.','gfe.','statement.','aging.','todo.','login.','user.','backup.','audit.'];
 const f=el('form',{class:'card',onsubmit:act(async()=>{const d=formData(f);const q=new URLSearchParams({patient:d.patient,user:d.user,action:d.action,from:d.from,to:d.to}).toString();
   const r=await api('GET','/api/pm/access?'+q);
   out.replaceChildren(el('p',{class:'muted'},T('pm.acc.matched',{n:r.matched,shown:r.entries.length})),r.entries.length?wrap(el('table',null,el('thead',null,el('tr',null,['app.audit.col_time','pm.acc.col_user','app.audit.col_action','pm.f.patient','pm.acc.col_detail'].map(h=>el('th',null,T(h))))),
    el('tbody',null,r.entries.map(e=>el('tr',null,el('td',null,fmt(e.ts)),auto('td',r.users[e.actor]||e.actor),el('td',{class:'ltr'},e.action),auto('td',r.patients[e.target]||e.target||''),
     el('td',{class:'ltr'},Object.entries(e.detail||{}).map(([k,v])=>k+'='+(typeof v==='object'?JSON.stringify(v):v)).join(' ').slice(0,120))))))):null)})},
  el('h3',null,T('pm.acc.title')),el('p',{class:'muted'},T('pm.acc.intro')),
  el('div',{class:'row'},field(T('pm.f.patient'),el('select',{name:'patient'},el('option',{value:''},T('pm.acc.any')),pts.map(p=>el('option',{value:p.id},p.name+' ('+dday(p.dob)+')')))),
   field(T('pm.acc.col_user'),el('select',{name:'user'},el('option',{value:''},T('pm.acc.any')),us.map(u=>el('option',{value:u.id},u.display)))),
   field(T('app.audit.col_action'),sel('action',pfx,v=>v?v.slice(0,-1):T('pm.acc.any'),''))),
  el('div',{class:'row'},field(T('pm.acc.from'),inp('from','date','')),field(T('pm.acc.to'),inp('to','date',''))),
  el('div',{class:'pmacts'},el('button',{type:'submit'},T('pm.acc.show')),el('button',{class:'ghost',type:'button',onclick:act(async()=>{const d=formData(f);
   await download('GET','/api/pm/access.csv?'+new URLSearchParams({patient:d.patient,user:d.user,action:d.action,from:d.from,to:d.to}).toString(),null,'access-report.csv')})},ic('down'),T('pm.acc.csv'))),out);
 box.replaceChildren(f)}
// --- care suite (care_suite): measurement-based care, safety plans, psychotherapy notes, releases and disclosures,
// discharge, authorizations, Good Faith Estimates, statements, aging, the to-do list and caseload outcomes -------
const CTONE={improved:'ok',worsened:'bad',no_reliable_change:''};
const AATONE={expired:'bad',ends_soon:'warn',used_up:'bad',low:'warn',overbooked:'bad',booked_after_end:'warn'};
const enc=encodeURIComponent;
function pdfBtn(path,name,label){return el('button',{class:'ghost',type:'button',onclick:act(()=>download('GET',path,null,name))},ic('down'),label||T('pm.form.pdf'))}
function signForm(path,body,ok,after,label){const f=el('form',{class:'sign',onsubmit:act(async()=>{await api('POST',path,Object.assign({},body,{pin:f.querySelector('[name=pin]').value}));say(T(ok),'ok');await after()})},
 field(T('app.field.your_pin'),pinInput()),el('button',{type:'submit'},ic('lock'),T(label||'pm.care.sign')));return f}
function errBox(path,body,after){return el('details',null,el('summary',null,T('pm.meas.mark_error')),reasonForm(T('app.field.reason'),T('pm.meas.mark_error'),async r=>{await api('POST',path,Object.assign({},body,{reason:r}));say(T('app.common.saved'),'ok');await after()}))}
function sealChip(x){return x.status==='entered_in_error'?chip(T('pm.meas.in_error',{reason:x.error_reason||''})):x.signature?chip(T(x.signature_valid?'pm.form.seal_ok':'app.chip.sig_invalid'),x.signature_valid?'ok':'bad'):chip(T('app.chip.draft'),'warn')}
function iname(ii,k){return (ii.instruments[k]||{}).name||k}
function outLine(ii,o){
 return el('li',null,el('div',{class:'it'},el('div',{class:'t'},iname(ii,o.instrument)),
  el('div',{class:'sub'},T('pm.care.out.line',{name:iname(ii,o.instrument),first:o.first.total,fdate:dday(o.first.date),last:o.latest.total,ldate:dday(o.latest.date),n:o.count}))),
  el('div',{class:'pmacts'},o.direction?chip(T('pm.care.dir.'+o.direction),CTONE[o.direction]):null,o.response!=null?chip(T(o.response?'pm.care.out.response':'pm.care.out.no_response'),o.response?'ok':''):null,
   o.remission!=null?chip(T(o.remission?'pm.care.out.remission':'pm.care.out.no_remission'),o.remission?'ok':''):null))}
function rules(ii,src){return el('details',null,el('summary',null,T('pm.care.out.rules')),...Object.entries(src).map(([k,v])=>el('p',{class:'muted',lang:'en',dir:'ltr'},iname(ii,k)+': '+v)))}
async function mbcCard(){// Measures tab: the client's measure schedule and outcome indicators
 const ii=await info(),r=await api('GET','/api/care/mbc?patient='+enc(S.pid)),w=can('measure.write'),box=el('div',{class:['card','wide']});
 const redo=async()=>box.replaceWith(await mbcCard());
 const rows=r.schedule.filter(s=>s.status==='active').map(s=>el('li',null,el('div',{class:'it'},el('div',{class:'t'},iname(ii,s.instrument)),
   el('div',{class:'sub'},[T('pm.care.mbc.every',{n:s.every_days}),T('pm.care.mbc.due',{date:dday(s.due)}),s.last?T('pm.care.mbc.last',{date:dday(s.last)}):''].filter(Boolean).join(' · '))),
  el('div',{class:'pmacts'},s.overdue_days?chip(T('pm.care.mbc.overdue',{n:s.overdue_days}),'warn'):s.due<=r.today?chip(T('pm.care.mbc.due_now'),'info'):null,
   w?el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/care/mbc/stop',{patient:S.pid,schedule:s.id});say(T('app.common.saved'),'ok');await redo()})},T('pm.care.mbc.stop')):null)));
 const outs=Object.values(r.outcomes).filter(o=>o.count>1);
 const f=w?el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(f);await api('POST','/api/care/mbc',{patient:S.pid,instrument:d.mbc_instrument,every_days:Number(d.every),start:d.start});say(T('app.common.saved'),'ok');await redo()})},
  el('div',{class:'row'},field(T('pm.meas.col_measure'),sel('mbc_instrument',Object.keys(ii.instruments),k=>iname(ii,k),'phq9')),field(T('pm.care.mbc.every_label'),sel('every',r.every,v=>T('pm.care.mbc.every',{n:v}),28)),
   field(T('pm.care.mbc.start'),inp('start','date',r.today,{required:true}))),el('button',{type:'submit'},T('pm.care.mbc.save'))):null;
 box.append(el('h3',{class:'pmh'},ic('clock'),T('pm.care.mbc.title')),el('p',{class:'muted'},T('pm.care.mbc.intro')),rows.length?el('ul',{class:'items'},rows):none(T('pm.care.mbc.none')),
  ...(f?[el('details',null,el('summary',null,T('pm.care.mbc.add')),f)]:[]),
  el('h3',{class:'pmh'},ic('chart'),T('pm.care.out.title')),outs.length?el('ul',{class:'items'},outs.map(o=>outLine(ii,o))):none(T('pm.care.out.none')),rules(ii,r.sources));
 return box}
async function safetyTab(box){
 const ii=await info(),r=await api('GET','/api/care/safety?patient='+enc(S.pid)),w=can('plan.write'),redo=()=>safetyTab(box);
 const form=(p,base)=>{const src=p||base||{sections:{}};
  const f=el('form',{class:['card','wide'],onsubmit:act(async()=>{const d=formData(f);const sections={};for(const k of r.sections)sections[k]=d['s_'+k];
    await api('POST','/api/care/safety',{patient:S.pid,plan_id:(p||base||{}).id||null,plan:{sections,client_name:d.client_name,client_date:d.client_date}});say(T('app.common.saved'),'ok');await redo()})},
   el('h3',null,T(p?'pm.care.safety.edit':'pm.care.safety.new')),el('p',{class:'muted'},T('pm.care.safety.help')),
   ...r.sections.map(k=>field(T('pm.care.safety.s_'+k),el('textarea',{name:'s_'+k,value:(src.sections||{})[k]||'',dir:'auto',style:'min-height:70px'}))),
   el('div',{class:'row'},field(T('pm.care.safety.client_name'),inp('client_name','text',src.client_name||'',{dir:'auto',maxlength:120})),field(T('pm.care.safety.client_date'),inp('client_date','date',src.client_date||'',{max:ii.today}))),
   el('div',{class:'pmacts'},el('button',{type:'submit'},T('app.note.save_draft')),el('button',{class:'ghost',type:'button',onclick:act(redo)},T('app.common.cancel'))));return f};
 const cardOf=p=>{const cur=p.status==='signed',old=p.status==='superseded'||p.status==='entered_in_error';
  const c=el('div',{class:['card','wide',old?'muted':'']},el('div',{class:'ch'},el('h3',{class:'pmh'},ic('alert'),T('pm.care.safety.version',{v:p.version})),
    el('span',{class:'pmacts'},cur?chip(T('pm.care.safety.current'),'ok'):p.status==='superseded'?chip(T('pm.care.safety.superseded')):null,sealChip(p))),
   el('p',{class:'muted'},(p.author_name||'')+' · '+fmt(p.updated)),
   ...r.sections.filter(k=>p.sections[k]).map(k=>el('div',null,el('b',null,T('pm.care.safety.s_'+k)),auto('pre',p.sections[k],{class:'note'}))),
   p.client_name?el('p',null,T('pm.care.safety.client_line',{name:p.client_name,date:p.client_date?dday(p.client_date):'-'})):null);
  if(p.signature)c.append(el('p',{class:p.signature_valid?'signed':'bad'},T('pm.care.signed_line',{name:p.signed_by_name||p.signed_by,when:fmt(p.signed_at),code:p.content_sha.slice(0,12)})),
   el('div',{class:'pmacts'},pdfBtn('/api/care/safety/pdf?patient='+enc(S.pid)+'&plan='+p.id,'safety-plan-v'+p.version+'.pdf',T('pm.care.safety.print')),
    w&&cur?el('button',{class:'ghost',type:'button',onclick:()=>c.replaceWith(form(null,p))},T('pm.care.safety.revise')):null));
  else if(w&&p.status==='draft'&&p.author===S.user.id)c.append(el('div',{class:'pmacts'},el('button',{class:'ghost',type:'button',onclick:()=>c.replaceWith(form(p))},T('app.common.edit'))),
   signForm('/api/care/safety/sign',{patient:S.pid,plan_id:p.id},'pm.care.safety.signed_ok',redo));
  if(w&&p.status!=='entered_in_error')c.append(errBox('/api/care/safety/error',{patient:S.pid,plan_id:p.id},redo));
  return c};
 const em=r.emergency||{};
 box.replaceChildren(el('div',{class:'card'},el('h2',{class:'pmh'},ic('alert'),T('pm.care.safety.title')),el('p',{class:'muted'},T('pm.care.safety.intro')),
   el('p',null,(em.numbers||[]).length?T('pm.care.safety.emergency_line',{numbers:em.numbers.join(' / ')}):T('pm.care.safety.emergency_plain')),
   w?el('button',{type:'button',onclick:()=>box.replaceChildren(form(null,r.plans.find(p=>p.status==='signed')))},ic('plus'),T('pm.care.safety.new')):null),
  ...r.plans.map(cardOf),...(r.plans.length?[]:[el('div',{class:'card'},none(T('pm.care.safety.none')))]))}
async function psyTab(box){// psychotherapy notes: the author's own, kept outside the chart and its exports
 const ii=await info(),r=await api('GET','/api/care/psy?patient='+enc(S.pid)),redo=()=>psyTab(box);
 const form=n=>{n=n||{};const f=el('form',{class:['card','wide'],onsubmit:act(async()=>{const d=formData(f);await api('POST','/api/care/psy',{patient:S.pid,note_id:n.id||null,note:{date:d.date,text:d.text}});say(T('app.common.saved'),'ok');await redo()})},
  el('h3',null,T(n.id?'pm.care.psy.edit':'pm.care.psy.new')),field(T('pm.care.psy.date'),inp('date','date',n.date||ii.today,{required:true,max:ii.today})),
  field(T('pm.care.psy.text'),el('textarea',{name:'text',value:n.text||'',dir:'auto',style:'min-height:180px',required:true})),
  el('div',{class:'pmacts'},el('button',{type:'submit'},T('app.note.save_draft')),el('button',{class:'ghost',type:'button',onclick:act(redo)},T('app.common.cancel'))));return f};
 const cardOf=n=>{const c=el('div',{class:['card','wide',n.status==='entered_in_error'?'muted':'']},el('div',{class:'ch'},el('h3',null,dday(n.date)),sealChip(n)),auto('pre',n.text,{class:'note'}));
  if(n.signature)c.append(el('p',{class:n.signature_valid?'signed':'bad'},T('pm.care.signed_line',{name:n.author_name||n.author,when:fmt(n.signed_at),code:n.content_sha.slice(0,12)})));
  else if(n.status==='draft')c.append(el('div',{class:'pmacts'},el('button',{class:'ghost',type:'button',onclick:()=>c.replaceWith(form(n))},T('app.common.edit'))),signForm('/api/care/psy/sign',{patient:S.pid,note_id:n.id},'pm.care.psy.signed_ok',redo));
  if(n.status!=='entered_in_error')c.append(errBox('/api/care/psy/error',{patient:S.pid,note_id:n.id},redo));return c};
 box.replaceChildren(el('div',{class:'card'},el('h2',{class:'pmh'},ic('lock'),T('pm.care.psy.title')),el('p',{class:'muted'},T('pm.care.psy.intro')),
   el('div',{class:'pmacts'},el('button',{type:'button',onclick:()=>box.replaceChildren(form(null))},ic('plus'),T('pm.care.psy.new')),r.notes.length?pdfBtn('/api/care/psy/pdf?patient='+enc(S.pid),'psychotherapy-notes.pdf',T('pm.care.psy.print')):null)),
  ...r.notes.map(cardOf),...(r.notes.length?[]:[el('div',{class:'card'},none(T('pm.care.psy.none')))]))}
async function releaseTab(box){// releases of information (client-signed, staff-witnessed) and the disclosure log
 const ii=await info(),r=await api('GET','/api/care/releases?patient='+enc(S.pid)),redo=()=>releaseTab(box);
 const RT={active:'ok',expired:'warn',revoked:'bad',entered_in_error:''};
 const rf=el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(rf);
   await api('POST','/api/care/releases',{patient:S.pid,pin:d.pin,release:{direction:d.direction,party:d.party,party_contact:d.party_contact,information:[...rf.querySelectorAll('[name=rinfo]:checked')].map(i=>i.value),
    information_other:d.information_other,purpose:d.purpose,expires:d.expires,expires_event:d.expires_event,signer_name:d.signer_name,signer_role:d.signer_role,signed_date:d.signed_date}});
   say(T('pm.care.rel.saved'),'ok');await redo()})},
  el('p',{class:'muted'},T('pm.care.rel.help')),
  el('div',{class:'row'},field(T('pm.care.rel.direction'),sel('direction',r.directions,v=>T('pm.care.rdir.'+v),'release_to')),field(T('pm.care.rel.party'),inp('party','text','',{required:true,dir:'auto',maxlength:200}))),
  field(T('pm.care.rel.party_contact'),el('textarea',{name:'party_contact',dir:'auto',style:'min-height:50px'})),
  el('fieldset',{class:'pmq'},el('legend',null,T('pm.care.rel.information')),el('div',{class:'pmopts'},r.information.map(k=>el('label',{class:'inl'},el('input',{type:'checkbox',name:'rinfo',value:k}),' '+T('pm.care.rinfo.'+k))))),
  el('p',{class:'muted'},T('pm.care.rel.psy_note')),
  field(T('pm.care.rel.other'),inp('information_other','text','',{dir:'auto',maxlength:300})),field(T('pm.care.rel.purpose'),inp('purpose','text','',{required:true,dir:'auto',maxlength:300})),
  el('div',{class:'row'},field(T('pm.care.rel.expires'),inp('expires','date','')),field(T('pm.care.rel.expires_event'),inp('expires_event','text','',{dir:'auto',maxlength:200}))),
  el('div',{class:'row'},field(T('pm.form.signer'),inp('signer_name','text','',{required:true,dir:'auto',maxlength:120})),field(T('pm.form.signer_role'),sel('signer_role',r.signer_roles,v=>TK('pm.srole',v),'client')),
   field(T('pm.form.signed_date'),inp('signed_date','date',ii.today,{required:true,max:ii.today}))),
  field(T('pm.care.rel.witness_pin'),pinInput()),el('button',{type:'submit'},ic('lock'),T('pm.care.rel.sign')));
 const relRow=x=>el('li',null,el('div',{class:'it'},el('div',{class:'t'},T('pm.care.rdir.'+x.direction)+': ',auto('span',x.party)),
   el('div',{class:'sub'},[...x.information.map(k=>T('pm.care.rinfo.'+k)),x.information_other].filter(Boolean).join(', ')),
   el('div',{class:'meta'},T('pm.care.rel.line',{date:dday(x.signed_date),name:x.signer_name,until:x.expires?dday(x.expires):(x.expires_event||'-')})),
   x.state==='revoked'?el('div',{class:'meta'},T('pm.care.rel.revoked_line',{date:dday(x.revoked_date),reason:x.revoked_reason||''})):null),
  el('div',{class:'pmacts'},chip(T('pm.care.rstate.'+x.state),RT[x.state]),x.signature_valid===false?chip(T('app.chip.sig_invalid'),'bad'):null,
   pdfBtn('/api/care/releases/pdf?patient='+enc(S.pid)+'&release='+x.id,'release-'+x.id+'.pdf'),
   x.state==='active'?el('details',null,el('summary',null,T('pm.care.rel.revoke')),reasonForm(T('app.field.reason'),T('pm.care.rel.revoke'),async y=>{await api('POST','/api/care/releases/revoke',{patient:S.pid,release_id:x.id,reason:y});say(T('app.common.saved'),'ok');await redo()})):null,
   x.status!=='entered_in_error'?errBox('/api/care/releases/error',{patient:S.pid,release_id:x.id},redo):null));
 const usable=r.releases.filter(x=>x.state==='active'&&x.direction!=='obtain_from');
 const df=el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(df);await api('POST','/api/care/disclosures',{patient:S.pid,disclosure:d});say(T('app.common.saved'),'ok');await redo()})},
  el('div',{class:'row'},field(T('app.field.date'),inp('date','date',ii.today,{required:true,max:ii.today})),field(T('pm.care.disc.recipient'),inp('recipient','text','',{required:true,dir:'auto',maxlength:200}))),
  field(T('pm.care.disc.recipient_contact'),inp('recipient_contact','text','',{dir:'auto',maxlength:300})),
  field(T('pm.care.disc.information'),el('textarea',{name:'information',dir:'auto',style:'min-height:50px',required:true})),field(T('pm.care.disc.purpose'),inp('purpose','text','',{dir:'auto',maxlength:300})),
  el('div',{class:'row'},field(T('pm.care.disc.basis'),sel('basis',r.bases,v=>T('pm.care.dbasis.'+v),usable.length?'authorization':'required_by_law')),
   field(T('pm.care.disc.release'),el('select',{name:'release'},el('option',{value:''},'-'),usable.map(x=>el('option',{value:x.id},x.party+' ('+dday(x.signed_date)+')')))),
   field(T('pm.care.disc.method'),sel('method',r.methods,v=>T('pm.care.dmethod.'+v),'mail'))),el('button',{type:'submit'},T('pm.care.disc.save')));
 const drows=r.disclosures.map(d=>el('tr',{class:d.status!=='active'?'muted':null},el('td',null,dday(d.date)),auto('td',d.recipient),auto('td',d.information),el('td',null,T('pm.care.dbasis.'+d.basis)),
   el('td',null,d.status==='active'?errBox('/api/care/disclosures/error',{patient:S.pid,disclosure_id:d.id},redo):T('pm.meas.in_error',{reason:d.error_reason||''}))));
 const q='/api/care/disclosures/pdf?patient='+enc(S.pid);
 box.replaceChildren(el('div',{class:'card'},el('h2',{class:'pmh'},ic('consent'),T('pm.care.rel.title')),el('p',{class:'muted'},T('pm.care.rel.intro')),
   r.releases.length?el('ul',{class:'items'},r.releases.map(relRow)):none(T('pm.care.rel.none')),el('details',null,el('summary',null,T('pm.care.rel.new')),rf)),
  el('div',{class:'card'},el('h2',{class:'pmh'},ic('audit'),T('pm.care.disc.title')),el('p',{class:'muted'},T('pm.care.disc.intro')),
   drows.length?wrap(el('table',null,el('thead',null,el('tr',null,['app.field.date','pm.care.disc.recipient','pm.care.disc.information','pm.care.disc.basis'].map(h=>el('th',null,T(h))),el('th',null,el('span',{class:'sr'},T('app.vital.col_actions'))))),el('tbody',null,drows))):none(T('pm.care.disc.none')),
   el('details',null,el('summary',null,T('pm.care.disc.add')),df),
   el('div',{class:'pmacts'},pdfBtn(q,'disclosures.pdf',T('pm.care.disc.accounting')),pdfBtn(q+'&all=1','disclosures-all.pdf',T('pm.care.disc.all'))),
   el('p',{class:'muted'},T('pm.care.disc.left_out_note'))))}
async function episodeCard(){// Treatment plan tab: episode of care, discharge summary, reopening
 const ii=await info(),r=await api('GET','/api/care/episodes?patient='+enc(S.pid)),w=can('plan.write'),box=el('div',{class:['card','wide']});
 const redo=async()=>box.replaceWith(await episodeCard());
 const cur=r.episodes[0]||{},dsc=cur.status==='open'?cur.discharge:null;
 box.append(el('div',{class:'ch'},el('h3',{class:'pmh'},ic('clip'),T('pm.care.dis.care_title')),chip(T('pm.care.estatus.'+r.status),r.status==='active'?'ok':'')),
  ...(cur.opened?[el('p',{class:'muted'},T('pm.care.dis.opened',{date:dday(cur.opened)}))]:[]));
 if(r.status==='active'&&w){
  if(dsc&&dsc.author!==S.user.id)box.append(el('p',{class:'muted'},T('pm.care.dis.other_draft',{name:dsc.author_name||''})));
  else{const f=el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(f);await api('POST','/api/care/discharge',{patient:S.pid,discharge:{closed:d.closed,reason:d.reason,summary:d.summary,recommendations:d.recommendations,referrals:d.referrals}});say(T('app.common.saved'),'ok');await redo()})},
    el('div',{class:'row'},field(T('pm.care.dis.date'),inp('closed','date',(dsc&&dsc.closed)||ii.today,{required:true,max:ii.today})),field(T('pm.care.dis.reason'),sel('reason',r.reasons,v=>T('pm.care.dreason.'+v),(dsc&&dsc.reason)||'goals_met'))),
    field(T('pm.care.dis.summary'),el('textarea',{name:'summary',value:(dsc&&dsc.summary)||'',dir:'auto',style:'min-height:120px',required:true})),
    field(T('pm.care.dis.recommendations'),el('textarea',{name:'recommendations',value:(dsc&&dsc.recommendations)||'',dir:'auto',style:'min-height:60px'})),
    field(T('pm.care.dis.referrals'),el('textarea',{name:'referrals',value:(dsc&&dsc.referrals)||'',dir:'auto',style:'min-height:50px'})),
    el('button',{type:'submit'},T('app.note.save_draft')));
   box.append(el('details',{open:dsc?true:null},el('summary',null,T(dsc?'pm.care.dis.draft':'pm.care.dis.start')),el('p',{class:'muted'},T('pm.care.dis.help')),f,
    dsc?signForm('/api/care/discharge/sign',{patient:S.pid},'pm.care.dis.signed_ok',reload,'pm.care.dis.sign'):null))}}
 for(const e of r.episodes.filter(e=>e.discharge&&e.discharge.signature)){const d=e.discharge;
  box.append(el('div',{class:'pmep'},el('p',null,el('b',null,T('pm.care.dis.period',{opened:dday(e.opened),closed:dday(d.closed)})),' ',chip(T('pm.care.dreason.'+d.reason)),' ',sealChip(d)),
   auto('pre',d.summary,{class:'note'}),el('div',{class:'pmacts'},pdfBtn('/api/care/discharge/pdf?patient='+enc(S.pid)+'&episode='+e.id,'discharge-'+e.id+'.pdf',T('pm.care.dis.print')))))}
 if(r.status==='discharged'&&(w||can('sched.write')))box.append(el('details',null,el('summary',null,T('pm.care.dis.reopen')),
  reasonForm(T('app.field.reason'),T('pm.care.dis.reopen'),async y=>{await api('POST','/api/care/reopen',{patient:S.pid,reason:y});say(T('app.common.saved'),'ok');await redo()})));
 return box}
async function billCare(){// Billing tab: payer authorizations, Good Faith Estimates and the client statement
 const ii=await info(),w=can('bill.write'),box=el('div');
 const redo=async()=>box.replaceWith(await billCare());
 const[a,g]=await Promise.all([api('GET','/api/care/auths?patient='+enc(S.pid)),api('GET','/api/care/gfe?patient='+enc(S.pid))]);
 function aform(x){x=x||{};const f=el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(f);await api('POST','/api/care/auths',{patient:S.pid,auth_id:x.id||null,auth:d});say(T('app.common.saved'),'ok');await redo()})},
  el('div',{class:'row'},field(T('pm.care.auth.payer'),inp('payer','text',x.payer||'',{required:true,dir:'auto',maxlength:120})),field(T('pm.care.auth.number'),inp('number','text',x.number||'',{required:true,dir:'ltr',maxlength:40}))),
  el('div',{class:'row'},field(T('pm.care.auth.visits'),inp('visits','number',x.visits||'',{required:true,min:1,max:999,dir:'ltr'})),field(T('pm.care.auth.used_before'),inp('used_before','number',x.used_before||0,{min:0,max:999,dir:'ltr'})),
   field(T('pm.care.auth.start'),inp('start','date',x.start||ii.today,{required:true})),field(T('pm.care.auth.end'),inp('end','date',x.end||'',{required:true}))),
  field(T('pm.care.auth.codes'),inp('codes','text',(x.codes||[]).join(', '),{dir:'ltr',maxlength:80})),field(T('pm.f.notes'),inp('notes','text',x.notes||'',{dir:'auto',maxlength:500})),
  el('button',{type:'submit'},T('app.common.save')));return f}
 function gform(x){x=x||{lines:[],diagnoses:[]};const lines=el('div');
  const lrow=l=>{l=l||{};const r=el('div',{class:['row','pmgfe']},field(T('pm.bill.code'),inp('code','text',l.code||'',{dir:'ltr',maxlength:5,placeholder:'90837'})),
    field(T('pm.bill.line_desc'),inp('description','text',l.description||'',{dir:'auto',maxlength:120})),field(T('pm.care.gfe.quantity'),inp('quantity','number',l.quantity||1,{min:1,max:99,dir:'ltr'})),
    field(T('pm.bill.fee'),inp('fee','number',l.fee!=null?(l.fee/100).toFixed(2):'',{min:0,step:'0.01',dir:'ltr'})),el('button',{class:'ghost',type:'button',onclick:()=>r.remove()},T('pm.plan.remove')));return r};
  (x.lines.length?x.lines:[{}]).forEach(l=>lines.append(lrow(l)));
  const f=el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(f);
    const ls=[...lines.querySelectorAll('.pmgfe')].map(r=>({code:r.querySelector('[name=code]').value,description:r.querySelector('[name=description]').value,quantity:r.querySelector('[name=quantity]').value,fee:r.querySelector('[name=fee]').value})).filter(l=>l.code.trim());
    await api('POST','/api/care/gfe',{patient:S.pid,estimate_id:x.id||null,estimate:{period_start:d.period_start,period_end:d.period_end,lines:ls,diagnoses:(d.diagnoses||'').split(/[\s,;]+/).filter(Boolean),notes:d.gnotes}});
    say(T('app.common.saved'),'ok');await redo()})},
   el('div',{class:'row'},field(T('pm.care.gfe.start'),inp('period_start','date',x.period_start||ii.today,{required:true})),field(T('pm.care.gfe.end'),inp('period_end','date',x.period_end||addDays(ii.today,364),{required:true}))),
   lines,el('button',{class:'ghost',type:'button',onclick:()=>lines.append(lrow())},ic('plus'),T('pm.care.gfe.add_line')),
   field(T('pm.care.gfe.diagnoses'),inp('diagnoses','text',(x.diagnoses||[]).join(', '),{dir:'ltr',maxlength:120})),field(T('pm.f.notes'),inp('gnotes','text',x.notes||'',{dir:'auto',maxlength:1000})),
   el('button',{type:'submit'},T('app.common.save')));return f}
 const arow=x=>el('li',null,el('div',{class:'it'},el('div',{class:'t'},auto('span',x.payer+' #'+x.number)),
   el('div',{class:'sub'},T('pm.care.auth.window',{start:dday(x.start),end:dday(x.end)})+' · '+T('pm.care.auth.usage',{used:x.attended+x.used_before,visits:x.visits,booked:x.booked,left:x.remaining})),
   x.codes.length?el('div',{class:'meta',dir:'ltr'},x.codes.join(', ')):null,x.notes?auto('div',x.notes,{class:'meta'}):null),
  el('div',{class:'pmacts'},x.status!=='active'?chip(T('pm.care.auth.closed')):null,x.alerts.map(k=>chip(T('pm.care.aalert.'+k),AATONE[k])),
   w&&x.status==='active'?el('details',null,el('summary',null,T('app.common.edit')),aform(x)):null,
   w&&x.status==='active'?el('details',null,el('summary',null,T('pm.care.auth.close')),reasonForm(T('app.field.reason'),T('pm.care.auth.close'),async y=>{await api('POST','/api/care/auths/close',{auth_id:x.id,reason:y});say(T('app.common.saved'),'ok');await redo()})):null));
 const grow=x=>el('li',null,el('div',{class:'it'},el('div',{class:'t'},x.number||T('app.chip.draft'),' · ',el('span',{dir:'ltr'},cash(x.total,x.currency))),
   el('div',{class:'sub'},T('pm.care.gfe.period',{start:dday(x.period_start),end:dday(x.period_end)})),
   x.status==='issued'?el('div',{class:'meta'},T('pm.care.gfe.billed',{billed:cash(x.billed,x.currency),total:cash(x.total,x.currency)})):null,
   x.dispute_risk?el('p',{class:'bad'},T('pm.care.gfe.over',{over:cash(x.over,x.currency)})):null),
  el('div',{class:'pmacts'},chip(TK('pm.istatus',x.status),{draft:'warn',issued:'info',void:''}[x.status]),
   w&&x.status==='draft'?el('button',{type:'button',onclick:act(async()=>{await api('POST','/api/care/gfe/issue',{estimate_id:x.id});say(T('pm.care.gfe.issued'),'ok');await redo()})},T('pm.bill.issue')):null,
   w&&x.status==='draft'?el('details',null,el('summary',null,T('app.common.edit')),gform(x)):null,
   x.status!=='draft'?pdfBtn('/api/care/gfe/pdf?estimate='+x.id,'good-faith-estimate-'+x.number+'.pdf'):null,
   w&&x.status!=='void'?el('details',null,el('summary',null,T('pm.bill.void')),reasonForm(T('app.field.reason'),T('pm.bill.void'),async y=>{await api('POST','/api/care/gfe/void',{estimate_id:x.id,reason:y});say(T('pm.bill.voided'),'ok');await redo()})):null));
 const st=el('form',{class:'pmline',onsubmit:act(async()=>{const d=formData(st);await download('GET','/api/care/statement?patient='+enc(S.pid)+'&as_of='+enc(d.as_of),null,'statement-'+d.as_of+'.pdf')})},
  field(T('pm.care.st.as_of_label'),inp('as_of','date',ii.today,{required:true,max:ii.today})),el('button',{type:'submit'},ic('down'),T('pm.care.st.btn')));
 box.append(card(T('pm.care.auth.title'),'key',el('p',{class:'muted'},T('pm.care.auth.intro')),a.auths.length?el('ul',{class:'items'},a.auths.map(arow)):none(T('pm.care.auth.none')),
   w?el('details',null,el('summary',null,T('pm.care.auth.new')),aform(null)):null),
  card(T('pm.care.gfe.title'),'bill',el('p',{class:'muted'},T('pm.care.gfe.intro')),g.estimates.length?el('ul',{class:'items'},g.estimates.map(grow)):none(T('pm.care.gfe.none')),
   w?el('details',null,el('summary',null,T('pm.care.gfe.new')),gform(null)):null),
  card(T('pm.care.st.title'),'print',el('p',{class:'muted'},T('pm.care.st.intro')),st));
 return box}
async function careBilling(){// Practice > Billing: accounts-receivable aging and the Good Faith Estimate settings
 const r=await api('GET','/api/care/aging'),out=[];
 const head=['pm.f.patient',...r.buckets.map(b=>'pm.care.aging.b_'+b),'pm.care.aging.credit','pm.bill.col_balance'];
 const rows=r.rows.map(x=>el('tr',null,el('td',null,openBtn(x.patient,x.patient_name)),r.buckets.map(b=>el('td',{dir:'ltr'},cash(x.buckets[b],x.currency))),el('td',{dir:'ltr'},cash(x.credit,x.currency)),el('td',{dir:'ltr'},cash(x.total,x.currency))));
 for(const[c,t]of Object.entries(r.totals))rows.push(el('tr',null,el('td',null,el('b',null,T('pm.care.aging.total',{cur:c}))),r.buckets.map(b=>el('td',{dir:'ltr'},cash(t.buckets[b],c))),el('td',{dir:'ltr'},cash(t.credit,c)),el('td',{dir:'ltr'},el('b',null,cash(t.total,c)))));
 out.push(el('div',{class:['card','wide']},el('h3',{class:'pmh'},ic('bill'),T('pm.care.aging.title')),el('p',{class:'muted'},T('pm.care.aging.intro',{date:dday(r.as_of)})),
  rows.length?wrap(el('table',null,el('thead',null,el('tr',null,head.map(h=>el('th',null,T(h))))),el('tbody',null,rows))):none(T('pm.bill.all_settled')),
  el('div',{class:'pmacts'},pdfBtn('/api/care/aging.csv?as_of='+r.as_of,'ar-aging-'+r.as_of+'.csv',T('pm.care.aging.csv')))));
 if(can('bill.write')){const s=await api('GET','/api/care/gfe/settings');
  const f=el('form',{class:'card',onsubmit:act(async()=>{await api('POST','/api/care/gfe/settings',{notice:f.querySelector('[name=gnotice]').value,reminders:f.querySelector('[name=grem]').checked});say(T('app.common.saved'),'ok')})},
   el('h3',null,T('pm.care.gfe.settings')),el('p',{class:'muted'},T('pm.care.gfe.settings_help')),
   el('label',{class:'inl'},el('input',{type:'checkbox',name:'grem',checked:s.reminders?true:null}),' '+T('pm.care.gfe.reminders')),
   field(T('pm.care.gfe.notice'),el('textarea',{name:'gnotice',value:s.notice,dir:'auto',style:'min-height:160px'})),el('button',{type:'submit'},T('app.common.save')));
  out.push(f)}
 return out}
async function todo(){// Practice > To-do: what needs doing, from this computer's records
 await strings();const ii=await info();const d=await api('GET','/api/care/todo');const cards=[],g=d.gaps;
 const row=(pid,name,sub,extra)=>el('li',null,el('div',{class:'it'},el('div',{class:'t'},openBtn(pid,name)),sub?auto('div',sub,{class:'sub'}):null),extra||null);
 const ap=a=>[dday(a.date),a.time,a.clinician_name].filter(Boolean).join(' · ');
 const list=(title,icon,xs,fn)=>card(title,icon,xs.length?el('ul',{class:'items'},xs.map(fn)):none(T('pm.today.none')));
 if(d.measures_due)cards.push(list(T('pm.care.todo.due',{n:d.measures_due.length}),'pulse',d.measures_due,x=>row(x.patient,x.patient_name,
  [iname(ii,x.instrument),T('pm.care.mbc.due',{date:dday(x.due)}),x.next_appt?T('pm.care.todo.next_appt',{date:dday(x.next_appt)}):''].filter(Boolean).join(' · '),
  x.overdue_days?chip(T('pm.care.mbc.overdue',{n:x.overdue_days}),'warn'):null)));
 if(g&&g.missing_notes){cards.push(list(T('pm.care.todo.missing',{n:g.missing_notes.length}),'note',g.missing_notes,a=>row(a.patient,a.patient_name,ap(a))));
  cards.push(list(T('pm.care.todo.unsigned',{n:g.unsigned_notes.length}),'lock',g.unsigned_notes,a=>row(a.patient,a.patient_name,ap(a))));
  const t=g.timeliness,tot=t['1']+t['3']+t.later;
  cards.push(card(T('pm.care.todo.timely'),'clock',tot?el('p',null,T('pm.care.todo.timely_line',{d1:t['1'],d3:t['3'],later:t.later,n:tot})):none(T('pm.care.todo.timely_none'))))}
 if(g&&g.unbilled)cards.push(list(T('pm.care.todo.unbilled',{n:g.unbilled.length}),'bill',g.unbilled,a=>row(a.patient,a.patient_name,ap(a))));
 if(d.auth_alerts)cards.push(list(T('pm.care.todo.auths',{n:d.auth_alerts.length}),'key',d.auth_alerts,x=>row(x.patient,x.patient_name,
  x.payer+' #'+x.number+' · '+T('pm.care.auth.usage',{used:x.attended+x.used_before,visits:x.visits,booked:x.booked,left:x.remaining}),
  el('span',{class:'pmacts'},x.alerts.map(k=>chip(T('pm.care.aalert.'+k),AATONE[k]))))));
 if(d.gfe_missing)cards.push(list(T('pm.care.todo.gfe',{n:d.gfe_missing.length}),'bill',d.gfe_missing,x=>row(x.patient,x.patient_name,T('pm.care.todo.gfe_line',{date:dday(x.date)}))));
 content(el('div',{class:'card'},el('h2',{class:'pmh'},ic('audit'),T('app.pm.todo')),el('p',{class:'muted'},g?T('pm.care.todo.intro',{from:dday(g.from),to:dday(g.to)}):T('pm.care.todo.intro_short')),
   d.discharged?el('p',{class:'muted'},T('pm.care.todo.discharged',{n:d.discharged})):null),cards.length?el('div',{class:'grid'},cards):el('div',{class:'card'},none(T('pm.today.nothing'))))}
async function outcomes(range){// Practice > Outcomes: the caseload's measure results (local data only)
 await strings();const ii=await info();range=range||{};const d=await api('GET','/api/care/outcomes?'+new URLSearchParams(range).toString());
 const sum=Object.entries(d.summary).map(([k,s])=>card(iname(ii,k),'chart',el('p',null,T('pm.care.out.clients',{n:s.clients})),
  s.change_rule?el('p',null,T('pm.care.out.dirs',{up:s.improved,down:s.worsened,same:s.no_reliable_change,pts:s.change_rule})):null,
  s.response_eligible?el('p',null,T('pm.care.out.resp',{n:s.response_eligible,resp:s.response,rem:s.remission})):null));
 const rows=d.rows.map(o=>el('tr',null,el('td',null,openBtn(o.patient,o.patient_name)),el('td',null,iname(ii,o.instrument)),
  el('td',{dir:'ltr'},o.first.total+' → '+o.latest.total),el('td',null,dday(o.latest.date)),el('td',null,o.direction?chip(T('pm.care.dir.'+o.direction),CTONE[o.direction]):'-'),
  el('td',null,o.response==null?'-':T(o.response?'pm.care.out.response':'pm.care.out.no_response')),el('td',null,o.remission==null?'-':T(o.remission?'pm.care.out.remission':'pm.care.out.no_remission'))));
 const f=el('form',{class:'pmline',onsubmit:act(async()=>{await outcomes({from:f.querySelector('[name=from]').value,to:f.querySelector('[name=to]').value})})},
  el('div',{class:'row'},field(T('pm.acc.from'),inp('from','date',d.from,{required:true})),field(T('pm.acc.to'),inp('to','date',d.to,{required:true}))),el('button',{type:'submit'},T('pm.dash.show')));
 content(el('div',{class:'card'},el('h2',{class:'pmh'},ic('pulse'),T('app.pm.outcomes')),el('p',{class:'muted'},T('pm.care.out.intro')),f),
  sum.length?el('div',{class:'grid'},sum):el('div',{class:'card'},none(T('pm.care.out.none_caseload'))),
  ...(rows.length?[card(T('pm.care.out.table'),null,wrap(el('table',null,el('thead',null,el('tr',null,['pm.f.patient','pm.meas.col_measure','pm.care.out.col_change','pm.care.out.col_latest','pm.care.out.col_dir','pm.care.out.col_resp','pm.care.out.col_rem'].map(h=>el('th',null,T(h))))),el('tbody',null,rows))))]:[]),
  el('div',{class:'card'},el('p',{class:'muted'},T('pm.meas.not_diagnosis')),rules(ii,d.sources)))}
async function tab(k,box){
 await info();
 const fn={pm_appt:apptTab,pm_meas:measTab,pm_plan:planTab,pm_bill:billTab,pm_form:formTab,pm_letter:letterTab,pm_safety:safetyTab,pm_psy:psyTab,pm_release:releaseTab}[k];if(fn)await fn(box)}
return {strings,view:{today,cal,billing,forms,dash,groups,todo,outcomes},hook:{tab,note:noteHook,noteform:async nf=>{await info();noteForm(nf)},users:usersHook,audit:auditHook}}})();
