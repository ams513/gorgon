/* Stark World Engine Systems runtime: core. One namespace (GSYS), an event bus, an entity store, a seeded RNG and the
   system registry every module plugs into. No DOM, no network: the same code runs in cartridges, the studio and
   node (tests). Modules register with GSYS.register(name, order, factory); GSYS.create(world, opt) builds a
   Runtime with every registered system. */
var GSYS = (function (root) {
  'use strict';
  var G = root.GSYS || {};
  G.version = '1.0.0';
  G.schema = 1;
  G.root = root;
  G.LEXICON = root.GSYS_LEXICON || G.LEXICON || null;

  // ---------------------------------------------------------------- utilities
  var U = G.util = {};
  U.clamp = function (v, a, b) { return v < a ? a : v > b ? b : v; };
  U.lerp = function (a, b, t) { return a + (b - a) * t; };
  U.dist = function (a, b) { var dx = a.x - b.x, dz = (a.z || 0) - (b.z || 0); return Math.sqrt(dx * dx + dz * dz); };
  U.dist3 = function (a, b) { var dx = a.x - b.x, dy = (a.y || 0) - (b.y || 0), dz = (a.z || 0) - (b.z || 0); return Math.sqrt(dx * dx + dy * dy + dz * dz); };
  U.angle = function (a, b) { return Math.atan2(b.x - a.x, (b.z || 0) - (a.z || 0)); };
  U.wrap = function (a) { while (a > Math.PI) a -= 2 * Math.PI; while (a < -Math.PI) a += 2 * Math.PI; return a; };
  U.hash = function (s) { s = String(s); var h = 2166136261; for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; };
  U.copy = function (o) { return o == null ? o : JSON.parse(JSON.stringify(o)); };
  U.isObj = function (o) { return o !== null && typeof o === 'object' && !Array.isArray(o); };
  U.num = function (v, d) { v = +v; return isFinite(v) ? v : (d || 0); };
  U.ease = {
    linear: function (t) { return t; },
    inOut: function (t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; },
    out: function (t) { return 1 - (1 - t) * (1 - t); },
    in: function (t) { return t * t; }
  };
  // mulberry32: small, fast, deterministic; the state is one 32-bit int so saves can store it
  U.rng = function (seed) {
    var s = (seed >>> 0) || 1;
    var r = function () { s = (s + 0x6D2B79F5) >>> 0; var t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
    r.int = function (a, b) { return a + Math.floor(r() * (b - a + 1)); };
    r.pick = function (arr) { return arr && arr.length ? arr[Math.floor(r() * arr.length)] : undefined; };
    r.weighted = function (list, wkey) { var tot = 0, i; wkey = wkey || 'w'; for (i = 0; i < list.length; i++) tot += Math.max(0, +list[i][wkey] || 1); var x = r() * tot; for (i = 0; i < list.length; i++) { x -= Math.max(0, +list[i][wkey] || 1); if (x <= 0) return list[i]; } return list[list.length - 1]; };
    r.shuffle = function (arr) { for (var i = arr.length - 1; i > 0; i--) { var j = Math.floor(r() * (i + 1)), t = arr[i]; arr[i] = arr[j]; arr[j] = t; } return arr; };
    r.state = function (v) { if (v !== undefined) s = v >>> 0; return s; };
    return r;
  };

  // ---------------------------------------------------------------- event bus
  function Bus() { this.h = Object.create(null); this.log = []; this.keepLog = 0; }
  Bus.prototype.on = function (ev, fn, owner) { (this.h[ev] = this.h[ev] || []).push({ fn: fn, owner: owner }); return fn; };
  Bus.prototype.off = function (ev, fn) { var l = this.h[ev]; if (l) this.h[ev] = l.filter(function (x) { return x.fn !== fn; }); };
  Bus.prototype.offOwner = function (owner) { for (var k in this.h) this.h[k] = this.h[k].filter(function (x) { return x.owner !== owner; }); };
  Bus.prototype.emit = function (ev, data) {
    if (this.keepLog) { this.log.push([ev, data]); if (this.log.length > this.keepLog) this.log.shift(); }
    var l = this.h[ev], out;
    if (!l) return;
    l = l.slice();
    for (var i = 0; i < l.length; i++) {
      try { var r = l[i].fn(data, ev); if (r !== undefined) out = r; } catch (e) { if (e && e.safety) throw e; G.warn('event ' + ev + ': ' + (e && e.message)); }
    }
    return out;
  };
  G.Bus = Bus;
  G.warnings = [];
  G.warn = function (m) { G.warnings.push(String(m)); if (G.warnings.length > 200) G.warnings.shift(); if (root.console && G.debug) root.console.warn('[GSYS] ' + m); };

  // ---------------------------------------------------------------- entities
  var nextId = 1;
  function Entity(spec) {
    spec = spec || {};
    this.id = String(spec.id != null ? spec.id : 'e' + (nextId++));
    this.name = spec.name != null ? String(spec.name) : this.id;
    this.kind = spec.kind || spec.t || 'thing';
    this.tags = new Set(spec.tags || []);
    this.pos = { x: U.num(spec.x), y: U.num(spec.y), z: U.num(spec.z) };
    this.vel = { x: 0, y: 0, z: 0 };
    this.rot = U.num(spec.rot);
    this.pitch = 0;
    this.team = spec.team || (this.kind === 'enemy' ? 'enemy' : this.kind === 'player' ? 'player' : 'neutral');
    this.maxHp = U.num(spec.maxHp || spec.hp, 0) || (this.kind === 'enemy' ? 3 : 10);
    this.hp = U.num(spec.hp, this.maxHp) || this.maxHp;
    this.dead = false;
    this.comps = {};
    this.flags = {};
    this.spec = spec;
  }
  Entity.prototype.has = function (t) { return this.tags.has(t); };
  G.Entity = Entity;

  // ---------------------------------------------------------------- registry + runtime
  var REG = [];
  G.register = function (name, order, factory) {
    for (var i = 0; i < REG.length; i++) if (REG[i].name === name) { REG[i] = { name: name, order: order, factory: factory }; return; }
    REG.push({ name: name, order: order, factory: factory });
    REG.sort(function (a, b) { return a.order - b.order; });
  };
  G.registered = function () { return REG.map(function (r) { return r.name; }); };

  function Runtime(world, opt) {
    opt = opt || {};
    this.world = world || { name: 'Untitled', objects: [] };
    this.sysdata = (this.world.systems && typeof this.world.systems === 'object') ? this.world.systems : {};
    this.opt = opt;
    this.bus = new Bus();
    this.ents = new Map();
    this.time = 0;
    this.frame = 0;
    this.paused = false;
    this.timeScale = 1;
    this.seed = (opt.seed != null ? opt.seed : (this.world.seed || 1)) >>> 0;
    this.rng = U.rng(this.seed);
    this.input = { move: { x: 0, y: 0 }, look: { dx: 0, dy: 0 }, aim: null, jump: false, jumpHeld: false, fire: false, fireHeld: false, alt: false, interact: false, run: false, keys: {} };
    this.sys = {};
    this.order = [];
    this.player = null;
    this.timers = [];
    var self = this;
    REG.forEach(function (r) {
      if (opt.only && opt.only.indexOf(r.name) < 0 && r.name !== 'safety') return;
      try { self.sys[r.name] = r.factory(self) || {}; self.order.push(r.name); }
      catch (e) { if (e && e.safety) throw e; G.warn('system ' + r.name + ' failed to start: ' + e.message); }
    });
    this.order.forEach(function (n) { var s = self.sys[n]; if (s.init) s.init(); });
    this.bus.emit('ready', { rt: this });
  }
  Runtime.prototype.spawn = function (spec, fromData) {
    var e = spec instanceof Entity ? spec : new Entity(spec);
    if (this.ents.has(e.id)) e.id = e.id + '_' + (nextId++);
    if (G.safety) G.safety.adopt(e, spec, fromData, this);
    this.ents.set(e.id, e);
    if (e.kind === 'player' && !this.player) this.player = e;
    for (var i = 0; i < this.order.length; i++) { var s = this.sys[this.order[i]]; if (s.onSpawn) s.onSpawn(e, spec); }
    this.bus.emit('spawn', { entity: e });
    return e;
  };
  Runtime.prototype.get = function (id) { if (id && id.id) return id; return this.ents.get(String(id)) || null; };
  Runtime.prototype.find = function (idOrName) {
    var e = this.get(idOrName);
    if (e) return e;
    var n = String(idOrName).toLowerCase(), hit = null;
    this.ents.forEach(function (x) { if (!hit && x.name.toLowerCase() === n) hit = x; });
    return hit;
  };
  Runtime.prototype.query = function (pred) {
    var out = [];
    if (typeof pred === 'string') { var tag = pred; pred = function (e) { return e.kind === tag || e.tags.has(tag); }; }
    this.ents.forEach(function (e) { if (!pred || pred(e)) out.push(e); });
    return out;
  };
  Runtime.prototype.near = function (p, r, pred) {
    var out = [], r2 = r * r;
    this.ents.forEach(function (e) { var dx = e.pos.x - p.x, dz = e.pos.z - (p.z || 0); if (dx * dx + dz * dz <= r2 && (!pred || pred(e))) out.push(e); });
    return out;
  };
  Runtime.prototype.remove = function (id) {
    var e = this.get(id);
    if (!e) return false;
    for (var i = 0; i < this.order.length; i++) { var s = this.sys[this.order[i]]; if (s.onRemove) s.onRemove(e); }
    this.ents.delete(e.id);
    this.bus.emit('remove', { entity: e });
    return true;
  };
  Runtime.prototype.after = function (sec, fn, owner) { var t = { at: this.time + Math.max(0, +sec || 0), fn: fn, owner: owner }; this.timers.push(t); return t; };
  Runtime.prototype.every = function (sec, fn, owner) { var t = { at: this.time + Math.max(0.01, +sec || 1), every: Math.max(0.01, +sec || 1), fn: fn, owner: owner }; this.timers.push(t); return t; };
  Runtime.prototype.step = function (dt) {
    if (this.paused) return;
    dt = Math.min(0.1, Math.max(0, +dt || 0)) * this.timeScale;
    this.dt = dt;
    this.time += dt;
    this.frame++;
    for (var i = 0; i < this.order.length; i++) {
      var s = this.sys[this.order[i]];
      if (s.update) { try { s.update(dt); } catch (e) { if (e && e.safety) throw e; G.warn(this.order[i] + '.update: ' + e.message); } }
    }
    if (this.timers.length) {
      var due = this.timers.filter(function (t) { return t.at <= this.time; }, this);
      for (var j = 0; j < due.length; j++) {
        var t = due[j];
        if (t.every) t.at += t.every; else this.timers.splice(this.timers.indexOf(t), 1);
        try { t.fn(); } catch (e2) { if (e2 && e2.safety) throw e2; G.warn('timer: ' + e2.message); }
      }
    }
    this.bus.emit('tick', dt);
  };
  Runtime.prototype.run = function (seconds, dt) { dt = dt || 1 / 60; for (var t = 0; t < seconds; t += dt) this.step(dt); };
  Runtime.prototype.destroy = function () { var self = this; this.order.forEach(function (n) { var s = self.sys[n]; if (s.destroy) try { s.destroy(); } catch (e) { G.warn(e.message); } }); this.ents.clear(); this.timers = []; };
  G.Runtime = Runtime;

  /** world -> Runtime with every registered system; world objects become entities (data path, already checked). */
  G.create = function (world, opt) {
    opt = opt || {};
    // the child-safety boot check always runs (there is deliberately no option to skip it)
    var rep = G.safety.boot(world);
    if (!rep.ok) throw new G.safety.SafetyError(rep.message, rep.report);
    opt.safetyReport = rep;
    var rt = new Runtime(world, opt);
    if (opt.populate !== false) G.populate(rt, world, opt.level || 0);
    // second phase: systems that need the spawned entities (squads, kits, streaming, auto quests)
    rt.order.forEach(function (n) { var s = rt.sys[n]; if (s.begin) s.begin(); });
    rt.bus.emit('start', { rt: rt });
    return rt;
  };
  G.levelOf = function (world, i) { return !i ? world : (world.levels || [])[i - 1] || world; };
  G.populate = function (rt, world, li) {
    var L = G.levelOf(world, li), sy = rt.sysdata, objs = (L && L.objects) || [];
    var chars = U.isObj(sy.characters) ? sy.characters : {};
    var placed = {};
    var ENEMY_HP = { grunt: 3, archer: 2, brute: 8, wisp: 2, boss: 40 }, ENEMY_W = { grunt: 'claw', archer: 'bow', brute: 'smash', wisp: 'spit', boss: 'smash' };
    objs.forEach(function (o, i) {
      if (!o || typeof o !== 'object') return;
      var spec = U.copy(o);
      if (o.t === 'enemy') { spec.enemy = o.kind || 'grunt'; }           // the studio's enemy kind (grunt, archer, ...)
      spec.kind = o.t;
      if (o.id && chars[o.id]) { var c = chars[o.id]; for (var k in c) if (spec[k] === undefined) spec[k] = U.copy(c[k]); placed[o.id] = 1; }
      if (o.t === 'enemy') { spec.team = spec.team || 'enemy'; spec.hp = spec.hp || ENEMY_HP[spec.enemy] || 3; spec.weapon = spec.weapon || ENEMY_W[spec.enemy] || 'claw'; if (spec.enemy === 'archer' || spec.enemy === 'wisp') spec.ranged = true; }
      spec.id = o.id || (o.t + '_' + i);
      rt.spawn(spec, true);
    });
    Object.keys(chars).forEach(function (id) {
      var c = chars[id];
      if (placed[id] || !U.isObj(c) || c.x == null || (c.level || 0) !== li) return;
      var spec = U.copy(c); spec.id = id; spec.kind = c.kind || 'npc';
      rt.spawn(spec, true);
    });
    // systems.entities: things the studio's object list has no type for (campfires, vehicles, exits, buildings...)
    (Array.isArray(sy.entities) ? sy.entities : []).forEach(function (s, i) {
      if (!U.isObj(s) || (s.level || 0) !== li) return;
      var spec = U.copy(s); spec.id = s.id || ((s.kind || 'thing') + '_x' + i); spec.kind = s.kind || 'thing';
      rt.spawn(spec, true);
    });
    var pl = sy.player || world.player || {};
    if (!rt.player) {
      var sp = (L && L.spawn) || {};
      rt.spawn({ id: 'player', kind: 'player', name: pl.name || 'Hero', x: U.num(pl.x, U.num(sp.x)), z: U.num(pl.z, U.num(sp.z)), y: U.num(pl.y), hp: pl.hp || 10, team: 'player', tags: ['player'], controller: pl.controller }, true);
    }
    rt.bus.emit('populated', { level: li });
  };
  return (root.GSYS = G);
})(typeof window !== 'undefined' ? window : globalThis);
