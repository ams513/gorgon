/* Stark World Engine Systems: physics + character controllers.
   Two coordinate modes. 'plane' (top-down, third-person, first-person, vehicle): x right, z down-screen / forward is
   -z, y up. 'side' (platformer): x right, y DOWN (tile rows), units = tiles. Bodies are circles (plane) or boxes
   (side) against static boxes, a tile grid and each other. Controllers read rt.input:
   move {x: right +1, y: forward/up +1}, look {dx, dy}, aim {x, z}, jump, jumpHeld, run, alt.
   systems.physics = {mode, gravity, ground, tiles: {rows: [...], solid: '#', oneway: '=', cell: 1}}
   systems.controller = {type: third_person|first_person|top_down|side_scroll|vehicle|point_click|none, speed, ...} */
(function (G) {
  'use strict';
  var U = G.util;
  var DEF = {
    top_down: { speed: 6, run: 1.6, accel: 40, friction: 30 },
    third_person: { speed: 6, run: 1.6, accel: 30, friction: 26, jump: 8.5, airControl: 0.4 },
    first_person: { speed: 5, run: 1.7, accel: 40, friction: 34, jump: 7.5, sens: 0.0025, airControl: 0.5 },
    side_scroll: { speed: 9, run: 1.4, accel: 70, airAccel: 40, friction: 60, jump: 15.5, coyote: 0.1, buffer: 0.12, cut: 0.45, maxFall: 22, gravity: 46 },
    vehicle: { maxSpeed: 26, accel: 18, brake: 30, reverse: 8, steer: 2.4, grip: 6, drag: 0.35, boost: 14, boostTime: 1.5, boostCool: 4 },
    point_click: { speed: 4.5 },
    none: {}
  };
  G.CONTROLLERS = Object.keys(DEF);

  G.register('physics', 10, function (rt) {
    var cfg = rt.sysdata.physics || {}, ctl = rt.sysdata.controller || {};
    var P = { statics: [], zones: [], tiles: null, projectiles: [] };
    P.mode = cfg.mode || (ctl.type === 'side_scroll' ? 'side' : 'plane');
    P.gravity = U.num(cfg.gravity, P.mode === 'side' ? (ctl.gravity || DEF.side_scroll.gravity) : 24);
    P.ground = U.num(cfg.ground, 0);
    P.bounds = cfg.bounds || null;            // {x0, z0, x1, z1}
    var terrain = rt.world.terrain != null && rt.world.mode !== '2d' && cfg.flat !== true ? { amp: +rt.world.terrain, seed: +rt.world.seed || 0 } : null;

    /** Same height field as the studio's terrainH(), so GSYS bodies stand on the terrain the studio draws. */
    P.groundAt = function (x, z) {
      if (!terrain) return P.ground;
      var s = terrain.seed, v = Math.sin(x * 0.13 + s) * 0.6 + Math.cos(z * 0.11 + s * 0.7) * 0.6 + Math.sin((x + z) * 0.05 + s) * 0.8 + Math.cos(x * 0.045 - z * 0.06 + s * 0.4) * 0.9 + Math.sin(x * 0.29 + z * 0.23 + s * 1.3) * 0.18;
      return P.ground + terrain.amp * v * 0.55;
    };
    P.addBox = function (x0, z0, x1, z1, extra) { var b = { x0: Math.min(x0, x1), z0: Math.min(z0, z1), x1: Math.max(x0, x1), z1: Math.max(z0, z1) }; if (extra) for (var k in extra) b[k] = extra[k]; P.statics.push(b); return b; };
    P.setTiles = function (t) {
      if (!t || !Array.isArray(t.rows) || !t.rows.length) { P.tiles = null; return; }
      P.tiles = { rows: t.rows, w: t.rows[0].length, h: t.rows.length, cell: +t.cell || 1, solid: t.solid || '#', oneway: t.oneway || '=', ox: +t.ox || 0, oz: +t.oz || 0 };
    };
    P.tileAt = function (cx, cz) { var T = P.tiles; if (!T || cz < 0 || cz >= T.h || cx < 0 || cx >= T.w) return T && (cz >= T.h) ? T.solid[0] : ' '; return T.rows[cz][cx]; };
    P.solidCell = function (cx, cz) { var T = P.tiles; if (!T) return false; if (cx < 0 || cx >= T.w) return true; if (cz < 0) return false; if (cz >= T.h) return false; return T.solid.indexOf(T.rows[cz][cx]) >= 0; };
    if (cfg.tiles) P.setTiles(cfg.tiles);
    (cfg.boxes || []).forEach(function (b) { P.addBox(b.x0, b.z0, b.x1, b.z1, b); });
    (rt.sysdata.zones || []).forEach(function (z) { P.zones.push({ id: z.id, x: +z.x || 0, z: +z.z || 0, r: +z.r || 2, box: z.box || null, data: z, inside: new Set() }); });

    // -------------------------------------------------------------- queries
    function boxHit(x, z, r) {
      for (var i = 0; i < P.statics.length; i++) { var b = P.statics[i]; if (b.oneway || b.nonsolid) continue; if (x > b.x0 - r && x < b.x1 + r && z > b.z0 - r && z < b.z1 + r) return b; }
      if (P.tiles && P.mode === 'plane') {
        var T = P.tiles, c = T.cell, x0 = Math.floor((x - r - T.ox) / c), x1 = Math.floor((x + r - T.ox) / c), z0 = Math.floor((z - r - T.oz) / c), z1 = Math.floor((z + r - T.oz) / c);
        for (var cx = x0; cx <= x1; cx++) for (var cz = z0; cz <= z1; cz++) if (cz >= 0 && cz < T.h && cx >= 0 && cx < T.w && T.solid.indexOf(T.rows[cz][cx]) >= 0) return { tile: true, cx: cx, cz: cz };
      }
      return null;
    }
    P.blocked = function (x, z, r) {
      if (P.bounds && (x < P.bounds.x0 + r || x > P.bounds.x1 - r || z < P.bounds.z0 + r || z > P.bounds.z1 - r)) return { bounds: true };
      return boxHit(x, z, r || 0.3);
    };
    /** Ray on the ground plane: -> {dist, x, z, hit: box|tile|entity|null}. opts.entities: also test bodies. */
    P.ray = function (x, z, dx, dz, maxD, opts) {
      opts = opts || {};
      var l = Math.hypot(dx, dz) || 1; dx /= l; dz /= l;
      var best = { dist: maxD, x: x + dx * maxD, z: z + dz * maxD, hit: null };
      P.statics.forEach(function (b) {
        if (b.nonsolid) return;
        var t0 = 0, t1 = best.dist, ok = true;
        [[x, dx, b.x0, b.x1], [z, dz, b.z0, b.z1]].forEach(function (a) {
          if (!ok) return;
          if (Math.abs(a[1]) < 1e-9) { if (a[0] < a[2] || a[0] > a[3]) ok = false; return; }
          var ta = (a[2] - a[0]) / a[1], tb = (a[3] - a[0]) / a[1]; if (ta > tb) { var t = ta; ta = tb; tb = t; }
          t0 = Math.max(t0, ta); t1 = Math.min(t1, tb); if (t0 > t1) ok = false;
        });
        if (ok && t0 < best.dist) best = { dist: t0, x: x + dx * t0, z: z + dz * t0, hit: b };
      });
      if (P.tiles) {      // DDA over the grid
        var T = P.tiles, c = T.cell, px = (x - T.ox) / c, pz = (z - T.oz) / c, cx = Math.floor(px), cz = Math.floor(pz);
        var sx = dx > 0 ? 1 : -1, sz = dz > 0 ? 1 : -1, tdx = Math.abs(1 / (dx || 1e-9)), tdz = Math.abs(1 / (dz || 1e-9));
        var tmx = (dx > 0 ? cx + 1 - px : px - cx) * tdx, tmz = (dz > 0 ? cz + 1 - pz : pz - cz) * tdz, t = 0;
        for (var n = 0; n < 512 && t * c < best.dist; n++) {
          if (tmx < tmz) { cx += sx; t = tmx; tmx += tdx; } else { cz += sz; t = tmz; tmz += tdz; }
          if (cx < 0 || cz < 0 || cx >= T.w || cz >= T.h) break;
          if (T.solid.indexOf(T.rows[cz][cx]) >= 0) { if (t * c < best.dist) best = { dist: t * c, x: x + dx * t * c, z: z + dz * t * c, hit: { tile: true, cx: cx, cz: cz } }; break; }
        }
      }
      if (opts.entities) rt.ents.forEach(function (e) {
        if (e === opts.ignore || e.dead || !e.comps.body || (opts.filter && !opts.filter(e))) return;
        var r = e.comps.body.r || 0.4, ox = e.pos.x - x, oz = e.pos.z - z, tc = ox * dx + oz * dz;
        if (tc < 0 || tc > best.dist) return;
        var d2 = ox * ox + oz * oz - tc * tc;
        if (d2 > r * r) return;
        var th = tc - Math.sqrt(r * r - d2);
        if (th < best.dist) best = { dist: Math.max(0, th), x: x + dx * th, z: z + dz * th, hit: e, entity: e };
      });
      return best;
    };
    P.los = function (a, b) { var d = U.dist(a.pos || a, b.pos || b), A = a.pos || a, Bp = b.pos || b; if (d < 0.01) return true; return P.ray(A.x, A.z, Bp.x - A.x, Bp.z - A.z, d).hit === null; };
    P.impulse = function (e, ix, iz, opts) {
      if (opts && opts.attack && G.safety.isMinor(e)) return false;     // no combat knockback on children
      var m = (e.comps.body && e.comps.body.mass) || 1; e.vel.x += ix / m; if (P.mode === 'side') e.vel.y += iz / m; else e.vel.z += iz / m; return true;
    };

    // -------------------------------------------------------------- bodies
    P.onSpawn = function (e, spec) {
      if (e.comps.body || spec.body === false) return;
      var k = e.kind, solid = ['player', 'enemy', 'npc', 'vehicle', 'animal', 'crate'].indexOf(k) >= 0 || spec.solid === true;
      if (!solid && !spec.body) return;
      var b = spec.body || {};
      e.comps.body = { r: +b.r || (k === 'vehicle' ? 0.9 : 0.4), w: +b.w || 0.7, h: +b.h || 0.9, mass: +b.mass || 1, solid: b.solid !== false, dynamic: b.dynamic !== false, grav: b.grav != null ? !!b.grav : (P.mode === 'side'), ground: false, friction: b.friction != null ? +b.friction : 10 };
      var type = spec.controller || (k === 'player' ? (ctl.type || (P.mode === 'side' ? 'side_scroll' : 'top_down')) : null);
      if (type && DEF[type]) { var c = {}, d = DEF[type], src = k === 'player' ? ctl : (spec.controllerCfg || {}); for (var key in d) c[key] = src[key] != null ? +src[key] : d[key]; c.type = type; c.coyote_t = 0; c.buffer_t = 0; c.boost_t = 0; c.boost_cd = 0; c.speedNow = 0; e.comps.ctrl = c; }
    };
    function moveCircle(e, nx, nz) {
      var r = e.comps.body.r;
      if (!P.blocked(nx, nz, r)) { e.pos.x = nx; e.pos.z = nz; return 0; }
      if (!P.blocked(nx, e.pos.z, r)) { e.pos.x = nx; e.vel.z *= 0.2; return 1; }
      if (!P.blocked(e.pos.x, nz, r)) { e.pos.z = nz; e.vel.x *= 0.2; return 2; }
      e.vel.x *= 0.1; e.vel.z *= 0.1; return 3;
    }
    function moveBoxSide(e, dt) {
      var b = e.comps.body, T = P.tiles, hw = b.w / 2;
      e.pos.x += e.vel.x * dt;
      if (T) {
        var top = Math.floor(e.pos.y - b.h + 0.001), bot = Math.floor(e.pos.y - 0.001);
        var side = e.vel.x > 0 ? Math.floor(e.pos.x + hw) : Math.floor(e.pos.x - hw);
        b.wall = 0;
        for (var cz = top; cz <= bot; cz++) if (P.solidCell(side, cz)) { b.wall = e.vel.x > 0 ? 1 : -1; e.pos.x = e.vel.x > 0 ? side - hw - 0.001 : side + 1 + hw + 0.001; e.vel.x = 0; break; }
      }
      var wasY = e.pos.y;
      e.pos.y += e.vel.y * dt;
      b.ground = false;
      if (T) {
        var l = Math.floor(e.pos.x - hw + 0.01), r = Math.floor(e.pos.x + hw - 0.01), cx;
        if (e.vel.y >= 0) {
          var row = Math.floor(e.pos.y);
          for (cx = l; cx <= r; cx++) {
            var ch = P.tileAt(cx, row);
            if (P.solidCell(cx, row) || (T.oneway.indexOf(ch) >= 0 && wasY <= row + 0.01)) { e.pos.y = row; e.vel.y = 0; b.ground = true; break; }
          }
          if (row >= T.h + 4) { e.pos.y = T.h + 4; b.fell = true; }
        } else {
          var headRow = Math.floor(e.pos.y - b.h);
          for (cx = l; cx <= r; cx++) if (P.solidCell(cx, headRow)) { e.pos.y = headRow + 1 + b.h; e.vel.y = 0; rt.bus.emit('bump', { entity: e, cx: cx, cz: headRow }); break; }
        }
      }
    }

    // -------------------------------------------------------------- controllers
    var CTRL = {};
    CTRL.top_down = function (e, c, inp, dt) {
      var mx = inp.move.x, my = inp.move.y, l = Math.hypot(mx, my); if (l > 1) { mx /= l; my /= l; }
      var sp = c.speed * (inp.run ? c.run : 1) * (e.flags.slow || 1) * envMod('move_speed');
      approach(e, mx * sp, -my * sp, l > 0.01 ? c.accel : c.friction, dt);
      if (inp.aim) e.rot = Math.atan2(inp.aim.x - e.pos.x, inp.aim.z - e.pos.z); else if (l > 0.01) e.rot = Math.atan2(mx, -my);
    };
    CTRL.point_click = function (e, c, inp, dt) {
      if (inp.clickTo) e.flags.goal = { x: inp.clickTo.x, z: inp.clickTo.z }, inp.clickTo = null;
      var g = e.flags.goal; if (!g) { approach(e, 0, 0, 30, dt); return; }
      var dx = g.x - e.pos.x, dz = g.z - e.pos.z, d = Math.hypot(dx, dz);
      if (d < 0.2) { e.flags.goal = null; e.vel.x = e.vel.z = 0; rt.bus.emit('arrive', { entity: e }); return; }
      approach(e, dx / d * c.speed, dz / d * c.speed, 30, dt); e.rot = Math.atan2(dx, dz);
    };
    function camYaw() { return rt.camera ? rt.camera.yaw || 0 : 0; }
    CTRL.third_person = function (e, c, inp, dt) {
      var yaw = camYaw(), mx = inp.move.x, my = inp.move.y, l = Math.hypot(mx, my); if (l > 1) { mx /= l; my /= l; }
      var fx = Math.sin(yaw), fz = -Math.cos(yaw), rx = Math.cos(yaw), rz = Math.sin(yaw);
      var wx = fx * my + rx * mx, wz = fz * my + rz * mx;
      var sp = c.speed * (inp.run ? c.run : 1) * (e.flags.slow || 1) * envMod('move_speed'), g = e.comps.body.ground;
      approach(e, wx * sp, wz * sp, (l > 0.01 ? c.accel : c.friction) * (g ? 1 : c.airControl), dt);
      if (l > 0.01) e.rot = U.lerp(e.rot, e.rot + U.wrap(Math.atan2(wx, wz) - e.rot), Math.min(1, dt * 12));
      jump3d(e, c, inp);
    };
    CTRL.first_person = function (e, c, inp, dt) {
      e.rot += (inp.look.dx || 0) * c.sens; e.pitch = U.clamp(e.pitch - (inp.look.dy || 0) * c.sens, -1.4, 1.4);
      inp.look.dx = inp.look.dy = 0;
      var mx = inp.move.x, my = inp.move.y, l = Math.hypot(mx, my); if (l > 1) { mx /= l; my /= l; }
      var fx = Math.sin(e.rot), fz = -Math.cos(e.rot), rx = Math.cos(e.rot), rz = Math.sin(e.rot);
      var sp = c.speed * (inp.run ? c.run : 1) * (e.flags.slow || 1) * envMod('move_speed');
      approach(e, (fx * my + rx * mx) * sp, (fz * my + rz * mx) * sp, (l > 0.01 ? c.accel : c.friction) * (e.comps.body.ground ? 1 : c.airControl), dt);
      jump3d(e, c, inp);
      if (rt.camera && rt.camera.rig === 'first_person') rt.camera.yaw = e.rot;
    };
    function jump3d(e, c, inp) { var b = e.comps.body; if (inp.jump && b.ground) { e.vel.y = c.jump; b.ground = false; rt.bus.emit('jump', { entity: e }); } inp.jump = false; }
    CTRL.side_scroll = function (e, c, inp, dt) {
      var b = e.comps.body, mx = U.clamp(inp.move.x, -1, 1), sp = c.speed * (inp.run ? c.run : 1) * (e.flags.slow || 1) * envMod('move_speed');
      var acc = b.ground ? (Math.abs(mx) > 0.01 ? c.accel : c.friction) : c.airAccel;
      var target = mx * sp;
      e.vel.x += U.clamp(target - e.vel.x, -acc * dt, acc * dt);
      if (Math.abs(mx) > 0.01) e.rot = mx > 0 ? 1 : -1;
      c.coyote_t = b.ground ? c.coyote : Math.max(0, c.coyote_t - dt);
      if (inp.jump) { c.buffer_t = c.buffer; inp.jump = false; } else c.buffer_t = Math.max(0, c.buffer_t - dt);
      if (c.buffer_t > 0 && c.coyote_t > 0) { e.vel.y = -c.jump; c.buffer_t = 0; c.coyote_t = 0; b.ground = false; e.flags.jumping = true; rt.bus.emit('jump', { entity: e }); }
      if (e.flags.jumping && !inp.jumpHeld && e.vel.y < 0) { e.vel.y *= c.cut; e.flags.jumping = false; }
      if (e.vel.y >= 0) e.flags.jumping = false;
    };
    CTRL.vehicle = function (e, c, inp, dt) {
      var thr = U.clamp(inp.move.y, -1, 1), st = U.clamp(inp.move.x, -1, 1);
      var fx = Math.sin(e.rot), fz = -Math.cos(e.rot);
      var fwd = e.vel.x * fx + e.vel.z * fz, latx = e.vel.x - fwd * fx, latz = e.vel.z - fwd * fz;
      var grip = c.grip * envMod('grip') * (e.flags.surfaceGrip || 1);
      if (inp.alt && c.boost_cd <= 0) { c.boost_t = c.boostTime; c.boost_cd = c.boostCool; rt.bus.emit('boost', { entity: e }); }
      c.boost_t = Math.max(0, c.boost_t - dt); c.boost_cd = Math.max(0, c.boost_cd - dt);
      var acc = thr > 0 ? c.accel * thr : thr < 0 ? (fwd > 0.5 ? -c.brake : c.reverse * thr) : 0;
      if (c.boost_t > 0) acc += c.boost;
      fwd += acc * dt;
      fwd -= fwd * c.drag * dt;
      var maxS = c.maxSpeed * (c.boost_t > 0 ? 1.35 : 1);
      fwd = U.clamp(fwd, -maxS * 0.35, maxS);
      var steerAmt = st * c.steer * U.clamp(Math.abs(fwd) / 8, 0, 1) * (fwd < 0 ? -1 : 1);
      e.rot = U.wrap(e.rot + steerAmt * dt);
      var keep = Math.exp(-grip * dt);                    // lateral grip: low grip = drift
      latx *= keep; latz *= keep;
      fx = Math.sin(e.rot); fz = -Math.cos(e.rot);
      e.vel.x = fx * fwd + latx; e.vel.z = fz * fwd + latz;
      c.speedNow = Math.abs(fwd); c.drift = Math.hypot(latx, latz);
    };
    function approach(e, tx, tz, acc, dt) { var dx = tx - e.vel.x, dz = tz - e.vel.z, d = Math.hypot(dx, dz), s = acc * dt; if (d <= s) { e.vel.x = tx; e.vel.z = tz; } else { e.vel.x += dx / d * s; e.vel.z += dz / d * s; } }
    function envMod(name) { return rt.sys.env && rt.sys.env.mod ? rt.sys.env.mod(name) : 1; }
    P.controllers = CTRL;

    // -------------------------------------------------------------- step
    P.update = function (dt) {
      rt.ents.forEach(function (e) {
        var b = e.comps.body;
        if (!b || e.dead) return;
        var c = e.comps.ctrl;
        if (c && e === rt.player && !rt.inputLocked && CTRL[c.type] && !(e.comps.fighter && e.comps.fighter.stagger > 0)) CTRL[c.type](e, c, rt.input, dt);
        else if (c && c.type === 'vehicle' && e.flags.ai) CTRL.vehicle(e, c, e.flags.ai, dt);
        if (!b.dynamic) return;
        if (P.mode === 'side') {
          if (b.grav) e.vel.y = Math.min(e.vel.y + P.gravity * dt, (c && c.maxFall) || 30);
          moveBoxSide(e, dt);
          if (!c || !(Math.abs(rt.input.move.x) > 0.01 && e === rt.player)) if (b.ground && !c) e.vel.x *= Math.max(0, 1 - b.friction * dt);
        } else {
          var gy = P.groundAt(e.pos.x, e.pos.z);
          if (c && (c.type === 'third_person' || c.type === 'first_person') || b.grav) { e.vel.y -= P.gravity * dt; e.pos.y += e.vel.y * dt; if (e.pos.y <= gy) { e.pos.y = gy; e.vel.y = 0; b.ground = true; } else b.ground = false; }
          else { e.pos.y = gy; b.ground = true; }
          if (!c && (e.vel.x || e.vel.z)) { var f = Math.max(0, 1 - b.friction * dt); e.vel.x *= f; e.vel.z *= f; }
          if (e.vel.x || e.vel.z) moveCircle(e, e.pos.x + e.vel.x * dt, e.pos.z + e.vel.z * dt);
        }
      });
      // body vs body separation (plane mode); minors are pushed gently but never by attacks
      if (P.mode === 'plane') {
        var list = []; rt.ents.forEach(function (e) { if (e.comps.body && e.comps.body.solid && !e.dead) list.push(e); });
        for (var i = 0; i < list.length; i++) for (var j = i + 1; j < list.length; j++) {
          var a = list[i], c2 = list[j], dx = c2.pos.x - a.pos.x, dz = c2.pos.z - a.pos.z, rr = a.comps.body.r + c2.comps.body.r, d2 = dx * dx + dz * dz;
          if (d2 < rr * rr && d2 > 1e-8) { var d = Math.sqrt(d2), push = (rr - d) / 2, nx = dx / d, nz = dz / d; if (a.comps.body.dynamic) moveCircle(a, a.pos.x - nx * push, a.pos.z - nz * push); if (c2.comps.body.dynamic) moveCircle(c2, c2.pos.x + nx * push, c2.pos.z + nz * push); }
        }
      }
      // trigger zones
      if (P.zones.length) rt.ents.forEach(function (e) {
        if (!(e === rt.player || e.tags.has('zoneTracked'))) return;
        P.zones.forEach(function (z) {
          var inside = z.box ? (e.pos.x >= z.box.x0 && e.pos.x <= z.box.x1 && e.pos.z >= z.box.z0 && e.pos.z <= z.box.z1) : Math.hypot(e.pos.x - z.x, (P.mode === 'side' ? e.pos.y : e.pos.z) - z.z) <= z.r;
          if (inside && !z.inside.has(e.id)) { z.inside.add(e.id); rt.bus.emit('enterZone', { zone: z.id, data: z.data, entity: e }); }
          else if (!inside && z.inside.has(e.id)) { z.inside.delete(e.id); rt.bus.emit('exitZone', { zone: z.id, data: z.data, entity: e }); }
        });
      });
    };
    P.save = function () { return { zones: P.zones.map(function (z) { return [z.id, Array.from(z.inside)]; }) }; };
    P.load = function (d) { (d && d.zones || []).forEach(function (p) { var z = P.zones.filter(function (q) { return q.id === p[0]; })[0]; if (z) z.inside = new Set(p[1]); }); };
    return P;
  });
})(GSYS);
