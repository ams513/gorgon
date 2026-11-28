/* Stark World Engine Systems: AI. Perception (sight cone + line of sight, hearing, memory), behaviour trees, state machines,
   squads (formations, shared alerts, flank / surround / swarm / hold), daily schedules, steering + grid A*.
   CHILD SAFETY: perception never reports a child or teen as a target, setTarget() on one throws, and children are
   never squad combatants.
   systems.ai = {behaviors: {id: {type: bt, root: NODE} | {type: fsm, start, states: {s: {do, go: [{if, to, v}]}}}
     | {type: schedule}}, perception: {sight, fov, hearing, memory}, squads: {id: {members, leader, formation,
     tactic, spacing}}, schedules: {id: [{from, to, at: {x, z} | poi id, do, say}]}, pois: {id: {x, z}}}
   NODE = {sel: [NODE]} | {seq: [NODE]} | {not: NODE} | {cool: sec, child: NODE} | {rand: p, child: NODE}
        | {cond: name, v} | {act: name, ...params} */
(function (G) {
  'use strict';
  var U = G.util, S = G.safety;
  var BUILTIN = {
    melee: { type: 'bt', root: { sel: [
      { seq: [{ cond: 'hpBelow', v: 0.2 }, { cond: 'random', v: 0.5 }, { act: 'flee' }] },
      { seq: [{ cond: 'hasTarget' }, { sel: [{ seq: [{ cond: 'targetInRange', v: 1.4 }, { act: 'attack' }] }, { act: 'chase' }] }] },
      { seq: [{ cond: 'heardNoise' }, { act: 'investigate' }] },
      { act: 'patrol' }] } },
    ranged: { type: 'bt', root: { sel: [
      { seq: [{ cond: 'hasTarget' }, { sel: [{ seq: [{ cond: 'targetInRange', v: 9 }, { act: 'keepDistance', d: 6 }, { act: 'attack' }] }, { act: 'chase' }] }] },
      { seq: [{ cond: 'heardNoise' }, { act: 'investigate' }] },
      { act: 'patrol' }] } },
    stalker: { type: 'bt', root: { sel: [
      { seq: [{ cond: 'hasTarget' }, { sel: [{ seq: [{ cond: 'targetInRange', v: 1.2 }, { act: 'attack' }] }, { act: 'chase', speed: 1.15 }] }] },
      { seq: [{ cond: 'heardNoise' }, { act: 'investigate', speed: 0.9 }] },
      { act: 'wander', r: 14 }] } },
    villager: { type: 'schedule' },
    guard: { type: 'fsm', start: 'patrol', states: {
      patrol: { do: 'patrol', go: [{ if: 'hasTarget', to: 'chase' }, { if: 'heardNoise', to: 'investigate' }] },
      investigate: { do: 'investigate', go: [{ if: 'hasTarget', to: 'chase' }, { if: 'arrived', to: 'patrol' }] },
      chase: { do: 'chase', go: [{ if: '!hasTarget', to: 'patrol' }, { if: 'targetInRange', v: 1.5, to: 'attack' }] },
      attack: { do: 'attack', go: [{ if: '!targetInRange', v: 1.9, to: 'chase' }, { if: '!hasTarget', to: 'patrol' }] } } },
    idle: { type: 'bt', root: { act: 'idle' } }
  };
  G.AI_BEHAVIORS = BUILTIN;

  G.register('ai', 30, function (rt) {
    var cfg = rt.sysdata.ai || {}, A = {};
    var behaviors = {}, k;
    for (k in BUILTIN) behaviors[k] = BUILTIN[k];
    for (k in (cfg.behaviors || {})) behaviors[k] = cfg.behaviors[k];
    var per = cfg.perception || {}, schedules = cfg.schedules || {}, pois = cfg.pois || {}, squads = {};
    var noises = [];
    A.behaviors = behaviors;
    rt.bus.on('noise', function (n) { if (n) noises.push({ x: n.x, z: n.z, r: n.r || 6, t: rt.time, src: n.source }); });

    A.onSpawn = function (e, spec) {
      if (spec.ai === 'none' || spec.ai === false) return;
      var wdef = spec.weapon && ((rt.sysdata.combat && rt.sysdata.combat.weapons && rt.sysdata.combat.weapons[spec.weapon]) || G.DEFAULT_WEAPONS[spec.weapon]);
      var beh = spec.ai || spec.behavior || (e.kind === 'enemy' ? (spec.ranged || (wdef && wdef.type !== 'melee') ? 'ranged' : 'melee') : (spec.schedule ? 'villager' : null));
      if (!beh || e.kind === 'player') return;
      e.comps.ai = { behavior: beh, sight: +spec.sight || +per.sight || 9, fov: +spec.fov || +per.fov || 140, hearing: +spec.hearing || +per.hearing || 7, memory: +spec.memory || +per.memory || 4,
        target: null, lastSeen: null, noise: null, home: { x: e.pos.x, z: e.pos.z }, patrolR: +spec.patrol || 4, speed: +spec.speed || (e.kind === 'enemy' ? 3.2 : 2.2),
        want: null, state: null, timer: rt.rng() * 0.1, path: null, pathT: 0, wanderPt: null, cool: {}, schedule: spec.schedule || null, squad: null, attackRange: +spec.attackRange || 0 };
    };
    A.begin = function () {
      Object.keys(cfg.squads || {}).forEach(function (id) {
        var sq = cfg.squads[id], members = (sq.members || []).filter(function (m) { var e = rt.get(m); if (e && S.isMinor(e)) { G.warn('child-safety: "' + e.name + '" removed from squad ' + id); return false; } return !!e; });
        squads[id] = { id: id, members: members, leader: sq.leader && members.indexOf(sq.leader) >= 0 ? sq.leader : members[0], formation: sq.formation || 'wedge', tactic: sq.tactic || 'flank', spacing: +sq.spacing || 2, target: null };
        members.forEach(function (m) { var e = rt.get(m); if (e && e.comps.ai) e.comps.ai.squad = id; });
      });
    };
    A.squads = squads;

    // -------------------------------------------------------------- perception
    function envMod(n) { return rt.sys.env && rt.sys.env.mod ? rt.sys.env.mod(n) : 1; }
    A.canSee = function (e, t) {
      var ai = e.comps.ai; if (!ai || !t || t.dead) return false;
      var d = U.dist(e.pos, t.pos), sight = ai.sight * envMod('vision') * (t.flags.hidden ? 0.35 : 1) * (t.flags.lit === false ? 0.5 : 1);
      if (d > sight) return false;
      var ang = Math.abs(U.wrap(U.angle(e.pos, t.pos) - e.rot)) * 360 / Math.PI;
      if (ang > ai.fov && d > 1.5) return false;
      return !rt.sys.physics || rt.sys.physics.los(e, t);
    };
    function perceive(e, ai) {
      var cands = rt.sys.combat ? rt.sys.combat.targetsNear(e, ai.sight * 1.2 + 2) : [];
      cands = S.filterTargets(cands);                             // children are never targets
      var seen = null, best = 1e9;
      cands.forEach(function (t) { if (A.canSee(e, t)) { var d = U.dist(e.pos, t.pos); if (d < best) { best = d; seen = t; } } });
      if (seen) { if (!ai.target) rt.bus.emit('alert', { entity: e, target: seen }); ai.target = seen; ai.lastSeen = { x: seen.pos.x, z: seen.pos.z, t: rt.time }; if (ai.squad && squads[ai.squad]) squads[ai.squad].target = seen; }
      else if (ai.target && (!ai.lastSeen || rt.time - ai.lastSeen.t > ai.memory || ai.target.dead)) { ai.target = null; rt.bus.emit('lost', { entity: e }); }
      if (!ai.target && ai.squad && squads[ai.squad] && squads[ai.squad].target && !squads[ai.squad].target.dead && !S.isMinor(squads[ai.squad].target)) { ai.target = squads[ai.squad].target; ai.lastSeen = { x: ai.target.pos.x, z: ai.target.pos.z, t: rt.time }; }
      var hear = ai.hearing * envMod('hearing');
      noises.forEach(function (n) { if (n.src === e || rt.time - n.t > 0.2) return; if (U.dist(e.pos, n) <= Math.min(hear, n.r * 1.5)) ai.noise = { x: n.x, z: n.z, t: rt.time }; });
      if (ai.noise && rt.time - ai.noise.t > ai.memory * 2) ai.noise = null;
    }

    // -------------------------------------------------------------- conditions + actions
    var COND = {
      hasTarget: function (e, ai) { return !!ai.target; },
      seesTarget: function (e, ai) { return !!ai.target && A.canSee(e, ai.target); },
      targetInRange: function (e, ai, n) { return !!ai.target && U.dist(e.pos, ai.target.pos) <= (n.v || ai.attackRange || 1.5); },
      hpBelow: function (e, ai, n) { return e.hp / (e.maxHp || 1) < (n.v || 0.3); },
      heardNoise: function (e, ai) { return !!ai.noise; },
      arrived: function (e, ai) { return !ai.want; },
      isNight: function () { return !!(rt.sys.env && rt.sys.env.isNight()); },
      random: function (e, ai, n) { return rt.rng() < (n.v != null ? n.v : 0.5); },
      flag: function (e, ai, n) { return !!e.flags[n.v]; },
      far: function (e, ai, n) { return U.dist(e.pos, ai.home) > (n.v || 10); }
    };
    function go(e, ai, x, z, sp) { ai.want = { x: x, z: z, speed: (sp || 1) * ai.speed }; }
    var ACT = {
      idle: function (e, ai) { ai.want = null; return 'success'; },
      patrol: function (e, ai) {
        if (!ai.wanderPt || U.dist(e.pos, ai.wanderPt) < 0.6 || (ai.cool.patrol || 0) < rt.time) { var a = rt.rng() * Math.PI * 2, r = rt.rng() * ai.patrolR; ai.wanderPt = { x: ai.home.x + Math.sin(a) * r, z: ai.home.z + Math.cos(a) * r }; ai.cool.patrol = rt.time + 3 + rt.rng() * 3; }
        go(e, ai, ai.wanderPt.x, ai.wanderPt.z, 0.5); return 'running';
      },
      wander: function (e, ai, n) { var save = ai.patrolR; ai.patrolR = n.r || ai.patrolR * 2; var r = ACT.patrol(e, ai); ai.patrolR = save; return r; },
      chase: function (e, ai, n) {
        if (!ai.target) return 'failure';
        var tp = A.canSee(e, ai.target) ? ai.target.pos : ai.lastSeen, sq = ai.squad && squads[ai.squad];
        if (sq && sq.members.length > 1 && (sq.tactic === 'flank' || sq.tactic === 'surround')) {
          var idx = sq.members.indexOf(e.id), cnt = sq.members.length, base = U.angle(tp, e.pos);
          var off = sq.tactic === 'surround' ? (idx / cnt) * Math.PI * 2 : (idx % 2 ? 1 : -1) * Math.min(1.2, 0.5 + 0.35 * Math.ceil(idx / 2));
          var ring = U.dist(e.pos, tp) > 4 ? 2.5 : 1.0, a = sq.tactic === 'surround' ? off : base + off;
          go(e, ai, tp.x + Math.sin(a) * ring, tp.z + Math.cos(a) * ring, n.speed || 1);
        } else go(e, ai, tp.x, tp.z, n.speed || 1);
        e.rot = U.angle(e.pos, tp);
        return 'running';
      },
      attack: function (e, ai) {
        if (!ai.target || !rt.sys.combat) return 'failure';
        if (S.isMinor(ai.target)) { ai.target = null; return 'failure'; }
        e.rot = U.angle(e.pos, ai.target.pos);
        ai.want = null;
        rt.sys.combat.attack(e, null, ai.target.pos);
        return 'success';
      },
      keepDistance: function (e, ai, n) {
        if (!ai.target) return 'failure';
        var d = U.dist(e.pos, ai.target.pos), want = n.d || 6, a = U.angle(ai.target.pos, e.pos);
        if (Math.abs(d - want) > 1) go(e, ai, ai.target.pos.x + Math.sin(a) * want, ai.target.pos.z + Math.cos(a) * want, 0.9);
        else { var s = a + (ai.cool.strafeDir || 1) * 0.6; go(e, ai, ai.target.pos.x + Math.sin(s) * want, ai.target.pos.z + Math.cos(s) * want, 0.6); if (rt.rng() < 0.01) ai.cool.strafeDir = -(ai.cool.strafeDir || 1); }
        return 'success';
      },
      flee: function (e, ai) { var from = ai.target ? ai.target.pos : ai.home, a = U.angle(from, e.pos); go(e, ai, e.pos.x + Math.sin(a) * 5, e.pos.z + Math.cos(a) * 5, 1.1); return 'running'; },
      investigate: function (e, ai, n) { if (!ai.noise) return 'failure'; go(e, ai, ai.noise.x, ai.noise.z, n.speed || 0.8); if (U.dist(e.pos, ai.noise) < 1) { ai.noise = null; ai.want = null; return 'success'; } return 'running'; },
      returnHome: function (e, ai) { go(e, ai, ai.home.x, ai.home.z, 0.7); return U.dist(e.pos, ai.home) < 0.8 ? 'success' : 'running'; },
      gotoPoint: function (e, ai, n) { var p = n.poi ? pois[n.poi] : n; if (!p) return 'failure'; go(e, ai, +p.x, +p.z, n.speed || 0.8); return U.dist(e.pos, p) < 0.8 ? 'success' : 'running'; },
      followLeader: function (e, ai) {
        var sq = ai.squad && squads[ai.squad]; if (!sq) return 'failure';
        var L = rt.get(sq.leader); if (!L || L === e) return 'failure';
        var slot = A.formationSlot(sq, e.id, L);
        go(e, ai, slot.x, slot.z, 1); return 'running';
      },
      followSchedule: function (e, ai) {
        var entry = A.scheduleEntry(e); if (!entry) return ACT.patrol(e, ai);
        var p = typeof entry.at === 'string' ? pois[entry.at] : entry.at;
        e.flags.activity = entry.do || 'idle';
        if (p && U.dist(e.pos, p) > 0.8) { go(e, ai, +p.x, +p.z, 0.6); return 'running'; }
        if (entry.say && ai.cool.say !== entry) { ai.cool.say = entry; rt.bus.emit('bark', { entity: e, text: S.safeText(entry.say, '') }); }
        ai.want = null; return 'success';
      },
      say: function (e, ai, n) { if ((ai.cool.sayT || 0) > rt.time) return 'success'; ai.cool.sayT = rt.time + (n.every || 6); rt.bus.emit('bark', { entity: e, text: S.safeText(n.text || '', '') }); return 'success'; },
      callSquad: function (e, ai) { var sq = ai.squad && squads[ai.squad]; if (sq && ai.target) sq.target = ai.target; return 'success'; }
    };
    A.COND = COND; A.ACT = ACT;

    function tick(n, e, ai) {
      if (!n) return 'failure';
      if (n.sel) { for (var i = 0; i < n.sel.length; i++) { var r = tick(n.sel[i], e, ai); if (r !== 'failure') return r; } return 'failure'; }
      if (n.seq) { for (var j = 0; j < n.seq.length; j++) { var r2 = tick(n.seq[j], e, ai); if (r2 !== 'success') return r2; } return 'success'; }
      if (n.not) { var r3 = tick(n.not, e, ai); return r3 === 'success' ? 'failure' : r3 === 'failure' ? 'success' : r3; }
      if (n.cool != null) { var key = 'c' + JSON.stringify(n.child || {}).length + '_' + n.cool; if ((ai.cool[key] || 0) > rt.time) return 'failure'; var r4 = tick(n.child, e, ai); if (r4 === 'success') ai.cool[key] = rt.time + n.cool; return r4; }
      if (n.rand != null) return rt.rng() < n.rand ? tick(n.child, e, ai) : 'failure';
      if (n.cond) { var neg = n.cond[0] === '!', c = COND[neg ? n.cond.slice(1) : n.cond]; var ok = c ? !!c(e, ai, n) : false; return (neg ? !ok : ok) ? 'success' : 'failure'; }
      if (n.act) { var a = ACT[n.act]; return a ? a(e, ai, n) : 'failure'; }
      return 'failure';
    }
    function think(e, ai) {
      var b = behaviors[ai.behavior] || behaviors.idle;
      if (b.type === 'schedule') { if (ai.target && !S.isMinor(e) && e.kind === 'enemy') tick(BUILTIN.melee.root, e, ai); else ACT.followSchedule(e, ai); return; }
      if (b.type === 'fsm') {
        var st = ai.state || b.start, def = b.states[st];
        if (!def) { ai.state = b.start; return; }
        for (var i = 0; i < (def.go || []).length; i++) {
          var g = def.go[i], neg = g.if[0] === '!', c = COND[neg ? g.if.slice(1) : g.if], ok = c ? !!c(e, ai, g) : false;
          if (neg ? !ok : ok) { ai.state = g.to; rt.bus.emit('aiState', { entity: e, state: g.to }); def = b.states[g.to] || def; break; }
        }
        ai.state = ai.state || b.start;
        var act = ACT[def.do]; if (act) act(e, ai, def);
        return;
      }
      tick(b.root, e, ai);
    }

    // -------------------------------------------------------------- schedules, formations, pathing
    A.scheduleEntry = function (e) {
      var ai = e.comps.ai, list = ai && (typeof ai.schedule === 'string' ? schedules[ai.schedule] : ai.schedule);
      if (!Array.isArray(list) || !list.length) return null;
      var h = rt.sys.env ? rt.sys.env.hour() : 12;
      for (var i = 0; i < list.length; i++) { var s = list[i], f = +s.from || 0, t = s.to != null ? +s.to : 24; if (f <= t ? (h >= f && h < t) : (h >= f || h < t)) return s; }
      return list[list.length - 1];
    };
    A.formationSlot = function (sq, id, L) {
      var idx = sq.members.indexOf(id), sp = sq.spacing, back = -1, side = 0, a = L.rot;
      if (sq.formation === 'line') { side = (idx % 2 ? 1 : -1) * Math.ceil(idx / 2) * sp; back = -0.5; }
      else if (sq.formation === 'column') { back = -idx * sp; }
      else if (sq.formation === 'circle') { var ang = idx / sq.members.length * Math.PI * 2; return { x: L.pos.x + Math.sin(ang) * sp * 1.5, z: L.pos.z + Math.cos(ang) * sp * 1.5 }; }
      else { side = (idx % 2 ? 1 : -1) * Math.ceil(idx / 2) * sp; back = -Math.ceil(idx / 2) * sp; }
      return { x: L.pos.x + Math.sin(a) * back + Math.cos(a) * side, z: L.pos.z + Math.cos(a) * back - Math.sin(a) * side };
    };
    /** Grid A* over the physics tile map (8-neighbour, no corner cutting). -> [{x, z}] or null. */
    A.path = function (from, to, maxN) {
      var ph = rt.sys.physics, T = ph && ph.tiles; if (!T) return null;
      var c = T.cell, sx = Math.floor((from.x - T.ox) / c), sz = Math.floor((from.z - T.oz) / c), tx = Math.floor((to.x - T.ox) / c), tz = Math.floor((to.z - T.oz) / c);
      if (ph.solidCell(tx, tz)) return null;
      var key = function (x, z) { return z * T.w + x; }, open = [[sx, sz, 0, 0]], came = {}, g = {}, done = {}, n = 0;
      g[key(sx, sz)] = 0;
      while (open.length && n++ < (maxN || 4000)) {
        var bi = 0; for (var i = 1; i < open.length; i++) if (open[i][3] < open[bi][3]) bi = i;
        var cur = open.splice(bi, 1)[0], ck = key(cur[0], cur[1]);
        if (cur[0] === tx && cur[1] === tz) { var out = [], kk = ck; while (kk !== undefined) { out.unshift({ x: (kk % T.w + 0.5) * c + T.ox, z: (Math.floor(kk / T.w) + 0.5) * c + T.oz }); kk = came[kk]; } return out; }
        if (done[ck]) continue; done[ck] = 1;
        for (var dx = -1; dx <= 1; dx++) for (var dz = -1; dz <= 1; dz++) {
          if (!dx && !dz) continue;
          var nx = cur[0] + dx, nz = cur[1] + dz;
          if (ph.solidCell(nx, nz) || nz < 0 || nz >= T.h) continue;
          if (dx && dz && (ph.solidCell(cur[0] + dx, cur[1]) || ph.solidCell(cur[0], cur[1] + dz))) continue;
          var nk = key(nx, nz), ng = g[ck] + (dx && dz ? 1.414 : 1);
          if (g[nk] === undefined || ng < g[nk]) { g[nk] = ng; came[nk] = ck; open.push([nx, nz, ng, ng + Math.hypot(tx - nx, tz - nz)]); }
        }
      }
      return null;
    };
    function steer(e, ai, dt) {
      var w = ai.want; if (!w) { e.vel.x *= 0.8; e.vel.z *= 0.8; return; }
      var tx = w.x, tz = w.z, ph = rt.sys.physics;
      if (ph && ph.tiles && U.dist(e.pos, w) > 2 && !ph.los(e.pos, w)) {
        if (!ai.path || ai.pathT < rt.time) { ai.path = A.path(e.pos, w); ai.pathT = rt.time + 1; }
        if (ai.path && ai.path.length) { while (ai.path.length > 1 && U.dist(e.pos, ai.path[0]) < 0.5) ai.path.shift(); tx = ai.path[0].x; tz = ai.path[0].z; }
      }
      var dx = tx - e.pos.x, dz = tz - e.pos.z, d = Math.hypot(dx, dz);
      if (d < 0.3) { ai.want = null; e.vel.x = e.vel.z = 0; return; }
      var sp = w.speed * (e.flags.slow || 1) * Math.min(1, d / 1.2 + 0.3), dirA = Math.atan2(dx, dz);
      if (ph) for (var tries = 0; tries < 5; tries++) {                     // feelers: straight, +-40, +-80 degrees
        var a = dirA + [0, 0.7, -0.7, 1.4, -1.4][tries], r = (e.comps.body && e.comps.body.r) || 0.4;
        if (!ph.blocked(e.pos.x + Math.sin(a) * (r + 0.5), e.pos.z + Math.cos(a) * (r + 0.5), r * 0.9)) { dirA = a; break; }
      }
      e.vel.x = Math.sin(dirA) * sp; e.vel.z = Math.cos(dirA) * sp;
      if (!ai.target) e.rot = U.lerp(e.rot, e.rot + U.wrap(dirA - e.rot), Math.min(1, dt * 6));
    }

    A.setBehavior = function (e, id) { e = rt.get(e); if (e && e.comps.ai) { e.comps.ai.behavior = id; e.comps.ai.state = null; } };
    A.setTarget = function (e, t) { e = rt.get(e); t = rt.get(t); if (t) S.guard('target', t); if (e && e.comps.ai) { e.comps.ai.target = t; e.comps.ai.lastSeen = t ? { x: t.pos.x, z: t.pos.z, t: rt.time } : null; } };
    A.capture = function (e, t) { t = rt.get(t); S.guard('capture', t); if (t) { t.flags.captured = true; rt.bus.emit('captured', { by: rt.get(e), entity: t }); } return !!t; };
    A.goTo = function (e, x, z, speed) { e = rt.get(e); if (e && e.comps.ai) { e.comps.ai.want = { x: +x, z: +z, speed: (speed || 1) * e.comps.ai.speed }; e.comps.ai.behavior = 'idle'; } };
    A.update = function (dt) {
      noises = noises.filter(function (n) { return rt.time - n.t < 0.5; });
      rt.ents.forEach(function (e) {
        var ai = e.comps.ai; if (!ai || e.dead || e.flags.captured || e.flags.aiSleep || (e.comps.fighter && e.comps.fighter.stagger > 0)) return;
        ai.timer -= dt;
        if (ai.timer <= 0) { ai.timer = 0.1; perceive(e, ai); think(e, ai); }
        steer(e, ai, dt);
      });
    };
    A.save = function () { var o = {}; rt.ents.forEach(function (e) { if (e.comps.ai) o[e.id] = { state: e.comps.ai.state, behavior: e.comps.ai.behavior, home: e.comps.ai.home }; }); return o; };
    A.load = function (d) { Object.keys(d || {}).forEach(function (id) { var e = rt.get(id); if (e && e.comps.ai) { e.comps.ai.state = d[id].state; e.comps.ai.behavior = d[id].behavior; e.comps.ai.home = d[id].home || e.comps.ai.home; } }); };
    return A;
  });
})(GSYS);
