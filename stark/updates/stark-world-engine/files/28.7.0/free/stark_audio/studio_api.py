"""Studio glue: what the studio server and the exporters call (see INTEGRATION_PATCHES.md).

handle(method, path, query, body, games) -> (status, content type, bytes) or None when the path is not ours
    POST /api/audio/build     {world, budget?}        render + encode the game's audio pack (cached renders)
    GET  /api/audio/settings?world=                    audio.json (preset, key, bpm, budget, states, ...)
    POST /api/audio/settings  {world, settings}
    GET  /api/audio/status?world=                      last build report
    /voice, /voice/api/*                               the voice booth (booth.handle)
cartridge_html(html, world, games) -> html with the game's audio pack injected (built when missing or stale)
export_files(world, games, target) -> {relative path: bytes} for the UE5 / Godot exporters (WAV + mapping.json)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile

from . import adaptive, booth, dsp, export_map, pack

SETTING_KEYS = {'preset': str, 'key': str, 'mode': str, 'bpm': float, 'seed': int, 'budget': (str, int),
                'states': list, 'stingers': list, 'regions': str, 'bars': dict, 'sfx': list, 'sfx_add': list,
                'sfx_remove': list}


def _slug(name):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', str(name or '')).strip('_')[:48] or 'world'


def world_dir(games, name):
    games = os.path.realpath(games)
    d = os.path.realpath(os.path.join(games, _slug(name)))
    if not d.startswith(games + os.sep):
        raise ValueError('bad world name')
    return d


def load_world(games, name):
    with open(os.path.join(world_dir(games, name), 'world.json'), encoding='utf-8') as fh:
        return json.load(fh)


def clean_settings(s):
    out = {}
    for k, t in SETTING_KEYS.items():
        if k in (s or {}) and s[k] is not None:
            v = s[k]
            if not isinstance(v, t if isinstance(t, tuple) else (t,)) and not (t is float and isinstance(v, int)):
                raise ValueError('audio setting %s has the wrong type' % k)
            out[k] = v
    if 'bpm' in out:
        out['bpm'] = max(60.0, min(180.0, float(out['bpm'])))
    if 'preset' in out and out['preset'] not in ('orchestral', 'synthwave', 'folk', 'ambient', 'chiptune'):
        raise ValueError('preset must be orchestral, synthwave, folk, ambient or chiptune')
    return out


def _pack_dir(wd):
    return os.path.join(wd, 'audio', 'pack')


def save_pack(wd, pk, key):
    d = _pack_dir(wd)
    tmp = d + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for aid, raw in pk['data'].items():
        with open(os.path.join(tmp, aid + '.ogg'), 'wb') as fh:
            fh.write(raw)
    dsp.save_json(os.path.join(tmp, 'manifest.json'), pk['manifest'])
    dsp.save_json(os.path.join(tmp, 'report.json'), dict(pk['report'], key=key))
    shutil.rmtree(d, ignore_errors=True)
    os.replace(tmp, d)


def load_pack(wd):
    d = _pack_dir(wd)
    man = dsp.load_json(os.path.join(d, 'manifest.json'))
    rep = dsp.load_json(os.path.join(d, 'report.json'))
    if not man or not rep:
        return None
    data = {}
    for aid in man['assets']:
        p = os.path.join(d, aid + '.ogg')
        if not os.path.isfile(p):
            return None
        with open(p, 'rb') as fh:
            data[aid] = fh.read()
    return {'manifest': man, 'data': data, 'report': rep}


def _key(world, wd, ac):
    """Staleness key: score config + sfx selection + voice state."""
    import hashlib
    cfg = adaptive.score_config(world, ac)
    from . import sfx_library, voice
    v = voice.load(wd)
    vk = sorted((k, (e.get('processed') or {}).get('take'), (e.get('processed') or {}).get('profile'))
                for k, e in v['lines'].items())
    texts = [(l['id'], l['text']) for l in voice.lines(world)]
    blob = json.dumps([adaptive.config_hash(cfg), pack.world_sounds(world, ac), vk, texts,
                       ac.get('budget'), pack.VERSION], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def build(games, name, budget=None, force=False, progress=None, world=None):
    """Build (or reuse) the pack for a game. `world` overrides world.json (e.g. the Free tier's first level only)."""
    wd = world_dir(games, name)
    if not os.path.isfile(os.path.join(wd, 'world.json')):
        raise FileNotFoundError('no such game: %s' % name)
    world = world or load_world(games, name)
    ac = pack.load_audio_cfg(wd)
    key = _key(world, wd, ac)
    old = None if force else load_pack(wd)
    if old and old['report'].get('key') == key:
        return old
    pk = pack.build_pack(world, wd, budget or ac.get('budget') or 'standard', audio_cfg=ac, progress=progress)
    save_pack(wd, pk, key)
    pk['report']['key'] = key
    return pk


def cartridge_html(html, world, games):
    """Inject the world's audio pack (building it when missing or stale). Any failure keeps the plain cartridge."""
    try:
        pk = build(games, world.get('name'), world=world)
        return pack.inject(html, pk)
    except Exception as e:  # noqa: BLE001 - audio must never break an export
        print('[audio] cartridge export without the audio pack: %s: %s' % (e.__class__.__name__, e))
        return html


def export_files(world, games, target='godot'):
    """{relative path: bytes} of WAV + mapping files for an engine project (under audio/)."""
    pk = build(games, world.get('name'), world=world)
    tmp = tempfile.mkdtemp(prefix='ga_export_')
    try:
        export_map.write(pk, tmp)
        out = {}
        for root, _, names in os.walk(tmp):
            for n in names:
                p = os.path.join(root, n)
                rel = 'audio/' + os.path.relpath(p, tmp).replace(os.sep, '/')
                with open(p, 'rb') as fh:
                    out[rel] = fh.read()
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def handle(method, path, query, body, games):
    if path == '/voice' or path.startswith('/voice/'):
        return booth.handle(method, path, query, body, games)
    if not path.startswith('/api/audio/'):
        return None
    q = {k: (v[0] if isinstance(v, list) else v) for k, v in (query or {}).items()}
    body = body if isinstance(body, dict) else {}
    try:
        name = q.get('world') if method == 'GET' else body.get('world')
        wd = world_dir(games, name)
        if not os.path.isfile(os.path.join(wd, 'world.json')):
            raise FileNotFoundError('no such game')
        if path == '/api/audio/settings':
            p = os.path.join(wd, 'audio', 'audio.json')
            if method == 'POST':
                cur = dsp.load_json(p, {}) or {}
                cur.update(clean_settings(body.get('settings') or {}))
                dsp.save_json(p, cur)
                return 200, 'application/json', json.dumps({'settings': cur}).encode()
            return 200, 'application/json', json.dumps({'settings': dsp.load_json(p, {}) or {}}).encode()
        if path == '/api/audio/status':
            return 200, 'application/json', json.dumps({'report': dsp.load_json(os.path.join(_pack_dir(wd), 'report.json'))}).encode()
        if path == '/api/audio/build' and method == 'POST':
            pk = build(games, name, body.get('budget'), bool(body.get('force')))
            return 200, 'application/json', json.dumps({'report': pk['report']}).encode()
        return 404, 'application/json', b'{"error":"not found"}'
    except FileNotFoundError as e:
        return 404, 'application/json', json.dumps({'error': str(e)[:200]}).encode()
    except (ValueError, RuntimeError, OSError) as e:
        return 400, 'application/json', json.dumps({'error': str(e)[:300]}).encode()
