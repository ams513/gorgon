"""Cartridge packer: everything a game needs, encoded to Ogg Opus inside a size budget, plus the runtime.

build_pack(world, world_dir)   -> pack dict {'manifest', 'data' (id -> bytes), 'report'}
pack_script(pack)              -> '<script>window.STARK_AUDIO_PACK=...</script>' (base64 data inline)
inject(html, pack)             -> cartridge HTML with the pack + stark_audio.js + boot call before </body>
cartridge_with_audio(html, world, world_dir)  one call for the exporter (cached renders make it quick)
write_export(pack, out_dir)    -> decoded WAV + JSON folder for the UE5 / Godot bridges (see export_map)

Loops are encoded with periodic padding (the loop's tail before it, its head after it), so the decoded loop region
is seamless whatever priming offset a browser's decoder applies. One-shots share audio sprites (fewer streams,
fewer decodes). Voice lines are separate small assets that the runtime decodes on demand.
"""
from __future__ import annotations

import base64
import json
import math
import os
import time

import numpy as np

from . import VERSION, RUNTIME_JS, dsp, adaptive, sfx_library

SR = dsp.SR
BUDGETS = {'compact': 2_500_000, 'standard': 5_000_000, 'rich': 10_000_000}   # bytes the audio adds to the HTML
# quality ladder: kbps for (music stems, sfx sprites, ambience loops, voice) + sfx variant cap + extras kept
LADDER = [   # effects lose variants/bitrate first; music keeps every region down to 32 kbps before any is dropped
    {'music': 64, 'sfx': 48, 'amb': 40, 'voice': 32, 'variants': None, 'drop': []},
    {'music': 56, 'sfx': 40, 'amb': 36, 'voice': 28, 'variants': None, 'drop': []},
    {'music': 48, 'sfx': 36, 'amb': 32, 'voice': 24, 'variants': 4, 'drop': []},
    {'music': 48, 'sfx': 28, 'amb': 28, 'voice': 24, 'variants': 3, 'drop': []},
    {'music': 40, 'sfx': 28, 'amb': 24, 'voice': 22, 'variants': 3, 'drop': []},
    {'music': 32, 'sfx': 24, 'amb': 24, 'voice': 20, 'variants': 2, 'drop': []},
    {'music': 32, 'sfx': 24, 'amb': 20, 'voice': 18, 'variants': 2, 'drop': ['regions']},
    {'music': 28, 'sfx': 20, 'amb': 20, 'voice': 18, 'variants': 2, 'drop': ['regions', 'night', 'town']},
    {'music': 24, 'sfx': 20, 'amb': 18, 'voice': 16, 'variants': 1, 'drop': ['regions', 'night', 'town', 'stingers']},
]
PAD_S = 0.12          # periodic padding around loops
GAP_S = 0.06          # silence between sprite segments (codec overlap never bleeds into a neighbour)
MIX = {  # runtime bus defaults (dB) and ducking; loudness anchors documented in EXPORT_MAPPING.md
    'buses': {'music': 0.0, 'amb': 0.0, 'sfx': 0.0, 'ui': -2.0, 'voice': 0.0},
    'duck': {'voice': {'music': -9.0, 'amb': -6.0, 'sfx': -3.0, 'attack': 0.12, 'release': 0.7}},
    'limiter': {'threshold': -3.0, 'ratio': 20, 'attack': 0.001, 'release': 0.12},
    'night_mode': {'threshold': -26.0, 'ratio': 3.5, 'knee': 12},
    'targets_lufs': {'music': -19, 'voice': -18, 'program': -18, 'true_peak_dbtp': -1},
    'spatial': {'refDistance': 2.0, 'maxDistance': 60.0, 'rolloff': 1.0, 'airHz': [18000, 4500]},
}


def _b64len(n):
    return 4 * int(math.ceil(n / 3.0))


def _est(dur, kbps):
    return int(dur * kbps * 1000 / 8 * 1.04) + 300


def _pad_loop(y):
    p = int(PAD_S * SR)
    y = dsp.as_stereo(y)
    L = y.shape[1]
    p = min(p, L // 2)
    return np.concatenate([y[:, L - p:], y, y[:, :p]], axis=1), p / float(SR), L / float(SR)


def _sprite(clips):
    """[(key, array)] -> (stereo array, {key: [start_s, dur_s]})"""
    gap = np.zeros((2, int(GAP_S * SR)))
    parts, segs, pos = [gap], {}, gap.shape[1]
    for key, y in clips:
        y = dsp.as_stereo(y)
        segs[key] = [round(pos / float(SR), 5), round(y.shape[1] / float(SR), 5)]
        parts += [y, gap]
        pos += y.shape[1] + gap.shape[1]
    return np.concatenate(parts, axis=1), segs


def load_audio_cfg(world_dir):
    return dsp.load_json(os.path.join(world_dir, 'audio', 'audio.json'), {}) if world_dir else {}


def _sfx_cache(cache_dir, tonic):
    return os.path.join(cache_dir or adaptive.DEFAULT_CACHE, 'sfx_v%d_t%d' % (sfx_library.VERSION, tonic % 12))


def render_sfx(names, ctx, cache_dir=None, variants=None, progress=None):
    """Render (or load cached) variants -> {name: [arrays]}."""
    d = _sfx_cache(cache_dir, int(ctx.get('tonic', 0)))
    os.makedirs(d, exist_ok=True)
    out = {}
    for name in names:
        e = sfx_library.CATALOG[name]
        nv = e['variants'] if variants is None else max(1, min(int(variants), e['variants']))
        arr = []
        for v in range(nv):
            p = os.path.join(d, '%s_%d.wav' % (name.replace('.', '_'), v))
            if os.path.isfile(p):
                y, _ = dsp.read_wav(p)
            else:
                if progress:
                    progress('sfx', name)
                y = sfx_library.render(name, v, ctx)
                dsp.write_wav(p, y, (0, y.shape[-1]) if e['loop'] else None, name)
                y, _ = dsp.read_wav(p)
            arr.append(y)
        out[name] = arr
    return out


def _world_info(world):
    """Positions the runtime adapter uses (houses for town detection, water, day cycle, mode) per level."""
    out = []
    for L in [world] + list(world.get('levels') or []):
        houses = [[round(float(o['x']), 2), round(float(o['z']), 2)] for o in (L.get('objects') or [])
                  if o.get('t') == 'house']
        out.append({'mode': L.get('mode') or world.get('mode'), 'mood': L.get('mood') or world.get('mood'),
                    'houses': houses, 'water': L.get('water'), 'day': L.get('day') or None,
                    'weather': L.get('weather') or 'auto'})
    return out


def _ambience_manifest(world, loops_present, spots_present):
    levels = []
    for L in [world] + list(world.get('levels') or []):
        mood = L.get('mood') or world.get('mood')
        a = sfx_library.MOOD_AMBIENCE.get(mood, {})
        beds = [b for b in [a.get('bed')] + list(a.get('layers', [])) if b and b in loops_present]
        w = L.get('weather')
        weather = [b for b in sfx_library.WEATHER_AMBIENCE.get(w, []) if b in loops_present]
        spots = [{'sfx': s, 'every': every} for s, every in a.get('spots', []) if s in spots_present]
        if w == 'storm' and 'weather.thunder' in spots_present:
            spots.append({'sfx': 'weather.thunder', 'every': 22})
        surfaces = [s for s in sfx_library.MOOD_SURFACES.get(mood, ['grass']) if 'foot.' + s in spots_present]
        levels.append({'beds': beds, 'weather': weather, 'spots': spots, 'surfaces': surfaces})
    return {'levels': levels, 'town': 'amb.town' if 'amb.town' in loops_present else None,
            'townSpots': [{'sfx': s, 'every': e} for s, e in (('spot.hammer', 14), ('spot.bell', 120), ('spot.hooves', 40))
                          if s in spots_present]}


def build_pack(world, world_dir=None, budget='standard', codec='opus', cache_dir=None, audio_cfg=None,
               progress=None, include_voice=True, sfx_names=None):
    """Render/collect and encode everything a world needs inside `budget` (name or bytes of HTML)."""
    t0 = time.time()
    limit = BUDGETS.get(budget, budget) if not isinstance(budget, (int, float)) else int(budget)
    if not isinstance(limit, (int, float)):
        raise ValueError('budget must be compact, standard, rich or a number of bytes')
    ac = audio_cfg if audio_cfg is not None else load_audio_cfg(world_dir)
    cfg = adaptive.score_config(world, ac)
    score = adaptive.build_score(world, ac, cache_dir, progress, cfg)
    tonic = me_tonic(cfg['key'])
    names = sorted(sfx_names or world_sounds(world, ac))
    sfx = render_sfx(names, {'tonic': tonic}, cache_dir, None, progress)
    voice_lines = []
    if include_voice:
        from . import voice
        voice_lines = voice.pack_entries(world, world_dir)
    # ---- size estimate per ladder level, pick the best that fits ------------------------------------------------
    def cue_items(level):
        items = []
        for rid, r in score['regions'].items():
            if rid != 'main' and 'regions' in level['drop']:
                continue
            for st, c in r['cues'].items():
                if st in level['drop']:
                    continue
                for layer, p in c['files'].items():
                    items.append((rid, st, layer, p, c['len_s']))
        return items

    def estimate(level):
        b64 = 0
        for _, _, _, _, L in cue_items(level):
            b64 += _b64len(_est(L + 2 * PAD_S, level['music']))
        if 'stingers' not in level['drop']:
            b64 += _b64len(_est(sum(s['len_s'] + GAP_S for s in score['stingers'].values()), level['music']))
        else:
            b64 += _b64len(_est(sum(s['len_s'] for k, s in score['stingers'].items() if k in ('combat_start', 'level_up')), level['music']))
        one = 0.0
        for n in names:
            e = sfx_library.CATALOG[n]
            v = sfx[n] if level['variants'] is None else sfx[n][:level['variants']]
            if e['loop']:
                b64 += _b64len(_est(v[0].shape[-1] / SR + 2 * PAD_S, level['amb']))
            else:
                one += sum(y.shape[-1] / SR + GAP_S for y in v)
        b64 += _b64len(_est(one, level['sfx']))
        for ln in voice_lines:
            if ln.get('audio') is not None:
                b64 += _b64len(_est(len(ln['audio']) / SR, level['voice']))
        return b64 + 60000                                # manifest + JSON overhead
    chosen = len(LADDER) - 1
    for i, lv in enumerate(LADDER):
        if estimate(lv) <= limit:
            chosen = i
            break
    # ---- encode (step down if the real encode overshoots) --------------------------------------------------------
    while True:
        level = LADDER[chosen]
        pack = _encode(world, score, cfg, names, sfx, voice_lines, level, codec, cue_items(level))
        size = pack['report']['html_bytes']
        if size <= limit or chosen == len(LADDER) - 1:
            break
        chosen += 1
    pack['report'].update({'budget': limit, 'level': chosen, 'fits': size <= limit, 'build_s': round(time.time() - t0, 1),
                           'score_cached': score.get('cached', False), 'states': cfg['states'],
                           'stingers': list(pack['manifest']['music']['stingers'])})
    return pack


def world_sounds(world, ac=None):
    """The library sounds a game ships: the automatic pick, or audio.json's 'sfx' list, plus 'sfx_add' minus 'sfx_remove'."""
    ac = ac or {}
    names = set(ac.get('sfx') or sfx_library.sounds_for_world(world))
    names |= set(ac.get('sfx_add') or [])
    names -= set(ac.get('sfx_remove') or [])
    return sorted(n for n in names if n in sfx_library.CATALOG)


def me_tonic(key):
    import music_engine as me
    return me._parse_key(key, __import__('random').Random(0))[0]


def _encode(world, score, cfg, names, sfx, voice_lines, level, codec, items):
    data, assets = {}, {}
    counter = [0]

    def add(kind, raw, dur, extra=None):
        aid = '%s%d' % (kind, counter[0])
        counter[0] += 1
        data[aid] = raw
        assets[aid] = dict({'bytes': len(raw), 'dur': round(dur, 4), 'kind': kind}, **(extra or {}))
        return aid
    # music stems (loop padded), stingers (one sprite)
    ids = {}
    for rid, st, layer, p, L in items:
        y, _ = dsp.read_wav(p)
        c = score['regions'][rid]['cues'][st]
        if c['loop_samples']:
            yp, start, Ls = _pad_loop(y)
            aid = add('m', dsp.encode(yp, codec, level['music'], vbr='constrained'), yp.shape[1] / SR)
            ids[p] = {'a': aid, 'start': round(start, 5), 'loopStart': round(start, 5), 'loopEnd': round(start + Ls, 5)}
        else:
            y = dsp.as_stereo(y)
            yp = np.concatenate([np.zeros((2, int(0.02 * SR))), y], axis=1)
            aid = add('m', dsp.encode(yp, codec, level['music'], vbr='constrained'), yp.shape[1] / SR)
            ids[p] = {'a': aid, 'start': 0.02, 'dur': round(y.shape[1] / SR, 5)}
    st_keep = [k for k in score['stingers'] if 'stingers' not in level['drop'] or k in ('combat_start', 'level_up')]
    if st_keep:
        clips = [(k, dsp.read_wav(score['stingers'][k]['path'])[0]) for k in st_keep]
        spr, segs = _sprite(clips)
        aid = add('s', dsp.encode(spr, codec, level['music']), spr.shape[1] / SR)
        for k in st_keep:
            ids[score['stingers'][k]['path']] = {'a': aid, 'start': segs[k][0], 'dur': segs[k][1]}
    music = adaptive.music_manifest(score, ids)
    # sfx: one-shot sprites per bus, loops separately
    sfx_m, loops_m = {}, {}
    groups = {}
    for n in names:
        e = sfx_library.CATALOG[n]
        vs = sfx[n] if level['variants'] is None else sfx[n][:level['variants']]
        meta = sfx_library.meta(n)
        if e['loop']:
            yp, start, Ls = _pad_loop(vs[0])
            aid = add('l', dsp.encode(yp, codec, level['amb'], vbr='constrained'), yp.shape[1] / SR)
            loops_m[n] = dict(meta, a=aid, start=round(start, 5), loopStart=round(start, 5), loopEnd=round(start + Ls, 5))
        else:
            groups.setdefault(e['bus'], []).append((n, vs, meta))
    for bus, lst in sorted(groups.items()):
        clips = [('%s#%d' % (n, i), y) for n, vs, _ in lst for i, y in enumerate(vs)]
        spr, segs = _sprite(clips)
        aid = add('x', dsp.encode(spr, codec, level['sfx']), spr.shape[1] / SR)
        for n, vs, meta in lst:
            sfx_m[n] = dict(meta, a=aid, segs=[segs['%s#%d' % (n, i)] for i in range(len(vs))])
    # voice: per-line assets (lazy decode), subtitles + visemes inline
    lines = {}
    for ln in voice_lines:
        ent = {k: ln[k] for k in ('speaker', 'text', 'subs', 'vis', 'profile', 'placeholder') if k in ln}
        if ln.get('audio') is not None:
            a = np.asarray(ln['audio'], dtype=np.float64)
            a = np.concatenate([np.zeros(int(0.01 * SR)), a])
            ent['a'] = add('v', dsp.encode(a, codec, level['voice'], application='voip'), len(a) / SR)
            ent['start'] = 0.01
            ent['dur'] = round((len(a) - int(0.01 * SR)) / SR, 4)
        lines[ln['id']] = ent
    legacy = dict(sfx_library.LEGACY_MAP)
    if cfg['preset'] == 'chiptune':
        legacy['jump'] = 'ui.pickup'
    manifest = {
        'v': 1, 'engine': 'stark_audio %s' % VERSION, 'codec': codec, 'mime': dsp.MIME[codec],
        'assets': assets, 'music': music, 'sfx': {k: v for k, v in sfx_m.items() if k in names},
        'loops': loops_m, 'ambience': _ambience_manifest(world, set(loops_m), set(sfx_m) | set(loops_m)),
        'legacy': {k: v for k, v in legacy.items() if v in sfx_m or v in loops_m},
        'voice': {'lines': lines, 'speakers': _speaker_profiles(voice_lines)},
        'mix': MIX, 'world': _world_info(world), 'quality': level,
    }
    js_bytes = len(_manifest_json(manifest)) + sum(_b64len(len(v)) for v in data.values()) + 64
    report = {'assets': len(data), 'encoded_bytes': sum(len(v) for v in data.values()), 'html_bytes': js_bytes,
              'by_kind': {k: sum(a['bytes'] for a in assets.values() if a['kind'] == k) for k in 'msxlv'},
              'music_kbps': level['music'], 'voice_lines': len(lines),
              'voice_recorded': sum(1 for v in lines.values() if 'a' in v)}
    return {'manifest': manifest, 'data': data, 'report': report}


def _speaker_profiles(voice_lines):
    out = {}
    for ln in voice_lines:
        sp = ln.get('speaker') or ''
        if sp and sp not in out:
            out[sp] = ln.get('profile_info') or {}
    return out


def _manifest_json(manifest):
    s = json.dumps(manifest, separators=(',', ':'))
    return (s.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


def pack_script(pack):
    parts = ['<script>window.STARK_AUDIO_PACK=', _manifest_json(pack['manifest'])[:-1], ',"data":{']
    first = True
    for aid, raw in pack['data'].items():
        parts.append(('' if first else ',') + '"%s":"%s"' % (aid, base64.b64encode(raw).decode('ascii')))
        first = False
    parts.append('}};</script>')
    return ''.join(parts)


def runtime_js():
    with open(RUNTIME_JS, encoding='utf-8') as fh:
        return fh.read()


MARK = '<!--stark-audio-->'


def inject(html, pack, boot=True):
    """Insert pack + runtime before </body>. Idempotent: an earlier injection is replaced."""
    if MARK in html:
        a = html.index(MARK)
        b = html.index(MARK, a + 1) + len(MARK)
        html = html[:a] + html[b:]
    js = runtime_js()
    if '</script' in js.lower():
        raise ValueError('runtime must not contain a closing script tag')
    block = MARK + pack_script(pack) + '<script>' + js + '</script>'
    if boot:
        block += '<script>try{StarkAudio.boot();}catch(e){if(window.console)console.warn("audio",e);}</script>'
    block += MARK
    i = html.lower().rfind('</body>')
    if i < 0:
        return html + block
    return html[:i] + block + html[i:]


def cartridge_with_audio(html, world, world_dir=None, budget=None, **kw):
    ac = load_audio_cfg(world_dir)
    pack = build_pack(world, world_dir, budget or ac.get('budget') or 'standard', audio_cfg=ac, **kw)
    return inject(html, pack), pack['report']


def write_export(pack, out_dir):
    """Decode every asset back to WAV (48 kHz) + write audio_manifest.json for the UE5/Godot bridges."""
    os.makedirs(out_dir, exist_ok=True)
    files = {}
    for aid, raw in pack['data'].items():
        p = os.path.join(out_dir, aid + '.ogg')
        with open(p, 'wb') as fh:
            fh.write(raw)
        files[aid] = os.path.basename(p)
    man = dict(pack['manifest'])
    man['files'] = files
    dsp.save_json(os.path.join(out_dir, 'audio_manifest.json'), man)
    return out_dir
