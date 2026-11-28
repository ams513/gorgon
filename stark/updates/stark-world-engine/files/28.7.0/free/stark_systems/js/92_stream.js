/* Stark World Engine Systems: streaming for large worlds. Entities live in chunks (systems.streaming.chunk metres); only chunks
   within `radius` of the focus (player) are active - the rest are dormant (kept, not updated). Procedural chunks
   (generator 'region') are generated on first visit from (seed, cx, cz) and dropped again when they were never
   touched, so an endless world costs memory only for places the player changed. LOD levels by distance, and
   budgets: max visible instances (total + per kind) and max awake AI; the farthest go over budget first.
   systems.streaming = {chunk: 32, radius: 2, keep: 1, lod: [{dist, level}], budgets: {instances, per_kind: {},
   active_ai}, generator: 'region' | null, region: {...procgen region options}} */
(function (G) {
  'use strict';
  var U = G.util;
  G.register('stream', 92, function (rt) {
    var cfg = rt.sysdata.streaming, ST = { enabled: !!cfg, stats: { chunks: 0, active: 0, dormant: 0, culled: 0, aiAwake: 0, aiAsleep: 0, generated: 0, dropped: 0 } };
    if (!cfg) { ST.update = function () {}; return ST; }
    var size = +cfg.chunk || 32, radius = cfg.radius != null ? +cfg.radius : 2, keep = cfg.keep != null ? +cfg.keep : 1;
    var lod = cfg.lod || [{ dist: size * 1.5, level: 0 }, { dist: size * 3, level: 1 }, { dist: 1e9, level: 2 }];
    var bud = cfg.budgets || {}, maxInst = +bud.instances || 5000, perKind = bud.per_kind || {}, maxAi = +bud.active_ai || 48;
    var chunks = new Map(), active = new Set(), acc = 0, lodAcc = 0;
    function key(cx, cz) { return cx + ',' + cz; }
    function chunkOf(p) { return [Math.floor(p.x / size), Math.floor(p.z / size)]; }
    function get(cx, cz) { var k = key(cx, cz), c = chunks.get(k); if (!c) { c = { cx: cx, cz: cz, dormant: [], generated: false, touched: false, procIds: [] }; chunks.set(k, c); } return c; }
    ST.chunkOf = chunkOf; ST.size = size;
    function streamable(e) { return e !== rt.player && !e.flags.persistent && !e.tags.has('global') && e.kind !== 'player'; }
    ST.begin = function () {
      rt.ents.forEach(function (e) { if (!streamable(e)) return; var c = chunkOf(e.pos); e.flags.chunk = key(c[0], c[1]); });
      ST.refresh(true);
    };
    ['damage', 'pickup', 'chestOpened', 'interact', 'death'].forEach(function (ev) { rt.bus.on(ev, function (d) { var e = d && (d.target || d.entity); if (e && e.flags && e.flags.chunk) { var c = chunks.get(e.flags.chunk); if (c) c.touched = true; } }); });
    function sleep(c) {
      rt.ents.forEach(function (e) { if (e.flags.chunk === key(c.cx, c.cz) && streamable(e)) { rt.ents.delete(e.id); c.dormant.push(e); } });
      if (c.generated && !c.touched && cfg.generator) { ST.stats.dropped += c.dormant.length; c.dormant = []; c.generated = false; }   // pristine procedural chunk: regenerate later
    }
    function wake(c) {
      if (cfg.generator && !c.generated && rt.sys.procgen) {
        c.generated = true;
        var spawned = rt.sys.procgen.chunk(c.cx, c.cz, size, cfg.region || {});
        spawned.forEach(function (e) { e.flags.chunk = key(c.cx, c.cz); e.flags.procedural = true; });
        ST.stats.generated += spawned.length;
      }
      c.dormant.forEach(function (e) { rt.ents.set(e.id, e); });
      c.dormant = [];
    }
    /** Re-evaluate which chunks are active around the focus (player or rt.focus). */
    ST.refresh = function (first) {
      var f = rt.focus || (rt.player && rt.player.pos) || { x: 0, z: 0 }, c0 = chunkOf(f), want = new Set();
      for (var dx = -radius; dx <= radius; dx++) for (var dz = -radius; dz <= radius; dz++) want.add(key(c0[0] + dx, c0[1] + dz));
      var all = new Set(); rt.ents.forEach(function (e) { if (e.flags.chunk) all.add(e.flags.chunk); });
      if (first) all.forEach(function (k) { var p = k.split(','); get(+p[0], +p[1]); });
      chunks.forEach(function (c, k) {
        var dist = Math.max(Math.abs(c.cx - c0[0]), Math.abs(c.cz - c0[1]));
        if (active.has(k) && dist > radius + keep) { sleep(c); active.delete(k); rt.bus.emit('chunkSleep', { cx: c.cx, cz: c.cz }); }
        else if (first && !want.has(k)) sleep(c);
      });
      want.forEach(function (k) { if (!active.has(k)) { var p = k.split(','), c = get(+p[0], +p[1]); wake(c); active.add(k); rt.bus.emit('chunkWake', { cx: c.cx, cz: c.cz }); } });
      ST.stats.chunks = chunks.size; ST.stats.active = active.size;
      var dorm = 0; chunks.forEach(function (c) { dorm += c.dormant.length; }); ST.stats.dormant = dorm;
    };
    function budgets() {
      var f = rt.focus || (rt.player && rt.player.pos) || { x: 0, z: 0 }, list = [];
      rt.ents.forEach(function (e) { if (e !== rt.player) { e._d = U.dist(e.pos, f); list.push(e); } });
      list.sort(function (a, b) { return a._d - b._d; });
      var counts = {}, total = 0, culled = 0, ai = 0, asleep = 0;
      list.forEach(function (e) {
        var lv = lod[lod.length - 1].level; for (var i = 0; i < lod.length; i++) if (e._d <= lod[i].dist) { lv = lod[i].level; break; }
        e.lod = lv;
        counts[e.kind] = (counts[e.kind] || 0) + 1; total++;
        var over = total > maxInst || (perKind[e.kind] != null && counts[e.kind] > +perKind[e.kind]);
        e.flags.culled = over; if (over) culled++;
        if (e.comps.ai) { if (ai < maxAi && !over) { e.flags.aiSleep = false; ai++; } else { e.flags.aiSleep = true; asleep++; } }
      });
      ST.stats.culled = culled; ST.stats.aiAwake = ai; ST.stats.aiAsleep = asleep; ST.stats.visible = total - culled;
    }
    ST.update = function (dt) { acc += dt; lodAcc += dt; if (acc > 0.5) { acc = 0; ST.refresh(false); } if (lodAcc > 0.25) { lodAcc = 0; budgets(); } };
    ST.save = function () { var t = []; chunks.forEach(function (c, k) { if (c.touched) t.push(k); }); return { touched: t }; };
    ST.load = function (d) { (d && d.touched || []).forEach(function (k) { var c = chunks.get(k); if (c) c.touched = true; }); };
    return ST;
  });
})(GSYS);
