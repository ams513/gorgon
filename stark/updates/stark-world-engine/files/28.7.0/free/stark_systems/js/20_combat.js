/* Stark World Engine Systems: combat. Melee arcs, projectiles, hitscan, abilities, hit reactions (stagger, knockback, flash,
   hit-stop events), statuses, crits, armour/resistances, ammo + reload, death + loot hand-off.
   CHILD SAFETY: child/teen characters are never hit, targeted, knocked back, given harmful statuses or killed.
   Sweeps skip them silently; direct calls (scripts) on them throw SafetyError.
   systems.combat = {weapons: {id: {type: melee|ranged|hitscan, damage, range, arc, cooldown, knockback, stagger,
     projectile: {speed, life, radius, pierce}, ammo: {clip, reserve, reload}, spread, pellets, dtype}},
     abilities: {id: {cooldown, cost: {stamina|mana: n}, effect: {type: damage|aoe|heal|buff|dash|projectile|shield,
     amount, radius, duration, stat, mult, weapon}}}, hit_reactions: {light|heavy: {stagger, knockback}},
     crit: {chance, mult}, iframes, friendly_fire} */
(function (G) {
  'use strict';
  var U = G.util, S = G.safety;
  var WEAPONS = {
    fists: { type: 'melee', damage: 1, range: 1.4, arc: 100, cooldown: 0.35, knockback: 2, stagger: 0.15 },
    sword: { type: 'melee', damage: 2, range: 2.0, arc: 120, cooldown: 0.4, knockback: 3, stagger: 0.25 },
    bow: { type: 'ranged', damage: 2, range: 18, cooldown: 0.6, projectile: { speed: 22, life: 1.2, radius: 0.25 }, ammo: { clip: 1, reserve: 30, reload: 0.4 } },
    pistol: { type: 'hitscan', damage: 2, range: 30, cooldown: 0.25, spread: 2, ammo: { clip: 8, reserve: 48, reload: 1.1 } },
    shotgun: { type: 'hitscan', damage: 1, range: 12, cooldown: 0.8, spread: 14, pellets: 6, ammo: { clip: 4, reserve: 24, reload: 1.6 } },
    claw: { type: 'melee', damage: 1, range: 1.5, arc: 90, cooldown: 0.9, knockback: 2.5, stagger: 0.2 },
    smash: { type: 'melee', damage: 2, range: 1.9, arc: 110, cooldown: 1.4, knockback: 6, stagger: 0.4, heavy: true },
    spit: { type: 'ranged', damage: 1, range: 12, cooldown: 1.6, projectile: { speed: 10, life: 1.6, radius: 0.3 } }
  };
  G.DEFAULT_WEAPONS = WEAPONS;

  G.register('combat', 20, function (rt) {
    var cfg = rt.sysdata.combat || {}, C = { projectiles: [] };
    var weapons = {}, k;
    for (k in WEAPONS) weapons[k] = WEAPONS[k];
    for (k in (cfg.weapons || {})) weapons[k] = cfg.weapons[k];
    var abilities = cfg.abilities || {}, reactions = cfg.hit_reactions || { light: { stagger: 0.15, knockback: 2 }, heavy: { stagger: 0.45, knockback: 6 } };
    var crit = cfg.crit || { chance: 0.1, mult: 2 }, iframes = cfg.iframes != null ? +cfg.iframes : 0.5;
    C.weapons = weapons; C.abilities = abilities;

    C.onSpawn = function (e, spec) {
      if (!(e.kind === 'player' || e.kind === 'enemy' || spec.weapon || spec.fighter)) return;
      var f = e.comps.fighter = { weapon: spec.weapon || (e.kind === 'player' ? (cfg.player_weapon || 'sword') : 'claw'), cd: 0, stagger: 0, iframes: 0, statuses: [], armor: +spec.armor || 0, resist: spec.resist || {}, abilityCd: {}, ammo: {} };
      var w = weapons[f.weapon]; if (w && w.ammo) f.ammo[f.weapon] = { clip: w.ammo.clip, reserve: w.ammo.reserve, reload: 0 };
      if (e.kind === 'player' || spec.vitals) e.comps.vitals = { stamina: 10, maxStamina: 10, mana: 6, maxMana: 6, regen: 2.5, manaRegen: 0.6 };
    };
    /** Hostility: factions decide when present; children and teens are never hostile and never targets. */
    C.hostile = function (a, b) {
      if (!a || !b || a === b || a.dead || b.dead) return false;
      if (S.isMinor(a) || S.isMinor(b)) return false;
      if (rt.sys.factions && rt.sys.factions.hostile) { var h = rt.sys.factions.hostile(a, b); if (h != null) return h; }
      if (a.team === b.team) return false;
      return (a.team === 'player' && b.team === 'enemy') || (a.team === 'enemy' && b.team === 'player') || (a.team === 'enemy' && b.team === 'ally') || (a.team === 'ally' && b.team === 'enemy');
    };
    C.targetsNear = function (src, r) { return S.filterTargets(rt.near(src.pos, r, function (e) { return C.hostile(src, e); })); };
    function stat(e, name, base) { return rt.sys.progression && rt.sys.progression.stat ? rt.sys.progression.stat(e, name, base) : base; }
    function weaponOf(e, id) { var f = e.comps.fighter || {}; return weapons[id || f.weapon] || weapons.fists; }

    /** One attack with the entity's weapon (or `wid`) toward its facing / aim. -> number of hits. */
    C.attack = function (e, wid, aim) {
      var f = e.comps.fighter;
      if (!f || e.dead || f.cd > 0 || f.stagger > 0) return 0;
      var id = wid || f.weapon, w = weaponOf(e, id);
      if (w.ammo) {
        var am = f.ammo[id] || (f.ammo[id] = { clip: w.ammo.clip, reserve: w.ammo.reserve, reload: 0 });
        if (am.reload > 0) return 0;
        if (am.clip <= 0) { C.reload(e); rt.bus.emit('dryfire', { entity: e }); return 0; }
        am.clip--;
      }
      f.cd = w.cooldown || 0.4;
      var dir = aim ? Math.atan2(aim.x - e.pos.x, aim.z - e.pos.z) : e.rot, hits = 0;
      var dmg = stat(e, 'atk', w.damage || 1);
      rt.bus.emit('attack', { entity: e, weapon: id, type: w.type });
      rt.bus.emit('noise', { x: e.pos.x, z: e.pos.z, r: w.type === 'melee' ? 4 : 14, source: e });
      if (w.type === 'melee') {
        var half = (w.arc || 120) * Math.PI / 360;
        C.targetsNear(e, (w.range || 1.6) + 0.6).forEach(function (t) {
          var d = U.dist(e.pos, t.pos), ang = Math.abs(U.wrap(U.angle(e.pos, t.pos) - dir));
          if (d <= (w.range || 1.6) + ((t.comps.body && t.comps.body.r) || 0.4) && (ang <= half || d < 0.6)) { if (C.hit(t, dmg, e, w)) hits++; }
        });
      } else if (w.type === 'hitscan') {
        var n = w.pellets || 1;
        for (var i = 0; i < n; i++) {
          var a = dir + (rt.rng() - 0.5) * (w.spread || 0) * Math.PI / 180;
          var r = rt.sys.physics ? rt.sys.physics.ray(e.pos.x, e.pos.z, Math.sin(a), Math.cos(a), w.range || 20, { entities: true, ignore: e, filter: function (x) { return !S.isMinor(x); } }) : null;
          if (r && r.entity && C.hostile(e, r.entity) && C.hit(r.entity, dmg, e, w)) hits++;
          rt.bus.emit('tracer', { from: { x: e.pos.x, z: e.pos.z }, to: r ? { x: r.x, z: r.z } : null, entity: e });
        }
      } else {
        var p = w.projectile || {}, sp = p.speed || 15;
        C.projectiles.push({ x: e.pos.x + Math.sin(dir) * 0.6, z: e.pos.z + Math.cos(dir) * 0.6, vx: Math.sin(dir) * sp, vz: Math.cos(dir) * sp, life: p.life || 1.5, r: p.radius || 0.25, owner: e, dmg: dmg, w: w, pierce: p.pierce || 0, hitIds: {} });
      }
      return hits;
    };
    C.reload = function (e) {
      var f = e.comps.fighter; if (!f) return false;
      var w = weaponOf(e), am = f.ammo[f.weapon];
      if (!w.ammo || !am || am.reload > 0 || am.clip >= w.ammo.clip || am.reserve <= 0) return false;
      am.reload = w.ammo.reload || 1; rt.bus.emit('reload', { entity: e }); return true;
    };
    /** Internal hit from a sweep or projectile. Children are skipped (returns false). */
    C.hit = function (t, dmg, src, w) {
      if (!t || t.dead || !S.canHarm(t)) return false;
      var f = t.comps.fighter;
      if (f && f.iframes > 0) return false;
      var isCrit = rt.rng() < (crit.chance || 0), amount = dmg * (isCrit ? (crit.mult || 2) : 1);
      amount = C.applyDamage(t, amount, src, (w && w.dtype) || 'physical', { crit: isCrit, weapon: w });
      if (amount <= 0) return false;
      var heavy = isCrit || (w && w.heavy), re = reactions[heavy ? 'heavy' : 'light'] || {};
      if (f) { f.stagger = Math.max(f.stagger, (w && w.stagger != null ? w.stagger : re.stagger) || 0.15); if (t.kind === 'player') f.iframes = iframes; }
      var kb = (w && w.knockback != null ? w.knockback : re.knockback) || 0;
      if (kb && src && rt.sys.physics) { var a = U.angle(src.pos, t.pos); rt.sys.physics.impulse(t, Math.sin(a) * kb, Math.cos(a) * kb, { attack: true }); }
      t.flags.hit = 0.18;
      rt.bus.emit('hitstop', { t: heavy ? 0.1 : 0.06, crit: isCrit });
      return true;
    };
    /** Damage after armour/resistances. Every damage path goes through here, so children never lose health. */
    C.applyDamage = function (t, amount, src, type, info) {
      if (!t || t.dead) return 0;
      if (!S.canHarm(t)) return 0;
      var f = t.comps.fighter || {}, res = (f.resist && f.resist[type]) || 0;
      var armor = stat(t, 'def', f.armor || 0);
      var a = Math.max(type === 'true' ? amount : 0.25, amount * (1 - U.clamp(res, -1, 0.95)) - armor * 0.5);
      if (rt.sys.env && type === 'fire') a *= rt.sys.env.mod('fire_damage');
      t.hp = Math.max(0, t.hp - a);
      rt.bus.emit('damage', { target: t, amount: a, source: src || null, type: type, crit: !!(info && info.crit) });
      if (t.hp <= 0) C.kill(t, src);
      return a;
    };
    /** Script / dialogue API: damage a target directly. Throws for children (clear error). */
    C.damage = function (t, amount, src, type) { t = rt.get(t); if (!t) return 0; S.guard('damage', t); return C.applyDamage(t, +amount || 0, rt.get(src), type || 'physical'); };
    C.kill = function (t, src) {
      t = rt.get(t); if (!t || t.dead) return false;
      S.guard('kill', t);
      t.dead = true; t.hp = 0; t.vel.x = t.vel.z = 0;
      rt.bus.emit('death', { entity: t, source: src || null, kind: t.kind, enemyKind: t.spec.enemy || null });
      return true;
    };
    C.heal = function (t, n) { t = rt.get(t); if (!t || t.dead) return 0; var before = t.hp; t.hp = Math.min(t.maxHp, t.hp + (+n || 0)); rt.bus.emit('heal', { target: t, amount: t.hp - before }); return t.hp - before; };
    C.revive = function (t, hp) { t = rt.get(t); if (!t) return; t.dead = false; t.hp = Math.min(t.maxHp, hp || t.maxHp); rt.bus.emit('revive', { entity: t }); };
    var HARMFUL = { burn: 1, poison: 1, bleed: 1, stun: 1, slow: 1, freeze: 1, fear: 1, shock: 1 };
    C.applyStatus = function (t, id, dur, opts) {
      t = rt.get(t); if (!t || t.dead) return false;
      opts = opts || {};
      if (HARMFUL[id] || opts.harmful) S.guard('status', t);
      var f = t.comps.fighter || (t.comps.fighter = { statuses: [], abilityCd: {}, ammo: {}, cd: 0, stagger: 0, iframes: 0, armor: 0, resist: {} });
      f.statuses = f.statuses.filter(function (s) { return s.id !== id; });
      f.statuses.push({ id: id, t: +dur || 3, dps: +opts.dps || (id === 'burn' ? 1 : id === 'poison' ? 0.6 : 0), slow: id === 'slow' || id === 'freeze' ? (opts.slow || 0.5) : 0, stun: id === 'stun' || id === 'freeze', buff: opts.buff || null, src: opts.source || null });
      rt.bus.emit('status', { target: t, id: id, duration: +dur || 3 });
      return true;
    };
    C.useAbility = function (e, id, aim) {
      var ab = abilities[id], f = e.comps.fighter, v = e.comps.vitals;
      if (!ab || !f || e.dead) return false;
      if ((f.abilityCd[id] || 0) > 0) return false;
      var cost = ab.cost || {};
      if (v && ((cost.stamina || 0) > v.stamina || (cost.mana || 0) > v.mana)) { rt.bus.emit('noResource', { entity: e, ability: id }); return false; }
      if (rt.sys.progression && ab.requires && !rt.sys.progression.hasUnlock(e, ab.requires)) return false;
      if (v) { v.stamina -= cost.stamina || 0; v.mana -= cost.mana || 0; }
      f.abilityCd[id] = ab.cooldown || 1;
      var fx = ab.effect || {}, dir = aim ? Math.atan2(aim.x - e.pos.x, aim.z - e.pos.z) : e.rot;
      if (fx.type === 'aoe' || fx.type === 'damage') C.targetsNear(e, fx.radius || 3).forEach(function (t) { C.hit(t, stat(e, 'atk', fx.amount || 2), e, { knockback: fx.knockback != null ? fx.knockback : 4, stagger: 0.3, dtype: fx.dtype, heavy: true }); });
      else if (fx.type === 'heal') C.heal(e, fx.amount || 3);
      else if (fx.type === 'dash') { if (rt.sys.physics) rt.sys.physics.impulse(e, Math.sin(dir) * (fx.amount || 14), Math.cos(dir) * (fx.amount || 14)); f.iframes = Math.max(f.iframes, fx.duration || 0.25); }
      else if (fx.type === 'buff' || fx.type === 'shield') C.applyStatus(e, fx.stat ? 'buff_' + fx.stat : 'shield', fx.duration || 5, { buff: { stat: fx.stat || 'def', mult: fx.mult || 1.5, add: fx.add || 0 } });
      else if (fx.type === 'projectile') { var w = weapons[fx.weapon] || weapons.bow; C.projectiles.push({ x: e.pos.x, z: e.pos.z, vx: Math.sin(dir) * ((w.projectile || {}).speed || 18), vz: Math.cos(dir) * ((w.projectile || {}).speed || 18), life: 1.5, r: 0.35, owner: e, dmg: stat(e, 'atk', fx.amount || 3), w: w, pierce: fx.pierce || 0, hitIds: {} }); }
      rt.bus.emit('ability', { entity: e, ability: id, effect: fx.type });
      return true;
    };

    C.update = function (dt) {
      rt.ents.forEach(function (e) {
        var f = e.comps.fighter, v = e.comps.vitals;
        if (v) { v.stamina = Math.min(v.maxStamina, v.stamina + v.regen * dt); v.mana = Math.min(v.maxMana, v.mana + v.manaRegen * dt); }
        if (e.flags.hit > 0) e.flags.hit -= dt;
        if (!f) return;
        if (f.cd > 0) f.cd -= dt;
        if (f.stagger > 0) f.stagger -= dt;
        if (f.iframes > 0) f.iframes -= dt;
        for (var a in f.abilityCd) if (f.abilityCd[a] > 0) f.abilityCd[a] -= dt;
        for (var wid in f.ammo) { var am = f.ammo[wid], w = weapons[wid]; if (am.reload > 0) { am.reload -= dt; if (am.reload <= 0 && w && w.ammo) { var need = w.ammo.clip - am.clip, take = Math.min(need, am.reserve); am.clip += take; am.reserve -= take; am.reload = 0; rt.bus.emit('reloaded', { entity: e }); } } }
        var slow = 1;
        f.statuses = f.statuses.filter(function (s) {
          s.t -= dt;
          if (s.dps && !e.dead && S.canHarm(e)) C.applyDamage(e, s.dps * dt, s.src, s.id === 'burn' ? 'fire' : 'poison');
          if (s.slow) slow *= 1 - s.slow;
          if (s.stun) f.stagger = Math.max(f.stagger, 0.05);
          return s.t > 0;
        });
        e.flags.slow = slow;
      });
      var ph = rt.sys.physics;
      C.projectiles = C.projectiles.filter(function (p) {
        p.life -= dt; if (p.life <= 0) return false;
        var nx = p.x + p.vx * dt, nz = p.z + p.vz * dt;
        if (ph && ph.blocked(nx, nz, 0.05)) { rt.bus.emit('impact', { x: nx, z: nz }); return false; }
        p.x = nx; p.z = nz;
        var keep = true;
        rt.ents.forEach(function (t) {
          if (!keep || t === p.owner || t.dead || p.hitIds[t.id] || !t.comps.body) return;
          if (S.isMinor(t)) return;                          // projectiles pass through children
          var r = (t.comps.body.r || 0.4) + p.r;
          if (Math.abs(t.pos.x - p.x) < r && Math.abs(t.pos.z - p.z) < r && Math.hypot(t.pos.x - p.x, t.pos.z - p.z) < r) {
            if (C.hostile(p.owner, t)) { p.hitIds[t.id] = 1; C.hit(t, p.dmg, p.owner, p.w); if (p.pierce-- <= 0) keep = false; }
            else if (t.comps.body.solid && t !== rt.player) keep = false;
          }
        });
        return keep;
      });
    };
    C.save = function () { var o = {}; rt.ents.forEach(function (e) { var f = e.comps.fighter; if (f) o[e.id] = { weapon: f.weapon, ammo: f.ammo, statuses: f.statuses.filter(function (s) { return !s.src; }) }; }); return o; };
    C.load = function (d) { Object.keys(d || {}).forEach(function (id) { var e = rt.get(id); if (e && e.comps.fighter) { e.comps.fighter.weapon = d[id].weapon; e.comps.fighter.ammo = d[id].ammo || {}; e.comps.fighter.statuses = d[id].statuses || []; } }); };
    return C;
  });
})(GSYS);
