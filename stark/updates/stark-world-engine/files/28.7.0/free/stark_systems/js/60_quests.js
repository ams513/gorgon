/* Stark World Engine Systems: quests. Runs the studio's quest format (steps collect / defeat / talk / reach / escort, rewards,
   next, final) plus craft / deliver / interact / survive / wait / custom steps, timers and fail conditions, and a
   RADIANT generator: templates + pools -> endless quests, with variety guards (template cooldown, verb cap, place /
   giver / target cooldowns, novelty scoring over many candidates, follow-up chains) so long play does not repeat.
   CHILD SAFETY: generated text is checked before use; children are never defeat / hunt targets (rescue and escort
   of children are allowed: they protect them).
   systems.quests_radiant = {templates: [...], pools: {enemies, items, places, givers}, variety: {...}, reward} */
(function (G) {
  'use strict';
  var U = G.util, S = G.safety;
  var TEMPLATES = [
    { id: 'hunt', verb: 'hunt', title: 'Thin the {enemyName} Pack', desc: '{giverName} says {enemyName}s have been seen near {placeName}. Defeat {n} of them.', steps: [{ type: 'defeat', kind: '{enemy}', count: '{n}' }], n: [3, 6] },
    { id: 'hunt_lone', verb: 'hunt', title: 'The {adj} {enemyName}', desc: 'A {adj} {enemyName} troubles {placeName}. {giverName} wants it gone.', steps: [{ type: 'reach', place: '{place}', r: 6 }, { type: 'defeat', kind: '{enemy}', count: 1 }], n: [1, 1] },
    { id: 'fetch', verb: 'fetch', title: '{itemName} for {giverName}', desc: '{giverName} needs {n} {itemName} {reason}.', steps: [{ type: 'collect', item: '{item}', count: '{n}', consume: true }, { type: 'talk', npc: '{giver}' }], n: [2, 5] },
    { id: 'deliver', verb: 'deliver', title: 'A Parcel for {placeName}', desc: '{giverName} asks you to carry a sealed parcel to {placeName}.', steps: [{ type: 'reach', place: '{place}', r: 4 }, { type: 'talk', npc: '{giver}' }], n: [1, 1], give: 'parcel' },
    { id: 'explore', verb: 'explore', title: 'What Lies at {placeName}?', desc: 'Nobody has been to {placeName} in a long time. {giverName} wants news.', steps: [{ type: 'reach', place: '{place}', r: 5 }, { type: 'talk', npc: '{giver}' }], n: [1, 1] },
    { id: 'clear', verb: 'clear', title: 'Clear {placeName}', desc: '{enemyName}s have taken {placeName}. Clear out {n} and make it safe again.', steps: [{ type: 'reach', place: '{place}', r: 7 }, { type: 'defeat', kind: '{enemy}', count: '{n}' }], n: [3, 5] },
    { id: 'gather', verb: 'gather', title: 'Gathering at {placeName}', desc: 'Collect {n} {itemName} around {placeName} {reason}.', steps: [{ type: 'collect', item: '{item}', count: '{n}' }], n: [3, 8] },
    { id: 'craft', verb: 'craft', title: 'Made by Hand', desc: '{giverName} would love a {itemName} made by you.', steps: [{ type: 'craft', item: '{craftItem}', count: 1 }, { type: 'talk', npc: '{giver}' }], n: [1, 1], needs: 'craftItem' },
    { id: 'escort', verb: 'escort', title: 'Safe Passage', desc: 'Walk with {giverName} to {placeName} and keep them safe.', steps: [{ type: 'escort', npc: '{giver}', place: '{place}', r: 4 }], n: [1, 1] },
    { id: 'rescue', verb: 'rescue', title: 'Lost Near {placeName}', desc: '{lostName} wandered off toward {placeName}. Find them and bring them home.', steps: [{ type: 'reach', place: '{place}', r: 5 }, { type: 'talk', npc: '{giver}' }], n: [1, 1] },
    { id: 'survey', verb: 'explore', title: 'Mapping the {adj} Wilds', desc: 'Visit {placeName} and return before nightfall.', steps: [{ type: 'reach', place: '{place}', r: 5 }], n: [1, 1] }
  ];
  var ADJ = ['Grey', 'Hungry', 'Old', 'Scarred', 'Restless', 'Howling', 'Cunning', 'Ancient', 'Pale', 'Bold'];
  var REASONS = ['for the winter stores', 'for a festival', 'to mend the mill', 'for a sick neighbour', 'to trade at the market', 'for an old recipe', 'to keep the lamps lit', 'before the rains'];
  var LOST = ['a curious goat', 'the miller\'s dog', 'a young apprentice', 'a travelling bard', 'the herbalist\'s cart pony'];
  G.RADIANT_TEMPLATES = TEMPLATES;

  G.register('quests', 60, function (rt) {
    var Q = { defs: {}, st: {}, radiantN: 0, history: [] };
    var all = [];
    [rt.world].concat(rt.world.levels || []).forEach(function (L, li) { (L && L.quests || []).forEach(function (q) { if (q && q.id) { q._lvl = li; all.push(q); } }); });
    (rt.sysdata.quests || []).forEach(function (q) { if (q && q.id) all.push(q); });
    all.forEach(function (q) { Q.defs[q.id] = q; });
    var rad = rt.sysdata.quests_radiant || {};
    var V = Object.assign({ history: 12, template_cooldown: 3, max_same_verb: 2, verb_window: 5, place_cooldown: 4, giver_cooldown: 2, target_cooldown: 3, candidates: 14, followup_every: 5 }, rad.variety || {});
    Q.variety = V;

    Q.isDone = function (id) { return !!(Q.st[id] && Q.st[id].state === 'done'); };
    Q.isActive = function (id) { return !!(Q.st[id] && Q.st[id].state === 'active'); };
    Q.state = function (id) { return Q.st[id] ? Q.st[id].state : 'none'; };
    Q.active = function () { return all.filter(function (q) { return Q.isActive(q.id); }); };
    Q.step = function (q) { var s = Q.st[q.id]; return s && (q.steps || [])[s.step]; };
    Q.stepText = function (st, q) {
      if (!st) return '';
      if (st.text) return st.text;
      var c = +st.count || 1, inv = rt.sys.inventory;
      if (st.type === 'collect') return 'Collect ' + (inv ? inv.def(st.item).name : st.item) + (c > 1 ? ' (' + Q.st[q.id].n + '/' + c + ')' : '');
      if (st.type === 'defeat') return 'Defeat ' + (st.id ? nameOf(st.id) : (st.kind || 'enemies')) + (c > 1 ? ' (' + Q.st[q.id].n + '/' + c + ')' : '');
      if (st.type === 'talk') return 'Talk to ' + nameOf(st.npc);
      if (st.type === 'escort') return 'Escort ' + nameOf(st.npc) + ' to ' + (st.label || placeName(st.place) || 'safety');
      if (st.type === 'reach') return 'Reach ' + (st.label || placeName(st.place) || 'the marked spot');
      if (st.type === 'craft') return 'Craft ' + (inv ? inv.def(st.item || st.recipe).name : st.item);
      if (st.type === 'survive') return 'Survive ' + Math.ceil(Math.max(0, (+st.seconds || 60) - Q.st[q.id].t)) + 's';
      return st.type;
    };
    function nameOf(id) { var e = rt.get(id); return e ? e.name : String(id || 'someone'); }
    function place(id) { var pois = (rt.sysdata.ai && rt.sysdata.ai.pois) || {}, p = pois[id]; if (p) return p; var e = rt.get(id); return e ? { x: e.pos.x, z: e.pos.z, name: e.name } : null; }
    function placeName(id) { var p = id && place(id); return p ? (p.name || id) : null; }
    function target(st) { if (st.place) { var p = place(st.place); if (p) return p; } return st.x != null ? { x: +st.x, z: +st.z } : null; }
    Q.target = function (q) { var st = Q.step(q); if (!st) return null; if (st.type === 'talk' || (st.type === 'escort' && Q.st[q.id].n === 0 && false)) { var e = rt.get(st.npc); return e ? e.pos : null; } return target(st); };

    Q.start = function (id, quiet) {
      var q = Q.defs[id]; if (!q || Q.st[id]) return false;
      if (q.title) S.assertText(q.title, 'quest title'); if (q.desc) S.assertText(q.desc, 'quest description');
      Q.st[id] = { state: 'active', step: 0, n: 0, t: 0 };
      initStep(q);
      if (q.give && rt.sys.inventory && rt.player) rt.sys.inventory.give(rt.player, q.give, 1, true);
      rt.bus.emit('questStart', { quest: q, quiet: !!quiet });
      check(q);
      return true;
    };
    function initStep(q) {
      var s = Q.st[q.id], st = Q.step(q); s.n = 0; s.t = 0;
      if (st && st.type === 'collect' && rt.sys.inventory && rt.player) s.n = Math.min(rt.sys.inventory.count(rt.player, st.item), +st.count || 1);
      if (st) rt.bus.emit('questStep', { quest: q, step: s.step, def: st });
    }
    function stepDone(q) { var s = Q.st[q.id], st = Q.step(q); return st && s.n >= (+st.count || 1); }
    function check(q) {
      var s = Q.st[q.id]; if (!s || s.state !== 'active') return;
      for (var guard = 0; guard < 20 && stepDone(q); guard++) {
        var st = Q.step(q);
        if (st.type === 'collect' && st.consume && rt.sys.inventory) rt.sys.inventory.take(rt.player, st.item, +st.count || 1);
        s.step++;
        if (s.step >= (q.steps || []).length) { Q.complete(q.id); return; }
        initStep(q);
      }
      if (!(q.steps || []).length) Q.complete(q.id);
    }
    Q.complete = function (id) {
      var q = Q.defs[id], s = Q.st[id]; if (!q || !s || s.state === 'done') return false;
      s.state = 'done';
      var rw = q.reward || {};
      if (rw.gold && rt.sys.economy) rt.sys.economy.add(rt.player, +rw.gold);
      (rw.items || (rw.item ? [rw.item] : [])).forEach(function (it) { if (rt.sys.inventory) rt.sys.inventory.give(rt.player, it, 1); });
      if (rw.rep && rt.sys.factions) Object.keys(rw.rep).forEach(function (f) { rt.sys.factions.addRep(f, +rw.rep[f]); });
      rt.bus.emit('questDone', { quest: q, reward: rw });
      if (q.next && Q.defs[q.next]) Q.start(q.next);
      if (q.radiant) Q.radiantDone(q);
      Q.checkWin();
      return true;
    };
    Q.fail = function (id, why) { var s = Q.st[id]; if (!s || s.state !== 'active') return false; s.state = 'failed'; rt.bus.emit('questFail', { quest: Q.defs[id], why: why || '' }); return true; };
    Q.advance = function (id, n) { var q = Q.defs[id], s = Q.st[id]; if (!q || !s || s.state !== 'active') return; s.n += n || 1; check(q); };
    Q.checkWin = function () {
      if (Q.won || !all.length) return;
      var fin = all.filter(function (q) { return q.final; }), list = fin.length ? fin : all.filter(function (q) { return !q.radiant; });
      if (list.length && list.every(function (q) { return Q.isDone(q.id); })) { Q.won = true; rt.bus.emit('win', { by: 'quests' }); }
    };
    function event(type, d) {
      Q.active().forEach(function (q) {
        var st = Q.step(q), s = Q.st[q.id]; if (!st || st.type !== type) return;
        if (st.level != null && st.level !== '' && q._lvl != null && +st.level !== (rt.level || 0)) return;
        var ok = false, add = 1;
        if (type === 'collect') { ok = st.item === d.item; add = d.n || 1; }
        else if (type === 'defeat') ok = (!st.kind || st.kind === 'any' || st.kind === d.enemyKind || st.kind === d.kind) && (!st.id || st.id === d.id);
        else if (type === 'talk' || type === 'escort') ok = st.npc === d.npc;
        else if (type === 'craft') ok = !st.item || (d.out && d.out[st.item]) || st.recipe === d.recipe;
        else if (type === 'interact') ok = !st.id || st.id === d.id;
        else if (type === 'custom') ok = st.event === d.event;
        if (ok) { s.n = Math.min(+st.count || 1, s.n + add); check(q); rt.bus.emit('questProgress', { quest: q }); }
      });
    }
    Q.event = event;
    rt.bus.on('collect', function (d) { if (d.entity === rt.player) event('collect', d); });
    rt.bus.on('death', function (d) { if (d.source && (d.source === rt.player || d.source.team === 'player')) event('defeat', { kind: d.entity.kind, enemyKind: d.enemyKind, id: d.entity.id }); });
    rt.bus.on('talk', function (d) { event('talk', { npc: d.npc && d.npc.id }); });
    rt.bus.on('craft', function (d) { if (d.entity === rt.player) event('craft', d); });
    rt.bus.on('interact', function (d) { if (d.target) event('interact', { id: d.target.id }); });
    rt.bus.on('questEvent', function (d) { event('custom', { event: d && (d.event || d) }); });

    Q.update = function (dt) {
      var p = rt.player; if (!p) return;
      Q.active().forEach(function (q) {
        var st = Q.step(q), s = Q.st[q.id]; if (!st) return;
        s.t += dt;
        if (q.fail && q.fail.time && s.t > +q.fail.time && s.step === 0) { Q.fail(q.id, 'out of time'); return; }
        if (st.type === 'reach') { var t = target(st); if (t && U.dist(p.pos, t) < (+st.r || 3)) { s.n = +st.count || 1; check(q); } }
        else if (st.type === 'escort') { var npc = rt.get(st.npc), t2 = target(st); if (npc && t2 && U.dist(npc.pos, t2) < (+st.r || 3)) { s.n = 1; check(q); } else if (npc && npc.comps.ai && U.dist(npc.pos, p.pos) > 2) rt.sys.ai.goTo(npc, p.pos.x - Math.sin(p.rot) * 1.5, p.pos.z - Math.cos(p.rot) * 1.5, 1.1); }
        else if (st.type === 'survive' || st.type === 'wait') { if (s.t >= (+st.seconds || 30)) { s.n = +st.count || 1; check(q); } }
      });
    };

    // -------------------------------------------------------------- radiant generator
    function pools() {
      var p = rad.pools || {}, out = {};
      out.enemies = p.enemies || (function () { var seen = {}, l = []; rt.ents.forEach(function (e) { var k = e.spec.enemy; if (e.kind === 'enemy' && k && !seen[k] && !S.isMinor(e)) { seen[k] = 1; l.push({ id: k, name: e.spec.kindName || e.name || (k.charAt(0).toUpperCase() + k.slice(1)) }); } }); return l; })();
      out.enemies = out.enemies.filter(function (x) { return x && !S.isMinor(x.id); });
      out.items = p.items || Object.keys((rt.sys.inventory && rt.sys.inventory.defs) || {}).filter(function (id) { var d = rt.sys.inventory.defs[id]; return d.kind === 'quest' || d.kind === 'material' || d.tags && d.tags.indexOf('gatherable') >= 0; }).map(function (id) { return { id: id, name: rt.sys.inventory.def(id).name }; });
      out.places = p.places || Object.keys((rt.sysdata.ai && rt.sysdata.ai.pois) || {}).map(function (id) { var x = rt.sysdata.ai.pois[id]; return { id: id, name: x.name || id }; });
      out.givers = (p.givers || rt.query(function (e) { return e.kind === 'npc' && !e.flags.noQuests; }).map(function (e) { return e.id; })).filter(function (id) { return !!rt.get(id); });
      out.crafts = p.crafts || Object.keys((rt.sys.inventory && rt.sys.inventory.recipes) || {}).map(function (r) { var o = Object.keys(rt.sys.inventory.recipes[r].out || {})[0]; return o ? { id: o, name: rt.sys.inventory.def(o).name } : null; }).filter(Boolean);
      return out;
    }
    function fill(v, ctx) {
      if (typeof v === 'string') { var m = v.match(/^\{(\w+)\}$/); if (m && ctx[m[1]] !== undefined) return ctx[m[1]]; return v.replace(/\{(\w+)\}/g, function (a, k) { return ctx[k] != null ? ctx[k] : a; }); }
      if (Array.isArray(v)) return v.map(function (x) { return fill(x, ctx); });
      if (U.isObj(v)) { var o = {}; for (var k in v) o[k] = fill(v[k], ctx); return o; }
      return v;
    }
    function recent(key, val, n) { var h = Q.history.slice(-n); return h.some(function (x) { return x[key] === val; }); }
    function candidate(tpl, P, rng) {
      var ctx = { adj: rng.pick(rad.adjectives || ADJ), reason: rng.pick(rad.reasons || REASONS), lostName: rng.pick(rad.lost || LOST) };
      var giver = rng.pick(P.givers), g = giver && rt.get(giver);
      if (!g) return null;
      ctx.giver = giver; ctx.giverName = g.name;
      var needEnemy = JSON.stringify(tpl.steps).indexOf('{enemy}') >= 0, needItem = JSON.stringify(tpl.steps).indexOf('{item}') >= 0, needPlace = JSON.stringify(tpl).indexOf('{place') >= 0;
      if (needEnemy) { var en = rng.pick(P.enemies); if (!en) return null; ctx.enemy = en.id; ctx.enemyName = en.name; }
      if (needItem) { var it = rng.pick(P.items); if (!it) return null; ctx.item = it.id; ctx.itemName = it.name; }
      if (tpl.needs === 'craftItem') { var cr = rng.pick(P.crafts); if (!cr) return null; ctx.craftItem = cr.id; ctx.itemName = cr.name; }
      if (needPlace) { var pl = rng.pick(P.places); if (!pl) return null; ctx.place = pl.id; ctx.placeName = pl.name; }
      if (tpl.verb === 'escort' && S.isMinor(g)) { /* escorting a child home is protective: allowed */ }
      var n = tpl.n ? rng.int(tpl.n[0], tpl.n[1]) : 1; ctx.n = n;
      var lvl = rt.sys.progression ? rt.sys.progression.level(rt.player) : 1, rw = rad.reward || {};
      var xp = (rw.xp || [30, 12]), gold = (rw.gold || [10, 5]);
      return { tpl: tpl, ctx: ctx, n: n, reward: { xp: Math.round((xp[0] + xp[1] * lvl) * (0.8 + 0.1 * n)), gold: Math.round(gold[0] + gold[1] * lvl) },
        key: { template: tpl.id, verb: tpl.verb, place: ctx.place || null, giver: giver, target: ctx.enemy || ctx.item || ctx.craftItem || null } };
    }
    function novelty(c) {
      var s = 0, k = c.key;
      if (recent('template', k.template, V.template_cooldown)) s -= 100;
      var verbs = Q.history.slice(-V.verb_window).filter(function (h) { return h.verb === k.verb; }).length;
      if (verbs >= V.max_same_verb) s -= 80; else s -= verbs * 10;
      if (k.place && recent('place', k.place, V.place_cooldown)) s -= 30;
      if (recent('giver', k.giver, V.giver_cooldown)) s -= 20;
      if (k.target && recent('target', k.target, V.target_cooldown)) s -= 25;
      var combo = Q.history.some(function (h) { return h.template === k.template && h.place === k.place && h.target === k.target; });
      if (combo) s -= 60;
      return s;
    }
    /** Make one new radiant quest (registered + returned, not started). -> quest or null. */
    Q.generate = function (opts) {
      opts = opts || {};
      var P = pools(), rng = opts.rng || rt.rng, tpls = rad.templates || TEMPLATES, best = null, bestS = -1e9;
      for (var i = 0; i < V.candidates; i++) {
        var c = candidate(rng.pick(tpls), P, rng);
        if (!c) continue;
        var sc = novelty(c) + rng() * 5;
        if (sc > bestS) { best = c; bestS = sc; }
      }
      if (!best) return null;
      Q.radiantN++;
      var id = 'rad_' + Q.radiantN + '_' + best.tpl.id;
      var q = { id: id, radiant: true, verb: best.tpl.verb, giver: best.ctx.giver, title: fill(best.tpl.title, best.ctx), desc: fill(best.tpl.desc, best.ctx), steps: fill(best.tpl.steps, best.ctx), reward: best.reward, give: best.tpl.give };
      q.steps.forEach(function (st) { if (st.count != null) st.count = +st.count || 1; });
      if (Q.radiantN % V.followup_every === 0 && Q.history.length) { var prev = Q.history[Q.history.length - 1]; q.title += ' (a new lead)'; q.desc += ' It may be connected to ' + (prev.placeName || 'what you found before') + '.'; q.followup = prev.id; }
      if (S.textBlocked(q.title, S.minorNames()) || S.textBlocked(q.desc, S.minorNames())) { G.warn('radiant quest text refused by child safety'); return null; }
      Q.defs[id] = q; all.push(q);
      var h = best.key; h.id = id; h.placeName = best.ctx.placeName; Q.history.push(h); if (Q.history.length > V.history * 4) Q.history.shift();
      rt.bus.emit('questOffered', { quest: q });
      return q;
    };
    Q.radiantDone = function (q) { rt.bus.emit('radiantDone', { quest: q }); };
    Q.radiantStats = function () {
      var verbs = {}, combos = {}; Q.history.forEach(function (h) { verbs[h.verb] = (verbs[h.verb] || 0) + 1; combos[h.template + '|' + h.place + '|' + h.target] = 1; });
      var maxRun = 0, run = 0; for (var i = 1; i < Q.history.length; i++) { if (Q.history[i].template === Q.history[i - 1].template) { run++; maxRun = Math.max(maxRun, run); } else run = 0; }
      return { generated: Q.history.length, verbs: verbs, uniqueCombos: Object.keys(combos).length, maxTemplateRepeatRun: maxRun };
    };
    Q.begin = function () { all.forEach(function (q) { if (q.auto && !q.radiant) Q.start(q.id, true); }); };
    Q.save = function () { return { st: U.copy(Q.st), radiant: all.filter(function (q) { return q.radiant; }).map(U.copy), history: U.copy(Q.history), n: Q.radiantN }; };
    Q.load = function (d) {
      d = d || {}; (d.radiant || []).forEach(function (q) { if (!Q.defs[q.id]) { Q.defs[q.id] = q; all.push(q); } });
      Q.st = d.st || {}; Q.history = d.history || []; Q.radiantN = d.n || 0;
    };
    return Q;
  });
})(GSYS);
