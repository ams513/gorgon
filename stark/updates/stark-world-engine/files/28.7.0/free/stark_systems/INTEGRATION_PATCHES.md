# Stark World Engine Systems: integration patches

Exact, minimal patches that wire the child-safety gate and the Stark World Engine Systems runtime into files owned by other
agents. Fixes QA-016 (export never calls `enforce_export`) and QA-017 (child enemies attackable in play mode).

* Each patch is a `SEARCH` / `REPLACE` block. `SEARCH` text is copied from `studio_app.py` as of 2026-11-19 (v28.5 work
  in progress) and must occur exactly once. Apply them in order.
* `tests/test_stark_systems_integration.py` applies every `studio_app.py` block to a **temporary copy**, imports it,
  and proves the fixes: a blocked world raises on cartridge / Godot / UE5 export, a clean world exports, the cartridge
  and studio page carry the runtime and parse under `node --check`, and child enemies stay unharmed in play.
  If an anchor goes stale it prints which one, and the test is skipped (not failed).
* Everything fails **closed**: if `stark_systems/` is missing, export refuses with a clear message and the play
  runtime refuses to start play mode.

Owners: P1-P9 `studio_app.py` (v28.5 agent), P10 `build_release.py` (v28.5 agent), P11 `ue5_export.py` /
`godot_export.py` (bridge agent), P12 `qa_gate.py` (lead).

---

### P1 studio_app.py: gate + runtime helpers (place before `export_world`)

<<<<<<< SEARCH
def export_world(name, target, tier=None, games=None):
=======
def _gs_gate(world):
    """Child-safety export gate (stark_systems). Fails closed: no checker, no export."""
    try:
        if APP_DIR not in sys.path:
            sys.path.insert(0, APP_DIR)
        from stark_systems import integration as _gsi
    except Exception as e:  # noqa: BLE001
        raise ValueError('Export needs the child-safety checker (the stark_systems folder is missing or damaged). '
                         'Reinstall Stark World Engine.') from e
    return _gsi.gate(world)


_GS_STUB = ("if(window.GS){var _c=GS.create;GS.create=function(cv,w,o){if(!o||o.mode!=='edit')throw new Error("
            "'Play needs the Stark World Engine child-safety runtime (stark_systems folder missing). Reinstall Stark World Engine.');"
            "return _c.apply(GS,arguments);};}")


def _gs_runtime():
    """The Stark World Engine Systems runtime JS (child-safety guards + systems). Missing -> a stub that refuses to play."""
    try:
        if APP_DIR not in sys.path:
            sys.path.insert(0, APP_DIR)
        from stark_systems import integration as _gsi
        return _gsi.runtime_js()
    except Exception as e:  # noqa: BLE001
        print('[studio] stark_systems runtime unavailable:', e.__class__.__name__, file=sys.stderr)
        return _GS_STUB


def _gs_tag(world):
    """Make-me-a-game: tag generated child characters 'child' (no-op when stark_systems is missing)."""
    try:
        if APP_DIR not in sys.path:
            sys.path.insert(0, APP_DIR)
        from stark_systems import integration as _gsi
        return _gsi.tag_generated(world)
    except Exception:  # noqa: BLE001
        return world


def export_world(name, target, tier=None, games=None):
>>>>>>> REPLACE

### P2 studio_app.py: export_world runs the gate (cartridge, Godot and UE5 all use the gated world)

<<<<<<< SEARCH
    world = validate_world(load_world(name, games))
    base = f'{slug(world["name"])}/export'
=======
    world = _gs_gate(validate_world(load_world(name, games)))     # child safety: blocks or returns the tagged world
    base = f'{slug(world["name"])}/export'
>>>>>>> REPLACE

### P3 studio_app.py: "Open in Unreal" route runs the gate too

<<<<<<< SEARCH
                w = validate_world(load_world(body.get('name'), self.games))
=======
                w = _gs_gate(validate_world(load_world(body.get('name'), self.games)))
>>>>>>> REPLACE

### P4 studio_app.py: cartridge_html inlines the runtime

<<<<<<< SEARCH
            .replace('%%CREDITS%%', safe(json.dumps(credits))).replace('%%ENGINE%%', ENGINE_JS).replace('%%WORLD%%', data))
=======
            .replace('%%CREDITS%%', safe(json.dumps(credits))).replace('%%ENGINE%%', ENGINE_JS)
            .replace('%%GSYS%%', _gs_runtime()).replace('%%WORLD%%', data))
>>>>>>> REPLACE

### P5 studio_app.py: CARTRIDGE_HTML loads the runtime right after the engine (it wraps GS.create)

<<<<<<< SEARCH
<script>%%ENGINE%%</script>
<script>
if(GS.R2)GS.R2.setSource({embedded:window.GS_EMBED});
=======
<script>%%ENGINE%%</script>
<script>%%GSYS%%</script>
<script>
if(GS.R2)GS.R2.setSource({embedded:window.GS_EMBED});
>>>>>>> REPLACE

### P6 studio_app.py: the studio page (Play mode in the editor) loads the runtime

<<<<<<< SEARCH
<script>%%ENGINE%%</script>
<script>
window.addEventListener('error',function(e){document.body.dataset.err=String(e.message).slice(0,200);});
=======
<script>%%ENGINE%%</script>
<script>%%GSYS%%</script>
<script>
window.addEventListener('error',function(e){document.body.dataset.err=String(e.message).slice(0,200);});
>>>>>>> REPLACE

<<<<<<< SEARCH
    return APP_HTML.replace('%%ENGINE%%', ENGINE_JS).replace('%%I18N%%', json.dumps(I18N, ensure_ascii=False))
=======
    return (APP_HTML.replace('%%ENGINE%%', ENGINE_JS).replace('%%GSYS%%', _gs_runtime())
            .replace('%%I18N%%', json.dumps(I18N, ensure_ascii=False)))
>>>>>>> REPLACE

### P7 studio_app.py: engine damage paths skip child / teen characters (defence in depth; GSYS.install already
turns child-tagged enemies into friendly villagers before play)

<<<<<<< SEARCH
 function damage(e,dmg,kx,kz){if(e.dead)return;
=======
 function damage(e,dmg,kx,kz){if(e.dead)return;if(window.GSYS&&!GSYS.canHarm(e.o||e))return;
>>>>>>> REPLACE

<<<<<<< SEARCH
 function hitE(e,dmg){e.hp-=dmg;
=======
 function hitE(e,dmg){if(window.GSYS&&!GSYS.canHarm(e.o||e))return;e.hp-=dmg;
>>>>>>> REPLACE

### P8 studio_app.py: Make-me-a-game tags generated children

<<<<<<< SEARCH
    if n > 1:
        w['levels'] = levels[1:]
    return validate_world(w)
=======
    if n > 1:
        w['levels'] = levels[1:]
    return validate_world(_gs_tag(w))
>>>>>>> REPLACE

### P9 studio_app.py: GET-free Safety route for the UI (full report for the current, possibly unsaved, world)

<<<<<<< SEARCH
            if u.path == '/api/export':
=======
            if u.path == '/api/safety':          # child-safety report for a Safety panel: {blocked, findings, ...}
                if self.app_dir not in sys.path:
                    sys.path.insert(0, self.app_dir)
                from stark_systems import integration as _gsi
                return self._send(200, _gsi.report(validate_world(body.get('world'))))
            if u.path == '/api/export':
>>>>>>> REPLACE

### P9b studio_app.py: licence clause (wording proposal: stark_systems/TOS_CHILD_SAFETY_CLAUSE.md)

<<<<<<< SEARCH
5. Stark World Engine runs offline. It does not call any paid cloud service and needs no account.
=======
5. Stark World Engine runs offline. It does not call any paid cloud service and needs no account.
6. You may not use Stark World Engine to create content that sexualises minors or depicts the abuse of minors. Doing so
   terminates this licence immediately. The studio refuses to export such content and exported games refuse to run it.
>>>>>>> REPLACE

---

### P10 build_release.py: ship stark_systems/ in every tier (without it export refuses, by design)

In `tier_files()`, directly after the `EVERY_TIER` loop:

<<<<<<< SEARCH
    for name in EVERY_TIER:
        with open(os.path.join(ROOT, name), 'rb') as f:
            files[name] = f.read()
=======
    for name in EVERY_TIER:
        with open(os.path.join(ROOT, name), 'rb') as f:
            files[name] = f.read()
    gsd = os.path.join(ROOT, 'stark_systems')  # child-safety gate + systems runtime: export refuses without it
    for p in sorted(glob.glob(os.path.join(gsd, '**', '*'), recursive=True)):
        rel = os.path.relpath(p, ROOT).replace(os.sep, '/')
        if os.path.isfile(p) and '__pycache__' not in rel and not rel.endswith('.pyc') and '/samples/' not in rel:
            with open(p, 'rb') as f:
                files[rel] = f.read()
>>>>>>> REPLACE

Also add `tests/test_stark_systems_*.py` to the release test list if the zips carry tests.

### P11 ue5_export.py / godot_export.py (bridge agent): gate at the entry points too

`export_world` already passes the gated world (P2), but the exporters are also called directly (CLI, tests,
`open_unreal_action`). At the top of `ue5_project(world)` and `godot_project(world)` (and `open_unreal_action`):

```python
    from stark_systems import integration as _gsi   # fails closed: ImportError means no export
    world = _gsi.gate(world)
```

Then map `world['child_safety']['minors']` and every character's `age` into the engine (spec: CHILD_SAFETY.md,
"Bridge mirror"): minors get no damage component / are excluded from targeting, romance and capture.

### P12 qa_gate.py (lead): the runtime parses

In `c_js()`, add `'gsys': '<script>' + __import__('stark_systems.integration', fromlist=['x']).runtime_js() + '</script>'`
to `sources` (or rely on P4/P5: the cartridge and studio sources then already contain it).
