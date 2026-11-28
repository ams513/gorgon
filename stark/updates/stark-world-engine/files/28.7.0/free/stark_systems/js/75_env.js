/* Stark World Engine Systems: environment (clock, day/night, weather) and what it does to gameplay. Every system asks
   env.mod(name) for a multiplier: move_speed, vision (AI sight), hearing, grip (vehicles), fire_damage,
   stamina_drain, warmth (survival), spawn_rate, light. Modifiers multiply: night x rain x fog ...
   systems.environment = {day_length: seconds per day (0 = frozen), start_hour, weather: {start, pattern: [{type,
     w, min, max}], change_every: [min, max] seconds}, effects: {night: {...}, rain: {...}, storm, snow, fog, clear}}
   Matches the studio's world.day {start 0..1, len} and world.weather when systems.environment is absent. */
(function (G) {
  'use strict';
  var U = G.util;
  var EFFECTS = {
    night: { vision: 0.55, light: 0.35, warmth: 0.8, spawn_rate: 1.5 },
    rain: { move_speed: 0.95, vision: 0.85, grip: 0.7, fire_damage: 0.5, hearing: 0.8, warmth: 0.9 },
    storm: { move_speed: 0.9, vision: 0.65, grip: 0.55, fire_damage: 0.4, hearing: 0.6, warmth: 0.8 },
    snow: { move_speed: 0.85, vision: 0.8, grip: 0.5, warmth: 0.6, stamina_drain: 1.3 },
    fog: { vision: 0.45, hearing: 1.1 },
    embers: { fire_damage: 1.5, warmth: 1.3, vision: 0.9 },
    clear: {}, auto: {}, pollen: {}, bubbles: { move_speed: 0.9 }, fireflies: {}
  };
  G.WEATHER_EFFECTS = EFFECTS;
  G.register('env', 15, function (rt) {
    var w = rt.world, cfg = rt.sysdata.environment || {}, E = {};
    var dayLen = cfg.day_length != null ? +cfg.day_length : (w.day && w.day.len != null ? +w.day.len : 600);
    var hour0 = cfg.start_hour != null ? +cfg.start_hour : (w.day && w.day.start != null ? +w.day.start * 24 : 9);
    var effects = {}, k; for (k in EFFECTS) effects[k] = EFFECTS[k]; for (k in (cfg.effects || {})) effects[k] = Object.assign({}, effects[k] || {}, cfg.effects[k]);
    var wcfg = cfg.weather || {}, pattern = wcfg.pattern || null;
    var t = 0, weather = { type: wcfg.start || (w.weather && w.weather !== 'auto' ? w.weather : 'clear'), intensity: 1, next: 0 }, lastHour = -1, dawnDusk = null;
    E.effects = effects;
    E.hour = function () { return dayLen > 0 ? (hour0 + t / dayLen * 24) % 24 : hour0 % 24; };
    E.day = function () { return dayLen > 0 ? Math.floor((hour0 + t / dayLen * 24) / 24) : 0; };
    E.totalHours = function () { return dayLen > 0 ? hour0 + t / dayLen * 24 : hour0; };
    E.isNight = function () { var h = E.hour(); return h < 6 || h >= 20; };
    E.daylight = function () { var h = E.hour(); return U.clamp(Math.sin((h - 6) / 12 * Math.PI) * 1.4, 0, 1); };
    E.weather = function () { return weather; };
    E.setWeather = function (type, intensity) { if (!effects[type]) effects[type] = {}; weather.type = type; weather.intensity = intensity != null ? +intensity : 1; weather.next = t + 60; rt.bus.emit('weather', { type: type, intensity: weather.intensity }); };
    E.setHour = function (h) { hour0 = ((+h % 24) + 24) % 24 - (dayLen > 0 ? t / dayLen * 24 : 0); };
    /** Gameplay multiplier for `name` right now (1 = no effect). */
    E.mod = function (name) {
      var v = 1, fx;
      if (E.isNight() && effects.night && effects.night[name] != null) { var dl = E.daylight(); v *= U.lerp(effects.night[name], 1, dl); }
      fx = effects[weather.type]; if (fx && fx[name] != null) v *= U.lerp(1, fx[name], U.clamp(weather.intensity, 0, 1));
      var p = rt.player; if (p && name === 'vision' && p.flags.lightSource) v = Math.max(v, 0.8);
      return v;
    };
    E.update = function (dt) {
      t += dt;
      var h = Math.floor(E.hour());
      if (h !== lastHour) { lastHour = h; rt.bus.emit('hour', { hour: h, day: E.day() }); var nd = E.isNight(); if (dawnDusk !== null && nd !== dawnDusk) rt.bus.emit(nd ? 'dusk' : 'dawn', { day: E.day() }); dawnDusk = nd; }
      if (pattern && t >= weather.next) {
        var pick = rt.rng.weighted(pattern), ce = wcfg.change_every || [90, 240];
        weather.next = t + ce[0] + rt.rng() * (ce[1] - ce[0]);
        if (pick && pick.type !== weather.type) E.setWeather(pick.type, pick.min != null ? pick.min + rt.rng() * ((pick.max || 1) - pick.min) : 1);
        weather.next = t + ce[0] + rt.rng() * (ce[1] - ce[0]);
      }
    };
    E.save = function () { return { t: t, hour0: hour0, weather: U.copy(weather) }; };
    E.load = function (d) { if (!d) return; t = +d.t || 0; hour0 = d.hour0 != null ? +d.hour0 : hour0; if (d.weather) weather = d.weather; };
    return E;
  });

  /* Save / load: every system's save() blob + entity state, versioned. Ages are never saved or loaded: they come
     from the world and stay locked, so a save file can never turn a child character into an adult. */
  G.register('save', 80, function (rt) {
    var cfg = rt.sysdata.save || {}, SV = {}, slotsN = +cfg.slots || 3, mem = {};
    var key = 'gsys_save_' + (U.hash((rt.world.name || 'game') + '|' + (rt.world.seed || 0)) >>> 0).toString(36) + '_';
    SV.snapshot = function () {
      var ents = [];
      rt.ents.forEach(function (e) { ents.push({ id: e.id, kind: e.kind, name: e.name, pos: U.copy(e.pos), rot: e.rot, hp: e.hp, maxHp: e.maxHp, dead: e.dead, team: S().isMinor(e) ? 'neutral' : e.team, flags: U.copy(e.flags), tags: Array.from(e.tags), spawned: !!e.flags._runtime, spec: e.flags._runtime ? U.copy(e.spec) : undefined }); });
      var sys = {};
      rt.order.forEach(function (n) { var s = rt.sys[n]; if (s.save && n !== 'save') { try { sys[n] = s.save(); } catch (e) { G.warn('save ' + n + ': ' + e.message); } } });
      return { format: 'gsys-save', version: 1, world: rt.world.name, time: rt.time, level: rt.level || 0, rng: rt.rng.state(), entities: ents, systems: sys, at: Date.now ? Date.now() : 0 };
    };
    function S() { return G.safety; }
    SV.restore = function (snap) {
      if (!snap || snap.format !== 'gsys-save') throw new Error('not a Stark World Engine save');
      rt.time = +snap.time || 0; rt.rng.state(snap.rng);
      var keep = {};
      (snap.entities || []).forEach(function (d) {
        var e = rt.get(d.id);
        if (!e && d.spawned && d.spec) { d.spec.id = d.id; e = rt.spawn(d.spec); e.flags._runtime = true; }
        if (!e) return;
        keep[e.id] = 1;
        e.pos = U.copy(d.pos); e.rot = d.rot; e.maxHp = d.maxHp; e.hp = d.hp; e.dead = !!d.dead; e.flags = U.copy(d.flags) || {};
        e.team = S().isMinor(e) ? 'neutral' : d.team;           // ages are never restored from a save
        if (S().isMinor(e)) { e.dead = false; e.hp = Math.max(1, e.hp); }
      });
      rt.ents.forEach(function (e) { if (!keep[e.id] && e.flags._runtime) rt.remove(e); });
      rt.order.forEach(function (n) { var s = rt.sys[n]; if (s.load && snap.systems && snap.systems[n] !== undefined) { try { s.load(snap.systems[n]); } catch (e) { G.warn('load ' + n + ': ' + e.message); } } });
      rt.bus.emit('loaded', { snap: snap });
      return true;
    };
    function store() { try { return G.root.localStorage || null; } catch (e) { return null; } }
    SV.save = function (slot) { slot = slot || 'auto'; var s = JSON.stringify(SV.snapshot()), st = store(); mem[slot] = s; if (st) try { st.setItem(key + slot, s); } catch (e) { G.warn('save: storage full or blocked'); } rt.bus.emit('saved', { slot: slot }); return s.length; };
    SV.load = function (slot) { slot = slot || 'auto'; var st = store(), s = null; if (st) try { s = st.getItem(key + slot); } catch (e) { s = null; } s = s || mem[slot]; if (!s) return false; return SV.restore(JSON.parse(s)); };
    SV.slots = function () { var out = [], st = store(); for (var i = 1; i <= slotsN; i++) { var s = null; if (st) try { s = st.getItem(key + i); } catch (e) { s = null; } s = s || mem[i]; out.push({ slot: i, used: !!s, at: s ? (JSON.parse(s).at || 0) : 0 }); } return out; };
    SV.exportString = function () { return JSON.stringify(SV.snapshot()); };
    SV.importString = function (s) { return SV.restore(JSON.parse(s)); };
    var autoT = 0;
    SV.update = function (dt) { if (cfg.autosave_s) { autoT += dt; if (autoT >= +cfg.autosave_s) { autoT = 0; SV.save('auto'); } } };
    rt.bus.on('spawn', function (d) { if (d.entity && rt.frame > 0) d.entity.flags._runtime = true; });
    return SV;
  });
})(GSYS);
