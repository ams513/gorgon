"""studio_app.py: Stark World Engine - an offline 3D (WebGL2) + 2D game builder that runs in your browser.

  python studio_app.py                      start on 127.0.0.1, first free port 8505-8520, open the browser
  python studio_app.py --no-browser         start without opening a browser
  python studio_app.py --export NAME --target cartridge|godot|ue5     export a saved world, no server
  python studio_app.py --write-docs DIR     write GIFT_CERTIFICATE.html + PERSONAL_LICENSE.txt into DIR

Everything is local: the server binds 127.0.0.1 only, the page carries all of its JavaScript inline (no CDN, no
network), and files are read/written only inside My_Games/ (checked with os.path.realpath). Every overwrite keeps
the 3 newest older versions in My_Games/.backups/. Godot 4 / UE5 project export is in the Pro and Studio tiers.

Games are campaigns: quests, NPC dialogue, items, XP/levels, save slots, enemy AI and portals between levels, all
data-driven from world.json (see WORLD_SCHEMA_DOC). generate_world() ("Make me a game", worldgen.py) builds complete
games from a theme + size + seed. The browser runtime (engine3d/*.js, WebGL2) renders PBR materials with image-based
lighting from HDRI skies, soft cascaded shadows, SSAO, bloom, ACES tone mapping and FXAA/TAA, and draws rigged glTF 2.0
characters and CC0 art (assets/library.json); Godot 4 / UE5 export is the bridge to a full engine.
"""
import argparse, base64, datetime, glob, http.server, json, math, os, random, re, shutil, socket, socketserver, sys
import io, threading, urllib.parse, webbrowser, zipfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.environ.get('GS_DATA_DIR') or APP_DIR)   # Steam: Documents\Stark World Engine
GAMES = os.path.join(DATA_DIR, 'My_Games')
STEAM = os.path.isfile(os.path.join(APP_DIR, 'steam_build.json'))     # the Steam depot ships this marker


def set_data_dir(path):
    """Steam launcher hook: keep the user's games and imported art outside the install folder."""
    global DATA_DIR, GAMES
    DATA_DIR = os.path.abspath(path)
    GAMES = os.path.join(DATA_DIR, 'My_Games')
VERSION = '28.7.0'
PORTS = [p for p in range(8505, 8521) if p != 8510]     # 8510 belongs to the Creator Hub
TIERS = ('free', 'standard', 'pro', 'studio')
MOODS = ['Bioluminescent Twilight', 'Alpine Sunset', 'Sub-Zero Aurora', 'Crimson Citadel', 'Sunken Ruins']
LANGS = ['en', 'es', 'fr', 'de', 'pt', 'it', 'ja', 'zh']
MAX_BODY = 48 * 1024 * 1024
KEEP_BACKUPS = 3


# ---------------- tier ----------------
def license_info(app_dir=APP_DIR):
    """Offline license (licensing.py + key.txt next to this file). Missing/invalid key = unregistered, still works."""
    try:
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        import licensing
        return licensing.load_license(app_dir)
    except ImportError:
        return {'registered': False, 'tier': 'free', 'buyer': None, 'reason': 'licensing.py missing'}


def edition(app_dir=APP_DIR):
    """The edition shown to the player: from the licence + what is installed (Steam DLCs), else tier.json."""
    if os.path.isfile(os.path.join(app_dir, 'steam_build.json')):
        lic = license_info(app_dir)
        t = lic['tier'] if lic.get('registered') else 'standard'
        return 'studio' if t in ('pro', 'studio') and os.path.isfile(os.path.join(app_dir, 'obs_bridge.py')) else t
    try:
        with open(os.path.join(app_dir, 'tier.json'), encoding='utf-8-sig') as f:
            t = str(json.load(f).get('tier', '')).lower()
        return t if t in TIERS else 'free'
    except (OSError, ValueError, AttributeError):
        lic = license_info(app_dir)
        return lic['tier'] if lic.get('registered') else 'free'


def current_tier(app_dir=APP_DIR):
    """Feature tier from a VALID signed key only. No key -> free (Free exports one-level cartridges with a badge;
    Pro/Studio remove that and add Godot/UE5 export; an older Standard key removes the limits only)."""
    lic = license_info(app_dir)
    return lic['tier'] if lic.get('registered') else 'free'


# ---------------- sandbox ----------------
class SandboxError(ValueError):
    pass


WIN_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {f'{p}{i}' for p in ('COM', 'LPT') for i in range(1, 10)}


def _reserved(part):
    """Windows device names (CON, NUL, COM1, LPT1 ... with or without extension) and trailing dots/spaces."""
    return part.split('.')[0].rstrip(' ').upper() in WIN_RESERVED or part != part.rstrip('. ')


def slug(name):
    s = re.sub(r'[^A-Za-z0-9_-]+', '_', str(name or '')).strip('_')[:48]
    s = s or 'world'
    return s + '_world' if _reserved(s) else s


def safe_path(rel, games=None):
    """Resolve rel inside My_Games/ (realpath, so .. and symlinks/junctions cannot escape) or raise SandboxError."""
    games = os.path.realpath(games or GAMES)
    rel = str(rel or '').replace('\\', '/')
    if '\x00' in rel or os.path.isabs(rel) or re.match(r'^[A-Za-z]:', rel):
        raise SandboxError('absolute paths are not allowed')
    if any(_reserved(part) or ':' in part for part in rel.split('/') if part not in ('', '.', '..')):
        raise SandboxError('reserved or invalid file name')
    p = os.path.realpath(os.path.join(games, rel))
    if p != games and not p.startswith(games + os.sep):
        raise SandboxError('path escapes My_Games/')
    return p


_WRITE_LOCK = threading.Lock()


def write_versioned(rel, data, games=None):
    """Write bytes to My_Games/rel; the previous version goes to My_Games/.backups/ (3 newest kept per file).
    Serialised: an autosave and a Ctrl+S arriving together on two server threads cannot interleave the temp file."""
    with _WRITE_LOCK:
        return _write_versioned(rel, data, games)


def _write_versioned(rel, data, games=None):
    games = games or GAMES
    dest = safe_path(rel, games)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest):
        with open(dest, 'rb') as f:
            if f.read() == data:          # identical re-save: keep the older backups instead of rotating them out
                return dest
        bdir = safe_path(os.path.join('.backups', os.path.dirname(rel)), games)
        os.makedirs(bdir, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        base = os.path.basename(dest)
        bak, n = os.path.join(bdir, f'{base}.{stamp}.bak'), 1
        while os.path.exists(bak):           # two saves inside one clock tick (Windows): never overwrite a backup
            bak, n = os.path.join(bdir, f'{base}.{stamp}_{n:02d}.bak'), n + 1
        shutil.copy2(dest, bak)
        olds = sorted(glob.glob(os.path.join(glob.escape(bdir), glob.escape(base) + '.*.bak')))
        for old in olds[:-KEEP_BACKUPS]:
            os.remove(old)
    tmp = f'{dest}.{os.getpid()}.{threading.get_ident()}.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, dest)
    return dest


# ---------------- world schema (shared with the exporters) ----------------
WORLD_SCHEMA_DOC = """world.json (schema version 2, every new field optional; version-1 worlds load unchanged)

A world is level 0 of a campaign. Campaign-wide data (items, dialogs, quests) may live on the world or on any level.
  name, mode ('3d'|'2d'), mood (one of MOODS), seed (int), story (text)        - version 1
  objects: [{t, x, z, s, ...}]                                                 - version 1 (+ new types/fields below)
  tiles: ['....'] (2d only) characters  . # o ^ G e P  (v1)  + h potion, k key, D locked door, C checkpoint,
         X exit to next level, a archer, B boss, ~ water
  sprite: [256 colours] (2d player), sfx: {name: synth params}                  - version 1
  version: 2, author: text, difficulty: 'easy'|'normal'|'hard'
  size: half extent of a 3D level in metres (24..128, default 48); terrain: hill height 0..6 (default 1)
  water: water height (-3..3) or absent; weather: 'auto'|'clear'|'rain'|'storm'|'snow'|'embers'|'pollen'|'bubbles'|'fireflies'
  day: {start: 0..1 (0 midnight, .5 noon), len: seconds per full day (0 = time stands still)}
  spawn: {x, z} (3D start point; 2D uses the P tile)
  player: {hp: start HP, col: '#rrggbb', items: [item ids]}
  items: {id: {name, kind: quest|consumable|key|equip|treasure, heal, atk, def, hp, speed, slot: weapon|armor|boots|charm,
         gold, col: '#rrggbb', desc}}  (built-ins: crystal potion elixir key gem wood_sword iron_blade moon_blade leather
         warden_plate swift_boots ember_charm)
  quests: [{id, title, desc, giver: npc id, auto: bool (starts at once), final: bool (finishing it wins the game),
           next: quest id, steps: [{type: collect|defeat|talk|reach|escort, item, kind, id, npc, count, x, z, r, label,
           level, text, consume}], reward: {xp, gold, items: [ids]}}]
  dialogs: {id: {start: node id, branches: [{if: COND, node}], nodes: {node id: {speaker, text, next, do: ACTION,
           choices: [{text, goto, end, if: COND, ...ACTION}]}}}}
     COND   = {has, n, lacks, done, active, notStarted, flag, minLevel}
     ACTION = {start: quest, complete: quest, advance: npc id (counts as talking to it), give: item|[items], take: item,
               gold, xp, flag, heal, win}
  triggers: [{id, when: {type: enter|leave|start|talk|defeat|collect|flag|quest|timer, x, z, r, npc, id, kind, item, flag,
           quest, secs}, if: COND, once: bool (default true), then: [{do: door|sound|dialogue|say|flag|give|take|gold|xp|heal|
           startQuest|completeQuest|cutscene|teleport|level|win|achievement|shake|wait, ...}]}]   (per level; world = level 1)
  cutscenes: {id: {name, skip: bool, steps: [{t: seconds, do: camera|say|move|sound|fade|shake|flag|give|take|gold|xp|heal|
           door|teleport|achievement, dur, ...}]}}   achievements: [{id, title, desc, when: {type: kills|gold|level|quests|
           flag|item|win|levels|time (finish within n seconds), n, flag, item}}]   scoring: 'score'|'time' (high-score order), showTimer: bool
  levels: [level, ...]  each level = {name, mode, mood, seed, story, objects, tiles, size, terrain, water, weather, day,
          spawn, quests, dialogs, items}; portals and 2D X/G tiles lead to the next level.
Object types (t): tree ruin rock crystal enemy lamp (v1) + house npc item chest door portal checkpoint
  enemy: kind grunt|archer|brute|wisp|boss, id, name, hp, patrol (wander radius), col
  npc: id, name, dialog (dialog id), col, text (fallback line)      item: item (id), n
  chest: item, n, gold, rot      door: key (item id, default 'key'), rot, id (so a trigger can open it), sealed (only a trigger opens it)
  portal: to (level index | 'next'), lock (quest id), rot
  npc behaviour: behavior stand|patrol|follow|flee|companion, path [{x,z}] (patrol), speed, radius (flee / follow distance),
         shop {stock: [{item, price, n}], sell: bool, buyRate 0.05..1}    enemy behaviour: wander|patrol|guard|coward + path
  house: rot      checkpoint: -   In 2D levels, objects use tile coordinates (x = column, z = row); npc is supported.
28.7 additions (all optional):
  vars: [{id, name, start, min, max, show: true|'max'}] (world only, at most 30)  - numbers the game keeps, saved per slot
  THEN / cutscene / dialogue answer: {do: 'var', var, op: set|add|sub|random, num, max (random upper bound)}
  COND += {var, cmp: eq|ne|lt|le|gt|ge, num}, {chance: 0..100}
  when += {type: every, secs >= 0.5} (repeats), {type: key, key: KeyA..KeyZ|Digit0..9|Space|Enter|Arrow*},
          {type: var, var, cmp, num} (fires when the comparison becomes true)
  tune: {bpm, wave, scale, oct, len 16|32, vol, mel: [[step, row 0..11]], drum: [[step, 0..2]]} (world only)
"""
OBJ_TYPES = ('tree', 'ruin', 'rock', 'crystal', 'enemy', 'lamp', 'house', 'npc', 'item', 'chest', 'door', 'portal',
             'checkpoint', 'asset')
ASSET_REF = re.compile(r'^((nature|village|characters)/[\w-]{1,80}|lib:u[a-z2-7]{8}/[\w .()+-]{1,120})$')
ENEMY_KINDS = ('grunt', 'archer', 'brute', 'wisp', 'boss')
STEP_TYPES = ('collect', 'defeat', 'talk', 'reach', 'escort')
ITEM_KINDS = ('quest', 'consumable', 'key', 'equip', 'treasure')
SLOTS = ('weapon', 'armor', 'boots', 'charm')
WEATHERS = ('auto', 'clear', 'rain', 'storm', 'snow', 'embers', 'pollen', 'bubbles', 'fireflies')
TILE_CHARS = '.#o^GePhkDCXaB~'
_ID = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')


def _text(v, field, n=2000, req=False):
    if v is None and not req:
        return
    if not isinstance(v, str) or len(v) > n or (req and not v.strip()):
        raise ValueError(f'{field} must be text (at most {n} characters)')


def _num(v, field, lo=-1e6, hi=1e6, req=False):
    if v is None and not req:
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f'{field} must be a number between {lo:g} and {hi:g}')


def _ident(v, field, req=False):
    if v is None and not req:
        return
    if not isinstance(v, str) or not _ID.match(v):
        raise ValueError(f'{field} must be a short id (letters, digits, _ . -)')


def _dict(v, field):
    if v is not None and not isinstance(v, dict):
        raise ValueError(f'{field} must be an object')
    return v or {}


def _cond(c, field):
    c = _dict(c, field)
    for k in ('has', 'lacks', 'done', 'active', 'notStarted', 'flag'):
        _ident(c.get(k), f'{field}.{k}')
    _num(c.get('n'), f'{field}.n', 1, 999)
    _num(c.get('minLevel'), f'{field}.minLevel', 1, 99)
    _ident(c.get('var'), f'{field}.var')
    if c.get('cmp') not in (None,) + CMP_OPS:
        raise ValueError(f'{field}.cmp must be one of {", ".join(CMP_OPS)}')
    _num(c.get('num'), f'{field}.num', -1e6, 1e6)
    _num(c.get('chance'), f'{field}.chance', 0, 100)


def _action(a, field):
    for k in ('start', 'complete', 'advance', 'take', 'flag', 'goto', 'cutscene', 'achievement', 'door'):
        _ident(a.get(k), f'{field}.{k}')
    if a.get('shop') is not None and a['shop'] is not True:
        _ident(a['shop'], f'{field}.shop')
    g = a.get('give')
    for x in (g if isinstance(g, list) else [g] if g is not None else []):
        _ident(x, f'{field}.give')
    _num(a.get('gold'), f'{field}.gold', -99999, 99999)
    _num(a.get('xp'), f'{field}.xp', 0, 99999)
    _var_change(a, field)


def _var_change(a, field):
    """var / op / num / max of a 'Change a variable' block or a dialogue answer (28.7)."""
    _ident(a.get('var'), f'{field} var')
    if a.get('op') not in (None,) + VAR_OPS:
        raise ValueError(f'{field}: op must be one of {", ".join(VAR_OPS)}')
    _num(a.get('num'), f'{field} num', -1e6, 1e6)
    _num(a.get('max'), f'{field} max', -1e6, 1e6)


def validate_quests(qs, where):
    if qs is None:
        return
    if not isinstance(qs, list) or len(qs) > 200:
        raise ValueError(f'{where}quests must be a list of at most 200')
    for i, q in enumerate(qs):
        f = f'{where}quest {i + 1}'
        q = _dict(q, f)
        _ident(q.get('id'), f + ' id', True)
        _text(q.get('title'), f + ' title', 120)
        _text(q.get('desc'), f + ' description', 1000)
        _ident(q.get('giver'), f + ' giver')
        _ident(q.get('next'), f + ' next')
        steps = q.get('steps', [])
        if not isinstance(steps, list) or len(steps) > 20:
            raise ValueError(f + ': steps must be a list of at most 20')
        for j, s in enumerate(steps):
            sf = f'{f} step {j + 1}'
            s = _dict(s, sf)
            if s.get('type') not in STEP_TYPES:
                raise ValueError(f'{sf}: type must be one of {", ".join(STEP_TYPES)}')
            for k in ('item', 'id', 'npc'):
                _ident(s.get(k), f'{sf} {k}')
            if s.get('kind') not in (None, '', 'any') + ENEMY_KINDS:
                raise ValueError(f'{sf}: unknown enemy kind')
            _num(s.get('count'), sf + ' count', 1, 999)
            for k in ('x', 'z'):
                _num(s.get(k), f'{sf} {k}', -2000, 2000)
            _num(s.get('r'), sf + ' r', 0.5, 100)
            _num(s.get('level'), sf + ' level', 0, 99)
            _text(s.get('label'), sf + ' label', 120)
            _text(s.get('text'), sf + ' text', 300)
        r = _dict(q.get('reward'), f + ' reward')
        _num(r.get('xp'), f + ' reward xp', 0, 99999)
        _num(r.get('gold'), f + ' reward gold', 0, 99999)
        ri = r.get('items')
        if ri is not None and not isinstance(ri, list):
            raise ValueError(f + ' reward items must be a list of item ids')
        for x in ri or []:
            _ident(x, f + ' reward item')
        _ident(r.get('item'), f + ' reward item')


def validate_dialogs(ds, where):
    ds = _dict(ds, where + 'dialogs')
    if len(ds) > 300:
        raise ValueError('at most 300 dialogs')
    for did, d in ds.items():
        f = f'{where}dialog {did}'
        _ident(did, f)
        d = _dict(d, f)
        nodes = _dict(d.get('nodes'), f + ' nodes')
        if len(nodes) > 100:
            raise ValueError(f + ': at most 100 lines')
        _ident(d.get('start'), f + ' start')
        for b in d.get('branches', []) or []:
            b = _dict(b, f + ' branch')
            _cond(b.get('if'), f + ' branch if')
            _ident(b.get('node'), f + ' branch node')
        for nid, n in nodes.items():
            nf = f'{f} line {nid}'
            _ident(nid, nf)
            n = _dict(n, nf)
            _text(n.get('speaker'), nf + ' speaker', 80)
            _text(n.get('text'), nf + ' text', 2000)
            _ident(n.get('next'), nf + ' next')
            if n.get('do') is not None:
                _action(_dict(n.get('do'), nf + ' do'), nf + ' do')
            ch = n.get('choices', []) or []
            if not isinstance(ch, list) or len(ch) > 8:
                raise ValueError(nf + ': at most 8 choices')
            for c in ch:
                c = _dict(c, nf + ' choice')
                _text(c.get('text'), nf + ' choice text', 300)
                _cond(c.get('if'), nf + ' choice if')
                _action(c, nf + ' choice')


def validate_items(its, where):
    its = _dict(its, where + 'items')
    if len(its) > 200:
        raise ValueError('at most 200 custom items')
    for iid, it in its.items():
        f = f'{where}item {iid}'
        _ident(iid, f)
        it = _dict(it, f)
        _text(it.get('name'), f + ' name', 80, True)
        if it.get('kind', 'quest') not in ITEM_KINDS:
            raise ValueError(f'{f}: kind must be one of {", ".join(ITEM_KINDS)}')
        if it.get('kind') == 'equip' and it.get('slot') not in SLOTS:
            raise ValueError(f'{f}: equipment needs a slot ({", ".join(SLOTS)})')
        for k, lo, hi in (('heal', 0, 999), ('atk', -99, 99), ('def', -99, 99), ('hp', -99, 99), ('speed', 0.2, 3),
                          ('gold', 0, 99999), ('value', 0, 99999)):
            _num(it.get(k), f'{f} {k}', lo, hi)
        _text(it.get('desc'), f + ' description', 300)
        _text(it.get('col'), f + ' colour', 9)


NPC_BEHAVIORS = ('stand', 'patrol', 'follow', 'flee', 'companion')
ENEMY_BEHAVIORS = ('wander', 'patrol', 'guard', 'coward')
WHEN_TYPES = ('enter', 'leave', 'start', 'talk', 'defeat', 'collect', 'flag', 'quest', 'timer', 'every', 'key', 'var')
THEN_VERBS = ('door', 'sound', 'dialogue', 'say', 'flag', 'give', 'take', 'gold', 'xp', 'heal', 'startQuest',
              'completeQuest', 'cutscene', 'teleport', 'level', 'win', 'achievement', 'shake', 'wait', 'var')
CUT_VERBS = ('camera', 'say', 'move', 'sound', 'fade', 'shake', 'flag', 'give', 'take', 'gold', 'xp', 'heal', 'door',
             'teleport', 'achievement', 'var')
CMP_OPS = ('eq', 'ne', 'lt', 'le', 'gt', 'ge')            # variable comparisons (28.7)
VAR_OPS = ('set', 'add', 'sub', 'random')                # Change a variable (28.7)
KEY_CODE = re.compile(r'^(Key[A-Z]|Digit[0-9]|Space|Enter|Arrow(Up|Down|Left|Right))$')
VAR_MAX = 30
TUNE_WAVES = ('square', 'triangle', 'sine', 'saw')
TUNE_SCALES = ('penta', 'major', 'minor')
ACH_TYPES = ('kills', 'gold', 'level', 'quests', 'flag', 'item', 'win', 'levels', 'time')
SCORING = ('score', 'time')


def _step(a, f, verbs, timed=False):
    a = _dict(a, f)
    if a.get('do') not in verbs:
        raise ValueError(f'{f}: "do" must be one of {", ".join(verbs)}')
    for k in ('id', 'dialog', 'npc', 'flag', 'item', 'quest', 'cutscene', 'achievement', 'sound'):
        _ident(a.get(k), f'{f} {k}')
    _text(a.get('text'), f + ' text', 300)
    _text(a.get('speaker'), f + ' speaker', 80)
    _text(a.get('caption'), f + ' caption', 120)
    if a.get('value') is not None and not isinstance(a['value'], bool):
        raise ValueError(f + ' value must be true or false')
    if a.get('state') not in (None, 'open', 'close', 'toggle'):
        raise ValueError(f + ' state must be open, close or toggle')
    for k, lo, hi in (('n', 1, 999), ('gold', -99999, 99999), ('amount', 0, 99999), ('secs', 0, 3600), ('dur', 0, 120),
                      ('x', -2000, 2000), ('z', -2000, 2000), ('lx', -2000, 2000), ('lz', -2000, 2000),
                      ('h', -10, 80), ('lh', -10, 80), ('a', 0, 1), ('to', 0, 99), ('t', 0, 3600)):
        _num(a.get(k), f'{f} {k}', lo, hi)
    if a.get('do') == 'camera' and (a.get('x') is None or a.get('z') is None):
        raise ValueError(f + ': a camera step needs x and z')
    if a.get('do') == 'move' and not a.get('id'):
        raise ValueError(f + ': a move step needs the id of who moves (or "player")')
    _var_change(a, f)
    if a.get('do') == 'var' and not a.get('var'):
        raise ValueError(f + ': a Change a variable block needs a variable')
    if timed:
        _num(a.get('t'), f + ' time', 0, 3600, True)


def validate_logic(w, where):
    """triggers (when/then), cutscenes, achievements: the visual logic blocks."""
    trs = w.get('triggers')
    if trs is not None:
        if not isinstance(trs, list) or len(trs) > 200:
            raise ValueError(f'{where}triggers must be a list of at most 200')
        seen = set()
        for i, t in enumerate(trs):
            f = f'{where}trigger {i + 1}'
            t = _dict(t, f)
            _ident(t.get('id'), f + ' id')
            if t.get('id') in seen:
                raise ValueError(f'{f}: id {t["id"]} is used twice')
            seen.add(t.get('id'))
            wn = _dict(t.get('when'), f + ' when')
            if wn.get('type') not in WHEN_TYPES:
                raise ValueError(f'{f}: "when" type must be one of {", ".join(WHEN_TYPES)}')
            for k in ('npc', 'id', 'item', 'flag', 'quest'):
                _ident(wn.get(k), f'{f} when {k}')
            if wn.get('kind') not in (None, '', 'any') + ENEMY_KINDS:
                raise ValueError(f'{f}: unknown enemy kind')
            for k in ('x', 'z'):
                _num(wn.get(k), f'{f} when {k}', -2000, 2000)
            _num(wn.get('r'), f + ' when r', 0.5, 200)
            _num(wn.get('secs'), f + ' when secs', 0, 3600)
            if wn['type'] in ('enter', 'leave') and (wn.get('x') is None or wn.get('z') is None):
                raise ValueError(f'{f}: an area trigger needs x and z (use "Pick on map")')
            _ident(wn.get('var'), f + ' when var')
            if wn.get('cmp') not in (None,) + CMP_OPS:
                raise ValueError(f'{f}: when cmp must be one of {", ".join(CMP_OPS)}')
            _num(wn.get('num'), f + ' when num', -1e6, 1e6)
            if wn.get('key') is not None and not (isinstance(wn['key'], str) and KEY_CODE.match(wn['key'])):
                raise ValueError(f'{f}: when key must be a letter, a digit, Space, Enter or an arrow key')
            if wn['type'] == 'key' and not wn.get('key'):
                raise ValueError(f'{f}: a key trigger needs a key')
            if wn['type'] == 'var' and not wn.get('var'):
                raise ValueError(f'{f}: a variable trigger needs a variable')
            if wn['type'] == 'every' and (wn.get('secs') is None or wn['secs'] < 0.5):
                raise ValueError(f'{f}: a repeating trigger needs at least 0.5 seconds')
            _cond(t.get('if'), f + ' if')
            if t.get('once') is not None and not isinstance(t['once'], bool):
                raise ValueError(f + ' once must be true or false')
            th = t.get('then', [])
            if not isinstance(th, list) or len(th) > 30:
                raise ValueError(f'{f}: "then" must be a list of at most 30 blocks')
            for j, a in enumerate(th):
                _step(a, f'{f} then {j + 1}', THEN_VERBS)
    cs = w.get('cutscenes')
    if cs is not None:
        cs = _dict(cs, where + 'cutscenes')
        if len(cs) > 60:
            raise ValueError('at most 60 cutscenes')
        for cid, c in cs.items():
            f = f'{where}cutscene {cid}'
            _ident(cid, f)
            c = _dict(c, f)
            _text(c.get('name'), f + ' name', 80)
            steps = c.get('steps', [])
            if not isinstance(steps, list) or len(steps) > 80:
                raise ValueError(f + ': at most 80 steps')
            for j, a in enumerate(steps):
                _step(a, f'{f} step {j + 1}', CUT_VERBS, True)
    ach = w.get('achievements')
    if ach is not None:
        if not isinstance(ach, list) or len(ach) > 60:
            raise ValueError(f'{where}achievements must be a list of at most 60')
        for i, a in enumerate(ach):
            f = f'{where}achievement {i + 1}'
            a = _dict(a, f)
            _ident(a.get('id'), f + ' id', True)
            _text(a.get('title'), f + ' title', 60, True)
            _text(a.get('desc'), f + ' description', 160)
            wn = a.get('when')
            if wn is not None:
                wn = _dict(wn, f + ' when')
                if wn.get('type') not in ACH_TYPES:
                    raise ValueError(f'{f}: "when" type must be one of {", ".join(ACH_TYPES)}')
                _num(wn.get('n'), f + ' n', 1, 99999)
                _ident(wn.get('flag'), f + ' flag')
                _ident(wn.get('item'), f + ' item')


def _behavior(o, where):
    t = o['t']
    if o.get('sealed') is not None and (t != 'door' or not isinstance(o['sealed'], bool)):
        raise ValueError(f'{where}only a door can be sealed (true or false)')
    b = o.get('behavior')
    if b is not None and b not in (NPC_BEHAVIORS if t == 'npc' else ENEMY_BEHAVIORS if t == 'enemy' else ()):
        raise ValueError(f'{where}{t} behavior must be one of '
                         f'{", ".join(NPC_BEHAVIORS if t == "npc" else ENEMY_BEHAVIORS)}')
    pth = o.get('path')
    if pth is not None:
        if not isinstance(pth, list) or len(pth) > 24:
            raise ValueError(f'{where}{t} path must be a list of at most 24 points')
        for p in pth:
            p = _dict(p, f'{where}{t} path point')
            for k in ('x', 'z'):
                _num(p.get(k), f'{where}{t} path {k}', -2000, 2000, True)
    _num(o.get('speed'), f'{where}{t} speed', 0.3, 10)
    _num(o.get('radius'), f'{where}{t} radius', 1, 60)
    sh = o.get('shop')
    if sh is not None:
        if t != 'npc':
            raise ValueError(f'{where}only a person can run a shop')
        sh = _dict(sh, f'{where}shop')
        st = sh.get('stock', [])
        if not isinstance(st, list) or len(st) > 40:
            raise ValueError(f'{where}shop stock must be a list of at most 40 items')
        for e in st:
            e = _dict(e, f'{where}shop item')
            _ident(e.get('item'), f'{where}shop item', True)
            _num(e.get('price'), f'{where}shop price', 0, 99999)
            _num(e.get('n'), f'{where}shop stock count', 1, 999)
        _num(sh.get('buyRate'), f'{where}shop buyRate', 0.05, 1)
        if sh.get('sell') is not None and not isinstance(sh['sell'], bool):
            raise ValueError(f'{where}shop sell must be true or false')


def validate_level(w, where='', nlevels=0):
    if w.get('mode') not in ('3d', '2d'):
        raise ValueError(f'{where}mode must be 3d or 2d')
    if w.get('mood') not in MOODS:
        raise ValueError(f'{where}unknown mood')
    _text(w.get('name'), where + 'name', 120)
    _text(w.get('story'), where + 'story', 4000)
    objs = w.get('objects', [])
    if not isinstance(objs, list) or len(objs) > 5000:
        raise ValueError(f'{where}objects must be a list of at most 5000')
    for o in objs:
        if not isinstance(o, dict) or o.get('t') not in OBJ_TYPES:
            raise ValueError(f'{where}unknown object type {o.get("t") if isinstance(o, dict) else o}')
        for k in ('x', 'z'):
            if isinstance(o.get(k), bool) or not isinstance(o.get(k), (int, float)) or not math.isfinite(o[k])                     or abs(o[k]) > 10000:
                raise ValueError(f'{where}{o["t"]} position {k} must be a number')
        _num(o.get('s'), where + 'object size', 0.05, 20)
        t = o['t']
        if t == 'asset' and not (isinstance(o.get('asset'), str) and ASSET_REF.match(o['asset'])):
            raise ValueError(f'{where}asset objects need a library asset id')
        if t == 'enemy' and o.get('kind') not in (None, '') + ENEMY_KINDS:
            raise ValueError(f'{where}unknown enemy kind {o.get("kind")}')
        for k in ('id', 'dialog', 'item', 'key', 'lock'):
            _ident(o.get(k), f'{where}{t} {k}')
        for k in ('name', 'label'):
            _text(o.get(k), f'{where}{t} {k}', 80)
        _text(o.get('text'), f'{where}{t} text', 600)
        for k, lo, hi in (('hp', 1, 9999), ('patrol', 0, 60), ('n', 1, 999), ('gold', 0, 99999), ('rot', -10, 10)):
            _num(o.get(k), f'{where}{t} {k}', lo, hi)
        _behavior(o, where)
        if t == 'portal' and o.get('to') not in (None, 'next'):
            _num(o.get('to'), where + 'portal destination', 0, max(0, nlevels))
            if o['to'] != int(o['to']):
                raise ValueError(where + 'portal destination must be a whole level number')
    if w['mode'] == '2d':
        tiles = w.get('tiles')
        if (not isinstance(tiles, list) or not tiles or not all(isinstance(r, str) for r in tiles)
                or len({len(r) for r in tiles}) != 1 or len(tiles) > 64 or len(tiles[0]) > 1000
                or any(re.search('[^' + re.escape(TILE_CHARS) + ']', r) for r in tiles)):
            raise ValueError('2d tiles must be equal-length rows of . # o ^ G e P (and h k D C X a B ~)')
    _num(w.get('seed'), where + 'seed', -2e9, 2e9)
    _num(w.get('size'), where + 'size', 24, 128)
    _num(w.get('terrain'), where + 'terrain', 0, 6)
    _num(w.get('water'), where + 'water', -3, 3)
    if w.get('weather') not in (None,) + WEATHERS:
        raise ValueError(f'{where}weather must be one of {", ".join(WEATHERS)}')
    day = _dict(w.get('day'), where + 'day')
    _num(day.get('start'), where + 'day start', 0, 1)
    _num(day.get('len'), where + 'day length', 0, 7200)
    sp = _dict(w.get('spawn'), where + 'spawn')
    _num(sp.get('x'), where + 'spawn x', -200, 200)
    _num(sp.get('z'), where + 'spawn z', -200, 200)
    validate_quests(w.get('quests'), where)
    validate_dialogs(w.get('dialogs'), where)
    validate_items(w.get('items'), where)
    validate_logic(w, where)


def validate_vars(vs):
    """world.vars (28.7): [{id, name, start, min, max, show: true|'max'}] - named numbers the game keeps."""
    if vs is None:
        return
    if not isinstance(vs, list) or len(vs) > VAR_MAX:
        raise ValueError(f'vars must be a list of at most {VAR_MAX} variables')
    seen = set()
    for i, v in enumerate(vs):
        f = f'variable {i + 1}'
        v = _dict(v, f)
        _ident(v.get('id'), f + ' id', True)
        if v['id'] in seen:
            raise ValueError(f'{f}: id {v["id"]} is used twice')
        seen.add(v['id'])
        _text(v.get('name'), f + ' name', 40)
        for k in ('start', 'min', 'max'):
            _num(v.get(k), f'{f} {k}', -1e6, 1e6)
        if v.get('min') is not None and v.get('max') is not None and v['min'] > v['max']:
            raise ValueError(f'{f}: lowest is above highest')
        if v.get('show') not in (None, True, False, 'max'):
            raise ValueError(f'{f}: show must be true, false or "max"')


def validate_tune(t):
    """world.tune (28.7): a looping step tune that plays in Play mode and in the exported cartridge.
    {bpm 60..200, wave square|triangle|sine|saw, scale penta|major|minor, oct -1..1, len 16|32, vol 0..1,
     mel: [[step, row 0..11]], drum: [[step, 0 kick|1 snare|2 hat]]}"""
    if t is None:
        return
    t = _dict(t, 'tune')
    _num(t.get('bpm'), 'tune bpm', 60, 200)
    _num(t.get('vol'), 'tune volume', 0, 1)
    _num(t.get('oct'), 'tune octave', -1, 1)
    if t.get('wave') not in (None,) + TUNE_WAVES:
        raise ValueError(f'tune wave must be one of {", ".join(TUNE_WAVES)}')
    if t.get('scale') not in (None,) + TUNE_SCALES:
        raise ValueError(f'tune scale must be one of {", ".join(TUNE_SCALES)}')
    if t.get('len') not in (None, 16, 32):
        raise ValueError('tune length must be 16 or 32 steps')
    steps = t.get('len') or 16
    for key, rows, most in (('mel', 12, 512), ('drum', 3, 192)):
        notes = t.get(key)
        if notes is None:
            continue
        if not isinstance(notes, list) or len(notes) > most:
            raise ValueError(f'tune {key} must be a list of at most {most} notes')
        for n in notes:
            if (not isinstance(n, list) or len(n) != 2 or any(isinstance(x, bool) or not isinstance(x, int) for x in n)
                    or not 0 <= n[0] < steps or not 0 <= n[1] < rows):
                raise ValueError(f'tune {key}: every note is [step 0..{steps - 1}, row 0..{rows - 1}]')


def validate_world(w):
    if not isinstance(w, dict) or not w.get('name') or not isinstance(w.get('name'), str):
        raise ValueError('world needs a name')
    _text(w['name'], 'name', 120)
    levels = w.get('levels', [])
    if not isinstance(levels, list) or len(levels) > 24:
        raise ValueError('levels must be a list of at most 24')
    validate_level(w, '', len(levels))
    for i, L in enumerate(levels):
        if not isinstance(L, dict):
            raise ValueError(f'level {i + 2} must be an object')
        if 'levels' in L:
            raise ValueError('levels cannot contain more levels')
        validate_level(L, f'level {i + 2}: ', len(levels))
    if w.get('difficulty') not in (None, 'easy', 'normal', 'hard'):
        raise ValueError('difficulty must be easy, normal or hard')
    if w.get('scoring') not in (None,) + SCORING:
        raise ValueError('scoring must be score or time')
    if w.get('showTimer') is not None and not isinstance(w['showTimer'], bool):
        raise ValueError('showTimer must be true or false')
    _text(w.get('author'), 'author', 80)
    _text(w.get('remixOf'), 'remixOf', 160)
    if w.get('music') is not None and not (isinstance(w['music'], str) and
                                           re.match(r'^assets/[A-Za-z0-9_-]{1,48}\.wav$', w['music'])):
        raise ValueError('music must be a file in the world\'s assets folder (assets/name.wav)')
    validate_vars(w.get('vars'))
    validate_tune(w.get('tune'))
    pl = _dict(w.get('player'), 'player')
    _num(pl.get('hp'), 'player hp', 1, 99)
    _text(pl.get('col'), 'player colour', 9)
    pi = pl.get('items')
    if pi is not None and (not isinstance(pi, list) or len(pi) > 100):
        raise ValueError('player items must be a list of item ids')
    for x in pi or []:
        _ident(x, 'player item')
    sp = w.get('sprite')
    if sp is not None and (not isinstance(sp, list) or len(sp) > 256
                           or not all(c is None or (isinstance(c, str) and len(c) <= 32) for c in sp)):
        raise ValueError('sprite must be a list of at most 256 colours')
    sfx = _dict(w.get('sfx'), 'sfx')
    if len(sfx) > 40:
        raise ValueError('at most 40 sound effects')
    for k, p in sfx.items():
        p = _dict(p, f'sound {k}')
        if p.get('wave') not in (None, 'square', 'saw', 'sine', 'noise'):
            raise ValueError(f'sound {k}: wave must be square, saw, sine or noise')
        for f, lo, hi in (('freq', 1, 20000), ('slide', -10, 10), ('dur', 0, 3), ('decay', 0, 20), ('vol', 0, 1),
                          ('noise', 0, 1), ('seed', -1e9, 1e9)):
            _num(p.get(f), f'sound {k} {f}', lo, hi)
    ids = [q.get('id') for L in [w] + levels for q in (L.get('quests') or [])]
    if len(ids) != len(set(ids)):
        raise ValueError('quest ids must be unique across the whole game')
    return w


# ---------------- starter worlds and campaigns (original content) ----------------
def _scatter(r, kind, n, spread, avoid=6, **extra):
    out = []
    while len(out) < n:
        x, z = r.uniform(-spread, spread), r.uniform(-spread, spread)
        if abs(x) < avoid and abs(z) < avoid:
            continue
        o = {'t': kind, 'x': round(x, 2), 'z': round(z, 2), 's': round(r.uniform(0.8, 1.4), 2)}
        o.update(extra)
        out.append(o)
    return out


def _clear(objs, spots, rad=5.0):
    """Drop scatter objects that would sit on top of hand-placed story spots."""
    return [o for o in objs if all(math.hypot(o['x'] - x, o['z'] - z) > rad for x, z in spots)]


def _o(t, x, z, s=1, **kw):
    d = {'t': t, 'x': x, 'z': z, 's': s}
    d.update(kw)
    return d


def _offer(did, npc_name, hello, offer, accept, qid, wait, done, extra_nodes=None, extra_branches=None):
    """A standard quest-giver dialog: offer -> accept / decline; then 'how goes it' and 'thank you' branches."""
    nodes = {'hi': {'speaker': npc_name, 'text': hello + ' ' + offer,
                    'choices': [{'text': accept, 'start': qid, 'goto': 'yes'}, {'text': 'Not right now.', 'end': True}]},
             'yes': {'speaker': npc_name, 'text': 'Wonderful. Come back to me when it is done.'},
             'wait': {'speaker': npc_name, 'text': wait},
             'done': {'speaker': npc_name, 'text': done}}
    nodes.update(extra_nodes or {})
    br = (extra_branches or []) + [{'if': {'done': qid}, 'node': 'done'}, {'if': {'active': qid}, 'node': 'wait'}]
    return {did: {'start': 'hi', 'branches': br, 'nodes': nodes}}


def _fen_campaign():
    r = random.Random(2811)
    spots = [(-12, 6), (-18, -2), (-8, 12), (0, -36), (20, -22), (-4, 3), (24, 18), (-26, 20), (14, 8)]
    objs = _clear(_scatter(r, 'tree', 34, 40) + _scatter(r, 'ruin', 3, 30, 10) + _scatter(r, 'lamp', 6, 20)
                  + _scatter(r, 'rock', 10, 40), spots)
    objs += _scatter(r, 'crystal', 8, 26)
    objs += [_o('enemy', x, z, 1, kind='wisp', patrol=6) for x, z in ((22, -14), (-24, -22), (30, 6))]
    objs += [_o('enemy', x, z, 1, kind='grunt') for x, z in ((14, -30), (-30, 10))]
    objs += [_o('house', -14, 8, 1, rot=0.3), _o('house', -20, -2, 0.9, rot=-0.4), _o('house', -8, 13, 0.85, rot=2.6),
             _o('lamp', -10, 2), _o('lamp', -15, 13),
             _o('npc', -6, 4, id='moss', name='Elder Moss', dialog='moss', col='#6fa35a'),
             _o('npc', 20, -22, id='pip', name='Pip', dialog='pip', col='#e09f3e'),
             _o('npc', 24, 18, id='odo', name='Odo the Ferryman', dialog='odo', col='#3d5a80'),
             _o('checkpoint', -4, 0), _o('chest', -26, 20, item='leather', gold=15),
             _o('item', 14, 8, item='potion'), _o('portal', 0, -36, to='next', lock='q_pip')]
    dialogs = {}
    dialogs.update(_offer('moss', 'Elder Moss', 'Ah, a traveller! The fen glows brighter than it has in years.',
                          'Our lanterns need glow-crystals. Would you gather all 8 and bring word back to me?',
                          'I will gather the crystals.', 'q_spores', 'The crystals hum near the old trees. Eight in all.',
                          'Our lanterns will burn all winter. You have a good heart.',
                          extra_nodes={'pip': {'speaker': 'Elder Moss', 'text': 'One more thing... my grandson Pip chased '
                                               'a firefly into the eastern marsh and has not come back. The wisps are '
                                               'restless. Please, bring him home.',
                                               'choices': [{'text': 'I will find Pip.', 'start': 'q_pip', 'goto': 'yes'},
                                                           {'text': 'Later.', 'end': True}]},
                                       'bye': {'speaker': 'Elder Moss', 'text': 'Pip is safe, thanks to you. The '
                                               'ferry stone to the south has woken. It will carry you to the Drowned '
                                               'Hollow, where the wisps come from.'}},
                          extra_branches=[{'if': {'done': 'q_pip'}, 'node': 'bye'},
                                          {'if': {'done': 'q_spores', 'notStarted': 'q_pip'}, 'node': 'pip'}]))
    dialogs['pip'] = {'start': 'a', 'branches': [{'if': {'done': 'q_pip'}, 'node': 'home'}], 'nodes': {
        'a': {'speaker': 'Pip', 'text': 'You came! The wisps keep circling me. Can you take me back to the village?',
              'choices': [{'text': 'Stay close to me.', 'end': True}]},
        'home': {'speaker': 'Pip', 'text': 'Grandpa says you are the bravest person in the fen!'}}}
    dialogs.update(_offer('odo', 'Odo the Ferryman', 'No ferry today, friend. The wisps swarm the crossing.',
                          'Clear out three of them and I will give you my old blade.', 'Three wisps. Done.', 'q_wisps',
                          'Wisps glow blue. Swing true and they pop like bubbles.', 'Ha! Take the blade, you earned it.'))
    quests = [{'id': 'q_spores', 'title': 'Spore Harvest', 'giver': 'moss',
               'desc': 'Gather the 8 glow-crystals of the fen and tell Elder Moss.',
               'steps': [{'type': 'collect', 'item': 'crystal', 'count': 8},
                         {'type': 'talk', 'npc': 'moss', 'text': 'Return to Elder Moss'}],
               'reward': {'xp': 60, 'gold': 20, 'items': ['potion']}},
              {'id': 'q_wisps', 'title': 'Wisp Trouble', 'giver': 'odo', 'desc': 'Odo needs the crossing cleared.',
               'steps': [{'type': 'defeat', 'kind': 'wisp', 'count': 3}], 'reward': {'xp': 50, 'items': ['iron_blade']}},
              {'id': 'q_pip', 'title': 'Lost in the Marsh', 'giver': 'moss',
               'desc': 'Find Pip in the eastern marsh and walk him home to the village.',
               'steps': [{'type': 'talk', 'npc': 'pip', 'text': 'Find Pip in the eastern marsh'},
                         {'type': 'escort', 'npc': 'pip', 'x': -10, 'z': 6, 'r': 5, 'label': 'the village'}],
               'reward': {'xp': 80, 'gold': 30}}]
    l0 = {'name': 'Firefly Fen', 'mode': '3d', 'mood': 'Bioluminescent Twilight', 'seed': 11, 'version': 2,
          'author': 'Stark World Engine', 'water': -0.75,
          'story': 'Collect the 8 glow-spores before the bog wisps notice. Wisps hate a well-timed bonk. '
                   'Then find out where the wisps are coming from.',
          'objects': objs, 'quests': quests, 'dialogs': dialogs}
    # level 2: the Drowned Hollow
    r = random.Random(77)
    spots2 = [(0, 0), (-20, 18), (18, 20), (0, 30), (0, 36), (-24, -16), (10, -8), (26, -6), (-6, 10)]
    o2 = _clear(_scatter(r, 'tree', 26, 40) + _scatter(r, 'ruin', 7, 34, 10) + _scatter(r, 'rock', 14, 40), spots2, 6)
    o2 += [_o('item', x, z, item='tide_pearl') for x, z in ((-30, 4), (12, -30), (32, 12), (-14, -34), (28, -26))]
    o2 += [_o('enemy', x, z, 1, kind='archer') for x, z in ((-18, -8), (16, -18), (24, 4), (-28, 26))]
    o2 += [_o('enemy', x, z, 1, kind='grunt') for x, z in ((6, -22), (-10, -24), (30, -12))]
    o2 += [_o('enemy', 0, 24, 1.3, kind='brute', id='hollowbrute', name='Hollow Brute', patrol=3),
           _o('npc', -6, 10, id='nerea', name='Nerea the Diver', dialog='nerea', col='#2a9d8f'),
           _o('checkpoint', 4, 4), _o('chest', -24, -16, item='key', gold=10), _o('chest', 26, -6, item='warden_plate'),
           _o('door', 0, 30, key='key'), _o('portal', 0, 37, to='next'), _o('lamp', -3, 6), _o('lamp', 6, 8),
           _o('item', 10, -8, item='potion')]
    d2 = _offer('nerea', 'Nerea the Diver', 'The Hollow sank a hundred years ago and the pearls sank with it.',
                'Find me 5 tide pearls and I will tell you how to reach the Wisp Queen.', 'Five pearls, coming up.',
                'q_pearls', 'Pearls shine on the sea floor... I mean, the bog floor.',
                'The Queen hides beyond the shrine gate to the north. The key lies in an old chest in the south-west '
                'ruins. Beware the Hollow Brute.')
    l1 = {'name': 'The Drowned Hollow', 'mode': '3d', 'mood': 'Sunken Ruins', 'seed': 23, 'water': 0.15,
          'terrain': 1.6, 'weather': 'rain', 'spawn': {'x': 0, 'z': 0},
          'story': 'Rain over sunken stone. Somewhere north, behind a locked gate, the wisps are born.',
          'items': {'tide_pearl': {'name': 'Tide Pearl', 'kind': 'quest', 'col': '#bff6ff',
                                   'desc': 'Cool and smooth, it hums like the sea.'}},
          'objects': o2, 'dialogs': d2,
          'quests': [{'id': 'q_pearls', 'title': 'Pearls of the Hollow', 'giver': 'nerea',
                      'desc': 'Nerea wants 5 tide pearls.', 'steps': [{'type': 'collect', 'item': 'tide_pearl',
                                                                        'count': 5, 'consume': True},
                                                                       {'type': 'talk', 'npc': 'nerea',
                                                                        'text': 'Bring the pearls to Nerea'}],
                      'next': 'q_gate', 'reward': {'xp': 90, 'gold': 40, 'items': ['potion', 'potion']}},
                     {'id': 'q_gate', 'title': 'The Shrine Gate', 'desc': 'Find the key, beat the Hollow Brute and '
                                                                         'pass the shrine gate.',
                      'steps': [{'type': 'defeat', 'id': 'hollowbrute', 'text': 'Defeat the Hollow Brute'},
                                {'type': 'reach', 'x': 0, 'z': 34, 'r': 4, 'label': 'the shrine beyond the gate'}],
                      'reward': {'xp': 120}}]}
    # level 3: the boss grotto
    r = random.Random(91)
    o3 = _clear(_scatter(r, 'ruin', 8, 30, 10) + _scatter(r, 'rock', 20, 34) + _scatter(r, 'tree', 10, 34), [(0, 0), (0, -18)], 7)
    o3 += [_o('lamp', x, z) for x, z in ((-6, 6), (6, 6), (-10, -10), (10, -10))]
    o3 += [_o('enemy', x, z, 1, kind='wisp') for x, z in ((-14, -6), (14, -6), (0, -30))]
    o3 += [_o('enemy', -18, -20, 1.1, kind='brute'), _o('checkpoint', 0, 8), _o('chest', 12, 12, item='elixir'),
           _o('enemy', 0, -18, 1, kind='boss', id='queen', name='The Wisp Queen', patrol=2, col='#8e44ad')]
    l2 = {'name': "The Wisp Queen's Grotto", 'mode': '3d', 'mood': 'Crimson Citadel', 'seed': 5, 'size': 36,
          'terrain': 0.6, 'spawn': {'x': 0, 'z': 14}, 'objects': o3,
          'story': 'Embers drift through the grotto. The Queen of all wisps waits on her broken throne.',
          'quests': [{'id': 'q_queen', 'title': 'Snuff the Queen', 'auto': True, 'final': True,
                      'desc': 'Defeat the Wisp Queen and bring peace back to the fen.',
                      'steps': [{'type': 'defeat', 'id': 'queen', 'text': 'Defeat the Wisp Queen'}],
                      'reward': {'xp': 300, 'gold': 100}}]}
    l0['levels'] = [l1, l2]
    return l0


def _summit_campaign():
    r = random.Random(4402)
    spots = [(-8, 6), (-12, -4), (30, -30), (0, -40), (-4, 12)]
    objs = _clear(_scatter(r, 'tree', 40, 44) + _scatter(r, 'rock', 22, 44) + _scatter(r, 'ruin', 2, 30, 12), spots)
    objs += _scatter(r, 'crystal', 6, 30)
    objs += [_o('enemy', x, z, 1, kind=k) for x, z, k in ((18, 10, 'grunt'), (-22, 20, 'grunt'), (20, -18, 'archer'),
                                                          (-26, -20, 'grunt'))]
    objs += [_o('enemy', 26, -26, 1.4, kind='brute', id='goatking', name='The Goat King', patrol=4),
             _o('house', -12, -4, 1.1, rot=0.5), _o('lamp', -8, 2), _o('checkpoint', -2, 4),
             _o('npc', -8, 6, id='ida', name='Grandma Ida', dialog='ida', col='#c3365a'),
             _o('chest', 30, -32, item='swift_boots', gold=25), _o('portal', 0, -40, to='next', lock='q_snacks')]
    dialogs = _offer('ida', 'Grandma Ida', 'Those rascal goats stole my picnic!',
                     'There are 6 golden snacks scattered over the mountain. Bring them back before sundown?',
                     'I will find your snacks.', 'q_snacks', 'Six golden snacks, dear. Mind the Goat King up north-east.',
                     'You found every crumb! The pass to the north is open now - the view from up there is wonderful.')
    l0 = {'name': 'Summit Snack Run', 'mode': '3d', 'mood': 'Alpine Sunset', 'seed': 4, 'version': 2,
          'author': 'Stark World Engine', 'terrain': 1.4, 'day': {'start': 0.68, 'len': 300},
          'story': 'The mountain goats stole the picnic. Recover 6 golden snacks before sundown.',
          'objects': objs, 'dialogs': dialogs,
          'quests': [{'id': 'q_snacks', 'title': 'The Great Picnic Heist', 'giver': 'ida',
                      'desc': 'Recover Grandma Ida\'s 6 golden snacks.',
                      'steps': [{'type': 'collect', 'item': 'crystal', 'count': 6, 'text': 'Find the golden snacks'},
                                {'type': 'talk', 'npc': 'ida', 'text': 'Bring the snacks to Grandma Ida'}],
                      'reward': {'xp': 80, 'gold': 30, 'items': ['potion']}},
                     {'id': 'q_goat', 'title': 'Dethrone the Goat King', 'auto': True,
                      'desc': 'The Goat King leads the snack thieves. Teach him manners.',
                      'steps': [{'type': 'defeat', 'id': 'goatking', 'text': 'Defeat the Goat King'}],
                      'reward': {'xp': 70, 'items': ['ember_charm']}}]}
    r = random.Random(808)
    o2 = _clear(_scatter(r, 'tree', 30, 40) + _scatter(r, 'rock', 26, 40) + _scatter(r, 'ruin', 4, 30, 10),
                [(0, 0), (0, -24), (-16, 10)], 6)
    o2 += [_o('enemy', x, z, 1, kind=k) for x, z, k in ((-14, -10, 'archer'), (14, -12, 'archer'), (20, 14, 'grunt'),
                                                        (-24, 18, 'brute'), (8, 24, 'wisp'))]
    o2 += [_o('enemy', 0, -24, 1, kind='boss', id='ragnhild', name='Old Ram Ragnhild', col='#9fb7d0'),
           _o('checkpoint', 0, 6), _o('lamp', -3, 3), _o('lamp', 3, 3), _o('chest', -16, 10, item='potion', gold=20),
           _o('npc', 4, 8, id='sven', name='Sven the Guide', dialog='sven', col='#3d5a80')]
    l1 = {'name': 'Frostbite Pass', 'mode': '3d', 'mood': 'Sub-Zero Aurora', 'seed': 31, 'terrain': 2.2,
          'spawn': {'x': 0, 'z': 10}, 'objects': o2,
          'story': 'Under the aurora, the oldest ram of all guards the last stolen picnic basket.',
          'dialogs': {'sven': {'start': 'a', 'nodes': {'a': {'speaker': 'Sven the Guide', 'text': 'Old Ragnhild sits '
                                                             'on the picnic basket at the top of the pass. She charges '
                                                             'and throws ice - dodge with Shift once you have learnt it!',
                                                             'choices': [{'text': 'Thanks, Sven.', 'end': True}]}}}},
          'quests': [{'id': 'q_ram', 'title': 'The Last Basket', 'auto': True, 'final': True,
                      'desc': 'Defeat Old Ram Ragnhild and recover the last basket.',
                      'steps': [{'type': 'defeat', 'id': 'ragnhild', 'text': 'Defeat Old Ram Ragnhild'}],
                      'reward': {'xp': 250, 'gold': 80}}]}
    l0['levels'] = [l1]
    return l0


def _leap_tiles():
    rows = ['.' * 60 for _ in range(15)]
    g = [list(x) for x in rows]
    for x in range(60):
        g[14][x] = '#'
        g[13][x] = '#' if x not in (17, 18, 31, 32, 33, 46) else '.'
        if x in (17, 18, 31, 32, 33, 46):
            g[14][x] = '^'
    for x0, y, w in ((6, 10, 4), (12, 8, 3), (20, 9, 5), (27, 7, 3), (35, 10, 4), (40, 8, 3), (48, 9, 4)):
        for x in range(x0, x0 + w):
            g[y][x] = '#'
        g[y - 1][x0 + 1] = 'o'
    for x, y in ((9, 12), (24, 12), (38, 12), (44, 12), (52, 12)):
        g[y][x] = 'o'
    for x in (14, 26, 41, 50):
        g[12][x] = 'e'
    g[12][2] = 'P'
    g[12][57] = 'G'
    return [''.join(x) for x in g]


def platformer_tiles(seed, width=96, height=15, boss=False, keydoor=True, water=False, density=1.0):
    """Procedural 2D level: ground with pits/spikes, floating platforms, pearls, enemies, potion, key+door, checkpoint."""
    r = random.Random(seed)
    g = [['.'] * width for _ in range(height)]
    for x in range(width):
        g[height - 1][x] = '#'
        g[height - 2][x] = '#'
    x = 10
    while x < width - 14:
        x += r.randint(7, 13)
        if x >= width - 12:
            break
        w = r.randint(2, 3)
        for xx in range(x, x + w):
            g[height - 2][xx] = '~' if water and r.random() < 0.5 else '.'
            g[height - 1][xx] = '^' if g[height - 2][xx] == '.' else '~'
    for _ in range(int(width / 6 * density)):
        px, py, w = r.randint(4, width - 8), r.randint(height - 8, height - 4), r.randint(2, 5)
        for xx in range(px, min(width - 2, px + w)):
            if g[py][xx] == '.':
                g[py][xx] = '#'
        if g[py - 1][px] == '.':
            g[py - 1][px] = 'o'
    ground = height - 3
    for xx in range(6, width - 6, 5):
        if g[ground + 1][xx] == '#' and g[ground][xx] == '.' and r.random() < 0.45:
            g[ground][xx] = 'o'
    for xx in range(14, width - 10, max(6, int(11 / density))):
        if g[ground + 1][xx] == '#' and g[ground][xx] == '.':
            g[ground][xx] = 'a' if r.random() < 0.3 else 'e'
    def put(xx, ch):
        for d in range(width):
            for c in (xx + d, xx - d):
                if 2 < c < width - 2 and g[ground + 1][c] == '#' and g[ground][c] == '.':
                    g[ground][c] = ch
                    return c
    put(3, 'P')
    put(width // 2, 'C')
    put(width // 3, 'h')
    put(2 * width // 3, 'h')
    if keydoor:
        put(width // 2 + 6, 'k')
        dx = put(width - 9, 'D')
        if dx is not None:
            g[ground - 1][dx] = '#'
            g[ground - 2][dx] = '#'
    if boss:
        put(width - 16, 'B')
    put(width - 4, 'X')
    return [''.join(row) for row in g]


def _leap_campaign():
    t1 = platformer_tiles(5150, 110, 15, keydoor=True, water=True)
    t2 = platformer_tiles(9090, 80, 15, boss=True, keydoor=False)
    l0 = {'name': 'Lantern Leap', 'mode': '2d', 'mood': 'Sunken Ruins', 'seed': 7, 'version': 2,
          'author': 'Stark World Engine', 'tiles': _leap_tiles(),
          'story': 'A sunken lighthouse keeper hops ruin to ruin collecting pearls to relight the lamp.',
          'objects': [_o('npc', 5, 12, id='wren', name='Keeper Wren', dialog='wren', col='#e9c46a')],
          'dialogs': _offer('wren', 'Keeper Wren', 'The great lamp went dark when the sea rose.',
                            'Gather 10 pearls on your way to the flag and the lamp may shine again.',
                            'Ten pearls. Leave it to me.', 'q_pearls10', 'Pearls glow - follow the light.',
                            'The lamp flickers! Now climb to the top of the lighthouse.'),
          'quests': [{'id': 'q_pearls10', 'title': 'Pearls for the Lamp', 'giver': 'wren',
                      'desc': 'Collect 10 pearls for Keeper Wren.',
                      'steps': [{'type': 'collect', 'item': 'crystal', 'count': 10, 'text': 'Collect pearls'}],
                      'reward': {'xp': 50, 'items': ['wood_sword']}}],
          'levels': [{'name': 'Coral Caverns', 'mode': '2d', 'mood': 'Bioluminescent Twilight', 'seed': 8,
                      'tiles': t1, 'objects': [],
                      'story': 'Glowing coral, deep water and a locked sea gate. Find the key!'},
                     {'name': 'Lighthouse Top', 'mode': '2d', 'mood': 'Crimson Citadel', 'seed': 9, 'tiles': t2,
                      'objects': [], 'story': 'The Storm Warden has taken the lamp room. Stomp it three times!',
                      'quests': [{'id': 'q_warden', 'title': 'Relight the Lamp', 'auto': True, 'final': True,
                                  'desc': 'Defeat the Storm Warden at the top of the lighthouse.',
                                  'steps': [{'type': 'defeat', 'kind': 'boss', 'text': 'Defeat the Storm Warden'}],
                                  'reward': {'xp': 200, 'gold': 60}}]}]}
    return l0


def starter_worlds():
    """The three starter games. Each is a multi-level campaign; level 1 keeps the classic collect/bonk/goal loop."""
    return [_fen_campaign(), _summit_campaign(), _leap_campaign()]


def classic_templates():
    """Simplest templates: the original v1 loop (collect crystals / reach the flag), no quests."""
    r = random.Random(12)
    w3 = {'name': 'Classic Crystal Hunt', 'mode': '3d', 'mood': 'Alpine Sunset', 'seed': 12,
          'story': 'Collect every crystal. Bonk anything that bonks you.',
          'objects': _scatter(r, 'tree', 30, 40) + _scatter(r, 'rock', 12, 40) + _scatter(r, 'crystal', 8, 26)
          + _scatter(r, 'enemy', 4, 26, 10) + _scatter(r, 'lamp', 4, 20)}
    w2 = {'name': 'Classic Platformer', 'mode': '2d', 'mood': 'Sunken Ruins', 'seed': 3, 'tiles': _leap_tiles(),
          'objects': [], 'story': 'Run, jump, stomp, reach the flag.'}
    return [w3, w2] + day_one_templates()


# ---------------- day-one templates: a mystery village, a dungeon crawl, a time trial ----------------
def _ring(cx, cz, rad, n, gap_deg, s=1.8):
    """A closed circle of boulders around (cx, cz) with one rock left out: returns (rocks, door x, door z, door rot)."""
    step = 360.0 / n
    gap = round(gap_deg / step) * step
    out = []
    for k in range(n):
        a = step * k
        if abs(a - gap) < 1e-6:
            continue
        out.append(_o('rock', round(cx + rad * math.cos(math.radians(a)), 2), round(cz + rad * math.sin(math.radians(a)), 2), s))
    g = math.radians(gap)
    return (out, round(cx + rad * math.cos(g), 2), round(cz + rad * math.sin(g), 2),
            round(math.atan2(math.cos(g), -math.sin(g)) % math.pi, 2))


def _wall(z, x0, x1, gap_x, gap_w, step=3.0, s=2.4):
    """A line of boulders across the level at depth z with a doorway of half width gap_w at gap_x (door fills it)."""
    out, x = [], gap_x + gap_w
    while x <= x1:
        out.append(_o('rock', round(x, 2), z, s))
        x += step
    x = gap_x - gap_w
    while x >= x0:
        out.append(_o('rock', round(x, 2), z, s))
        x -= step
    return out


def _declutter(objs, scat, spawn, rad=3.2):
    """Drop random scenery that would sit on a person, enemy, chest, door, patrol lane or the start point."""
    sp = [spawn]
    ids = {id(o) for o in scat}
    for o in objs:
        if id(o) in ids:
            continue
        if o['t'] in ('npc', 'enemy', 'chest', 'door', 'item', 'checkpoint', 'crystal', 'portal'):
            sp.append((o['x'], o['z']))
            pts = [(o['x'], o['z'])] + [(p['x'], p['z']) for p in o.get('path') or []]
            for a, b in zip(pts, pts[1:] + (pts[:1] if len(pts) > 2 else [])):
                n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) // 2.5))
                sp += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]
    return [o for o in objs if id(o) not in ids or all(math.hypot(o['x'] - x, o['z'] - z) > rad for x, z in sp)]


def _mystery_village():
    r = random.Random(4417)
    spots = [(-2, -4), (-14, 6), (12, 10), (-6, -24), (27, -24), (24, -18), (0, 0), (-18, 16), (8, -8)]
    scat = _clear(_scatter(r, 'tree', 34, 40, 8) + _scatter(r, 'rock', 8, 40, 8) + _scatter(r, 'lamp', 4, 18), spots, 6)
    objs = list(scat)
    mill_yard, dx, dz, drot = _ring(28, -24, 8.5, 22, 160)
    objs += [_o('house', -11, -11, 1, rot=0.2), _o('house', 2, -12, 1.0, rot=-0.3), _o('house', -16, 12, 0.9, rot=2.4),
             _o('house', 14, 16, 1, rot=3.4), _o('house', 18, -6, 0.95, rot=1.2), _o('house', 28, -24, 1.4, rot=0.0),
             _o('lamp', -4, -2), _o('lamp', 9, 2), _o('lamp', -12, 4), _o('lamp', 22, -16), _o('checkpoint', 2, 2)]
    objs += mill_yard
    objs += [_o('door', dx, dz, 1, rot=drot, key='mill_key', id='milldoor'),
             _o('npc', -2, -4, id='orla', name='Mayor Orla', dialog='orla', col='#a14b5a'),
             _o('npc', -14, 6, id='tomas', name='Tomas the Baker', dialog='tomas', col='#d9a441'),
             _o('npc', 12, 10, id='edda', name='Edda the Weaver', dialog='edda', col='#7d5ba6'),
             _o('npc', -6, -24, id='piet', name='Piet the Fisher', dialog='piet', col='#3d7ea6'),
             _o('npc', 28, -26, id='bram', name='Bram the Miller', dialog='bram', col='#8a6b4a'),
             _o('npc', 6, 6, id='ansel', name='Ansel the Watchman', dialog='ansel', col='#5a6a7a', behavior='patrol', speed=1.6,
                path=[{'x': 6, 'z': 6}, {'x': 6, 'z': -6}, {'x': -6, 'z': -6}, {'x': -6, 'z': 6}]),
             _o('npc', -20, -4, id='una', name='Una the Pedlar', dialog='una', col='#2a9d8f',
                shop={'stock': [{'item': 'potion', 'price': 8}, {'item': 'leather', 'price': 30, 'n': 1},
                                {'item': 'elixir'}], 'sell': True, 'buyRate': 0.5}),
             _o('item', -10, 20, item='potion'), _o('chest', 30, 12, gold=25, item='swift_boots'), _o('chest', -3, -27, item='mill_key')]
    objs = _declutter(objs, scat, (2, 6))
    dialogs = {}
    dialogs.update(_offer('orla', 'Mayor Orla', 'Thank goodness you are here.',
                          'The festival bell has vanished from the tower, and the festival is tomorrow. Would you ask around? '
                          'Tomas the baker is up before anyone.', 'I will find the bell.', 'q_bell',
                          'Any luck? Tomas, then Edda, then Piet. Somebody saw something.',
                          'The bell rings again, and the whole village owes you a pie.'))
    dialogs['tomas'] = {'start': 'hi', 'branches': [{'if': {'flag': 'clue1'}, 'node': 'again'}], 'nodes': {
        'hi': {'speaker': 'Tomas', 'text': 'A missing bell? Hm. At dawn I saw cart tracks heading east, toward the old mill. '
                                           'Odd - the mill has been shut all week. Edda the weaver watches the east road.',
               'do': {'flag': 'clue1'}, 'choices': [{'text': 'Thank you, Tomas.', 'end': True}]},
        'again': {'speaker': 'Tomas', 'text': 'Cart tracks, east road, toward the mill. Go and ask Edda.'}}}
    dialogs['edda'] = {'start': 'early', 'branches': [{'if': {'flag': 'clue2'}, 'node': 'again'},
                                                      {'if': {'flag': 'clue1'}, 'node': 'clue'}], 'nodes': {
        'early': {'speaker': 'Edda', 'text': 'Wool, wool, wool. I have nothing to say until I hear what Tomas saw at dawn.'},
        'clue': {'speaker': 'Edda', 'text': 'Tracks to the mill? Then take this: a bit of tower rope, snagged on a cart wheel. '
                                            'Piet fishes by the east reeds and hears everything at night.',
                 'do': {'flag': 'clue2', 'give': 'rope_scrap'}, 'choices': [{'text': 'Thank you, Edda.', 'end': True}]},
        'again': {'speaker': 'Edda', 'text': 'Piet, by the east reeds. Mind the mud.'}}}
    dialogs['piet'] = {'start': 'early', 'branches': [{'if': {'flag': 'clue3'}, 'node': 'again'},
                                                      {'if': {'flag': 'clue2'}, 'node': 'clue'}], 'nodes': {
        'early': {'speaker': 'Piet', 'text': 'The fish are not biting and I am not talking.'},
        'clue': {'speaker': 'Piet', 'text': 'Hammering! All night, from the mill. And I fished a key with a little wheel stamped '
                                            'on it out of the reeds on the mill road - it is in the crate by my boat. Take it.',
                 'do': {'flag': 'clue3'}, 'choices': [{'text': 'Thank you, Piet.', 'end': True}]},
        'again': {'speaker': 'Piet', 'text': 'The mill, friend. Be kind to whoever you find there.'}}}
    dialogs['bram'] = {'start': 'hi', 'branches': [{'if': {'done': 'q_bell'}, 'node': 'done'}], 'nodes': {
        'hi': {'speaker': 'Bram', 'text': 'The bell cracked in spring, and I could not bear a festival without it. I borrowed it '
                                          'to mend and meant to hang it back at dawn. I am sorry for the worry.',
               'choices': [{'text': 'Then let us hang it together.', 'end': True}]},
        'done': {'speaker': 'Bram', 'text': 'Listen to her ring. Good as new.'}}}
    dialogs['ansel'] = {'start': 'a', 'nodes': {'a': {'speaker': 'Ansel', 'text': 'Quiet night. I walk the same circuit every '
                                                      'hour; my feet know it by heart.'}}}
    dialogs['una'] = {'start': 'a', 'nodes': {'a': {'speaker': 'Una', 'text': 'Potions, leather, a good deal. Have a look!'}}}
    quests = [{'id': 'q_bell', 'title': 'The Missing Bell', 'giver': 'orla', 'final': True,
               'desc': 'The festival bell has vanished. Follow the clues: Tomas, Edda, Piet - then the mill.',
               'steps': [{'type': 'talk', 'npc': 'tomas', 'text': 'Ask Tomas the baker'},
                         {'type': 'talk', 'npc': 'edda', 'text': 'Ask Edda the weaver'},
                         {'type': 'talk', 'npc': 'piet', 'text': 'Ask Piet the fisher'},
                         {'type': 'reach', 'x': 26, 'z': -23, 'r': 4, 'label': 'the old mill yard'},
                         {'type': 'talk', 'npc': 'bram', 'text': 'Speak to the miller'}],
               'reward': {'xp': 120, 'gold': 50, 'items': ['potion']}}]
    cut = {'reveal': {'name': 'The miller\'s secret', 'steps': [
        {'t': 0, 'do': 'camera', 'x': 21, 'z': -16, 'h': 4, 'lx': 28, 'lz': -26, 'lh': 1.5, 'dur': 1.5},
        {'t': 0.6, 'do': 'say', 'speaker': 'Bram', 'text': 'Oh! I was not expecting company.', 'secs': 2.4},
        {'t': 3, 'do': 'move', 'id': 'bram', 'x': 26, 'z': -23, 'dur': 1.6},
        {'t': 3, 'do': 'camera', 'x': 23, 'z': -18, 'h': 2.6, 'lx': 26, 'lz': -23, 'lh': 1.4, 'dur': 1.6},
        {'t': 3.4, 'do': 'say', 'speaker': 'Bram', 'text': 'The bell is cracked. I wanted it whole again before the festival.', 'secs': 3.4},
        {'t': 7, 'do': 'sound', 'sound': 'quest', 'caption': 'a soft bell hum'},
        {'t': 7, 'do': 'flag', 'flag': 'revealed'}]},
        'festival': {'name': 'The bell rings', 'steps': [
            {'t': 0, 'do': 'camera', 'x': 0, 'z': 22, 'h': 9, 'lx': 0, 'lz': -4, 'lh': 6, 'dur': 3},
            {'t': 0.5, 'do': 'sound', 'sound': 'levelup', 'caption': 'the festival bell rings'},
            {'t': 1, 'do': 'say', 'speaker': 'Mayor Orla', 'text': 'Ring, old bell! The festival has begun!', 'secs': 3},
            {'t': 4.5, 'do': 'fade', 'a': 0, 'dur': 0.2}]}}
    triggers = [
        {'id': 'brief', 'when': {'type': 'start'}, 'then': [{'do': 'say', 'speaker': '', 'text': 'The festival bell is missing. Talk to Mayor Orla.', 'secs': 4}]},
        {'id': 'rope', 'when': {'type': 'collect', 'item': 'mill_key'},
         'then': [{'do': 'sound', 'sound': 'coin', 'caption': 'a small key chimes'}, {'do': 'say', 'speaker': '', 'text': 'A key stamped with a little mill-wheel. The old mill is to the east.', 'secs': 4}]},
        {'id': 'mill', 'when': {'type': 'enter', 'x': 27, 'z': -24, 'r': 4}, 'if': {'flag': 'clue3'}, 'then': [{'do': 'cutscene', 'cutscene': 'reveal'}]},
        {'id': 'ring', 'when': {'type': 'quest', 'quest': 'q_bell'}, 'then': [{'do': 'achievement', 'achievement': 'sharp_eye'}]},
        {'id': 'well', 'once': False, 'when': {'type': 'enter', 'x': 0, 'z': 0, 'r': 2.5}, 'then': [{'do': 'heal'}, {'do': 'sound', 'sound': 'power', 'caption': 'a cool splash'}]}]
    return {'name': 'The Missing Bell', 'mode': '3d', 'mood': 'Alpine Sunset', 'seed': 31, 'version': 2, 'size': 44, 'terrain': 0.8,
            'weather': 'pollen', 'day': {'start': 0.4, 'len': 480}, 'author': 'Stark World Engine', 'spawn': {'x': 2, 'z': 6},
            'story': 'A mystery village. The festival bell has vanished the day before the festival. Question the villagers, '
                     'follow the clues, and find out who took it. Talk with E.',
            'player': {'hp': 6, 'items': ['wood_sword']}, 'achievements': [
                {'id': 'sharp_eye', 'title': 'Sharp Eye', 'desc': 'Solve the mystery of the missing bell.'},
                {'id': 'nosy', 'title': 'Nosy Neighbour', 'desc': 'Collect all three clues.', 'when': {'type': 'flag', 'flag': 'clue3'}}],
            'items': {'rope_scrap': {'name': 'Scrap of Bell Rope', 'kind': 'quest', 'col': '#c9a66b', 'desc': 'Frayed tower rope.'},
                      'mill_key': {'name': "Miller's Key", 'kind': 'key', 'col': '#d9d36b', 'desc': 'Stamped with a little mill-wheel.'}},
            'objects': objs, 'quests': quests, 'dialogs': dialogs, 'cutscenes': cut, 'triggers': triggers}


def _cinder_crypt():
    r = random.Random(808)
    objs = _wall(8, -44, 44, 0, 3.8) + _wall(-18, -44, 44, 0, 3.8)
    objs = [o for o in objs if abs(o['x']) < 42]
    spots = [(0, 34), (-6, 34), (5, 34), (0, 22), (-30, 22), (0, 8), (0, -18), (0, -30), (0, -6), (22, -2), (-22, -4)]
    scat = _clear(_scatter(r, 'ruin', 7, 36, 6) + _scatter(r, 'rock', 8, 40, 6) + _scatter(r, 'lamp', 6, 30), spots, 6)
    objs += scat
    objs += [_o('lamp', -3, 8), _o('lamp', 3, 8), _o('lamp', -3, -18), _o('lamp', 3, -18), _o('lamp', -4, 34), _o('lamp', 4, 30),
             _o('checkpoint', 0, 32), _o('checkpoint', 0, -4), _o('checkpoint', 0, -26),
             _o('npc', -7, 34, id='quarter', name='The Quartermaster', dialog='quarter', col='#8a6b4a',
                shop={'stock': [{'item': 'potion', 'price': 10}, {'item': 'elixir', 'price': 40, 'n': 2},
                                {'item': 'iron_blade', 'price': 60, 'n': 1}, {'item': 'leather', 'price': 35, 'n': 1}],
                      'sell': True, 'buyRate': 0.5}),
             _o('npc', 6, 34, id='mara', name='Mara the Sellsword', dialog='mara', col='#b04a3a', behavior='companion', speed=3.2, radius=3),
             _o('chest', 10, 36, gold=80), _o('chest', -38, 22, item='crypt_key'), _o('chest', 26, -2, item='warden_plate'),
             _o('chest', -26, -6, item='elixir'), _o('item', 14, 24, item='potion'),
             _o('door', 0, 8, 1, key='crypt_key', id='vault'), _o('door', 0, -18, 1, key='warden_seal', sealed=True, id='wardengate')]
    # room 1: the approach - a coward wisp, grunts, archers guard the key
    objs += [_o('enemy', x, z, 1, kind='grunt', behavior='guard', patrol=4) for x, z in ((-34, 24), (-40, 18))]
    objs += [_o('enemy', x, z, 1, kind='archer') for x, z in ((-36, 26), (-32, 18))]
    objs += [_o('enemy', 18, 20, 1, kind='wisp', behavior='coward', patrol=5), _o('enemy', -14, 20, 1, kind='grunt', patrol=5)]
    # room 2: patrols and the captain
    objs += [_o('enemy', -12, -2, 1, kind='grunt', behavior='patrol', path=[{'x': -12, 'z': -2}, {'x': 12, 'z': -2}]),
             _o('enemy', 12, -8, 1, kind='grunt', behavior='patrol', path=[{'x': 12, 'z': -8}, {'x': -12, 'z': -8}]),
             _o('enemy', 22, 0, 1, kind='archer'), _o('enemy', -22, -10, 1, kind='archer'),
             _o('enemy', 0, -12, 1.3, kind='brute', id='captain', name='The Crypt Captain', patrol=3)]
    objs += [_o('enemy', 0, -34, 1.2, kind='boss', id='warden', name='The Cinder Warden', patrol=2, col='#c0461d')]
    objs = _declutter(objs, scat, (0, 38))
    dialogs = {'quarter': {'start': 'a', 'nodes': {'a': {'speaker': 'Quartermaster', 'text': 'Supplies for the crypt? Everything has a price, and I buy loot too.'}}},
               'mara': {'start': 'a', 'nodes': {'a': {'speaker': 'Mara', 'text': 'Three coins says I outfight you. Hm, no - I will stay at your shoulder. Lead on.',
                                                       'do': {'flag': 'mara_joined'}, 'choices': [{'text': 'Welcome aboard.', 'end': True}]}}}}
    quests = [{'id': 'q_crypt', 'title': 'Break the Cinder Crypt', 'auto': True, 'final': True,
               'desc': 'Find the crypt key, defeat the Crypt Captain to open the warden gate, then the Cinder Warden.',
               'steps': [{'type': 'defeat', 'id': 'captain', 'text': 'Defeat the Crypt Captain'},
                         {'type': 'defeat', 'id': 'warden', 'text': 'Defeat the Cinder Warden'}],
               'reward': {'xp': 250, 'gold': 100}}]
    cut = {'warden_intro': {'name': 'The Warden wakes', 'steps': [
        {'t': 0, 'do': 'camera', 'x': 8, 'z': -22, 'h': 5, 'lx': 0, 'lz': -34, 'lh': 2.5, 'dur': 2},
        {'t': 0.5, 'do': 'sound', 'sound': 'boom', 'caption': 'a deep rumble'}, {'t': 0.5, 'do': 'shake', 'amount': 0.8},
        {'t': 1, 'do': 'say', 'speaker': 'The Cinder Warden', 'text': 'Who walks my ash?', 'secs': 2.4},
        {'t': 3.4, 'do': 'camera', 'x': 0, 'z': -24, 'h': 3, 'lx': 0, 'lz': -34, 'lh': 3, 'dur': 1.4},
        {'t': 4, 'do': 'say', 'speaker': 'Mara', 'text': 'Eyes up. Here he comes.', 'secs': 2}]}}
    triggers = [
        {'id': 'brief', 'when': {'type': 'start'}, 'then': [{'do': 'say', 'speaker': '', 'text': 'Find the crypt key in the west ruins. Spend gold at the Quartermaster first.', 'secs': 4.5}]},
        {'id': 'heat', 'when': {'type': 'enter', 'x': 0, 'z': 2, 'r': 5}, 'then': [{'do': 'sound', 'sound': 'boom', 'caption': 'distant thunder'}, {'do': 'say', 'speaker': '', 'text': 'The air grows hot.', 'secs': 2.5}]},
        {'id': 'captain', 'when': {'type': 'defeat', 'id': 'captain'},
         'then': [{'do': 'sound', 'sound': 'door', 'caption': 'stone grinds open'}, {'do': 'door', 'id': 'wardengate'}, {'do': 'shake', 'amount': 0.5},
                  {'do': 'say', 'speaker': '', 'text': 'The warden gate grinds open.', 'secs': 3}]},
        {'id': 'hall', 'when': {'type': 'enter', 'x': 0, 'z': -26, 'r': 5}, 'then': [{'do': 'cutscene', 'cutscene': 'warden_intro'}]},
        {'id': 'victory', 'when': {'type': 'defeat', 'id': 'warden'}, 'then': [{'do': 'achievement', 'achievement': 'crypt_cleared'}]}]
    return {'name': 'Cinder Crypt', 'mode': '3d', 'mood': 'Crimson Citadel', 'seed': 52, 'version': 2, 'size': 46, 'terrain': 0.5,
            'weather': 'embers', 'author': 'Stark World Engine', 'spawn': {'x': 0, 'z': 38}, 'difficulty': 'normal',
            'story': 'A dungeon crawl. Buy supplies, take Mara along, find the crypt key, break the captain, and wake the Cinder Warden.',
            'player': {'hp': 6, 'items': ['wood_sword']}, 'achievements': [
                {'id': 'crypt_cleared', 'title': 'Crypt Cleared', 'desc': 'Defeat the Cinder Warden.'},
                {'id': 'comrade', 'title': 'Comrades', 'desc': 'Recruit Mara.', 'when': {'type': 'flag', 'flag': 'mara_joined'}}],
            'items': {'crypt_key': {'name': 'Crypt Key', 'kind': 'key', 'col': '#ff9a3c', 'desc': 'Opens the vault door.'}},
            'objects': objs, 'quests': quests, 'dialogs': dialogs, 'cutscenes': cut, 'triggers': triggers}


def _pennant_run():
    r = random.Random(606)
    gates = []
    for k in range(9):
        a = math.radians(-90 + 360 * k / 9)
        gates.append((round(34 * math.cos(a), 1), round(26 * math.sin(a), 1)))
    start = (round(34 * math.cos(math.radians(-90 - 20)), 1), round(26 * math.sin(math.radians(-90 - 20)), 1))
    objs, spots = [], list(gates) + [start]
    for i, (x, z) in enumerate(gates):
        nx, nz = gates[(i + 1) % 9]
        spots.append((round((x + nx) / 2, 1), round((z + nz) / 2, 1)))
    scat = _clear(_scatter(r, 'tree', 46, 52, 4) + _scatter(r, 'rock', 14, 52, 4), spots, 5)
    objs += scat
    for x, z in gates:
        objs += [_o('lamp', round(x - 2.2, 1), z), _o('lamp', round(x + 2.2, 1), z)]
    objs += [_o('crystal', x, z) for x, z in gates]
    objs += [_o('enemy', gates[2][0], gates[2][1], 1, kind='grunt', behavior='patrol', path=[{'x': gates[2][0] - 8, 'z': gates[2][1] + 3}, {'x': gates[2][0] + 8, 'z': gates[2][1] - 3}]),
             _o('enemy', gates[5][0], gates[5][1], 1, kind='grunt', behavior='patrol', path=[{'x': gates[5][0] + 6, 'z': gates[5][1] - 6}, {'x': gates[5][0] - 6, 'z': gates[5][1] + 6}]),
             _o('enemy', gates[7][0], gates[7][1], 1, kind='wisp', behavior='coward', patrol=6),
             _o('npc', start[0] + 3, start[1] - 2, id='starter', name='Race Marshal', dialog='starter', col='#d9a441'),
             _o('checkpoint', start[0], start[1] + 3)]
    objs = _declutter(objs, scat, start)
    steps = [{'type': 'reach', 'x': x, 'z': z, 'r': 4, 'label': f'gate {i + 1}', 'text': f'Run through gate {i + 1} of 9'} for i, (x, z) in enumerate(gates)]
    quests = [{'id': 'q_run', 'title': 'Pennant Run', 'auto': True, 'final': True,
               'desc': 'Run through all nine gates, in order, as fast as you can.', 'steps': steps, 'reward': {'xp': 100, 'gold': 50}}]
    triggers = [{'id': 'go', 'when': {'type': 'start'}, 'then': [{'do': 'say', 'speaker': 'Race Marshal', 'text': 'Nine gates, in order. The clock is running - go!', 'secs': 3.5}, {'do': 'sound', 'sound': 'portal', 'caption': 'a starting horn'}]}]
    for i, (x, z) in enumerate(gates):
        triggers.append({'id': f'gate{i + 1}', 'when': {'type': 'enter', 'x': x, 'z': z, 'r': 4},
                         'then': [{'do': 'sound', 'sound': 'coin', 'caption': f'gate {i + 1}'}]})
    dialogs = {'starter': {'start': 'a', 'nodes': {'a': {'speaker': 'Race Marshal', 'text': 'Swift Boots on, eyes up. Lamps mark each gate; follow the gold beam. Slimes may wander across the course.'}}}}
    return {'name': 'Pennant Run', 'mode': '3d', 'mood': 'Alpine Sunset', 'seed': 19, 'version': 2, 'size': 56, 'terrain': 1.2,
            'weather': 'pollen', 'author': 'Stark World Engine', 'spawn': {'x': start[0], 'z': start[1]}, 'scoring': 'time', 'showTimer': True,
            'story': 'A time trial on foot. Run through all nine gates in order and beat your best time - your fastest runs are kept in the high-score table.',
            'player': {'hp': 8, 'items': ['swift_boots']}, 'achievements': [
                {'id': 'quick', 'title': 'Quick Feet', 'desc': 'Finish in under 100 seconds.', 'when': {'type': 'time', 'n': 100}},
                {'id': 'blazing', 'title': 'Blazing', 'desc': 'Finish in under 70 seconds.', 'when': {'type': 'time', 'n': 70}}],
            'objects': objs, 'quests': quests, 'dialogs': dialogs, 'triggers': triggers}


def day_one_templates():
    """Three more starter games built only with the studio's own tools (logic blocks, cutscenes, behaviours, shops)."""
    return [_mystery_village(), _cinder_crypt(), _pennant_run()]


# ---------------- procedural generator ("Make me a game") ----------------
THEMES = {
    'Spooky Swamp': {'mood': 'Bioluminescent Twilight', 'water': -0.6, 'terrain': 1.0, 'weather': 'auto',
                     'foes': ['wisp', 'grunt', 'archer'], 'flora': (1.0, 0.5, 0.3), 'boss': 'The Bog Hag',
                     'lv': ['Glowmire', 'Hollow Reeds', 'Lantern Marsh', 'Witchwood', 'Mirefall', 'The Drowned Bell'],
                     'loot': ('moonpetal', 'Moonpetal', '#c9b6ff')},
    'Sky Islands': {'mood': 'Alpine Sunset', 'water': None, 'terrain': 2.8, 'weather': 'pollen',
                    'foes': ['grunt', 'archer', 'wisp'], 'flora': (0.8, 0.3, 0.6), 'boss': 'The Storm Roc',
                    'lv': ['Cloudmeadow', 'Windstep Ridge', 'Kite Hollow', 'Sunspire', 'Featherfall', 'The Highest Isle'],
                    'loot': ('sky_feather', 'Sky Feather', '#ffe8a3')},
    'Frozen Kingdom': {'mood': 'Sub-Zero Aurora', 'water': -0.9, 'terrain': 1.8, 'weather': 'snow',
                       'foes': ['grunt', 'brute', 'archer'], 'flora': (0.7, 0.6, 0.4), 'boss': 'The Frost Jarl',
                       'lv': ['Rimeholt', 'Glacier Gate', 'Aurora Steppe', 'Icefang Pass', 'Winterhold Ruins',
                              'The Frozen Throne'], 'loot': ('frost_lily', 'Frost Lily', '#bff0ff')},
    'Lava Fortress': {'mood': 'Crimson Citadel', 'water': None, 'terrain': 1.5, 'weather': 'embers',
                      'foes': ['brute', 'grunt', 'archer'], 'flora': (0.35, 0.9, 0.8), 'boss': 'The Cinder King',
                      'lv': ['Ashgate', 'Ember Fields', 'Slagworks', 'Obsidian Hall', 'Magma Steps', 'The Cinder Throne'],
                      'loot': ('ember_shard', 'Ember Shard', '#ff9a3c')},
    'Sunken City': {'mood': 'Sunken Ruins', 'water': 0.2, 'terrain': 1.6, 'weather': 'bubbles',
                    'foes': ['grunt', 'archer', 'wisp'], 'flora': (0.6, 1.0, 0.4), 'boss': 'The Tide Colossus',
                    'lv': ['Coral Steps', 'Pearl Market', 'Kelp Cathedral', 'Old Harbour', 'The Abyss Arch',
                           'The Drowned Palace'], 'loot': ('sea_glass', 'Sea Glass', '#9ff7e8')},
    'Cozy Village': {'mood': 'Alpine Sunset', 'water': -0.8, 'terrain': 0.8, 'weather': 'clear',
                     'foes': ['grunt', 'wisp'], 'flora': (1.0, 0.3, 0.2), 'boss': 'The Pumpkin Golem',
                     'lv': ['Honeybrook', 'Applewood', 'Millpond', 'Sunny Hills', 'Harvest Green', 'The Old Orchard'],
                     'loot': ('honeycomb', 'Honeycomb', '#ffcf4a')},
}
SIZES = {'snack': {'levels': 1, 'size': 44, 'quests': 3}, 'evening': {'levels': 3, 'size': 56, 'quests': 4},
         'epic': {'levels': 6, 'size': 72, 'quests': 5}}
_SYL = ['al', 'bri', 'cor', 'da', 'el', 'fen', 'gwy', 'ha', 'is', 'jo', 'ka', 'li', 'mo', 'nel', 'or', 'pim', 'qua',
        'ro', 'sa', 'tor', 'ul', 'vi', 'wen', 'ya', 'zed']
_JOBS = ['the Baker', 'the Smith', 'the Scout', 'the Herbalist', 'the Fisher', 'the Bard', 'the Hunter', 'the Miller']
_GEAR = ['wood_sword', 'leather', 'iron_blade', 'swift_boots', 'warden_plate', 'ember_charm', 'moon_blade']


def _name(r):
    return (r.choice(_SYL) + r.choice(_SYL)).capitalize()


def terrain_h(x, z, seed, amp):
    """Same height field as terrainH() in ENGINE_JS (the generator keeps quest spots out of deep water)."""
    s = seed or 0
    v = (math.sin(x * 0.13 + s) * 0.6 + math.cos(z * 0.11 + s * 0.7) * 0.6 + math.sin((x + z) * 0.05 + s) * 0.8
         + math.cos(x * 0.045 - z * 0.06 + s * 0.4) * 0.9 + math.sin(x * 0.29 + z * 0.23 + s * 1.3) * 0.18)
    return amp * v * 0.55


class _Walk:
    """Where the hero can walk on a generated 3D level: the runtime refuses steps into water deeper than 1.3 m, so
    every spot the player must reach has to be on ground connected to the spawn point (1 m flood fill)."""

    def __init__(self, seed, size, amp, water):
        self.seed, self.size, self.amp, self.water = seed, size, amp, water
        self.lim = size - 1.5
        self.cells, self.list = set(), []

    def depth(self, x, z):
        return -9.0 if self.water is None else self.water - terrain_h(x, z, self.seed, self.amp)

    def fill(self, x, z):
        start = (int(round(x)), int(round(z)))
        todo, self.cells = [start], {start}
        while todo:
            ci, cj = todo.pop()
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                c = (ci + di, cj + dj)
                if c not in self.cells and abs(c[0]) <= self.lim and abs(c[1]) <= self.lim and self.depth(*c) <= 1.2:
                    self.cells.add(c)
                    todo.append(c)
        self.list = sorted(self.cells)

    def ok(self, x, z, dry=0.5):
        """Walkable, connected to the spawn and at most `dry` metres under water."""
        return ((int(round(x)), int(round(z))) in self.cells and self.depth(x, z) <= dry
                and abs(x) <= self.lim and abs(z) <= self.lim)


# ---- authored voices: every quest giver gets a temperament, a habit of speech, a motive and a small secret ----
_VOICES = [
    {'tone': 'gruff', 'hello': ['Hrm. You look like you can swing a blade.', 'Mind the mud. Who are you, then?',
                                'Another wanderer. Fine. Listen.'],
     'yes': ['Good. Do not make me regret it.', 'Then go. Daylight is wasting.'],
     'wait': ['Still standing here? {wait}', 'Not done? {wait}'], 'thanks': 'Well. That was better than I hoped.'},
    {'tone': 'cheerful', 'hello': ['Oh! A visitor! Nobody visits!', 'Hello hello! Is that a real adventurer?',
                                   'You have kind boots. I can tell these things.'],
     'yes': ['You are a treasure. A walking treasure!', 'I knew it the moment I saw your boots.'],
     'wait': ['No rush! Well, a little rush. {wait}', 'How is it going? {wait}'], 'thanks': 'You did it! I am going to tell everyone.'},
    {'tone': 'nervous', 'hello': ['Oh - sorry - you startled me.', 'Is it safe? Are you... safe?',
                                  'Please keep your voice down, the walls listen here.'],
     'yes': ['Thank you. Truly. I will light a candle for you.', 'Be careful. Please be careful.'],
     'wait': ['I keep hearing noises. {wait}', 'Any luck? {wait}'], 'thanks': 'You came back. I was so sure you would not.'},
    {'tone': 'poetic', 'hello': ['The wind brought you here, I think.', 'Ah, a stranger, and the evening only just begun.',
                                 'Sit a moment. The world is louder than it needs to be.'],
     'yes': ['Then the road has chosen you as well.', 'Go softly, and come back with stories.'],
     'wait': ['The day turns slowly. {wait}', 'Patience is a kind of map. {wait}'], 'thanks': 'There. Now the story has an ending I can live with.'},
    {'tone': 'wry', 'hello': ['Let me guess. You are here to help. Everyone is, until the slimes show up.',
                              'Welcome to the most exciting place on the map. That was a joke.',
                              'You are not from around here. Lucky you.'],
     'yes': ['Brave or bored? Either works for me.', 'Try not to get eaten. Paperwork.'],
     'wait': ['I can see you are busy standing still. {wait}', 'Any day now. {wait}'], 'thanks': 'Huh. You actually did it. I owe you a drink and an apology.'},
]
_MOTIVES = {
    'gather': [('my granny swears they cure her cough', 'Granny is up and arguing again. Thank you.'),
               ('the festival lanterns need them, and the festival is tomorrow',
                'Turns out they were for a surprise festival - for YOU. Act surprised.'),
               ('I promised my sister, and she never forgets a promise', 'My sister says you are allowed to visit. That is rare.')],
    'hunt': [('they trampled my garden twice this week', 'The garden is quiet. The carrots thank you.'),
             ('my little brother wants to play by the river again', 'He is down at the river already. Of course he is.'),
             ('someone has been feeding them, and I want to know who',
              'The tracks led back here. The miller was feeding them scraps. We are having words.')],
    'crystals': [('without their light the night goes on far too long', 'Look - the lamps burn bluer already.'),
                 ('I think they are pieces of something that broke', 'Fit together, they hum one note. Something is listening.'),
                 ('a traveller paid me in one once, and I never forgot the glow', 'I will keep one by the window. For the next traveller.')],
    'escort': [('we argued, and they walked off before I could say sorry', 'We talked. I said sorry. They did too.'),
               ('they know the old songs, and the village is too quiet without them', 'Listen - singing. The village sounds right again.'),
               ('they went looking for a lost goat', 'The goat came home on its own an hour ago. Do not tell them.')],
    'explore': [('my father drew a map of it and never came back to finish it', 'You saw it with your own eyes. I will finish his map tonight.'),
                ('people say lights move there at night', 'Moving lights... and you came back. That is the strangest part.'),
                ('I need to know if it is still standing', 'Still standing. Then so are we.')],
}


def _voice(r):
    return r.choice(_VOICES)


def _authored(r, kind, nm, offer, wait):
    """-> (hello, offer, accept, wait, done) in one character's voice, with a reason and a small twist at the end."""
    v = _voice(r)
    why, twist = r.choice(_MOTIVES.get(kind, _MOTIVES['explore']))
    hello = r.choice(v['hello'])
    offer = f'{offer} I ask because {why}.'
    accept = r.choice(['I will help.', 'Leave it to me.', 'Consider it done.', 'Why not? Point me the right way.'])
    return hello, offer, accept, r.choice(v['wait']).format(wait=wait), f'{v["thanks"]} {twist}', r.choice(v['yes'])


def _gen_level3d(r, th, i, nlev, size, nq, final, lseed=0, amp=1.0, water=None):
    """One 3D level: village with quest givers, camps, a gated shrine with a key, scattered loot, portal or boss.
    Every spot the player must reach (village, givers, camps, loot, key, gate, portal, boss) is on dry ground that is
    connected to the spawn point."""
    objs, quests, dialogs, spots = [], [], {}, []
    lid = f'L{i + 1}'
    tf, rf, ruf = th['flora']
    wk = _Walk(lseed, size, amp, water)
    vx, vz = r.uniform(-6, 6), r.uniform(-6, 6)
    if water is not None:      # the village (spawn, givers, checkpoint) goes on the nearest dry ground to the centre
        ring = [(0, 0)] + [(math.cos(k / 8 * math.tau) * d, math.sin(k / 8 * math.tau) * d) for d in (3, 6)
                           for k in range(8)]
        best = None
        for rad in range(0, int(size * 0.7), 2):
            for k in range(16):
                a = k / 16 * math.tau
                cx, cz = vx + math.cos(a) * rad, vz + math.sin(a) * rad
                if abs(cx) < size - 12 and abs(cz) < size - 12 and \
                        all(wk.depth(cx + dx, cz + dz) <= 0.2 for dx, dz in ring):
                    best = (cx, cz)
                    break
            if best:
                break
        vx, vz = best or (vx, vz)
    spawn = {'x': round(vx, 1), 'z': round(vz + 6, 1)}
    wk.fill(spawn['x'], spawn['z'])
    if len(wk.cells) < 50:                                 # hopeless terrain: fall back to the open layout
        wk.water = None
        wk.fill(spawn['x'], spawn['z'])
    for k in range(r.randint(3, 5)):
        a = k / 5 * math.tau + r.random() * 0.4
        objs.append(_o('house', round(vx + math.cos(a) * 15, 1), round(vz + math.sin(a) * 15, 1), round(r.uniform(0.85, 1.15), 2),
                       rot=round(-a + math.pi / 2, 2)))
    objs += [_o('lamp', round(vx + math.cos(a) * 6, 1), round(vz + math.sin(a) * 6, 1)) for a in (0.5, 2.6, 4.5)]
    objs.append(_o('checkpoint', round(vx, 1), round(vz + 3, 1)))
    spots.append((vx, vz))
    spots += [(o['x'], o['z']) for o in objs if o['t'] == 'house']     # nothing to collect inside a house
    far = size * 0.72

    def farpt(minr=size * 0.35, also=None):
        for _ in range(400):
            x, z = r.uniform(-far, far), r.uniform(-far, far)
            if math.hypot(x - vx, z - vz) > minr and all(math.hypot(x - a, z - b) > 9 for a, b in spots) \
                    and wk.ok(x, z) and (also is None or wk.ok(x + also[0], z + also[1])):
                spots.append((x, z))
                return round(x, 1), round(z, 1)
        for _ in range(4000):                               # crowded or wet level: any reachable dry spot
            x, z = r.choice(wk.list)
            if math.hypot(x - vx, z - vz) > minr * 0.5 and wk.ok(x, z) and \
                    (also is None or wk.ok(x + also[0], z + also[1])):
                spots.append((x, z))
                return float(x), float(z)
        x, z = r.choice(wk.list)
        return float(x), float(z)

    def near(x, z, spread):
        """A reachable spot around (x, z) (camp members, the chest next to a camp)."""
        for _ in range(40):
            nx, nz = round(x + r.uniform(-spread, spread), 1), round(z + r.uniform(-spread, spread), 1)
            if wk.ok(nx, nz, 0.9):
                return nx, nz
        return x, z
    # enemy camps
    foes = th['foes']
    for c in range(3 + i // 2 + (1 if size > 60 else 0)):
        cx, cz = farpt()
        objs.append(_o('ruin', cx, cz, round(r.uniform(0.9, 1.3), 2)))
        for e in range(r.randint(2, 3) + i // 3):
            k = r.choice(foes)
            ex, ez = near(cx, cz, 5)
            objs.append(_o('enemy', ex, ez, 1, kind=k, hp=ENEMY_HP[k] + i, patrol=5))
        hx, hz = (round(cx + 2, 1), round(cz - 2, 1)) if wk.ok(cx + 2, cz - 2) else near(cx, cz, 2.5)
        objs.append(_o('chest', hx, hz, item=r.choice(['potion', 'gem', 'potion']), gold=10 + 5 * i))
    # quests
    loot_id, loot_name, loot_col = th['loot']
    givers = []

    def giver():
        n = len(givers)
        a = n * 1.4 + 0.7
        nid = f'{lid.lower()}_npc{n + 1}'
        nm = f'{_name(r)} {r.choice(_JOBS)}'
        objs.append(_o('npc', round(vx + math.cos(a) * 5, 1), round(vz + math.sin(a) * 5, 1), id=nid, name=nm,
                       dialog=nid, col='#%02x%02x%02x' % (r.randint(60, 230), r.randint(60, 230), r.randint(60, 230))))
        givers.append(nid)
        return nid, nm
    gear = _GEAR[min(len(_GEAR) - 1, i)]
    kinds = ['gather', 'hunt', 'crystals', 'escort', 'explore']
    r.shuffle(kinds)
    for qn, kind in enumerate(kinds[:nq - 1]):
        qid = f'{lid.lower()}_q{qn + 1}'
        nid, nm = giver()
        rew = {'xp': 40 + 25 * i + 10 * qn, 'gold': 15 + 8 * i}
        if qn == 0:
            rew['items'] = [gear]
        if kind == 'gather':
            n = r.randint(4, 6)
            for _ in range(n + 1):
                x, z = farpt(size * 0.2)
                objs.append(_o('item', x, z, item=loot_id))
            q = {'title': f'{loot_name} Gathering', 'desc': f'{nm} needs {n} {loot_name.lower()}s.',
                 'steps': [{'type': 'collect', 'item': loot_id, 'count': n, 'consume': True},
                           {'type': 'talk', 'npc': nid, 'text': f'Bring them to {nm}'}]}
            say = (f'My stores of {loot_name.lower()} are empty. Could you find {n} of them out in the wilds?',
                   f'Gather {loot_name.lower()}s', f'Still {n} {loot_name.lower()}s, friend. They glow, you cannot miss them.')
        elif kind == 'hunt':                 # only ask for monsters the camps on this level really have
            have = {}
            for o in objs:
                if o['t'] == 'enemy':
                    have[o['kind']] = have.get(o['kind'], 0) + 1
            k = max(sorted(have), key=lambda kk: have[kk]) if have else r.choice(foes)
            n = max(1, min(r.randint(3, 5), have.get(k, 3)))
            q = {'title': f'Thin the {ENEMY_NAMES[k]}s', 'desc': f'{ENEMY_NAMES[k]}s are bothering {nm}.',
                 'steps': [{'type': 'defeat', 'kind': k, 'count': n}]}
            say = (f'{ENEMY_NAMES[k]}s keep raiding the village. Defeat {n} of them?', f'Hunt {ENEMY_NAMES[k]}s',
                   'The camps are marked on your map - red dots.')
        elif kind == 'crystals':
            n = r.randint(5, 8)
            for _ in range(n):
                x, z = farpt(size * 0.15)
                objs.append(_o('crystal', x, z))
            q = {'title': 'Lights of the Land', 'desc': f'Collect {n} glowing crystals for {nm}.',
                 'steps': [{'type': 'collect', 'item': 'crystal', 'count': n}, {'type': 'talk', 'npc': nid}]}
            say = (f'Crystals have fallen all over the land. Bring back {n}?', 'Collect crystals', 'They hum. Listen!')
        elif kind == 'escort':
            x, z = farpt(size * 0.45)
            lost = f'{lid.lower()}_lost'
            lname = _name(r)
            objs.append(_o('npc', x, z, id=lost, name=lname, dialog=lost, col='#e09f3e'))
            dialogs[lost] = {'start': 'a', 'nodes': {'a': {'speaker': lname, 'text': 'Oh thank goodness! Please take '
                                                           'me home - stay close!', 'choices': [{'text': 'Follow me.',
                                                                                                 'end': True}]}}}
            q = {'title': f'Bring {lname} Home', 'desc': f'{lname} is lost in the wilds.',
                 'steps': [{'type': 'talk', 'npc': lost, 'text': f'Find {lname}'},
                           {'type': 'escort', 'npc': lost, 'x': round(vx, 1), 'z': round(vz, 1), 'r': 6,
                            'label': 'the village'}]}
            say = (f'My friend {lname} wandered off and never came back. Please find them!', f'Find {lname}',
                   f'Have you found {lname}? They went somewhere far from here.')
        else:
            x, z = farpt(size * 0.5)
            objs.append(_o('ruin', x, z, 1.4))
            objs.append(_o('lamp', round(x + 3, 1), z))
            lab = r.choice(['the Old Watchtower', 'the Singing Stones', 'the Forgotten Shrine', 'the Giant\'s Seat'])
            q = {'title': 'Into the Unknown', 'desc': f'{nm} wants news of {lab}.',
                 'steps': [{'type': 'reach', 'x': x, 'z': z, 'r': 5, 'label': lab, 'level': i},
                           {'type': 'talk', 'npc': nid, 'text': f'Tell {nm} what you saw'}]}
            say = (f'Nobody has been to {lab} for years. Will you look?', 'Explore', f'Follow the gold beam to {lab}.')
        q.update({'id': qid, 'giver': nid, 'reward': rew})
        quests.append(q)
        hello, offer, accept, wait, done, yes = _authored(r, kind, nm, say[0], say[2])
        d = _offer(nid, nm, hello, offer, accept, qid, wait, done)
        d[nid]['nodes']['yes']['text'] = yes
        dialogs.update(d)
    # main quest: key, gate, portal or final boss
    main = f'{lid.lower()}_main'
    gx, gz = farpt(size * 0.55, None if final else (0, 6))
    spots.append((gx, gz + 6))
    if final:
        objs.append(_o('enemy', gx, gz, 1, kind='boss', id=f'{lid.lower()}_boss', name=th['boss'], patrol=3, hp=36 + 6 * i))
        objs += [_o('ruin', round(gx + dx, 1), round(gz + dz, 1), 1.3) for dx, dz in ((-8, -6), (8, -6), (0, 9))]
        if wk.ok(gx, gz + 12):
            objs.append(_o('checkpoint', round(gx, 1), round(gz + 12, 1)))
        quests.append({'id': main, 'title': f'Face {th["boss"]}', 'auto': True, 'final': True,
                       'desc': f'{th["boss"]} rules this land. End its reign.',
                       'steps': [{'type': 'defeat', 'id': f'{lid.lower()}_boss', 'text': f'Defeat {th["boss"]}'}],
                       'reward': {'xp': 300, 'gold': 150}})
    else:
        kx, kz = farpt(size * 0.4)
        objs.append(_o('chest', kx, kz, item='key', gold=20))
        wx, wz = (round(kx + 3, 1), round(kz + 3, 1)) if wk.ok(kx + 3, kz + 3) else near(kx, kz, 3)
        objs.append(_o('enemy', wx, wz, 1.25, kind='brute', id=f'{lid.lower()}_warden',
                       name='Gate Warden', hp=8 + 2 * i, patrol=3))
        objs.append(_o('door', gx, gz, key='key'))
        objs.append(_o('portal', gx, round(gz + 6, 1), to='next'))
        quests.append({'id': main, 'title': 'The Way Onward', 'auto': True,
                       'desc': 'A sealed gate guards the portal to the next land. The Gate Warden keeps its key.',
                       'steps': [{'type': 'defeat', 'id': f'{lid.lower()}_warden', 'text': 'Defeat the Gate Warden (his chest holds the gate key)'},
                                 {'type': 'reach', 'x': gx, 'z': round(gz + 6, 1), 'r': 3, 'level': i,
                                  'label': 'the portal beyond the gate'}],
                       'reward': {'xp': 80 + 30 * i}})
    # scenery
    def scatter(kind, n, sc=(0.8, 1.4)):
        for _ in range(n):
            x, z = r.uniform(-size * 0.95, size * 0.95), r.uniform(-size * 0.95, size * 0.95)
            if all(math.hypot(x - a, z - b) > 5 for a, b in spots) and math.hypot(x - vx, z - vz) > 14:
                objs.append(_o(kind, round(x, 1), round(z, 1), round(r.uniform(*sc), 2)))
    area = (size / 48.0) ** 2
    scatter('tree', int(45 * tf * area))
    scatter('rock', int(30 * rf * area))
    scatter('ruin', int(6 * ruf * area))
    scatter('lamp', int(6 * area))
    for _ in range(3):
        x, z = farpt(size * 0.25)
        objs.append(_o('item', x, z, item='potion'))
    return {'objects': objs, 'quests': quests, 'dialogs': dialogs, 'spawn': spawn}


ENEMY_HP = {'grunt': 3, 'archer': 2, 'brute': 8, 'wisp': 2, 'boss': 40}
ENEMY_NAMES = {'grunt': 'Slime', 'archer': 'Thorn Archer', 'brute': 'Stone Brute', 'wisp': 'Wisp', 'boss': 'Warden'}


def generate_world(seed=None, theme='Spooky Swamp', size='evening', name=None, mode='3d'):
    """-> a complete, playable world/campaign (validated). size: snack | evening | epic. mode '2d' = platformer run."""
    seed = int(seed if seed is not None else random.randrange(1, 10 ** 6))
    th = THEMES.get(theme) or THEMES['Spooky Swamp']
    sz = SIZES.get(size) or SIZES['evening']
    r = random.Random(seed)
    title = name or f'{theme}: {r.choice(th["lv"])}'
    lv = th['lv'][:]
    r.shuffle(lv)
    levels = []
    n = sz['levels']
    for i in range(n):
        final = i == n - 1
        if mode == '2d' or (n >= 3 and i == n // 2 and not final):
            tiles = platformer_tiles(seed + i * 101, 70 + 20 * min(i, 3), 15, boss=final, keydoor=not final,
                                     water=th['water'] is not None, density=1 + i * 0.1)
            L = {'name': lv[i % len(lv)] + (' Run' if mode != '2d' else ''), 'mode': '2d', 'mood': th['mood'],
                 'seed': seed + i, 'tiles': tiles, 'objects': [],
                 'story': 'A side-scrolling dash! Grab the key, open the door, find the swirling exit.'}
            # a villager by the start line with a pearl quest, so 2D games have people and quests too
            py = next((y for y, row in enumerate(tiles) if 'P' in row), len(tiles) - 3)
            pxx = tiles[py].index('P') if 'P' in tiles[py] else 3
            pearls = ''.join(tiles).count('o')
            nid, nm = f'l{i + 1}_npc1', f'{_name(r)} {r.choice(_JOBS)}'
            L['objects'] = [_o('npc', pxx + 3, py, id=nid, name=nm, dialog=nid,
                               col='#%02x%02x%02x' % (r.randint(60, 230), r.randint(60, 230), r.randint(60, 230)))]
            L['quests'] = []
            if pearls >= 3:
                want = max(3, min(10, pearls * 2 // 3))
                qid = f'l{i + 1}_pearls'
                L['quests'].append({'id': qid, 'title': 'Pearls for the Lamps', 'giver': nid,
                                    'desc': f'{nm} wants {want} glowing pearls from this run.',
                                    'steps': [{'type': 'collect', 'item': 'crystal', 'count': want,
                                               'text': f'Collect {want} pearls'}],
                                    'reward': {'xp': 40 + 20 * i, 'gold': 15 + 5 * i,
                                               'items': [_GEAR[min(len(_GEAR) - 1, i)]]}})
                L['dialogs'] = _offer(nid, nm, r.choice(['Oh, a runner!', 'Well met, traveller!', 'Hello there!']),
                                      f'The lamps went dark. Could you bring me {want} glowing pearls from along '
                                      f'the way?', 'I will collect them.', qid, 'Pearls glow - grab them as you run.',
                                      'Lovely! The lamps shine again. Take this.')
            else:
                L['dialogs'] = {nid: {'start': 'a', 'nodes': {'a': {'speaker': nm, 'text': 'Run fast, jump high '
                                                                    'and mind the spikes!'}}}}
            if final:
                L['quests'] += [{'id': f'l{i + 1}_main', 'title': f'Face {th["boss"]}', 'auto': True, 'final': True,
                                'desc': 'Stomp the boss at the end of the run.',
                                'steps': [{'type': 'defeat', 'kind': 'boss', 'text': f'Defeat {th["boss"]}'}],
                                'reward': {'xp': 250, 'gold': 120}}]
        else:
            g = _gen_level3d(r, th, i, n, sz['size'] + 4 * i, sz['quests'], final, seed + i * 7, th['terrain'],
                             th['water'])
            L = {'name': lv[i % len(lv)], 'mode': '3d', 'mood': th['mood'], 'seed': seed + i * 7,
                 'size': sz['size'] + 4 * i, 'terrain': th['terrain'], 'weather': th['weather'],
                 'story': f'Chapter {i + 1}: {lv[i % len(lv)]}. Talk to the villagers (gold !) to find work, '
                          f'then follow the gold beam.', 'day': {'start': round(r.uniform(0.3, 0.7), 2),
                                                                'len': 480}}
            if th['water'] is not None:
                L['water'] = th['water']
            L.update(g)
        levels.append(L)
    w = levels[0]
    w.update({'name': title[:120], 'version': 2, 'author': 'you', 'difficulty': 'easy' if theme == 'Cozy Village' else 'normal',
              'items': {th['loot'][0]: {'name': th['loot'][1], 'kind': 'quest', 'col': th['loot'][2],
                                        'desc': 'Someone in the village wants these.'}}})
    w['story'] = (f'A {theme.lower()} adventure in {n} chapter{"s" if n > 1 else ""}. ' + w.get('story', ''))[:4000]
    if n > 1:
        w['levels'] = levels[1:]
    return validate_world(_gs_tag(w))


def seed_worlds(games=None):
    games = games or GAMES
    made = []
    for w in starter_worlds():
        rel = f'{slug(w["name"])}/world.json'
        if not os.path.exists(safe_path(rel, games)):
            write_versioned(rel, json.dumps(w, indent=1).encode('utf-8'), games)
            made.append(w['name'])
    return made


def list_worlds(games=None):
    out = []
    for p in sorted(glob.glob(os.path.join(glob.escape(games or GAMES), '*', 'world.json'))):
        try:
            with open(p, encoding='utf-8') as f:
                w = json.load(f)
            out.append({'name': w.get('name', ''), 'slug': os.path.basename(os.path.dirname(p)),
                        'mode': w.get('mode', '3d'), 'mood': w.get('mood', MOODS[0]),
                        'levels': 1 + len(w.get('levels') or [])})
        except (OSError, ValueError):
            pass
    return out


def load_world(name, games=None):
    with open(safe_path(f'{slug(name)}/world.json', games), encoding='utf-8') as f:
        return json.load(f)


# ---------------- exporters ----------------
def _cart_paths(world):
    """Every bundled art file the 3D engine may load for this world (mirrors engine3d/60_world.js choices)."""
    looks = load_looks()
    cyc = ['hdri/kloofendal_48d_partly_cloudy_puresky_1k.hdr', 'hdri/kloppenheim_06_puresky_1k.hdr',
           'hdri/kloppenheim_02_puresky_1k.hdr']
    ground = [f'ground/{g}/{g}_{m}_1k.jpg' for g in ('forest_ground_04', 'rocky_terrain_02', 'coast_sand_01', 'snow_02')
              for m in ('diff', 'nor_gl', 'arm')]
    props = [f'ground/castle_brick_07/castle_brick_07_{m}_1k.jpg' for m in ('diff', 'nor_gl', 'arm')] + \
        ['textures/village/T_WoodTrim_BaseColor.jpg', 'textures/village/T_WoodTrim_Normal.jpg']
    scatter = ['Grass_Common_Short', 'Grass_Common_Tall', 'Grass_Wispy_Short', 'Grass_Wispy_Tall', 'Flower_3_Group',
               'Flower_4_Group', 'Fern_1', 'Plant_7', 'Bush_Common', 'Bush_Common_Flowers', 'Mushroom_Common',
               'Mushroom_Laetiporus', 'Clover_1', 'Clover_2', 'Pebble_Round_1', 'Pebble_Round_2', 'Pebble_Round_3',
               'Pebble_Square_1', 'Pebble_Square_2', 'RockPath_Round_Small_1', 'RockPath_Round_Small_2',
               'RockPath_Round_Small_3']
    rocks = ['Rock_Medium_1', 'Rock_Medium_2', 'Rock_Medium_3']
    house = ['Wall_Plaster_Window_Wide_Round', 'Wall_Plaster_Door_Round', 'Wall_Plaster_WoodGrid',
             'Wall_Plaster_Window_Thin_Round', 'Wall_UnevenBrick_Window_Wide_Round', 'Wall_UnevenBrick_Door_Round',
             'Wall_UnevenBrick_Straight', 'Wall_UnevenBrick_Window_Thin_Round', 'Roof_RoundTiles_4x4', 'Roof_Front_Brick4',
             'Prop_Chimney', 'Prop_Chimney2', 'Door_1_Round', 'Door_2_Round', 'Window_Wide_Round1', 'Window_Thin_Round1',
             'WindowShutters_Wide_Round_Open', 'WindowShutters_Thin_Round_Open', 'Prop_Vine1', 'Prop_Vine2',
             'Corner_Exterior_Wood', 'Prop_Crate']
    models, files = set(), set(props)
    levels = [world] + [L for L in (world.get('levels') or []) if isinstance(L, dict)]
    body = 'female' if (world.get('player') or {}).get('body') == 'female' else 'male'
    for L in levels:
        if L.get('mode') == '2d':
            continue
        lk = looks.get(L.get('mood')) or looks.get('Alpine Sunset') or {}
        day = isinstance(L.get('day'), dict) and float(L['day'].get('len') or 0) > 0
        files.update(cyc if day else ([lk['hdri']] if lk.get('hdri') else []))
        files.update(ground)
        models.update('nature/' + k for k in (lk.get('trees') or {}))
        models.update('nature/' + k for k in scatter + rocks)
        models.add(f'characters/{body}_adventurer')
        for o in L.get('objects') or []:
            t = o.get('t')
            if t == 'house':
                models.update('village/' + k for k in house)
            elif t == 'ruin':
                models.update(['village/Prop_Brick1', 'village/Prop_Brick2', 'village/Prop_Brick3', 'village/Prop_Vine4',
                               'village/Prop_Vine5'])
            elif t == 'door':
                models.update(['village/DoorFrame_Round_Brick', 'village/Door_1_Round'])
            elif t == 'npc':
                models.add('characters/' + _npc_look(o))
            elif t == 'enemy' and o.get('kind') == 'archer':
                models.update(['characters/male_bandit', 'characters/female_bandit'])
            elif t == 'asset' and o.get('asset'):
                models.add(str(o['asset']))
    libfiles = set()
    for m in models:
        if m.startswith('lib:'):
            libfiles.add(m)
            p = os.path.join(user_assets_dir(), m[4:])
            if os.path.isfile(p):
                for ref in _model_refs_at(p):
                    libfiles.add('lib:' + os.path.normpath(os.path.join(os.path.dirname(m[4:]), ref)).replace(os.sep, '/'))
            continue
        cat, _, name = m.partition('/')
        rel = f'models/{cat}/{name}' + ('.gltf' if cat == 'characters' else '.glb')
        files.add(rel)
        files.update(_model_refs(rel))
    return sorted(f for f in files if os.path.isfile(os.path.join(ASSETS_DIR, f))) +         sorted(f for f in libfiles if os.path.isfile(os.path.join(user_assets_dir(), f[4:])))


def _model_refs_at(p):
    """Relative URIs (images, buffers) inside one .glb/.gltf file."""
    try:
        with open(p, 'rb') as f:
            data = f.read()
        j = json.loads(data[20:20 + int.from_bytes(data[12:16], 'little')]) if data[:4] == b'glTF' else json.loads(data)
    except (OSError, ValueError):
        return []
    return [urllib.parse.unquote(x['uri']) for x in (j.get('images') or []) + (j.get('buffers') or [])
            if x.get('uri') and not x['uri'].startswith('data:')]


def _npc_look(o):
    jobs = {'baker': 'baker', 'smith': 'guard', 'scout': 'adventurer', 'herbalist': 'elder', 'fisher': 'farmer',
            'bard': 'merchant', 'hunter': 'adventurer', 'miller': 'farmer', 'guard': 'guard', 'elder': 'elder',
            'merchant': 'merchant', 'farmer': 'farmer', 'keeper': 'merchant', 'witch': 'elder', 'sage': 'elder',
            'knight': 'guard', 'captain': 'guard', 'cook': 'baker', 'innkeep': 'baker'}
    nm = str(o.get('name') or o.get('id') or '')
    h = 0
    for ch in str(o.get('id') or nm):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    job = o.get('outfit')
    if not job:
        low = nm.lower()
        job = next((v for k, v in jobs.items() if k in low), None)
    if not job:
        job = ['farmer', 'baker', 'merchant', 'elder', 'guard', 'farmer', 'adventurer'][h % 7]
    b = o.get('body') or ('female' if (h >> 4) % 2 else 'male')
    return f'{b}_{job}'


def _model_refs(rel):
    """Texture / buffer files a .glb or .gltf references (resolved relative to the assets folder)."""
    p = os.path.join(ASSETS_DIR, rel)
    try:
        with open(p, 'rb') as f:
            data = f.read()
        if data[:4] == b'glTF':
            n = int.from_bytes(data[12:16], 'little')
            j = json.loads(data[20:20 + n])
        else:
            j = json.loads(data)
    except (OSError, ValueError):
        return []
    base = os.path.dirname(rel)
    out = []
    for item in (j.get('images') or []) + (j.get('buffers') or []):
        uri = item.get('uri')
        if uri and not uri.startswith('data:'):
            out.append(os.path.normpath(os.path.join(base, urllib.parse.unquote(uri))).replace(os.sep, '/'))
    return out


CART_BUDGET = 40 * 1024 * 1024


def cartridge_assets(world, lite=True, budget=CART_BUDGET):
    """-> ({path: 'z'+base64(gzip) | 'b'+base64}, report). Textures use the 512 px copies in assets/lite/."""
    import gzip
    table, total, by = {}, 0, {}
    for rel in _cart_paths(world):
        if rel.startswith('lib:'):
            src = os.path.join(user_assets_dir(), rel[4:])
        else:
            src = os.path.join(ASSETS_DIR, 'lite', rel) if lite and os.path.isfile(os.path.join(ASSETS_DIR, 'lite', rel)) \
            else os.path.join(ASSETS_DIR, rel)
        with open(src, 'rb') as f:
            raw = f.read()
        if rel.lower().endswith(('.jpg', '.jpeg', '.png')):
            enc = 'b' + base64.b64encode(raw).decode('ascii')
        else:
            enc = 'z' + base64.b64encode(gzip.compress(raw, 6, mtime=0)).decode('ascii')
        table[rel] = enc
        total += len(enc)
        kind = rel.split('/')[0]
        by[kind] = by.get(kind, 0) + len(enc)
    if total > budget:
        raise ValueError(f'this cartridge would be {total / 1e6:.1f} MB of art (budget {budget / 1e6:.0f} MB): use fewer '
                         'different imported models or smaller textures, then export again')
    return table, {'files': len(table), 'bytes': total, 'by_kind': by}


def art_credits(paths=None):
    """Human-readable credits for the bundled art (every pack once), from assets/library.json."""
    try:
        with open(os.path.join(ASSETS_DIR, 'library.json'), encoding='utf-8') as fh:
            lib = json.load(fh)
    except (OSError, ValueError):
        return []
    want = set(paths) if paths is not None else None

    def hit(a):
        if want is None or (a.get('file') or '') in want:
            return True
        if a.get('kind') == 'material':
            pre = 'ground/' + str(a.get('id', '')).split('/')[-1] + '/'
            return any(x.startswith(pre) for x in want)
        return False
    seen, out = set(), []
    for a in lib.get('assets', []) + user_library():
        if not hit(a):
            continue
        key = (a.get('pack') or a.get('kind'), a.get('credit'))
        if key in seen:
            continue
        seen.add(key)
        what = a.get('pack') or {'hdri': 'Poly Haven HDRI skies', 'material': 'Poly Haven PBR textures'}.get(a.get('kind'), a.get('kind'))
        out.append(f"{what} - {a.get('credit')} - {a.get('licence')} - {a.get('source_url', '')}")
    return out


def cartridge_html(world, art=True, badge=False):
    """A single self-contained HTML5 file: engine + world + the bundled art it uses (gzip + base64), no network."""
    # every < > & is \u-escaped so no story or dialogue text can close or confuse the <script> block
    def safe(txt):
        return (txt.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
                .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))
    data = safe(json.dumps(world))
    title = re.sub(r'[<>&"%]', '', str(world.get('name', 'Game')))[:120]
    table, credits = {}, []
    if art and os.path.isdir(ASSETS_DIR):
        table, _ = cartridge_assets(world)
        credits = art_credits(list(table))
    return (CARTRIDGE_HTML.replace('%%TITLE%%', title).replace('%%BADGE%%', FREE_BADGE if badge else '')
            .replace('%%ART%%', json.dumps(table, separators=(',', ':')))
            .replace('%%CREDITS%%', safe(json.dumps(credits))).replace('%%ENGINE%%', ENGINE_JS)
            .replace('%%GSYS%%', _gs_runtime()).replace('%%WORLD%%', data))


def _mood_colors(mood):
    return MOOD_PY.get(mood, MOOD_PY[MOODS[0]])


def _c(rgb):
    return 'Color(%.3f, %.3f, %.3f, 1)' % tuple(rgb)


EXPORT_DIMS = {'tree': (1.6, 3.2, 1.6), 'ruin': (1.2, 3.0, 1.2), 'rock': (1.4, 0.9, 1.2), 'crystal': (0.5, 0.9, 0.5),
               'enemy': (1.0, 1.0, 1.0), 'lamp': (0.3, 2.2, 0.3), 'house': (4.2, 2.6, 3.4), 'npc': (0.8, 1.8, 0.8),
               'item': (0.5, 0.5, 0.5), 'chest': (1.0, 0.6, 0.7), 'door': (3.8, 3.0, 0.4), 'portal': (3.0, 3.0, 0.3),
               'checkpoint': (1.0, 1.6, 1.0)}
EXPORT_NOTE = ('Gameplay data (quests, NPC dialogue, items, enemy kinds, portals, extra levels, weather, time of day) is '
               'carried in world.json. This exporter builds level 1 as blocks with lights and a walking player; the quest, '
               'dialogue and combat systems run in the HTML5 cartridge and are not re-implemented here yet.')


def _ext_exporter(modname, fn):
    """Use the richer exporter module (godot_export.py / ue5_export.py) when it ships and imports cleanly."""
    try:
        if APP_DIR not in sys.path:
            sys.path.insert(0, APP_DIR)
        mod = __import__(modname)
        return getattr(mod, fn)
    except Exception:  # noqa: BLE001 - any import problem falls back to the built-in exporter
        return None


def godot_project(world, library=None):
    """-> {relative path: text or bytes} for a Godot 4 project (3D or 2D); delegates to godot_export.py when present."""
    ext = _ext_exporter('godot_export', 'godot_project')
    if ext:
        try:
            files = ext(world, library=library) if library else ext(world)
            if isinstance(files, dict) and files:
                return files
        except ValueError:
            raise                               # child-safety refusal or bad world: never fall back to the built-in
        except Exception as e:  # noqa: BLE001
            print('godot_export failed, using the built-in exporter:', e.__class__.__name__, file=sys.stderr)
    return _builtin_godot_project(world)


def _builtin_godot_project(world):
    """-> {relative path: text} for a Godot 4 project (3D or 2D)."""
    m = _mood_colors(world['mood'])
    name = re.sub(r'["\\]', '', world['name'])
    files = {'project.godot': f'; Engine configuration file.\nconfig_version=5\n\n[application]\n\n'
                              f'config/name="{name}"\nrun/main_scene="res://main.tscn"\n'
                              f'config/features=PackedStringArray("4.2")\n\n[rendering]\n\n'
                              f'environment/defaults/default_clear_color={_c(m["fog"])}\n',
             'world.json': json.dumps(world, indent=1),
             'README_GODOT.txt': f'{name} - exported by Stark World Engine {VERSION}.\nOpen Godot 4.2+ > Import > '
                                 'select project.godot > Run (F5). WASD/arrows move, Space jumps.\n' + EXPORT_NOTE + '\n'}
    subs, nodes = [], []
    if world['mode'] == '3d':
        files['player.gd'] = GODOT_PLAYER_3D
        subs.append(f'[sub_resource type="Environment" id="env"]\nbackground_mode = 1\nbackground_color = '
                    f'{_c(m["fog"])}\nambient_light_source = 2\nambient_light_color = {_c(m["amb"])}\n'
                    f'fog_enabled = true\nfog_light_color = {_c(m["fog"])}\nfog_density = {m["fogD"] / 2:.4f}\n')
        subs.append('[sub_resource type="BoxMesh" id="box"]\n')
        subs.append('[sub_resource type="BoxShape3D" id="boxshape"]\n')
        subs.append('[sub_resource type="CapsuleShape3D" id="cap"]\nradius = 0.4\nheight = 1.6\n')
        mats = {'ground': m['ground'], 'tree': m['leaf'], 'trunk': [0.35, 0.22, 0.12], 'ruin': m['stone'],
                'rock': [x * 0.8 for x in m['stone']], 'crystal': m['fx'], 'enemy': [0.8, 0.2, 0.3],
                'lamp': m['fx'], 'player': [0.95, 0.85, 0.6], 'house': [0.86, 0.78, 0.64], 'npc': [0.85, 0.6, 0.3],
                'item': m['fx'], 'chest': [0.5, 0.3, 0.15], 'door': [0.35, 0.22, 0.12], 'portal': [0.55, 0.45, 1.0],
                'checkpoint': m['stone']}
        for k, col in mats.items():
            emis = k in ('crystal', 'lamp', 'portal', 'item')
            subs.append(f'[sub_resource type="StandardMaterial3D" id="m_{k}"]\nalbedo_color = {_c(col)}\n'
                        + (f'emission_enabled = true\nemission = {_c(col)}\n' if emis else ''))
        nodes.append('[node name="World" type="Node3D"]\n')
        nodes.append('[node name="Env" type="WorldEnvironment" parent="."]\nenvironment = SubResource("env")\n')
        sd = m['sunDir']
        nodes.append(f'[node name="Sun" type="DirectionalLight3D" parent="."]\ntransform = Transform3D(1, 0, 0, 0, '
                     f'0.6, 0.8, 0, -0.8, 0.6, {sd[0] * 10:.2f}, 20, {sd[2] * 10:.2f})\nlight_color = {_c(m["sun"])}\n'
                     'shadow_enabled = true\n')
        nodes.append('[node name="Ground" type="StaticBody3D" parent="."]\ntransform = Transform3D(1, 0, 0, 0, 1, 0, '
                     '0, 0, 1, 0, -0.5, 0)\n')
        nodes.append('[node name="Mesh" type="MeshInstance3D" parent="Ground"]\ntransform = Transform3D(100, 0, 0, 0, '
                     '1, 0, 0, 0, 100, 0, 0, 0)\nmesh = SubResource("box")\nmaterial_override = SubResource("m_ground")\n')
        nodes.append('[node name="Shape" type="CollisionShape3D" parent="Ground"]\ntransform = Transform3D(100, 0, 0, '
                     '0, 1, 0, 0, 0, 100, 0, 0, 0)\nshape = SubResource("boxshape")\n')
        dims = EXPORT_DIMS
        for i, o in enumerate(o for o in world.get('objects', []) if o.get('t') in EXPORT_DIMS):
            sx, sy, sz = (d * float(o.get('s', 1)) for d in dims[o['t']])
            nodes.append(f'[node name="{o["t"]}_{i}" type="MeshInstance3D" parent="."]\ntransform = Transform3D('
                         f'{sx:.3f}, 0, 0, 0, {sy:.3f}, 0, 0, 0, {sz:.3f}, {float(o["x"]):.3f}, {sy / 2:.3f}, '
                         f'{float(o["z"]):.3f})\nmesh = SubResource("box")\nmaterial_override = SubResource("m_{o["t"]}")\n')
        nodes.append('[node name="Player" type="CharacterBody3D" parent="."]\ntransform = Transform3D(1, 0, 0, 0, 1, 0, '
                     '0, 0, 1, 0, 1.5, 0)\nscript = ExtResource("1")\n')
        nodes.append('[node name="Shape" type="CollisionShape3D" parent="Player"]\nshape = SubResource("cap")\n')
        nodes.append('[node name="Body" type="MeshInstance3D" parent="Player"]\ntransform = Transform3D(0.8, 0, 0, 0, '
                     '1.6, 0, 0, 0, 0.6, 0, 0, 0)\nmesh = SubResource("box")\nmaterial_override = SubResource("m_player")\n')
        nodes.append('[node name="Camera" type="Camera3D" parent="Player"]\ntransform = Transform3D(1, 0, 0, 0, 0.9, '
                     '0.43, 0, -0.43, 0.9, 0, 4, 8)\ncurrent = true\n')
        ext = '[ext_resource type="Script" path="res://player.gd" id="1"]\n'
    else:
        files['player.gd'] = GODOT_PLAYER_2D
        subs.append('[sub_resource type="RectangleShape2D" id="tile"]\nsize = Vector2(32, 32)\n')
        subs.append('[sub_resource type="RectangleShape2D" id="pl"]\nsize = Vector2(22, 28)\n')
        nodes.append('[node name="World" type="Node2D"]\n')
        tiles = world['tiles']
        nodes.append(f'[node name="Sky" type="ColorRect" parent="."]\noffset_right = {len(tiles[0]) * 32}\n'
                     f'offset_bottom = {len(tiles) * 32}\ncolor = {_c(m["fog"])}\n')
        colors = {'#': m['ground'], 'o': m['fx'], '^': [0.9, 0.2, 0.2], 'G': [1, 0.9, 0.3], 'e': [0.8, 0.2, 0.4]}
        px = py = 64
        for y, row in enumerate(tiles):
            for x, ch in enumerate(row):
                if ch == 'P':
                    px, py = x * 32 + 16, y * 32 + 16
                if ch not in colors:
                    continue
                n = f't{x}_{y}'
                kind = 'StaticBody2D' if ch == '#' else 'Area2D'
                nodes.append(f'[node name="{n}" type="{kind}" parent="."]\nposition = Vector2({x * 32 + 16}, '
                             f'{y * 32 + 16})\n' + ('' if ch == '#' else f'metadata/kind = "{ch}"\n'))
                nodes.append(f'[node name="C" type="CollisionShape2D" parent="{n}"]\nshape = SubResource("tile")\n')
                nodes.append(f'[node name="R" type="ColorRect" parent="{n}"]\noffset_left = -16.0\noffset_top = -16.0\n'
                             f'offset_right = 16.0\noffset_bottom = 16.0\ncolor = {_c(colors[ch])}\n')
        nodes.append(f'[node name="Player" type="CharacterBody2D" parent="."]\nposition = Vector2({px}, {py})\n'
                     'script = ExtResource("1")\n')
        nodes.append('[node name="C" type="CollisionShape2D" parent="Player"]\nshape = SubResource("pl")\n')
        nodes.append('[node name="R" type="ColorRect" parent="Player"]\noffset_left = -11.0\noffset_top = -14.0\n'
                     'offset_right = 11.0\noffset_bottom = 14.0\ncolor = Color(0.95, 0.85, 0.6, 1)\n')
        nodes.append('[node name="Camera" type="Camera2D" parent="Player"]\n')
        ext = '[ext_resource type="Script" path="res://player.gd" id="1"]\n'
    files['main.tscn'] = (f'[gd_scene load_steps={len(subs) + 2} format=3]\n\n' + ext + '\n' + '\n'.join(subs)
                          + '\n' + '\n'.join(nodes))
    return files


GODOT_PLAYER_3D = """extends CharacterBody3D
# Stark World Engine export: WASD / arrows move, Space jumps.
const SPEED := 6.0
const JUMP := 7.0
var gravity: float = ProjectSettings.get_setting("physics/3d/default_gravity")

func _physics_process(delta: float) -> void:
\tif not is_on_floor():
\t\tvelocity.y -= gravity * delta
\tif Input.is_key_pressed(KEY_SPACE) and is_on_floor():
\t\tvelocity.y = JUMP
\tvar d := Vector3.ZERO
\tif Input.is_key_pressed(KEY_W) or Input.is_key_pressed(KEY_UP): d.z -= 1
\tif Input.is_key_pressed(KEY_S) or Input.is_key_pressed(KEY_DOWN): d.z += 1
\tif Input.is_key_pressed(KEY_A) or Input.is_key_pressed(KEY_LEFT): d.x -= 1
\tif Input.is_key_pressed(KEY_D) or Input.is_key_pressed(KEY_RIGHT): d.x += 1
\td = d.normalized()
\tvelocity.x = d.x * SPEED
\tvelocity.z = d.z * SPEED
\tmove_and_slide()
"""

GODOT_PLAYER_2D = """extends CharacterBody2D
# Stark World Engine export: A/D or arrows move, Space/W jumps.
const SPEED := 220.0
const JUMP := -560.0
const GRAVITY := 1500.0

func _physics_process(delta: float) -> void:
\tif not is_on_floor():
\t\tvelocity.y += GRAVITY * delta
\tif (Input.is_key_pressed(KEY_SPACE) or Input.is_key_pressed(KEY_W) or Input.is_key_pressed(KEY_UP)) and is_on_floor():
\t\tvelocity.y = JUMP
\tvar d := 0.0
\tif Input.is_key_pressed(KEY_A) or Input.is_key_pressed(KEY_LEFT): d -= 1.0
\tif Input.is_key_pressed(KEY_D) or Input.is_key_pressed(KEY_RIGHT): d += 1.0
\tvelocity.x = d * SPEED
\tmove_and_slide()
"""


def ue5_project(world, library=None):
    """-> {relative path: text or bytes}; delegates to ue5_export.py when present."""
    ext = _ext_exporter('ue5_export', 'ue5_project')
    if ext:
        try:
            files = ext(world, library=library) if library else ext(world)
            if isinstance(files, dict) and files:
                return files
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001
            print('ue5_export failed, using the built-in exporter:', e.__class__.__name__, file=sys.stderr)
    return _builtin_ue5_project(world)


def _builtin_ue5_project(world):
    """-> {relative path: text}: .uproject + an editor Python script that builds the level from world.json."""
    if world['mode'] != '3d':
        raise ValueError('UE5 export supports 3D worlds; use Godot 4 or the HTML5 cartridge for 2D worlds')
    name = slug(world['name'])
    m = _mood_colors(world['mood'])
    up = {'FileVersion': 3, 'EngineAssociation': '5.4', 'Category': '', 'Description': world.get('story', ''),
          'Plugins': [{'Name': 'PythonScriptPlugin', 'Enabled': True},
                      {'Name': 'EditorScriptingUtilities', 'Enabled': True}]}
    script = UE5_SCRIPT.replace('%%MOOD%%', json.dumps(m))
    return {f'{name}.uproject': json.dumps(up, indent=2),
            'Config/DefaultEngine.ini': '[/Script/EngineSettings.GeneralProjectSettings]\nProjectName=' + name + '\n',
            'Content/Python/world.json': json.dumps(world, indent=1),
            'Content/Python/stark_build_level.py': script,
            'README_UE5.txt': f'{world["name"]} - exported by Stark World Engine {VERSION}.\n1. Open {name}.uproject in '
                              'Unreal Engine 5.4+ (it asks to convert if you use a newer version: allow it).\n2. Tools '
                              '> Execute Python Script... > Content/Python/stark_build_level.py\n3. The script places '
                              'cubes for every object, a sun, sky light and height fog in the open level. Save it.\n' + EXPORT_NOTE + '\n'}


UE5_SCRIPT = '''"""Builds the Stark World Engine world into the open UE5 level (run from Tools > Execute Python Script)."""
import json, os
import unreal

HERE = os.path.dirname(os.path.abspath(__file__))
MOOD = %%MOOD%%
DIMS = {"tree": (1.6, 3.2, 1.6), "ruin": (1.2, 3.0, 1.2), "rock": (1.4, 0.9, 1.2), "crystal": (0.5, 0.9, 0.5),
        "enemy": (1.0, 1.0, 1.0), "lamp": (0.3, 2.2, 0.3), "house": (4.2, 2.6, 3.4), "npc": (0.8, 1.8, 0.8),
        "item": (0.5, 0.5, 0.5), "chest": (1.0, 0.6, 0.7), "door": (3.8, 3.0, 0.4), "portal": (3.0, 3.0, 0.3),
        "checkpoint": (1.0, 1.6, 1.0)}
with open(os.path.join(HERE, "world.json"), encoding="utf-8") as f:
    world = json.load(f)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
cube = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube.Cube")


def spawn_cube(label, x, y, z, sx, sy, sz, folder):
    a = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(x * 100, z * 100, y * 100))
    a.static_mesh_component.set_static_mesh(cube)
    a.set_actor_scale3d(unreal.Vector(sx, sz, sy))
    a.set_actor_label(label)
    a.set_folder_path(folder)
    return a


spawn_cube("Ground", 0, -0.5, 0, 100, 1, 100, "Stark")
for i, o in enumerate(o for o in world.get("objects", []) if o.get("t") in DIMS):
    s = float(o.get("s", 1))
    sx, sy, sz = (d * s for d in DIMS[o["t"]])
    spawn_cube(f"{o['t']}_{i}", float(o["x"]), sy / 2, float(o["z"]), sx, sy, sz, "Stark/" + o["t"])
sun = actors.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 2000), unreal.Rotator(0, -45, 30))
sun.light_component.set_light_color(unreal.LinearColor(*MOOD["sun"], 1))
actors.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 1000))
fog = actors.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 0))
fog.component.set_fog_inscattering_color(unreal.LinearColor(*MOOD["fog"], 1))
unreal.log("Stark World Engine: level built from world.json")
'''


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
    """-> list of written paths (relative to My_Games). Godot/UE5 need the Pro or Studio tier."""
    tier = tier or current_tier()
    world = _gs_gate(validate_world(load_world(name, games)))     # child safety: blocks or returns the tagged world
    base = f'{slug(world["name"])}/export'
    if target == 'itch':                     # 28.7: upload-ready zip for itch.io (index.html + credits), offline packaging
        paths = export_world(name, 'cartridge', tier=tier, games=games)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for arc, rel in (('index.html', paths[0]), ('CREDITS.txt', paths[1])):
                with open(safe_path(rel, games), 'rb') as fh:
                    zi = zipfile.ZipInfo(arc, (2026, 1, 1, 0, 0, 0))
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    z.writestr(zi, fh.read())
        rel = f'{base}/{slug(world["name"])}_itch.zip'
        write_versioned(rel, buf.getvalue(), games)
        return [rel] + paths
    if target == 'cartridge':
        rel = f'{base}/{slug(world["name"])}_cartridge.html'
        free = tier == 'free'
        if free:                                # Free: the first level only (any key exports every level)
            world = dict(world, levels=[])
        html = cartridge_html(world, badge=free)
        if os.path.isdir(os.path.join(games or GAMES, slug(world['name']), 'audio', 'pack')):
            try:
                from stark_audio import studio_api as _ga      # adaptive music, SFX, ambience, voices (offline)
                html = _ga.cartridge_html(html, world, games or GAMES)
            except ImportError:
                pass
        write_versioned(rel, html.encode('utf-8'), games)
        cred = f'{base}/{slug(world["name"])}_CREDITS.txt'
        lines = [f'{world.get("name", "Game")} - made with Stark World Engine', f'A game by {world.get("author") or "you"}', '',
                 'Art used in this game (thank you!):'] + ['  ' + c for c in art_credits(_cart_paths(world))]
        write_versioned(cred, ('\n'.join(lines) + '\n').encode('utf-8'), games)
        return [rel, cred]
    if target not in ('godot', 'ue5'):
        raise ValueError('target must be cartridge, itch, godot or ue5')
    if tier not in ('pro', 'studio'):
        raise PermissionError(f'{target} export is part of the Pro and Studio tiers (this copy is {tier})')
    lib = user_assets_dir() if os.path.isfile(_lib_index_path()) else None
    files = godot_project(world, library=lib) if target == 'godot' else ue5_project(world, library=lib)
    try:
        if not os.path.isdir(os.path.join(games or GAMES, slug(world['name']), 'audio', 'pack')):
            raise ImportError('no audio pack yet (press Build game audio first)')   # never a surprise 1-3 min render
        from stark_audio import studio_api as _ga
        files.update(_ga.export_files(world, games or GAMES, target))   # {relative path: bytes} under audio/
    except Exception as e:  # noqa: BLE001 - audio is optional for engine projects
        print('[studio] engine export without audio:', e.__class__.__name__, file=sys.stderr)
    out = []
    for rel, text in files.items():
        r = f'{base}/{target}/{rel}'
        write_versioned(r, text if isinstance(text, bytes) else text.encode('utf-8'), games)
        out.append(r)
    return out


# ---------------- your asset library (My_Assets): import glTF / GLB / textures / HDRI with licence + credit ----------------
USER_ASSETS = os.path.join(os.path.dirname(GAMES), 'My_Assets')
LIB_EXT = {'.glb': 'model', '.gltf': 'model', '.bin': 'buffer', '.png': 'image', '.jpg': 'image', '.jpeg': 'image',
           '.webp': 'image', '.hdr': 'hdri'}
LICENCES = ('CC0 1.0', 'CC-BY 4.0', 'CC-BY-SA 4.0', 'Bought licence (see my receipt)', 'My own work', 'Other (see note)')
PBR_HINTS = [('albedo', r'(albedo|basecolou?r|base_colou?r|diffuse|diff|col(or)?)\b'), ('normal', r'(normal|nor_gl|nrm|nor)\b'),
             ('orm', r'\b(orm|arm)\b'), ('roughness', r'(rough(ness)?)\b'), ('metallic', r'(metal(lic|ness)?)\b'),
             ('ao', r'\b(ao|occlusion|ambientocclusion)\b'), ('emissive', r'(emissive|emission)\b'), ('height', r'(height|disp(lacement)?)\b')]


def user_assets_dir():
    return os.path.join(os.path.dirname(GAMES), 'My_Assets')


def _lib_index_path():
    return os.path.join(user_assets_dir(), 'library.json')


def user_library():
    """-> list of the creator's imported assets (same fields as assets/library.json)."""
    try:
        with open(_lib_index_path(), encoding='utf-8') as f:
            data = json.load(f)
        return [a for a in data.get('assets', []) if isinstance(a, dict)]
    except (OSError, ValueError):
        return []


def _pbr_guess(names):
    """Which file is which PBR map, from the usual file name suffixes."""
    out = {}
    for n in names:
        low = re.sub(r'[\-.]', '_', os.path.splitext(n)[0].lower())
        for role, rx in PBR_HINTS:
            if role not in out and re.search(rx, low.replace('_', ' ')):
                out[role] = n
                break
    return out


def import_asset(body):
    """Save an imported asset into My_Assets/<id>/ and add it to My_Assets/library.json. -> the manifest entry."""
    files = body.get('files')
    if not isinstance(files, list) or not files or len(files) > 40:
        raise ValueError('choose 1 to 40 files (a .glb, or a .gltf with its .bin and textures, or textures, or one .hdr)')
    def txt(v, n):
        v = '' if v is None else str(v).strip()
        if len(v) > n or '<' in v:
            raise ValueError('text too long or contains <')
        return v
    name = txt(body.get('name'), 80) or os.path.splitext(str(files[0].get('name') or 'asset'))[0]
    lic = str(body.get('licence') or '')
    if lic not in LICENCES:
        raise ValueError('pick the licence of this art (it goes into the credits of every game that uses it)')
    credit = txt(body.get('credit'), 200) or ('me' if lic == 'My own work' else '')
    if not credit:
        raise ValueError('say who made this art (it goes into the credits)')
    src_url = txt(body.get('source_url'), 400)
    blobs = []
    for f in files:
        fn = os.path.basename(str(f.get('name') or ''))
        ext = os.path.splitext(fn)[1].lower()
        if ext not in LIB_EXT or not re.fullmatch(r'[\w .()+-]{1,120}', fn):
            raise ValueError(f'{fn or "a file"}: only .glb .gltf .bin .png .jpg .webp .hdr files with plain names')
        data = base64.b64decode(str(f.get('b64') or ''), validate=True)
        if ext == '.glb' and data[:4] != b'glTF':
            raise ValueError(f'{fn} is not a binary glTF file')
        if ext == '.png' and not data.startswith(b'\x89PNG'):
            raise ValueError(f'{fn} is not a PNG')
        if ext in ('.jpg', '.jpeg') and data[:2] != b'\xff\xd8':
            raise ValueError(f'{fn} is not a JPEG')
        if ext == '.hdr' and not data.startswith(b'#?'):
            raise ValueError(f'{fn} is not a Radiance .hdr file')
        if ext == '.gltf':
            try:
                j = json.loads(data)
            except ValueError:
                raise ValueError(f'{fn} is not valid glTF JSON')
            req = [e for e in j.get('extensionsRequired', []) if e not in ('KHR_materials_emissive_strength', 'KHR_texture_transform',
                                                                            'KHR_materials_unlit', 'KHR_mesh_quantization')]
            if req:
                raise ValueError(f'{fn} needs {", ".join(req)}: export it again without compression')
        blobs.append((fn, ext, data))
    kinds = {LIB_EXT[e] for _, e, _ in blobs}
    kind = 'model' if 'model' in kinds else 'hdri' if 'hdri' in kinds else 'material'
    aid = 'u' + base64.b32encode(os.urandom(5)).decode().lower()
    folder = os.path.join(user_assets_dir(), aid)
    os.makedirs(folder, exist_ok=True)
    for fn, _, data in blobs:
        with open(os.path.join(folder, fn), 'wb') as fh:
            fh.write(data)
    main = next((fn for fn, e, _ in blobs if e in ('.glb', '.gltf')), None) or \
        next((fn for fn, e, _ in blobs if e == '.hdr'), None) or blobs[0][0]
    try:
        scale = float(body.get('scale') or 1)
    except (TypeError, ValueError):
        scale = 1.0
    entry = {'id': f'lib:{aid}', 'name': name, 'kind': kind, 'category': 'imported', 'file': f'lib:{aid}/{main}',
             'files': [fn for fn, _, _ in blobs], 'licence': lic, 'credit': credit, 'source_url': src_url,
             'scale': max(0.001, min(1000.0, scale)), 'pivot': str(body.get('pivot') or 'bottom-center'),
             'up': 'Y', 'pbr': _pbr_guess([fn for fn, e, _ in blobs if LIB_EXT[e] == 'image']),
             'imported': datetime.date.today().isoformat()}
    lib = user_library() + [entry]
    tmp = _lib_index_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump({'version': 1, 'about': 'Your imported art. Paths: lib:<id>/<file> = My_Assets/<id>/<file>. '
                   'Schema as in the bundled assets/library.json.', 'assets': lib}, fh, indent=1)
    os.replace(tmp, _lib_index_path())
    return entry


def library_listing():
    """Bundled (assets/library.json) + imported (My_Assets/library.json), trimmed for the Assets tab."""
    try:
        with open(os.path.join(ASSETS_DIR, 'library.json'), encoding='utf-8') as f:
            built = json.load(f).get('assets', [])
    except (OSError, ValueError):
        built = []
    keep = ('id', 'name', 'kind', 'category', 'file', 'licence', 'credit', 'source_url', 'scale', 'pivot', 'bounds', 'pbr', 'tags')
    out = [{k: a[k] for k in keep if k in a} for a in built if a.get('kind') in ('model', 'hdri', 'material', 'character')]
    return {'bundled': out, 'mine': user_library(), 'licences': list(LICENCES)}


# ---------------- docs ----------------
def personal_license(ed=None):
    """The licence text: EULA.txt next to this file (the End User Licence Agreement) with a header; the short personal
    licence below is the fallback if EULA.txt is missing, so the text is never empty."""
    ed = ed if ed in TIERS else edition()
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'EULA.txt'), encoding='utf-8') as f:
            eula = f.read().strip()
        if len(eula) > 500:
            return f"STARK WORLD ENGINE {VERSION} - LICENCE ({ed.capitalize()} edition)\n\n" + eula + "\n"
    except OSError:
        pass
    return f"""STARK WORLD ENGINE {VERSION} - PERSONAL LICENSE ({ed.capitalize()} edition)

1. You may install and use this copy of Stark World Engine on computers you own, for personal projects.
2. The games, worlds, pixel art and sounds YOU make with it are yours.
3. You may not resell, sublicense, re-upload or redistribute Stark World Engine itself (in whole or in part), and you
   may not monetize Stark World Engine itself (for example: selling it, bundling it, or charging for access to it).
4. NO WARRANTY. The software is provided "AS IS", without warranty of any kind, express or implied, including but
   not limited to merchantability, fitness for a particular purpose and non-infringement. In no event shall the
   author be liable for any claim, damages or other liability arising from the software or its use.
5. Stark World Engine runs offline. It does not call any paid cloud service and needs no account.
6. You may not use Stark World Engine to create content that sexualises minors or depicts the abuse of minors. Doing so
   terminates this licence immediately. The studio refuses to export such content and exported games refuse to run it.
"""


def gift_certificate():
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Stark World Engine Gift Certificate</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{margin:0;background:#f4f1ea;font-family:Georgia,serif;display:flex;justify-content:center;padding:24px}
.card{max-width:760px;width:100%;background:#fffdf7;border:10px double #2d6a5a;padding:36px;box-sizing:border-box}
h1{margin:0 0 4px;font-size:34px;color:#2d6a5a;letter-spacing:1px}.sub{color:#555;margin-bottom:24px}
.row{margin:14px 0;font-size:18px}.f{display:inline-block;min-width:60%;border-bottom:1px solid #888;padding:2px 6px}
.note{font-size:13px;color:#666;margin-top:26px}@media print{body{background:#fff}.hint{display:none}}
</style></head><body><div class="card"><h1>Stark World Engine</h1>
<div class="sub">Gift certificate: an offline 3D + 2D game builder</div>
<div class="row">To: <span class="f" contenteditable="true"></span></div>
<div class="row">From: <span class="f" contenteditable="true"></span></div>
<div class="row">Edition: <span class="f" contenteditable="true">Pro / Studio</span></div>
<div class="row">Message: <span class="f" contenteditable="true"></span></div>
<div class="row">Download link or code: <span class="f" contenteditable="true"></span></div>
<p class="note">Click a line to type, then print (Ctrl+P). Stark World Engine runs on your own computer, offline,
with no subscription. Personal license; see PERSONAL_LICENSE.txt.</p>
<p class="note hint">This line is hidden when printed.</p></div></body></html>
"""


def write_docs(dest):
    os.makedirs(dest, exist_ok=True)
    out = []
    for name, text in (('GIFT_CERTIFICATE.html', gift_certificate()), ('PERSONAL_LICENSE.txt', personal_license())):
        p = os.path.join(dest, name)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        out.append(p)
    return out


# ---------------- remix: open a game someone shared (28.7) ----------------
REMIX_MAX = 48 * 1024 * 1024


def remix_source(text):
    """The world inside a shared file: a one-file HTML5 cartridge made with this studio, or a world.json. Only the game
    data is read as JSON; nothing in the file is ever run. -> world dict (not validated yet)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('That file is empty.')
    if len(text) > REMIX_MAX:
        raise ValueError('That file is too big to be a game from this studio.')
    t = text.lstrip('\ufeff').strip()
    if t.startswith('{'):
        data = json.loads(t)
    else:
        i = t.find('var WORLD=')
        j = t.find(';\nvar QS=', i) if i >= 0 else -1
        if j < 0:
            raise ValueError('No game inside this file. Choose a game (.html) exported from Stark World Engine, or a world.json.')
        data = json.loads(t[i + len('var WORLD='):j])
    if isinstance(data, dict) and isinstance(data.get('world'), dict) and 'objects' not in data:
        data = data['world']
    if not isinstance(data, dict):
        raise ValueError('That file does not hold a game.')
    return data


def remix_world(text, games=None):
    """Save a shared game as a NEW game in My_Games: '<name> (remix)', crediting where it came from. Imported art that
    lives only on the sender's computer (lib:...) and is not in this studio's library is left out. -> summary dict."""
    w = remix_source(text)
    lib = user_assets_dir()
    dropped = 0
    for L in [w] + [x for x in (w.get('levels') or []) if isinstance(x, dict)]:
        objs = L.get('objects')
        if isinstance(objs, list):
            keep = [o for o in objs if not (isinstance(o, dict) and str(o.get('asset') or '').startswith('lib:')
                                             and not os.path.isfile(os.path.join(lib, str(o['asset'])[4:])))]
            dropped += len(objs) - len(keep)
            L['objects'] = keep
        sky = L.get('sky')
        if isinstance(sky, str) and sky.startswith('lib:') and not os.path.isfile(os.path.join(lib, sky[4:])):
            del L['sky']
            dropped += 1
    for k in ('music',):                       # the sender's soundtrack file is not inside a cartridge
        w.pop(k, None)
    orig = str(w.get('name') or 'Shared game')[:100]
    w['name'] = orig
    w = validate_world(w)
    by = str(w.get('author') or '').strip()
    w['remixOf'] = (orig + (' by ' + by if by else ''))[:160]
    base, n, name = f'{orig} (remix)', 1, f'{orig} (remix)'
    while os.path.exists(safe_path(f'{slug(name)}/world.json', games)):
        n += 1
        name = f'{base} {n}'
    w['name'] = name
    write_versioned(f'{slug(name)}/world.json', json.dumps(w, indent=1).encode('utf-8'), games)
    lv = 1 + len(w.get('levels') or [])
    return {'slug': slug(name), 'name': name, 'levels': lv, 'dropped': dropped, 'remixOf': w['remixOf']}


# ---------------- classroom mode (Stark Education Suite): lesson packs, student-safe profile, reflection export ----------------
def classroom_on():
    """True when config.json carries "classroom_profile": true (read each time, so a teacher can flip it)."""
    import stark_classroom
    return stark_classroom.is_classroom(DATA_DIR)


def classroom_start(pack_id, games=None):
    """Save a fresh copy of a lesson pack's starter project into My_Games. -> {'slug', 'name'} (never overwrites)."""
    import stark_classroom
    w = validate_world(stark_classroom.pack_world(str(pack_id)))
    base, n, name = w['name'], 1, w['name']
    while os.path.exists(safe_path(f'{slug(name)}/world.json', games)):
        n += 1
        name = f'{base} ({n})'
    w['name'] = name
    write_versioned(f'{slug(name)}/world.json', json.dumps(w, indent=1).encode('utf-8'), games)
    return {'slug': slug(name), 'name': name}


def classroom_export(name, student='', answers=None, pack_id=None, tier=None, games=None):
    """Export a student's finished game as the one-file HTML5 cartridge plus a short reflection text file.
    -> list of written paths (relative to My_Games). The cartridge follows the normal tier rules (Free = level 1)."""
    import stark_classroom
    written = export_world(name, 'cartridge', tier=tier, games=games)
    world = load_world(name, games)
    pack = None
    base = re.sub(r' \(\d+\)$', '', str(world.get('name', '')))     # "Lesson - X (2)" is a copy of lesson X
    for pk in stark_classroom.list_packs():
        if stark_classroom.pack_world(pk['id'])['name'] == base:        # the opened game wins over the dropdown
            pack_id = pk['id']
    try:
        pack = stark_classroom.get_pack(pack_id) if pack_id else None
    except KeyError:
        pack = None
    text = stark_classroom.reflection_text(world, answers, student, pack, stark_classroom.lint_world(world))
    rel = f'{slug(world["name"])}/export/{slug(world["name"])}_REFLECTION.txt'
    write_versioned(rel, text.encode('utf-8'), games)
    return written + [rel]


# ---------------- server ----------------
def list_backups(name, games=None):
    """-> [{'id': backup file name, 'when': 'YYYY-MM-DD HH:MM:SS'}] newest first, for My_Games/<world>/world.json."""
    bdir = safe_path(os.path.join('.backups', slug(name)), games)
    out = []
    for p in sorted(glob.glob(os.path.join(glob.escape(bdir), 'world.json.*.bak')), reverse=True):
        b = os.path.basename(p)
        m = re.match(r'world\.json\.(\d{8})_(\d{6})_\d+\.bak$', b)
        when = (f'{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]} {m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:]}'
                if m else b)
        out.append({'id': b, 'when': when})
    return out


def read_backup(name, backup_id, games=None):
    if not re.match(r'^world\.json\.\d{8}_\d{6}_\d+\.bak$', str(backup_id or '')):
        raise ValueError('unknown backup')
    with open(safe_path(f'.backups/{slug(name)}/{backup_id}', games), encoding='utf-8') as f:
        return validate_world(json.load(f))


_DOCTOR_CACHE = {}
_DOCTOR_LOCK = threading.Lock()


def doctor_report(app_dir=None, port=None, fix=False):
    """'Fix my setup': doctor.py checks (if it ships) -> {'available', 'items': [non-ok checks], 'webgl2_fix'}.
    fix=False only looks (no repairs); the check-only answer is cached for 10 s so repeated calls stay cheap."""
    import time
    d = app_dir or APP_DIR
    ck = (d, port)
    with _DOCTOR_LOCK:
        hit = _DOCTOR_CACHE.get(ck)
        if not fix and hit and time.time() - hit[0] < 10:
            return dict(hit[1], cached=True)
    try:
        if d not in sys.path:
            sys.path.insert(0, d)
        import doctor
        try:
            items = doctor.run_checks(d, quick=True, running_port=port, fix=bool(fix)) or []
        except TypeError:                                    # an older doctor.py without the fix switch
            items = doctor.run_checks(d, quick=True, running_port=port) if fix else []
        keep = ('id', 'ok', 'severity', 'title', 'fix_text', 'auto_fixed', 'action')
        bad = [{k: it.get(k) for k in keep} for it in items if isinstance(it, dict) and not it.get('ok')]
        out = {'available': True, 'checked': len(items), 'items': bad, 'fixed': bool(fix),
               'webgl2_fix': str(getattr(doctor, 'FIX_TEXTS', {}).get('webgl2', ''))}
        with _DOCTOR_LOCK:
            _DOCTOR_CACHE[ck] = (time.time(), out)
        return out
    except Exception as e:  # noqa: BLE001 - the studio never depends on the doctor
        print(f'[studio] doctor failed: {e.__class__.__name__}: {e}', file=sys.stderr)
        return {'available': False, 'checked': 0, 'items': [], 'webgl2_fix': ''}


def make_soundtrack(world_name, mood, seconds=40, seed=None, games=None, app_dir=None):
    """Render a looping soundtrack for a world with music_engine.py (if it ships) -> {'saved', 'music'}."""
    if mood not in MOODS:
        raise ValueError('unknown mood')
    seconds = int(min(90, max(10, int(seconds or 40))))
    seed = int(seed or 0) % 100000
    try:
        d = app_dir or APP_DIR
        if d not in sys.path:
            sys.path.insert(0, d)
        import music_engine
        path = music_engine.compose_world(mood, seconds=seconds, seed=seed)
        with open(path, 'rb') as f:
            data = f.read()
    except Exception as e:  # noqa: BLE001 - the studio must keep working without the music engine
        print(f'[studio] soundtrack failed: {e.__class__.__name__}: {e}', file=sys.stderr)
        raise ValueError('The soundtrack maker is not available in this copy (music_engine.py). '
                         'Your game still has its sound effects.')
    if not (data[:4] == b'RIFF' and data[8:12] == b'WAVE'):
        raise ValueError('the soundtrack maker returned an unexpected file')
    rel = f'{slug(world_name)}/assets/soundtrack.wav'
    write_versioned(rel, data, games)
    return {'saved': rel, 'music': 'assets/soundtrack.wav', 'bytes': len(data)}


def _friendly(e):
    """An error message for the browser: no absolute paths, no user names."""
    msg = str(e)
    msg = re.sub(r'[A-Za-z]:[\\/][^\s\'"]*', '<path>', msg)
    msg = re.sub(r'(?<![A-Za-z0-9])/(?:[^\s\'"/]+/)+[^\s\'"]*', '<path>', msg)
    if isinstance(e, FileNotFoundError):
        return 'Not found - it may have been moved or deleted.'
    if isinstance(e, PermissionError) and e.errno is None:     # a tier refusal, not a file problem: keep its words
        return msg[:300]
    if isinstance(e, (OSError,)) and not isinstance(e, SandboxError):
        return 'Could not read or write that file (is it open in another program, or is the disk full?).'
    if isinstance(e, (json.JSONDecodeError, RecursionError)):
        return 'That data is not valid JSON.'
    return msg[:300] or e.__class__.__name__


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'StarkWorldEngine/' + VERSION
    games = GAMES
    app_dir = APP_DIR
    timeout = 20                      # socket timeout: idle or slow connections cannot pin a thread forever

    def log_message(self, fmt, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get('Host') or '').split(':')[0].lower()
        return host in ('127.0.0.1', 'localhost')

    def _origin_ok(self):
        """POSTs must come from the studio page itself (or a non-browser client without an Origin header)."""
        o = self.headers.get('Origin')
        if o is None:
            return True
        try:
            u = urllib.parse.urlparse(o)
        except ValueError:
            return False
        return u.scheme == 'http' and (u.hostname or '') in ('127.0.0.1', 'localhost') and \
            u.port == self.server.server_address[1]

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        hdrs = dict(extra or {})
        self.send_header('Content-Security-Policy', hdrs.pop('Content-Security-Policy', None) or
                         "default-src 'self' 'unsafe-inline' data: blob:; connect-src 'self'; img-src 'self' data: blob:")
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, e):
        if code >= 500 or not isinstance(e, (ValueError, PermissionError, SandboxError)):
            print(f'[studio] {self.command} {self.path.split("?")[0]}: {e.__class__.__name__}: {e}', file=sys.stderr)
        return self._send(code, {'error': _friendly(e)})

    def _json_body(self):
        raw = self.headers.get('Content-Length')
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ValueError('a Content-Length header is required')
        if n < 0 or n > MAX_BODY:
            raise ValueError('request too large' if n > 0 else 'bad Content-Length')
        data = self.rfile.read(n) if n else b''
        if len(data) != n:
            raise ValueError('request body was cut short')
        return json.loads(data or b'{}')

    ASSET_TYPES = {'.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json', '.bin': 'application/octet-stream',
                   '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
                   '.hdr': 'application/octet-stream', '.json': 'application/json', '.txt': 'text/plain; charset=utf-8'}

    def _static(self, root, rel):
        """Serve one file from a read-only folder (realpath-sandboxed, known types only, cacheable)."""
        base = os.path.realpath(root)
        p = os.path.realpath(os.path.join(base, rel))
        ext = os.path.splitext(p)[1].lower()
        if not p.startswith(base + os.sep) or ext not in self.ASSET_TYPES or not os.path.isfile(p):
            return self._send(404, {'error': 'not found'})
        with open(p, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', self.ASSET_TYPES[ext])
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'max-age=3600')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {'error': 'bad host'})
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ('/', '/index.html'):
                return self._send(200, app_html(), 'text/html; charset=utf-8')
            if u.path == '/favicon.ico':
                return self._send(204, b'', 'image/x-icon')
            if u.path == '/api/info':
                lic = license_info(self.app_dir)
                return self._send(200, {'version': VERSION, 'tier': current_tier(self.app_dir), 'moods': MOODS,
                                        'langs': LANGS, 'edition': edition(self.app_dir), 'license': lic,
                                        'worlds': list_worlds(self.games), 'themes': list(THEMES),
                                        'classroom': classroom_on(), 'sizes': list(SIZES), 'templates': [w['name'] for w in classic_templates()], 'steam': STEAM})
            if u.path == '/api/world':
                return self._send(200, load_world(q.get('name', [''])[0], self.games))
            if u.path == '/api/template':
                nm = q.get('name', [''])[0]
                for w in classic_templates() + starter_worlds():
                    if w['name'] == nm:
                        return self._send(200, w)
                return self._send(404, {'error': 'no such template'})
            if u.path.startswith('/api/classroom/'):
                import stark_classroom
                if u.path == '/api/classroom/profile':
                    return self._send(200, stark_classroom.profile(DATA_DIR))
                if u.path == '/api/classroom/packs':
                    return self._send(200, {'packs': stark_classroom.list_packs()})
                if u.path == '/api/classroom/pack':
                    try:
                        pk = stark_classroom.get_pack(q.get('id', [''])[0])
                    except KeyError as e:
                        return self._send(404, {'error': str(e.args[0])})
                    return self._send(200, {k: v for k, v in pk.items() if k not in ('build', 'answer_key')})
                return self._send(404, {'error': 'not found'})
            if u.path == '/api/doctor':          # it runs probes and may repair things: POST (origin-checked) only
                return self._send(405, {'error': 'use POST'}, extra={'Allow': 'POST'})
            if u.path == '/api/backups':
                return self._send(200, {'backups': list_backups(q.get('name', [''])[0], self.games)})
            if u.path == '/api/backup':
                return self._send(200, read_backup(q.get('name', [''])[0], q.get('id', [''])[0], self.games))
            if u.path == '/GIFT_CERTIFICATE.html':
                return self._send(200, gift_certificate(), 'text/html; charset=utf-8')
            if u.path == '/PERSONAL_LICENSE.txt':
                return self._send(200, personal_license(edition(self.app_dir)), 'text/plain; charset=utf-8')
            if u.path.startswith('/library/'):   # the creator's imported art (My_Assets), read-only here
                return self._static(user_assets_dir(), urllib.parse.unquote(u.path[len('/library/'):]))
            if u.path == '/api/library':
                return self._send(200, library_listing())
            if u.path.startswith('/assets/'):    # bundled CC0 art (read-only): glTF models, textures, HDRI skies
                return self._static(ASSETS_DIR, urllib.parse.unquote(u.path[len('/assets/'):]))
            if u.path.startswith('/files/'):
                p = safe_path(urllib.parse.unquote(u.path[len('/files/'):]), self.games)
                if not os.path.isfile(p):
                    return self._send(404, {'error': 'not found'})
                ext = os.path.splitext(p)[1].lower()
                ctype = {'.png': 'image/png', '.wav': 'audio/wav', '.json': 'application/json',
                         '.html': 'text/html; charset=utf-8'}.get(ext, 'application/octet-stream')
                extra = {}
                if ext == '.html':
                    # user/exported pages run in an opaque sandboxed origin: they cannot call the studio API
                    extra['Content-Security-Policy'] = ("sandbox allow-scripts allow-pointer-lock allow-downloads; "
                                                        "default-src 'none'; script-src 'unsafe-inline'; "
                                                        "style-src 'unsafe-inline'; img-src data: blob:; "
                                                        "media-src data: blob:; connect-src 'none'")
                elif ext not in ('.png', '.wav', '.json'):
                    extra['Content-Disposition'] = 'attachment'
                with open(p, 'rb') as f:
                    return self._send(200, f.read(), ctype, extra)
            if u.path.startswith(('/anim/', '/api/anim/')):   # stark_anim: runtime, webcam mocap, library
                from stark_anim import studio_api as _anim
                r = _anim.handle_get(u.path, q, self.games)
                if r is not None:
                    return self._send(*r)
            if u.path == '/api/updates':
                if classroom_on():
                    return self._send(403, {'error': 'Updates are switched off in classroom mode.'})
                from stark_update import updater as _gu
                return self._send(200, _gu.status(self.app_dir))
            if u.path == '/voice' or u.path.startswith(('/voice/', '/api/audio/')):
                from stark_audio import studio_api as _ga
                r = _ga.handle('GET', u.path, q, None, self.games)
                if r:
                    return self._send(r[0], r[2], r[1])
            return self._send(404, {'error': 'not found'})
        except SandboxError as e:
            return self._send(403, {'error': _friendly(e)})
        except FileNotFoundError as e:
            return self._send(404, {'error': _friendly(e)})
        except Exception as e:  # noqa: BLE001
            return self._err(400, e)

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {'error': 'bad host'})
        if not self._origin_ok():
            return self._send(403, {'error': 'bad origin'})
        if (self.headers.get('Content-Type') or '').split(';')[0] != 'application/json':
            return self._send(415, {'error': 'json only'})
        u = urllib.parse.urlparse(self.path)
        try:
            try:
                body = self._json_body()
            except ValueError as e:
                self.close_connection = True
                return self._send(400, {'error': _friendly(e)})
            if not isinstance(body, dict):
                raise ValueError('expected a JSON object')
            if u.path == '/api/world':
                w = validate_world(body.get('world'))
                rel = f'{slug(w["name"])}/world.json'
                write_versioned(rel, json.dumps(w, indent=1).encode('utf-8'), self.games)
                return self._send(200, {'saved': rel})
            if u.path == '/api/generate':
                seed = body.get('seed')
                if seed is not None:
                    seed = int(seed) % 10 ** 9
                return self._send(200, generate_world(seed, str(body.get('theme') or 'Spooky Swamp'),
                                                      str(body.get('size') or 'evening'),
                                                      mode='2d' if body.get('mode') == '2d' else '3d'))
            if u.path == '/api/doctor':
                return self._send(200, doctor_report(self.app_dir, self.server.server_address[1], body.get('fix') is True))
            if u.path == '/api/music':
                return self._send(200, make_soundtrack(body.get('world'), body.get('mood'), body.get('seconds'),
                                                       body.get('seed'), self.games, self.app_dir))
            if u.path == '/api/asset':
                kind = body.get('kind')
                data = base64.b64decode(body.get('b64', ''), validate=True)
                if kind == 'png' and not data.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('not a PNG')
                if kind == 'wav' and not (data[:4] == b'RIFF' and data[8:12] == b'WAVE'):
                    raise ValueError('not a WAV')
                if kind not in ('png', 'wav'):
                    raise ValueError('kind must be png or wav')
                folder = 'postcards' if body.get('folder') == 'postcards' else 'assets'
                rel = f'{slug(body.get("world"))}/{folder}/{slug(body.get("name"))}.{kind}'
                write_versioned(rel, data, self.games)
                return self._send(200, {'saved': rel})
            if u.path == '/api/quit':            # "Stop the studio": Steam (and players) see it end cleanly
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self._send(200, {'ok': True, 'message': 'Stark World Engine is stopping. You can close this tab.'})
            if u.path == '/api/import':
                return self._send(200, {'asset': import_asset(body)})
            if u.path == '/api/remix':           # 28.7: open a game someone shared (cartridge .html or world.json)
                return self._send(200, remix_world(body.get('text'), self.games))
            if u.path == '/api/safety':          # child-safety report for a Safety panel: {blocked, findings, ...}
                if self.app_dir not in sys.path:
                    sys.path.insert(0, self.app_dir)
                from stark_systems import integration as _gsi
                return self._send(200, _gsi.report(validate_world(body.get('world'))))
            if u.path == '/api/export':
                return self._send(200, {'written': export_world(body.get('name'), body.get('target'),
                                                                tier=current_tier(self.app_dir), games=self.games)})
            if u.path == '/api/open_folder':     # opens a folder INSIDE My_Games in the file manager
                p = safe_path(str(body.get('path') or ''), self.games)
                if not os.path.isdir(p):
                    raise FileNotFoundError('not a folder')
                if sys.platform.startswith('win'):
                    os.startfile(p)  # noqa: S606 - a sandboxed folder path, never a file
                else:
                    import subprocess
                    subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', p])
                return self._send(200, {'opened': os.path.relpath(p, os.path.realpath(self.games)).replace(os.sep, '/')})
            if u.path == '/api/open_unreal':
                try:
                    import ue5_export
                except ImportError:
                    return self._send(404, {'ok': False, 'error': 'missing',
                                            'guide': 'The Unreal bridge (ue5_export.py) is not in this copy. Use '
                                                     'Export > UE5 project and open the .uproject by hand.'})
                w = _gs_gate(validate_world(load_world(body.get('name'), self.games)))
                live = os.path.join(safe_path(slug(w['name']), self.games), 'export', 'ue5_live')
                st, out = ue5_export.open_unreal_action(w, live, current_tier(self.app_dir))
                if not isinstance(out, dict):
                    out = {'ok': st == 200, 'result': str(out)}
                return self._send(int(st), json.loads(json.dumps(out, default=str)))
            if u.path in ('/api/updates', '/api/download_godot') and classroom_on():
                return self._send(403, {'ok': False, 'error': 'This needs the network, so it is switched off in classroom mode.'})
            if u.path in ('/api/open_godot', '/api/download_godot'):
                try:
                    import godot_export
                except ImportError:
                    return self._send(404, {'ok': False, 'error': 'missing',
                                            'guide': 'The Godot bridge (godot_export.py) is not in this copy. Use '
                                                     'Export > Godot 4 project and open it by hand.'})
                if u.path == '/api/download_godot':
                    if current_tier(self.app_dir) not in ('pro', 'studio'):
                        return self._send(402, {'ok': False, 'error': 'tier'})
                    dest = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'StarkWorldEngine', 'godot')
                    res = godot_export.download_godot(dest, consent=body.get('consent') is True)
                    return self._send(200 if res.get('ok') else 400, json.loads(json.dumps(res, default=str)))
                w = _gs_gate(validate_world(load_world(body.get('name'), self.games)))
                live = os.path.join(safe_path(slug(w['name']), self.games), 'export', 'godot_live')
                lib = user_assets_dir() if os.path.isfile(_lib_index_path()) else None
                st, out = godot_export.open_godot_action(w, live, current_tier(self.app_dir), library=lib)
                return self._send(int(st), json.loads(json.dumps(out, default=str)))
            if u.path.startswith('/api/anim/'):              # stark_anim: takes, imports, exports, previews
                from stark_anim import studio_api as _anim
                return self._send(*_anim.handle_post(u.path, body, self.games))
            if u.path == '/api/classroom/start':
                return self._send(200, classroom_start(body.get('id'), self.games))
            if u.path == '/api/classroom/check':
                import stark_classroom
                if 'name' in body and 'world' not in body:
                    w = load_world(body.get('name'), self.games)
                else:
                    w = body.get('world')
                issues = stark_classroom.lint_world(validate_world(w))
                return self._send(200, {'ok': not issues, 'issues': issues})
            if u.path == '/api/classroom/export':
                ans = body.get('answers')
                if not isinstance(ans, list) or len(ans) > 12 or not all(isinstance(a, str) and len(a) <= 4000 for a in ans):
                    raise ValueError('answers must be a list of short texts')
                st = body.get('student')
                if st is not None and not (isinstance(st, str) and len(st) <= 80):
                    raise ValueError('student name must be text (at most 80 characters)')
                return self._send(200, {'written': classroom_export(body.get('name'), st or '', ans,
                                                                     body.get('pack'), current_tier(self.app_dir), self.games),
                                        'tier': current_tier(self.app_dir)})
            if u.path == '/api/updates':
                from stark_update import updater as _gu
                act = body.get('action')
                if act in ('enable', 'disable'):
                    _gu.set_settings(self.app_dir, check_for_updates=act == 'enable')
                elif act == 'channel':
                    _gu.set_settings(self.app_dir, channel=str(body.get('channel') or 'stable'))
                elif act == 'check':
                    st, detail = _gu.check_for_updates(self.app_dir)
                    return self._send(200, dict(_gu.status(self.app_dir), result=st, detail=detail))
                return self._send(200, _gu.status(self.app_dir))
            if u.path.startswith(('/voice/', '/api/audio/')):
                from stark_audio import studio_api as _ga
                r = _ga.handle('POST', u.path, {}, body, self.games)
                if r:
                    return self._send(r[0], r[2], r[1])
            if u.path == '/api/activate':
                if self.app_dir not in sys.path:
                    sys.path.insert(0, self.app_dir)
                import licensing
                try:
                    res = licensing.save_key(self.app_dir, body.get('key', ''))
                except (ValueError, KeyError, TypeError) as e:
                    # a rejected key is an answer, not a server error: 200 keeps the buyer's console clean
                    return self._send(200, {'ok': False, 'error': _friendly(e)})
                if isinstance(res, dict) and res.get('registered') is False and res.get('reason'):
                    return self._send(200, dict(res, ok=False, error=str(res.get('reason'))))
                return self._send(200, dict(res, ok=True) if isinstance(res, dict) else {'ok': True, 'result': res})
            return self._send(404, {'error': 'not found'})
        except SandboxError as e:
            return self._send(403, {'error': _friendly(e)})
        except FileNotFoundError as e:
            return self._send(404, {'error': _friendly(e)})
        except PermissionError as e:
            if e.errno is not None:                                  # a locked file, not a tier refusal
                return self._err(400, e)
            return self._send(402, {'error': _friendly(e) + ' - activate a Pro or Studio key in Export > Activate'})
        except Exception as e:  # noqa: BLE001
            return self._err(400, e)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 64      # 28.7: Play mode loads many models at once; a backlog of 5 could reset a save or remix request

    def handle_error(self, request, client_address):
        """Dropped or timed-out browser connections are normal; only print real errors."""
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionError, TimeoutError, socket.timeout)):
            return
        super().handle_error(request, client_address)


def make_server(port=None, games=None, app_dir=None):
    """Bind 127.0.0.1 on `port`, or on the first free port in 8505-8520."""
    handler = type('H', (Handler,), {'games': games or GAMES, 'app_dir': app_dir or APP_DIR})
    for p in ([port] if port else PORTS):
        try:
            return Server(('127.0.0.1', p), handler)
        except OSError:
            continue
    raise OSError('no free port in 8505-8520')


BRAND_DIR = os.path.join(APP_DIR, 'brand')


def _brand_uri(name):
    """A brand SVG (copied from the Stark World Engine brand kit) as a data: URI, or '' when the file is missing."""
    try:
        with open(os.path.join(BRAND_DIR, name), 'rb') as f:
            return 'data:image/svg+xml;base64,' + base64.b64encode(f.read()).decode('ascii')
    except OSError:
        return ''


def _logic_editor_js():
    """engine3d/*_editor.js, in name order: the studio's Logic tab (72: triggers, cutscenes, behaviours, variables) and the
    28.7 studio panels (76: Tune Maker, remix, guided tutorials, copy / paste / snap, itch.io zip, Watch panel). Studio
    only: never shipped inside a cartridge."""
    parts = []
    for f in sorted(glob.glob(os.path.join(ENGINE3D_DIR, '*_editor.js'))):
        try:
            with open(f, encoding='utf-8') as fh:
                parts.append(fh.read())
        except OSError:
            pass
    if not any('function renderLogic' in p for p in parts):
        parts.insert(0, 'function renderLogic(){}function lgInsp(){return"";}function lgInspBind(){}')
    return chr(10).join(parts)


def _app_page():
    return (APP_HTML.replace('%%ENGINE%%', ENGINE_JS).replace('%%GSYS%%', _gs_runtime())
            .replace('%%I18N%%', json.dumps(I18N, ensure_ascii=False)))


def app_html():
    html = _app_page().replace('%%LOGICUI%%', _logic_editor_js())
    if classroom_on():       # student-safe profile: the page carries no update controls at all
        html = re.sub(r'<h4>Updates</h4>.*?<div id="updOut" class="mute"></div>', '', html, flags=re.S)
    return html


# ---------------- moods (shared with exporters) ----------------
MOOD_PY = {
    'Bioluminescent Twilight': {'fog': [0.06, 0.09, 0.2], 'fogD': 0.022, 'sun': [0.5, 0.6, 1.0], 'sunDir': [0.3, 0.8, 0.2],
                                'amb': [0.18, 0.2, 0.35], 'ground': [0.08, 0.16, 0.14], 'leaf': [0.1, 0.7, 0.6],
                                'stone': [0.3, 0.32, 0.4], 'fx': [0.5, 1.0, 0.8]},
    'Alpine Sunset': {'fog': [0.93, 0.62, 0.45], 'fogD': 0.012, 'sun': [1.0, 0.7, 0.45], 'sunDir': [-0.6, 0.35, 0.3],
                      'amb': [0.35, 0.3, 0.35], 'ground': [0.38, 0.5, 0.28], 'leaf': [0.13, 0.38, 0.2],
                      'stone': [0.55, 0.5, 0.5], 'fx': [1.0, 0.85, 0.4]},
    'Sub-Zero Aurora': {'fog': [0.05, 0.1, 0.16], 'fogD': 0.016, 'sun': [0.6, 0.8, 1.0], 'sunDir': [0.2, 0.6, -0.5],
                        'amb': [0.25, 0.3, 0.4], 'ground': [0.85, 0.9, 0.95], 'leaf': [0.15, 0.3, 0.3],
                        'stone': [0.6, 0.7, 0.8], 'fx': [0.4, 1.0, 0.7]},
    'Crimson Citadel': {'fog': [0.3, 0.07, 0.06], 'fogD': 0.02, 'sun': [1.0, 0.45, 0.3], 'sunDir': [0.4, 0.5, 0.6],
                        'amb': [0.3, 0.12, 0.12], 'ground': [0.22, 0.1, 0.09], 'leaf': [0.4, 0.08, 0.06],
                        'stone': [0.45, 0.2, 0.18], 'fx': [1.0, 0.45, 0.15]},
    'Sunken Ruins': {'fog': [0.03, 0.3, 0.38], 'fogD': 0.03, 'sun': [0.5, 0.95, 0.9], 'sunDir': [0.1, 0.9, 0.2],
                     'amb': [0.15, 0.3, 0.32], 'ground': [0.55, 0.5, 0.35], 'leaf': [0.1, 0.5, 0.35],
                     'stone': [0.4, 0.5, 0.48], 'fx': [0.7, 1.0, 1.0]},
}

# ---------------- UI strings: 8 languages ----------------
_K = ['build', 'play', 'save', 'new3d', 'new2d', 'moods', 'tools', 'tree', 'ruin', 'rock', 'crystal', 'enemy', 'lamp',
      'erase', 'drone', 'postcard', 'pixel', 'sfx', 'export', 'cartridge', 'godot', 'ue5', 'proOnly', 'saved',
      'help3d', 'help2d', 'score', 'hp', 'win', 'gift', 'license', 'playSfx', 'saveSfx', 'useSprite', 'savePng',
      'world', 'ground', 'spike', 'goal', 'start', 'name']
_T = {
    'en': ['Build', 'Play', 'Save', 'New 3D world', 'New 2D world', 'Living World Moods', 'Tools', 'Tree', 'Ruin',
           'Rock', 'Crystal', 'Enemy', 'Lamp', 'Erase', 'Drone (P)', 'Postcard PNG', 'Pixel Art', 'SFX Forge',
           'Export', 'HTML5 cartridge', 'Godot 4 project', 'UE5 project (preview)', 'Pro and Studio tiers', 'Saved',
           'Build: click the ground to place. Play: WASD move, Space jump, J or click attack, drag to look, P drone.',
           'Build: click tiles to paint. Play: A/D move, Space jump, stomp enemies, reach the goal.', 'Score', 'HP',
           'World complete!', 'Gift certificate', 'Personal license', 'Play', 'Save WAV', 'Use as player sprite',
           'Save PNG', 'World', 'Ground', 'Spike', 'Goal', 'Start', 'Name'],
    'es': ['Construir', 'Jugar', 'Guardar', 'Nuevo mundo 3D', 'Nuevo mundo 2D', 'Ambientes vivos', 'Herramientas',
           'Arbol', 'Ruina', 'Roca', 'Cristal', 'Enemigo', 'Farol', 'Borrar', 'Dron (P)', 'Postal PNG', 'Pixel Art',
           'Forja de SFX', 'Exportar', 'Cartucho HTML5', 'Proyecto Godot 4', 'Proyecto UE5', 'Niveles Pro y Studio',
           'Guardado', 'Construir: clic en el suelo. Jugar: WASD, Espacio salta, J ataca, arrastra para mirar, P dron.',
           'Construir: clic para pintar. Jugar: A/D, Espacio salta, pisa enemigos, llega a la meta.', 'Puntos', 'Vida',
           'Mundo completado!', 'Certificado de regalo', 'Licencia personal', 'Reproducir', 'Guardar WAV',
           'Usar como sprite', 'Guardar PNG', 'Mundo', 'Suelo', 'Pinchos', 'Meta', 'Inicio', 'Nombre'],
    'fr': ['Construire', 'Jouer', 'Enregistrer', 'Nouveau monde 3D', 'Nouveau monde 2D', 'Ambiances vivantes', 'Outils',
           'Arbre', 'Ruine', 'Rocher', 'Cristal', 'Ennemi', 'Lanterne', 'Effacer', 'Drone (P)', 'Carte postale PNG',
           'Pixel Art', 'Forge SFX', 'Exporter', 'Cartouche HTML5', 'Projet Godot 4', 'Projet UE5',
           'Editions Pro et Studio', 'Enregistre', 'Construire: cliquez le sol. Jouer: WASD, Espace saute, J attaque, '
           'glisser pour regarder, P drone.', 'Construire: cliquez pour peindre. Jouer: A/D, Espace saute, ecrasez les '
           'ennemis, atteignez le but.', 'Score', 'PV', 'Monde termine !', 'Carte cadeau', 'Licence personnelle',
           'Ecouter', 'Enregistrer WAV', 'Utiliser comme sprite', 'Enregistrer PNG', 'Monde', 'Sol', 'Pics', 'But',
           'Depart', 'Nom'],
    'de': ['Bauen', 'Spielen', 'Speichern', 'Neue 3D-Welt', 'Neue 2D-Welt', 'Lebendige Stimmungen', 'Werkzeuge',
           'Baum', 'Ruine', 'Fels', 'Kristall', 'Gegner', 'Laterne', 'Loeschen', 'Drohne (P)', 'Postkarte PNG',
           'Pixel-Art', 'SFX-Schmiede', 'Export', 'HTML5-Cartridge', 'Godot-4-Projekt', 'UE5-Projekt',
           'Pro- und Studio-Stufe', 'Gespeichert', 'Bauen: Boden anklicken. Spielen: WASD, Leertaste springt, J greift '
           'an, ziehen zum Umsehen, P Drohne.', 'Bauen: Kacheln anklicken. Spielen: A/D, Leertaste springt, Gegner '
           'zertreten, Ziel erreichen.', 'Punkte', 'LP', 'Welt geschafft!', 'Geschenkgutschein', 'Persoenliche Lizenz',
           'Abspielen', 'WAV speichern', 'Als Spielfigur nutzen', 'PNG speichern', 'Welt', 'Boden', 'Stacheln', 'Ziel',
           'Start', 'Name'],
    'pt': ['Construir', 'Jogar', 'Salvar', 'Novo mundo 3D', 'Novo mundo 2D', 'Climas vivos', 'Ferramentas', 'Arvore',
           'Ruina', 'Rocha', 'Cristal', 'Inimigo', 'Lanterna', 'Apagar', 'Drone (P)', 'Cartao-postal PNG', 'Pixel Art',
           'Forja de SFX', 'Exportar', 'Cartucho HTML5', 'Projeto Godot 4', 'Projeto UE5', 'Niveis Pro e Studio',
           'Salvo', 'Construir: clique no chao. Jogar: WASD, Espaco pula, J ataca, arraste para olhar, P drone.',
           'Construir: clique para pintar. Jogar: A/D, Espaco pula, pise nos inimigos, chegue ao objetivo.', 'Pontos',
           'Vida', 'Mundo concluido!', 'Vale-presente', 'Licenca pessoal', 'Tocar', 'Salvar WAV', 'Usar como sprite',
           'Salvar PNG', 'Mundo', 'Chao', 'Espinhos', 'Objetivo', 'Inicio', 'Nome'],
    'it': ['Costruisci', 'Gioca', 'Salva', 'Nuovo mondo 3D', 'Nuovo mondo 2D', 'Atmosfere vive', 'Strumenti', 'Albero',
           'Rovina', 'Roccia', 'Cristallo', 'Nemico', 'Lanterna', 'Cancella', 'Drone (P)', 'Cartolina PNG',
           'Pixel Art', 'Fucina SFX', 'Esporta', 'Cartuccia HTML5', 'Progetto Godot 4', 'Progetto UE5',
           'Livelli Pro e Studio', 'Salvato', 'Costruisci: clicca il terreno. Gioca: WASD, Spazio salta, J attacca, '
           'trascina per guardare, P drone.', 'Costruisci: clicca per dipingere. Gioca: A/D, Spazio salta, schiaccia i '
           'nemici, raggiungi il traguardo.', 'Punti', 'Vita', 'Mondo completato!', 'Buono regalo',
           'Licenza personale', 'Ascolta', 'Salva WAV', 'Usa come sprite', 'Salva PNG', 'Mondo', 'Terreno', 'Spine',
           'Traguardo', 'Partenza', 'Nome'],
    'ja': ['ビルド', 'プレイ', '保存', '新しい3Dワールド', '新しい2Dワールド', 'リビングワールド・ムード', 'ツール', '木',
           '遺跡', '岩', 'クリスタル', '敵', 'ランプ', '消す', 'ドローン (P)', 'ポストカードPNG', 'ピクセルアート',
           'SFXフォージ', 'エクスポート', 'HTML5カートリッジ', 'Godot 4 プロジェクト', 'UE5 プロジェクト',
           'Pro/Studio 版のみ', '保存しました', 'ビルド: 地面をクリックして配置。プレイ: WASD移動、Spaceジャンプ、J攻撃、'
           'ドラッグで視点、Pドローン。', 'ビルド: タイルをクリック。プレイ: A/D移動、Spaceジャンプ、敵を踏んでゴールへ。',
           'スコア', 'HP', 'ワールドクリア!', 'ギフト券', '個人ライセンス', '再生', 'WAV保存', 'プレイヤーに使う',
           'PNG保存', 'ワールド', '地面', 'トゲ', 'ゴール', 'スタート', '名前'],
    'zh': ['搭建', '游玩', '保存', '新建3D世界', '新建2D世界', '生动世界氛围', '工具', '树', '遗迹', '岩石', '水晶', '敌人',
           '灯', '擦除', '无人机 (P)', '明信片PNG', '像素画', '音效工坊', '导出', 'HTML5卡带', 'Godot 4 项目', 'UE5 项目',
           '仅限专业版和工作室版', '已保存', '搭建: 点击地面放置。游玩: WASD移动, 空格跳, J攻击, 拖动视角, P无人机。',
           '搭建: 点击绘制图块。游玩: A/D移动, 空格跳, 踩敌人, 到达终点。', '分数', '生命', '世界完成!', '礼品卡',
           '个人许可', '播放', '保存WAV', '用作角色', '保存PNG', '世界', '地面', '尖刺', '终点', '起点', '名称'],
}
I18N = {lang: dict(zip(_K, vals)) for lang, vals in _T.items()}
_K2 = ['magic', 'undo', 'redo', 'worldTab', 'questsTab', 'dialogTab', 'itemsTab']
_T2 = {'en': ['Make me a game', 'Undo', 'Redo', 'World', 'Quests', 'Dialogue', 'Items'],
       'es': ['Hazme un juego', 'Deshacer', 'Rehacer', 'Mundo', 'Misiones', 'Dialogo', 'Objetos'],
       'fr': ['Fais-moi un jeu', 'Annuler', 'Retablir', 'Monde', 'Quetes', 'Dialogues', 'Objets'],
       'de': ['Mach mir ein Spiel', 'Rueckgaengig', 'Wiederholen', 'Welt', 'Quests', 'Dialoge', 'Gegenstaende'],
       'pt': ['Faca um jogo para mim', 'Desfazer', 'Refazer', 'Mundo', 'Missoes', 'Dialogo', 'Itens'],
       'it': ['Creami un gioco', 'Annulla', 'Ripeti', 'Mondo', 'Missioni', 'Dialoghi', 'Oggetti'],
       'ja': ['ゲームを作って', '元に戻す', 'やり直し', 'ワールド', 'クエスト', '会話', 'アイテム'],
       'zh': ['帮我做个游戏', '撤销', '重做', '世界', '任务', '对话', '物品']}
for _lang in I18N:
    I18N[_lang].update(dict(zip(_K2, _T2[_lang])))
assert all(len(v) == len(_K2) for v in _T2.values()) and set(_T2) == set(I18N)
assert all(len(v) == len(_K) for v in _T.values()), 'every language needs every UI string'

# ---------------- engine (shared by the editor and exported cartridges) ----------------
ENGINE_JS = r'''
var GS=(function(){'use strict';
var MOODS={
'Bioluminescent Twilight':{fog:[0.06,0.09,0.2],fogD:0.022,sun:[0.5,0.6,1.0],sunDir:[0.3,0.8,0.2],amb:[0.18,0.2,0.35],ground:[0.08,0.16,0.14],leaf:[[0.1,0.7,0.6],[0.3,0.4,0.9],[0.6,0.2,0.8]],stone:[0.3,0.32,0.4],fx:'fireflies',fxCol:[0.5,1.0,0.8],sky2:[0.02,0.02,0.08],zen:[0.02,0.03,0.12],water:[0.05,0.25,0.35],grade:{sat:1.15,con:1.08,tint:[0.95,1.0,1.08]},night0:0.55},
'Alpine Sunset':{fog:[0.93,0.62,0.45],fogD:0.012,sun:[1.0,0.7,0.45],sunDir:[-0.6,0.35,0.3],amb:[0.35,0.3,0.35],ground:[0.38,0.5,0.28],leaf:[[0.13,0.38,0.2],[0.2,0.45,0.25],[0.1,0.3,0.18]],stone:[0.55,0.5,0.5],fx:'pollen',fxCol:[1.0,0.85,0.4],sky2:[0.45,0.35,0.6],zen:[0.25,0.35,0.7],water:[0.15,0.4,0.55],grade:{sat:1.12,con:1.06,tint:[1.06,1.0,0.94]},night0:0.0},
'Sub-Zero Aurora':{fog:[0.05,0.1,0.16],fogD:0.016,sun:[0.6,0.8,1.0],sunDir:[0.2,0.6,-0.5],amb:[0.25,0.3,0.4],ground:[0.85,0.9,0.95],leaf:[[0.15,0.3,0.3],[0.2,0.35,0.4],[0.8,0.9,1.0]],stone:[0.6,0.7,0.8],fx:'snow',fxCol:[1,1,1],aurora:true,sky2:[0.0,0.02,0.06],zen:[0.01,0.03,0.09],water:[0.1,0.3,0.45],grade:{sat:1.05,con:1.1,tint:[0.94,1.0,1.1]},night0:0.6},
'Crimson Citadel':{fog:[0.3,0.07,0.06],fogD:0.02,sun:[1.0,0.45,0.3],sunDir:[0.4,0.5,0.6],amb:[0.3,0.12,0.12],ground:[0.22,0.1,0.09],leaf:[[0.4,0.08,0.06],[0.25,0.1,0.08],[0.55,0.2,0.1]],stone:[0.45,0.2,0.18],fx:'embers',fxCol:[1.0,0.45,0.15],sky2:[0.08,0.0,0.02],zen:[0.12,0.02,0.04],water:[0.35,0.08,0.05],grade:{sat:1.1,con:1.12,tint:[1.1,0.96,0.92]},night0:0.3},
'Sunken Ruins':{fog:[0.03,0.3,0.38],fogD:0.03,sun:[0.5,0.95,0.9],sunDir:[0.1,0.9,0.2],amb:[0.15,0.3,0.32],ground:[0.55,0.5,0.35],leaf:[[0.1,0.5,0.35],[0.2,0.6,0.3],[0.6,0.3,0.4]],stone:[0.4,0.5,0.48],fx:'bubbles',fxCol:[0.7,1.0,1.0],sky2:[0.0,0.08,0.15],zen:[0.0,0.12,0.22],water:[0.05,0.4,0.45],grade:{sat:1.1,con:1.04,tint:[0.94,1.04,1.04]},night0:0.15}
};
function rng(seed){var a=(seed|0)>>>0;return function(){a=(a+0x6D2B79F5)>>>0;var t=a;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296;};}
function sub(a,b){return[a[0]-b[0],a[1]-b[1],a[2]-b[2]];}
function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}
function cross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}
function norm(a){var l=Math.sqrt(dot(a,a))||1;return[a[0]/l,a[1]/l,a[2]/l];}
function persp(fov,asp,n,f){var t=1/Math.tan(fov/2);return[t/asp,0,0,0,0,t,0,0,0,0,(f+n)/(n-f),-1,0,0,2*f*n/(n-f),0];}
function ortho(l,r,b,t,n,f){return[2/(r-l),0,0,0,0,2/(t-b),0,0,0,0,-2/(f-n),0,-(r+l)/(r-l),-(t+b)/(t-b),-(f+n)/(f-n),1];}
function lookAt(e,c,u){var z=norm(sub(e,c)),x=norm(cross(u,z)),y=cross(z,x);return[x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,e),-dot(y,e),-dot(z,e),1];}
function mul(a,b){var o=new Array(16);for(var c=0;c<4;c++)for(var r=0;r<4;r++){var s=0;for(var k=0;k<4;k++)s+=a[k*4+r]*b[c*4+k];o[c*4+r]=s;}return o;}
function clamp(v,a,b){return v<a?a:v>b?b:v;}
function lerp(a,b,t){return a+(b-a)*t;}
function mix3(a,b,t){return[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t,a[2]+(b[2]-a[2])*t];}
function scl3(a,k){return[a[0]*k,a[1]*k,a[2]*k];}
function hexCol(h,def){if(Array.isArray(h)&&h.length===3)return h.map(Number);var m=/^#?([0-9a-f]{6})$/i.exec(String(h||''));if(!m)return def||[1,1,1];var v=parseInt(m[1],16);return[(v>>16&255)/255,(v>>8&255)/255,(v&255)/255];}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
var SFX={jump:{wave:'square',freq:260,slide:1.4,dur:0.18,decay:1.5,vol:0.3,noise:0},coin:{wave:'square',freq:880,slide:0.6,dur:0.2,decay:1,vol:0.25,noise:0},hit:{wave:'noise',freq:200,slide:-0.5,dur:0.22,decay:2.5,vol:0.55,noise:0.7},laser:{wave:'saw',freq:1200,slide:-0.8,dur:0.25,decay:2,vol:0.3,noise:0},boom:{wave:'noise',freq:90,slide:-0.6,dur:0.6,decay:3,vol:0.7,noise:1},power:{wave:'sine',freq:330,slide:2,dur:0.45,decay:1,vol:0.4,noise:0},
quest:{wave:'sine',freq:520,slide:0.5,dur:0.5,decay:0.8,vol:0.35,noise:0},levelup:{wave:'square',freq:392,slide:1.0,dur:0.7,decay:0.7,vol:0.3,noise:0},door:{wave:'saw',freq:110,slide:-0.3,dur:0.4,decay:1.5,vol:0.35,noise:0.3},portal:{wave:'sine',freq:180,slide:3,dur:0.9,decay:1,vol:0.4,noise:0.1},talk:{wave:'square',freq:660,slide:0.1,dur:0.06,decay:1,vol:0.15,noise:0},magic:{wave:'saw',freq:700,slide:-0.5,dur:0.35,decay:1.5,vol:0.25,noise:0.1}};
function sfxSamples(p,rate){rate=rate||22050;var n=Math.max(1,Math.floor((p.dur||0.3)*rate)),out=new Float32Array(n),ph=0,f=p.freq||440,r=rng(p.seed||7);
for(var i=0;i<n;i++){var t=i/n,fr=Math.max(20,f*(1+(p.slide||0)*t)),v;ph+=fr/rate;var w=p.wave||'square';
if(w==='square')v=(ph%1)<0.5?1:-1;else if(w==='saw')v=2*(ph%1)-1;else if(w==='sine')v=Math.sin(2*Math.PI*ph);else v=r()*2-1;
if(p.noise)v=v*(1-p.noise)+(r()*2-1)*p.noise;
out[i]=v*Math.min(1,t*40)*Math.pow(1-t,p.decay||2)*(p.vol||0.5);}
return out;}
function wavBytes(s,rate){rate=rate||22050;var b=new ArrayBuffer(44+s.length*2),d=new DataView(b);
function W(o,str){for(var i=0;i<str.length;i++)d.setUint8(o+i,str.charCodeAt(i));}
W(0,'RIFF');d.setUint32(4,36+s.length*2,true);W(8,'WAVE');W(12,'fmt ');d.setUint32(16,16,true);d.setUint16(20,1,true);d.setUint16(22,1,true);
d.setUint32(24,rate,true);d.setUint32(28,rate*2,true);d.setUint16(32,2,true);d.setUint16(34,16,true);W(36,'data');d.setUint32(40,s.length*2,true);
for(var i=0;i<s.length;i++)d.setInt16(44+i*2,Math.round(clamp(s[i],-1,1)*32767),true);return new Uint8Array(b);}
var AC=null,MUTE=false;
function playSfx(p){if(MUTE||!p)return;try{if(!AC)AC=new(window.AudioContext||window.webkitAudioContext)();var s=sfxSamples(p,22050),buf=AC.createBuffer(1,s.length,22050);buf.copyToChannel(s,0);var src=AC.createBufferSource();src.buffer=buf;src.connect(AC.destination);src.start();}catch(e){}}
function sfxFor(world,k){return (world&&world.sfx&&world.sfx[k])||SFX[k];}
function Input(target){var keys={},self=this;this.keys=keys;this.pressed={};
this.down=function(e){if(e.target&&/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))return;var cd=KM(e.code);if(!keys[cd])self.pressed[cd]=true;keys[cd]=true;if(self.onKey)self.onKey(cd,e);if(/Space|Arrow|Tab/.test(e.code))e.preventDefault();};
this.up=function(e){keys[KM(e.code)]=false;};
this.blur=function(){for(var k in keys)keys[k]=false;};
window.addEventListener('keydown',this.down);window.addEventListener('keyup',this.up);window.addEventListener('blur',this.blur);
this.take=function(c){var v=self.pressed[c];self.pressed[c]=false;return v;};
this.dispose=function(){window.removeEventListener('keydown',this.down);window.removeEventListener('keyup',this.up);window.removeEventListener('blur',this.blur);};}
function fit(canvas,scale){var d=(window.devicePixelRatio||1)*(scale||1),w=Math.max(2,Math.floor(canvas.clientWidth*d)),h=Math.max(2,Math.floor(canvas.clientHeight*d));if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}}
var ITEMS={
crystal:{name:'Glow Crystal',kind:'quest',col:'#7fffd0',desc:'A humming shard of light.'},
potion:{name:'Healing Draught',kind:'consumable',heal:3,col:'#ff5577',desc:'Restores 3 HP. Hotkey H.'},
elixir:{name:'Grand Elixir',kind:'consumable',heal:99,col:'#ffcc33',desc:'Restores all HP.'},
key:{name:'Old Key',kind:'key',col:'#ffd84a',desc:'Opens a locked door.'},
gem:{name:'Star Gem',kind:'treasure',gold:25,col:'#c78bff',desc:'Worth 25 gold.'},
wood_sword:{name:'Driftwood Sword',kind:'equip',slot:'weapon',atk:1,col:'#a0703a',desc:'+1 attack.'},
iron_blade:{name:'Iron Blade',kind:'equip',slot:'weapon',atk:2,col:'#c8d0d8',desc:'+2 attack.'},
moon_blade:{name:'Moonsilver Blade',kind:'equip',slot:'weapon',atk:4,col:'#b0e8ff',desc:'+4 attack.'},
leather:{name:'Leather Vest',kind:'equip',slot:'armor',def:1,col:'#8a5a33',desc:'+1 defence.'},
warden_plate:{name:'Warden Plate',kind:'equip',slot:'armor',def:2,hp:2,col:'#7a8aa0',desc:'+2 defence, +2 max HP.'},
swift_boots:{name:'Swift Boots',kind:'equip',slot:'boots',speed:1.6,col:'#5fd3b5',desc:'Run faster.'},
ember_charm:{name:'Ember Charm',kind:'equip',slot:'charm',atk:1,hp:1,col:'#ff7a33',desc:'+1 attack, +1 max HP.'}
};
var ENEMY={
grunt:{name:'Bog Slime',hp:3,dmg:1,speed:2.6,sight:14,xp:10,gold:2,col:[0.75,0.2,0.35]},
archer:{name:'Thorn Archer',hp:2,dmg:1,speed:2.3,sight:18,xp:15,gold:3,ranged:true,col:[0.35,0.6,0.25]},
brute:{name:'Stone Brute',hp:8,dmg:2,speed:1.7,sight:12,xp:30,gold:6,col:[0.5,0.45,0.42]},
wisp:{name:'Bog Wisp',hp:2,dmg:1,speed:3.6,sight:16,xp:12,gold:2,fly:true,col:[0.5,0.8,1.0]},
boss:{name:'Ancient Warden',hp:40,dmg:2,speed:2.3,sight:26,xp:200,gold:60,boss:true,col:[0.55,0.15,0.6]}
};
var DIFF={easy:{hp:0.7,dmg:0.5},normal:{hp:1,dmg:1},hard:{hp:1.5,dmg:1.5}};
var ABILITIES=[{lvl:2,key:'Shift',name:'Dash',cd:1.2},{lvl:3,key:'K',name:'Whirl',cd:4},{lvl:4,key:'F',name:'Star Bolt',cd:1.5}];
var UI_CSS='.gsui{position:absolute;inset:0;pointer-events:none;font:14px system-ui,Segoe UI,sans-serif;color:#f4f1ea;overflow:hidden;z-index:5}'+
'.gsui *{box-sizing:border-box}.gsui .pn{background:rgba(10,14,22,.72);border:1px solid rgba(255,255,255,.14);border-radius:12px;backdrop-filter:blur(6px);box-shadow:0 8px 30px rgba(0,0,0,.35)}'+
'.gsui .hl{position:absolute;left:12px;top:12px;padding:10px 12px;min-width:210px}.gsui .bar{height:10px;border-radius:6px;background:rgba(255,255,255,.12);overflow:hidden;margin:4px 0}.gsui .bar i{display:block;height:100%;border-radius:6px;transition:width .25s}'+
'.gsui .hp i{background:linear-gradient(90deg,#ff4d6d,#ff8fa3)}.gsui .xp i{background:linear-gradient(90deg,#5fd3b5,#b8ffe9)}.gsui .bb i{background:linear-gradient(90deg,#b5179e,#ff4d6d)}'+
'.gsui .ab{display:inline-block;margin:4px 4px 0 0;padding:2px 6px;border-radius:6px;background:rgba(95,211,181,.18);font-size:11px}.gsui .ab.cd{opacity:.4}'+
'.gsui .tr{position:absolute;right:12px;top:12px;max-width:300px;padding:10px 12px}.gsui .tr b{color:#ffd166}.gsui .tr .st{font-size:12px;color:#cfe;margin:2px 0 6px}.gsui .tr .st.ok{color:#7d7;text-decoration:line-through}'+
'.gsui .toasts{position:absolute;left:50%;top:70px;transform:translateX(-50%);display:flex;flex-direction:column;gap:6px;align-items:center}'+
'.gsui .toast{padding:8px 16px;animation:gsin .35s cubic-bezier(.2,1.6,.4,1);font-weight:600}.gsui .toast.q{border-color:#ffd166;color:#ffe9a8}.gsui .toast.l{border-color:#5fd3b5;color:#b8ffe9}'+
'@keyframes gsin{from{transform:scale(.6) translateY(-10px);opacity:0}to{transform:none;opacity:1}}'+
'.gsui .prompt{position:absolute;left:50%;bottom:120px;transform:translateX(-50%);padding:6px 14px;font-weight:600}.gsui kbd{background:#fff;color:#111;border-radius:4px;padding:0 6px;margin-right:6px;font:600 12px monospace}'+
'.gsui .dlg{position:absolute;left:50%;bottom:18px;transform:translateX(-50%);width:min(720px,94%);padding:14px 18px;pointer-events:auto;animation:gsin .25s ease-out}.gsui .dlg .sp{color:#ffd166;font-weight:700;margin-bottom:4px}.gsui .dlg .tx{font-size:16px;line-height:1.45;min-height:44px}'+
'.gsui .ch{display:block;width:100%;text-align:left;margin-top:6px;padding:8px 10px;border-radius:8px;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.06);color:inherit;font:inherit;cursor:pointer}.gsui .ch:hover{background:rgba(95,211,181,.25);border-color:#5fd3b5}'+
'.gsui .modal{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:min(560px,94%);max-height:86%;overflow:auto;padding:16px 18px;pointer-events:auto;animation:gsin .25s ease-out}.gsui .modal h2{margin:0 0 10px;font-size:20px;color:#ffd166}'+
'.gsui .row{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0}.gsui .btn{padding:8px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.2);background:rgba(255,255,255,.08);color:inherit;font:inherit;cursor:pointer}.gsui .btn:hover{border-color:#5fd3b5;background:rgba(95,211,181,.2)}'+
'.gsui .it{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:8px;background:rgba(255,255,255,.05);margin:4px 0}.gsui .sw{width:14px;height:14px;border-radius:4px;flex:none}.gsui .mute{color:#9fb0bf;font-size:12px}'+
'.gsui .boss{position:absolute;left:50%;top:14px;transform:translateX(-50%);width:min(460px,70%);padding:6px 12px;text-align:center;font-weight:700}'+
'.gsui .big{position:absolute;left:0;right:0;top:34%;text-align:center;pointer-events:none}.gsui .big h1{font-size:44px;margin:0;text-shadow:0 4px 24px rgba(0,0,0,.6);letter-spacing:1px;animation:gsin .6s cubic-bezier(.2,1.4,.4,1)}.gsui .big p{font-size:16px;margin:6px 0;text-shadow:0 2px 8px #000}'+
'.gsui .title{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;background:radial-gradient(ellipse at center,rgba(0,0,0,.25),rgba(0,0,0,.82));pointer-events:auto;text-align:center;padding:20px}'+
'.gsui .title h1{font-size:clamp(34px,7vw,72px);margin:0;letter-spacing:2px;background:linear-gradient(180deg,#fff,#ffd166);-webkit-background-clip:text;background-clip:text;color:transparent;animation:gsin .8s cubic-bezier(.2,1.4,.4,1)}'+
'.gsui .title p{max-width:640px;color:#dde;line-height:1.5}.gsui .cf{position:absolute;top:-12px;width:9px;height:14px;border-radius:2px;animation:gsfall 2.4s linear forwards}@keyframes gsfall{to{transform:translateY(110vh) rotate(720deg)}}'+
'.gsui .mm{position:absolute;right:12px;bottom:12px;width:150px;height:150px;border-radius:50%;opacity:.85}';
function Game(world,opt){
opt=opt||{};var G=this;G.world=world;G.levels=[world].concat(Array.isArray(world.levels)?world.levels:[]);G.li=0;G.opt=opt;
G.diff=DIFF[world.difficulty]?world.difficulty:'normal';
var pl=world.player||{};
G.st={hp:+pl.hp||6,lvl:1,xp:0,gold:0,inv:{},eq:{},score:0};G.q={};G.gone={};G.flags={};G.cp=null;G.won=false;G.busyT=0;G.cds={};G.clock=null;G.playT=0;
G.allQuests=[];G.levels.forEach(function(L,i){(L.quests||[]).forEach(function(q){if(q&&q.id){q._lvl=i;G.allQuests.push(q);}});});
(pl.items||[]).forEach(function(id){G.give(id,1,true);});
G.mkUI();
}
Game.prototype.items=function(){var o={},w=this.world;for(var k in ITEMS)o[k]=ITEMS[k];this.levels.forEach(function(L){var it=L.items||{};for(var k2 in it)o[k2]=it[k2];});return o;};
Game.prototype.item=function(id){return this.items()[id]||{name:String(id).replace(/_/g,' '),kind:'quest',col:'#dddddd'};};
Game.prototype.dialogs=function(){var o={};this.levels.forEach(function(L){var d=L.dialogs||{};for(var k in d)o[k]=d[k];});return o;};
Game.prototype.quest=function(id){for(var i=0;i<this.allQuests.length;i++)if(this.allQuests[i].id===id)return this.allQuests[i];return null;};
Game.prototype.npcName=function(id){var n=null;this.levels.forEach(function(L){(L.objects||[]).forEach(function(o){if(o.t==='npc'&&o.id===id&&!n)n=o.name||id;});});return n||String(id||'someone');};
Game.prototype.stats=function(){var s=this.st,its=this.items(),atk=1+Math.floor((s.lvl-1)/2),def=0,hp=(+((this.world.player||{}).hp)||6)+(s.lvl-1),spd=1;
for(var sl in s.eq){var it=its[s.eq[sl]];if(!it)continue;atk+=+it.atk||0;def+=+it.def||0;hp+=+it.hp||0;if(it.speed)spd*=+it.speed;}
return{atk:Math.max(1,atk),def:def,maxHp:Math.max(1,hp),speed:clamp(spd,0.2,4)};};
Game.prototype.has=function(id,n){return(this.st.inv[id]||0)>=(n||1);};
Game.prototype.give=function(id,n,quiet){n=n||1;var it=this.item(id);
if(it.kind==='treasure'&&it.gold){this.st.gold+=it.gold*n;if(!quiet)this.toast('+'+(it.gold*n)+' gold ('+it.name+')');return;}
this.st.inv[id]=(this.st.inv[id]||0)+n;if(!quiet)this.toast('+ '+it.name+(n>1?' x'+n:''));
if(it.kind==='equip'&&!this.st.eq[it.slot]){this.st.eq[it.slot]=id;if(!quiet)this.toast('Equipped '+it.name,'l');}
this.event('collect',{item:id,n:n});};
Game.prototype.take=function(id,n){n=n||1;if(!this.has(id,n))return false;this.st.inv[id]-=n;if(this.st.inv[id]<=0){delete this.st.inv[id];for(var s in this.st.eq)if(this.st.eq[s]===id)delete this.st.eq[s];}return true;};
Game.prototype.use=function(id){var it=this.item(id),s=this.st;if(!this.has(id))return;
if(it.kind==='consumable'){var mx=this.stats().maxHp;if(s.hp>=mx&&!it.atkBuff){this.toast('Already at full health');return;}this.take(id,1);s.hp=Math.min(mx,s.hp+(+it.heal||1));this.toast(it.name+': +'+(+it.heal||1)+' HP','l');playSfx(SFX.power);}
else if(it.kind==='equip'){if(s.eq[it.slot]===id){delete s.eq[it.slot];this.toast('Unequipped '+it.name);}else{s.eq[it.slot]=id;this.toast('Equipped '+it.name,'l');}s.hp=Math.min(s.hp,this.stats().maxHp);playSfx(SFX.coin);}
this.refresh();};
Game.prototype.quaff=function(){var its=this.items(),best=null;for(var id in this.st.inv){var it=its[id];if(it&&it.kind==='consumable'&&(!best||(+it.heal||0)<(+its[best].heal||0)))best=id;}if(best)this.use(best);else this.toast('No potions');};
Game.prototype.addXp=function(n){var s=this.st;s.xp+=n;s.score+=n;var need;while(s.xp>=(need=this.xpNeed())){s.xp-=need;s.lvl++;s.hp=this.stats().maxHp;playSfx(SFX.levelup);this.toast('Level up! You are now level '+s.lvl,'l');this.confetti(40);
ABILITIES.forEach(function(a){if(a.lvl===s.lvl)this.toast('New ability: '+a.name+' ('+a.key+')','q');},this);}this.refresh();};
Game.prototype.xpNeed=function(){return Math.round(40*Math.pow(this.st.lvl,1.45));};
Game.prototype.hasAbility=function(name){var s=this.st;return ABILITIES.some(function(a){return a.name===name&&s.lvl>=a.lvl;});};
Game.prototype.dmgMul=function(){return DIFF[this.diff].dmg;};Game.prototype.hpMul=function(){return DIFF[this.diff].hp;};
Game.prototype.hurt=function(d){var s=this.st,st=this.stats();d=Math.max(0.5,d*this.dmgMul()-st.def*0.5);s.hp=Math.max(0,s.hp-d);this.refresh();if(s.hp<=0){this.dead();return true;}return false;};
Game.prototype.dead=function(){var s=this.st;s.gold=Math.floor(s.gold*0.9);s.hp=this.stats().maxHp;this.big('You fell...','Back to the last checkpoint. (Lost 10% gold)',2.2);
if(this.engine&&this.engine.respawn)this.engine.respawn(this.cp&&this.cp.li===this.li?this.cp:null);};
Game.prototype.startQuest=function(id,quiet){var q=this.quest(id);if(!q||this.q[id])return;this.q[id]={state:'active',step:0,n:0};this.initStep(q);if(!quiet){this.toast('New quest: '+(q.title||id),'q');playSfx(SFX.quest);}this.refresh();this.checkStep(q);};
Game.prototype.initStep=function(q){var r=this.q[q.id],s=(q.steps||[])[r.step];r.n=0;if(s&&s.type==='collect')r.n=Math.min(this.st.inv[s.item]||0,+s.count||1);this.foes(q);};
Game.prototype.foes=function(q){var r=this.q[q.id],s=r&&(q.steps||[])[r.step];if(!s||s.type!=='defeat'||s.id||r.state!=='active')return;var lv=s.level!=null&&s.level!==''?+s.level:q._lvl;
if(lv===this.li&&this.engine&&this.engine.ensureFoes)this.engine.ensureFoes(s.kind,(+s.count||1)-r.n);};
Game.prototype.stepDone=function(q){var r=this.q[q.id],s=(q.steps||[])[r.step];return s&&r.n>=(+s.count||1);};
Game.prototype.checkStep=function(q){var r=this.q[q.id];if(!r||r.state!=='active')return;var guard=0;
while(this.stepDone(q)&&guard++<20){var s=q.steps[r.step];if(s.type==='collect'&&s.consume)this.take(s.item,+s.count||1);r.step++;if(r.step>=q.steps.length){this.completeQuest(q);return;}this.initStep(q);this.toast('Next: '+this.stepText(q.steps[r.step],q),'q');}
if(!(q.steps||[]).length)this.completeQuest(q);};
Game.prototype.completeQuest=function(q){var r=this.q[q.id];r.state='done';var rw=q.reward||{};this.toast('Quest complete: '+(q.title||q.id),'q');playSfx(SFX.power);this.confetti(70);
if(rw.gold){this.st.gold+=+rw.gold;this.toast('+'+rw.gold+' gold');}(rw.items||(rw.item?[rw.item]:[])).forEach(function(id){this.give(id,1);},this);if(rw.xp)this.addXp(+rw.xp);
if(q.next)this.startQuest(q.next);this.refresh();this.checkWin();};
Game.prototype.event=function(type,d){var self=this,changed=false;
this.allQuests.forEach(function(q){var r=self.q[q.id];if(!r||r.state!=='active')return;var s=(q.steps||[])[r.step];if(!s||s.type!==type)return;if(s.level!=null&&s.level!==''&&+s.level!==self.li)return;var ok=false,add=1;
if(type==='collect'){ok=s.item===d.item;add=d.n||1;}
else if(type==='defeat'){ok=(!s.kind||s.kind==='any'||s.kind===d.kind)&&(!s.id||s.id===d.id);}
else if(type==='talk'||type==='escort'){ok=s.npc===d.npc;}
else if(type==='reach'){ok=Math.hypot(d.x-(+s.x||0),d.z-(+s.z||0))<(+s.r||3);}
if(ok){r.n=Math.min(+s.count||1,r.n+add);changed=true;self.checkStep(q);}});
if(changed)this.refresh();};
Game.prototype.stepText=function(s,q){if(!s)return'';if(s.text)return s.text;var c=+s.count||1;
if(s.type==='collect')return'Collect '+this.item(s.item).name+(c>1?' ('+c+')':'');if(s.type==='defeat')return'Defeat '+(s.id?this.npcName(s.id):(s.kind&&s.kind!=='any'?(ENEMY[s.kind]||{}).name||s.kind:'enemies'))+(c>1?' ('+c+')':'');
if(s.type==='talk')return'Talk to '+this.npcName(s.npc);if(s.type==='escort')return'Escort '+this.npcName(s.npc)+' to '+(s.label||'safety');return'Reach '+(s.label||'the marked spot');};
Game.prototype.active=function(){var self=this;return this.allQuests.filter(function(q){return self.q[q.id]&&self.q[q.id].state==='active';});};
Game.prototype.curSteps=function(){var self=this;return this.active().map(function(q){return{q:q,s:(q.steps||[])[self.q[q.id].step]};}).filter(function(x){return!!x.s;});};
Game.prototype.escortStep=function(npc){var r=null;this.curSteps().forEach(function(x){if(x.s.type==='escort'&&x.s.npc===npc)r=x.s;});return r;};
Game.prototype.checkWin=function(){if(this.won||!this.allQuests.length)return;var self=this,fin=this.allQuests.filter(function(q){return q.final;});var list=fin.length?fin:this.allQuests;
if(list.every(function(q){return self.q[q.id]&&self.q[q.id].state==='done';}))this.win();};
Game.prototype.win=function(){if(this.won)return;this.won=true;this.st.score+=100;playSfx(SFX.power);this.confetti(140);var self=this;
setTimeout(function(){self.credits();},1600);this.refresh();};
Game.prototype.cond=function(c){if(!c)return true;var q=this.q;
if(c.has&&!this.has(c.has,+c.n||1))return false;if(c.lacks&&this.has(c.lacks))return false;if(c.done&&!(q[c.done]&&q[c.done].state==='done'))return false;
if(c.active&&!(q[c.active]&&q[c.active].state==='active'))return false;if(c.notStarted&&q[c.notStarted])return false;if(c.flag&&!this.flags[c.flag])return false;if(c.minLevel&&this.st.lvl<+c.minLevel)return false;return true;};
Game.prototype.talk=function(npc){var D=this.dialogs()[npc.dialog];this.event('talk',{npc:npc.id});playSfx(SFX.talk);
if(!D){this.showNode(npc,{nodes:{a:{text:npc.text||'Lovely day for an adventure, isn\'t it?'}}},'a');return;}
var start=D.start||Object.keys(D.nodes||{})[0];(D.branches||[]).some(function(b){if(this.cond(b.if)&&D.nodes&&D.nodes[b.node]){start=b.node;return true;}return false;},this);this.showNode(npc,D,start);};
Game.prototype.showNode=function(npc,D,id){var self=this,node=(D.nodes||{})[id];if(!node){this.closeDlg();return;}
if(node.do)this.act(node.do);var box=this.el.dlg;box.hidden=false;this.dlgOpen=true;
var ch=(node.choices||[]).filter(function(c){return self.cond(c.if);});if(!ch.length)ch=[{text:node.next?'Continue':'Farewell',goto:node.next,end:!node.next}];
box.innerHTML='<div class="sp">'+esc(node.speaker||npc.name||'Stranger')+'</div><div class="tx">'+esc(node.text||'')+'</div>';
this.dlgCh=ch.map(function(c,i){var b=document.createElement('button');b.className='ch';b.innerHTML='<kbd>'+(i+1)+'</kbd>'+esc(c.text||'...');b.onclick=function(){self.pick(npc,D,c);};box.appendChild(b);return b;});};
Game.prototype.pick=function(npc,D,c){playSfx(SFX.talk);this.act(c);if(c.goto&&!c.end)this.showNode(npc,D,c.goto);else this.closeDlg();};
Game.prototype.act=function(c){if(!c)return;var self=this;if(c.take)this.take(c.take,+c.n||1);if(c.give)[].concat(c.give).forEach(function(g){self.give(g,1);});if(c.gold){this.st.gold+=+c.gold;this.toast((c.gold>0?'+':'')+c.gold+' gold');}
if(c.start)this.startQuest(c.start);if(c.advance)this.event('talk',{npc:c.advance});if(c.flag)this.flags[c.flag]=true;if(c.xp)this.addXp(+c.xp);if(c.heal)this.st.hp=this.stats().maxHp;
if(c.complete){var q=this.quest(c.complete);if(q&&this.q[c.complete]&&this.q[c.complete].state==='active')this.completeQuest(q);}if(c.win)this.win();this.refresh();};
Game.prototype.closeDlg=function(){this.el.dlg.hidden=true;this.dlgOpen=false;this.dlgCh=null;this.busyT=0.25;};
Game.prototype.busy=function(){return this.dlgOpen||!!this.modal||this.titleOn||this.busyT>0;};
Game.prototype.key=function(slot){if(!this._fp){var h=2166136261>>>0,str=this.levels.map(function(L){return(L.mode||'')+'|'+(L.seed||'')+'|'+(L.objects||[]).map(function(o){return(o&&o.t)+(o&&o.id||'');}).join(',')+'|'+(Array.isArray(L.tiles)?L.tiles.join(''):'');}).join('#');
for(var i=0;i<str.length;i++){h^=str.charCodeAt(i);h=Math.imul(h,16777619)>>>0;}this._fp=h.toString(36);}return'gs_save|'+(this.world.name||'game')+'|'+this._fp+'|'+slot;};
Game.prototype.okSave=function(j){if(!j||typeof j!=='object'||!j.st||typeof j.st!=='object'||j.li!==(j.li|0)||j.li<0||j.li>=this.levels.length)return false;var s=j.st;
s.inv=s.inv&&typeof s.inv==='object'?s.inv:{};s.eq=s.eq&&typeof s.eq==='object'?s.eq:{};['hp','lvl','xp','gold','score'].forEach(function(k){s[k]=isFinite(+s[k])?+s[k]:(k==='lvl'?1:0);});s.lvl=Math.max(1,s.lvl|0);
if(j.pos&&!(isFinite(+j.pos.x)&&isFinite(+j.pos.z)))j.pos=null;if(j.q&&typeof j.q!=='object')j.q={};return true;};
Game.prototype.snapshot=function(){var p=this.engine&&this.engine.getPos?this.engine.getPos():null;return JSON.stringify({v:2,li:this.li,pos:p,st:this.st,q:this.q,gone:this.gone,flags:this.flags,cp:this.cp,won:this.won,diff:this.diff,clock:this.clock,t:Date.now()});};
Game.prototype.save=function(slot,quiet){try{localStorage.setItem(this.key(slot),this.snapshot());if(!quiet)this.toast('Saved to '+(slot==='auto'?'autosave':'slot '+slot),'l');return true;}catch(e){if(!quiet)this.toast('Could not save (browser storage blocked)');return false;}};
Game.prototype.slotInfo=function(slot){try{var j=JSON.parse(localStorage.getItem(this.key(slot))||'null');if(!j||!this.okSave(j))return null;return'Lv '+j.st.lvl+' - '+((this.levels[j.li]||{}).name||'Level '+(j.li+1))+' - '+new Date(j.t).toLocaleString();}catch(e){return null;}};
Game.prototype.load=function(slot){var j;try{j=JSON.parse(localStorage.getItem(this.key(slot))||'null');}catch(e){j=null;}if(!j){this.toast('That slot is empty');return false;}
if(!this.okSave(j)){this.toast('That save does not fit this version of the game');return false;}
this.st=j.st;this.q=j.q||{};this.gone=j.gone||{};this.flags=j.flags||{};this.cp=j.cp||null;this.won=!!j.won;this.diff=DIFF[j.diff]?j.diff:this.diff;this.clock=j.clock;
var self=this,pos=j.pos;this.closeModal();if(j.li!==this.li&&this.opt.switchLevel){this.opt.switchLevel(j.li,function(){if(pos&&self.engine)self.engine.setPos(pos);});}else if(this.engine){this.engine.reload&&this.engine.reload();if(pos)this.engine.setPos(pos);}
this.toast('Loaded '+(slot==='auto'?'autosave':'slot '+slot),'l');this.refresh();return true;};
Game.prototype.checkpoint=function(pos){this.cp={li:this.li,x:pos.x,z:pos.z};this.st.hp=this.stats().maxHp;this.save('auto',true);this.toast('Checkpoint - progress saved','l');playSfx(SFX.power);this.refresh();};
Game.prototype.mkUI=function(){if(!document.getElementById('gs-ui-css')){var st=document.createElement('style');st.id='gs-ui-css';st.textContent=UI_CSS;document.head.appendChild(st);}
var root=document.createElement('div');root.className='gsui';var h='<div class="pn hl"></div><div class="pn tr" hidden></div><div class="toasts"></div><div class="pn prompt" hidden></div><div class="pn boss" hidden></div><div class="big" hidden></div><div class="pn dlg" hidden></div>';
root.innerHTML=h;var q=function(c){return root.querySelector('.'+c);};this.el={root:root,hl:q('hl'),tr:q('tr'),toasts:q('toasts'),prompt:q('prompt'),boss:q('boss'),big:q('big'),dlg:q('dlg')};this.root=root;};
Game.prototype.attach=function(parent){if(this.root.parentNode!==parent)parent.appendChild(this.root);this.refresh();};
Game.prototype.toast=function(msg,kind){var last=this.el.toasts.lastChild;if(last&&last.textContent===msg)return;var t=document.createElement('div');t.className='pn toast'+(kind?' '+kind:'');t.textContent=msg;this.el.toasts.appendChild(t);while(this.el.toasts.children.length>4)this.el.toasts.removeChild(this.el.toasts.firstChild);
setTimeout(function(){if(t.parentNode)t.parentNode.removeChild(t);},3200);if(this.opt.onToast)this.opt.onToast(msg);};
Game.prototype.big=function(title,sub,secs){var b=this.el.big;b.hidden=false;b.innerHTML='<h1>'+esc(title)+'</h1>'+(sub?'<p>'+esc(sub)+'</p>':'');clearTimeout(this._bigT);if(secs)this._bigT=setTimeout(function(){b.hidden=true;},secs*1000);};
Game.prototype.confetti=function(n){var r=this.root,cols=['#ffd166','#5fd3b5','#ff4d6d','#9b5de5','#f4f1ea','#00bbf9'];for(var i=0;i<n;i++){var c=document.createElement('i');c.className='cf';c.style.left=(Math.random()*100)+'%';c.style.background=cols[i%cols.length];c.style.animationDelay=(Math.random()*0.6)+'s';c.style.animationDuration=(1.8+Math.random()*1.4)+'s';r.appendChild(c);(function(c){setTimeout(function(){if(c.parentNode)c.parentNode.removeChild(c);},3600);})(c);}};
Game.prototype.prompt=function(txt){var p=this.el.prompt;if(!txt){p.hidden=true;return;}p.hidden=false;if(p._t!==txt){p._t=txt;p.innerHTML=txt;}};
Game.prototype.bossBar=function(name,f){var b=this.el.boss;if(name==null){b.hidden=true;return;}b.hidden=false;b.innerHTML=esc(name)+'<div class="bar bb"><i style="width:'+Math.round(clamp(f,0,1)*100)+'%"></i></div>';};
Game.prototype.refresh=function(){var s=this.st,st=this.stats(),self=this;if(!this.el)return;s.hp=Math.min(s.hp,st.maxHp);
var ab=ABILITIES.map(function(a){var ok=s.lvl>=a.lvl,cd=(self.cds[a.name]||0)>0;return'<span class="ab'+(ok&&!cd?'':' cd')+'" title="'+(ok?'':'Unlocks at level '+a.lvl)+'">'+a.key+' '+a.name+'</span>';}).join('');
this.el.hl.innerHTML='<div><b>Lv '+s.lvl+'</b> &nbsp;HP '+Math.ceil(s.hp)+'/'+st.maxHp+' &nbsp;<span style="color:#ffd166">'+s.gold+' gold</span></div><div class="bar hp"><i style="width:'+Math.round(s.hp/st.maxHp*100)+'%"></i></div>'+
'<div class="bar xp"><i style="width:'+Math.round(s.xp/this.xpNeed()*100)+'%"></i></div><div class="mute">ATK '+st.atk+' &middot; DEF '+st.def+' &middot; '+esc((this.levels[this.li]||{}).name||this.world.name)+'</div><div>'+ab+'</div>';
var cs=this.curSteps(),tr=this.el.tr;if(this.extraGoal)cs=cs.concat([{q:{title:'Main goal'},s:{text:this.extraGoal}}]);
if(!cs.length){tr.hidden=true;}else{tr.hidden=false;tr.innerHTML=cs.slice(0,3).map(function(x){var r=self.q[x.q.id],c=+x.s.count||1;return'<div><b>'+esc(x.q.title||x.q.id)+'</b><div class="st">'+esc(self.stepText(x.s,x.q))+(r&&c>1?' - '+r.n+'/'+c:'')+'</div></div>';}).join('')+'<div class="mute">L quests &middot; I bag &middot; Esc menu</div>';}
if(this.modal==='quests')this.openQuests();if(this.modal==='bag')this.openBag();};
Game.prototype.openModal=function(kind,html){this.closeModal();var m=document.createElement('div');m.className='pn modal';m.innerHTML=html;this.root.appendChild(m);this.modalEl=m;this.modal=kind;return m;};
Game.prototype.closeModal=function(){if(this.modalEl&&this.modalEl.parentNode)this.modalEl.parentNode.removeChild(this.modalEl);this.modalEl=null;this.modal=null;this.busyT=0.15;};
Game.prototype.openQuests=function(){var self=this,h='<h2>Quest log</h2>';var list=this.allQuests.filter(function(q){return self.q[q.id];});
if(!list.length)h+='<p class="mute">No quests yet. Look for people with a gold <b>!</b> above their heads and press E to talk.</p>';
list.forEach(function(q){var r=self.q[q.id];h+='<div class="it" style="display:block"><b style="color:'+(r.state==='done'?'#7d7':'#ffd166')+'">'+esc(q.title||q.id)+(r.state==='done'?' (done)':'')+'</b><div class="mute">'+esc(q.desc||'')+'</div>'+
(q.steps||[]).map(function(s,i){return'<div class="mute" style="color:'+(i<r.step||r.state==='done'?'#7d7':i===r.step?'#fff':'#789')+'">'+(i<r.step||r.state==='done'?'&#10003; ':'&bull; ')+esc(self.stepText(s,q))+'</div>';}).join('')+'</div>';});
var m=this.openModal('quests',h+'<div class="row"><button class="btn" data-x>Close (L)</button></div>');m.querySelector('[data-x]').onclick=function(){self.closeModal();};};
Game.prototype.openBag=function(){var self=this,its=this.items(),s=this.st,st=this.stats(),h='<h2>Bag</h2><div class="mute">ATK '+st.atk+' &middot; DEF '+st.def+' &middot; Max HP '+st.maxHp+' &middot; '+s.gold+' gold</div>';var ids=Object.keys(s.inv);
if(!ids.length)h+='<p class="mute">Empty. Open chests and pick things up to fill it.</p>';
ids.forEach(function(id){var it=its[id]||self.item(id),eq=s.eq[it.slot]===id,act=it.kind==='consumable'?'Use':it.kind==='equip'?(eq?'Unequip':'Equip'):'';
h+='<div class="it"><span class="sw" style="background:'+esc(Array.isArray(it.col)?'#888':it.col||'#888')+'"></span><div style="flex:1"><b>'+esc(it.name)+'</b>'+(s.inv[id]>1?' x'+s.inv[id]:'')+(eq?' <span class="mute">(equipped)</span>':'')+'<div class="mute">'+esc(it.desc||it.kind)+'</div></div>'+(act?'<button class="btn" data-use="'+esc(id)+'">'+act+'</button>':'')+'</div>';});
var m=this.openModal('bag',h+'<div class="row"><button class="btn" data-x>Close (I)</button></div>');m.querySelector('[data-x]').onclick=function(){self.closeModal();};
[].forEach.call(m.querySelectorAll('[data-use]'),function(b){b.onclick=function(){self.use(b.getAttribute('data-use'));};});};
Game.prototype.openMenu=function(){var self=this,h='<h2>Paused</h2><div class="row"><button class="btn" data-r>Resume</button></div><h3 style="margin:10px 0 4px">Save</h3>';
[1,2,3].forEach(function(i){h+='<div class="it"><div style="flex:1">Slot '+i+' <div class="mute">'+esc(self.slotInfo(i)||'empty')+'</div></div><button class="btn" data-s="'+i+'">Save</button><button class="btn" data-l="'+i+'">Load</button></div>';});
h+='<div class="it"><div style="flex:1">Autosave (checkpoints) <div class="mute">'+esc(this.slotInfo('auto')||'empty')+'</div></div><button class="btn" data-l="auto">Load</button></div>';
h+='<h3 style="margin:10px 0 4px">Options</h3><div class="row"><span class="mute" style="align-self:center">Difficulty</span>'+['easy','normal','hard'].map(function(d){return'<button class="btn" data-d="'+d+'" style="'+(self.diff===d?'border-color:#5fd3b5':'')+'">'+d+'</button>';}).join('')+'</div>';
h+='<div class="row"><span class="mute" style="align-self:center">Graphics</span>'+['auto','high','medium','low'].map(function(d){return'<button class="btn" data-g="'+d+'" style="'+(getQ()===d?'border-color:#5fd3b5':'')+'">'+d+'</button>';}).join('')+'<button class="btn" data-m>'+(MUTE?'Sound: off':'Sound: on')+'</button></div>';
h+='<p class="mute">Move WASD/arrows &middot; Jump Space &middot; Attack J or click &middot; Talk/open E &middot; Potion H &middot; Dash Shift &middot; Whirl K &middot; Bolt F &middot; Camera: drag or Z/C &middot; Quests L &middot; Bag I</p>';
var m=this.openModal('menu',h);m.querySelector('[data-r]').onclick=function(){self.closeModal();};
[].forEach.call(m.querySelectorAll('[data-s]'),function(b){b.onclick=function(){self.save(+b.getAttribute('data-s'));self.openMenu();};});
[].forEach.call(m.querySelectorAll('[data-l]'),function(b){b.onclick=function(){var v=b.getAttribute('data-l');self.load(v==='auto'?'auto':+v);};});
[].forEach.call(m.querySelectorAll('[data-d]'),function(b){b.onclick=function(){self.diff=b.getAttribute('data-d');self.openMenu();};});
[].forEach.call(m.querySelectorAll('[data-g]'),function(b){b.onclick=function(){setQ(b.getAttribute('data-g'));self.openMenu();};});
m.querySelector('[data-m]').onclick=function(){MUTE=!MUTE;self.openMenu();};};
Game.prototype.titleScreen=function(onStart){var self=this,w=this.world,cont=this.slotInfo('auto')||this.slotInfo(1);this.titleOn=true;
var m=document.createElement('div');m.className='title';m.innerHTML='<h1>'+esc(w.name||'Untitled Adventure')+'</h1><p>'+esc(w.story||'')+'</p><div class="row"><button class="btn" data-n style="font-size:18px;padding:12px 26px">New game</button>'+(cont?'<button class="btn" data-c style="font-size:18px;padding:12px 26px">Continue</button>':'')+'</div><p class="mute">'+(this.levels.length>1?this.levels.length+' levels &middot; ':'')+'Press Enter to start &middot; Made with Stark World Engine</p>';
this.root.appendChild(m);this.titleEl=m;function go(load){if(!self.titleOn)return;self.titleOn=false;if(m.parentNode)m.parentNode.removeChild(m);playSfx(SFX.portal);if(onStart)onStart();if(load){if(!self.load('auto'))self.load(1);}}
m.querySelector('[data-n]').onclick=function(){go(false);};if(cont)m.querySelector('[data-c]').onclick=function(){go(true);};this._titleGo=go;};
Game.prototype.credits=function(){var self=this,w=this.world,s=this.st,mins=Math.max(1,Math.round(this.playT/60));
var h='<h2 style="text-align:center;font-size:28px">'+esc(w.name||'The End')+' - complete!</h2><p style="text-align:center">Level '+s.lvl+' &middot; '+s.gold+' gold &middot; '+Object.keys(this.q).length+' quests &middot; about '+mins+' min</p>'+
'<div style="text-align:center;line-height:1.8">'+this.levels.map(function(L,i){return esc(L.name||('Level '+(i+1)));}).join('<br>')+'</div><p class="mute" style="text-align:center">A game by '+esc(w.author||'you')+' &middot; Made with Stark World Engine</p>'+(window.GS_CREDITS&&window.GS_CREDITS.length?'<p class="mute" style="text-align:center;font-size:11px;line-height:1.5">Art: '+window.GS_CREDITS.map(function(c){return esc(String(c).split(' - ').slice(0,3).join(', '));}).join('<br>')+'</p>':'')+'<div class="row" style="justify-content:center"><button class="btn" data-x>Keep exploring</button></div>';
var m=this.openModal('credits',h);m.querySelector('[data-x]').onclick=function(){self.closeModal();};if(this.opt.onWin)this.opt.onWin();};
Game.prototype.keys=function(c){if(this.titleOn){if(c==='Enter'||c==='Space'){this._titleGo(false);}return true;}
if(this.dlgOpen){var m=/^Digit([1-9])$/.exec(c);if(m&&this.dlgCh&&this.dlgCh[+m[1]-1]){this.dlgCh[+m[1]-1].click();return true;}if((c==='KeyE'||c==='Enter'||c==='Space')&&this.dlgCh&&this.dlgCh.length===1){this.dlgCh[0].click();return true;}if(c==='Escape'){this.closeDlg();return true;}return true;}
if(c==='Escape'||c==='KeyM'){if(this.modal)this.closeModal();else this.openMenu();return true;}
if(c==='KeyL'){if(this.modal==='quests')this.closeModal();else this.openQuests();return true;}
if(c==='KeyI'||c==='Tab'){if(this.modal==='bag')this.closeModal();else this.openBag();return true;}
if(c==='KeyH'&&!this.modal){this.quaff();return true;}return!!this.modal;};
Game.prototype.update=function(dt){if(this.busyT>0)this.busyT-=dt;this.playT+=dt;var ch=false;for(var k in this.cds){if(this.cds[k]>0){this.cds[k]-=dt;if(this.cds[k]<=0)ch=true;}}if(ch)this.refresh();};
Game.prototype.destroy=function(){if(this.root.parentNode)this.root.parentNode.removeChild(this.root);};
var QUAL='auto';try{QUAL=localStorage.getItem('gs_quality')||'auto';}catch(e){}
function getQ(){return QUAL;}function setQ(q){QUAL=q;try{localStorage.setItem('gs_quality',q);}catch(e){}}
var GLSL_TERRAIN='uniform float uSeed;uniform float uAmp;\nfloat th(vec2 p){float s=uSeed;float r=sin(p.x*0.13+s)*0.6+cos(p.y*0.11+s*0.7)*0.6+sin((p.x+p.y)*0.05+s)*0.8+cos(p.x*0.045-p.y*0.06+s*0.4)*0.9+sin(p.x*0.29+p.y*0.23+s*1.3)*0.18;return uAmp*r*0.55;}\n';
var GLSL_SKY='uniform vec3 uZen;uniform vec3 uHor;uniform vec3 uSunDir;uniform vec3 uSunCol;uniform float uNight;uniform float uTime;uniform float uAur;uniform float uFlash;\n'+
'vec3 sky(vec3 d){float y=max(d.y,0.0);vec3 c=mix(uHor,uZen,pow(y,0.5));float sd=max(dot(d,normalize(uSunDir)),0.0);\n'+
'c+=uSunCol*(pow(sd,900.0)*12.0+pow(sd,10.0)*0.28+pow(sd,3.0)*0.08)*(1.0-uNight*0.6);\n'+
'if(uNight>0.01){vec3 q=floor(d*190.0);float h=fract(sin(dot(q,vec3(12.9898,78.233,45.164)))*43758.5453);float tw=0.6+0.4*sin(uTime*3.0+h*90.0);c+=vec3(step(0.9965,h))*uNight*smoothstep(0.0,0.2,y)*2.2*tw;}\n'+
'if(uAur>0.0){float a=sin(d.x*5.0+uTime*0.25+sin(d.z*3.0+uTime*0.17)*2.5)*0.5+0.5;float a2=sin(d.z*7.0-uTime*0.2+d.x*2.0)*0.5+0.5;float band=smoothstep(0.12,0.3,y)*smoothstep(0.8,0.38,y);c+=mix(vec3(0.1,1.0,0.55),vec3(0.55,0.25,1.0),a2)*pow(a,4.0)*band*uAur*2.2;}\n'+
'vec2 cp=d.xz/(y+0.12)*0.9+vec2(uTime*0.012,uTime*0.004);float cl=sin(cp.x*1.9+sin(cp.y*1.3))*sin(cp.y*1.7+sin(cp.x*0.8)*1.5)*0.5+0.5;cl=smoothstep(0.55,0.95,cl)*smoothstep(0.02,0.3,y)*0.4;\n'+
'c=mix(c,uHor*1.15+uSunCol*0.12*(1.0-uNight),cl);c+=vec3(uFlash);\n'+
'if(d.y<0.0)c=mix(uHor,uHor*0.55,min(1.0,-d.y*3.0));return c;}\n';
var VS3=`#version 300 es
layout(location=0) in vec3 aPos;layout(location=1) in vec3 aNor;layout(location=2) in vec3 iOff;layout(location=3) in vec3 iScl;layout(location=4) in vec3 iCol;layout(location=5) in vec4 iMisc;layout(location=6) in vec3 aCol;
uniform mat4 uVP;uniform float uTime;uniform float uTerrain;
out vec3 vNor;out vec3 vCol;out vec3 vW;out float vEmit;out float vSpec;out float vAo;
void main(){vec3 p=aPos*iScl;float c=cos(iMisc.z),s=sin(iMisc.z);p=vec3(c*p.x+s*p.z,p.y,-s*p.x+c*p.z);
vec3 n=aNor/max(iScl,vec3(0.001));n=vec3(c*n.x+s*n.z,n.y,-s*n.x+c*n.z);
float h=aPos.y+0.5;if(iMisc.y>0.0){p.x+=sin(uTime*1.7+iOff.x*0.7+iOff.z)*iMisc.y*h;p.z+=cos(uTime*1.3+iOff.z*0.5)*iMisc.y*0.5*h;}
vec3 w=p+iOff;vW=w;vNor=n;vCol=iCol*aCol;vEmit=iMisc.x;vSpec=iMisc.w;vAo=uTerrain>0.5?1.0:mix(0.55,1.0,clamp(h*1.6,0.0,1.0));gl_Position=uVP*vec4(w,1.0);}`;
var FS3=`#version 300 es
precision highp float;precision highp sampler2DShadow;
in vec3 vNor;in vec3 vCol;in vec3 vW;in float vEmit;in float vSpec;in float vAo;
uniform vec3 uSun;uniform vec3 uLCol;uniform vec3 uSkyAmb;uniform vec3 uGndAmb;uniform vec3 uFog;uniform float uFogD;uniform vec3 uEye;
uniform sampler2DShadow uShadow;uniform mat4 uSVP;uniform float uShadowOn;uniform int uNL;uniform vec4 uLP[12];uniform vec3 uLC[12];uniform float uTerrain;
out vec4 o;
float hash(vec3 p){return fract(sin(dot(p,vec3(127.1,311.7,74.7)))*43758.5453);}
float vn(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);return mix(mix(mix(hash(i),hash(i+vec3(1,0,0)),f.x),mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z);}
float shadow(vec3 w,vec3 n){vec4 s=uSVP*vec4(w+n*0.06,1.0);vec3 q=s.xyz/s.w*0.5+0.5;if(q.x<0.0||q.x>1.0||q.y<0.0||q.y>1.0||q.z>1.0)return 1.0;
float t=1.0/float(textureSize(uShadow,0).x);float r=0.0;for(int i=-1;i<=1;i++)for(int j=-1;j<=1;j++)r+=texture(uShadow,vec3(q.xy+vec2(float(i),float(j))*t*1.4,q.z-0.0012));
float e=max(max(abs(q.x-0.5),abs(q.y-0.5))*2.0-0.85,0.0)/0.15;return mix(r/9.0,1.0,clamp(e,0.0,1.0));}
void main(){vec3 gn=normalize(vNor);float ef=clamp(vEmit,0.0,1.0);
vec3 bp=vW*(uTerrain>0.5?1.7:3.3);float b1=vn(bp),b2=vn(bp+vec3(5.2,1.3,7.7));vec3 n=normalize(gn+(vec3(b1,0.0,b2)-0.5)*(uTerrain>0.5?0.55:0.3)*(1.0-ef));
vec3 L=normalize(uSun);float ndl=max(dot(n,L),0.0);float sh=uShadowOn>0.5?shadow(vW,gn):1.0;
vec3 V=normalize(uEye-vW);vec3 H=normalize(L+V);float sp=pow(max(dot(n,H),0.0),mix(12.0,90.0,vSpec))*vSpec;
float grain=mix(0.86,1.1,vn(vW*0.9))*mix(0.94,1.04,vn(vW*7.0));vec3 alb=vCol*grain;
vec3 amb=mix(uGndAmb,uSkyAmb,n.y*0.5+0.5)*vAo;
vec3 c=alb*(amb+uLCol*ndl*sh*1.2)+uLCol*sp*sh*0.7;
for(int i=0;i<12;i++){if(i>=uNL)break;vec3 d=uLP[i].xyz-vW;float dl=length(d);float att=clamp(1.0-dl/uLP[i].w,0.0,1.0);att*=att;vec3 l=d/max(dl,0.001);
c+=alb*uLC[i]*(max(dot(n,l),0.0)*0.85+0.15)*att*1.7+uLC[i]*pow(max(dot(n,normalize(l+V)),0.0),40.0)*att*(vSpec+0.1);}
float rim=pow(1.0-max(dot(n,V),0.0),3.0);c+=(uSkyAmb*0.6+uLCol*0.2)*rim*0.4*vAo*(1.0-uTerrain);
c=mix(c,vCol*(1.15+vEmit*1.6),ef);
float dist=length(vW-uEye);float f=1.0-exp(-pow(dist*uFogD,1.35));f*=mix(1.0,0.7,clamp(vW.y*0.08,0.0,1.0));f*=1.0-ef*0.5;c=mix(c,uFog,clamp(f,0.0,1.0));
o=vec4(c,1.0);}`;
var VSSH=`#version 300 es
layout(location=0) in vec3 aPos;layout(location=2) in vec3 iOff;layout(location=3) in vec3 iScl;layout(location=5) in vec4 iMisc;
uniform mat4 uVP;uniform float uTime;out float vE;
void main(){vec3 p=aPos*iScl;float c=cos(iMisc.z),s=sin(iMisc.z);p=vec3(c*p.x+s*p.z,p.y,-s*p.x+c*p.z);float h=aPos.y+0.5;if(iMisc.y>0.0){p.x+=sin(uTime*1.7+iOff.x*0.7+iOff.z)*iMisc.y*h;}
vE=iMisc.x;gl_Position=uVP*vec4(p+iOff,1.0);}`;
var FSSH=`#version 300 es
precision mediump float;in float vE;void main(){if(vE>0.45)discard;}`;
var VSFS=`#version 300 es
layout(location=0) in vec2 aP;out vec2 vUv;void main(){vUv=aP*0.5+0.5;gl_Position=vec4(aP,0.0,1.0);}`;
var FSSKY=`#version 300 es
precision highp float;in vec2 vUv;uniform vec3 uF;uniform vec3 uR;uniform vec3 uU;uniform float uTh;uniform float uAsp;out vec4 o;
`+GLSL_SKY+`void main(){vec2 p=vUv*2.0-1.0;vec3 d=normalize(uF+uR*p.x*uTh*uAsp+uU*p.y*uTh);o=vec4(sky(d),1.0);}`;
var VSWAT=`#version 300 es
layout(location=0) in vec2 aP;uniform mat4 uVP;uniform float uSize;uniform float uLvl;out vec3 vW;void main(){vW=vec3(aP.x*uSize,uLvl,aP.y*uSize);gl_Position=uVP*vec4(vW,1.0);}`;
var FSWAT=`#version 300 es
precision highp float;in vec3 vW;uniform vec3 uEye;uniform vec3 uWCol;uniform vec3 uFog;uniform float uFogD;uniform float uLvl;uniform vec3 uSkyAmb;out vec4 o;
`+GLSL_SKY+GLSL_TERRAIN+`void main(){vec2 p=vW.xz;float t=uTime;
vec3 n=normalize(vec3(sin(p.x*1.3+t*1.6)*0.05+sin(p.x*0.7-p.y*0.9+t*1.1)*0.05+cos(p.y*2.7+t*2.3)*0.025+sin((p.x+p.y)*4.1+t*3.1)*0.012,1.0,cos(p.y*1.1+t*1.4)*0.05+sin(p.x*0.5+p.y*0.8+t*0.9)*0.05+sin(p.x*2.3-t*2.6)*0.025+cos((p.x-p.y)*3.7+t*2.7)*0.012));
vec3 V=normalize(uEye-vW);vec3 R=reflect(-V,n);R.y=abs(R.y);vec3 rc=sky(R);float fr=0.03+0.97*pow(1.0-max(dot(n,V),0.0),5.0);
float depth=uLvl-th(p);vec3 body=uWCol*mix(1.3,0.35,clamp(depth*0.5,0.0,1.0))*(uSkyAmb*1.4+uSunCol*0.35);
vec3 c=mix(body,rc,clamp(fr*1.2,0.0,1.0));c+=uSunCol*pow(max(dot(R,normalize(uSunDir)),0.0),220.0)*4.0*(1.0-uNight*0.7);
float foam=smoothstep(0.32,0.0,depth)*(0.55+0.45*sin(t*2.2+p.x*2.6+p.y*1.9));c=mix(c,vec3(0.92,0.97,1.0)*(uSkyAmb+uSunCol*0.6),foam*0.65);
float a=clamp(0.62+fr*0.35+foam*0.5,0.0,1.0)*smoothstep(-0.02,0.06,depth);
float dist=length(vW-uEye);float f=1.0-exp(-pow(dist*uFogD,1.35));c=mix(c,uFog,clamp(f,0.0,1.0));o=vec4(c,a);}`;
var FSBRIGHT=`#version 300 es
precision highp float;in vec2 vUv;uniform sampler2D uT;uniform vec2 uPx;out vec4 o;
void main(){vec3 c=texture(uT,vUv+uPx*vec2(-1,-1)).rgb+texture(uT,vUv+uPx*vec2(1,-1)).rgb+texture(uT,vUv+uPx*vec2(-1,1)).rgb+texture(uT,vUv+uPx*vec2(1,1)).rgb;c*=0.25;
float l=dot(c,vec3(0.2126,0.7152,0.0722));o=vec4(c*smoothstep(1.1,2.6,l),1.0);}`;
var FSBLUR=`#version 300 es
precision highp float;in vec2 vUv;uniform sampler2D uT;uniform vec2 uDir;out vec4 o;
void main(){vec3 c=texture(uT,vUv).rgb*0.227;c+=(texture(uT,vUv+uDir*1.385).rgb+texture(uT,vUv-uDir*1.385).rgb)*0.316;c+=(texture(uT,vUv+uDir*3.231).rgb+texture(uT,vUv-uDir*3.231).rgb)*0.07;o=vec4(c,1.0);}`;
var FSCOMP=`#version 300 es
precision highp float;in vec2 vUv;uniform sampler2D uScene;uniform sampler2D uBloom;uniform float uBloomK;uniform float uExpo;uniform float uSat;uniform float uCon;uniform vec3 uTint;uniform float uVig;uniform float uTime;uniform float uHurt;out vec4 o;
vec3 aces(vec3 x){return clamp((x*(2.51*x+0.03))/(x*(2.43*x+0.59)+0.14),0.0,1.0);}
void main(){vec3 c=texture(uScene,vUv).rgb;c+=texture(uBloom,vUv).rgb*uBloomK;c*=uExpo;c=aces(c);c=pow(c,vec3(1.0/1.12));
float l=dot(c,vec3(0.299,0.587,0.114));c=mix(vec3(l),c,uSat);c=(c-0.5)*uCon+0.5;c*=uTint;
vec2 q=vUv-0.5;float v=1.0-dot(q,q)*uVig;c*=v;c=mix(c,vec3(0.8,0.05,0.08),uHurt*smoothstep(0.15,0.6,length(q)));
float g=fract(sin(dot(vUv*vec2(1734.1,971.3)+uTime,vec2(12.9898,78.233)))*43758.5453);c+=(g-0.5)*0.018;
c=clamp(c,0.0,1.0);o=vec4(c,dot(c,vec3(0.299,0.587,0.114)));}`;
var FSFXAA=`#version 300 es
precision highp float;in vec2 vUv;uniform sampler2D uT;uniform vec2 uPx;out vec4 o;
void main(){vec3 rgbM=texture(uT,vUv).rgb;float lM=texture(uT,vUv).a;float lNW=texture(uT,vUv+vec2(-1,-1)*uPx).a,lNE=texture(uT,vUv+vec2(1,-1)*uPx).a,lSW=texture(uT,vUv+vec2(-1,1)*uPx).a,lSE=texture(uT,vUv+vec2(1,1)*uPx).a;
float mn=min(lM,min(min(lNW,lNE),min(lSW,lSE))),mx=max(lM,max(max(lNW,lNE),max(lSW,lSE)));if(mx-mn<max(0.0312,mx*0.125)){o=vec4(rgbM,1.0);return;}
vec2 dir=vec2(-((lNW+lNE)-(lSW+lSE)),((lNW+lSW)-(lNE+lSE)));float rd=1.0/(min(abs(dir.x),abs(dir.y))+max((lNW+lNE+lSW+lSE)*0.03125,0.0078));dir=clamp(dir*rd,-8.0,8.0)*uPx;
vec3 a=0.5*(texture(uT,vUv+dir*(1.0/3.0-0.5)).rgb+texture(uT,vUv+dir*(2.0/3.0-0.5)).rgb);vec3 b=a*0.5+0.25*(texture(uT,vUv-dir*0.5).rgb+texture(uT,vUv+dir*0.5).rgb);
float lb=dot(b,vec3(0.299,0.587,0.114));o=vec4((lb<mn||lb>mx)?a:b,1.0);}`;
function shader(gl,type,src){var s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));return s;}
function program(gl,vs,fs,names){var p=gl.createProgram();gl.attachShader(p,shader(gl,gl.VERTEX_SHADER,vs));gl.attachShader(p,shader(gl,gl.FRAGMENT_SHADER,fs));gl.linkProgram(p);
if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(p));var U={p:p};names.forEach(function(n){U[n]=gl.getUniformLocation(p,n);});return U;}
function flat(tris){var out=[];for(var i=0;i<tris.length;i+=3){var a=tris[i],b=tris[i+1],c=tris[i+2],n=norm(cross(sub(b,a),sub(c,a)));[a,b,c].forEach(function(p){out.push(p[0],p[1],p[2],n[0],n[1],n[2]);});}return new Float32Array(out);}
function cubeGeo(){var F=[[0,0,1],[0,0,-1],[1,0,0],[-1,0,0],[0,1,0],[0,-1,0]],out=[];
F.forEach(function(n){var u=n[1]!==0?[1,0,0]:[0,1,0],v=cross(n,u);
function c(a,b){return[(n[0]+u[0]*a+v[0]*b)*0.5,(n[1]+u[1]*a+v[1]*b)*0.5,(n[2]+u[2]*a+v[2]*b)*0.5];}
[c(-1,-1),c(1,-1),c(1,1),c(-1,-1),c(1,1),c(-1,1)].forEach(function(p){out.push(p[0],p[1],p[2],n[0],n[1],n[2]);});});
return new Float32Array(out);}
function ring(seg,r,y,off){var o=[];for(var i=0;i<seg;i++){var a=(i+(off||0))/seg*Math.PI*2;o.push([Math.sin(a)*r,y,Math.cos(a)*r]);}return o;}
function cylGeo(seg){var t=[],b=ring(seg,0.5,-0.5),u=ring(seg,0.5,0.5);for(var i=0;i<seg;i++){var j=(i+1)%seg;t.push(b[i],b[j],u[j],b[i],u[j],u[i],[0,0.5,0],u[i],u[j],[0,-0.5,0],b[j],b[i]);}return flat(t);}
function coneGeo(seg,off){var t=[],b=ring(seg,0.5,-0.5,off);for(var i=0;i<seg;i++){var j=(i+1)%seg;t.push(b[i],b[j],[0,0.5,0],[0,-0.5,0],b[j],b[i]);}return flat(t);}
function icoTris(){var g=(1+Math.sqrt(5))/2,v=[[-1,g,0],[1,g,0],[-1,-g,0],[1,-g,0],[0,-1,g],[0,1,g],[0,-1,-g],[0,1,-g],[g,0,-1],[g,0,1],[-g,0,-1],[-g,0,1]].map(norm);
var f=[[0,11,5],[0,5,1],[0,1,7],[0,7,10],[0,10,11],[1,5,9],[5,11,4],[11,10,2],[10,7,6],[7,1,8],[3,9,4],[3,4,2],[3,2,6],[3,6,8],[3,8,9],[4,9,5],[2,4,11],[6,2,10],[8,6,7],[9,8,1]];return{v:v,f:f};}
function rockGeo(){var I=icoTris(),r=rng(99),v=I.v.map(function(p){var k=0.38+r()*0.16;return[p[0]*k,p[1]*k*0.8,p[2]*k];}),t=[];I.f.forEach(function(f){t.push(v[f[0]],v[f[1]],v[f[2]]);});return flat(t);}
function sphereGeo(){var I=icoTris(),t=[];I.f.forEach(function(f){var a=I.v[f[0]],b=I.v[f[1]],c=I.v[f[2]],ab=norm(scl3([a[0]+b[0],a[1]+b[1],a[2]+b[2]],0.5)),bc=norm(scl3([b[0]+c[0],b[1]+c[1],b[2]+c[2]],0.5)),ca=norm(scl3([c[0]+a[0],c[1]+a[1],c[2]+a[2]],0.5));
[[a,ab,ca],[ab,b,bc],[ca,bc,c],[ab,bc,ca]].forEach(function(tr){tr.forEach(function(p){t.push(p[0]*0.5,p[1]*0.5,p[2]*0.5,p[0],p[1],p[2]);});});});return new Float32Array(t);}
function octaGeo(){var T=[0,0.5,0],B=[0,-0.5,0],e=ring(6,0.5,0.05),t=[];for(var i=0;i<6;i++){var j=(i+1)%6;t.push(e[i],e[j],T,e[j],e[i],B);}return flat(t);}
var MESHES=['cube','cyl','cone','pyr','rock','sphere','gem'];
function terrainH(x,z,seed,amp){var s=seed||0;var r=Math.sin(x*0.13+s)*0.6+Math.cos(z*0.11+s*0.7)*0.6+Math.sin((x+z)*0.05+s)*0.8+Math.cos(x*0.045-z*0.06+s*0.4)*0.9+Math.sin(x*0.29+z*0.23+s*1.3)*0.18;return(amp==null?1:amp)*r*0.55;}
function Renderer(canvas){
var gl=canvas.getContext('webgl2',{antialias:false,alpha:false,preserveDrawingBuffer:false,powerPreference:'high-performance'});if(!gl)return null;
var R={gl:gl,b:{},hdr:!!gl.getExtension('EXT_color_buffer_float'),q:'medium',scale:1,fbW:0,fbH:0};gl.getExtension('OES_texture_float_linear');
R.main=program(gl,VS3,FS3,['uVP','uTime','uTerrain','uSun','uLCol','uSkyAmb','uGndAmb','uFog','uFogD','uEye','uShadow','uSVP','uShadowOn','uNL','uLP','uLC']);
R.sh=program(gl,VSSH,FSSH,['uVP','uTime']);
R.sky=program(gl,VSFS,FSSKY,['uF','uR','uU','uTh','uAsp','uZen','uHor','uSunDir','uSunCol','uNight','uTime','uAur','uFlash']);
R.wat=program(gl,VSWAT,FSWAT,['uVP','uSize','uLvl','uEye','uWCol','uFog','uFogD','uSkyAmb','uZen','uHor','uSunDir','uSunCol','uNight','uTime','uAur','uFlash','uSeed','uAmp']);
R.bright=program(gl,VSFS,FSBRIGHT,['uT','uPx']);R.blur=program(gl,VSFS,FSBLUR,['uT','uDir']);
R.comp=program(gl,VSFS,FSCOMP,['uScene','uBloom','uBloomK','uExpo','uSat','uCon','uTint','uVig','uTime','uHurt']);R.fxaa=program(gl,VSFS,FSFXAA,['uT','uPx']);
var geos={cube:cubeGeo(),cyl:cylGeo(8),cone:coneGeo(7),pyr:coneGeo(4,0.5),rock:rockGeo(),sphere:sphereGeo(),gem:octaGeo()},CAP=12000;
MESHES.forEach(function(k){var m={verts:geos[k].length/6,cap:CAP,data:new Float32Array(CAP*13),n:0,sn:0,dirtyStatic:true};m.vao=gl.createVertexArray();gl.bindVertexArray(m.vao);
var vb=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,vb);gl.bufferData(gl.ARRAY_BUFFER,geos[k],gl.STATIC_DRAW);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,3,gl.FLOAT,false,24,0);gl.enableVertexAttribArray(1);gl.vertexAttribPointer(1,3,gl.FLOAT,false,24,12);
m.ib=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,m.ib);gl.bufferData(gl.ARRAY_BUFFER,m.data.byteLength,gl.DYNAMIC_DRAW);
[[2,3,0],[3,3,12],[4,3,24],[5,4,36]].forEach(function(a){gl.enableVertexAttribArray(a[0]);gl.vertexAttribPointer(a[0],a[1],gl.FLOAT,false,52,a[2]);gl.vertexAttribDivisor(a[0],1);});R.b[k]=m;});
gl.bindVertexArray(null);gl.vertexAttrib3f(6,1,1,1);
R.fsq=gl.createVertexArray();gl.bindVertexArray(R.fsq);var qb=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,qb);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,2,gl.FLOAT,false,8,0);gl.bindVertexArray(null);
R.add=function(k,x,y,z,sx,sy,sz,c,emit,sway,rot,spec){var m=R.b[k];if(m.n>=m.cap){if(m.cap>=1048576)return;var nd=new Float32Array(m.cap*2*13);nd.set(m.data);m.data=nd;m.cap*=2;m.realloc=true;}var d=m.data,o=m.n*13;d[o]=x;d[o+1]=y;d[o+2]=z;d[o+3]=sx;d[o+4]=sy;d[o+5]=sz;d[o+6]=c[0];d[o+7]=c[1];d[o+8]=c[2];d[o+9]=emit||0;d[o+10]=sway||0;d[o+11]=rot||0;d[o+12]=spec==null?0.15:spec;m.n++;};
R.beginStatic=function(){MESHES.forEach(function(k){R.b[k].n=0;});};
function realloc(m){gl.bindBuffer(gl.ARRAY_BUFFER,m.ib);if(m.realloc){gl.bufferData(gl.ARRAY_BUFFER,m.data.byteLength,gl.DYNAMIC_DRAW);m.realloc=false;return true;}return false;}
R.endStatic=function(){MESHES.forEach(function(k){var m=R.b[k];m.sn=m.n;realloc(m);gl.bindBuffer(gl.ARRAY_BUFFER,m.ib);gl.bufferSubData(gl.ARRAY_BUFFER,0,m.data,0,m.n*13);});};
R.beginDynamic=function(){MESHES.forEach(function(k){R.b[k].n=R.b[k].sn;});};
R.upload=function(){MESHES.forEach(function(k){var m=R.b[k];if(realloc(m)){gl.bufferSubData(gl.ARRAY_BUFFER,0,m.data,0,m.n*13);return;}if(m.n>m.sn){gl.bindBuffer(gl.ARRAY_BUFFER,m.ib);gl.bufferSubData(gl.ARRAY_BUFFER,m.sn*52,m.data,m.sn*13,(m.n-m.sn)*13);}});};
R.count=function(){var c=0;MESHES.forEach(function(k){c+=R.b[k].n;});return c;};
R.terrain=function(size,seed,amp,water,mood){var st=Math.max(1,size/64),N=Math.round(2*size/st),r=rng(seed*31+7),pos=[],idx=[];var wl=water==null?-99:water;
function H(x,z){return terrainH(x,z,seed,amp);}
for(var j=0;j<=N;j++)for(var i=0;i<=N;i++){var x=-size+i*st,z=-size+j*st,h=H(x,z),nx=H(x-0.5,z)-H(x+0.5,z),nz=H(x,z-0.5)-H(x,z+0.5),nn=norm([nx,1,nz]);
var k=0.82+0.3*r(),col=scl3(mood.ground,k),slope=1-nn[1];col=mix3(col,mood.stone,clamp(slope*3.5-0.35,0,1));
if(h<wl+0.35)col=mix3(col,mix3([0.78,0.7,0.5],mood.ground,0.35),clamp((wl+0.35-h)*2.5,0,1));
var ao=clamp(0.78+(h-(H(x+3,z)+H(x-3,z)+H(x,z+3)+H(x,z-3))*0.25)*0.9,0.55,1.1);col=scl3(col,ao);
var ed=Math.max(Math.abs(x),Math.abs(z))/size;if(ed>0.94)col=scl3(col,1-(ed-0.94)*6);
pos.push(x,h,z,nn[0],nn[1],nn[2],col[0],col[1],col[2]);}
for(j=0;j<N;j++)for(i=0;i<N;i++){var a=j*(N+1)+i,b=a+1,c=a+N+1,d=c+1;idx.push(a,c,b,b,c,d);}
if(R.ter){gl.deleteVertexArray(R.ter.vao);}
var t={n:idx.length,vao:gl.createVertexArray()};gl.bindVertexArray(t.vao);var vb=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,vb);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(pos),gl.STATIC_DRAW);
gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,3,gl.FLOAT,false,36,0);gl.enableVertexAttribArray(1);gl.vertexAttribPointer(1,3,gl.FLOAT,false,36,12);gl.enableVertexAttribArray(6);gl.vertexAttribPointer(6,3,gl.FLOAT,false,36,24);
var eb=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,eb);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,new Uint32Array(idx),gl.STATIC_DRAW);gl.bindVertexArray(null);R.ter=t;};
function tex(w,h,fmt,ifmt,type,filter){var t=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,t);gl.texImage2D(gl.TEXTURE_2D,0,ifmt,w,h,0,fmt,type,null);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,filter||gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,filter||gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);return t;}
function fbo(t,depth,w,h){var f=gl.createFramebuffer();gl.bindFramebuffer(gl.FRAMEBUFFER,f);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,t,0);if(depth){var rb=gl.createRenderbuffer();gl.bindRenderbuffer(gl.RENDERBUFFER,rb);gl.renderbufferStorage(gl.RENDERBUFFER,gl.DEPTH_COMPONENT24,w,h);gl.framebufferRenderbuffer(gl.FRAMEBUFFER,gl.DEPTH_ATTACHMENT,gl.RENDERBUFFER,rb);f.rb=rb;}
var ok=gl.checkFramebufferStatus(gl.FRAMEBUFFER)===gl.FRAMEBUFFER_COMPLETE;gl.bindFramebuffer(gl.FRAMEBUFFER,null);f.ok=ok;return f;}
R.shadowSize=0;
R.setShadow=function(sz){if(R.shadowSize===sz)return;if(R.stex){gl.deleteTexture(R.stex);gl.deleteFramebuffer(R.sfb);}R.shadowSize=sz;var s=Math.max(1,sz);
var t=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,t);gl.texStorage2D(gl.TEXTURE_2D,1,gl.DEPTH_COMPONENT24,s,s);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_COMPARE_MODE,gl.COMPARE_REF_TO_TEXTURE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_COMPARE_FUNC,gl.LEQUAL);
var f=gl.createFramebuffer();gl.bindFramebuffer(gl.FRAMEBUFFER,f);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.DEPTH_ATTACHMENT,gl.TEXTURE_2D,t,0);gl.bindFramebuffer(gl.FRAMEBUFFER,null);R.stex=t;R.sfb=f;};
R.setShadow(1);
R.resize=function(w,h){if(R.fbW===w&&R.fbH===h)return;R.fbW=w;R.fbH=h;['sceneT','ldrT','b1','b2'].forEach(function(k){if(R[k])gl.deleteTexture(R[k]);});['sceneF','ldrF','bf1','bf2'].forEach(function(k){if(R[k]){if(R[k].rb)gl.deleteRenderbuffer(R[k].rb);gl.deleteFramebuffer(R[k]);}});
var hf=R.hdr?[gl.RGBA,gl.RGBA16F,gl.HALF_FLOAT]:[gl.RGBA,gl.RGBA8,gl.UNSIGNED_BYTE];R.sceneT=tex(w,h,hf[0],hf[1],hf[2]);R.sceneF=fbo(R.sceneT,true,w,h);
if(!R.sceneF.ok&&R.hdr){R.hdr=false;R.fbW=0;return R.resize(w,h);}
R.ldrT=tex(w,h,gl.RGBA,gl.RGBA8,gl.UNSIGNED_BYTE);R.ldrF=fbo(R.ldrT,false);var bw=Math.max(1,w>>2),bh=Math.max(1,h>>2);R.bw=bw;R.bh=bh;
R.b1=tex(bw,bh,hf[0],hf[1],hf[2]);R.bf1=fbo(R.b1);R.b2=tex(bw,bh,hf[0],hf[1],hf[2]);R.bf2=fbo(R.b2);};
R.drawMeshes=function(P,shadowPass){MESHES.forEach(function(k){var m=R.b[k];if(!m.n)return;gl.bindVertexArray(m.vao);gl.drawArraysInstanced(gl.TRIANGLES,0,m.verts,m.n);});};
R.drawTerrain=function(){if(!R.ter)return;gl.bindVertexArray(R.ter.vao);gl.vertexAttrib3f(2,0,0,0);gl.vertexAttrib3f(3,1,1,1);gl.vertexAttrib3f(4,1,1,1);gl.vertexAttrib4f(5,0,0,0,0.05);gl.drawElements(gl.TRIANGLES,R.ter.n,gl.UNSIGNED_INT,0);};
function skyU(U,F){gl.uniform3fv(U.uZen,F.zen);gl.uniform3fv(U.uHor,F.hor);gl.uniform3fv(U.uSunDir,F.sunDir);gl.uniform3fv(U.uSunCol,F.sunCol);gl.uniform1f(U.uNight,F.night);gl.uniform1f(U.uTime,F.t);gl.uniform1f(U.uAur,F.aur);gl.uniform1f(U.uFlash,F.flash||0);}
R.render=function(F){var W=canvas.width,H=canvas.height,post=R.q!=='low'||true,w=Math.max(2,Math.round(W)),h=Math.max(2,Math.round(H));R.resize(w,h);
var useSh=R.shadowSize>1&&F.svp;
if(useSh){gl.bindFramebuffer(gl.FRAMEBUFFER,R.sfb);gl.viewport(0,0,R.shadowSize,R.shadowSize);gl.clear(gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LESS);gl.enable(gl.POLYGON_OFFSET_FILL);gl.polygonOffset(2,4);
gl.useProgram(R.sh.p);gl.uniformMatrix4fv(R.sh.uVP,false,new Float32Array(F.svp));gl.uniform1f(R.sh.uTime,F.t);R.drawMeshes();gl.disable(gl.POLYGON_OFFSET_FILL);}
gl.bindFramebuffer(gl.FRAMEBUFFER,R.sceneF);gl.viewport(0,0,w,h);gl.clearColor(F.fog[0],F.fog[1],F.fog[2],1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
gl.disable(gl.DEPTH_TEST);gl.depthMask(false);gl.useProgram(R.sky.p);skyU(R.sky,F);gl.uniform3fv(R.sky.uF,F.f);gl.uniform3fv(R.sky.uR,F.r);gl.uniform3fv(R.sky.uU,F.u);gl.uniform1f(R.sky.uTh,F.th);gl.uniform1f(R.sky.uAsp,w/h);gl.bindVertexArray(R.fsq);gl.drawArrays(gl.TRIANGLES,0,6);
gl.enable(gl.DEPTH_TEST);gl.depthMask(true);gl.depthFunc(gl.LEQUAL);
var M=R.main;gl.useProgram(M.p);gl.uniformMatrix4fv(M.uVP,false,new Float32Array(F.vp));gl.uniform1f(M.uTime,F.t);gl.uniform3fv(M.uSun,F.lightDir);gl.uniform3fv(M.uLCol,F.lightCol);gl.uniform3fv(M.uSkyAmb,F.skyAmb);gl.uniform3fv(M.uGndAmb,F.gndAmb);
gl.uniform3fv(M.uFog,F.fog);gl.uniform1f(M.uFogD,F.fogD);gl.uniform3fv(M.uEye,F.eye);gl.uniform1f(M.uShadowOn,useSh?1:0);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,R.stex);gl.uniform1i(M.uShadow,0);
if(useSh)gl.uniformMatrix4fv(M.uSVP,false,new Float32Array(F.svp));var L=F.lights||[],lp=new Float32Array(48),lc=new Float32Array(36);for(var i=0;i<Math.min(12,L.length);i++){lp.set([L[i][0],L[i][1],L[i][2],L[i][3]],i*4);lc.set(L[i][4],i*3);}
gl.uniform1i(M.uNL,Math.min(12,L.length));gl.uniform4fv(M.uLP,lp);gl.uniform3fv(M.uLC,lc);
gl.uniform1f(M.uTerrain,1);R.drawTerrain();gl.uniform1f(M.uTerrain,0);R.drawMeshes();
if(F.water!=null){gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.depthMask(false);var Wt=R.wat;gl.useProgram(Wt.p);skyU(Wt,F);gl.uniformMatrix4fv(Wt.uVP,false,new Float32Array(F.vp));gl.uniform1f(Wt.uSize,F.size);gl.uniform1f(Wt.uLvl,F.water);
gl.uniform3fv(Wt.uEye,F.eye);gl.uniform3fv(Wt.uWCol,F.wcol);gl.uniform3fv(Wt.uFog,F.fog);gl.uniform1f(Wt.uFogD,F.fogD);gl.uniform3fv(Wt.uSkyAmb,F.skyAmb);gl.uniform1f(Wt.uSeed,F.seed);gl.uniform1f(Wt.uAmp,F.amp);gl.bindVertexArray(R.fsq);gl.drawArrays(gl.TRIANGLES,0,6);gl.depthMask(true);gl.disable(gl.BLEND);}
gl.disable(gl.DEPTH_TEST);gl.bindVertexArray(R.fsq);var bloom=R.q!=='low';
if(bloom){gl.bindFramebuffer(gl.FRAMEBUFFER,R.bf1);gl.viewport(0,0,R.bw,R.bh);gl.useProgram(R.bright.p);gl.bindTexture(gl.TEXTURE_2D,R.sceneT);gl.uniform1i(R.bright.uT,0);gl.uniform2f(R.bright.uPx,1/w,1/h);gl.drawArrays(gl.TRIANGLES,0,6);
gl.useProgram(R.blur.p);gl.uniform1i(R.blur.uT,0);for(var it=0;it<2;it++){gl.bindFramebuffer(gl.FRAMEBUFFER,R.bf2);gl.bindTexture(gl.TEXTURE_2D,R.b1);gl.uniform2f(R.blur.uDir,(1+it)/R.bw,0);gl.drawArrays(gl.TRIANGLES,0,6);
gl.bindFramebuffer(gl.FRAMEBUFFER,R.bf1);gl.bindTexture(gl.TEXTURE_2D,R.b2);gl.uniform2f(R.blur.uDir,0,(1+it)/R.bh);gl.drawArrays(gl.TRIANGLES,0,6);}}
var C=R.comp;gl.bindFramebuffer(gl.FRAMEBUFFER,R.ldrF);gl.viewport(0,0,w,h);gl.useProgram(C.p);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,R.sceneT);gl.uniform1i(C.uScene,0);gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,R.b1);gl.uniform1i(C.uBloom,1);
gl.uniform1f(C.uBloomK,bloom?0.55:0);gl.uniform1f(C.uExpo,F.expo);gl.uniform1f(C.uSat,F.grade.sat);gl.uniform1f(C.uCon,F.grade.con);gl.uniform3fv(C.uTint,F.grade.tint);gl.uniform1f(C.uVig,0.9);gl.uniform1f(C.uTime,F.t%10);gl.uniform1f(C.uHurt,F.hurt||0);gl.drawArrays(gl.TRIANGLES,0,6);
gl.activeTexture(gl.TEXTURE0);gl.bindFramebuffer(gl.FRAMEBUFFER,null);gl.viewport(0,0,W,H);gl.useProgram(R.fxaa.p);gl.bindTexture(gl.TEXTURE_2D,R.ldrT);gl.uniform1i(R.fxaa.uT,0);gl.uniform2f(R.fxaa.uPx,1/w,1/h);gl.drawArrays(gl.TRIANGLES,0,6);gl.bindVertexArray(null);};
R.setQuality=function(q){R.q=q;R.setShadow(q==='high'?2048:q==='medium'?1024:1);R.scale=q==='high'?1:q==='medium'?0.8:0.6;};
R.destroy=function(){var e=gl.getExtension('WEBGL_lose_context');if(e)e.loseContext();};
return R;}
/*%%ENGINE3D%%*/
var HALF=48,BLOCK={tree:1,rock:1,lamp:1,house:1,ruin:1};
function smooth(a,b,x){var t=clamp((x-a)/(b-a),0,1);return t*t*(3-2*t);}
function rotXZ(lx,lz,a){var c=Math.cos(a),s=Math.sin(a);return[c*lx+s*lz,-s*lx+c*lz];}
function levelEnv(L,mood,clock){var m=mood,E={};var d=L.day||null,cyc=d&&(+d.len>0||d.start!=null);
if(!cyc){E.sunDir=norm(m.sunDir);E.lightDir=E.sunDir;E.lightCol=m.sun;E.night=m.night0;E.fog=m.fog;E.zen=m.zen;E.hor=scl3(m.fog,1.08);E.skyAmb=scl3(m.amb,1.3);E.gndAmb=mix3(scl3(m.ground,0.5),scl3(m.amb,0.6),0.5);E.dayK=1-m.night0;E.expo=1.05;return E;}
var ang=(clock-0.25)*Math.PI*2,sy=Math.sin(ang),dayK=smooth(-0.15,0.22,sy),warm=1-smooth(0.02,0.5,Math.abs(sy));E.dayK=dayK;E.night=Math.max(m.night0*0.5,1-dayK);
E.sunDir=norm([Math.cos(ang)*0.85,sy,0.35]);var sunCol=scl3(mix3(m.sun,[1.0,0.5,0.28],warm*0.65),dayK);var moonDir=norm([-Math.cos(ang)*0.85,-sy,0.35]),moonCol=[0.22,0.3,0.52];
if(sy>-0.05){E.lightDir=E.sunDir;E.lightCol=mix3(scl3(moonCol,0.6),sunCol,dayK);}else{E.lightDir=moonDir;E.lightCol=scl3(moonCol,0.75);}
E.fog=mix3(mix3([0.012,0.018,0.04],scl3(m.fog,0.25),0.5),mix3(m.fog,[1,0.55,0.35],warm*dayK*0.25),dayK);E.zen=mix3([0.004,0.008,0.03],m.zen,dayK);E.hor=scl3(E.fog,1.1);
E.skyAmb=scl3(m.amb,lerp(0.45,1.3,dayK));E.gndAmb=scl3(mix3(m.ground,m.amb,0.5),lerp(0.2,0.55,dayK));E.expo=lerp(1.35,1.05,dayK);return E;}
function create3D(canvas,L,opt,G){
opt=opt||{};var R=null;try{R=typeof Renderer2==='function'?Renderer2(canvas):null;if(R){R.v2=true;canvas._gsR=R;}}catch(e){if(window.console)console.warn('Renderer2 unavailable, classic look:',e.message);R=null;}
if(!R)R=Renderer(canvas);if(!R){if(opt.onHud)opt.onHud({error:'WebGL2 is not available in this browser'});return null;}
var world=opt.world||L,li=opt.li||0,input=new Input(canvas),Q=opt.quality||getQ(),HW=null;
function autoQ(){if(R.v2&&R2.hwPick){HW=R2.hwPick(R.gpu);return HW.preset;}return R.gpu&&R.gpu.software?'low':'medium';}
R.setQuality(Q==='auto'?autoQ():Q);
var S={mode:opt.mode||'play',tool:'tree',toolProps:{},mood:MOODS[L.mood]||MOODS['Bioluminescent Twilight'],t:0,dirty:true,yaw:0.6,pitch:0.42,dist:9.5,editYaw:0.7,editT:[0,0,0],editDist:34,drone:false,droneA:0,shake:0,hitStop:0,score:0,hp:5,won:false,shot:null,hudT:0,parts:[],bursts:[],proj:[],raf:0,drag:null,ft:[],qT:0,flash:0,hurtFx:0,reachT:0,sel:-1};
var seed=L.seed||1,size=clamp(+L.size||HALF,24,128),amp=L.terrain==null?1:clamp(+L.terrain,0,6),water=(L.water==null||L.water==='')?null:+L.water;
function gh(x,z){return terrainH(x,z,seed,amp);}
var spawn=L.spawn&&L.spawn.x!=null?{x:+L.spawn.x,z:+L.spawn.z}:{x:0,z:0};
var P={x:spawn.x,y:gh(spawn.x,spawn.z),z:spawn.z,vy:0,a:0,ta:0,atk:0,inv:0,dash:0,ph:0,whirl:0,moving:false};
var ents=null,cols=[],CG={},CS=4;
function cgAdd(c,x0,x1,z0,z1){for(var ix=Math.floor(x0/CS);ix<=Math.floor(x1/CS);ix++)for(var iz=Math.floor(z0/CS);iz<=Math.floor(z1/CS);iz++){var k=ix*8192+iz;(CG[k]||(CG[k]=[])).push(c);}}
function cgBuild(){CG={};cols.forEach(function(c){if(c.box)cgAdd(c,c.box[0],c.box[1],c.box[2],c.box[3]);else cgAdd(c,c.x-c.r,c.x+c.r,c.z-c.r,c.z+c.r);});}
function gone(i){return G&&G.gone[li+':'+i];}function setGone(i){if(G)G.gone[li+':'+i]=1;}
function enemyEnt(o,i,x,z,s){var k=ENEMY[o.kind]?o.kind:'grunt',D=ENEMY[k],hp=Math.max(1,Math.round((+o.hp||D.hp)*(G?G.hpMul():1)));
return{i:i,o:o,kind:k,D:D,id:o.id,x:x,z:z,hx:x,hz:z,y:0,hp:hp,max:hp,s:s,hit:0,cd:0.6,st:'patrol',wt:0,tx:x,tz:z,pat:o.patrol==null?5:+o.patrol,sh:1+Math.random(),wind:0,vol:4,enr:false,face:0,dead:false};}
function ensureFoes(kind,need){if(S.mode!=='play'||!ents||!(need>0))return;kind=kind||'any';
function m(e){return(kind==='boss')===!!e.D.boss&&(kind==='any'||e.kind===kind);}
var alive=ents.en.filter(function(e){return!e.dead&&m(e);}).length;
ents.en.forEach(function(e){if(alive<need&&e.dead&&!e.id&&e.i>=0&&m(e)){e.dead=false;e.hp=e.max;e.x=e.hx;e.z=e.hz;e.st='patrol';e.enr=false;if(G)delete G.gone[li+':'+e.i];alive++;}});
(L.objects||[]).forEach(function(o,i){if(alive>=need||o.t!=='enemy'||o.id||!gone(i))return;var x=+o.x,z=+o.z;if(!isFinite(x)||!isFinite(z))return;var e=enemyEnt(o,i,x,z,+o.s||1);if(!m(e))return;delete G.gone[li+':'+i];ents.en.push(e);alive++;});
var homes=ents.en.filter(function(e){return!e.D.boss;}),tries=0;
while(alive<need&&tries++<60){var hx,hz;if(homes.length){var h=homes[tries%homes.length];hx=h.hx+(Math.random()-0.5)*6;hz=h.hz+(Math.random()-0.5)*6;}else{var a=Math.random()*6.283;hx=P.x+Math.cos(a)*14;hz=P.z+Math.sin(a)*14;}
hx=clamp(hx,-size+2,size-2);hz=clamp(hz,-size+2,size-2);if(wDepth(hx,hz)>0.8)continue;var ce=enemyEnt({kind:kind==='any'?'grunt':kind,patrol:5},-1,hx,hz,1);ents.en.push(ce);alive++;}}
function resetPlay(keepPos){ents={cr:[],en:[],npc:[],it:[],ch:[],dr:[],po:[],cp:[]};var hm=G?G.hpMul():1;
(L.objects||[]).forEach(function(o,i){var x=+o.x,z=+o.z,s=+o.s||1;if(!isFinite(x)||!isFinite(z))return;var g=gone(i);
if(o.t==='crystal'){ents.cr.push({i:i,x:x,z:z,got:!!g});}
else if(o.t==='enemy'){if(!g||S.mode!=='play')ents.en.push(enemyEnt(o,i,x,z,s));}
else if(o.t==='npc'){ents.npc.push({i:i,o:o,x:x,z:z,hx:x,hz:z,a:0,id:o.id,col:hexCol(o.col,[0.85,0.6,0.3])});}
else if(o.t==='item'){if(!g)ents.it.push({i:i,item:o.item||'potion',n:+o.n||1,x:x,z:z});}
else if(o.t==='chest'){ents.ch.push({i:i,o:o,x:x,z:z,open:!!g,rot:+o.rot||0});}
else if(o.t==='door'){ents.dr.push({i:i,o:o,x:x,z:z,open:!!g,rot:+o.rot||0,key:o.key||'key'});}
else if(o.t==='portal'){ents.po.push({i:i,o:o,x:x,z:z,rot:+o.rot||0});}
else if(o.t==='checkpoint'){ents.cp.push({i:i,x:x,z:z});}});
if(!keepPos){P.x=spawn.x;P.z=spawn.z;P.y=gh(P.x,P.z);P.vy=0;}S.score=0;S.hp=5;S.won=false;S.proj=[];
if(G&&!G.allQuests.length&&ents.cr.length){G.extraGoal='Collect the glowing crystals ('+ents.cr.filter(function(c){return c.got;}).length+'/'+ents.cr.length+')';}else if(G)G.extraGoal=null;}
resetPlay();
function buildCols(){cols=[];(L.objects||[]).forEach(function(o){var x=+o.x,z=+o.z,s=+o.s||1;if(!isFinite(x)||!isFinite(z))return;
if(o.t==='tree')cols.push({x:x,z:z,r:0.45*s});else if(o.t==='rock')cols.push({x:x,z:z,r:0.75*s});else if(o.t==='lamp')cols.push({x:x,z:z,r:0.25});
else if(o.t==='ruin'){for(var c=0;c<4;c++)cols.push({x:x+(c%2?1.6:-1.6)*s,z:z+(c<2?1.6:-1.6)*s,r:0.42*s});}
else if(o.t==='house'){var hw=4.2*s,hd=3.4*s,ro=+o.rot||0,ca=Math.abs(Math.cos(ro)),sa=Math.abs(Math.sin(ro)),ex=(hw*ca+hd*sa)/2+0.1,ez=(hw*sa+hd*ca)/2+0.1;cols.push({box:[x-ex,x+ex,z-ez,z+ez]});}});
cgBuild();S.lamps=[];(L.objects||[]).forEach(function(o){var x=+o.x,z=+o.z;if((o.t==='lamp'||o.t==='house')&&isFinite(x)&&isFinite(z))S.lamps.push([x,gh(x,z),z,o.t==='house']);});}
function buildStatic2(){var lk=R2.look(L);if(typeof L.sky==='string'&&/\.hdr$/i.test(L.sky)){lk.hdri=L.sky;lk.rot=+L.skyRot||0;lk.skyK=lk.skyK||0.45;}S.look=lk;buildCols();S.paths=R2.paths(L,spawn,size);var plz=[];(L.objects||[]).forEach(function(o){if(o.t==='house'||o.t==='portal'||o.t==='checkpoint')plz.push([+o.x,+o.z,o.t==='house'?2.6:1.8]);});
R.setTerrain(R2.terrainMesh({size:size,seed:seed,amp:amp,water:water,gh:gh,look:lk,paths:S.paths,plazas:plz,step:R.q==='low'?1.0:0}));if(!R.groundSet&&!R.groundAsked){R.groundAsked=true;R.setGround(R2.GROUND,R.Q.env>=256?1024:512);}
R.clearStatic();R.beginStatic();var A=R.add,m=S.mood;
(L.objects||[]).forEach(function(o){var x=+o.x,z=+o.z,s=+o.s||1,b=gh(x,z);if(!isFinite(x)||!isFinite(z))return;
if(o.t==='lamp'){A('sphere',x,b+2.62*s,z,0.2*s,0.26*s,0.2*s,m.fxCol,1,0,0,0.2);}});
R.endStatic();S.scene=R2.Scene(R,{gh:gh,look:lk,seed:seed,size:size,water:water,objects:L.objects||[],paths:S.paths,spawn:spawn});S.scene.build().then(function(){S.sceneReady=true;});
S.hdri=null;if(L.day&&+L.day.len>0){S.cyc=S.cyc||{};['day','dusk','night'].forEach(function(k){R.loadHDRI(CYC[k]).then(function(H){S.cyc[k]=H;R.invalidateEnv();}).catch(function(e){if(window.console)console.warn('sky',e.message);});});}
else if(lk.hdri){R.loadHDRI(lk.hdri).then(function(H){if(S.look===lk){S.hdri=H;R.invalidateEnv();}}).catch(function(e){if(window.console)console.warn('sky',e.message);});}
S.dirty=false;}
function buildStatic(){if(R.v2)return buildStatic2();var m=S.mood,r=rng(seed*7+3),A=R.add;R.terrain(size,seed,amp,water,m);R.beginStatic();cols=[];
var nG=Math.min(2600,Math.round(1000*(size*size)/(48*48)));
for(var i=0;i<nG;i++){var x=(r()*2-1)*size*0.97,z=(r()*2-1)*size*0.97,h=gh(x,z);if(water!=null&&h<water+0.1)continue;var lc=m.leaf[i%3],k=0.8+r()*0.5;
A('cone',x,h+0.22,z,0.16,0.5+r()*0.35,0.16,[lc[0]*k*1.2,lc[1]*k*1.2,lc[2]*k*1.2],0,0.2,r()*6,0.05);
if(i%15===0)A('sphere',x+0.15,h+0.4,z,0.14,0.14,0.14,mix3(m.fxCol,[1,0.6,0.8],(i%30)/30),0.3,0.05);
if(i%9===0)A('rock',x,h+0.05,z,0.35,0.22,0.3,scl3(m.stone,0.8+r()*0.3),0,0,r()*6,0.3);}
(L.objects||[]).forEach(function(o,idx){var x=+o.x,z=+o.z,s=+o.s||1,b=gh(x,z),lc=m.leaf[idx%3],rr=rng(idx*13+seed),rot=rr()*6.28;if(!isFinite(x)||!isFinite(z))return;
if(o.t==='tree'){var v=(idx*7+seed)%3,tc=[0.36,0.23,0.14];cols.push({x:x,z:z,r:0.45*s});
if(v<2){A('cyl',x,b+0.9*s,z,0.36*s,1.8*s,0.36*s,tc,0,0,rot,0.1);A('cone',x,b+2.1*s,z,2.6*s,1.7*s,2.6*s,lc,0,0.04,rot,0.1);A('cone',x,b+3.0*s,z,2.0*s,1.5*s,2.0*s,scl3(lc,1.1),0,0.06,rot+1,0.1);A('cone',x,b+3.8*s,z,1.3*s,1.3*s,1.3*s,scl3(lc,1.2),0,0.08,rot+2,0.1);}
else{A('cyl',x,b+1.1*s,z,0.4*s,2.2*s,0.4*s,tc,0,0,rot,0.1);A('sphere',x,b+3.0*s,z,2.8*s,2.3*s,2.8*s,lc,0,0.04,rot,0.12);A('sphere',x+0.8*s,b+3.6*s,z+0.3*s,1.7*s,1.5*s,1.7*s,scl3(lc,1.15),0,0.06,rot,0.12);A('sphere',x-0.7*s,b+2.6*s,z-0.5*s,1.5*s,1.3*s,1.5*s,scl3(lc,0.9),0,0.05,rot,0.12);
if(m.fx==='fireflies'||m.fx==='bubbles')for(var f=0;f<4;f++){var fa=f*1.6+rot;A('sphere',x+Math.cos(fa)*1.3*s,b+2.6*s+f*0.3,z+Math.sin(fa)*1.3*s,0.22,0.22,0.22,m.fxCol,0.9,0.05);}}}
else if(o.t==='ruin'){for(var c=0;c<4;c++){var px=x+(c%2?1.6:-1.6)*s,pz=z+(c<2?1.6:-1.6)*s,ph=(1.2+rr()*2.4)*s;A('cyl',px,b+ph/2,pz,0.75*s,ph,0.75*s,m.stone,0,0,rot,0.25);A('cube',px,b+ph+0.06,pz,0.9*s,0.14,0.9*s,scl3(m.stone,1.1),0,0,0,0.2);A('sphere',px,b+ph+0.16,pz,0.8*s,0.25,0.8*s,lc,0,0,0,0.05);cols.push({x:px,z:pz,r:0.42*s});}
A('cube',x,b+3.3*s,z-1.6*s,4.0*s,0.5*s,0.8*s,m.stone,0,0,0,0.25);A('rock',x+1.2*s,b+0.2,z+0.8*s,1.0*s,0.6,0.8*s,scl3(m.stone,0.85),0,0,rot,0.2);A('cube',x-0.4*s,b+0.08,z+0.3*s,3.4*s,0.16,3.4*s,scl3(m.stone,0.75),0,0,0.2,0.3);}
else if(o.t==='rock'){A('rock',x,b+0.35*s,z,1.7*s,1.2*s,1.4*s,scl3(m.stone,0.82),0,0,rot,0.3);A('rock',x+0.5*s,b+0.25*s,z+0.4*s,0.8*s,0.6*s,0.7*s,m.stone,0,0,rot+2,0.3);cols.push({x:x,z:z,r:0.75*s});}
else if(o.t==='lamp'){A('cyl',x,b+1.1*s,z,0.16*s,2.2*s,0.16*s,[0.16,0.16,0.18],0,0,0,0.6);A('cube',x,b+2.35*s,z,0.42*s,0.42*s,0.42*s,m.fxCol,1,0,0.785,0.2);A('pyr',x,b+2.7*s,z,0.7*s,0.3*s,0.7*s,[0.14,0.14,0.16],0,0,0,0.5);cols.push({x:x,z:z,r:0.25});}
else if(o.t==='house'){var hw=4.2*s,hd=3.4*s,hh=2.6*s,wc=[0.86,0.78,0.64],rc=mix3([0.55,0.22,0.16],m.stone,0.3),ro=+o.rot||0;
A('cube',x,b+hh/2-0.2,z,hw,hh+0.4,hd,wc,0,0,ro,0.1);A('pyr',x,b+hh+0.9*s,z,(hw+0.8*s)/0.707,2.0*s,(hd+0.8*s)/0.707,rc,0,0,ro,0.3);
var dp=rotXZ(0,hd/2+0.02,ro);A('cube',x+dp[0],b+0.7*s,z+dp[1],0.9*s,1.5*s,0.1,[0.3,0.18,0.1],0,0,ro,0.2);
var w1=rotXZ(1.3*s,hd/2+0.03,ro),w2=rotXZ(-1.3*s,hd/2+0.03,ro);A('cube',x+w1[0],b+1.4*s,z+w1[1],0.7*s,0.6*s,0.08,[1,0.75,0.4],0.65,0,ro,0.2);A('cube',x+w2[0],b+1.4*s,z+w2[1],0.7*s,0.6*s,0.08,[1,0.75,0.4],0.65,0,ro,0.2);
var ch=rotXZ(1.2*s,-0.6*s,ro);A('cube',x+ch[0],b+hh+1.3*s,z+ch[1],0.5*s,1.4*s,0.5*s,scl3(m.stone,0.9),0,0,ro,0.2);
var ca=Math.abs(Math.cos(ro)),sa=Math.abs(Math.sin(ro)),ex=(hw*ca+hd*sa)/2+0.1,ez=(hw*sa+hd*ca)/2+0.1;cols.push({box:[x-ex,x+ex,z-ez,z+ez]});}});
cgBuild();S.lamps=[];(L.objects||[]).forEach(function(o){var x=+o.x,z=+o.z;if((o.t==='lamp'||o.t==='house')&&isFinite(x)&&isFinite(z))S.lamps.push([x,gh(x,z),z,o.t==='house']);});
R.endStatic();S.dirty=false;}
function burst(x,y,z,col,cnt,sp){var r=Math.random;sp=sp||1;cnt=Math.min(cnt,Math.max(0,3000-S.bursts.length));for(var i=0;i<cnt;i++)S.bursts.push({x:x,y:y,z:z,vx:(r()-0.5)*8*sp,vy:(r()*6+1)*sp,vz:(r()-0.5)*8*sp,life:0.5+r()*0.4,c:col,s:0.1+r()*0.12});}
function wDepth(x,z){return water==null?-9:water-gh(x,z);}
function blocked(x,z,rad){var x1=Math.floor((x+rad)/CS),z0=Math.floor((z-rad)/CS),z1=Math.floor((z+rad)/CS);for(var ix=Math.floor((x-rad)/CS);ix<=x1;ix++)for(var iz=z0;iz<=z1;iz++){var cl=CG[ix*8192+iz];if(cl)for(var i=0;i<cl.length;i++){var c=cl[i];if(c.box){if(x>c.box[0]-rad&&x<c.box[1]+rad&&z>c.box[2]-rad&&z<c.box[3]+rad)return c;}else if(Math.hypot(x-c.x,z-c.z)<c.r+rad)return c;}}
if(ents){for(i=0;i<ents.dr.length;i++){var d=ents.dr[i];if(!d.open){var p=rotXZ(x-d.x,z-d.z,-d.rot);if(Math.abs(p[0])<1.9&&Math.abs(p[1])<0.5+rad)return d;}}
for(i=0;i<ents.ch.length;i++){if(Math.hypot(x-ents.ch[i].x,z-ents.ch[i].z)<0.6+rad)return ents.ch[i];}}return null;}
function moveTo(o,nx,nz,rad,isPlayer){nx=clamp(nx,-size+1,size-1);nz=clamp(nz,-size+1,size-1);if(wDepth(nx,nz)>1.3&&!(isPlayer&&wDepth(o.x,o.z)>1.3))return false;if(isPlayer&&gh(nx,nz)-gh(o.x,o.z)>1.2)return false;
if(!blocked(nx,nz,rad)){o.x=nx;o.z=nz;return true;}if(!blocked(nx,o.z,rad)){o.x=nx;return true;}if(!blocked(o.x,nz,rad)){o.z=nz;return true;}return false;}
function stats(){return G?G.stats():{atk:1,def:0,maxHp:5,speed:1};}
function hurtP(d){if(P.inv>0||P.dash>0||!G)return;P.inv=1.0;S.shake=0.6;S.hitStop=0.07;S.hurtFx=0.6;playSfx(sfxFor(world,'hit'));burst(P.x,P.y+1,P.z,[1,0.3,0.3],14);G.hurt(d);}
function killEnemy(e){e.dead=true;e.hp=0;setGone(e.i);S.score+=50;playSfx(sfxFor(world,'boom'));burst(e.x,gh(e.x,e.z)+0.8+e.y,e.z,[1,0.4,0.6],e.D.boss?120:40,e.D.boss?2:1);
if(G){G.addXp(e.D.xp);G.st.gold+=e.D.gold;G.event('defeat',{kind:e.kind,id:e.id});if(Math.random()<(e.D.boss?1:0.18))ents.it.push({i:-1,item:e.D.boss?'elixir':'potion',n:1,x:e.x,z:e.z});G.refresh();if(e.D.boss){G.bossBar(null);G.big('Victory!',(e.o.name||e.D.name)+' is defeated',3);}}}
function damage(e,dmg,kx,kz){if(e.dead)return;if(window.GSYS&&!GSYS.canHarm(e.o||e))return;var crit=Math.random()<0.15;dmg*=crit?2:1;e.hp-=dmg;e.hit=0.22;if(!e.D.boss){e.x+=kx*1.2;e.z+=kz*1.2;}e.st='chase';S.hitStop=crit?0.11:0.07;S.shake=crit?0.7:0.4;playSfx(sfxFor(world,'hit'));
burst(e.x,gh(e.x,e.z)+0.9+e.y,e.z,crit?[1,0.95,0.4]:[1,0.9,0.7],crit?30:16);if(G&&crit)G.toast('Critical hit!');if(e.hp<=0)killEnemy(e);}
function attack(){if(S.mode!=='play'||P.atk>0||(G&&G.busy()))return;P.atk=0.28;playSfx(sfxFor(world,'laser'));var fx=Math.sin(P.a),fz=Math.cos(P.a),st=stats();
ents.en.forEach(function(e){if(e.dead)return;var dx=e.x-P.x,dz=e.z-P.z,d=Math.hypot(dx,dz),reach=2.5+(e.D.boss?1.5*e.s:0);if(d<reach&&(dx*fx+dz*fz)/(d||1)>-0.25)damage(e,st.atk,dx/(d||1),dz/(d||1));});}
function whirl(){if(!G||!G.hasAbility('Whirl')||(G.cds.Whirl||0)>0)return;G.cds.Whirl=4;P.whirl=0.5;playSfx(SFX.magic);var st=stats();burst(P.x,P.y+0.8,P.z,S.mood.fxCol,40,1.4);
ents.en.forEach(function(e){if(e.dead)return;var dx=e.x-P.x,dz=e.z-P.z,d=Math.hypot(dx,dz);if(d<3.6+(e.D.boss?2:0))damage(e,Math.ceil(st.atk*1.5),dx/(d||1),dz/(d||1));});G.refresh();}
function bolt(){if(!G||!G.hasAbility('Star Bolt')||(G.cds['Star Bolt']||0)>0)return;G.cds['Star Bolt']=1.5;playSfx(SFX.magic);var fx=Math.sin(P.a),fz=Math.cos(P.a);
S.proj.push({x:P.x+fx,y:P.y+1.1,z:P.z+fz,vx:fx*18,vz:fz*18,life:1.4,dmg:stats().atk+1,own:'p',c:[0.6,0.9,1]});G.refresh();}
function dash(){if(!G||!G.hasAbility('Dash')||(G.cds.Dash||0)>0)return;G.cds.Dash=1.2;P.dash=0.2;playSfx(SFX.jump);burst(P.x,P.y+0.4,P.z,[0.9,0.9,1],12);G.refresh();}
function interactable(){var best=null,bd=2.8;function c(kind,o,extra){var d=Math.hypot(o.x-P.x,o.z-P.z);if(d<bd+(extra||0)){bd=d;best={k:kind,o:o};}}
ents.npc.forEach(function(n){c('npc',n);});ents.ch.forEach(function(h){if(!h.open)c('chest',h);});ents.dr.forEach(function(d){if(!d.open)c('door',d,0.6);});return best;}
function interact(){if(!G||G.busy())return;var t=interactable();if(!t)return;var o=t.o;
if(t.k==='npc'){o.a=Math.atan2(P.x-o.x,P.z-o.z);o.talk=1;G.talk(o.o);}
else if(t.k==='chest'){o.open=true;setGone(o.i);playSfx(sfxFor(world,'coin'));burst(o.x,gh(o.x,o.z)+1,o.z,[1,0.85,0.3],30);var it=o.o.item,gd=+o.o.gold||0;if(it)G.give(it,+o.o.n||1);if(gd){G.st.gold+=gd;G.toast('+'+gd+' gold');}if(!it&&!gd){G.st.gold+=10;G.toast('+10 gold');}G.refresh();}
else if(t.k==='door'){if(o.o.sealed){G.toast('It will not budge. Something else must open it.');playSfx(SFX.hit);}else if(G.has(o.key)){var kd=G.item(o.key);if(kd.kind==='key')G.take(o.key,1);o.open=true;setGone(o.i);playSfx(SFX.door);S.shake=0.3;G.toast('Unlocked with '+kd.name);burst(o.x,gh(o.x,o.z)+1.5,o.z,S.mood.stone,30);}else{G.toast('Locked. You need: '+G.item(o.key).name);playSfx(SFX.hit);}}}
function entById(id){if(id==='player')return P;var f=null;ents.npc.forEach(function(n){if(!f&&n.id===id)f=n;});ents.en.forEach(function(e){if(!f&&e.id===id&&!e.dead)f=e;});return f;}
function stepTo(n,tx,tz,sp,dt){var dx=tx-n.x,dz=tz-n.z,d=Math.hypot(dx,dz);if(d<0.05)return d;var v=Math.min(sp*dt,d),a=Math.atan2(dx,dz),sd=n._sd||1,offs=[0,0.7*sd,-0.7*sd,1.4*sd,-1.4*sd],ok=false;
for(var i=0;i<offs.length&&!ok;i++){ok=moveTo(n,n.x+Math.sin(a+offs[i])*v,n.z+Math.cos(a+offs[i])*v,0.35);if(ok&&i>0)n._sd=offs[i]>0?1:-1;}
n.a=a;n.walk=(n.walk||0)+dt*8;n._mv=0.25;return d;}
function npcBehave(n,d,dt,spd){var o=n.o,b=o.behavior,sp=Math.min(+o.speed||2.2,spd*0.95),pa=o.path;
if(b==='patrol'&&pa&&pa.length){n.pi=n.pi||0;if(n.wait>0){n.wait-=dt;}else{var pt=pa[n.pi%pa.length];if(stepTo(n,+pt.x,+pt.z,sp,dt)<0.4){n.pi=(n.pi+1)%pa.length;n.wait=1.2;}}}
else if(b==='follow'||b==='companion'){var fd=+o.radius||3;if(d>28){n.x=P.x-Math.sin(P.a)*2;n.z=P.z-Math.cos(P.a)*2;burst(n.x,gh(n.x,n.z)+1,n.z,[1,1,1],8,0.4);}else if(d>fd)stepTo(n,P.x,P.z,Math.min(Math.max(sp,3)*1.6,d*2.2),dt);
if(b==='companion'){n.cd=Math.max(0,(n.cd||0)-dt);if(n.cd<=0){var tg=null,bd=3.4;ents.en.forEach(function(e){if(e.dead)return;var ed=Math.hypot(e.x-n.x,e.z-n.z);if(ed<bd&&Math.hypot(e.x-P.x,e.z-P.z)<16){bd=ed;tg=e;}});
if(tg){n.cd=1.4;n.a=Math.atan2(tg.x-n.x,tg.z-n.z);n._mv=0.4;damage(tg,Math.max(1,Math.round(stats().atk*0.6)),(tg.x-n.x)/(bd||1),(tg.z-n.z)/(bd||1));}}}}
else if(b==='flee'){var fr=+o.radius||8;if(d<fr){var ax=n.x-P.x,az=n.z-P.z,l=Math.hypot(ax,az)||1;stepTo(n,n.x+ax/l*4,n.z+az/l*4,Math.max(sp,3)*1.8,dt);}else if(Math.hypot(n.x-n.hx,n.z-n.hz)>1)stepTo(n,n.hx,n.hz,sp*0.6,dt);}}
function epath(e,es,dt){var pa=e.o.path,pt=pa[(e.pi||0)%pa.length],dx=+pt.x-e.x,dz=+pt.z-e.z,d=Math.hypot(dx,dz);if(d<0.6){e.pi=((e.pi||0)+1)%pa.length;}else{stepTo(e,+pt.x,+pt.z,es*0.55,dt);e.face=Math.atan2(dx,dz);}}
function update(dt){S.t+=dt;if(S.shake>0)S.shake=Math.max(0,S.shake-dt*1.6);if(S.hurtFx>0)S.hurtFx=Math.max(0,S.hurtFx-dt*1.5);if(S.flash>0)S.flash=Math.max(0,S.flash-dt*3);if(S.drone)S.droneA+=dt*0.16;
var k=input.keys;
if(S.mode==='edit'){var sp=22*dt*(S.editDist/34);var fx=Math.sin(S.editYaw),fz=Math.cos(S.editYaw);
if(k.KeyW||k.ArrowUp)S.editT=[S.editT[0]-fx*sp,0,S.editT[2]-fz*sp];if(k.KeyS||k.ArrowDown)S.editT=[S.editT[0]+fx*sp,0,S.editT[2]+fz*sp];
if(k.KeyA||k.ArrowLeft)S.editT=[S.editT[0]-fz*sp,0,S.editT[2]+fx*sp];if(k.KeyD||k.ArrowRight)S.editT=[S.editT[0]+fz*sp,0,S.editT[2]-fx*sp];
if(k.KeyQ)S.editYaw+=dt*1.5;if(k.KeyE)S.editYaw-=dt*1.5;S.editT[0]=clamp(S.editT[0],-size,size);S.editT[2]=clamp(S.editT[2],-size,size);return;}
if(G){G.update(dt);if(L.day&&+L.day.len>0)G.clock=((G.clock==null?(+L.day.start||0.3):G.clock)+dt/(+L.day.len))%1;}
if(S.hitStop>0){S.hitStop-=dt;return;}
var busy=G&&G.busy();if(k.KeyZ)S.yaw+=dt*2;if(k.KeyC)S.yaw-=dt*2;
var mx=0,mz=0;if(!busy){if(k.KeyW||k.ArrowUp)mz-=1;if(k.KeyS||k.ArrowDown)mz+=1;if(k.KeyA||k.ArrowLeft)mx-=1;if(k.KeyD||k.ArrowRight)mx+=1;}
var st=stats(),spd=7*st.speed*(wDepth(P.x,P.z)>0.15?0.6:1);P.moving=!!(G&&G.cut&&P.cmv>0);if(P.cmv>0)P.cmv-=dt;
if(P.dash>0){P.dash-=dt;moveTo(P,P.x+Math.sin(P.a)*24*dt,P.z+Math.cos(P.a)*24*dt,0.4,true);if(Math.random()<0.6)burst(P.x,P.y+0.5,P.z,[0.85,0.9,1],1,0.3);}
else if(mx||mz){var cs=Math.cos(S.yaw),sn=Math.sin(S.yaw),dx=mx*cs+mz*sn,dz=-mx*sn+mz*cs,l=Math.hypot(dx,dz);dx/=l;dz/=l;moveTo(P,P.x+dx*spd*dt,P.z+dz*spd*dt,0.4,true);P.ta=Math.atan2(dx,dz);P.moving=true;P.ph+=dt*spd*1.3;}
var da=((P.ta-P.a+Math.PI*3)%(Math.PI*2))-Math.PI;P.a+=da*Math.min(1,dt*14);
var g=gh(P.x,P.z);if(water!=null)g=Math.max(g,water-0.55);if(P.y<=g+0.001&&k.Space&&P.vy<=0&&!busy){P.vy=8.5;playSfx(sfxFor(world,'jump'));}
P.vy-=24*dt;P.y+=P.vy*dt;if(P.y<g){if(P.vy<-12){S.shake=0.25;burst(P.x,g+0.1,P.z,scl3(S.mood.ground,1.6),10,0.5);}P.y=g;P.vy=0;}
if(P.inv>0)P.inv-=dt;if(P.atk>0)P.atk-=dt;if(P.whirl>0)P.whirl-=dt;
ents.cr.forEach(function(c){if(!c.got&&Math.hypot(c.x-P.x,c.z-P.z)<1.4&&Math.abs(gh(c.x,c.z)+1-P.y-0.5)<2.2){c.got=true;setGone(c.i);S.score+=10;playSfx(sfxFor(world,'coin'));burst(c.x,gh(c.x,c.z)+1.2,c.z,S.mood.fxCol,26);if(G){G.st.score+=10;G.give('crystal',1,true);G.toast('Crystal '+ents.cr.filter(function(q){return q.got;}).length+'/'+ents.cr.length);if(G.extraGoal!=null)resetGoal();}}});
ents.it=ents.it.filter(function(it){if(Math.hypot(it.x-P.x,it.z-P.z)<1.3){playSfx(sfxFor(world,'coin'));burst(it.x,gh(it.x,it.z)+0.8,it.z,hexCol(G?G.item(it.item).col:'#fff'),20);if(it.i>=0)setGone(it.i);if(G)G.give(it.item,it.n);return false;}return true;});
ents.cp.forEach(function(c){if(G&&Math.hypot(c.x-P.x,c.z-P.z)<1.6&&!(G.cp&&G.cp.li===li&&Math.hypot(G.cp.x-c.x,G.cp.z-c.z)<0.5)){G.checkpoint({x:c.x,z:c.z+1.5});burst(c.x,gh(c.x,c.z)+1.6,c.z,[1,0.9,0.5],40);}});
ents.po.forEach(function(p){if(!G||G.won&&false)return;if(Math.hypot(p.x-P.x,p.z-P.z)<1.3&&!S.porting){var lk=p.o.lock;if(lk&&G.quest(lk)&&!(G.q[lk]&&G.q[lk].state==='done')){if(!p.warn){p.warn=1;G.toast('Sealed. Finish "'+((G.quest(lk)||{}).title||lk)+'" first.');}return;}
var to=p.o.to==null||p.o.to==='next'?li+1:+p.o.to;if(to>=G.levels.length||to<0){if(!p.warn){p.warn=1;G.toast('This portal leads nowhere yet.');}return;}S.porting=true;G.event('reach',{x:P.x,z:P.z});playSfx(SFX.portal);S.flash=1;if(opt.switchLevel)setTimeout(function(){opt.switchLevel(to);},350);}else if(Math.hypot(p.x-P.x,p.z-P.z)>2.5)p.warn=0;});
ents.npc.forEach(function(n){var d=Math.hypot(P.x-n.x,P.z-n.z),es=G&&G.escortStep(n.id);if(n._mv>0)n._mv-=dt;if(n.o.behavior&&!es&&!busy)npcBehave(n,d,dt,spd);if(d<5&&!(n._mv>0))n.a+=(((Math.atan2(P.x-n.x,P.z-n.z)-n.a+Math.PI*3)%(Math.PI*2))-Math.PI)*Math.min(1,dt*5);
if(es){if(d>9){n.x=P.x-Math.sin(P.a)*1.8;n.z=P.z-Math.cos(P.a)*1.8;burst(n.x,gh(n.x,n.z)+1,n.z,[1,1,1],8,0.4);}else if(d>2.2){var v=Math.min(spd*0.95,d*2.5)*dt;moveTo(n,n.x+(P.x-n.x)/d*v,n.z+(P.z-n.z)/d*v,0.35);n.walk=(n.walk||0)+dt*8;}if(Math.hypot(n.x-(+es.x||0),n.z-(+es.z||0))<(+es.r||3)){G.event('escort',{npc:n.id});G.toast(G.npcName(n.id)+' is safe!','q');}}});
var boss=null;ents.en.forEach(function(e){if(e.dead)return;var D=e.D,d=Math.hypot(P.x-e.x,P.z-e.z),dx=(P.x-e.x)/(d||1),dz=(P.z-e.z)/(d||1),hm=Math.hypot(e.x-e.hx,e.z-e.hz),dm=G?G.dmgMul():1,es=D.speed*(e.enr?1.35:1);
e.hit=Math.max(0,e.hit-dt);e.cd=Math.max(0,e.cd-dt);if(D.boss&&e.st==='chase'&&d<D.sight*1.7)boss=e;if(busy)return;if(d>80&&e.st==='patrol')return;var eb=e.o&&e.o.behavior;if(eb==='coward'&&e.hp<e.max*0.45&&d<D.sight*1.5){moveTo(e,e.x-dx*es*1.2*dt,e.z-dz*es*1.2*dt,0.4);e.face=Math.atan2(-dx,-dz);return;}
if(e.st!=='chase'&&d<D.sight&&hm<(eb==='guard'?9:26)){e.st='chase';burst(e.x,gh(e.x,e.z)+2.2*e.s,e.z,[1,0.9,0.2],6,0.4);}
if(e.st==='chase'&&(d>D.sight*1.7||hm>(eb==='guard'?12:30)))e.st='return';
if(e.st==='return'){var dh=hm||1;moveTo(e,e.x+(e.hx-e.x)/dh*es*dt,e.z+(e.hz-e.z)/dh*es*dt,0.4);e.face=Math.atan2(e.hx-e.x,e.hz-e.z);if(hm<1)e.st='patrol';}
else if(e.st==='patrol'&&eb==='patrol'&&e.o.path&&e.o.path.length){epath(e,es,dt);}
else if(e.st==='patrol'){e.wt-=dt;if(e.wt<=0){e.wt=2+Math.random()*3;var a=Math.random()*6.28,rr=Math.random()*e.pat;e.tx=e.hx+Math.cos(a)*rr;e.tz=e.hz+Math.sin(a)*rr;}var pd=Math.hypot(e.tx-e.x,e.tz-e.z);if(pd>0.4){moveTo(e,e.x+(e.tx-e.x)/pd*es*0.45*dt,e.z+(e.tz-e.z)/pd*es*0.45*dt,0.4);e.face=Math.atan2(e.tx-e.x,e.tz-e.z);}}
else if(e.hit<=0){e.face=Math.atan2(dx,dz);
if(D.ranged){if(d<6)moveTo(e,e.x-dx*es*dt,e.z-dz*es*dt,0.4);else if(d>11)moveTo(e,e.x+dx*es*dt,e.z+dz*es*dt,0.4);e.sh-=dt;if(e.sh<=0&&d<16){e.sh=1.9;e._shot=0.6;S.proj.push({x:e.x,y:gh(e.x,e.z)+1.2,z:e.z,vx:dx*11,vz:dz*11,life:2,dmg:D.dmg,own:'e',c:[0.6,1,0.4]});playSfx(SFX.laser);}}
else if(e.wind>0){e.wind-=dt;if(e.wind<=0){S.shake=0.5;burst(e.x,gh(e.x,e.z)+0.2,e.z,S.mood.stone,30,1.2);if(d<2.6+e.s)hurtP(D.dmg);e.cd=1.6;}}
else{var reach=1.1+(D.boss?1.4*e.s:D.fly?0.2:0.3*e.s);if(d>reach*0.9)moveTo(e,e.x+dx*es*dt,e.z+dz*es*dt,D.boss?1.2:0.45);if(d<reach&&e.cd<=0){if(k==='x'){}if(e.kind==='brute'||D.boss){e.wind=0.55;}else{e.cd=1.2;hurtP(D.dmg);}}}
if(D.boss){e.vol-=dt;if(e.vol<=0){e.vol=e.enr?3.2:5;for(var b=0;b<14;b++){var ba=b/14*Math.PI*2;S.proj.push({x:e.x,y:gh(e.x,e.z)+1.4,z:e.z,vx:Math.cos(ba)*8,vz:Math.sin(ba)*8,life:2.4,dmg:D.dmg,own:'e',c:[1,0.35,0.8]});}playSfx(SFX.boom);S.shake=0.3;}
if(!e.enr&&e.hp<e.max/2){e.enr=true;if(G)G.toast((e.o.name||D.name)+' is enraged!','q');for(var sm=0;sm<2;sm++)ents.en.push({i:-1,o:{},kind:'grunt',D:ENEMY.grunt,x:e.x+(sm?3:-3),z:e.z+2,hx:e.x,hz:e.z,y:0,hp:3,max:3,s:1,hit:0,cd:1,st:'chase',wt:0,tx:0,tz:0,pat:4,sh:2,wind:0,vol:9,face:0,dead:false});}}}
if(D.fly)e.y=1.2+Math.sin(S.t*2+e.hx)*0.4;});
S.combatT=Math.max(0,(S.combatT||0)-dt);if(ents.en.some(function(e){return!e.dead&&e.st==='chase';}))S.combatT=2;
if(G){if(boss)G.bossBar(boss.o.name||boss.D.name,boss.hp/boss.max);else G.bossBar(null);}
S.proj=S.proj.filter(function(p){p.life-=dt;p.x+=p.vx*dt;p.z+=p.vz*dt;if(p.life<=0||blocked(p.x,p.z,0.1)||gh(p.x,p.z)>p.y){burst(p.x,p.y,p.z,p.c,6,0.4);return false;}
if(p.own==='e'){if(Math.hypot(p.x-P.x,p.z-P.z)<0.7&&Math.abs(p.y-P.y-0.9)<1.2){hurtP(p.dmg);return false;}}
else{for(var i=0;i<ents.en.length;i++){var e=ents.en[i];if(!e.dead&&Math.hypot(p.x-e.x,p.z-e.z)<0.8*e.s+(e.D.boss?1.2:0)){damage(e,p.dmg,p.vx/18,p.vz/18);return false;}}}return true;});
if(G){S.reachT-=dt;if(S.reachT<=0){S.reachT=0.25;G.event('reach',{x:P.x,z:P.z});}
var t=interactable();G.prompt(busy||!t?null:'<kbd>E</kbd>'+(t.k==='npc'?'Talk to '+esc(t.o.o.name||'stranger'):t.k==='chest'?'Open chest':'Unlock door'));}
if(G&&!G.won&&!G.allQuests.length&&ents.cr.length&&ents.cr.every(function(c){return c.got;})){S.won=true;G.win();}
else if(!G&&!S.won&&ents.cr.length&&ents.cr.every(function(c){return c.got;})){S.won=true;S.score+=100;playSfx(sfxFor(world,'power'));}}
function resetGoal(){var g=ents.cr.filter(function(c){return c.got;}).length;G.extraGoal='Collect the glowing crystals ('+g+'/'+ents.cr.length+')';G.refresh();}
var A=R.add;
function hero(x,y,z,a,ph,flash){var m=S.mood,cl=hexCol((world.player||{}).col,[0.18,0.55,0.62]),sk=[0.98,0.8,0.62],sw=P.moving?Math.sin(ph):0,e=flash?1:0;
function at(lx,ly,lz,sx,sy,sz,c,mesh,em,ro){var p=rotXZ(lx,lz,a);A(mesh||'cube',x+p[0],y+ly,z+p[1],sx,sy,sz,flash?[1,1,1]:c,em||e,0,a+(ro||0),0.25);}
at(0.2,0.35,sw*0.22,0.24,0.62,0.26,[0.25,0.2,0.18]);at(-0.2,0.35,-sw*0.22,0.24,0.62,0.26,[0.25,0.2,0.18]);
at(0,1.0,0,0.72,0.85,0.5,cl,'cyl');at(0,0.62,0,0.8,0.22,0.56,[0.4,0.26,0.14],'cyl');at(0,1.0,-0.3,0.66,0.95,0.08,scl3(cl,0.6));
at(0.47,1.0,-sw*0.18,0.2,0.62,0.2,cl);at(-0.47,1.0,sw*0.18,0.2,0.62,0.2,cl);
at(0,1.72,0,0.56,0.56,0.56,sk,'sphere');at(0,1.98,-0.04,0.62,0.32,0.62,[0.45,0.28,0.15],'sphere');at(0.12,1.76,0.26,0.08,0.1,0.06,[0.08,0.08,0.1]);at(-0.12,1.76,0.26,0.08,0.1,0.06,[0.08,0.08,0.1]);
var wcol=[0.85,0.88,0.95],wid=G&&G.st.eq.weapon;if(wid){wcol=hexCol(G.item(wid).col,wcol);}
if(P.atk>0||P.whirl>0){var sw2=P.whirl>0?(0.5-P.whirl)*28:(0.28-P.atk)/0.28*2.6-1.3,ba=a+sw2;for(var i=1;i<=3;i++){var bp=rotXZ(0,0.35+i*0.38,ba);A('cube',x+bp[0],y+1.0,z+bp[1],0.12,0.08,0.42,wcol,0.6,0,ba,0.9);}
for(var j=0;j<5;j++){var ta=ba-j*0.28,tp=rotXZ(0,1.25,ta);A('gem',x+tp[0],y+1.0,z+tp[1],0.18,0.06,0.18,m.fxCol,1-j*0.18,0,ta);}}
else{at(0.32,1.25,-0.36,0.08,1.0,0.08,wcol,'cube',0,0);}}
function enemy(e,t){var b=gh(e.x,e.z)+e.y,s=e.s,D=e.D,fl=e.hit>0,col=fl?[1,1,1]:e.enr?[0.9,0.2,0.3]:hexCol(e.o.col,D.col),em=fl?1:0,f=e.face,eye=[1,0.95,0.6];
function at(lx,ly,lz,sx,sy,sz,c,mesh,emi,ro){var p=rotXZ(lx*s,lz*s,f);A(mesh||'cube',e.x+p[0],b+ly*s,e.z+p[1],sx*s,sy*s,sz*s,c,emi==null?em:emi,0,f+(ro||0),0.5);}
if(e.kind==='grunt'){var sq=1+Math.sin(t*7+e.hx)*0.1;at(0,0.45*sq,0,1.1/sq,0.9*sq,1.1/sq,col,'sphere');at(0.22,0.65,0.42,0.16,0.2,0.1,eye,'sphere',1);at(-0.22,0.65,0.42,0.16,0.2,0.1,eye,'sphere',1);at(0,0.95*sq,0,0.3,0.2,0.3,scl3(col,1.3),'sphere');}
else if(e.kind==='archer'){at(0,0.7,0,0.9,1.4,0.9,col,'cone');at(0,1.5,0,0.55,0.55,0.55,scl3(col,0.7),'sphere');at(0.14,1.52,0.24,0.1,0.08,0.06,[0.7,1,0.4],'cube',1);at(-0.14,1.52,0.24,0.1,0.08,0.06,[0.7,1,0.4],'cube',1);at(0.45,1.0,0.25,0.06,1.1,0.08,[0.4,0.25,0.12]);}
else if(e.kind==='brute'){var wu=e.wind>0?Math.sin(t*40)*0.08:0;at(0,0.9+wu,0,1.4,1.3,1.0,col,'rock');at(0,1.75,0.1,0.8,0.6,0.7,scl3(col,0.85),'rock');at(0.85,1.25,0,0.6,0.6,0.6,scl3(col,0.9),'rock');at(-0.85,1.25,0,0.6,0.6,0.6,scl3(col,0.9),'rock');
at(0.9,0.55+(e.wind>0?0.7:0),0.2,0.45,0.55,0.45,scl3(col,0.8),'rock');at(-0.9,0.55+(e.wind>0?0.7:0),0.2,0.45,0.55,0.45,scl3(col,0.8),'rock');at(0.2,1.8,0.45,0.12,0.1,0.06,e.wind>0?[1,0.2,0.1]:[1,0.6,0.2],'cube',1);at(-0.2,1.8,0.45,0.12,0.1,0.06,e.wind>0?[1,0.2,0.1]:[1,0.6,0.2],'cube',1);}
else if(e.kind==='wisp'){at(0,0.5,0,0.7,0.7,0.7,fl?[1,1,1]:col,'sphere',0.9);for(var i=1;i<4;i++){var tp=rotXZ(0,-i*0.35,f);A('sphere',e.x+tp[0],b+0.5-i*0.08+Math.sin(t*5+i)*0.1,e.z+tp[1],0.4-i*0.08,0.4-i*0.08,0.4-i*0.08,col,0.7,0,0);}}
else{var pulse=1+Math.sin(t*3)*0.04;at(0,1.6*pulse,0,2.6,2.6,2.2,col,'rock');at(0,3.1,0.2,1.4,1.2,1.2,scl3(col,0.8),'rock');for(var c=0;c<5;c++){at(-0.6+c*0.3,3.8+(c%2)*0.25,0.2,0.22,0.6,0.22,[1,0.8,0.3],'gem',0.9);}
at(0.35,3.2,0.75,0.25,0.18,0.1,[1,0.3,0.9],'cube',1);at(-0.35,3.2,0.75,0.25,0.18,0.1,[1,0.3,0.9],'cube',1);
for(var o2=0;o2<4;o2++){var oa=t*1.5+o2*1.57;A('gem',e.x+Math.cos(oa)*2.6*s,b+2.2*s+Math.sin(t*2+o2)*0.4,e.z+Math.sin(oa)*2.6*s,0.4*s,0.8*s,0.4*s,e.enr?[1,0.3,0.3]:[0.8,0.4,1],1,0,oa);}}
if(e.hp<e.max&&!D.boss){var w=1.2*s*e.hp/e.max;A('cube',e.x,b+(e.kind==='grunt'?1.4:2.4)*s,e.z,w,0.1,0.1,[1,0.25,0.3],1,0,S.yaw+Math.PI/2);}
if(e.wind>0)A('cyl',e.x,gh(e.x,e.z)+0.05,e.z,(2.6+e.s)*2*(1-e.wind/0.55),0.04,(2.6+e.s)*2*(1-e.wind/0.55),[1,0.2,0.1],1,0,0);}
function npc(n,t){var b=gh(n.x,n.z),c=n.col,a=n.a,bob=Math.sin(t*2+n.hx)*0.03,wk=n.walk?Math.sin(n.walk)*0.15:0;
function at(lx,ly,lz,sx,sy,sz,col,mesh,em){var p=rotXZ(lx,lz,a);A(mesh||'cube',n.x+p[0],b+ly+bob,n.z+p[1],sx,sy,sz,col,em||0,0,a,0.2);}
at(0,0.75,0,0.9,1.5,0.9,c,'cone');at(0,0.5,0,0.75,1.0,0.75,scl3(c,0.8),'cyl');at(0,1.55,0,0.55,0.55,0.55,[0.96,0.8,0.64],'sphere');at(0,1.95,0,0.75,0.55,0.75,scl3(c,0.6),'pyr');
at(0.11,1.58,0.25,0.07,0.08,0.05,[0.1,0.1,0.1]);at(-0.11,1.58,0.25,0.07,0.08,0.05,[0.1,0.1,0.1]);at(0.45,1.0+wk,0,0.18,0.55,0.18,c);at(-0.45,1.0-wk,0,0.18,0.55,0.18,c);
if(G){var mk=null,id=n.id;G.allQuests.forEach(function(q){if(q.giver===id&&!G.q[q.id]&&!mk)mk='!';});G.curSteps().forEach(function(x){if((x.s.type==='talk'&&x.s.npc===id))mk='?';});
if(mk==='!')A('gem',n.x,b+2.7+Math.sin(t*3)*0.12,n.z,0.32,0.6,0.32,[1,0.82,0.2],1,0,t*2);else if(mk==='?')A('sphere',n.x,b+2.7+Math.sin(t*3)*0.12,n.z,0.36,0.36,0.36,[0.5,0.9,1],1,0,0);}}
var CHM={};
function chModel(id){var m=CHM[id];if(!m){m=CHM[id]={G:null};R.model(R2.assetPath(id)).then(function(G2){return G2.ready;}).then(function(G2){m.G=G2;m.clips=R2.clipsFor(G2,G2.M);}).catch(function(e){m.bad=true;if(window.console)console.warn('character',id,e.message);});}return m.G?m:null;}
function actor(ent,id,first){if(ent._act&&ent._actId===id)return ent._act;var m=chModel(id);if(!m)return null;ent._act=window.StarkAnim?StarkAnim.R2Actor(m.G,{entity:ent.o||ent,library:R2.HumanClips.src,clips:world.anim_clips||[]}):R2.Actor(m.G,m.clips);ent._actId=id;ent._act.play(first||'Idle_Loop',{at:Math.random()*2});return ent._act;}
var JOBS={baker:'baker',smith:'guard',scout:'adventurer',herbalist:'elder',fisher:'farmer',bard:'merchant',hunter:'adventurer',miller:'farmer',guard:'guard',elder:'elder',merchant:'merchant',farmer:'farmer',keeper:'merchant',witch:'elder',sage:'elder',knight:'guard',captain:'guard',cook:'baker',innkeep:'baker'};
function hstr(s2){var h=0;s2=String(s2||'');for(var i=0;i<s2.length;i++)h=(h*31+s2.charCodeAt(i))>>>0;return h;}
function npcLook(n){var o=n.o||{},nm=String(o.name||o.id||''),h=hstr(o.id||nm),job=o.outfit;if(!job){var lw=nm.toLowerCase();for(var k in JOBS)if(lw.indexOf(k)>=0){job=JOBS[k];break;}}
if(!job)job=['farmer','baker','merchant','elder','guard','farmer','adventurer'][h%7];var body=o.body||((h>>4)%2?'female':'male');return'characters/'+body+'_'+job;}
function heroId(){var b=(world.player||{}).body;return'characters/'+(b==='female'?'female':'male')+'_adventurer';}
function hero2(dt){var a=actor(P,heroId());if(!a)return false;var g=gh(P.x,P.z),gw=water!=null?Math.max(g,water-0.55):g,air=P.y>gw+0.3,st=stats(),cur=a.cur&&a.cur.name;
if(G&&G.st.hp<=0)a.play('Death01',{once:true});else if(P.dash>0)a.play('Roll',{speed:1.7,fade:0.08});
else if(P.atk>0.26||P.whirl>0.48)a.play('Sword_Attack',{once:true,restart:true,speed:P.whirl>0?2.8:2.1,fade:0.06,at:0.12});
else if((P.atk>0||P.whirl>0)&&cur==='Sword_Attack'&&!a.done){}else if(S.castT>0&&a.has('Spell_Simple_Shoot'))a.play('Spell_Simple_Shoot',{once:true,speed:1.6});
else if(air)a.play(P.vy>0?'Jump_Start':'Jump_Loop',{fade:0.12});else if(P.moving)a.play(st.speed>1.3?'Sprint_Loop':'Jog_Fwd_Loop',{speed:0.9+0.1*st.speed});
else a.play(S.combatT>0?'Sword_Idle':'Idle_Loop',{fade:0.25});a.update(dt);a.pose();R.drawSkinned(a.G,m4yaw(P.x,P.y,P.z,P.a,1),a.joints,a.nj,[1,1,1,1]);return true;}
function marker(n,b,t){if(!G)return;var mk=null,id=n.id;G.allQuests.forEach(function(q){if(q.giver===id&&!G.q[q.id]&&!mk)mk='!';});G.curSteps().forEach(function(x){if(x.s.type==='talk'&&x.s.npc===id)mk='?';});
if(mk==='!')A('gem',n.x,b+2.35+Math.sin(t*3)*0.1,n.z,0.28,0.52,0.28,[1,0.82,0.2],1,0,t*2);else if(mk==='?')A('sphere',n.x,b+2.35+Math.sin(t*3)*0.1,n.z,0.3,0.3,0.3,[0.5,0.9,1],1,0,0);}
function npc2(n,t,dt){var a=actor(n,npcLook(n));if(!a)return false;var b=gh(n.x,n.z),walking=n.walk!==n._lw;n._lw=n.walk;
var talking=G&&G.busy()&&n.talk>0;if(n.talk>0&&!(G&&G.busy()))n.talk=0;var h=hstr(n.id),idle=['Idle_Loop','Idle_Loop','Idle_Talking_Loop','Idle_Loop'][h%4];
if(S.night>0.55&&h%3===0&&a.has('Idle_Torch_Loop'))idle='Idle_Torch_Loop';a.play(walking?'Walk_Loop':talking?'Idle_Talking_Loop':idle,{fade:0.3});a.update(dt);a.pose();
R.drawSkinned(a.G,m4yaw(n.x,b,n.z,n.a,1),a.joints,a.nj,[1,1,1,1]);marker(n,b,t);return true;}
var ROCKS=['nature/Rock_Medium_1','nature/Rock_Medium_2','nature/Rock_Medium_3'];
function rockG(i){var m=chModel(ROCKS[i%3]);return m?m.G:null;}
function golem(e,t){var r0=rockG(0),r1=rockG(1),r2=rockG(2);if(!r0||!r1||!r2)return false;var s=e.s*(e.D.boss?1.6:1),b=gh(e.x,e.z)+e.y,f=e.face,fl=e.hit>0,tint=fl?[3.5,3.5,3.5,1]:e.enr?[1.4,0.55,0.5,1]:[0.92,0.9,0.86,1];
var mv=Math.hypot(e.x-(e._px||e.x),e.z-(e._pz||e.z))>0.002;e._px=e.x;e._pz=e.z;e._wt=(e._wt||0)+(mv?0.12:0.02);var sw=Math.sin(e._wt*3)*(mv?0.35:0.06),up=e.wind>0?1.1:0;
function part(G2,lx,ly,lz,sc,rx){var p=rotXZ(lx*s,lz*s,f);var M=mul(m4yaw(e.x+p[0],b+ly*s,e.z+p[1],f,sc*s),m4trs([0,0,0],qaxis([1,0,0],rx||0),[1,1,1]));R.drawModel(G2,M,tint,{tintAll:true});}
part(r0,0,1.05+Math.abs(sw)*0.05,0,0.42);part(r1,0,2.05,0.12,0.22);part(r2,0.78,1.35+up*0.6,0.05+sw*0.3,0.2,-sw-up);part(r2,-0.78,1.35+up*0.6,0.05-sw*0.3,0.2,sw-up);
part(r1,0.32,0.35,sw*0.35,0.16);part(r1,-0.32,0.35,-sw*0.35,0.16);var ec=e.wind>0?[1,0.2,0.1]:e.D.boss?[1,0.3,0.9]:[1,0.6,0.2];var ep=rotXZ(0.12*s,0.36*s,f),ep2=rotXZ(-0.12*s,0.36*s,f);
A('sphere',e.x+ep[0],b+2.12*s,e.z+ep[1],0.08*s,0.06*s,0.05*s,ec,1,0,f);A('sphere',e.x+ep2[0],b+2.12*s,e.z+ep2[1],0.08*s,0.06*s,0.05*s,ec,1,0,f);
if(e.D.boss)for(var c=0;c<5;c++){var cp=rotXZ((-0.5+c*0.25)*s,-0.1*s,f);A('gem',e.x+cp[0],b+(2.55+(c%2)*0.2)*s,e.z+cp[1],0.16*s,0.5*s,0.16*s,e.enr?[1,0.3,0.3]:[0.8,0.45,1],1,0,t+c);}
if(e.wind>0)A('cyl',e.x,gh(e.x,e.z)+0.05,e.z,(2.6+e.s)*2*(1-e.wind/0.55),0.04,(2.6+e.s)*2*(1-e.wind/0.55),[1,0.2,0.1],1,0,0);return true;}
function enemy2(e,t,dt){var b=gh(e.x,e.z)+e.y;
if(e.kind==='archer'){var a=actor(e,'characters/'+(e.i%2?'female':'male')+'_bandit');if(!a)return false;var mv=Math.hypot(e.x-(e._px||e.x),e.z-(e._pz||e.z))>0.002;e._px=e.x;e._pz=e.z;
if(e.hit>0.15)a.play('Hit_Chest',{once:true,speed:1.6,fade:0.05});else if(e._shot>0)a.play('Spell_Simple_Shoot',{once:true,speed:1.5});else a.play(mv?(e.st==='chase'?'Jog_Fwd_Loop':'Walk_Loop'):'Idle_Loop',{fade:0.2});
if(e._shot>0)e._shot-=dt;a.update(dt);a.pose();R.drawSkinned(a.G,m4yaw(e.x,b,e.z,e.face,e.s),a.joints,a.nj,e.enr?[1.3,0.6,0.6,1]:[1,1,1,1]);}
else if(e.kind==='brute'||e.D.boss){if(!golem(e,t))return false;}else return false;
if(e.hp<e.max&&!e.D.boss){var w=1.2*e.s*e.hp/e.max;A('cube',e.x,b+(e.kind==='archer'?2.15:2.55)*e.s,e.z,w,0.08,0.08,[1,0.25,0.3],1,0,S.yaw+Math.PI/2);}return true;}
function PG(n){return R2.prop(R,n);}
function chest2(h,t){var base=PG('chest_base'),lid=PG('chest_lid'),band=PG('chest_band');if(!base||!lid||!band)return false;var b=gh(h.x,h.z),M=m4yaw(h.x,b,h.z,h.rot,1);
h.lidA=lerp(h.lidA||0,h.open?1.9:0,0.12);R.drawModel(base,M,[1,1,1,1]);R.drawModel(band,mul(M,m4yaw(0,0.18,0,0,1)),[1,1,1,1]);
var hinge=mul(mul(M,m4trs([0,0.55,-0.33],[0,0,0,1],[1,1,1])),m4trs([0,0,0],qaxis([1,0,0],-h.lidA),[1,1,1]));R.drawModel(lid,mul(hinge,m4trs([0,0,0.33],[0,0,0,1],[1,1,1])),[1,1,1,1]);
if(!h.open){A('gem',h.x,b+0.62,h.z,0.12,0.16,0.06,[1,0.82,0.3],0.8,0,h.rot);if(Math.random()<0.15)R.part(h.x+(Math.random()-0.5)*0.8,b+0.9+Math.random()*0.4,h.z+(Math.random()-0.5)*0.5,0.04,[1,0.85,0.4],0.8);}return true;}
var DOORM={};function vmodel(id){var m=DOORM[id];if(!m){m=DOORM[id]={G:null};R.model(R2.assetPath(id)).then(function(G2){return G2.ready;}).then(function(G2){m.G=G2;}).catch(function(){});}return m.G;}
function door2(d,t){var fr=vmodel('village/DoorFrame_Round_Brick'),lf=vmodel('village/Door_1_Round');if(!fr||!lf)return false;var b=gh(d.x,d.z),M=mul(m4yaw(d.x,b,d.z,d.rot,1),[2.3,0,0,0,0,1.5,0,0,0,0,1.6,0,0,0,0,1]);
d.doorA=lerp(d.doorA||0,d.open?1.7:0,0.08);R.drawModel(fr,M,[1,1,1,1]);
R.drawModel(lf,mul(M,m4trs([-0.55,0,0],qaxis([0,1,0],d.doorA),[1,1,1])),[1,1,1,1]);R.drawModel(lf,mul(M,m4trs([0.55,0,0],qaxis([0,1,0],-d.doorA),[-1,1,1])),[1,1,1,1]);
if(!d.open)A('gem',d.x,b+1.45,d.z,0.22,0.26,0.1,[1,0.82,0.3],0.85,0,d.rot);return true;}
function portal2(p,t){var ring=PG('portal_ring'),base=PG('portal_base');if(!ring||!base)return false;var b=gh(p.x,p.z),lk=p.o.lock&&G&&G.quest(p.o.lock)&&!(G.q[p.o.lock]&&G.q[p.o.lock].state==='done'),c=lk?[0.9,0.25,0.25]:[0.55,0.45,1.0];
var M=m4yaw(p.x,b,p.z,p.rot,1);R.drawModel(base,mul(M,m4trs([0,-0.1,0],[0,0,0,1],[1,1,1])),[0.85,0.86,0.9,1]);R.drawModel(ring,mul(M,m4trs([0,1.95,0],[0,0,0,1],[1,1,1])),[0.85,0.86,0.9,1]);
var pu=1+Math.sin(t*4)*0.04;A('sphere',p.x,b+1.95,p.z,2.9*pu,2.9*pu,0.05,lk?[0.35,0.06,0.08]:mix3(c,[0.3,1,0.9],0.5+0.5*Math.sin(t*2)),lk?0.35:0.9,0,p.rot);
for(var i=0;i<14;i++){var a=i/14*Math.PI*2+t*0.8,lp=rotXZ(Math.cos(a)*1.25,0,p.rot);R.part(p.x+lp[0],b+1.95+Math.sin(a)*1.25,p.z+lp[1],0.09,c,0.9);}
if(!lk&&Math.random()<0.5){var ra=Math.random()*6.28,rr2=Math.random()*1.2,q=rotXZ(Math.cos(ra)*rr2,0,p.rot);R.part(p.x+q[0],b+1.95+Math.sin(ra)*rr2,p.z+q[1],0.06,[0.7,1,0.95],0.8);}return true;}
function shrine2(c,t){var base=PG('shrine_base'),col=PG('shrine_col'),br=PG('brazier');if(!base||!col||!br)return false;var b=gh(c.x,c.z),on=G&&G.cp&&G.cp.li===li&&Math.hypot(G.cp.x-c.x,G.cp.z-c.z-1.5)<0.6,M=m4yaw(c.x,b,c.z,0,1);
R.drawModel(base,mul(M,m4trs([0,-0.12,0],[0,0,0,1],[1,1,1])),[0.85,0.86,0.82,1]);R.drawModel(col,mul(M,m4trs([0,0.24,0],[0,0,0,1],[1,1,1])),[0.85,0.86,0.82,1]);R.drawModel(br,mul(M,m4trs([0,1.19,0],[0,0,0,1],[1,1,1])),[1,1,1,1]);
var fy=b+1.42,n=on?5:2;A('sphere',c.x,fy+0.05,c.z,on?0.42:0.26,on?0.5:0.3,on?0.42:0.26,on?[1,0.75,0.35]:[0.6,0.7,0.9],1,0,0);
for(var k=0;k<n;k++)R.part(c.x+(Math.random()-0.5)*0.35,fy+Math.random()*(on?0.9:0.4),c.z+(Math.random()-0.5)*0.35,on?0.12:0.07,on?[1,0.6,0.25]:[0.6,0.75,1],0.85);return true;}
function dynamic(dt){var m=S.mood,t=S.t,play=S.mode==='play';
if(play||S.drone){var flick=P.inv>0&&Math.floor(t*20)%2===0;if(!flick&&!(R.v2&&hero2(dt)))hero(P.x,P.y,P.z,P.a,P.ph,P.dash>0);
if(R.shadowSize<=1)A('cyl',P.x,gh(P.x,P.z)+0.03,P.z,1.0,0.02,1.0,[0.02,0.02,0.03],0,0,0,0);}
ents.cr.forEach(function(c){if(c.got&&play)return;var b=gh(c.x,c.z)+1.1+Math.sin(t*2+c.x)*0.25;A('gem',c.x,b,c.z,0.55,1.0,0.55,m.fxCol,0.9,0,t*1.5+c.x,1);A('gem',c.x,b,c.z,0.25,0.45,0.25,[1,1,1],1,0,-t*2);});
ents.en.forEach(function(e){if(e.dead&&play)return;if(play&&!S.drone&&Math.abs(e.x-P.x)+Math.abs(e.z-P.z)>160)return;if(!(R.v2&&enemy2(e,t,dt)))enemy(e,t);});
ents.npc.forEach(function(n){if(!(R.v2&&npc2(n,t,dt)))npc(n,t);});
ents.it.forEach(function(it){var c=hexCol(G?G.item(it.item).col:(ITEMS[it.item]||{}).col,[1,1,1]),b=gh(it.x,it.z)+0.7+Math.sin(t*3+it.x)*0.15;A('gem',it.x,b,it.z,0.45,0.6,0.45,c,0.75,0,t*2,1);A('cyl',it.x,gh(it.x,it.z)+0.03,it.z,0.9,0.02,0.9,c,0.6,0,0);});
ents.ch.forEach(function(h){if(R.v2&&chest2(h,t))return;var b=gh(h.x,h.z),wd=[0.5,0.3,0.15],gd=[0.95,0.75,0.3];A('cube',h.x,b+0.3,h.z,1.0,0.6,0.7,wd,0,0,h.rot,0.2);A('cube',h.x,b+0.3,h.z,1.04,0.12,0.74,gd,0,0,h.rot,0.8);
if(h.open){var lp=rotXZ(0,-0.4,h.rot);A('cube',h.x+lp[0],b+0.75,h.z+lp[1],1.0,0.5,0.12,wd,0,0,h.rot,0.2);}else{A('cube',h.x,b+0.7,h.z,1.0,0.25,0.7,scl3(wd,1.1),0,0,h.rot,0.2);A('cube',h.x,b+0.55,h.z,0.16,0.2,0.76,gd,0.4,0,h.rot,0.8);}});
ents.dr.forEach(function(d){if(R.v2&&door2(d,t))return;var b=gh(d.x,d.z),p1=rotXZ(1.6,0,d.rot),p2=rotXZ(-1.6,0,d.rot);A('cube',d.x+p1[0],b+1.5,d.z+p1[1],0.6,3.0,0.7,m.stone,0,0,d.rot,0.3);A('cube',d.x+p2[0],b+1.5,d.z+p2[1],0.6,3.0,0.7,m.stone,0,0,d.rot,0.3);A('cube',d.x,b+3.15,d.z,3.9,0.5,0.8,scl3(m.stone,0.9),0,0,d.rot,0.3);
if(!d.open){A('cube',d.x,b+1.4,d.z,2.6,2.8,0.3,[0.35,0.22,0.12],0,0,d.rot,0.2);A('cube',d.x,b+1.4,d.z,0.35,0.35,0.36,[1,0.82,0.3],0.8,0,d.rot,0.9);}});
ents.po.forEach(function(p){if(R.v2&&portal2(p,t))return;var b=gh(p.x,p.z),lk=p.o.lock&&G&&G.quest(p.o.lock)&&!(G.q[p.o.lock]&&G.q[p.o.lock].state==='done'),c=lk?[0.9,0.25,0.25]:[0.55,0.45,1.0];
for(var i=0;i<12;i++){var a=i/12*Math.PI*2+t*0.6,lp=rotXZ(Math.cos(a)*1.5,0,p.rot);A('gem',p.x+lp[0],b+1.9+Math.sin(a)*1.5,p.z+lp[1],0.3,0.45,0.3,c,1,0,a);}
var pu=1+Math.sin(t*4)*0.06;A('cube',p.x,b+1.9,p.z,2.4*pu,2.4*pu,0.06,lk?[0.4,0.08,0.1]:mix3(c,[0.3,1,0.9],0.5+0.5*Math.sin(t*2)),lk?0.4:0.85,0,p.rot);A('cyl',p.x,b+0.08,p.z,3.4,0.16,3.4,m.stone,0,0,0,0.3);});
ents.cp.forEach(function(c){if(R.v2&&shrine2(c,t))return;var b=gh(c.x,c.z),on=G&&G.cp&&G.cp.li===li&&Math.hypot(G.cp.x-c.x,G.cp.z+0-c.z-1.5)<0.6;A('cyl',c.x,b+0.5,c.z,0.9,1.0,0.9,m.stone,0,0,0,0.3);A('cyl',c.x,b+1.05,c.z,1.1,0.12,1.1,scl3(m.stone,1.2),0,0,0,0.3);A('sphere',c.x,b+1.6+Math.sin(t*2)*0.1,c.z,0.55,0.55,0.55,on?[1,0.9,0.5]:[0.5,0.6,0.7],on?1:0.5,0,0);});
if(G&&play){G.curSteps().forEach(function(x){var tg=target(x.s);if(!tg)return;var b=gh(tg[0],tg[1]),pul=0.5+0.5*Math.sin(t*4);A('cube',tg[0],b+9,tg[1],0.12+pul*0.06,18,0.12+pul*0.06,[1,0.85,0.3],0.7,0,0);for(var r2=0;r2<8;r2++){var ra=r2/8*Math.PI*2+t;A('gem',tg[0]+Math.cos(ra)*1.4,b+0.3,tg[1]+Math.sin(ra)*1.4,0.2,0.3,0.2,[1,0.85,0.3],1,0,ra);}});}
S.proj.forEach(function(p){A('sphere',p.x,p.y,p.z,0.38,0.38,0.38,p.c,1,0,0);A('sphere',p.x-p.vx*0.03,p.y,p.z-p.vz*0.03,0.25,0.25,0.25,p.c,0.8,0,0);});
var wx=weatherKind(),em=wx==='snow'?0.25:wx==='pollen'?0.45:wx==='rain'?0.3:1;
if(R.v2){var pc=wx==='snow'?[0.95,0.97,1]:m.fxCol,ps=wx==='snow'?0.07:wx==='fireflies'?0.09:wx==='embers'?0.06:0.05,pa=wx==='snow'?0.35:wx==='fireflies'?(0.55+0.45*Math.sin(t*3)):wx==='pollen'?0.25:0.6;
S.parts.forEach(function(p,i){if(wx==='rain'||wx==='storm')A('cyl',p.x,p.y,p.z,0.015,0.6,0.015,[0.75,0.82,0.95],0.25,0,0);else R.part(p.x,p.y,p.z,ps*(0.7+0.6*((i*7)%10)/10),pc,pa*(wx==='fireflies'?(0.5+0.5*Math.sin(t*2.3+i)):1));});
S.bursts=S.bursts.filter(function(b){b.life-=dt;if(S.hitStop<=0){b.vy-=14*dt;b.x+=b.vx*dt;b.y+=b.vy*dt;b.z+=b.vz*dt;}if(b.life>0)R.part(b.x,b.y,b.z,b.s*1.4,b.c,Math.min(1,b.life*2.2));return b.life>0;});}
else{S.parts.forEach(function(p){if(wx==='rain'||wx==='storm')A('cube',p.x,p.y,p.z,0.03,0.7,0.03,[0.7,0.8,1],0.35,0,0);else A(wx==='snow'?'sphere':'cube',p.x,p.y,p.z,0.11,0.11,0.11,m.fxCol,em,0,p.p);});
S.bursts=S.bursts.filter(function(b){b.life-=dt;if(S.hitStop<=0){b.vy-=14*dt;b.x+=b.vx*dt;b.y+=b.vy*dt;b.z+=b.vz*dt;}if(b.life>0)A('cube',b.x,b.y,b.z,b.s,b.s,b.s,b.c,1,0,b.life*9);return b.life>0;});}
if(S.mode==='edit'&&S.sel>=0){var so=(L.objects||[])[S.sel];if(so){var sb=gh(+so.x,+so.z);for(var q=0;q<12;q++){var qa=q/12*Math.PI*2+t;A('gem',+so.x+Math.cos(qa)*1.6,sb+0.3,+so.z+Math.sin(qa)*1.6,0.18,0.3,0.18,[0.4,1,0.8],1,0,qa);}}}}
function target(s){if(!s)return null;if(s.type==='reach')return[+s.x||0,+s.z||0];if(s.type==='escort'){var es=s.x!=null?[+s.x,+s.z]:null;return es;}
if(s.type==='talk'){for(var i=0;i<ents.npc.length;i++)if(ents.npc[i].id===s.npc)return[ents.npc[i].x,ents.npc[i].z];return null;}
if(s.type==='defeat'){var best=null,bd=1e9;ents.en.forEach(function(e){if(e.dead)return;if((s.id&&e.id!==s.id)||(!s.id&&s.kind&&s.kind!=='any'&&e.kind!==s.kind))return;var d=Math.hypot(e.x-P.x,e.z-P.z);if(d<bd){bd=d;best=[e.x,e.z];}});return best;}
if(s.type==='collect'){var b2=null,bd2=1e9;function c(x,z){var d=Math.hypot(x-P.x,z-P.z);if(d<bd2){bd2=d;b2=[x,z];}}if(s.item==='crystal')ents.cr.forEach(function(q){if(!q.got)c(q.x,q.z);});ents.it.forEach(function(it){if(it.item===s.item)c(it.x,it.z);});ents.ch.forEach(function(h){if(!h.open&&h.o.item===s.item)c(h.x,h.z);});return b2;}return null;}
function weatherKind(){var w=L.weather;if(w&&w!=='auto')return w==='clear'?'none':w;return S.mood.fx;}
function weather(dt,cx,cz){var wx=weatherKind(),ps=S.parts,Rr=26,n=wx==='none'?0:(wx==='rain'||wx==='storm')?500:320;if(ps.length>n)ps.length=n;while(ps.length<n)ps.push({x:cx+(Math.random()*2-1)*Rr,y:Math.random()*14,z:cz+(Math.random()*2-1)*Rr,p:Math.random()*6});
if(wx==='storm'&&Math.random()<dt*0.12){S.flash=1;setTimeout(function(){playSfx(SFX.boom);},200);}
ps.forEach(function(p){p.p+=dt;var f=wx;
if(f==='rain'||f==='storm'){p.y-=dt*22;p.x+=dt*2;}else if(f==='snow'){p.y-=dt*2.2;p.x+=Math.sin(p.p)*dt*0.6;}else if(f==='embers'){p.y+=dt*1.6;p.x+=Math.sin(p.p*2)*dt;}else if(f==='bubbles'){p.y+=dt*1.2;p.x+=Math.sin(p.p*3)*dt*0.4;}
else if(f==='pollen'){p.x+=dt*0.8;p.y+=Math.sin(p.p)*dt*0.3;}else{p.x+=Math.sin(p.p*1.3)*dt;p.z+=Math.cos(p.p*1.1)*dt;p.y+=Math.sin(p.p*2)*dt*0.4;}
if(p.y<gh(p.x,p.z)-0.5||p.y>16||Math.abs(p.x-cx)>Rr||Math.abs(p.z-cz)>Rr){if((f==='rain'||f==='storm')&&p.y<16&&Math.random()<0.3)S.bursts.push({x:p.x,y:gh(p.x,p.z)+0.05,z:p.z,vx:0,vy:1.5,vz:0,life:0.12,c:[0.7,0.8,1],s:0.06});
p.x=cx+(Math.random()*2-1)*Rr;p.z=cz+(Math.random()*2-1)*Rr;p.y=(f==='snow'||f==='rain'||f==='storm')?14:f==='embers'||f==='bubbles'?gh(p.x,p.z)+0.2:gh(p.x,p.z)+Math.random()*6+0.5;}});}
function camVecs(){var e,c;if(S.camOv&&S.mode==='play'){return{e:S.camOv.e.slice(),c:S.camOv.c.slice()};}if(S.drone){var a=S.droneA;c=[P.x*0.3,2,P.z*0.3];e=[c[0]+Math.cos(a)*36,17+Math.sin(a*0.7)*5,c[2]+Math.sin(a)*36];}
else if(S.mode==='edit'){c=[S.editT[0],gh(S.editT[0],S.editT[2]),S.editT[2]];var dd=S.editDist;e=[c[0]+Math.sin(S.editYaw)*dd,c[1]+dd*0.88,c[2]+Math.cos(S.editYaw)*dd];}
else{c=[P.x,P.y+1.4,P.z];var cp=Math.cos(S.pitch),d2=S.dist,arm=1;for(var tj=2;tj<=12;tj++){var t2=tj/12;if(blocked(P.x+Math.sin(S.yaw)*cp*d2*t2,P.z+Math.cos(S.yaw)*cp*d2*t2,0.3)){arm=Math.max(0.3,(tj-1)/12);break;}}
S.arm=S.arm==null?arm:S.arm+(arm-S.arm)*(arm<S.arm?0.35:0.06);d2*=S.arm;e=[P.x+Math.sin(S.yaw)*cp*d2,P.y+1.4+Math.sin(S.pitch)*d2,P.z+Math.cos(S.yaw)*cp*d2];for(var ti=1;ti<=6;ti++){var tt=ti/6,qx=c[0]+(e[0]-c[0])*tt,qz=c[2]+(e[2]-c[2])*tt,qy=c[1]+(e[1]-c[1])*tt,gq=gh(qx,qz)+0.7;if(qy<gq)e[1]+=(gq-qy)/tt;}var gy=gh(e[0],e[2])+0.8;if(e[1]<gy)e[1]=gy;}
if(S.shake>0){var k=S.shake*0.6;e=[e[0]+(Math.random()-0.5)*k,e[1]+(Math.random()-0.5)*k,e[2]+(Math.random()-0.5)*k];}
return{e:e,c:c};}
function lights(E){var out=[],m=S.mood,lc=m.fxCol,nk=0.6+E.night*1.2;function L2(x,y,z,r,c,k){out.push([x,y,z,r,scl3(c,k),Math.hypot(x-P.x,z-P.z)]);}
var vc=S.mode==='edit'?S.editT:[P.x,0,P.z];(S.lamps||[]).forEach(function(o){if(Math.abs(o[0]-vc[0])+Math.abs(o[2]-vc[2])>90)return;if(!o[3])L2(o[0],o[1]+2.4,o[2],10,lc,1.3*nk);else if(E.night>0.3)L2(o[0],o[1]+1.6,o[2]+2.4,7,[1,0.7,0.35],nk);});
ents.cr.forEach(function(c){if(!c.got||S.mode!=='play')L2(c.x,gh(c.x,c.z)+1.2,c.z,6,lc,nk*0.8);});ents.po.forEach(function(p){L2(p.x,gh(p.x,p.z)+1.9,p.z,9,[0.55,0.45,1],1.4*nk);});
ents.en.forEach(function(e){if(!e.dead&&(e.kind==='wisp'||e.D.boss))L2(e.x,gh(e.x,e.z)+e.y+1,e.z,e.D.boss?12:6,e.D.boss?[0.9,0.4,1]:[0.5,0.8,1],nk);});
S.proj.forEach(function(p){L2(p.x,p.y,p.z,5,p.c,1.5);});ents.cp.forEach(function(c){L2(c.x,gh(c.x,c.z)+1.6,c.z,5,[1,0.85,0.5],0.7*nk);});
if(S.mode==='play'&&E.night>0.35)L2(P.x,P.y+2.2,P.z,9,[1,0.75,0.45],E.night*0.9);
out.sort(function(a,b){return a[5]-b[5];});return out.slice(0,12);}
function shadowVP(E,cx,cz){var Rr=R.q==='high'?34:28,ts=2*Rr/Math.max(1,R.shadowSize);cx=Math.floor(cx/ts)*ts;cz=Math.floor(cz/ts)*ts;var ld=E.lightDir;if(ld[1]<0.08)ld=norm([ld[0],0.08,ld[2]]);var cy=gh(cx,cz);
var eye=[cx+ld[0]*70,cy+ld[1]*70,cz+ld[2]*70];return mul(ortho(-Rr,Rr,-Rr,Rr,1,160),lookAt(eye,[cx,cy,cz],Math.abs(ld[1])>0.99?[0,0,1]:[0,1,0]));}
var mm=null;if(G){mm=document.createElement('canvas');mm.className='mm';mm.width=150;mm.height=150;mm.style.cssText='position:absolute;inset:auto 12px 12px auto;width:150px;height:150px;border-radius:50%;opacity:.85;pointer-events:none';G.root.appendChild(mm);}
function drawMini(){if(!mm)return;var g=mm.getContext('2d'),sc=75/Math.min(size,60);g.clearRect(0,0,150,150);g.save();g.beginPath();g.arc(75,75,74,0,7);g.fillStyle='rgba(8,12,20,.65)';g.fill();g.clip();
function pt(x,z,c,r){var px=75+(x-P.x)*sc,pz=75+(z-P.z)*sc;g.fillStyle=c;g.beginPath();g.arc(px,pz,r,0,7);g.fill();}
if(water!=null){g.fillStyle='rgba(60,140,200,.25)';for(var x=-size;x<size;x+=4)for(var z=-size;z<size;z+=4)if(gh(x,z)<water)g.fillRect(75+(x-P.x)*sc,75+(z-P.z)*sc,4*sc,4*sc);}
(L.objects||[]).forEach(function(o){if(o.t==='house'||o.t==='ruin')pt(+o.x,+o.z,'rgba(220,210,190,.6)',3);});
ents.cr.forEach(function(c){if(!c.got)pt(c.x,c.z,'#7fffd0',2);});ents.en.forEach(function(e){if(!e.dead)pt(e.x,e.z,e.D.boss?'#ff4dd2':'#ff4d6d',e.D.boss?5:2.5);});ents.npc.forEach(function(n){pt(n.x,n.z,'#ffd166',3.5);});
ents.po.forEach(function(p){pt(p.x,p.z,'#9b8cff',4.5);});ents.ch.forEach(function(h){if(!h.open)pt(h.x,h.z,'#c08a40',3);});
if(G)G.curSteps().forEach(function(x){var tg=target(x.s);if(tg){var px=clamp(75+(tg[0]-P.x)*sc,8,142),pz=clamp(75+(tg[1]-P.z)*sc,8,142);g.fillStyle='#ffd166';g.font='bold 14px sans-serif';g.fillText('★',px-6,pz+5);}});
g.translate(75,75);g.rotate(-P.a+Math.PI);g.fillStyle='#fff';g.beginPath();g.moveTo(0,-7);g.lineTo(5,5);g.lineTo(-5,5);g.fill();g.restore();}
function lin(c){return srgb2lin(c);}
var CYC={day:'hdri/kloofendal_48d_partly_cloudy_puresky_1k.hdr',dusk:'hdri/kloppenheim_06_puresky_1k.hdr',night:'hdri/kloppenheim_02_puresky_1k.hdr'};
function hdriRot(H,az){var sd=H.sunDir;return Math.atan2(sd[0],-sd[2])-az;}
function frame2(E,cv,asp,f,rt,up){var lk=S.look||R2.look(L),day=L.day&&+L.day.len>0,H=(S.hdri&&!day)?S.hdri:null,sky,sunDir,sunCol,key,fogC,expo=lk.expo,C=S.cyc;
if(H){var k=lk.skyK/Math.max(1e-4,H.avg),sd=H.sunDir,ph=Math.atan2(sd[0],-sd[2])-lk.rot,ct=Math.sqrt(Math.max(0,1-sd[1]*sd[1])),tint=lin(hexCol(lk.sunTint||'#ffffff'));
sunDir=norm([ct*Math.sin(ph),Math.max(sd[1],0.07),-ct*Math.cos(ph)]);var sl=clamp((H.sunIrr[0]+H.sunIrr[1]+H.sunIrr[2])/3*k,0.5,6.5)*lk.sunK;sunCol=[tint[0]*sl,tint[1]*sl,tint[2]*sl];
sky={mode:'hdri',hdri:H,rot:lk.rot,k:k,aur:lk.aur?1:0,time:S.t};key='h:'+lk.hdri+':'+lk.rot;fogC=lin(hexCol(lk.fog));}
else if(day&&C&&C.day&&C.dusk&&C.night){var clock=G&&G.clock!=null?G.clock:(+L.day.start||0.3),ang=(clock-0.25)*Math.PI*2,sy=Math.sin(ang),A,B,mx;
if(sy>0.3){A=C.day;B=C.day;mx=0;}else if(sy>0.04){A=C.day;B=C.dusk;mx=1-(sy-0.04)/0.26;}else if(sy>-0.14){A=C.dusk;B=C.night;mx=(0.04-sy)/0.18;}else{A=C.night;B=C.night;mx=0;}
var tgt=function(Hh){return Hh===C.day?0.45:Hh===C.dusk?0.42:0.16;},isSun=sy>-0.06,ld=E.lightDir,az=Math.atan2(ld[0],-ld[2]),dk=E.dayK==null?1:E.dayK;
sunDir=norm([ld[0],Math.max(ld[1],0.06),ld[2]]);var lc=lin(E.lightCol);sunCol=isSun?scl3(lc,3.4*(lk.sunK||1)):scl3([0.34,0.42,0.62],0.9);
var nt=[0.72,0.82,1.1],tint=mix3(nt,[1,1,1],clamp(dk*1.4,0,1));
sky={mode:'hdri',hdri:A,hdri2:B,mix:clamp(mx,0,1),k:tgt(A)/Math.max(1e-4,A.avg),k2:tgt(B)/Math.max(1e-4,B.avg),rot:hdriRot(A,az),rot2:hdriRot(B,az),tint:tint,bgClamp:4.0,
discDir:E.sunDir,discCol:isSun?scl3(lc,2.5*dk):[0,0,0],aur:S.mood.aurora?1:0,time:S.t};
key='c:'+Math.round(mx*16)+':'+(A===C.day?'d':A===C.dusk?'k':'n')+(B===C.day?'d':B===C.dusk?'k':'n')+':'+Math.round(az*12)+':'+Math.round(sy*20);
var dayFog=mix3(lin(hexCol('#9fb6c8')),lin(hexCol(lk.fog)),lk.cycleFogMix==null?0.35:lk.cycleFogMix),nightFog=lin(hexCol('#0c1424'));fogC=mix3(nightFog,dayFog,clamp(dk,0,1));expo=lerp(lk.nightExpo||1.9,lk.expo,clamp(dk,0,1));}
else{var ld2=E.lightDir;sunDir=norm([ld2[0],Math.max(ld2[1],0.05),ld2[2]]);var lc2=lin(E.lightCol);sunCol=scl3(lc2,3.0*(lk.sunK||1));
sky={mode:'proc',zen:lin(E.zen),hor:lin(E.hor),sunCol:scl3(lc2,2.5),sunDir:E.sunDir,night:E.night,aur:S.mood.aurora?1:0,time:S.t,stars:E.night};key='p:'+L.mood+':'+sunDir.map(function(v){return Math.round(v*25);}).join(',')+':'+Math.round(E.night*10);fogC=lin(E.fog);}
if(R.envKey!==key){R.envKey=key;R.invalidateEnv();}
var fogS=lin(hexCol(lk.fogSun||lk.fog)),edit=S.mode==='edit';
var Ls=lights(E).map(function(l){return[l[0],l[1],l[2],l[3]*1.25,scl3(lin(l[4]),2.2)];});
return{eye:cv.e,target:cv.c,fwd:f,right:rt,up:up,fov:1.0,asp:asp,near:0.1,far:420,t:S.t,sunDir:sunDir,sunCol:sunCol,sky:sky,envK:lk.envK,
fogCol:fogC,fogSun:scl3(fogS,0.6),fogDen:lk.fogDen*(edit?0.5:1),fogH:lk.fogH,fogY:water!=null?water:-1.2,skyFog:0.45,
meadow:[lin(hexCol(lk.meadow[0])),lin(hexCol(lk.meadow[1]))],pathCol:scl3(lin(hexCol(lk.path)),1.6),tile:3.0,
water:water,size:size,wcol:lin(hexCol(lk.wcol)),wdeep:lin(hexCol(lk.wdeep)),lights:Ls,expo:expo,grade:lk.grade,bloomK:lk.bloomK,
hurt:S.hurtFx,flash:S.flash*0.6,wind:[0.85,0.35,weatherKind()==='storm'?1.1:0.55,0.12],cut:S.cut,ice:!!lk.ice,aerial:lk.aerial==null?0.35:lk.aerial,shafts:lk.shafts==null?0.3:lk.shafts,tone:lk.tone,sunAng:0.03,mtn:lk.mountains?{seed:seed,rock:lin(hexCol(lk.mtnCol||'#5d6470')),rock2:lin(hexCol(lk.mtnCol2||'#3e4450')),snow:lk.mtnSnow==null?0.6:lk.mtnSnow,haze:lk.mtnHaze||[0.35,0.22,0.35],base:-2}:null};}
var autoT=0,autoN=0,autoSum=0;
function frame(ts){S.raf=requestAnimationFrame(frame);var now=ts/1000,rdt=now-(S.last||now),dt=Math.min(0.05,rdt)*TSC();S.last=now;if(window.GS_BUSY)return;
if(Q==='auto'&&rdt>0&&rdt<1&&(!R.v2||(S.sceneReady&&(S.readyT=(S.readyT||0)+rdt)>3))){autoSum+=rdt;autoN++;autoT+=rdt;if(autoT>2.5){var avg=autoSum/autoN;if(avg>0.05&&R.q!=='low')R.setQuality('low');else if(avg>0.034&&R.q!=='low')R.setQuality(R.q==='high'?'medium':'low');else if(avg<0.013&&R.q==='medium'&&S.frames>200)R.setQuality('high');autoT=0;autoN=0;autoSum=0;}}
fit(canvas,Math.min(R.scale,1.5/(window.devicePixelRatio||1))*(window.devicePixelRatio>1?1:1));if(S.dirty)buildStatic();update(dt);var cv=camVecs();weather(dt,cv.c[0],cv.c[2]);R.beginDynamic();dynamic(dt);R.upload();
var E=levelEnv(L,S.mood,G&&G.clock!=null?G.clock:(L.day&&L.day.start!=null?+L.day.start:0.3)),asp=canvas.width/canvas.height,f=norm(sub(cv.c,cv.e)),rt=norm(cross(f,[0,1,0])),up=cross(rt,f);
var F={vp:mul(persp(1.0,asp,0.1,500),lookAt(cv.e,cv.c,[0,1,0])),eye:cv.e,f:f,r:rt,u:up,th:Math.tan(0.5),t:S.t,lightDir:E.lightDir,lightCol:E.lightCol,sunDir:E.sunDir,sunCol:E.lightCol,skyAmb:E.skyAmb,gndAmb:E.gndAmb,
fog:E.fog,fogD:S.mood.fogD*(S.mode==='edit'?0.6:1),zen:E.zen,hor:E.hor,night:E.night,aur:S.mood.aurora?1:0,flash:S.flash*0.6,water:water,size:size,wcol:S.mood.water,seed:seed,amp:amp,expo:E.expo,grade:S.mood.grade,hurt:S.hurtFx,lights:lights(E)};
if(R.v2){F=frame2(E,cv,asp,f,rt,up);S.cut=false;}
else if(R.shadowSize>1){var sc=S.mode==='edit'?[S.editT[0],S.editT[2]]:[P.x,P.z];F.svp=shadowVP(E,sc[0],sc[1]);}
R.render(F);S.frames=(S.frames||0)+1;
if(rdt>0&&rdt<1){S.ftAcc=(S.ftAcc||0)+rdt;S.ftN=(S.ftN||0)+1;if(S.ftAcc>=1){S.ftMs=Math.round(S.ftAcc/S.ftN*10000)/10;S.ftAcc=0;S.ftN=0;}}
if(S.shot){var cb=S.shot;S.shot=null;cb(canvas.toDataURL('image/png'));}
S.hudT-=dt;if(S.hudT<=0){S.hudT=0.2;drawMini();if(opt.onHud)opt.onHud({score:G?G.st.score:S.score,hp:G?Math.ceil(G.st.hp):S.hp,left:ents.cr.filter(function(c){return!c.got;}).length,total:ents.cr.length,won:G?G.won:S.won,drone:S.drone,mode:S.mode,frames:S.frames,instances:R.count(),quality:R.q,level:li,perf:S.ftMs?{ms:S.ftMs,q:R.q,setting:Q}:null});}}
function ray(ev){var r=canvas.getBoundingClientRect(),x=((ev.clientX-r.left)/r.width)*2-1,y=1-((ev.clientY-r.top)/r.height)*2,cv=camVecs();
var f=norm(sub(cv.c,cv.e)),rt=norm(cross(f,[0,1,0])),up=cross(rt,f),th=Math.tan(0.5),asp=r.width/r.height;return{o:cv.e,d:norm([f[0]+rt[0]*x*th*asp+up[0]*y*th,f[1]+rt[1]*x*th*asp+up[1]*y*th,f[2]+rt[2]*x*th*asp+up[2]*y*th])};}
function pick(ev){var R2=ray(ev),p=R2.o,d=R2.d;for(var i=0;i<600;i++){var t=i*0.4,x=p[0]+d[0]*t,y=p[1]+d[1]*t,z=p[2]+d[2]*t;if(y<=gh(x,z))return[x,z];}return null;}
function nearest(p,maxd){var best=-1,bd=maxd||3;(L.objects||[]).forEach(function(o,i){var dd=Math.hypot(o.x-p[0],o.z-p[1]);if(dd<bd){bd=dd;best=i;}});return best;}
function onDown(ev){if(ev.button===2)return;S.drag={x:ev.clientX,y:ev.clientY,moved:false};if(S.mode==='edit'&&S.tool==='select'){var p=pick(ev);if(p){var i=nearest(p,3);if(i>=0){S.sel=i;S.drag.obj=i;if(opt.onSelect)opt.onSelect(i);}}}}
function onMove(ev){if(!S.drag)return;var dx=ev.clientX-S.drag.x,dy=ev.clientY-S.drag.y;if(Math.abs(dx)+Math.abs(dy)>3)S.drag.moved=true;S.drag.x=ev.clientX;S.drag.y=ev.clientY;
if(S.drag.obj!=null&&S.drag.moved){var p=pick(ev),o=L.objects[S.drag.obj];if(p&&o){o.x=Math.round(p[0]*100)/100;o.z=Math.round(p[1]*100)/100;S.dirty=true;resetPlay(true);S.drag.changed=true;}return;}
if(S.mode==='play'){S.yaw-=dx*0.008;S.pitch=clamp(S.pitch+dy*0.005,0.12,1.2);}else{S.editYaw-=dx*0.006;}}
function onUp(ev){var d=S.drag;S.drag=null;if(d&&d.obj!=null){if(d.changed&&opt.onChange)opt.onChange(L);return;}if(d&&d.moved)return;if(!d)return;
if(S.mode==='play'){attack();return;}
var p=pick(ev);if(!p||Math.abs(p[0])>size||Math.abs(p[1])>size)return;L.objects=L.objects||[];
if(S.tool==='pickpoint'){if(opt.onPoint)opt.onPoint(Math.round(p[0]*10)/10,Math.round(p[1]*10)/10);return;}
if(S.tool==='select'){S.sel=-1;if(opt.onSelect)opt.onSelect(-1);return;}
if(S.tool==='erase'){var best=nearest(p,3);if(best>=0)L.objects.splice(best,1);S.sel=-1;}
else if(S.tool==='spawn'){L.spawn={x:Math.round(p[0]*10)/10,z:Math.round(p[1]*10)/10};spawn=L.spawn;}
else{var o={t:S.tool,x:Math.round(p[0]*100)/100,z:Math.round(p[1]*100)/100,s:Math.round((0.85+Math.random()*0.4)*100)/100};var tp=S.toolProps||{};for(var k in tp)if(tp[k]!==''&&tp[k]!=null)o[k]=tp[k];
if(o.t==='npc'&&!o.id){var n=1;while((L.objects||[]).some(function(q){return q.id==='npc'+n;}))n++;o.id='npc'+n;o.name=o.name||'Villager '+n;o.s=1;}
if(o.t==='enemy'||o.t==='portal'||o.t==='chest'||o.t==='door'||o.t==='checkpoint'||o.t==='item')o.s=1;L.objects.push(o);S.sel=L.objects.length-1;if(opt.onSelect)opt.onSelect(S.sel);playSfx(SFX.talk);}
resetPlay(true);S.dirty=true;if(opt.onChange)opt.onChange(L);}
function onWheel(ev){if(S.mode==='edit')S.editDist=clamp(S.editDist*(ev.deltaY>0?1.1:0.9),8,110);else S.dist=clamp(S.dist*(ev.deltaY>0?1.1:0.9),4,20);ev.preventDefault();}
canvas.addEventListener('pointerdown',onDown);window.addEventListener('pointermove',onMove);canvas.addEventListener('pointerup',onUp);canvas.addEventListener('wheel',onWheel,{passive:false});canvas.addEventListener('contextmenu',function(e){e.preventDefault();});
input.onKey=function(c){if(S.mode==='play'&&G&&G.keys(c))return;if(c==='KeyP')api.toggleDrone();if(S.mode!=='play')return;
if(c==='KeyJ')attack();else if(c==='KeyE'||c==='Enter')interact();else if(c==='KeyK')whirl();else if(c==='KeyF')bolt();else if(c==='ShiftLeft'||c==='ShiftRight')dash();};
var api={kind:'3d',world:L,
setMood:function(name){if(MOODS[name]){L.mood=name;S.mood=MOODS[name];S.parts=[];S.dirty=true;}},
setTool:function(t,props){S.tool=t;S.toolProps=props||{};},setMode:function(m){S.mode=m;resetPlay();S.drone=false;S.sel=-1;S.dirty=true;},
toggleDrone:function(){S.drone=!S.drone;S.droneA=Math.atan2(P.z,P.x);return S.drone;},rebuild:function(){S.dirty=true;resetPlay(true);},
select:function(i){S.sel=i;},getView:function(){return{t:S.editT.slice(),y:S.editYaw,d:S.editDist};},setView:function(v){if(v){S.editT=v.t;S.editYaw=v.y;S.editDist=v.d;}},
setQuality:function(q){Q=q;setQ(q);R.setQuality(q==='auto'?autoQ():q);if(R.v2)S.dirty=true;S.ftAcc=0;S.ftN=0;S.ftT=0;},
perf:function(){return{ms:S.ftMs||0,fps:S.ftMs?Math.round(10000/S.ftMs)/10:0,q:R.q,setting:Q,hw:HW,instances:R.count()};},
postcard:function(cb){S.shot=cb;},state:function(){return S;},player:function(){return P;},ents:function(){return ents;},
getPos:function(){return{x:Math.round(P.x*100)/100,z:Math.round(P.z*100)/100,yaw:S.yaw};},setPos:function(p){P.x=+p.x;P.z=+p.z;P.y=gh(P.x,P.z);if(p.yaw!=null)S.yaw=p.yaw;},
respawn:function(cp){P.x=cp?cp.x:spawn.x;P.z=cp?cp.z:spawn.z;P.y=gh(P.x,P.z)+1;P.vy=0;P.inv=2;S.proj=[];ents.en.forEach(function(e){if(!e.dead){e.st='return';}});},
unit:1,step:function(dt){update(dt);},groundAt:gh,shake:function(v){S.shake=Math.max(S.shake,+v||0.5);},
camSet:function(e,c){S.camOv=e?{e:e,c:c}:null;},camNow:function(){var o=S.camOv;S.camOv=null;var v=camVecs();S.camOv=o;return v;},
entPos:function(id){var f=entById(id);return f?{x:f.x,z:f.z}:null;},
entSet:function(id,x,z,face,moving){var f=entById(id);if(!f)return;f.x=x;f.z=z;if(f===P){P.y=gh(x,z);P.ta=face;P.a=face;if(moving){P.cmv=0.1;P.ph+=0.25;}}else{if(f.a!=null)f.a=face;else f.face=face;if(moving){f.walk=(f.walk||0)+0.25;f._mv=0.25;}}},
openDoor:function(a){var st=a.state||'open';if(!a.id)return;ents.dr.forEach(function(d){if(d.o.id!==a.id)return;var op=st==='toggle'?!d.open:st==='open';if(op===d.open)return;d.open=op;if(op){setGone(d.i);playSfx(SFX.door);S.shake=Math.max(S.shake,0.3);burst(d.x,gh(d.x,d.z)+1.5,d.z,S.mood.stone,30);}else if(G)delete G.gone[li+':'+d.i];});},
reload:function(){resetPlay(true);},interact:interact,attack:attack,ensureFoes:ensureFoes,
destroy:function(){cancelAnimationFrame(S.raf);input.dispose();window.removeEventListener('pointermove',onMove);if(mm&&mm.parentNode)mm.parentNode.removeChild(mm);R.destroy();}};
if(G){G.engine=api;G.li=li;}
S.raf=requestAnimationFrame(frame);return api;}
function create2D(canvas,L,opt,G){
opt=opt||{};var ctx=canvas.getContext('2d'),T=32,input=new Input(canvas),world=opt.world||L,li=opt.li||0;
var S={mode:opt.mode||'play',tool:'#',toolProps:{},mood:MOODS[L.mood]||MOODS['Sunken Ruins'],t:0,camX:0,camY:0,shake:0,hitStop:0,score:0,hp:3,won:false,shot:null,hudT:0,parts:[],bursts:[],proj:[],raf:0,paint:false,drone:false,hurtFx:0,flash:0};
var grid,en,P,W,H,spr=null,npcs=[],light=document.createElement('canvas'),lctx=light.getContext('2d');
function key(x,y){return li+':t'+x+'_'+y;}
function reset(keepPos){grid=L.tiles.map(function(r){return r.split('');});H=grid.length;W=grid[0].length;en=[];npcs=[];var px=P?P.x:64,py=P?P.y:64;P={x:64,y:64,vx:0,vy:0,w:22,h:28,on:false,inv:0,face:1,atk:0,dash:0,coyote:0};var hm=G?G.hpMul():1;
for(var y=0;y<H;y++)for(var x=0;x<W;x++){var c=grid[y][x];if(c==='P'){P.x=x*T+5;P.y=y*T+2;}
if(S.mode==='play'&&G&&G.gone[key(x,y)]&&'ohkDaeB'.indexOf(c)>=0){grid[y][x]='.';continue;}
if((c==='e'||c==='a'||c==='B')&&S.mode==='play'){var k=c==='a'?'archer':c==='B'?'boss':'grunt',D=ENEMY[k],hp=Math.round(D.hp*hm*(k==='boss'?0.6:1));en.push({k:key(x,y),kind:k,D:D,x:x*T+4,y:y*T+(k==='boss'?-T:8),vx:k==='boss'?80:60,w:k==='boss'?56:24,h:k==='boss'?56:24,hit:0,hp:hp,max:hp,sh:1.5,cd:0});grid[y][x]='.';}}
(L.objects||[]).forEach(function(o,i){if(o.t==='npc')npcs.push({o:o,id:o.id,x:(+o.x)*T+4,y:(+o.z)*T+2,col:hexCol(o.col,[0.85,0.6,0.3])});});
if(keepPos){P.x=px;P.y=py;}S.score=0;S.hp=3;S.won=false;S.proj=[];pearls();
if(G&&!G.allQuests.length)G.extraGoal='Reach the golden flag'+(S.total?' - pearls '+(S.total-left())+'/'+S.total:'');}
function left(){var n=0;grid.forEach(function(r){r.forEach(function(c){if(c==='o')n++;});});return n;}
function pearls(){S.total=S.mode==='edit'?left():(L.tiles||[]).join('').split('o').length-1;}
function buildSprite(){spr=null;if(!world.sprite||!world.sprite.length)return;var c=document.createElement('canvas');c.width=16;c.height=16;var g=c.getContext('2d');
world.sprite.forEach(function(col,i){if(col){g.fillStyle=col;g.fillRect(i%16,Math.floor(i/16),1,1);}});spr=c;}
reset();buildSprite();
function solid(px,py){var x=Math.floor(px/T),y=Math.floor(py/T);if(x<0||x>=W)return true;if(y<0||y>=H)return false;var c=grid[y][x];return c==='#'||c==='D';}
function tileAt(px,py){var x=Math.floor(px/T),y=Math.floor(py/T);if(x<0||x>=W||y<0||y>=H)return'.';return grid[y][x];}
function rgb(c,k,a){k=k||1;return'rgba('+Math.round(clamp(c[0]*k,0,1)*255)+','+Math.round(clamp(c[1]*k,0,1)*255)+','+Math.round(clamp(c[2]*k,0,1)*255)+','+(a==null?1:a)+')';}
function burst(x,y,col,cnt,sp){sp=sp||1;cnt=Math.min(cnt,Math.max(0,2000-S.bursts.length));for(var i=0;i<cnt;i++)S.bursts.push({x:x,y:y,vx:(Math.random()-0.5)*300*sp,vy:-Math.random()*300*sp,life:0.5+Math.random()*0.3,c:col,s:3+Math.random()*3});}
function hurt(d){if(P.inv>0||P.dash>0)return;P.inv=1;S.shake=0.6;S.hitStop=0.06;S.hurtFx=0.6;playSfx(sfxFor(world,'hit'));burst(P.x+11,P.y+14,[1,0.3,0.3],14);
if(G){G.hurt(d||1);}else{S.hp--;if(S.hp<=0)reset();}}
function kill(e){e.dead=true;if(G)G.gone[e.k]=1;S.score+=50;S.hitStop=0.09;S.shake=0.45;playSfx(sfxFor(world,'boom'));burst(e.x+e.w/2,e.y+e.h/2,[1,0.5,0.7],e.D.boss?90:30,e.D.boss?1.6:1);
if(G){G.addXp(e.D.xp);G.st.gold+=e.D.gold;G.event('defeat',{kind:e.kind});G.refresh();if(e.D.boss){G.bossBar(null);G.big('Victory!',e.D.name+' is defeated',3);}}}
function hitE(e,dmg){if(window.GSYS&&!GSYS.canHarm(e.o||e))return;e.hp-=dmg;e.hit=0.2;S.hitStop=0.06;S.shake=0.35;playSfx(sfxFor(world,'hit'));burst(e.x+e.w/2,e.y+e.h/2,[1,0.9,0.6],12);if(e.hp<=0)kill(e);}
function interact(){if(!G||G.busy())return;var best=null,bd=48;npcs.forEach(function(n){var d=Math.hypot(n.x-P.x,n.y-P.y);if(d<bd){bd=d;best=n;}});if(best){G.talk(best.o);return;}
var cx=Math.floor((P.x+P.w/2)/T),cy=Math.floor((P.y+P.h/2)/T);for(var dx=-1;dx<=1;dx++){var c=grid[cy]&&grid[cy][cx+dx];if(c==='D'){if(G.has('key')){G.take('key',1);grid[cy][cx+dx]='.';G.gone[key(cx+dx,cy)]=1;playSfx(SFX.door);G.toast('Door unlocked');burst((cx+dx)*T+16,cy*T+16,S.mood.stone,24);}else{G.toast('Locked. Find a key.');playSfx(SFX.hit);}return;}}}
function exitLevel(){if(!G){if(!S.won){S.won=true;S.score+=100;playSfx(sfxFor(world,'power'));}return;}
if(li+1<G.levels.length){if(S.porting)return;S.porting=true;playSfx(SFX.portal);S.flash=1;setTimeout(function(){opt.switchLevel(li+1);},350);}else if(!G.won){if(G.allQuests.length&&G.active().length){if(!S.warn){S.warn=1;G.toast('Finish your quests first!');}return;}G.win();S.won=true;}}
function move(dt){var k=input.keys,busy=G&&G.busy(),spd=230*(G?G.stats().speed:1);var dir=busy?0:(k.KeyD||k.ArrowRight?1:0)-(k.KeyA||k.ArrowLeft?1:0);
if(P.dash>0){P.dash-=dt;P.vx=P.face*620;}else P.vx=dir*spd;if(dir)P.face=dir;P.coyote-=dt;if(P.on)P.coyote=0.1;
if(!busy&&(k.Space||k.KeyW||k.ArrowUp)&&P.coyote>0){P.vy=-570;P.on=false;P.coyote=0;playSfx(sfxFor(world,'jump'));burst(P.x+11,P.y+P.h,scl3(S.mood.ground,1.5),6,0.3);}
var wet=tileAt(P.x+11,P.y+14)==='~';P.vy=Math.min(wet?160:900,P.vy+(wet?500:1500)*dt);if(wet&&!busy&&(k.Space||k.KeyW||k.ArrowUp))P.vy=-200;
var nx=P.x+P.vx*dt;if(P.vx>0&&(solid(nx+P.w,P.y+1)||solid(nx+P.w,P.y+P.h-1)))nx=Math.floor((nx+P.w)/T)*T-P.w-0.01;
if(P.vx<0&&(solid(nx,P.y+1)||solid(nx,P.y+P.h-1)))nx=Math.floor(nx/T+1)*T+0.01;P.x=nx;
var ny=P.y+P.vy*dt,was=P.on;P.on=false;
if(P.vy>0&&(solid(P.x+1,ny+P.h)||solid(P.x+P.w-1,ny+P.h))){ny=Math.floor((ny+P.h)/T)*T-P.h-0.01;if(!was&&P.vy>500){burst(P.x+11,ny+P.h,scl3(S.mood.ground,1.5),8,0.4);S.shake=0.15;}P.vy=0;P.on=true;}
if(P.vy<0&&(solid(P.x+1,ny)||solid(P.x+P.w-1,ny))){ny=Math.floor(ny/T+1)*T+0.01;P.vy=0;}P.y=ny;
if(P.y>H*T+64){hurt(1);if(G&&G.cp&&G.cp.li===li){P.x=G.cp.x;P.y=G.cp.z;}else{var sp=P;reset();P.inv=1;}P.vy=0;}
[[P.x+4,P.y+4],[P.x+P.w-4,P.y+P.h-4],[P.x+P.w/2,P.y+P.h/2]].forEach(function(q){var c=tileAt(q[0],q[1]),gx=Math.floor(q[0]/T),gy=Math.floor(q[1]/T);
if(c==='o'){grid[gy][gx]='.';if(G){G.gone[key(gx,gy)]=1;G.give('crystal',1,true);G.st.score+=10;if(G.extraGoal!=null)G.extraGoal='Reach the golden flag - pearls '+(S.total-left())+'/'+S.total;G.refresh();}S.score+=10;playSfx(sfxFor(world,'coin'));burst(gx*T+16,gy*T+16,S.mood.fxCol,16);}
if(c==='h'||c==='k'){grid[gy][gx]='.';if(G){G.gone[key(gx,gy)]=1;G.give(c==='h'?'potion':'key',1);}playSfx(sfxFor(world,'coin'));burst(gx*T+16,gy*T+16,c==='h'?[1,0.3,0.4]:[1,0.85,0.3],18);}
if(c==='C'&&G&&!(G.cp&&G.cp.li===li&&G.cp.x===gx*T+5)){G.checkpoint({x:gx*T+5,z:gy*T+2});burst(gx*T+16,gy*T,[1,0.9,0.5],30);}
if(c==='^')hurt(1);if(c==='G'||c==='X'){if(!S.won)burst(gx*T+16,gy*T+16,[1,0.9,0.3],40);exitLevel();}});
en.forEach(function(e){if(e.dead)return;e.hit=Math.max(0,e.hit-dt);e.cd=Math.max(0,e.cd-dt);if(busy)return;var nx2=e.x+e.vx*dt,ahead=e.vx>0?nx2+e.w:nx2;
if(e.D.boss){var dxp=P.x-e.x;e.vx=(dxp>0?1:-1)*Math.abs(e.vx);}
if(solid(ahead,e.y+e.h/2)||!solid(ahead,e.y+e.h+4))e.vx=-e.vx;else e.x=nx2;
if(e.kind==='archer'||e.D.boss){e.sh-=dt;var dpx=P.x-e.x,dpy=P.y-e.y,dd=Math.hypot(dpx,dpy);if(e.sh<=0&&dd<420){e.sh=e.D.boss?1.3:2.2;var n=e.D.boss?5:1;for(var i=0;i<n;i++){var a=Math.atan2(dpy,dpx)+(i-(n-1)/2)*0.25;S.proj.push({x:e.x+e.w/2,y:e.y+e.h/3,vx:Math.cos(a)*260,vy:Math.sin(a)*260,life:2.5,c:e.D.boss?[1,0.35,0.8]:[0.6,1,0.4]});}playSfx(SFX.laser);}}
if(P.x<e.x+e.w&&P.x+P.w>e.x&&P.y<e.y+e.h&&P.y+P.h>e.y){if(P.vy>0&&P.y+P.h-e.y<16){P.vy=-420;hitE(e,e.D.boss?3:99);}else if(e.cd<=0){e.cd=0.8;hurt(e.D.dmg);}}});
S.proj=S.proj.filter(function(p){p.life-=dt;p.x+=p.vx*dt;p.y+=p.vy*dt;if(p.life<=0||solid(p.x,p.y))return false;if(p.own){for(var i=0;i<en.length;i++){var e=en[i];if(!e.dead&&p.x>e.x&&p.x<e.x+e.w&&p.y>e.y&&p.y<e.y+e.h){hitE(e,p.dmg);return false;}}return true;}
if(p.x>P.x&&p.x<P.x+P.w&&p.y>P.y&&p.y<P.y+P.h){hurt(1);return false;}return true;});
var bs=null;en.forEach(function(e){if(!e.dead&&e.D.boss&&Math.abs(e.x-P.x)<600)bs=e;});if(G){if(bs)G.bossBar(bs.D.name,bs.hp/bs.max);else G.bossBar(null);}
if(P.inv>0)P.inv-=dt;if(P.atk>0)P.atk-=dt;
if(G){var nb=null;npcs.forEach(function(n){if(Math.hypot(n.x-P.x,n.y-P.y)<48)nb=n;});var cx=Math.floor((P.x+P.w/2)/T),cy=Math.floor((P.y+P.h/2)/T),door=grid[cy]&&(grid[cy][cx-1]==='D'||grid[cy][cx+1]==='D');
G.prompt(busy?null:nb?'<kbd>E</kbd>Talk to '+esc(nb.o.name||'stranger'):door?'<kbd>E</kbd>Unlock door':null);}}
function attack(){if(S.mode!=='play'||P.atk>0||(G&&G.busy()))return;P.atk=0.25;playSfx(sfxFor(world,'laser'));var hx=P.face>0?P.x+P.w:P.x-40,st=G?G.stats():{atk:1};
en.forEach(function(e){if(!e.dead&&hx<e.x+e.w&&hx+40>e.x&&P.y<e.y+e.h&&P.y+P.h>e.y)hitE(e,st.atk);});}
function bolt(){if(!G||!G.hasAbility('Star Bolt')||(G.cds['Star Bolt']||0)>0)return;G.cds['Star Bolt']=1.5;playSfx(SFX.magic);S.proj.push({x:P.x+11,y:P.y+12,vx:P.face*520,vy:0,life:1.2,c:[0.6,0.9,1],own:1,dmg:G.stats().atk+1});G.refresh();}
function dash(){if(!G||!G.hasAbility('Dash')||(G.cds.Dash||0)>0)return;G.cds.Dash=1.2;P.dash=0.16;P.inv=Math.max(P.inv,0.2);playSfx(SFX.jump);G.refresh();}
function update(dt){S.t+=dt;if(S.shake>0)S.shake=Math.max(0,S.shake-dt*1.6);if(S.hurtFx>0)S.hurtFx-=dt*1.5;if(S.flash>0)S.flash-=dt*3;var vw=canvas.width/(window.devicePixelRatio||1);
if(S.mode==='edit'){var k=input.keys;if(k.KeyD||k.ArrowRight)S.camX+=400*dt;if(k.KeyA||k.ArrowLeft)S.camX-=400*dt;S.camX=clamp(S.camX,0,Math.max(0,W*T-vw/S.sc));return;}
if(G)G.update(dt);if(S.hitStop>0){S.hitStop-=dt;return;}move(dt);var vws=vw/(S.sc||1);S.camX+=(clamp(P.x-vws/2,0,Math.max(0,W*T-vws))-S.camX)*Math.min(1,dt*8);}
function hills(vw,vh,par,base,amp,col,seedk){ctx.fillStyle=col;ctx.beginPath();ctx.moveTo(0,vh);for(var x=0;x<=vw+20;x+=20){var wx=x+S.camX*par;ctx.lineTo(x,base-amp*(Math.sin(wx*0.004+seedk)*0.6+Math.sin(wx*0.011+seedk*2)*0.3+Math.sin(wx*0.031)*0.1));}ctx.lineTo(vw,vh);ctx.fill();}
function draw(dt){var d=window.devicePixelRatio||1,vw=canvas.width/d,vh=canvas.height/d,m=S.mood;ctx.setTransform(d,0,0,d,0,0);
var g=ctx.createLinearGradient(0,0,0,vh);g.addColorStop(0,rgb(m.sky2));g.addColorStop(0.6,rgb(mix3(m.sky2,m.fog,0.6)));g.addColorStop(1,rgb(m.fog));ctx.fillStyle=g;ctx.fillRect(0,0,vw,vh);
if(m.night0>0.3)for(var st=0;st<80;st++){ctx.fillStyle='rgba(255,255,255,'+(0.3+0.3*Math.sin(S.t*2+st))+')';ctx.fillRect((st*137.5-S.camX*0.02)%vw+(st*137.5-S.camX*0.02<0?vw:0),(st*71)%(vh*0.5),2,2);}
var sg=ctx.createRadialGradient(vw*0.75,vh*0.22,0,vw*0.75,vh*0.22,vh*0.35);sg.addColorStop(0,rgb(m.sun,1,0.7));sg.addColorStop(1,rgb(m.sun,1,0));ctx.fillStyle=sg;ctx.fillRect(0,0,vw,vh);
hills(vw,vh,0.1,vh*0.55,70,rgb(mix3(m.fog,m.stone,0.35)),1);hills(vw,vh,0.25,vh*0.68,60,rgb(mix3(m.fog,m.stone,0.6),0.8),3);hills(vw,vh,0.45,vh*0.8,40,rgb(mix3(m.fog,m.leaf[0],0.6),0.6),5);
var sc=Math.min(1.6,Math.max(0.6,vh/(H*T)));S.sc=sc;ctx.save();ctx.scale(sc,sc);var vws=vw/sc;
var sx=S.shake>0?(Math.random()-0.5)*S.shake*14:0,sy=S.shake>0?(Math.random()-0.5)*S.shake*14:0;
var cam=Math.min(S.camX,Math.max(0,W*T-vws));ctx.translate(-cam+sx,sy+(vh/sc-H*T));
for(var y=0;y<H;y++)for(var x=0;x<W;x++){var c=grid[y][x],X=x*T,Y=y*T;if(X+T<cam||X>cam+vws)continue;
if(c==='#'){var top=y===0||(grid[y-1][x]!=='#'&&grid[y-1][x]!=='D');var tg=ctx.createLinearGradient(0,Y,0,Y+T);tg.addColorStop(0,rgb(m.ground,top?1.25:1.0));tg.addColorStop(1,rgb(m.ground,0.75));ctx.fillStyle=tg;ctx.fillRect(X,Y,T,T);
ctx.fillStyle=rgb(m.ground,0.6,0.5);ctx.fillRect(X+((x*7)%20),Y+((y*13)%22)+4,6,3);if(top){ctx.fillStyle=rgb(m.leaf[0],1.25);ctx.fillRect(X,Y,T,6);ctx.fillStyle=rgb(m.leaf[1],1.4);for(var gb=0;gb<4;gb++)ctx.fillRect(X+gb*8+2,Y-3-Math.abs(Math.sin(S.t*2+x+gb))*2,2,5);}}
else if(c==='D'){ctx.fillStyle='#5a3a1e';ctx.fillRect(X+2,Y,T-4,T);ctx.fillStyle='#ffd84a';ctx.fillRect(X+13,Y+12,6,8);}
else if(c==='o'){ctx.save();ctx.shadowColor=rgb(m.fxCol);ctx.shadowBlur=16;ctx.fillStyle=rgb(m.fxCol);var b=Math.sin(S.t*3+x)*3;ctx.beginPath();ctx.arc(X+16,Y+16+b,7,0,Math.PI*2);ctx.fill();ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(X+14,Y+14+b,2.5,0,7);ctx.fill();ctx.restore();}
else if(c==='h'||c==='k'){ctx.save();ctx.shadowColor=c==='h'?'#ff5577':'#ffd84a';ctx.shadowBlur=12;ctx.fillStyle=c==='h'?'#ff5577':'#ffd84a';var b2=Math.sin(S.t*3+x)*3;if(c==='h'){ctx.fillRect(X+11,Y+12+b2,10,12);ctx.fillRect(X+14,Y+8+b2,4,5);}else{ctx.beginPath();ctx.arc(X+12,Y+16+b2,5,0,7);ctx.fill();ctx.fillRect(X+15,Y+14+b2,12,4);}ctx.restore();}
else if(c==='^'){ctx.fillStyle='#d33';ctx.beginPath();ctx.moveTo(X,Y+T);ctx.lineTo(X+8,Y+12);ctx.lineTo(X+16,Y+T);ctx.lineTo(X+24,Y+12);ctx.lineTo(X+32,Y+T);ctx.fill();}
else if(c==='~'){ctx.fillStyle=rgb(m.water,1.2,0.6);ctx.fillRect(X,Y+(grid[y-1]&&grid[y-1][x]==='~'?0:6+Math.sin(S.t*3+x)*2),T,T);}
else if(c==='G'){ctx.fillStyle='#ffd84a';ctx.fillRect(X+12,Y-T,6,T*2);ctx.fillStyle=rgb(m.fxCol);ctx.fillRect(X+18,Y-T+Math.sin(S.t*4)*2,16,11);}
else if(c==='X'){ctx.save();ctx.translate(X+16,Y);ctx.rotate(S.t);ctx.strokeStyle='#9b8cff';ctx.lineWidth=4;ctx.shadowColor='#9b8cff';ctx.shadowBlur=18;ctx.beginPath();ctx.ellipse(0,0,13,22,0,0,7);ctx.stroke();ctx.restore();}
else if(c==='C'){var on=G&&G.cp&&G.cp.li===li&&G.cp.x===x*T+5;ctx.fillStyle=rgb(m.stone);ctx.fillRect(X+8,Y+12,16,20);ctx.save();ctx.shadowColor='#ffd166';ctx.shadowBlur=on?20:4;ctx.fillStyle=on?'#ffe9a8':'#889';ctx.beginPath();ctx.arc(X+16,Y+6+Math.sin(S.t*2)*2,6,0,7);ctx.fill();ctx.restore();}
else if(S.mode==='edit'&&(c==='e'||c==='P'||c==='a'||c==='B')){ctx.fillStyle=c==='P'?'#f2d79a':c==='B'?'#9b2c8e':c==='a'?'#4a8a3a':'#c3365a';ctx.fillRect(X+4,Y+6,24,24);ctx.fillStyle='#fff';ctx.font='bold 14px sans-serif';ctx.fillText(c,X+11,Y+23);}
if(S.mode==='edit'){ctx.strokeStyle='rgba(255,255,255,0.08)';ctx.strokeRect(X,Y,T,T);}}
npcs.forEach(function(n){var c=n.col,bob=Math.sin(S.t*2+n.x)*1.5;ctx.fillStyle=rgb(c);ctx.beginPath();ctx.moveTo(n.x-2,n.y+30);ctx.lineTo(n.x+12,n.y+4+bob);ctx.lineTo(n.x+26,n.y+30);ctx.fill();ctx.fillStyle='#f5cfa0';ctx.beginPath();ctx.arc(n.x+12,n.y+4+bob,7,0,7);ctx.fill();ctx.fillStyle=rgb(c,0.6);ctx.beginPath();ctx.moveTo(n.x+3,n.y+bob);ctx.lineTo(n.x+12,n.y-12+bob);ctx.lineTo(n.x+21,n.y+bob);ctx.fill();
if(G){var mk=null;G.allQuests.forEach(function(q){if(q.giver===n.id&&!G.q[q.id])mk='!';});G.curSteps().forEach(function(x){if(x.s.type==='talk'&&x.s.npc===n.id)mk='?';});if(mk){ctx.fillStyle='#ffd166';ctx.font='bold 20px sans-serif';ctx.fillText(mk,n.x+7,n.y-18+Math.sin(S.t*4)*3);}}});
en.forEach(function(e){if(e.dead)return;var fl=e.hit>0,c=fl?[1,1,1]:e.D.col,bob=Math.abs(Math.sin(S.t*8+e.x))*3;ctx.fillStyle=rgb(c);
if(e.D.boss){ctx.save();ctx.shadowColor='#c060ff';ctx.shadowBlur=25;ctx.beginPath();ctx.moveTo(e.x,e.y+e.h);ctx.lineTo(e.x+8,e.y+10+bob);ctx.lineTo(e.x+e.w/2,e.y-6+bob);ctx.lineTo(e.x+e.w-8,e.y+10+bob);ctx.lineTo(e.x+e.w,e.y+e.h);ctx.fill();ctx.restore();ctx.fillStyle='#ff4dd2';ctx.fillRect(e.x+14,e.y+18+bob,8,6);ctx.fillRect(e.x+34,e.y+18+bob,8,6);}
else if(e.kind==='archer'){ctx.beginPath();ctx.moveTo(e.x,e.y+e.h);ctx.lineTo(e.x+e.w/2,e.y-4+bob);ctx.lineTo(e.x+e.w,e.y+e.h);ctx.fill();ctx.fillStyle='#bfff80';ctx.fillRect(e.x+(e.vx>0?14:6),e.y+8+bob,5,4);}
else{ctx.beginPath();ctx.ellipse(e.x+e.w/2,e.y+e.h/2+4,e.w/2+2,e.h/2-bob/2,0,0,7);ctx.fill();ctx.fillStyle='#fff';ctx.fillRect(e.x+(e.vx>0?14:4),e.y+8,6,6);}});
S.proj.forEach(function(p){ctx.save();ctx.shadowColor=rgb(p.c);ctx.shadowBlur=12;ctx.fillStyle=rgb(p.c);ctx.beginPath();ctx.arc(p.x,p.y,5,0,7);ctx.fill();ctx.restore();});
if(S.mode==='play'&&!(P.inv>0&&Math.floor(S.t*20)%2===0)){if(spr){ctx.imageSmoothingEnabled=false;ctx.save();if(P.face<0){ctx.translate(P.x+P.w+5,P.y-4);ctx.scale(-1,1);ctx.drawImage(spr,0,0,32,32);}else ctx.drawImage(spr,P.x-5,P.y-4,32,32);ctx.restore();}
else{var cl=hexCol((world.player||{}).col,[0.18,0.55,0.62]);ctx.fillStyle=rgb(cl);ctx.fillRect(P.x,P.y+10,P.w,P.h-10);ctx.fillStyle='#f5cfa0';ctx.fillRect(P.x+3,P.y,P.w-6,12);ctx.fillStyle='#6b4423';ctx.fillRect(P.x+2,P.y-2,P.w-4,5);ctx.fillStyle='#222';ctx.fillRect(P.x+(P.face>0?14:4),P.y+4,4,4);
var run=Math.sin(S.t*18)*(Math.abs(P.vx)>1?4:0);ctx.fillStyle='#3a2a20';ctx.fillRect(P.x+3,P.y+P.h-4+Math.max(0,run)*0.5,7,4);ctx.fillRect(P.x+P.w-10,P.y+P.h-4+Math.max(0,-run)*0.5,7,4);}
if(P.atk>0){ctx.strokeStyle=rgb(m.fxCol);ctx.lineWidth=4;ctx.shadowColor=rgb(m.fxCol);ctx.shadowBlur=14;ctx.beginPath();ctx.arc(P.x+P.w/2,P.y+P.h/2,30,P.face>0?-1.2+(0.25-P.atk)*6:Math.PI+1.2-(0.25-P.atk)*6,P.face>0?-0.2+(0.25-P.atk)*6:Math.PI+0.2-(0.25-P.atk)*6);ctx.stroke();ctx.shadowBlur=0;}}
S.bursts=S.bursts.filter(function(b){b.life-=dt;if(S.hitStop<=0){b.vy+=900*dt;b.x+=b.vx*dt;b.y+=b.vy*dt;}ctx.fillStyle=rgb(b.c,1,clamp(b.life*2,0,1));ctx.fillRect(b.x,b.y,b.s,b.s);return b.life>0;});
ctx.restore();ctx.setTransform(d,0,0,d,0,0);
var dark=clamp(m.night0*0.75+0.12,0,0.8);if(S.mode==='play'&&dark>0.05){if(light.width!==canvas.width||light.height!==canvas.height){light.width=canvas.width;light.height=canvas.height;}
lctx.setTransform(d,0,0,d,0,0);lctx.globalCompositeOperation='source-over';lctx.clearRect(0,0,vw,vh);lctx.fillStyle='rgba(4,6,16,'+dark+')';lctx.fillRect(0,0,vw,vh);lctx.globalCompositeOperation='destination-out';
var oy=vh/sc-H*T;function hole(wx,wy,r,a){var X2=(wx-cam)*sc,Y2=(wy+oy)*sc;if(X2<-r||X2>vw+r)return;var gr=lctx.createRadialGradient(X2,Y2,0,X2,Y2,r*sc);gr.addColorStop(0,'rgba(0,0,0,'+a+')');gr.addColorStop(1,'rgba(0,0,0,0)');lctx.fillStyle=gr;lctx.fillRect(X2-r*sc,Y2-r*sc,r*2*sc,r*2*sc);}
hole(P.x+11,P.y+14,170,1);for(var yy=0;yy<H;yy++)for(var xx=0;xx<W;xx++){var cc=grid[yy][xx];if(cc==='o'||cc==='G'||cc==='X'||cc==='C'||cc==='h'||cc==='k')hole(xx*T+16,yy*T+16,cc==='o'?70:110,0.85);}
S.proj.forEach(function(p){hole(p.x,p.y,60,0.8);});ctx.drawImage(light,0,0,vw,vh);}
while(S.parts.length<140)S.parts.push({x:Math.random()*vw,y:Math.random()*vh,p:Math.random()*6,z:0.5+Math.random()});
ctx.fillStyle=rgb(m.fxCol);S.parts.forEach(function(p){p.p+=dt;var f=L.weather&&L.weather!=='auto'?L.weather:m.fx;if(f==='snow')p.y+=40*dt*p.z;else if(f==='rain'||f==='storm'){p.y+=520*dt*p.z;p.x+=60*dt;}else if(f==='embers'||f==='bubbles')p.y-=30*dt*p.z;else{p.x+=Math.sin(p.p)*20*dt;p.y+=Math.cos(p.p)*10*dt;}
if(p.y>vh)p.y=0;if(p.y<0)p.y=vh;if(p.x<0)p.x=vw;if(p.x>vw)p.x=0;ctx.globalAlpha=0.5+0.5*Math.sin(p.p*3);if(f==='rain'||f==='storm'){ctx.fillStyle='rgba(180,200,255,.6)';ctx.fillRect(p.x,p.y,1.5,12);}else ctx.fillRect(p.x,p.y,3*p.z,3*p.z);});ctx.globalAlpha=1;
var vg=ctx.createRadialGradient(vw/2,vh/2,vh*0.35,vw/2,vh/2,vh*0.95);vg.addColorStop(0,'rgba(0,0,0,0)');vg.addColorStop(1,'rgba(0,0,0,0.45)');ctx.fillStyle=vg;ctx.fillRect(0,0,vw,vh);
if(S.hurtFx>0){ctx.fillStyle='rgba(200,10,30,'+S.hurtFx*0.3+')';ctx.fillRect(0,0,vw,vh);}if(S.flash>0){ctx.fillStyle='rgba(255,255,255,'+S.flash*0.6+')';ctx.fillRect(0,0,vw,vh);}}
function frame(ts){S.raf=requestAnimationFrame(frame);var now=ts/1000,dt=Math.min(0.05,now-(S.last||now))*TSC();S.last=now;fit(canvas);update(dt);draw(dt);S.frames=(S.frames||0)+1;
if(S.shot){var cb=S.shot;S.shot=null;cb(canvas.toDataURL('image/png'));}
S.hudT-=dt;if(S.hudT<=0&&opt.onHud){S.hudT=0.2;opt.onHud({score:G?G.st.score:S.score,hp:G?Math.ceil(G.st.hp):S.hp,left:left(),total:S.total,won:G?G.won:S.won,mode:S.mode,frames:S.frames,level:li});}}
function cell(ev){var r=canvas.getBoundingClientRect(),vh=r.height,sc=Math.min(1.6,Math.max(0.6,vh/(H*T)));var x=Math.floor(((ev.clientX-r.left)/sc+S.camX)/T),y=Math.floor(((ev.clientY-r.top)/sc-(vh/sc-H*T))/T);return(x>=0&&x<W&&y>=0&&y<H)?[x,y]:null;}
function paint(ev){if(S.mode!=='edit')return;var c=cell(ev);if(!c)return;
if(S.tool==='npc'){if(!S.paintedNpc){S.paintedNpc=1;L.objects=L.objects||[];var n=1;while(L.objects.some(function(q){return q.id==='npc'+n;}))n++;var o={t:'npc',x:c[0],z:c[1],id:'npc'+n,name:'Villager '+n};var tp=S.toolProps||{};for(var k in tp)if(tp[k])o[k]=tp[k];L.objects.push(o);reset();if(opt.onSelect)opt.onSelect(L.objects.length-1);if(opt.onChange)opt.onChange(L);}return;}
if(S.tool==='select'){var bi=-1;(L.objects||[]).forEach(function(o,i){if(+o.x===c[0]&&+o.z===c[1])bi=i;});if(opt.onSelect)opt.onSelect(bi);return;}
if(S.tool==='erase'&&L.objects&&L.objects.length){var before=L.objects.length;L.objects=L.objects.filter(function(o){return!(+o.x===c[0]&&+o.z===c[1]);});if(L.objects.length!==before){reset();if(opt.onChange)opt.onChange(L);return;}}
var ch=S.tool==='erase'?'.':S.tool;if(ch==='P')grid.forEach(function(r){r.forEach(function(v,i){if(v==='P')r[i]='.';});});
if(grid[c[1]][c[0]]!==ch){grid[c[1]][c[0]]=ch;L.tiles=grid.map(function(r){return r.join('');});pearls();if(opt.onChange)opt.onChange(L);}}
function onDown(ev){S.paint=true;S.paintedNpc=0;if(S.mode==='play'){attack();return;}paint(ev);}function onMove(ev){if(S.paint&&S.tool!=='P'&&S.tool!=='npc'&&S.tool!=='select')paint(ev);}function onUp(){S.paint=false;}
canvas.addEventListener('pointerdown',onDown);canvas.addEventListener('pointermove',onMove);window.addEventListener('pointerup',onUp);
var api={kind:'2d',world:L,
setMood:function(name){if(MOODS[name]){L.mood=name;S.mood=MOODS[name];}},setTool:function(t,props){S.tool=t;S.toolProps=props||{};},
setMode:function(m){L.tiles=(S.mode==='edit')?grid.map(function(r){return r.join('');}):L.tiles;S.mode=m;reset();buildSprite();},rebuild:function(){reset();},
toggleDrone:function(){return false;},postcard:function(cb){S.shot=cb;},state:function(){return S;},refreshSprite:buildSprite,player:function(){return P;},ents:function(){return{en:en,npc:npcs};},
getPos:function(){return{x:P.x,z:P.y};},unit:T,step:function(dt){update(dt);},groundAt:function(){return 0;},shake:function(v){S.shake=Math.max(S.shake,+v||0.5);},
entPos:function(id){if(id==='player')return{x:P.x,z:P.y};var f=null;npcs.forEach(function(n){if(!f&&n.id===id)f=n;});return f?{x:f.x,z:f.y}:null;},
entSet:function(id,x,z,face){if(id==='player'){P.x=x;P.y=z;P.vy=0;return;}npcs.forEach(function(n){if(n.id===id){n.x=x;n.y=z;}});},
openDoor:function(a){var cx=Math.round(+a.x),cy=Math.round(+a.z);if(a.x==null||!grid[cy]||grid[cy][cx]!=='D')return;grid[cy][cx]='.';if(G)G.gone[key(cx,cy)]=1;playSfx(SFX.door);burst(cx*T+16,cy*T+16,S.mood.stone,24);},setPos:function(p){P.x=+p.x;P.y=+p.z;P.vy=0;},respawn:function(cp){if(cp){P.x=cp.x;P.y=cp.z;}else reset();P.inv=2;},reload:function(){reset(true);},interact:interact,attack:attack,
select:function(){},getView:function(){return{camX:S.camX};},setView:function(v){if(v&&v.camX!=null)S.camX=v.camX;},setQuality:function(q){setQ(q);},
destroy:function(){cancelAnimationFrame(S.raf);input.dispose();window.removeEventListener('pointerup',onUp);}};
input.onKey=function(c){if(S.mode==='play'&&G&&G.keys(c))return;if(c==='KeyP'&&opt.onPostcard)opt.onPostcard();if(S.mode!=='play')return;if(c==='KeyJ')attack();else if(c==='KeyE'||c==='Enter')interact();else if(c==='KeyF')bolt();else if(c==='ShiftLeft'||c==='ShiftRight')dash();};
if(G){G.engine=api;G.li=li;}
S.raf=requestAnimationFrame(frame);return api;}
function levelOf(world,i){return i<=0?world:(world.levels||[])[i-1]||world;}
function create(canvas,world,opt){opt=opt||{};var cur=null,G=null,mode=opt.mode||'play',li=clamp(opt.level|0,0,(world.levels||[]).length),cv=canvas,parent=canvas.parentNode,dead=false;
function swapCanvas(){var n=document.createElement('canvas');n.id=cv.id;n.className=cv.className;n.style.cssText=cv.style.cssText;if(cv.parentNode)cv.parentNode.replaceChild(n,cv);cv=n;return n;}
function mk(i,m,cb){if(cur){cur.destroy();cur=null;swapCanvas();}li=i;var L=levelOf(world,i);if(!L.objects)L.objects=[];
var o={mode:m,world:world,li:i,quality:opt.quality,onHud:opt.onHud,onChange:opt.onChange,onPostcard:opt.onPostcard,onSelect:opt.onSelect,onPoint:opt.onPoint,switchLevel:function(j,cb2){go(j,cb2);}};
try{cur=(L.mode==='2d'?create2D:create3D)(cv,L,o,m==='play'?G:null);}catch(e){if(opt.onHud)opt.onHud({error:'Could not start this level: '+e.message});throw e;}
if(cur&&m==='play'&&G){G.attach(cv.parentNode||parent);(L.quests||[]).forEach(function(q){if(q&&q.auto&&q.id&&!G.q[q.id])G.startQuest(q.id);});G.active().forEach(function(q){G.foes(q);});}api.kind=cur?cur.kind:null;if(opt.onLevel)opt.onLevel(i);if(cb)cb();}
function go(j,cb){if(dead||!G)return;var from=li;G.li=j;mk(j,'play',cb);var L=levelOf(world,j);G.big(L.name||('Level '+(j+1)),L.story||'',3.2);if(j!==from){G.save('auto',true);G.confetti(60);playSfx(SFX.levelup);}G.refresh();}
function startPlay(){if(G){G.destroy();G=null;}G=new Game(world,{switchLevel:go,onWin:opt.onWin,onToast:opt.onToast});G.li=li;mk(li,'play');var L=levelOf(world,li);
if(opt.title){G.titleScreen(function(){G.big(L.name||world.name,L.story||'',3.5);});}else G.big(L.name||world.name,(L.story||world.story||'').slice(0,160),3);}
var api={kind:null,world:world,
setMood:function(n){if(cur)cur.setMood(n);},setTool:function(t,p){if(cur)cur.setTool(t,p);},
setMode:function(m){mode=m;if(m==='play')startPlay();else{if(G){G.destroy();G=null;}mk(li,'edit');}},
setLevel:function(i){li=clamp(i|0,0,(world.levels||[]).length);if(mode==='play')startPlay();else mk(li,'edit');},level:function(){return li;},
toggleDrone:function(){return cur?cur.toggleDrone():false;},postcard:function(cb){if(cur)cur.postcard(cb);},state:function(){return cur?cur.state():{};},
refreshSprite:function(){if(cur&&cur.refreshSprite)cur.refreshSprite();},rebuild:function(){if(cur&&cur.rebuild)cur.rebuild();},select:function(i){if(cur&&cur.select)cur.select(i);},
getView:function(){return cur&&cur.getView?cur.getView():null;},setView:function(v){if(cur&&cur.setView)cur.setView(v);},setQuality:function(q){if(cur&&cur.setQuality)cur.setQuality(q);},
engine:function(){return cur;},game:function(){return G;},canvas:function(){return cv;},
destroy:function(){dead=true;if(cur)cur.destroy();if(G)G.destroy();cur=null;G=null;}};
if(mode==='play')startPlay();else mk(li,'edit');return api;}
return{R2:typeof R2!=='undefined'?R2:null,MOODS:MOODS,SFX:SFX,ITEMS:ITEMS,ENEMY:ENEMY,ABILITIES:ABILITIES,create:create,sfxSamples:sfxSamples,wavBytes:wavBytes,playSfx:playSfx,terrainH:terrainH,Game:Game,Logic:LGX,Plus:typeof PLX!=='undefined'?PLX:null,levelOf:levelOf,setQuality:setQ,getQuality:getQ,mute:function(v){MUTE=!!v;}};
})();
'''


ENGINE3D_DIR = os.path.join(APP_DIR, 'engine3d')
ASSETS_DIR = os.path.join(APP_DIR, 'assets')


def _engine3d_js():
    """Stark World Engine Render 2 (engine3d/*.js, spliced into ENGINE_JS): PBR + IBL, soft cascaded shadows, SSAO, bloom, TAA,
    splat terrain, glTF 2.0 models with skinning. Missing folder = the classic renderer still works."""
    parts = []
    for f in sorted(glob.glob(os.path.join(ENGINE3D_DIR, '*.js'))):
        if f.endswith('_editor.js'):                    # studio-only panels: never shipped inside a cartridge
            continue
        with open(f, encoding='utf-8') as fh:
            parts.append(fh.read())
    js = chr(10).join(parts)
    try:
        with open(os.path.join(ASSETS_DIR, 'looks.json'), encoding='utf-8') as fh:
            looks = json.load(fh)
    except (OSError, ValueError):
        looks = {}
    return js.replace('/*%%LOOKS%%*/', 'R2.LOOK=' + json.dumps(looks, separators=(',', ':')) + ';')


def load_looks():
    try:
        with open(os.path.join(ASSETS_DIR, 'looks.json'), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _stark_anim_js():
    """stark_anim/runtime/stark_anim.js: character animation runtime (state machines, IK, events, lip-sync, child
    safety). Missing file = engine3d's R2.Actor keeps working alone."""
    try:
        with open(os.path.join(APP_DIR, 'stark_anim', 'runtime', 'stark_anim.js'), encoding='utf-8') as fh:
            return fh.read()
    except OSError:
        return ''


ENGINE_JS = ENGINE_JS.replace('/*%%ENGINE3D%%*/', _engine3d_js() + chr(10) + _stark_anim_js())

# S12: the Free edition's closable note links back to the itch page; utm_campaign = this release (28.6.0 ...).
STORE_LINK_MADEWITH = ('https://ams513.itch.io/stark-world-engine?utm_source=madewith_note&utm_campaign=' + VERSION
                       + '&utm_medium=game&utm_content=s12')   # bare & is valid here: no 'utm' character reference
FREE_BADGE = ('<div id="gsbadge" style="position:fixed;right:12px;bottom:176px;z-index:20;padding:6px 10px;border-radius:9px;'
              'background:rgba(10,14,22,.62);color:#e9eef2;font:12px system-ui,sans-serif;pointer-events:none;'
              'border:1px solid rgba(255,255,255,.14)"><a href="' + STORE_LINK_MADEWITH + '" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;pointer-events:auto">Made with <b>Stark World Engine</b></a><button id="gsbx" type="button" '
              'aria-label="Hide this note" style="display:none;margin-left:8px;pointer-events:auto;background:none;border:0;'
              'color:#e9eef2;font:14px system-ui;cursor:pointer">&times;</button></div><script>setTimeout(function(){'
              'var b=document.getElementById("gsbx");if(b){b.style.display="inline";b.onclick=function(){'
              'var d=document.getElementById("gsbadge");if(d)d.remove();};}},4000);</script>')

CARTRIDGE_HTML = r'''
<!doctype html><html lang="en"><head><meta charset="utf-8"><title>%%TITLE%%</title><link rel="icon" href="data:,">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="generator" content="Stark World Engine">
<style>html,body{margin:0;height:100%;background:#000;color:#fff;font:14px system-ui,sans-serif;overflow:hidden}
canvas{position:fixed;inset:0;width:100vw;height:100vh;display:block;touch-action:none}#hud{position:fixed;left:12px;bottom:10px;font-size:12px;opacity:.7;text-shadow:0 1px 3px #000;z-index:6;pointer-events:none}
#bars{position:fixed;inset:0;pointer-events:none;box-shadow:inset 0 11vh 0 #000,inset 0 -11vh 0 #000;display:none;z-index:4}
#msg{position:fixed;left:0;right:0;top:40%;text-align:center;font-size:32px;display:none;text-shadow:0 2px 8px #000;z-index:7;pointer-events:none}</style></head>
<body><canvas id="c"></canvas><div id="hud"></div><div id="bars"></div><div id="msg"></div>
%%BADGE%%<script>window.GS_EMBED=%%ART%%;window.GS_CREDITS=%%CREDITS%%;</script>
<script>%%ENGINE%%</script>
<script>%%GSYS%%</script>
<script>
if(GS.R2)GS.R2.setSource({embedded:window.GS_EMBED});
var WORLD=%%WORLD%%;
var QS=new URLSearchParams(location.search);
var eng=GS.create(document.getElementById('c'),WORLD,{mode:'play',title:!QS.has('notitle'),quality:QS.get('q')||undefined,onHud:function(h){
var m=document.getElementById('msg');if(h.error){m.style.display='block';m.textContent=h.error;return;}
document.getElementById('hud').textContent=WORLD.name+'  |  Score '+h.score+'  |  HP '+h.hp+(h.total?'  |  '+(h.total-h.left)+'/'+h.total:'')+'  |  P: drone  O: postcard  Esc: menu';
document.getElementById('bars').style.display=h.drone?'block':'none';m.style.display='none';m.textContent=h.won?'World complete!':'';document.body.dataset.won=h.won?'1':'';document.body.dataset.frames=h.frames;}});
window.addEventListener('keydown',function(e){if(e.code==='KeyO'&&eng)eng.postcard(function(url){var a=document.createElement('a');a.href=url;a.download='postcard.png';a.click();});});
</script></body></html>
'''

APP_HTML = r'''
<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Stark World Engine</title>
<link rel="icon" href="%%FAVICON%%">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0e1412;--panel:#16201c;--panel2:#1c2924;--ink:#e7efe9;--mute:#9db2a8;--acc:#3dbb8c;--acc2:#d9a441;--line:#2a3a33;--bad:#ff6b81}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font:14px system-ui,Segoe UI,sans-serif}
header{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:7px 10px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#141c22,#0f1418)}
header b.logo{color:var(--acc);margin-right:6px;letter-spacing:.5px}
button,select,input,textarea{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:6px 10px;font:inherit}
button{cursor:pointer;transition:transform .08s,border-color .15s,background .15s}button:hover{border-color:var(--acc)}button:active{transform:scale(.95)}
button.on{background:var(--acc);color:#05231c;border-color:var(--acc)}button:disabled{opacity:.45;cursor:default}
button.big{background:linear-gradient(135deg,#ffd166,#ff8fa3);color:#2a1020;border:0;font-weight:700;padding:7px 14px;box-shadow:0 2px 12px rgba(255,140,160,.35)}
button.play{background:linear-gradient(135deg,#5fd3b5,#2a9d8f);color:#04201a;border:0;font-weight:700}
#moods{display:flex;flex-wrap:wrap;gap:6px;padding:6px 10px;border-bottom:1px solid var(--line)}#moods button{font-size:12px;padding:4px 9px}
body{display:flex;flex-direction:column}#wrap{display:grid;grid-template-columns:1fr 360px;flex:1 1 auto;min-height:0}
@media(min-width:901px){body{overflow:hidden}}
@media(max-width:1500px){header{gap:4px;padding:5px 6px}header button,header select,header input{padding:4px 7px;font-size:12px}#wname{width:110px}#status{max-width:200px}#moods{padding:4px 8px}#moods button{padding:3px 7px}#wrap{grid-template-columns:1fr 320px}}
#out{word-break:break-all;overflow-wrap:anywhere}#tour{max-height:calc(100% - 28px);overflow:auto}@media(max-width:900px){#wrap{grid-template-columns:1fr;grid-template-rows:60vh auto;height:auto}}
#stage{position:relative;min-height:300px;overflow:hidden}#stage canvas{position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none}
#hud{position:absolute;left:10px;bottom:8px;font-size:12px;opacity:.75;text-shadow:0 1px 3px #000;pointer-events:none;z-index:6}
#perfm{position:absolute;right:10px;top:8px;z-index:7;display:flex;align-items:center;gap:6px;padding:4px 8px;border-radius:9px;background:rgba(10,14,22,.66);border:1px solid var(--line);font-size:12px}
#perfm[hidden]{display:none}#perfm i{width:10px;height:10px;border-radius:50%;background:#888;display:inline-block}#perfm[data-level=green] i{background:#3dbb8c}#perfm[data-level=amber] i{background:#e9b949}#perfm[data-level=red] i{background:#ff5c6c}#perfm select{font-size:11px;padding:1px 2px}#bars{position:absolute;inset:0;pointer-events:none;box-shadow:inset 0 9vh 0 #000,inset 0 -9vh 0 #000;display:none;z-index:4}
aside{background:var(--panel);border-left:1px solid var(--line);overflow:auto;padding:10px}.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:10px}.tabs button{font-size:12px;padding:5px 8px}
.row{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0;align-items:center}.mute{color:var(--mute);font-size:12px}label{display:block;margin:6px 0 2px;color:var(--mute);font-size:12px}
.grp{font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.6px;margin:10px 0 2px}
#tools{display:flex;flex-wrap:wrap;gap:5px;row-gap:16px;padding-top:14px}#tools button{font-size:12px;padding:5px 8px;position:relative}#tools button[data-grp]{margin-left:8px}#tools button[data-grp]:first-child{margin-left:0}#tools button[data-grp]::before{content:attr(data-grp);position:absolute;left:0;top:-15px;font-size:10px;color:var(--mute);text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}
.card{border:1px solid var(--line);border-radius:10px;padding:9px;margin:8px 0;background:var(--panel2)}.card h4{margin:0 0 6px;color:var(--acc2)}
.card input,.card select,.card textarea{width:100%}.two{display:grid;grid-template-columns:1fr 1fr;gap:6px}.three{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px}
textarea{resize:vertical}.x{float:right;padding:2px 8px;font-size:12px;color:var(--bad)}
#px{image-rendering:pixelated;width:288px;height:288px;border:1px solid var(--line);background:repeating-conic-gradient(#222 0 25%,#2c2c2c 0 50%) 0 0/18px 18px;touch-action:none}
.sw{width:22px;height:22px;border-radius:4px;border:2px solid transparent;padding:0}.sw.on{border-color:#fff}#out{white-space:pre-wrap;font-size:12px;color:var(--mute)}
a{color:var(--acc)}#status{max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ov{position:fixed;inset:0;background:rgba(5,8,12,.72);display:flex;align-items:center;justify-content:center;z-index:50;animation:fade .2s}
.ov[hidden]{display:none}.dlgbox{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;width:min(760px,94vw);max-height:90vh;overflow:auto;animation:pop .25s cubic-bezier(.2,1.5,.4,1)}
@keyframes pop{from{transform:scale(.9);opacity:0}to{transform:none;opacity:1}}@keyframes fade{from{opacity:0}to{opacity:1}}
.themes{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}.theme{border-radius:12px;padding:14px 12px;min-height:92px;text-align:left;border:2px solid transparent;color:#fff;font-weight:700;text-shadow:0 1px 4px rgba(0,0,0,.6)}
.theme small{display:block;font-weight:400;opacity:.9;margin-top:4px}.theme.on{border-color:#fff;transform:scale(1.03)}
.sizes{display:flex;gap:8px;flex-wrap:wrap}.sizes button{flex:1;min-width:130px;text-align:left}.sizes button small{display:block;color:var(--mute)}.sizes button.on small{color:#063}
#tour{position:absolute;left:14px;bottom:14px;z-index:30;width:min(360px,80%);background:var(--panel);border:1px solid var(--acc);border-radius:12px;padding:12px 14px;box-shadow:0 8px 30px rgba(0,0,0,.5);animation:pop .3s}
#tour b{color:var(--acc2)}#tour .dots{float:right;color:var(--mute)}
.kb{display:grid;grid-template-columns:auto 1fr;gap:4px 14px}.kb kbd{background:#fff;color:#111;border-radius:4px;padding:1px 6px;font:600 12px monospace;justify-self:start}
.empty{color:var(--mute);font-size:13px;padding:10px;border:1px dashed var(--line);border-radius:10px;text-align:center}
</style></head><body>
<header><b class="logo" title="Stark World Engine - a Stark product"><img src="%%LOCKUP%%" alt="Stark World Engine" style="height:26px;vertical-align:middle" onerror="this.replaceWith('Stark World Engine')"></b><button id="magic" class="big" data-t="magic" title="Pick a theme and size - a whole adventure is built for you and starts right away">Make me a game</button>
<select id="worlds" title="Your games"></select><select id="level" title="Which level of this game you are editing"></select>
<button id="new3d" data-t="new3d" title="Start an empty 3D world"></button><button id="new2d" data-t="new2d" title="Start an empty 2D platformer"></button><input id="wname" size="14" placeholder="Name" title="Your game's name">
<button id="save" data-t="save" title="Save (Ctrl+S). Changes also autosave."></button><button id="undo" data-t="undo" title="Undo (Ctrl+Z)">Undo</button><button id="redo" data-t="redo" title="Redo (Ctrl+Y)">Redo</button>
<button id="mBuild" data-t="build" title="Edit the world (Ctrl+Enter toggles)"></button><button id="mPlay" class="play" data-t="play" title="Playtest right now (Ctrl+Enter)"></button>
<select id="lang" title="Language"></select><button id="keysBtn" title="Keyboard shortcuts (?)">?</button><span id="status" class="mute"></span></header>
<div id="moods"></div>
<div id="wrap"><div id="stage"><canvas id="view"></canvas><div id="hud"></div><div id="bars"></div><div id="perfm" hidden title="Measured on this computer: the average time to draw one frame over the last second, with the Graphics preset shown. Green: smooth (45+ fps). Amber: playable (25-45 fps). Red: slow - remove some objects or pick a lower preset."><i></i><span id="perfTxt"></span><select id="perfQ" title="Graphics preset (auto follows the hardware check)"><option value="auto">auto</option><option value="high">high</option><option value="medium">medium</option><option value="low">low</option></select></div></div>
<aside><div class="tabs"><button data-tab="tBuild" data-t="tools"></button><button data-tab="tWorld" data-t="worldTab">World</button><button data-tab="tQuest" data-t="questsTab">Quests</button><button data-tab="tDlg" data-t="dialogTab">Dialogue</button><button data-tab="tItems" data-t="itemsTab">Items</button><button data-tab="tLogic" title="When / then triggers, cutscenes, achievements">Logic</button><button data-tab="tPixel" data-t="pixel"></button><button data-tab="tSfx" data-t="sfx"></button><button data-tab="tTune" title="Tune Maker: music that plays inside your exported game">Tune</button><button data-tab="tAssets">Assets</button><button data-tab="tClass" title="Guided tutorials and classroom lessons">Learn</button><button data-tab="tExport" data-t="export"></button></div>
<section id="tBuild"><div id="tools"></div><div class="row" id="toolOpts"></div><div class="row"><button id="drone" data-t="drone" title="Cinematic fly-around camera"></button><button id="shot" data-t="postcard" title="Save a screenshot into your game folder"></button></div>
<div id="insp"></div><p class="mute" id="help"></p><p class="mute" id="story"></p></section>
<section id="tAssets" hidden></section><section id="tWorld" hidden></section><section id="tQuest" hidden></section><section id="tDlg" hidden></section><section id="tItems" hidden></section><section id="tLogic" hidden></section><section id="tTune" hidden></section>
<section id="tPixel" hidden><canvas id="px" width="16" height="16"></canvas><div class="row" id="pal"></div>
<div class="row"><button id="pxClear">Clear</button><button id="pxUse" data-t="useSprite"></button><button id="pxSave" data-t="savePng"></button></div><p class="mute">Draw your 2D hero. "Use as player sprite" makes it the hero of 2D levels.</p></section>
<section id="tSfx" hidden><label>Preset</label><select id="sfxPreset"></select><label>Wave</label><select id="sfxWave"><option>square</option><option>saw</option><option>sine</option><option>noise</option></select>
<div id="sliders"></div><div class="row"><button id="sfxPlay" data-t="playSfx"></button><button id="sfxSave" data-t="saveSfx"></button></div><p class="mute">The preset you tune becomes this world's in-game sound for that action.</p></section>
<section id="tClass" hidden><label for="clPack">Lesson</label><select id="clPack" style="width:100%"></select><div id="clInfo"></div>
<div class="row"><button id="clStart" class="big">Start this lesson</button><button id="clCheck" title="Looks for things that stop the level being finished. It never changes your game.">Check my level</button></div><div id="clOut" class="mute"></div>
<h4>Reflection and hand-in</h4><label for="clName">Your name (optional, stays in the file)</label><input id="clName" maxlength="80" style="width:100%"><div id="clQ"></div>
<div class="row"><button id="clExport" title="Saves your game as one HTML file plus a short reflection text file in My_Games">Export my game + reflection</button></div><div id="clDone"></div>
<p class="mute">Everything here stays on this computer. Nothing is uploaded.</p></section>
<section id="tExport" hidden><div class="row"><button id="exCart" data-t="cartridge" title="One HTML file anyone can play offline - with a title screen and credits"></button><button id="exItch" title="A zip ready to upload to itch.io as an HTML game (index.html inside). Same edition rules as the HTML5 game.">itch.io zip</button></div><div class="row"><button id="exGodot" data-t="godot"></button><button id="exUe5" data-t="ue5"></button><button id="exUeOpen" title="Build the UE5 project and open it in Unreal Engine 5 (Pro and Studio)">Open in Unreal Engine 5 (preview)</button><button id="exGdOpen" title="Write the Godot 4 project and open it in Godot (Pro and Studio)">Open in Godot</button></div>
<p class="mute">The HTML5 cartridge plays everything (quests, dialogue, levels, saves) in any current browser, with the bundled art inside the file. Godot 4 / UE5 export is the bridge to a full engine: it carries all your data in world.json and builds the first level; see its README.</p>
<p class="mute" id="tierNote"></p><div class="card"><b>Activate</b> <span class="mute" id="licState"></span>
<textarea id="licKey" rows="3" style="width:100%;margin-top:6px;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px" placeholder="Paste your key (GS1-...) - offline, no account"></textarea><button id="licGo">Activate</button></div><p><a href="/GIFT_CERTIFICATE.html" target="_blank" data-t="gift"></a> &middot; <a href="/PERSONAL_LICENSE.txt" target="_blank" data-t="license"></a></p><div id="out"></div></section>
</aside></div>
<div class="ov" id="magicOv" hidden><div class="dlgbox"><h2 style="margin:0 0 4px">Make me a game</h2><p class="mute" style="margin:0 0 12px">Pick a theme and a size. You get villages, people with quests, monsters, treasure, bosses and levels - ready to play, ready to change.</p>
<div class="themes" id="themes"></div><h3 style="margin:14px 0 6px">How long?</h3><div class="sizes" id="sizes"></div>
<div class="row" style="margin-top:14px"><label style="margin:0">Name <input id="mkName" size="18" placeholder="(we pick one)" maxlength="60"></label><label style="margin:0">Same world again? code <input id="seed" size="8" title="Same seed + theme = same game. Leave empty for a surprise." placeholder="random"></label><label style="margin:0"><input type="checkbox" id="mk2d"> 2D platformer instead of 3D</label></div>
<div class="row" style="justify-content:flex-end"><button id="magicNo">Cancel</button><button id="magicGo" class="big">Make it and play!</button></div></div></div>
<div class="ov" id="keysOv" hidden><div class="dlgbox"><h2 style="margin-top:0">Keyboard shortcuts</h2><div class="two"><div><h4>Building</h4><div class="kb"><kbd>Click</kbd><span>place the selected thing</span><kbd>Drag</kbd><span>turn the camera (or move a selected thing)</span><kbd>Wheel</kbd><span>zoom</span><kbd>WASD</kbd><span>move the camera</span><kbd>Q / E</kbd><span>turn the camera</span><kbd>Ctrl+Z / Ctrl+Y</kbd><span>undo / redo</span><kbd>Ctrl+S</kbd><span>save</span><kbd>Ctrl+Enter</kbd><span>play / back to building</span><kbd>Delete</kbd><span>remove the selected thing</span><kbd>?</kbd><span>this card</span></div></div>
<div><h4>Playing</h4><div class="kb"><kbd>WASD</kbd><span>move</span><kbd>Space</kbd><span>jump</span><kbd>J / click</kbd><span>attack</span><kbd>E</kbd><span>talk, open, unlock</span><kbd>1-4</kbd><span>pick an answer</span><kbd>H</kbd><span>drink a potion</span><kbd>Shift</kbd><span>dash (level 2)</span><kbd>K</kbd><span>whirl (level 3)</span><kbd>F</kbd><span>star bolt (level 4)</span><kbd>L / I</kbd><span>quests / bag</span><kbd>Esc</kbd><span>menu: save, load, options</span><kbd>Z / C, drag</kbd><span>camera</span><kbd>P</kbd><span>drone camera</span></div></div></div>
<h4>Something not working?</h4><div class="row"><button id="quitBtn" title="Stops the local studio server (your work is saved). Start it again with the launcher.">Stop the studio</button><button id="doctorBtn" title="Checks Python, browser graphics, ports and folders and says how to fix problems">Fix my setup</button></div><div id="doctorOut"></div><h4>Updates</h4><div class="row"><label><input type="checkbox" id="updOn"> Check for updates (off until you tick it; nothing about you is sent)</label> <select id="updCh"><option value="stable">stable</option><option value="beta">preview</option></select> <button id="updBtn">Check now</button></div><div id="updOut" class="mute"></div><div class="row" style="justify-content:flex-end"><button id="keysNo">Got it</button></div></div></div>
<div class="ov" id="restoreOv" hidden><div class="dlgbox"><h2 style="margin-top:0">Restore an earlier version</h2><div id="restoreList"></div><div class="row" style="justify-content:flex-end"><button id="restoreNo">Close</button></div></div></div>
<script>%%ENGINE%%</script>
<script>%%GSYS%%</script>
<script>
window.addEventListener('error',function(e){document.body.dataset.err=String(e.message).slice(0,200);});
var I18N=%%I18N%%,LANG='en',INFO=null,W=null,ENG=null,DIRTY=false,LI=0,MODE='edit',SEL=-1,HIST=[],HI=-1,AUTO=null,PENDING_PICK=null,HOME=null;
try{LANG=localStorage.getItem('gs_lang')||(navigator.language||'en').slice(0,2);}catch(e){}if(!I18N[LANG])LANG='en';
function T(k){return(I18N[LANG]&&I18N[LANG][k])||I18N.en[k]||k;}
function $(id){return document.getElementById(id);}
function E(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function api(path,body){return fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||('Server said '+r.status));return j;});});}
function status(s,bad){var el=$('status');el.textContent=s;el.style.color=bad?'var(--bad)':'';el.title=s;}
function click(){GS.playSfx({wave:'sine',freq:900,slide:-0.3,dur:0.05,decay:2,vol:0.08,noise:0});}
function L(){return W?GS.levelOf(W,LI):null;}
function levels(){return W?[W].concat(W.levels||[]):[];}
var TOOLS3D=[['Nature',[['tree','Tree','A tree (blocks the way)'],['rock','Rock','A boulder'],['ruin','Ruin','Old ruined pillars'],['house','House','A cosy house for your village'],['lamp','Lamp','A glowing lamp - lights the night'],['crystal','Crystal','Collectible glowing crystal (counts as item "crystal")']]],
['Creatures',[['enemy:grunt','Slime','Chases and bumps you'],['enemy:archer','Archer','Keeps its distance and shoots'],['enemy:brute','Brute','Slow, tough, smashes the ground'],['enemy:wisp','Wisp','Fast floating spirit'],['enemy:boss','Boss','A big boss with a health bar and special attacks']]],
['Story',[['npc','Person','Someone to talk to - give them dialogue and quests'],['item','Item','Something to pick up (choose which below)'],['chest','Chest','A chest to open with E (choose what is inside)'],['door','Locked door','Needs a key to open'],['portal','Portal','Takes the player to the next level'],['checkpoint','Checkpoint','Heals and saves progress'],['spawn','Player start','Where the hero begins this level']]],
['Edit',[['select','Select / move','Click a thing to edit it, drag to move it'],['erase','Erase','Click a thing to remove it']]]];
var TOOLS2D=[['Tiles',[['#','Ground','Solid ground'],['o','Pearl','Collectible (counts as item "crystal")'],['^','Spike','Hurts on touch'],['~','Water','Swim through it'],['G','Flag','Goal: next level or victory'],['X','Exit','Swirling exit to the next level'],['P','Start','Where the hero begins']]],
['Creatures',[['e','Slime','Walks back and forth - stomp it'],['a','Archer','Shoots at you'],['B','Boss','Big boss: stomp it several times']]],
['Story',[['h','Potion','Heals'],['k','Key','Opens a locked door'],['D','Locked door','Needs a key'],['C','Checkpoint','Saves progress'],['npc','Person','Someone to talk to']]],
['Edit',[['select','Select','Click a person to edit them'],['erase','Erase','Clear a tile or person']]]];
function itemIds(){var o=Object.keys(GS.ITEMS);levels().forEach(function(l){Object.keys(l.items||{}).forEach(function(k){if(o.indexOf(k)<0)o.push(k);});});return o;}
function itemName(id){var it=GS.ITEMS[id];levels().forEach(function(l){if(l.items&&l.items[id])it=l.items[id];});return it?it.name:id;}
function npcs(){var out=[];levels().forEach(function(l,i){(l.objects||[]).forEach(function(o){if(o.t==='npc'&&o.id)out.push({id:o.id,name:o.name||o.id,li:i,o:o});});});return out;}
function quests(){var out=[];levels().forEach(function(l,i){(l.quests||[]).forEach(function(q){out.push({q:q,li:i});});});return out;}
function allDialogs(){var o={};levels().forEach(function(l){var d=l.dialogs||{};for(var k in d)o[k]=d[k];});return o;}
function opts(list,val,empty){return(empty!=null?'<option value="">'+E(empty)+'</option>':'')+list.map(function(x){var v=Array.isArray(x)?x[0]:x,t=Array.isArray(x)?x[1]:x;return'<option value="'+E(v)+'"'+(String(v)===String(val==null?'':val)?' selected':'')+'>'+E(t)+'</option>';}).join('');}
function applyLang(){document.querySelectorAll('[data-t]').forEach(function(el){el.textContent=T(el.getAttribute('data-t'));});$('mPlay').textContent='▶ '+T('play');var lv=L();$('help').textContent=lv&&lv.mode==='2d'?T('help2d'):T('help3d');buildTools();}
var TOOL='tree';
function buildTools(){var t=$('tools');t.innerHTML='';var lv=L();var list=lv&&lv.mode==='2d'?TOOLS2D:TOOLS3D,first=null;
list.forEach(function(g,gi){g[1].forEach(function(p,pi){var b=document.createElement('button');b.textContent=p[1];b.title=g[0]+': '+p[2];b.dataset.tool=p[0];if(pi===0)b.dataset.grp=g[0];b.onclick=function(){click();pickTool(p[0]);};t.appendChild(b);if(!first)first=p[0];});});
if(TOOL==='asset'&&!(lv&&lv.mode==='2d'))return;pickTool(lv&&lv.mode==='2d'?(TOOL.length===1||TOOL==='npc'||TOOL==='select'||TOOL==='erase'?TOOL:'#'):(TOOL.length>1?TOOL:'tree'));}
function pickTool(id){TOOL=id;document.querySelectorAll('#tools button').forEach(function(x){x.classList.toggle('on',x.dataset.tool===id);});var o=$('toolOpts');o.innerHTML='';var props={},t=id;
if(id.indexOf('enemy:')===0){t='enemy';props.kind=id.split(':')[1];}
if(id==='item'||id==='chest'){o.innerHTML='<label style="margin:0">'+(id==='chest'?'Inside:':'Which item:')+' <select id="toolItem">'+opts(itemIds().map(function(k){return[k,itemName(k)];}),id==='chest'?'potion':'potion')+'</select></label>'+(id==='chest'?' <label style="margin:0">Gold <input id="toolGold" size="4" value="10"></label>':'');}
if(id==='door')o.innerHTML='<span class="mute">Opens with an "Old Key" (put one in a chest). Change it in the editor after placing.</span>';
if(id==='portal')o.innerHTML='<span class="mute">Leads to the next level. Add levels in the World tab.</span>';
function upd(){var p={};for(var k in props)p[k]=props[k];if($('toolItem'))p.item=$('toolItem').value;if($('toolGold'))p.gold=+$('toolGold').value||0;if(t==='door')p.key='key';if(t==='portal')p.to='next';if(ENG)ENG.setTool(t,p);}
o.querySelectorAll('select,input').forEach(function(e){e.onchange=upd;});upd();}
var DOC_SHOWN=false;function hud(h){if(h.error){$('hud').textContent=h.error;if(/WebGL2/.test(h.error)&&!DOC_SHOWN){DOC_SHOWN=true;doctor(true);}return;}$('hud').textContent=T('score')+' '+h.score+'   '+T('hp')+' '+h.hp+(h.total?'   '+(h.total-h.left)+'/'+h.total:'')+(h.won?'   '+T('win'):'')+(h.quality?'   gfx '+h.quality:'');$('bars').style.display=h.drone?'block':'none';document.body.dataset.frames=h.frames;perfMeter(h.perf);}
function perfLevel(ms){return ms<=22.3?'green':ms<=40?'amber':'red';}
function perfMeter(p){var m=$('perfm');if(!m)return;if(!p||MODE!=='edit'||!(p.ms>0)){m.hidden=true;return;}m.hidden=false;var lv=perfLevel(p.ms),fps=Math.round(1000/p.ms);
m.dataset.level=lv;m.dataset.ms=String(p.ms);m.dataset.q=p.q;$('perfTxt').textContent=p.q.charAt(0).toUpperCase()+p.q.slice(1)+': '+(p.ms<10?p.ms.toFixed(1):Math.round(p.ms))+' ms/frame ('+fps+' fps) '+(lv==='green'?'smooth':lv==='amber'?'playable':'slow');
var sel=$('perfQ');if(sel&&document.activeElement!==sel)sel.value=p.setting||getQuality();}
function getQuality(){try{return GS.getQuality();}catch(e){return'auto';}}
function changed(skipHist){DIRTY=true;status('* edited - autosave in a moment (Ctrl+S now)');if(!skipHist)pushHist();clearTimeout(AUTO);AUTO=setTimeout(function(){if(DIRTY&&W)save(true);},15000);}
function pushHist(){var s=JSON.stringify(W);if(HIST[HI]===s)return;HIST=HIST.slice(0,HI+1);HIST.push(s);if(HIST.length>100)HIST.shift();HI=HIST.length-1;updUndo();}
function updUndo(){$('undo').disabled=HI<=0;$('redo').disabled=HI>=HIST.length-1;}
function undo(d){var n=HI+d;if(n<0||n>=HIST.length)return;HI=n;var v=ENG&&ENG.getView?ENG.getView():null;W=JSON.parse(HIST[HI]);mount(W,MODE,LI,v,true);DIRTY=true;status(d<0?'Undone':'Redone');updUndo();click();}
function mount(world,mode,li,view,keepHist,home){if(ENG)ENG.destroy();if(!keepHist){HOME=home||null;if($('out'))$('out').textContent='';}var old=$('view'),c=document.createElement('canvas');c.id='view';old.parentNode.replaceChild(c,old);W=world;LI=Math.min(li|0,(W.levels||[]).length);MODE=mode||'edit';SEL=-1;
$('wname').value=W.name;$('story').textContent=W.story||'';if(!keepHist){HIST=[JSON.stringify(W)];HI=0;updUndo();}
ENG=GS.create(c,W,{mode:MODE,level:LI,quality:new URLSearchParams(location.search).get('q')||undefined,onHud:hud,onChange:function(){changed();if(SEL>=0)inspector();},onPostcard:postcard,onSelect:function(i){SEL=i;inspector();},onPoint:function(x,z){if(PENDING_PICK){PENDING_PICK(x,z);PENDING_PICK=null;pickTool('select');}},onWin:function(){status('Your game was beaten - great design!');}});
if(view)ENG.setView(view);music(MODE==='play');setModeUI();applyLang();markMood();fillLevels();renderPanels();}
function setModeUI(){$('mBuild').classList.toggle('on',MODE==='edit');$('mPlay').classList.toggle('on',MODE==='play');}
function setMode(m){if(!ENG)return;if(m===MODE){if(m==='play'){ENG.setMode('play');}return;}if(MODE==='edit')VIEW=ENG.getView();MODE=m;ENG.setMode(m);if(m==='edit'&&VIEW)ENG.setView(VIEW);music(m==='play');setModeUI();buildTools();if(m==='play'){blurAll();}}
var VIEW=null;function blurAll(){if(document.activeElement&&document.activeElement.blur)document.activeElement.blur();}
function markMood(){var lv=L();document.querySelectorAll('#moods button').forEach(function(b){b.classList.toggle('on',lv&&b.textContent===lv.mood);});}
function fillLevels(){var s=$('level');s.innerHTML=levels().map(function(l,i){return'<option value="'+i+'">'+(i+1)+'. '+E(l.name||(i?'Level '+(i+1):W.name))+' ('+l.mode+')</option>';}).join('');s.value=LI;s.style.display=levels().length>1?'':'none';}
function setLevel(i){LI=i;SEL=-1;if(ENG)ENG.setLevel(i);fillLevels();buildTools();markMood();renderPanels();applyLang();}
function loadWorld(slug){api('/api/world?name='+encodeURIComponent(slug)).then(function(w){mount(w,'edit',0,null,false,slug);DIRTY=false;status('Opened '+w.name);}).catch(function(e){status('Could not open that game: '+e.message,true);});}
function refreshList(sel){return api('/api/info').then(function(i){INFO=i;var s=$('worlds');s.innerHTML='';i.worlds.forEach(function(w){var o=document.createElement('option');o.value=w.slug;o.textContent=w.name+' ('+w.mode+(w.levels>1?', '+w.levels+' levels':'')+')';s.appendChild(o);});if(sel)s.value=sel;
var pro=i.tier==='pro'||i.tier==='studio';$('exGodot').disabled=!pro;$('exUe5').disabled=!pro;$('exUeOpen').disabled=!pro;$('exGdOpen').disabled=!pro;var ed=i.edition||'free',Ed=tierName(ed),Lc=i.license||{},act=' <a href="#" id="actLink">Have a key? Activate</a>';
var tip=pro?'':((ed==='pro'||ed==='studio')?Ed+' edition - paste your key below (Activate) to unlock this':'Part of the Pro and Studio editions');['exGodot','exUe5','exUeOpen','exGdOpen'].forEach(function(id){$(id).title=tip||$(id).title;});
$('tierNote').innerHTML=Lc.registered?'<b>'+E(tierName(i.tier))+' edition</b> - '+E(tierUnlocks(i.tier))+' - v'+E(i.version):((ed==='pro'||ed==='studio')?'<b>'+E(Ed)+' edition, not yet activated</b> - paste your key to unlock Godot/UE5 export.'+act:'<b>Stark World Engine Free</b> - '+E(tierUnlocks('free'))+'. A Pro or Studio key (or an older Standard key) exports every level with no note; Pro and Studio add Godot/UE5 export. v'+E(i.version)+act);
if(i.steam){var lk=$('licKey');if(lk&&lk.parentNode)lk.parentNode.style.display='none';document.querySelectorAll('a[href="/GIFT_CERTIFICATE.html"]').forEach(function(x){x.style.display='none';});$('tierNote').textContent=pro?'Pro Upgrade (Steam DLC) - Godot 4 and UE5 export':'Godot 4 and UE5 export come with the Stark World Engine Pro Upgrade DLC';}
if($('actLink'))$('actLink').onclick=function(e){e.preventDefault();showTab('tExport');$('licKey').focus();};
$('licState').textContent=Lc.registered?'registered to '+Lc.buyer+' ('+tierName(i.tier)+' edition) - '+tierUnlocks(i.tier):((ed==='pro'||ed==='studio')?Ed+' edition, not yet activated - paste your key':'Free edition - paste a key to unlock every level and remove the note');return i;});}
function tierName(t){return t==='standard'?'Standard':t==='pro'?'Pro':t==='studio'?'Studio':'Free';}
function tierUnlocks(t){return t==='pro'?'every level, no note, Godot 4 and UE5 project export':t==='studio'?'every level, no note, Godot 4 and UE5 project export, creator suite':t==='standard'?'every level, no note (Godot/UE5 export is part of Pro and Studio)':'HTML5 cartridges export the first level with a small closable "Made with Stark World Engine" note';}
function taken(n){var sl=slugJs(n);return((INFO&&INFO.worlds)||[]).some(function(x){return x.slug.toLowerCase()===sl.toLowerCase();});}
function freeName(n){var base=String(n||'My Game').replace(/ \d+$/,''),k=2,out=n;while(taken(out))out=base+' '+(k++);return out;}
function save(quiet){if(!W)return Promise.resolve();W.name=$('wname').value.trim()||W.name||'My Game';var renamed=null;
if((!HOME||slugJs(W.name).toLowerCase()!==HOME.toLowerCase())&&taken(W.name)){renamed=W.name;W.name=freeName(W.name);$('wname').value=W.name;}
var mine=W;return api('/api/world',{world:W}).then(function(r){var sl=r.saved.split('/')[0];if(mine===W){DIRTY=false;HOME=sl;}status((quiet?'Autosaved':T('saved'))+': '+r.saved+(renamed?' ("'+renamed+'" is another game, so this one is called "'+mine.name+'")':''));return refreshList(W===mine?sl:(HOME||sl));}).catch(function(e){status('Not saved: '+e.message+' - fix that and press Save again.',true);});}
function b64(bytes){var s='';for(var i=0;i<bytes.length;i+=8192)s+=String.fromCharCode.apply(null,bytes.subarray(i,i+8192));return btoa(s);}
function postcard(){if(!ENG)return;ENG.postcard(function(url){api('/api/asset',{world:W.name,kind:'png',folder:'postcards',name:'postcard_'+Date.now(),b64:url.split(',')[1]}).then(function(r){status('Postcard saved: '+r.saved);}).catch(function(e){status(e.message,true);});});}
function blank(mode){if(mode==='2d'){var rows=[];for(var y=0;y<15;y++)rows.push(y>=13?'#'.repeat(48):'.'.repeat(48));rows[12]='..P'+'.'.repeat(42)+'G..';return{name:'My 2D World',mode:'2d',mood:'Sunken Ruins',seed:3,version:2,tiles:rows,objects:[]};}
return{name:'My 3D World',mode:'3d',mood:'Alpine Sunset',seed:Math.floor(Math.random()*90)+1,version:2,objects:[]};}
function inspector(){var box=$('insp');var lv=L();var o=lv&&(lv.objects||[])[SEL];if(!o){box.innerHTML=SEL<0?'':'';return;}var h='<div class="card"><button class="x" id="iDel" title="Delete (Del)">Delete</button><h4>'+E(o.t==='npc'?(o.name||'Person'):o.t[0].toUpperCase()+o.t.slice(1))+'</h4>';
function f(k,label,type,list){var v=o[k]==null?'':o[k];if(list)return'<label>'+label+'<select data-k="'+k+'">'+list+'</select></label>';return'<label>'+label+'<input data-k="'+k+'" '+(type==='n'?'type="number" step="0.1"':'')+' value="'+E(v)+'"></label>';}
var qs=quests().map(function(x){return[x.q.id,x.q.title||x.q.id];});
if(o.t==='npc'){h+=f('name','Name')+f('id','Id (used by quests)')+f('dialog','Dialogue','',opts(Object.keys(allDialogs()),o.dialog,'(just a greeting)'))+f('text','Greeting if there is no dialogue')+f('col','Colour (#rrggbb)');h+='<div class="row"><button id="iDlg">Write dialogue for this person</button><button id="iQuest">Give them a quest</button></div>'+lgInsp(o);}
else if(o.t==='enemy'){h+=f('kind','Kind','',opts([['grunt','Slime'],['archer','Archer'],['brute','Brute'],['wisp','Wisp'],['boss','Boss']],o.kind||'grunt'))+'<div class="two">'+f('name','Name')+f('id','Id (for "defeat" quests)')+'</div><div class="two">'+f('hp','Health','n')+f('patrol','Wander radius','n')+'</div>'+lgInsp(o);}
else if(o.t==='item')h+=f('item','Item','',opts(itemIds().map(function(k){return[k,itemName(k)];}),o.item))+f('n','How many','n');
else if(o.t==='chest')h+=f('item','Inside','',opts(itemIds().map(function(k){return[k,itemName(k)];}),o.item,'(only gold)'))+f('gold','Gold','n');
else if(o.t==='door')h+=f('key','Opens with','',opts(itemIds().map(function(k){return[k,itemName(k)];}),o.key||'key'))+f('rot','Rotation','n')+lgInsp(o);
else if(o.t==='portal')h+=f('to','Goes to','',opts([['next','The next level']].concat(levels().map(function(l,i){return[String(i),'Level '+(i+1)+': '+(l.name||'')];})),o.to==null?'next':String(o.to)))+f('lock','Opens after quest','',opts(qs,o.lock,'(always open)'));
else if(o.t==='house')h+=f('rot','Rotation','n');
if(lv.mode==='3d')h+='<div class="two">'+f('s','Size','n')+'<label>Position<input disabled value="'+(+o.x).toFixed(1)+', '+(+o.z).toFixed(1)+'"></label></div>';
box.innerHTML=h+'</div>';
box.querySelectorAll('[data-k]').forEach(function(el){el.onchange=function(){var k=el.dataset.k,v=el.value;if(el.type==='number')v=v===''||!isFinite(+v)?undefined:+v;if(k==='id'&&v){v=idOf(v);el.value=v;}if(k==='to'&&v!=='next')v=+v;if(v===''||v===undefined)delete o[k];else o[k]=v;if(k==='id'&&o.t==='npc'){}if(ENG)ENG.rebuild();changed();renderPanels();if(k==='name')inspector();};});
lgInspBind(box,o);
$('iDel').onclick=function(){lv.objects.splice(SEL,1);SEL=-1;if(ENG){ENG.select(-1);ENG.rebuild();}box.innerHTML='';changed();};
if($('iDlg'))$('iDlg').onclick=function(){var did=o.dialog||o.id;lv.dialogs=lv.dialogs||{};if(!allDialogs()[did])lv.dialogs[did]={start:'hi',nodes:{hi:{speaker:o.name||'',text:'Hello, traveller! Lovely weather today.',choices:[{text:'Goodbye.',end:true}]}}};o.dialog=did;changed();showTab('tDlg');DLG=did;renderPanels();};
if($('iQuest'))$('iQuest').onclick=function(){addQuest('fetch',o);showTab('tQuest');};}
function renderWorld(){var lv=L(),s=$('tWorld');if(!W){s.innerHTML='';return;}var is3=lv.mode==='3d',day=lv.day||{};
var h='<div class="card"><h4>Game</h4><label>Story / description (shown on the title screen)</label><textarea id="wStory" rows="3">'+E(W.story||'')+'</textarea>'+
'<div class="two"><label>Difficulty<select id="wDiff">'+opts([['easy','Easy'],['normal','Normal'],['hard','Hard']],W.difficulty||'normal')+'</select></label><label>Made by<input id="wAuthor" value="'+E(W.author||'')+'"></label></div></div>'+'<div class="card"><h4>Soundtrack</h4><p class="mute">An original looping tune for this world&#39;s mood. It plays in Play mode here in the studio. Press Build game audio once and exported cartridges carry adaptive music, sound effects, ambience and your recorded voices.</p><div class="row"><button id="mkMusic">'+(W.music?'Make a new soundtrack':'Make a soundtrack')+'</button>'+(W.music?'<button id="noMusic">Remove</button><button id="tryMusic">Listen</button>':'')+'</div><div class="row"><button id="mkAudio" title="Adaptive music, sound effects, ambience and voices for the HTML5 cartridge">Build game audio</button><a id="voiceBooth" target="_blank" title="Record your own voice lines">Voice booth</a></div></div>';
h+='<div class="card"><h4>This level: '+E(lv.name||W.name)+'</h4>'+(LI?'<label>Level name<input id="lName" value="'+E(lv.name||'')+'"></label><label>Level intro text</label><textarea id="lStory" rows="2">'+E(lv.story||'')+'</textarea>':'')+
'<div class="two"><label>Weather<select id="lWeather">'+opts([['auto','Mood default'],['clear','Clear'],['rain','Rain'],['storm','Thunderstorm'],['snow','Snow'],['embers','Embers'],['pollen','Pollen'],['bubbles','Bubbles'],['fireflies','Fireflies']],lv.weather||'auto')+'</select></label>'+
(is3?'<label>Day and night<select id="lDay">'+opts([['0','Time stands still'],['600','Slow (10 min day)'],['300','Normal (5 min day)'],['90','Fast (90 s day)']],String(day.len||0))+'</select></label></div>'+
'<label>Time of day <span class="mute" id="lTimeV"></span><input type="range" id="lTime" min="0" max="1" step="0.01" value="'+(day.start!=null?day.start:0.3)+'" style="width:100%"></label>'+
'<label><input type="checkbox" id="lWaterOn" style="width:auto"'+(lv.water!=null?' checked':'')+'> Water (lakes and rivers)</label><input type="range" id="lWater" min="-2" max="1.5" step="0.05" value="'+(lv.water!=null?lv.water:-0.6)+'" style="width:100%">'+
'<label>Hills</label><input type="range" id="lHills" min="0" max="5" step="0.1" value="'+(lv.terrain!=null?lv.terrain:1)+'" style="width:100%"><label>World size</label><input type="range" id="lSize" min="24" max="128" step="4" value="'+(lv.size||48)+'" style="width:100%">':'</div>')+'</div>';
h+='<div class="card"><h4>Levels</h4><p class="mute">Portals (3D) and the flag or exit (2D) lead to the next level.</p>'+levels().map(function(l,i){return'<div class="row" style="margin:3px 0"><button data-go="'+i+'" class="'+(i===LI?'on':'')+'" style="flex:1;text-align:left">'+(i+1)+'. '+E(l.name||(i?'Level '+(i+1):W.name))+' <span class="mute">'+l.mode+'</span></button>'+(i?'<button data-up="'+i+'" title="Move earlier">Up</button><button data-del="'+i+'" title="Delete level" style="color:var(--bad)">X</button>':'')+'</div>';}).join('')+
'<div class="row"><button id="addL3">+ 3D level</button><button id="addL2">+ 2D level</button><button id="dupL">Duplicate this level</button></div></div>';
h+='<div class="card"><h4>Start from a template</h4><div class="row"><select id="tpl" style="flex:1">'+opts(((INFO&&INFO.templates)||[]).concat(['Firefly Fen','Summit Snack Run','Lantern Leap']))+'</select><button id="tplGo">Open</button></div><h4 style="margin-top:10px">Safety net</h4><div class="row"><button id="restoreBtn">Restore an earlier version...</button></div></div>';
s.innerHTML=h;
function on(id,fn){var e=$(id);if(e)e.onchange=e.oninput=fn;}
on('wStory',function(){W.story=this.value;$('story').textContent=W.story;changed();});on('wDiff',function(){W.difficulty=this.value;changed();});on('wAuthor',function(){W.author=this.value;changed();});
on('lName',function(){lv.name=this.value;fillLevels();changed();});on('lStory',function(){lv.story=this.value;changed();});
var re=null;function remount(){clearTimeout(re);re=setTimeout(function(){var v=ENG.getView();mount(W,MODE,LI,v,true);},250);}
on('lWeather',function(){if(this.value==='auto')delete lv.weather;else lv.weather=this.value;changed();remount();});
on('lDay',function(){lv.day=lv.day||{};lv.day.len=+this.value;changed();remount();});
var tv=function(){var v=+$('lTime').value,h2=Math.floor(v*24),m=Math.floor((v*24-h2)*60);if($('lTimeV'))$('lTimeV').textContent=(h2<10?'0':'')+h2+':'+(m<10?'0':'')+m;};if($('lTime')){tv();on('lTime',function(){lv.day=lv.day||{};lv.day.start=+this.value;tv();changed();remount();});}
on('lWaterOn',function(){if(this.checked)lv.water=+$('lWater').value;else delete lv.water;changed();remount();});on('lWater',function(){if($('lWaterOn').checked){lv.water=+this.value;changed();remount();}});
on('lHills',function(){lv.terrain=+this.value;changed();remount();});on('lSize',function(){lv.size=+this.value;changed();remount();});
s.querySelectorAll('[data-go]').forEach(function(b){b.onclick=function(){setLevel(+b.dataset.go);};});
s.querySelectorAll('[data-del]').forEach(function(b){b.onclick=function(){var i=+b.dataset.del;if(!confirm('Delete level '+(i+1)+'? (Undo can bring it back.)'))return;W.levels.splice(i-1,1);levels().forEach(function(l){(l.objects||[]).forEach(function(o){if(o.t==='portal'&&o.to!=null&&o.to!=='next'){if(+o.to===i)o.to='next';else if(+o.to>i)o.to=+o.to-1;}});});if(!W.levels.length)delete W.levels;changed();setLevel(Math.min(LI,(W.levels||[]).length));};});
s.querySelectorAll('[data-up]').forEach(function(b){b.onclick=function(){var i=+b.dataset.up;if(i<2)return;var a=W.levels;var t=a[i-1];a[i-1]=a[i-2];a[i-2]=t;changed();setLevel(i-1);};});
function addL(mode){W.levels=W.levels||[];var b=blank(mode);delete b.version;b.name=(mode==='2d'?'Platform Run ':'New Land ')+(W.levels.length+2);if(mode==='3d')b.objects.push({t:'portal',x:0,z:-20,s:1,to:'next'});W.levels.push(b);changed();setLevel(W.levels.length);status('Level added - you are now editing it.');}
$('addL3').onclick=function(){addL('3d');};$('addL2').onclick=function(){addL('2d');};
$('dupL').onclick=function(){W.levels=W.levels||[];var c=JSON.parse(JSON.stringify(lv));delete c.levels;delete c.version;delete c.author;delete c.difficulty;delete c.player;c.name=(lv.name||W.name)+' (copy)';(c.quests||[]).forEach(function(q){q.id=q.id+'_copy';});W.levels.splice(LI,0,c);changed();setLevel(LI+1);};
$('tplGo').onclick=function(){if(DIRTY&&!confirm('Open the template? Your unsaved changes stay in Undo.'))return;api('/api/template?name='+encodeURIComponent($('tpl').value)).then(function(w){w.name=w.name+' (my copy)';mount(w,'edit',0);changed();status('Template opened - it is yours to change. Save to keep it.');}).catch(function(e){status(e.message,true);});};
$('restoreBtn').onclick=openRestore;
if($('voiceBooth'))$('voiceBooth').href='/voice?world='+encodeURIComponent(slugJs(W.name));
if($('mkAudio'))$('mkAudio').onclick=function(){var b=this;b.disabled=true;status('Building game audio (the first time takes about 2 minutes)...');
save().then(function(){return api('/api/audio/build',{world:W.name});}).then(function(r){b.disabled=false;
status('Game audio ready: '+(r.report.html_bytes/1e6).toFixed(1)+' MB in the cartridge ('+r.report.states.join(', ')+')');})
.catch(function(e){b.disabled=false;status(e.message,true);});};
$('mkMusic').onclick=function(){var b=this;b.disabled=true;b.textContent='Composing... about 20 seconds';status('Composing your soundtrack... about 20 seconds. You can keep building meanwhile.');api('/api/music',{world:W.name,mood:L().mood||W.mood,seconds:40,seed:W.seed||1}).then(function(r){W.music=r.music;changed();renderWorld();status('Soundtrack ready - press Play to hear it.');}).catch(function(e){b.disabled=false;b.textContent='Make a soundtrack';status(e.message,true);});};
if($('noMusic'))$('noMusic').onclick=function(){delete W.music;music(false);changed();renderWorld();};if($('tryMusic'))$('tryMusic').onclick=function(){if(MUSIC)music(false);else music(true);};}
var MUSIC=null;function idOf(v){return String(v).trim().replace(/[^A-Za-z0-9_.-]+/g,'_').slice(0,64)||'id';}
function slugJs(n){var s=String(n||'').replace(/[^A-Za-z0-9_-]+/g,'_').replace(/^_+|_+$/g,'').slice(0,48);return s||'world';}
function music(on){if(MUSIC){try{MUSIC.pause();}catch(e){}MUSIC=null;}if(on&&W&&W.music){try{MUSIC=new Audio('/files/'+encodeURIComponent(slugJs(W.name))+'/'+W.music+'?v='+Date.now());MUSIC.loop=true;MUSIC.volume=0.45;var p=MUSIC.play();if(p&&p.catch)p.catch(function(){});}catch(e){MUSIC=null;}}}
function openRestore(){var nm=W.name;api('/api/backups?name='+encodeURIComponent(nm)).then(function(r){var l=$('restoreList');if(!r.backups.length){l.innerHTML='<div class="empty">No earlier versions yet. Every save keeps the 3 previous versions here.</div>';}
else l.innerHTML=r.backups.map(function(b){return'<div class="row"><span style="flex:1">'+E(b.when)+'</span><button data-b="'+E(b.id)+'">Restore this one</button></div>';}).join('')+'<p class="mute">Restoring opens that version in the editor. Press Save to make it current (today\'s version then becomes a backup too).</p>';
l.querySelectorAll('[data-b]').forEach(function(b){b.onclick=function(){api('/api/backup?name='+encodeURIComponent(nm)+'&id='+encodeURIComponent(b.dataset.b)).then(function(w){mount(w,'edit',0,null,false,HOME||slugJs(nm));changed();$('restoreOv').hidden=true;status('Earlier version opened - Save to keep it.');}).catch(function(e){status(e.message,true);});};});$('restoreOv').hidden=false;}).catch(function(e){status(e.message,true);});}
function stepFields(s,qi,si){var t=s.type,nl=npcs().map(function(n){return[n.id,n.name];}),il=itemIds().map(function(k){return[k,itemName(k)];});var h='<div class="card" style="background:var(--panel)"><button class="x" data-sdel="'+qi+':'+si+'">X</button><div class="two"><label>Step '+(si+1)+'<select data-s="'+qi+':'+si+':type">'+opts([['collect','Collect items'],['defeat','Defeat enemies'],['talk','Talk to someone'],['reach','Go to a place'],['escort','Escort someone']],t)+'</select></label>';
if(t==='collect')h+='<label>Item<select data-s="'+qi+':'+si+':item">'+opts(il,s.item)+'</select></label></div><div class="two"><label>How many<input type="number" data-s="'+qi+':'+si+':count" value="'+(s.count||1)+'"></label><label><input type="checkbox" style="width:auto" data-s="'+qi+':'+si+':consume"'+(s.consume?' checked':'')+'> hand them over</label></div>';
else if(t==='defeat')h+='<label>Which<select data-s="'+qi+':'+si+':kind">'+opts([['any','Any enemy'],['grunt','Slimes'],['archer','Archers'],['brute','Brutes'],['wisp','Wisps'],['boss','Bosses']],s.kind||'any')+'</select></label></div><div class="two"><label>How many<input type="number" data-s="'+qi+':'+si+':count" value="'+(s.count||1)+'"></label><label>Or one named enemy (id)<input data-s="'+qi+':'+si+':id" value="'+E(s.id||'')+'"></label></div>';
else if(t==='talk'||t==='escort')h+='<label>Person<select data-s="'+qi+':'+si+':npc">'+opts(nl,s.npc,'(choose)')+'</select></label></div>';else h+='<div></div></div>';
if(t==='reach'||t==='escort')h+='<div class="three"><label>X<input type="number" data-s="'+qi+':'+si+':x" value="'+(s.x||0)+'"></label><label>Z<input type="number" data-s="'+qi+':'+si+':z" value="'+(s.z||0)+'"></label><label>Radius<input type="number" data-s="'+qi+':'+si+':r" value="'+(s.r||4)+'"></label></div><div class="row"><button data-pick="'+qi+':'+si+'">Pick the spot on the map</button><input data-s="'+qi+':'+si+':label" placeholder="name of the place" value="'+E(s.label||'')+'" style="flex:1"></div>';
h+='<label>Custom objective text (optional)<input data-s="'+qi+':'+si+':text" value="'+E(s.text||'')+'"></label></div>';return h;}
var QTPL={fetch:{title:'A Small Favour',steps:[{type:'collect',item:'crystal',count:3},{type:'talk'}],reward:{xp:40,gold:20}},hunt:{title:'Monster Trouble',steps:[{type:'defeat',kind:'any',count:3}],reward:{xp:50,items:['potion']}},
talk:{title:'A Message',steps:[{type:'talk'}],reward:{xp:20}},go:{title:'Explorer',steps:[{type:'reach',x:10,z:10,r:4,label:'the old stones'}],reward:{xp:30}},escort:{title:'Safe Passage',steps:[{type:'talk'},{type:'escort',x:0,z:0,r:5,label:'home'}],reward:{xp:60,gold:25}}};
function addQuest(kind,npc){var lv=L();lv.quests=lv.quests||[];var q=JSON.parse(JSON.stringify(QTPL[kind]||QTPL.fetch)),n=1;var ids=quests().map(function(x){return x.q.id;});while(ids.indexOf('q'+n)>=0)n++;q.id='q'+n;
var sel=(lv.objects||[])[SEL],np=npc||(sel&&sel.t==='npc'&&sel.id?{id:sel.id,o:sel}:null);if(np){q.giver=np.id;q.steps.forEach(function(s){if((s.type==='talk'||s.type==='escort')&&!s.npc)s.npc=np.id;});}else q.auto=true;q.desc='';lv.quests.push(q);if(q.giver)makeGiverDialog(q);changed();renderPanels();
status(q.giver?'Quest added. A gold ! will float over '+(np.o&&np.o.name||np.id)+' in Play.':'Quest added - nobody gives it, so it starts by itself when the level begins. Pick a person in "Given by" to make it theirs.');}
function makeGiverDialog(q){var lv=L(),np=npcs().filter(function(n){return n.id===q.giver;})[0];if(!np)return;var o=np.o;var did=o.dialog||o.id;var D=allDialogs()[did];
if(!D){lv.dialogs=lv.dialogs||{};D=lv.dialogs[did]={start:'hi',branches:[],nodes:{hi:{speaker:o.name,text:'Hello, traveller!',choices:[{text:'Goodbye.',end:true}]}}};o.dialog=did;}
D.nodes=D.nodes||{};D.branches=D.branches||[];var k=q.id;D.nodes[k+'_offer']={speaker:o.name,text:'Could you help me? '+(q.title||'I have a task for you.'),choices:[{text:'I will help.',start:q.id,goto:k+'_yes'},{text:'Not now.',end:true}]};
D.nodes[k+'_yes']={speaker:o.name,text:'Thank you! Come back when it is done.'};D.nodes[k+'_wait']={speaker:o.name,text:'How is it going?'};D.nodes[k+'_done']={speaker:o.name,text:'You did it! Thank you so much.'};
D.branches=D.branches.filter(function(b){return b.node.indexOf(k+'_')!==0;});D.branches.push({if:{active:q.id},node:k+'_wait'});D.branches.push({if:{notStarted:q.id},node:k+'_offer'});D.branches.unshift({if:{done:q.id},node:k+'_done'});}
function renderQuests(){var s=$('tQuest'),lv=L();if(!W){s.innerHTML='';return;}var list=lv.quests||[];var nl=npcs().map(function(n){return[n.id,n.name];}),il=itemIds().map(function(k){return[k,itemName(k)];});
var h='<p class="mute">Quests on this level ('+E(lv.name||W.name)+'). People with a quest get a gold <b>!</b>; the player follows a gold beam to each objective.</p><div class="row"><span class="mute">Add:</span><button data-aq="fetch">Fetch</button><button data-aq="hunt">Hunt</button><button data-aq="talk">Talk</button><button data-aq="go">Explore</button><button data-aq="escort">Escort</button></div>';
if(!list.length)h+='<div class="empty">No quests here yet. Place a Person (Build tab), then click "Fetch" above - you get a quest and their dialogue in one go.</div>';
list.forEach(function(q,qi){var r=q.reward||{};h+='<div class="card"><button class="x" data-qdel="'+qi+'">Delete</button><h4>'+E(q.title||q.id)+'</h4><div class="two"><label>Title<input data-q="'+qi+':title" value="'+E(q.title||'')+'"></label><label>Given by<select data-q="'+qi+':giver">'+opts(nl,q.giver,'(nobody - starts automatically)')+'</select></label></div>'+
'<label>Description<input data-q="'+qi+':desc" value="'+E(q.desc||'')+'"></label><div class="row"><label style="margin:0"><input type="checkbox" style="width:auto" data-q="'+qi+':auto"'+(q.auto?' checked':'')+'> starts when the level begins</label><label style="margin:0"><input type="checkbox" style="width:auto" data-q="'+qi+':final"'+(q.final?' checked':'')+'> finishing it wins the game</label></div>'+
(q.steps||[]).map(function(st,si){return stepFields(st,qi,si);}).join('')+'<div class="row"><button data-sadd="'+qi+'">+ step</button>'+(q.giver?'<button data-gdlg="'+qi+'">Write the giver\'s lines for me</button>':'')+'</div>'+
'<div class="three"><label>Reward XP<input type="number" data-r="'+qi+':xp" value="'+(r.xp||0)+'"></label><label>Gold<input type="number" data-r="'+qi+':gold" value="'+(r.gold||0)+'"></label><label>Item<select data-r="'+qi+':item">'+opts(il,(r.items||[])[0],'(none)')+'</select></label></div><span class="mute">id: '+E(q.id)+'</span></div>';});
s.innerHTML=h;
s.querySelectorAll('[data-aq]').forEach(function(b){b.onclick=function(){addQuest(b.dataset.aq);};});
s.querySelectorAll('[data-qdel]').forEach(function(b){b.onclick=function(){list.splice(+b.dataset.qdel,1);changed();renderQuests();};});
s.querySelectorAll('[data-q]').forEach(function(el){el.onchange=function(){var a=el.dataset.q.split(':'),q=list[+a[0]];if(el.type==='checkbox')q[a[1]]=el.checked;else if(el.value==='')delete q[a[1]];else q[a[1]]=el.value;if(a[1]==='giver'&&!q.giver)q.auto=true;changed();if(a[1]==='giver'&&q.giver)makeGiverDialog(q);if(a[1]!=='desc')renderQuests();};});
s.querySelectorAll('[data-s]').forEach(function(el){el.onchange=function(){var a=el.dataset.s.split(':'),st=list[+a[0]].steps[+a[1]],k=a[2];var v=el.type==='checkbox'?el.checked:el.type==='number'?+el.value:el.value;if(k==='id'&&v){v=idOf(v);el.value=v;}if(v===''||v===false||(el.type==='number'&&!isFinite(v)))delete st[k];else st[k]=v;if(k==='type'){['item','kind','npc','x','z','r','label','id','consume'].forEach(function(z){delete st[z];});st.count=1;}changed();if(k==='type')renderQuests();};});
s.querySelectorAll('[data-sdel]').forEach(function(b){b.onclick=function(){var a=b.dataset.sdel.split(':');list[+a[0]].steps.splice(+a[1],1);changed();renderQuests();};});
s.querySelectorAll('[data-sadd]').forEach(function(b){b.onclick=function(){var q=list[+b.dataset.sadd];q.steps=q.steps||[];q.steps.push({type:'talk',npc:q.giver});changed();renderQuests();};});
s.querySelectorAll('[data-gdlg]').forEach(function(b){b.onclick=function(){makeGiverDialog(list[+b.dataset.gdlg]);changed();status('Dialogue written - see the Dialogue tab.');};});
s.querySelectorAll('[data-r]').forEach(function(el){el.onchange=function(){var a=el.dataset.r.split(':'),q=list[+a[0]];q.reward=q.reward||{};if(a[1]==='item'){if(el.value)q.reward.items=[el.value];else delete q.reward.items;}else q.reward[a[1]]=+el.value||0;changed();};});
s.querySelectorAll('[data-pick]').forEach(function(b){b.onclick=function(){var a=b.dataset.pick.split(':');if(MODE!=='edit')setMode('edit');if(L().mode!=='3d'){status('Spots can be picked on 3D levels.');return;}ENG.setTool('pickpoint');status('Click the ground to choose the spot...');PENDING_PICK=function(x,z){var st=list[+a[0]].steps[+a[1]];st.x=x;st.z=z;changed();renderQuests();status('Spot set: '+x+', '+z);};};});}
var DLG=null;
function renderDialogs(){var s=$('tDlg');if(!W){s.innerHTML='';return;}var all=allDialogs(),ids=Object.keys(all);if(!DLG||!all[DLG])DLG=ids[0]||null;
var h='<p class="mute">Conversations. Each line can offer up to 8 answers; answers can start quests, give items and jump to other lines.</p><div class="row"><select id="dSel" style="flex:1">'+opts(ids,DLG,ids.length?null:'(none yet)')+'</select><button id="dNew">New</button></div>';
if(!DLG){s.innerHTML=h+'<div class="empty">No dialogue yet. Select a Person in the Build tab and press "Write dialogue for this person".</div>';$('dNew').onclick=newDlg;return;}
var D=all[DLG],nids=Object.keys(D.nodes||{}),qs=quests().map(function(x){return[x.q.id,x.q.title||x.q.id];}),il=itemIds().map(function(k){return[k,itemName(k)];});
h+='<label>First line<select id="dStart">'+opts(nids,D.start)+'</select></label>';
h+='<div class="card"><h4>Say something different when...</h4>'+(D.branches||[]).map(function(b,i){var c=b.if||{},k=Object.keys(c)[0]||'done';return'<div class="row"><select data-bk="'+i+'">'+opts([['done','quest is done'],['active','quest is in progress'],['notStarted','quest not started'],['has','player has item']],k)+'</select><select data-bv="'+i+'">'+opts(k==='has'?il:qs,c[k])+'</select><span class="mute">start at</span><select data-bn="'+i+'">'+opts(nids,b.node)+'</select><button class="x" data-bdel="'+i+'">X</button></div>';}).join('')+'<button id="bAdd">+ rule</button></div>';
nids.forEach(function(nid){var n=D.nodes[nid];h+='<div class="card"><button class="x" data-ndel="'+E(nid)+'">X</button><h4>Line "'+E(nid)+'"</h4><div class="two"><label>Speaker<input data-n="'+E(nid)+':speaker" value="'+E(n.speaker||'')+'"></label><span></span></div><label>Text</label><textarea rows="2" data-n="'+E(nid)+':text">'+E(n.text||'')+'</textarea>';
(n.choices||[]).forEach(function(c,ci){var act=c.start?'start':c.complete?'complete':c.give?'give':c.take?'take':'';h+='<div class="card" style="background:var(--panel)"><button class="x" data-cdel="'+E(nid)+':'+ci+'">X</button><label>Answer '+(ci+1)+'<input data-c="'+E(nid)+':'+ci+':text" value="'+E(c.text||'')+'"></label><div class="three"><label>Then<select data-c="'+E(nid)+':'+ci+':goto">'+opts([['','end the talk']].concat(nids.map(function(x){return[x,'go to "'+x+'"'];})),c.goto||'')+'</select></label>'+
'<label>Also<select data-ca="'+E(nid)+':'+ci+'">'+opts([['','nothing'],['start','start quest'],['complete','finish quest'],['give','give item'],['take','take item']],act)+'</select></label><label>Which<select data-cv="'+E(nid)+':'+ci+'">'+(act?opts(act==='give'||act==='take'?il:qs,c[act]):'<option></option>')+'</select></label></div></div>';});
h+='<button data-cadd="'+E(nid)+'">+ answer</button></div>';});
h+='<div class="row"><button id="nAdd">+ line</button><button id="dDel" style="color:var(--bad)">Delete this dialogue</button></div>';s.innerHTML=h;
function home(){var r=null;levels().forEach(function(l){if(l.dialogs&&l.dialogs[DLG])r=l.dialogs;});return r;}
$('dSel').onchange=function(){DLG=this.value;renderDialogs();};$('dNew').onclick=newDlg;$('dStart').onchange=function(){D.start=this.value;changed();};
$('bAdd').onclick=function(){D.branches=D.branches||[];D.branches.push({if:{done:(qs[0]||[''])[0]},node:nids[0]});changed();renderDialogs();};
s.querySelectorAll('[data-bk]').forEach(function(el){el.onchange=function(){var b=D.branches[+el.dataset.bk];b.if={};b.if[el.value]='';changed();renderDialogs();};});
s.querySelectorAll('[data-bv]').forEach(function(el){el.onchange=function(){var b=D.branches[+el.dataset.bv];var k=Object.keys(b.if||{})[0]||'done';b.if={};b.if[k]=el.value;changed();};});
s.querySelectorAll('[data-bn]').forEach(function(el){el.onchange=function(){D.branches[+el.dataset.bn].node=el.value;changed();};});
s.querySelectorAll('[data-bdel]').forEach(function(el){el.onclick=function(){D.branches.splice(+el.dataset.bdel,1);changed();renderDialogs();};});
s.querySelectorAll('[data-n]').forEach(function(el){el.onchange=function(){var a=el.dataset.n.split(':');D.nodes[a[0]][a[1]]=el.value;changed();};});
s.querySelectorAll('[data-ndel]').forEach(function(el){el.onclick=function(){delete D.nodes[el.dataset.ndel];changed();renderDialogs();};});
s.querySelectorAll('[data-c]').forEach(function(el){el.onchange=function(){var a=el.dataset.c.split(':'),c=D.nodes[a[0]].choices[+a[1]];if(a[2]==='goto'){if(el.value){c.goto=el.value;delete c.end;}else{delete c.goto;c.end=true;}}else c[a[2]]=el.value;changed();};});
s.querySelectorAll('[data-ca]').forEach(function(el){el.onchange=function(){var a=el.dataset.ca.split(':'),c=D.nodes[a[0]].choices[+a[1]];['start','complete','give','take'].forEach(function(k){delete c[k];});if(el.value)c[el.value]=(el.value==='give'||el.value==='take'?(il[0]||[''])[0]:(qs[0]||[''])[0]);changed();renderDialogs();};});
s.querySelectorAll('[data-cv]').forEach(function(el){el.onchange=function(){var a=el.dataset.cv.split(':'),c=D.nodes[a[0]].choices[+a[1]];['start','complete','give','take'].forEach(function(k){if(c[k]!=null)c[k]=el.value;});changed();};});
s.querySelectorAll('[data-cdel]').forEach(function(el){el.onclick=function(){var a=el.dataset.cdel.split(':');D.nodes[a[0]].choices.splice(+a[1],1);changed();renderDialogs();};});
s.querySelectorAll('[data-cadd]').forEach(function(el){el.onclick=function(){var n=D.nodes[el.dataset.cadd];n.choices=n.choices||[];if(n.choices.length>=8)return;n.choices.push({text:'...',end:true});changed();renderDialogs();};});
$('nAdd').onclick=function(){var k=1;while(D.nodes['line'+k])k++;D.nodes['line'+k]={speaker:'',text:'',choices:[{text:'Goodbye.',end:true}]};changed();renderDialogs();};
$('dDel').onclick=function(){if(!confirm('Delete this dialogue?'))return;var h2=home();if(h2)delete h2[DLG];DLG=null;changed();renderDialogs();};}
function newDlg(){var lv=L();lv.dialogs=lv.dialogs||{};var k=1;while(allDialogs()['talk'+k])k++;DLG='talk'+k;lv.dialogs[DLG]={start:'hi',nodes:{hi:{speaker:'',text:'Hello there!',choices:[{text:'Goodbye.',end:true}]}}};changed();renderDialogs();}
function renderItems(){var s=$('tItems');if(!W){s.innerHTML='';return;}W.items=W.items||{};var its=W.items;
var h='<p class="mute">Make your own items: quest things to fetch, potions, keys, weapons and armour. Place them with the Item or Chest tool.</p><div class="row"><button id="iAdd">+ new item</button></div>';
Object.keys(its).forEach(function(id){var it=its[id];h+='<div class="card"><button class="x" data-idel="'+E(id)+'">X</button><h4>'+E(it.name||id)+'</h4><div class="two"><label>Name<input data-i="'+E(id)+':name" value="'+E(it.name||'')+'"></label><label>Kind<select data-i="'+E(id)+':kind">'+opts([['quest','Quest item'],['consumable','Potion / food'],['key','Key'],['equip','Equipment'],['treasure','Treasure (turns into gold)']],it.kind||'quest')+'</select></label></div>'+
(it.kind==='equip'?'<div class="two"><label>Slot<select data-i="'+E(id)+':slot">'+opts([['weapon','Weapon'],['armor','Armour'],['boots','Boots'],['charm','Charm']],it.slot||'weapon')+'</select></label><label>Attack bonus<input type="number" data-i="'+E(id)+':atk" value="'+(it.atk||0)+'"></label></div><div class="two"><label>Defence bonus<input type="number" data-i="'+E(id)+':def" value="'+(it.def||0)+'"></label><label>Max HP bonus<input type="number" data-i="'+E(id)+':hp" value="'+(it.hp||0)+'"></label></div>':'')+
(it.kind==='consumable'?'<label>Heals<input type="number" data-i="'+E(id)+':heal" value="'+(it.heal||1)+'"></label>':'')+(it.kind==='treasure'?'<label>Gold value<input type="number" data-i="'+E(id)+':gold" value="'+(it.gold||10)+'"></label>':'')+
'<div class="two"><label>Colour<input type="color" data-i="'+E(id)+':col" value="'+E(/^#[0-9a-f]{6}$/i.test(it.col||'')?it.col:'#ffd166')+'"></label><label>Description<input data-i="'+E(id)+':desc" value="'+E(it.desc||'')+'"></label></div><label>Shop value in gold (empty = automatic)<input type="number" min="0" data-i="'+E(id)+':value" value="'+E(it.value==null?'':it.value)+'"></label><span class="mute">id: '+E(id)+'</span></div>';});
h+='<details><summary class="mute">Built-in items</summary>'+Object.keys(GS.ITEMS).map(function(k){var it=GS.ITEMS[k];return'<div class="mute">'+E(it.name)+' ('+k+') - '+E(it.desc)+'</div>';}).join('')+'</details>';s.innerHTML=h;
$('iAdd').onclick=function(){var k=1;while(its['item'+k]||GS.ITEMS['item'+k])k++;its['item'+k]={name:'Shiny Thing '+k,kind:'quest',col:'#ffd166',desc:''};changed();renderItems();};
s.querySelectorAll('[data-idel]').forEach(function(b){b.onclick=function(){delete its[b.dataset.idel];changed();renderItems();};});
s.querySelectorAll('[data-i]').forEach(function(el){el.onchange=function(){var a=el.dataset.i.split(':'),it=its[a[0]];var v=el.type==='number'?+el.value:el.value;if(a[1]==='value'&&el.value==='')delete it.value;else it[a[1]]=v;if(a[1]==='kind'&&v==='equip'&&!it.slot)it.slot='weapon';changed();if(a[1]==='kind'||a[1]==='name')renderItems();};});}
%%LOGICUI%%
function renderPanels(){renderWorld();renderQuests();renderDialogs();renderItems();renderLogic();inspector();}
var CLP=[];
function clPack(){return CLP[+$('clPack').value]||null;}
function clShow(){var p=clPack();if(!p)return;$('clInfo').innerHTML='<p><b>Goal.</b> '+E(p.goal)+'</p><p class="mute">Concept: '+E(p.concept)+'. About '+p.minutes+' minutes (a planning guess, not a rule).</p><h4>Steps</h4><ol>'+p.steps.map(function(x){return'<li>'+E(x)+'</li>';}).join('')+'</ol><h4>Check your work</h4>'+p.check.map(function(c){return'<label style="display:block"><input type="checkbox"> '+E(c)+'</label>';}).join('');
$('clQ').innerHTML=p.reflect.map(function(q){return'<label>'+E(q)+'</label><textarea class="clA" rows="2" maxlength="1500" style="width:100%;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px"></textarea>';}).join('');}
function classInit(){if(!$('clPack'))return;api('/api/classroom/packs').then(function(r){CLP=r.packs;var s=$('clPack');s.innerHTML='';CLP.forEach(function(p,i){var o=document.createElement('option');o.value=i;o.textContent=p.title;s.appendChild(o);});clShow();}).catch(function(){});
$('clPack').onchange=clShow;
$('clStart').onclick=function(){var p=clPack();if(!p)return;(DIRTY?save(true):Promise.resolve()).then(function(){return api('/api/classroom/start',{id:p.id});}).then(function(r){return refreshList(r.slug).then(function(){loadWorld(r.slug);status('Lesson started: '+r.name);});}).catch(function(e){status(e.message,true);});};
$('clCheck').onclick=function(){if(!W){$('clOut').textContent='Open a game first.';return;}save().then(function(){return api('/api/classroom/check',{name:W.name});}).then(function(r){$('clOut').innerHTML=r.ok?'<b>No problems found.</b> Play it from the start to be sure.':'<b>Worth a look ('+r.issues.length+'):</b><ul>'+r.issues.map(function(i){return'<li>'+E(i.text)+'</li>';}).join('')+'</ul>';}).catch(function(e){$('clOut').textContent=e.message;});};
$('clExport').onclick=function(){if(!W){$('clDone').textContent='Open a game first.';return;}var p=clPack(),ans=[].map.call(document.querySelectorAll('.clA'),function(t){return t.value;});status('Exporting...');save().then(function(){return api('/api/classroom/export',{name:W.name,student:$('clName').value,answers:ans,pack:(p&&W.name.indexOf('Lesson - ')===0)?p.id:''});}).then(function(r){var first=r.written[0]||'',dir=first.split('/').slice(0,2).join('/');$('clDone').innerHTML='<b>Saved in My_Games/'+E(dir)+'</b><div class="row"><a href="/files/'+E(first)+'" target="_blank">Open game</a></div>'+(r.tier==='free'?'<p class="mute">The Free edition exports the first level only.</p>':'')+r.written.map(function(x){return'<div class="mute">'+E(x)+'</div>';}).join('');status('Hand-in files are ready.');}).catch(function(e){$('clDone').textContent=e.message;});};}
function showTab(id){if(id==='tAssets')renderAssets();['tBuild','tWorld','tQuest','tDlg','tItems','tLogic','tPixel','tSfx','tAssets','tClass','tExport'].forEach(function(x){$(x).hidden=x!==id;});document.querySelectorAll('[data-tab]').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-tab')===id);});}
var LIB=null,LIBCAT='tree';
function libLoad(){return api('/api/library').then(function(j){LIB=j;renderAssets();return j;}).catch(function(e){$('tAssets').innerHTML='<p class="mute">'+E(e.message)+'</p>';});}
function placeAsset(a){if(!W||!ENG)return;var lv=L();if(lv.mode!=='3d'){status('Assets go into 3D levels.',true);return;}
if(a.kind==='hdri'){lv.sky=a.file.replace(/^lib:/,'lib:');changed();if(ENG)ENG.rebuild();status('Sky set to '+a.name+' (World tab to undo).');return;}
var id=a.file.indexOf('lib:')===0?a.file:a.id;setMode('edit');showTab('tBuild');TOOL='asset';document.querySelectorAll('#tools button').forEach(function(x){x.classList.remove('on');});ENG.setTool('asset',{asset:id,s:a.scale||1});
$('toolOpts').innerHTML='<span class="mute">Placing <b>'+E(a.name)+'</b> - click the ground. ('+E(a.licence||'')+', '+E(a.credit||'')+')</span>';status('Click the ground to place '+a.name);}
function renderAssets(){var s=$('tAssets');if(!s)return;if(!LIB){s.innerHTML='<p class="mute">Loading the library...</p>';libLoad();return;}
var cats={tree:'Trees',bush:'Bushes',plant:'Plants',flower:'Flowers',grass:'Grass',rock:'Rocks',mushroom:'Mushrooms',wall:'Walls',roof:'Roofs',door:'Doors',window:'Windows',prop:'Props',stairs:'Stairs',hdri:'Skies (HDRI)',mine:'My imports'};
var h='<div class="card"><h4>Import your own art</h4><p class="mute">glTF 2.0 (.glb, or .gltf with its .bin and textures), PBR textures (.png/.jpg: albedo, normal, ORM/roughness are detected by name) or an .hdr sky. Bought packs and your own models are welcome - say where they come from, it goes into every game\'s credits.</p>'+
'<div id="dropZ" class="empty" style="cursor:pointer">Drop files here or click to choose</div><input type="file" id="impF" multiple hidden accept=".glb,.gltf,.bin,.png,.jpg,.jpeg,.webp,.hdr">'+
'<div class="two"><label>Name<input id="impN" placeholder="My statue"></label><label>Size in the world<input id="impS" type="number" step="0.1" value="1"></label></div>'+
'<label>Licence<select id="impL">'+opts(LIB.licences,'CC0 1.0')+'</select></label><div class="two"><label>Made by (credit)<input id="impC" placeholder="Artist name"></label><label>Source link<input id="impU" placeholder="Where you got it (web page)"></label></div>'+
'<div class="row"><button id="impGo" class="play">Import</button><span class="mute" id="impMsg"></span></div></div>';
h+='<div class="row">'+Object.keys(cats).map(function(k){return'<button class="'+(LIBCAT===k?'on':'')+'" data-cat="'+k+'">'+cats[k]+'</button>';}).join('')+'</div>';
var list=LIBCAT==='mine'?LIB.mine:LIBCAT==='hdri'?LIB.bundled.filter(function(a){return a.kind==='hdri';}).concat(LIB.mine.filter(function(a){return a.kind==='hdri';})):LIB.bundled.filter(function(a){return a.category===LIBCAT&&a.kind==='model';});
h+=list.length?list.map(function(a,i){return'<div class="it"><b style="flex:1">'+E(a.name)+'</b><span class="mute">'+E(a.licence||'')+' &middot; '+E(String(a.credit||'').split(' (')[0])+'</span><button data-place="'+i+'">'+(a.kind==='hdri'?'Use as sky':'Place')+'</button></div>';}).join(''):'<p class="empty">Nothing here yet.</p>';
h+='<p class="mute">Bundled art: free-to-use CC0 packs by Quaternius and Poly Haven (credits are added to your exports automatically). Fab / Megascans assets: swap them in after the Unreal export.</p>';
s.innerHTML=h;s.querySelectorAll('[data-cat]').forEach(function(b){b.onclick=function(){LIBCAT=b.dataset.cat;renderAssets();};});
s.querySelectorAll('[data-place]').forEach(function(b){b.onclick=function(){placeAsset(list[+b.dataset.place]);};});
var files=[];function pick(fl){files=Array.prototype.slice.call(fl);$('dropZ').textContent=files.map(function(f){return f.name;}).join(', ')||'Drop files here or click to choose';if(!$('impN').value&&files[0])$('impN').value=files[0].name.replace(/\.[^.]+$/,'').replace(/[_-]+/g,' ');}
$('dropZ').onclick=function(){$('impF').click();};$('impF').onchange=function(){pick(this.files);};
$('dropZ').ondragover=function(e){e.preventDefault();};$('dropZ').ondrop=function(e){e.preventDefault();pick(e.dataTransfer.files);};
$('impGo').onclick=function(){if(!files.length){$('impMsg').textContent='Choose files first.';return;}$('impMsg').textContent='Importing...';
Promise.all(files.map(function(f){return f.arrayBuffer().then(function(b){return{name:f.name,b64:b64(new Uint8Array(b))};});})).then(function(fs){
return api('/api/import',{name:$('impN').value,files:fs,licence:$('impL').value,credit:$('impC').value,source_url:$('impU').value,scale:+$('impS').value||1});})
.then(function(r){$('impMsg').textContent='Imported '+r.asset.name+'.';LIBCAT='mine';libLoad();}).catch(function(e){$('impMsg').textContent=e.message;});};}
var THEME_LOOK={'Spooky Swamp':['#1b2a49','#5fd3b5','Fireflies, wisps and a bog hag'],'Sky Islands':['#ff9a5a','#7aa8ff','Hills in the clouds and a storm bird'],'Frozen Kingdom':['#0b2440','#9fe8ff','Snow, auroras and a frost jarl'],
'Lava Fortress':['#3a0a0a','#ff6a3c','Embers, brutes and a cinder king'],'Sunken City':['#04323d','#38d0c0','Lakes, ruins and a tide colossus'],'Cozy Village':['#6a8f3a','#ffd166','Gentle quests, easy fights, pumpkins']};
var MK={theme:'Spooky Swamp',size:'snack'};
function openMagic(){var t=$('themes');t.innerHTML='';Object.keys(THEME_LOOK).forEach(function(k){var b=document.createElement('button');b.className='theme'+(MK.theme===k?' on':'');var c=THEME_LOOK[k];b.style.background='linear-gradient(135deg,'+c[0]+','+c[1]+')';b.innerHTML=E(k)+'<small>'+E(c[2])+'</small>';b.onclick=function(){click();MK.theme=k;openMagic();};t.appendChild(b);});
var z=$('sizes');z.innerHTML='';[['snack','Snack','1 level - about 15 minutes'],['evening','Evening','3 levels - about an hour'],['epic','Epic','6 levels - a few hours']].forEach(function(s){var b=document.createElement('button');b.className=MK.size===s[0]?'on':'';b.innerHTML='<b>'+s[1]+'</b><small>'+s[2]+'</small>';b.onclick=function(){click();MK.size=s[0];openMagic();};z.appendChild(b);});$('magicOv').hidden=false;}
function makeGame(){var seed=$('seed').value.trim();$('magicGo').disabled=true;status('Building your game...');
return api('/api/generate',{theme:MK.theme,size:MK.size,seed:seed?(+seed||seed.length*7919):null,mode:$('mk2d').checked?'2d':'3d'}).then(function(w){$('magicOv').hidden=true;$('magicGo').disabled=false;var nm=($('mkName').value||'').trim().replace(/[<>]/g,'').slice(0,60);if(nm)w.name=nm;var base=w.name,n=2,names=((INFO&&INFO.worlds)||[]).map(function(x){return x.name;});while(names.indexOf(w.name)>=0)w.name=base+' '+(n++);
mount(w,'edit',0);return save().then(function(){setMode('play');status('Your game is ready - have fun! (Ctrl+Enter or Build to change anything)');GS.playSfx(GS.SFX.levelup);});}).catch(function(e){$('magicGo').disabled=false;status('Could not make the game: '+e.message,true);});}
var TOUR=[['Welcome to Stark World Engine!','Build a game. Share it as one file. One install, endless ideas. In the next two minutes you will play a game you shaped. Step 1: pick a world, how long it should be, and give it a name.','<button id="tMagic" class="big">Make my game</button> <button id="tPlayNow">Just look around a starter game</button>'],
['You are playing your game!','WASD to move, Space to jump, J (or click) to attack, E to talk. Follow the gold beam to the next goal; L shows your quests. Esc pauses and saves.',''],
['Now make it yours','Press Build. Try a different mood at the top, pick House or Tree on the right and click the ground, then press Play again. Everything autosaves; Ctrl+Z undoes.','']];
function tour(i,cont){var old=$('tour');if(old)old.remove();var done=false;try{done=localStorage.getItem('gs_tour_done')==='1';}catch(e){}if((done&&!cont)||i>=TOUR.length){try{localStorage.setItem('gs_tour_done','1');}catch(e){}return;}
try{localStorage.setItem('gs_tour_done','1');}catch(e){}
var d=document.createElement('div');d.id='tour';d.innerHTML='<span class="dots">'+(i+1)+' / '+TOUR.length+'</span><b>'+TOUR[i][0]+'</b><p style="margin:6px 0">'+TOUR[i][1]+'</p><div class="row">'+TOUR[i][2]+'</div><div class="row" style="justify-content:flex-end"><button id="tSkip">Skip tour</button><button id="tNext">'+(i===TOUR.length-1?'Done':'Next')+'</button></div>';$('stage').appendChild(d);
$('tSkip').onclick=function(){d.remove();try{localStorage.setItem('gs_tour_done','1');}catch(e){}};$('tNext').onclick=function(){if(i===0&&MODE!=='play')setMode('play');if(i===1&&MODE==='play')setMode('edit');tour(i+1,true);};
if($('tPlayNow'))$('tPlayNow').onclick=function(){setMode('play');tour(1,true);};if($('tMagic'))$('tMagic').onclick=function(){openMagic();d.remove();TOUR_AFTER=1;};}
var TOUR_AFTER=0;
function doctor(webgl,fix){var o=$('doctorOut');$('keysOv').hidden=false;o.innerHTML='<p class="mute">'+(fix?'Fixing...':'Checking...')+'</p>';api('/api/doctor',{fix:!!fix}).then(function(r){var h='';if(webgl)h+='<div class="card"><h4>3D graphics are switched off in this browser</h4><p style="white-space:pre-wrap">'+E(r.webgl2_fix||'Turn on hardware acceleration in your browser settings, update your graphics driver, or try Chrome / Edge / Firefox. 2D worlds still work.')+'</p></div>';if(!r.available){h+='<p class="mute">The setup doctor is not included in this copy.</p>';}else if(!r.items.length){h+='<p>All '+r.checked+' checks passed - your setup looks good.</p>';}else {r.items.forEach(function(it){h+='<div class="card"><h4>'+E(it.title||it.id)+(it.auto_fixed?' (fixed for you)':'')+'</h4><p style="white-space:pre-wrap;margin:0">'+E(it.fix_text||'')+'</p></div>';});if(!r.fixed)h+='<div class="row"><button id="doctorFix" title="Repairs what it safely can (for example the bundled Python path file)">Fix it for me</button></div>';}o.innerHTML=h;if($('doctorFix'))$('doctorFix').onclick=function(){doctor(webgl,true);};}).catch(function(e){o.innerHTML='<p class="mute">Could not run the check: '+E(e.message)+'</p>';});}
var PAL=['#000000','#ffffff','#f2d79a','#e07a5f','#c3365a','#7b2d8e','#3d5a80','#5fd3b5','#2a9d8f','#8ab17d','#e9c46a','#f4a261','#6d4c41','#9e9e9e','#3a3a3a',null],PX=new Array(256).fill(null),PCOL='#f2d79a';
function drawPx(){var c=$('px'),g=c.getContext('2d');g.clearRect(0,0,16,16);PX.forEach(function(col,i){if(col){g.fillStyle=col;g.fillRect(i%16,Math.floor(i/16),1,1);}});}
function pxAt(ev){var r=$('px').getBoundingClientRect();var x=Math.floor((ev.clientX-r.left)/r.width*16),y=Math.floor((ev.clientY-r.top)/r.height*16);if(x>=0&&x<16&&y>=0&&y<16){PX[y*16+x]=PCOL;drawPx();}}
var SL=[['freq',40,2000,1],['slide',-2,3,0.05],['dur',0.05,1.2,0.01],['decay',0.3,4,0.1],['noise',0,1,0.05],['vol',0.05,1,0.05]],CUR=null;
function sfxParams(){var p={wave:$('sfxWave').value};SL.forEach(function(s){p[s[0]]=parseFloat($('sl_'+s[0]).value);});return p;}
function sfxLoad(k){CUR=k;var p=(W&&W.sfx&&W.sfx[k])||GS.SFX[k];$('sfxWave').value=p.wave;SL.forEach(function(s){$('sl_'+s[0]).value=p[s[0]]||0;$('lv_'+s[0]).textContent=p[s[0]]||0;});}
window.addEventListener('load',function(){
var ls=$('lang');Object.keys(I18N).forEach(function(l){var o=document.createElement('option');o.value=l;o.textContent=l.toUpperCase();ls.appendChild(o);});ls.value=LANG;ls.onchange=function(){LANG=ls.value;try{localStorage.setItem('gs_lang',LANG);}catch(e){}applyLang();};
Object.keys(GS.MOODS).forEach(function(m){var b=document.createElement('button');b.textContent=m;b.title='Change the look and atmosphere of this level';b.onclick=function(){if(ENG){click();ENG.setMood(m);changed();markMood();}};$('moods').appendChild(b);});
document.querySelectorAll('[data-tab]').forEach(function(b){b.onclick=function(){click();showTab(b.getAttribute('data-tab'));};});showTab('tBuild');classInit();
$('mBuild').onclick=function(){click();setMode('edit');};$('mPlay').onclick=function(){click();setMode('play');};$('save').onclick=function(){save();};
$('undo').onclick=function(){undo(-1);};$('redo').onclick=function(){undo(1);};$('level').onchange=function(){setLevel(+this.value);};
$('new3d').onclick=function(){mount(blank('3d'),'edit');changed();status('New 3D world: pick a tool on the right, then click the ground. Or press Make me a game!');};$('new2d').onclick=function(){mount(blank('2d'),'edit');changed();status('New 2D world - click tiles to paint.');};
$('worlds').onchange=function(){if(DIRTY)save(true);loadWorld($('worlds').value);};$('drone').onclick=function(){if(ENG)ENG.toggleDrone();};$('shot').onclick=postcard;$('perfQ').onchange=function(){if(ENG)ENG.setQuality(this.value);status('Graphics: '+this.value+' (the meter updates within a second)');};
$('magic').onclick=openMagic;$('magicNo').onclick=function(){$('magicOv').hidden=true;};$('magicGo').onclick=function(){makeGame().then(function(){if(TOUR_AFTER){TOUR_AFTER=0;tour(1,true);}});};
$('keysBtn').onclick=function(){$('keysOv').hidden=false;};$('quitBtn').onclick=function(){var go=function(){api('/api/quit',{}).then(function(r){document.body.innerHTML='<div style="padding:40px;font:18px system-ui;color:#e9eef2">'+E(r.message)+'</div>';}).catch(function(e){status(e.message,true);});};if(DIRTY)save(true).then(go);else go();};function updShow(s){if(!s||!$('updOn'))return;$('updOn').checked=!!s.enabled;$('updCh').value=s.channel||'stable';
$('updOut').textContent=(s.detail?s.detail+' ':'')+'(installed '+s.version+', '+s.edition+(s.pending_restart?', restart to finish':'')+')';}
function upd(a,x){fetch('/api/updates',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify(Object.assign({action:a},x||{}))}).then(function(r){return r.json();}).then(updShow).catch(function(){});}
if($('updOn')){fetch('/api/updates').then(function(r){return r.json();}).then(updShow).catch(function(){});
$('updOn').onchange=function(){upd(this.checked?'enable':'disable');};
$('updCh').onchange=function(){upd('channel',{channel:this.value});};
$('updBtn').onclick=function(){$('updOut').textContent='Checking...';upd('check');};}
$('doctorBtn').onclick=function(){doctor(false);};$('keysNo').onclick=function(){$('keysOv').hidden=true;};$('restoreNo').onclick=function(){$('restoreOv').hidden=true;};
$('wname').onchange=function(){if(W){W.name=this.value.trim()||W.name;changed();}};
PAL.forEach(function(c){var b=document.createElement('button');b.className='sw';b.style.background=c||'transparent';b.title=c||'erase';b.onclick=function(){PCOL=c;document.querySelectorAll('.sw').forEach(function(x){x.classList.remove('on');});b.classList.add('on');};$('pal').appendChild(b);});
var down=false;$('px').addEventListener('pointerdown',function(e){down=true;pxAt(e);});$('px').addEventListener('pointermove',function(e){if(down)pxAt(e);});window.addEventListener('pointerup',function(){down=false;});
$('pxClear').onclick=function(){PX=new Array(256).fill(null);drawPx();};
$('pxUse').onclick=function(){if(!W)return;W.sprite=PX.slice();changed();if(ENG&&ENG.refreshSprite)ENG.refreshSprite();status('Sprite set as the 2D hero');};
$('pxSave').onclick=function(){var c=document.createElement('canvas');c.width=c.height=128;var g=c.getContext('2d');g.imageSmoothingEnabled=false;g.drawImage($('px'),0,0,128,128);api('/api/asset',{world:W.name,kind:'png',name:'sprite_'+Date.now(),b64:c.toDataURL('image/png').split(',')[1]}).then(function(r){status(r.saved);}).catch(function(e){status(e.message,true);});};
Object.keys(GS.SFX).forEach(function(k){var o=document.createElement('option');o.textContent=k;$('sfxPreset').appendChild(o);});
SL.forEach(function(s){var d=document.createElement('div');d.innerHTML='<label>'+s[0]+' <span id="lv_'+s[0]+'"></span></label><input type="range" id="sl_'+s[0]+'" min="'+s[1]+'" max="'+s[2]+'" step="'+s[3]+'" style="width:100%">';$('sliders').appendChild(d);});
SL.forEach(function(s){$('sl_'+s[0]).oninput=function(){$('lv_'+s[0]).textContent=this.value;};});
$('sfxPreset').onchange=function(){sfxLoad($('sfxPreset').value);};$('sfxPlay').onclick=function(){GS.playSfx(sfxParams());};
$('sfxSave').onclick=function(){if(!W)return;var p=sfxParams();W.sfx=W.sfx||{};W.sfx[CUR]=p;changed();api('/api/asset',{world:W.name,kind:'wav',name:'sfx_'+CUR,b64:b64(GS.wavBytes(GS.sfxSamples(p)))}).then(function(r){status(r.saved+' *');}).catch(function(e){status(e.message,true);});};
function ex(target){$('out').textContent='';status('Exporting...');window.GS_BUSY=1;save().then(function(){return api('/api/export',{name:W.name,target:target});}).then(function(r){var first=r.written[0]||'',dir=first.split('/').slice(0,target==='cartridge'||target==='itch'?2:3).join('/');
$('out').innerHTML='<b>Saved in My_Games/'+E(dir)+'</b><div class="row"><button id="openDir">Open folder</button>'+(target==='cartridge'?'<a href="/files/'+E(first)+'" target="_blank">Open game</a>':'')+'</div><details><summary class="mute">'+r.written.length+' file(s)</summary>'+r.written.map(function(x){return'<div class="mute">'+E(x)+'</div>';}).join('')+'</details>';
$('openDir').onclick=function(){api('/api/open_folder',{path:dir}).then(function(){status('Opened My_Games/'+dir);}).catch(function(e){status(e.message,true);});};
window.GS_BUSY=0;status('Export ready: My_Games/'+dir);GS.playSfx(GS.SFX.power);}).catch(function(e){window.GS_BUSY=0;$('out').textContent=e.message;});}
$('exCart').onclick=function(){ex('cartridge');};if($('exItch'))$('exItch').onclick=function(){ex('itch');};$('licGo').onclick=function(){api('/api/activate',{key:$('licKey').value}).then(function(l){if(l.ok===false){$('licState').textContent='That key did not work: '+l.error+' - paste it again in one piece.';return;}$('licKey').value='';status('Registered'+(l.buyer?' to '+l.buyer:'')+' ('+tierName(l.tier)+' edition): '+tierUnlocks(l.tier)+'.');refreshList($('worlds').value);}).catch(function(e){$('licState').textContent=e.message;});};$('exGodot').onclick=function(){ex('godot');};$('exUe5').onclick=function(){ex('ue5');};
$('exGdOpen').onclick=function(){var b=this;b.disabled=true;status('Opening in Godot...');function post(u,d){return save().then(function(){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});}).then(function(r){return r.json().then(function(j){return{st:r.status,j:j};});});}post('/api/open_godot',{name:W.name}).then(function(r){if(r.j.ok){status('Godot is opening your game. Press F5 in Godot to play.');return;}$('out').textContent=r.j.guide||r.j.error||'Could not open Godot';if(r.j.download&&confirm('Godot 4 is not installed. Download the official Godot '+'(free, MIT licence, about 70 MB, checked against Godot\'s published checksums)?')){status('Downloading Godot...');return post('/api/download_godot',{consent:true}).then(function(d){if(!d.j.ok){$('out').textContent=d.j.error||'Download failed';return;}return post('/api/open_godot',{name:W.name}).then(function(r2){status(r2.j.ok?'Godot is opening your game. Press F5 to play.':(r2.j.guide||r2.j.error));});});}}).catch(function(e){$('out').textContent=e.message;}).then(function(){b.disabled=false;});};$('exUeOpen').onclick=function(){var b=this;b.disabled=true;$('out').textContent='';status('Building the Unreal project and starting Unreal Engine 5 (this can take a minute)...');
save().then(function(){return fetch('/api/open_unreal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:W.name})});}).then(function(r){return r.json().then(function(j){return{st:r.status,j:j};});})
.then(function(x){b.disabled=false;var j=x.j||{};if(x.st===200){status('Unreal Engine 5 is opening your world.');$('out').textContent=j.uproject||j.project||'';}else{status(x.st===402?'Open in Unreal is part of the Pro and Studio tiers.':'Unreal Engine 5 could not be opened - see below.',true);$('out').textContent=String(j.guide||j.message||j.error||('Server said '+x.st));}})
.catch(function(e){b.disabled=false;status(e.message,true);});};
window.addEventListener('keydown',function(e){var tag=(e.target&&e.target.tagName)||'';var typing=/INPUT|TEXTAREA|SELECT/.test(tag);
if((e.ctrlKey||e.metaKey)&&e.code==='KeyS'){e.preventDefault();save();return;}if((e.ctrlKey||e.metaKey)&&e.code==='Enter'){e.preventDefault();setMode(MODE==='play'?'edit':'play');return;}
if(typing)return;if((e.ctrlKey||e.metaKey)&&e.code==='KeyZ'){e.preventDefault();undo(e.shiftKey?1:-1);return;}if((e.ctrlKey||e.metaKey)&&e.code==='KeyY'){e.preventDefault();undo(1);return;}
if(e.key==='?'){$('keysOv').hidden=!$('keysOv').hidden;return;}if(e.code==='Delete'&&MODE==='edit'&&SEL>=0&&$('iDel')){$('iDel').click();return;}
if(e.code==='Escape'){['magicOv','keysOv','restoreOv'].forEach(function(id){$(id).hidden=true;});}});
window.addEventListener('beforeunload',function(e){if(DIRTY){e.preventDefault();e.returnValue='';}});
refreshList().then(function(i){var q=new URLSearchParams(location.search);if(i.worlds.length){var w=i.worlds.find(function(x){return x.slug===q.get('world');})||i.worlds[0];$('worlds').value=w.slug;
api('/api/world?name='+encodeURIComponent(w.slug)).then(function(wd){mount(wd,q.get('mode')==='play'?'play':'edit',+(q.get('level')||0),null,false,w.slug);if(!q.get('world'))tour(0);}).catch(function(e){status(e.message,true);mount(blank('3d'),'edit');});}
else{mount(blank('3d'),'edit');tour(0);}}).catch(function(e){status('The studio server is not answering - restart START_STUDIO. ('+e.message+')',true);});
sfxLoad('jump');drawPx();
});
</script></body></html>
'''
APP_HTML = (APP_HTML.replace('%%LOCKUP%%', _brand_uri('stark-world-engine-lockup-dark.svg'))
            .replace('%%FAVICON%%', _brand_uri('stark-world-engine-favicon.svg') or 'data:,'))


def keep_server_responsive():
    """Windows: run this local server one step above normal priority. With software rendering (no graphics chip) the
    browser's 3D view keeps every core busy, which made saves and exports up to 40x slower (28.6 measurement: a
    cartridge export took 78 s instead of 2 s). Above-normal (never high or realtime) lets the server answer at once.
    -> True when the priority was raised."""
    if os.name != 'nt':
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = wintypes.HANDLE      # the pseudo-handle is -1: keep it pointer-sized
        k.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k.SetPriorityClass.restype = wintypes.BOOL
        return bool(k.SetPriorityClass(k.GetCurrentProcess(), 0x00008000))   # ABOVE_NORMAL_PRIORITY_CLASS
    except (AttributeError, OSError):
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--export', metavar='NAME')
    ap.add_argument('--target', default='cartridge', choices=['cartridge', 'godot', 'ue5'])
    ap.add_argument('--write-docs', metavar='DIR')
    a = ap.parse_args(argv)
    if a.write_docs:
        for p in write_docs(a.write_docs):
            print('wrote', p)
        return 0
    os.makedirs(GAMES, exist_ok=True)
    made = seed_worlds()
    if made:
        print('starter worlds added:', ', '.join(made))
    if a.export:
        try:
            for p in export_world(a.export, a.target):
                print('wrote My_Games/' + p)
        except (PermissionError, ValueError, FileNotFoundError) as e:
            print('export refused:', e)
            return 2
        return 0
    srv = make_server(a.port)
    keep_server_responsive()
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    try:   # in-app updates: this version started, so keep it (stark_update rolls back a version that never gets here)
        from stark_update import updater as _gu
        _gu.confirm_healthy(os.path.dirname(os.path.abspath(__file__)))
    except Exception:  # noqa: BLE001 - updates are optional; the studio never depends on them
        pass
    ed, tier = edition(), current_tier()
    shown = f'{(tier if tier != "free" else ed).capitalize()} edition' + (', not yet activated' if tier == 'free' and ed in ('pro', 'studio') else '')
    print(f'Stark World Engine {VERSION} ({shown}) at {url}  (Ctrl+C to stop; local only)')
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
