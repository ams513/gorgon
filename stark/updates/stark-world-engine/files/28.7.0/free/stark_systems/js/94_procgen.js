/* Stark World Engine Systems: runtime procedural generation (deterministic from seeds). dungeon(seed, depth) -> BSP floor with
   rooms, corridors, doors, themed rooms, spawns and loot; region chunk(cx, cz) -> trees, rocks, ruins, camps,
   shrines, animals for streaming; settlement(seed) -> houses, plaza, families of NPCs with names, jobs, backstories
   and daily schedules. Children in generated families are tagged child (immune, friendly, never quest targets).
   Author-time baking (editable in the studio) is the Python twin: stark_systems/procgen.py.
   systems.procgen = {names: {first: [...], last: [...]}, syllables: [...], jobs: [...], enemies: [...], loot: id} */
(function (G) {
  'use strict';
  var U = G.util, S = G.safety;
  var SYL = ['al', 'bri', 'cor', 'da', 'el', 'fen', 'gwy', 'ha', 'is', 'jo', 'ka', 'li', 'mo', 'nel', 'or', 'pim', 'qua', 'ro', 'sa', 'tor', 'ul', 'vi', 'wen', 'ya', 'zed', 'mar', 'tes', 'lin', 'bel', 'dor'];
  var JOBS = ['baker', 'smith', 'farmer', 'fisher', 'herbalist', 'carpenter', 'weaver', 'miller', 'guard', 'innkeeper', 'hunter', 'scribe', 'potter', 'merchant'];
  var TRAITS = ['cheerful', 'stubborn', 'curious', 'gentle', 'proud', 'quiet', 'restless', 'kind', 'clever', 'cautious', 'generous', 'superstitious'];
  var BACK = ['Born in {town}, {name} took over the {job}\'s trade from a parent.', 'Came to {town} after a hard winter in the hills and stayed for the people.', 'Known in {town} for a {trait} heart and a good memory for stories.', 'Spent years on the road as a traveller before settling in {town}.', 'Keeps an old map of the hills and swears there is treasure in the north.'];
  var KID_BACK = ['The youngest in the family; loves the pond and asks endless questions.', 'Collects shiny stones and knows every cat in {town} by name.', 'Wants to be a {job} like their parent one day.', 'Is learning letters from the scribe and reads aloud to anyone who listens.'];
  var KID_WANTS = ['a lost kitten', 'a kite stuck in a tree', 'a carved wooden horse', 'a ribbon dropped near the well', 'a marble that rolled into the reeds'];
  G.register('procgen', 94, function (rt) {
    var cfg = rt.sysdata.procgen || {}, PG = {};
    var seed0 = (rt.world.seed || 1) >>> 0;
    function rng(a, b, c) { return U.rng(U.hash(seed0 + ':' + a + ':' + (b || 0) + ':' + (c || 0))); }
    PG.name = function (r) {
      var nm = cfg.names || {};
      if (nm.first && nm.first.length) return r.pick(nm.first) + (nm.last && nm.last.length ? ' ' + r.pick(nm.last) : '');
      var syl = cfg.syllables || SYL, s = r.pick(syl) + r.pick(syl) + (r() < 0.3 ? r.pick(syl) : '');
      return s.charAt(0).toUpperCase() + s.slice(1);
    };
    function safe(t, fb) { return S.safeText(t, fb); }

    /** BSP dungeon floor: tiles ('#' wall, '.' floor, '+' door, '<' entry, '>' stairs down). */
    PG.dungeon = function (dseed, depth, opts) {
      opts = opts || {}; depth = depth || 1;
      var r = rng('dungeon', dseed, depth), W = opts.w || 48 + Math.min(32, depth * 4), H = opts.h || 36 + Math.min(20, depth * 2);
      var grid = []; for (var y = 0; y < H; y++) { grid.push([]); for (var x = 0; x < W; x++) grid[y].push('#'); }
      var leaves = [], rooms = [];
      (function split(x, y, w, h, d) {
        if (d > 5 || (w < 16 && h < 14) || (d > 2 && r() < 0.15)) { leaves.push({ x: x, y: y, w: w, h: h }); return; }
        var vert = w / h > 1.25 ? true : h / w > 1.25 ? false : r() < 0.5;
        if (vert && w >= 16) { var s = r.int(Math.floor(w * 0.35), Math.floor(w * 0.65)); split(x, y, s, h, d + 1); split(x + s, y, w - s, h, d + 1); }
        else if (!vert && h >= 14) { var s2 = r.int(Math.floor(h * 0.35), Math.floor(h * 0.65)); split(x, y, w, s2, d + 1); split(x, y + s2, w, h - s2, d + 1); }
        else leaves.push({ x: x, y: y, w: w, h: h });
      })(1, 1, W - 2, H - 2, 0);
      var kinds = ['den', 'treasure', 'shrine', 'library', 'barracks', 'well', 'garden', 'crypt'];
      leaves.forEach(function (L) {
        var rw = r.int(Math.max(4, Math.floor(L.w * 0.5)), Math.max(4, L.w - 2)), rh = r.int(Math.max(3, Math.floor(L.h * 0.5)), Math.max(3, L.h - 2));
        var rx = L.x + r.int(0, Math.max(0, L.w - rw - 1)), ry = L.y + r.int(0, Math.max(0, L.h - rh - 1));
        rooms.push({ x: rx, y: ry, w: rw, h: rh, cx: Math.floor(rx + rw / 2), cy: Math.floor(ry + rh / 2), type: r.pick(kinds) });
        for (var yy = ry; yy < ry + rh; yy++) for (var xx = rx; xx < rx + rw; xx++) if (yy > 0 && yy < H - 1 && xx > 0 && xx < W - 1) grid[yy][xx] = '.';
      });
      function carve(a, b) {
        var x = a.cx, y = a.cy, horizFirst = r() < 0.5;
        function step(tx, ty) { while (x !== tx) { x += x < tx ? 1 : -1; if (grid[y][x] === '#') grid[y][x] = ','; } while (y !== ty) { y += y < ty ? 1 : -1; if (grid[y][x] === '#') grid[y][x] = ','; } }
        if (horizFirst) { step(b.cx, y); step(b.cx, b.cy); } else { step(x, b.cy); step(b.cx, b.cy); }
      }
      rooms.sort(function (a, b) { return a.cx + a.cy * 0.5 - (b.cx + b.cy * 0.5); });
      for (var i = 1; i < rooms.length; i++) carve(rooms[i - 1], rooms[i]);
      if (rooms.length > 3) carve(rooms[0], rooms[r.int(2, rooms.length - 1)]);           // a loop, so floors are not one long line
      for (y = 1; y < H - 1; y++) for (x = 1; x < W - 1; x++) {
        if (grid[y][x] !== ',') continue;
        var nearRoom = (grid[y][x - 1] === '.' || grid[y][x + 1] === '.' || grid[y - 1][x] === '.' || grid[y + 1][x] === '.');
        grid[y][x] = nearRoom && r() < 0.35 ? '+' : '.';
      }
      var start = rooms[0], exit = rooms[rooms.length - 1];
      grid[start.cy][start.cx] = '<'; grid[exit.cy][exit.cx] = '>'; start.type = 'entry'; exit.type = 'stairs';
      var foes = cfg.enemies || ['grunt', 'archer', 'brute', 'wisp'], spawns = [], loot = [];
      rooms.forEach(function (rm, idx) {
        if (idx === 0) return;
        var n = rm.type === 'den' ? r.int(2, 3 + depth) : rm.type === 'barracks' ? r.int(2, 4) : rm.type === 'treasure' || rm.type === 'shrine' ? r.int(0, 1) : r.int(0, 2);
        for (var k = 0; k < n; k++) spawns.push({ x: rm.x + 1 + r.int(0, Math.max(0, rm.w - 3)) + 0.5, z: rm.y + 1 + r.int(0, Math.max(0, rm.h - 3)) + 0.5, kind: foes[Math.min(foes.length - 1, r.int(0, Math.min(foes.length - 1, 1 + Math.floor(depth / 2))))], level: depth });
        if (rm.type === 'treasure' || r() < 0.25) loot.push({ x: rm.cx + 0.5, z: rm.cy + 0.5, table: cfg.loot || 'dungeon', room: rm.type });
      });
      return { w: W, h: H, rows: grid.map(function (row) { return row.join(''); }), rooms: rooms, start: { x: start.cx + 0.5, z: start.cy + 0.5 }, exit: { x: exit.cx + 0.5, z: exit.cy + 0.5 }, spawns: spawns, loot: loot, depth: depth, seed: dseed };
    };

    /** Streaming region chunk: deterministic content for chunk (cx, cz) of `size` metres. -> spawned entities. */
    PG.chunk = function (cx, cz, size, opts) {
      opts = opts || {};
      var r = rng('chunk', cx, cz), x0 = cx * size, z0 = cz * size, out = [];
      var biomeN = Math.sin(cx * 0.7 + seed0) + Math.cos(cz * 0.6 - seed0 * 0.3);
      var biome = biomeN > 0.8 ? 'forest' : biomeN < -0.8 ? 'rocky' : 'meadow';
      var trees = biome === 'forest' ? r.int(10, 18) : biome === 'meadow' ? r.int(2, 6) : r.int(0, 3), rocks = biome === 'rocky' ? r.int(6, 12) : r.int(0, 4);
      function at() { return { x: x0 + r() * size, z: z0 + r() * size }; }
      function sp(spec) { var p = at(); spec.x = spec.x != null ? spec.x : p.x; spec.z = spec.z != null ? spec.z : p.z; out.push(rt.spawn(spec, true)); }
      for (var i = 0; i < trees; i++) sp({ kind: 'tree', s: 0.8 + r() * 0.6 });
      for (i = 0; i < rocks; i++) sp({ kind: 'rock', s: 0.7 + r() * 0.8 });
      var roll = r();
      if (roll < (opts.camp || 0.18) && (cx || cz)) {
        var c = at(), n = r.int(2, 4), foes = cfg.enemies || ['grunt', 'archer', 'brute'];
        sp({ kind: 'ruin', x: c.x, z: c.z });
        for (i = 0; i < n; i++) sp({ kind: 'enemy', x: c.x + (r() - 0.5) * 6, z: c.z + (r() - 0.5) * 6, team: 'enemy', hp: 3 + Math.floor(Math.hypot(cx, cz)), patrol: 4, ai: 'melee', name: 'Raider' });
        sp({ kind: 'chest', x: c.x + 1.5, z: c.z, loot: cfg.loot || 'camp', gold: 5 + r.int(0, 10) });
      } else if (roll < (opts.shrine || 0.28)) { var s = at(); sp({ kind: 'shrine', x: s.x, z: s.z, name: 'Wayshrine', station: 'shrine' }); }
      else if (roll < 0.4) { for (i = 0; i < r.int(1, 3); i++) sp({ kind: 'animal', name: r.pick(['Deer', 'Hare', 'Fox', 'Boar']), ai: 'idle', team: 'neutral', hp: 2 }); }
      return out;
    };

    /** A settlement plan + families. -> {houses, plaza, npcs: [{id, name, age, job, home, work, schedule, backstory, traits}]} */
    PG.settlement = function (sseed, opts) {
      opts = opts || {};
      var r = rng('settlement', sseed), town = opts.name || (PG.name(r) + r.pick(['ford', 'brook', 'holm', 'stead', 'wick', 'dale'])), cxp = +opts.x || 0, czp = +opts.z || 0;
      var nh = opts.houses || r.int(5, 9), houses = [], npcs = [], jobs = cfg.jobs || JOBS;
      for (var i = 0; i < nh; i++) { var a = i / nh * Math.PI * 2 + r() * 0.3, d = 12 + r() * 8; houses.push({ id: 'house' + i, x: cxp + Math.sin(a) * d, z: czp + Math.cos(a) * d, rot: -a, w: 4 + r() * 2, d: 3 + r() * 1.5 }); }
      var tavern = { x: cxp + 4, z: czp - 3, name: 'the ' + r.pick(['Sleepy', 'Golden', 'Laughing', 'Crooked']) + ' ' + r.pick(['Goose', 'Kettle', 'Lantern', 'Badger']) };
      var plaza = { x: cxp, z: czp }, n = 0;
      houses.forEach(function (h) {
        var family = PG.name(r).split(' ')[0], adults = r.int(1, 2), kids = r() < 0.6 ? r.int(1, 2) : 0;
        for (var k = 0; k < adults + kids; k++) {
          var child = k >= adults, job = child ? null : r.pick(jobs), trait = r.pick(TRAITS), nm = PG.name(r) + ' ' + family;
          var work = child ? plaza : { x: cxp + (r() - 0.5) * 20, z: czp + (r() - 0.5) * 20 };
          var sched = child ? [{ from: 21, to: 7, at: { x: h.x, z: h.z }, do: 'sleep' }, { from: 7, to: 12, at: plaza, do: 'play' }, { from: 12, to: 13, at: { x: h.x, z: h.z }, do: 'eat' }, { from: 13, to: 18, at: { x: plaza.x + (r() - 0.5) * 8, z: plaza.z + (r() - 0.5) * 8 }, do: 'play' }, { from: 18, to: 21, at: { x: h.x, z: h.z }, do: 'home' }]
            : [{ from: 22, to: 6, at: { x: h.x, z: h.z }, do: 'sleep' }, { from: 6, to: 8, at: { x: h.x, z: h.z }, do: 'eat' }, { from: 8, to: 17, at: work, do: 'work' }, { from: 17, to: 20, at: tavern, do: 'tavern' }, { from: 20, to: 22, at: { x: h.x, z: h.z }, do: 'home' }];
          var ctx = { town: town, name: nm.split(' ')[0], job: job || r.pick(jobs), trait: trait };
          var back = (child ? r.pick(KID_BACK) : r.pick(BACK)).replace(/\{(\w+)\}/g, function (m, kk) { return ctx[kk] || m; });
          var npc = { id: 'pg_' + sseed + '_' + (n++), name: safe(nm, 'Villager'), age: child ? 'child' : 'adult', role: child ? 'child' : job, job: job, home: h.id, x: h.x + (r() - 0.5) * 2, z: h.z + 2, schedule: sched, traits: [trait], backstory: safe(back, '') };
          if (child) npc.wants = r.pick(KID_WANTS);
          npcs.push(npc);
        }
      });
      return { name: town, houses: houses, plaza: plaza, tavern: tavern, npcs: npcs };
    };
    /** Spawn a generated settlement's people into the runtime (children friendly + protected). */
    PG.populateSettlement = function (plan) {
      return plan.npcs.map(function (n) { return rt.spawn({ id: n.id, kind: 'npc', name: n.name, age: n.age, x: n.x, z: n.z, schedule: n.schedule, desc: n.backstory, role: n.role, team: 'neutral' }, true); });
    };
    return PG;
  });
})(GSYS);
