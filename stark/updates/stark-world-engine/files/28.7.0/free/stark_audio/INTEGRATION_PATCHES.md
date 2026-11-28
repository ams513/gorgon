# stark_audio integration patches

Exact, minimal patches for files the Audio department does not own. Each one is optional and guarded: if
`stark_audio/` is missing, everything behaves exactly as today. Anchors are quoted code (line numbers move while
other agents edit). Audio never writes `world.json`: its own data lives in `My_Games/<game>/audio/` and
`My_Games/<game>/voice/`, so the studio's autosave can never clobber it.

What works today **without any patch**:

- `python -m stark_audio booth` starts the voice booth on 127.0.0.1 (ports 8531-8540).
- `python -m stark_audio build My_Games/<game> --cartridge out.html` writes a cartridge with full audio.
- Any cartridge containing the pack boots the runtime, which attaches to the current engine on its own
  (`StarkAudio.attachLegacy`). It patches `GS.Game.prototype` methods and the engine's sound-key lookups, so no
  engine edit is needed.

---

## 1. studio_app.py: cartridge export carries the audio pack (owner: v28.5 agent)

In `export_world()`, `if target == 'cartridge':` branch, replace

```python
        write_versioned(rel, cartridge_html(world, badge=free).encode('utf-8'), games)
```

with

```python
        html = cartridge_html(world, badge=free)
        try:
            from stark_audio import studio_api as _ga          # adaptive music, SFX, ambience, voices (offline)
            html = _ga.cartridge_html(html, world, games or GAMES)
        except ImportError:
            pass
        write_versioned(rel, html.encode('utf-8'), games)
```

`_ga.cartridge_html` never raises: any audio problem logs one line and returns the plain cartridge.
The first export of a world renders its score and sounds (about 1-3 minutes). The renders are cached in
`_music_out/ga_cache/` and the pack in `My_Games/<game>/audio/pack/`, so later exports take a few seconds.
The pack is rebuilt only when the world's moods, enemies, houses, quests, dialogue text or voice takes change.

## 2. studio_app.py: routes for the voice booth and the audio build (owner: v28.5 agent)

In `Handler.do_GET`, just before its final `return self._send(404, {'error': 'not found'})`:

```python
            if u.path == '/voice' or u.path.startswith(('/voice/', '/api/audio/')):
                from stark_audio import studio_api as _ga
                r = _ga.handle('GET', u.path, q, None, self.games)
                if r:
                    return self._send(r[0], r[2], r[1])
```

In `Handler.do_POST`, just before its final `return self._send(404, {'error': 'not found'})` (after the body was
parsed and the Origin checked):

```python
            if u.path.startswith(('/voice/', '/api/audio/')):
                from stark_audio import studio_api as _ga
                r = _ga.handle('POST', u.path, {}, body, self.games)
                if r:
                    return self._send(r[0], r[2], r[1])
```

The booth only touches `My_Games/<game>/voice/`, resolves every path inside `My_Games`, and inherits the studio's
Host/Origin checks and CSP. A take is posted as base64 JSON; the studio's 12 MB `MAX_BODY` covers takes of a few
minutes. The microphone opens only after the browser's own permission prompt. 127.0.0.1 counts as a secure
context, so `getUserMedia` is allowed there.

Endpoints: `GET /voice` (page), `GET /voice/api/{worlds,script,audio,line,preview,export_script}`,
`POST /voice/api/{take,import,best,trim,delete,process,profile,chain}`,
`POST /api/audio/build {world, budget?, force?}`, `GET|POST /api/audio/settings`, `GET /api/audio/status`.

## 3. Studio UI, World tab: build audio + open the booth (owner: v28.5 agent)

In the "Soundtrack" card markup (the string containing `<div class="card"><h4>Soundtrack</h4>`), add after the
`mkMusic` button's row:

```html
<div class="row"><button id="mkAudio" title="Adaptive music, sound effects, ambience and voices for the HTML5 cartridge">Build game audio</button><a id="voiceBooth" target="_blank" title="Record your own voice lines">Voice booth</a></div>
```

The card's sentence "exported cartridges stay small and keep their built-in sound effects" should then read:
"exported cartridges carry adaptive music, sound effects, ambience and your recorded voices".

Next to `$('mkMusic').onclick=...`:

```js
 if($('voiceBooth'))$('voiceBooth').href='/voice?world='+encodeURIComponent(slugJs(W.name));
 if($('mkAudio'))$('mkAudio').onclick=function(){var b=this;b.disabled=true;status('Building game audio (the first time takes about 2 minutes)...');
  save().then(function(){return api('/api/audio/build',{world:W.name});}).then(function(r){b.disabled=false;
  status('Game audio ready: '+(r.report.html_bytes/1e6).toFixed(1)+' MB in the cartridge ('+r.report.states.join(', ')+')');})
  .catch(function(e){b.disabled=false;status(e.message,true);});};
```

## 4. ENGINE_JS: optional explicit hooks (owner: v28.5 agent; the systems agent may emit the same events)

Without this patch the runtime infers combat from hit/attack/kill sounds and player damage. These hooks add the
events it cannot infer: enemy alert, positions for spatial hits, landing. Put a helper near `playSfx`:

```js
function aud(n,d){try{if(window.StarkAudio)window.StarkAudio.emit(n,d||{});}catch(e){}}
```

and set the flag in the module's returned object (anchor `SFX:SFX,ITEMS:ITEMS,ENEMY:ENEMY,ABILITIES:ABILITIES,create:create,`):

```js
audioHooks:true,SFX:SFX,ITEMS:ITEMS,ENEMY:ENEMY,ABILITIES:ABILITIES,create:create,
```

With `audioHooks:true`, the adapter stops mapping `hit`, `laser`, `boom`, `jump` and `door` to library sounds and
drops its combat guesswork, so nothing plays twice. Call sites (3D engine; the 2D engine has the same names):

| anchor | add |
|---|---|
| `function damage(e,dmg,kx,kz){if(e.dead)return;` (after its `playSfx(sfxFor(world,'hit'))`) | `aud('hit',{material:e.D.boss?'metal':'flesh',crit:crit,x:e.x,z:e.z});` |
| `function killEnemy(e){e.dead=true;` | `aud('enemy_die',{kind:e.kind,x:e.x,z:e.z});` |
| `function attack(){if(S.mode!=='play'\|\|P.atk>0\|\|(G&&G.busy()))return;` (after the guard) | `aud('attack',{});` |
| every `e.st='chase';` where the enemy was not chasing before | `aud('combat_start',{});aud('enemy_alert',{kind:e.kind,x:e.x,z:e.z,level:1});` |
| when the last chasing enemy dies or gives up | `aud('combat_end',{victory:true\|false});aud('tension_end',{});` |
| `P.vy=8.5;playSfx(sfxFor(world,'jump'));` | `aud('jump',{});` and on touchdown `aud('land',{});` |
| `t.k==='chest'){o.open=true;` | `aud('chest_open',{x:o.x,z:o.z});` |
| door unlock / portal use | `aud('door_open',{x:..,z:..})` / `aud('portal_enter',{})` |

Every frame in play mode (spatial audio):
`aud('listener',{x:P.x,z:P.z,yaw:S.yaw})`. Footsteps with the real surface (set `audioFootsteps:true` next to
`audioHooks` so the adapter stops making its own): `aud('footstep',{surface:'grass'|'stone'|'wood'|'water'|...,player:true,run:speed>6})`.

## 5. Godot / UE5 exporters (owner: bridge agent)

```python
try:
    from stark_audio import studio_api as _ga
    files.update(_ga.export_files(world, games, 'godot'))   # or 'ue5'; {relative path: bytes} under audio/
except ImportError:
    pass
```

`audio/mapping.json` holds every event, music state, stem, loop point, sound variant, bus and voice line. The
mapping rules are in `EXPORT_MAPPING.md`.

## 6. build_release.py: ship the package in every tier (owner: v28.5 agent)

In `tier_files()`, right after the `for name in EVERY_TIER:` loop:

```python
    for p in sorted(glob.glob(os.path.join(ROOT, 'stark_audio', '**', '*'), recursive=True)):
        if os.path.isfile(p) and '__pycache__' not in p and not p.endswith('.pyc'):
            with open(p, 'rb') as f:
                files[os.path.relpath(p, ROOT).replace(os.sep, '/')] = f.read()
```

The package has no personal paths or keys, uses only the standard library + numpy, and calls ffmpeg (already a
setup-wizard dependency) to encode, decode and measure. Without ffmpeg the studio still works: the cartridge falls
back to the built-in synth sounds.

## 7. qa_gate.py (optional, owner: v28.5 agent)

The `unittest` check already runs `tests/test_stark_audio_*.py` (about 90 s; `GS_FAST=1` skips the headless
browser mix test). To syntax-check the runtime too, add to the `js` check's sources:
`'stark_audio_runtime': '<script>' + open(os.path.join(ROOT, 'stark_audio', 'runtime', 'stark_audio.js')).read() + '</script>'`.

## 8. Systems runtime event contract (owner: systems agent)

The game calls `StarkAudio.emit(name, data)`. The runtime also listens to `StarkAudio.on(...)` for its own
outputs. These are the events it understands and what each one does (the same table ships in
`audio/mapping.json` under `events`):

| event | data | audio does |
|---|---|---|
| `combat_start` / `combat_end` | `{boss?}` / `{victory?}` | combat cue on the next beat with the combat stinger; victory fanfare then back to the calm state |
| `enemy_alert`, `tension_start` / `tension_end` | `{kind, x, z, level 0..1}` | tension cue (level >= 0.5), alert growl with a directional caption |
| `boss_start` / `boss_defeated` | | boss stinger + roar / victory |
| `quest_start`, `quest_complete`, `level_up` | `{id}` | UI cue; quest and level-up stingers sit on the beat over the music |
| `enter_region` | `{level}` or `{region}` | that level's region music + ambience beds + weather + reverb zone; discovery stinger |
| `enter_town` / `leave_town`, `time_of_day {t}` (0..1), `night_start` / `day_start`, `weather {kind}` | | town / night cues, beds |
| `player_hurt {hp,max}`, `player_heal`, `player_death`, `player_respawn` | | hurt sound, music low-pass under 30 % HP, defeat stinger |
| `dialogue_start {dialog}`, `dialogue_line {lineId, speaker, text, ui}`, `dialogue_end` | | music to bed layer, voice line (recorded or placeholder), ducking, subtitles, visemes |
| `menu_open` / `menu_close`, `pause` / `resume` | | music low-pass 1.2 kHz -5 dB |
| `footstep {surface, x, z, run, player}`, `jump`, `land`, `dash` | | movement sounds |
| `attack {heavy, weapon}`, `hit {material, crit, x, z}`, `cast {spell}`, `spell_hit`, `enemy_die`, `enemy_sound {kind}` | | combat / magic / creature sounds, spatial when x/z are given |
| `item_pickup`, `chest_open`, `door_open`, `portal_enter` | `{x, z}` | world sounds |
| `listener {x, z, yaw}` | each frame | spatial audio, reverb-zone lookup |
| `reverb_zone {type}`, `intensity {value}`, `sound {name, ...}` | | overrides |

Audio outputs for the animation runtime: `StarkAudio.on('viseme', fn)` receives `{lineId, speaker, v, open, weights}`
(`v` is one of `A B C D E F G H X`; `weights` uses ARKit-style blendshape names). `StarkAudio.VISEMES` is the
shape-to-blendshape table. `on('caption')`, `on('subtitle')`, `on('line_start')` and `on('line_end')` let a custom UI
replace the built-in overlay.

## 9. Soundtrack length shown in the UI (QA-029, owner: v28.5 agent)

`music_engine.compose_world()` now returns a seamless loop that is **at least** the requested length (rounded up
to whole 4-bar phrases, so never shorter). The actual length: `music_engine.soundtrack_seconds(path)`.

In `make_soundtrack()`, after `path = music_engine.compose_world(mood, seconds=seconds, seed=seed)`:

```python
        actual = round(music_engine.soundtrack_seconds(path), 1)
```

and return it: `return {'saved': rel, 'music': 'assets/soundtrack.wav', 'bytes': len(data), 'seconds': actual}`.
In the `mkMusic` handler's success branch:
`status('Soundtrack ready ('+(r.seconds||'?')+' s seamless loop) - press Play to hear it.');`
