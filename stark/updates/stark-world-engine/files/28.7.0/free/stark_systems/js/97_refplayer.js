/* Stark World Engine Systems: reference player. Plays any world that uses the systems, in a plain browser, offline:
   GSYS.play(container, world) -> {rt, stop}. Canvas 2D views: 'top' (top-down / third-person / vehicles),
   'side' (platformer), 'fp' (first-person raycast), 'grid' (puzzle), 'vn' (visual novel, DOM).
   It is the proof renderer for the sample games; the studio's WebGL2 renderer draws the same entities after
   integration. A world blocked by the child-safety rules never starts: the player shows why instead. */
(function (G) {
  'use strict';
  var U = G.util;
  var MOOD = { 'Bioluminescent Twilight': ['#0b1630', '#12263d', '#5cf2c6'], 'Alpine Sunset': ['#5c7a3e', '#40582c', '#ffd98a'], 'Sub-Zero Aurora': ['#d9e6ef', '#b8cad8', '#7cf0c0'], 'Crimson Citadel': ['#3a1714', '#2a0f0d', '#ff8a4a'], 'Sunken Ruins': ['#8a7d58', '#6c6244', '#9ff7e8'] };
  var KIND = { player: '#ffd166', npc: '#f2c38b', enemy: '#e8505b', item: '#9ff7e8', crystal: '#7cf0ff', chest: '#a0662b', tree: '#2f7d4a', rock: '#8a8f99', ruin: '#7c7f8c', house: '#c9a87c', lamp: '#ffe08a', vehicle: '#5aa9ff', campfire: '#ff9a3c', building: '#b58b5b', shrine: '#c7b6ff', animal: '#b0855a', exit: '#7dff9a', gem: '#7cf0ff', door: '#6b4a2b', station: '#d4a373', portal: '#9b8cff', checkpoint: '#c7c7c7', bush: '#8a3fa0', pond: '#3a7bd5', closet: '#6b4a2b', board: '#c9a87c', station: '#d4a373' };
  function viewFor(world) {
    var sy = world.systems || {}, c = (sy.controller || {}).type, g = sy.genre;
    if (sy.view) return sy.view;
    if (g === 'visual_novel') return 'vn'; if (g === 'puzzle') return 'grid';
    if (c === 'side_scroll') return 'side'; if (c === 'first_person') return 'fp';
    return 'top';
  }
  G.viewFor = viewFor;

  G.play = function (container, world, opt) {
    opt = opt || {};
    var doc = document, rt;
    if ((G.root.getComputedStyle ? G.root.getComputedStyle(container).position : container.style.position) === 'static') container.style.position = 'relative';
    if (!container.clientHeight) container.style.minHeight = '480px';
    var boot = G.safety.boot(world);
    if (!boot.ok) { blocked(container, boot); return { rt: null, stop: function () {}, blocked: boot }; }
    try { rt = G.create(world, { seed: opt.seed }); }
    catch (e) { blocked(container, { message: e.message, report: e.info }); return { rt: null, stop: function () {}, error: e }; }
    var view = opt.view || viewFor(world);
    var cv = doc.createElement('canvas'); cv.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none;outline:none'; cv.tabIndex = 0;
    container.appendChild(cv);
    var g = cv.getContext('2d'), W = 0, H = 0, dpr = 1;
    function resize() { dpr = Math.min(2, G.root.devicePixelRatio || 1); W = container.clientWidth || 960; H = container.clientHeight || 600; cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); g.setTransform(dpr, 0, 0, dpr, 0, 0); }
    resize(); G.root.addEventListener('resize', resize);
    rt.sys.ui.mount(container);
    var mood = MOOD[world.mood] || MOOD['Alpine Sunset'], fx = [], popups = [], keys = {}, mouse = { x: 0, y: 0, down: false, wx: 0, wz: 0 }, paused = false, overlay = null;
    var scale = (world.systems && world.systems.camera && world.systems.camera.scale) || (view === 'side' ? 34 : 30);
    // ------------------------------------------------------------ events -> effects
    rt.bus.on('damage', function (d) { var p = d.target.pos; popups.push({ x: p.x, y: p.y, z: p.z, t: 0.9, text: '-' + (Math.round(d.amount * 10) / 10), col: d.target === rt.player ? '#ff6b6b' : '#fff' }); burst(p, '#ffb3b3', 8); });
    rt.bus.on('death', function (d) { burst(d.entity.pos, '#ff7aa8', 24); });
    rt.bus.on('pickup', function (d) { burst(d.entity.pos, '#9ff7e8', 14); });
    rt.bus.on('tracer', function (d) { if (d.from) fx.push({ tracer: true, a: d.from, b: d.to || d.from, t: 0.08 }); });
    rt.bus.on('win', function () { banner('Victory!', 'You beat ' + (world.name || 'the game') + '.'); });
    rt.bus.on('scriptError', function (d) { if (d.safety) rt.sys.ui.toast('Blocked by child safety: ' + d.message.slice(0, 80)); });
    rt.bus.on('openShop', function (d) { shopPanel(d.shop); });
    rt.bus.on('openCraft', function () { craftPanel(); });
    function burst(p, col, n) { for (var i = 0; i < n; i++) { var a = Math.random() * 6.28, s = 1 + Math.random() * 3; fx.push({ x: p.x, y: p.y, z: p.z, vx: Math.sin(a) * s, vz: Math.cos(a) * s, t: 0.4 + Math.random() * 0.3, col: col }); } }
    function banner(title, sub) { var b = doc.createElement('div'); b.style.cssText = 'position:absolute;left:0;right:0;top:32%;text-align:center;color:#fff;text-shadow:0 3px 18px #000;pointer-events:none;z-index:8'; var h = doc.createElement('div'); h.style.cssText = 'font:700 48px system-ui'; h.textContent = title; var s = doc.createElement('div'); s.style.cssText = 'font:16px system-ui;opacity:.85'; s.textContent = sub; b.appendChild(h); b.appendChild(s); container.appendChild(b); }
    // ------------------------------------------------------------ input
    function key(e, down) {
      var k = e.code; keys[k] = down;
      var I = rt.input, gen = rt.sys.genres && rt.sys.genres.kit, D = rt.sys.dialogue;
      I.move.x = (keys.KeyD || keys.ArrowRight ? 1 : 0) - (keys.KeyA || keys.ArrowLeft ? 1 : 0);
      I.move.y = (keys.KeyW || keys.ArrowUp ? 1 : 0) - (keys.KeyS || keys.ArrowDown ? 1 : 0);
      I.run = !!(keys.ShiftLeft || keys.ShiftRight); I.jumpHeld = !!keys.Space; I.alt = !!keys.ShiftLeft && view === 'top' && rt.player && rt.player.comps.ctrl && rt.player.comps.ctrl.type === 'vehicle';
      if (!down) return;
      if (k === 'Tab') { e.preventDefault(); invPanel(); return; }
      if (k === 'Escape') { if (overlay) closeOverlay(); else togglePause(); return; }
      if (D && D.busy()) { if (/^Digit[1-9]$/.test(k)) rt.sys.ui.choose(+k.slice(5) - 1); else if (k === 'Space' || k === 'Enter' || k === 'KeyE') rt.sys.ui.advance(); e.preventDefault(); return; }
      if (view === 'grid' && gen && gen.move) { var mv = { ArrowUp: [0, -1], KeyW: [0, -1], ArrowDown: [0, 1], KeyS: [0, 1], ArrowLeft: [-1, 0], KeyA: [-1, 0], ArrowRight: [1, 0], KeyD: [1, 0] }[k]; if (mv) gen.move(mv[0], mv[1]); if (k === 'KeyZ') gen.undo(); if (k === 'KeyR') gen.restart(); e.preventDefault(); return; }
      if (k === 'Space') { I.jump = true; e.preventDefault(); }
      if (k === 'KeyE') interact();
      if (k === 'KeyJ') attack();
      if (k === 'KeyK' || k === 'KeyL') { var ab = ((world.systems || {}).input || {}).abilities || Object.keys(rt.sys.combat.abilities); var id = ab[k === 'KeyK' ? 0 : 1]; if (id) rt.sys.combat.useAbility(rt.player, id, mouse.used ? { x: mouse.wx, z: mouse.wz } : null); }
      if (k === 'KeyQ') { var inv = rt.sys.inventory.of(rt.player), it = inv.items.filter(function (s) { var d = rt.sys.inventory.def(s.id); return d.use || d.heal; })[0]; if (it) rt.sys.inventory.use(rt.player, it.id); }
      if (k === 'KeyR') { if ((rt.player && rt.player.dead) || (gen && gen.over)) location.reload(); else rt.sys.combat.reload(rt.player); }
      if (k === 'KeyF' && gen && gen.toggleLight) gen.toggleLight();
      if (k === 'KeyC') craftPanel();
      if (k === 'KeyM') rt.sys.audio.mute(!rt.sys.audio.muted);
      if (k === 'F5') { e.preventDefault(); rt.sys.save.save(1); rt.sys.ui.toast('Saved (slot 1)'); }
      if (k === 'F9') { e.preventDefault(); if (rt.sys.save.load(1)) rt.sys.ui.toast('Loaded slot 1'); }
      if (/^Digit[1-9]$/.test(k)) { var n = +k.slice(5); if (rt.sysdata.genre === 'shooter') { var ws = ((world.systems || {}).input || {}).weapons || ['pistol', 'shotgun']; if (ws[n - 1]) { rt.player.comps.fighter.weapon = ws[n - 1]; rt.sys.ui.toast(ws[n - 1]); } } if (rt.sysdata.genre === 'strategy') stratSel = Object.keys(gen.defs)[n - 1] || null; }
    }
    doc.addEventListener('keydown', function (e) { if (!paused || e.code === 'Escape') key(e, true); });
    doc.addEventListener('keyup', function (e) { key(e, false); });
    cv.addEventListener('mousemove', function (e) { var r = cv.getBoundingClientRect(); mouse.x = e.clientX - r.left; mouse.y = e.clientY - r.top; mouse.used = true; if (view === 'fp' && doc.pointerLockElement === cv) { rt.input.look.dx += e.movementX; rt.input.look.dy += e.movementY; } });
    cv.addEventListener('mousedown', function (e) {
      cv.focus(); mouse.down = true;
      if (view === 'fp' && doc.pointerLockElement !== cv && cv.requestPointerLock) { try { cv.requestPointerLock(); } catch (er) { /* ignore */ } }
      if (rt.sysdata.genre === 'strategy' && stratSel) { var r = rt.sys.genres.kit.build(stratSel, mouse.wx, mouse.wz); if (!r.ok) rt.sys.ui.toast(r.why); return; }
      if (rt.player && rt.player.comps.ctrl && rt.player.comps.ctrl.type === 'point_click') { rt.input.clickTo = { x: mouse.wx, z: mouse.wz }; return; }
      attack();
    });
    cv.addEventListener('mouseup', function () { mouse.down = false; });
    function attack() { if (rt.player && !rt.player.dead && rt.sys.combat && !(rt.sys.dialogue && rt.sys.dialogue.busy())) rt.sys.combat.attack(rt.player, null, (view === 'top' && mouse.used) ? { x: mouse.wx, z: mouse.wz } : null); }
    function interactables() { var p = rt.player; return p ? rt.near(p.pos, view === 'side' ? 1.6 : 2.4, function (e) { return e !== p && !e.dead && (e.kind === 'npc' || e.kind === 'chest' || e.kind === 'exit' || e.kind === 'door' || e.spec.station || e.spec.board || e.spec.resource || e.spec.water || e.spec.hide || e.spec.interact); }) : []; }
    function interact() { var list = interactables(); if (!list.length) return; var p = rt.player; list.sort(function (a, b) { return U.dist(a.pos, p.pos) - U.dist(b.pos, p.pos); }); rt.bus.emit('interact', { target: list[0], by: p }); }
    var stratSel = null;
    // ------------------------------------------------------------ overlays (inventory, crafting, shop, pause)
    function panel(title) { closeOverlay(); var o = doc.createElement('div'); o.style.cssText = 'position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);min-width:320px;max-width:90%;max-height:80%;overflow:auto;background:rgba(12,14,28,.94);color:#fff;border:1px solid rgba(255,255,255,.2);border-radius:12px;padding:16px;z-index:9;font:14px system-ui'; var h = doc.createElement('div'); h.style.cssText = 'font-weight:700;font-size:17px;margin-bottom:8px'; h.textContent = title + '  (Esc to close)'; o.appendChild(h); container.appendChild(o); overlay = o; paused = true; return o; }
    function row(o, text, label, fn) { var r = doc.createElement('div'); r.style.cssText = 'display:flex;justify-content:space-between;gap:12px;align-items:center;margin:4px 0'; var s = doc.createElement('span'); s.textContent = text; r.appendChild(s); if (label) { var b = doc.createElement('button'); b.textContent = label; b.style.cssText = 'background:#2d6a5a;color:#fff;border:0;border-radius:6px;padding:4px 10px;cursor:pointer'; b.onclick = function () { fn(); }; r.appendChild(b); } o.appendChild(r); }
    function closeOverlay() { if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay); overlay = null; paused = false; cv.focus(); }
    function invPanel() { if (overlay) return closeOverlay(); var I = rt.sys.inventory, inv = I.of(rt.player), o = panel('Bag - ' + (rt.sys.economy ? rt.sys.economy.gold() + ' gold' : '')); if (!inv.items.length) row(o, 'Empty'); inv.items.forEach(function (s) { var d = I.def(s.id); row(o, d.name + (s.n > 1 ? ' x' + s.n : '') + (Object.keys(inv.eq).some(function (k) { return inv.eq[k] === s.id; }) ? ' (equipped)' : ''), d.equip ? 'Equip' : (d.use || d.heal) ? 'Use' : null, function () { I.use(rt.player, s.id); invPanel(); invPanel(); }); }); var P = rt.sys.progression, pr = P.of(rt.player); row(o, 'Level ' + pr.level + ' - XP ' + pr.xp + '/' + P.need(pr.level) + ' - skill points ' + pr.points); var trees = ((world.systems || {}).progression || {}).skill_trees || {}; Object.keys(trees).forEach(function (t) { Object.keys(trees[t].nodes || {}).forEach(function (nid) { var n = trees[t].nodes[nid], have = P.hasUnlock(rt.player, t + '.' + nid); row(o, '[' + (trees[t].name || t) + '] ' + (n.name || nid) + (have ? ' - learned' : ' (' + (n.cost || 1) + ' pt)'), have ? null : 'Learn', function () { var r = P.learn(rt.player, t, nid); rt.sys.ui.toast(r.ok ? 'Learned ' + (n.name || nid) : r.why); invPanel(); invPanel(); }); }); }); }
    function craftPanel() { if (overlay) return closeOverlay(); var I = rt.sys.inventory, o = panel('Crafting'); var ids = Object.keys(I.recipes); if (!ids.length) row(o, 'No recipes in this game.'); ids.forEach(function (rid) { var r = I.recipes[rid], need = Object.keys(r.in || {}).map(function (k) { return r.in[k] + ' ' + I.def(k).name; }).join(', '), out = Object.keys(r.out || {}).map(function (k) { return I.def(k).name; }).join(', '); var c = I.canCraft(rt.player, rid); row(o, out + '  <-  ' + need + (r.station ? ' (at ' + r.station + ')' : '') + (c.ok ? '' : ' - ' + c.why), 'Craft', function () { var res = I.craft(rt.player, rid); rt.sys.ui.toast(res.ok ? 'Crafted ' + out : res.why); craftPanel(); craftPanel(); }); }); }
    function shopPanel(sid) { var E = rt.sys.economy, s = E.shops[sid]; if (!s) return; var o = panel((s.name || 'Shop') + ' - you have ' + E.gold() + ' gold'); s.stock.forEach(function (l) { row(o, rt.sys.inventory.def(l.item).name + (l.qty != null ? ' (' + l.qty + ' left)' : '') + ' - ' + E.price(sid, l.item, 'buy') + 'g', 'Buy', function () { var r = E.buy(sid, l.item, 1); rt.sys.ui.toast(r.ok ? 'Bought' : r.why); shopPanel(sid); }); }); rt.sys.inventory.of(rt.player).items.forEach(function (it) { var d = rt.sys.inventory.def(it.id); if (d.kind !== 'quest') row(o, 'Sell ' + d.name + ' for ' + E.price(sid, it.id, 'sell') + 'g', 'Sell', function () { E.sell(sid, it.id, 1); shopPanel(sid); }); }); }
    function togglePause() { if (overlay) return closeOverlay(); var o = panel('Paused'); rt.sys.save.slots().forEach(function (s) { row(o, 'Slot ' + s.slot + (s.used ? ' - saved' : ' - empty'), 'Save', function () { rt.sys.save.save(s.slot); togglePause(); togglePause(); }); if (s.used) row(o, '', 'Load', function () { rt.sys.save.load(s.slot); closeOverlay(); }); }); row(o, 'Keys: WASD move, Shift run, Space jump, E use/talk, J or click attack, K/L abilities, Q potion, Tab bag, C craft, F5/F9 quick save/load, M mute'); }
    // ------------------------------------------------------------ drawing helpers
    var camX = 0, camZ = 0;
    function sx(x) { return W / 2 + (x - camX) * scale; } function sz(z) { return H / 2 + (z - camZ) * scale; }
    function circle(x, y, r, col) { g.fillStyle = col; g.beginPath(); g.arc(x, y, r, 0, 6.283); g.fill(); }
    function label(t, x, y, col) { g.font = '12px system-ui'; g.textAlign = 'center'; g.fillStyle = 'rgba(0,0,0,.6)'; g.fillText(t, x + 1, y + 1); g.fillStyle = col || '#fff'; g.fillText(t, x, y); }
    function drawTop() {
      var cam = rt.camera; camX = cam.x; camZ = cam.z;
      var sh = cam.shake * 8; if (sh) { camX += (Math.random() - 0.5) * sh / scale; camZ += (Math.random() - 0.5) * sh / scale; }
      var grd = g.createLinearGradient(0, 0, 0, H); grd.addColorStop(0, mood[0]); grd.addColorStop(1, mood[1]); g.fillStyle = grd; g.fillRect(0, 0, W, H);
      g.globalAlpha = 0.07; g.fillStyle = '#000'; var step = scale * 2, ox = ((-camX * scale) % step + step) % step, oz = ((-camZ * scale) % step + step) % step;
      for (var gx = ox - step; gx < W; gx += step) for (var gz = oz - step; gz < H; gz += step) if (((Math.round((gx - ox) / step) + Math.round((gz - oz) / step)) & 1) === 0) g.fillRect(gx + W / 2 % step, gz + H / 2 % step, step, step);
      g.globalAlpha = 1;
      var kit = rt.sys.genres && rt.sys.genres.kit;
      if (kit && kit.track && kit.track.length) { g.lineJoin = 'round'; g.lineCap = 'round'; g.strokeStyle = '#3b3f47'; g.lineWidth = kit.width * scale; g.beginPath(); kit.track.forEach(function (p, i) { if (i) g.lineTo(sx(p.x), sz(p.z)); else g.moveTo(sx(p.x), sz(p.z)); }); g.closePath(); g.stroke(); g.strokeStyle = 'rgba(255,255,255,.35)'; g.lineWidth = 2; g.setLineDash([12, 12]); g.stroke(); g.setLineDash([]); var a = kit.track[0], b = kit.track[1], an = Math.atan2(b.z - a.z, b.x - a.x); g.save(); g.translate(sx(a.x), sz(a.z)); g.rotate(an + Math.PI / 2); g.fillStyle = '#fff'; for (var c = -4; c < 4; c++) { g.fillStyle = (c & 1) ? '#fff' : '#111'; g.fillRect(c * kit.width * scale / 8, -4, kit.width * scale / 8, 8); } g.restore(); }
      var ph = rt.sys.physics, T = ph && ph.tiles;
      if (T) for (var y = 0; y < T.h; y++) for (var x = 0; x < T.w; x++) { var ch = T.rows[y][x], X = sx(x * T.cell + T.ox), Z = sz(y * T.cell + T.oz), s = T.cell * scale; if (X > W || Z > H || X < -s || Z < -s) continue; if (T.solid.indexOf(ch) >= 0) { g.fillStyle = '#3a3542'; g.fillRect(X, Z, s + 1, s + 1); g.fillStyle = 'rgba(255,255,255,.08)'; g.fillRect(X, Z, s + 1, 3); } else if (ch === '~') { g.fillStyle = 'rgba(60,140,220,.6)'; g.fillRect(X, Z, s + 1, s + 1); } }
      (ph ? ph.statics : []).forEach(function (b) { g.fillStyle = b.color || '#4b4552'; g.fillRect(sx(b.x0), sz(b.z0), (b.x1 - b.x0) * scale, (b.z1 - b.z0) * scale); g.fillStyle = 'rgba(0,0,0,.25)'; g.fillRect(sx(b.x0), sz(b.z1), (b.x1 - b.x0) * scale, 5); });
      if (stratSel && kit && kit.defs) { var gxp = Math.round(mouse.wx / 2) * 2, gzp = Math.round(mouse.wz / 2) * 2; g.globalAlpha = 0.4; g.fillStyle = kit.canAfford(stratSel) ? '#7dff9a' : '#ff6b6b'; g.fillRect(sx(gxp - 1.2), sz(gzp - 1.2), 2.4 * scale, 2.4 * scale); g.globalAlpha = 1; }
      var list = []; rt.ents.forEach(function (e) { if (!e.flags.culled) list.push(e); }); list.sort(function (a, b) { return a.pos.z - b.pos.z; });
      list.forEach(function (e) { drawEnt(e); });
      rt.sys.combat.projectiles.forEach(function (p) { circle(sx(p.x), sz(p.z), Math.max(2, p.r * scale * 0.6), p.owner === rt.player ? '#ffe9a8' : '#ff8a8a'); });
      fx.forEach(function (f) { if (f.tracer) { g.strokeStyle = 'rgba(255,240,180,' + (f.t / 0.08) + ')'; g.lineWidth = 2; g.beginPath(); g.moveTo(sx(f.a.x), sz(f.a.z)); g.lineTo(sx(f.b.x), sz(f.b.z)); g.stroke(); } else circle(sx(f.x), sz(f.z), 2.5, f.col); });
      popups.forEach(function (p) { label(p.text, sx(p.x), sz(p.z) - 30 - (0.9 - p.t) * 30, p.col); });
      lighting(); weather();
    }
    function drawEnt(e) {
      var X = sx(e.pos.x), Z = sz(e.pos.z), r = ((e.comps.body && e.comps.body.r) || 0.4) * scale, col = e.spec.col || KIND[e.kind] || '#ccc', s = +e.spec.s || 1;
      if (X < -80 || Z < -80 || X > W + 80 || Z > H + 80) return;
      g.fillStyle = 'rgba(0,0,0,.25)'; g.beginPath(); g.ellipse(X, Z + r * 0.6, r * 1.1, r * 0.45, 0, 0, 6.283); g.fill();
      if (e.kind === 'tree') { circle(X, Z - 10 * s, 15 * s * scale / 30, '#1f5c36'); circle(X - 5 * s, Z - 16 * s, 11 * s * scale / 30, '#2f7d4a'); circle(X + 5, Z - 19 * s, 8 * s * scale / 30, '#3f9a5c'); return; }
      if (e.kind === 'rock') { g.fillStyle = '#7d828c'; g.beginPath(); g.ellipse(X, Z - 4, 12 * s, 9 * s, 0.3, 0, 6.283); g.fill(); g.fillStyle = '#9aa0aa'; g.beginPath(); g.ellipse(X - 3, Z - 7, 6 * s, 4 * s, 0.3, 0, 6.283); g.fill(); return; }
      if (e.kind === 'house' || e.kind === 'building') { var w = (e.spec.w || 4) * scale * 0.6, h = (e.spec.d || 3) * scale * 0.6; g.fillStyle = '#8a6a48'; g.fillRect(X - w / 2, Z - h / 2, w, h); g.fillStyle = '#b5463c'; g.beginPath(); g.moveTo(X - w / 2 - 4, Z - h / 2); g.lineTo(X, Z - h / 2 - h * 0.7); g.lineTo(X + w / 2 + 4, Z - h / 2); g.fill(); if (e.spec.name) label(e.spec.name, X, Z + h / 2 + 14, '#ffe'); return; }
      if (e.kind === 'lamp') { g.fillStyle = '#4a4036'; g.fillRect(X - 2, Z - 18, 4, 18); circle(X, Z - 20, 5, '#ffe08a'); return; }
      if (e.kind === 'pond') { g.fillStyle = 'rgba(58,123,213,.75)'; g.beginPath(); g.ellipse(X, Z, 3 * scale, 2 * scale, 0, 0, 6.283); g.fill(); label(e.name, X, Z, '#dff'); return; }
      if (e.kind === 'campfire') { var on = !e.flags.out; circle(X, Z, 7, '#5a3a22'); if (on) { circle(X, Z - 3, 6 + Math.sin(rt.time * 12) * 1.5, '#ff9a3c'); circle(X, Z - 5, 3, '#ffe08a'); } return; }
      if (e.kind === 'item' || e.kind === 'crystal' || e.kind === 'gem') { g.save(); g.translate(X, Z - 6 + Math.sin(rt.time * 3 + e.pos.x) * 2); g.rotate(0.785); g.fillStyle = col; g.fillRect(-5, -5, 10, 10); g.restore(); return; }
      if (e.kind === 'chest') { g.fillStyle = e.flags.open ? '#5a3a1a' : col; g.fillRect(X - 9, Z - 8, 18, 12); g.fillStyle = '#ffd166'; g.fillRect(X - 2, Z - 4, 4, 4); return; }
      if (e.kind === 'vehicle' || (e.comps.ctrl && e.comps.ctrl.type === 'vehicle')) { g.save(); g.translate(X, Z); g.rotate(e.rot); g.fillStyle = e === rt.player ? '#ffd166' : col; g.fillRect(-0.55 * scale, -0.95 * scale, 1.1 * scale, 1.9 * scale); g.fillStyle = 'rgba(0,0,0,.5)'; g.fillRect(-0.45 * scale, -0.6 * scale, 0.9 * scale, 0.5 * scale); g.restore(); label(e.name, X, Z - scale * 1.3); return; }
      var hit = e.flags.hit > 0, minor = G.safety.isMinor(e);
      circle(X, Z - r * 0.4, r * (minor ? 0.8 : 1), hit ? '#fff' : (e.dead ? '#555' : col));
      g.strokeStyle = 'rgba(0,0,0,.35)'; g.lineWidth = 2; g.beginPath(); g.arc(X, Z - r * 0.4, r * (minor ? 0.8 : 1), 0, 6.283); g.stroke();
      if (!e.dead) { g.strokeStyle = 'rgba(0,0,0,.6)'; g.beginPath(); g.moveTo(X, Z - r * 0.4); g.lineTo(X + Math.sin(e.rot) * r, Z - r * 0.4 + Math.cos(e.rot) * r); g.stroke(); }
      if (e.kind === 'enemy' && !e.dead) { g.fillStyle = '#300'; g.fillRect(X - 14, Z - r * 1.8, 28, 4); g.fillStyle = '#ff5a5a'; g.fillRect(X - 14, Z - r * 1.8, 28 * U.clamp(e.hp / e.maxHp, 0, 1), 4); }
      if ((e.kind === 'npc' || e.kind === 'shrine' || e.spec.board) && rt.player && U.dist(e.pos, rt.player.pos) < 6) label(e.name + (e.spec.board ? ' (E: take a job)' : ''), X, Z - r * 1.9, minor ? '#bfe3ff' : '#ffe9b8');
      if (e.kind === 'player' && e.flags.hidden) label('(hidden)', X, Z - r * 2);
    }
    function lighting() {
      var env = rt.sys.env; if (!env) return;
      var dark = 1 - env.daylight(); dark = U.clamp(dark * 0.82, 0, 0.82);
      if (dark < 0.04) return;
      var L = lighting.c || (lighting.c = doc.createElement('canvas')); L.width = W; L.height = H; var l = L.getContext('2d');
      l.fillStyle = 'rgba(8,10,30,' + dark + ')'; l.fillRect(0, 0, W, H); l.globalCompositeOperation = 'destination-out';
      function hole(x, y, r) { var gr = l.createRadialGradient(x, y, 0, x, y, r); gr.addColorStop(0, 'rgba(0,0,0,1)'); gr.addColorStop(1, 'rgba(0,0,0,0)'); l.fillStyle = gr; l.beginPath(); l.arc(x, y, r, 0, 6.283); l.fill(); }
      if (rt.player) hole(sx(rt.player.pos.x), sz(rt.player.pos.z), scale * 5);
      rt.ents.forEach(function (e) { if ((e.kind === 'lamp' || (e.kind === 'campfire' && !e.flags.out) || e.kind === 'shrine') && !e.flags.culled) hole(sx(e.pos.x), sz(e.pos.z), scale * (e.kind === 'campfire' ? 6 : 4)); });
      g.drawImage(L, 0, 0, W, H);
    }
    var drops = []; for (var di = 0; di < 140; di++) drops.push({ x: Math.random(), y: Math.random(), s: 0.5 + Math.random() });
    function weather() {
      var w = rt.sys.env && rt.sys.env.weather().type; if (!w || w === 'clear' || w === 'auto') return;
      g.strokeStyle = w === 'snow' ? 'rgba(255,255,255,.85)' : w === 'embers' ? 'rgba(255,150,60,.8)' : 'rgba(170,200,255,.5)'; g.fillStyle = g.strokeStyle; g.lineWidth = 1;
      drops.forEach(function (d) { d.y += (w === 'snow' ? 0.002 : 0.02) * d.s; d.x += w === 'snow' ? Math.sin(rt.time + d.s * 9) * 0.0008 : 0.003; if (d.y > 1) d.y = 0; if (d.x > 1) d.x = 0; var X = d.x * W, Y = d.y * H; if (w === 'rain' || w === 'storm') { g.beginPath(); g.moveTo(X, Y); g.lineTo(X - 3, Y + 12); g.stroke(); } else { g.beginPath(); g.arc(X, Y, w === 'snow' ? 2 : 1.4, 0, 6.283); g.fill(); } });
      if (w === 'fog') { g.fillStyle = 'rgba(200,210,220,.25)'; g.fillRect(0, 0, W, H); }
      if (w === 'storm' && Math.random() < 0.004) { g.fillStyle = 'rgba(255,255,255,.5)'; g.fillRect(0, 0, W, H); }
    }
    function drawSide() {
      var cam = rt.camera, s = scale; camX = cam.x; var camY = cam.y;
      var grd = g.createLinearGradient(0, 0, 0, H); grd.addColorStop(0, '#6ec3ff'); grd.addColorStop(1, mood[2] === '#ffd98a' ? '#ffe2b0' : '#cfe8ff'); g.fillStyle = grd; g.fillRect(0, 0, W, H);
      g.fillStyle = 'rgba(60,120,90,.35)'; g.beginPath(); g.moveTo(0, H); for (var x = 0; x <= W; x += 20) g.lineTo(x, H * 0.62 + Math.sin((x + camX * s * 0.3) * 0.01) * 40); g.lineTo(W, H); g.fill();
      var T = rt.sys.physics.tiles; function X(x) { return W / 2 + (x - camX) * s; } function Y(y) { return H / 2 + (y - camY) * s; }
      if (T) for (var ty = 0; ty < T.h; ty++) for (var tx = Math.max(0, Math.floor(camX - W / 2 / s) - 1); tx < Math.min(T.w, camX + W / 2 / s + 1); tx++) { var ch = T.rows[ty][tx]; if (T.solid.indexOf(ch) >= 0) { g.fillStyle = '#6b4f3a'; g.fillRect(X(tx), Y(ty), s + 1, s + 1); if (ty === 0 || T.solid.indexOf(T.rows[ty - 1][tx]) < 0) { g.fillStyle = '#56b35a'; g.fillRect(X(tx), Y(ty), s + 1, 6); } } else if (T.oneway.indexOf(ch) >= 0) { g.fillStyle = '#c7a26b'; g.fillRect(X(tx), Y(ty), s + 1, 7); } else if (ch === '~') { g.fillStyle = 'rgba(40,120,220,.5)'; g.fillRect(X(tx), Y(ty), s + 1, s + 1); } }
      rt.ents.forEach(function (e) {
        var x = X(e.pos.x), y = Y(e.pos.y);
        if (e.kind === 'gem') { g.save(); g.translate(x, y + Math.sin(rt.time * 4 + e.pos.x) * 3); g.rotate(0.785); g.fillStyle = '#7cf0ff'; g.fillRect(-6, -6, 12, 12); g.restore(); }
        else if (e.kind === 'spike') { g.fillStyle = '#ddd'; for (var k = -1; k <= 1; k++) { g.beginPath(); g.moveTo(x + k * 10 - 6, y + 10); g.lineTo(x + k * 10, y - 6); g.lineTo(x + k * 10 + 6, y + 10); g.fill(); } }
        else if (e.kind === 'goal') { g.fillStyle = '#eee'; g.fillRect(x - 2, y - 30, 4, 46); g.fillStyle = '#ffd166'; g.beginPath(); g.moveTo(x + 2, y - 30); g.lineTo(x + 26, y - 22); g.lineTo(x + 2, y - 14); g.fill(); }
        else if (e.kind === 'checkpoint') { g.fillStyle = '#bbb'; g.fillRect(x - 2, y - 20, 4, 32); circle(x, y - 22, 6, '#7dff9a'); }
        else if (e.kind === 'platform') { g.fillStyle = '#a5794a'; g.fillRect(x - s * 1.2, y - 6, s * 2.4, 12); }
        else if (e.kind === 'enemy' && !e.dead) { var bw = s * 0.85; g.fillStyle = e.flags.hit > 0 ? '#fff' : '#9b4dff'; g.beginPath(); g.ellipse(x, y - bw / 2, bw / 2, bw / 2.2, 0, 0, 6.283); g.fill(); circle(x - 6, y - bw / 2 - 4, 3, '#fff'); circle(x + 6, y - bw / 2 - 4, 3, '#fff'); }
        else if (e === rt.player) { var pw = s * 0.7, phh = s * 0.9, f = e.comps.fighter, blink = f && f.iframes > 0 && Math.floor(rt.time * 20) % 2; if (!blink) { g.fillStyle = '#ffd166'; g.fillRect(x - pw / 2, y - phh, pw, phh); g.fillStyle = '#222'; g.fillRect(x + (e.rot || 1) * 4 - 2, y - phh + 8, 4, 5); } }
        else if (e.kind === 'npc') { g.fillStyle = '#f2c38b'; g.fillRect(x - s * 0.35, y - s * 0.9, s * 0.7, s * 0.9); label(e.name, x, y - s - 4); }
      });
      popups.forEach(function (p) { label(p.text, X(p.x), Y(p.y) - 30, p.col); });
    }
    var FP = { w: 320, h: 200, c: null, z: [] };
    function drawFp() {
      var p = rt.player, T = rt.sys.physics.tiles; if (!p || !T) return;
      var c = FP.c || (FP.c = doc.createElement('canvas')); c.width = FP.w; c.height = FP.h; var l = c.getContext('2d'), img = l.createImageData(FP.w, FP.h), px = img.data;
      var kit = rt.sys.genres.kit, lightOn = !!(kit && kit.light), dir = p.rot, fov = 1.1, zb = FP.z;
      var amb = lightOn ? 0.04 : 0.015, flick = lightOn && kit.battery < 20 ? (Math.random() < 0.1 ? 0.3 : 1) : 1;
      for (var x = 0; x < FP.w; x++) {
        var ang = dir + (x / FP.w - 0.5) * fov, dx = Math.sin(ang), dz = -Math.cos(ang);
        var r = rt.sys.physics.ray(p.pos.x, p.pos.z, dx, dz, 24), d = r.dist * Math.cos(ang - dir); zb[x] = d;
        var hgt = Math.min(FP.h * 2, FP.h / Math.max(0.2, d)), top = (FP.h - hgt) / 2 + p.pitch * 60, cone = Math.max(0, 1 - Math.abs(x / FP.w - 0.5) * 2.4);
        var lum = Math.min(1, amb + (lightOn ? flick * cone * 1.6 / (1 + d * d * 0.07) : 0) + 0.25 / (1 + d * d));
        var ch = r.hit && r.hit.tile ? T.rows[r.hit.cz][r.hit.cx] : '#', base = ch === '%' ? [120, 90, 70] : ch === '&' ? [90, 100, 120] : [110, 105, 95];
        var side = r.hit && r.hit.tile && Math.abs(r.x - Math.round(r.x)) < 0.02 ? 0.8 : 1;
        for (var y = 0; y < FP.h; y++) {
          var i = (y * FP.w + x) * 4, v;
          if (y >= top && y < top + hgt && r.hit) { var stripe = ((r.x + r.z) * 4 % 1) < 0.08 ? 0.85 : 1; v = lum * side * stripe; px[i] = base[0] * v; px[i + 1] = base[1] * v; px[i + 2] = base[2] * v; }
          else { var fl = y > FP.h / 2 + p.pitch * 60, dd = Math.abs(FP.h / 2 + p.pitch * 60 - y) + 1, ld = FP.h / dd, fv = Math.min(1, amb + (lightOn ? flick * cone * 1.2 / (1 + ld * ld * 0.08) : 0)); px[i] = (fl ? 50 : 20) * fv * 3; px[i + 1] = (fl ? 45 : 22) * fv * 3; px[i + 2] = (fl ? 40 : 30) * fv * 3; }
          px[i + 3] = 255;
        }
      }
      l.putImageData(img, 0, 0);
      var sprites = []; rt.ents.forEach(function (e) { if (e === p || e.dead) return; var vx = e.pos.x - p.pos.x, vz = e.pos.z - p.pos.z, dist = Math.hypot(vx, vz); if (dist > 22 || dist < 0.2) return; var a = U.wrap(Math.atan2(vx, -vz) - dir); if (Math.abs(a) > fov) return; sprites.push({ e: e, d: dist * Math.cos(a), a: a }); });
      sprites.sort(function (a, b) { return b.d - a.d; });
      sprites.forEach(function (s2) {
        var e = s2.e, cx = (s2.a / fov + 0.5) * FP.w, h = FP.h / Math.max(0.3, s2.d) * (e.kind === 'enemy' ? 1.1 : 0.4), w = h * (e.kind === 'enemy' ? 0.45 : 1), top = FP.h / 2 + p.pitch * 60 - (e.kind === 'enemy' ? h / 2 : -FP.h / Math.max(0.3, s2.d) * 0.3);
        var cone = Math.max(0, 1 - Math.abs(cx / FP.w - 0.5) * 2.4), lum = Math.min(1, 0.06 + (lightOn ? cone * 1.5 / (1 + s2.d * s2.d * 0.07) : 0) + (e.kind === 'item' || e.kind === 'exit' ? 0.5 : 0));
        for (var sxp = Math.max(0, Math.floor(cx - w / 2)); sxp < Math.min(FP.w, cx + w / 2); sxp++) {
          if (zb[sxp] < s2.d) continue;
          l.globalAlpha = 1;
          l.fillStyle = e.kind === 'enemy' ? 'rgba(' + Math.round(40 * lum + 5) + ',' + Math.round(10 * lum) + ',' + Math.round(14 * lum) + ',0.95)' : e.kind === 'exit' ? 'rgba(80,255,140,' + lum + ')' : e.spec.hide ? 'rgba(120,90,60,' + lum + ')' : 'rgba(255,230,120,' + lum + ')';
          l.fillRect(sxp, top, 1, h);
        }
        if (e.kind === 'enemy' && lum > 0.1) { l.fillStyle = 'rgba(255,40,40,' + Math.min(1, lum * 2) + ')'; l.fillRect(cx - w * 0.15, top + h * 0.18, 2, 2); l.fillRect(cx + w * 0.1, top + h * 0.18, 2, 2); }
      });
      g.imageSmoothingEnabled = false; g.drawImage(c, 0, 0, W, H);
      var sanity = kit && kit.sanity != null ? kit.sanity : 100;
      if (sanity < 60) { var vg = g.createRadialGradient(W / 2, H / 2, H * 0.2, W / 2, H / 2, H * 0.8); vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(60,0,10,' + (0.8 - sanity / 100) + ')'); g.fillStyle = vg; g.fillRect(0, 0, W, H); }
      if (doc.pointerLockElement !== cv) label('Click to look around (mouse), WASD move, Shift run (noisy), F flashlight, E use', W / 2, H - 30, '#ccc');
    }
    function drawGrid() {
      var K = rt.sys.genres.kit; if (!K || !K.grid) return;
      g.fillStyle = '#1b1d2a'; g.fillRect(0, 0, W, H);
      var rows = K.grid, cw = Math.floor(Math.min((W - 40) / rows[0].length, (H - 80) / rows.length)), ox = (W - cw * rows[0].length) / 2, oy = (H - cw * rows.length) / 2 + 10, open = K.doorsOpen();
      rows.forEach(function (row, y) { row.forEach(function (c, x) { var X = ox + x * cw, Y = oy + y * cw; g.fillStyle = c === '#' ? '#4a4e69' : c === 'i' ? '#bfe7ff' : '#2b2d42'; g.fillRect(X, Y, cw - 1, cw - 1); if (c === 'x') { g.strokeStyle = '#ffd166'; g.lineWidth = 3; g.strokeRect(X + cw * 0.2, Y + cw * 0.2, cw * 0.6, cw * 0.6); } if (c === 's') circle(X + cw / 2, Y + cw / 2, cw * 0.2, '#7dff9a'); if (c === 'd') { g.fillStyle = open ? 'rgba(125,255,154,.25)' : '#a05a3c'; g.fillRect(X + 3, Y + 3, cw - 7, cw - 7); } }); });
      K.boxes.forEach(function (b) { var on = K.targets.some(function (t) { return t.x === b.x && t.y === b.y; }); g.fillStyle = on ? '#7dff9a' : '#c8945a'; g.fillRect(ox + b.x * cw + 4, oy + b.y * cw + 4, cw - 9, cw - 9); g.strokeStyle = 'rgba(0,0,0,.4)'; g.strokeRect(ox + b.x * cw + 8, oy + b.y * cw + 8, cw - 17, cw - 17); });
      circle(ox + K.player.x * cw + cw / 2, oy + K.player.y * cw + cw / 2, cw * 0.34, '#ffd166');
      label('Arrows / WASD move - Z undo - R restart', W / 2, H - 14, '#aab');
    }
    var vnEl = null;
    function drawVn() {
      var K = rt.sys.genres.kit; if (!K) return;
      var BG = { dusk: ['#2b1d3a', '#e08a5a'], night: ['#060818', '#1d2550'], day: ['#7ec8ff', '#f4f1d0'], rain: ['#2a3340', '#56606e'], inn: ['#3a2416', '#9a6a3c'], garden: ['#2f5a3a', '#bfe3a0'] };
      var bg = BG[K.bg] || BG.dusk, grd = g.createLinearGradient(0, 0, 0, H); grd.addColorStop(0, bg[0]); grd.addColorStop(1, bg[1]); g.fillStyle = grd; g.fillRect(0, 0, W, H);
      var cast = (world.systems || {}).characters || {}, shown = K.shown || [];
      shown.forEach(function (id, i) { var c = cast[id] || {}, x = W * (i + 1) / (shown.length + 1), y = H * 0.62, sc = G.safety.isMinor(id) ? 0.78 : 1; g.fillStyle = c.col || '#c9a0dc'; g.beginPath(); g.arc(x, y - 150 * sc, 46 * sc, 0, 6.283); g.fill(); g.beginPath(); g.ellipse(x, y + 20, 80 * sc, 140 * sc, 0, Math.PI, 0); g.fill(); label(c.name || id, x, y + 40, '#fff'); });
      if (!vnEl) { vnEl = 1; }
    }
    // ------------------------------------------------------------ loop
    var last = 0, acc = 0, raf = 0, stopped = false;
    function frame(t) {
      if (stopped) return;
      raf = G.root.requestAnimationFrame(frame);
      var dt = last ? Math.min(0.1, (t - last) / 1000) : 1 / 60; last = t;
      if (view === 'top') { mouse.wx = camX + (mouse.x - W / 2) / scale; mouse.wz = camZ + (mouse.y - H / 2) / scale; if (mouse.used && rt.player && rt.player.comps.ctrl && rt.player.comps.ctrl.type === 'top_down') rt.input.aim = { x: mouse.wx, z: mouse.wz }; }
      if (mouse.down && rt.sysdata.genre === 'shooter') attack();
      if (!paused) { acc += dt; var n = 0; while (acc >= 1 / 60 && n++ < 5) { rt.step(1 / 60); acc -= 1 / 60; } }
      fx = fx.filter(function (f) { f.t -= dt; if (!f.tracer) { f.x += f.vx * dt; f.z += f.vz * dt; } return f.t > 0; });
      popups = popups.filter(function (p) { p.t -= dt; return p.t > 0; });
      var near = interactables(); rt.sys.ui.prompt = near.length && !(rt.sys.dialogue && rt.sys.dialogue.busy()) ? 'E: ' + (near[0].kind === 'npc' ? 'talk to ' + near[0].name : near[0].spec.resource ? 'gather' : 'use ' + near[0].name) : '';
      if (view === 'side') drawSide(); else if (view === 'fp') drawFp(); else if (view === 'grid') drawGrid(); else if (view === 'vn') drawVn(); else drawTop();
      var cam = rt.camera;
      if (cam.letterbox) { g.fillStyle = '#000'; g.fillRect(0, 0, W, H * 0.11 * cam.letterbox); g.fillRect(0, H - H * 0.11 * cam.letterbox, W, H * 0.11 * cam.letterbox); }
      if (cam.subtitle) label(cam.subtitle, W / 2, H - H * 0.06, '#fff');
      if (cam.fade) { g.fillStyle = 'rgba(0,0,0,' + cam.fade + ')'; g.fillRect(0, 0, W, H); }
      if (rt.sysdata.genre === 'strategy' && rt.sys.genres.kit.defs) { var defs = rt.sys.genres.kit.defs; g.font = '13px system-ui'; g.textAlign = 'left'; Object.keys(defs).forEach(function (k, i) { var d = defs[k], cost = Object.keys(d.cost || {}).map(function (r) { return d.cost[r] + ' ' + r; }).join(' '); g.fillStyle = stratSel === k ? '#ffd166' : 'rgba(255,255,255,.85)'; g.fillText((i + 1) + ': ' + (d.name || k) + ' (' + cost + ')', 12, H - 16 - (Object.keys(defs).length - 1 - i) * 18); }); }
    }
    raf = G.root.requestAnimationFrame(frame);
    cv.focus();
    if (rt.sysdata.camera && rt.sysdata.camera.intro) rt.sys.camera.play(rt.sysdata.camera.intro);
    return { rt: rt, view: view, stop: function () { stopped = true; G.root.cancelAnimationFrame(raf); rt.destroy(); } };
  };

  function blocked(container, rep) {
    var d = document.createElement('div');
    d.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:#140b0e;color:#fff;font:15px system-ui;padding:24px;text-align:left;z-index:10';
    var box = document.createElement('div'); box.style.cssText = 'max-width:720px';
    var h = document.createElement('h2'); h.textContent = 'This game cannot run'; h.style.margin = '0 0 10px';
    var p = document.createElement('p'); p.textContent = rep.message || 'It breaks the Stark World Engine child-safety rules.';
    box.appendChild(h); box.appendChild(p);
    ((rep.report && rep.report.findings) || []).filter(function (f) { return f.severity === 'block'; }).slice(0, 8).forEach(function (f) { var li = document.createElement('div'); li.style.cssText = 'margin:6px 0;opacity:.9'; li.textContent = '- ' + f.where + ': ' + f.title; box.appendChild(li); });
    d.appendChild(box); container.appendChild(d);
  }
})(GSYS);
