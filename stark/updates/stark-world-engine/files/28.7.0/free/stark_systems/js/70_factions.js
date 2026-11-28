/* Stark World Engine Systems: factions + reputation, economy + shops.
   systems.factions = {id: {name, desc, relations: {other: -100..100}, members: [char ids], hostile_below: -25,
     ranks: [{at: 0, name}], player: 0}}
   systems.economy = {currency: 'gold', start: 0, shops: {id: {name, owner, stock: [{item, qty, price}], buy_mult,
     sell_mult, restock_hours, dynamic: true}}, rep_price: 0.002}
   Children are members of factions like anyone else, but a faction's hostility never makes a child a combatant. */
(function (G) {
  'use strict';
  var U = G.util, S = G.safety;
  G.register('factions', 70, function (rt) {
    var cfg = rt.sysdata.factions || {}, F = { rep: null, defs: cfg };
    var reps = {}, rel = {}, member = {};
    Object.keys(cfg).forEach(function (id) {
      var f = cfg[id] || {}; reps[id] = +f.player || 0;
      Object.keys(f.relations || {}).forEach(function (o) { rel[id + '|' + o] = +f.relations[o]; rel[o + '|' + id] = rel[o + '|' + id] != null ? rel[o + '|' + id] : +f.relations[o]; });
      (f.members || []).forEach(function (m) { member[m] = id; });
    });
    F.of = function (e) { e = rt.get(e); if (!e) return null; return e.spec.faction || member[e.id] || null; };
    F.rep = function (fid) { return reps[fid] || 0; };
    F.rank = function (fid) { var r = (cfg[fid] || {}).ranks || [], v = F.rep(fid), out = null; r.forEach(function (x) { if (v >= +x.at) out = x.name; }); return out; };
    F.addRep = function (fid, n, why) {
      if (!cfg[fid]) return 0;
      var before = F.rank(fid);
      reps[fid] = U.clamp((reps[fid] || 0) + (+n || 0), -100, 100);
      Object.keys(cfg).forEach(function (o) { var r = rel[fid + '|' + o]; if (o !== fid && r && Math.abs(r) >= 50 && !why) reps[o] = U.clamp((reps[o] || 0) + n * (r / 200), -100, 100); });
      rt.bus.emit('rep', { faction: fid, value: reps[fid], change: n, why: why || null });
      var after = F.rank(fid); if (after !== before) rt.bus.emit('rank', { faction: fid, rank: after });
      return reps[fid];
    };
    F.relation = function (a, b) { if (a === b) return 100; return rel[a + '|' + b] || 0; };
    /** -> true/false when factions decide hostility, null to let combat use teams. Children: never hostile. */
    F.hostile = function (a, b) {
      if (S.isMinor(a) || S.isMinor(b)) return false;
      var fa = F.of(a), fb = F.of(b);
      if (a === rt.player || b === rt.player) { var other = a === rt.player ? fb : fa; if (!other || !cfg[other]) return null; var thr = cfg[other].hostile_below != null ? +cfg[other].hostile_below : -25; return F.rep(other) <= thr || (a === rt.player ? b : a).flags.provoked === true; }
      if (fa && fb) return F.relation(fa, fb) <= -50;
      return null;
    };
    rt.bus.on('damage', function (d) {
      if (d.source !== rt.player || !d.target) return;
      var f = F.of(d.target); if (!f || !cfg[f]) return;
      d.target.flags.provoked = true;
      F.addRep(f, -Math.max(2, Math.round(d.amount * 2)), 'attacked');
    });
    rt.bus.on('death', function (d) { if (d.source === rt.player) { var f = F.of(d.entity); if (f && cfg[f]) F.addRep(f, -15, 'killed'); Object.keys(cfg).forEach(function (o) { if (f && F.relation(o, f) <= -50) F.addRep(o, 3, 'enemy of enemy'); }); } });
    F.save = function () { return U.copy(reps); };
    F.load = function (d) { Object.keys(d || {}).forEach(function (k) { reps[k] = +d[k]; }); };
    return F;
  });

  G.register('economy', 72, function (rt) {
    var cfg = rt.sysdata.economy || {}, E = {}, shops = {}, lastRestock = {};
    Object.keys(cfg.shops || {}).forEach(function (id) { var s = cfg.shops[id]; shops[id] = { id: id, name: s.name || id, owner: s.owner || null, faction: s.faction || null, buy: s.buy_mult != null ? +s.buy_mult : 1, sell: s.sell_mult != null ? +s.sell_mult : 0.5, restock: +s.restock_hours || 24, dynamic: s.dynamic !== false, base: U.copy(s.stock || []), stock: U.copy(s.stock || []) }; lastRestock[id] = 0; });
    E.shops = shops;
    E.wallet = function (e) { e = rt.get(e); if (!e) return null; return e.comps.wallet || (e.comps.wallet = { gold: e === rt.player ? +cfg.start || 0 : +e.spec.gold || 0 }); };
    E.gold = function (e) { var w = E.wallet(e || rt.player); return w ? w.gold : 0; };
    E.add = function (e, n) { var w = E.wallet(e || rt.player); if (!w) return 0; w.gold = Math.max(0, w.gold + Math.round(+n || 0)); rt.bus.emit('gold', { entity: rt.get(e || rt.player), gold: w.gold, change: +n || 0 }); return w.gold; };
    function itemValue(id) { var d = rt.sys.inventory ? rt.sys.inventory.def(id) : {}; return +d.value || +d.price || 5; }
    /** Price the player pays (side 'buy') or gets (side 'sell'); reputation and scarcity move it. */
    E.price = function (sid, id, side) {
      var s = shops[sid]; if (!s) return 0;
      var line = s.stock.filter(function (l) { return l.item === id; })[0], base = line && line.price != null ? +line.price : itemValue(id);
      var mult = side === 'sell' ? s.sell : s.buy;
      var fac = s.faction || (s.owner && rt.sys.factions ? rt.sys.factions.of(s.owner) : null);
      if (fac && rt.sys.factions) mult *= 1 - U.clamp(rt.sys.factions.rep(fac) * (cfg.rep_price != null ? +cfg.rep_price : 0.002), -0.3, 0.3) * (side === 'sell' ? -1 : 1);
      if (s.dynamic && line && side !== 'sell') { var orig = s.base.filter(function (l) { return l.item === id; })[0], q0 = orig ? +orig.qty || 1 : 1; mult *= 1 + U.clamp(0.4 * (1 - (+line.qty || 0) / q0), 0, 0.4); }
      return Math.max(1, Math.round(base * mult));
    };
    E.buy = function (sid, id, n, who) {
      who = rt.get(who || rt.player); n = Math.max(1, +n || 1);
      var s = shops[sid], line = s && s.stock.filter(function (l) { return l.item === id; })[0];
      if (!line || (line.qty != null && line.qty < n)) return { ok: false, why: 'out of stock' };
      var cost = E.price(sid, id, 'buy') * n;
      if (E.gold(who) < cost) return { ok: false, why: 'not enough ' + (cfg.currency || 'gold') };
      if (rt.sys.inventory && rt.sys.inventory.give(who, id, n, true) < n) return { ok: false, why: 'bag is full' };
      E.add(who, -cost); if (line.qty != null) line.qty -= n;
      rt.bus.emit('buy', { shop: sid, item: id, n: n, cost: cost });
      return { ok: true, cost: cost };
    };
    E.sell = function (sid, id, n, who) {
      who = rt.get(who || rt.player); n = Math.max(1, +n || 1);
      var s = shops[sid]; if (!s || !rt.sys.inventory || !rt.sys.inventory.has(who, id, n)) return { ok: false, why: 'nothing to sell' };
      var d = rt.sys.inventory.def(id); if (d.kind === 'quest') return { ok: false, why: 'quest items cannot be sold' };
      var got = E.price(sid, id, 'sell') * n;
      rt.sys.inventory.take(who, id, n); E.add(who, got);
      var line = s.stock.filter(function (l) { return l.item === id; })[0];
      if (line && line.qty != null) line.qty += n; else s.stock.push({ item: id, qty: n });
      rt.bus.emit('sell', { shop: sid, item: id, n: n, gold: got });
      return { ok: true, gold: got };
    };
    E.update = function () {
      if (!rt.sys.env) return;
      var hours = rt.sys.env.totalHours();
      Object.keys(shops).forEach(function (id) { var s = shops[id]; if (hours - lastRestock[id] >= s.restock) { lastRestock[id] = hours; s.stock = U.copy(s.base); rt.bus.emit('restock', { shop: id }); } });
    };
    E.save = function () { var w = {}; rt.ents.forEach(function (e) { if (e.comps.wallet) w[e.id] = e.comps.wallet.gold; }); var st = {}; Object.keys(shops).forEach(function (k) { st[k] = shops[k].stock; }); return { wallets: w, stock: st, restock: U.copy(lastRestock) }; };
    E.load = function (d) { d = d || {}; Object.keys(d.wallets || {}).forEach(function (id) { var w = E.wallet(id); if (w) w.gold = d.wallets[id]; }); Object.keys(d.stock || {}).forEach(function (k) { if (shops[k]) shops[k].stock = d.stock[k]; }); lastRestock = d.restock || lastRestock; };
    return E;
  });
})(GSYS);
