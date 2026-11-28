/* Stark World Engine Systems: inventory, equipment, crafting, loot tables, pickups.
   Item defs merge the studio's world.items / level items with systems.items: {id: {name, kind, stack, weight,
   value, tags, use: {heal, food, water, warmth, buff: {stat, mult, add, duration}}, equip: {slot, atk, def, hp, speed},
   weapon: weapon id}}.
   systems.inventory = {slots, max_weight, start: {item: n}}
   systems.recipes = {id: {in: {item: n}, out: {item: n}, station, time, skill, level}}
   systems.loot_tables = {id: {rolls: n | [min, max], entries: [{item | table, w, min, max, chance, minLevel}],
     guaranteed: [{item, n}], gold: [min, max]}} */
(function (G) {
  'use strict';
  var U = G.util;
  G.register('inventory', 40, function (rt) {
    var sy = rt.sysdata, cfg = sy.inventory || {}, I = {};
    var defs = {}, k;
    function merge(src) { for (var id in (src || {})) defs[id] = Object.assign({}, defs[id] || {}, src[id]); }
    merge(rt.world.items); (rt.world.levels || []).forEach(function (L) { merge(L && L.items); }); merge(sy.items);
    I.defs = defs;
    var recipes = sy.recipes || {}, tables = sy.loot_tables || {};
    I.def = function (id) { return defs[id] || { name: String(id).replace(/_/g, ' '), kind: 'misc', stack: 99 }; };
    I.of = function (e) { e = rt.get(e); if (!e) return null; return e.comps.inv || (e.comps.inv = { slots: +cfg.slots || 24, maxWeight: +cfg.max_weight || 0, items: [], eq: {} }); };
    function stackSize(id) { var d = I.def(id); return d.stack != null ? Math.max(1, +d.stack) : (d.kind === 'equip' ? 1 : 99); }
    I.count = function (e, id) { var inv = I.of(e); return inv ? inv.items.reduce(function (a, s) { return a + (s.id === id ? s.n : 0); }, 0) : 0; };
    I.has = function (e, id, n) { return I.count(e, id) >= (n || 1); };
    I.weight = function (e) { var inv = I.of(e); return inv.items.reduce(function (a, s) { return a + (+I.def(s.id).weight || 0) * s.n; }, 0); };
    /** -> how many were added (the rest did not fit). Emits 'collect' so quests count it. */
    I.give = function (e, id, n, quiet) {
      var inv = I.of(e); n = Math.max(0, Math.floor(+n || 1)); if (!inv || !n) return 0;
      var d = I.def(id);
      if (d.kind === 'treasure' && d.gold && rt.sys.economy) { rt.sys.economy.add(e, d.gold * n); return n; }
      var left = n, cap = stackSize(id);
      if (inv.maxWeight && d.weight) left = Math.min(left, Math.floor((inv.maxWeight - I.weight(e)) / d.weight));
      var added = 0;
      inv.items.forEach(function (s) { if (left > 0 && s.id === id && s.n < cap) { var t = Math.min(cap - s.n, left); s.n += t; left -= t; added += t; } });
      while (left > 0 && inv.items.length < inv.slots) { var t2 = Math.min(cap, left); inv.items.push({ id: id, n: t2 }); left -= t2; added += t2; }
      if (added) { rt.bus.emit('collect', { entity: rt.get(e), item: id, n: added, quiet: !!quiet }); if (d.kind === 'equip' && d.equip && !inv.eq[d.equip.slot] && cfg.auto_equip !== false) I.equip(e, id); }
      if (left > 0) rt.bus.emit('inventoryFull', { entity: rt.get(e), item: id, n: left });
      return added;
    };
    I.take = function (e, id, n) {
      var inv = I.of(e); n = Math.floor(+n || 1);
      if (!inv || I.count(e, id) < n) return false;
      for (var i = inv.items.length - 1; i >= 0 && n > 0; i--) { var s = inv.items[i]; if (s.id !== id) continue; var t = Math.min(s.n, n); s.n -= t; n -= t; if (!s.n) inv.items.splice(i, 1); }
      for (var sl in inv.eq) if (inv.eq[sl] === id && !I.has(e, id)) delete inv.eq[sl];
      rt.bus.emit('itemRemoved', { entity: rt.get(e), item: id });
      return true;
    };
    I.equip = function (e, id) {
      var inv = I.of(e), d = I.def(id); if (!inv || !I.has(e, id) || !d.equip) return false;
      inv.eq[d.equip.slot || 'weapon'] = id;
      var ent = rt.get(e);
      if (d.weapon && ent.comps.fighter) ent.comps.fighter.weapon = d.weapon;
      rt.bus.emit('equip', { entity: ent, item: id, slot: d.equip.slot });
      return true;
    };
    I.unequip = function (e, slot) { var inv = I.of(e); if (inv && inv.eq[slot]) { var id = inv.eq[slot]; delete inv.eq[slot]; rt.bus.emit('unequip', { entity: rt.get(e), item: id, slot: slot }); return true; } return false; };
    I.equipStats = function (e) { var inv = I.of(e), out = {}; if (!inv) return out; for (var sl in inv.eq) { var q = (I.def(inv.eq[sl]).equip) || {}; ['atk', 'def', 'hp', 'speed', 'warmth', 'light'].forEach(function (s) { if (q[s] != null) out[s] = (out[s] || (s === 'speed' ? 1 : 0)) + (s === 'speed' ? 0 : +q[s]); if (s === 'speed' && q[s] != null) out.speed = (out.speed || 1) * +q[s]; }); } return out; };
    /** Use a consumable: heal, food/water/warmth (survival needs), timed buffs. */
    I.use = function (e, id) {
      var ent = rt.get(e), d = I.def(id), u = d.use || (d.heal ? { heal: d.heal } : null);
      if (!ent || !I.has(ent, id)) return false;
      if (d.kind === 'equip') return I.equip(ent, id);
      if (!u) return false;
      if (u.heal && rt.sys.combat) { if (ent.hp >= ent.maxHp && !u.buff && !u.food && !u.water) { rt.bus.emit('toast', { text: 'Already at full health' }); return false; } rt.sys.combat.heal(ent, u.heal); }
      if (rt.sys.genres && rt.sys.genres.needs) rt.sys.genres.needs.feed(ent, u);
      if (u.buff && rt.sys.combat) rt.sys.combat.applyStatus(ent, 'buff_' + (u.buff.stat || 'atk'), u.buff.duration || 20, { buff: u.buff });
      I.take(ent, id, 1);
      rt.bus.emit('use', { entity: ent, item: id });
      return true;
    };

    // -------------------------------------------------------------- crafting
    I.recipes = recipes;
    I.stationNear = function (e, station, r) { if (!station) return true; var ent = rt.get(e); return rt.near(ent.pos, r || 2.5, function (x) { return x.spec.station === station || x.tags.has(station) || x.kind === station; }).length > 0; };
    I.canCraft = function (e, rid) {
      var r = recipes[rid]; if (!r) return { ok: false, why: 'unknown recipe' };
      for (var id in (r.in || {})) if (!I.has(e, id, r.in[id])) return { ok: false, why: 'needs ' + r.in[id] + ' ' + I.def(id).name };
      if (!I.stationNear(e, r.station)) return { ok: false, why: 'needs a ' + r.station + ' nearby' };
      if (r.level && rt.sys.progression && rt.sys.progression.level(e) < r.level) return { ok: false, why: 'needs level ' + r.level };
      if (r.skill && rt.sys.progression && !rt.sys.progression.hasUnlock(e, r.skill)) return { ok: false, why: 'needs ' + r.skill };
      return { ok: true };
    };
    var crafting = [];
    I.craft = function (e, rid) {
      var chk = I.canCraft(e, rid); if (!chk.ok) { rt.bus.emit('craftFail', { entity: rt.get(e), recipe: rid, why: chk.why }); return chk; }
      var r = recipes[rid];
      for (var id in r.in) I.take(e, id, r.in[id]);
      var done = function () { for (var o in (r.out || {})) I.give(e, o, r.out[o]); rt.bus.emit('craft', { entity: rt.get(e), recipe: rid, out: r.out }); };
      if (r.time > 0) crafting.push({ e: rt.get(e), rid: rid, t: +r.time, done: done }); else done();
      return { ok: true };
    };
    I.update = function (dt) {
      crafting = crafting.filter(function (c) { c.t -= dt; if (c.t <= 0) { c.done(); return false; } return true; });
      // pickups: entities with spec.pickup {item, n} or kind 'item'
      var p = rt.player; if (!p || p.dead) return;
      rt.near(p.pos, 1.4, function (x) { return (x.kind === 'item' || x.kind === 'crystal' || x.comps.pickup) && !x.flags.taken; }).forEach(function (x) {
        var pk = x.comps.pickup || { item: x.spec.item || (x.kind === 'crystal' ? 'crystal' : 'item'), n: +x.spec.n || 1 };
        if (I.give(p, pk.item, pk.n)) { x.flags.taken = true; rt.bus.emit('pickup', { entity: x, item: pk.item }); rt.remove(x); }
      });
    };

    // -------------------------------------------------------------- loot
    /** Roll a loot table -> [{item, n}] (+ {gold}). Nested tables, weights, chances, level gates. Seeded. */
    I.roll = function (tid, opts, depth) {
      opts = opts || {}; depth = depth || 0;
      var T = tables[tid], out = [], rng = opts.rng || rt.rng;
      if (!T || depth > 6) return out;
      var lvl = opts.level || 1;
      (T.guaranteed || []).forEach(function (g) { out.push({ item: g.item, n: +g.n || 1 }); });
      var rolls = Array.isArray(T.rolls) ? rng.int ? rng.int(T.rolls[0], T.rolls[1]) : T.rolls[0] : (+T.rolls || 1);
      var pool = (T.entries || []).filter(function (en) { return !en.minLevel || lvl >= en.minLevel; });
      for (var i = 0; i < rolls && pool.length; i++) {
        var en = rng.weighted ? rng.weighted(pool) : pool[0];
        if (en.chance != null && rng() > en.chance) continue;
        if (en.table) out = out.concat(I.roll(en.table, opts, depth + 1));
        else if (en.item) { var mn = +en.min || 1, mx = +en.max || mn; out.push({ item: en.item, n: mn + Math.floor(rng() * (mx - mn + 1)) }); }
      }
      if (T.gold) out.push({ gold: Math.round(T.gold[0] + rng() * ((T.gold[1] || T.gold[0]) - T.gold[0])) });
      return out;
    };
    I.drop = function (x, z, items, opts) {
      (items || []).forEach(function (it, i) {
        if (it.gold) { if (rt.player && rt.sys.economy && (!opts || opts.toPlayer !== false)) rt.sys.economy.add(rt.player, it.gold); return; }
        var a = i * 2.1;
        rt.spawn({ kind: 'item', item: it.item, n: it.n, x: x + Math.sin(a) * 0.6, z: z + Math.cos(a) * 0.6, name: I.def(it.item).name, pickup: true });
      });
    };
    I.onSpawn = function (e, spec) {
      if (spec.pickup === true || (e.kind === 'item' && spec.item)) e.comps.pickup = { item: spec.item, n: +spec.n || 1 };
      if (e.kind === 'player') { var st = cfg.start || {}; for (var id in st) I.give(e, id, st[id], true); ((sy.player || rt.world.player || {}).items || []).forEach(function (id2) { I.give(e, id2, 1, true); }); }
    };
    rt.bus.on('death', function (d) {
      var e = d.entity, ek = e.spec.enemy || 'enemy', tid = e.spec.loot || (e.kind === 'enemy' && tables[ek] ? ek : null);
      if (tid) I.drop(e.pos.x, e.pos.z, I.roll(tid, { level: rt.sys.progression ? rt.sys.progression.level(rt.player) : 1 }));
    });
    rt.bus.on('interact', function (d) {
      var t = d && d.target; if (!t || t.kind !== 'chest' || t.flags.open) return;
      t.flags.open = true;
      var items = t.spec.loot ? I.roll(t.spec.loot) : (t.spec.item ? [{ item: t.spec.item, n: +t.spec.n || 1 }] : []);
      if (t.spec.gold) items.push({ gold: +t.spec.gold });
      items.forEach(function (it) { if (it.gold) { if (rt.sys.economy) rt.sys.economy.add(d.by, it.gold); } else I.give(d.by, it.item, it.n); });
      rt.bus.emit('chestOpened', { entity: t, items: items });
    });
    I.save = function () { var o = {}; rt.ents.forEach(function (e) { if (e.comps.inv) o[e.id] = U.copy({ items: e.comps.inv.items, eq: e.comps.inv.eq }); }); return { inv: o, crafting: crafting.map(function (c) { return { e: c.e.id, rid: c.rid, t: c.t }; }) }; };
    I.load = function (d) { d = d || {}; Object.keys(d.inv || {}).forEach(function (id) { var inv = I.of(id); if (inv) { inv.items = d.inv[id].items || []; inv.eq = d.inv[id].eq || {}; } }); };
    return I;
  });

  G.register('progression', 50, function (rt) {
    var sy = rt.sysdata, cfg = sy.progression || {}, P = {};
    var curve = cfg.xp_curve || { base: 40, exp: 1.45 }, trees = cfg.skill_trees || {}, perks = cfg.perks || {};
    var maxLevel = +cfg.max_level || 99, pointsPerLevel = cfg.points_per_level != null ? +cfg.points_per_level : 1;
    P.of = function (e) { e = rt.get(e); if (!e) return null; return e.comps.prog || (e.comps.prog = { level: 1, xp: 0, points: +cfg.start_points || 0, unlocked: {}, perks: {} }); };
    P.need = function (lvl) { return Math.round((+curve.base || 40) * Math.pow(lvl, +curve.exp || 1.45)); };
    P.level = function (e) { var p = P.of(e); return p ? p.level : 1; };
    P.award = function (e, n, why) {
      var p = P.of(e); if (!p || !(n > 0)) return 0;
      p.xp += n; rt.bus.emit('xp', { entity: rt.get(e), amount: n, why: why || null });
      while (p.level < maxLevel && p.xp >= P.need(p.level)) {
        p.xp -= P.need(p.level); p.level++; p.points += pointsPerLevel;
        var ent = rt.get(e); ent.maxHp += +cfg.hp_per_level || 1; ent.hp = ent.maxHp;
        rt.bus.emit('levelUp', { entity: ent, level: p.level });
        Object.keys(perks).forEach(function (pid) { var pk = perks[pid]; if (pk.auto && P.perkOk(ent, pid)) P.grantPerk(ent, pid); });
      }
      return n;
    };
    P.nodeOk = function (e, tree, nid) {
      var T = trees[tree], n = T && T.nodes && T.nodes[nid], p = P.of(e);
      if (!n) return { ok: false, why: 'unknown skill' };
      if (p.unlocked[tree + '.' + nid]) return { ok: false, why: 'already learned' };
      if ((n.cost || 1) > p.points) return { ok: false, why: 'needs ' + (n.cost || 1) + ' skill point(s)' };
      if (n.level && p.level < n.level) return { ok: false, why: 'needs level ' + n.level };
      var req = n.requires || [];
      for (var i = 0; i < req.length; i++) if (!p.unlocked[tree + '.' + req[i]] && !p.unlocked[req[i]]) return { ok: false, why: 'needs ' + ((T.nodes[req[i]] || {}).name || req[i]) };
      return { ok: true };
    };
    P.learn = function (e, tree, nid) {
      var chk = P.nodeOk(e, tree, nid); if (!chk.ok) return chk;
      var p = P.of(e), n = trees[tree].nodes[nid];
      p.points -= n.cost || 1; p.unlocked[tree + '.' + nid] = 1; p.unlocked[nid] = 1;
      if (n.effect && n.effect.ability) p.unlocked[n.effect.ability] = 1;
      if (n.effect && n.effect.perk) P.grantPerk(e, n.effect.perk);
      rt.bus.emit('skillLearned', { entity: rt.get(e), tree: tree, node: nid });
      return { ok: true };
    };
    P.hasUnlock = function (e, id) { var p = P.of(e); return !!(p && (p.unlocked[id] || p.perks[id])); };
    P.perkOk = function (e, pid) {
      var pk = perks[pid], p = P.of(e); if (!pk || p.perks[pid]) return false;
      var r = pk.requires || {};
      if (r.level && p.level < r.level) return false;
      if (r.quest && !(rt.sys.quests && rt.sys.quests.isDone(r.quest))) return false;
      if (r.skill && !p.unlocked[r.skill]) return false;
      return true;
    };
    P.grantPerk = function (e, pid) { var p = P.of(e); if (!perks[pid] || p.perks[pid]) return false; p.perks[pid] = 1; rt.bus.emit('perk', { entity: rt.get(e), perk: pid }); return true; };
    /** Stat after skills, perks, equipment and buffs. base: the caller's base value. */
    P.stat = function (e, name, base) {
      e = rt.get(e); var p = e && e.comps.prog, v = +base || 0, mult = 1;
      if (p) {
        Object.keys(p.unlocked).forEach(function (key) { var parts = key.split('.'), T = trees[parts[0]], n = T && T.nodes && T.nodes[parts[1]]; if (n && n.effect && n.effect.stat === name) { v += +n.effect.add || 0; mult *= +n.effect.mult || 1; } });
        Object.keys(p.perks).forEach(function (pid) { var fx = (perks[pid] || {}).effect || {}; if (fx.stat === name) { v += +fx.add || 0; mult *= +fx.mult || 1; } });
        if (name === 'atk') v += Math.floor((p.level - 1) / (+cfg.levels_per_atk || 3));
      }
      if (rt.sys.inventory && e) { var eq = rt.sys.inventory.equipStats(e); if (eq[name] != null && name !== 'speed') v += eq[name]; if (name === 'speed' && eq.speed) mult *= eq.speed; }
      var f = e && e.comps.fighter;
      if (f) f.statuses.forEach(function (s) { if (s.buff && s.buff.stat === name) { v += +s.buff.add || 0; mult *= +s.buff.mult || 1; } });
      return v * mult;
    };
    rt.bus.on('death', function (d) { var src = d.source; if (src && src.kind === 'player') P.award(src, +d.entity.spec.xp || (d.entity.spec.kind === 'boss' ? 100 : 15), 'defeat'); });
    rt.bus.on('questDone', function (d) { if (d.reward && d.reward.xp && rt.player) P.award(rt.player, +d.reward.xp, 'quest'); });
    P.save = function () { var o = {}; rt.ents.forEach(function (e) { if (e.comps.prog) o[e.id] = U.copy(e.comps.prog); }); return o; };
    P.load = function (d) { Object.keys(d || {}).forEach(function (id) { var e = rt.get(id); if (e) e.comps.prog = d[id]; }); };
    return P;
  });
})(GSYS);
