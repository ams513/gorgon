# stark_anim: integration patches

These are the hooks the animation and motion-capture department needs in files other departments own.

- **None of these are applied.** Each patch is minimal and guarded: if `stark_anim/` is missing, everything behaves as it does today.
- **Patches 1-3 were verified on a scratch copy of `studio_app.py` (2026-11-19).** The real file was untouched. On the copy:
  - the studio routes answered over HTTP;
  - the editor page loaded with `StarkAnim` present and **zero uncaught JS errors**;
  - the mocap page loaded the local MediaPipe files and drew the character.
- **Anchors are text, not line numbers.** `studio_app.py` is changing under the v28.5 agent, so each patch quotes the exact text to find.

| # | File (owner) | What it adds |
|---|---|---|
| 1 | `studio_app.py` (v28.5) | `/anim/*` + `/api/anim/*` routes (mocap page, library, takes, import, export, previews) |
| 2 | `studio_app.py` (v28.5) | runtime spliced into ENGINE_JS (editor + every cartridge) |
| 3 | `studio_app.py` ENGINE_JS (v28.5) | characters use `StarkAnim.R2Actor` (drop-in for `R2.Actor`) |
| 4 | systems / audio boot (systems, audio) | animation events -> systems bus; audio visemes -> mouths |
| 5 | studio UI (v28.5) | "Animation" tab: record motion, library browser |
| 6 | `cartridge_html` (v28.5) | embeds the creator's own clips (`world.anim_clips`) |
| 7 | `build_release.py` (build) | ships `stark_anim/` (runtime, tools, models + licences) |
| 8 | product CREDITS (assets / build) | MediaPipe (Apache-2.0) + Quaternius-derived data (CC0) |
| 9 | `.gitignore` (lead) | regenerable previews |
| 10 | `doctor.py` (doctor) | optional "mocap models installed" probe |
| 11 | bridge exporters (bridge) | Godot AnimationLibrary + UE5 package API |
| 12 | `stark_systems/CHILD_SAFETY.md` (systems) | animation rule text + optional keyword block |

---

## 1. Studio routes (`studio_app.py`, `Handler`)

**1a. `do_GET`.** Find this text (the end of the `/files/` branch, just before do_GET's final 404):

```python
                with open(p, 'rb') as f:
                    return self._send(200, f.read(), ctype, extra)
            return self._send(404, {'error': 'not found'})
```

Insert these lines just before `return self._send(404, ...)`:

```python
            if u.path.startswith(('/anim/', '/api/anim/')):   # stark_anim: runtime, webcam mocap, library
                from stark_anim import studio_api as _anim
                r = _anim.handle_get(u.path, q, self.games)
                if r is not None:
                    return self._send(*r)
```

**1b. `do_POST`.** Find the end of the `/api/activate` branch:

```python
                return self._send(200, dict(res, ok=True) if isinstance(res, dict) else {'ok': True, 'result': res})
            return self._send(404, {'error': 'not found'})
```

Insert these lines before the 404:

```python
            if u.path.startswith('/api/anim/'):              # stark_anim: takes, imports, exports, previews
                from stark_anim import studio_api as _anim
                return self._send(*_anim.handle_post(u.path, body, self.games))
```

**How the handlers behave.** `handle_get` and `handle_post` return `(status, body, content_type, extra_headers)`, which is exactly `Handler._send`'s signature.

- **Static files.** They are served only from an allow-list of stark_anim folders: `runtime`, `webcam`, `preview`, `models`, `library`, `data`, `previews`, `fixtures`. Known file types only, never `.py`, and `..` or drive paths get a 404.
- **CSP for HTML pages.** These pages get the studio's own CSP plus `'wasm-unsafe-eval'`, because MediaPipe compiles WebAssembly. Nothing else is relaxed.
- **Where user files go.** Only into `My_Games/_animations/{takes,clips,exports,previews}`.
- **Takes.** These are landmark numbers only, refused if they contain image or video data, and capped at 2 minutes. That cap fits comfortably under `MAX_BODY` (12 MB).
- **Routes.**

| Method | Route | Body / query | Returns |
|---|---|---|---|
| GET | `/anim/<file>` | - | runtime JS, webcam pages, models, library clips |
| GET | `/api/anim/status` | - | models installed (body/face/runtime), ffmpeg, Chrome, FBX converter, library size |
| GET | `/api/anim/library` | `?q=walk&category=locomotion&child=1` | built-in + the creator's clips (`child=1` hides harm/intimacy) |
| GET | `/api/anim/clip` | `?id=lib:quaternius_ual/Walk_Loop` or `mine:<name>` | one `.gclip.json` |
| POST | `/api/anim/take` | a `stark-mocap-take` | solved and cleaned clip saved, `{clip, file, report, summary}` |
| POST | `/api/anim/import` | `{name, ext: bvh\|glb\|gltf\|fbx, b64}` | retargeted, cleaned clips |
| POST | `/api/anim/export` | `{ids, target: godot\|ue5, name}` | written files |
| POST | `/api/anim/preview` | `{id}` | GIF + MP4 (headless Chrome) |

## 2. Runtime in the engine (`studio_app.py`, module level)

Find:

```python
ENGINE_JS = ENGINE_JS.replace('/*%%ENGINE3D%%*/', _engine3d_js())
```

Replace it with:

```python
def _stark_anim_js():
    """stark_anim/runtime/stark_anim.js: character animation runtime (state machines, IK, events, lip-sync, child
    safety). Missing file = engine3d's R2.Actor keeps working alone."""
    try:
        with open(os.path.join(APP_DIR, 'stark_anim', 'runtime', 'stark_anim.js'), encoding='utf-8') as fh:
            return fh.read()
    except OSError:
        return ''


ENGINE_JS = ENGINE_JS.replace('/*%%ENGINE3D%%*/', _engine3d_js() + chr(10) + _stark_anim_js())
```

The runtime is a self-contained UMD file of about 75 KB. It sets `window.StarkAnim`, makes no network calls, and is safe to drop into a `'use strict'` module.

## 3. Characters use the runtime (`ENGINE_JS`, `function actor(...)`)

Find, inside `function actor(ent,id,first){...}`:

```js
ent._act=R2.Actor(m.G,m.clips);
```

Replace it with:

```js
ent._act=window.StarkAnim?StarkAnim.R2Actor(m.G,{entity:ent.o||ent,library:R2.HumanClips.src,clips:world.anim_clips||[]}):R2.Actor(m.G,m.clips);
```

`StarkAnim.R2Actor` has the same API as `R2.Actor`: `play(name, {fade, once, speed, restart, at, onEnd})`, `update(dt)`, `pose()`, `joints[0]`, `nj`, `cur.name`, `done`, `has(name)` and `bone(name)`. So `hero2`, `npc2`, `enemy2` and `R.drawSkinned(a.G, mat, a.joints, a.nj, ...)` keep working unchanged. Tested in `tests/test_stark_anim_runtime.py::test_engine_actor_dropin`.

What every character gains with that one line:

- **Footstep and hit events** (auto-detected for glTF clips that have none), with world positions.
- **Child safety.** A minor's actor refuses harm and intimacy clips: `play()` returns false, so the call site keeps the previous animation.
- **Lip-sync** from stark_audio visemes.
- **Root motion**, available on `a.anim.rootMotion`.
- **Optional foot IK** on terrain.

Optional extras, one line each, in the per-character call sites:

```js
if(a.setWorld)a.setWorld([P.x,P.y,P.z],P.a,1,gh);   // before a.update(dt): world-space event positions + foot IK ground
```

Pass `footIK:true` in the `R2Actor` options to plant feet on slopes (the hips drop to the lower foot; legs bend with two-bone IK).

## 4. Events and lip-sync (cartridge boot; systems and audio owners)

`StarkAnim` binds by itself to `GSYS.safety` (minor = child or teen, ages locked by the systems runtime) and to `StarkAudio` when they are loaded before it. Wherever the systems runtime instance `rt` is created:

```js
if (window.StarkAnim) {
  StarkAnim.setEventSink(function (e) { rt.bus.emit('anim.' + e.type, e); });   // footstep, hit, state_enter, clip_end, viseme, speech_end, safety_block
  if (window.GSYS && GSYS.safety) StarkAnim.useSafety(GSYS.safety);
  if (window.StarkAudio) StarkAnim.connectAudio(StarkAudio);                      // 'viseme' events -> speaker's mouth
}
```

**Event payloads.** Each event carries `{type, clip, state, layer, time, character, t_world}`.

- `footstep` adds `foot` ('l' or 'r') and `position` (world [x, y, z]).
- `hit` adds `limb` and `position`.
- `viseme` adds `viseme` and `jaw`.

**Speakers.** stark_audio's `speaker` field is the entity id. An animator created with `{entity}` registers under `entity.id`; or pass `speaker: 'id'`.

**Visemes.** The runtime reads stark_audio's compact tracks (`lipsync.compact()`: `{d, v, o}`) and its live `viseme` events (ARKit weights go straight onto morph targets). It also reads Rhubarb `mouthCues`, Azure viseme IDs, ARPAbet phonemes and dense `stark-visemes`.

**Characters without a face rig.** The Quaternius characters have none, so they get a subtle speech nod, and a `viseme` event drives any 2D mouth.

## 5. Studio UI: "Animation" tab (v28.5)

Find, in the `<div class="tabs">` bar:

```html
<button data-tab="tExport" data-t="export"></button></div>
```

Replace it with:

```html
<button data-tab="tAnim">Animation</button><button data-tab="tExport" data-t="export"></button></div>
```

Add the section after `<section id="tAssets" hidden></section>`:

```html
<section id="tAnim" hidden><div class="row"><button id="animMocap" title="Record yourself with the webcam - nothing leaves this PC">Record motion</button><button id="animLib">Animation library</button></div><p class="mute">Import BVH / glTF / FBX motion, record with your webcam, then give clips to your characters.</p></section>
```

In `showTab`, add `'tAnim'` to the list `['tBuild','tWorld',...,'tExport']`. Then add:

```js
$('animMocap').onclick=function(){window.open('/anim/webcam/mocap_studio.html?character='+encodeURIComponent('/assets/models/characters/'+((W&&W.player&&W.player.body)==='female'?'female':'male')+'_adventurer.gltf'),'_blank');};
$('animLib').onclick=function(){window.open('/anim/preview/preview.html','_blank');}; // until the browser panel (LIBRARY_BROWSER_SPEC.md) lands
```

## 6. Creator clips in cartridges (`cartridge_html`, v28.5)

At the top of `cartridge_html(world)`:

```python
    try:  # the creator's own mocap / imported clips the world uses (world['anim_clips'] = ['mine:my_dance', ...])
        from stark_anim.studio_api import embed_clips
        world = dict(world, anim_clips=embed_clips(world, GAMES))
    except Exception:  # noqa: BLE001 - cartridges export without them
        pass
```

Patch 3 already passes `world.anim_clips` to every actor. Safety classes travel inside each clip's meta, and the runtime enforces them.

## 7. Release build (`build_release.py`, build)

Next to the `engine3d` loop:

```python
    skip = ('__pycache__', os.sep + 'fixtures', os.sep + 'tests_js', os.sep + 'previews')
    for dirpath, _, names in os.walk(os.path.join(ROOT, 'stark_anim')):   # animation + mocap department
        if any(s in dirpath for s in skip):
            continue
        for n in sorted(names):
            full = os.path.join(dirpath, n)
            with open(full, 'rb') as fh:
                files[os.path.relpath(full, ROOT).replace(os.sep, '/')] = fh.read()
```

**Sizes.**

- Models: about 24.7 MB, ending up in `models/`. Their `LICENSE-Apache-2.0.txt`, `NOTICE.txt` and `CREDITS.md` must ship with them (Apache-2.0 section 4).
- Library: about 1 MB (43 CC0 clips).
- Runtime plus pages: about 150 KB.

## 8. Product credits (asset library / build)

Append `stark_anim/CREDITS.md`, or at least its two blocks (MediaPipe Apache-2.0, and Quaternius CC0-derived data), to the product CREDITS and to the cartridge credits screen whenever the runtime or library clips are embedded.

## 9. `.gitignore` (lead)

```
stark_anim/previews/
```

The previews are regenerable with `python -m stark_anim preview ...`. The models are pinned by size and hash, so they can either be committed or fetched at build time with `python -m stark_anim models fetch --approved`.

## 10. Doctor (optional)

Add a probe that runs `python -m stark_anim models status`. Exit code 0 means the mocap models are installed and their hashes verify. The studio itself shows the same through `GET /api/anim/status`.

## 11. Bridge (UE5 / Godot exporters)

```python
from stark_anim.clip import Clip
from stark_anim.export_map import write_godot_library, write_ue5_package, ue5_retarget_spec, ue5_import_settings
write_godot_library([Clip.load(p) for p in paths], 'out/animations.tres', skeleton_path='Armature/Skeleton3D')
write_ue5_package(clips, 'out/ue5_anims', ue5_bake=False)
```

**Godot 4.** The `.tres` file is an AnimationLibrary with `rotation_3d` and `position_3d` bone tracks.

- Root motion is baked onto the `root` bone, so set `AnimationTree.root_motion_track = "Armature/Skeleton3D:root"`.
- A method track calls `_on_anim_event(type, data)` for footstep and hit events.
- `metadata/stark_safety` lets the addon enforce the child rule.

**UE5.** The package holds:

- one GLB per clip on the Stark World Engine skeleton, which uses UE4-Mannequin names;
- `ue5_import.json` with the AnimSequence import options (sample rate, root motion, loop, notifies from events, safety metadata);
- `ue5_retarget.json` with the IK Rig and IK Retargeter chains, which use the same names as Epic's IK_Mannequin (Root, Spine, Neck, Head, Left/RightClavicle, Arm, Leg, Thumb, Index, Middle, Ring, Pinky), plus IK goals and the bone map.

`ue5_bake=True` also writes clips baked onto a UE5-named skeleton (approximate Manny proportions) for pipelines without an IK Retargeter.

## 12. Child safety (systems)

**What the animation side enforces.**

- **Runtime.** An animator or actor bound to an entity checks `GSYS.safety.isMinor(entity)`. Without the systems runtime it reads the entity's own `age` or `child` fields, where an age under 18 or `child`/`teen` counts as a minor. Once true, `child` can never be switched off.
- **What a minor's animator refuses.** It never plays a clip whose safety class is `harm` (attacks, weapons, being hit, dying, fighting) or `intimacy`:
  - state-machine states using such clips are stripped;
  - `play()`, `playUpper()` and `R2Actor.play()` refuse;
  - a `safety_block` event is emitted.
- **Python.** `stark_anim.child_safety.check_apply(clip, character)` raises; `filter_for` and `/api/anim/library?child=1` hide the clips; `is_child` follows `stark_systems.child_safety.normalize_age` (child + teen).
- **Classifier.** The same word rules run in Python and JS, and a test keeps them identical. A declared class can only be raised, never lowered.

**Ask for `CHILD_SAFETY.md`.** When it is written, please add one line: "Animation: harm and intimacy clips are never applied to child or teen characters (stark_anim)". Optionally add a machine-readable block:

````
```child-safety-anim
{"block_keywords": ["extra", "words"]}
```
````

stark_anim reads that block to block more words; it can never unblock anything.
