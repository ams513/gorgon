/* Stark World Engine Systems: genre kits - the rules that turn the shared systems into a specific kind of game. One kit runs per
   world (systems.genre); kits only add rules, every system stays available.
   action_rpg  quest board (radiant), stations, skill/ability keys          platformer  gems, spikes, stomps, goal, platforms
   shooter     waves, ammo/health pickups, score                             survival    needs (food/water/warmth), gather, fire
   horror      flashlight battery, nerve (sanity), keys, exit, hiding        racing      track, checkpoints, laps, AI racers
   puzzle      grid pushing (boxes, targets, switches, doors, ice), undo     strategy    buildings, workers, production, goals
   visual_novel scenes (backgrounds, portraits) on top of the dialogue system
   systems.genre_data = per-kit settings (see SYSTEMS_GUIDE.md). */
(function (G) {
  'use strict';
  var U = G.util, S = G.safety;
  var KITS = {};
  G.GENRES = ['action_rpg', 'platformer', 'shooter', 'survival', 'horror', 'racing', 'puzzle', 'strategy', 'visual_novel', 'custom'];

  KITS.action_rpg = function (rt, d) {
    var K = { score: 0 };
    K.init = function () {
      if (d.board && rt.sys.quests) for (var i = 0; i < (d.board_size || 3); i++) rt.sys.quests.generate();
      rt.bus.on('interact', function (ev) { var t = ev.target; if (t && t.spec.board && rt.sys.quests) { var offered = Object.keys(rt.sys.quests.defs).map(function (k) { return rt.sys.quests.defs[k]; }).filter(function (q) { return q.radiant && rt.sys.quests.state(q.id) === 'none'; }); if (!offered.length) offered = [rt.sys.quests.generate()].filter(Boolean); if (offered[0]) { rt.sys.quests.start(offered[0].id); rt.sys.quests.generate(); } } if (t && t.spec.station && rt.sys.ui) rt.bus.emit('openCraft', { station: t.spec.station }); });
      rt.bus.on('death', function (ev) { if (ev.entity.kind === 'enemy') K.score += 10; if (ev.entity === rt.player) K.respawn(); });
      rt.bus.on('radiantDone', function () { if (rt.sys.quests) rt.sys.quests.generate(); });
    };
    K.respawn = function () { var p = rt.player, sp = d.respawn || rt.world.spawn || { x: 0, z: 0 }; rt.sys.combat.revive(p); p.pos.x = +sp.x || 0; p.pos.z = +sp.z || 0; if (rt.sys.economy) rt.sys.economy.add(p, -Math.floor(rt.sys.economy.gold() * 0.1)); rt.bus.emit('toast', { text: 'You fell... back at the village (lost 10% gold).' }); };
    K.value = function (k) { return k === 'score' ? K.score : undefined; };
    return K;
  };

  KITS.platformer = function (rt, d) {
    var K = { score: 0, total: 0, checkpoint: null, won: false };
    K.init = function () {
      var ph = rt.sys.physics, T = ph.tiles; if (!T) return;
      var rows = T.rows.map(function (r) { return r.split(''); });
      for (var y = 0; y < T.h; y++) for (var x = 0; x < T.w; x++) {
        var c = rows[y][x], at = { x: x + 0.5, y: y + 1, z: 0 };
        if (c === 'o') { rt.spawn({ kind: 'gem', x: at.x, y: at.y - 0.5, name: 'Gem', body: false }, true); K.total++; rows[y][x] = '.'; }
        else if (c === 'P') { rt.player.pos.x = at.x; rt.player.pos.y = at.y; K.checkpoint = { x: at.x, y: at.y }; rows[y][x] = '.'; }
        else if (c === 'e' || c === 'a') { rt.spawn({ kind: 'enemy', x: at.x, y: at.y, hp: 1, name: c === 'a' ? 'Spitter' : 'Slime', walker: true, ai: 'none', team: 'enemy', body: { w: 0.8, h: 0.8, grav: true } }, true); rows[y][x] = '.'; }
        else if (c === 'G') { rt.spawn({ kind: 'goal', x: at.x, y: at.y - 0.5, name: 'Goal', body: false }, true); rows[y][x] = '.'; }
        else if (c === 'C') { rt.spawn({ kind: 'checkpoint', x: at.x, y: at.y - 0.5, name: 'Checkpoint', body: false }, true); rows[y][x] = '.'; }
        else if (c === '^') { rt.spawn({ kind: 'spike', x: at.x, y: at.y - 0.3, body: false }, true); rows[y][x] = '.'; }
        else if (c === 'M') { rt.spawn({ kind: 'platform', x: at.x, y: at.y - 0.5, range: 3, speed: 1.2, body: false }, true); rows[y][x] = '.'; }
      }
      ph.setTiles({ rows: rows.map(function (r) { return r.join(''); }), solid: T.solid, oneway: T.oneway });
      var b = rt.player.comps.body; b.w = 0.7; b.h = 0.9; b.grav = true;
    };
    function overlap(a, b, r) { return Math.abs(a.pos.x - b.pos.x) < (r || 0.7) && Math.abs((a.pos.y - 0.45) - b.pos.y) < (r || 0.8); }
    K.update = function (dt) {
      var p = rt.player; if (!p || K.won) return;
      rt.ents.forEach(function (e) {
        if (e.kind === 'platform') { e.flags.t = (e.flags.t || 0) + dt; var ox = e.flags.ox != null ? e.flags.ox : (e.flags.ox = e.pos.x); var nx = ox + Math.sin(e.flags.t * (e.spec.speed || 1)) * (e.spec.range || 3), dx = nx - e.pos.x; e.pos.x = nx; if (Math.abs(p.pos.x - e.pos.x) < 1.3 && Math.abs(p.pos.y - (e.pos.y - 0.25)) < 0.3 && p.vel.y >= 0) { p.pos.y = e.pos.y - 0.25; p.vel.y = 0; p.comps.body.ground = true; p.pos.x += dx; } }
        if (e.kind === 'gem' && overlap(p, e)) { K.score++; rt.bus.emit('pickup', { entity: e, item: 'gem' }); rt.remove(e); if (K.score === K.total) rt.bus.emit('toast', { text: 'All gems found!' }); }
        if (e.kind === 'checkpoint' && overlap(p, e) && (!K.checkpoint || K.checkpoint.x !== e.pos.x)) { K.checkpoint = { x: e.pos.x, y: e.pos.y + 0.5 }; rt.bus.emit('toast', { text: 'Checkpoint!' }); }
        if (e.kind === 'spike' && overlap(p, e, 0.55)) K.hurt();
        if (e.kind === 'goal' && overlap(p, e)) { K.won = true; rt.bus.emit('win', { by: 'goal' }); }
        if (e.kind === 'enemy' && !e.dead && e.spec.walker) {
          e.vel.x = (e.flags.dir || -1) * 2; if (e.comps.body.wall || (rt.sys.physics.tiles && !rt.sys.physics.solidCell(Math.floor(e.pos.x + (e.flags.dir || -1) * 0.6), Math.floor(e.pos.y + 0.1)))) e.flags.dir = -(e.flags.dir || -1);
          if (overlap(p, e, 0.8)) { if (p.vel.y > 1 && p.pos.y < e.pos.y - 0.2) { rt.sys.combat.applyDamage(e, 99, p, 'stomp'); p.vel.y = -9; K.score += 2; } else K.hurt(); }
        }
      });
      if (p.comps.body.fell) { p.comps.body.fell = false; K.hurt(true); }
    };
    K.hurt = function (fell) {
      var p = rt.player, f = p.comps.fighter;
      if (f && f.iframes > 0 && !fell) return;
      rt.sys.combat.applyDamage(p, fell ? 2 : 1, null, 'true');
      if (f) f.iframes = 1;
      if (p.dead || fell) { rt.sys.combat.revive(p, p.maxHp); var c = K.checkpoint || { x: 2, y: 2 }; p.pos.x = c.x; p.pos.y = c.y; p.vel.x = p.vel.y = 0; }
      else { p.vel.y = -8; p.vel.x = -(p.rot || 1) * 6; }
    };
    K.value = function (k) { return k === 'score' ? K.score + '/' + K.total : undefined; };
    return K;
  };

  KITS.shooter = function (rt, d) {
    var K = { wave: 0, score: 0, alive: 0, t: 0, between: 2 };
    var waves = d.waves || [{ grunt: 4 }, { grunt: 5, archer: 2 }, { grunt: 6, archer: 3, brute: 1 }, { grunt: 8, archer: 4, brute: 2 }];
    var spawnPts = d.spawns || [{ x: -14, z: -10 }, { x: 14, z: -10 }, { x: -14, z: 10 }, { x: 14, z: 10 }];
    function spawnWave() {
      var w = waves[Math.min(K.wave, waves.length - 1)], n = 0, scale = 1 + Math.max(0, K.wave - waves.length + 1) * 0.3, members = [];
      Object.keys(w).forEach(function (kind) { for (var i = 0; i < Math.round(w[kind] * scale); i++) { var sp = spawnPts[(n++) % spawnPts.length]; var e = rt.spawn({ kind: 'enemy', name: kind === 'archer' ? 'Gunner' : kind === 'brute' ? 'Heavy' : 'Grunt', x: sp.x + (rt.rng() - 0.5) * 3, z: sp.z + (rt.rng() - 0.5) * 3, hp: kind === 'brute' ? 8 : 3, weapon: kind === 'archer' ? 'pistol' : 'claw', ai: kind === 'archer' ? 'ranged' : 'melee', sight: 40, fov: 360, team: 'enemy', loot: 'drop' }, true); e.comps.ai.target = rt.player; e.comps.ai.lastSeen = { x: rt.player.pos.x, z: rt.player.pos.z, t: rt.time }; members.push(e.id); } });
      rt.sys.ai.squads['wave' + K.wave] = { id: 'wave' + K.wave, members: members, leader: members[0], formation: 'wedge', tactic: K.wave % 2 ? 'surround' : 'flank', spacing: 2, target: rt.player };
      members.forEach(function (m) { rt.get(m).comps.ai.squad = 'wave' + K.wave; });
      K.wave++; rt.bus.emit('toast', { text: 'Wave ' + K.wave });
    }
    K.update = function (dt) {
      K.alive = rt.query(function (e) { return e.kind === 'enemy' && !e.dead; }).length;
      if (!K.alive) { K.between -= dt; if (K.between <= 0) { K.between = 3; spawnWave(); } }
      rt.ents.forEach(function (e) { if (e.dead && e.kind === 'enemy' && !e.flags.removedAt) e.flags.removedAt = rt.time + 1; if (e.flags.removedAt && rt.time > e.flags.removedAt) rt.remove(e); });
      if (rt.player.dead && !K.over) { K.over = true; rt.bus.emit('toast', { text: 'Game over - score ' + K.score + ' (press R to restart)' }); }
    };
    rt.bus.on('death', function (ev) { if (ev.entity.kind === 'enemy') K.score += ev.entity.maxHp * 10; });
    rt.bus.on('collect', function (ev) {
      if (ev.entity !== rt.player) return;
      var f = rt.player.comps.fighter, inv = rt.sys.inventory;
      if (ev.item === 'ammo_box' && f) { Object.keys(f.ammo).forEach(function (w) { f.ammo[w].reserve += 12; }); inv.take(rt.player, 'ammo_box', ev.n || 1); rt.bus.emit('toast', { text: '+ ammo' }); }
      if (ev.item === 'medkit') { rt.sys.combat.heal(rt.player, 4); inv.take(rt.player, 'medkit', ev.n || 1); }
    });
    K.value = function (k) { return k === 'wave' ? K.wave : k === 'score' ? K.score : undefined; };
    return K;
  };

  KITS.survival = function (rt, d) {
    var K = { needs: { food: 80, water: 80, warmth: 90 }, days: 0 }, rate = d.rates || { food: 0.35, water: 0.5, warmth: 0.25 };
    K.needs.feed = function (e, use) { if (use.food) K.needs.food = Math.min(100, K.needs.food + use.food); if (use.water) K.needs.water = Math.min(100, K.needs.water + use.water); if (use.warmth) K.needs.warmth = Math.min(100, K.needs.warmth + use.warmth); };
    K.init = function () {
      rt.bus.on('interact', function (ev) {
        var t = ev.target; if (!t) return;
        var res = t.spec.resource;
        if (res) { var inv = rt.sys.inventory, tool = t.spec.tool; if (tool && !inv.has(rt.player, tool)) { rt.bus.emit('toast', { text: 'You need a ' + inv.def(tool).name }); return; } inv.give(rt.player, res, t.spec.yield || 1); t.flags.uses = (t.flags.uses || 0) + 1; if (t.flags.uses >= (t.spec.uses || 3)) rt.remove(t); }
        if (t.spec.water) { K.needs.water = 100; rt.bus.emit('toast', { text: 'You drink.' }); }
      });
      rt.bus.on('craft', function (ev) { var out = ev.out || {}; if (out.campfire) { rt.sys.inventory.take(rt.player, 'campfire', 1); var p = rt.player; rt.spawn({ kind: 'campfire', name: 'Campfire', x: p.pos.x + Math.sin(p.rot), z: p.pos.z + Math.cos(p.rot), station: 'fire', fuel: 120 }, true); } });
      rt.bus.on('dawn', function () { K.days++; rt.bus.emit('toast', { text: 'You survived night ' + K.days + '!' }); if (K.days >= (d.days_to_win || 3)) rt.bus.emit('win', { by: 'survival' }); });
    };
    K.update = function (dt) {
      var p = rt.player, env = rt.sys.env; if (!p || p.dead) return;
      var nearFire = rt.near(p.pos, 4, function (e) { return e.kind === 'campfire' && (e.spec.fuel || 0) > 0; }).length > 0;
      K.needs.food -= rate.food * dt; K.needs.water -= rate.water * dt * (env && env.weather().type === 'embers' ? 1.5 : 1);
      var cold = (env ? (1 - env.mod('warmth')) * 2 + (env.isNight() ? 0.6 : 0) : 0.2);
      K.needs.warmth += nearFire ? 6 * dt : -rate.warmth * dt * (1 + cold * 2);
      rt.ents.forEach(function (e) { if (e.kind === 'campfire') { e.spec.fuel -= dt * (env && (env.weather().type === 'rain' || env.weather().type === 'storm') ? 3 : 1); if (e.spec.fuel <= 0 && !e.flags.out) { e.flags.out = true; rt.bus.emit('toast', { text: 'The campfire went out.' }); } } });
      ['food', 'water', 'warmth'].forEach(function (n) { K.needs[n] = U.clamp(K.needs[n], 0, 100); if (K.needs[n] <= 0) rt.sys.combat.applyDamage(p, 0.25 * dt, null, 'true'); });
      if (p.dead && !K.over) { K.over = true; rt.bus.emit('toast', { text: 'You did not survive. Press R to try again.' }); }
    };
    K.value = function (k) { var m = String(k).split('.'); if (m[0] === 'needs') return Math.round(K.needs[m[1]] || 0); return undefined; };
    return K;
  };

  KITS.horror = function (rt, d) {
    var K = { battery: 100, sanity: 100, keys: 0, keysNeeded: d.keys || 3, beat: 0, light: true };
    K.init = function () {
      rt.bus.on('pickup', function (ev) { if (ev.item === 'old_key') { K.keys++; rt.bus.emit('toast', { text: 'Key ' + K.keys + '/' + K.keysNeeded }); } if (ev.item === 'battery') K.battery = Math.min(100, K.battery + 40); });
      rt.bus.on('interact', function (ev) { var t = ev.target; if (!t) return; if (t.kind === 'exit') { if (K.keys >= K.keysNeeded) rt.bus.emit('win', { by: 'escape' }); else rt.bus.emit('toast', { text: 'Locked. ' + (K.keysNeeded - K.keys) + ' more key(s).' }); } if (t.spec.hide) { rt.player.flags.hidden = !rt.player.flags.hidden; rt.bus.emit('toast', { text: rt.player.flags.hidden ? 'You hide... (E to leave)' : 'You step out.' }); } });
      rt.bus.on('damage', function (ev) { if (ev.target === rt.player) K.sanity = Math.max(0, K.sanity - 15); });
      rt.bus.on('death', function (ev) { if (ev.entity === rt.player && !K.over) { K.over = true; rt.bus.emit('toast', { text: 'It caught you. Press R to try again.' }); } });
    };
    K.toggleLight = function () { if (K.battery > 0) K.light = !K.light; };
    K.update = function (dt) {
      var p = rt.player; if (!p) return;
      if (K.light) { K.battery = Math.max(0, K.battery - dt * (d.drain || 1.2)); if (K.battery <= 0) { K.light = false; rt.bus.emit('toast', { text: 'The flashlight dies.' }); } }
      p.flags.lit = K.light; p.flags.lightSource = K.light;
      if (rt.input.run && (rt.input.move.x || rt.input.move.y)) rt.bus.emit('noise', { x: p.pos.x, z: p.pos.z, r: 9, source: p });
      var near = 1e9; rt.ents.forEach(function (e) { if (e.kind === 'enemy' && !e.dead) near = Math.min(near, U.dist(e.pos, p.pos)); });
      if (near < 7) K.sanity = Math.max(0, K.sanity - dt * (7 - near) * 1.2); else K.sanity = Math.min(100, K.sanity + dt * (p.flags.hidden ? 4 : 1.2));
      K.beat -= dt; if (near < 10 && K.beat <= 0 && rt.sys.audio) { rt.sys.audio.play('heartbeat', null, { vol: U.clamp(1 - near / 10, 0.1, 1) }); K.beat = 0.3 + near * 0.08; }
      if (K.sanity <= 0 && !K.over) { K.over = true; rt.bus.emit('toast', { text: 'Your nerve breaks. Press R to try again.' }); }
      if (p.flags.hidden) { p.vel.x = p.vel.z = 0; }
    };
    K.value = function (k) { return k === 'battery' ? K.battery : k === 'sanity' ? K.sanity : undefined; };
    return K;
  };

  KITS.racing = function (rt, d) {
    var K = { laps: d.laps || 3, track: d.track || [], width: d.width || 9, racers: [], finished: [], t: 0, started: false, countdown: 3 };
    function segDist(p, a, b) { var dx = b.x - a.x, dz = b.z - a.z, l2 = dx * dx + dz * dz || 1, t = U.clamp(((p.x - a.x) * dx + (p.z - a.z) * dz) / l2, 0, 1); return Math.hypot(p.x - (a.x + dx * t), p.z - (a.z + dz * t)); }
    K.onTrack = function (p) { var T = K.track, best = 1e9; for (var i = 0; i < T.length; i++) best = Math.min(best, segDist(p, T[i], T[(i + 1) % T.length])); return best <= K.width / 2; };
    K.init = function () {
      var T = K.track; if (!T.length) return;
      var a = U.angle(T[0], T[1]), n = 0;
      [rt.player].concat(rt.query(function (e) { return e.kind === 'vehicle'; })).forEach(function (e) {
        var row = Math.floor(n / 2), side = n % 2 ? 1 : -1; n++;
        e.pos.x = T[0].x - Math.sin(a) * (2 + row * 4) + Math.cos(a) * side * 2; e.pos.z = T[0].z - Math.cos(a) * (2 + row * 4) - Math.sin(a) * side * 2; e.rot = Math.atan2(Math.sin(a), -Math.cos(a));
        e.rot = Math.atan2(T[1].x - T[0].x, -(T[1].z - T[0].z));
        e.flags.race = { lap: 0, cp: 0, done: false, time: 0 };
        if (e !== rt.player) { e.flags.ai = { move: { x: 0, y: 0 }, alt: false }; e.comps.ctrl = e.comps.ctrl || null; }
        K.racers.push(e);
      });
      rt.inputLocked = true;
    };
    K.update = function (dt) {
      if (!K.track.length) return;
      if (!K.started) { K.countdown -= dt; if (K.countdown <= 0) { K.started = true; rt.inputLocked = false; rt.bus.emit('toast', { text: 'GO!' }); } return; }
      K.t += dt;
      rt.hudVars = rt.hudVars || {}; rt.hudVars.timer = Math.floor(K.t / 60) + ':' + ('0' + (K.t % 60).toFixed(1)).slice(-4);
      var T = K.track;
      K.racers.forEach(function (e) {
        var r = e.flags.race; if (r.done) return;
        r.time += dt;
        e.flags.surfaceGrip = K.onTrack(e.pos) ? 1 : 0.45;
        if (!K.onTrack(e.pos) && e.comps.ctrl) { e.vel.x *= 1 - dt * 1.2; e.vel.z *= 1 - dt * 1.2; }
        var next = T[(r.cp + 1) % T.length];
        if (U.dist(e.pos, next) < K.width) { r.cp = (r.cp + 1) % T.length; if (r.cp === 0) { r.lap++; if (e === rt.player) rt.bus.emit('toast', { text: r.lap >= K.laps ? 'Finish!' : 'Lap ' + (r.lap + 1) + '/' + K.laps }); if (r.lap >= K.laps) { r.done = true; K.finished.push(e.id); if (e === rt.player) rt.bus.emit(K.finished.length === 1 ? 'win' : 'toast', { by: 'race', text: 'You finished #' + K.finished.length }); } } }
        if (e.flags.ai) {                                       // AI driver: aim at a point ahead on the racing line
          var tgt = T[(r.cp + 2) % T.length], want = Math.atan2(tgt.x - e.pos.x, -(tgt.z - e.pos.z)), diff = U.wrap(want - e.rot), c = e.comps.ctrl;
          e.flags.ai.move.x = U.clamp(diff * 2.2, -1, 1);
          e.flags.ai.move.y = Math.abs(diff) > 0.6 && c && c.speedNow > 14 ? 0.2 : (e.spec.skill || 0.92);
        }
      });
    };
    K.position = function () { var ord = K.racers.slice().sort(function (a, b) { var ra = a.flags.race, rb = b.flags.race; return (rb.lap * 1000 + rb.cp) - (ra.lap * 1000 + ra.cp); }); return ord.indexOf(rt.player) + 1; };
    K.value = function (k) { var r = rt.player && rt.player.flags.race; if (k === 'lap') return r ? Math.min(K.laps, r.lap + 1) : 1; if (k === 'laps') return K.laps; if (k === 'speed') return rt.player && rt.player.comps.ctrl ? Math.round(rt.player.comps.ctrl.speedNow * 3.6) : 0; if (k === 'position') return K.position(); return undefined; };
    return K;
  };

  KITS.puzzle = function (rt, d) {
    var K = { levels: d.levels || [], li: 0, grid: null, moves: 0, hist: [], won: false };
    function load(i) {
      var rows = K.levels[i] || ['#####', '#P.x#', '#.b.#', '#####'];
      K.grid = rows.map(function (r) { return r.split(''); }); K.boxes = []; K.targets = []; K.switches = []; K.player = { x: 1, y: 1 }; K.moves = 0; K.hist = [];
      K.grid.forEach(function (row, y) { row.forEach(function (c, x) { if (c === 'P') { K.player = { x: x, y: y }; row[x] = '.'; } if (c === 'b') { K.boxes.push({ x: x, y: y }); row[x] = '.'; } if (c === 'B') { K.boxes.push({ x: x, y: y }); K.targets.push({ x: x, y: y }); row[x] = '.'; } if (c === 'x') K.targets.push({ x: x, y: y }); if (c === 's') K.switches.push({ x: x, y: y }); }); });
      K.li = i; K.won = false;
      rt.hudVars = rt.hudVars || {}; rt.hudVars.puzzle = (i + 1) + '/' + K.levels.length;
    }
    function boxAt(x, y) { for (var i = 0; i < K.boxes.length; i++) if (K.boxes[i].x === x && K.boxes[i].y === y) return i; return -1; }
    function doorsOpen() { return K.switches.every(function (s) { return boxAt(s.x, s.y) >= 0 || (K.player.x === s.x && K.player.y === s.y); }); }
    function wall(x, y) { var c = (K.grid[y] || [])[x]; return c === undefined || c === '#' || (c === 'd' && !doorsOpen()); }
    K.init = function () { load(0); };
    /** One grid move; boxes slide on ice ('i'). -> true if moved. */
    K.move = function (dx, dy) {
      if (K.won) return false;
      var nx = K.player.x + dx, ny = K.player.y + dy;
      if (wall(nx, ny)) return false;
      var bi = boxAt(nx, ny);
      if (bi >= 0) {
        var bx = nx + dx, by = ny + dy;
        if (wall(bx, by) || boxAt(bx, by) >= 0) return false;
        K.hist.push({ p: { x: K.player.x, y: K.player.y }, b: K.boxes.map(function (b) { return { x: b.x, y: b.y }; }) });
        while (K.grid[by][bx] === 'i' && !wall(bx + dx, by + dy) && boxAt(bx + dx, by + dy) < 0) { bx += dx; by += dy; }
        K.boxes[bi] = { x: bx, y: by };
      } else K.hist.push({ p: { x: K.player.x, y: K.player.y }, b: K.boxes.map(function (b) { return { x: b.x, y: b.y }; }) });
      K.player = { x: nx, y: ny }; K.moves++;
      rt.bus.emit('sfx', { name: bi >= 0 ? 'door' : 'step' });
      if (K.targets.length && K.targets.every(function (t) { return boxAt(t.x, t.y) >= 0; })) {
        K.won = true; rt.bus.emit('toast', { text: 'Solved in ' + K.moves + ' moves!' });
        if (K.li + 1 < K.levels.length) rt.after(1.2, function () { load(K.li + 1); }); else rt.bus.emit('win', { by: 'puzzle' });
      }
      return true;
    };
    K.undo = function () { var h = K.hist.pop(); if (h) { K.player = h.p; K.boxes = h.b; K.moves = Math.max(0, K.moves - 1); } };
    K.restart = function () { load(K.li); };
    K.doorsOpen = doorsOpen;
    K.value = function (k) { return k === 'moves' ? K.moves : k === 'puzzle' ? (K.li + 1) + '/' + K.levels.length : undefined; };
    return K;
  };

  KITS.strategy = function (rt, d) {
    var K = { res: Object.assign({ wood: 30, stone: 10, food: 30, gold: 0 }, d.start || {}), buildings: [], defs: d.buildings || {}, goal: d.goal || { population: 12 }, pop: 0, t: 0, sel: null };
    K.init = function () {
      rt.bus.on('hour', function (ev) { if (ev.hour === 6) { var houses = K.buildings.filter(function (b) { return K.defs[b.type] && K.defs[b.type].housing; }).reduce(function (a, b) { return a + K.defs[b.type].housing; }, 0); if (K.res.food > K.pop * 2 && K.pop < houses) { K.addWorker(); rt.bus.emit('toast', { text: 'A new villager arrived (' + K.pop + ')' }); } } });
      for (var i = 0; i < (d.workers || 3); i++) K.addWorker();
    };
    K.addWorker = function () { K.pop++; var e = rt.spawn({ kind: 'npc', name: G.util.rng(K.pop * 77).pick(['Ada', 'Bram', 'Cora', 'Dov', 'Esme', 'Finn', 'Gil', 'Hana', 'Ivo', 'Juna']) + ' ' + K.pop, x: (rt.rng() - 0.5) * 4, z: (rt.rng() - 0.5) * 4, ai: 'idle', team: 'neutral', worker: true }, true); e.flags.job = null; return e; };
    K.canAfford = function (type) { var c = (K.defs[type] || {}).cost || {}; return Object.keys(c).every(function (k) { return (K.res[k] || 0) >= c[k]; }); };
    /** Place a building on the grid (cell size 2 m). -> {ok, why}. */
    K.build = function (type, x, z) {
      var def = K.defs[type]; if (!def) return { ok: false, why: 'unknown building' };
      if (!K.canAfford(type)) return { ok: false, why: 'not enough resources' };
      x = Math.round(x / 2) * 2; z = Math.round(z / 2) * 2;
      if (K.buildings.some(function (b) { return Math.abs(b.x - x) < 3 && Math.abs(b.z - z) < 3; })) return { ok: false, why: 'too close to another building' };
      var c = def.cost || {}; Object.keys(c).forEach(function (k) { K.res[k] -= c[k]; });
      var b = { type: type, x: x, z: z, workers: [], prog: 0 }; K.buildings.push(b);
      rt.spawn({ kind: 'building', name: def.name || type, x: x, z: z, building: type, body: false }, true);
      rt.bus.emit('built', { type: type, x: x, z: z }); return { ok: true };
    };
    K.update = function (dt) {
      K.t += dt;
      var night = rt.sys.env && rt.sys.env.isNight(), idle = rt.query(function (e) { return e.spec.worker && !e.flags.job; });
      K.buildings.forEach(function (b) {
        var def = K.defs[b.type] || {};
        while ((def.workers || 0) > b.workers.length && idle.length) { var w = idle.shift(); w.flags.job = b; b.workers.push(w.id); }
        if (!def.produces || night) return;
        var working = b.workers.filter(function (id) { var w = rt.get(id); return w && U.dist(w.pos, b) < 2.5; }).length;
        if (def.consumes && Object.keys(def.consumes).some(function (k) { return (K.res[k] || 0) < def.consumes[k] * dt * working; })) return;
        Object.keys(def.produces).forEach(function (k) { K.res[k] = (K.res[k] || 0) + def.produces[k] * dt * working * (rt.sys.env ? rt.sys.env.mod('work') : 1); });
        if (def.consumes) Object.keys(def.consumes).forEach(function (k) { K.res[k] -= def.consumes[k] * dt * working; });
      });
      K.res.food = Math.max(0, K.res.food - K.pop * 0.02 * dt);
      rt.query(function (e) { return e.spec.worker; }).forEach(function (w) { var job = w.flags.job, home = { x: 0, z: 0 }; var dest = night || !job ? home : job; if (U.dist(w.pos, dest) > 1.8 && w.comps.ai) rt.sys.ai.goTo(w, dest.x + (U.hash(w.id) % 3 - 1), dest.z + 1.5, 1); });
      if (!K.won && ((K.goal.population && K.pop >= K.goal.population) || (K.goal.gold && K.res.gold >= K.goal.gold))) { K.won = true; rt.bus.emit('win', { by: 'goal' }); }
    };
    K.value = function (k) { if (k === 'resources') return Object.keys(K.res).map(function (r) { return r + ' ' + Math.floor(K.res[r]); }).join('   ') + '   pop ' + K.pop; return undefined; };
    return K;
  };

  KITS.visual_novel = function (rt, d) {
    var K = { scene: null, shown: [], bg: d.bg || 'dusk' };
    K.init = function () {
      rt.bus.on('dialogueView', function (v) { K.view(v); });
      rt.after(0.2, function () { var first = d.start || Object.keys(rt.sys.dialogue.dialogs)[0]; if (first) rt.bus.emit('dialogueView', rt.sys.dialogue.start(first, d.narrator || null)); });
    };
    K.view = function (v) { if (!v) return; var D = rt.sys.dialogue, c = D.cur, n = c && c.d.nodes[c.node]; if (n) { if (n.bg) K.bg = n.bg; if (n.show) K.shown = n.show.slice(); if (n.hide) K.shown = K.shown.filter(function (x) { return n.hide.indexOf(x) < 0; }); } };
    K.value = function () { return undefined; };
    return K;
  };
  KITS.custom = function () { return {}; };

  G.register('genres', 97, function (rt) {
    var g = rt.sysdata.genre || 'custom', d = rt.sysdata.genre_data || {}, kit = (KITS[g] || KITS.custom)(rt, d);
    var GN = { genre: g, kit: kit, needs: kit.needs || null };
    GN.begin = function () { if (kit.init) kit.init(); };
    GN.update = function (dt) { if (kit.update) kit.update(dt); };
    GN.value = function (k) { return kit.value ? kit.value(k) : undefined; };
    GN.save = function () { var o = {}; ['score', 'wave', 'needs', 'battery', 'sanity', 'keys', 'res', 'pop', 'li', 'days'].forEach(function (k) { if (kit[k] !== undefined && typeof kit[k] !== 'function') o[k] = U.copy(kit[k]); }); return o; };
    GN.load = function (o) { Object.keys(o || {}).forEach(function (k) { if (k === 'needs') { for (var n in o.needs) kit.needs[n] = o.needs[n]; } else kit[k] = o[k]; }); };
    return GN;
  });
  G.KITS = KITS;
})(GSYS);
