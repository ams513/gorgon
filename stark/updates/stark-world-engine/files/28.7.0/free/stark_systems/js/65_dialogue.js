/* Stark World Engine Systems: branching dialogue with state. Runs the studio's dialogs format ({start, branches: [{if, node}],
   nodes: {id: {speaker, text, next, do, choices: [{text, goto, end, if, ...ACTION}]}}}) and adds:
   COND  + {var: {name: {eq|ne|gte|lte|gt|lt: v}}, rep: {faction: min}, affinity: {char: min}, chance, time: day|night,
           skill, item}
   ACTION + {set: {var: v}, inc: {var: n}, rep: {faction: n}, affinity: {char: n} (friendship, any age),
           romance: target (adults only - guarded), shop, craft, event, sfx, music}
   Text supports {var} and {player} interpolation. CHILD SAFETY: romance / undress / harm / capture actions on a
   child or teen throw; interpolated text is checked before it is shown. */
(function (G) {
  'use strict';
  var U = G.util, S = G.safety;
  G.register('dialogue', 65, function (rt) {
    var D = { vars: {}, cur: null, affinity: {}, romance: {}, seen: {} };
    var dialogs = {};
    [rt.world].concat(rt.world.levels || []).forEach(function (L) { var d = L && L.dialogs; for (var k in (d || {})) dialogs[k] = d[k]; });
    var extra = rt.sysdata.dialogs || {}; for (var k2 in extra) dialogs[k2] = extra[k2];
    D.dialogs = dialogs;
    D.vars = U.copy((rt.sysdata.dialogue && rt.sysdata.dialogue.vars) || {});

    function cmp(v, c) {
      if (!U.isObj(c)) return v === c;
      if ('eq' in c && v !== c.eq) return false; if ('ne' in c && v === c.ne) return false;
      if ('gte' in c && !(v >= c.gte)) return false; if ('lte' in c && !(v <= c.lte)) return false;
      if ('gt' in c && !(v > c.gt)) return false; if ('lt' in c && !(v < c.lt)) return false;
      return true;
    }
    D.cond = function (c) {
      if (!c) return true;
      var p = rt.player, inv = rt.sys.inventory, qs = rt.sys.quests;
      if (c.has && !(inv && inv.has(p, c.has, +c.n || 1))) return false;
      if (c.item && !(inv && inv.has(p, c.item, +c.n || 1))) return false;
      if (c.lacks && inv && inv.has(p, c.lacks)) return false;
      if (c.done && !(qs && qs.isDone(c.done))) return false;
      if (c.active && !(qs && qs.isActive(c.active))) return false;
      if (c.notStarted && qs && qs.state(c.notStarted) !== 'none') return false;
      if (c.flag && !D.vars[c.flag]) return false;
      if (c.minLevel && rt.sys.progression && rt.sys.progression.level(p) < +c.minLevel) return false;
      if (c.var) for (var vk in c.var) if (!cmp(D.vars[vk], c.var[vk])) return false;
      if (c.rep && rt.sys.factions) for (var f in c.rep) if (rt.sys.factions.rep(f) < +c.rep[f]) return false;
      if (c.affinity) for (var ch in c.affinity) if ((D.affinity[ch] || 0) < +c.affinity[ch]) return false;
      if (c.skill && !(rt.sys.progression && rt.sys.progression.hasUnlock(p, c.skill))) return false;
      if (c.time && rt.sys.env && (c.time === 'night') !== rt.sys.env.isNight()) return false;
      if (c.chance != null && rt.rng() > +c.chance) return false;
      return true;
    };
    // an id that is not spawned (a cast member of a visual novel, a character on another level) stays an id string,
    // so the child-safety registry still protects it
    function resolve(t, npc) { if (t === 'self' || t === 'npc' || t === true) return npc; var e = rt.get(t); return e || (typeof t === 'string' ? t : null); }
    function entOf(x) { return x && typeof x === 'object' ? x : null; }
    /** Run an ACTION object. Romance / undress / capture / harm on a child or teen throw SafetyError. */
    D.act = function (a, npc) {
      if (!U.isObj(a)) return;
      var p = rt.player, inv = rt.sys.inventory, qs = rt.sys.quests;
      ['romance', 'romance_start', 'intimacy', 'date', 'marry', 'kiss', 'seduce', 'flirt'].forEach(function (k) { if (a[k] != null) { var t = resolve(a[k], npc); S.guard('romance', t, p); if (t) { var tid = typeof t === 'string' ? t : t.id; D.romance[tid] = (D.romance[tid] || 0) + 1; rt.bus.emit('romance', { with: entOf(t), id: tid, action: k }); } } });
      ['undress', 'strip', 'nude'].forEach(function (k) { if (a[k] != null) S.guard('undress', resolve(a[k], npc)); });
      ['capture', 'kidnap', 'imprison', 'enslave'].forEach(function (k) { if (a[k] != null) { var t = resolve(a[k], npc); S.guard('capture', t); if (rt.sys.ai && entOf(t)) rt.sys.ai.capture(p, t); } });
      ['damage', 'harm', 'hurt', 'kill', 'attack'].forEach(function (k) { if (a[k] != null) { var t = resolve(U.isObj(a[k]) ? a[k].target : a[k], npc); S.guard('harm', t); if (entOf(t) && rt.sys.combat) rt.sys.combat.damage(t, U.isObj(a[k]) ? +a[k].n || 1 : 1, p); } });
      if (a.start && qs) qs.start(a.start);
      if (a.complete && qs) qs.complete(a.complete);
      if (a.advance) rt.bus.emit('talk', { npc: rt.get(a.advance) || { id: a.advance } });
      if (a.give && inv) (Array.isArray(a.give) ? a.give : [a.give]).forEach(function (id) { inv.give(p, id, 1); });
      if (a.take && inv) inv.take(p, a.take, 1);
      if (a.gold && rt.sys.economy) rt.sys.economy.add(p, +a.gold);
      if (a.xp && rt.sys.progression) rt.sys.progression.award(p, +a.xp, 'dialogue');
      if (a.heal && rt.sys.combat) rt.sys.combat.heal(p, +a.heal);
      if (a.flag) D.vars[a.flag] = true;
      if (a.set) for (var sk in a.set) D.vars[sk] = a.set[sk];
      if (a.inc) for (var ik in a.inc) D.vars[ik] = (+D.vars[ik] || 0) + (+a.inc[ik] || 0);
      if (a.rep && rt.sys.factions) for (var f in a.rep) rt.sys.factions.addRep(f, +a.rep[f]);
      if (a.affinity) for (var ch in a.affinity) D.affinity[ch] = (D.affinity[ch] || 0) + (+a.affinity[ch] || 0);
      if (a.shop) rt.bus.emit('openShop', { shop: a.shop, npc: npc });
      if (a.craft) rt.bus.emit('openCraft', { station: a.craft });
      if (a.event) rt.bus.emit('questEvent', { event: a.event });
      if (a.sfx) rt.bus.emit('sfx', { name: a.sfx });
      if (a.music) rt.bus.emit('music', { state: a.music });
      if (a.win) rt.bus.emit('win', { by: 'dialogue' });
    };
    D.interp = function (t) {
      return String(t || '').replace(/\{(\w+)\}/g, function (m, k) { if (k === 'player') return rt.player ? rt.player.name : 'traveller'; return D.vars[k] != null ? String(D.vars[k]) : m; });
    };
    /** Start a conversation. -> the first view {speaker, text, choices: [{i, text}]} or null. */
    D.start = function (did, npc) {
      npc = rt.get(npc);
      var d = dialogs[did];
      rt.bus.emit('talk', { npc: npc });
      if (!d) { var line = npc && npc.spec && npc.spec.text; if (!line) return null; D.cur = { did: null, node: '_', d: { nodes: { _: { speaker: npc.name, text: line } } }, npc: npc }; return D.view(); }
      var start = d.start || Object.keys(d.nodes || {})[0];
      (d.branches || []).some(function (b) { if (D.cond(b.if) && d.nodes && d.nodes[b.node]) { start = b.node; return true; } return false; });
      D.cur = { did: did, node: start, d: d, npc: npc };
      rt.bus.emit('dialogueStart', { dialog: did, npc: npc });
      return enter(start);
    };
    function enter(nid) {
      var c = D.cur; if (!c) return null;
      var n = (c.d.nodes || {})[nid];
      if (!n) { D.end(); return null; }
      c.node = nid; D.seen[(c.did || '') + '.' + nid] = 1;
      if (n.do) D.act(n.do, c.npc);
      return D.view();
    }
    D.view = function () {
      var c = D.cur; if (!c) return null;
      var n = c.d.nodes[c.node];
      var choices = (n.choices || []).map(function (ch, i) { return { i: i, text: S.safeText(D.interp(ch.text), '...'), ok: D.cond(ch.if) }; }).filter(function (x) { return x.ok; });
      return { speaker: n.speaker || (c.npc ? c.npc.name : ''), text: S.safeText(D.interp(n.text), '...'), choices: choices, canContinue: !choices.length, npc: c.npc ? c.npc.id : null };
    };
    D.choose = function (i) {
      var c = D.cur; if (!c) return null;
      var n = c.d.nodes[c.node], ch = (n.choices || [])[i];
      if (!ch || !D.cond(ch.if)) return D.view();
      rt.bus.emit('dialogueChoice', { dialog: c.did, node: c.node, choice: i, text: ch.text });
      D.act(ch, c.npc);
      if (ch.goto && !ch.end) return enter(ch.goto);
      D.end();
      return null;
    };
    D.next = function () { var c = D.cur; if (!c) return null; var n = c.d.nodes[c.node]; if (n.choices && n.choices.length) return D.view(); if (n.next) return enter(n.next); D.end(); return null; };
    D.end = function () { if (D.cur) rt.bus.emit('dialogueEnd', { dialog: D.cur.did, npc: D.cur.npc }); D.cur = null; };
    D.busy = function () { return !!D.cur; };
    D.setVar = function (k, v) { D.vars[k] = v; }; D.getVar = function (k) { return D.vars[k]; };
    D.addAffinity = function (who, n, romantic) { var e = rt.get(who); if (romantic) S.guard('romance', e, rt.player); var id = e ? e.id : String(who); D.affinity[id] = (D.affinity[id] || 0) + (+n || 0); return D.affinity[id]; };
    rt.bus.on('interact', function (d) { var t = d.target; if (t && t.kind === 'npc' && d.by === rt.player && !D.cur && (t.spec.dialog || t.spec.text)) { rt.bus.emit('dialogueView', D.start(t.spec.dialog || t.id, t)); } });
    D.save = function () { return { vars: U.copy(D.vars), affinity: U.copy(D.affinity), romance: U.copy(D.romance), seen: U.copy(D.seen) }; };
    D.load = function (d) { d = d || {}; D.vars = d.vars || {}; D.affinity = d.affinity || {}; D.seen = d.seen || {}; D.romance = {}; Object.keys(d.romance || {}).forEach(function (id) { if (!S.isMinor(id)) D.romance[id] = d.romance[id]; }); };
    return D;
  });
})(GSYS);
