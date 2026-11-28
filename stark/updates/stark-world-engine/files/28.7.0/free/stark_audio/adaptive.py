"""Adaptive score: world -> score config -> rendered mood cues + stingers (cached) -> runtime music manifest.

The music director rules live here (TRANSITIONS, LAYERS, REST, PRIORITY) so the cartridge runtime, the
UE5/Godot export mapping and the docs all read one table.

score_config(world, audio_cfg)   preset/key/mode/bpm/seed, the states + stingers this game needs, regions
build_score(world, ...)          renders (or loads from the cache) every cue stem and stinger as WAV
music_manifest(score, ids)       the JSON the runtime reads (asset ids filled in by the packer)
"""
from __future__ import annotations

import hashlib
import json
import os
import time

from . import ROOT, dsp
import music_engine as me

DEFAULT_CACHE = os.environ.get('STARK_AUDIO_CACHE') or os.path.join(ROOT, '_music_out', 'ga_cache')
SCORE_VERSION = 1

# Director rules ------------------------------------------------------------------------------------------------
# sync: 'beat' | 'bar' | 'end' (wait for a one-shot to finish) | 'now'. Times in seconds. First match wins.
TRANSITIONS = [
    {'from': '*', 'to': 'combat', 'sync': 'beat', 'fade_in': 0.06, 'fade_out': 0.8, 'stinger': 'combat_start'},
    {'from': 'combat', 'to': 'victory', 'sync': 'bar', 'fade_in': 0.02, 'fade_out': 0.5},
    {'from': 'combat', 'to': '*', 'sync': 'bar', 'fade_in': 2.5, 'fade_out': 2.0, 'resume': True},
    {'from': '*', 'to': 'tension', 'sync': 'beat', 'fade_in': 1.5, 'fade_out': 2.0},
    {'from': 'tension', 'to': '*', 'sync': 'bar', 'fade_in': 3.0, 'fade_out': 3.0, 'resume': True},
    {'from': '*', 'to': 'victory', 'sync': 'bar', 'fade_in': 0.02, 'fade_out': 0.6},
    {'from': 'victory', 'to': '*', 'sync': 'end', 'fade_in': 3.0, 'fade_out': 3.0, 'resume': True},
    {'from': '*', 'to': 'town', 'sync': 'bar', 'fade_in': 3.0, 'fade_out': 3.0, 'resume': True},
    {'from': '*', 'to': 'night', 'sync': 'bar', 'fade_in': 6.0, 'fade_out': 6.0, 'resume': True},
    {'from': '*', 'to': 'explore', 'sync': 'bar', 'fade_in': 3.0, 'fade_out': 3.0, 'resume': True},
]
# vertical layering: a layer sounds when the intensity (0..1) reaches its threshold
LAYERS = {'explore': {'bed': 0.0, 'perc': 0.3, 'lead': 0.55}, 'tension': {'bed': 0.0, 'perc': 0.0, 'lead': 0.4},
          'combat': {'bed': 0.0, 'perc': 0.0, 'lead': 0.25}, 'town': {'bed': 0.0, 'perc': 0.25, 'lead': 0.45},
          'night': {'bed': 0.0, 'perc': 0.7, 'lead': 0.35}, 'victory': {'mix': 0.0}}
LAYER_ORDER = {'explore': ['bed', 'perc', 'lead'], 'tension': ['bed', 'perc', 'lead'], 'combat': ['bed', 'perc', 'lead'],
               'town': ['bed', 'perc', 'lead'], 'night': ['bed', 'lead', 'perc'], 'victory': ['mix']}
# long-play fatigue control: ambient states play a while, rest (ambience carries the world), then return
REST = {'explore': {'play': [150, 300], 'rest': [50, 130]}, 'town': {'play': [180, 360], 'rest': [40, 90]},
        'night': {'play': [120, 240], 'rest': [60, 150]}}
# the melody layer breathes: it sits out about one loop pass in three so the theme never wears thin
BREATHE = {'explore': 0.34, 'town': 0.25, 'night': 0.4}
PRIORITY = {'combat': 5, 'victory': 4, 'tension': 3, 'town': 2, 'night': 1, 'explore': 0}
STINGER_RULES = {'combat_start': {'sync': 'beat', 'duck_db': -8}, 'boss': {'sync': 'beat', 'duck_db': -10},
                 'quest_complete': {'sync': 'beat', 'duck_db': -7}, 'discovery': {'sync': 'beat', 'duck_db': -6},
                 'danger': {'sync': 'beat', 'duck_db': -6}, 'defeat': {'sync': 'now', 'duck_db': -30},
                 'level_up': {'sync': 'beat', 'duck_db': -7}}
NIGHTISH = {'Bioluminescent Twilight', 'Sub-Zero Aurora'}


def _levels(world):
    return [world] + list(world.get('levels') or [])


def score_config(world, audio_cfg=None):
    """Score settings for a world. audio_cfg (My_Games/<game>/audio/audio.json) may override any field."""
    ac = dict(audio_cfg or {})
    levels = _levels(world)
    base = me.world_score_defaults(world.get('mood'))
    preset = me.preset_for_style(ac.get('preset') or base['preset'])
    key = ac.get('key') or base['key']
    mode = ac.get('mode') or base['mode']
    bpm = float(ac.get('bpm') or me.CUE[preset]['bpm'])
    seed = int(ac.get('seed', world.get('seed') or 0)) % 100000
    objs = [o for L in levels for o in (L.get('objects') or [])]
    tiles = ''.join(''.join(L.get('tiles') or []) for L in levels)
    has_enemy = any(o.get('t') == 'enemy' for o in objs) or any(c in tiles for c in 'eaB')
    has_boss = any(o.get('t') == 'enemy' and o.get('kind') == 'boss' for o in objs) or 'B' in tiles
    has_quest = any(L.get('quests') for L in levels)
    has_town = sum(1 for o in objs if o.get('t') == 'house') >= 2
    has_day = any(float((L.get('day') or {}).get('len') or 0) > 0 for L in levels)
    states = ['explore']
    if has_enemy:
        states += ['tension', 'combat']
    if has_enemy or has_quest:
        states.append('victory')
    if has_town:
        states.append('town')
    if has_day:
        states.append('night')
    states = [s for s in me.SCORE_STATES if s in (ac.get('states') or states)]
    stingers = ['level_up', 'defeat']
    if has_enemy:
        stingers += ['combat_start', 'danger']
    if has_boss:
        stingers.append('boss')
    if has_quest:
        stingers.append('quest_complete')
    if len(levels) > 1 or any(o.get('t') == 'portal' for o in objs):
        stingers.append('discovery')
    stingers = [s for s in me.STINGERS if s in (ac.get('stingers') or stingers)]
    # regions: level 0 mood = 'main'; each other mood gets its own explore cue (own motif, orchestration, mode)
    regions = [{'id': 'main', 'mood': world.get('mood'), 'name': world.get('name') or 'world', 'preset': preset,
                'mode': mode, 'states': states}]
    level_region = []
    seen = {world.get('mood'): 'main'}
    for i, L in enumerate(levels):
        md = L.get('mood') or world.get('mood')
        if md not in seen and ac.get('regions', 'per_mood') != 'single':
            rid = 'mood_%d' % len(seen)
            d = me.world_score_defaults(md)
            regions.append({'id': rid, 'mood': md, 'name': L.get('name') or md, 'preset': d['preset'],
                            'mode': d['mode'], 'states': ['explore']})
            seen[md] = rid
        level_region.append(seen.get(md, 'main'))
    bars = dict(me.CUE_BARS)
    bars.update(ac.get('bars') or {})
    return {'v': SCORE_VERSION, 'engine': me.VERSION, 'preset': preset, 'key': key, 'mode': mode, 'bpm': bpm,
            'seed': seed, 'states': states, 'stingers': stingers, 'regions': regions, 'level_region': level_region,
            'bars': {k: int(v) for k, v in bars.items()}, 'night_mood': world.get('mood') in NIGHTISH}


def config_hash(cfg):
    keep = {k: cfg[k] for k in ('v', 'engine', 'preset', 'key', 'mode', 'bpm', 'seed', 'states', 'stingers', 'bars')}
    keep['regions'] = [{k: r[k] for k in ('id', 'mood', 'name', 'preset', 'mode', 'states')} for r in cfg['regions']]
    return hashlib.sha1(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


def build_score(world, audio_cfg=None, cache_dir=None, progress=None, cfg=None):
    """Render (or load from the cache) every cue stem and stinger. -> score dict with WAV paths + metadata."""
    cfg = cfg or score_config(world, audio_cfg)
    h = config_hash(cfg)
    d = os.path.join(cache_dir or DEFAULT_CACHE, 'score_' + h)
    man_p = os.path.join(d, 'score.json')
    old = dsp.load_json(man_p)
    if old and old.get('hash') == h and all(os.path.isfile(p) for p in _score_files(old)):
        old['cached'] = True
        return old
    t0 = time.time()
    os.makedirs(d, exist_ok=True)
    out = {'hash': h, 'cfg': cfg, 'dir': d, 'regions': {}, 'stingers': {}}
    for r in cfg['regions']:
        motif = me.region_motif(r['name'], cfg['seed'])
        rd = {'motif': motif, 'cues': {}}
        for st in r['states']:
            if progress:
                progress('music', '%s %s' % (r['id'], st))
            info = me.compose_cue(st, r['preset'], cfg['key'], r['mode'], cfg['bpm'],
                                  cfg['bars'].get(st) if st != 'victory' else None, cfg['seed'], motif, r['name'],
                                  write=False)
            files = {}
            for layer, y in info.pop('_arrays').items():
                p = os.path.join(d, 'cue_%s_%s_%s.wav' % (r['id'], st, layer))
                dsp.write_wav(p, y, (0, y.shape[1]) if info['loop_samples'] else None, '%s %s' % (st, layer))
                files[layer] = p
            info['files'] = files
            rd['cues'][st] = info
        out['regions'][r['id']] = rd
    main_motif = out['regions']['main']['motif']
    for k in cfg['stingers']:
        if progress:
            progress('music', 'stinger ' + k)
        si = me.compose_stinger(k, cfg['preset'], cfg['key'], cfg['mode'], cfg['bpm'], cfg['seed'], main_motif,
                                cfg['regions'][0]['name'], write=False)
        y = si.pop('array')
        p = os.path.join(d, 'stinger_%s.wav' % k)
        dsp.write_wav(p, y, None, 'stinger ' + k)
        si['path'] = p
        out['stingers'][k] = si
    out['render_s'] = round(time.time() - t0, 1)
    dsp.save_json(man_p, out)
    out['cached'] = False
    return out


def _score_files(score):
    for r in score.get('regions', {}).values():
        for c in r.get('cues', {}).values():
            for p in c.get('files', {}).values():
                yield p
    for s in score.get('stingers', {}).values():
        yield s.get('path', '')


def music_manifest(score, asset_ids):
    """Runtime JSON. asset_ids: {wav path: {'id', 'start', 'loopStart', 'loopEnd', 'dur'}} from the packer."""
    cfg = score['cfg']
    regions = {}
    for rid, r in score['regions'].items():
        cues = {}
        for st, c in r['cues'].items():
            layers = {}
            for layer, p in c['files'].items():
                a = asset_ids.get(p)
                if a:
                    layers[layer] = a
            if not layers:
                continue
            one = c['loop_samples'] is None
            cues[st] = {'state': st, 'len': c['len_s'], 'bars': c['bars'], 'bar': c['bar_s'], 'bpm': c['bpm'],
                        'musicEnd': round((c['bars'] + 1.0) * c['bar_s'], 4) if one else None,
                        'bpb': c['beats_per_bar'], 'key': c['key'], 'oneShot': c['loop_samples'] is None,
                        'layers': layers, 'order': [x for x in LAYER_ORDER[st] if x in layers],
                        'thresholds': {k: v for k, v in LAYERS[st].items() if k in layers}}
        if cues or rid == 'main':
            regions[rid] = {'cues': cues, 'motif': r.get('motif')}
    stingers = {}
    for k, s in score['stingers'].items():
        a = asset_ids.get(s['path'])
        if a:
            stingers[k] = dict(a, len=s['len_s'], **STINGER_RULES.get(k, {'sync': 'beat', 'duck_db': -6}))
    return {'bpm': cfg['bpm'], 'bpb': 4, 'key': cfg['key'], 'mode': cfg['mode'], 'preset': cfg['preset'],
            'regions': regions, 'levelRegion': [x if x in regions else 'main' for x in cfg['level_region']],
            'stingers': stingers,
            'transitions': TRANSITIONS, 'rest': REST, 'breathe': BREATHE, 'priority': PRIORITY,
            'nightMood': cfg.get('night_mood', False)}
