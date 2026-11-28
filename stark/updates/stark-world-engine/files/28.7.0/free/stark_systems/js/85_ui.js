/* Stark World Engine Systems: UI / HUD builder. Widgets from systems.hud.widgets are plain data, rendered as DOM overlays in a
   browser and kept as state headless (tests, exporters read the same model).
   widget = {type: bar|text|icons|quests|minimap|crosshair|timer|prompt|compass|messages, bind: 'player.hp',
     max: 'player.maxHp', text: 'Gold: {gold}', x, y, w, h, anchor: tl|tr|bl|br|tc|bc|c, color, label, when}
   Binds: player.hp, player.maxHp, player.stamina, player.mana, gold, level, xp, xpNeed, ammo, reserve, time, hour,
   day, weather, quest, rep.<faction>, var.<name>, speed, lap, laps, needs.<name>, sanity, battery, score, wave.
   Every displayed string goes through the child-safety text check. */
(function (G) {
  'use strict';
  var U = G.util, S = G.safety;
  G.register('ui', 85, function (rt) {
    var cfg = rt.sysdata.hud || {}, UI = { messages: [], dialog: null, menu: null, prompt: null, widgets: cfg.widgets || defaultWidgets() };
    var doc = typeof document !== 'undefined' ? document : null, root = null, els = [], acc = 0;
    function defaultWidgets() {
      var g = rt.sysdata.genre || 'action_rpg';
      var base = [{ type: 'bar', bind: 'player.hp', max: 'player.maxHp', label: 'HP', anchor: 'tl', x: 12, y: 12, w: 180, color: '#e05555' }, { type: 'messages', anchor: 'bc', y: 90 }, { type: 'prompt', anchor: 'bc', y: 140 }];
      if (g === 'action_rpg' || g === 'survival') base.push({ type: 'bar', bind: 'player.stamina', max: 'player.maxStamina', label: 'ST', anchor: 'tl', x: 12, y: 34, w: 140, color: '#e0c255' }, { type: 'text', text: 'Lv {level}  Gold {gold}', anchor: 'tl', x: 12, y: 56 }, { type: 'quests', anchor: 'tr', x: 12, y: 12 });
      if (g === 'shooter') base.push({ type: 'text', text: 'Ammo {ammo}/{reserve}', anchor: 'br', x: 16, y: 16 }, { type: 'crosshair' }, { type: 'text', text: 'Wave {wave}  Score {score}', anchor: 'tr', x: 16, y: 12 });
      if (g === 'racing') base = [{ type: 'text', text: 'Lap {lap}/{laps}', anchor: 'tl', x: 16, y: 12 }, { type: 'text', text: '{speed} km/h', anchor: 'br', x: 16, y: 16 }, { type: 'timer', anchor: 'tr', x: 16, y: 12 }, { type: 'messages', anchor: 'bc', y: 90 }];
      if (g === 'horror') base.push({ type: 'bar', bind: 'battery', max: 100, label: 'Light', anchor: 'bl', x: 12, y: 12, w: 120, color: '#ffe9a8' }, { type: 'bar', bind: 'sanity', max: 100, label: 'Nerve', anchor: 'bl', x: 12, y: 34, w: 120, color: '#9fb4ff' });
      if (g === 'survival') base.push({ type: 'bar', bind: 'needs.food', max: 100, label: 'Food', anchor: 'bl', x: 12, y: 12, w: 110, color: '#c98b4a' }, { type: 'bar', bind: 'needs.water', max: 100, label: 'Water', anchor: 'bl', x: 12, y: 34, w: 110, color: '#4aa3c9' }, { type: 'bar', bind: 'needs.warmth', max: 100, label: 'Warm', anchor: 'bl', x: 12, y: 56, w: 110, color: '#e07a4a' }, { type: 'text', text: 'Day {day}  {hour}:00  {weather}', anchor: 'tc', y: 10 });
      if (g === 'strategy') base = [{ type: 'text', text: '{resources}', anchor: 'tl', x: 12, y: 10 }, { type: 'text', text: 'Day {day}  {hour}:00', anchor: 'tr', x: 12, y: 10 }, { type: 'messages', anchor: 'bc', y: 90 }];
      if (g === 'platformer') base.push({ type: 'text', text: 'Gems {score}', anchor: 'tr', x: 16, y: 12 });
      if (g === 'puzzle') base = [{ type: 'text', text: 'Moves {moves}   Level {puzzle}', anchor: 'tl', x: 12, y: 10 }, { type: 'messages', anchor: 'bc', y: 90 }];
      if (g === 'visual_novel') base = [];
      return base;
    }
    /** Resolve a bind path to a value (also used by scripts / tests). */
    UI.value = function (path) {
      var p = rt.player, f = p && p.comps.fighter, v = p && p.comps.vitals;
      var m = String(path || '').split('.'), g = rt.sys.genres;
      switch (m[0]) {
        case 'player': if (!p) return 0; if (m[1] === 'stamina') return v ? v.stamina : 0; if (m[1] === 'maxStamina') return v ? v.maxStamina : 1; if (m[1] === 'mana') return v ? v.mana : 0; if (m[1] === 'maxMana') return v ? v.maxMana : 1; return p[m[1]] != null ? p[m[1]] : 0;
        case 'gold': return rt.sys.economy ? rt.sys.economy.gold() : 0;
        case 'level': return rt.sys.progression ? rt.sys.progression.level(p) : 1;
        case 'xp': return rt.sys.progression && p ? rt.sys.progression.of(p).xp : 0;
        case 'xpNeed': return rt.sys.progression && p ? rt.sys.progression.need(rt.sys.progression.level(p)) : 1;
        case 'ammo': case 'reserve': { var am = f && f.ammo[f.weapon]; return am ? (m[0] === 'ammo' ? am.clip : am.reserve) : '-'; }
        case 'time': return Math.floor(rt.time);
        case 'hour': return rt.sys.env ? ('0' + Math.floor(rt.sys.env.hour())).slice(-2) : '12';
        case 'day': return rt.sys.env ? rt.sys.env.day() + 1 : 1;
        case 'weather': return rt.sys.env ? rt.sys.env.weather().type : 'clear';
        case 'quest': { var q = rt.sys.quests && rt.sys.quests.active()[0]; return q ? (q.title || q.id) : ''; }
        case 'rep': return rt.sys.factions ? Math.round(rt.sys.factions.rep(m[1])) : 0;
        case 'var': return rt.sys.dialogue ? rt.sys.dialogue.getVar(m[1]) : null;
        default: return g && g.value ? g.value(path) : (rt.hudVars && rt.hudVars[path] != null ? rt.hudVars[path] : '');
      }
    };
    UI.format = function (t) { return S.safeText(String(t || '').replace(/\{([\w.]+)\}/g, function (a, k) { var v = UI.value(k); return v == null ? '' : String(v); }), ''); };
    UI.toast = function (text, kind) { var t = S.safeText(String(text), null); if (t == null) return; UI.messages.push({ text: t, kind: kind || 'info', t: rt.time }); if (UI.messages.length > 6) UI.messages.shift(); rt.bus.emit('uiToast', { text: t }); };
    UI.say = function (speaker, text) { UI.dialog = { speaker: S.safeText(String(speaker || ''), ''), text: S.assertText(String(text || ''), 'ui.say'), choices: [], t: rt.time }; rt.bus.emit('uiSay', UI.dialog); };
    UI.showDialogue = function (view) { UI.dialog = view; rt.inputLocked = !!view; };
    UI.questsView = function () { var Q = rt.sys.quests; if (!Q) return []; return Q.active().map(function (q) { return { id: q.id, title: q.title || q.id, step: Q.stepText(Q.step(q), q) }; }); };
    rt.bus.on('toast', function (d) { UI.toast(d.text, d.kind); });
    rt.bus.on('questStart', function (d) { if (!d.quiet) UI.toast('New quest: ' + (d.quest.title || d.quest.id), 'quest'); });
    rt.bus.on('questDone', function (d) { UI.toast('Quest complete: ' + (d.quest.title || d.quest.id), 'quest'); });
    rt.bus.on('levelUp', function (d) { if (d.entity === rt.player) UI.toast('Level up! You are level ' + d.level, 'level'); });
    rt.bus.on('collect', function (d) { if (d.entity === rt.player && !d.quiet && rt.sys.inventory) UI.toast('+ ' + rt.sys.inventory.def(d.item).name + (d.n > 1 ? ' x' + d.n : '')); });
    rt.bus.on('dialogueView', function (v) { UI.showDialogue(v); });
    rt.bus.on('dialogueEnd', function () { UI.showDialogue(null); });
    rt.bus.on('bark', function (d) { if (d.text) UI.toast((d.entity ? d.entity.name + ': ' : '') + d.text, 'bark'); });
    rt.bus.on('craftFail', function (d) { UI.toast('Cannot craft: ' + d.why); });
    rt.bus.on('win', function () { UI.toast('You win!', 'win'); UI.won = true; });

    // -------------------------------------------------------------- DOM
    function css(el, o) { for (var k in o) el.style[k] = o[k]; }
    function place(el, w) {
      var a = w.anchor || 'tl', x = (w.x || 0) + 'px', y = (w.y || 0) + 'px';
      css(el, { position: 'absolute', left: '', right: '', top: '', bottom: '', transform: '' });
      if (a[0] === 't') el.style.top = y; else if (a[0] === 'b') el.style.bottom = y; else { el.style.top = '50%'; el.style.transform = 'translateY(-50%)'; }
      if (a[1] === 'l') el.style.left = x; else if (a[1] === 'r') el.style.right = x; else { el.style.left = '50%'; el.style.transform = (el.style.transform || '') + ' translateX(-50%)'; }
      if (a === 'c') { el.style.left = '50%'; el.style.top = '50%'; el.style.transform = 'translate(-50%,-50%)'; }
    }
    UI.mount = function (parent) {
      if (!doc || !parent) return;
      root = doc.createElement('div'); root.className = 'gsys-hud';
      css(root, { position: 'absolute', inset: '0', pointerEvents: 'none', font: '14px system-ui,sans-serif', color: '#fff', textShadow: '0 1px 3px #000', zIndex: 5 });
      parent.appendChild(root);
      els = UI.widgets.map(function (w) {
        var el = doc.createElement('div'); place(el, w); root.appendChild(el);
        if (w.type === 'bar') { css(el, { width: (w.w || 160) + 'px', height: (w.h || 14) + 'px', background: 'rgba(0,0,0,.45)', borderRadius: '7px', overflow: 'hidden', border: '1px solid rgba(255,255,255,.25)' }); var fill = doc.createElement('div'); css(fill, { height: '100%', width: '0%', background: w.color || '#5c5', transition: 'width .12s' }); el.appendChild(fill); var lab = doc.createElement('div'); css(lab, { position: 'absolute', left: '6px', top: '-1px', fontSize: '11px' }); lab.textContent = w.label || ''; el.appendChild(lab); el._fill = fill; }
        if (w.type === 'crosshair') { place(el, { anchor: 'c' }); css(el, { width: '14px', height: '14px', border: '2px solid rgba(255,255,255,.8)', borderRadius: '50%' }); }
        if (w.type === 'messages') css(el, { textAlign: 'center', minWidth: '300px' });
        if (w.type === 'quests') css(el, { textAlign: 'right', maxWidth: '320px', fontSize: '13px' });
        return el;
      });
      UI.dlgEl = doc.createElement('div');
      css(UI.dlgEl, { position: 'absolute', left: '50%', bottom: '24px', transform: 'translateX(-50%)', width: 'min(720px,92vw)', background: 'rgba(10,12,24,.86)', border: '1px solid rgba(255,255,255,.2)', borderRadius: '12px', padding: '14px 18px', display: 'none', pointerEvents: 'auto' });
      root.appendChild(UI.dlgEl);
    };
    function renderDom() {
      if (!root) return;
      UI.widgets.forEach(function (w, i) {
        var el = els[i]; if (!el) return;
        if (w.when && !UI.value(w.when)) { el.style.display = 'none'; return; } el.style.display = '';
        if (w.type === 'bar') { var v = +UI.value(w.bind) || 0, mx = typeof w.max === 'number' ? w.max : (+UI.value(w.max) || 1); el._fill.style.width = U.clamp(v / mx * 100, 0, 100) + '%'; }
        else if (w.type === 'text') el.textContent = UI.format(w.text);
        else if (w.type === 'timer') el.textContent = (rt.hudVars && rt.hudVars.timer) || (Math.floor(rt.time / 60) + ':' + ('0' + Math.floor(rt.time % 60)).slice(-2));
        else if (w.type === 'messages') el.innerHTML = '', UI.messages.filter(function (m) { return rt.time - m.t < 4; }).forEach(function (m) { var d = doc.createElement('div'); d.textContent = m.text; d.style.opacity = String(U.clamp(4 - (rt.time - m.t), 0, 1)); el.appendChild(d); });
        else if (w.type === 'quests') { el.innerHTML = ''; UI.questsView().slice(0, 4).forEach(function (q) { var d = doc.createElement('div'); d.style.marginBottom = '6px'; var b = doc.createElement('b'); b.textContent = q.title; d.appendChild(b); d.appendChild(doc.createElement('br')); d.appendChild(doc.createTextNode(q.step)); el.appendChild(d); }); }
        else if (w.type === 'prompt') el.textContent = UI.prompt || '';
      });
      var dv = UI.dialog;
      if (UI.dlgEl) {
        if (!dv) UI.dlgEl.style.display = 'none';
        else {
          UI.dlgEl.style.display = 'block'; UI.dlgEl.innerHTML = '';
          var sp = doc.createElement('div'); css(sp, { color: '#ffd166', fontWeight: '700', marginBottom: '6px' }); sp.textContent = dv.speaker || ''; UI.dlgEl.appendChild(sp);
          var tx = doc.createElement('div'); css(tx, { lineHeight: '1.45', marginBottom: '8px' }); tx.textContent = dv.text || ''; UI.dlgEl.appendChild(tx);
          (dv.choices || []).forEach(function (c, n) { var b = doc.createElement('button'); b.textContent = (n + 1) + '. ' + c.text; css(b, { display: 'block', margin: '4px 0', background: 'rgba(255,255,255,.08)', color: '#fff', border: '1px solid rgba(255,255,255,.25)', borderRadius: '8px', padding: '6px 10px', cursor: 'pointer', textAlign: 'left', width: '100%' }); b.onclick = function () { UI.choose(c.i); }; UI.dlgEl.appendChild(b); });
          if (!(dv.choices || []).length) { var hint = doc.createElement('div'); css(hint, { opacity: '.6', fontSize: '12px' }); hint.textContent = 'Space / click to continue'; UI.dlgEl.appendChild(hint); UI.dlgEl.onclick = function () { UI.advance(); }; } else UI.dlgEl.onclick = null;
        }
      }
    }
    UI.choose = function (i) { var D = rt.sys.dialogue; if (!D) return; var v = D.choose(i); UI.showDialogue(v); };
    UI.advance = function () { var D = rt.sys.dialogue; if (D && D.busy()) UI.showDialogue(D.next()); else UI.showDialogue(null); };
    UI.update = function (dt) { acc += dt; if (acc >= 0.1) { acc = 0; renderDom(); } };
    UI.destroy = function () { if (root && root.parentNode) root.parentNode.removeChild(root); root = null; };
    return UI;
  });

  /* Camera rigs + cinematics. Renderers read rt.camera: {rig, x, y, z, yaw, pitch, zoom, shake, fade, letterbox,
     subtitle}. systems.camera = {rig: follow|side|first_person|top_down|orbit|fixed, distance, height, lag,
     lookahead, zoom, deadzone, cinematics: {id: {duration, tracks: [{type: camera|subtitle|move|say|fade|letterbox|
     event, at, to, x, y, z, yaw, zoom, text, entity, ease}]}}} */
  G.register('camera', 88, function (rt) {
    var cfg = rt.sysdata.camera || {}, ctl = rt.sysdata.controller || {};
    var rigDefault = cfg.rig || (ctl.type === 'side_scroll' ? 'side' : ctl.type === 'first_person' ? 'first_person' : ctl.type === 'third_person' ? 'follow' : 'top_down');
    var cam = rt.camera = { rig: rigDefault, x: 0, y: 0, z: 0, yaw: +cfg.yaw || 0, pitch: +cfg.pitch || -0.5, zoom: +cfg.zoom || 1, shake: 0, fade: 0, letterbox: 0, subtitle: null, distance: +cfg.distance || 8, height: +cfg.height || 4 };
    var C = { cam: cam, playing: null, trauma: 0 };
    var lag = cfg.lag != null ? +cfg.lag : 6, look = cfg.lookahead != null ? +cfg.lookahead : 1.2, dz = cfg.deadzone || [1.5, 1];
    C.shake = function (amount) { C.trauma = U.clamp(C.trauma + (+amount || 0.3), 0, 1); };
    rt.bus.on('damage', function (d) { if (d.target === rt.player) C.shake(0.35); });
    rt.bus.on('hitstop', function (d) { if (d.crit) C.shake(0.2); });
    C.play = function (id, opts) {
      var cin = (cfg.cinematics || {})[id]; if (!cin) return false;
      (cin.tracks || []).forEach(function (t) { if (t.type === 'subtitle' && t.text) S.assertText(t.text, 'cinematic subtitle'); });
      C.playing = { id: id, t: 0, d: +cin.duration || 5, tracks: cin.tracks || [], fired: {}, from: { x: cam.x, y: cam.y, z: cam.z, yaw: cam.yaw, zoom: cam.zoom }, lock: !(opts && opts.free) };
      if (C.playing.lock) rt.inputLocked = true;
      rt.bus.emit('cinematicStart', { id: id });
      return true;
    };
    C.stop = function () { if (!C.playing) return; var id = C.playing.id; C.playing = null; rt.inputLocked = false; cam.subtitle = null; cam.letterbox = 0; rt.bus.emit('cinematicEnd', { id: id }); };
    function runCinematic(dt) {
      var P = C.playing; P.t += dt;
      var camKeys = P.tracks.filter(function (t) { return t.type === 'camera'; });
      if (camKeys.length) {
        var prev = P.from, next = null;
        for (var i = 0; i < camKeys.length; i++) { if (camKeys[i].at <= P.t) prev = camKeys[i]; else { next = camKeys[i]; break; } }
        if (next) { var t0 = prev === P.from ? 0 : prev.at, f = (P.t - t0) / Math.max(0.001, next.at - t0), e = (U.ease[next.ease] || U.ease.inOut)(U.clamp(f, 0, 1)); ['x', 'y', 'z', 'yaw', 'zoom'].forEach(function (k) { var a = prev[k] != null ? prev[k] : cam[k], b = next[k] != null ? next[k] : a; cam[k] = U.lerp(a, b, e); }); }
        else ['x', 'y', 'z', 'yaw', 'zoom'].forEach(function (k) { if (prev[k] != null) cam[k] = prev[k]; });
      }
      cam.subtitle = null;
      P.tracks.forEach(function (t, i) {
        if (t.type === 'subtitle' && P.t >= t.at && P.t < t.at + (t.dur || 3)) cam.subtitle = t.text;
        if (t.type === 'fade') cam.fade = P.t >= t.at ? U.clamp((P.t - t.at) / (t.dur || 1), 0, 1) * (t.to != null ? t.to : 1) : cam.fade;
        if (t.type === 'letterbox') cam.letterbox = P.t >= t.at ? (t.to != null ? t.to : 1) : cam.letterbox;
        if (!P.fired[i] && P.t >= (t.at || 0)) {
          P.fired[i] = 1;
          if (t.type === 'move' && rt.sys.ai) rt.sys.ai.goTo(t.entity, t.x, t.z, t.speed || 1);
          if (t.type === 'say') rt.bus.emit('bark', { entity: rt.get(t.entity), text: S.safeText(t.text, '') });
          if (t.type === 'event') rt.bus.emit('questEvent', { event: t.event });
        }
      });
      if (P.t >= P.d) C.stop();
    }
    C.update = function (dt) {
      C.trauma = Math.max(0, C.trauma - dt * 1.4); cam.shake = C.trauma * C.trauma;
      if (C.playing) { runCinematic(dt); return; }
      var p = rt.player; if (!p) return;
      var k = Math.min(1, dt * lag), tx = p.pos.x, tz = p.pos.z, ty = p.pos.y;
      if (cam.rig === 'side') { tx += (p.rot || 1) * look * 2; var ddx = tx - cam.x, ddy = ty - cam.y; if (Math.abs(ddx) > dz[0]) cam.x += (ddx - Math.sign(ddx) * dz[0]) * k; if (Math.abs(ddy) > dz[1]) cam.y += (ddy - Math.sign(ddy) * dz[1]) * k; }
      else if (cam.rig === 'first_person') { cam.x = tx; cam.z = tz; cam.y = ty + 1.6 + Math.sin(rt.time * 9) * 0.04 * U.clamp(Math.hypot(p.vel.x, p.vel.z) / 5, 0, 1); cam.yaw = p.rot; cam.pitch = p.pitch; }
      else if (cam.rig === 'fixed') { /* creator-placed */ }
      else { tx += p.vel.x * look * 0.15; tz += p.vel.z * look * 0.15; cam.x += (tx - cam.x) * k; cam.z += (tz - cam.z) * k; cam.y += (ty - cam.y) * k; if (cam.rig === 'orbit') cam.yaw += dt * 0.2; }
    };
    return C;
  });

  /* Audio events: bus events -> synthesized cues (WebAudio, no files) with distance attenuation + stereo pan,
     and music states (explore / combat / danger / victory) that the music layer cross-fades. Headless: logs.
     systems.audio = {events: {busEvent: {cue, vol, pitch}}, cues: {name: {wave, freq, slide, dur, vol, noise}},
     music: {explore, combat, danger, victory}, listener: 'player'} */
  G.register('audio', 90, function (rt) {
    var cfg = rt.sysdata.audio || {}, AU = { log: [], state: 'explore', muted: false };
    var CUES = { hit: { wave: 'square', freq: 180, slide: -120, dur: 0.12, vol: 0.25 }, attack: { wave: 'noise', freq: 900, dur: 0.08, vol: 0.12 }, pickup: { wave: 'sine', freq: 660, slide: 440, dur: 0.12, vol: 0.2 }, quest: { wave: 'square', freq: 440, slide: 220, dur: 0.3, vol: 0.2 }, level: { wave: 'saw', freq: 330, slide: 660, dur: 0.45, vol: 0.2 }, death: { wave: 'noise', freq: 300, dur: 0.35, vol: 0.25 }, jump: { wave: 'square', freq: 300, slide: 300, dur: 0.1, vol: 0.15 }, shot: { wave: 'noise', freq: 1600, dur: 0.12, vol: 0.25 }, step: { wave: 'noise', freq: 200, dur: 0.04, vol: 0.05 }, heartbeat: { wave: 'sine', freq: 60, dur: 0.18, vol: 0.5 }, door: { wave: 'saw', freq: 120, slide: -40, dur: 0.3, vol: 0.2 }, ui: { wave: 'sine', freq: 880, dur: 0.05, vol: 0.12 }, engine: { wave: 'saw', freq: 90, dur: 0.1, vol: 0.06 }, boost: { wave: 'saw', freq: 200, slide: 400, dur: 0.4, vol: 0.18 } };
    var cues = Object.assign({}, CUES, cfg.cues || {});
    var map = Object.assign({ damage: 'hit', attack: 'attack', pickup: 'pickup', questDone: 'quest', levelUp: 'level', death: 'death', jump: 'jump', tracer: 'shot', chestOpened: 'door', boost: 'boost', uiToast: 'ui' }, cfg.events || {});
    var ctx = null;
    function audioCtx() { if (ctx || AU.muted) return ctx; try { var AC = G.root.AudioContext || G.root.webkitAudioContext; if (AC) ctx = new AC(); } catch (e) { ctx = null; } return ctx; }
    /** Play cue `name` at an optional world position (attenuated by distance to the listener). */
    AU.play = function (name, at, opts) {
      var c = typeof name === 'object' ? name : cues[name]; if (!c) return false;
      var vol = (c.vol || 0.2) * ((opts && opts.vol) || 1), pan = 0, L = rt.player;
      if (at && L) { var d = U.dist(L.pos, at); vol *= U.clamp(1 - d / (c.range || 25), 0, 1); pan = U.clamp((at.x - L.pos.x) / 12, -1, 1); }
      AU.log.push({ cue: typeof name === 'string' ? name : 'custom', vol: +vol.toFixed(3), t: rt.time }); if (AU.log.length > 200) AU.log.shift();
      if (vol < 0.005) return false;
      var A = audioCtx(); if (!A) return true;
      try {
        var t0 = A.currentTime, dur = c.dur || 0.15, g = A.createGain(), out = g;
        g.gain.setValueAtTime(vol, t0); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
        if (A.createStereoPanner) { var p = A.createStereoPanner(); p.pan.value = pan; g.connect(p); out = p; }
        out.connect(A.destination);
        if (c.wave === 'noise') { var n = Math.floor(A.sampleRate * dur), buf = A.createBuffer(1, n, A.sampleRate), ch = buf.getChannelData(0); for (var i = 0; i < n; i++) ch[i] = (Math.random() * 2 - 1) * (1 - i / n); var src = A.createBufferSource(); src.buffer = buf; var bq = A.createBiquadFilter(); bq.frequency.value = c.freq || 1000; src.connect(bq); bq.connect(g); src.start(t0); }
        else { var o = A.createOscillator(); o.type = c.wave === 'saw' ? 'sawtooth' : (c.wave || 'square'); o.frequency.setValueAtTime(c.freq || 440, t0); if (c.slide) o.frequency.linearRampToValueAtTime(Math.max(20, (c.freq || 440) + c.slide), t0 + dur); o.connect(g); o.start(t0); o.stop(t0 + dur + 0.02); }
      } catch (e) { G.warn('audio: ' + e.message); }
      return true;
    };
    AU.music = function (state) { if (state === AU.state) return; AU.state = state; rt.bus.emit('musicState', { state: state, track: (cfg.music || {})[state] || null }); };
    Object.keys(map).forEach(function (ev) { rt.bus.on(ev, function (d) { var at = d && (d.target || d.entity || d.from); AU.play(map[ev], at && at.pos ? at.pos : (at && at.x != null ? at : null)); }); });
    rt.bus.on('sfx', function (d) { AU.play(d.name); });
    rt.bus.on('music', function (d) { AU.music(d.state); });
    var combatT = 0;
    rt.bus.on('alert', function () { combatT = 6; AU.music('combat'); });
    AU.update = function (dt) { if (combatT > 0) { combatT -= dt; if (combatT <= 0) AU.music('explore'); } };
    AU.mute = function (v) { AU.muted = !!v; if (ctx && v) try { ctx.suspend(); } catch (e) { /* ignore */ } };
    return AU;
  });
})(GSYS);
