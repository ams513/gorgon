/* Stark World Engine Systems: boot + integration with the studio's existing engine (GS in studio_app.py ENGINE_JS).
   GSYS.install(GS)       wraps GS.create: every world is child-safety checked and sanitised before the legacy
                          engine plays it (ages locked onto objects, child/teen enemies become friendly villagers);
                          a blocked world shows the reason instead of starting.
   GSYS.canHarm(obj)      the one-line guard for the legacy damage paths (3D damage(), 2D hitE()).
   GSYS.cartridgeBoot(W)  -> true when the cartridge may start; false after drawing the "cannot run" screen.
   GSYS.wantsSystems(W)   -> true when a world asks for the Stark World Engine Systems player (systems.player === 'gsys', or a
                          genre other than the studio's own action-adventure / platformer). */
(function (G) {
  'use strict';
  var S = G.safety;
  G.canHarm = function (o) { try { return S.canHarm(o && o.o ? o.o : o); } catch (e) { return false; } };
  G.wantsSystems = function (w) { var sy = w && w.systems; if (!sy || typeof sy !== 'object') return false; if (sy.player === 'legacy') return false; return sy.player === 'gsys' || (!!sy.genre && sy.genre !== 'custom'); };
  G.install = function (GS) {
    if (!GS || GS.__gsysInstalled) return GS;
    var create = GS.create;
    GS.create = function (canvas, world, opt) {
      opt = opt || {};
      var play = opt.mode === 'play' || !opt.mode;
      // edit mode: check only (the editor's world must stay editable); play mode: play a sanitised copy
      var w = play ? JSON.parse(JSON.stringify(world)) : world;
      var rep = play ? S.sanitize(w) : S.boot(w);
      if (!rep.ok) {
        if (opt.onHud) try { opt.onHud({ error: rep.message }); } catch (e) { /* ignore */ }
        if (play) throw new S.SafetyError(rep.message, rep.report);
      }
      return create.call(GS, canvas, w, opt);
    };
    GS.__gsysInstalled = true;
    GS.safety = S;
    return GS;
  };
  G.cartridgeBoot = function (world) {
    var rep = S.sanitize(world);
    if (rep.ok) return true;
    if (typeof document !== 'undefined') {
      var d = document.createElement('div');
      d.style.cssText = 'position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:#140b0e;color:#fff;font:15px system-ui;padding:24px;z-index:99';
      d.textContent = rep.message;
      (document.body || document.documentElement).appendChild(d);
    }
    return false;
  };
  if (G.root.GS && G.root.GS.create) G.install(G.root.GS);
})(GSYS);
